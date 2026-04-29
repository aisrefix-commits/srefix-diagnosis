"""字节火山引擎 (Volcengine) managed-services adapter.

Services covered:
  RDS              → tech: postgres / mysql / sqlserver
  Redis            → tech: redis
  MongoDB          → tech: mongo
  Kafka (BMQ)      → tech: kafka
  HBase / EMR      → tech: hbase / spark / hadoop / hive

Auth: volcengine-python-sdk via VOLCENGINE_ACCESS_KEY / VOLCENGINE_SECRET_KEY env.
"""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Cluster, Host

_RDS_ENGINE_MAP = {
    "MySQL": "mysql", "PostgreSQL": "postgres", "SQLServer": "sqlserver",
}


def build_volc_rds_cluster(inst: dict, region: str) -> Optional[Cluster]:
    engine = inst.get("DBEngine") or inst.get("Engine", "")
    tech = _RDS_ENGINE_MAP.get(engine)
    if not tech:
        return None
    cid_suffix = inst.get("InstanceId", "rds-unknown")
    cid = f"volcengine/{region}/rds/{cid_suffix}"
    return Cluster(
        id=cid, tech=tech,
        version=inst.get("DBEngineVersion") or inst.get("EngineVersion"),
        hosts=[Host(
            fqdn=inst.get("ConnectionInfo", {}).get("InternalEndpoint") or cid_suffix,
            address=inst.get("ConnectionInfo", {}).get("InternalEndpoint"),
            port=inst.get("ConnectionInfo", {}).get("InternalPort"), role="primary",
            tags={"region": region, "spec": inst.get("InstanceSpecName", "")},
            cluster_id=cid, health=inst.get("InstanceStatus", "unknown"),
        )],
        discovery_source="volcengine-rds",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": f"volc-rds:{engine}"},
    )


def build_volc_redis_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("InstanceId", "redis-unknown")
    cid = f"volcengine/{region}/redis/{cid_suffix}"
    return Cluster(
        id=cid, tech="redis", version=inst.get("EngineVersion"),
        hosts=[Host(
            fqdn=inst.get("VisitAddrs", [{}])[0].get("Address", cid_suffix)
                 if inst.get("VisitAddrs") else cid_suffix,
            port=inst.get("VisitAddrs", [{}])[0].get("Port", 6379)
                 if inst.get("VisitAddrs") else 6379,
            role="primary",
            tags={"region": region}, cluster_id=cid,
            health=inst.get("Status", "unknown"),
        )],
        discovery_source="volcengine-redis",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "volc-redis"},
    )


def build_volc_mongo_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("InstanceId", "mongo-unknown")
    cid = f"volcengine/{region}/mongo/{cid_suffix}"
    return Cluster(
        id=cid, tech="mongo", version=inst.get("DBEngineVersion"),
        hosts=[Host(fqdn=cid_suffix, role="primary",
                    tags={"region": region,
                          "type": inst.get("InstanceType", "")},
                    cluster_id=cid,
                    health=inst.get("InstanceStatus", "unknown"))],
        discovery_source="volcengine-mongo",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "volc-mongo"},
    )


def build_volc_kafka_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("InstanceId", "kafka-unknown")
    cid = f"volcengine/{region}/kafka/{cid_suffix}"
    return Cluster(
        id=cid, tech="kafka", version=inst.get("Version"),
        hosts=[Host(fqdn=cid_suffix, role="cluster",
                    tags={"region": region}, cluster_id=cid,
                    health=inst.get("InstanceStatus", "unknown"))],
        discovery_source="volcengine-kafka",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "volc-kafka"},
    )


