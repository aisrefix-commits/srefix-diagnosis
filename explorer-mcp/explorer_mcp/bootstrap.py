"""Tier-3 free-exploration bootstrap.

When Tier-1 (manual) and Tier-2 (structured plan) both fail to find root cause,
this module returns a "prelude" guidance: 4 facts the LLM should fetch BEFORE
going free, so it knows the local schema (metric names, log labels, baselines)
instead of hallucinating PromQL.

Output is purely structured — no network calls. The LLM acts on it.
"""
from __future__ import annotations

from typing import Optional

from .categorizer import categorize

# Per-category metric-name keyword filters. After fetching all metric names from
# Prometheus, the LLM filters by these substrings to narrow the candidate pool.
_METRIC_KEYWORDS: dict[str, list[str]] = {
    "latency":          ["latency", "duration", "p99", "p95", "rt_ms", "response_time", "histogram"],
    "errors":           ["error", "fail", "exception", "5xx", "panic", "abort", "rejected"],
    "down":             ["up", "scrape", "health", "alive", "available", "reachable"],
    "memory":           ["memory", "heap", "rss", "oom", "alloc", "gc_", "evict"],
    "cpu":              ["cpu", "load", "user_seconds", "system_seconds", "throttl"],
    "disk":             ["disk", "filesystem", "wal", "iops", "io_time", "inode", "checkpoint"],
    "replication":      ["replication", "replica_lag", "wal", "binlog", "standby", "slot"],
    "hot_query":        ["query", "scan", "skew", "hot_partition", "slowlog"],
    "config_change":    ["start_time", "deploy", "version", "config_reload", "rollout"],
    "network":          ["tcp", "udp", "dns", "tls", "handshake", "retransmit", "drop"],
    "saturation":       ["queue", "backlog", "pool", "limit", "capacity", "quota", "throttle"],
    "data_consistency": ["checksum", "corrupt", "diverg"],
    "security":         ["auth", "token", "401", "403", "denied"],
    "unknown":          [],  # fall back to fetching all metric names
}

# Per-category log-line filter regex (used inside LogQL `|~` filter)
_LOG_REGEX: dict[str, str] = {
    "latency":          "(?i)slow|timeout|deadline|p99|latency",
    "errors":           "(?i)error|fail|fatal|exception|panic",
    "down":             "(?i)connection refused|reset by peer|unreachable|disconnect|lost connection",
    "memory":           "(?i)oom|out of memory|allocat|gc.+pause|heap",
    "cpu":              "(?i)throttl|saturat|cpu",
    "disk":             "(?i)disk full|no space|wal|checkpoint|inode",
    "replication":      "(?i)replic|sync|standby|wal|binlog|split.brain|failover",
    "hot_query":        "(?i)slow query|long.running|scatter|hot",
    "config_change":    "(?i)deploy|rollout|reload|migration|release",
    "network":          "(?i)dns|certificate|tls|x509|connection|reset|refused",
    "saturation":       "(?i)rate.?limit|throttl|429|backpress|queue full|too many",
    "data_consistency": "(?i)corrupt|checksum|inconsist|diverg|stale",
    "security":         "(?i)unauthor|forbidden|denied|invalid token|401|403",
    "unknown":          "(?i)error|warn|fail",
}


