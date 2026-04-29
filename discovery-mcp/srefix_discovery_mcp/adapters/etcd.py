"""etcd direct-query discovery adapter.

Hits the etcd v3 HTTP gRPC-gateway endpoint /v3/cluster/member/list.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host


def build_etcd_cluster(cluster_name: str, members: list[dict]) -> Cluster:
    cid = f"etcd/{cluster_name}"
    hosts = []
    for m in members:
        peer_urls = m.get("peerURLs") or []
        client_urls = m.get("clientURLs") or []
        primary_url = client_urls[0] if client_urls else (peer_urls[0] if peer_urls else "")
        # Strip scheme for fqdn
        fqdn = primary_url.split("://", 1)[-1].rsplit(":", 1)[0]
        port = int(primary_url.rsplit(":", 1)[-1]) if ":" in primary_url else 2379
        hosts.append(Host(
            fqdn=fqdn, address=fqdn, port=port,
            role="leader" if m.get("isLeader") else "member",
            tags={"member_id": m.get("ID", ""),
                  "name": m.get("name", "")},
            cluster_id=cid,
        ))
    return Cluster(
        id=cid, tech="etcd", hosts=hosts,
        discovery_source="etcd-direct",
        metadata={"member_count": len(members),
                  "tech_confidence": "high",
                  "tech_signal": "/v3/cluster/member/list"},
    )


class EtcdAdapter:
    def __init__(self, endpoints: dict[str, str],
                 cert: Optional[str] = None, key: Optional[str] = None,
                 ca: Optional[str] = None, timeout: int = 5):
        self.endpoints = endpoints
        self.session = requests.Session()
        if cert and key:
            self.session.cert = (cert, key)
        if ca:
            self.session.verify = ca
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "EtcdAdapter":
        # Format: ETCD_CLUSTERS="k8s-prod=https://etcd-1:2379;app-prod=http://etcd-2:2379"
        eps: dict[str, str] = {}
        for entry in os.environ.get("ETCD_CLUSTERS", "").split(";"):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            name, _, url = entry.partition("=")
            eps[name.strip()] = url.strip().rstrip("/")
        return cls(endpoints=eps,
                   cert=os.environ.get("ETCD_CERT"),
                   key=os.environ.get("ETCD_KEY"),
                   ca=os.environ.get("ETCD_CA"))

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if tech_filter and tech_filter != "etcd":
            return []
        clusters = []
        for name, url in self.endpoints.items():
            try:
                resp = self.session.post(
                    f"{url}/v3/cluster/member/list", json={}, timeout=self.timeout
                )
                resp.raise_for_status()
                payload = resp.json()
                members = payload.get("members") or []
                # Determine leader via /v3/maintenance/status (per-member; not all etcds expose it)
                # Best-effort: call /v3/maintenance/status on each member's first clientURL
                leader_id = None
                for m in members:
                    cu = (m.get("clientURLs") or [None])[0]
                    if not cu:
                        continue
                    try:
                        s = self.session.post(f"{cu}/v3/maintenance/status",
                                              json={}, timeout=2).json()
                        if s.get("leader"):
                            leader_id = s.get("leader")
                            break
                    except Exception:  # noqa
                        continue
                for m in members:
                    m["isLeader"] = m.get("ID") == leader_id
                clusters.append(build_etcd_cluster(name, members))
            except Exception:  # noqa  # pragma: no cover
                continue
        return clusters
