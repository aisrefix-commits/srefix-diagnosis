"""Backstage Catalog adapter — modern internal developer portal.

Backstage entities have spec.type values like: service / website / library / etc.
Component entities have annotations / labels / metadata.tags identifying tech.

Endpoints:
  GET /api/catalog/entities?filter=kind=Component   list components
  GET /api/catalog/entities?filter=kind=Resource    list resources (databases, queues)
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host

# Reuse canonical mapping + name regex
from .opscloud4 import _TECH_ALIAS_TO_CANONICAL, _TECH_NAME_PATTERN


def _normalize(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip().lower()
    if s in _TECH_ALIAS_TO_CANONICAL:
        return _TECH_ALIAS_TO_CANONICAL[s]
    m = _TECH_NAME_PATTERN.search(s)
    return _TECH_ALIAS_TO_CANONICAL.get(m.group(1).lower()) if m else None


def identify_tech(entity: dict) -> Optional[tuple[str, str, str]]:
    """Returns (tech, confidence, signal). Backstage doesn't enforce a tech type
    field, so we look at multiple places."""
    metadata = entity.get("metadata") or {}
    spec = entity.get("spec") or {}

    # ① spec.type (e.g. "service", "database", "kafka-topic" — usually low signal)
    spec_type = spec.get("type", "")
    tech = _normalize(spec_type)
    if tech:
        return tech, "medium", f"spec.type={spec_type}"

    # ② labels (k8s-style)
    for k, v in (metadata.get("labels") or {}).items():
        if k.lower() in ("tech", "app", "kind"):
            tech = _normalize(v)
            if tech:
                return tech, "high", f"label:{k}={v}"

    # ③ annotations (Backstage convention)
    for k, v in (metadata.get("annotations") or {}).items():
        if "kafka" in k.lower():
            return "kafka", "high", f"annotation:{k}"
        if k.endswith("/tech") or k.endswith("/middleware"):
            tech = _normalize(v)
            if tech:
                return tech, "high", f"annotation:{k}={v}"

    # ④ tags
    for tag in (metadata.get("tags") or []):
        tech = _normalize(tag)
        if tech:
            return tech, "high", f"tag:{tag}"

    # ⑤ name regex (last resort)
    name = metadata.get("name", "")
    tech = _normalize(name)
    if tech:
        return tech, "low", f"name:{name}"

    return None


def build_backstage_cluster(entity: dict, tech: str, confidence: str, signal: str) -> Cluster:
    metadata = entity.get("metadata") or {}
    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "default")
    cid = f"backstage/{namespace}/{name}"
    # Backstage entities don't usually expose host topology; treat as a single
    # logical Host record. Production teams pair Backstage with K8s/cloud
    # adapters for actual host data.
    spec = entity.get("spec") or {}
    return Cluster(
        id=cid, tech=tech,
        hosts=[Host(
            fqdn=name, role="logical",
            tags={"namespace": namespace,
                  "owner": str(spec.get("owner", "")),
                  "lifecycle": str(spec.get("lifecycle", "")),
                  "system": str(spec.get("system", ""))},
            cluster_id=cid,
        )],
        discovery_source="backstage",
        metadata={"name": name, "namespace": namespace,
                  "owner": spec.get("owner"),
                  "tech_confidence": confidence,
                  "tech_signal": signal,
                  "note": "Backstage entries are logical — pair with k8s/cloud adapter for hosts"},
    )


class BackstageAdapter:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "BackstageAdapter":
        return cls(
            base_url=os.environ.get("BACKSTAGE_URL", ""),
            token=os.environ.get("BACKSTAGE_TOKEN"),
        )

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.base_url:
            return []
        clusters: list[Cluster] = []
        for kind in ("Component", "Resource"):
            try:
                resp = self.session.get(
                    f"{self.base_url}/api/catalog/entities",
                    params={"filter": f"kind={kind}", "limit": 1000},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                entities = resp.json()
            except Exception:  # noqa  # pragma: no cover
                continue
            for entity in entities:
                ident = identify_tech(entity)
                if not ident:
                    continue
                tech, conf, sig = ident
                if tech_filter and tech != tech_filter:
                    continue
                clusters.append(build_backstage_cluster(entity, tech, conf, sig))
        return clusters
