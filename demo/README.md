# srefix-diagnosis Demo

This directory contains everything needed to demo the srefix-diagnosis stack
without a real production environment. It uses **5 incident scenarios** sourced
from a 1000-scenario SRE benchmark suite, plus a **mock-telemetry MCP** that
plays the role of Prometheus / Loki / SSH-jumphost during the demo.

## What you'll see

A 30-second clip of Claude Desktop using srefix-diagnosis MCPs to diagnose:

```
User: "Production alert: MongoDB rs.election_count=5 on user-db-prod,
       primary stepdown detected"

Claude Desktop:
  ▸ 🔧 diag-mongo.diagnose_symptom("election storm primary stepdown")
      → matched: 'Replica Set Election Storm' (confidence 0.85)
  ▸ 🔧 diag-mongo.extract_diagnostic_queries(...)
      → 5 queries (PromQL + SQL)
  ▸ 🔧 srefix-mock-telemetry.prom_range_query(mongodb_rs_election_count)
      → step: 0 → 5 at t=150s (5 elections in 2 min)
  ▸ 🔧 srefix-mock-telemetry.jumphost_run_safe(preset=mongo-rs-status)
      → member[1] state: STEPPED_DOWN
  ▸ Final diagnosis: Network flap → election storm. Recommend
    increasing electionTimeoutMillis to 10s + setting priority.

✓ Matches expected diagnosis: keywords [election, primary, replica, stepdown]
```

## The 5 demo scenarios

| ID | Difficulty | Tech | Why selected |
|---|---|---|---|
| `nginx-502-upstream-001` | Basic | nginx | Universally familiar; quick to grasp |
| `mongodb-replicaset-election-001` | Advanced | mongo | Multi-host, election storm, visually exciting |
| `etcd-disk-latency-001` | Advanced | etcd → k8s | **Cross-tech cascade** — shows `expand_to_dependents` |
| `dns-resolution-failure-001` | Advanced | coredns | Common on-call pain |
| `cassandra-gc-pause-001` | Intermediate | cassandra | JVM-heavy, multi-step diagnosis |

Each scenario in `scenarios.json` includes:
- `input_alerts` — what the user pastes / what an alerting system would page you with
- `expected_diagnosis` — ground truth (root_cause_category, keywords, confidence)
- `expected_runbook` / `expected_actions` — what should be selected
- `actual_timeline` — the real chain of events
- `lessons_learned` — postmortem-style notes
- `canned_telemetry` — what the mock-telemetry MCP returns when Claude calls
  prom/loki/jumphost during this scenario

## How to run the demo locally

### 1. Install all the MCPs

```bash
# In the parent srefix-diagnosis/ directory
cd /path/to/srefix-diagnosis

# 250 diag-{tech} MCPs (or filter to demo subset)
cd mcp
python3 generate.py --techs nginx mongo etcd coredns cassandra k8s
pip install -e .

# Explorer MCP
cd ../explorer-mcp && pip install -e .

# Mock telemetry MCP (this directory)
cd ../demo/mock-telemetry-mcp && pip install -e .
```

### 2. Configure Claude Desktop

Copy the contents of `claude_config_demo.json` into:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Restart Claude Desktop. You should see the MCPs appear in the tools list.

### 3. Switch the active scenario (optional)

```bash
# In the Claude Desktop config, change DEMO_SCENARIO_ID:
"env": { "DEMO_SCENARIO_ID": "etcd-disk-latency-001" }

# Or use the runtime tool inside Claude:
#   "Switch the demo scenario to etcd-disk-latency-001"
#   (Claude calls srefix-mock-telemetry.set_scenario for you)
```

### 4. Drive the demo

In Claude Desktop, paste one of these prompts (matching your active scenario):

| Scenario | Demo prompt |
|---|---|
| nginx-502 | `Production alert: Nginx 502 error rate hit 25% on backend-api upstream. Investigate.` |
| mongo-election | `Alert: MongoDB rs.election_count=5 on user-db-prod, primary stepdown detected. Diagnose.` |
| etcd-disk | `Critical: etcd WAL fsync p99 800ms on k8s-prod, kube-apiserver latency 12s. Find root cause.` |
| dns | `CoreDNS SERVFAIL spike, K8s services can't resolve. Diagnose.` |
| cassandra-gc | `cassandra-03: GC pause 8.5s, cluster reads timing out. Diagnose.` |

Claude will call the MCP tools and produce a diagnosis matching the scenario's
`expected_diagnosis` keywords.

## Run the benchmark (5 scenarios)

```bash
# Run all 5 scenarios headlessly through Claude Code CLI
python3 demo/run_benchmark.py

# Filter by glob
python3 demo/run_benchmark.py 'mongo*' 'nginx*'

# Extend per-scenario timeout (default 300s)
python3 demo/run_benchmark.py --timeout 600
```

For each scenario, `run_benchmark.py`:
1. Sets `DEMO_SCENARIO_ID` so mock-telemetry-mcp serves the right canned data
2. Invokes `claude --print` (headless) with the demo prompt + MCP config
3. Captures Claude's diagnosis output
4. Scores: how many of `expected_diagnosis.keywords` appear in the output
5. Marks `pass` if keyword coverage ≥ scenario's `min_confidence`

Output:
```
| Scenario              | Difficulty   | Pass | Duration | Keywords matched |
|-----------------------|--------------|------|----------|------------------|
| Nginx 502 Bad Gateway | Basic        | ✓    | 12.3s    | 4/4 (100%)       |
| MongoDB RS Election   | Advanced     | ✓    | 18.7s    | 4/4 (100%)       |
| etcd disk latency     | Advanced     | ✓    | 22.1s    | 4/5 (80%)        |
| DNS resolution failure| Advanced     | ✗    | 14.5s    | 2/4 (50%)        |
| Cassandra GC pause    | Intermediate | ✓    | 16.2s    | 4/5 (80%)        |

Pass rate: 4/5 (80%) · avg duration: 16.8s
```

Detailed JSON per-scenario report saved to `demo/benchmark_report.json`.

**Prereq**: `claude` CLI on PATH ([Claude Code](https://claude.com/claude-code)).

## What's mocked vs real

| Component | Source | Notes |
|---|---|---|
| `diag-{tech}` MCPs | **Real** | The 250 diagnostic manuals + tools |
| `srefix-explorer` | **Real** | Tier-2/3 fallback + dependency graph |
| `prom` / `loki` / `jumphost` | **Mocked** | `srefix-mock-telemetry` returns canned scenario data |
| Claude reasoning | **Real** | Genuine LLM orchestration over the mocked telemetry |

The mock returns deterministic data per scenario, so demos are reproducible.
For a real production run, swap `srefix-mock-telemetry` out for the real
`srefix-prom` / `srefix-loki` / `srefix-jumphost` MCPs (configure with your
Prometheus URL / SSH config / etc.) — Claude won't notice the difference.

## Demo provenance

The 5 scenarios were selected from a 1000-scenario SRE benchmark suite
focused on common production incidents. Each scenario was originally crafted
as a fault-injection / replay specification with ground-truth diagnosis
labels. They are reproduced here under fair use for demonstration purposes;
attribution and sources are tracked in the parent `NOTICE` file.
