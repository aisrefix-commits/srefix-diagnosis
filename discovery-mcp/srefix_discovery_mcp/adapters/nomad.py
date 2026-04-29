"""HashiCorp Nomad adapter — jobs as logical clusters.

API: GET /v1/jobs / GET /v1/job/<id>/allocations / GET /v1/nodes
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host

# Reuse canonical mapping for tech identification from job name / meta
from .opscloud4 import _TECH_ALIAS_TO_CANONICAL, _TECH_NAME_PATTERN


def _normalize(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip().lower()
    if s in _TECH_ALIAS_TO_CANONICAL:
        return _TECH_ALIAS_TO_CANONICAL[s]
    m = _TECH_NAME_PATTERN.search(s)
    return _TECH_ALIAS_TO_CANONICAL.get(m.group(1).lower()) if m else None


def build_nomad_job_cluster(job: dict, allocs: list[dict],
                            tech: str, signal: str) -> Cluster:
    name = job.get("ID", "job-unknown")
    cid = f"nomad/{name}"
    hosts = [Host(
        fqdn=a.get("NodeName", a.get("NodeID", "")),
        address=a.get("NodeID"), port=None,
        role=a.get("TaskGroup", "task"),
        tags={"client_status": a.get("ClientStatus", ""),
              "job_id": name, "task_group": a.get("TaskGroup", "")},
        cluster_id=cid,
        health=a.get("ClientStatus", "unknown"),
    ) for a in allocs]
    return Cluster(
        id=cid, tech=tech or "nomad", hosts=hosts,
        discovery_source="nomad",
        metadata={"job_status": job.get("Status"),
                  "tech_confidence": "high" if tech else "low",
                  "tech_signal": signal},
    )


def build_nomad_self_cluster(nodes: list[dict]) -> Cluster:
    """The Nomad cluster itself (control + clients)."""
    cid = "nomad/cluster"
    hosts = [Host(
        fqdn=n.get("Name", n.get("ID", "")),
        address=n.get("Address"),
        port=4646,
        role=("server" if n.get("ServerNodeID") else "client"),
        tags={"datacenter": n.get("Datacenter", ""),
              "status": n.get("Status", ""),
              "drain": str(n.get("Drain", False))},
        cluster_id=cid,
        health=n.get("Status", "unknown"),
    ) for n in nodes]
    return Cluster(
        id=cid, tech="nomad", hosts=hosts,
        discovery_source="nomad",
        metadata={"tech_confidence": "high", "tech_signal": "/v1/nodes"},
    )


class NomadAdapter:
    def __init__(self, base_url: str, token: Optional[str] = None,
                 namespace: str = "default", timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if token:
            self.session.headers["X-Nomad-Token"] = token
        self.namespace = namespace
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "NomadAdapter":
        return cls(
            base_url=os.environ.get("NOMAD_ADDR", ""),
            token=os.environ.get("NOMAD_TOKEN"),
            namespace=os.environ.get("NOMAD_NAMESPACE", "default"),
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.base_url:
            return []
        clusters: list[Cluster] = []
        try:  # pragma: no cover (network)
            # Nomad cluster itself
            nodes = self.session.get(f"{self.base_url}/v1/nodes",
                                     params={"namespace": self.namespace},
                                     timeout=self.timeout).json()
            if nodes and (not tech_filter or tech_filter == "nomad"):
                clusters.append(build_nomad_self_cluster(nodes))
            # Jobs as logical app-clusters
            jobs = self.session.get(f"{self.base_url}/v1/jobs",
                                    params={"namespace": self.namespace},
                                    timeout=self.timeout).json()
            for j in jobs:
                jid = j.get("ID")
                tech = _normalize((j.get("Meta") or {}).get("tech")) \
                    or _normalize((j.get("Meta") or {}).get("app")) \
                    or _normalize(jid)
                if not tech:
                    continue
                if tech_filter and tech != tech_filter:
                    continue
                allocs = self.session.get(f"{self.base_url}/v1/job/{jid}/allocations",
                                          params={"namespace": self.namespace},
                                          timeout=self.timeout).json()
                signal = "meta:tech" if (j.get("Meta") or {}).get("tech") \
                         else f"name:{jid}"
                clusters.append(build_nomad_job_cluster(j, allocs, tech, signal))
        except Exception:  # noqa  # pragma: no cover
            pass
        return clusters
