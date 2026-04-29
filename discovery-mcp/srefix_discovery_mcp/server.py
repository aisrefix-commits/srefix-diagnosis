"""FastMCP server: cluster discovery for the diagnosis stack.

Tools exposed:
  list_clusters(tech?)           – list discovered clusters (cached)
  get_cluster(cluster_id)        – full topology for one cluster
  list_hosts(...)                – flexible host filter
  list_discoverable_techs()      – distinct tech values seen
  discover_now(tech?)            – force re-poll all adapters
  discovery_health()             – adapter status, last run, errors

Adapters auto-register based on environment variables:
  OPSCLOUD4_BASE_URL + OPSCLOUD4_TOKEN   → enable Opscloud4Adapter
  (more adapters added later: kubernetes, aws, zookeeper, ...)

Cache TTL is controlled by DISCOVERY_CACHE_TTL (seconds, default 300).
"""
from __future__ import annotations

import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .adapters.aliyun import AliyunAdapter
from .adapters.aws import AWSAdapter
from .adapters.azure import AzureAdapter
from .adapters.backstage import BackstageAdapter
from .adapters.cassandra import CassandraAdapter
from .adapters.consul import ConsulAdapter
from .adapters.digitalocean import DigitalOceanAdapter
from .adapters.elasticsearch_direct import ElasticsearchAdapter
from .adapters.etcd import EtcdAdapter
from .adapters.eureka import EurekaAdapter
from .adapters.flyio import FlyIOAdapter
from .adapters.gcp import GCPAdapter
from .adapters.helm_releases import HelmAdapter
from .adapters.heroku import HerokuAdapter
from .adapters.huaweicloud import HuaweiCloudAdapter
from .adapters.jdcloud import JDCloudAdapter
from .adapters.kubernetes import KubernetesAdapter
from .adapters.mongodb import MongoDBAdapter
from .adapters.monitoring_servers import (
    KnativeAdapter, NagiosAdapter, OpenFaaSAdapter, ZabbixAdapter,
)
from .adapters.nacos import NacosAdapter
from .adapters.nomad import NomadAdapter
from .adapters.opscloud4 import Opscloud4Adapter
from .adapters.railway import RailwayAdapter
from .adapters.rancher import RancherAdapter
from .adapters.redis_cluster import RedisClusterAdapter
from .adapters.saas import (
    Auth0Adapter, CloudflareAdapter, DatadogAdapter, GitHubActionsAdapter,
    GitLabCIAdapter, NetlifyAdapter, NewRelicAdapter, OktaAdapter,
    OpsGenieAdapter, PagerDutyAdapter, PlanetScaleAdapter, SentryAdapter,
    SnowflakeAdapter, SplunkAdapter,
)
from .adapters.tencentcloud import TencentCloudAdapter
from .adapters.vercel import VercelAdapter
from .adapters.virtual import VirtualAdapter
from .adapters.volcengine import VolcengineAdapter
from .adapters.zookeeper import ZookeeperAdapter
from .core.registry import Adapter, DiscoveryRegistry


