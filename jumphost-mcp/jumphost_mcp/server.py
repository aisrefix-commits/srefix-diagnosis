"""FastMCP server: SSH-via-jumphost executor with safety gating.

Tools:
  list_hosts(tech?, role?, env?)
  list_presets(tech?)
  describe_preset(tech, name)
  run_safe(host, tech, preset_name, args?)
  run(host, command, timeout?)         (only enabled when JUMPHOST_MODE != preset_only)
  tail(host, file, lines?, grep?)

Modes (env JUMPHOST_MODE):
  preset_only           default — only run_safe is enabled
  filtered_arbitrary    run is enabled but commands go through safety filter
  unrestricted          run accepts anything (use only with external approval gate)

Other env:
  JUMPHOST_INVENTORY=/path/to/inventory.yaml
  JUMPHOST_PRESETS=/path/to/presets.yaml
  JUMPHOST_DRY_RUN=1     never actually exec; return what would have run
  JUMPHOST_DEFAULT_TIMEOUT=30
"""
from __future__ import annotations

import dataclasses
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .executor import run_via_ssh
from .inventory import Inventory, PresetRegistry
from .safety import check_command


def _mode() -> str:
    return os.environ.get("JUMPHOST_MODE", "preset_only").strip().lower()


def make_server() -> FastMCP:
    inventory = Inventory.load()
    presets = PresetRegistry.load()
    dry_run = os.environ.get("JUMPHOST_DRY_RUN", "").lower() in ("1", "true")
    default_timeout = int(os.environ.get("JUMPHOST_DEFAULT_TIMEOUT", "30"))
    mode = _mode()

    mcp = FastMCP("srefix-jumphost")

    @mcp.tool()
    def list_hosts(tech: str = "", role: str = "", env: str = "") -> list[dict]:
        """List configured hosts. Filters: tech (e.g. 'postgres'), role, env."""
        return [
            {"host": h.name, "tags": h.tags}
            for h in inventory.filter(tech or None, role or None, env or None)
        ]

    @mcp.tool()
    def list_presets(tech: str = "") -> list[dict]:
        """List available preset commands. Filter by tech (e.g. 'postgres')."""
        return [
            {
                "tech": p.tech,
                "name": p.name,
                "description": p.description,
                "allowed_roles": p.allowed_roles,
                "allowed_args": p.allowed_args,
                "timeout": p.timeout,
            }
            for p in presets.list_for_tech(tech or None)
        ]

    @mcp.tool()
    def describe_preset(tech: str, name: str) -> dict:
        """Show full preset definition including the command template."""
        p = presets.get(tech, name)
        if p is None:
            return {"error": f"no preset {tech}/{name}"}
        return dataclasses.asdict(p)

    @mcp.tool()
    def run_safe(host: str, tech: str, preset_name: str, args: dict = None) -> dict:
        """Execute a pre-approved preset command on a host. The preferred entry point.

        - Validates the host's role against `allowed_roles`
        - Substitutes args only against `allowed_args` (with shell-metacharacter denial)
        - Always honors the preset's timeout
        """
        h = inventory.get(host)
        if h is None:
            return {"error": f"host '{host}' not in inventory"}
        p = presets.get(tech, preset_name)
        if p is None:
            return {"error": f"no preset {tech}/{preset_name}"}
        if p.allowed_roles and h.tags.get("role") not in p.allowed_roles:
            return {
                "error": f"host '{host}' role={h.tags.get('role')!r} not in preset's "
                         f"allowed_roles={p.allowed_roles}",
            }
        try:
            command = PresetRegistry.render(p, args)
        except ValueError as e:
            return {"error": f"preset render failed: {e}"}
        result = run_via_ssh(host, command, timeout=p.timeout, dry_run=dry_run)
        return dataclasses.asdict(result)

    @mcp.tool()
    def tail(host: str, file: str, lines: int = 200, grep: str = "") -> dict:
        """Tail a remote file (last N lines). Optional grep filter (read-only fixed string).

        Implemented as a safe `tail -n <lines> <file> | grep -F <grep>` — no shell expansion.
        """
        if any(c in file for c in "`$;&|<>(){}\"'\\\n\r\t"):
            return {"error": "file path contains forbidden characters"}
        if any(c in grep for c in "`$;&|<>(){}\"'\\\n\r\t"):
            return {"error": "grep pattern contains forbidden characters"}
        cmd = f"tail -n {int(lines)} {file}"
        if grep:
            cmd += f" | grep -F {grep!r}".replace("'", '"')
        result = run_via_ssh(host, cmd, timeout=default_timeout, dry_run=dry_run)
        return dataclasses.asdict(result)

    if mode != "preset_only":

        @mcp.tool()
        def run(host: str, command: str, timeout: int = 0) -> dict:
            """Execute an arbitrary command on a host (only when JUMPHOST_MODE != preset_only).

            With JUMPHOST_MODE=filtered_arbitrary, the command is checked against
            a denylist of destructive patterns (rm -rf, DROP TABLE, kubectl delete, ...).
            With JUMPHOST_MODE=unrestricted, no filtering — pair with an external
            approval gate before exposing this in production.
            """
            h = inventory.get(host)
            if h is None:
                return {"error": f"host '{host}' not in inventory"}
            if mode == "filtered_arbitrary":
                check = check_command(command)
                if not check.allowed:
                    return {
                        "error": f"command rejected by safety filter: {check.reason}",
                        "matched_pattern": check.matched_pattern,
                    }
            t = timeout or default_timeout
            result = run_via_ssh(host, command, timeout=t, dry_run=dry_run)
            return dataclasses.asdict(result)

    @mcp.tool()
    def server_info() -> dict:
        """Server configuration overview (mode, inventory size, preset count)."""
        return {
            "mode": mode,
            "dry_run": dry_run,
            "default_timeout": default_timeout,
            "host_count": len(inventory.hosts),
            "preset_count": len(presets.presets),
            "tools": ["list_hosts", "list_presets", "describe_preset", "run_safe", "tail",
                      *(["run"] if mode != "preset_only" else []),
                      "server_info"],
        }

    return mcp


def run() -> None:
    make_server().run()
