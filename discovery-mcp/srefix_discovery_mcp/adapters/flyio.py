"""Fly.io adapter — apps (incl. fly-postgres clusters).

Uses Fly's GraphQL API at https://api.fly.io/graphql.
Postgres apps appear as `app.role.name == 'postgres_cluster'` (Fly clusters
Postgres via stolon — multi-node with leader/replica roles).
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host


def _query(token: str, q: str) -> dict:  # pragma: no cover
    resp = requests.post(
        "https://api.fly.io/graphql",
        json={"query": q},
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


def build_fly_postgres_cluster(app: dict) -> Optional[Cluster]:
    name = app.get("name", "")
    cid = f"flyio/{name}"
    machines = (app.get("machines") or {}).get("nodes") or []
    hosts = []
    for m in machines:
        # Fly Postgres machines have role in metadata or env
        meta = (m.get("config") or {}).get("metadata", {}) or {}
        role = meta.get("role") or m.get("region", "node")
        hosts.append(Host(
            fqdn=m.get("name", ""), address=m.get("ips", {}).get("ipv6"),
            port=5432, role=role,
            tags={"region": m.get("region", ""),
                  "state": m.get("state", "")},
            cluster_id=cid,
            health=m.get("state", "unknown"),
        ))
    if not hosts:
        return None
    return Cluster(
        id=cid, tech="postgres", hosts=hosts,
        discovery_source="flyio",
        metadata={"app_name": name, "tech_confidence": "high",
                  "tech_signal": "fly-postgres-app"},
    )


class FlyIOAdapter:
    def __init__(self, token: str, organization: Optional[str] = None):
        self.token = token
        self.organization = organization

    @classmethod
    def from_env(cls) -> "FlyIOAdapter":
        return cls(token=os.environ.get("FLY_API_TOKEN", ""),
                   organization=os.environ.get("FLY_ORGANIZATION"))

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.token:
            return []
        if tech_filter and tech_filter != "postgres":
            return []
        clusters: list[Cluster] = []
        try:  # pragma: no cover (network)
            org_filter = (f', organization: "{self.organization}"'
                          if self.organization else "")
            data = _query(self.token, f"""
            {{ apps(role: "postgres_cluster"{org_filter}, first: 100) {{
                 nodes {{ name organization {{ slug }}
                          machines {{ nodes {{ id name region state ips {{ ipv6 }}
                                                config }} }} }} }} }}""")
            for app in data.get("apps", {}).get("nodes", []):
                cluster = build_fly_postgres_cluster(app)
                if cluster:
                    clusters.append(cluster)
        except Exception:  # noqa
            pass
        return clusters
