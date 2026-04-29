#!/usr/bin/env bash
# Record a single-scenario demo asciinema cast and convert to GIF.
#
# Includes a preamble showing how many MCPs are installed + which ones,
# then runs Claude on the chosen scenario and shows the diagnosis output.
#
# Usage:  ./record_demo.sh [scenario-id]
# Default scenario: nginx-502-upstream-001 (fastest, ~60s)
#
# Produces:
#   demo/demo.cast            asciinema recording (replayable in terminal)
#   demo/demo.gif             rendered GIF (for README)
#
# Prereqs:
#   - asciinema (pip)
#   - agg (brew)
#   - claude (Claude Code CLI)
#   - All demo MCPs (srefix-diag-* / srefix-explorer / srefix-mock-telemetry)
set -euo pipefail

SCENARIO="${1:-nginx-502-upstream-001}"
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAST="$DEMO_DIR/demo.cast"
GIF="$DEMO_DIR/demo.gif"
DRIVER="$DEMO_DIR/_demo_driver.sh"

# Make sure demo MCPs are on PATH
export PATH="/Users/albericliu/Library/Python/3.10/bin:$PATH"
export DEMO_SCENARIO_ID="$SCENARIO"
export SCENARIO

# Pick the matching demo prompt
case "$SCENARIO" in
  nginx-502-upstream-001)
    export PROMPT="Production alert: Nginx 502 error rate hit 25% on backend-api upstream. Investigate using available tools and explain the root cause." ;;
  mongodb-replicaset-election-001)
    export PROMPT="Alert: MongoDB rs.election_count=5 on user-db-prod, primary stepdown detected. Diagnose using available diag-* and telemetry tools." ;;
  etcd-disk-latency-001)
    export PROMPT="Critical: etcd WAL fsync p99 is 800ms on k8s-prod, and kube-apiserver request latency is 12s. Find the root cause." ;;
  dns-resolution-failure-001)
    export PROMPT="CoreDNS SERVFAIL spike: K8s services can't resolve names. Diagnose." ;;
  cassandra-gc-pause-001)
    export PROMPT="cassandra-03 has an 8.5s GC pause; cluster reads are timing out. Diagnose." ;;
  *)
    echo "Unknown scenario: $SCENARIO" >&2; exit 1 ;;
esac

echo "Recording demo for scenario: $SCENARIO"
echo

rm -f "$CAST"
asciinema rec --quiet --cols=120 --rows=36 --idle-time-limit=3 \
  --command "bash $DRIVER" \
  "$CAST"

echo
echo "✓ recording saved to: $CAST"

# Render to GIF — small font + slight speed-up to fit ~14 tool calls
# in ~1MB while staying readable.
echo "Rendering GIF (font=11, speed=1.3, theme=monokai)..."
agg --font-size 11 --speed 1.3 --theme monokai "$CAST" "$GIF"

# Compress — gifsicle -O3 + 16-color palette + lossy=200 brings us
# from ~3MB raw to ~1MB without visible quality loss for terminal text.
if command -v gifsicle >/dev/null; then
    echo "Compressing with gifsicle (-O3 --colors 16 --lossy=200)..."
    gifsicle -O3 --colors 16 --lossy=200 "$GIF" -o "${GIF}.tmp" \
      && mv "${GIF}.tmp" "$GIF"
fi

echo
echo "✓ GIF saved to: $GIF"
ls -lh "$CAST" "$GIF"
