"""Consul service registry adapter.

Pulls services + their healthy nodes from Consul's HTTP API. Each registered
service becomes a Cluster, with nodes as Hosts.

Tech identification:
  ① service tag matching `tech=<X>` or `app=<X>`
  ② service meta keys "tech" / "app" / "service_kind"
  ③ service name regex against known tech list (fallback)

Endpoints:
  GET /v1/agent/services                  list services on this agent
  GET /v1/catalog/services                catalog-wide service list
  GET /v1/catalog/service/<name>          nodes registered for a service
  GET /v1/health/service/<name>?passing=1 healthy instances
"""
from __future__ import annotations

import os
import re
from typing import Iterable, Optional

import requests

from ..core.models import Cluster, Host

# Reuse the canonical-tech mapping from opscloud4 to keep tech naming consistent
from .opscloud4 import _TECH_ALIAS_TO_CANONICAL, _TECH_NAME_PATTERN


def _normalize(name: str) -> Optional[str]:
    s = (name or "").strip().lower()
    if s in _TECH_ALIAS_TO_CANONICAL:
        return _TECH_ALIAS_TO_CANONICAL[s]
    m = _TECH_NAME_PATTERN.search(s)
    return _TECH_ALIAS_TO_CANONICAL.get(m.group(1).lower()) if m else None


def identify_tech(service_name: str, tags: list[str], meta: dict) -> tuple[Optional[str], str, str]:
    """Returns (tech, confidence, signal). First-match-wins like opscloud4."""
    # ① tag k=v
    for t in tags or []:
        if "=" in t:
            k, _, v = t.partition("=")
            if k.lower() in ("tech", "app", "service_kind", "kind"):
                tech = _normalize(v)
                if tech:
                    return tech, "high", f"tag:{k}={v}"

    # ② meta
    for key in ("tech", "app", "service_kind", "kind", "middleware"):
        v = (meta or {}).get(key)
        if v:
            tech = _normalize(v)
            if tech:
                return tech, "high", f"meta:{key}={v}"

    # ③ service name regex match
    tech = _normalize(service_name)
    if tech:
        return tech, "low", f"service_name:{service_name}"

    return None, "unknown", "none"


def build_consul_cluster(service_name: str, instances: list[dict],
                        tech: str, confidence: str, signal: str) -> Cluster:
    cid = f"consul/{service_name}"
    hosts: list[Host] = []
    for entry in instances:
        node = entry.get("Node") or {}
        svc = entry.get("Service") or {}
        checks = entry.get("Checks") or []
        passing = all(c.get("Status") == "passing" for c in checks)
        hosts.append(Host(
            fqdn=node.get("Node", svc.get("Address", "")),
            address=svc.get("Address") or node.get("Address"),
            port=svc.get("Port"),
            role=svc.get("Tags", [None])[0] if svc.get("Tags") else "node",
            tags={k: str(v) for k, v in (svc.get("Meta") or {}).items()},
            cluster_id=cid,
            health="passing" if passing else "failing",
        ))
    return Cluster(
        id=cid, tech=tech, hosts=hosts,
        discovery_source="consul",
        metadata={
            "service": service_name,
            "tech_confidence": confidence, "tech_signal": signal,
        },
    )


class ConsulAdapter:
    def __init__(self, base_url: str, token: Optional[str] = None,
                 datacenter: Optional[str] = None, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if token:
            self.session.headers["X-Consul-Token"] = token
        self.datacenter = datacenter
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "ConsulAdapter":
        return cls(
            base_url=os.environ.get("CONSUL_URL", "http://localhost:8500"),
            token=os.environ.get("CONSUL_TOKEN"),
            datacenter=os.environ.get("CONSUL_DATACENTER"),
            timeout=int(os.environ.get("CONSUL_TIMEOUT", "10")),
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        clusters: list[Cluster] = []
        params = {"dc": self.datacenter} if self.datacenter else None

        # 1) Catalog list of services + their tags
        resp = self.session.get(f"{self.base_url}/v1/catalog/services",
                                params=params, timeout=self.timeout)
        resp.raise_for_status()
        services_with_tags = resp.json()  # {"service-name": ["tag1", "tag2"], ...}

        for svc_name, tags in services_with_tags.items():
            # 2) Pull instances + their meta
            inst_resp = self.session.get(
                f"{self.base_url}/v1/health/service/{svc_name}",
                params={**(params or {}), "passing": 0},
                timeout=self.timeout,
            )
            if inst_resp.status_code != 200:
                continue
            instances = inst_resp.json()
            # Get meta from first instance (assuming uniform)
            meta = (instances[0].get("Service", {}).get("Meta") if instances else {}) or {}
            tech, confidence, signal = identify_tech(svc_name, tags, meta)
            if not tech:
                continue
            if tech_filter and tech != tech_filter:
                continue
            clusters.append(build_consul_cluster(svc_name, instances,
                                                 tech, confidence, signal))
        return clusters
