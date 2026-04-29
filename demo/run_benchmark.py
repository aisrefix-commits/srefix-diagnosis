#!/usr/bin/env python3
"""Run the 5 demo scenarios through Claude (headless) and score the output.

For each scenario:
  1. Set DEMO_SCENARIO_ID so the mock-telemetry-mcp serves the right canned data
  2. Invoke Claude Code CLI in --print (headless) mode with the demo prompt
     and the demo MCP config — Claude actually orchestrates the diag-{tech}
     manuals + mock telemetry tools and writes a diagnosis to stdout
  3. Score: how many expected_diagnosis.keywords appear in the output
  4. Pass = keyword coverage ≥ scenario's min_confidence

Outputs a markdown summary + benchmark_report.json with per-scenario detail.

Prereqs:
  - claude CLI on PATH (Claude Code, https://claude.com/claude-code)
  - All demo MCPs installed: see ../README.md install steps
  - 5 minutes per run (Claude tool use loop is slow on cold cache)

Usage:
  python3 demo/run_benchmark.py                 # run all 5
  python3 demo/run_benchmark.py mongodb-* nginx-*   # filter by glob
  python3 demo/run_benchmark.py --timeout 600   # extend per-scenario timeout
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
SCENARIOS_PATH = DEMO_DIR / "scenarios.json"
MCP_CONFIG_PATH = DEMO_DIR / "claude_config_demo.json"
REPORT_PATH = DEMO_DIR / "benchmark_report.json"


DEMO_PROMPTS: dict[str, str] = {
    "nginx-502-upstream-001":
        "Production alert: Nginx 502 error rate hit 25% on backend-api upstream. "
        "Investigate using available tools and explain the root cause.",
    "mongodb-replicaset-election-001":
        "Alert: MongoDB rs.election_count=5 on user-db-prod, primary stepdown "
        "detected. Diagnose using available diag-* and telemetry tools.",
    "etcd-disk-latency-001":
        "Critical: etcd WAL fsync p99 is 800ms on k8s-prod, and kube-apiserver "
        "request latency is 12s. Find the root cause — is the K8s control plane "
        "broken because of etcd? Use the explorer expand_to_dependents tool to "
        "trace cross-tech impact.",
    "dns-resolution-failure-001":
        "CoreDNS SERVFAIL spike: K8s services can't resolve names. Use the "
        "diag-coredns tools and check telemetry to diagnose.",
    "cassandra-gc-pause-001":
        "cassandra-03 has an 8.5s GC pause; cluster reads are timing out. "
        "Diagnose using diag-cassandra + telemetry.",
}


def keyword_score(output: str, expected_keywords: list[str]) -> tuple[float, list[str]]:
    """Lower-case substring match — robust for multi-word keywords."""
    text = output.lower()
    matched = [k for k in expected_keywords if k.lower() in text]
    if not expected_keywords:
        return 0.0, []
    return len(matched) / len(expected_keywords), matched


def run_scenario(scenario: dict, timeout: int) -> dict:
    sid = scenario["id"]
    prompt = DEMO_PROMPTS.get(sid)
    if not prompt:
        return {"id": sid, "skipped": True, "reason": "no demo prompt for this scenario"}

    expected = scenario.get("expected_diagnosis", {}) or {}
    expected_keywords = expected.get("keywords", []) or []
    min_confidence = float(expected.get("min_confidence", 0.7))

    env = os.environ.copy()
    env["DEMO_SCENARIO_ID"] = sid

    print(f"\n── Running {sid} ──")
    print(f"  prompt:   {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
    print(f"  expected keywords: {expected_keywords}")
    print(f"  min_confidence:    {min_confidence}")

    t0 = time.time()
    try:
        proc = subprocess.run(
            ["claude", "--print",
             "--mcp-config", str(MCP_CONFIG_PATH),
             "--dangerously-skip-permissions",
             prompt],
            env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "id": sid, "name": scenario["name"], "difficulty": scenario["difficulty"],
            "passed": False, "duration_s": timeout,
            "error": f"timeout after {timeout}s",
            "expected_keywords": expected_keywords,
            "matched_keywords": [], "keyword_score": 0.0,
        }
    duration = time.time() - t0

    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    score, matched = keyword_score(output, expected_keywords)
    passed = score >= min_confidence

    print(f"  → {'PASS' if passed else 'FAIL'}  duration={duration:.1f}s  "
          f"keyword_score={score:.0%}  matched={len(matched)}/{len(expected_keywords)}")

    return {
        "id": sid,
        "name": scenario["name"],
        "difficulty": scenario["difficulty"],
        "passed": passed,
        "duration_s": round(duration, 1),
        "exit_code": proc.returncode,
        "expected_keywords": expected_keywords,
        "matched_keywords": matched,
        "keyword_score": round(score, 3),
        "min_confidence": min_confidence,
        "output_preview": (proc.stdout or "")[:600],
    }


def render_markdown(results: list[dict]) -> str:
    runs = [r for r in results if not r.get("skipped")]
    pass_count = sum(1 for r in runs if r.get("passed"))
    total = len(runs)
    rate = (pass_count / total * 100) if total else 0
    avg_dur = (sum(r.get("duration_s", 0) for r in runs) / total) if total else 0

    out = []
    out.append("## srefix-diagnosis — 5-scenario benchmark\n")
    out.append(f"**Pass rate: {pass_count}/{total} ({rate:.0f}%) · "
               f"avg duration: {avg_dur:.1f}s**\n")
    out.append("| Scenario | Difficulty | Pass | Duration | Keywords matched |")
    out.append("|----------|------------|------|----------|------------------|")
    for r in runs:
        check = "✓" if r["passed"] else "✗"
        kw_count = f"{len(r['matched_keywords'])}/{len(r['expected_keywords'])}"
        name = r["name"][:50]
        out.append(f"| {name} | {r['difficulty']} | {check} | "
                   f"{r['duration_s']}s | {kw_count} ({r['keyword_score']:.0%}) |")
    out.append("")
    out.append("Each scenario uses the diag-{tech} manual + srefix-explorer + "
               "mock-telemetry MCPs. Claude's reasoning is real; only the "
               "telemetry I/O is mocked (canned data per scenario).")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("filters", nargs="*",
                    help="optional glob patterns to filter scenario IDs")
    ap.add_argument("--timeout", type=int, default=300,
                    help="per-scenario timeout in seconds (default: 300)")
    args = ap.parse_args()

    if not shutil.which("claude"):
        print("ERROR: claude CLI not on PATH. Install Claude Code first:")
        print("       https://claude.com/claude-code")
        return 2

    if not MCP_CONFIG_PATH.exists():
        print(f"ERROR: {MCP_CONFIG_PATH} not found")
        return 2

    if not SCENARIOS_PATH.exists():
        print(f"ERROR: {SCENARIOS_PATH} not found")
        return 2

    scenarios = json.loads(SCENARIOS_PATH.read_text())
    if args.filters:
        scenarios = [s for s in scenarios
                     if any(fnmatch.fnmatch(s["id"], pat) for pat in args.filters)]
    if not scenarios:
        print("No scenarios matched filter")
        return 1

    print(f"Running {len(scenarios)} scenario(s) (timeout {args.timeout}s each)")
    print(f"  MCP config: {MCP_CONFIG_PATH}")
    print(f"  Scenarios:  {SCENARIOS_PATH}\n")

    results = [run_scenario(s, args.timeout) for s in scenarios]

    md = render_markdown(results)
    print("\n" + "=" * 70)
    print(md)
    print("=" * 70)

    REPORT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nDetailed JSON report: {REPORT_PATH}")

    pass_count = sum(1 for r in results if r.get("passed"))
    return 0 if pass_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
