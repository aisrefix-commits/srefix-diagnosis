"""Elasticsearch / OpenSearch HTTP client (raw REST, version-agnostic).

Endpoints used:
  POST /<index>/_search                 query string + DSL
  GET  /_cat/indices?format=json        index list
  GET  /<index>/_mapping                schema
  GET  /_cluster/health                 cluster status
  GET  /_nodes                          node listing
  GET  /<index>/_count                  match count
  GET  /<index>/_field_caps?fields=*    field listing per-index
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional, Union

import requests

_DURATION_RE = re.compile(r"^-?(\d+)([smhdwy])$")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}


def normalize_time(t: Union[str, int, float, None]) -> Optional[str]:
    """ES accepts ISO 8601, unix ms, or its own date-math (`now-1h`).

    For consistency with the rest of the stack, accept '-1h'/'now' and pass
    everything through (ES understands `now-1h` natively if user uses that)."""
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return str(int(t * 1000)) if t < 1_000_000_000_000 else str(int(t))  # to ms
    s = str(t).strip()
    if not s:
        return None
    if s == "now":
        return "now"
    m = _DURATION_RE.match(s)
    if m:
        return f"now-{m.group(1)}{m.group(2)}"
    return s


def summarize_hits(hits: dict, max_hits: int = 50) -> dict:
    """Reduce ES hits payload for LLM context."""
    total_obj = hits.get("total")
    if isinstance(total_obj, dict):
        total = total_obj.get("value")
    else:
        total = total_obj
    raw = hits.get("hits") or []
    out_hits = [
        {
            "_index": h.get("_index"),
            "_score": h.get("_score"),
            "_id": h.get("_id"),
            "_source": h.get("_source"),
        }
        for h in raw[:max_hits]
    ]
    return {
        "total": total,
        "hits": out_hits,
        "returned": len(out_hits),
        "truncated": len(raw) > max_hits,
    }


class ESClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None,
                 username: Optional[str] = None, password: Optional[str] = None,
                 timeout: int = 30, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = verify_tls
        if api_key:
            self.session.headers["Authorization"] = f"ApiKey {api_key}"
        elif username and password:
            self.session.auth = (username, password)
        self.session.headers["Content-Type"] = "application/json"
        self.timeout = timeout

    def _request(self, method: str, path: str, params: Optional[dict] = None,
                 json_body: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, params=params, json=json_body, timeout=self.timeout)
        if resp.status_code >= 400:
            return {"error": True, "status": resp.status_code, "body": resp.text[:2000]}
        return resp.json()

    def search_querystring(self, index: str, query: str = "*",
                           size: int = 20, sort: Optional[str] = None,
                           time_field: str = "@timestamp",
                           start: Union[str, int, float, None] = None,
                           end: Union[str, int, float, None] = None,
                           max_hits: int = 50) -> dict:
        """Search using Lucene query-string syntax + optional time range filter.

        Examples of `query`:
          'level:ERROR AND service:postgres'
          'message:"too many connections"'
          '_exists_:exception_class'
        """
        body: dict[str, Any] = {
            "query": {
                "bool": {
                    "must": [{"query_string": {"query": query}}],
                }
            },
            "size": size,
        }
        ns = normalize_time(start)
        ne = normalize_time(end)
        if ns or ne:
            time_filter: dict[str, Any] = {}
            if ns:
                time_filter["gte"] = ns
            if ne:
                time_filter["lte"] = ne
            body["query"]["bool"]["filter"] = [{"range": {time_field: time_filter}}]
        if sort:
            field, _, order = sort.partition(":")
            body["sort"] = [{field: {"order": order or "desc"}}]
        else:
            # Default newest first if time_field exists
            body["sort"] = [{time_field: {"order": "desc", "missing": "_last"}}]

        resp = self._request("POST", f"/{index}/_search", json_body=body)
        if resp.get("error"):
            return resp
        return {"index": index, "query": query, **summarize_hits(resp.get("hits", {}), max_hits)}

    def search_dsl(self, index: str, body: dict, max_hits: int = 50) -> dict:
        """Full-DSL search for advanced cases (aggs, nested queries, etc.)."""
        resp = self._request("POST", f"/{index}/_search", json_body=body)
        if resp.get("error"):
            return resp
        result = {"index": index, **summarize_hits(resp.get("hits", {}), max_hits)}
        if "aggregations" in resp:
            result["aggregations"] = resp["aggregations"]
        return result

    def list_indices(self, pattern: Optional[str] = None) -> dict:
        path = "/_cat/indices"
        if pattern:
            path += f"/{pattern}"
        resp = self._request("GET", path, params={"format": "json"})
        if isinstance(resp, dict) and resp.get("error"):
            return resp
        return {"indices": resp}

    def get_mapping(self, index: str) -> dict:
        resp = self._request("GET", f"/{index}/_mapping")
        if resp.get("error"):
            return resp
        return resp

    def cluster_health(self, level: Optional[str] = None) -> dict:
        params = {"level": level} if level else None
        return self._request("GET", "/_cluster/health", params=params)

    def nodes_info(self) -> dict:
        resp = self._request("GET", "/_nodes")
        if resp.get("error"):
            return resp
        nodes = resp.get("nodes", {})
        return {
            "cluster_name": resp.get("cluster_name"),
            "node_count": len(nodes),
            "nodes": [
                {
                    "name": n.get("name"),
                    "host": n.get("host"),
                    "ip": n.get("ip"),
                    "version": n.get("version"),
                    "roles": n.get("roles", []),
                }
                for n in nodes.values()
            ],
        }

    def count(self, index: str, query: str = "*",
              time_field: str = "@timestamp",
              start: Union[str, int, float, None] = None,
              end: Union[str, int, float, None] = None) -> dict:
        body: dict[str, Any] = {"query": {"bool": {"must": [{"query_string": {"query": query}}]}}}
        ns = normalize_time(start)
        ne = normalize_time(end)
        if ns or ne:
            tf: dict[str, Any] = {}
            if ns:
                tf["gte"] = ns
            if ne:
                tf["lte"] = ne
            body["query"]["bool"]["filter"] = [{"range": {time_field: tf}}]
        return self._request("POST", f"/{index}/_count", json_body=body)

    def field_caps(self, index: str, fields: str = "*") -> dict:
        return self._request("GET", f"/{index}/_field_caps", params={"fields": fields})
