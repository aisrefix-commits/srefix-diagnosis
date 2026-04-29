"""FastMCP server for Prometheus.

Tools:
  instant(query, time?)
  range(query, start, end, step?, max_series?, max_points?)
  labels(match?, start?, end?)
  label_values(label, match?)
  series(match[], start?, end?, limit?)
  alerts()
  targets(state?)
  metadata(metric?)
  rules()

Configuration via env:
  PROMETHEUS_URL          (required, e.g. http://prom.prod:9090)
  PROMETHEUS_TOKEN        (optional bearer)
  PROMETHEUS_USERNAME     (optional basic auth)
  PROMETHEUS_PASSWORD     (optional basic auth)
  PROMETHEUS_TIMEOUT      (seconds, default 30)
  PROMETHEUS_VERIFY_TLS   ("0" to skip TLS verification)
"""
from __future__ import annotations

import os
from typing import Optional, Union

from mcp.server.fastmcp import FastMCP

from .client import PrometheusClient


def _build_client() -> PrometheusClient:
    base_url = os.environ.get("PROMETHEUS_URL")
    if not base_url:
        raise RuntimeError(
            "PROMETHEUS_URL is required (e.g. PROMETHEUS_URL=http://prometheus.prod:9090)"
        )
    return PrometheusClient(
        base_url=base_url,
        token=os.environ.get("PROMETHEUS_TOKEN"),
        username=os.environ.get("PROMETHEUS_USERNAME"),
        password=os.environ.get("PROMETHEUS_PASSWORD"),
        timeout=int(os.environ.get("PROMETHEUS_TIMEOUT", "30")),
        verify_tls=os.environ.get("PROMETHEUS_VERIFY_TLS", "1") != "0",
    )


def make_server() -> FastMCP:
    client = _build_client()
    mcp = FastMCP("srefix-prom")

    @mcp.tool()
    def instant(query: str, time: str = "") -> dict:
        """Run a PromQL instant query.

        - query: PromQL expression (e.g. 'up{job="postgres"}')
        - time: 'now' / '-30m' / unix ts / ISO 8601 (default = current time)
        """
        return client.instant(query, time or None)

    @mcp.tool()
    def range_query(
        query: str,
        start: str = "-1h",
        end: str = "now",
        step: str = "30s",
        max_series: int = 50,
        max_points: int = 200,
    ) -> dict:
        """Run a PromQL range query.

        - start / end: 'now' / '-1h' / unix ts / ISO 8601
        - step: '15s' / '1m' / '5m' (Prometheus duration string)
        - max_series / max_points: cap result size for LLM context
        """
        return client.range(query, start, end, step, max_series, max_points)

    @mcp.tool()
    def labels(match: list[str] = None, start: str = "", end: str = "") -> dict:
        """List label NAMES, optionally filtered by series matchers."""
        return client.labels(match=match or None, start=start or None, end=end or None)

    @mcp.tool()
    def label_values(label: str, match: list[str] = None) -> dict:
        """List all VALUES of a given label, optionally filtered by series matchers."""
        return client.label_values(label, match=match or None)

    @mcp.tool()
    def series(match: list[str], start: str = "", end: str = "", limit: int = 200) -> dict:
        """List series matching label selectors. match[] is required (e.g. ['up{job=\"node\"}'])."""
        return client.series(match, start=start or None, end=end or None, limit=limit)

    @mcp.tool()
    def alerts() -> dict:
        """List currently firing/pending alerts (Prometheus's view, not Alertmanager)."""
        return client.alerts()

    @mcp.tool()
    def targets(state: str = "") -> dict:
        """List scrape targets. state: 'active' / 'dropped' / '' (both)."""
        return client.targets(state=state or None)

    @mcp.tool()
    def metadata(metric: str = "") -> dict:
        """Metric HELP / TYPE metadata. Pass a specific metric name to scope."""
        return client.metadata(metric=metric or None)

    @mcp.tool()
    def rules() -> dict:
        """All alerting + recording rule groups loaded by Prometheus."""
        return client.rules()

    return mcp


def run() -> None:
    make_server().run()