def _build_adapters() -> list[Adapter]:
    """Auto-register adapters based on env vars. Each adapter is opt-in.

      OPSCLOUD4_BASE_URL + OPSCLOUD4_TOKEN     → Opscloud4Adapter
      K8S_DISCOVERY_ENABLED=1                  → KubernetesAdapter (ambient kubeconfig)
      ZK_QUORUMS                               → ZookeeperAdapter
      AWS_DISCOVERY_REGIONS                    → AWSAdapter (RDS / ElastiCache / MSK / OpenSearch / EMR / Aurora / DocDB / Redshift)
      GCP_PROJECTS                             → GCPAdapter (Cloud SQL / Memorystore / AlloyDB / Spanner / BigQuery)
      AZURE_SUBSCRIPTION_IDS                   → AzureAdapter (Postgres / MySQL / SQL / Redis / Cosmos / Event Hubs / HDInsight)
      CONSUL_URL                               → ConsulAdapter (any service registered with tech=/app= tag)
      REDIS_CLUSTERS                           → RedisClusterAdapter (direct CLUSTER NODES query)
    """
    adapters: list[Adapter] = []
    if os.environ.get("OPSCLOUD4_BASE_URL") and os.environ.get("OPSCLOUD4_TOKEN"):
        adapters.append(Opscloud4Adapter.from_env())
    if os.environ.get("K8S_DISCOVERY_ENABLED", "").lower() in ("1", "true"):
        adapters.append(KubernetesAdapter.from_env())
    if os.environ.get("ZK_QUORUMS"):
        adapters.append(ZookeeperAdapter.from_env())
    if os.environ.get("AWS_DISCOVERY_REGIONS"):
        adapters.append(AWSAdapter.from_env())
    if os.environ.get("GCP_PROJECTS"):
        adapters.append(GCPAdapter.from_env())
    if os.environ.get("AZURE_SUBSCRIPTION_IDS"):
        adapters.append(AzureAdapter.from_env())
    if os.environ.get("CONSUL_URL"):
        adapters.append(ConsulAdapter.from_env())
    if os.environ.get("REDIS_CLUSTERS"):
        adapters.append(RedisClusterAdapter.from_env())
    if os.environ.get("MONGODB_CLUSTERS"):
        adapters.append(MongoDBAdapter.from_env())
    if os.environ.get("CASSANDRA_CLUSTERS"):
        adapters.append(CassandraAdapter.from_env())
    if os.environ.get("ES_DISCOVERY_ENDPOINTS"):
        adapters.append(ElasticsearchAdapter.from_env())
    if os.environ.get("ETCD_CLUSTERS"):
        adapters.append(EtcdAdapter.from_env())
    if os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"):
        adapters.append(AliyunAdapter.from_env())
    if os.environ.get("TENCENTCLOUD_SECRET_ID") or os.environ.get("TC_SECRET_ID"):
        adapters.append(TencentCloudAdapter.from_env())
    if os.environ.get("BACKSTAGE_URL"):
        adapters.append(BackstageAdapter.from_env())
    if os.environ.get("HUAWEICLOUD_ACCESS_KEY") or os.environ.get("HW_AK"):
        adapters.append(HuaweiCloudAdapter.from_env())
    if os.environ.get("DIGITALOCEAN_TOKEN"):
        adapters.append(DigitalOceanAdapter.from_env())
    if os.environ.get("EUREKA_URL"):
        adapters.append(EurekaAdapter.from_env())
    if os.environ.get("NACOS_URL"):
        adapters.append(NacosAdapter.from_env())
    if os.environ.get("JDCLOUD_ACCESS_KEY"):
        adapters.append(JDCloudAdapter.from_env())
    if os.environ.get("VOLCENGINE_ACCESS_KEY"):
        adapters.append(VolcengineAdapter.from_env())
    if os.environ.get("VERCEL_TOKEN"):
        adapters.append(VercelAdapter.from_env())
    if os.environ.get("FLY_API_TOKEN"):
        adapters.append(FlyIOAdapter.from_env())
    if os.environ.get("RAILWAY_TOKEN"):
        adapters.append(RailwayAdapter.from_env())
    if os.environ.get("HEROKU_API_TOKEN"):
        adapters.append(HerokuAdapter.from_env())
    # ─── SaaS adapters ───
    if os.environ.get("CLOUDFLARE_API_TOKEN"):
        adapters.append(CloudflareAdapter.from_env())
    if os.environ.get("DD_API_KEY") and os.environ.get("DD_APP_KEY"):
        adapters.append(DatadogAdapter.from_env())
    if os.environ.get("SENTRY_AUTH_TOKEN") and os.environ.get("SENTRY_ORG"):
        adapters.append(SentryAdapter.from_env())
    if os.environ.get("SNOWFLAKE_ACCOUNT"):
        adapters.append(SnowflakeAdapter.from_env())
    if os.environ.get("PLANETSCALE_TOKEN") and os.environ.get("PLANETSCALE_ORG"):
        adapters.append(PlanetScaleAdapter.from_env())
    if os.environ.get("AUTH0_DOMAIN") and os.environ.get("AUTH0_MGMT_TOKEN"):
        adapters.append(Auth0Adapter.from_env())
    if os.environ.get("OKTA_DOMAIN") and os.environ.get("OKTA_API_TOKEN"):
        adapters.append(OktaAdapter.from_env())
    if os.environ.get("NETLIFY_TOKEN"):
        adapters.append(NetlifyAdapter.from_env())
    if os.environ.get("PAGERDUTY_TOKEN"):
        adapters.append(PagerDutyAdapter.from_env())
    if os.environ.get("OPSGENIE_API_KEY"):
        adapters.append(OpsGenieAdapter.from_env())
    if os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_ORG"):
        adapters.append(GitHubActionsAdapter.from_env())
    if os.environ.get("GITLAB_TOKEN"):
        adapters.append(GitLabCIAdapter.from_env())
    if os.environ.get("NEW_RELIC_API_KEY"):
        adapters.append(NewRelicAdapter.from_env())
    if os.environ.get("SPLUNK_URL") and os.environ.get("SPLUNK_TOKEN"):
        adapters.append(SplunkAdapter.from_env())
    # ─── orchestrator / runtime / monitoring servers ───
    if os.environ.get("NOMAD_ADDR"):
        adapters.append(NomadAdapter.from_env())
    if os.environ.get("RANCHER_URL") and os.environ.get("RANCHER_TOKEN"):
        adapters.append(RancherAdapter.from_env())
    if os.environ.get("HELM_DISCOVERY_ENABLED", "").lower() in ("1", "true"):
        adapters.append(HelmAdapter.from_env())
    if os.environ.get("ZABBIX_URL") and os.environ.get("ZABBIX_API_TOKEN"):
        adapters.append(ZabbixAdapter.from_env())
    if os.environ.get("NAGIOS_URL"):
        adapters.append(NagiosAdapter.from_env())
    if os.environ.get("OPENFAAS_URL"):
        adapters.append(OpenFaaSAdapter.from_env())
    if os.environ.get("KNATIVE_DISCOVERY_ENABLED", "").lower() in ("1", "true"):
        adapters.append(KnativeAdapter.from_env())
    # Virtual adapter — always on (provides 100% coverage of meta + tools)
    if os.environ.get("VIRTUAL_DISCOVERY_DISABLED", "").lower() not in ("1", "true"):
        adapters.append(VirtualAdapter.from_env())
    return adapters


