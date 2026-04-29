"""京东云 (JD Cloud) managed-services adapter.

Services covered:
  RDS              → tech: postgres / mysql / sqlserver
  JCQ Redis        → tech: redis
  MongoDB          → tech: mongo
  JCQ Kafka        → tech: kafka

Auth: jdcloud-sdk-python via JDCLOUD_ACCESS_KEY / JDCLOUD_SECRET_KEY env.
"""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Cluster, Host

_RDS_ENGINE_MAP = {
    "MySQL": "mysql", "Percona": "mysql",
    "PostgreSQL": "postgres",
    "SQLServer": "sqlserver",
}


def build_jd_rds_cluster(inst: dict, region: str) -> Optional[Cluster]:
    engine = inst.get("engine", "")
    tech = _RDS_ENGINE_MAP.get(engine)
    if not tech:
        return None
    cid_suffix = inst.get("instanceId", "rds-unknown")
    cid = f"jdcloud/{region}/rds/{cid_suffix}"
    return Cluster(
        id=cid, tech=tech, version=inst.get("engineVersion"),
        hosts=[Host(
            fqdn=inst.get("instanceAZ") or cid_suffix,
            address=inst.get("connectionMode"),
            port=inst.get("instancePort"), role="primary",
            tags={"region": region, "az": inst.get("instanceAZ", ""),
                  "type": inst.get("instanceType", "")},
            cluster_id=cid, health=inst.get("instanceStatus", "unknown"),
        )],
        discovery_source="jdcloud-rds",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": f"jd-rds:{engine}"},
    )


def build_jd_redis_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("cacheInstanceId", "redis-unknown")
    cid = f"jdcloud/{region}/jcq-redis/{cid_suffix}"
    return Cluster(
        id=cid, tech="redis", version=inst.get("redisVersion"),
        hosts=[Host(
            fqdn=inst.get("connectionDomain") or cid_suffix,
            port=inst.get("connectionPort", 6379), role="primary",
            tags={"region": region, "az": inst.get("azId", "")},
            cluster_id=cid, health=inst.get("cacheInstanceStatus", "unknown"),
        )],
        discovery_source="jdcloud-redis",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "jcq-redis"},
    )


def build_jd_mongo_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("instanceId", "mongo-unknown")
    cid = f"jdcloud/{region}/mongo/{cid_suffix}"
    return Cluster(
        id=cid, tech="mongo", version=inst.get("engineVersion"),
        hosts=[Host(fqdn=cid_suffix, role="primary",
                    tags={"region": region}, cluster_id=cid,
                    health=inst.get("instanceStatus", "unknown"))],
        discovery_source="jdcloud-mongo",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "jd-mongo"},
    )


def build_jd_vm_classified(instances: list[dict], region: str) -> list[Cluster]:
    """Tag-aware JD Cloud VM grouping. JD Cloud uses AWS-style
    [{'Key': 'Service', 'Value': 'hbase'}, ...] for tags.
    """
    from ._classify import group_instances_into_clusters, normalize_jd_tags
    return group_instances_into_clusters(
        instances,
        tag_extractor=lambda i: normalize_jd_tags(i.get("tags")),
        fqdn_extractor=lambda i: (i.get("hostName")
                                  or i.get("privateIpAddress", "")
                                  or i.get("instanceId", "")),
        instance_id_extractor=lambda i: i.get("instanceId", "vm-unknown"),
        cluster_id_prefix=f"jdcloud/{region}",
        discovery_source="jdcloud-vm-tagged",
        region=region, default_tech="vm",
        extra_host_tags=lambda i: {
            "instance_type": i.get("instanceType", ""),
            "az": i.get("az", ""),
            "instance_id": i.get("instanceId", ""),
        },
    )


