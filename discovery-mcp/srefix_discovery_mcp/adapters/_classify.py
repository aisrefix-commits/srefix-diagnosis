"""Tag-based tech classification — shared across all cloud adapters.

When users self-deploy stateful tech (HBase / Cassandra / Kafka / etc.) on
raw cloud VMs, cloud APIs only see "an instance" — not "an HBase node".
The convention this module enforces: tag the instance with

    Service=<tech>          (e.g. Service=hbase)
    ClusterName=<group-id>  (e.g. ClusterName=hbase-prod-us-east-1)
    Role=<role>             (optional, e.g. Role=master / regionserver)

…and any cloud adapter can group raw instances into typed clusters
matching the diag-{tech}.md filenames in agents/.

Two key functions:

    classify_by_tags(tags) -> str
        Returns the tech short-name. Falls back to the cloud's default
        (e.g. "ec2" / "gce" / "azure-vm") when no Service tag matches a
        known tech.

    group_instances_into_clusters(instances, ...) -> list[Cluster]
        Walks raw instances, classifies each, groups by (tech, cluster_name),
        emits one Cluster per group.

The set of "known techs" is auto-loaded from agents/*.md so adding a new
manual automatically extends the matcher — no hardcoded list of 250.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..core.models import Cluster, Host

# Tag keys the classifier checks, in priority order.
# Casing-insensitive: the lookup tries each in both Pascal/lower forms.
SERVICE_TAG_KEYS = (
    "Service", "service",
    "Tech", "tech",
    "Component", "component",
    "App", "app",
    "Application", "application",
    "Workload", "workload",
)

CLUSTER_TAG_KEYS = (
    "ClusterName", "cluster_name", "Cluster", "cluster",
    "ClusterId", "cluster_id", "ClusterID",
    "Group", "group",
    "Stack", "stack",
)

ROLE_TAG_KEYS = (
    "Role", "role",
    "NodeType", "node_type", "NodeRole",
    "Tier", "tier",
)

ENV_TAG_KEYS = (
    "Env", "env", "Environment", "environment",
    "Stage", "stage",
)


@lru_cache(maxsize=1)
def load_known_techs() -> frozenset[str]:
    """Returns the set of valid tech short-names by reading agents/*.md.

    Looks for an agents/ directory walking up from this file, then up from
    cwd. If neither has manuals, returns an empty set — in which case
    classification falls through to the default for every instance.
    """
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    for parent in here.parents:
        candidates.append(parent / "agents")
    candidates.append(Path.cwd() / "agents")

    for c in candidates:
        if c.is_dir():
            techs = {p.stem.replace("-agent", "") for p in c.glob("*.md")}
            techs.discard("README")
            if techs:
                return frozenset(techs)
    return frozenset()


def classify_by_tags(tags: dict[str, str], default: str = "vm") -> str:
    """Inspect tags, return the matched tech short-name or `default`.

    Matching is case-insensitive on both keys and values. Only tag values
    that match a known tech (from agents/*.md) are accepted — random
    values like "Service=production" don't classify as a tech.
    """
    if not tags:
        return default
    known = load_known_techs()
    # Build case-folded view of incoming tags
    folded = {k.lower(): str(v) for k, v in tags.items()}
    for key in SERVICE_TAG_KEYS:
        val = folded.get(key.lower())
        if val:
            v = val.strip().lower()
            if known and v in known:
                return v
            # Common aliases users might tag with
            alias = {
                "k8s": "kubernetes", "es": "elasticsearch",
                "mongo": "mongodb", "pg": "postgres",
                "ch": "clickhouse", "mq": "rabbitmq",
            }.get(v)
            if alias and known and alias in known:
                return alias
    return default


def extract_cluster_name(tags: dict[str, str], fallback: str) -> str:
    """Pull a cluster name from tags; fall back to instance-id-style default."""
    folded = {k.lower(): str(v) for k, v in (tags or {}).items()}
    for key in CLUSTER_TAG_KEYS:
        val = folded.get(key.lower())
        if val and val.strip():
            return val.strip()
    return fallback


def extract_role(tags: dict[str, str], default: str = "member") -> str:
    folded = {k.lower(): str(v) for k, v in (tags or {}).items()}
    for key in ROLE_TAG_KEYS:
        val = folded.get(key.lower())
        if val and val.strip():
            return val.strip()
    return default


def extract_env(tags: dict[str, str]) -> Optional[str]:
    folded = {k.lower(): str(v) for k, v in (tags or {}).items()}
    for key in ENV_TAG_KEYS:
        val = folded.get(key.lower())
        if val and val.strip():
            return val.strip()
    return None


# ─── Tag-shape adapters per cloud ─────────────────────────────────────
# Each cloud's SDK returns tags in a different shape; normalise to a flat
# dict[str,str] before passing to classify_by_tags / extract_cluster_name.

def normalize_aws_tags(tag_list) -> dict[str, str]:
    """AWS shape: [{'Key': 'k', 'Value': 'v'}, ...]"""
    return {t.get("Key", ""): t.get("Value", "") for t in (tag_list or []) if t.get("Key")}


def normalize_aliyun_tags(tag_obj) -> dict[str, str]:
    """Aliyun shape: {'Tag': [{'TagKey': 'k', 'TagValue': 'v'}, ...]} or list directly."""
    if isinstance(tag_obj, dict):
        tag_list = tag_obj.get("Tag", [])
    else:
        tag_list = tag_obj or []
    return {t.get("TagKey", ""): t.get("TagValue", "")
            for t in tag_list if t.get("TagKey")}


def normalize_tencent_tags(tag_list) -> dict[str, str]:
    """Tencent Cloud: [{'Key': 'k', 'Value': 'v'}, ...] or [{'TagKey', 'TagValue'}, ...]"""
    out: dict[str, str] = {}
    for t in tag_list or []:
        k = t.get("Key") or t.get("TagKey") or ""
        v = t.get("Value") or t.get("TagValue") or ""
        if k:
            out[k] = v
    return out


def normalize_huawei_tags(tag_list) -> dict[str, str]:
    """Huawei Cloud: [{'key': 'k', 'value': 'v'}, ...] (lowercase)"""
    return {t.get("key", ""): t.get("value", "")
            for t in (tag_list or []) if t.get("key")}


def normalize_jd_tags(tag_list) -> dict[str, str]:
    """JD Cloud: same shape as AWS — [{'Key', 'Value'}, ...]"""
    return normalize_aws_tags(tag_list)


def normalize_volc_tags(tag_list) -> dict[str, str]:
    """Volcengine: [{'Key', 'Value'}, ...]"""
    return normalize_aws_tags(tag_list)


def normalize_gcp_labels(label_dict) -> dict[str, str]:
    """GCP shape: already a flat dict."""
    return {str(k): str(v) for k, v in (label_dict or {}).items()}


def normalize_azure_tags(tag_dict) -> dict[str, str]:
    """Azure shape: already a flat dict."""
    return {str(k): str(v) for k, v in (tag_dict or {}).items()}


def normalize_do_tags(tag_list) -> dict[str, str]:
    """DigitalOcean shape: ['service:hbase', 'cluster:hbase-prod', ...] OR
    ['hbase', 'us-east'] — DO tags are flat strings.
    Convention: 'k:v' pairs are split; bare tags become {tag: tag}."""
    out: dict[str, str] = {}
    for t in tag_list or []:
        if not isinstance(t, str):
            continue
        if ":" in t:
            k, _, v = t.partition(":")
            out[k.strip()] = v.strip()
        else:
            out[t.strip()] = t.strip()
    return out


# ─── Grouping primitive ───────────────────────────────────────────────

def group_instances_into_clusters(
    instances: Iterable[dict],
    *,
    tag_extractor: Callable[[dict], dict[str, str]],
    fqdn_extractor: Callable[[dict], str],
    instance_id_extractor: Callable[[dict], str],
    cluster_id_prefix: str,
    discovery_source: str,
    region: str = "",
    account: str = "default",
    default_tech: str = "vm",
    extra_host_tags: Optional[Callable[[dict], dict[str, str]]] = None,
    extra_metadata: Optional[Callable[[dict], dict]] = None,
) -> list[Cluster]:
    """Group raw instance dicts into typed Clusters via tag classification.

    Args:
      instances:               raw cloud-API instance dicts
      tag_extractor:           inst -> flat dict[str,str] (cloud-specific)
      fqdn_extractor:          inst -> fqdn / private DNS / IP
      instance_id_extractor:   inst -> stable per-instance id
      cluster_id_prefix:       e.g. f"aws/{account}/{region}"
      discovery_source:        label that ends up in Cluster.discovery_source
      default_tech:            tech for instances without a Service tag
                               (e.g. "ec2" for AWS, "gce" for GCP)
      extra_host_tags:         per-instance host tags (optional)
      extra_metadata:          per-cluster metadata (optional, last instance wins)

    Returns one Cluster per (tech, cluster_name) group.
    """
    by_group: dict[tuple[str, str], list[tuple[dict, dict[str, str]]]] = {}

    for inst in instances:
        tags = tag_extractor(inst) or {}
        tech = classify_by_tags(tags, default=default_tech)
        iid = instance_id_extractor(inst) or "unknown"
        cluster_name = extract_cluster_name(tags, fallback=iid if tech == default_tech else f"{tech}-{iid}")
        by_group.setdefault((tech, cluster_name), []).append((inst, tags))

    clusters: list[Cluster] = []
    for (tech, cluster_name), members in by_group.items():
        cid = f"{cluster_id_prefix}/{tech}/{cluster_name}"
        hosts: list[Host] = []
        env_seen: Optional[str] = None
        last_meta: dict = {}
        for inst, tags in members:
            host_tags = {"region": region}
            if extra_host_tags:
                host_tags.update(extra_host_tags(inst) or {})
            host_tags.update({k: v for k, v in tags.items() if k.lower() not in
                              {k.lower() for k in (*SERVICE_TAG_KEYS, *CLUSTER_TAG_KEYS)}})
            hosts.append(Host(
                fqdn=fqdn_extractor(inst) or instance_id_extractor(inst),
                role=extract_role(tags, default="member"),
                tags=host_tags,
                cluster_id=cid,
            ))
            env_seen = env_seen or extract_env(tags)
            if extra_metadata:
                last_meta = {**last_meta, **(extra_metadata(inst) or {})}

        meta = {"region": region, "account": account,
                "tech_confidence": "high" if tech != default_tech else "low",
                "tech_signal": f"tag:Service={tech}" if tech != default_tech else "tag:none",
                "instance_count": len(members),
                **last_meta}
        if env_seen:
            meta["env"] = env_seen

        clusters.append(Cluster(
            id=cid, tech=tech, hosts=hosts,
            discovery_source=discovery_source,
            metadata=meta,
        ))

    return clusters
