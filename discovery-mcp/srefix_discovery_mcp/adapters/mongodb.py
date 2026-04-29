"""MongoDB direct-query adapter — replica set + sharded cluster topology.

For each seed URI, runs:
  - rs.status()  → replica set member list with state (PRIMARY/SECONDARY/ARBITER)
  - sh.status() → sharded cluster topology (mongos / config server / shard members)
"""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Cluster, Host

_STATE_TO_ROLE = {
    1: "primary",
    2: "secondary",
    3: "recovering",
    5: "startup",
    7: "arbiter",
    8: "down",
    9: "rollback",
    10: "removed",
}


def parse_replica_set(rs_status: dict, cluster_id: str) -> Cluster:
    members = rs_status.get("members") or []
    set_name = rs_status.get("set", "rs")
    hosts = []
    for m in members:
        host_str = m.get("name", "")
        h, _, p = host_str.partition(":")
        try:
            port = int(p)
        except ValueError:
            port = 27017
        role = _STATE_TO_ROLE.get(m.get("state"), str(m.get("stateStr", "unknown")).lower())
        hosts.append(Host(
            fqdn=h, address=h, port=port, role=role,
            tags={"set": set_name, "health": str(m.get("health", "?"))},
            cluster_id=cluster_id,
            health="up" if m.get("health") == 1 else "down",
        ))
    return Cluster(
        id=cluster_id, tech="mongo", hosts=hosts,
        discovery_source="mongodb-rs",
        metadata={"set_name": set_name,
                  "tech_confidence": "high",
                  "tech_signal": "rs.status()"},
    )


def parse_sharded_cluster(sh_status: dict, cluster_id: str) -> list[Cluster]:
    """sharded cluster → emit one Cluster for the whole sharded mongos layer +
    one Cluster per shard's replica-set."""
    clusters: list[Cluster] = []
    # 1) mongos / config-server cluster
    shards = sh_status.get("shards") or []
    config = sh_status.get("config") or {}
    mongos_hosts: list[Host] = []
    for cfg_member in config.get("members", []):
        name = cfg_member.get("name", "")
        h, _, p = name.partition(":")
        mongos_hosts.append(Host(
            fqdn=h, address=h, port=int(p) if p.isdigit() else 27019,
            role="config-server",
            cluster_id=f"{cluster_id}/sharded",
            health="up" if cfg_member.get("health") == 1 else "down",
        ))
    if mongos_hosts:
        clusters.append(Cluster(
            id=f"{cluster_id}/sharded", tech="mongo", hosts=mongos_hosts,
            discovery_source="mongodb-sharded",
            metadata={"role": "sharded-cluster-meta",
                      "shard_count": len(shards),
                      "tech_confidence": "high",
                      "tech_signal": "sh.status()"},
        ))
    # 2) per-shard replica sets
    for shard in shards:
        shard_id = shard.get("_id", "")
        rs_uri = shard.get("host", "")  # e.g. "rs0/host1:27017,host2:27017"
        if "/" in rs_uri:
            rs_name, _, members_str = rs_uri.partition("/")
            members = members_str.split(",")
            hosts = []
            for m in members:
                h, _, p = m.partition(":")
                hosts.append(Host(
                    fqdn=h, address=h, port=int(p) if p.isdigit() else 27017,
                    role="member",
                    tags={"shard": shard_id, "rs": rs_name},
                    cluster_id=f"{cluster_id}/shard/{shard_id}",
                ))
            clusters.append(Cluster(
                id=f"{cluster_id}/shard/{shard_id}", tech="mongo",
                hosts=hosts, discovery_source="mongodb-sharded",
                metadata={"shard_id": shard_id, "rs_name": rs_name,
                          "tech_confidence": "high",
                          "tech_signal": "sh.status()"},
            ))
    return clusters


class MongoDBAdapter:
    """seeds: {cluster_name: 'mongodb://host1:27017,host2:27017/?replicaSet=rs0'}"""

    def __init__(self, seeds: dict[str, str], timeout: int = 5):
        self.seeds = seeds
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "MongoDBAdapter":
        # Format: MONGODB_CLUSTERS="prod-east=mongodb://h1:27017,h2:27017/?replicaSet=rs0;analytics=mongodb://..."
        seeds: dict[str, str] = {}
        for entry in os.environ.get("MONGODB_CLUSTERS", "").split(";"):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            name, _, uri = entry.partition("=")
            seeds[name.strip()] = uri.strip()
        return cls(seeds=seeds)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if tech_filter and tech_filter != "mongo":
            return []
        clusters: list[Cluster] = []
        for name, uri in self.seeds.items():
            try:
                from pymongo import MongoClient  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "pymongo not installed; pip install 'srefix-discovery-mcp[mongo]'"
                ) from e
            try:  # pragma: no cover (network)
                cli = MongoClient(uri, serverSelectionTimeoutMS=self.timeout * 1000)
                # Try sharded first
                try:
                    sh = cli.admin.command("listShards")
                    if sh.get("ok") == 1 and sh.get("shards"):
                        clusters.extend(parse_sharded_cluster(sh, f"mongo/{name}"))
                        continue
                except Exception:  # noqa
                    pass
                # Replica set
                rs = cli.admin.command("replSetGetStatus")
                clusters.append(parse_replica_set(rs, f"mongo/{name}"))
            except Exception:  # noqa  # pragma: no cover
                continue
        return clusters
