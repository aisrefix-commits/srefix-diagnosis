"""Loki HTTP client.

Endpoints (Loki v1):
  GET /loki/api/v1/query              instant
  GET /loki/api/v1/query_range        range (most-used for log diagnosis)
  GET /loki/api/v1/labels             label names
  GET /loki/api/v1/label/<name>/values
  GET /loki/api/v1/series             series
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional, Union

import requests

_DURATION_RE = re.compile(r"^-?(\d+)([smhdwy])$")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}


def normalize_time_ns(t: Union[str, int, float, None]) -> Optional[str]:
    """Loki accepts unix nanoseconds OR RFC3339. Normalize relative durations + 'now'."""
    if t is None:
        return None
    if isinstance(t, (int, float)):
        n = int(t)
        # Heuristic: if looks like seconds, convert to ns
        if n < 1_000_000_000_000:
            n *= 1_000_000_000
        return str(n)
    s = str(t).strip()
    if not s:
        return None
    if s == "now":
        return str(int(time.time() * 1_000_000_000))
    m = _DURATION_RE.match(s)
    if m:
        secs = int(m.group(1)) * _DURATION_UNITS[m.group(2)]
        return str(int((time.time() - secs) * 1_000_000_000))
    return s


def summarize_streams(streams: list[dict], max_streams: int = 30,
                      max_lines: int = 200) -> dict:
    """Loki streams result → flat list capped for LLM context."""
    streams_total = len(streams)
    if streams_total > max_streams:
        streams = streams[:max_streams]
    out: list[dict] = []
    for s in streams:
        values = s.get("values") or []
        kept = values[-max_lines:] if len(values) > max_lines else values
        out.append({
            "labels": s.get("stream", {}),
            "lines": [{"ts": v[0], "line": v[1]} for v in kept],
            "lines_total": len(values),
            "truncated": len(values) > max_lines,
        })
    return {
        "streams": out,
        "streams_total": streams_total,
        "streams_truncated": streams_total > max_streams,
    }


class LokiClient:
    def __init__(self, base_url: str, token: Optional[str] = None,
                 username: Optional[str] = None, password: Optional[str] = None,
                 org_id: Optional[str] = None, timeout: int = 30,
                 verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = verify_tls
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif username and password:
            self.session.auth = (username, password)
        if org_id:
            self.session.headers["X-Scope-OrgID"] = org_id
        self.timeout = timeout

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") not in ("success", None):
            return {"error": True, "error_message": body.get("error", body)}
        return body

    def query_range(self, query: str, start: Union[str, int, float] = "-1h",
                    end: Union[str, int, float] = "now",
                    limit: int = 1000, step: Optional[str] = None,
                    direction: str = "backward",
                    max_streams: int = 30, max_lines: int = 200) -> dict:
        params: dict[str, Any] = {
            "query": query,
            "start": normalize_time_ns(start),
            "end": normalize_time_ns(end),
            "limit": limit,
            "direction": direction,
        }
        if step:
            params["step"] = step
        body = self._get("/loki/api/v1/query_range", params)
        if body.get("error"):
            return body
        data = body.get("data", {})
        result_type = data.get("resultType")
        result = data.get("result", [])
        if result_type == "streams":
            return {"query": query, "result_type": "streams",
                    **summarize_streams(result, max_streams, max_lines)}
        # matrix: same shape as Prometheus matrix (LogQL metric query)
        return {"query": query, "result_type": result_type, "result": result}

    def instant(self, query: str, time_: Union[str, int, float, None] = None,
                limit: int = 100, direction: str = "backward") -> dict:
        params: dict[str, Any] = {"query": query, "limit": limit, "direction": direction}
        nt = normalize_time_ns(time_)
        if nt:
            params["time"] = nt
        body = self._get("/loki/api/v1/query", params)
        if body.get("error"):
            return body
        return {"query": query, "data": body.get("data", {})}

    def labels(self, start: Optional[str] = None, end: Optional[str] = None) -> dict:
        params: dict[str, Any] = {}
        ns = normalize_time_ns(start)
        ne = normalize_time_ns(end)
        if ns:
            params["start"] = ns
        if ne:
            params["end"] = ne
        body = self._get("/loki/api/v1/labels", params)
        return {"labels": body.get("data", [])}

    def label_values(self, label: str, start: Optional[str] = None,
                     end: Optional[str] = None) -> dict:
        params: dict[str, Any] = {}
        ns = normalize_time_ns(start)
        ne = normalize_time_ns(end)
        if ns:
            params["start"] = ns
        if ne:
            params["end"] = ne
        body = self._get(f"/loki/api/v1/label/{label}/values", params)
        return {"label": label, "values": body.get("data", [])}

    def series(self, match: list[str], start: Optional[str] = None,
               end: Optional[str] = None) -> dict:
        params: dict[str, Any] = {"match[]": match}
        ns = normalize_time_ns(start)
        ne = normalize_time_ns(end)
        if ns:
            params["start"] = ns
        if ne:
            params["end"] = ne
        body = self._get("/loki/api/v1/series", params)
        return {"match": match, "series": body.get("data", [])}
