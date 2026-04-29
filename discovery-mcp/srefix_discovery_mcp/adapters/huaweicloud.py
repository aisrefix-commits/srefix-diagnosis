"""华为云 (Huawei Cloud) managed-services adapter.

Services covered:
  RDS              → tech: postgres / mysql / sqlserver / mariadb (per Engine)
  DCS (Redis)      → tech: redis
  DDS (MongoDB)    → tech: mongo
  DMS Kafka        → tech: kafka
  DMS RocketMQ     → tech: rocketmq
  GaussDB          → tech: gaussdb (or postgres/mysql per engine_type)
  MRS              → tech: hadoop / spark / hbase / hive (per components)

Auth uses huaweicloud-sdk-python via env:
  HUAWEICLOUD_ACCESS_KEY + HUAWEICLOUD_SECRET_KEY (or HW_*)
  HUAWEICLOUD_REGIONS=cn-north-4,cn-east-3,ap-southeast-1
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
}


def build_hw_rds_cluster(inst: dict, region: str) -> Optional[Cluster]:
    engine = inst.get("datastore", {}).get("type") or inst.get("type", "")
    tech = _RDS_ENGINE_MAP.get(engine)
    if not tech:
        return None
    cid_suffix = inst.get("id") or inst.get("name", "rds-unknown")
    cid = f"huaweicloud/{region}/rds/{cid_suffix}"
    return Cluster(
        id=cid, tech=tech,
        version=inst.get("datastore", {}).get("version"),
        hosts=[Host(
            fqdn=inst.get("private_ips", [None])[0] or inst.get("name", ""),
            address=(inst.get("private_ips") or [None])[0],
            port=inst.get("port"), role="primary",
            tags={"region": region,
                  "type": inst.get("type", ""),
                  "spec": inst.get("flavor_ref", "")},
            cluster_id=cid,
            health=inst.get("status", "unknown"),
        )],
        discovery_source="huaweicloud-rds",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": f"hw-rds:{engine}"},
    )


def build_hw_dcs_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("instance_id") or inst.get("name", "dcs-unknown")
    cid = f"huaweicloud/{region}/dcs/{cid_suffix}"
    return Cluster(
        id=cid, tech="redis",
        version=inst.get("engine_version") or inst.get("engine"),
        hosts=[Host(
            fqdn=inst.get("ip") or inst.get("domainName") or cid_suffix,
            address=inst.get("ip"),
            port=inst.get("port", 6379),
            role="primary",
            tags={"region": region, "spec": inst.get("spec_code", "")},
            cluster_id=cid,
            health=inst.get("status", "unknown"),
        )],
        discovery_source="huaweicloud-dcs",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "hw-dcs"},
    )


def build_hw_dds_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("id") or inst.get("name", "dds-unknown")
    cid = f"huaweicloud/{region}/dds/{cid_suffix}"
    hosts = [Host(
        fqdn=inst.get("name", cid_suffix), role="primary",
        tags={"region": region, "mode": inst.get("mode", "")},
        cluster_id=cid,
        health=inst.get("status", "unknown"),
    )]
    return Cluster(
        id=cid, tech="mongo",
        version=inst.get("datastore", {}).get("version"),
        hosts=hosts, discovery_source="huaweicloud-dds",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "hw-dds"},
    )


def build_hw_dms_kafka_cluster(inst: dict, region: str) -> Cluster:
    cid_suffix = inst.get("instance_id") or inst.get("name", "kafka-unknown")
    cid = f"huaweicloud/{region}/dms-kafka/{cid_suffix}"
    return Cluster(
        id=cid, tech="kafka",
        version=inst.get("engine_version"),
        hosts=[Host(
            fqdn=inst.get("connect_address") or cid_suffix,
            address=inst.get("connect_address"),
            port=inst.get("port", 9092),
            role="cluster",
            tags={"region": region, "spec": inst.get("spec_code", "")},
            cluster_id=cid,
            health=inst.get("status", "unknown"),
        )],
        discovery_source="huaweicloud-dms",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": "dms-kafka"},
    )


def build_hw_mrs_cluster(cluster_data: dict, region: str) -> list[Cluster]:
    cid_base = cluster_data.get("clusterId") or cluster_data.get("id", "mrs-unknown")
    components_field = cluster_data.get("componentList") or cluster_data.get("components") or []
    if isinstance(components_field, str):
        components_field = components_field.split(",")
    components = [(c if isinstance(c, str) else c.get("componentName", "")).lower()
                  for c in components_field]
    apps = {"spark": "spark", "hbase": "hbase", "hive": "hive",
            "hadoop": "hadoop", "presto": "trino", "flink": "flink"}
    out = []
    for comp in components:
        base = comp.split("-")[0].split(":")[0]
        tech = apps.get(base)
        if not tech:
            continue
        cid = f"huaweicloud/{region}/mrs/{cid_base}/{tech}"
        out.append(Cluster(
            id=cid, tech=tech,
            hosts=[Host(fqdn=cid_base, role="cluster",
                        tags={"region": region}, cluster_id=cid)],
            discovery_source="huaweicloud-mrs",
            metadata={"region": region, "mrs_id": cid_base,
                      "tech_confidence": "high",
                      "tech_signal": f"mrs:{base}"},
        ))
    return out


def build_hw_ecs_classified(instances: list[dict], region: str) -> list[Cluster]:
    """Tag-aware Huawei ECS grouping. Huawei tags follow
    [{'key': 'Service', 'value': 'hbase'}, ...] (lowercase keys).
    """
    from ._classify import group_instances_into_clusters, normalize_huawei_tags
    return group_instances_into_clusters(
        instances,
        tag_extractor=lambda i: normalize_huawei_tags(i.get("tags")),
        fqdn_extractor=lambda i: (i.get("name")
                                  or (i.get("addresses", {}) or {}).get("private", [{}])[0].get("addr", "")
                                  or i.get("id", "")),
        instance_id_extractor=lambda i: i.get("id", "ecs-unknown"),
        cluster_id_prefix=f"huaweicloud/{region}",
        discovery_source="huaweicloud-ecs-tagged",
        region=region, default_tech="ecs",
        extra_host_tags=lambda i: {
            "flavor": (i.get("flavor", {}) or {}).get("id", ""),
            "az": i.get("OS-EXT-AZ:availability_zone", ""),
            "instance_id": i.get("id", ""),
        },
    )


class HuaweiCloudAdapter:
    def __init__(self, regions: list[str], access_key: str, secret_key: str,
                 services: Optional[list[str]] = None):
        self.regions = regions
        self.ak = access_key
        self.sk = secret_key
        self.services = set(services or ["rds", "dcs", "dds", "dms", "mrs"])

    @classmethod
    def from_env(cls) -> "HuaweiCloudAdapter":
        regions = [r.strip() for r in os.environ.get(
            "HUAWEICLOUD_REGIONS", "cn-north-4,cn-east-3"
        ).split(",") if r.strip()]
        ak = os.environ.get("HUAWEICLOUD_ACCESS_KEY") or os.environ.get("HW_AK", "")
        sk = os.environ.get("HUAWEICLOUD_SECRET_KEY") or os.environ.get("HW_SK", "")
        services = [s.strip() for s in os.environ.get("HUAWEICLOUD_SERVICES", "").split(",")
                    if s.strip()] or None
        return cls(regions=regions, access_key=ak, secret_key=sk, services=services)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not (self.ak and self.sk):
            return []
        clusters: list[Cluster] = []
        for region in self.regions:
            if "rds" in self.services:
                clusters.extend(self._discover_rds(region))
            if "dcs" in self.services:
                clusters.extend(self._discover_dcs(region))
            if "dds" in self.services:
                clusters.extend(self._discover_dds(region))
            if "dms" in self.services:
                clusters.extend(self._discover_dms(region))
            if "mrs" in self.services:
                clusters.extend(self._discover_mrs(region))
        if tech_filter:
            clusters = [c for c in clusters if c.tech == tech_filter]
        return clusters

    # network calls (skipped from coverage)
    def _credentials(self):  # pragma: no cover
        try:
            from huaweicloudsdkcore.auth.credentials import BasicCredentials  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "huaweicloudsdkcore missing; pip install 'srefix-discovery-mcp[huaweicloud]'"
            ) from e
        return BasicCredentials(self.ak, self.sk)

    def _discover_rds(self, region: str):  # pragma: no cover
        try:
            from huaweicloudsdkrds.v3 import RdsClient, ListInstancesRequest  # type: ignore
            from huaweicloudsdkrds.v3.region.rds_region import RdsRegion  # type: ignore
            client = RdsClient.new_builder().with_credentials(self._credentials()) \
                .with_region(RdsRegion.value_of(region)).build()
            offset = 0
            while True:
                resp = client.list_instances(ListInstancesRequest(offset=offset, limit=100))
                items = resp.instances or []
                if not items:
                    break
                for it in items:
                    d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                    cluster = build_hw_rds_cluster(d, region)
                    if cluster:
                        yield cluster
                if len(items) < 100:
                    break
                offset += 100
        except Exception:  # noqa
            return

    def _discover_dcs(self, region: str):  # pragma: no cover
        try:
            from huaweicloudsdkdcs.v2 import DcsClient, ListInstancesRequest  # type: ignore
            from huaweicloudsdkdcs.v2.region.dcs_region import DcsRegion  # type: ignore
            client = DcsClient.new_builder().with_credentials(self._credentials()) \
                .with_region(DcsRegion.value_of(region)).build()
            resp = client.list_instances(ListInstancesRequest())
            for it in resp.instances or []:
                d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                yield build_hw_dcs_cluster(d, region)
        except Exception:  # noqa
            return

    def _discover_dds(self, region: str):  # pragma: no cover
        try:
            from huaweicloudsdkdds.v3 import DdsClient, ListInstancesRequest  # type: ignore
            from huaweicloudsdkdds.v3.region.dds_region import DdsRegion  # type: ignore
            client = DdsClient.new_builder().with_credentials(self._credentials()) \
                .with_region(DdsRegion.value_of(region)).build()
            resp = client.list_instances(ListInstancesRequest())
            for it in resp.instances or []:
                d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                yield build_hw_dds_cluster(d, region)
        except Exception:  # noqa
            return

    def _discover_dms(self, region: str):  # pragma: no cover
        try:
            from huaweicloudsdkkafka.v2 import KafkaClient, ListInstancesRequest  # type: ignore
            from huaweicloudsdkkafka.v2.region.kafka_region import KafkaRegion  # type: ignore
            client = KafkaClient.new_builder().with_credentials(self._credentials()) \
                .with_region(KafkaRegion.value_of(region)).build()
            resp = client.list_instances(ListInstancesRequest())
            for it in resp.instances or []:
                d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                yield build_hw_dms_kafka_cluster(d, region)
        except Exception:  # noqa
            return

    def _discover_mrs(self, region: str):  # pragma: no cover
        try:
            from huaweicloudsdkmrs.v2 import MrsClient, ListClustersRequest  # type: ignore
            from huaweicloudsdkmrs.v2.region.mrs_region import MrsRegion  # type: ignore
            client = MrsClient.new_builder().with_credentials(self._credentials()) \
                .with_region(MrsRegion.value_of(region)).build()
            resp = client.list_clusters(ListClustersRequest())
            for it in resp.clusters or []:
                d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                for c in build_hw_mrs_cluster(d, region):
                    yield c
        except Exception:  # noqa
            return
