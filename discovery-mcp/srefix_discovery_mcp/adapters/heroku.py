"""Heroku adapter — apps + add-ons (Postgres / Redis / Kafka / Mongo / etc.).

REST API at https://api.heroku.com/apps and /apps/{id}/addons.
Auth via Bearer token.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host

# Heroku addon service names → canonical tech
_ADDON_SERVICE_MAP = {
    "heroku-postgresql": "postgres",
    "heroku-redis": "redis",
    "heroku-kafka": "kafka",
    "mongolab": "mongo", "mongohq": "mongo", "mongo-rocket": "mongo",
    "rediscloud": "redis", "redistogo": "redis",
    "elements": None,  # generic
    "memcachier": "memcached", "memcachedcloud": "memcached",
    "rabbitmq-bigwig": "rabbitmq", "cloudamqp": "rabbitmq",
    "searchbox": "elasticsearch", "bonsai": "elasticsearch",
    "logdna": None, "papertrail": None,  # log services
}


def build_heroku_addon_cluster(app_name: str, addon: dict) -> Optional[Cluster]:
    service_name = (addon.get("addon_service") or {}).get("name", "")
    tech = _ADDON_SERVICE_MAP.get(service_name)
    if not tech:
        return None
    aid = addon.get("id") or addon.get("name", "")
    cid = f"heroku/{app_name}/{service_name}/{aid}"
    return Cluster(
        id=cid, tech=tech,
        hosts=[Host(
            fqdn=addon.get("name", aid), role="primary",
            tags={"app": app_name,
                  "plan": (addon.get("plan") or {}).get("name", ""),
                  "service": service_name},
            cluster_id=cid,
            health=addon.get("state", "unknown"),
        )],
        discovery_source="heroku",
        metadata={"app": app_name, "service": service_name,
                  "tech_confidence": "high",
                  "tech_signal": f"heroku-addon:{service_name}"},
    )


class HerokuAdapter:
    BASE = "https://api.heroku.com"

    def __init__(self, token: str, timeout: int = 15):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.heroku+json; version=3",
        })
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "HerokuAdapter":
        return cls(token=os.environ.get("HEROKU_API_TOKEN", ""))

    def discover(self, tech_filter: Optional[str] = None) -> list[Cluster]:
        if not self.token:
            return []
        clusters = []
        try:  # pragma: no cover (network)
            apps_resp = self.session.get(f"{self.BASE}/apps", timeout=self.timeout)
            apps_resp.raise_for_status()
            for app in apps_resp.json():
                app_name = app.get("name", "")
                addons_resp = self.session.get(
                    f"{self.BASE}/apps/{app_name}/addons", timeout=self.timeout
                )
                if addons_resp.status_code != 200:
                    continue
                for addon in addons_resp.json():
                    cluster = build_heroku_addon_cluster(app_name, addon)
                    if cluster and (not tech_filter or cluster.tech == tech_filter):
                        clusters.append(cluster)
        except Exception:  # noqa
            pass
        return clusters
