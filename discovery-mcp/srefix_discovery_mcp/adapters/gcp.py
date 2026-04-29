"""GCP managed-services adapter.

Services covered:
  Cloud SQL       → tech: postgres / mysql / sqlserver (per database_version)
  AlloyDB         → tech: alloydb
  Memorystore     → tech: redis
  Cloud Spanner   → tech: cloud-spanner
  BigQuery        → tech: bigquery (one logical "cluster" per project/dataset)
  Bigtable        → tech: bigtable

Each service is opt-in via env GCP_DISCOVERY_SERVICES.
Auth uses ambient ADC (Application Default Credentials).
"""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Cluster, Host

_CLOUDSQL_DB_VER_MAP = {
    "POSTGRES": "postgres",
    "MYSQL": "mysql",
    "SQLSERVER": "sqlserver",
}


def _cloudsql_tech(database_version: str) -> str:
    for prefix, tech in _CLOUDSQL_DB_VER_MAP.items():
        if database_version.startswith(prefix):
            return tech
    return "postgres"  # safe default


def build_cloudsql_cluster(instance: dict, project: str) -> Cluster:
    name = instance.get("name", "")
    db_ver = instance.get("databaseVersion", "")
    tech = _cloudsql_tech(db_ver)
    region = instance.get("region", "")
    cluster_id = f"gcp/{project}/{region}/cloudsql/{name}"

    hosts: list[Host] = []
    primary_ip = next(
        (i["ipAddress"] for i in (instance.get("ipAddresses") or [])
         if i.get("type") == "PRIMARY"),
        None,
    )
    hosts.append(Host(
        fqdn=primary_ip or name, address=primary_ip,
        port=5432 if tech == "postgres" else 3306,
        role="primary",
        tags={"project": project, "region": region, "tier": instance.get("settings", {}).get("tier", "")},
        cluster_id=cluster_id,
        health=instance.get("state", "unknown"),
    ))
    # Replica refs
    for replica_name in instance.get("replicaNames", []) or []:
        hosts.append(Host(
            fqdn=replica_name, address=None, port=None, role="replica",
            tags={"project": project, "region": region},
            cluster_id=cluster_id,
        ))
    return Cluster(
        id=cluster_id, tech=tech, version=db_ver,
        hosts=hosts, discovery_source="gcp-cloudsql",
        metadata={"project": project, "region": region,
                  "tech_confidence": "high",
                  "tech_signal": f"cloudsql:{db_ver}"},
    )


def build_memorystore_cluster(instance: dict, project: str, region: str) -> Cluster:
    name = instance.get("name", "").rsplit("/", 1)[-1]
    cluster_id = f"gcp/{project}/{region}/memorystore/{name}"
    hosts = [Host(
        fqdn=instance.get("host") or name,
        address=instance.get("host"),
        port=instance.get("port", 6379),
        role="primary",
        tags={"project": project, "region": region,
              "tier": instance.get("tier", "")},
        cluster_id=cluster_id,
        health=instance.get("state", "unknown"),
    )]
    if instance.get("readReplicasMode") == "READ_REPLICAS_ENABLED":
        for rep in instance.get("readReplicasInstances", []) or []:
            hosts.append(Host(
                fqdn=rep.get("host", ""), address=rep.get("host"),
                port=rep.get("port"), role="replica",
                tags={"project": project, "region": region},
                cluster_id=cluster_id,
            ))
    return Cluster(
        id=cluster_id, tech="redis", version=instance.get("redisVersion"),
        hosts=hosts, discovery_source="gcp-memorystore",
        metadata={"project": project, "region": region,
                  "tech_confidence": "high", "tech_signal": "memorystore"},
    )


def build_alloydb_cluster(cluster_data: dict, instances: list[dict],
                          project: str, region: str) -> Cluster:
    cid = cluster_data.get("name", "").rsplit("/", 1)[-1]
    full_id = f"gcp/{project}/{region}/alloydb/{cid}"
    hosts: list[Host] = []
    for inst in instances:
        role = "primary" if inst.get("instanceType") == "PRIMARY" else "read-pool"
        hosts.append(Host(
            fqdn=inst.get("ipAddress") or inst.get("name", ""),
            address=inst.get("ipAddress"),
            port=5432, role=role,
            tags={"project": project, "region": region},
            cluster_id=full_id, health=inst.get("state", "unknown"),
        ))
    return Cluster(
        id=full_id, tech="alloydb",
        version=cluster_data.get("databaseVersion"),
        hosts=hosts, discovery_source="gcp-alloydb",
        metadata={"project": project, "region": region,
                  "tech_confidence": "high", "tech_signal": "alloydb"},
    )


