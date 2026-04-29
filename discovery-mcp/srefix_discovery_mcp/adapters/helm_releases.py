"""Helm releases adapter — discover what's installed on a K8s cluster via Helm.

Reads Helm v3 release secrets from the K8s API (Helm stores releases as
Secrets with type `helm.sh/release.v1` in the release namespace).

This is the "Helm" tech that has actual installed instances. Pure k8s clients.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
from typing import Optional

from ..core.models import Cluster, Host

# Use canonical mapping for chart-name → tech identification
from .opscloud4 import _TECH_ALIAS_TO_CANONICAL, _TECH_NAME_PATTERN


def _normalize(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip().lower()
    if s in _TECH_ALIAS_TO_CANONICAL:
        return _TECH_ALIAS_TO_CANONICAL[s]
    m = _TECH_NAME_PATTERN.search(s)
    return _TECH_ALIAS_TO_CANONICAL.get(m.group(1).lower()) if m else None


def parse_helm_release_secret(secret_data: str) -> dict:
    """Helm release secret data is base64(gzip(JSON))."""
    try:
        compressed = base64.b64decode(secret_data)
        return json.loads(gzip.decompress(compressed).decode())
    except Exception:  # noqa
        return {}


def build_helm_release_cluster(release: dict, namespace: str,
                              context: str = "default") -> Cluster:
    name = release.get("name", "release-unknown")
    chart = (release.get("chart") or {}).get("metadata") or {}
    chart_name = chart.get("name", "")
    cid = f"helm/{context}/{namespace}/{name}"
    return Cluster(
        id=cid, tech="helm",
        version=chart.get("version"),
        hosts=[Host(
            fqdn=name, role="release",
            tags={"namespace": namespace, "chart": chart_name,
                  "chart_version": chart.get("version", ""),
                  "app_version": chart.get("appVersion", "")},
            cluster_id=cid,
            health=release.get("info", {}).get("status", "unknown"),
        )],
        discovery_source="helm",
        metadata={"chart_name": chart_name,
                  "chart_version": chart.get("version"),
                  "app_version": chart.get("appVersion"),
                  "tech_confidence": "high",
                  "tech_signal": "helm-release-secret"},
    )


def build_chart_app_cluster(release: dict, namespace: str,
                            tech: str, context: str = "default") -> Cluster:
    """For a release where chart name maps to a known tech, also emit an
    app-tech cluster (tech=postgres etc) so it shows up under that tech."""
    name = release.get("name", "release-unknown")
    cid = f"helm-app/{context}/{namespace}/{name}/{tech}"
    return Cluster(
        id=cid, tech=tech,
        hosts=[Host(fqdn=name, role="release",
                    tags={"namespace": namespace,
                          "chart": (release.get("chart") or {}).get("metadata", {}).get("name", "")},
                    cluster_id=cid)],
        discovery_source="helm",
        metadata={"release": name, "tech_confidence": "high",
                  "tech_signal": "helm-chart-name"},
    )


class HelmAdapter:
    def __init__(self, contexts: Optional[list[str]] = None,
                 kubeconfig: Optional[str] = None):
        self.contexts = contexts or [None]
        self.kubeconfig = kubeconfig

    @classmethod
    def from_env(cls) -> "HelmAdapter":
        ctx_env = os.environ.get("HELM_CONTEXTS", "")
        contexts = [c.strip() for c in ctx_env.split(",") if c.strip()] or None
        return cls(contexts=contexts, kubeconfig=os.environ.get("KUBECONFIG"))

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        clusters: list[Cluster] = []
        for ctx in self.contexts:
            try:  # pragma: no cover (network)
                from kubernetes import client, config  # type: ignore
                config.load_kube_config(config_file=self.kubeconfig, context=ctx)
                v1 = client.CoreV1Api()
                ctx_label = ctx or "default"
                # Helm v3 releases are Secrets with label owner=helm
                secrets = v1.list_secret_for_all_namespaces(
                    label_selector="owner=helm", limit=500
                ).items
                for s in secrets:
                    if not s.data or "release" not in s.data:
                        continue
                    release = parse_helm_release_secret(s.data["release"])
                    if not release:
                        continue
                    ns = s.metadata.namespace
                    chart_name = (release.get("chart") or {}).get("metadata", {}).get("name", "")
                    chart_tech = _normalize(chart_name)
                    if not tech_filter or tech_filter == "helm":
                        clusters.append(build_helm_release_cluster(release, ns, ctx_label))
                    if chart_tech and (not tech_filter or tech_filter == chart_tech):
                        clusters.append(build_chart_app_cluster(release, ns, chart_tech, ctx_label))
            except Exception:  # noqa  # pragma: no cover
                continue
        return clusters
