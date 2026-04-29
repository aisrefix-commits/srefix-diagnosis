"""FastMCP server for Elasticsearch / OpenSearch.

Tools:
  search(index, query, size?, sort?, time_field?, start?, end?, max_hits?)
  search_dsl(index, body, max_hits?)
  count(index, query, time_field?, start?, end?)
  list_indices(pattern?)
  get_mapping(index)
  field_caps(index, fields?)
  cluster_health(level?)
  nodes_info()

Env:
  ES_URL          (required, e.g. https://es.prod:9200)
  ES_API_KEY      (preferred — pass `id:secret` base64 ApiKey)
  ES_USERNAME / ES_PASSWORD  (basic auth alternative)
  ES_TIMEOUT      (seconds, default 30)
  ES_VERIFY_TLS   ("0" to skip)
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .client import ESClient


def _build_client() -> ESClient:
    base_url = os.environ.get("ES_URL")
    if not base_url:
        raise RuntimeError("ES_URL is required")
    return ESClient(
        base_url=base_url,
        api_key=os.environ.get("ES_API_KEY"),
        username=os.environ.get("ES_USERNAME"),
        password=os.environ.get("ES_PASSWORD"),
        timeout=int(os.environ.get("ES_TIMEOUT", "30")),
        verify_tls=os.environ.get("ES_VERIFY_TLS", "1") != "0",
    )


def make_server() -> FastMCP:
    client = _build_client()
    mcp = FastMCP("srefix-es")

    @mcp.tool()
    def search(index: str, query: str = "*", size: int = 20,
               sort: str = "", time_field: str = "@timestamp",
               start: str = "", end: str = "", max_hits: int = 50) -> dict:
        """Search via Lucene query string + optional time range.

        Examples:
          query='level:ERROR AND service:postgres'
          query='message:"too many connections"'
        sort: 'field:asc' or 'field:desc' (default: time_field desc).
        """
        return client.search_querystring(
            index=index, query=query, size=size, sort=sort or None,
            time_field=time_field, start=start or None, end=end or None,
            max_hits=max_hits,
        )

    @mcp.tool()
    def search_dsl(index: str, body: dict, max_hits: int = 50) -> dict:
        """Full Elasticsearch DSL — for aggregations, nested queries, etc."""
        return client.search_dsl(index=index, body=body, max_hits=max_hits)

    @mcp.tool()
    def count(index: str, query: str = "*",
              time_field: str = "@timestamp",
              start: str = "", end: str = "") -> dict:
        """Match count without fetching documents (cheap)."""
        return client.count(index=index, query=query, time_field=time_field,
                            start=start or None, end=end or None)

    @mcp.tool()
    def list_indices(pattern: str = "") -> dict:
        """List indices, optionally matching a pattern (e.g. 'logs-*')."""
        return client.list_indices(pattern or None)

    @mcp.tool()
    def get_mapping(index: str) -> dict:
        """Return field mappings (schema) for an index."""
        return client.get_mapping(index)

    @mcp.tool()
    def field_caps(index: str, fields: str = "*") -> dict:
        """Field type capabilities — discoverable schema for queries."""
        return client.field_caps(index=index, fields=fields)

    @mcp.tool()
    def cluster_health(level: str = "") -> dict:
        """Cluster health (level: '' / 'cluster' / 'indices' / 'shards')."""
        return client.cluster_health(level or None)

    @mcp.tool()
    def nodes_info() -> dict:
        """List nodes with version + roles (master / data / ingest / coordinator)."""
        return client.nodes_info()

    return mcp


def run() -> None:
    make_server().run()