def build_volc_ecs_classified(instances: list[dict], region: str) -> list[Cluster]:
    """Tag-aware Volcengine ECS grouping. Tags follow AWS shape
    [{'Key': 'Service', 'Value': 'hbase'}, ...].
    """
    from ._classify import group_instances_into_clusters, normalize_volc_tags
    return group_instances_into_clusters(
        instances,
        tag_extractor=lambda i: normalize_volc_tags(i.get("Tags")),
        fqdn_extractor=lambda i: (i.get("Hostname")
                                  or i.get("InstanceName", "")
                                  or i.get("InstanceId", "")),
        instance_id_extractor=lambda i: i.get("InstanceId", "ecs-unknown"),
        cluster_id_prefix=f"volcengine/{region}",
        discovery_source="volcengine-ecs-tagged",
        region=region, default_tech="ecs",
        extra_host_tags=lambda i: {
            "instance_type": i.get("InstanceTypeId", ""),
            "zone": i.get("ZoneId", ""),
            "vpc_id": i.get("VpcId", ""),
            "instance_id": i.get("InstanceId", ""),
        },
    )


class VolcengineAdapter:
    def __init__(self, regions: list[str], access_key: str, secret_key: str,
                 services: Optional[list[str]] = None):
        self.regions = regions
        self.ak = access_key
        self.sk = secret_key
        self.services = set(services or ["rds", "redis", "mongo", "kafka"])

    @classmethod
    def from_env(cls) -> "VolcengineAdapter":
        regions = [r.strip() for r in os.environ.get(
            "VOLCENGINE_REGIONS", "cn-beijing,cn-shanghai,cn-guangzhou"
        ).split(",") if r.strip()]
        ak = os.environ.get("VOLCENGINE_ACCESS_KEY", "")
        sk = os.environ.get("VOLCENGINE_SECRET_KEY", "")
        return cls(regions=regions, access_key=ak, secret_key=sk)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not (self.ak and self.sk):
            return []
        clusters = []
        for region in self.regions:
            if "rds" in self.services:
                clusters.extend(self._discover_rds(region))
            if "redis" in self.services:
                clusters.extend(self._discover_redis(region))
            if "mongo" in self.services:
                clusters.extend(self._discover_mongo(region))
            if "kafka" in self.services:
                clusters.extend(self._discover_kafka(region))
        if tech_filter:
            clusters = [c for c in clusters if c.tech == tech_filter]
        return clusters

    def _discover_rds(self, region: str):  # pragma: no cover (network)
        try:
            from volcenginesdkrdsmysqlv2 import RDSMYSQLV2Api  # type: ignore
            from volcenginesdkrdsmysqlv2.models.describe_db_instances_request \
                import DescribeDBInstancesRequest  # type: ignore
            api = RDSMYSQLV2Api()  # picks up creds from env
            req = DescribeDBInstancesRequest(region_id=region, page_size=100)
            resp = api.describe_db_instances(req)
            for it in (resp.instances or []):
                d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                cluster = build_volc_rds_cluster(d, region)
                if cluster:
                    yield cluster
        except Exception:  # noqa
            return

    def _discover_redis(self, region: str):  # pragma: no cover
        try:
            from volcenginesdkredis import REDISApi  # type: ignore
            from volcenginesdkredis.models.describe_db_instances_request \
                import DescribeDBInstancesRequest  # type: ignore
            api = REDISApi()
            resp = api.describe_db_instances(
                DescribeDBInstancesRequest(region_id=region, page_size=100))
            for it in (resp.instances or []):
                d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                yield build_volc_redis_cluster(d, region)
        except Exception:  # noqa
            return

    def _discover_mongo(self, region: str):  # pragma: no cover
        try:
            from volcenginesdkmongodb import MONGODBApi  # type: ignore
            from volcenginesdkmongodb.models.describe_db_instances_request \
                import DescribeDBInstancesRequest  # type: ignore
            api = MONGODBApi()
            resp = api.describe_db_instances(
                DescribeDBInstancesRequest(region_id=region, page_size=100))
            for it in (resp.db_instances or []):
                d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                yield build_volc_mongo_cluster(d, region)
        except Exception:  # noqa
            return

    def _discover_kafka(self, region: str):  # pragma: no cover
        try:
            from volcenginesdkkafka import KAFKAApi  # type: ignore
            from volcenginesdkkafka.models.describe_instances_request \
                import DescribeInstancesRequest  # type: ignore
            api = KAFKAApi()
            resp = api.describe_instances(
                DescribeInstancesRequest(region_id=region, page_size=100))
            for it in (resp.instances or []):
                d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                yield build_volc_kafka_cluster(d, region)
        except Exception:  # noqa
            return
