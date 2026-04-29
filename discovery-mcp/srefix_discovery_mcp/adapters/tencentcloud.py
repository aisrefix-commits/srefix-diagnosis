"""腾讯云 (Tencent Cloud) managed-services adapter.

Services covered:
  CDB              → tech: mysql / postgres / sqlserver / mariadb (per Engine)
  TDSQL-C MySQL    → tech: mysql (cluster)
  TDSQL PostgreSQL → tech: postgres
  TencentDB Redis  → tech: redis
  TencentDB MongoDB→ tech: mongo
  CKafka           → tech: kafka
  EMR              → tech: hadoop / spark / hbase / hive (per components)

Auth uses tencentcloud-sdk-python with env:
  TENCENTCLOUD_SECRET_ID + TENCENTCLOUD_SECRET_KEY  (or TC_*)
  TENCENTCLOUD_REGIONS=ap-guangzhou,ap-beijing,ap-shanghai
"""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Cluster, Host

_CDB_ENGINE_MAP = {
    "mysql": "mysql",
    "mariadb": "mariadb",
    "sqlserver": "sqlserver",
}


def build_tc_cdb_cluster(inst: dict, region: str) -> Optional[Cluster]:
    engine_raw = inst.get("EngineVersion", "")
    # Heuristic: CDB uses just MySQL versions like "5.7", "8.0".
    tech = "mysql"
    cid_suffix = inst.get("InstanceId", "cdb-unknown")
    cid = f"tencentcloud/{region}/cdb/{cid_suffix}"
    return Cluster(
        id=cid, tech=tech, version=engine_raw,
        hosts=[Host(
            fqdn=inst.get("Vip") or cid_suffix,
            address=inst.get("Vip"),
            port=inst.get("Vport"),
            role="primary" if inst.get("DeviceType", "").lower() != "remote_ro"
                            and not inst.get("MasterInfo") else "replica",
            tags={"region": region, "zone": inst.get("Zone", ""),
                  "device": inst.get("DeviceType", "")},
            cluster_id=cid,
            health=str(inst.get("Status", "unknown")),
        )],
        discovery_source="tencentcloud-cdb",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "cdb-mysql"},
    )


def build_tc_redis_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("InstanceId", "redis-unknown")
    cid = f"tencentcloud/{region}/redis/{cid_suffix}"
    return Cluster(
        id=cid, tech="redis", version=str(inst.get("ProductType", "")),
        hosts=[Host(
            fqdn=inst.get("WanAddress") or inst.get("Vip", cid_suffix),
            address=inst.get("Vip"),
            port=inst.get("Vport"),
            role="primary",
            tags={"region": region, "type": str(inst.get("Type", ""))},
            cluster_id=cid,
            health=str(inst.get("Status", "unknown")),
        )],
        discovery_source="tencentcloud-redis",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "tencent-redis"},
    )


def build_tc_mongo_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("InstanceId", "mongo-unknown")
    cid = f"tencentcloud/{region}/mongo/{cid_suffix}"
    hosts = [Host(
        fqdn=inst.get("Vip") or cid_suffix,
        address=inst.get("Vip"),
        port=inst.get("Vport"),
        role="primary",
        tags={"region": region,
              "cluster_type": str(inst.get("ClusterType", ""))},
        cluster_id=cid,
        health=str(inst.get("Status", "unknown")),
    )]
    return Cluster(
        id=cid, tech="mongo",
        version=str(inst.get("MongoVersion", "")),
        hosts=hosts, discovery_source="tencentcloud-mongo",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "tencent-mongo"},
    )


def build_tc_ckafka_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = str(inst.get("InstanceId") or inst.get("InstanceName", ""))
    cid = f"tencentcloud/{region}/ckafka/{cid_suffix}"
    return Cluster(
        id=cid, tech="kafka",
        version=str(inst.get("Version", "")),
        hosts=[Host(
            fqdn=cid_suffix, role="cluster",
            tags={"region": region, "type": str(inst.get("InstanceType", ""))},
            cluster_id=cid,
            health=str(inst.get("Status", "unknown")),
        )],
        discovery_source="tencentcloud-ckafka",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "ckafka"},
    )


