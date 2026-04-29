"""Hardcoded tech → dependency graph for cross-tech diagnostic fan-out.

When a symptom appears in tech X, the root cause may live UPSTREAM
(postgres timeouts → maybe DNS broke → maybe K8s control plane is unhealthy).
This module ships a curated graph of "what depends on what" + a tool that
expands a tech into its dependency tree, suggesting `diag-{dep}` calls.

Severity grades:
  critical — failure of dep almost always cascades into tech
  high     — common cause of cascading failure
  medium   — sometimes related
  low      — rarely the root cause but worth a quick check

Relationships (informational; LLM uses them to phrase its hypothesis):
  depends-on    — control plane / coordination dependency
  runs-on       — host / orchestrator dependency
  uses-data-of  — reads/writes data managed by dep
  managed-by    — managed-service relationship (e.g. RDS)
  proxies-via   — sits behind dep (e.g. ingress, sidecar)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dep:
    tech: str
    relationship: str
    severity: str
    pivot_when: str = ""


# Universal infra deps — apply to ANY tech running in a typical org. Merged
# in alongside per-tech deps in expand_to_dependencies.
UNIVERSAL_INFRA: list[Dep] = [
    Dep("coredns", "depends-on", "high",
        "intermittent hostname resolution failures, DNS NXDOMAIN spikes"),
    Dep("k8s", "runs-on", "medium",
        "if the tech runs on Kubernetes — node pressure, scheduling failures, OOMKill"),
]


# Per-tech dependency lists. Only includes techs we actually have a diag-{tech}
# manual for (verified against agents/ directory).
DEPENDENCY_GRAPH: dict[str, list[Dep]] = {
    # ── databases ──────────────────────────────────────────────
    "postgres": [
        Dep("aws-rds", "managed-by", "high", "if this Postgres is RDS"),
        Dep("alloydb", "managed-by", "high", "if AlloyDB"),
        Dep("cloud-sql", "managed-by", "high", "if Cloud SQL"),
        Dep("azure-postgres", "managed-by", "high", "if Azure Database for Postgres"),
        Dep("supabase", "managed-by", "high", "if Supabase"),
        Dep("aurora", "managed-by", "high", "if Aurora Postgres"),
        Dep("longhorn", "uses-data-of", "medium", "if storage on Longhorn"),
        Dep("rook", "uses-data-of", "medium", "if storage on Rook/Ceph"),
        Dep("ceph", "uses-data-of", "medium", "underlying storage"),
        Dep("external-secrets", "depends-on", "low", "if credentials via ESO"),
        Dep("vault", "depends-on", "low", "if credentials via Vault"),
    ],
    "mysql": [
        Dep("aws-rds", "managed-by", "high", ""),
        Dep("cloud-sql", "managed-by", "high", ""),
        Dep("aurora", "managed-by", "high", "if Aurora MySQL"),
        Dep("vault", "depends-on", "low", ""),
    ],
    "mariadb": [Dep("aws-rds", "managed-by", "high", "")],
    "redis": [
        Dep("elasticache", "managed-by", "high", "if ElastiCache"),
        Dep("memorystore", "managed-by", "high", "if Memorystore"),
        Dep("azure-cache-redis", "managed-by", "high", "if Azure Cache for Redis"),
    ],
    "memcached": [
        Dep("elasticache", "managed-by", "high", ""),
    ],
    "mongo": [],
    "cassandra": [],
    "scylladb": [],
    "clickhouse": [
        Dep("zookeeper", "depends-on", "high", "if ClickHouse Keeper not used — replication coordination"),
    ],
    "tidb": [
        Dep("etcd", "depends-on", "high", "PD uses etcd-like coordination"),
    ],
    "cockroachdb": [],
    # ── messaging ──────────────────────────────────────────────
    "kafka": [
        Dep("zookeeper", "depends-on", "critical",
            "if running in ZK mode (not KRaft) — broker registration, topic metadata"),
    ],
    "redpanda": [],
    "pulsar": [
        Dep("zookeeper", "depends-on", "critical", "metadata coordination"),
        Dep("hdfs", "uses-data-of", "low", "if BookKeeper offload to HDFS"),
    ],
    "rabbitmq": [],
    "nats": [],
    "rocketmq": [],
    "activemq": [],
    "msk": [
        Dep("zookeeper", "depends-on", "high", "MSK uses ZK"),
        Dep("aws-vpc", "depends-on", "medium", "VPC connectivity"),
    ],
    # ── storage ─────────────────────────────────────────────
    "hdfs": [
        Dep("zookeeper", "depends-on", "critical", "ZK-based NameNode HA"),
    ],
    "hbase": [
        Dep("zookeeper", "depends-on", "critical",
            "RegionServer registration, master election — if ZK is unhealthy, HBase IS unhealthy"),
        Dep("hdfs", "depends-on", "critical", "all HBase data lives on HDFS"),
        Dep("hadoop", "runs-on", "high", "common Hadoop deps + YARN for compaction MR"),
    ],
    "elasticsearch": [
        Dep("k8s", "runs-on", "medium", "if ECK-managed"),
    ],
    "es": [
        Dep("k8s", "runs-on", "medium", ""),
    ],
    "opensearch": [],
    "solr": [
        Dep("zookeeper", "depends-on", "critical", "SolrCloud coordination"),
    ],
    "minio": [],
    "ceph": [],
    "longhorn": [Dep("k8s", "runs-on", "high", "")],
    "rook": [Dep("ceph", "uses-data-of", "high", "Rook orchestrates Ceph")],
    "glusterfs": [],
    "nfs": [],
    "efs": [Dep("aws-vpc", "depends-on", "medium", "")],
    "filestore": [],
    # ── coordination / control plane ──────────────────────────
    "zookeeper": [],
    "etcd": [],
    "consul": [],
    "nacos": [],
    # ── kubernetes plane ──────────────────────────────────────
    "k8s": [
        Dep("etcd", "depends-on", "critical", "K8s control plane state"),
        Dep("coredns", "depends-on", "high", "in-cluster DNS"),
        Dep("containerd", "runs-on", "high", "container runtime"),
        Dep("calico", "depends-on", "medium", "if Calico CNI"),
        Dep("cilium", "depends-on", "medium", "if Cilium CNI"),
        Dep("flannel", "depends-on", "medium", "if Flannel CNI"),
    ],
    "kubernetes": [
        Dep("etcd", "depends-on", "critical", ""),
        Dep("coredns", "depends-on", "high", ""),
        Dep("containerd", "runs-on", "high", ""),
    ],
    "eks": [Dep("aws-vpc", "depends-on", "medium", ""), Dep("aws-iam", "depends-on", "medium", "")],
    "gke": [],
    "aks": [Dep("azure-vnet", "depends-on", "medium", "")],
    "openshift": [Dep("k8s", "runs-on", "critical", "")],
    "rancher": [Dep("k8s", "runs-on", "critical", "")],
    # ── service mesh / ingress ────────────────────────────────
    "istio": [
        Dep("k8s", "runs-on", "high", ""),
        Dep("envoy", "uses-data-of", "high", "data plane"),
        Dep("coredns", "depends-on", "high", ""),
    ],
    "linkerd": [Dep("k8s", "runs-on", "high", "")],
    "envoy": [],
    "ingress-nginx": [
        Dep("k8s", "runs-on", "high", ""),
        Dep("cert-manager", "depends-on", "medium", "TLS automation"),
        Dep("externaldns", "depends-on", "low", "DNS record automation"),
    ],
    "nginx": [
        Dep("cert-manager", "depends-on", "medium", "if k8s-managed certs"),
    ],
    "haproxy": [],
    "traefik": [
        Dep("k8s", "runs-on", "high", ""),
        Dep("cert-manager", "depends-on", "medium", ""),
    ],
    "kong": [
        Dep("postgres", "depends-on", "high", "if DB-backed Kong"),
        Dep("cassandra", "depends-on", "high", "alt DB"),
    ],
    "apisix": [
        Dep("etcd", "depends-on", "critical", "config store"),
    ],
    # ── compute / data processing ─────────────────────────────
    "spark": [
        Dep("hdfs", "depends-on", "high", "if data on HDFS"),
        Dep("hive", "uses-data-of", "medium", "metastore"),
        Dep("k8s", "runs-on", "medium", "if Spark-on-K8s"),
    ],
    "flink": [
        Dep("kafka", "uses-data-of", "high", "common source/sink"),
        Dep("zookeeper", "depends-on", "medium", "HA mode"),
        Dep("hdfs", "uses-data-of", "medium", "checkpointing"),
    ],
    "trino": [
        Dep("hive", "uses-data-of", "high", "metastore"),
        Dep("hdfs", "depends-on", "medium", ""),
        Dep("postgres", "depends-on", "low", "if Postgres connector"),
    ],
    "presto": [
        Dep("hive", "uses-data-of", "high", ""),
    ],
    "hive": [
        Dep("hdfs", "depends-on", "critical", ""),
        Dep("postgres", "depends-on", "high", "metastore DB"),
        Dep("mysql", "depends-on", "high", "alt metastore DB"),
    ],
    "hadoop": [
        Dep("hdfs", "depends-on", "critical", "core component"),
        Dep("zookeeper", "depends-on", "high", "HA coordination"),
    ],
    "airflow": [
        Dep("postgres", "depends-on", "critical", "metadata DB"),
        Dep("mysql", "depends-on", "critical", "alt metadata DB"),
        Dep("redis", "depends-on", "medium", "Celery broker"),
        Dep("k8s", "runs-on", "medium", "if KubernetesExecutor"),
    ],
    # ── observability stack ───────────────────────────────────
    "grafana": [
        Dep("prometheus", "uses-data-of", "high", "metrics datasource"),
        Dep("loki", "uses-data-of", "medium", "logs datasource"),
        Dep("postgres", "depends-on", "low", "Grafana metadata DB"),
    ],
    "prometheus": [
        Dep("alertmanager", "depends-on", "medium", "alerting pipeline"),
    ],
    "alertmanager": [
        Dep("pagerduty", "depends-on", "medium", ""),
        Dep("opsgenie", "depends-on", "medium", ""),
    ],
    "thanos": [
        Dep("prometheus", "uses-data-of", "high", ""),
        Dep("aws-s3", "uses-data-of", "high", "long-term storage"),
        Dep("gcs", "uses-data-of", "high", ""),
    ],
    "mimir": [
        Dep("aws-s3", "uses-data-of", "high", ""),
    ],
    "loki": [
        Dep("aws-s3", "uses-data-of", "high", "chunk storage"),
    ],
    "tempo": [
        Dep("aws-s3", "uses-data-of", "high", ""),
    ],
    "jaeger": [
        Dep("elasticsearch", "uses-data-of", "high", "default backend"),
        Dep("cassandra", "uses-data-of", "high", "alt backend"),
    ],
    "logstash": [
        Dep("elasticsearch", "uses-data-of", "high", ""),
    ],
    "fluentd": [
        Dep("elasticsearch", "uses-data-of", "high", ""),
    ],
    "filebeat": [
        Dep("elasticsearch", "uses-data-of", "high", ""),
        Dep("logstash", "uses-data-of", "high", ""),
    ],
    "vector": [],
    "datadog": [],
    "splunk": [],
    "sentry": [
        Dep("postgres", "depends-on", "high", "metadata"),
        Dep("redis", "depends-on", "high", "queueing"),
        Dep("kafka", "depends-on", "medium", "ingest pipeline"),
    ],
    # ── CI/CD ─────────────────────────────────────────────────
    "argocd": [
        Dep("k8s", "runs-on", "high", ""),
        Dep("redis", "depends-on", "medium", "session cache"),
    ],
    "flux": [Dep("k8s", "runs-on", "high", "")],
    "jenkins": [],
    "tekton": [Dep("k8s", "runs-on", "high", "")],
    "spinnaker": [
        Dep("redis", "depends-on", "medium", ""),
        Dep("k8s", "runs-on", "medium", ""),
    ],
    "drone": [],
    # ── identity / security ───────────────────────────────────
    "vault": [],
    "keycloak": [
        Dep("postgres", "depends-on", "high", "user store"),
    ],
    "okta": [],
    "auth0": [],
    "external-secrets": [
        Dep("vault", "depends-on", "high", "if Vault provider"),
        Dep("aws-secrets-manager", "depends-on", "high", ""),
        Dep("gcp-secret-manager", "depends-on", "high", ""),
    ],
    "cert-manager": [
        Dep("k8s", "runs-on", "high", ""),
        Dep("vault", "depends-on", "low", "if Vault issuer"),
    ],
    # ── service registry / config ─────────────────────────────
    "apollo": [
        Dep("mysql", "depends-on", "critical", "ConfigDB"),
        Dep("k8s", "runs-on", "medium", ""),
    ],
    "spring-cloud-config": [],
    # ── DNS / networking ──────────────────────────────────────
    "coredns": [
        Dep("k8s", "runs-on", "high", "in-cluster CoreDNS lives here"),
    ],
    "bind": [],
    "powerdns": [
        Dep("postgres", "depends-on", "high", "if SQL backend"),
        Dep("mysql", "depends-on", "high", ""),
    ],
    "route53": [],
    "cloud-dns": [],
    "cloudflare": [],
    "externaldns": [
        Dep("k8s", "runs-on", "high", ""),
        Dep("route53", "depends-on", "medium", "if AWS"),
        Dep("cloud-dns", "depends-on", "medium", "if GCP"),
    ],
    # ── managed services connect back to platform ─────────────
    "aws-rds": [Dep("aws-vpc", "depends-on", "medium", "")],
    "aws-lambda": [Dep("aws-iam", "depends-on", "medium", "")],
    "alloydb": [],
    "cloud-sql": [],
    "azure-postgres": [Dep("azure-vnet", "depends-on", "medium", "")],
    "supabase": [Dep("postgres", "uses-data-of", "critical", "Supabase IS Postgres")],
    "planetscale": [Dep("vitess", "managed-by", "high", "PS uses Vitess")],
    "vitess": [Dep("etcd", "depends-on", "high", "topology service")],
}


def get_dependencies(tech: str) -> list[Dep]:
    """Direct + universal infra deps for one tech (deduped)."""
    direct = DEPENDENCY_GRAPH.get(tech, [])
    seen = {d.tech for d in direct}
    universal = [d for d in UNIVERSAL_INFRA if d.tech not in seen]
    return direct + universal


def get_dependents(tech: str) -> list[tuple[str, Dep]]:
    """Reverse lookup — list (parent_tech, Dep) where parent depends on `tech`.

    Useful for blast-radius analysis: "if Zookeeper goes down, who breaks?"
    """
    out: list[tuple[str, Dep]] = []
    for parent, deps in DEPENDENCY_GRAPH.items():
        for d in deps:
            if d.tech == tech:
                out.append((parent, d))
    # Universal infra is depended on by everyone
    for d in UNIVERSAL_INFRA:
        if d.tech == tech:
            for parent in DEPENDENCY_GRAPH:
                # Skip if parent already directly depends on this tech
                if not any(x.tech == tech for x in DEPENDENCY_GRAPH[parent]):
                    out.append((parent, d))
            break
    return out


def expand(tech: str, depth: int = 1, observation: str = "",
           visited: set[str] | None = None) -> list[dict]:
    """Recursive expand. Returns a flat list of dep dicts with `depth` field."""
    if depth < 1 or depth > 3:
        return []
    visited = visited or {tech}
    out: list[dict] = []
    for d in get_dependencies(tech):
        if d.tech in visited:
            continue
        visited.add(d.tech)
        out.append({
            "tech": d.tech,
            "depth": 1,
            "via": tech,
            "relationship": d.relationship,
            "severity": d.severity,
            "pivot_when": d.pivot_when,
            "diag_mcp": f"srefix-diag-{d.tech}",
            "suggested_call": {
                "mcp": f"srefix-diag-{d.tech}",
                "tool": "diagnose_symptom",
                "args": {"symptom": observation or "(propagated upstream check)",
                         "max_results": 3},
            },
        })
        if depth > 1:
            for sub in expand(d.tech, depth - 1, observation, visited):
                sub["depth"] = sub["depth"] + 1
                out.append(sub)
    return out
