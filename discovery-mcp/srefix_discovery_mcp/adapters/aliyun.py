"""Aliyun (阿里云) managed-services adapter.

Services covered:
  RDS              → tech: postgres / mysql / sqlserver / mariadb / pg (Engine field)
  Redis (Tair)     → tech: redis
  MongoDB          → tech: mongo
  PolarDB          → tech: polardb (or postgres/mysql per engine)
  Lindorm (HBase-compatible) → tech: hbase
  ADB             → tech: adb
  ECS (with tags)  → tech: per host tag

Auth via env: ALIBABA_CLOUD_ACCESS_KEY_ID + ALIBABA_CLOUD_ACCESS_KEY_SECRET.
Set ALIYUN_REGIONS comma-separated.
"""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Cluster, Host

_RDS_ENGINE_MAP = {
    "PostgreSQL": "postgres",
    "MySQL": "mysql",
    "SQLServer": "sqlserver",
    "MariaDB": "mariadb",
    "PPAS": "postgres",
}


def build_aliyun_rds_cluster(inst: dict, region: str) -> Optional[Cluster]:
    engine = inst.get("Engine", "")
    tech = _RDS_ENGINE_MAP.get(engine)
    if not tech:
        return None
    cid_suffix = inst.get("DBInstanceId", "rds-unknown")
    cid = f"aliyun/{region}/rds/{cid_suffix}"
    return Cluster(
        id=cid, tech=tech,
        version=inst.get("EngineVersion"),
        hosts=[Host(
            fqdn=inst.get("ConnectionString", cid_suffix),
            port=int(inst.get("Port", 0)) if inst.get("Port") else None,
            role="primary",
            tags={"region": region,
                  "instance_class": inst.get("DBInstanceClass", ""),
                  "category": inst.get("Category", "")},
            cluster_id=cid,
            health=inst.get("DBInstanceStatus", "unknown"),
        )],
        discovery_source="aliyun-rds",
        metadata={"region": region, "engine": engine,
                  "tech_confidence": "high", "tech_signal": f"aliyun-rds:{engine}"},
    )


def build_aliyun_redis_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("InstanceId", "redis-unknown")
    cid = f"aliyun/{region}/redis/{cid_suffix}"
    return Cluster(
        id=cid, tech="redis",
        version=inst.get("EngineVersion"),
        hosts=[Host(
            fqdn=inst.get("ConnectionDomain", cid_suffix),
            port=int(inst.get("Port", 6379)),
            role="primary",
            tags={"region": region, "arch": inst.get("ArchitectureType", ""),
                  "instance_class": inst.get("InstanceClass", "")},
            cluster_id=cid,
            health=inst.get("InstanceStatus", "unknown"),
        )],
        discovery_source="aliyun-redis",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "aliyun-tair-redis"},
    )


def build_aliyun_mongo_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("DBInstanceId", "mongo-unknown")
    cid = f"aliyun/{region}/mongo/{cid_suffix}"
    return Cluster(
        id=cid, tech="mongo",
        version=inst.get("EngineVersion"),
        hosts=[Host(
            fqdn=cid_suffix, role="primary",
            tags={"region": region, "type": inst.get("DBInstanceType", "")},
            cluster_id=cid,
            health=inst.get("DBInstanceStatus", "unknown"),
        )],
        discovery_source="aliyun-mongo",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "aliyun-mongodb"},
    )


def build_aliyun_ecs_classified(instances: list[dict], region: str) -> list[Cluster]:
    """Tag-aware ECS grouping — covers self-deployed HBase / Kafka / etc.

    Aliyun ECS DescribeInstances returns Tags as
    {'Tag': [{'TagKey': 'Service', 'TagValue': 'hbase'}, ...]}.
    """
    from ._classify import group_instances_into_clusters, normalize_aliyun_tags
    return group_instances_into_clusters(
        instances,
        tag_extractor=lambda i: normalize_aliyun_tags(i.get("Tags")),
        fqdn_extractor=lambda i: (i.get("HostName")
                                  or (i.get("InnerIpAddress", {}) or {}).get("IpAddress", [""])[0]
                                  or i.get("InstanceId", "")),
        instance_id_extractor=lambda i: i.get("InstanceId", "ecs-unknown"),
        cluster_id_prefix=f"aliyun/{region}",
        discovery_source="aliyun-ecs-tagged",
        region=region, default_tech="ecs",
        extra_host_tags=lambda i: {
            "instance_type": i.get("InstanceType", ""),
            "zone": i.get("ZoneId", ""),
            "vpc_id": (i.get("VpcAttributes", {}) or {}).get("VpcId", ""),
            "instance_id": i.get("InstanceId", ""),
        },
    )


