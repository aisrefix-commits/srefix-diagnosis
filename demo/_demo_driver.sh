#!/usr/bin/env bash
# Driver script run inside asciinema rec — produces the demo terminal output.
# Reads SCENARIO and PROMPT from environment.
set -e
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

clear
echo
echo "═════════════════════════════════════════════════════════"
echo "  srefix-diagnosis demo  ·  scenario: ${SCENARIO}"
echo "═════════════════════════════════════════════════════════"
echo
sleep 1.5

echo "── Installed MCP servers ──"
ls /Users/albericliu/Library/Python/3.10/bin/srefix-* 2>/dev/null | xargs -n1 basename | nl
total=$(ls /Users/albericliu/Library/Python/3.10/bin/srefix-* 2>/dev/null | wc -l | tr -d ' ')
configured=$(grep -c '"command"' "${DEMO_DIR}/claude_config_demo.json")
echo
echo "  total: ${total} MCP commands on PATH"
echo "  configured in claude_config_demo.json: ${configured}"
echo
sleep 4

echo "── Production alert ──"
echo "  ${PROMPT}"
echo
sleep 3

echo "── Invoking Claude (streaming tool calls live) ──"
echo "\$ claude --print --output-format=stream-json --verbose [...] | _stream_pretty.py"
echo
sleep 1

claude --print --output-format stream-json --verbose \
       --mcp-config "${DEMO_DIR}/claude_config_demo.json" \
       --dangerously-skip-permissions \
       "${PROMPT}" \
  | python3 "${DEMO_DIR}/_stream_pretty.py"

echo
echo "═════════════════════════════════════════════════════════"
echo "  ✓ Diagnosis complete · Claude orchestrated real MCPs"
echo "═════════════════════════════════════════════════════════"
sleep 2
