"""Aggregate multiple discovery adapters behind one cached interface."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol

from .cache import TTLCache
from .models import Cluster, Host


class Adapter(Protocol):
    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]: ...


class DiscoveryRegistry:
    def __init__(self, adapters: list[Adapter], cache_ttl_seconds: int = 300):
        self.adapters = adapters
        self.cache = TTLCache(cache_ttl_seconds)
        self.last_run: Optional[datetime] = None
        self.errors: list[dict] = []

    def discover(self, tech_filter: Optional[str] = None, force: bool = False) -> list[Cluster]:
        cache_key = f"discover::{tech_filter or '*'}"
        if not force:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        clusters: list[Cluster] = []
        self.errors = []
        for adapter in self.adapters:
            adapter_name = adapter.__class__.__name__
            try:
                clusters.extend(adapter.discover(tech_filter))
            except Exception as e:  # noqa: BLE001  (collect, don't crash MCP)
                self.errors.append({
                    "adapter": adapter_name,
                    "error_type": type(e).__name__,
                    "error": str(e),
                })

        self.last_run = datetime.now(timezone.utc)
        self.cache.set(cache_key, clusters)
        return clusters

    def get_cluster(self, cluster_id: str) -> Optional[Cluster]:
        for c in self.discover():
            if c.id == cluster_id:
                return c
        return None

    def filter_hosts(
        self,
        cluster_id: Optional[str] = None,
        role: Optional[str] = None,
        tech: Optional[str] = None,
        tag_kv: Optional[str] = None,
    ) -> list[tuple[Cluster, Host]]:
        clusters = self.discover(tech_filter=tech)
        if cluster_id:
            clusters = [c for c in clusters if c.id == cluster_id]

        out: list[tuple[Cluster, Host]] = []
        for c in clusters:
            for h in c.hosts:
                if role and h.role != role:
                    continue
                if tag_kv:
                    if "=" in tag_kv:
                        k, _, v = tag_kv.partition("=")
                        if h.tags.get(k.strip()) != v.strip():
                            continue
                    else:
                        if tag_kv not in h.tags and tag_kv not in h.tags.values():
                            continue
                out.append((c, h))
        return out
