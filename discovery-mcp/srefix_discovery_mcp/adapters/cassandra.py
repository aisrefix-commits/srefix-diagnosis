"""Cassandra direct-query adapter — `system.peers` + `system.local`.

For each seed, queries:
  SELECT * FROM system.local        the contact node
  SELECT * FROM system.peers        all other nodes in the cluster
"""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Cluster, Host


def build_cassandra_cluster(cluster_name: str, contact: dict, peers: list[dict]) -> Cluster:
    cid = f"cassandra/{cluster_name}"
    hosts: list[Host] = []
    seen_addrs: set[str] = set()
    if contact:
        seen_addrs.add(contact.get("broadcast_address") or contact.get("listen_address") or "")
        hosts.append(Host(
            fqdn=contact.get("rpc_address") or contact.get("broadcast_address") or "",
            address=contact.get("rpc_address") or contact.get("broadcast_address"),
            port=9042, role="contact",
            tags={"dc": str(contact.get("data_center", "")),
                  "rack": str(contact.get("rack", "")),
                  "release_version": str(contact.get("release_version", ""))},
            cluster_id=cid,
        ))
    for p in peers:
        addr = p.get("peer") or p.get("rpc_address")
        if not addr or addr in seen_addrs:
            continue
        seen_addrs.add(addr)
        hosts.append(Host(
            fqdn=str(addr), address=str(addr), port=9042, role="peer",
            tags={"dc": str(p.get("data_center", "")),
                  "rack": str(p.get("rack", "")),
                  "release_version": str(p.get("release_version", ""))},
            cluster_id=cid,
        ))
    return Cluster(
        id=cid, tech="cassandra", hosts=hosts,
        discovery_source="cassandra-direct",
        metadata={"tech_confidence": "high", "tech_signal": "system.peers",
                  "node_count": len(hosts)},
    )


class CassandraAdapter:
    """seeds: {cluster_name: ['host1', 'host2', ...]} — first reachable wins."""

    def __init__(self, seeds: dict[str, list[str]], port: int = 9042,
                 username: Optional[str] = None, password: Optional[str] = None):
        self.seeds = seeds
        self.port = port
        self.username = username
        self.password = password

    @classmethod
    def from_env(cls) -> "CassandraAdapter":
        # Format: CASSANDRA_CLUSTERS="prod=10.0.1.1,10.0.1.2;analytics=10.0.5.1"
        seeds: dict[str, list[str]] = {}
        for entry in os.environ.get("CASSANDRA_CLUSTERS", "").split(";"):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            name, _, hosts = entry.partition("=")
            seeds[name.strip()] = [h.strip() for h in hosts.split(",") if h.strip()]
        return cls(
            seeds=seeds,
            port=int(os.environ.get("CASSANDRA_PORT", "9042")),
            username=os.environ.get("CASSANDRA_USERNAME"),
            password=os.environ.get("CASSANDRA_PASSWORD"),
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if tech_filter and tech_filter != "cassandra":
            return []
        clusters: list[Cluster] = []
        for name, hosts in self.seeds.items():
            contact, peers = self._query(hosts)
            if contact or peers:
                clusters.append(build_cassandra_cluster(name, contact, peers))
        return clusters

    def _query(self, seeds: list[str]) -> tuple[dict, list[dict]]:  # pragma: no cover
        try:
            from cassandra.auth import PlainTextAuthProvider  # type: ignore
            from cassandra.cluster import Cluster as CCluster  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "cassandra-driver not installed; pip install 'srefix-discovery-mcp[cassandra]'"
            ) from e
        auth = PlainTextAuthProvider(self.username, self.password) if self.username else None
        try:
            ccluster = CCluster(seeds, port=self.port, auth_provider=auth,
                                control_connection_timeout=5)
            session = ccluster.connect()
            local_rows = list(session.execute("SELECT * FROM system.local"))
            peer_rows = list(session.execute("SELECT * FROM system.peers"))
            session.shutdown()
            ccluster.shutdown()
            contact = dict(local_rows[0]._asdict()) if local_rows else {}
            peers = [dict(r._asdict()) for r in peer_rows]
            return contact, peers
        except Exception:  # noqa
            return {}, []
