"""FastMCP server for Loki.

Tools:
  query_range(query, start?, end?, limit?, direction?, max_streams?, max_lines?)
  instant(query, time?, limit?, direction?)
  labels(start?, end?)
  label_values(label, start?, end?)
  series(match[], start?, end?)

Env:
  LOKI_URL         (required, e.g. http://loki.prod:3100)
  LOKI_TOKEN       (optional bearer)
  LOKI_USERNAME / LOKI_PASSWORD  (optional basic)
  LOKI_ORG_ID      (optional, for multi-tenant Loki — sets X-Scope-OrgID)
  LOKI_TIMEOUT     (seconds, default 30)
  LOKI_VERIFY_TLS  ("0" to skip)
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .client import LokiClient


def _build_client() -> LokiClient:
    base_url = os.environ.get("LOKI_URL")
    if not base_url:
        raise RuntimeError("LOKI_URL is required")
    return LokiClient(
        base_url=base_url,
        token=os.environ.get("LOKI_TOKEN"),
        username=os.environ.get("LOKI_USERNAME"),
        password=os.environ.get("LOKI_PASSWORD"),
        org_id=os.environ.get("LOKI_ORG_ID"),
        timeout=int(os.environ.get("LOKI_TIMEOUT", "30")),
        verify_tls=os.environ.get("LOKI_VERIFY_TLS", "1") != "0",
    )


def make_server() -> FastMCP:
    client = _build_client()
    mcp = FastMCP("srefix-loki")

    @mcp.tool()
    def query_range(query: str, start: str = "-1h", end: str = "now",
                    limit: int = 1000, direction: str = "backward",
                    max_streams: int = 30, max_lines: int = 200) -> dict:
        """Run a LogQL range query (the typical log-pull entry point).

        Example: '{app="postgres",env="prod"} |= "ERROR" | logfmt'
        - direction: 'backward' (newest first) or 'forward'
        - limit: max log lines (server-side cap)
        - max_streams / max_lines: cap result for LLM context
        """
        return client.query_range(query, start, end, limit, direction=direction,
                                  max_streams=max_streams, max_lines=max_lines)

    @mcp.tool()
    def instant(query: str, time: str = "", limit: int = 100,
                direction: str = "backward") -> dict:
        """Instant LogQL query (snapshot at a point in time)."""
        return client.instant(query, time or None, limit=limit, direction=direction)

    @mcp.tool()
    def labels(start: str = "", end: str = "") -> dict:
        """List label names (within optional time window)."""
        return client.labels(start or None, end or None)

    @mcp.tool()
    def label_values(label: str, start: str = "", end: str = "") -> dict:
        """List values of a label (e.g. label='app')."""
        return client.label_values(label, start or None, end or None)

    @mcp.tool()
    def series(match: list[str], start: str = "", end: str = "") -> dict:
        """List streams matching label selectors (e.g. ['{app=\"postgres\"}'])."""
        return client.series(match, start or None, end or None)

    return mcp


def run() -> None:
    make_server().run()
