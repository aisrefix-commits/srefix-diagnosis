"""AWS managed-services adapter.

Discovers clusters across major AWS data + messaging services without
relying on CMDB or K8s labels. Each service is independently optional —
if boto3 isn't installed or AWS creds aren't present, the adapter returns
an empty list (logs the error in the registry).

Services covered:
  RDS              → tech: postgres / mysql / mariadb / sqlserver / oracle / aurora-*
  ElastiCache      → tech: redis / memcached
  MSK              → tech: kafka
  OpenSearch       → tech: opensearch / elasticsearch
  EMR              → tech: emr / spark / hbase / hive / presto (per Application set)
  DynamoDB         → tech: dynamodb (one logical "table" per cluster)
  DocumentDB       → tech: mongo
  Neptune          → tech: neptune
  Redshift         → tech: redshift
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

from ..core.models import Cluster, Host

# RDS engine → canonical tech name
_RDS_ENGINE_MAP = {
    "postgres": "postgres",
    "aurora-postgresql": "aurora",
    "mysql": "mysql",
    "aurora-mysql": "aurora",
    "mariadb": "mariadb",
    "sqlserver-ee": "sqlserver", "sqlserver-se": "sqlserver",
    "sqlserver-ex": "sqlserver", "sqlserver-web": "sqlserver",
    "oracle-ee": "oracle", "oracle-se2": "oracle",
    "oracle-se1": "oracle", "oracle-se": "oracle",
}


def _flatten_tags(tag_list: list[dict]) -> dict[str, str]:
    return {t.get("Key", ""): t.get("Value", "") for t in (tag_list or [])}


# ─── per-service discoverers (pure transforms, accept already-fetched data) ──

def build_rds_cluster(instance: dict, region: str) -> Optional[Cluster]:
    """One RDS instance → one Cluster (members already have role from rds metadata)."""
    engine = instance.get("Engine", "")
    tech = _RDS_ENGINE_MAP.get(engine)
    if not tech:
        return None
    cid = instance.get("DBInstanceIdentifier", "rds-unknown")
    role = "primary"
    if instance.get("ReadReplicaSourceDBInstanceIdentifier"):
        role = "replica"
    endpoint = instance.get("Endpoint") or {}
    fqdn = endpoint.get("Address", "")
    port = endpoint.get("Port")
    return Cluster(
        id=f"aws/{region}/rds/{cid}",
        tech=tech,
        version=instance.get("EngineVersion"),
        hosts=[Host(fqdn=fqdn, address=fqdn, port=port, role=role,
                    tags={"region": region, "engine": engine,
                          **_flatten_tags(instance.get("TagList", []))},
                    cluster_id=f"aws/{region}/rds/{cid}",
                    health=instance.get("DBInstanceStatus", "unknown"))],
        discovery_source="aws-rds",
        metadata={"region": region, "instance_class": instance.get("DBInstanceClass"),
                  "multi_az": instance.get("MultiAZ"),
                  "tech_confidence": "high", "tech_signal": f"rds_engine:{engine}"},
    )


def build_aurora_cluster(cluster_meta: dict, members: list[dict], region: str) -> Optional[Cluster]:
    """Aurora cluster (DescribeDBClusters) — multi-member with writer + reader roles."""
    engine = cluster_meta.get("Engine", "")
    tech = _RDS_ENGINE_MAP.get(engine, "aurora")
    cid = cluster_meta.get("DBClusterIdentifier", "aurora-unknown")
    hosts: list[Host] = []
    by_id = {m.get("DBInstanceIdentifier"): m for m in members}
    for cm in cluster_meta.get("DBClusterMembers", []):
        member = by_id.get(cm.get("DBInstanceIdentifier")) or {}
        endpoint = member.get("Endpoint") or {}
        hosts.append(Host(
            fqdn=endpoint.get("Address", cm.get("DBInstanceIdentifier", "")),
            address=endpoint.get("Address"),
            port=endpoint.get("Port"),
            role="writer" if cm.get("IsClusterWriter") else "reader",
            tags={"region": region, "engine": engine, "az": member.get("AvailabilityZone", "")},
            cluster_id=f"aws/{region}/aurora/{cid}",
            health=member.get("DBInstanceStatus", "unknown"),
        ))
    return Cluster(
        id=f"aws/{region}/aurora/{cid}",
        tech=tech,
        version=cluster_meta.get("EngineVersion"),
        hosts=hosts,
        discovery_source="aws-rds",
        metadata={"region": region,
                  "endpoint": cluster_meta.get("Endpoint"),
                  "reader_endpoint": cluster_meta.get("ReaderEndpoint"),
                  "tech_confidence": "high", "tech_signal": f"aurora:{engine}"},
    )


def build_elasticache_cluster(cluster_meta: dict, region: str) -> Optional[Cluster]:
    engine = cluster_meta.get("Engine", "redis")
    cid = cluster_meta.get("CacheClusterId") or cluster_meta.get("ReplicationGroupId", "")
    tech = "redis" if engine.startswith("redis") else "memcached"
    hosts: list[Host] = []
    for node in cluster_meta.get("CacheNodes", []) or cluster_meta.get("NodeGroupMembers", []):
        endpoint = node.get("Endpoint") or {}
        hosts.append(Host(
            fqdn=endpoint.get("Address", ""),
            address=endpoint.get("Address"),
            port=endpoint.get("Port"),
            role=node.get("CurrentRole", "node"),
            tags={"region": region, "engine": engine, "az": node.get("CustomerAvailabilityZone", "")},
            cluster_id=f"aws/{region}/elasticache/{cid}",
            health=node.get("CacheNodeStatus") or "unknown",
        ))
    return Cluster(
        id=f"aws/{region}/elasticache/{cid}",
        tech=tech,
        version=cluster_meta.get("EngineVersion"),
        hosts=hosts,
        discovery_source="aws-elasticache",
        metadata={"region": region, "tech_confidence": "high",
                  "tech_signal": f"elasticache:{engine}"},
    )


def build_msk_cluster(cluster_arn: str, name: str, brokers: list[str], region: str,
                      version: str = "") -> Cluster:
    hosts = []
    for ep in brokers:  # broker endpoints are "host:port" strings
        h, _, p = ep.partition(":")
        hosts.append(Host(
            fqdn=h, address=h, port=int(p) if p.isdigit() else None,
            role="broker", tags={"region": region},
            cluster_id=f"aws/{region}/msk/{name}",
        ))
    return Cluster(
        id=f"aws/{region}/msk/{name}",
        tech="kafka",
        version=version,
        hosts=hosts,
        discovery_source="aws-msk",
        metadata={"arn": cluster_arn, "region": region,
                  "tech_confidence": "high", "tech_signal": "msk"},
    )


def build_opensearch_cluster(domain: dict, region: str) -> Cluster:
    name = domain.get("DomainName", "")
    eng = domain.get("EngineVersion", "")
    # ES 7.x or below → "elasticsearch"; OpenSearch_x.y → "opensearch"
    tech = "opensearch" if "OpenSearch" in eng else "elasticsearch"
    endpoint = domain.get("Endpoint") or domain.get("Endpoints", {}).get("vpc", "")
    return Cluster(
        id=f"aws/{region}/opensearch/{name}",
        tech=tech,
        version=eng,
        hosts=[Host(fqdn=endpoint, address=endpoint, port=443, role="cluster",
                    tags={"region": region}, cluster_id=f"aws/{region}/opensearch/{name}",
                    health=domain.get("Processing") and "processing" or "active")],
        discovery_source="aws-opensearch",
        metadata={"region": region, "instance_count": domain.get("ClusterConfig", {}).get("InstanceCount"),
                  "tech_confidence": "high", "tech_signal": "opensearch-domain"},
    )


def build_emr_cluster(cluster_meta: dict, instances: list[dict], region: str) -> list[Cluster]:
    """EMR is special — one EMR cluster runs N applications (Spark, HBase, Hive, ...).
    We emit one Cluster per application so each maps to its own diag-{tech}.
    """
    apps = [a.get("Name", "").lower() for a in cluster_meta.get("Applications", [])]
    cid = cluster_meta.get("Id", "")
    name = cluster_meta.get("Name", "")
    out: list[Cluster] = []
    # Map EMR Application names to our canonical tech names
    app_to_tech = {"spark": "spark", "hbase": "hbase", "hive": "hive",
                   "presto": "trino", "trino": "trino", "hadoop": "hadoop",
                   "flink": "flink"}
    for app_name in apps:
        tech = app_to_tech.get(app_name)
        if not tech:
            continue
        hosts = [Host(
            fqdn=i.get("PrivateDnsName") or i.get("Ec2InstanceId", ""),
            address=i.get("PrivateIpAddress"),
            role={"MASTER": "master", "CORE": "core", "TASK": "task"}.get(
                i.get("InstanceGroupType", ""), "node"),
            tags={"region": region, "instance_id": i.get("Ec2InstanceId", "")},
            cluster_id=f"aws/{region}/emr/{cid}/{tech}",
            health=i.get("Status", {}).get("State", "unknown"),
        ) for i in instances]
        out.append(Cluster(
            id=f"aws/{region}/emr/{cid}/{tech}",
            tech=tech,
            hosts=hosts,
            discovery_source="aws-emr",
            metadata={"region": region, "emr_cluster_id": cid, "emr_name": name,
                      "tech_confidence": "high", "tech_signal": f"emr-app:{app_name}"},
        ))
    return out


# ─── adapter wrapper that handles boto3 lazy-load + iteration ────────────────

class AWSAdapter:
    def __init__(self, regions: list[str], profile: Optional[str] = None,
                 services: Optional[list[str]] = None):
        self.regions = regions
        self.profile = profile
        # which AWS services to query (default = all)
        self.services = set(services or [
            # core data services
            "rds", "elasticache", "msk", "opensearch", "emr",
            "documentdb", "dynamodb", "redshift", "neptune",
            # extended (compute / storage / network / messaging / config)
            "s3", "lambda", "ec2", "ecs", "ecr", "eks", "efs",
            "sqs", "sns", "kinesis", "eventbridge",
            "route53", "cloudfront", "elb",
            "iam", "secretsmanager", "acm", "ssm",
            "cloudtrail", "cloudwatch", "stepfunctions", "apigateway",
        ])

    @classmethod
    def from_env(cls) -> "AWSAdapter":
        regions = [r.strip() for r in os.environ.get("AWS_DISCOVERY_REGIONS",
                                                      "us-east-1").split(",") if r.strip()]
        profile = os.environ.get("AWS_PROFILE")
        services = os.environ.get("AWS_DISCOVERY_SERVICES", "")
        services_list = [s.strip() for s in services.split(",") if s.strip()] or None
        return cls(regions=regions, profile=profile, services=services_list)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise RuntimeError("boto3 not installed; pip install 'srefix-discovery-mcp[aws]'") from e

        clusters: list[Cluster] = []
        session_kwargs = {"profile_name": self.profile} if self.profile else {}
        for region in self.regions:
            session = boto3.Session(region_name=region, **session_kwargs)
            if "rds" in self.services:
                clusters.extend(self._discover_rds(session, region))
            if "elasticache" in self.services:
                clusters.extend(self._discover_elasticache(session, region))
            if "msk" in self.services:
                clusters.extend(self._discover_msk(session, region))
            if "opensearch" in self.services:
                clusters.extend(self._discover_opensearch(session, region))
            if "emr" in self.services:
                clusters.extend(self._discover_emr(session, region))
            if "documentdb" in self.services:
                clusters.extend(self._discover_documentdb(session, region))
            if "redshift" in self.services:
                clusters.extend(self._discover_redshift(session, region))
            if "neptune" in self.services:
                clusters.extend(self._discover_neptune(session, region))
            # ─── extended services ───
            if "s3" in self.services:
                clusters.extend(self._discover_s3(session, region))
            if "lambda" in self.services:
                clusters.extend(self._discover_lambda(session, region))
            if "ec2" in self.services:
                clusters.extend(self._discover_ec2(session, region))
            if "ecs" in self.services:
                clusters.extend(self._discover_ecs(session, region))
            if "ecr" in self.services:
                clusters.extend(self._discover_ecr(session, region))
            if "eks" in self.services:
                clusters.extend(self._discover_eks(session, region))
            if "efs" in self.services:
                clusters.extend(self._discover_efs(session, region))
            if "sqs" in self.services:
                clusters.extend(self._discover_sqs(session, region))
            if "sns" in self.services:
                clusters.extend(self._discover_sns(session, region))
            if "kinesis" in self.services:
                clusters.extend(self._discover_kinesis(session, region))
            if "eventbridge" in self.services:
                clusters.extend(self._discover_eventbridge(session, region))
            if "route53" in self.services:
                clusters.extend(self._discover_route53(session, region))
            if "cloudfront" in self.services:
                clusters.extend(self._discover_cloudfront(session, region))
            if "elb" in self.services:
                clusters.extend(self._discover_elb(session, region))
            if "iam" in self.services:
                clusters.extend(self._discover_iam(session, region))
            if "secretsmanager" in self.services:
                clusters.extend(self._discover_secretsmanager(session, region))
            if "acm" in self.services:
                clusters.extend(self._discover_acm(session, region))
            if "ssm" in self.services:
                clusters.extend(self._discover_ssm(session, region))
            if "cloudtrail" in self.services:
                clusters.extend(self._discover_cloudtrail(session, region))
            if "cloudwatch" in self.services:
                clusters.extend(self._discover_cloudwatch(session, region))
            if "stepfunctions" in self.services:
                clusters.extend(self._discover_stepfunctions(session, region))
            if "apigateway" in self.services:
                clusters.extend(self._discover_apigateway(session, region))
        if tech_filter:
            clusters = [c for c in clusters if c.tech == tech_filter]
        return clusters

    def _discover_rds(self, session, region):  # pragma: no cover (network)
        client = session.client("rds")
        # Aurora clusters first
        for c in client.get_paginator("describe_db_clusters").paginate():
            for cl in c.get("DBClusters", []):
                # Get member instance details
                members = []
                for cm in cl.get("DBClusterMembers", []):
                    inst_id = cm.get("DBInstanceIdentifier")
                    try:
                        inst = client.describe_db_instances(
                            DBInstanceIdentifier=inst_id
                        )["DBInstances"][0]
                        members.append(inst)
                    except Exception:  # noqa
                        continue
                cluster = build_aurora_cluster(cl, members, region)
                if cluster:
                    yield cluster
        # Standalone RDS instances (non-Aurora)
        seen_aurora_members = set()
        for c in client.get_paginator("describe_db_clusters").paginate():
            for cl in c.get("DBClusters", []):
                for cm in cl.get("DBClusterMembers", []):
                    seen_aurora_members.add(cm.get("DBInstanceIdentifier"))
        for page in client.get_paginator("describe_db_instances").paginate():
            for inst in page.get("DBInstances", []):
                if inst.get("DBInstanceIdentifier") in seen_aurora_members:
                    continue
                cluster = build_rds_cluster(inst, region)
                if cluster:
                    yield cluster

    def _discover_elasticache(self, session, region):  # pragma: no cover
        client = session.client("elasticache")
        for page in client.get_paginator("describe_replication_groups").paginate():
            for rg in page.get("ReplicationGroups", []):
                cluster = build_elasticache_cluster(rg, region)
                if cluster:
                    yield cluster
        # Standalone (non-replication) clusters
        for page in client.get_paginator("describe_cache_clusters").paginate(ShowCacheNodeInfo=True):
            for cl in page.get("CacheClusters", []):
                if cl.get("ReplicationGroupId"):
                    continue
                cluster = build_elasticache_cluster(cl, region)
                if cluster:
                    yield cluster

    def _discover_msk(self, session, region):  # pragma: no cover
        client = session.client("kafka")
        paginator = client.get_paginator("list_clusters_v2")
        for page in paginator.paginate():
            for cl in page.get("ClusterInfoList", []):
                arn = cl.get("ClusterArn", "")
                name = cl.get("ClusterName", "")
                version = cl.get("Provisioned", {}).get("CurrentBrokerSoftwareInfo", {}) \
                    .get("KafkaVersion", "") or cl.get("Serverless", {}).get("ClusterType", "")
                try:
                    bs = client.get_bootstrap_brokers(ClusterArn=arn)
                    brokers_str = bs.get("BootstrapBrokerString") or \
                                  bs.get("BootstrapBrokerStringTls", "")
                    brokers = [b.strip() for b in brokers_str.split(",") if b.strip()]
                except Exception:  # noqa
                    brokers = []
                yield build_msk_cluster(arn, name, brokers, region, version)

    def _discover_opensearch(self, session, region):  # pragma: no cover
        client = session.client("opensearch")
        domain_names = [d["DomainName"] for d in client.list_domain_names().get("DomainNames", [])]
        for batch_start in range(0, len(domain_names), 5):
            batch = domain_names[batch_start: batch_start + 5]
            try:
                resp = client.describe_domains(DomainNames=batch)
                for d in resp.get("DomainStatusList", []):
                    yield build_opensearch_cluster(d, region)
            except Exception:  # noqa
                continue

    def _discover_emr(self, session, region):  # pragma: no cover
        client = session.client("emr")
        for page in client.get_paginator("list_clusters").paginate(
            ClusterStates=["RUNNING", "WAITING"]
        ):
            for cl_summary in page.get("Clusters", []):
                cid = cl_summary.get("Id")
                try:
                    full = client.describe_cluster(ClusterId=cid)["Cluster"]
                except Exception:  # noqa
                    continue
                instances = []
                for ip in client.get_paginator("list_instances").paginate(ClusterId=cid):
                    instances.extend(ip.get("Instances", []))
                for c in build_emr_cluster(full, instances, region):
                    yield c

    def _discover_documentdb(self, session, region):  # pragma: no cover
        client = session.client("docdb")
        # DocumentDB shares the rds boto3 client API surface but uses docdb client
        try:
            for page in client.get_paginator("describe_db_clusters").paginate():
                for cl in page.get("DBClusters", []):
                    cid = cl.get("DBClusterIdentifier", "")
                    yield Cluster(
                        id=f"aws/{region}/docdb/{cid}", tech="mongo",
                        version=cl.get("EngineVersion"),
                        hosts=[Host(
                            fqdn=cl.get("Endpoint", ""),
                            address=cl.get("Endpoint"),
                            port=cl.get("Port", 27017),
                            role="primary",
                            cluster_id=f"aws/{region}/docdb/{cid}",
                            tags={"region": region},
                            health=cl.get("Status", "unknown"),
                        )],
                        discovery_source="aws-documentdb",
                        metadata={"region": region, "tech_confidence": "high",
                                  "tech_signal": "docdb"},
                    )
        except Exception:  # noqa
            return

    def _discover_redshift(self, session, region):  # pragma: no cover
        client = session.client("redshift")
        try:
            for page in client.get_paginator("describe_clusters").paginate():
                for cl in page.get("Clusters", []):
                    cid = cl.get("ClusterIdentifier", "")
                    yield Cluster(
                        id=f"aws/{region}/redshift/{cid}", tech="redshift",
                        version=cl.get("ClusterVersion"),
                        hosts=[Host(
                            fqdn=cl.get("Endpoint", {}).get("Address", ""),
                            address=cl.get("Endpoint", {}).get("Address"),
                            port=cl.get("Endpoint", {}).get("Port"),
                            role="leader",
                            cluster_id=f"aws/{region}/redshift/{cid}",
                            tags={"region": region, "node_type": cl.get("NodeType", "")},
                            health=cl.get("ClusterStatus", "unknown"),
                        )],
                        discovery_source="aws-redshift",
                        metadata={"region": region, "tech_confidence": "high",
                                  "tech_signal": "redshift"},
                    )
        except Exception:  # noqa
            return

    # ──────────────── extended-services discoverers ────────────────

    def _discover_neptune(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_neptune
        try:
            client = session.client("neptune")
            for p in client.get_paginator("describe_db_clusters").paginate():
                for cl in p.get("DBClusters", []):
                    yield build_aws_neptune(cl, region)
        except Exception:  # noqa
            return

    def _discover_s3(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_s3_bucket
        if region != self.regions[0]:  # S3 listing is global; only do once
            return
        try:
            client = session.client("s3")
            for b in client.list_buckets().get("Buckets", []):
                try:
                    loc = client.get_bucket_location(Bucket=b["Name"]) \
                        .get("LocationConstraint") or "us-east-1"
                except Exception:  # noqa
                    loc = "unknown"
                yield build_aws_s3_bucket(b, loc)
        except Exception:  # noqa
            return

    def _discover_lambda(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_lambda
        try:
            client = session.client("lambda")
            for p in client.get_paginator("list_functions").paginate():
                for fn in p.get("Functions", []):
                    yield build_aws_lambda(fn, region)
        except Exception:  # noqa
            return

    def _discover_ec2(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_ec2, build_aws_vpc
        try:
            ec2 = session.client("ec2")
            # VPCs
            if "ec2" in self.services or "vpc" in self.services:
                for v in ec2.describe_vpcs().get("Vpcs", []):
                    yield build_aws_vpc(v, region)
            # Instances
            for p in ec2.get_paginator("describe_instances").paginate():
                for r in p.get("Reservations", []):
                    for inst in r.get("Instances", []):
                        if inst.get("State", {}).get("Name") in ("running", "stopped"):
                            yield build_aws_ec2(inst, region)
        except Exception:  # noqa
            return

    def _discover_ecs(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_ecs_cluster
        try:
            ecs = session.client("ecs")
            for p in ecs.get_paginator("list_clusters").paginate():
                arns = p.get("clusterArns", [])
                if not arns:
                    continue
                desc = ecs.describe_clusters(clusters=arns).get("clusters", [])
                for c in desc:
                    yield build_aws_ecs_cluster(c, region)
        except Exception:  # noqa
            return

    def _discover_ecr(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_ecr_repo
        try:
            ecr = session.client("ecr")
            for p in ecr.get_paginator("describe_repositories").paginate():
                for r in p.get("repositories", []):
                    yield build_aws_ecr_repo(r, region)
        except Exception:  # noqa
            return

    def _discover_eks(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_eks_cluster
        try:
            eks = session.client("eks")
            for p in eks.get_paginator("list_clusters").paginate():
                for name in p.get("clusters", []):
                    cl = eks.describe_cluster(name=name)["cluster"]
                    yield build_aws_eks_cluster(cl, region)
        except Exception:  # noqa
            return

    def _discover_efs(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_efs
        try:
            efs = session.client("efs")
            for p in efs.get_paginator("describe_file_systems").paginate():
                for fs in p.get("FileSystems", []):
                    yield build_aws_efs(fs, region)
        except Exception:  # noqa
            return

    def _discover_sqs(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_sqs_queue
        try:
            sqs = session.client("sqs")
            for url in sqs.list_queues().get("QueueUrls", []) or []:
                yield build_aws_sqs_queue(url, region)
        except Exception:  # noqa
            return

    def _discover_sns(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_sns_topic
        try:
            sns = session.client("sns")
            for p in sns.get_paginator("list_topics").paginate():
                for t in p.get("Topics", []):
                    yield build_aws_sns_topic(t, region)
        except Exception:  # noqa
            return

    def _discover_kinesis(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_kinesis_stream
        try:
            kn = session.client("kinesis")
            paginator = kn.get_paginator("list_streams")
            for p in paginator.paginate():
                for s in p.get("StreamNames", []):
                    yield build_aws_kinesis_stream(s, region)
        except Exception:  # noqa
            return

    def _discover_eventbridge(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_eventbridge_rule
        try:
            eb = session.client("events")
            for p in eb.get_paginator("list_rules").paginate():
                for r in p.get("Rules", []):
                    yield build_aws_eventbridge_rule(r, region)
        except Exception:  # noqa
            return

    def _discover_route53(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_route53_zone
        if region != self.regions[0]:
            return  # Route 53 is global
        try:
            r53 = session.client("route53")
            for p in r53.get_paginator("list_hosted_zones").paginate():
                for z in p.get("HostedZones", []):
                    yield build_aws_route53_zone(z)
        except Exception:  # noqa
            return

    def _discover_cloudfront(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_cloudfront
        if region != self.regions[0]:
            return
        try:
            cf = session.client("cloudfront")
            for p in cf.get_paginator("list_distributions").paginate():
                for d in p.get("DistributionList", {}).get("Items", []):
                    yield build_aws_cloudfront(d)
        except Exception:  # noqa
            return

    def _discover_elb(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_alb
        try:
            elb = session.client("elbv2")
            for p in elb.get_paginator("describe_load_balancers").paginate():
                for lb in p.get("LoadBalancers", []):
                    yield build_aws_alb(lb, region)
        except Exception:  # noqa
            return

    def _discover_iam(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_iam_user
        if region != self.regions[0]:
            return  # IAM is global
        try:
            iam = session.client("iam")
            for p in iam.get_paginator("list_users").paginate():
                for u in p.get("Users", []):
                    yield build_aws_iam_user(u)
        except Exception:  # noqa
            return

    def _discover_secretsmanager(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_secret
        try:
            sm = session.client("secretsmanager")
            for p in sm.get_paginator("list_secrets").paginate():
                for s in p.get("SecretList", []):
                    yield build_aws_secret(s, region)
        except Exception:  # noqa
            return

    def _discover_acm(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_acm_cert
        try:
            acm = session.client("acm")
            for p in acm.get_paginator("list_certificates").paginate():
                for c in p.get("CertificateSummaryList", []):
                    yield build_aws_acm_cert(c, region)
        except Exception:  # noqa
            return

    def _discover_ssm(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_ssm_param
        try:
            ssm = session.client("ssm")
            for p in ssm.get_paginator("describe_parameters").paginate():
                for param in p.get("Parameters", []):
                    yield build_aws_ssm_param(param, region)
        except Exception:  # noqa
            return

    def _discover_cloudtrail(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_cloudtrail
        try:
            ct = session.client("cloudtrail")
            for t in ct.describe_trails().get("trailList", []):
                yield build_aws_cloudtrail(t, region)
        except Exception:  # noqa
            return

    def _discover_cloudwatch(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_cloudwatch_alarm
        try:
            cw = session.client("cloudwatch")
            for p in cw.get_paginator("describe_alarms").paginate():
                for a in p.get("MetricAlarms", []):
                    yield build_aws_cloudwatch_alarm(a, region)
        except Exception:  # noqa
            return

    def _discover_stepfunctions(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_step_functions
        try:
            sfn = session.client("stepfunctions")
            for p in sfn.get_paginator("list_state_machines").paginate():
                for sm in p.get("stateMachines", []):
                    yield build_aws_step_functions(sm, region)
        except Exception:  # noqa
            return

    def _discover_apigateway(self, session, region):  # pragma: no cover
        from .aws_extended import build_aws_apigateway
        try:
            apigw = session.client("apigateway")
            for api in apigw.get_rest_apis().get("items", []) or []:
                yield build_aws_apigateway(api, region)
            apigw_v2 = session.client("apigatewayv2")
            for api in apigw_v2.get_apis().get("Items", []) or []:
                yield build_aws_apigateway(api, region)
        except Exception:  # noqa
            return
