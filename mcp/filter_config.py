#!/usr/bin/env python3
"""Filter claude_mcp_config.json down to a subset of techs.

Usage:
  python3 filter_config.py postgres redis kafka hbase k8s > my_subset.json
  python3 filter_config.py --from-file my_techs.txt > my_subset.json
  python3 filter_config.py --regex 'postgres|redis|.*-cli' > matching.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CONFIG = Path(__file__).parent / "claude_mcp_config.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("techs", nargs="*", help="tech names (e.g. postgres redis kafka)")
    ap.add_argument("--from-file", type=Path, help="read tech names (one per line) from file")
    ap.add_argument("--regex", help="include techs whose name matches this regex")
    ap.add_argument("--list-all", action="store_true",
                    help="just list all 250 available techs, one per line")
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())
    all_servers: dict = cfg.get("mcpServers", {})

    if args.list_all:
        for name in sorted(all_servers):
            print(name.removeprefix("diag-"))
        return

    wanted: set[str] = set(args.techs)
    if args.from_file and args.from_file.exists():
        wanted.update(
            line.strip() for line in args.from_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        )
    pattern = re.compile(args.regex) if args.regex else None

    filtered: dict = {}
    for name, body in all_servers.items():
        tech = name.removeprefix("diag-")
        if tech in wanted or (pattern and pattern.search(tech)):
            filtered[name] = body

    if not filtered:
        print("# WARNING: no MCPs matched your filter", file=sys.stderr)
        print(f"# Available techs (run with --list-all to see):", file=sys.stderr)
        for t in sorted(all_servers)[:10]:
            print(f"#   {t.removeprefix('diag-')}", file=sys.stderr)
        sys.exit(1)

    print(f"# Filtered: {len(filtered)} of {len(all_servers)} MCPs", file=sys.stderr)
    json.dump({"mcpServers": filtered}, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
