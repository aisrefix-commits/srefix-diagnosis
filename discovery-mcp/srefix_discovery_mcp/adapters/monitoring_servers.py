"""Zabbix / Nagios / OpenFaaS / Knative / Terraform / Docker — small adapters
batched together. Most "tools" don't have clusters in the traditional sense,
but they have managed inventories (zabbix hosts, nagios services, etc.).
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host


# ───── Zabbix server ─────
class ZabbixAdapter:
    """Zabbix exposes a JSON-RPC API that lists managed hosts and host groups."""
    def __init__(self, url: str, token: str, timeout: int = 15):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "ZabbixAdapter":
        return cls(
            url=os.environ.get("ZABBIX_URL", ""),
            token=os.environ.get("ZABBIX_API_TOKEN", ""),
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not (self.url and self.token):
            return []
        if tech_filter and tech_filter != "zabbix":
            return []
        try:  # pragma: no cover (network)
            resp = requests.post(
                f"{self.url}/api_jsonrpc.php",
                json={"jsonrpc": "2.0", "method": "hostgroup.get",
                      "params": {"output": "extend"}, "auth": self.token, "id": 1},
                timeout=self.timeout,
            )
            groups = resp.json().get("result", [])
            cid = "zabbix/server"
            hosts = [Host(fqdn=g.get("name", ""), role="hostgroup",
                          tags={"groupid": g.get("groupid", "")},
                          cluster_id=cid) for g in groups]
            return [Cluster(id=cid, tech="zabbix", hosts=hosts,
                            discovery_source="zabbix",
                            metadata={"hostgroup_count": len(groups),
                                      "tech_confidence": "high",
                                      "tech_signal": "zabbix-api"})]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── Nagios (statusjson.cgi or status data file) ─────
class NagiosAdapter:
    """Nagios Core via statusjson.cgi (Nagios XI / Core 4+)."""
    def __init__(self, url: str, username: str, password: str, timeout: int = 15):
        self.url = url.rstrip("/")
        self.auth = (username, password) if username else None
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "NagiosAdapter":
        return cls(
            url=os.environ.get("NAGIOS_URL", ""),
            username=os.environ.get("NAGIOS_USER", ""),
            password=os.environ.get("NAGIOS_PASSWORD", ""),
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.url:
            return []
        if tech_filter and tech_filter != "nagios":
            return []
        try:  # pragma: no cover (network)
            resp = requests.get(
                f"{self.url}/cgi-bin/statusjson.cgi",
                params={"query": "hostlist"}, auth=self.auth, timeout=self.timeout,
            )
            data = resp.json().get("data", {}).get("hostlist", {})
            cid = "nagios/server"
            hosts = [Host(fqdn=h, role="monitored", cluster_id=cid)
                     for h in data.keys()]
            return [Cluster(id=cid, tech="nagios", hosts=hosts,
                            discovery_source="nagios",
                            metadata={"host_count": len(hosts),
                                      "tech_confidence": "high",
                                      "tech_signal": "nagios-statusjson"})]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── OpenFaaS (functions endpoint) ─────
class OpenFaaSAdapter:
    def __init__(self, url: str, basic_auth: Optional[tuple] = None,
                 timeout: int = 10):
        self.url = url.rstrip("/")
        self.auth = basic_auth
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "OpenFaaSAdapter":
        user = os.environ.get("OPENFAAS_USER")
        pwd = os.environ.get("OPENFAAS_PASSWORD")
        return cls(
            url=os.environ.get("OPENFAAS_URL", ""),
            basic_auth=(user, pwd) if (user and pwd) else None,
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.url:
            return []
        if tech_filter and tech_filter != "openfaas":
            return []
        try:  # pragma: no cover (network)
            resp = requests.get(f"{self.url}/system/functions",
                                auth=self.auth, timeout=self.timeout)
            resp.raise_for_status()
            cid = "openfaas/gateway"
            hosts = [Host(fqdn=f.get("name", ""), role="function",
                          tags={"replicas": str(f.get("replicas", 0)),
                                "image": f.get("image", "")},
                          cluster_id=cid)
                     for f in resp.json()]
            return [Cluster(id=cid, tech="openfaas", hosts=hosts,
                            discovery_source="openfaas",
                            metadata={"function_count": len(hosts),
                                      "tech_confidence": "high",
                                      "tech_signal": "openfaas-gateway"})]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── Knative (services via K8s CRD) ─────
class KnativeAdapter:
    """Knative Services are CRDs in K8s. Reads `services.serving.knative.dev`."""
    def __init__(self, contexts: Optional[list[str]] = None,
                 kubeconfig: Optional[str] = None):
        self.contexts = contexts or [None]
        self.kubeconfig = kubeconfig

    @classmethod
    def from_env(cls) -> "KnativeAdapter":
        ctxs = os.environ.get("KNATIVE_CONTEXTS", "")
        return cls(
            contexts=[c.strip() for c in ctxs.split(",") if c.strip()] or None,
            kubeconfig=os.environ.get("KUBECONFIG"),
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if tech_filter and tech_filter != "knative":
            return []
        clusters: list[Cluster] = []
        for ctx in self.contexts:
            try:  # pragma: no cover (network)
                from kubernetes import client, config  # type: ignore
                config.load_kube_config(config_file=self.kubeconfig, context=ctx)
                api = client.CustomObjectsApi()
                services = api.list_cluster_custom_object(
                    group="serving.knative.dev", version="v1", plural="services"
                ).get("items", [])
                ctx_label = ctx or "default"
                for svc in services:
                    name = svc.get("metadata", {}).get("name", "")
                    ns = svc.get("metadata", {}).get("namespace", "")
                    cid = f"knative/{ctx_label}/{ns}/{name}"
                    clusters.append(Cluster(
                        id=cid, tech="knative",
                        hosts=[Host(fqdn=name, role="service",
                                    tags={"namespace": ns},
                                    cluster_id=cid,
                                    health=svc.get("status", {})
                                          .get("conditions", [{}])[0]
                                          .get("status", "unknown"))],
                        discovery_source="knative",
                        metadata={"context": ctx_label,
                                  "tech_confidence": "high",
                                  "tech_signal": "knative-service-crd"},
                    ))
            except Exception:  # noqa  # pragma: no cover
                continue
        return clusters
