"""FastMCP server: fallback exploration when diag-{tech} has no matching case.

Tools:
  fallback_exploration_plan(symptom, tech?, cluster_id?, host_pattern?)
      Build a structured exploration plan: pre-filled prom/loki/discovery calls
      grouped by symptom category (latency / errors / memory / replication / ...).
      Use this when diag-{tech}.diagnose_symptom() returned nothing useful.

  reflect_on_findings(findings)
      After running the plan, feed back what you saw. Returns extracted keywords
      to retry against diag-{tech}.search() and suggested follow-up MCP calls.

  list_symptom_categories()
      List the keyword→category mapping used by the categorizer.

  list_supported_techs()
      List techs with tech-specific overrides (others fall back to generic).
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .bootstrap import free_explore_bootstrap as _free_explore_bootstrap
from .categorizer import SYMPTOM_PATTERNS, categorize
from .dependencies import DEPENDENCY_GRAPH, expand, get_dependents
from .planner import build_plan
from .reflector import reflect
from .templates import GENERIC_TEMPLATES, TECH_TEMPLATES


def make_server() -> FastMCP:
    mcp = FastMCP("srefix-explorer")

    @mcp.tool()
    def fallback_exploration_plan(
        symptom: str,
        tech: str = "",
        cluster_id: str = "",
        host_pattern: str = "",
    ) -> dict:
        """Build a structured exploration plan for a symptom.

        Use this WHEN diag-{tech}.diagnose_symptom() found no matching case.

        - symptom: free-text description (e.g. 'p99 spike + restarts')
        - tech: 'postgres' / 'redis' / 'kafka' / ... — picks tech-specific
                templates if available, else generic
        - cluster_id: limits queries to one cluster (substituted into PromQL)
        - host_pattern: regex for `instance=~` filters (default '.*')
        """
        return build_plan(symptom, tech, cluster_id, host_pattern)

    @mcp.tool()
    def reflect_on_findings(findings: list[dict], top_k: int = 20) -> dict:
        """Distill plan execution results into follow-up search keywords + actions.

        Pass the raw findings you collected from running the plan. Returns:
          - extracted_keywords: candidate terms for diag-{tech}.search()
          - next_actions: ordered list of suggested follow-up MCP calls

        Finding formats accepted:
          {"type": "log_lines",      "lines": [...]}
          {"type": "metric_anomaly", "metric": "...", "labels": {...}, "value": ...}
          {"type": "alert",          "alertname": "...", "labels": {...}}
          {"type": "raw",            "text": "..."}
        """
        return reflect(findings, top_k=top_k)

    @mcp.tool()
    def list_symptom_categories() -> dict:
        """Show the regex patterns each category matches against (for debugging)."""
        return SYMPTOM_PATTERNS

    @mcp.tool()
    def list_supported_techs() -> dict:
        """Techs with tech-specific overrides; others use generic templates."""
        return {
            "tech_specific": sorted(TECH_TEMPLATES.keys()),
            "generic_categories": sorted(GENERIC_TEMPLATES.keys()),
        }

    @mcp.tool()
    def categorize_symptom(symptom: str) -> dict:
        """Show which categories a symptom maps to (no plan, just classification)."""
        return {"symptom": symptom, "categories": categorize(symptom)}

    @mcp.tool()
    def free_explore_bootstrap(
        symptom: str,
        tech: str = "",
        cluster_id: str = "",
        host_pattern: str = "",
        max_queries: int = 20,
        max_minutes: int = 10,
    ) -> dict:
        """Tier-3 free-exploration bootstrap pack.

        Use this when BOTH diag-{tech}.diagnose_symptom() AND
        fallback_exploration_plan() failed to find root cause.

        Returns 4 starter probes that establish local schema awareness
        (metric catalog, log stream catalog, baseline comparison, dependency chain)
        plus termination rules so the LLM doesn't loop forever.

        Strategy: don't hallucinate PromQL — first enumerate what exists,
        then drill into anomalies, then cross-reference with logs.
        """
        return _free_explore_bootstrap(symptom, tech, cluster_id, host_pattern,
                                       max_queries, max_minutes)

    @mcp.tool()
    def expand_to_dependencies(tech: str, depth: int = 1, observation: str = "") -> dict:
        """Fan out to upstream dependencies of `tech` for cross-tech root-cause hunt.

        Use when a symptom in `tech` looks like it might originate UPSTREAM
        (postgres timeout → check DNS / k8s / RDS; hbase down → check ZK / HDFS).

        - tech: the failing tech (must match a diag-{tech} we have)
        - depth: 1 (direct deps) or 2 (deps-of-deps). Capped at 3.
        - observation: forwarded into each suggested diag-{dep}.diagnose_symptom() call

        Returns dependencies with severity + relationship + a pre-filled
        suggested_call to that tech's diag MCP.
        """
        deps = expand(tech, depth=max(1, min(depth, 3)), observation=observation)
        # Group by depth for readable output
        by_depth: dict[int, list[dict]] = {}
        for d in deps:
            by_depth.setdefault(d["depth"], []).append(d)
        return {
            "tech": tech,
            "depth_requested": depth,
            "total_dependencies": len(deps),
            "by_depth": {f"depth_{k}": sorted(v, key=lambda x: ("critical", "high", "medium", "low").index(x["severity"]))
                         for k, v in sorted(by_depth.items())},
            "rationale": (
                "If symptom in '{tech}' started at the same time as anomalies in any "
                "of these deps, the root cause is upstream. Walk in severity order "
                "(critical → high → medium → low). For each dep, also call "
                "srefix-prom.alerts() filtered by job=~'{dep}.*' to see if it's already firing."
            ).format(tech=tech, dep="<dep>"),
        }

    @mcp.tool()
    def expand_to_dependents(tech: str) -> dict:
        """Reverse-lookup blast radius: which techs DEPEND ON `tech`?

        Use when assessing impact of an outage in `tech` on upstream services.
        Example: zookeeper just degraded → call this with tech='zookeeper' to
        see HBase / Kafka / Solr / Hadoop are all at risk.
        """
        parents = get_dependents(tech)
        return {
            "tech": tech,
            "dependent_count": len(parents),
            "dependents": [
                {
                    "parent_tech": p,
                    "relationship": d.relationship,
                    "severity": d.severity,
                    "pivot_when": d.pivot_when,
                    "diag_mcp": f"srefix-diag-{p}",
                }
                for (p, d) in parents
            ],
        }

    return mcp


def run() -> None:
    make_server().run()
