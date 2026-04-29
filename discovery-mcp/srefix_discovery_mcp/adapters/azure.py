"""Azure managed-services adapter.

Services covered:
  Azure Database for PostgreSQL    → tech: postgres
  Azure Database for MySQL         → tech: mysql
  Azure Database for MariaDB       → tech: mariadb
  Azure SQL Database               → tech: sqlserver
  Azure Cache for Redis            → tech: redis
  Azure Cosmos DB                  → tech: azure-cosmosdb (multi-API: SQL/Mongo/Cassandra/Gremlin)
  Azure Event Hubs                 → tech: kafka (when Kafka protocol enabled) / event-hubs
  Azure Service Bus                → tech: azure-service-bus
  Azure HDInsight                  → tech: hbase / kafka / spark / hadoop / hive (per cluster type)

Auth: uses DefaultAzureCredential (managed identity → CLI → env → ...).
Set AZURE_SUBSCRIPTION_IDS env to enumerate.
"""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Cluster, Host

_HDINSIGHT_TYPE_MAP = {
    "hadoop": "hadoop", "hbase": "hbase", "kafka": "kafka",
    "spark": "spark", "interactivehive": "hive", "interactivequery": "hive",
}


def build_azure_pg_cluster(server: dict, sub_id: str) -> Cluster:
    name = server.get("name", "")
    rg = (server.get("id", "")
          .split("/resourceGroups/", 1)[-1].split("/", 1)[0]) if server.get("id") else ""
    cid = f"azure/{sub_id}/{rg}/azure-postgres/{name}"
    return Cluster(
        id=cid, tech="postgres",
        version=server.get("properties", {}).get("version"),
        hosts=[Host(
            fqdn=server.get("properties", {}).get("fullyQualifiedDomainName", name),
            address=None, port=5432, role="primary",
            tags={"sub_id": sub_id, "rg": rg,
                  "tier": server.get("sku", {}).get("tier", "")},
            cluster_id=cid,
            health=server.get("properties", {}).get("state", "unknown"),
        )],
        discovery_source="azure-postgres",
        metadata={"sub_id": sub_id, "resource_group": rg,
                  "tech_confidence": "high", "tech_signal": "azure-postgres"},
    )


def build_azure_mysql_cluster(server: dict, sub_id: str) -> Cluster:
    name = server.get("name", "")
    rg = (server.get("id", "")
          .split("/resourceGroups/", 1)[-1].split("/", 1)[0]) if server.get("id") else ""
    cid = f"azure/{sub_id}/{rg}/azure-mysql/{name}"
    return Cluster(
        id=cid, tech="mysql",
        version=server.get("properties", {}).get("version"),
        hosts=[Host(
            fqdn=server.get("properties", {}).get("fullyQualifiedDomainName", name),
            address=None, port=3306, role="primary",
            tags={"sub_id": sub_id, "rg": rg},
            cluster_id=cid,
            health=server.get("properties", {}).get("state", "unknown"),
        )],
        discovery_source="azure-mysql",
        metadata={"sub_id": sub_id, "resource_group": rg,
                  "tech_confidence": "high", "tech_signal": "azure-mysql"},
    )


def build_azure_sql_cluster(db: dict, sub_id: str) -> Cluster:
    name = db.get("name", "")
    rg = (db.get("id", "")
          .split("/resourceGroups/", 1)[-1].split("/", 1)[0]) if db.get("id") else ""
    cid = f"azure/{sub_id}/{rg}/azure-sql/{name}"
    return Cluster(
        id=cid, tech="sqlserver",
        version=db.get("properties", {}).get("version"),
        hosts=[Host(
            fqdn=db.get("properties", {}).get("fullyQualifiedDomainName") or name,
            address=None, port=1433, role="primary",
            tags={"sub_id": sub_id, "rg": rg,
                  "tier": db.get("sku", {}).get("tier", "")},
            cluster_id=cid,
            health=db.get("properties", {}).get("status", "unknown"),
        )],
        discovery_source="azure-sql",
        metadata={"sub_id": sub_id, "resource_group": rg,
                  "tech_confidence": "high", "tech_signal": "azure-sql"},
    )


