"""Rancher adapter — managed K8s clusters fronting multiple downstream clusters.

API: GET /v3/clusters / GET /v3/nodes
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host


def build_rancher_cluster(c: dict, nodes: list[dict]) -> Cluster:
    cid_suffix = c.get("id", "rancher-unknown")
    cid = f"rancher/{cid_suffix}"
    hosts = [Host(
        fqdn=n.get("hostname", ""), address=n.get("ipAddress"),
        role=",".join([
            r for r in ("controlplane", "etcd", "worker") if n.get(r)
        ]) or "node",
        tags={"cluster": cid_suffix, "state": n.get("state", "")},
        cluster_id=cid,
        health=n.get("state", "unknown"),
    ) for n in nodes if n.get("clusterId") == cid_suffix]
    return Cluster(
        id=cid, tech="rancher",
        version=c.get("rancherKubernetesEngineConfig", {}).get("kubernetesVersion"),
        hosts=hosts, discovery_source="rancher",
        metadata={"name": c.get("name"),
                  "state": c.get("state"),
                  "tech_confidence": "high",
                  "tech_signal": "rancher-managed-cluster"},
    )


class RancherAdapter:
    def __init__(self, base_url: str, token: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "RancherAdapter":
        return cls(
            base_url=os.environ.get("RANCHER_URL", ""),
            token=os.environ.get("RANCHER_TOKEN", ""),
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not (self.base_url and self.session.headers.get("Authorization")):
            return []
        if tech_filter and tech_filter not in ("rancher", "kubernetes", "k8s"):
            return []
        try:  # pragma: no cover (network)
            cl_resp = self.session.get(f"{self.base_url}/v3/clusters",
                                       timeout=self.timeout)
            cl_resp.raise_for_status()
            n_resp = self.session.get(f"{self.base_url}/v3/nodes",
                                      timeout=self.timeout)
            nodes = n_resp.json().get("data", []) if n_resp.status_code == 200 else []
            return [build_rancher_cluster(c, nodes)
                    for c in cl_resp.json().get("data", [])]
        except Exception:  # noqa  # pragma: no cover
            return []