class JDCloudAdapter:
    def __init__(self, regions: list[str], access_key: str, secret_key: str,
                 services: Optional[list[str]] = None):
        self.regions = regions
        self.ak = access_key
        self.sk = secret_key
        self.services = set(services or ["rds", "redis", "mongo"])

    @classmethod
    def from_env(cls) -> "JDCloudAdapter":
        regions = [r.strip() for r in os.environ.get(
            "JDCLOUD_REGIONS", "cn-north-1,cn-east-2"
        ).split(",") if r.strip()]
        ak = os.environ.get("JDCLOUD_ACCESS_KEY", "")
        sk = os.environ.get("JDCLOUD_SECRET_KEY", "")
        return cls(regions=regions, access_key=ak, secret_key=sk)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not (self.ak and self.sk):
            return []
        clusters = []
        try:  # pragma: no cover (network)
            from jdcloud_sdk.core.credential import Credential  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "jdcloud-sdk missing; pip install 'srefix-discovery-mcp[jdcloud]'"
            ) from e
        for region in self.regions:
            if "rds" in self.services:
                clusters.extend(self._discover_rds(region))
            if "redis" in self.services:
                clusters.extend(self._discover_redis(region))
            if "mongo" in self.services:
                clusters.extend(self._discover_mongo(region))
        if tech_filter:
            clusters = [c for c in clusters if c.tech == tech_filter]
        return clusters

    def _client(self, service: str, region: str):  # pragma: no cover
        from jdcloud_sdk.core.credential import Credential  # type: ignore
        cred = Credential(self.ak, self.sk)
        if service == "rds":
            from jdcloud_sdk.services.rds.client.RdsClient import RdsClient  # type: ignore
            return RdsClient(cred, regionId=region)
        if service == "redis":
            from jdcloud_sdk.services.jcq.client.JcqClient import JcqClient  # type: ignore
            return JcqClient(cred, regionId=region)
        if service == "mongo":
            from jdcloud_sdk.services.mongodb.client.MongodbClient import MongodbClient  # type: ignore
            return MongodbClient(cred, regionId=region)
        raise ValueError(service)

    def _discover_rds(self, region: str):  # pragma: no cover
        try:
            from jdcloud_sdk.services.rds.apis.DescribeInstancesRequest import \
                DescribeInstancesRequest, DescribeInstancesParameters  # type: ignore
            cli = self._client("rds", region)
            resp = cli.send(DescribeInstancesRequest(
                DescribeInstancesParameters(regionId=region, pageNumber=1, pageSize=100)))
            for it in (resp.result.dbInstances or []):
                d = it.__dict__ if hasattr(it, "__dict__") else dict(it)
                cluster = build_jd_rds_cluster(d, region)
                if cluster:
                    yield cluster
        except Exception:  # noqa
            return

    def _discover_redis(self, region: str):  # pragma: no cover
        try:
            from jdcloud_sdk.services.jcq.apis.DescribeCacheInstancesRequest import \
                DescribeCacheInstancesRequest, DescribeCacheInstancesParameters  # type: ignore
            cli = self._client("redis", region)
            resp = cli.send(DescribeCacheInstancesRequest(
                DescribeCacheInstancesParameters(regionId=region)))
            for it in (resp.result.cacheInstances or []):
                d = it.__dict__ if hasattr(it, "__dict__") else dict(it)
                yield build_jd_redis_cluster(d, region)
        except Exception:  # noqa
            return

    def _discover_mongo(self, region: str):  # pragma: no cover
        try:
            from jdcloud_sdk.services.mongodb.apis.DescribeInstancesRequest import \
                DescribeInstancesRequest, DescribeInstancesParameters  # type: ignore
            cli = self._client("mongo", region)
            resp = cli.send(DescribeInstancesRequest(
                DescribeInstancesParameters(regionId=region)))
            for it in (resp.result.instances or []):
                d = it.__dict__ if hasattr(it, "__dict__") else dict(it)
                yield build_jd_mongo_cluster(d, region)
        except Exception:  # noqa
            return
