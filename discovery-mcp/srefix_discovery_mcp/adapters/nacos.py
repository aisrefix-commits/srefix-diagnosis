"""Nacos service registry + config adapter.

Endpoints (Nacos 2.x):
  GET /nacos/v1/ns/catalog/services?pageNo=1&pageSize=100[&namespaceId=...]
       → list services in a namespace
  GET /nacos/v1/ns/instance/list?serviceName=<svc>&namespaceId=<ns>
       → instances of a service

Tech identification (per-service):
  ① service metadata 'tech' / 'app' / 'middleware'
  ② instance metadata
  ③ service name regex
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


def identify_tech(service_name: str, service_meta: dict,
                  instances: list[dict]) -> Optional[tuple[str, str, str]]:
    # ① service metadata
    for k in ("tech", "app", "middleware", "kind"):
        v = (service_meta or {}).get(k)
        if v:
            tech = _normalize(v)
            if tech:
                return tech, "high", f"meta:{k}={v}"
    # ② instance metadata (any one)
    for inst in instances:
        for k in ("tech", "app", "middleware", "kind"):
            v = (inst.get("metadata") or {}).get(k)
            if v:
                tech = _normalize(v)
                if tech:
                    return tech, "high", f"instance_meta:{k}={v}"
    # ③ name regex
    tech = _normalize(service_name)
    if tech:
        return tech, "low", f"service_name:{service_name}"
    return None


def build_nacos_cluster(service_name: str, namespace: str,
                       instances: list[dict],
                       tech: str, confidence: str, signal: str) -> Cluster:
    cid = f"nacos/{namespace}/{service_name}"
    hosts: list[Host] = []
    for inst in instances:
        meta = inst.get("metadata") or {}
        hosts.append(Host(
            fqdn=inst.get("ip", ""),
            address=inst.get("ip"),
            port=inst.get("port"),
            role=meta.get("role") or inst.get("clusterName", "instance"),
            tags={"weight": str(inst.get("weight", 1.0)),
                  "healthy": str(inst.get("healthy", True)),
                  "ephemeral": str(inst.get("ephemeral", True)),
                  **{k: str(v) for k, v in meta.items()}},
            cluster_id=cid,
            health="healthy" if inst.get("healthy") else "unhealthy",
        ))
    return Cluster(
        id=cid, tech=tech, hosts=hosts,
        discovery_source="nacos",
        metadata={"service": service_name, "namespace": namespace,
                  "tech_confidence": confidence, "tech_signal": signal},
    )


class NacosAdapter:
    def __init__(self, base_url: str, namespaces: Optional[list[str]] = None,
                 username: Optional[str] = None, password: Optional[str] = None,
                 timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.namespaces = namespaces or [""]  # "" = public namespace
        self.session = requests.Session()
        self.username = username
        self.password = password
        self.timeout = timeout
        self._token: Optional[str] = None

    @classmethod
    def from_env(cls) -> "NacosAdapter":
        ns_env = os.environ.get("NACOS_NAMESPACES", "")
        namespaces = [n.strip() for n in ns_env.split(",") if n.strip()] or None
        return cls(
            base_url=os.environ.get("NACOS_URL", ""),
            namespaces=namespaces,
            username=os.environ.get("NACOS_USERNAME"),
            password=os.environ.get("NACOS_PASSWORD"),
        )

    def _login(self) -> Optional[str]:  # pragma: no cover
        if self._token:
            return self._token
        if not (self.username and self.password):
            return None
        try:
            resp = self.session.post(
                f"{self.base_url}/nacos/v1/auth/login",
                data={"username": self.username, "password": self.password},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            self._token = resp.json().get("accessToken")
            return self._token
        except Exception:  # noqa
            return None

    def _params(self, **extra) -> dict:
        params = dict(extra)
        token = self._login()
        if token:
            params["accessToken"] = token
        return params

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.base_url:
            return []
        clusters: list[Cluster] = []
        for ns in self.namespaces:
            try:
                clusters.extend(self._discover_namespace(ns, tech_filter))
            except Exception:  # noqa  # pragma: no cover
                continue
        return clusters

    def _discover_namespace(self, namespace: str,
                           tech_filter: Optional[str]):  # pragma: no cover (network)
        page = 1
        while True:
            params = self._params(pageNo=page, pageSize=100)
            if namespace:
                params["namespaceId"] = namespace
            resp = self.session.get(f"{self.base_url}/nacos/v1/ns/catalog/services",
                                    params=params, timeout=self.timeout)
            if resp.status_code != 200:
                return
            payload = resp.json()
            svc_list = payload.get("serviceList") or []
            if not svc_list:
                return
            for svc in svc_list:
                svc_name = svc.get("name", "")
                if "@@" in svc_name:
                    # Nacos service names look like "DEFAULT_GROUP@@my-svc"
                    pass
                inst_resp = self.session.get(
                    f"{self.base_url}/nacos/v1/ns/instance/list",
                    params=self._params(serviceName=svc_name,
                                        namespaceId=namespace or "public"),
                    timeout=self.timeout,
                )
                if inst_resp.status_code != 200:
                    continue
                inst_data = inst_resp.json()
                instances = inst_data.get("hosts") or []
                ident = identify_tech(svc_name, svc.get("metadata") or {}, instances)
                if not ident:
                    continue
                tech, conf, sig = ident
                if tech_filter and tech != tech_filter:
                    continue
                yield build_nacos_cluster(svc_name, namespace or "public",
                                          instances, tech, conf, sig)
            if len(svc_list) < 100:
                return
            page += 1
