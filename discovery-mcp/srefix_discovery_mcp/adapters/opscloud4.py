"""Discover clusters from opscloud4 (https://github.com/ixrjog/opscloud4).

Strategy: identify tech via 4 sources, first hit wins, then expand.
  ① ServerGroupType.name      (e.g. "HBase集群")
  ② Tags                      (e.g. app=hbase / tech=hbase)
  ③ BusinessProperty          (e.g. tech_type=hbase)
  ④ Naming convention regex   (fallback, lowest confidence)

Endpoints (verified against ServerGroupController / ServerController):
  POST /api/server/group/page/query   ServerGroup pagination (returns
                                       serverGroupType + tags +
                                       businessProperty all in one shot)
  POST /api/server/page/query         Server pagination filtered by
                                       serverGroupId
Auth: HTTP header `X-Token`.
"""
from __future__ import annotations

import os
import re
from typing import Iterator, Optional

import requests

from ..core.models import Cluster, Host

# Canonical tech name → aliases. Broadly covers common middleware so that
# tag-based / name-based discovery (opscloud4 / k8s / consul) recognizes them.
_CANONICAL_TECH = {
    # ─ databases ─
    "hbase": {"hbase"},
    "elasticsearch": {"elasticsearch", "es", "opensearch"},
    "mongo": {"mongo", "mongodb"},
    "redis": {"redis"},
    "memcached": {"memcached"},
    "postgres": {"postgres", "postgresql", "pg"},
    "mysql": {"mysql"},
    "mariadb": {"mariadb"},
    "tidb": {"tidb"},
    "clickhouse": {"clickhouse", "ck"},
    "cassandra": {"cassandra"},
    "scylladb": {"scylla", "scylladb"},
    "cockroachdb": {"cockroach", "cockroachdb", "crdb"},
    "couchbase": {"couchbase"},
    "couchdb": {"couchdb"},
    "aerospike": {"aerospike"},
    "neo4j": {"neo4j"},
    "arangodb": {"arangodb"},
    "dgraph": {"dgraph"},
    "janusgraph": {"janusgraph"},
    "tdengine": {"tdengine"},
    "questdb": {"questdb"},
    "timescaledb": {"timescaledb"},
    "influxdb": {"influxdb"},
    "druid": {"druid"},
    "pinecone": {"pinecone"},
    "milvus": {"milvus"},
    "weaviate": {"weaviate"},
    "qdrant": {"qdrant"},
    "chromadb": {"chromadb"},
    "pgvector": {"pgvector"},
    "typesense": {"typesense"},
    "meilisearch": {"meilisearch"},
    "solr": {"solr"},
    "foundationdb": {"foundationdb", "fdb"},
    "vitess": {"vitess"},
    # ─ messaging / streaming ─
    "kafka": {"kafka"},
    "redpanda": {"redpanda"},
    "pulsar": {"pulsar"},
    "rocketmq": {"rocketmq", "ons"},
    "rabbitmq": {"rabbitmq"},
    "activemq": {"activemq"},
    "nats": {"nats"},
    # ─ storage ─
    "hdfs": {"hdfs"},
    "minio": {"minio"},
    "ceph": {"ceph"},
    "longhorn": {"longhorn"},
    "rook": {"rook"},
    "glusterfs": {"glusterfs", "gluster"},
    # ─ compute / data processing ─
    "spark": {"spark"},
    "flink": {"flink"},
    "trino": {"trino"},
    "presto": {"presto"},
    "hive": {"hive"},
    "hadoop": {"hadoop"},
    "airflow": {"airflow"},
    "dbt": {"dbt"},
    "databricks": {"databricks"},
    # ─ coordination / control plane ─
    "zookeeper": {"zookeeper", "zk"},
    "etcd": {"etcd"},
    "consul": {"consul"},
    "nacos": {"nacos"},
    "kubernetes": {"kubernetes", "k8s"},
    # ─ ingress / proxy / api gateway ─
    "nginx": {"nginx"},
    "haproxy": {"haproxy"},
    "envoy": {"envoy"},
    "istio": {"istio"},
    "linkerd": {"linkerd"},
    "kong": {"kong"},
    "traefik": {"traefik"},
    "apisix": {"apisix"},
    "caddy": {"caddy"},
    "ingress-nginx": {"ingress-nginx"},
    "oauth2-proxy": {"oauth2-proxy"},
    # ─ identity / security / secrets ─
    "vault": {"vault"},
    "keycloak": {"keycloak"},
    "okta": {"okta"},
    "auth0": {"auth0"},
    "cert-manager": {"cert-manager", "certmanager"},
    "external-secrets": {"external-secrets", "eso"},
    "teleport": {"teleport"},
    "falco": {"falco"},
    "opa": {"opa"},
    "trivy": {"trivy"},
    # ─ observability ─
    "prometheus": {"prometheus"},
    "grafana": {"grafana"},
    "loki": {"loki"},
    "tempo": {"tempo"},
    "jaeger": {"jaeger"},
    "zipkin": {"zipkin"},
    "thanos": {"thanos"},
    "mimir": {"mimir"},
    "alertmanager": {"alertmanager"},
    "victoriametrics": {"victoriametrics", "vm"},
    "fluentd": {"fluentd"},
    "filebeat": {"filebeat"},
    "logstash": {"logstash"},
    "vector": {"vector"},
    "graylog": {"graylog"},
    "datadog": {"datadog"},
    "newrelic": {"newrelic"},
    "splunk": {"splunk"},
    "sentry": {"sentry"},
    "pyroscope": {"pyroscope"},
    "otel-collector": {"otel-collector", "opentelemetry-collector"},
    # ─ ci/cd / config ─
    "jenkins": {"jenkins"},
    "argocd": {"argocd"},
    "flux": {"flux"},
    "tekton": {"tekton"},
    "spinnaker": {"spinnaker"},
    "drone": {"drone"},
    "circleci": {"circleci"},
    "harness": {"harness"},
    "apollo": {"apollo"},  # Apollo Config (携程)
    "spring-cloud-config": {"spring-cloud-config", "scc"},
    # ─ DNS / networking ─
    "coredns": {"coredns"},
    "bind": {"bind"},
    "powerdns": {"powerdns", "pdns"},
    "calico": {"calico"},
    "cilium": {"cilium"},
    "flannel": {"flannel"},
    "metallb": {"metallb"},
}

