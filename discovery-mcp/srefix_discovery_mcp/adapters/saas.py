"""SaaS platform adapters bundled into one module — all are thin HTTP clients.

Each platform exposes its account's logical resources as Clusters with
tech matching the diag-{tech}.md filename.

Covered:
  Cloudflare (zones)
  Datadog (monitors)
  Sentry (projects)
  Snowflake (warehouses)
  PlanetScale (databases)
  Auth0 (tenants)
  Okta (apps)
  Netlify (sites)
  PagerDuty (services)
  OpsGenie (teams)
  GitHub Actions (workflows)
  GitLab CI (projects)
  NewRelic (entities)
  Splunk (apps)
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.models import Cluster, Host


def _logical(tech: str, account: str, name: str,
             extra_tags: Optional[dict] = None,
             metadata: Optional[dict] = None) -> Cluster:
    cid = f"saas/{tech}/{account}/{name}"
    return Cluster(
        id=cid, tech=tech,
        hosts=[Host(fqdn=name, role="resource",
                    tags=extra_tags or {}, cluster_id=cid)],
        discovery_source=f"saas-{tech}",
        metadata={"account": account, "tech_confidence": "high",
                  "tech_signal": f"saas:{tech}", **(metadata or {})},
    )


# ───── Cloudflare ─────
class CloudflareAdapter:
    BASE = "https://api.cloudflare.com/client/v4"
    def __init__(self, token: str): self.token = token
    @classmethod
    def from_env(cls): return cls(os.environ.get("CLOUDFLARE_API_TOKEN", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not self.token: return []
        if tech_filter and tech_filter != "cloudflare": return []
        try:
            r = requests.get(f"{self.BASE}/zones", headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
            r.raise_for_status()
            return [_logical("cloudflare", "default", z.get("name", ""),
                             extra_tags={"status": z.get("status", "")},
                             metadata={"id": z.get("id"), "plan": z.get("plan", {}).get("name")})
                    for z in r.json().get("result", [])]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── Datadog (monitors as discovery surface) ─────
class DatadogAdapter:
    BASE = "https://api.datadoghq.com/api/v1"
    def __init__(self, api_key: str, app_key: str, site: str = "datadoghq.com"):
        self.api_key = api_key; self.app_key = app_key
        self.base = f"https://api.{site}/api/v1"
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("DD_API_KEY", ""),
                   os.environ.get("DD_APP_KEY", ""),
                   os.environ.get("DD_SITE", "datadoghq.com"))
    def discover(self, tech_filter: Optional[str] = None):
        if not (self.api_key and self.app_key): return []
        if tech_filter and tech_filter != "datadog": return []
        try:
            r = requests.get(f"{self.base}/monitor",
                             headers={"DD-API-KEY": self.api_key, "DD-APPLICATION-KEY": self.app_key},
                             timeout=15)
            r.raise_for_status()
            return [_logical("datadog", "default", m.get("name", "monitor"),
                             extra_tags={"type": m.get("type", "")},
                             metadata={"id": m.get("id")})
                    for m in r.json()]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── Sentry (projects) ─────
class SentryAdapter:
    BASE = "https://sentry.io/api/0"
    def __init__(self, token: str, org: str): self.token = token; self.org = org
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("SENTRY_AUTH_TOKEN", ""),
                   os.environ.get("SENTRY_ORG", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not (self.token and self.org): return []
        if tech_filter and tech_filter != "sentry": return []
        try:
            r = requests.get(f"{self.BASE}/organizations/{self.org}/projects/",
                             headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
            r.raise_for_status()
            return [_logical("sentry", self.org, p.get("slug", ""),
                             extra_tags={"platform": p.get("platform", "")},
                             metadata={"id": p.get("id"), "name": p.get("name")})
                    for p in r.json()]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── Snowflake (warehouses) ─────
class SnowflakeAdapter:
    """Uses snowflake-connector-python for SHOW WAREHOUSES."""
    def __init__(self, account: str, user: str, password: str):
        self.account = account; self.user = user; self.password = password
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("SNOWFLAKE_ACCOUNT", ""),
                   os.environ.get("SNOWFLAKE_USER", ""),
                   os.environ.get("SNOWFLAKE_PASSWORD", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not (self.account and self.user and self.password): return []
        if tech_filter and tech_filter != "snowflake": return []
        try:  # pragma: no cover (network)
            import snowflake.connector  # type: ignore
            conn = snowflake.connector.connect(
                account=self.account, user=self.user, password=self.password)
            cur = conn.cursor()
            cur.execute("SHOW WAREHOUSES")
            rows = cur.fetchall()
            cols = [d[0].lower() for d in cur.description]
            cur.close(); conn.close()
            return [_logical("snowflake", self.account, dict(zip(cols, r)).get("name", ""),
                             extra_tags={"size": dict(zip(cols, r)).get("size", ""),
                                         "state": dict(zip(cols, r)).get("state", "")})
                    for r in rows]
        except Exception:  # noqa
            return []


# ───── PlanetScale (databases) ─────
class PlanetScaleAdapter:
    BASE = "https://api.planetscale.com/v1"
    def __init__(self, token: str, org: str): self.token = token; self.org = org
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("PLANETSCALE_TOKEN", ""),
                   os.environ.get("PLANETSCALE_ORG", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not (self.token and self.org): return []
        if tech_filter and tech_filter != "planetscale": return []
        try:
            r = requests.get(f"{self.BASE}/organizations/{self.org}/databases",
                             headers={"Authorization": self.token}, timeout=15)
            r.raise_for_status()
            return [_logical("planetscale", self.org, db.get("name", ""),
                             extra_tags={"region": db.get("region", {}).get("slug", ""),
                                         "state": db.get("state", "")})
                    for db in r.json().get("data", [])]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── Auth0 ─────
class Auth0Adapter:
    def __init__(self, domain: str, token: str): self.domain = domain; self.token = token
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("AUTH0_DOMAIN", ""),
                   os.environ.get("AUTH0_MGMT_TOKEN", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not (self.domain and self.token): return []
        if tech_filter and tech_filter != "auth0": return []
        try:
            r = requests.get(f"https://{self.domain}/api/v2/clients",
                             headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
            r.raise_for_status()
            return [_logical("auth0", self.domain, c.get("name", ""),
                             extra_tags={"app_type": c.get("app_type", "")},
                             metadata={"client_id": c.get("client_id")})
                    for c in r.json()]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── Okta ─────
class OktaAdapter:
    def __init__(self, domain: str, token: str): self.domain = domain; self.token = token
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("OKTA_DOMAIN", ""),
                   os.environ.get("OKTA_API_TOKEN", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not (self.domain and self.token): return []
        if tech_filter and tech_filter != "okta": return []
        try:
            r = requests.get(f"https://{self.domain}/api/v1/apps",
                             headers={"Authorization": f"SSWS {self.token}"}, timeout=15)
            r.raise_for_status()
            return [_logical("okta", self.domain, a.get("label", a.get("name", "")),
                             extra_tags={"status": a.get("status", "")},
                             metadata={"id": a.get("id")})
                    for a in r.json()]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── Netlify ─────
class NetlifyAdapter:
    BASE = "https://api.netlify.com/api/v1"
    def __init__(self, token: str): self.token = token
    @classmethod
    def from_env(cls): return cls(os.environ.get("NETLIFY_TOKEN", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not self.token: return []
        if tech_filter and tech_filter != "netlify": return []
        try:
            r = requests.get(f"{self.BASE}/sites",
                             headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
            r.raise_for_status()
            return [_logical("netlify", "default", s.get("name", ""),
                             extra_tags={"state": s.get("state", "")},
                             metadata={"url": s.get("url")})
                    for s in r.json()]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── PagerDuty (services) ─────
class PagerDutyAdapter:
    BASE = "https://api.pagerduty.com"
    def __init__(self, token: str): self.token = token
    @classmethod
    def from_env(cls): return cls(os.environ.get("PAGERDUTY_TOKEN", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not self.token: return []
        if tech_filter and tech_filter != "pagerduty": return []
        try:
            r = requests.get(f"{self.BASE}/services",
                             headers={"Authorization": f"Token token={self.token}",
                                      "Accept": "application/vnd.pagerduty+json;version=2"},
                             timeout=15)
            r.raise_for_status()
            return [_logical("pagerduty", "default", s.get("name", ""),
                             extra_tags={"status": s.get("status", "")},
                             metadata={"id": s.get("id")})
                    for s in r.json().get("services", [])]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── OpsGenie (teams) ─────
class OpsGenieAdapter:
    BASE = "https://api.opsgenie.com/v2"
    def __init__(self, token: str): self.token = token
    @classmethod
    def from_env(cls): return cls(os.environ.get("OPSGENIE_API_KEY", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not self.token: return []
        if tech_filter and tech_filter != "opsgenie": return []
        try:
            r = requests.get(f"{self.BASE}/teams",
                             headers={"Authorization": f"GenieKey {self.token}"}, timeout=15)
            r.raise_for_status()
            return [_logical("opsgenie", "default", t.get("name", ""),
                             metadata={"id": t.get("id")})
                    for t in r.json().get("data", [])]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── GitHub Actions (repos with workflows) ─────
class GitHubActionsAdapter:
    BASE = "https://api.github.com"
    def __init__(self, token: str, org: str): self.token = token; self.org = org
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("GITHUB_TOKEN", ""),
                   os.environ.get("GITHUB_ORG", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not (self.token and self.org): return []
        if tech_filter and tech_filter != "github-actions": return []
        try:
            r = requests.get(f"{self.BASE}/orgs/{self.org}/repos",
                             headers={"Authorization": f"Bearer {self.token}"},
                             params={"per_page": 100}, timeout=15)
            r.raise_for_status()
            return [_logical("github-actions", self.org, repo.get("name", ""),
                             extra_tags={"private": str(repo.get("private", False))},
                             metadata={"full_name": repo.get("full_name")})
                    for repo in r.json()
                    if repo.get("has_issues") is not None]  # rough proxy
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── GitLab CI (projects) ─────
class GitLabCIAdapter:
    def __init__(self, token: str, base_url: str = "https://gitlab.com"):
        self.token = token; self.base_url = base_url.rstrip("/")
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("GITLAB_TOKEN", ""),
                   os.environ.get("GITLAB_URL", "https://gitlab.com"))
    def discover(self, tech_filter: Optional[str] = None):
        if not self.token: return []
        if tech_filter and tech_filter != "gitlab-ci": return []
        try:
            r = requests.get(f"{self.base_url}/api/v4/projects",
                             headers={"PRIVATE-TOKEN": self.token},
                             params={"per_page": 100, "membership": True}, timeout=15)
            r.raise_for_status()
            return [_logical("gitlab-ci", "default", p.get("path_with_namespace", ""),
                             metadata={"id": p.get("id"), "default_branch": p.get("default_branch")})
                    for p in r.json()]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── NewRelic (entities) ─────
class NewRelicAdapter:
    def __init__(self, api_key: str, account_id: str):
        self.api_key = api_key; self.account_id = account_id
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("NEW_RELIC_API_KEY", ""),
                   os.environ.get("NEW_RELIC_ACCOUNT_ID", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not self.api_key: return []
        if tech_filter and tech_filter != "newrelic": return []
        try:
            r = requests.post("https://api.newrelic.com/graphql",
                              headers={"API-Key": self.api_key,
                                       "Content-Type": "application/json"},
                              json={"query": """{ actor { entitySearch(query: "domain='APM'") { results { entities { name guid type } } } } }"""},
                              timeout=15)
            r.raise_for_status()
            entities = (((((r.json().get("data") or {}).get("actor") or {})
                          .get("entitySearch") or {}).get("results") or {})
                        .get("entities") or [])
            return [_logical("newrelic", self.account_id or "default",
                             e.get("name", ""),
                             extra_tags={"type": e.get("type", "")},
                             metadata={"guid": e.get("guid")})
                    for e in entities]
        except Exception:  # noqa  # pragma: no cover
            return []


# ───── Splunk (apps) ─────
class SplunkAdapter:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/"); self.token = token
    @classmethod
    def from_env(cls):
        return cls(os.environ.get("SPLUNK_URL", ""),
                   os.environ.get("SPLUNK_TOKEN", ""))
    def discover(self, tech_filter: Optional[str] = None):
        if not (self.base_url and self.token): return []
        if tech_filter and tech_filter != "splunk": return []
        try:
            r = requests.get(f"{self.base_url}/services/apps/local",
                             headers={"Authorization": f"Bearer {self.token}"},
                             params={"output_mode": "json", "count": 100},
                             timeout=15, verify=False)  # Splunk often has self-signed
            r.raise_for_status()
            return [_logical("splunk", "default", a.get("name", ""),
                             metadata={"label": a.get("content", {}).get("label", "")})
                    for a in r.json().get("entry", [])]
        except Exception:  # noqa  # pragma: no cover
            return []
