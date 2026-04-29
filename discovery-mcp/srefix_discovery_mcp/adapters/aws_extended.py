"""Extension to AWSAdapter — covers 18 additional AWS services.

Each builder is a pure transform; the AWSAdapter itself wires them
in via additional service flags (s3 / lambda / sqs / sns / iam / ec2 /
route53 / dynamodb / cloudfront / cloudwatch / cloudtrail / eventbridge /
kinesis / stepfunctions / ssm / apigateway / neptune / eks / ecs /
ecr / efs / vpc / alb / nlb / kms / secretsmanager / acm).

The output `tech` field uses the diag-{tech}.md filename so
diag-aws-s3 / diag-aws-lambda etc. line up.
"""
from __future__ import annotations

from typing import Optional

from ..core.models import Cluster, Host


def _logical_cluster(account: str, region: str, tech: str, name: str,
                     extra_tags: Optional[dict] = None,
                     metadata_extra: Optional[dict] = None,
                     hosts: Optional[list[Host]] = None) -> Cluster:
    cid = f"aws/{account}/{region}/{tech}/{name}"
    if hosts is None:
        hosts = [Host(fqdn=name, role="resource",
                      tags={"region": region, **(extra_tags or {})},
                      cluster_id=cid)]
    return Cluster(
        id=cid, tech=tech, hosts=hosts,
        discovery_source=f"aws-{tech.removeprefix('aws-')}",
        metadata={"region": region, "account": account,
                  "tech_confidence": "high",
                  "tech_signal": tech,
                  **(metadata_extra or {})},
    )


# ─── object storage ───
def build_aws_s3_bucket(b: dict, region: str, account: str = "default") -> Cluster:
    name = b.get("Name", "s3-unknown")
    return _logical_cluster(account, region, "aws-s3", name,
        metadata_extra={"creation_date": str(b.get("CreationDate", ""))})


def build_aws_efs(fs: dict, region: str, account: str = "default") -> Cluster:
    fid = fs.get("FileSystemId", "efs-unknown")
    return _logical_cluster(account, region, "efs", fid,
        extra_tags={"performance_mode": fs.get("PerformanceMode", "")},
        metadata_extra={"size_bytes": fs.get("SizeInBytes", {}).get("Value")})


# ─── compute ───
def build_aws_lambda(fn: dict, region: str, account: str = "default") -> Cluster:
    name = fn.get("FunctionName", "lambda-unknown")
    return _logical_cluster(account, region, "aws-lambda", name,
        extra_tags={"runtime": fn.get("Runtime", ""),
                    "memory": str(fn.get("MemorySize", ""))},
        metadata_extra={"arn": fn.get("FunctionArn", ""),
                        "version": fn.get("Version", "")})


def build_aws_ec2(inst: dict, region: str, account: str = "default") -> Cluster:
    iid = inst.get("InstanceId", "ec2-unknown")
    return _logical_cluster(account, region, "ec2", iid,
        extra_tags={"type": inst.get("InstanceType", ""),
                    "az": inst.get("Placement", {}).get("AvailabilityZone", "")},
        metadata_extra={"state": inst.get("State", {}).get("Name"),
                        "vpc_id": inst.get("VpcId", "")})


def build_aws_ec2_classified(instances: list[dict], region: str,
                             account: str = "default") -> list[Cluster]:
    """Tag-aware EC2 grouping — covers self-deployed HBase / Kafka / etc.

    Reads `Service` / `ClusterName` / `Role` tags on each instance to group
    related EC2 instances into a single typed cluster matching the
    diag-{tech}.md filenames.

    Untagged instances fall through to `tech=ec2` (per-instance clusters).
    """
    from ._classify import group_instances_into_clusters, normalize_aws_tags
    return group_instances_into_clusters(
        instances,
        tag_extractor=lambda i: normalize_aws_tags(i.get("Tags")),
        fqdn_extractor=lambda i: (i.get("PrivateDnsName")
                                  or i.get("PublicDnsName")
                                  or i.get("PrivateIpAddress")
                                  or i.get("InstanceId", "")),
        instance_id_extractor=lambda i: i.get("InstanceId", "ec2-unknown"),
        cluster_id_prefix=f"aws/{account}/{region}",
        discovery_source="aws-ec2-tagged",
        region=region, account=account, default_tech="ec2",
        extra_host_tags=lambda i: {
            "instance_type": i.get("InstanceType", ""),
            "az": i.get("Placement", {}).get("AvailabilityZone", ""),
            "vpc_id": i.get("VpcId", ""),
            "instance_id": i.get("InstanceId", ""),
        },
        extra_metadata=lambda i: {"vpc_id": i.get("VpcId", "")},
    )


def build_aws_ecs_cluster(c: dict, region: str, account: str = "default") -> Cluster:
    name = c.get("clusterName") or c.get("ClusterName", "ecs-unknown")
    return _logical_cluster(account, region, "ecs", name,
        metadata_extra={"running_tasks": c.get("RunningTasksCount"),
                        "active_services": c.get("ActiveServicesCount")})


def build_aws_eks_cluster(c: dict, region: str, account: str = "default") -> Cluster:
    name = c.get("name", "eks-unknown")
    return _logical_cluster(account, region, "eks", name,
        extra_tags={"k8s_version": c.get("version", "")},
        metadata_extra={"endpoint": c.get("endpoint"),
                        "status": c.get("status")})


def build_aws_ecr_repo(r: dict, region: str, account: str = "default") -> Cluster:
    name = r.get("repositoryName", "ecr-unknown")
    return _logical_cluster(account, region, "ecr", name,
        metadata_extra={"uri": r.get("repositoryUri")})