# Reverse index: alias → canonical
_TECH_ALIAS_TO_CANONICAL = {alias: canon for canon, aliases in _CANONICAL_TECH.items() for alias in aliases}

# Pattern for naming-convention fallback (line ④)
_TECH_NAME_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_TECH_ALIAS_TO_CANONICAL.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Suffixes to strip when normalizing names like "HBase集群" / "kafka cluster"
_SUFFIX_RE = re.compile(r"(集群|群集|cluster|service|服务)$", re.IGNORECASE)

# Per-tech hostname → role inference (line ④ for role)
_HOSTNAME_ROLE_RULES: dict[str, callable] = {
    "hbase": lambda n: (
        "backup-master" if "backup" in n and "master" in n
        else "active-master" if "master" in n
        else "regionserver" if any(p in n for p in ("rs-", "regionserver", "-rs"))
        else None
    ),
    "elasticsearch": lambda n: (
        "master" if "master" in n
        else "data" if "data" in n or "hot" in n or "warm" in n or "cold" in n
        else "ingest" if "ingest" in n
        else "client" if "client" in n or "coord" in n
        else None
    ),
    "kafka": lambda n: (
        "controller" if "controller" in n
        else "broker" if any(p in n for p in ("broker", "kafka-")) else None
    ),
    "mongo": lambda n: (
        "mongos" if "mongos" in n
        else "config" if "config" in n
        else "arbiter" if "arbiter" in n
        else "primary" if "primary" in n
        else "secondary" if "secondary" in n
        else None
    ),
    "redis": lambda n: (
        "sentinel" if "sentinel" in n
        else "master" if "master" in n
        else "replica" if "replica" in n or "slave" in n
        else None
    ),
    "cassandra": lambda n: "seed" if "seed" in n else None,
    "zookeeper": lambda n: "leader" if "leader" in n else "follower" if "follower" in n else None,
}


