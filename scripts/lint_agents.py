#!/usr/bin/env python3
"""Lint 250 .md manuals for known machine-detectable accuracy issues.

Categories:
  PROMQL_CHAIN     PromQL chained-comparison `a + b >= c > d` (broken)
  PROMQL_BAD       PromQL with obviously broken syntax (mismatched parens, etc)
  DEAD_NAMESPACE   References to renamed/removed packages (Titan, etc)
  DEAD_FLAG        Deprecated CLI flags (hbck -repair, etc)
  HALLUCINATED     Metric/config names that look invented
  VAGUE_ADVICE     'Monitor closely', 'consider scaling', 'as needed' platitudes
                   inside diagnostic instructions (not OK in operational runbooks)
  VERSION_DRIFT    Things that have changed between major versions
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

AGENTS = Path("/Users/albericliu/PrivateWorkspace/GitHub/srefix-diagnosis/agents")


# Pre-compiled rules: (category, regex, description)
RULES = [
    # ── PromQL syntax issues (strict: only match real-looking PromQL) ──
    # Backtick-wrapped content starting with a real PromQL function or metric
    # prefix, that THEN has a chained comparison.
    ("PROMQL_CHAIN",
     re.compile(r"`(?:rate|sum|avg|max|min|histogram_quantile|count|topk|"
                r"[a-z_]+_(?:total|seconds|bytes|count|ratio))[^`]*"
                r"(?:\+[^`]*>=[^`]*>|>[^`]*>=)[^`]*`"),
     "PromQL chained comparison (a + b >= c > d / a > b >= c) — not valid"),
    ("PROMQL_BAD",
     re.compile(r"`(?:rate|histogram_quantile)\([^`)]*\[\d+[smhd]\]\)\)"),
     "Extra closing paren after rate()/histogram_quantile() time-window"),
    ("PROMQL_BAD",
     re.compile(r"`[^`]*\bby\s*\(\s*\)\s*[^`]*`"),
     "PromQL `by ()` — empty grouping is suspicious"),

    # ── Dead namespaces / renamed projects ──
    ("DEAD_NAMESPACE",
     re.compile(r"thinkaurelius\.titan"),
     "Titan was renamed to JanusGraph in 2017 (org.janusgraph.*)"),
    ("DEAD_NAMESPACE",
     re.compile(r"\bTitanGraph\b"),
     "Use JanusGraph instead of TitanGraph"),

    # ── Deprecated / dangerous CLI flags ──
    ("DEAD_FLAG",
     re.compile(r"\bhbase\s+hbck\s+-(repair|repairHoles|fixAssignments)\b"),
     "hbck -repair* removed in HBase 2.x — use HBCK2"),
    ("DEAD_FLAG",
     re.compile(r"\bdocker\s+--no-include-email"),
     "docker --no-include-email removed in 19.03+"),
    ("DEAD_FLAG",
     re.compile(r"\bkubectl\s+create\s+--dry-run\b(?!=)"),
     "kubectl --dry-run requires =client/server in 1.18+"),
    ("DEAD_FLAG",
     re.compile(r"\bredis-cli\s+--scan\b\s+(?!--pattern)"),
     "redis-cli --scan should specify --pattern in 6.x+"),
    ("DEAD_FLAG",
     re.compile(r"\bnodetool\s+upgradesstables\b\s+(?!-a)"),
     "nodetool upgradesstables benefits from -a flag in C* 4.x"),
    ("DEAD_FLAG",
     re.compile(r"\bcassandra\.thrift\b"),
     "Thrift transport removed in Cassandra 4.0 (2021)"),
    ("DEAD_FLAG",
     re.compile(r"\bES\s+--type\b"),
     "Elasticsearch --type/_type removed in ES 7.x+"),

    # ── Vague platitudes inside diagnostic blocks ──
    ("VAGUE_ADVICE",
     re.compile(r"\bmonitor\s+closely\b", re.I),
     "vague: 'monitor closely' — should specify metric + threshold"),
    ("VAGUE_ADVICE",
     re.compile(r"\bconsider\s+(scaling|increasing|upgrading)\s+as\s+needed\b", re.I),
     "vague: 'consider scaling as needed' — runbook should be specific"),
    ("VAGUE_ADVICE",
     re.compile(r"\bappropriate\s+(threshold|level|value)\b", re.I),
     "vague: 'appropriate threshold' — should give a number"),
    ("VAGUE_ADVICE",
     re.compile(r"\bregularly\s+review\b", re.I),
     "vague: 'regularly review' — should specify cadence + check"),

    # ── Hallucinated/garbled config keys (specific patterns LLMs invent) ──
    ("HALLUCINATED",
     re.compile(r"index\.search\.elasticsearch\.http\.ext\."),
     "JanusGraph ES config key looks invented — real form: index.search.elasticsearch.{ssl|client-only}.*"),

    # ── Version-drift gotchas ──
    ("VERSION_DRIFT",
     re.compile(r"\bkubectl\s+run\s+\S+\s+--image\b[^\n`]*--port"),
     "kubectl run --port deprecated; use kubectl create deployment"),
    ("VERSION_DRIFT",
     re.compile(r"\bdocker-compose\s+\b(?!.*--profile)"),
     "consider noting docker compose v2 (no hyphen) for newer Docker"),
]


def lint_file(path: Path) -> list[tuple[int, str, str, str]]:
    """Returns list of (line_no, category, snippet, description)."""
    findings: list[tuple[int, str, str, str]] = []
    text = path.read_text()
    for i, line in enumerate(text.split("\n"), 1):
        for cat, pat, desc in RULES:
            m = pat.search(line)
            if m:
                snippet = line.strip()[:120]
                findings.append((i, cat, snippet, desc))
    return findings


def main() -> int:
    files = sorted(AGENTS.glob("*.md"))
    by_category: dict[str, list[tuple[str, int, str, str]]] = defaultdict(list)
    by_file: dict[str, int] = defaultdict(int)

    for f in files:
        for line_no, cat, snippet, desc in lint_file(f):
            by_category[cat].append((f.name, line_no, snippet, desc))
            by_file[f.name] += 1

    total = sum(len(v) for v in by_category.values())
    print(f"Scanned {len(files)} files; {total} potential issues across "
          f"{len(by_file)} files\n")

    print("── By category ──")
    for cat in sorted(by_category, key=lambda c: -len(by_category[c])):
        print(f"  {cat:<18} {len(by_category[cat]):>4}")

    print("\n── Top 15 files by issue count ──")
    for fname, n in sorted(by_file.items(), key=lambda x: -x[1])[:15]:
        print(f"  {n:>3}  {fname}")

    print("\n── Sample findings per category (first 5) ──")
    for cat in sorted(by_category, key=lambda c: -len(by_category[c])):
        print(f"\n  [{cat}]")
        for fname, line_no, snippet, desc in by_category[cat][:5]:
            print(f"    {fname}:{line_no}  {snippet[:100]}")
            print(f"      → {desc}")

    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
