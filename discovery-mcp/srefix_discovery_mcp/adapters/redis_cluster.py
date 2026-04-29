"""Redis Cluster direct-query adapter.

Pattern: "tech is its own registry". For each seed `host:port`, run
`CLUSTER NODES` and parse the cluster topology directly from Redis.

This proves the direct-query pattern; same approach can be applied for:
  MongoDB    → rs.status() / sh.status()
  Cassandra  → system.peers
  Elasticsearch → /_nodes (already covered by es-mcp; could add a discovery wrapper)
  TiDB       → PD /api/v1/members + /pd/api/v1/stores
  etcd       → /v2/members or /v3 alpha
"""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Cluster, Host


def parse_cluster_nodes(output: str) -> list[dict]:
    """Parse the response of `CLUSTER NODES`.

    Each line:
      <node-id> <ip:port>@<bus-port> <flags> <master-or-self> <ping-sent>
      <pong-recv> <config-epoch> <link-state> [<slot> ...]

    Flags include: master / slave / myself / fail / fail? / handshake / noaddr
    """
    nodes: list[dict] = []
    for raw_line in output.strip().splitlines():
        parts = raw_line.split()
        if len(parts) < 8:
            continue
        node_id = parts[0]
        addr_part = parts[1]
        addr_port, _, bus = addr_part.partition("@")
        host, _, port_str = addr_port.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            port = 0
        flags = parts[2].split(",")
        master_id = parts[3]  # "-" if no master (self is master)
        link_state = parts[7]
        slots = parts[8:]

        is_master = "master" in flags
        is_myself = "myself" in flags
        is_failing = any(f in flags for f in ("fail", "fail?"))

        nodes.append({
            "id": node_id, "host": host, "port": port,
            "role": "master" if is_master else "replica",
            "master_id": master_id if not is_master else None,
            "link_state": link_state,
            "is_self": is_myself,
            "failing": is_failing,
            "slot_ranges": slots,
        })
    return nodes


def build_redis_cluster(cluster_id: str, nodes: list[dict]) -> Cluster:
    hosts = [
        Host(
            fqdn=n["host"], address=n["host"], port=n["port"],
            role=n["role"],
            tags={"node_id": n["id"][:12],
                  "link_state": n["link_state"],
                  "slot_count": str(sum(
                      1 + int(r.split("-")[1]) - int(r.split("-")[0])
                      if "-" in r else 1
                      for r in n["slot_ranges"] if "[" not in r
                  ))},
            cluster_id=cluster_id,
            health="failing" if n["failing"] else (n["link_state"] or "unknown"),
        )
        for n in nodes
    ]
    return Cluster(
        id=cluster_id, tech="redis", hosts=hosts,
        discovery_source="redis-cluster",
        metadata={"tech_confidence": "high",
                  "tech_signal": "CLUSTER NODES",
                  "master_count": sum(1 for n in nodes if n["role"] == "master"),
                  "replica_count": sum(1 for n in nodes if n["role"] == "replica")},
    )


class RedisClusterAdapter:
    """Each entry in `seeds` is a 'cluster_name=host:port[,host:port...]' pair."""

    def __init__(self, seeds: dict[str, list[tuple[str, int]]],
                 password: Optional[str] = None,
                 socket_timeout: int = 5):
        self.seeds = seeds
        self.password = password
        self.timeout = socket_timeout

    @classmethod
    def from_env(cls) -> "RedisClusterAdapter":
        # Format: REDIS_CLUSTERS="prod-east=10.0.1.1:7000,10.0.1.2:7000;prod-west=10.0.2.1:7000"
        seeds: dict[str, list[tuple[str, int]]] = {}
        raw = os.environ.get("REDIS_CLUSTERS", "")
        for entry in raw.split(";"):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            cname, _, addrs = entry.partition("=")
            seed_list: list[tuple[str, int]] = []
            for a in addrs.split(","):
                if ":" in a:
                    h, _, p = a.strip().partition(":")
                    try:
                        seed_list.append((h, int(p)))
                    except ValueError:
                        continue
            if seed_list:
                seeds[cname.strip()] = seed_list
        return cls(seeds=seeds, password=os.environ.get("REDIS_PASSWORD"))

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if tech_filter and tech_filter != "redis":
            return []
        clusters: list[Cluster] = []
        for cluster_name, seed_list in self.seeds.items():
            output = self._cluster_nodes_from_first_reachable(seed_list)
            if not output:
                continue
            nodes = parse_cluster_nodes(output)
            if nodes:
                clusters.append(build_redis_cluster(f"redis/{cluster_name}", nodes))
        return clusters

    def _cluster_nodes_from_first_reachable(self, seeds) -> Optional[str]:  # pragma: no cover
        try:
            import redis  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "redis lib not installed; pip install 'srefix-discovery-mcp[redis-cluster]'"
            ) from e
        for host, port in seeds:
            try:
                r = redis.Redis(host=host, port=port, password=self.password,
                                socket_connect_timeout=self.timeout,
                                socket_timeout=self.timeout, decode_responses=True)
                return r.execute_command("CLUSTER", "NODES")
            except Exception:  # noqa
                continue
        return None