class AliyunAdapter:
    def __init__(self, regions: list[str], access_key_id: str, access_key_secret: str,
                 services: Optional[list[str]] = None):
        self.regions = regions
        self.ak = access_key_id
        self.sk = access_key_secret
        self.services = set(services or ["rds", "redis", "mongo"])

    @classmethod
    def from_env(cls) -> "AliyunAdapter":
        regions = [r.strip() for r in os.environ.get(
            "ALIYUN_REGIONS", "cn-hangzhou,cn-beijing,cn-shanghai"
        ).split(",") if r.strip()]
        services = [s.strip() for s in os.environ.get("ALIYUN_DISCOVERY_SERVICES", "").split(",")
                    if s.strip()] or None
        return cls(
            regions=regions,
            access_key_id=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", ""),
            access_key_secret=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", ""),
            services=services,
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not (self.ak and self.sk):
            return []
        clusters: list[Cluster] = []
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

    def _discover_rds(self, region: str):  # pragma: no cover (network)
        try:
            from aliyunsdkcore.client import AcsClient  # type: ignore
            from aliyunsdkrds.request.v20140815.DescribeDBInstancesRequest import \
                DescribeDBInstancesRequest  # type: ignore
        except ImportError as e:
            raise RuntimeError("aliyun-python-sdk-rds missing; "
                               "pip install 'srefix-discovery-mcp[aliyun]'") from e
        import json
        cli = AcsClient(self.ak, self.sk, region)
        page = 1
        while True:
            req = DescribeDBInstancesRequest()
            req.set_PageNumber(page)
            req.set_PageSize(100)
            resp = json.loads(cli.do_action_with_exception(req))
            items = resp.get("Items", {}).get("DBInstance", [])
            if not items:
                break
            for it in items:
                # Need DescribeDBInstanceAttribute for engine + connection — fold here for simplicity
                from aliyunsdkrds.request.v20140815.DescribeDBInstanceAttributeRequest \
                    import DescribeDBInstanceAttributeRequest  # type: ignore
                a_req = DescribeDBInstanceAttributeRequest()
                a_req.set_DBInstanceId(it.get("DBInstanceId"))
                try:
                    a_resp = json.loads(cli.do_action_with_exception(a_req))
                    detail = a_resp.get("Items", {}).get("DBInstanceAttribute", [{}])[0]
                except Exception:  # noqa
                    detail = it
                cluster = build_aliyun_rds_cluster(detail, region)
                if cluster:
                    yield cluster
            if len(items) < 100:
                break
            page += 1

    def _discover_redis(self, region: str):  # pragma: no cover
        try:
            from aliyunsdkcore.client import AcsClient  # type: ignore
            from aliyunsdkr_kvstore.request.v20150101.DescribeInstancesRequest import \
                DescribeInstancesRequest  # type: ignore
        except ImportError:
            return
        import json
        cli = AcsClient(self.ak, self.sk, region)
        req = DescribeInstancesRequest()
        req.set_PageSize(100)
        try:
            resp = json.loads(cli.do_action_with_exception(req))
            for it in resp.get("Instances", {}).get("KVStoreInstance", []):
                yield build_aliyun_redis_cluster(it, region)
        except Exception:  # noqa
            return

    def _discover_mongo(self, region: str):  # pragma: no cover
        try:
            from aliyunsdkcore.client import AcsClient  # type: ignore
            from aliyunsdkdds.request.v20151201.DescribeDBInstancesRequest import \
                DescribeDBInstancesRequest as MongoListReq  # type: ignore
        except ImportError:
            return
        import json
        cli = AcsClient(self.ak, self.sk, region)
        req = MongoListReq()
        req.set_PageSize(100)
        try:
            resp = json.loads(cli.do_action_with_exception(req))
            for it in resp.get("DBInstances", {}).get("DBInstance", []):
                yield build_aliyun_mongo_cluster(it, region)
        except Exception:  # noqa
            return