def build_azure_redis_cluster(cache: dict, sub_id: str) -> Cluster:
    name = cache.get("name", "")
    rg = (cache.get("id", "")
          .split("/resourceGroups/", 1)[-1].split("/", 1)[0]) if cache.get("id") else ""
    cid = f"azure/{sub_id}/{rg}/azure-redis/{name}"
    props = cache.get("properties", {})
    hosts = [Host(
        fqdn=props.get("hostName") or name,
        address=None, port=props.get("sslPort") or props.get("port", 6379),
        role="primary",
        tags={"sub_id": sub_id, "rg": rg,
              "sku": cache.get("sku", {}).get("name", "")},
        cluster_id=cid,
        health=props.get("provisioningState", "unknown"),
    )]
    return Cluster(
        id=cid, tech="redis",
        version=props.get("redisVersion"),
        hosts=hosts, discovery_source="azure-redis",
        metadata={"sub_id": sub_id, "resource_group": rg,
                  "tech_confidence": "high", "tech_signal": "azure-cache-redis"},
    )


def build_azure_cosmosdb_cluster(account: dict, sub_id: str) -> Cluster:
    name = account.get("name", "")
    rg = (account.get("id", "")
          .split("/resourceGroups/", 1)[-1].split("/", 1)[0]) if account.get("id") else ""
    cid = f"azure/{sub_id}/{rg}/cosmos/{name}"
    api_kind = (account.get("kind") or "").lower()  # GlobalDocumentDB / MongoDB / etc.
    return Cluster(
        id=cid, tech="azure-cosmosdb",
        version=None,
        hosts=[Host(
            fqdn=account.get("properties", {}).get("documentEndpoint", name),
            address=None, port=443, role="account",
            tags={"sub_id": sub_id, "rg": rg, "api": api_kind},
            cluster_id=cid,
        )],
        discovery_source="azure-cosmos",
        metadata={"sub_id": sub_id, "resource_group": rg,
                  "api_kind": api_kind, "tech_confidence": "high",
                  "tech_signal": f"cosmosdb:{api_kind}"},
    )


def build_event_hubs_namespace(ns: dict, sub_id: str) -> Cluster:
    name = ns.get("name", "")
    rg = (ns.get("id", "")
          .split("/resourceGroups/", 1)[-1].split("/", 1)[0]) if ns.get("id") else ""
    cid = f"azure/{sub_id}/{rg}/eventhubs/{name}"
    kafka_enabled = (ns.get("properties", {}).get("kafkaEnabled")
                     if isinstance(ns.get("properties"), dict) else None)
    tech = "kafka" if kafka_enabled else "event-hubs"
    return Cluster(
        id=cid, tech=tech,
        hosts=[Host(
            fqdn=f"{name}.servicebus.windows.net", port=9093 if kafka_enabled else 5671,
            role="namespace",
            tags={"sub_id": sub_id, "rg": rg,
                  "kafka_enabled": str(bool(kafka_enabled))},
            cluster_id=cid,
        )],
        discovery_source="azure-event-hubs",
        metadata={"sub_id": sub_id, "resource_group": rg,
                  "tech_confidence": "high",
                  "tech_signal": "event-hubs-kafka" if kafka_enabled else "event-hubs"},
    )


def build_hdinsight_cluster(cluster_data: dict, sub_id: str) -> list[Cluster]:
    """HDInsight ships pre-packaged Hadoop ecosystem; emit per-app Cluster."""
    name = cluster_data.get("name", "")
    rg = (cluster_data.get("id", "")
          .split("/resourceGroups/", 1)[-1].split("/", 1)[0]) if cluster_data.get("id") else ""
    cluster_def = cluster_data.get("properties", {}).get("clusterDefinition", {}) or {}
    kind = (cluster_def.get("kind") or "").lower()
    tech = _HDINSIGHT_TYPE_MAP.get(kind)
    if not tech:
        return []
    cid = f"azure/{sub_id}/{rg}/hdinsight/{name}/{tech}"
    return [Cluster(
        id=cid, tech=tech,
        version=cluster_data.get("properties", {}).get("clusterVersion"),
        hosts=[Host(fqdn=name, role="cluster",
                    tags={"sub_id": sub_id, "rg": rg, "kind": kind},
                    cluster_id=cid)],
        discovery_source="azure-hdinsight",
        metadata={"sub_id": sub_id, "resource_group": rg,
                  "hdinsight_kind": kind, "tech_confidence": "high",
                  "tech_signal": f"hdinsight:{kind}"},
    )]