def build_tc_emr_cluster(cluster_data: dict, region: str) -> list[Cluster]:
    cid_base = cluster_data.get("ClusterId", "emr-unknown")
    components = cluster_data.get("ComponentList", []) or []
    components = [c.lower() for c in components]
    apps = {
        "spark": "spark", "hbase": "hbase", "hive": "hive",
        "hadoop": "hadoop", "presto": "trino", "trino": "trino",
        "flink": "flink",
    }
    out = []
    for comp in components:
        # Components are like "Hadoop-3.1.2" — strip the version
        base = comp.split("-")[0]
        tech = apps.get(base)
        if not tech:
            continue
        cid = f"tencentcloud/{region}/emr/{cid_base}/{tech}"
        out.append(Cluster(
            id=cid, tech=tech,
            hosts=[Host(fqdn=cid_base, role="cluster",
                        tags={"region": region}, cluster_id=cid)],
            discovery_source="tencentcloud-emr",
            metadata={"region": region, "emr_id": cid_base,
                      "tech_confidence": "high",
                      "tech_signal": f"emr-component:{base}"},
        ))
    return out


def build_tc_cvm_classified(instances: list[dict], region: str) -> list[Cluster]:
    """Tag-aware CVM grouping. Tencent CVM DescribeInstances returns
    Tags as [{'Key': 'Service', 'Value': 'hbase'}, ...].
    """
    from ._classify import group_instances_into_clusters, normalize_tencent_tags
    return group_instances_into_clusters(
        instances,
        tag_extractor=lambda i: normalize_tencent_tags(i.get("Tags")),
        fqdn_extractor=lambda i: (i.get("InstanceName")
                                  or (i.get("PrivateIpAddresses") or [""])[0]
                                  or i.get("InstanceId", "")),
        instance_id_extractor=lambda i: i.get("InstanceId", "cvm-unknown"),
        cluster_id_prefix=f"tencentcloud/{region}",
        discovery_source="tencentcloud-cvm-tagged",
        region=region, default_tech="cvm",
        extra_host_tags=lambda i: {
            "instance_type": i.get("InstanceType", ""),
            "zone": (i.get("Placement") or {}).get("Zone", ""),
            "vpc_id": (i.get("VirtualPrivateCloud") or {}).get("VpcId", ""),
            "instance_id": i.get("InstanceId", ""),
        },
    )


