"""Elasticsearch / OpenSearch direct-query discovery adapter.

Fetches cluster topology from `/_nodes` and `/_cluster/health`.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host


def build_es_cluster(cluster_name: str, info: dict, nodes: dict, health: dict) -> Cluster:
    cid = f"elasticsearch/{cluster_name}"
    version = info.get("version", {}).get("number") or ""
    distribution = info.get("version", {}).get("distribution", "")
    tech = "opensearch" if distribution == "opensearch" else "elasticsearch"
    hosts = []
    for nid, n in (nodes.get("nodes") or {}).items():
        roles = n.get("roles", [])
        # Pick "primary" role label: master / data / ingest / coord
        if "master" in roles:
            role = "master-eligible"
        elif "data" in roles or any(r.startswith("data") for r in roles):
            role = "data"
        elif "ingest" in roles:
            role = "ingest"
        else:
            role = "coordinator"
        hosts.append(Host(
            fqdn=n.get("name", nid),
            address=n.get("ip"),
            port=int(str(n.get("http", {}).get("publish_address", "0:9200"))
                     .rsplit(":", 1)[-1]) if n.get("http") else 9200,
            role=role,
            tags={"version": n.get("version", ""),
                  "node_id": nid[:16],
                  "all_roles": ",".join(roles)},
            cluster_id=cid,
        ))
    return Cluster(
        id=cid, tech=tech, version=version, hosts=hosts,
        discovery_source="elasticsearch-direct",
        metadata={"cluster_name": cluster_name,
                  "status": health.get("status"),
                  "number_of_nodes": health.get("number_of_nodes"),
                  "active_shards": health.get("active_shards"),
                  "tech_confidence": "high",
                  "tech_signal": "/_nodes"},
    )


class ElasticsearchAdapter:
    def __init__(self, endpoints: dict[str, str], api_key: Optional[str] = None,
                 username: Optional[str] = None, password: Optional[str] = None,
                 verify_tls: bool = True, timeout: int = 10):
        self.endpoints = endpoints
        self.session = requests.Session()
        self.session.verify = verify_tls
        if api_key:
            self.session.headers["Authorization"] = f"ApiKey {api_key}"
        elif username and password:
            self.session.auth = (username, password)
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "ElasticsearchAdapter":
        # Format: ES_DISCOVERY_ENDPOINTS="prod-east=https://es.prod:9200;analytics=https://es.analytics:9200"
        eps: dict[str, str] = {}
        for entry in os.environ.get("ES_DISCOVERY_ENDPOINTS", "").split(";"):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            name, _, url = entry.partition("=")
            eps[name.strip()] = url.strip().rstrip("/")
        return cls(
            endpoints=eps,
            api_key=os.environ.get("ES_API_KEY"),
            username=os.environ.get("ES_USERNAME"),
            password=os.environ.get("ES_PASSWORD"),
            verify_tls=os.environ.get("ES_VERIFY_TLS", "1") != "0",
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if tech_filter and tech_filter not in ("elasticsearch", "opensearch"):
            return []
        clusters: list[Cluster] = []
        for name, url in self.endpoints.items():
            try:
                info = self.session.get(f"{url}/", timeout=self.timeout).json()
                nodes = self.session.get(f"{url}/_nodes", timeout=self.timeout).json()
                health = self.session.get(f"{url}/_cluster/health", timeout=self.timeout).json()
            except Exception:  # noqa  # pragma: no cover
                continue
            c = build_es_cluster(name, info, nodes, health)
            if tech_filter and c.tech != tech_filter:
                continue
            clusters.append(c)
        return clusters