class AzureAdapter:
    def __init__(self, subscription_ids: list[str],
                 services: Optional[list[str]] = None):
        self.subscription_ids = subscription_ids
        self.services = set(services or [
            "postgres", "mysql", "sql", "redis", "cosmos", "eventhubs", "hdinsight",
            # extended
            "functions", "vm", "vnet", "dns", "service_bus", "key_vault",
            "app_gateway", "front_door", "aks", "traffic_manager",
            "logic_apps", "acr", "apim", "app_config", "app_insights",
        ])

    @classmethod
    def from_env(cls) -> "AzureAdapter":
        sub_ids = [s.strip() for s in os.environ.get("AZURE_SUBSCRIPTION_IDS", "").split(",")
                   if s.strip()]
        services = os.environ.get("AZURE_DISCOVERY_SERVICES", "")
        services_list = [s.strip() for s in services.split(",") if s.strip()] or None
        return cls(subscription_ids=sub_ids, services=services_list)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.subscription_ids:
            return []
        clusters: list[Cluster] = []
        for sub_id in self.subscription_ids:
            if "postgres" in self.services:
                clusters.extend(self._discover_postgres(sub_id))
            if "mysql" in self.services:
                clusters.extend(self._discover_mysql(sub_id))
            if "sql" in self.services:
                clusters.extend(self._discover_sql(sub_id))
            if "redis" in self.services:
                clusters.extend(self._discover_redis(sub_id))
            if "cosmos" in self.services:
                clusters.extend(self._discover_cosmos(sub_id))
            if "eventhubs" in self.services:
                clusters.extend(self._discover_event_hubs(sub_id))
            if "hdinsight" in self.services:
                clusters.extend(self._discover_hdinsight(sub_id))
            # ─── extended ───
            if "functions" in self.services:
                clusters.extend(self._discover_functions(sub_id))
            if "vm" in self.services:
                clusters.extend(self._discover_vm(sub_id))
            if "vnet" in self.services:
                clusters.extend(self._discover_vnet(sub_id))
            if "dns" in self.services:
                clusters.extend(self._discover_dns(sub_id))
            if "service_bus" in self.services:
                clusters.extend(self._discover_service_bus(sub_id))
            if "key_vault" in self.services:
                clusters.extend(self._discover_key_vault(sub_id))
            if "app_gateway" in self.services:
                clusters.extend(self._discover_app_gateway(sub_id))
            if "front_door" in self.services:
                clusters.extend(self._discover_front_door(sub_id))
            if "aks" in self.services:
                clusters.extend(self._discover_aks(sub_id))
            if "traffic_manager" in self.services:
                clusters.extend(self._discover_traffic_manager(sub_id))
            if "logic_apps" in self.services:
                clusters.extend(self._discover_logic_apps(sub_id))
            if "acr" in self.services:
                clusters.extend(self._discover_acr(sub_id))
            if "apim" in self.services:
                clusters.extend(self._discover_apim(sub_id))
            if "app_config" in self.services:
                clusters.extend(self._discover_app_config(sub_id))
            if "app_insights" in self.services:
                clusters.extend(self._discover_app_insights(sub_id))
        if tech_filter:
            clusters = [c for c in clusters if c.tech == tech_filter]
        return clusters

    def _credential(self):  # pragma: no cover (network)
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "azure-identity not installed; pip install 'srefix-discovery-mcp[azure]'"
            ) from e
        return DefaultAzureCredential()

    def _discover_postgres(self, sub_id: str):  # pragma: no cover
        try:
            from azure.mgmt.rdbms.postgresql_flexibleservers import PostgreSQLManagementClient  # type: ignore
        except ImportError:
            return
        try:
            client = PostgreSQLManagementClient(self._credential(), sub_id)
            for s in client.servers.list():
                # SDK returns a model object; coerce to dict-ish via .__dict__ / .as_dict()
                yield build_azure_pg_cluster(_to_dict(s), sub_id)
        except Exception:  # noqa
            return

    def _discover_mysql(self, sub_id: str):  # pragma: no cover
        try:
            from azure.mgmt.rdbms.mysql_flexibleservers import MySQLManagementClient  # type: ignore
        except ImportError:
            return
        try:
            client = MySQLManagementClient(self._credential(), sub_id)
            for s in client.servers.list():
                yield build_azure_mysql_cluster(_to_dict(s), sub_id)
        except Exception:  # noqa
            return

    def _discover_sql(self, sub_id: str):  # pragma: no cover
        try:
            from azure.mgmt.sql import SqlManagementClient  # type: ignore
        except ImportError:
            return
        try:
            client = SqlManagementClient(self._credential(), sub_id)
            for srv in client.servers.list():
                # list databases under each SQL server
                for db in client.databases.list_by_server(
                    resource_group_name=_rg_from_id(srv.id), server_name=srv.name
                ):
                    yield build_azure_sql_cluster(_to_dict(db), sub_id)
        except Exception:  # noqa
            return

    def _discover_redis(self, sub_id: str):  # pragma: no cover
        try:
            from azure.mgmt.redis import RedisManagementClient  # type: ignore
        except ImportError:
            return
        try:
            client = RedisManagementClient(self._credential(), sub_id)
            for cache in client.redis.list_by_subscription():
                yield build_azure_redis_cluster(_to_dict(cache), sub_id)
        except Exception:  # noqa
            return

    def _discover_cosmos(self, sub_id: str):  # pragma: no cover
        try:
            from azure.mgmt.cosmosdb import CosmosDBManagementClient  # type: ignore
        except ImportError:
            return
        try:
            client = CosmosDBManagementClient(self._credential(), sub_id)
            for acct in client.database_accounts.list():
                yield build_azure_cosmosdb_cluster(_to_dict(acct), sub_id)
        except Exception:  # noqa
            return

    def _discover_event_hubs(self, sub_id: str):  # pragma: no cover
        try:
            from azure.mgmt.eventhub import EventHubManagementClient  # type: ignore
        except ImportError:
            return
        try:
            client = EventHubManagementClient(self._credential(), sub_id)
            for ns in client.namespaces.list():
                yield build_event_hubs_namespace(_to_dict(ns), sub_id)
        except Exception:  # noqa
            return

    def _discover_hdinsight(self, sub_id: str):  # pragma: no cover
        try:
            from azure.mgmt.hdinsight import HDInsightManagementClient  # type: ignore
        except ImportError:
            return
        try:
            client = HDInsightManagementClient(self._credential(), sub_id)
            for cl in client.clusters.list():
                for c in build_hdinsight_cluster(_to_dict(cl), sub_id):
                    yield c
        except Exception:  # noqa
            return


