#!/usr/bin/env python3
"""Pretty-print Claude Code's stream-json output for the demo GIF.

Reads JSONL from stdin (one event per line) and emits terminal-friendly
lines as events arrive — so viewers see each MCP tool call land instead
of staring at a black screen for 60s while Claude runs in --print mode.

Event shapes (Claude Code 2.x stream-json):
  {"type":"system","subtype":"init",...}
  {"type":"assistant","message":{"content":[{"type":"text","text":"..."} | {"type":"tool_use","name":"...","input":{...}}]}}
  {"type":"user","message":{"content":[{"type":"tool_result","content":[...]}]}}
  {"type":"result","subtype":"success","result":"...","duration_ms":...}

We coalesce text deltas, summarize tool calls, and show abbreviated tool
results so the demo stays readable in a 120-col terminal.
"""
from __future__ import annotations

import json
import sys

# ANSI colors for the GIF terminal renderer
DIM = "\x1b[2m"
GRAY = "\x1b[90m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"


def trunc(s: str, n: int = 100) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def show_text(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    # First line is enough for the streaming display
    first_line = text.split("\n", 1)[0]
    print(f"{CYAN}🤖 Claude:{RESET} {trunc(first_line, 90)}", flush=True)


def show_tool_use(block: dict) -> None:
    name = block.get("name", "?")
    inp = block.get("input", {}) or {}
    # Keep arg display compact
    arg_str = ", ".join(f"{k}={trunc(json.dumps(v) if not isinstance(v, str) else v, 40)}"
                        for k, v in list(inp.items())[:3])
    if len(inp) > 3:
        arg_str += ", …"
    print(f"  {YELLOW}→ 🔧{RESET} {BOLD}{name}{RESET}({GRAY}{arg_str}{RESET})", flush=True)


def show_tool_result(block: dict) -> None:
    content = block.get("content", "")
    if isinstance(content, list):
        text_parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
        text = " ".join(text_parts)
    else:
        text = str(content)
    text = trunc(text, 110)
    print(f"     {GREEN}←{RESET} {GRAY}{text}{RESET}", flush=True)


def show_result(ev: dict) -> None:
    result = ev.get("result", "") or ""
    duration_ms = ev.get("duration_ms", 0)
    print()
    print(f"{MAGENTA}{'═' * 70}{RESET}")
    print(f"{BOLD}{MAGENTA}  ✓ Diagnosis complete{RESET}{GRAY} "
          f"({ev.get('num_turns', '?')} turns, {duration_ms/1000:.1f}s){RESET}")
    print(f"{MAGENTA}{'═' * 70}{RESET}")
    # Show the first ~10 lines of the diagnosis as a summary
    lines = result.split("\n")
    for line in lines[:12]:
        if line.strip():
            print(f"  {line}")
    if len(lines) > 12:
        print(f"  {GRAY}…({len(lines)-12} more lines in full diagnosis){RESET}")


def main() -> int:
    n_tool_calls = 0
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue

        typ = ev.get("type")
        if typ == "system":
            sub = ev.get("subtype", "")
            if sub == "init":
                tools = ev.get("tools") or []
                mcps = [t for t in tools if t.startswith("mcp__")]
                print(f"{BLUE}⚙  Session ready{RESET}{GRAY}: "
                      f"{len(mcps)} MCP tools registered, "
                      f"model={ev.get('model','?')}{RESET}", flush=True)

        elif typ == "assistant":
            msg = ev.get("message", {}) or {}
            for block in msg.get("content", []) or []:
                btype = block.get("type")
                if btype == "text":
                    show_text(block.get("text", ""))
                elif btype == "tool_use":
                    n_tool_calls += 1
                    show_tool_use(block)

        elif typ == "user":
            msg = ev.get("message", {}) or {}
            for block in msg.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    show_tool_result(block)

        elif typ == "result":
            show_result(ev)
            print(f"{GRAY}  total tool calls: {n_tool_calls}{RESET}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