def make_server() -> FastMCP:
    adapters = _build_adapters()
    registry = DiscoveryRegistry(
        adapters,
        cache_ttl_seconds=int(os.environ.get("DISCOVERY_CACHE_TTL", "300")),
    )

    mcp = FastMCP("srefix-discovery")

    @mcp.tool()
    def list_clusters(tech: str = "") -> list[dict]:
        """List discovered clusters (cached). Optionally filter by tech name (e.g. 'hbase')."""
        clusters = registry.discover(tech_filter=tech or None)
        return [
            {
                "id": c.id,
                "tech": c.tech,
                "host_count": len(c.hosts),
                "discovery_source": c.discovery_source,
                "tech_confidence": c.metadata.get("tech_confidence"),
                "tech_signal": c.metadata.get("tech_signal"),
            }
            for c in clusters
        ]

    @mcp.tool()
    def get_cluster(cluster_id: str) -> dict:
        """Return full cluster topology (all hosts + roles + tags + metadata)."""
        c = registry.get_cluster(cluster_id)
        if c is None:
            return {"error": f"Cluster '{cluster_id}' not found"}
        return c.to_dict()

    @mcp.tool()
    def list_hosts(
        cluster_id: str = "",
        role: str = "",
        tech: str = "",
        tag_kv: str = "",
    ) -> list[dict]:
        """Filter hosts across clusters.

        - cluster_id: limit to one cluster
        - role: e.g. 'regionserver', 'primary', 'broker'
        - tech: limit to clusters of this tech
        - tag_kv: 'key=value' or just 'value'/'key' to match host tags
        """
        pairs = registry.filter_hosts(
            cluster_id=cluster_id or None,
            role=role or None,
            tech=tech or None,
            tag_kv=tag_kv or None,
        )
        return [
            {**h.to_dict(), "cluster_tech": c.tech, "cluster_id": c.id}
            for (c, h) in pairs
        ]

    @mcp.tool()
    def list_discoverable_techs() -> list[str]:
        """Return distinct tech values currently visible across all adapters."""
        return sorted({c.tech for c in registry.discover()})

    @mcp.tool()
    def discover_now(tech: str = "") -> dict:
        """Force re-poll all adapters (bypasses cache). Returns summary with counts and errors."""
        clusters = registry.discover(tech_filter=tech or None, force=True)
        by_tech: dict[str, int] = {}
        for c in clusters:
            by_tech[c.tech] = by_tech.get(c.tech, 0) + 1
        return {
            "clusters_found": len(clusters),
            "by_tech": by_tech,
            "errors": registry.errors,
            "last_run": registry.last_run.isoformat() if registry.last_run else None,
        }

    @mcp.tool()
    def discovery_health() -> dict:
        """Adapter status, cache stats, last run timestamp, recent errors."""
        return {
            "adapters_enabled": [a.__class__.__name__ for a in adapters],
            "cache": registry.cache.stats(),
            "last_run": registry.last_run.isoformat() if registry.last_run else None,
            "errors": registry.errors,
        }

    return mcp


def run() -> None:
    make_server().run()
