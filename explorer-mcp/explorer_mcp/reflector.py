"""Distill findings (metric/log results) into new keywords + suggested next steps.

Used after the LLM ran an exploration plan: feed back the raw findings, get
candidate keywords to retry against diag-{tech}.search() / diagnose_symptom().
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

# Words to ignore when extracting keywords from log lines
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "for", "with", "from", "into",
    "and", "or", "but", "of", "in", "on", "at", "to", "by", "as", "this", "that",
    "it", "be", "been", "have", "has", "had", "will", "would", "should", "could",
    "info", "warn", "error", "debug", "trace", "level",
    "true", "false", "null", "none",
}

_CAMEL_OR_PASCAL = re.compile(r"\b([A-Z][A-Za-z0-9_]{4,})\b")
_QUOTED_TOKEN = re.compile(r"'([A-Za-z0-9_]{4,})'|\"([A-Za-z0-9_]{4,})\"")
_ERROR_CODE = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")
_HOST_LIKE = re.compile(r"\b([a-z0-9][a-z0-9-]{2,}(?:\.[a-z0-9][a-z0-9-]{1,}){1,})\b")


def _extract_from_log_line(line: str) -> list[str]:
    out: list[str] = []
    for m in _CAMEL_OR_PASCAL.finditer(line):
        out.append(m.group(1))
    for m in _QUOTED_TOKEN.finditer(line):
        out.append(m.group(1) or m.group(2))
    for m in _ERROR_CODE.finditer(line):
        out.append(m.group(1))
    return out


def _candidate(token: str) -> bool:
    if len(token) < 4 or len(token) > 60:
        return False
    if token.lower() in _STOPWORDS:
        return False
    if token.replace("_", "").isdigit():
        return False
    return True


def reflect(findings: Iterable[dict], top_k: int = 20) -> dict:
    """Inspect a list of findings and propose follow-up search keywords + actions.

    A `finding` is loosely-typed — common shapes:
      {"type": "log_lines", "lines": ["ERROR: ...", ...]}
      {"type": "metric_anomaly", "metric": "...", "labels": {...}, "value": ...}
      {"type": "alert", "alertname": "...", "labels": {...}}
      {"type": "raw", "text": "..."}                      generic free text
    """
    keyword_counter: Counter = Counter()
    metrics_seen: set[str] = set()
    hosts_seen: set[str] = set()

    for f in findings or []:
        ftype = (f or {}).get("type", "raw")
        if ftype == "log_lines":
            for line in f.get("lines", []):
                for tok in _extract_from_log_line(str(line)):
                    if _candidate(tok):
                        keyword_counter[tok] += 1
                for h in _HOST_LIKE.findall(str(line)):
                    hosts_seen.add(h)
        elif ftype == "metric_anomaly":
            metrics_seen.add(f.get("metric", ""))
            for k, v in (f.get("labels") or {}).items():
                if isinstance(v, str) and _candidate(v):
                    keyword_counter[v] += 1
        elif ftype == "alert":
            if name := f.get("alertname"):
                keyword_counter[name] += 5
            for v in (f.get("labels") or {}).values():
                if isinstance(v, str) and _candidate(v):
                    keyword_counter[v] += 1
        else:  # raw
            for tok in _extract_from_log_line(str(f.get("text", ""))):
                if _candidate(tok):
                    keyword_counter[tok] += 1

    keywords = [kw for kw, _ in keyword_counter.most_common(top_k)]

    next_actions = []
    if keywords:
        next_actions.append({
            "action": "retry_search_with_extracted_keywords",
            "rationale": "These tokens appeared often in evidence; they may name a "
                         "specific case or component the manual covers.",
            "keywords": keywords,
            "suggested_calls": [
                {"mcp": "diag-{tech}", "tool": "search", "args": {"query": kw}}
                for kw in keywords[:5]
            ],
        })
    if metrics_seen:
        next_actions.append({
            "action": "drill_into_metrics",
            "rationale": "These metrics showed anomalies; pull their label_values "
                         "and per-label breakdowns to find the noisy series.",
            "metrics": sorted(metrics_seen),
            "suggested_calls": [
                {"mcp": "srefix-prom", "tool": "series", "args": {"match": [m]}}
                for m in sorted(metrics_seen)[:5]
            ],
        })
    if hosts_seen:
        next_actions.append({
            "action": "investigate_specific_hosts",
            "rationale": "Host names mentioned in error logs — narrow next probes to these.",
            "hosts": sorted(hosts_seen)[:10],
        })

    return {
        "extracted_keywords": keywords,
        "metrics_seen": sorted(metrics_seen),
        "hosts_seen": sorted(hosts_seen),
        "next_actions": next_actions,
    }