class Opscloud4Adapter:
    def __init__(self, base_url: str, token: str, timeout: int = 10, page_size: int = 100):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "X-Token": token,
            "Content-Type": "application/json",
        })
        self.timeout = timeout
        self.page_size = page_size

    @classmethod
    def from_env(cls) -> "Opscloud4Adapter":
        return cls(
            base_url=os.environ["OPSCLOUD4_BASE_URL"],
            token=os.environ["OPSCLOUD4_TOKEN"],
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        clusters: list[Cluster] = []
        for group in self._iter_server_groups():
            tech, confidence, signal = self._identify_tech(group)
            if tech == "unknown":
                continue
            if tech_filter and tech != tech_filter:
                continue
            servers = list(self._iter_servers(group["id"]))
            hosts = [self._map_host(s, tech, group["name"]) for s in servers]
            clusters.append(Cluster(
                id=group["name"],
                tech=tech,
                hosts=hosts,
                discovery_source="opscloud4",
                metadata={
                    "opscloud4_group_id": group.get("id"),
                    "tech_confidence": confidence,
                    "tech_signal": signal,
                    "comment": group.get("comment"),
                    "server_size": group.get("serverSize"),
                },
            ))
        return clusters

    # ──────── tech identification (4 sources, first hit wins) ────────

    def _identify_tech(self, group: dict) -> tuple[str, str, str]:
        # ① ServerGroupType
        sgt = group.get("serverGroupType") or {}
        if sgt.get("name"):
            tech = self._normalize_tech_name(sgt["name"])
            if tech:
                return tech, "high", "server_group_type"

        # ② Tags
        tag_kv = self._tag_dict(group.get("tags") or [])
        for key in ("app", "tech", "service", "component", "middleware", "kind"):
            v = tag_kv.get(key)
            if v:
                tech = self._normalize_tech_name(v)
                if tech:
                    return tech, "high", f"tag:{key}={v}"

        # ③ BusinessProperty
        bp = group.get("businessProperty") or {}
        # opscloud4 BusinessProperty uses {properties: {...}} or {property: [{name, value}]}
        props = bp.get("properties") if isinstance(bp.get("properties"), dict) else None
        if props is None and isinstance(bp.get("property"), list):
            props = {p.get("name"): p.get("value") for p in bp["property"] if p.get("name")}
        props = props or {}
        for key in ("tech_type", "tech", "app_type", "service_kind", "kind", "app", "middleware"):
            v = props.get(key)
            if v:
                tech = self._normalize_tech_name(v)
                if tech:
                    return tech, "medium", f"business_property:{key}={v}"

        # ④ Naming convention
        m = _TECH_NAME_PATTERN.search(group.get("name") or "")
        if m:
            tech = _TECH_ALIAS_TO_CANONICAL.get(m.group(1).lower())
            if tech:
                return tech, "low", f"name_pattern:{m.group(1)}"

        return "unknown", "unknown", "none"

    @staticmethod
    def _normalize_tech_name(name: str) -> Optional[str]:
        if not name:
            return None
        s = name.strip().lower()
        # Direct hit
        if s in _TECH_ALIAS_TO_CANONICAL:
            return _TECH_ALIAS_TO_CANONICAL[s]
        # Strip suffixes ("hbase集群" → "hbase")
        stripped = _SUFFIX_RE.sub("", s).strip()
        if stripped in _TECH_ALIAS_TO_CANONICAL:
            return _TECH_ALIAS_TO_CANONICAL[stripped]
        # Embedded match
        m = _TECH_NAME_PATTERN.search(stripped)
        if m:
            return _TECH_ALIAS_TO_CANONICAL.get(m.group(1).lower())
        return None

    @staticmethod
    def _tag_dict(tags: list[dict]) -> dict[str, str]:
        """Coerce opscloud4 tag list → flat key=value dict.

        Handles three shapes:
          A. {"tagKey": "app=hbase"}             (key=value packed in one field)
          B. {"tagKey": "app", "tagValue": "hbase"}
          C. {"name": "app=hbase"}               (older style)
        """
        out: dict[str, str] = {}
        for t in tags or []:
            tag_key = t.get("tagKey") or t.get("name") or ""
            if not tag_key:
                continue
            if "=" in tag_key:
                k, _, v = tag_key.partition("=")
                out[k.strip()] = v.strip()
            elif "tagValue" in t:
                out[tag_key.strip()] = str(t["tagValue"]).strip()
            else:
                out[tag_key.strip()] = ""
        return out

    # ──────── pagination iterators ────────

    def _iter_server_groups(self) -> Iterator[dict]:
        page = 1
        while True:
            resp = self.session.post(
                f"{self.base_url}/api/server/group/page/query",
                json={"page": page, "length": self.page_size},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            body = (payload.get("body") or {}).get("data") or []
            if not body:
                break
            yield from body
            if len(body) < self.page_size:
                break
            page += 1

    def _iter_servers(self, group_id: int) -> Iterator[dict]:
        page = 1
        while True:
            resp = self.session.post(
                f"{self.base_url}/api/server/page/query",
                json={"page": page, "length": self.page_size, "serverGroupId": group_id},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            body = (payload.get("body") or {}).get("data") or []
            if not body:
                break
            yield from body
            if len(body) < self.page_size:
                break
            page += 1

    # ──────── host / role mapping ────────

    def _map_host(self, server: dict, tech: str, cluster_id: str) -> Host:
        return Host(
            fqdn=server.get("name") or server.get("privateIp") or server.get("publicIp") or "",
            address=server.get("privateIp") or server.get("publicIp"),
            port=None,
            role=self._identify_role(server, tech),
            tags=self._tag_dict(server.get("tags") or []),
            cluster_id=cluster_id,
            health="active" if server.get("isActive") else "unknown",
        )

    def _identify_role(self, server: dict, tech: str) -> str:
        # ① server tags
        tag_kv = self._tag_dict(server.get("tags") or [])
        for k in ("role", "host_role", "node_role"):
            v = tag_kv.get(k)
            if v:
                return v.lower()

        # ② BusinessProperty on server
        bp = server.get("businessProperty") or {}
        props = bp.get("properties") if isinstance(bp.get("properties"), dict) else None
        if props is None and isinstance(bp.get("property"), list):
            props = {p.get("name"): p.get("value") for p in bp["property"] if p.get("name")}
        props = props or {}
        for k in ("role", "host_role"):
            v = props.get(k)
            if v:
                return str(v).lower()

        # ③ Naming convention by tech
        rule = _HOSTNAME_ROLE_RULES.get(tech)
        if rule:
            role = rule((server.get("name") or "").lower())
            if role:
                return role

        return "unknown"
