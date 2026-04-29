"""CLI driver for the install-time corpus check.

Usage:
    srefix-verify-corpus                 # auto-find agents/ relative to repo
    srefix-verify-corpus path/to/agents  # explicit path
    srefix-verify-corpus --tech vitess   # single tech only

Exit code 0 if no flagged metrics in covered manuals; 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .verifier import audit_corpus, list_whitelisted_techs, verify_manual


def _find_agents_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "agents"
        if candidate.is_dir() and any(candidate.glob("*.md")):
            return candidate
    return Path.cwd() / "agents"


def _print_corpus_report(report: dict) -> None:
    total = report["manuals_total"]
    covered = report["manuals_with_whitelist"]
    uncovered = report["manuals_without_whitelist"]
    flagged = report["total_flagged"]
    refs = report["total_metric_refs_in_covered"]
    good = report["total_known_good"]

    print(f"Manuals scanned: {total}")
    print(f"  with whitelist:    {covered:>3}  ({', '.join(report['whitelists_available'])})")
    print(f"  without whitelist: {uncovered:>3}  (not yet covered)")
    print()
    print(f"In covered manuals: {refs} metric references")
    print(f"  matched whitelist (likely real): {good}")
    print(f"  flagged (likely hallucinated):   {flagged}")
    print()

    if flagged:
        print("── Flagged metrics by manual ──")
        for r in report["covered_results"]:
            if r["metrics_flagged"] == 0:
                continue
            print(f"\n  [{r['tech']}]  {r['metrics_flagged']} flagged "
                  f"(source: {r['whitelist_source']})")
            for f in r["findings"][:10]:
                lines = ",".join(str(x) for x in f["lines"][:5])
                more = "" if len(f["lines"]) <= 5 else f",…(+{len(f['lines'])-5})"
                print(f"    × {f['name']}  ×{f['occurrences']}  L{lines}{more}")
            if len(r["findings"]) > 10:
                print(f"    …({len(r['findings'])-10} more)")

    if uncovered > 0:
        print()
        print(f"── {uncovered} techs without a whitelist ──")
        print("To contribute a whitelist for a tech, see README → "
              "'Verify accuracy' → 'Adding a whitelist'.")


def run() -> int:
    p = argparse.ArgumentParser(description="Verify metric-name accuracy of srefix-diagnosis manuals.")
    p.add_argument("agents_dir", nargs="?", help="path to agents/ (auto-detected if omitted)")
    p.add_argument("--tech", help="verify a single tech only")
    p.add_argument("--json", action="store_true", help="output JSON instead of text")
    p.add_argument("--list-whitelists", action="store_true",
                   help="list techs with whitelists and exit")
    args = p.parse_args()

    if args.list_whitelists:
        for t in list_whitelisted_techs():
            print(t)
        return 0

    agents_dir = Path(args.agents_dir) if args.agents_dir else _find_agents_dir()
    if not agents_dir.is_dir():
        print(f"agents dir not found: {agents_dir}", file=sys.stderr)
        return 2

    if args.tech:
        candidates = [agents_dir / f"{args.tech}-agent.md", agents_dir / f"{args.tech}.md"]
        manual = next((c for c in candidates if c.exists()), None)
        if manual is None:
            print(f"no manual for tech={args.tech} in {agents_dir}", file=sys.stderr)
            return 2
        result = verify_manual(args.tech, manual)
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            d = result.to_dict()
            print(f"tech: {d['tech']}")
            print(f"manual: {d['manual_path']}")
            if not d["has_whitelist"]:
                print(f"no whitelist for {args.tech} — {d['metrics_referenced']} metric refs uncheckable")
                return 0
            print(f"whitelist source: {d['whitelist_source']}")
            print(f"references: {d['metrics_referenced']}  "
                  f"matched: {d['metrics_known_good']}  "
                  f"flagged: {d['metrics_flagged']}")
            for f in d["findings"]:
                lines = ",".join(str(x) for x in f["lines"][:5])
                more = "" if len(f["lines"]) <= 5 else f",…(+{len(f['lines'])-5})"
                print(f"  × {f['name']}  ×{f['occurrences']}  L{lines}{more}")
        return 0 if (not result.has_whitelist or result.metrics_flagged == 0) else 1

    report = audit_corpus(agents_dir)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_corpus_report(report)
    return 0 if report["total_flagged"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
