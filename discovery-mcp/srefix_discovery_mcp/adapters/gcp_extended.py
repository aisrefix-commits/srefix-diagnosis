"""GCP service builders — 17 additional services beyond core GCP adapter.

Output `tech` aligns with diag-{tech}.md filenames.
"""
from __future__ import annotations

from typing import Optional

from ..core.models import Cluster, Host


def _logical(project: str, region: str, tech: str, name: str,
             extra_tags: Optional[dict] = None,
             metadata: Optional[dict] = None) -> Cluster:
    cid = f"gcp/{project}/{region}/{tech}/{name}"
    return Cluster(
        id=cid, tech=tech,
        hosts=[Host(fqdn=name, role="resource",
                    tags={"project": project, "region": region,
                          **(extra_tags or {})},
                    cluster_id=cid)],
        discovery_source=f"gcp-{tech}",
        metadata={"project": project, "region": region,
                  "tech_confidence": "high", "tech_signal": tech,
                  **(metadata or {})},
    )


def build_gcs_bucket(b: dict, project: str) -> Cluster:
    name = b.get("name", "")
    location = (b.get("location") or "").lower()
    return _logical(project, location, "gcs", name,
        extra_tags={"storage_class": b.get("storageClass", "")},
        metadata={"versioning": b.get("versioning", {}).get("enabled")})


def build_gce_instance(inst: dict, project: str) -> Cluster:
    name = inst.get("name", "")
    zone = (inst.get("zone") or "").rsplit("/", 1)[-1]
    return _logical(project, zone, "gce", name,
        extra_tags={"machine_type": (inst.get("machineType") or "").rsplit("/", 1)[-1],
                    "status": inst.get("status", "")})


def build_gce_instances_classified(instances: list[dict], project: str,
                                   zone: str = "") -> list[Cluster]:
    """Tag-aware GCE grouping (uses GCP `labels`, not metadata items).

    Convention: label your VMs with `service=hbase` etc. (GCP labels are
    lowercase, so the user's tag must use a lowercase service value.)
    """
    from ._classify import group_instances_into_clusters, normalize_gcp_labels
    return group_instances_into_clusters(
        instances,
        tag_extractor=lambda i: normalize_gcp_labels(i.get("labels")),
        fqdn_extractor=lambda i: (i.get("name", "")),
        instance_id_extractor=lambda i: i.get("name", "gce-unknown"),
        cluster_id_prefix=f"gcp/{project}/{zone or 'unknown'}",
        discovery_source="gcp-gce-tagged",
        region=zone, account=project, default_tech="gce",
        extra_host_tags=lambda i: {
            "machine_type": (i.get("machineType") or "").rsplit("/", 1)[-1],
            "status": i.get("status", ""),
            "zone": (i.get("zone") or "").rsplit("/", 1)[-1],
        },
    )


def build_gke_cluster(c: dict, project: str) -> Cluster:
    name = c.get("name", "")
    location = c.get("location", "")
    return _logical(project, location, "gke", name,
        extra_tags={"node_count": str(c.get("currentNodeCount", 0)),
                    "k8s_version": c.get("currentMasterVersion", "")},
        metadata={"endpoint": c.get("endpoint"), "status": c.get("status")})


def build_pubsub_topic(t: dict, project: str) -> Cluster:
    name = (t.get("name") or "").rsplit("/", 1)[-1]
    return _logical(project, "global", "pubsub", name,
        metadata={"full_name": t.get("name")})


def build_cloud_run_service(s: dict, project: str) -> Cluster:
    name = (s.get("name") or "").rsplit("/", 1)[-1]
    region = ((s.get("name") or "").split("/locations/")[-1].split("/")[0]
              if "/locations/" in (s.get("name") or "") else "")
    return _logical(project, region, "cloud-run", name,
        metadata={"url": (s.get("status") or {}).get("url")})


def build_cloud_function(f: dict, project: str) -> Cluster:
    name = (f.get("name") or "").rsplit("/", 1)[-1]
    region = ((f.get("name") or "").split("/locations/")[-1].split("/")[0]
              if "/locations/" in (f.get("name") or "") else "")
    return _logical(project, region, "cloud-functions", name,
        extra_tags={"runtime": f.get("runtime", ""),
                    "status": f.get("status", "")})


def build_cloud_build_trigger(t: dict, project: str) -> Cluster:
    name = t.get("name", t.get("id", "build-unknown"))
    return _logical(project, "global", "cloud-build", name,
        metadata={"description": t.get("description")})


def build_cloud_dns_zone(z: dict, project: str) -> Cluster:
    name = z.get("name", "zone-unknown")
    return _logical(project, "global", "cloud-dns", name,
        extra_tags={"dns_name": z.get("dnsName", "")})


def build_cloud_tasks_queue(q: dict, project: str) -> Cluster:
    name = (q.get("name") or "").rsplit("/", 1)[-1]
    return _logical(project, "global", "cloud-tasks", name)


def build_cloud_scheduler_job(j: dict, project: str) -> Cluster:
    name = (j.get("name") or "").rsplit("/", 1)[-1]
    return _logical(project, "global", "cloud-scheduler", name,
        metadata={"schedule": j.get("schedule"), "state": j.get("state")})


def build_cloud_armor_policy(p: dict, project: str) -> Cluster:
    name = p.get("name", "policy-unknown")
    return _logical(project, "global", "cloud-armor", name)


def build_cloud_router(r: dict, project: str) -> Cluster:
    name = r.get("name", "router-unknown")
    region = (r.get("region") or "").rsplit("/", 1)[-1]
    return _logical(project, region, "cloud-router", name)


def build_cloud_nat(g: dict, project: str) -> Cluster:
    name = g.get("name", "gateway-unknown")
    region = (g.get("region") or "").rsplit("/", 1)[-1]
    return _logical(project, region, "cloud-nat", name)


def build_gcp_iam_role(role: dict, project: str) -> Cluster:
    name = (role.get("name") or "").rsplit("/", 1)[-1]
    return _logical(project, "global", "gcp-iam", name,
        metadata={"description": role.get("description")})


def build_gcp_secret(s: dict, project: str) -> Cluster:
    name = (s.get("name") or "").rsplit("/", 1)[-1]
    return _logical(project, "global", "gcp-secret-manager", name)


def build_artifact_registry_repo(r: dict, project: str) -> Cluster:
    name = (r.get("name") or "").rsplit("/", 1)[-1]
    region = ((r.get("name") or "").split("/locations/")[-1].split("/")[0]
              if "/locations/" in (r.get("name") or "") else "")
    return _logical(project, region, "artifact-registry", name,
        extra_tags={"format": r.get("format", "")})


def build_firestore_database(d: dict, project: str) -> Cluster:
    name = (d.get("name") or "").rsplit("/", 1)[-1]
    location = d.get("locationId", "")
    return _logical(project, location, "firestore", name,
        extra_tags={"type": d.get("type", "")})


def build_filestore_instance(inst: dict, project: str) -> Cluster:
    name = (inst.get("name") or "").rsplit("/", 1)[-1]
    region = ((inst.get("name") or "").split("/locations/")[-1].split("/")[0]
              if "/locations/" in (inst.get("name") or "") else "")
    return _logical(project, region, "filestore", name,
        extra_tags={"tier": inst.get("tier", ""),
                    "state": inst.get("state", "")})