# ─── messaging / streaming ───
def build_aws_sqs_queue(url: str, region: str, account: str = "default") -> Cluster:
    name = url.rsplit("/", 1)[-1]
    return _logical_cluster(account, region, "aws-sqs", name,
        metadata_extra={"url": url})


def build_aws_sns_topic(t: dict, region: str, account: str = "default") -> Cluster:
    arn = t.get("TopicArn", "")
    name = arn.rsplit(":", 1)[-1] if arn else "sns-unknown"
    return _logical_cluster(account, region, "sns", name,
        metadata_extra={"arn": arn})


def build_aws_kinesis_stream(s: dict, region: str, account: str = "default") -> Cluster:
    name = s.get("StreamName") if isinstance(s, dict) else str(s)
    return _logical_cluster(account, region, "kinesis", name)


def build_aws_eventbridge_rule(r: dict, region: str, account: str = "default") -> Cluster:
    name = r.get("Name", "rule-unknown")
    return _logical_cluster(account, region, "eventbridge", name,
        metadata_extra={"state": r.get("State")})


# ─── data / state ───
def build_aws_dynamodb_table(t: dict, region: str, account: str = "default") -> Cluster:
    name = t.get("TableName", t) if isinstance(t, dict) else t
    return _logical_cluster(account, region, "dynamodb", str(name))


def build_aws_neptune(c: dict, region: str, account: str = "default") -> Cluster:
    name = c.get("DBClusterIdentifier", "neptune-unknown")
    return _logical_cluster(account, region, "neptune", name,
        metadata_extra={"endpoint": c.get("Endpoint"),
                        "engine_version": c.get("EngineVersion")})


# ─── networking ───
def build_aws_vpc(v: dict, region: str, account: str = "default") -> Cluster:
    vid = v.get("VpcId", "vpc-unknown")
    return _logical_cluster(account, region, "aws-vpc", vid,
        extra_tags={"cidr": v.get("CidrBlock", "")})


def build_aws_alb(lb: dict, region: str, account: str = "default") -> Cluster:
    name = lb.get("LoadBalancerName", "alb-unknown")
    tech = "aws-alb" if lb.get("Type") == "application" else "nlb"
    return _logical_cluster(account, region, tech, name,
        extra_tags={"scheme": lb.get("Scheme", ""), "type": lb.get("Type", "")},
        metadata_extra={"dns": lb.get("DNSName")})


def build_aws_route53_zone(z: dict, region: str = "global",
                          account: str = "default") -> Cluster:
    name = z.get("Name", "").rstrip(".") or "zone-unknown"
    return _logical_cluster(account, region, "route53", name,
        extra_tags={"private": str(z.get("Config", {}).get("PrivateZone", False))},
        metadata_extra={"record_count": z.get("ResourceRecordSetCount")})


def build_aws_cloudfront(d: dict, region: str = "global",
                        account: str = "default") -> Cluster:
    did = d.get("Id", "cf-unknown")
    return _logical_cluster(account, region, "cloudfront", did,
        extra_tags={"status": d.get("Status", "")},
        metadata_extra={"domain": d.get("DomainName")})


def build_aws_apigateway(api: dict, region: str, account: str = "default") -> Cluster:
    aid = api.get("id") or api.get("ApiId", "")
    name = api.get("name") or api.get("Name", aid)
    return _logical_cluster(account, region, "aws-api-gateway", name,
        metadata_extra={"id": aid, "endpoint_type":
                        api.get("EndpointConfiguration", {}).get("Types", [""])[0]})


# ─── identity / config / observability ───
def build_aws_iam_user(u: dict, region: str = "global",
                      account: str = "default") -> Cluster:
    name = u.get("UserName", "iam-unknown")
    return _logical_cluster(account, region, "aws-iam", name,
        metadata_extra={"arn": u.get("Arn"),
                        "last_used": str(u.get("PasswordLastUsed", ""))})


def build_aws_secret(s: dict, region: str, account: str = "default") -> Cluster:
    name = s.get("Name", "secret-unknown")
    return _logical_cluster(account, region, "secrets-manager", name,
        metadata_extra={"arn": s.get("ARN"),
                        "last_changed": str(s.get("LastChangedDate", ""))})


def build_aws_acm_cert(c: dict, region: str, account: str = "default") -> Cluster:
    name = c.get("DomainName", "cert-unknown")
    return _logical_cluster(account, region, "acm", name,
        extra_tags={"status": c.get("Status", "")},
        metadata_extra={"arn": c.get("CertificateArn")})


def build_aws_ssm_param(p: dict, region: str, account: str = "default") -> Cluster:
    name = p.get("Name", "ssm-unknown")
    return _logical_cluster(account, region, "ssm", name,
        metadata_extra={"type": p.get("Type"),
                        "version": p.get("Version")})


def build_aws_cloudwatch_alarm(a: dict, region: str, account: str = "default") -> Cluster:
    name = a.get("AlarmName", "alarm-unknown")
    return _logical_cluster(account, region, "cloudwatch", name,
        extra_tags={"state": a.get("StateValue", "")},
        metadata_extra={"namespace": a.get("Namespace")})


def build_aws_cloudtrail(t: dict, region: str, account: str = "default") -> Cluster:
    name = t.get("Name", "trail-unknown")
    return _logical_cluster(account, region, "cloudtrail", name,
        metadata_extra={"s3_bucket": t.get("S3BucketName"),
                        "is_multi_region": t.get("IsMultiRegionTrail")})


def build_aws_step_functions(sm: dict, region: str, account: str = "default") -> Cluster:
    name = sm.get("name", "stepfn-unknown")
    return _logical_cluster(account, region, "step-functions", name,
        metadata_extra={"arn": sm.get("stateMachineArn")})