class GCPAdapter:
    def __init__(self, projects: list[str], regions: Optional[list[str]] = None,
                 services: Optional[list[str]] = None):
        self.projects = projects
        self.regions = regions or ["-"]  # "-" = all regions in GCP REST APIs
        self.services = set(services or [
            "cloudsql", "memorystore", "alloydb", "spanner", "bigquery",
            # extended
            "gcs", "gce", "gke", "pubsub", "cloud_run", "cloud_functions",
            "cloud_build", "cloud_dns", "cloud_tasks", "cloud_scheduler",
            "iam", "secret_manager", "artifact_registry",
            "firestore", "filestore",
        ])

    @classmethod
    def from_env(cls) -> "GCPAdapter":
        projects = [p.strip() for p in os.environ.get("GCP_PROJECTS", "").split(",")
                    if p.strip()]
        regions = [r.strip() for r in os.environ.get("GCP_DISCOVERY_REGIONS", "").split(",")
                   if r.strip()] or None
        services = os.environ.get("GCP_DISCOVERY_SERVICES", "")
        services_list = [s.strip() for s in services.split(",") if s.strip()] or None
        return cls(projects=projects, regions=regions, services=services_list)

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.projects:
            return []
        clusters: list[Cluster] = []
        for project in self.projects:
            if "cloudsql" in self.services:
                clusters.extend(self._discover_cloudsql(project))
            if "memorystore" in self.services:
                for region in self.regions:
                    clusters.extend(self._discover_memorystore(project, region))
            if "alloydb" in self.services:
                for region in self.regions:
                    clusters.extend(self._discover_alloydb(project, region))
            if "spanner" in self.services:
                clusters.extend(self._discover_spanner(project))
            if "bigquery" in self.services:
                clusters.extend(self._discover_bigquery(project))
            # ─── extended ───
            if "gcs" in self.services:
                clusters.extend(self._discover_gcs(project))
            if "gce" in self.services:
                clusters.extend(self._discover_gce(project))
            if "gke" in self.services:
                clusters.extend(self._discover_gke(project))
            if "pubsub" in self.services:
                clusters.extend(self._discover_pubsub(project))
            if "cloud_run" in self.services:
                clusters.extend(self._discover_cloud_run(project))
            if "cloud_functions" in self.services:
                clusters.extend(self._discover_cloud_functions(project))
            if "cloud_build" in self.services:
                clusters.extend(self._discover_cloud_build(project))
            if "cloud_dns" in self.services:
                clusters.extend(self._discover_cloud_dns(project))
            if "cloud_tasks" in self.services:
                clusters.extend(self._discover_cloud_tasks(project))
            if "cloud_scheduler" in self.services:
                clusters.extend(self._discover_cloud_scheduler(project))
            if "iam" in self.services:
                clusters.extend(self._discover_iam(project))
            if "secret_manager" in self.services:
                clusters.extend(self._discover_secret_manager(project))
            if "artifact_registry" in self.services:
                clusters.extend(self._discover_artifact_registry(project))
            if "firestore" in self.services:
                clusters.extend(self._discover_firestore(project))
            if "filestore" in self.services:
                for region in self.regions:
                    clusters.extend(self._discover_filestore(project, region))
        if tech_filter:
            clusters = [c for c in clusters if c.tech == tech_filter]
        return clusters

    def _discover_cloudsql(self, project: str):  # pragma: no cover (network)
        try:
            from googleapiclient import discovery as gdiscovery  # type: ignore
        except ImportError as e:
            raise RuntimeError("google-api-python-client not installed; "
                               "pip install 'srefix-discovery-mcp[gcp]'") from e
        api = gdiscovery.build("sqladmin", "v1beta4")
        req = api.instances().list(project=project)
        while req is not None:
            resp = req.execute()
            for inst in resp.get("items", []):
                yield build_cloudsql_cluster(inst, project)
            req = api.instances().list_next(previous_request=req, previous_response=resp)

    def _discover_memorystore(self, project: str, region: str):  # pragma: no cover
        try:
            from googleapiclient import discovery as gdiscovery  # type: ignore
        except ImportError as e:
            raise RuntimeError("google-api-python-client missing") from e
        api = gdiscovery.build("redis", "v1")
        parent = f"projects/{project}/locations/{region}"
        req = api.projects().locations().instances().list(parent=parent)
        while req is not None:
            try:
                resp = req.execute()
            except Exception:  # noqa
                return
            for inst in resp.get("instances", []):
                yield build_memorystore_cluster(inst, project, region)
            req = api.projects().locations().instances().list_next(
                previous_request=req, previous_response=resp)

    def _discover_alloydb(self, project: str, region: str):  # pragma: no cover
        # AlloyDB API requires google-cloud-alloydb; skip if not installed
        try:
            from google.cloud import alloydb_v1  # type: ignore
        except ImportError:
            return
        client = alloydb_v1.AlloyDBAdminClient()
        parent = f"projects/{project}/locations/{region}"
        try:
            for cluster in client.list_clusters(parent=parent):
                inst_list = list(client.list_instances(parent=cluster.name))
                yield build_alloydb_cluster(
                    {"name": cluster.name, "databaseVersion": cluster.database_version.name},
                    [{"name": i.name, "instanceType": i.instance_type.name,
                      "ipAddress": i.ip_address, "state": i.state.name}
                     for i in inst_list],
                    project, region,
                )
        except Exception:  # noqa
            return

    def _discover_spanner(self, project: str):  # pragma: no cover
        try:
            from google.cloud import spanner  # type: ignore
        except ImportError:
            return
        client = spanner.Client(project=project)
        for inst in client.list_instances():
            yield Cluster(
                id=f"gcp/{project}/spanner/{inst.instance_id}",
                tech="cloud-spanner",
                hosts=[Host(fqdn=inst.instance_id, role="instance",
                            tags={"project": project,
                                  "config": str(inst.configuration_name),
                                  "node_count": str(inst.node_count)},
                            cluster_id=f"gcp/{project}/spanner/{inst.instance_id}")],
                discovery_source="gcp-spanner",
                metadata={"project": project, "tech_confidence": "high",
                          "tech_signal": "spanner-instance"},
            )

    def _discover_bigquery(self, project: str):  # pragma: no cover
        try:
            from google.cloud import bigquery  # type: ignore
        except ImportError:
            return
        client = bigquery.Client(project=project)
        # BigQuery doesn't have "clusters" — emit one logical Cluster per project
        try:
            datasets = list(client.list_datasets(max_results=50))
        except Exception:  # noqa
            return
        yield Cluster(
            id=f"gcp/{project}/bigquery", tech="bigquery",
            hosts=[Host(fqdn=f"bigquery.{project}", role="service",
                        tags={"project": project, "dataset_count": str(len(datasets))},
                        cluster_id=f"gcp/{project}/bigquery")],
            discovery_source="gcp-bigquery",
            metadata={"project": project, "tech_confidence": "high",
                      "tech_signal": "bigquery-project"},
        )

    # ──────────────── extended-service discoverers ────────────────

    def _gapi(self, name: str, version: str):  # pragma: no cover
        from googleapiclient import discovery as _d
        return _d.build(name, version)

    def _discover_gcs(self, project: str):  # pragma: no cover
        from .gcp_extended import build_gcs_bucket
        try:
            from google.cloud import storage  # type: ignore
            client = storage.Client(project=project)
            for b in client.list_buckets():
                yield build_gcs_bucket({
                    "name": b.name, "location": b.location,
                    "storageClass": b.storage_class,
                    "versioning": {"enabled": b.versioning_enabled},
                }, project)
        except Exception:  # noqa
            return

    def _discover_gce(self, project: str):  # pragma: no cover
        from .gcp_extended import build_gce_instance
        try:
            api = self._gapi("compute", "v1")
            req = api.instances().aggregatedList(project=project)
            while req is not None:
                resp = req.execute()
                for zone, val in (resp.get("items") or {}).items():
                    for inst in val.get("instances", []) or []:
                        yield build_gce_instance(inst, project)
                req = api.instances().aggregatedList_next(req, resp)
        except Exception:  # noqa
            return

    def _discover_gke(self, project: str):  # pragma: no cover
        from .gcp_extended import build_gke_cluster
        try:
            api = self._gapi("container", "v1")
            resp = api.projects().locations().clusters().list(
                parent=f"projects/{project}/locations/-").execute()
            for c in resp.get("clusters", []):
                yield build_gke_cluster(c, project)
        except Exception:  # noqa
            return

    def _discover_pubsub(self, project: str):  # pragma: no cover
        from .gcp_extended import build_pubsub_topic
        try:
            from google.cloud import pubsub_v1  # type: ignore
            client = pubsub_v1.PublisherClient()
            for t in client.list_topics(request={"project": f"projects/{project}"}):
                yield build_pubsub_topic({"name": t.name}, project)
        except Exception:  # noqa
            return

    def _discover_cloud_run(self, project: str):  # pragma: no cover
        from .gcp_extended import build_cloud_run_service
        try:
            api = self._gapi("run", "v1")
            for region in self.regions:
                if region == "-":
                    continue
                resp = api.projects().locations().services().list(
                    parent=f"projects/{project}/locations/{region}").execute()
                for s in resp.get("items", []) or []:
                    yield build_cloud_run_service(s, project)
        except Exception:  # noqa
            return

    def _discover_cloud_functions(self, project: str):  # pragma: no cover
        from .gcp_extended import build_cloud_function
        try:
            api = self._gapi("cloudfunctions", "v1")
            resp = api.projects().locations().functions().list(
                parent=f"projects/{project}/locations/-").execute()
            for f in resp.get("functions", []) or []:
                yield build_cloud_function(f, project)
        except Exception:  # noqa
            return

    def _discover_cloud_build(self, project: str):  # pragma: no cover
        from .gcp_extended import build_cloud_build_trigger
        try:
            api = self._gapi("cloudbuild", "v1")
            resp = api.projects().triggers().list(projectId=project).execute()
            for t in resp.get("triggers", []) or []:
                yield build_cloud_build_trigger(t, project)
        except Exception:  # noqa
            return

    def _discover_cloud_dns(self, project: str):  # pragma: no cover
        from .gcp_extended import build_cloud_dns_zone
        try:
            api = self._gapi("dns", "v1")
            resp = api.managedZones().list(project=project).execute()
            for z in resp.get("managedZones", []) or []:
                yield build_cloud_dns_zone(z, project)
        except Exception:  # noqa
            return

    def _discover_cloud_tasks(self, project: str):  # pragma: no cover
        from .gcp_extended import build_cloud_tasks_queue
        try:
            api = self._gapi("cloudtasks", "v2")
            for region in self.regions:
                if region == "-":
                    continue
                resp = api.projects().locations().queues().list(
                    parent=f"projects/{project}/locations/{region}").execute()
                for q in resp.get("queues", []) or []:
                    yield build_cloud_tasks_queue(q, project)
        except Exception:  # noqa
            return

    def _discover_cloud_scheduler(self, project: str):  # pragma: no cover
        from .gcp_extended import build_cloud_scheduler_job
        try:
            api = self._gapi("cloudscheduler", "v1")
            for region in self.regions:
                if region == "-":
                    continue
                resp = api.projects().locations().jobs().list(
                    parent=f"projects/{project}/locations/{region}").execute()
                for j in resp.get("jobs", []) or []:
                    yield build_cloud_scheduler_job(j, project)
        except Exception:  # noqa
            return

    def _discover_iam(self, project: str):  # pragma: no cover
        from .gcp_extended import build_gcp_iam_role
        try:
            api = self._gapi("iam", "v1")
            resp = api.projects().roles().list(
                parent=f"projects/{project}").execute()
            for r in resp.get("roles", []) or []:
                yield build_gcp_iam_role(r, project)
        except Exception:  # noqa
            return

    def _discover_secret_manager(self, project: str):  # pragma: no cover
        from .gcp_extended import build_gcp_secret
        try:
            api = self._gapi("secretmanager", "v1")
            resp = api.projects().secrets().list(
                parent=f"projects/{project}").execute()
            for s in resp.get("secrets", []) or []:
                yield build_gcp_secret(s, project)
        except Exception:  # noqa
            return

    def _discover_artifact_registry(self, project: str):  # pragma: no cover
        from .gcp_extended import build_artifact_registry_repo
        try:
            api = self._gapi("artifactregistry", "v1")
            for region in self.regions:
                if region == "-":
                    continue
                resp = api.projects().locations().repositories().list(
                    parent=f"projects/{project}/locations/{region}").execute()
                for r in resp.get("repositories", []) or []:
                    yield build_artifact_registry_repo(r, project)
        except Exception:  # noqa
            return

    def _discover_firestore(self, project: str):  # pragma: no cover
        from .gcp_extended import build_firestore_database
        try:
            api = self._gapi("firestore", "v1")
            resp = api.projects().databases().list(
                parent=f"projects/{project}").execute()
            for d in resp.get("databases", []) or []:
                yield build_firestore_database(d, project)
        except Exception:  # noqa
            return

    def _discover_filestore(self, project: str, region: str):  # pragma: no cover
        from .gcp_extended import build_filestore_instance
        if region == "-":
            return
        try:
            api = self._gapi("file", "v1")
            resp = api.projects().locations().instances().list(
                parent=f"projects/{project}/locations/{region}").execute()
            for i in resp.get("instances", []) or []:
                yield build_filestore_instance(i, project)
        except Exception:  # noqa
            return
