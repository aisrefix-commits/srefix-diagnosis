"""srefix-fix — proposer / applier CLI.

Subcommands:
  propose <tech> [--agents PATH] [--output PATH] [--print]
                                 [--allowed-tools STR] [--timeout SEC]
      Build the LLM prompt for drafting a fix-map. With --print, emit the
      prompt to stdout. Without, spawn `claude --print` and write the YAML
      output to PATH (default: fix_maps/<tech>.draft.yaml).

  apply <yaml> [--agents PATH] [--dry-run]
      Apply a human-reviewed fix-map. Only entries with non-empty
      `confirmed_by` are touched. Pure-sed; no LLM at apply time.

  validate <yaml>
      Check schema, surface duplicates / no-ops. Exit 0 on clean.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .fixer import apply_fix_map, load_fix_map, validate_fix_map
from .proposer import print_prompt, run_headless


def _agents_default() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "agents"
        if candidate.is_dir() and any(candidate.glob("*.md")):
            return candidate
    return Path.cwd() / "agents"


def _fix_maps_default() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "fix_maps"
        if candidate.is_dir():
            return candidate
    return Path.cwd() / "fix_maps"


def cmd_propose(args: argparse.Namespace) -> int:
    agents = Path(args.agents) if args.agents else _agents_default()
    if not agents.is_dir():
        print(f"agents dir not found: {agents}", file=sys.stderr)
        return 2
    if args.print:
        sys.stdout.write(print_prompt(args.tech, agents))
        return 0
    out = Path(args.output) if args.output else _fix_maps_default() / f"{args.tech}.draft.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    rc = run_headless(
        args.tech, agents, out,
        allowed_tools=args.allowed_tools,
        timeout_seconds=args.timeout,
    )
    if rc == 0:
        print(f"wrote {out}", file=sys.stderr)
    return rc


def cmd_apply(args: argparse.Namespace) -> int:
    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        print(f"fix-map not found: {yaml_path}", file=sys.stderr)
        return 2
    problems = validate_fix_map(yaml_path)
    if problems:
        print(f"{yaml_path}: validation problems:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        if not args.force:
            return 2

    fm = load_fix_map(yaml_path)
    agents = Path(args.agents) if args.agents else _agents_default()
    result = apply_fix_map(fm, agents, dry_run=args.dry_run)

    print(f"tech: {result.tech}")
    print(f"manual: {result.manual_path or '(not found)'}")
    if result.applied:
        print(f"applied {len(result.applied)} fixes:")
        for old, new, count in result.applied:
            print(f"  ✓ {old}  →  {new}   ×{count}")
    if result.skipped_unconfirmed:
        print(f"skipped {len(result.skipped_unconfirmed)} unconfirmed (empty confirmed_by):")
        for old in result.skipped_unconfirmed:
            print(f"  · {old}")
    if result.skipped_not_found:
        print(f"skipped {len(result.skipped_not_found)} not found in manual:")
        for old in result.skipped_not_found:
            print(f"  · {old}")
    if result.mismatched_counts:
        print(f"WARNING: {len(result.mismatched_counts)} fixes had unexpected occurrence counts:")
        for old, expected, actual in result.mismatched_counts:
            print(f"  ! {old}: expected {expected}, got {actual}")

    if args.dry_run and result.diff:
        print()
        print("── diff ──")
        sys.stdout.write(result.diff)
    elif not args.dry_run and result.applied:
        print()
        print("(written to disk; run `git diff` to review)")

    return 1 if result.mismatched_counts else 0


def cmd_validate(args: argparse.Namespace) -> int:
    problems = validate_fix_map(Path(args.yaml))
    if not problems:
        print("OK")
        return 0
    for p in problems:
        print(f"× {p}")
    return 1


def run() -> int:
    p = argparse.ArgumentParser(prog="srefix-fix",
                                description="Proposer/applier for diagnosis-manual metric-name fixes.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("propose", help="draft a fix-map for a tech")
    pp.add_argument("tech")
    pp.add_argument("--agents", help="path to agents/ (auto-detected)")
    pp.add_argument("--output", help="output YAML path (default: fix_maps/<tech>.draft.yaml)")
    pp.add_argument("--print", action="store_true",
                    help="just print the prompt; don't run claude")
    pp.add_argument("--allowed-tools",
                    default="Read,Bash(grep:*),Bash(find:*),WebFetch,WebSearch",
                    help="value passed to claude --allowedTools (read-only)")
    pp.add_argument("--timeout", type=int, default=600,
                    help="claude --print timeout in seconds (default 600)")
    pp.set_defaults(func=cmd_propose)

    pa = sub.add_parser("apply", help="apply a reviewed fix-map")
    pa.add_argument("yaml")
    pa.add_argument("--agents", help="path to agents/ (auto-detected)")
    pa.add_argument("--dry-run", action="store_true",
                    help="show diff without writing")
    pa.add_argument("--force", action="store_true",
                    help="apply even if validation problems exist")
    pa.set_defaults(func=cmd_apply)

    pv = sub.add_parser("validate", help="schema-check a fix-map")
    pv.add_argument("yaml")
    pv.set_defaults(func=cmd_validate)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(run())
