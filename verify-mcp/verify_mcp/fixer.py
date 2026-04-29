"""Pure-sed metric-name fixer.

Reads a fix_map YAML (proposer output, human-reviewed), validates schema,
and applies literal-string replacements to the corresponding manual.

Design contract:
  - The applier is **deterministic and LLM-free**. CI runs it 1000 times
    and gets the same result.
  - Only entries with non-empty `confirmed_by` are applied. Unreviewed
    entries are warned about and skipped.
  - Replacements use word boundaries (\\b ... \\b) so `vtgate_queries_error`
    does NOT match inside `vtgate_queries_error_total`.
  - --dry-run shows the diff; the real run writes in place.

YAML schema:

    tech: vitess
    proposed_at: 2026-04-27
    proposed_by: claude --print (claude-opus-4-7)
    authority: github.com/vitessio/vitess @ v18.0.2
    notes: ""
    fixes:
      - old: vtgate_queries_error
        new: vtgate_api_error_counts
        rationale: "Not in vtgate /metrics. Real counter is ..."
        confirmed_by: alberic        # required for apply
        occurrences_expected: 21     # safety check
"""
from __future__ import annotations

import difflib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Fix:
    old: str
    new: str
    rationale: str = ""
    confirmed_by: str = ""
    occurrences_expected: Optional[int] = None


@dataclass
class FixMap:
    tech: str
    proposed_at: str = ""
    proposed_by: str = ""
    authority: str = ""
    notes: str = ""
    fixes: list[Fix] = field(default_factory=list)


@dataclass
class ApplyResult:
    tech: str
    manual_path: str
    applied: list[tuple[str, str, int]]  # (old, new, occurrences)
    skipped_unconfirmed: list[str]
    skipped_not_found: list[str]
    mismatched_counts: list[tuple[str, int, int]]  # (old, expected, actual)
    diff: str


def load_fix_map(path: Path) -> FixMap:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "tech" not in data or "fixes" not in data:
        raise ValueError(f"{path}: missing required fields 'tech' and 'fixes'")
    fixes = []
    for i, raw in enumerate(data.get("fixes") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: fixes[{i}] is not a mapping")
        if "old" not in raw or "new" not in raw:
            raise ValueError(f"{path}: fixes[{i}] missing 'old' or 'new'")
        fixes.append(Fix(
            old=str(raw["old"]),
            new=str(raw["new"]),
            rationale=str(raw.get("rationale") or ""),
            confirmed_by=str(raw.get("confirmed_by") or ""),
            occurrences_expected=raw.get("occurrences_expected"),
        ))
    return FixMap(
        tech=str(data["tech"]),
        proposed_at=str(data.get("proposed_at") or ""),
        proposed_by=str(data.get("proposed_by") or ""),
        authority=str(data.get("authority") or ""),
        notes=str(data.get("notes") or ""),
        fixes=fixes,
    )


def find_manual(tech: str, agents_dir: Path) -> Optional[Path]:
    for candidate in (agents_dir / f"{tech}-agent.md", agents_dir / f"{tech}.md"):
        if candidate.exists():
            return candidate
    return None


def _replace_word_boundary(text: str, old: str, new: str) -> tuple[str, int]:
    """Replace `old` with `new` only at word boundaries; return (new_text, count)."""
    pattern = r"\b" + re.escape(old) + r"\b"
    new_text, count = re.subn(pattern, new, text)
    return new_text, count


def apply_fix_map(fix_map: FixMap, agents_dir: Path, dry_run: bool = False) -> ApplyResult:
    manual = find_manual(fix_map.tech, agents_dir)
    if manual is None:
        return ApplyResult(
            tech=fix_map.tech, manual_path="",
            applied=[], skipped_unconfirmed=[],
            skipped_not_found=[f.old for f in fix_map.fixes],
            mismatched_counts=[], diff="",
        )

    original = manual.read_text()
    text = original
    applied: list[tuple[str, str, int]] = []
    skipped_unconfirmed: list[str] = []
    skipped_not_found: list[str] = []
    mismatched: list[tuple[str, int, int]] = []

    for fix in fix_map.fixes:
        if not fix.confirmed_by.strip():
            skipped_unconfirmed.append(fix.old)
            continue
        text, count = _replace_word_boundary(text, fix.old, fix.new)
        if count == 0:
            skipped_not_found.append(fix.old)
            continue
        if fix.occurrences_expected is not None and count != fix.occurrences_expected:
            mismatched.append((fix.old, fix.occurrences_expected, count))
        applied.append((fix.old, fix.new, count))

    diff = ""
    if text != original:
        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"a/{manual.name}",
            tofile=f"b/{manual.name}",
            n=1,
        ))

    if not dry_run and text != original:
        manual.write_text(text)

    return ApplyResult(
        tech=fix_map.tech, manual_path=str(manual),
        applied=applied, skipped_unconfirmed=skipped_unconfirmed,
        skipped_not_found=skipped_not_found, mismatched_counts=mismatched,
        diff=diff,
    )


def validate_fix_map(path: Path) -> list[str]:
    """Return a list of validation problems, empty if all good."""
    try:
        fm = load_fix_map(path)
    except (ValueError, yaml.YAMLError) as e:
        return [f"schema error: {e}"]
    problems = []
    if not fm.authority:
        problems.append("missing 'authority' (required so reviewers know the source of truth)")
    seen_olds = set()
    for i, fix in enumerate(fm.fixes):
        if fix.old in seen_olds:
            problems.append(f"fixes[{i}]: duplicate 'old': {fix.old!r}")
        seen_olds.add(fix.old)
        if fix.old == fix.new:
            problems.append(f"fixes[{i}]: old == new ({fix.old!r}) — no-op")
    return problems
