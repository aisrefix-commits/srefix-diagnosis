"""Prometheus HTTP client — pure logic, no MCP deps.

Wraps the Prometheus HTTP API:
  /api/v1/query              instant
  /api/v1/query_range        range
  /api/v1/labels             label names
  /api/v1/label/{name}/values
  /api/v1/series             series matching selectors
  /api/v1/alerts             active alerts
  /api/v1/targets            scrape targets
  /api/v1/metadata           metric HELP/TYPE
  /api/v1/rules              alerting + recording rules

Time strings are normalized:
  - "now"           → current unix ts
  - "-30m" / "-1h"  → now minus duration
  - ISO 8601        → pass through
  - unix ts (int/float) → pass through
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional, Union

import requests

_DURATION_RE = re.compile(r"^-?(\d+)([smhdwy])$")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}


def normalize_time(t: Union[str, int, float, None]) -> Optional[str]:
    """Accept relative durations, 'now', ISO, or unix → string for Prometheus API.
    Returns None if input is None or empty.
    """
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return str(t)
    s = str(t).strip()
    if not s:
        return None
    if s == "now":
        return str(time.time())
    m = _DURATION_RE.match(s)
    if m:
        n = int(m.group(1))
        secs = n * _DURATION_UNITS[m.group(2)]
        # Negative or "-N" both mean "N ago"
        return str(time.time() - secs)
    return s


def normalize_step(step: Union[str, int, float, None], default: str = "30s") -> str:
    if step is None:
        return default
    if isinstance(step, (int, float)):
        return f"{int(step)}s"
    s = str(step).strip()
    return s or default


def summarize_matrix(result: list[dict], max_series: int = 50, max_points: int = 200) -> dict:
    """Reduce a Prometheus matrix result for LLM consumption: cap series + downsample points."""
    series_total = len(result)
    if series_total > max_series:
        result = result[:max_series]
    out_series: list[dict] = []
    for r in result:
        values = r.get("values") or []
        if len(values) > max_points:
            stride = max(1, len(values) // max_points)
            sampled = values[::stride][:max_points]
        else:
            sampled = values
        out_series.append({
            "labels": r.get("metric", {}),
            "points": sampled,  # [[ts, "value"], ...]
            "point_count": len(values),
            "first_ts": values[0][0] if values else None,
            "last_ts": values[-1][0] if values else None,
            "last_value": values[-1][1] if values else None,
        })
    return {
        "series": out_series,
        "series_total": series_total,
        "series_truncated": series_total > max_series,
    }


def summarize_vector(result: list[dict]) -> dict:
    """Instant vector → flat list of {labels, value, ts}."""
    return {
        "samples": [
            {
                "labels": r.get("metric", {}),
                "value": (r.get("value") or [None, None])[1],
                "ts": (r.get("value") or [None, None])[0],
            }
            for r in result
        ],
        "sample_count": len(result),
    }


class PrometheusClient:
    def __init__(self, base_url: str, token: Optional[str] = None,
                 username: Optional[str] = None, password: Optional[str] = None,
                 timeout: int = 30, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = verify_tls
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif username and password:
            self.session.auth = (username, password)
        self.timeout = timeout

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            return {
                "error": True,
                "errorType": body.get("errorType"),
                "error_message": body.get("error"),
            }
        return body

    def instant(self, query: str, time_: Union[str, int, float, None] = None) -> dict:
        params: dict[str, Any] = {"query": query}
        nt = normalize_time(time_)
        if nt is not None:
            params["time"] = nt
        body = self._get("/api/v1/query", params)
        if body.get("error"):
            return body
        data = body.get("data", {})
        if data.get("resultType") == "vector":
            return {"query": query, **summarize_vector(data.get("result", []))}
        if data.get("resultType") == "scalar":
            return {"query": query, "scalar": data.get("result")}
        if data.get("resultType") == "string":
            return {"query": query, "string": data.get("result")}
        return {"query": query, "raw": data}

    def range(self, query: str, start: Union[str, int, float], end: Union[str, int, float],
              step: Union[str, int, float, None] = None,
              max_series: int = 50, max_points: int = 200) -> dict:
        params = {
            "query": query,
            "start": normalize_time(start) or normalize_time("-1h"),
            "end": normalize_time(end) or normalize_time("now"),
            "step": normalize_step(step, default="30s"),
        }
        body = self._get("/api/v1/query_range", params)
        if body.get("error"):
            return body
        data = body.get("data", {})
        return {
            "query": query,
            **summarize_matrix(data.get("result", []), max_series=max_series, max_points=max_points),
        }

    def labels(self, match: Optional[list[str]] = None,
               start: Union[str, int, float, None] = None,
               end: Union[str, int, float, None] = None) -> dict:
        params: dict[str, Any] = {}
        if match:
            params["match[]"] = match
        ns = normalize_time(start)
        ne = normalize_time(end)
        if ns:
            params["start"] = ns
        if ne:
            params["end"] = ne
        body = self._get("/api/v1/labels", params)
        if body.get("error"):
            return body
        return {"labels": body.get("data", [])}

    def label_values(self, label: str,
                     match: Optional[list[str]] = None) -> dict:
        params: dict[str, Any] = {}
        if match:
            params["match[]"] = match
        body = self._get(f"/api/v1/label/{label}/values", params)
        if body.get("error"):
            return body
        return {"label": label, "values": body.get("data", [])}

    def series(self, match: list[str],
               start: Union[str, int, float, None] = None,
               end: Union[str, int, float, None] = None,
               limit: int = 200) -> dict:
        params: dict[str, Any] = {"match[]": match}
        ns = normalize_time(start)
        ne = normalize_time(end)
        if ns:
            params["start"] = ns
        if ne:
            params["end"] = ne
        body = self._get("/api/v1/series", params)
        if body.get("error"):
            return body
        data = body.get("data", [])
        truncated = len(data) > limit
        return {
            "match": match,
            "series": data[:limit],
            "series_count": len(data),
            "truncated": truncated,
        }

    def alerts(self) -> dict:
        body = self._get("/api/v1/alerts")
        if body.get("error"):
            return body
        return {"alerts": body.get("data", {}).get("alerts", [])}

    def targets(self, state: Optional[str] = None) -> dict:
        params = {"state": state} if state else None
        body = self._get("/api/v1/targets", params)
        if body.get("error"):
            return body
        data = body.get("data", {})
        active = data.get("activeTargets", [])
        return {
            "active_count": len(active),
            "active": [
                {
                    "labels": t.get("labels", {}),
                    "scrapeUrl": t.get("scrapeUrl"),
                    "health": t.get("health"),
                    "lastError": t.get("lastError"),
                    "lastScrape": t.get("lastScrape"),
                }
                for t in active
            ],
            "dropped_count": len(data.get("droppedTargets", [])),
        }

    def metadata(self, metric: Optional[str] = None) -> dict:
        params = {"metric": metric} if metric else None
        body = self._get("/api/v1/metadata", params)
        if body.get("error"):
            return body
        return {"metadata": body.get("data", {})}

    def rules(self) -> dict:
        body = self._get("/api/v1/rules")
        if body.get("error"):
            return body
        return {"groups": body.get("data", {}).get("groups", [])}