def _to_dict(obj) -> dict:
    """Flatten an Azure SDK model into a plain dict (handles .as_dict() if available)."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "as_dict"):
        try:
            return obj.as_dict()
        except Exception:  # noqa
            pass
    return {k: v for k, v in (getattr(obj, "__dict__", {}) or {}).items()
            if not k.startswith("_")}


def _rg_from_id(resource_id: str) -> str:
    parts = (resource_id or "").split("/")
    if "resourceGroups" in parts:
        i = parts.index("resourceGroups")
        if i + 1 < len(parts):
            return parts[i + 1]
    return ""

    # ──────────────── extended-service discoverers ────────────────

    def _discover_functions(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_function_app
        try:
            from azure.mgmt.web import WebSiteManagementClient  # type: ignore
            client = WebSiteManagementClient(self._credential(), sub_id)
            for app in client.web_apps.list():
                if "functionapp" in (app.kind or "").lower():
                    yield build_azure_function_app(app, sub_id)
        except Exception:  # noqa
            return

    def _discover_vm(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_vm
        try:
            from azure.mgmt.compute import ComputeManagementClient  # type: ignore
            client = ComputeManagementClient(self._credential(), sub_id)
            for vm in client.virtual_machines.list_all():
                yield build_azure_vm(vm, sub_id)
        except Exception:  # noqa
            return

    def _discover_vnet(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_vnet
        try:
            from azure.mgmt.network import NetworkManagementClient  # type: ignore
            client = NetworkManagementClient(self._credential(), sub_id)
            for v in client.virtual_networks.list_all():
                yield build_azure_vnet(v, sub_id)
        except Exception:  # noqa
            return

    def _discover_dns(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_dns_zone
        try:
            from azure.mgmt.dns import DnsManagementClient  # type: ignore
            client = DnsManagementClient(self._credential(), sub_id)
            for z in client.zones.list():
                yield build_azure_dns_zone(z, sub_id)
        except Exception:  # noqa
            return

    def _discover_service_bus(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_service_bus_namespace
        try:
            from azure.mgmt.servicebus import ServiceBusManagementClient  # type: ignore
            client = ServiceBusManagementClient(self._credential(), sub_id)
            for ns in client.namespaces.list():
                yield build_azure_service_bus_namespace(ns, sub_id)
        except Exception:  # noqa
            return

    def _discover_key_vault(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_key_vault
        try:
            from azure.mgmt.keyvault import KeyVaultManagementClient  # type: ignore
            client = KeyVaultManagementClient(self._credential(), sub_id)
            for v in client.vaults.list():
                yield build_azure_key_vault(v, sub_id)
        except Exception:  # noqa
            return

    def _discover_app_gateway(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_app_gateway
        try:
            from azure.mgmt.network import NetworkManagementClient  # type: ignore
            client = NetworkManagementClient(self._credential(), sub_id)
            for ag in client.application_gateways.list_all():
                yield build_azure_app_gateway(ag, sub_id)
        except Exception:  # noqa
            return

    def _discover_front_door(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_front_door
        try:
            from azure.mgmt.frontdoor import FrontDoorManagementClient  # type: ignore
            client = FrontDoorManagementClient(self._credential(), sub_id)
            for fd in client.front_doors.list():
                yield build_azure_front_door(fd, sub_id)
        except Exception:  # noqa
            return

    def _discover_aks(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_aks
        try:
            from azure.mgmt.containerservice import ContainerServiceClient  # type: ignore
            client = ContainerServiceClient(self._credential(), sub_id)
            for c in client.managed_clusters.list():
                yield build_azure_aks(c, sub_id)
        except Exception:  # noqa
            return

    def _discover_traffic_manager(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_traffic_manager
        try:
            from azure.mgmt.trafficmanager import TrafficManagerManagementClient  # type: ignore
            client = TrafficManagerManagementClient(self._credential(), sub_id)
            for tm in client.profiles.list_by_subscription():
                yield build_azure_traffic_manager(tm, sub_id)
        except Exception:  # noqa
            return

    def _discover_logic_apps(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_logic_app
        try:
            from azure.mgmt.logic import LogicManagementClient  # type: ignore
            client = LogicManagementClient(self._credential(), sub_id)
            for la in client.workflows.list_by_subscription():
                yield build_azure_logic_app(la, sub_id)
        except Exception:  # noqa
            return

    def _discover_acr(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_acr
        try:
            from azure.mgmt.containerregistry import ContainerRegistryManagementClient  # type: ignore
            client = ContainerRegistryManagementClient(self._credential(), sub_id)
            for r in client.registries.list():
                yield build_azure_acr(r, sub_id)
        except Exception:  # noqa
            return

    def _discover_apim(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_apim
        try:
            from azure.mgmt.apimanagement import ApiManagementClient  # type: ignore
            client = ApiManagementClient(self._credential(), sub_id)
            for a in client.api_management_service.list():
                yield build_azure_apim(a, sub_id)
        except Exception:  # noqa
            return

    def _discover_app_config(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_app_config
        try:
            from azure.mgmt.appconfiguration import AppConfigurationManagementClient  # type: ignore
            client = AppConfigurationManagementClient(self._credential(), sub_id)
            for ac in client.configuration_stores.list():
                yield build_azure_app_config(ac, sub_id)
        except Exception:  # noqa
            return

    def _discover_app_insights(self, sub_id: str):  # pragma: no cover
        from .azure_extended import build_azure_app_insights
        try:
            from azure.mgmt.applicationinsights import ApplicationInsightsManagementClient  # type: ignore
            client = ApplicationInsightsManagementClient(self._credential(), sub_id)
            for ai in client.components.list():
                yield build_azure_app_insights(ai, sub_id)
        except Exception:  # noqa
            return
