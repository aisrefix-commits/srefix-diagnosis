"""DigitalOcean managed-services adapter.

Services covered:
  Managed Databases  → tech: postgres / mysql / redis / mongo (per engine)
  DOKS (Kubernetes)  → tech: kubernetes (one cluster, members are nodes)

Auth: DIGITALOCEAN_TOKEN env. Uses bare HTTP (no SDK required).
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host

_DB_ENGINE_MAP = {
    "pg": "postgres",
    "mysql": "mysql",
    "redis": "redis",
    "mongodb": "mongo",
    "kafka": "kafka",
    "opensearch": "opensearch",
}


def build_do_db_cluster(db: dict) -> Optional[Cluster]:
    engine = db.get("engine", "")
    tech = _DB_ENGINE_MAP.get(engine)
    if not tech:
        return None
    name = db.get("name", "do-db-unknown")
    cid = f"digitalocean/db/{name}"
    conn = db.get("connection") or {}
    hosts: list[Host] = []
    if conn.get("host"):
        hosts.append(Host(
            fqdn=conn.get("host", ""), address=conn.get("host"),
            port=conn.get("port"), role="primary",
            tags={"region": db.get("region", ""),
                  "size": db.get("size", "")},
            cluster_id=cid,
            health=db.get("status", "unknown"),
        ))
    # Replica nodes (read-only)
    for r in db.get("standby_connections", []) or []:
        hosts.append(Host(
            fqdn=r.get("host", ""), address=r.get("host"),
            port=r.get("port"), role="replica",
            tags={"region": db.get("region", "")},
            cluster_id=cid,
        ))
    return Cluster(
        id=cid, tech=tech, version=db.get("version"),
        hosts=hosts, discovery_source="digitalocean",
        metadata={"region": db.get("region"),
                  "tech_confidence": "high",
                  "tech_signal": f"do-db:{engine}"},
    )


def build_do_droplets_classified(droplets: list[dict]) -> list[Cluster]:
    """Tag-aware Droplet grouping.

    DigitalOcean tags are flat strings (no key-value), so the convention
    here is `service:hbase`, `cluster:hbase-prod`, `role:master` (with `:` as
    the separator). Bare tags become {tag: tag}.
    """
    from ._classify import group_instances_into_clusters, normalize_do_tags
    return group_instances_into_clusters(
        droplets,
        tag_extractor=lambda d: normalize_do_tags(d.get("tags")),
        fqdn_extractor=lambda d: d.get("name", ""),
        instance_id_extractor=lambda d: str(d.get("id", "do-unknown")),
        cluster_id_prefix="digitalocean",
        discovery_source="do-droplet-tagged",
        region=(droplets[0].get("region", {}) or {}).get("slug", "") if droplets else "",
        default_tech="droplet",
        extra_host_tags=lambda d: {
            "size": (d.get("size_slug") or ""),
            "region": (d.get("region", {}) or {}).get("slug", ""),
            "image": (d.get("image", {}) or {}).get("slug", ""),
            "status": d.get("status", ""),
        },
    )


def build_do_kubernetes_cluster(cluster: dict, nodes_by_pool: dict) -> Cluster:
    name = cluster.get("name", "doks-unknown")
    cid = f"digitalocean/doks/{name}"
    hosts: list[Host] = []
    for pool_name, nodes in nodes_by_pool.items():
        for n in nodes:
            hosts.append(Host(
                fqdn=n.get("name", ""),
                role=f"node-pool:{pool_name}",
                tags={"region": cluster.get("region", ""),
                      "size": n.get("size", ""),
                      "status": n.get("status", {}).get("state", "")},
                cluster_id=cid,
                health=n.get("status", {}).get("state", "unknown"),
            ))
    return Cluster(
        id=cid, tech="kubernetes",
        version=cluster.get("version"),
        hosts=hosts, discovery_source="digitalocean",
        metadata={"region": cluster.get("region"),
                  "tech_confidence": "high", "tech_signal": "doks"},
    )


class DigitalOceanAdapter:
    BASE = "https://api.digitalocean.com/v2"

    def __init__(self, token: str, services: Optional[list[str]] = None,
                 timeout: int = 15):
        self.token = token
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.services = set(services or ["databases", "kubernetes"])
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "DigitalOceanAdapter":
        services = [s.strip() for s in os.environ.get("DIGITALOCEAN_SERVICES", "").split(",")
                    if s.strip()] or None
        return cls(token=os.environ.get("DIGITALOCEAN_TOKEN", ""), services=services)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.token:
            return []
        clusters: list[Cluster] = []
        if "databases" in self.services:
            clusters.extend(self._discover_databases())
        if "kubernetes" in self.services:
            clusters.extend(self._discover_kubernetes())
        if tech_filter:
            clusters = [c for c in clusters if c.tech == tech_filter]
        return clusters

    def _discover_databases(self):  # pragma: no cover (network)
        try:
            resp = self.session.get(f"{self.BASE}/databases", timeout=self.timeout)
            resp.raise_for_status()
            for db in resp.json().get("databases", []):
                cluster = build_do_db_cluster(db)
                if cluster:
                    yield cluster
        except Exception:  # noqa
            return

    def _discover_kubernetes(self):  # pragma: no cover (network)
        try:
            resp = self.session.get(f"{self.BASE}/kubernetes/clusters", timeout=self.timeout)
            resp.raise_for_status()
            for cluster in resp.json().get("kubernetes_clusters", []):
                cluster_id = cluster.get("id")
                # Pull node pools + nodes
                nodes_by_pool = {}
                try:
                    pools_resp = self.session.get(
                        f"{self.BASE}/kubernetes/clusters/{cluster_id}/node_pools",
                        timeout=self.timeout,
                    )
                    pools_resp.raise_for_status()
                    for pool in pools_resp.json().get("node_pools", []):
                        nodes_by_pool[pool.get("name", "")] = pool.get("nodes", [])
                except Exception:  # noqa
                    pass
                yield build_do_kubernetes_cluster(cluster, nodes_by_pool)
        except Exception:  # noqa
            return
