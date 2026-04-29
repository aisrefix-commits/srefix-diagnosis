"""Mock telemetry MCP — single FastMCP server impersonating prom/loki/jumphost.

Loads scenarios.json, picks one (DEMO_SCENARIO_ID env), and returns its
canned_telemetry block when Claude calls the matching tool.

If a query has no canned response, returns a structured "no data — try
adjusting query" message so Claude can adapt.

Tools exposed:
  prom_instant(query)
  prom_range_query(query, start, end, step)
  prom_alerts()
  loki_query_range(query, start, end, limit)
  jumphost_run_safe(host, tech, preset_name)
  list_scenarios()                – inspect available demo scenarios
  set_scenario(scenario_id)       – switch active scenario at runtime
  current_scenario()              – inspect what's active
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

DEMO_DIR = Path(__file__).resolve().parent.parent.parent
SCENARIOS_PATH = Path(os.environ.get(
    "DEMO_SCENARIOS_PATH", str(DEMO_DIR / "scenarios.json"),
))


def _load_scenarios() -> list[dict]:
    if not SCENARIOS_PATH.exists():
        return []
    return json.loads(SCENARIOS_PATH.read_text())


# Active scenario state (in-process; switchable via tool)
_state = {"scenario_id": os.environ.get("DEMO_SCENARIO_ID", "")}


def _active_scenario() -> Optional[dict]:
    scenarios = _load_scenarios()
    if not scenarios:
        return None
    if _state["scenario_id"]:
        for s in scenarios:
            if s["id"] == _state["scenario_id"]:
                return s
    return scenarios[0]


def _match_canned(canned: dict[str, Any], query: str) -> Optional[Any]:
    """Look up canned response by query.

    Tries exact match first, then substring matches against any key whose
    'core' fragment appears in the query (e.g. metric name).
    """
    if not canned:
        return None
    if query in canned:
        return canned[query]
    # Substring matching: pick the canned key whose core token is in query
    for key, val in canned.items():
        # Strip out any [{}=,~'"] noise to find the metric name
        m = re.match(r"[a-zA-Z_:][a-zA-Z0-9_:]*", key.lstrip())
        core = m.group(0) if m else key
        if core and core in query:
            return val
    return None


def make_server() -> FastMCP:
    mcp = FastMCP("srefix-mock-telemetry")

    @mcp.tool()
    def list_scenarios() -> list[dict]:
        """List the available demo scenarios."""
        return [
            {"id": s["id"], "name": s["name"], "difficulty": s["difficulty"],
             "tags": s["tags"]}
            for s in _load_scenarios()
        ]

    @mcp.tool()
    def set_scenario(scenario_id: str) -> dict:
        """Switch the active demo scenario."""
        scenarios = _load_scenarios()
        for s in scenarios:
            if s["id"] == scenario_id:
                _state["scenario_id"] = scenario_id
                return {"active": scenario_id, "name": s["name"]}
        return {"error": f"unknown scenario: {scenario_id}",
                "available": [s["id"] for s in scenarios]}

    @mcp.tool()
    def current_scenario() -> dict:
        """Show the active demo scenario."""
        s = _active_scenario()
        if s is None:
            return {"error": "no scenarios loaded"}
        return {
            "id": s["id"], "name": s["name"], "difficulty": s["difficulty"],
            "tags": s["tags"],
            "input_alerts": s.get("input_alerts", []),
            "actual_timeline": s.get("actual_timeline", []),
        }

    @mcp.tool()
    def prom_instant(query: str) -> dict:
        """Mock instant PromQL query — returns canned data for active scenario."""
        s = _active_scenario()
        if s is None:
            return {"error": "no active scenario"}
        canned = (s.get("canned_telemetry") or {}).get("prom") or {}
        match = _match_canned(canned, query)
        if match:
            return {"query": query, "scenario": s["id"], **match}
        return {"query": query, "scenario": s["id"], "type": "vector",
                "samples": [], "note": "no data for this query in scenario"}

    @mcp.tool()
    def prom_range_query(query: str, start: str = "-30m", end: str = "now",
                         step: str = "30s") -> dict:
        """Mock PromQL range query — returns canned time series for the scenario."""
        s = _active_scenario()
        if s is None:
            return {"error": "no active scenario"}
        canned = (s.get("canned_telemetry") or {}).get("prom") or {}
        match = _match_canned(canned, query)
        if match:
            return {"query": query, "scenario": s["id"],
                    "start": start, "end": end, "step": step, **match}
        return {"query": query, "scenario": s["id"], "type": "matrix",
                "series": [], "note": "no data for this query in scenario"}

    @mcp.tool()
    def prom_alerts() -> dict:
        """Currently-firing alerts for the active demo scenario."""
        s = _active_scenario()
        if s is None:
            return {"alerts": []}
        return {
            "alerts": [
                {"labels": {**a.get("labels", {}),
                            "alertname": a.get("title", "Alert"),
                            "severity": a.get("severity", "")},
                 "state": "firing",
                 "value": str(a.get("metric_value", "")),
                 "annotations": {"description": a.get("description", "")}}
                for a in s.get("input_alerts", [])
            ],
            "scenario": s["id"],
        }

    @mcp.tool()
    def loki_query_range(query: str, start: str = "-30m", end: str = "now",
                         limit: int = 200) -> dict:
        """Mock LogQL query — returns canned log streams."""
        s = _active_scenario()
        if s is None:
            return {"error": "no active scenario"}
        canned = (s.get("canned_telemetry") or {}).get("loki") or {}
        match = _match_canned(canned, query)
        if match:
            return {"query": query, "scenario": s["id"],
                    "result_type": "streams", **match}
        return {"query": query, "scenario": s["id"],
                "result_type": "streams", "streams": [],
                "note": "no log data for this query in scenario"}

    @mcp.tool()
    def jumphost_run_safe(host: str, tech: str, preset_name: str) -> dict:
        """Mock SSH preset execution — returns canned output for active scenario."""
        s = _active_scenario()
        if s is None:
            return {"error": "no active scenario"}
        canned = (s.get("canned_telemetry") or {}).get("jumphost") or {}
        if preset_name in canned:
            return {"host": host, "tech": tech, "preset": preset_name,
                    "scenario": s["id"], **canned[preset_name]}
        return {"host": host, "tech": tech, "preset": preset_name,
                "scenario": s["id"], "exit_code": 0,
                "stdout": "(no canned output for this preset in active scenario)",
                "stderr": "", "duration_ms": 50}

    @mcp.tool()
    def discovery_list_hosts(tech: str = "", role: str = "") -> list[dict]:
        """Mock cluster topology — returns hosts derived from scenario alerts."""
        s = _active_scenario()
        if s is None:
            return []
        # Synthesize hosts from alert labels (cluster, instance, host, etc.)
        hosts: list[dict] = []
        for alert in s.get("input_alerts", []):
            labels = alert.get("labels", {}) or {}
            instance = labels.get("instance") or labels.get("cluster") or alert.get("service", "host-unknown")
            cluster_name = labels.get("cluster") or labels.get("rs") or alert.get("service")
            host = {
                "host": instance, "tags": {**labels, "tech": _infer_tech_from_alert(alert)},
                "cluster_id": cluster_name,
            }
            if tech and host["tags"]["tech"] != tech:
                continue
            if role and labels.get("role") != role:
                continue
            hosts.append(host)
        # Add a few logical sibling hosts so topology looks realistic
        if hosts:
            base = hosts[0]
            for i in range(2, 4):
                hosts.append({
                    "host": f"{base['host'].rstrip('-1234567890')}-{i}",
                    "tags": base["tags"], "cluster_id": base["cluster_id"],
                })
        return hosts

    return mcp


_TECH_HINTS = {
    "mongodb": "mongo", "mongo": "mongo",
    "etcd": "etcd", "k8s": "kubernetes", "kubernetes": "kubernetes",
    "nginx": "nginx", "haproxy": "haproxy",
    "cassandra": "cassandra", "scylla": "cassandra",
    "redis": "redis", "kafka": "kafka",
    "postgres": "postgres", "mysql": "mysql",
    "coredns": "coredns", "dns": "coredns",
    "loki": "loki", "prometheus": "prometheus",
    "consul": "consul", "vault": "vault", "zookeeper": "zookeeper",
    "spark": "spark", "clickhouse": "clickhouse",
    "datadog": "datadog", "gitlab": "gitlab-ci", "nats": "nats",
    "istio": "istio", "tls": "cert-manager",
}


def _infer_tech_from_alert(alert: dict) -> str:
    """Best-effort tech inference from alert title/service/labels."""
    blob = " ".join([
        alert.get("title", ""),
        alert.get("service", ""),
        alert.get("description", ""),
        " ".join(str(v) for v in (alert.get("labels", {}) or {}).values()),
    ]).lower()
    for hint, tech in _TECH_HINTS.items():
        if hint in blob:
            return tech
    return "unknown"


def run() -> None:
    make_server().run()
