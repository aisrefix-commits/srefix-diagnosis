"""FastMCP server exposing the metric verifier as MCP tools.

Tools:
  verify_manual(tech, manual_path)  — verify one manual against its whitelist
  audit_corpus(agents_dir)          — run verifier across all 250 manuals
  list_whitelisted_techs()          — which techs currently have a whitelist
  whitelist_info(tech)              — provenance of a tech's whitelist

The verifier ships with a small set of built-in whitelists captured from real
exporters; users can extend via PR (see README → "Verify accuracy").
"""
from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .verifier import (
    audit_corpus,
    list_whitelisted_techs,
    load_whitelist,
    verify_manual,
)


def make_server() -> FastMCP:
    mcp = FastMCP("srefix-verify")

    @mcp.tool()
    def verify_manual_tool(tech: str, manual_path: str) -> dict:
        """Verify a single manual.

        - tech: short name, e.g. "vitess" / "pulsar"
        - manual_path: absolute path to the .md file

        Returns counts + the list of metric names that don't exist in the
        tech's exporter whitelist (likely hallucinations).
        """
        return verify_manual(tech, Path(manual_path)).to_dict()

    @mcp.tool()
    def audit_corpus_tool(agents_dir: str) -> dict:
        """Run the verifier across an entire agents/ directory.

        - agents_dir: absolute path to srefix-diagnosis/agents/

        Returns aggregate stats: how many manuals are covered by a whitelist,
        how many metric refs were checked, total flagged.
        """
        return audit_corpus(Path(agents_dir))

    @mcp.tool()
    def list_whitelisted_techs_tool() -> list[str]:
        """List techs that currently have a metric-name whitelist."""
        return list_whitelisted_techs()

    @mcp.tool()
    def whitelist_info(tech: str) -> dict:
        """Show provenance metadata for a tech's whitelist (source, capture date, size)."""
        names, source = load_whitelist(tech)
        return {
            "tech": tech,
            "source": source,
            "metric_count": len(names),
            "exists": bool(names),
        }

    return mcp


def run() -> None:
    make_server().run()


if __name__ == "__main__":
    run()
