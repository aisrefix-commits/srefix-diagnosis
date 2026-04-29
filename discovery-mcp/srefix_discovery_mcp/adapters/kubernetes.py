"""Discover clusters via the Kubernetes API.

Strategy: scan StatefulSets + Deployments across namespaces, identify tech
from `app.kubernetes.io/name` (or `app`) label, expand to member pods.

Most operators (Strimzi / ECK / cass-operator / redis-operator / hbase-operator
/ crunchy-postgres / zalando-postgres / kafka-operator) create their pods under
StatefulSets with conventional labels, so a generic label-based scan covers them.

Supported common labels:
  app.kubernetes.io/name        → tech identification
  app.kubernetes.io/component   → role (master/data/coordinator/...)
  app.kubernetes.io/instance    → cluster instance name
  app                            → fallback to tech identification
  role / component              → fallback to role
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

from ..core.models import Cluster, Host

# Aliases reused from opscloud4 — keep canonical mapping consistent across adapters
from .opscloud4 import _CANONICAL_TECH, _TECH_ALIAS_TO_CANONICAL


def _normalize_tech(name: str) -> Optional[str]:
    if not name:
        return None
    return _TECH_ALIAS_TO_CANONICAL.get(name.strip().lower())


def _identify_role(pod_labels: dict, workload_labels: dict, tech: str) -> str:
    """Best-effort role inference from pod / workload labels."""
    for key in (
        "role",
        "component",
        "node-role",
        "node-type",
        "app.kubernetes.io/component",
        "statefulset.kubernetes.io/pod-name",  # last resort, not really role
    ):
        v = pod_labels.get(key) or workload_labels.get(key)
        if v and key != "statefulset.kubernetes.io/pod-name":
            return v.lower()
    return "unknown"


def _build_clusters(
    workloads_with_pods: Iterable[tuple[dict, list[dict]]],
    context: str,
    tech_filter: Optional[str] = None,
) -> list[Cluster]:
    """Pure transform: workload+pods → Cluster list. Easy to unit-test."""
    clusters: list[Cluster] = []
    for workload, pods in workloads_with_pods:
        labels = workload.get("labels", {}) or {}
        raw_tech = (
            labels.get("app.kubernetes.io/name")
            or labels.get("app")
            or labels.get("tech")
        )
        tech = _normalize_tech(raw_tech)
        if not tech:
            continue
        if tech_filter and tech != tech_filter:
            continue

        ns = workload.get("namespace", "default")
        name = workload.get("name", "unknown")
        cluster_id = f"k8s/{context}/{ns}/{name}"

        hosts: list[Host] = []
        for pod in pods:
            pod_labels = pod.get("labels", {}) or {}
            hosts.append(Host(
                fqdn=pod.get("name", ""),
                address=pod.get("ip"),
                port=None,
                role=_identify_role(pod_labels, labels, tech),
                tags={
                    "namespace": ns,
                    "node": pod.get("node", ""),
                    "context": context,
                    **{k: v for k, v in pod_labels.items() if not k.startswith("pod-template")},
                },
                cluster_id=cluster_id,
                health=pod.get("phase", "unknown"),
            ))

        clusters.append(Cluster(
            id=cluster_id,
            tech=tech,
            hosts=hosts,
            discovery_source="kubernetes",
            metadata={
                "context": context,
                "namespace": ns,
                "workload_kind": workload.get("kind", "Unknown"),
                "workload_name": name,
                "tech_signal": f"label:{labels.get('app.kubernetes.io/name') and 'app.kubernetes.io/name' or 'app'}",
                "tech_confidence": "high",
            },
        ))
    return clusters


class KubernetesAdapter:
    def __init__(
        self,
        contexts: Optional[list[str]] = None,
        kubeconfig: Optional[str] = None,
        in_cluster: bool = False,
    ):
        self.contexts = contexts or [None]  # None = current context
        self.kubeconfig = kubeconfig
        self.in_cluster = in_cluster

    @classmethod
    def from_env(cls) -> "KubernetesAdapter":
        contexts_env = os.environ.get("K8S_CONTEXTS")
        contexts = [c.strip() for c in contexts_env.split(",")] if contexts_env else None
        return cls(
            contexts=contexts,
            kubeconfig=os.environ.get("KUBECONFIG"),
            in_cluster=os.environ.get("K8S_IN_CLUSTER", "").lower() in ("1", "true"),
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        clusters: list[Cluster] = []
        for ctx in self.contexts:
            apps_api, core_api = self._build_apis(ctx)
            ctx_label = ctx or "default"
            workloads_with_pods = self._fetch(apps_api, core_api)
            clusters.extend(_build_clusters(workloads_with_pods, ctx_label, tech_filter))
        return clusters

    # ──────── kubernetes lib glue (lazy import so it's optional) ────────

    def _build_apis(self, context: Optional[str]):  # pragma: no cover (network)
        from kubernetes import client, config  # type: ignore

        if self.in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config(config_file=self.kubeconfig, context=context)
        return client.AppsV1Api(), client.CoreV1Api()

    def _fetch(self, apps_api: Any, core_api: Any) -> list[tuple[dict, list[dict]]]:  # pragma: no cover
        results: list[tuple[dict, list[dict]]] = []

        # List all namespaces, then per-namespace list StatefulSets + Deployments
        for ns_obj in core_api.list_namespace().items:
            ns = ns_obj.metadata.name
            for sts in apps_api.list_namespaced_stateful_set(ns).items:
                results.append((self._workload_dict(sts, "StatefulSet"), self._pods_for(core_api, sts)))
            for dep in apps_api.list_namespaced_deployment(ns).items:
                results.append((self._workload_dict(dep, "Deployment"), self._pods_for(core_api, dep)))
        return results

    @staticmethod
    def _workload_dict(wl: Any, kind: str) -> dict:
        return {
            "name": wl.metadata.name,
            "namespace": wl.metadata.namespace,
            "labels": dict(wl.metadata.labels or {}),
            "kind": kind,
        }

    @staticmethod
    def _pods_for(core_api: Any, wl: Any) -> list[dict]:
        match_labels = (wl.spec.selector.match_labels or {}) if wl.spec.selector else {}
        if not match_labels:
            return []
        selector = ",".join(f"{k}={v}" for k, v in match_labels.items())
        pods = core_api.list_namespaced_pod(wl.metadata.namespace, label_selector=selector).items
        return [
            {
                "name": p.metadata.name,
                "namespace": p.metadata.namespace,
                "labels": dict(p.metadata.labels or {}),
                "ip": p.status.pod_ip,
                "node": p.spec.node_name,
                "phase": p.status.phase,
            }
            for p in pods
        ]
