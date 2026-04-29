"""Vercel adapter — projects + storage stores (Postgres / KV / Blob)."""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host

_STORAGE_TYPE_MAP = {
    "postgres": "postgres", "neon": "postgres",
    "kv": "redis", "upstash-kv": "redis", "redis": "redis",
    "blob": "blob-storage",
}


def build_vercel_storage_cluster(store: dict) -> Optional[Cluster]:
    store_type = (store.get("type") or "").lower()
    tech = _STORAGE_TYPE_MAP.get(store_type)
    if not tech:
        return None
    sid = store.get("id") or store.get("name", "")
    cid = f"vercel/{sid}"
    return Cluster(
        id=cid, tech=tech, version=None,
        hosts=[Host(
            fqdn=store.get("primaryRegion") or store.get("name", sid),
            role="primary",
            tags={"region": store.get("primaryRegion", ""),
                  "name": store.get("name", "")},
            cluster_id=cid, health="active",
        )],
        discovery_source="vercel",
        metadata={"vercel_store_id": sid, "store_type": store_type,
                  "tech_confidence": "high", "tech_signal": f"vercel-storage:{store_type}"},
    )


class VercelAdapter:
    BASE = "https://api.vercel.com"

    def __init__(self, token: str, team_id: Optional[str] = None, timeout: int = 15):
        self.token = token
        self.team_id = team_id
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "VercelAdapter":
        return cls(token=os.environ.get("VERCEL_TOKEN", ""),
                   team_id=os.environ.get("VERCEL_TEAM_ID"))

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.token:
            return []
        clusters = []
        try:  # pragma: no cover (network)
            params = {"teamId": self.team_id} if self.team_id else None
            resp = self.session.get(f"{self.BASE}/v1/storage/stores",
                                    params=params, timeout=self.timeout)
            resp.raise_for_status()
            for store in resp.json().get("stores", []):
                cluster = build_vercel_storage_cluster(store)
                if cluster and (not tech_filter or cluster.tech == tech_filter):
                    clusters.append(cluster)
        except Exception:  # noqa  # pragma: no cover
            pass
        return clusters
