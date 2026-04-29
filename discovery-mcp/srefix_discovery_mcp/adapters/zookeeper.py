"""Discover clusters via ZooKeeper. Targets HBase + Kafka legacy + Solr + HDFS HA.

Per ZK quorum, walks well-known znode paths and parses the live data:

  HBase:
    /hbase/master                       active HMaster (protobuf ServerName)
    /hbase/backup-masters/<server>      standby HMaster znodes (name = ServerName)
    /hbase/rs/<server>                  RegionServer znodes (name = ServerName)
                                        format: "host,port,startcode"

  Kafka (legacy ZK mode):
    /kafka/brokers/ids                  broker ID list
    /kafka/brokers/ids/<id>             JSON: {host, port, endpoints, ...}

  Solr:
    /solr/live_nodes/<host:port_solr>   live Solr nodes (name encodes endpoint)

  HDFS HA:
    /hadoop-ha/<nameservice>/ActiveStandbyElectorLock         active NN lock
    /hadoop-ha/<nameservice>/ActiveBreadCrumb                 active NN breadcrumb

The adapter returns one Cluster per (quorum, tech) pair, with all member hosts.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from ..core.models import Cluster, Host

# Layout configurable per quorum. Default = upstream conventions.
_DEFAULT_LAYOUTS = {
    "hbase": {"prefix": "/hbase"},
    "kafka": {"prefix": "/kafka"},
    "solr":  {"prefix": "/solr"},
    "hdfs":  {"prefix": "/hadoop-ha"},
}


def _parse_hbase_servername_znode_name(name: str) -> Optional[tuple[str, int]]:
    """RegionServer / backup-master znode names are 'host,port,starttime'."""
    parts = name.split(",")
    if len(parts) >= 2:
        try:
            return parts[0], int(parts[1])
        except ValueError:
            return None
    return None


def _parse_hbase_master_data(data: bytes) -> Optional[tuple[str, int]]:
    """Active HMaster's ZK data is a protobuf-encoded ServerName.

    Format: 4 bytes magic 0xFFFFFFFF + 'PBUF' + ServerName proto
        ServerName fields:
          field 1 (host_name, length-delimited string)  → tag byte 0x0a
          field 2 (port, varint)                         → tag byte 0x10
          field 3 (start_code, varint)                   → tag byte 0x18
    """
    pbuf = data.find(b"PBUF")
    if pbuf < 0:
        return None
    cursor = pbuf + 4
    if cursor >= len(data) or data[cursor] != 0x0A:
        return None
    cursor += 1
    if cursor >= len(data):
        return None
    name_len = data[cursor]
    cursor += 1
    host = data[cursor:cursor + name_len].decode("utf-8", errors="replace")
    cursor += name_len
    if cursor >= len(data) or data[cursor] != 0x10:
        return host, 0
    cursor += 1
    # Varint decode
    port = 0
    shift = 0
    while cursor < len(data):
        b = data[cursor]
        port |= (b & 0x7F) << shift
        cursor += 1
        if not (b & 0x80):
            break
        shift += 7
    return host, port


def _build_hbase_cluster(quorum_id: str, master: Optional[tuple[str, int]],
                        backups: list[tuple[str, int]],
                        regionservers: list[tuple[str, int]]) -> Cluster:
    hosts: list[Host] = []
    if master:
        hosts.append(Host(fqdn=master[0], address=master[0], port=master[1],
                          role="active-master", cluster_id=f"hbase/{quorum_id}"))
    for h, p in backups:
        hosts.append(Host(fqdn=h, address=h, port=p, role="backup-master",
                          cluster_id=f"hbase/{quorum_id}"))
    for h, p in regionservers:
        hosts.append(Host(fqdn=h, address=h, port=p, role="regionserver",
                          cluster_id=f"hbase/{quorum_id}"))
    return Cluster(
        id=f"hbase/{quorum_id}",
        tech="hbase",
        hosts=hosts,
        discovery_source="zookeeper",
        metadata={"zk_quorum": quorum_id, "tech_confidence": "high",
                  "tech_signal": "zk:/hbase"},
    )


def _build_kafka_cluster(quorum_id: str, brokers: list[dict]) -> Cluster:
    hosts = [
        Host(
            fqdn=b.get("host") or "",
            address=b.get("host"),
            port=b.get("port"),
            role="broker",
            tags={"broker_id": str(b.get("id", "")),
                  "endpoints": ",".join(b.get("endpoints", []))[:200]},
            cluster_id=f"kafka/{quorum_id}",
        )
        for b in brokers
    ]
    return Cluster(
        id=f"kafka/{quorum_id}",
        tech="kafka",
        hosts=hosts,
        discovery_source="zookeeper",
        metadata={"zk_quorum": quorum_id, "tech_confidence": "high",
                  "tech_signal": "zk:/kafka/brokers"},
    )


def _parse_active_namenode_data(data: bytes) -> Optional[tuple[str, str, str, int]]:
    """Parse Hadoop's ActiveNodeInfo protobuf (HAZKInfoProtos.ActiveNodeInfo).

    Returns (nameservice_id, namenode_id, hostname, port) or None.

    Wire format is a bare protobuf (no HBase-style 0xFFFFFFFF + 'PBUF' magic):
      field 1 (nameserviceId, string)  → tag 0x0a, length-delimited
      field 2 (namenodeId,    string)  → tag 0x12
      field 3 (hostname,      string)  → tag 0x1a
      field 4 (port,          int32 )  → tag 0x20, varint
      field 5 (zkfcPort,      int32 )  → tag 0x28, varint  (not surfaced)
    """
    if not data:
        return None
    i = 0
    ns_id = nn_id = hostname = ""
    port = 0
    try:
        while i < len(data):
            tag = data[i]; i += 1
            field_num = tag >> 3
            wire_type = tag & 0x7
            if wire_type == 2:  # length-delimited
                length = 0; shift = 0
                while i < len(data):
                    b = data[i]; i += 1
                    length |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                value = data[i:i+length].decode("utf-8", errors="replace")
                i += length
                if field_num == 1:
                    ns_id = value
                elif field_num == 2:
                    nn_id = value
                elif field_num == 3:
                    hostname = value
            elif wire_type == 0:  # varint
                v = 0; shift = 0
                while i < len(data):
                    b = data[i]; i += 1
                    v |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                if field_num == 4:
                    port = v
            else:
                return None
    except (IndexError, UnicodeDecodeError):
        return None
    if hostname and port:
        return (ns_id, nn_id, hostname, port)
    return None


def _build_hdfs_cluster(quorum_id: str, nameservice: str,
                       active: Optional[tuple[str, str, str, int]],
                       breadcrumb: Optional[tuple[str, str, str, int]]) -> Cluster:
    """Build an HDFS cluster from ZK-discovered active + breadcrumb NameNodes."""
    cluster_id = f"hdfs/{quorum_id}/{nameservice}"
    hosts: list[Host] = []
    if active:
        _, nn_id, hostname, port = active
        hosts.append(Host(
            fqdn=hostname, address=hostname, port=port,
            role="active-namenode", cluster_id=cluster_id,
            tags={"namenode_id": nn_id, "nameservice": nameservice},
        ))
    if breadcrumb:
        _, nn_id_bc, hostname_bc, port_bc = breadcrumb
        # Avoid double-listing if breadcrumb == current active.
        if not active or hostname_bc != active[2]:
            hosts.append(Host(
                fqdn=hostname_bc, address=hostname_bc, port=port_bc,
                # Could be standby, or stale (last-known-active before failover);
                # we can't tell from ZK alone.
                role="namenode-peer",
                cluster_id=cluster_id,
                tags={"namenode_id": nn_id_bc, "nameservice": nameservice,
                      "source": "breadcrumb"},
            ))
    return Cluster(
        id=cluster_id, tech="hdfs",
        hosts=hosts,
        discovery_source="zookeeper",
        metadata={"zk_quorum": quorum_id, "nameservice": nameservice,
                  "tech_confidence": "high",
                  "tech_signal": f"zk:/hadoop-ha/{nameservice}",
                  "note": "Only NameNodes are in ZK; DataNodes are not — use NN admin API to list DNs."},
    )


def _build_solr_cluster(quorum_id: str, live_nodes: list[str]) -> Cluster:
    """Solr live_nodes children look like 'host:port_solr' (underscore, not slash)."""
    hosts: list[Host] = []
    for node_name in live_nodes:
        m = re.match(r"^([^:]+):(\d+)_solr$", node_name)
        if not m:
            continue
        hosts.append(Host(fqdn=m.group(1), address=m.group(1), port=int(m.group(2)),
                          role="node", cluster_id=f"solr/{quorum_id}"))
    return Cluster(
        id=f"solr/{quorum_id}",
        tech="solr",
        hosts=hosts,
        discovery_source="zookeeper",
        metadata={"zk_quorum": quorum_id, "tech_confidence": "high",
                  "tech_signal": "zk:/solr/live_nodes"},
    )


class ZookeeperAdapter:
    """Discover via raw ZK reads. Configure quorums via env or constructor.

    Env config:
      ZK_QUORUMS="zk-prod-east=zk1:2181,zk2:2181,zk3:2181;zk-prod-west=zkw1:2181"
      ZK_WATCHES="hbase,kafka,solr"  (default: all)
    """

    def __init__(self, quorums: dict[str, str], watches: Optional[list[str]] = None,
                 timeout: int = 10):
        self.quorums = quorums  # {quorum_id: "host1:2181,host2:2181,..."}
        self.watches = watches or ["hbase", "kafka", "solr", "hdfs"]
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "ZookeeperAdapter":
        raw = os.environ.get("ZK_QUORUMS", "").strip()
        if not raw:
            return cls(quorums={})
        quorums: dict[str, str] = {}
        for entry in raw.split(";"):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            qid, _, hosts = entry.partition("=")
            quorums[qid.strip()] = hosts.strip()
        watches_env = os.environ.get("ZK_WATCHES", "")
        watches = [w.strip() for w in watches_env.split(",") if w.strip()] or None
        return cls(quorums=quorums, watches=watches)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        clusters: list[Cluster] = []
        for qid, hosts in self.quorums.items():
            zk = self._connect(hosts)
            try:
                if (not tech_filter or tech_filter == "hbase") and "hbase" in self.watches:
                    c = self._discover_hbase(zk, qid)
                    if c and c.hosts:
                        clusters.append(c)
                if (not tech_filter or tech_filter == "kafka") and "kafka" in self.watches:
                    c = self._discover_kafka(zk, qid)
                    if c and c.hosts:
                        clusters.append(c)
                if (not tech_filter or tech_filter == "solr") and "solr" in self.watches:
                    c = self._discover_solr(zk, qid)
                    if c and c.hosts:
                        clusters.append(c)
                if (not tech_filter or tech_filter == "hdfs") and "hdfs" in self.watches:
                    for c in self._discover_hdfs(zk, qid):
                        if c and c.hosts:
                            clusters.append(c)
            finally:
                self._close(zk)
        return clusters

    # ──────── kazoo glue (lazy import) ────────

    def _connect(self, hosts: str) -> Any:  # pragma: no cover (network)
        from kazoo.client import KazooClient  # type: ignore
        zk = KazooClient(hosts=hosts, timeout=self.timeout, read_only=True)
        zk.start(timeout=self.timeout)
        return zk

    @staticmethod
    def _close(zk: Any) -> None:  # pragma: no cover
        try:
            zk.stop()
            zk.close()
        except Exception:  # noqa: BLE001
            pass

    # ──────── per-tech discovery (split for testability) ────────

    def _discover_hbase(self, zk: Any, quorum_id: str) -> Optional[Cluster]:  # pragma: no cover
        prefix = "/hbase"
        master = None
        try:
            data, _ = zk.get(f"{prefix}/master")
            master = _parse_hbase_master_data(data)
        except Exception:  # noqa: BLE001
            pass
        backups: list[tuple[str, int]] = []
        try:
            for child in zk.get_children(f"{prefix}/backup-masters"):
                p = _parse_hbase_servername_znode_name(child)
                if p:
                    backups.append(p)
        except Exception:  # noqa: BLE001
            pass
        regionservers: list[tuple[str, int]] = []
        try:
            for child in zk.get_children(f"{prefix}/rs"):
                p = _parse_hbase_servername_znode_name(child)
                if p:
                    regionservers.append(p)
        except Exception:  # noqa: BLE001
            pass
        if not (master or backups or regionservers):
            return None
        return _build_hbase_cluster(quorum_id, master, backups, regionservers)

    def _discover_kafka(self, zk: Any, quorum_id: str) -> Optional[Cluster]:  # pragma: no cover
        ids_path = "/kafka/brokers/ids"
        try:
            broker_ids = zk.get_children(ids_path)
        except Exception:  # noqa: BLE001
            return None
        brokers: list[dict] = []
        for bid in broker_ids:
            try:
                data, _ = zk.get(f"{ids_path}/{bid}")
                payload = json.loads(data.decode("utf-8"))
                payload["id"] = bid
                brokers.append(payload)
            except Exception:  # noqa: BLE001
                continue
        if not brokers:
            return None
        return _build_kafka_cluster(quorum_id, brokers)

    def _discover_solr(self, zk: Any, quorum_id: str) -> Optional[Cluster]:  # pragma: no cover
        try:
            live_nodes = zk.get_children("/solr/live_nodes")
        except Exception:  # noqa: BLE001
            return None
        if not live_nodes:
            return None
        return _build_solr_cluster(quorum_id, live_nodes)

    def _discover_hdfs(self, zk: Any, quorum_id: str) -> list[Cluster]:  # pragma: no cover
        """One Cluster per HDFS nameservice, populated with the 2 NameNodes.

        DataNodes are not in ZK — they register with the NameNode directly,
        so this only surfaces the HA pair. To enumerate DNs, hit the NN's
        web UI / dfsadmin API after discovery.
        """
        prefix = "/hadoop-ha"
        try:
            nameservices = zk.get_children(prefix)
        except Exception:  # noqa: BLE001
            return []
        clusters: list[Cluster] = []
        for ns in nameservices:
            active = breadcrumb = None
            try:
                data, _ = zk.get(f"{prefix}/{ns}/ActiveStandbyElectorLock")
                active = _parse_active_namenode_data(data)
            except Exception:  # noqa: BLE001
                pass
            try:
                data, _ = zk.get(f"{prefix}/{ns}/ActiveBreadCrumb")
                breadcrumb = _parse_active_namenode_data(data)
            except Exception:  # noqa: BLE001
                pass
            if not (active or breadcrumb):
                continue
            clusters.append(_build_hdfs_cluster(quorum_id, ns, active, breadcrumb))
        return clusters
