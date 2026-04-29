"""Netflix Eureka service registry adapter.

Endpoints (Eureka 1.x compatible):
  GET /eureka/apps               JSON: {"applications": {"application": [...]}}
  GET /eureka/apps/<APP_NAME>    JSON: detail for one app

Each Eureka "application" = a service. Tech identification:
  ① metadata key 'tech' / 'app' / 'middleware'
  ② name regex match
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host

# Reuse canonical mapping
from .opscloud4 import _TECH_ALIAS_TO_CANONICAL, _TECH_NAME_PATTERN


def _normalize(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip().lower()
    if s in _TECH_ALIAS_TO_CANONICAL:
        return _TECH_ALIAS_TO_CANONICAL[s]
    m = _TECH_NAME_PATTERN.search(s)
    return _TECH_ALIAS_TO_CANONICAL.get(m.group(1).lower()) if m else None


def identify_tech(app_name: str, instances: list[dict]) -> Optional[tuple[str, str, str]]:
    # ① per-instance metadata
    for inst in instances:
        meta = inst.get("metadata") or {}
        for k in ("tech", "app", "middleware", "kind"):
            v = meta.get(k)
            if v:
                tech = _normalize(v)
                if tech:
                    return tech, "high", f"metadata:{k}={v}"
    # ② name regex
    tech = _normalize(app_name)
    if tech:
        return tech, "low", f"app_name:{app_name}"
    return None


def build_eureka_cluster(app_name: str, instances: list[dict],
                        tech: str, confidence: str, signal: str) -> Cluster:
    cid = f"eureka/{app_name}"
    hosts: list[Host] = []
    for inst in instances:
        host = inst.get("hostName") or inst.get("ipAddr") or ""
        port_obj = inst.get("port") or {}
        secure_obj = inst.get("securePort") or {}
        port = port_obj.get("$") if isinstance(port_obj, dict) else port_obj
        if isinstance(port, str):
            try:
                port = int(port)
            except ValueError:
                port = None
        hosts.append(Host(
            fqdn=host, address=inst.get("ipAddr") or host,
            port=int(port) if port else None,
            role=inst.get("metadata", {}).get("role", "instance"),
            tags={"status": inst.get("status", ""),
                  "app": app_name,
                  **{k: v for k, v in (inst.get("metadata") or {}).items()
                     if isinstance(v, str)}},
            cluster_id=cid,
            health=inst.get("status", "unknown"),
        ))
    return Cluster(
        id=cid, tech=tech, hosts=hosts,
        discovery_source="eureka",
        metadata={"app_name": app_name,
                  "tech_confidence": confidence, "tech_signal": signal,
                  "instance_count": len(instances)},
    )


class EurekaAdapter:
    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "EurekaAdapter":
        return cls(base_url=os.environ.get("EUREKA_URL", ""))

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.base_url:
            return []
        try:  # pragma: no cover (network)
            resp = requests.get(f"{self.base_url}/apps",
                               headers={"Accept": "application/json"},
                               timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:  # noqa  # pragma: no cover
            return []
        apps = (payload.get("applications") or {}).get("application") or []
        # Eureka returns either a list or a single dict for single app
        if isinstance(apps, dict):
            apps = [apps]
        clusters: list[Cluster] = []
        for app in apps:
            name = app.get("name", "")
            instances = app.get("instance") or []
            if isinstance(instances, dict):
                instances = [instances]
            ident = identify_tech(name, instances)
            if not ident:
                continue
            tech, conf, sig = ident
            if tech_filter and tech != tech_filter:
                continue
            clusters.append(build_eureka_cluster(name, instances, tech, conf, sig))
        return clusters