def free_explore_bootstrap(
    symptom: str,
    tech: str = "",
    cluster_id: str = "",
    host_pattern: str = "",
    max_queries: int = 20,
    max_minutes: int = 10,
) -> dict:
    """Build a Tier-3 bootstrap pack so the LLM can do schema-aware free exploration.

    Returns 4 structured starter probes + termination rules. The LLM is expected
    to call these probes first, build a local catalog of "what metrics + log
    streams exist here", and only then start hypothesizing.
    """
    categories = categorize(symptom)
    metric_keywords = sorted({kw for c in categories for kw in _METRIC_KEYWORDS.get(c, [])})
    log_regex = _LOG_REGEX.get(categories[0], _LOG_REGEX["unknown"]) if categories else _LOG_REGEX["unknown"]

    host_pat = host_pattern or (f"{tech}.*" if tech else ".*")

    return {
        "symptom": symptom,
        "tech": tech or None,
        "cluster_id": cluster_id or None,
        "categories_matched": categories,
        "rationale": (
            "Tier-1 (diag-{tech}) and Tier-2 (fallback_exploration_plan) both "
            "missed. Establish local schema first — don't hallucinate PromQL."
        ),
        "step_1_metric_catalog": {
            "rationale": "Enumerate ALL metric names this Prometheus knows about, then "
                         "filter by symptom-derived keywords to get a candidate shortlist.",
            "calls": [
                {"mcp": "srefix-prom", "tool": "label_values",
                 "args": {"label": "__name__"}},
            ],
            "filter_substrings": metric_keywords,
            "next": "From the returned list, keep names containing any of `filter_substrings`. "
                    "Skip the rest. Result is your candidate metric pool.",
        },
        "step_2_log_stream_catalog": {
            "rationale": "Find which label this Loki uses to identify services. "
                         "Common conventions: `app`, `service`, `component`, `job`. "
                         "Different orgs pick different ones.",
            "calls": [
                {"mcp": "srefix-loki", "tool": "labels"},
                {"mcp": "srefix-loki", "tool": "label_values", "args": {"label": "app"}},
                {"mcp": "srefix-loki", "tool": "label_values", "args": {"label": "service"}},
            ],
            "next": "Pick whichever label has values that look service-y. "
                    "Use it for all subsequent loki.query_range calls.",
            "log_filter_regex": log_regex,
        },
        "step_3_baseline_compare": {
            "rationale": "For each candidate metric from step 1, pull 24h history "
                         "to spot the moment-of-divergence and confirm anomaly is "
                         "not just baseline drift.",
            "template_call": {
                "mcp": "srefix-prom", "tool": "range_query",
                "args_template": {
                    "query": "<CANDIDATE_METRIC>{__INSTANCE_FILTER__}",
                    "start": "-24h", "end": "now", "step": "5m",
                    "max_series": 20, "max_points": 200,
                },
                "substitutions_to_apply": {
                    "__INSTANCE_FILTER__": (
                        f"{{instance=~'{host_pat}'}}" if host_pattern
                        else "{}  # no instance filter — fetch all"
                    ),
                },
            },
            "next": "Look for series that diverged from baseline at the same wall time "
                    "the user reports the symptom. Cross-reference with step 4 logs.",
        },
        "step_4_dependency_chain": {
            "rationale": "Symptom may originate UPSTREAM (DNS, k8s, storage, identity). "
                         "Pull dependencies from discovery, then iterate to those.",
            "calls": (
                [{"mcp": "srefix-discovery", "tool": "get_cluster",
                  "args": {"cluster_id": cluster_id}}]
                if cluster_id else
                [{"mcp": "srefix-discovery", "tool": "list_clusters",
                  "args": {"tech": tech}}]
            ),
            "next": "Read `.dependencies[]` from the result. For any dep that has "
                    "ALSO had a recent incident (check its alerts), pivot the "
                    "investigation upstream.",
        },
        "iteration_loop": {
            "after_each_finding": (
                "Call srefix-explorer.reflect_on_findings with what you got. "
                "It returns extracted_keywords — try EACH keyword against "
                "diag-{tech}.search(). The manual often does cover the case "
                "but with different wording."
            ),
        },
        "termination_rules": {
            "max_queries": max_queries,
            "max_minutes": max_minutes,
            "convergence_signal": "If a single metric anomaly + a log error pattern "
                                  "+ a host name all line up to the same wall-clock "
                                  "moment, you've converged. Report root cause.",
            "if_unresolved_action": (
                "After max_queries OR max_minutes, STOP. Compose a structured "
                "evidence dump: (1) what you queried, (2) raw outputs, "
                "(3) hypotheses you ruled out and why. Hand to the human. "
                "Do NOT confabulate a root cause."
            ),
        },
        "warnings": [
            "Verify metric existence with prom.series() before assuming it exists.",
            "Don't trust LLM-recalled metric names — names vary by exporter version.",
            "Wide time windows (-24h) cost tokens but give baseline; use for step 3 only.",
            "If you find 0 candidate metrics in step 1, the symptom keywords don't "
            "match this org's metric naming; widen by stripping prefixes (e.g. drop the tech name).",
        ],
    }
