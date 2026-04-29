"""Manual metric verifier.

Extracts metric-name references from a tech's manual and diffs them against a
whitelist captured from the tech's real Prometheus exporter. Anything in the
manual but not in the whitelist is flagged as a likely hallucination.

Whitelist format (JSON, in `whitelists/<tech>.json`):

    {
      "tech": "vitess",
      "source": "vtgate /metrics @ vitess v18.0.2",
      "captured_at": "2026-04-27",
      "method": "curl http://vtgate:15000/metrics | grep '^# HELP' | awk '{print $3}' | sort -u",
      "metric_names": ["vtgate_api_count", "vtgate_api_error_counts", ...]
    }

The verifier is deliberately conservative: it ONLY checks names matching a
metric-shape regex (snake_case ending in a Prometheus suffix), so prose
mentions like "the requests_total counter" don't get flagged.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# A "metric reference" is a snake_case identifier that appears in PromQL
# context. Two unambiguous markers:
#   1. <name>[<duration>]     — range-vector selector (e.g. rate(foo_total[5m]))
#   2. <name>{<label>=...}    — instant-vector selector with labels
# This is precise (config keys, env vars, file paths don't have these
# adjacencies) and catches both LLM-emitted PromQL code samples and the
# inline {label="..."} mentions in prose.
_METRIC_REGEX = re.compile(
    r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\s*(?:\[\d+(?:\.\d+)?[smhdwy]\]|\{)"
)

# Allow-list of names that are *generic Prometheus* metrics — emitted by every
# Go/Python exporter, not tech-specific. Skip them so per-tech whitelists
# don't need to repeat them 250 times.
_GENERIC_METRICS = frozenset({
    "go_gc_duration_seconds", "go_goroutines", "go_info", "go_memstats_alloc_bytes",
    "go_memstats_alloc_bytes_total", "go_memstats_buck_hash_sys_bytes",
    "go_memstats_frees_total", "go_memstats_gc_sys_bytes",
    "go_memstats_heap_alloc_bytes", "go_memstats_heap_idle_bytes",
    "go_memstats_heap_inuse_bytes", "go_memstats_heap_objects",
    "go_memstats_heap_released_bytes", "go_memstats_heap_sys_bytes",
    "go_memstats_last_gc_time_seconds", "go_memstats_lookups_total",
    "go_memstats_mallocs_total", "go_memstats_mcache_inuse_bytes",
    "go_memstats_mspan_inuse_bytes", "go_memstats_next_gc_bytes",
    "go_memstats_other_sys_bytes", "go_memstats_stack_inuse_bytes",
    "go_memstats_stack_sys_bytes", "go_memstats_sys_bytes",
    "go_threads", "process_cpu_seconds_total", "process_max_fds",
    "process_open_fds", "process_resident_memory_bytes",
    "process_start_time_seconds", "process_virtual_memory_bytes",
    "process_virtual_memory_max_bytes", "promhttp_metric_handler_requests_total",
    "promhttp_metric_handler_requests_in_flight", "scrape_duration_seconds",
    "scrape_samples_post_metric_relabeling", "scrape_samples_scraped",
    "scrape_series_added", "up",
})

WHITELIST_DIR = Path(__file__).parent / "whitelists"


@dataclass
class Finding:
    name: str
    occurrences: int
    line_numbers: list[int]


@dataclass
class VerifyResult:
    tech: str
    manual_path: str
    has_whitelist: bool
    whitelist_source: str
    metrics_referenced: int
    metrics_known_good: int
    metrics_flagged: int
    findings: list[Finding]
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "tech": self.tech,
            "manual_path": self.manual_path,
            "has_whitelist": self.has_whitelist,
            "whitelist_source": self.whitelist_source,
            "metrics_referenced": self.metrics_referenced,
            "metrics_known_good": self.metrics_known_good,
            "metrics_flagged": self.metrics_flagged,
            "findings": [
                {"name": f.name, "occurrences": f.occurrences, "lines": f.line_numbers}
                for f in self.findings
            ],
            "error": self.error,
        }


def load_whitelist(tech: str) -> tuple[set[str], str]:
    """Returns (metric_set, source_label). Empty set + '' if no whitelist."""
    path = WHITELIST_DIR / f"{tech}.json"
    if not path.exists():
        return set(), ""
    data = json.loads(path.read_text())
    return set(data.get("metric_names", [])), data.get("source", "")


def extract_metric_refs(text: str) -> dict[str, list[int]]:
    """Walk every line, return {metric_name: [line_numbers]}."""
    refs: dict[str, list[int]] = {}
    for i, line in enumerate(text.split("\n"), 1):
        for m in _METRIC_REGEX.finditer(line):
            name = m.group(1)
            if name in _GENERIC_METRICS:
                continue
            refs.setdefault(name, []).append(i)
    return refs


def verify_manual(tech: str, manual_path: Path) -> VerifyResult:
    if not manual_path.exists():
        return VerifyResult(
            tech=tech, manual_path=str(manual_path), has_whitelist=False,
            whitelist_source="", metrics_referenced=0, metrics_known_good=0,
            metrics_flagged=0, findings=[],
            error=f"manual not found: {manual_path}",
        )

    whitelist, source = load_whitelist(tech)
    refs = extract_metric_refs(manual_path.read_text())

    if not whitelist:
        return VerifyResult(
            tech=tech, manual_path=str(manual_path), has_whitelist=False,
            whitelist_source="", metrics_referenced=len(refs),
            metrics_known_good=0, metrics_flagged=0, findings=[],
        )

    flagged = []
    known_good = 0
    for name, lines in sorted(refs.items()):
        if name in whitelist:
            known_good += 1
        else:
            flagged.append(Finding(name=name, occurrences=len(lines), line_numbers=lines))

    return VerifyResult(
        tech=tech, manual_path=str(manual_path), has_whitelist=True,
        whitelist_source=source, metrics_referenced=len(refs),
        metrics_known_good=known_good, metrics_flagged=len(flagged),
        findings=flagged,
    )


def list_whitelisted_techs() -> list[str]:
    if not WHITELIST_DIR.exists():
        return []
    return sorted(p.stem for p in WHITELIST_DIR.glob("*.json"))


def audit_corpus(agents_dir: Path) -> dict:
    """Run verification across every manual in agents/. Returns summary dict."""
    techs_with_whitelist = set(list_whitelisted_techs())
    results: list[VerifyResult] = []
    for md in sorted(agents_dir.glob("*.md")):
        tech = md.stem.replace("-agent", "")
        results.append(verify_manual(tech, md))

    covered = [r for r in results if r.has_whitelist]
    uncovered = [r for r in results if not r.has_whitelist]
    total_flagged = sum(r.metrics_flagged for r in covered)

    return {
        "manuals_total": len(results),
        "manuals_with_whitelist": len(covered),
        "manuals_without_whitelist": len(uncovered),
        "whitelists_available": sorted(techs_with_whitelist),
        "total_metric_refs_in_covered": sum(r.metrics_referenced for r in covered),
        "total_known_good": sum(r.metrics_known_good for r in covered),
        "total_flagged": total_flagged,
        "covered_results": [r.to_dict() for r in covered],
        "uncovered_techs": sorted(r.tech for r in uncovered),
    }