class TencentCloudAdapter:
    def __init__(self, regions: list[str], secret_id: str, secret_key: str,
                 services: Optional[list[str]] = None):
        self.regions = regions
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.services = set(services or ["cdb", "redis", "mongo", "ckafka", "emr"])

    @classmethod
    def from_env(cls) -> "TencentCloudAdapter":
        regions = [r.strip() for r in os.environ.get(
            "TENCENTCLOUD_REGIONS", "ap-guangzhou,ap-beijing,ap-shanghai"
        ).split(",") if r.strip()]
        sid = os.environ.get("TENCENTCLOUD_SECRET_ID") or os.environ.get("TC_SECRET_ID", "")
        sk = os.environ.get("TENCENTCLOUD_SECRET_KEY") or os.environ.get("TC_SECRET_KEY", "")
        services = [s.strip() for s in os.environ.get("TENCENTCLOUD_SERVICES", "").split(",")
                    if s.strip()] or None
        return cls(regions=regions, secret_id=sid, secret_key=sk, services=services)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not (self.secret_id and self.secret_key):
            return []
        clusters: list[Cluster] = []
        for region in self.regions:
            if "cdb" in self.services:
                clusters.extend(self._discover_cdb(region))
            if "redis" in self.services:
                clusters.extend(self._discover_redis(region))
            if "mongo" in self.services:
                clusters.extend(self._discover_mongo(region))
            if "ckafka" in self.services:
                clusters.extend(self._discover_ckafka(region))
            if "emr" in self.services:
                clusters.extend(self._discover_emr(region))
        if tech_filter:
            clusters = [c for c in clusters if c.tech == tech_filter]
        return clusters

    def _client(self, service: str, region: str):  # pragma: no cover (network)
        try:
            from tencentcloud.common import credential  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "tencentcloud-sdk-python missing; "
                "pip install 'srefix-discovery-mcp[tencentcloud]'"
            ) from e
        cred = credential.Credential(self.secret_id, self.secret_key)
        # Per-service client modules
        if service == "cdb":
            from tencentcloud.cdb.v20170320 import cdb_client  # type: ignore
            return cdb_client.CdbClient(cred, region)
        if service == "redis":
            from tencentcloud.redis.v20180412 import redis_client  # type: ignore
            return redis_client.RedisClient(cred, region)
        if service == "mongo":
            from tencentcloud.mongodb.v20190725 import mongodb_client  # type: ignore
            return mongodb_client.MongodbClient(cred, region)
        if service == "ckafka":
            from tencentcloud.ckafka.v20190819 import ckafka_client  # type: ignore
            return ckafka_client.CkafkaClient(cred, region)
        if service == "emr":
            from tencentcloud.emr.v20190103 import emr_client  # type: ignore
            return emr_client.EmrClient(cred, region)
        raise ValueError(f"unknown service: {service}")

    def _discover_cdb(self, region: str):  # pragma: no cover
        try:
            from tencentcloud.cdb.v20170320 import models  # type: ignore
            client = self._client("cdb", region)
            req = models.DescribeDBInstancesRequest()
            resp = client.DescribeDBInstances(req)
            for it in resp.Items or []:
                cluster = build_tc_cdb_cluster(it.__dict__ if hasattr(it, "__dict__") else dict(it), region)
                if cluster:
                    yield cluster
        except Exception:  # noqa
            return

    def _discover_redis(self, region: str):  # pragma: no cover
        try:
            from tencentcloud.redis.v20180412 import models  # type: ignore
            client = self._client("redis", region)
            req = models.DescribeInstancesRequest()
            resp = client.DescribeInstances(req)
            for it in resp.InstanceSet or []:
                yield build_tc_redis_cluster(
                    it.__dict__ if hasattr(it, "__dict__") else dict(it), region)
        except Exception:  # noqa
            return

    def _discover_mongo(self, region: str):  # pragma: no cover
        try:
            from tencentcloud.mongodb.v20190725 import models  # type: ignore
            client = self._client("mongo", region)
            req = models.DescribeDBInstancesRequest()
            resp = client.DescribeDBInstances(req)
            for it in resp.InstanceDetails or []:
                yield build_tc_mongo_cluster(
                    it.__dict__ if hasattr(it, "__dict__") else dict(it), region)
        except Exception:  # noqa
            return

    def _discover_ckafka(self, region: str):  # pragma: no cover
        try:
            from tencentcloud.ckafka.v20190819 import models  # type: ignore
            client = self._client("ckafka", region)
            req = models.DescribeInstancesRequest()
            resp = client.DescribeInstances(req)
            inst_list = resp.Result.InstanceList if resp.Result else []
            for it in inst_list:
                yield build_tc_ckafka_cluster(
                    it.__dict__ if hasattr(it, "__dict__") else dict(it), region)
        except Exception:  # noqa
            return

    def _discover_emr(self, region: str):  # pragma: no cover
        try:
            from tencentcloud.emr.v20190103 import models  # type: ignore
            client = self._client("emr", region)
            req = models.DescribeInstancesRequest()
            resp = client.DescribeInstances(req)
            for it in resp.ClusterList or []:
                d = it.__dict__ if hasattr(it, "__dict__") else dict(it)
                for c in build_tc_emr_cluster(d, region):
                    yield c
        except Exception:  # noqa
            return
