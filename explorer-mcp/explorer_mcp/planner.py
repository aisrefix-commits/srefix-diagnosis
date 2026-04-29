"""Build a fallback exploration plan from symptom + tech (+ optional cluster/host)."""
from __future__ import annotations

import copy
import json
from typing import Optional

from .categorizer import categorize
from .templates import GENERIC_TEMPLATES, TECH_TEMPLATES


def _substitute(template: dict, ctx: dict[str, str]) -> dict:
    """Recursively substitute {placeholders} in string values of a step template.

    Uses str.format_map with a defaultdict so missing keys → empty string,
    keeping plans usable when caller didn't provide every context var.
    """
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return ""

    safe = _SafeDict(ctx)
    out = copy.deepcopy(template)

    def walk(node):
        if isinstance(node, str):
            try:
                return node.format_map(safe)
            except (ValueError, IndexError):
                return node
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(out)


def build_plan(symptom: str, tech: str = "", cluster_id: str = "",
               host_pattern: str = "") -> dict:
    """Return a structured exploration plan covering each matched category."""
    categories = categorize(symptom)
    ctx = {
        "tech": tech or "",
        "cluster_id": cluster_id or "",
        "host_pattern": host_pattern or ".*",
    }

    steps: list[dict] = []
    seen: set[str] = set()
    tech_specific = TECH_TEMPLATES.get(tech, {}) or {}

    for cat in categories:
        # Tech-specific overrides first, then generic
        for source_label, templates in (
            (f"tech:{tech}", tech_specific.get(cat, [])),
            ("generic", GENERIC_TEMPLATES.get(cat, [])),
        ):
            for tpl in templates:
                filled = _substitute(tpl, ctx)
                key = json.dumps(
                    {"mcp": filled.get("mcp"), "tool": filled.get("tool"),
                     "args": filled.get("args", {})},
                    sort_keys=True,
                )
                if key in seen:
                    continue
                seen.add(key)
                steps.append({
                    **filled,
                    "step": len(steps) + 1,
                    "category": cat,
                    "source": source_label,
                })

    return {
        "symptom": symptom,
        "tech": tech or None,
        "cluster_id": cluster_id or None,
        "host_pattern": host_pattern or None,
        "categories_matched": categories,
        "step_count": len(steps),
        "steps": steps,
        "if_still_unclear": (
            "If steps return no smoking gun, do free exploration: "
            "(1) call srefix-prom.label_values('__name__') to enumerate available metrics, "
            "(2) grep names matching the symptom, "
            "(3) call srefix-loki.labels() / label_values('app') to find adjacent log streams, "
            "(4) widen the time window to -2h or -4h."
        ),
    }
