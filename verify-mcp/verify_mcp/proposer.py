"""Proposer: build the LLM prompt for fix-map drafting.

Two modes:

  print_prompt(tech, agents_dir) -> str
      Returns the prompt text. User can pipe to any LLM:
          srefix-fix propose vitess --print > prompt.txt
          claude --print --allowedTools "Read,Bash(grep:*)" "$(cat prompt.txt)"

  run_headless(tech, agents_dir, output_path)
      Spawns `claude --print` as subprocess with read-only allowedTools,
      captures the YAML draft, writes to output_path. Requires `claude`
      on PATH; falls back to printing the prompt if not.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .verifier import verify_manual

_PROMPT_TEMPLATE = """\
You are auditing the srefix-diagnosis SRE manual for `{tech}`. The manual was
LLM-synthesized; it likely contains hallucinated metric names. Your job is
to draft a YAML fix-map of metric-name corrections.

## What you have access to

- The manual: `{manual_path}`
- Verifier output (metric names referenced in PromQL contexts that don't
  match a known whitelist — likely hallucinations): see flagged list below
- Web access for cross-checking against the tech's authoritative source
  (GitHub repo, official docs, exporter source code)

## Verifier-flagged metrics

{flagged_block}

## Your task

For each flagged metric, decide:

  (a) Is it real? (verify by checking the tech's exporter source code or
      a real /metrics dump)
  (b) If NOT real: what is the actual metric name that the manual should
      have referenced?
  (c) If you cannot determine the correct replacement with high confidence,
      do NOT include the entry — leave it for human triage.

## Output format

Emit ONLY a YAML document, no prose, no markdown fences. Schema:

```
tech: {tech}
proposed_at: 2026-04-27
proposed_by: claude (claude-opus-4-7)
authority: <github URL + version, e.g. github.com/vitessio/vitess @ v18.0.2>
notes: ""
fixes:
  - old: <hallucinated_name>
    new: <correct_name>
    rationale: "<one-line reason — cite the source file or doc URL>"
    confirmed_by: ""        # leave empty; human reviewer fills this in
    occurrences_expected: <int>   # how many times you observed it in the manual
```

## Rules

- ONLY include fixes you are 95%+ confident in.
- The `new` field MUST be a metric you verified exists in the real exporter
  (not another guess).
- If the `old` name is real (just not in our partial whitelist), DO NOT
  include it — those need a whitelist update, not a content fix.
- Skip any name where the right replacement is ambiguous.
- Do not include any text before or after the YAML.
"""


def _format_flagged(tech: str, agents_dir: Path) -> tuple[str, str]:
    """Return (manual_path_str, flagged_block_str)."""
    candidates = [agents_dir / f"{tech}-agent.md", agents_dir / f"{tech}.md"]
    manual = next((c for c in candidates if c.exists()), candidates[0])
    result = verify_manual(tech, manual)
    if not result.has_whitelist:
        block = (f"(no whitelist for `{tech}` — verifier extracted "
                 f"{result.metrics_referenced} metric refs but cannot pre-flag. "
                 f"Use Read/Grep to inspect the manual yourself.)")
    elif not result.findings:
        block = "(no flagged metrics — manual passes the verifier cleanly.)"
    else:
        lines = []
        for f in result.findings:
            preview_lines = ",".join(str(x) for x in f.line_numbers[:5])
            more = "" if len(f.line_numbers) <= 5 else f" (+{len(f.line_numbers)-5} more)"
            lines.append(f"  - {f.name}  (×{f.occurrences} at lines {preview_lines}{more})")
        block = "\n".join(lines)
    return str(manual), block


def print_prompt(tech: str, agents_dir: Path) -> str:
    manual_path, flagged_block = _format_flagged(tech, agents_dir)
    return _PROMPT_TEMPLATE.format(
        tech=tech, manual_path=manual_path, flagged_block=flagged_block,
    )


def run_headless(tech: str, agents_dir: Path, output_path: Path,
                 allowed_tools: str = "Read,Bash(grep:*),Bash(find:*),WebFetch,WebSearch",
                 timeout_seconds: int = 600) -> int:
    """Spawn `claude --print` and write its YAML output to output_path.

    Returns 0 on success, non-zero on error. Falls back to printing the
    prompt + instructions if `claude` is not on PATH.
    """
    if not shutil.which("claude"):
        prompt = print_prompt(tech, agents_dir)
        sys.stderr.write(
            "claude CLI not found on PATH — emitting prompt to stdout instead.\n"
            "Pipe to your LLM of choice and save the YAML output:\n\n"
            f"  srefix-fix propose {tech} --print > prompt.txt\n"
            f"  claude --print --allowedTools \"{allowed_tools}\" "
            f"\"$(cat prompt.txt)\" > {output_path}\n\n"
        )
        sys.stdout.write(prompt)
        return 1

    prompt = print_prompt(tech, agents_dir)
    cmd = [
        "claude", "--print",
        "--allowedTools", allowed_tools,
        prompt,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"claude --print timed out after {timeout_seconds}s\n")
        return 124
    if result.returncode != 0:
        sys.stderr.write(f"claude --print exited {result.returncode}\n")
        sys.stderr.write(result.stderr)
        return result.returncode
    output_path.write_text(result.stdout)
    return 0
