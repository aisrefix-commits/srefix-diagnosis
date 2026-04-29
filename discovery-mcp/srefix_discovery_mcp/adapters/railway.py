"""Railway adapter — projects + plugins (Postgres / MySQL / Redis / MongoDB).

Uses Railway's GraphQL API at https://backboard.railway.app/graphql/v2.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host

_PLUGIN_TO_TECH = {
    "postgresql": "postgres", "postgres": "postgres",
    "mysql": "mysql",
    "redis": "redis",
    "mongodb": "mongo", "mongo": "mongo",
}


def build_railway_plugin_cluster(project_name: str, plugin: dict) -> Optional[Cluster]:
    plugin_name = (plugin.get("name") or "").lower()
    tech = _PLUGIN_TO_TECH.get(plugin_name)
    if not tech:
        return None
    pid = plugin.get("id", "")
    cid = f"railway/{project_name}/{plugin_name}/{pid}"
    return Cluster(
        id=cid, tech=tech, version=None,
        hosts=[Host(
            fqdn=plugin_name, role="primary",
            tags={"project": project_name, "plugin_id": pid,
                  "status": plugin.get("status", "")},
            cluster_id=cid, health=plugin.get("status", "unknown"),
        )],
        discovery_source="railway",
        metadata={"project": project_name, "plugin": plugin_name,
                  "tech_confidence": "high", "tech_signal": f"railway-plugin:{plugin_name}"},
    )


class RailwayAdapter:
    GQL = "https://backboard.railway.app/graphql/v2"

    def __init__(self, token: str, timeout: int = 15):
        self.token = token
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "RailwayAdapter":
        return cls(token=os.environ.get("RAILWAY_TOKEN", ""))

    def _query(self, q: str) -> dict:  # pragma: no cover (network)
        resp = requests.post(
            self.GQL, json={"query": q},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}) or {}

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.token:
            return []
        clusters = []
        try:  # pragma: no cover
            data = self._query("""
            { me { projects { edges { node {
                 id name plugins { edges { node { id name status } } }
            } } } } }""")
            for edge in (data.get("me", {}).get("projects", {}).get("edges") or []):
                proj = edge.get("node") or {}
                proj_name = proj.get("name", "")
                for p_edge in (proj.get("plugins", {}).get("edges") or []):
                    plugin = p_edge.get("node") or {}
                    cluster = build_railway_plugin_cluster(proj_name, plugin)
                    if cluster and (not tech_filter or cluster.tech == tech_filter):
                        clusters.append(cluster)
        except Exception:  # noqa
            pass
        return clusters
