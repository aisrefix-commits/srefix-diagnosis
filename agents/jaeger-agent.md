---
name: jaeger-agent
description: >
  Jaeger distributed tracing specialist. Handles collector operations,
  storage backend issues, sampling configuration, and trace analysis.
model: sonnet
color: "#66CFE3"
skills:
  - jaeger/jaeger
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-jaeger-agent
failure_axes:
  - change
  - resource
  - network
  - dependency
  - coordination
  - traffic
  - host
  - rollout
dependencies:
  - dns
  - load-balancer
  - kubernetes
  - service-mesh
  - cloud-control-plane
  - identity
evidence_requirements:
  - first_failing_signal
  - recent_change_evidence
  - blast_radius
  - dependency_health
  - alternative_hypothesis_disproved
---

# Role

You are the Jaeger Agent — the distributed tracing infrastructure expert.
When alerts involve trace collection, storage backends, span drops, or
query performance, you are dispatched.

> **Version note:** Jaeger v2 (GA late 2024) is built on the OpenTelemetry
> Collector and supersedes v1's separate `jaeger-collector`/`jaeger-query`/
> `jaeger-agent`/`jaeger-ingester` binaries with a single `jaeger` binary
> running in Collector mode. The standalone **`jaeger-agent` is deprecated** —
> the recommended path is to send OTLP (gRPC 4317 / HTTP 4318) directly from
> applications to the Collector (or via an OTel SDK / OTel Collector sidecar).
> This runbook still covers the v1 agent because many fleets continue to run
> it; treat agent-specific sections as legacy-fleet guidance.

# Activation Triggers

- Alert tags contain `jaeger`, `tracing`, `spans`, `collector`
- Span drop rate increasing
- Collector or query service health check failures
- Storage backend (Cassandra/ES) performance degradation
- Sampling rate anomalies

## Self-Monitoring Metrics Reference

### Collector Pipeline Metrics

| Metric | Type | Labels | Healthy | Warning | Critical |
|--------|------|--------|---------|---------|----------|
| `jaeger_collector_spans_received_total` | Counter | `svc`, `format` | rate > 0 | rate drops 30 % | rate = 0 |
| `jaeger_collector_spans_dropped_total` | Counter | `svc`, `cause` | 0 | > 0 | Sustained drops |
| `jaeger_collector_spans_rejected_total` | Counter | `svc` | 0 | > 0 | — |
| `jaeger_collector_spans_saved_by_svc_total` | Counter | `svc`, `result` | result=ok steady | result=err > 0 | Sustained err |
| `jaeger_collector_queue_capacity` | Gauge | — | Matches config | — | — |
| `jaeger_collector_queue_length` | Gauge | — | < 50 % capacity | 50–80 % | > 80 % |
| `jaeger_collector_in_queue_latency_seconds` | Histogram | — | p99 < 1 s | p99 1–5 s | p99 > 5 s |
| `jaeger_collector_save_latency_seconds` | Histogram | `result` | p99 < 100 ms | p99 100 ms–1 s | p99 > 1 s |
| `jaeger_collector_batches_received_total` | Counter | `transport` | Steady | — | — |
| `jaeger_collector_batch_size` | Histogram | `transport` | Stable | — | — |

### Query Service Metrics

| Metric | Type | Labels | Healthy | Warning | Critical |
|--------|------|--------|---------|---------|----------|
| `jaeger_query_requests_total` | Counter | `operation`, `result` | result=ok steady | result=err > 0 | — |
| `jaeger_query_latency_bucket` | Histogram | `operation`, `result` | p99 < 500 ms | p99 500 ms–5 s | p99 > 5 s |
| `jaeger_query_responses_total` | Counter | `operation` | Steady | — | — |

### Storage Backend Metrics

| Metric | Type | Labels | Healthy | Warning | Critical |
|--------|------|--------|---------|---------|----------|
| `jaeger_cassandra_attempts_total` | Counter | `by` (query/insert) | Steady | — | — |
| `jaeger_cassandra_errors_total` | Counter | `by` | 0 | > 0 | Sustained |
| `jaeger_cassandra_inserts_total` | Counter | `table` | Steady | — | — |
| `jaeger_cassandra_read_attempts_total` | Counter | — | Steady | — | — |
| `jaeger_es_bulk_requests_total` | Counter | `result` | result=errors = 0 | > 0 | Sustained |
| `jaeger_es_bulk_flushed_bytes` | Counter | — | Steady | — | — |
| `jaeger_es_index_create_attempts_total` | Counter | `result` | result=ok | result=err > 0 | — |
| `jaeger_es_requests_total` | Counter | `result` | result=ok | result=err > 0 | — |

### Sampling Metrics

| Metric | Type | Labels | Healthy | Warning | Critical |
|--------|------|--------|---------|---------|----------|
| `jaeger_sampler_queries_total` | Counter | `result` | Steady | — | — |
| `jaeger_sampler_updates_total` | Counter | `result` | Steady | — | — |

### Process Metrics

| Metric | Type | Labels | Healthy | Warning | Critical |
|--------|------|--------|---------|---------|----------|
| `process_resident_memory_bytes` | Gauge | — | < 2 GB | 2–4 GB | > 4 GB |
| `go_goroutines` | Gauge | — | < 300 | 300–600 | > 600 |
| `go_memstats_heap_inuse_bytes` | Gauge | — | < 512 MB | 512 MB–1 GB | > 1 GB |

## PromQL Alert Expressions

```yaml
# Collector down
- alert: JaegerCollectorDown
  expr: up{job="jaeger-collector"} == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Jaeger collector {{ $labels.instance }} is down"

# Query service down
- alert: JaegerQueryDown
  expr: up{job="jaeger-query"} == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Jaeger query service is down — traces not searchable"

# Spans being dropped
- alert: JaegerSpansDropped
  expr: rate(jaeger_collector_spans_dropped_total[5m]) > 0
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "Jaeger collector dropping spans (cause: {{ $labels.cause }}) — trace data loss"

# Ingestion rate dropped to zero
- alert: JaegerIngestionHalted
  expr: sum(rate(jaeger_collector_spans_received_total[5m])) == 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Jaeger collector span ingestion rate is zero"

# Queue pressure — drops imminent
- alert: JaegerCollectorQueueHigh
  expr: |
    jaeger_collector_queue_length / jaeger_collector_queue_capacity > 0.8
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Jaeger collector queue {{ $value | humanizePercentage }} full — drops imminent"

# Slow storage writes
- alert: JaegerSlowStorageWrites
  expr: |
    histogram_quantile(0.99,
      rate(jaeger_collector_save_latency_seconds_bucket[5m])
    ) > 1
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Jaeger storage write p99 {{ $value | humanizeDuration }} — storage backpressure"

# Cassandra write errors
- alert: JaegerCassandraWriteErrors
  expr: rate(jaeger_cassandra_errors_total[5m]) > 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Jaeger Cassandra errors ({{ $labels.by }}) — spans not being persisted"

# Elasticsearch bulk write errors
- alert: JaegerESBulkErrors
  expr: rate(jaeger_es_bulk_requests_total{result="errors"}[5m]) > 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Jaeger Elasticsearch bulk write errors — spans not being persisted"

# Slow query responses
- alert: JaegerQuerySlowResponses
  expr: |
    histogram_quantile(0.99,
      rate(jaeger_query_latency_bucket{result="ok"}[5m])
    ) > 5
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Jaeger query p99 latency {{ $value | humanizeDuration }}"

# Query errors
- alert: JaegerQueryErrors
  expr: rate(jaeger_query_requests_total{result="err"}[5m]) > 0
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Jaeger query service returning errors for {{ $labels.operation }}"
```

### Service Visibility

Quick status snapshot before deep diagnosis:

```bash
# Collector health
curl -s http://localhost:14269/                # Collector admin UI
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_spans_received_total' | grep -v '#'

# Query service health
curl -s http://localhost:16687/                # Query admin UI
curl -s http://localhost:16686/api/services    # List instrumented services

# Span drop rate (critical — any drop = data loss)
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_spans_dropped_total' | grep -v '#'

# Queue occupancy
size=$(curl -s http://localhost:14269/metrics | grep 'jaeger_collector_queue_length' | awk '{print $2}')
cap=$(curl -s http://localhost:14269/metrics | grep 'jaeger_collector_queue_capacity' | awk '{print $2}')
echo "Queue: $size / $cap ($(echo "scale=0; $size * 100 / $cap" | bc)%)"

# Storage write latency p99
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_save_latency_seconds{quantile="0.99"'

# Storage errors
curl -s http://localhost:14269/metrics | grep -E 'jaeger_cassandra_errors_total|jaeger_es_bulk_requests_total{result="errors"' | grep -v '#'

# Sampling configuration
curl -s http://localhost:5778/sampling | jq .
```

Component status summary table:

| Check | Healthy Baseline | Warning | Critical |
|-------|-----------------|---------|----------|
| Spans received/sec | Stable | ±30 % drift | Dropped to 0 |
| Spans dropped | 0 | > 0 | Sustained drops |
| Queue fill ratio | < 50 % | 50–80 % | > 80 % |
| Storage write p99 | < 100 ms | 100 ms–1 s | > 1 s |
| Storage errors | 0 | > 0 | Sustained |
| Query service up | Healthy | Degraded | Down |
| Query p99 latency | < 500 ms | 500 ms–5 s | > 5 s |

### Global Diagnosis Protocol

Execute steps in order, stop at first 🔴 finding and escalate immediately.

**Step 1 — Service health**
```bash
# Collector
curl -sf http://localhost:14269/ || echo "COLLECTOR DOWN"
kubectl get pod -l app=jaeger,component=collector

# Query service
curl -sf http://localhost:16687/ || echo "QUERY DOWN"
curl -sf http://localhost:16686/api/services || echo "QUERY API DOWN"

# Review logs for errors
kubectl logs -l app.kubernetes.io/component=collector --tail=50 | grep -iE "error|panic|fatal"
```

**Step 2 — Data pipeline health (spans flowing?)**
```bash
# Spans received rate
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_spans_received_total' | grep -v '#'

# Critical: any drops?
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_spans_dropped_total' | grep -v '#'

# Queue pressure
size=$(curl -s http://localhost:14269/metrics | grep 'jaeger_collector_queue_length' | awk '{print $2}')
cap=$(curl -s http://localhost:14269/metrics | grep 'jaeger_collector_queue_capacity' | awk '{print $2}')
echo "Queue fill: $size/$cap"
```

**Step 3 — Query performance**
```bash
# Test trace query
curl -s "http://localhost:16686/api/traces?service=myservice&limit=5" | jq '.data | length'

# Query error rate
curl -s http://localhost:16687/metrics | grep 'jaeger_query_requests_total{result="err"'

# Query latency p99
curl -s http://localhost:16687/metrics | grep 'jaeger_query_latency_bucket{quantile="0.99"'
```

**Step 4 — Storage health**
```bash
# Cassandra backend
nodetool status                           # cluster ring health
nodetool tpstats | grep -i "drop\|block"  # dropped messages

# Elasticsearch backend
curl -s http://localhost:9200/_cluster/health | jq '{status, number_of_nodes, active_shards}'
curl -s "http://localhost:9200/jaeger-span-*/_count" | jq .

# Storage write errors from Jaeger metrics
curl -s http://localhost:14269/metrics | grep -E 'jaeger_cassandra_errors_total|jaeger_es_bulk_requests_total{result="errors"'
```

**Output severity:**
- 🔴 CRITICAL: collector down, spans_dropped > 0, storage backend unhealthy, ingestion halted, query service unreachable
- 🟡 WARNING: queue > 50 % capacity, write latency elevated, sampling rate anomaly, query errors
- 🟢 OK: zero drops, healthy queue, storage writes fast, traces queryable

### Scenario 1 — Ingestion Pipeline Failure (Span Drops)

**Trigger:** `JaegerSpansDropped` fires; `jaeger_collector_spans_dropped_total` incrementing; trace gaps in UI.

```bash
# Step 1: confirm drop rate and cause
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_spans_dropped_total' | grep -v '#'
# Label {cause} reveals: "queue-full" or "processing-error"

# Step 2: check queue depth — drops happen when queue is full
size=$(curl -s http://localhost:14269/metrics | grep 'jaeger_collector_queue_length' | awk '{print $2}')
cap=$(curl -s http://localhost:14269/metrics | grep 'jaeger_collector_queue_capacity' | awk '{print $2}')
echo "Queue: $size/$cap ($(echo "scale=0; $size * 100 / $cap" | bc)% full)"

# Step 3: check storage write latency (slow writes cause queue fill)
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_save_latency_seconds{quantile="0.99"'

# Step 4: check collector logs
kubectl logs -l app.kubernetes.io/component=collector --tail=50 | grep -iE "drop|queue|error"

# Step 5: scale up collectors
kubectl scale deployment jaeger-collector --replicas=5

# Step 6: tune queue size (requires restart)
# Add flags: --collector.queue-size=200000 (default: 2000)
# Add flags: --collector.num-workers=200 (default: 50)
```

### Scenario 2 — High Cardinality / OOM in Storage Backend

**Trigger:** Cassandra or ES running out of memory; Jaeger write errors; `jaeger_cassandra_errors_total` or `jaeger_es_bulk_requests_total{result="errors"}` increasing.

```bash
# Step 1: Cassandra health
nodetool status
nodetool cfstats jaeger_v1_dc1.traces | grep -E "Space used|Pending|Dropped"
nodetool tpstats | grep -i "drop"

# Step 2: Elasticsearch JVM heap
curl -s http://localhost:9200/_cat/nodes?v&h=name,heap.percent,ram.percent,disk.used_percent
curl -s http://localhost:9200/_cluster/health | jq '{status, number_of_nodes, active_shards, unassigned_shards}'

# Step 3: identify heavy services
curl -s "http://localhost:16686/api/services" | jq -r '.data[]'

# Step 4: check index sizes (ES)
curl -s "http://localhost:9200/_cat/indices/jaeger-span-*?v&h=index,docs.count,store.size" | head -20

# Step 5: check Jaeger ES write errors
curl -s http://localhost:14269/metrics | grep 'jaeger_es_bulk_requests_total' | grep -v '#'
```

### Scenario 3 — Query Timeout / Slow Trace Lookups

**Trigger:** `JaegerQuerySlowResponses` fires; trace search times out; UI shows "Service Unavailable".

```bash
# Step 1: query latency breakdown
curl -s http://localhost:16687/metrics | grep 'jaeger_query_latency_bucket' | tail -20

# Step 2: query error rates by operation
curl -s http://localhost:16687/metrics | grep 'jaeger_query_requests_total' | grep -v '#'

# Step 3: test search directly
time curl -s "http://localhost:16686/api/traces?service=myservice&limit=20&lookback=1h" | jq '.data | length'

# Step 4: Elasticsearch query performance
curl -s "http://localhost:9200/jaeger-service-*/_search" \
  -H "Content-Type: application/json" \
  -d '{"query":{"match_all":{}},"size":1}' | jq '.took'

# Step 5: check ES index size and shard count
curl -s "http://localhost:9200/_cat/indices/jaeger-span-*?v&h=index,docs.count,store.size,pri.store.size" | head -20

# Step 6: check query timeouts in logs
kubectl logs -l app.kubernetes.io/component=query --tail=50 | grep -iE "timeout|deadline|error"
```

### Scenario 4 — Sampling Configuration Issues

**Trigger:** Too many or too few traces; spans missing for specific services; storage costs unexpectedly high.

```bash
# Step 1: check current sampling config served to agents
curl -s http://localhost:5778/sampling | jq .

# Step 2: validate adaptive sampling metrics
curl -s http://localhost:14269/metrics | grep 'jaeger_sampler' | grep -v '#'

# Step 3: check per-service span volume
curl -s http://localhost:16686/api/services | jq -r '.data[]' | while read svc; do
  count=$(curl -s "http://localhost:16686/api/traces?service=$svc&limit=1&lookback=1h" \
    | jq '.total // 0')
  echo "$svc: $count traces/h"
done 2>/dev/null | sort -t: -k2 -rn | head -10

# Step 4: review and update sampling config
cat /etc/jaeger/sampling.json
# Example targeted config:
cat > /etc/jaeger/sampling.json << 'EOF'
{
  "service_strategies": [
    {"service": "high-traffic-svc", "type": "probabilistic", "param": 0.01},
    {"service": "payment-svc",      "type": "probabilistic", "param": 1.0},
    {"service": "auth-svc",         "type": "probabilistic", "param": 0.5}
  ],
  "default_strategy": {"type": "probabilistic", "param": 0.1}
}
EOF

# Step 5: apply by updating the sampling strategies file/ConfigMap and restarting collector
# (Jaeger collector loads sampling.json from --sampling.strategies-file at startup;
#  there is no runtime POST API. Use ConfigMap reload + rollout restart, or run an
#  HTTP server on a known URL referenced by --sampling.strategies-reload-interval.)
kubectl create configmap jaeger-sampling-config --from-file=/etc/jaeger/sampling.json -o yaml --dry-run=client | kubectl apply -f -
kubectl rollout restart deployment/jaeger-collector

# Step 6: for adaptive sampling — verify throughput targets
curl -s http://localhost:5778/sampling?service=myservice | jq .
```

## 5. Collector Span Queue Overflow

**Symptoms:** `jaeger_collector_queue_length / jaeger_collector_queue_capacity > 0.8`; `jaeger_collector_spans_dropped_total` rate increasing; trace gaps in UI

**Root Cause Decision Tree:**
- If `jaeger_collector_save_latency_seconds` p99 is high: → storage backend (Cassandra/ES) is the bottleneck causing queue backup
- If save latency is normal but queue still growing: → ingestion traffic spike; scale Collector horizontally
- If drops occur only at specific times: → traffic burst pattern; increase queue size or tune sampling

**Diagnosis:**
```bash
# Queue fill ratio
size=$(curl -s http://localhost:14269/metrics | grep 'jaeger_collector_queue_length' | awk '{print $2}')
cap=$(curl -s http://localhost:14269/metrics | grep 'jaeger_collector_queue_capacity' | awk '{print $2}')
echo "Queue: $size/$cap ($(echo "scale=0; $size * 100 / $cap" | bc)% full)"

# Storage write latency p99 (is backend the bottleneck?)
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_save_latency_seconds{quantile="0.99"'

# Span drop rate and cause
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_spans_dropped_total' | grep -v '#'

# Collector worker and queue config
kubectl exec -it $(kubectl get pod -l app.kubernetes.io/component=collector -o name | head -1) -- \
  cat /proc/1/cmdline | tr '\0' '\n' | grep -E "queue|workers"
```

**Thresholds:** Queue > 80% = Warning; drops > 0 = Critical (data loss).

## 6. Cassandra Write Timeout

**Symptoms:** `jaeger_cassandra_errors_total{by="timeout"}` rate > 0; span writes failing; trace data loss for affected services

**Root Cause Decision Tree:**
- If Cassandra `nodetool tpstats` shows `MutationStage` pending tasks > 0: → Cassandra coordinator is overloaded
- If timeout rate correlates with Jaeger traffic spikes: → Cassandra write throughput insufficient for span volume
- If only specific Cassandra nodes show timeouts: → unbalanced token distribution or hot partition

**Diagnosis:**
```bash
# Cassandra write timeout rate
curl -s http://localhost:14269/metrics | grep 'jaeger_cassandra_errors_total' | grep -v '#'

# Cassandra node thread pool status
nodetool tpstats | grep -E "MutationStage|ReadStage|Dropped"

# Cassandra node health
nodetool status

# Check Cassandra JVM heap pressure
nodetool info | grep "Heap Memory"

# Jaeger storage write latency
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_save_latency_seconds{quantile="0.99"'
```

**Thresholds:** `jaeger_cassandra_errors_total{by="timeout"}` rate > 0 = Warning; sustained > 1/s = Critical.

## 7. Agent UDP Packet Loss

**Symptoms:** `jaeger_agent_reporter_queue_size` at `jaeger_agent_reporter_queue_limit`; trace gaps despite services sending spans; no errors in SDK logs

**Root Cause Decision Tree:**
- If `netstat -su` shows UDP receive buffer errors on agent host: → UDP send buffer full; UDP is lossy and silently drops packets
- If Collector endpoint is slow: → agent reporter queue fills because Collector is not draining fast enough
- If spans are large (> 64KB): → UDP fragmentation causing packet loss at network layer

**Diagnosis:**
```bash
# Agent reporter queue fill (agent admin/metrics port is 14271; 5778 serves sampling, not metrics)
curl -s http://localhost:14271/metrics | grep -E 'jaeger_agent_reporter_queue_size|jaeger_agent_reporter_queue_limit' | grep -v '#'

# UDP buffer errors on agent host
netstat -su | grep -E "errors|buffer|overflow"

# Check UDP receive buffer size
cat /proc/sys/net/core/rmem_max
cat /proc/sys/net/core/rmem_default

# Collector response time (is collector the bottleneck?)
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_save_latency_seconds{quantile="0.99"'
```

**Thresholds:** Reporter queue at limit = Warning (UDP drops imminent); any UDP buffer errors = data loss occurring.

## 8. Query Timeout for Deep Traces

**Symptoms:** Trace search with > 10K spans timing out; Jaeger UI shows "Service Unavailable" for specific trace IDs; `jaeger_query_latency_bucket` p99 > 30s for `FindTraceIDs` operation

**Root Cause Decision Tree:**
- If only specific trace IDs time out: → those individual traces have abnormally high span counts (> 10K); storage reconstruction is slow
- If all queries slow: → Cassandra partition scanning overloaded; consider Elasticsearch backend
- If Elasticsearch backend: → missing index on `traceID` field; or index too large

**Diagnosis:**
```bash
# Query latency by operation
curl -s http://localhost:16687/metrics | grep 'jaeger_query_latency_bucket' | tail -20

# Query error rate
curl -s http://localhost:16687/metrics | grep 'jaeger_query_requests_total{result="err"' | grep -v '#'

# Cassandra: check partition scan cost for trace reconstruction
nodetool cfstats jaeger_v1_dc1.traces | grep -E "Read Latency|SSTable"

# Elasticsearch: check query performance
curl -s "http://localhost:9200/jaeger-span-*/_search" \
  -H "Content-Type: application/json" \
  -d '{"query":{"match_all":{}},"size":1}' | jq '.took'

# Find abnormally large traces (span count outliers)
curl -s 'http://localhost:16686/api/traces?service=myservice&limit=20&lookback=1h' | \
  jq '[.data[] | {traceID: .traceID, spanCount: (.spans | length)}] | sort_by(-.spanCount) | .[0:5]'
```

**Thresholds:** `jaeger_query_latency_bucket` p99 > 5s = Warning; > 30s = Critical.

## 9. Dependency Graph Stale

**Symptoms:** Service dependency graph in Jaeger UI not reflecting recent calls; new services don't appear; edges missing for recently added service integrations

**Root Cause Decision Tree:**
- If `spark-dependencies` CronJob has not run recently: → dependency job disabled or failing
- If job runs but graph not updating: → Spark job outputting to wrong index/table; or ES/Cassandra write failing
- If graph updates but shows stale edges: → retention window for dependency data too short relative to traffic patterns

**Diagnosis:**
```bash
# Check CronJob status
kubectl get cronjob -n monitoring | grep -i dependencies
kubectl get jobs -n monitoring | grep -i dependencies | tail -5

# Check last successful run
kubectl get job -n monitoring -l app=jaeger-spark-dependencies \
  -o jsonpath='{range .items[*]}{.metadata.name} {.status.completionTime}{"\n"}{end}' | sort

# Check Spark job logs for the last run
kubectl logs -n monitoring -l job-name=$(kubectl get job -n monitoring -l app=jaeger-spark-dependencies -o name | tail -1 | cut -d/ -f2) | tail -30

# Verify dependency data in storage
# Cassandra:
# SELECT * FROM jaeger_v1_dc1.dependencies_v2 LIMIT 5;

# Elasticsearch:
curl -s "http://localhost:9200/jaeger-dependencies-*/_count" | jq .
```

**Thresholds:** Dependency graph age > 24h = stale; CronJob missed > 2 runs = Warning.

## 10. Sampling Decision Inconsistency Causing Partial Traces (Intermittent)

**Symptoms:** Traces appear with some spans present and others missing despite all services being healthy; root span exists but downstream spans are absent for only some requests; issue is non-deterministic — affects ~5–20% of traces and varies by service; adaptive sampling decisions are not consistently propagated to all downstream services; `jaeger_tracer_sampled_total` shows mismatched sampling rates across services; in Jaeger UI, traces show broken chains with `[span not found]` gaps.

**Root Cause Decision Tree:**
- If head-based sampling is used but the sampling decision header is dropped or overwritten by a middleware: → downstream services make independent sampling decisions; spans that are sampled downstream but not upstream are orphaned
- If adaptive sampling is configured at the Collector but individual services poll the `jaeger-sampling` endpoint on different schedules: → during adaptive rate updates, some services get old rates and some get new rates; inconsistent sampling across a single request
- If W3C TraceContext and Jaeger B3 formats are mixed in a multi-service environment: → some services propagate `sampled=1` in B3 format; others read only W3C `traceflags`; the sampling bit is lost in translation
- If a service restarts and uses default `1/1000` sampling before fetching remote config: → that service's spans during startup window are not sampled even when upstream says sampled
- If tail-based sampling is configured at the Collector but not all Collectors receive all spans: → tail-based decision is made per-collector; spans for the same trace split across collectors get different decisions

**Diagnosis:**
```bash
# Check sampling config being served by Collector/Agent
curl -s http://localhost:5778/sampling?service=my-service | jq .
# Should return: {"strategyType":"PROBABILISTIC","probabilisticSampling":{"samplingRate":0.1}}

# Compare sampling rates across services
for svc in service-a service-b service-c; do
  echo "$svc: $(curl -s http://jaeger-agent:5778/sampling?service=$svc | jq '.probabilisticSampling.samplingRate')"
done

# Check if adaptive sampling has inconsistent state
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_sampling' | grep -v '#'

# Check for orphaned spans (spans with parentId but no matching parent in Jaeger)
curl -s 'http://localhost:16686/api/traces?service=my-service&limit=20' | \
  jq '[.data[] | {traceID, spanCount: (.spans | length), 
      hasOrphans: (.spans | any(.references[]?.refType == "CHILD_OF" and .traceID != .traceID))}]'

# Verify sampling header propagation in live traffic
# In a test: trace a request and check each service's span's sampled flag
curl -H "uber-trace-id: abc123:def456:0:1" http://my-service/api/endpoint -v 2>&1 | grep -i "uber-trace"
```

**Thresholds:**
- Traces with missing spans > 5% = WARNING (sampling inconsistency)
- `jaeger_tracer_sampled_total` rate significantly differs across services in same call chain = WARNING
- Sampling endpoint unreachable from a service = CRITICAL (service falls back to default, breaks consistency)

## 11. Jaeger Collector Buffer Filling During Traffic Spike (Intermittent)

**Symptoms:** `jaeger_collector_spans_dropped_total` spikes during peak traffic hours (e.g., 12:00–13:00 UTC daily or during deployments); `jaeger_collector_queue_length` reaches `jaeger_collector_queue_capacity` before drops begin; spans are dropped in bursts, not continuously — trace coverage degrades from 100% to 60–70% during the spike; self-resolves when traffic drops; alert fires and resolves within 30 minutes; storage backend (ES/Cassandra) write latency is normal — the queue is filling faster than workers can drain it.

**Root Cause Decision Tree:**
- If `jaeger_collector_queue_length` / `jaeger_collector_queue_capacity` > 0.8 before drops: → queue is undersized for burst traffic; increase queue size
- If storage write latency (`jaeger_collector_save_latency_seconds`) is normal but queue still fills: → insufficient worker count; increase `--collector.num-workers`
- If the burst is predictable (same time daily): → batch jobs or scheduled traffic is causing the spike; pre-scale Collector or reduce sampling during spike
- If drops occur only on specific Collector instances: → load balancer is not distributing spans evenly; some instances are hot
- If queue was previously larger and was reduced during a cost-cutting effort: → original sizing was intentional; restore it

**Diagnosis:**
```bash
# Real-time queue fill ratio
size=$(curl -s http://localhost:14269/metrics | grep '^jaeger_collector_queue_length ' | awk '{print $2}')
cap=$(curl -s http://localhost:14269/metrics | grep '^jaeger_collector_queue_capacity ' | awk '{print $2}')
echo "Queue: $size/$cap ($(awk "BEGIN{printf \"%.0f\", $size*100/$cap}") % full)"

# Drop rate per minute
curl -s http://localhost:14269/metrics | grep 'jaeger_collector_spans_dropped_total' | grep -v '#'

# Storage write latency (is backend the bottleneck?)
curl -s http://localhost:14269/metrics | \
  grep 'jaeger_collector_save_latency_seconds{quantile="0.99"}' | grep -v '#'

# Collector worker thread count
kubectl exec -it $(kubectl get pod -l app.kubernetes.io/component=collector -o name | head -1) -- \
  cat /proc/1/cmdline | tr '\0' '\n' | grep -E "workers|queue-size"

# Check if load is uneven across Collector replicas
kubectl top pods -l app.kubernetes.io/component=collector

# Monitor queue fill during spike window
watch -n 5 'curl -s http://localhost:14269/metrics | grep "jaeger_collector_queue_length\b"'
```

**Thresholds:**
- `jaeger_collector_queue_length / jaeger_collector_queue_capacity` > 80% = WARNING
- `jaeger_collector_spans_dropped_total` rate > 0 = CRITICAL (data loss)
- Storage write latency p99 > 500ms = WARNING (backend becoming bottleneck)

## 12. Trace Context Not Propagated Through Async Message Queue (Intermittent)

**Symptoms:** Traces for async workflows are broken — producer span exists, consumer span exists, but they are not linked as the same trace; two separate trace IDs appear for what should be one end-to-end flow; issue is intermittent — affects only async paths through Kafka/RabbitMQ/SQS, not synchronous HTTP calls; `service dependency graph` shows no edges between producer and consumer services; root cause is Kafka message headers not carrying Jaeger context.

**Root Cause Decision Tree:**
- If producer service uses Jaeger client but does not inject trace context into Kafka message headers: → consumer receives message with no trace context; starts a new root span with new trace ID
- If producer injects context but consumer uses a Kafka library that strips custom headers: → context injected but not extracted on the other side
- If `jaeger.propagation` format is `jaeger` (default Uber format) but consumer extracts `b3` format: → format mismatch; context is present in headers but not recognized
- If consumer processes messages in a thread pool without propagating context to worker threads: → context available in the consumer thread but lost when task is submitted to executor
- If using Kafka Streams or Flink: → frameworks may not automatically propagate message headers to processing context; explicit extraction required

**Diagnosis:**
```bash
# Verify a producer span has injected trace context into Kafka headers
# Use Kafka console consumer to inspect a message's headers:
kafka-console-consumer.sh --bootstrap-server $KAFKA_BROKERS \
  --topic my-topic --max-messages 1 --print-headers | grep -i "uber-trace\|b3\|traceparent"

# Check if consumer traces have parentId set (if yes, context is propagating)
curl -s 'http://localhost:16686/api/traces?service=my-consumer&limit=20' | \
  jq '.data[] | {traceID, rootSpan: (.spans[] | select(.references | length == 0) | .operationName)}'

# Compare trace count vs message count (significant difference = broken propagation)
# Consumer traces without parent = broken traces
curl -s 'http://localhost:16686/api/traces?service=my-consumer&limit=100' | \
  jq '[.data[] | select(.spans[0].references | length == 0)] | length'

# Check propagation format configured in the service
kubectl exec -it <consumer-pod> -- env | grep -iE "jaeger|propagat|b3|trace"
```

**Thresholds:**
- Consumer traces without parent spans > 10% = WARNING (context propagation degraded)
- Consumer traces without parent spans = 100% = CRITICAL (no context propagation at all)
- No edges in service dependency graph between producer and consumer = WARNING

## 13. Elasticsearch Scroll Context Expiry Causing Trace Search Missing Results (Intermittent)

**Symptoms:** Jaeger UI shows "No traces found" for a valid time range that definitely has traces; issue is intermittent and time-dependent — occurs only when querying time ranges > 7 days back; `jaeger_query_requests_total{result="err"}` increments when this query pattern is used; Jaeger Query logs show `search_context_missing` or `context_expired` errors from Elasticsearch; affects deep pagination queries (page 5+ of results); also occurs when Jaeger's ES scroll context expires between paginated UI requests.

**Root Cause Decision Tree:**
- If the error appears for deep pagination (user scrolling through many result pages): → Jaeger uses ES scroll API internally; if user pauses > scroll timeout before clicking "next page", the scroll context expires
- If time range is very large (> 7 days) and index pattern is date-based: → Jaeger must query many daily indices; ES response time exceeds scroll TTL
- If `es.max-span-age` is set shorter than the query time range: → Jaeger refuses to query beyond max-span-age; returns 0 results for out-of-range queries (not a bug — by design)
- If ES cluster is under memory pressure: → scroll contexts are evicted early by ES to reclaim heap
- If `search.maxDocCount` is hit: → ES returns partial results and truncates; Jaeger may not surface this as an error

**Diagnosis:**
```bash
# Check Jaeger Query error rate
curl -s http://localhost:16687/metrics | grep 'jaeger_query_requests_total' | grep -v '#'

# Check Elasticsearch scroll context state
curl -s "http://localhost:9200/_nodes/stats/indices/search?pretty" | \
  jq '.nodes[] | {name: .name, open_contexts: .indices.search.open_contexts}'

# Verify ES max-span-age configuration in Jaeger
kubectl exec -it $(kubectl get pod -l app.kubernetes.io/component=query -o name | head -1) -- \
  cat /proc/1/cmdline | tr '\0' '\n' | grep "max-span-age"

# Test ES query directly for the failing time range
start_ms=$(date -d "7 days ago" +%s%3N)
curl -s "http://localhost:9200/jaeger-span-*/_search?pretty" \
  -H 'Content-Type: application/json' \
  -d "{\"query\":{\"range\":{\"startTime\":{\"gte\":$start_ms}}},\"size\":1}" | \
  jq '{total: .hits.total, took: .took}'

# Check ES search.max_result_window setting on jaeger indices
curl -s "http://localhost:9200/jaeger-span-*/_settings?pretty" | \
  jq 'to_entries[0].value.settings.index.max_result_window'
```

**Thresholds:**
- `jaeger_query_requests_total{result="err"}` rate > 0 = WARNING
- ES `open_contexts` dropping rapidly = WARNING (scroll contexts being evicted)
- Query for time range > `es.max-span-age` = CRITICAL (returns no results by design — user confusion)

## 14. Jaeger Agent UDP Packet Loss Causing Sampling Bias (Intermittent)

**Symptoms:** Span counts are lower than expected during high traffic; traces exist but have significantly fewer spans than the service actually generates; issue appears under load (> 500 RPS) and resolves at lower traffic; `jaeger_agent_reporter_spans_dropped_total` incrementing; UDP socket buffer overflow; specific high-traffic services are disproportionately affected; switching from UDP agent to gRPC direct collector connection fixes the issue.

**Root Cause Decision Tree:**
- If `net.core.rmem_max` is at default (212992 bytes) and span volume is high: → UDP receive buffer overflows; kernel silently drops UDP packets; Jaeger agent loses spans without error
- If spans are large (> 65KB): → UDP datagram is fragmented; IP fragment reassembly failure drops the entire span batch silently
- If the service is co-located with Jaeger agent as sidecar but using default UDP buffer: → localhost UDP still subject to buffer limits; high RPS services overflow even on same node
- If `jaeger_agent_thrift_udp_server_packets_dropped_total` is > 0: → confirmed UDP drop at Jaeger agent's receive buffer (not OS kernel level)
- If drops correlate with GC pauses in the application: → span bursts occur post-GC when application catches up; UDP buffer cannot absorb the burst

**Diagnosis:**
```bash
# Check OS UDP drop counters (applies to all UDP sockets, look for drops)
cat /proc/net/udp | head -5
netstat -su | grep -E "errors|dropped|overflow"

# Check Jaeger agent UDP drop metrics
curl -s http://localhost:14271/metrics | grep 'jaeger_agent_thrift_udp_server_packets_dropped_total' | grep -v '#'
curl -s http://localhost:14271/metrics | grep 'jaeger_agent_reporter_spans_dropped_total' | grep -v '#'

# Check current UDP receive buffer size
cat /proc/sys/net/core/rmem_max
cat /proc/sys/net/core/rmem_default

# Check Jaeger agent's UDP server queue depth
curl -s http://localhost:14271/metrics | grep 'jaeger_agent_thrift_udp_server_queue_size' | grep -v '#'

# Check span size distribution (large spans fragment UDP)
curl -s http://localhost:14271/metrics | grep 'jaeger_agent_thrift_udp_bytes' | grep -v '#'

# Compare sent vs reported spans at SDK level
# Application instrumentation: spans_started vs spans_finished vs spans_reported
curl -s http://localhost:14271/metrics | grep 'jaeger_tracer_spans' | grep -v '#'
```

**Thresholds:**
- `jaeger_agent_thrift_udp_server_packets_dropped_total` rate > 0 = WARNING
- `jaeger_agent_reporter_spans_dropped_total` rate > 0 = CRITICAL (permanent data loss)
- OS UDP drops > 0 = WARNING
- Traffic > 500 RPS with default UDP buffer = WARNING configuration risk

## 15. Clock Skew Between Services Causing Span Ordering Reversal (Intermittent)

**Symptoms:** Jaeger UI shows child spans starting BEFORE their parent span; negative elapsed time values appear in trace view; spans from service-B appear to begin 2–5 seconds before the HTTP call from service-A was made; waterfall diagram shows impossible ordering; issue is intermittent — depends on NTP sync state of individual nodes; gets worse when new nodes are added (fresh NTP sync needed); `startTime` > `endTime` anomalies visible in raw span data.

**Root Cause Decision Tree:**
- If child span `startTime` < parent span `startTime`: → child service's clock is behind parent service's clock by the difference; this is a clock skew issue, not a code bug
- If the difference is 1–2 seconds: → NTP sync interval drift; nodes running ntpd with default 64s poll interval can drift by seconds between syncs
- If the difference is minutes: → NTP service is not running on the affected node; or node was recently migrated and clock not synchronized
- If the issue affects only recently added nodes: → new nodes have not completed first NTP sync (chrony/ntpd may take minutes to adjust on first start)
- If using container runtime on VMs: → VM clock drift is separate from container clock; hypervisor clock sync may be disabled
- If issue affects services in different cloud regions: → regional clock sources may have slight differences; cross-region traces always have some skew risk

**Diagnosis:**
```bash
# Check NTP sync status on affected nodes
timedatectl status | grep -E "synchronized|NTP|offset"
chronyc tracking | grep -E "offset|System time"
ntpq -p | grep -v "^$" | head -10

# Check clock offset between two services' nodes
# On node-A:
date +%s%3N
# On node-B (run within 1s of above):
date +%s%3N
# Difference > 500ms = significant skew

# Identify span ordering violations in a specific trace
curl -s 'http://localhost:16686/api/traces/<traceID>' | \
  jq '.data[0].spans | sort_by(.startTime) | 
      [.[] | {service: .processID, op: .operationName, start: .startTime, dur: .duration}]'

# Find traces with clock skew (child starts before parent)
curl -s 'http://localhost:16686/api/traces?service=my-service&limit=100' | \
  jq '[.data[] | .spans as $spans | $spans[] | 
      select(.references[]?.refType == "CHILD_OF") | 
      . as $child | $spans[] | 
      select(.spanID == $child.references[0].spanID) |
      select($child.startTime < .startTime) |
      {traceID: $child.traceID, skew_ms: (((.startTime - $child.startTime) / 1000))}]'
```

**Thresholds:**
- Clock offset > 100ms between any two services = WARNING (visible ordering issues in UI)
- Clock offset > 1000ms = CRITICAL (significant trace distortion, inaccurate latency measurements)
- `timedatectl` showing `synchronized: no` = CRITICAL (NTP not running)
- Child span `startTime` < parent `startTime` = WARNING (clock skew confirmed)

## 19. Silent Trace Sampling Drop (Adaptive Sampling)

**Symptoms:** Distributed traces missing for specific services. Jaeger appears healthy. Some services are traced normally, others are not.

**Root Cause Decision Tree:**
- If adaptive sampling rate dropped to 0% for a high-traffic service → 100% of that service's traces are dropped at the agent
- If `sampling.strategies-file` has not been updated → new services use the default strategy (potentially 0.001% for high-traffic services)
- If `jaeger-collector` is reporting `spans-dropped` → ingestion rate is exceeding collector capacity, causing upstream sampling adjustments
- If the sampling strategy server is unreachable → agents fall back to a stale or default strategy silently

**Diagnosis:**
```bash
# Check the current sampling strategy for a specific service
curl http://jaeger-query:16686/api/sampling?service=<service-name>

# Check collector span drop rate (collector admin/metrics port is 14269; 14268 is HTTP span ingest)
curl http://jaeger-collector:14269/metrics | grep spans_dropped

# Check adaptive sampling decisions (if using adaptive sampling)
kubectl logs -l app=jaeger-collector | grep -iE "sampling|rate|adjust" | tail -30

# Verify all services are listed in sampling config
curl http://jaeger-query:16686/api/services | jq '.data[]'
```

**Thresholds:** Sampling rate for any service = 0% = Critical (complete trace blackout); `spans_dropped` rate > 0 for > 5m = Warning.

## 20. Cross-Service Chain — Elasticsearch Storage Full Causing Silent Trace Loss

**Symptoms:** Old traces are not available in Jaeger UI. Recent traces appear fine. No Jaeger errors or alerts.

**Root Cause Decision Tree:**
- Alert: Jaeger trace not found when investigating an older incident
- Real cause: Elasticsearch disk watermark reached → Jaeger index blocked for new writes → new traces fail to write silently
- If `curl http://es:9200/_cluster/health` shows `status: red` or `relocating_shards > 0` → ES cluster is degraded
- If ES disk usage > 85% (low watermark) → ES stops allocating new shards; index writes may be rejected
- If ES disk usage > 90% (high watermark) → ES sets indices to read-only; all trace writes silently rejected
- If `jaeger_collector_spans_rejected_total` is incrementing → collector confirmed write failures to ES

**Diagnosis:**
```bash
# Check Elasticsearch cluster health and disk usage
curl http://es:9200/_cluster/health?pretty
curl http://es:9200/_cat/allocation?v

# Check for read-only indices (set automatically when disk watermark hit)
curl http://es:9200/_cat/indices/jaeger-* | grep -E "red|close"
curl http://es:9200/jaeger-span-*/_settings | jq '.. | .read_only_allow_delete? // empty'

# Check Jaeger collector rejection rate (admin/metrics port is 14269)
curl http://jaeger-collector:14269/metrics | grep jaeger_collector_spans_rejected_total

# Check disk watermark settings
curl http://es:9200/_cluster/settings?pretty | grep watermark
```

**Thresholds:** ES disk > 80% = Warning; `jaeger_collector_spans_rejected_total` rate > 0 = Critical (data loss).

## Common Error Messages & Root Causes

| Error Message | Root Cause |
|---------------|-----------|
| `SpanBuffer is full, dropping span` | Collector span queue is full — need more collector workers (`--collector.num-workers`) or the storage backend is too slow |
| `Failed to locate service ... in Jaeger storage` | Trace not yet written or not indexed — propagation delay between collector write and storage index availability |
| `WARN: ... sampling strategy not found` | Sampling strategy server is unreachable; Jaeger agent falls back to default (probabilistic) sampling |
| `transport: Error while dialing ...` | Jaeger collector unreachable from agent — wrong endpoint address, port, or network policy blocking gRPC/UDP |
| `ERROR: Failed to send batch: ... connection refused` | gRPC exporter pointing to wrong collector endpoint; collector not running or port mismatch |
| `WARNING: ... reporter unable to connect to ... retrying` | UDP or gRPC agent-to-collector connectivity broken — network policy, DNS resolution failure, or collector down |

---

## 16. Collector Queue Saturation During Traffic Spike (Resource Contention)

**Symptoms:** `jaeger_collector_spans_dropped_total{cause="queue-full"}` rate increases sharply during traffic spikes (e.g., Black Friday, release deploy, load test); trace gaps appear in Jaeger UI for services under load; `jaeger_collector_queue_length` / `jaeger_collector_queue_capacity` ratio approaches 1.0; drops correlate with bursts in service traffic, not storage backend issues; storage write latency is normal; issue resolves when traffic returns to baseline.

**Root Cause Decision Tree:**
- If `jaeger_collector_queue_length / jaeger_collector_queue_capacity > 0.9`: → collector queue is the bottleneck; spans arriving faster than workers can process and forward to storage
- If `jaeger_collector_save_latency_seconds` p99 is normal (< 200ms): → storage is not the bottleneck; increase workers and queue size
- If `jaeger_collector_save_latency_seconds` p99 is high (> 1s): → storage is slow; workers block waiting for storage writes; queue fills up; fix storage first
- If drops occur only on one collector pod: → load balancer is not distributing evenly; one pod is hot; scale horizontally
- If queue fills only during known traffic events: → provision for peak load; use HPA to scale collector ahead of traffic

**Diagnosis:**
```bash
# Queue utilization percentage
size=$(curl -s http://collector:14269/metrics | grep '^jaeger_collector_queue_length ' | awk '{print $2}')
cap=$(curl -s http://collector:14269/metrics | grep '^jaeger_collector_queue_capacity ' | awk '{print $2}')
echo "Collector queue: $size/$cap ($(echo "scale=1; $size * 100 / $cap" | bc -l)% full)"

# Drop rate by cause
curl -s http://collector:14269/metrics | grep 'jaeger_collector_spans_dropped_total' | grep -v '#'

# Storage write latency (is storage the bottleneck?)
curl -s http://collector:14269/metrics | grep 'jaeger_collector_save_latency_seconds' | grep -v '#'

# Worker count and utilization
curl -s http://collector:14269/metrics | grep -E 'jaeger_collector_num_workers|jaeger_collector_in_queue_latency' | grep -v '#'

# Check if multiple collector pods have balanced load
for pod in $(kubectl get pods -l app.kubernetes.io/component=collector -o name); do
  drops=$(kubectl exec $pod -- curl -s localhost:14269/metrics | grep '^jaeger_collector_spans_dropped_total ' | awk '{sum+=$2} END{print sum}')
  echo "$pod drops: $drops"
done
```

**Thresholds:**
- Queue fill ratio > 80% = WARNING (drops imminent); > 95% = CRITICAL (drops occurring)
- `jaeger_collector_spans_dropped_total` rate > 0 = CRITICAL (trace data loss)
- Storage write p99 > 500ms = WARNING (storage becoming the bottleneck)

## 17. Partial Trace Assembly Due to Async Message Queue Context Propagation Gap

**Symptoms:** Traces show a parent span from service-A but child spans from service-B (which consumed from a message queue) appear as separate, disconnected root traces; Jaeger UI shows incomplete traces with missing downstream spans; service dependency graph shows service-A and service-B as unconnected; the trace context (`traceparent` / `uber-trace-id`) is not propagated through the message queue headers; issue affects only async workflows (Kafka, RabbitMQ, SQS), not synchronous HTTP calls.

**Root Cause Decision Tree:**
- If service-B spans appear as new root traces (no parent): → trace context not injected into message headers by service-A, or not extracted by service-B's consumer
- If trace headers are present in the message but service-B does not propagate them: → service-B's OpenTelemetry SDK instrumentation is missing `TextMapPropagator` extraction from message headers
- If service-A uses Jaeger propagation format but service-B expects W3C `traceparent`: → propagation format mismatch; header is present but not recognized
- If using Kafka: → the producer instrumentation must inject headers into `ProducerRecord.headers()`; consumer must extract from `ConsumerRecord.headers()`
- If traces are complete in staging but broken in production: → production uses a different message library version or SDK configuration that disables auto-instrumentation

**Diagnosis:**
```bash
# Check if disconnected traces exist for the service pair
curl -s 'http://localhost:16686/api/traces?service=service-b&limit=20' | \
  jq '.data[] | select(.spans | length == 1) | {traceID: .traceID, rootOp: .spans[0].operationName}'

# Check if service-B traces have any references (parent links)
curl -s 'http://localhost:16686/api/traces?service=service-b&limit=5' | \
  jq '.data[0].spans[] | {spanID: .spanID, refs: .references}'

# Check service dependency graph for missing edge
curl -s 'http://localhost:16686/api/dependencies?endTs=$(date +%s000)&lookback=3600000' | \
  jq '.data[] | select(.parent == "service-a" and .child == "service-b")'

# Inspect raw message headers (requires access to Kafka consumer or debug logging)
# Enable debug logging in service-B to log incoming message headers:
# kubectl set env deployment/service-b OTEL_LOG_LEVEL=DEBUG
```

**Thresholds:**
- Service-B root traces with no parent refs when service-A → message queue → service-B flow exists = WARNING (trace context propagation broken)
- Service dependency graph missing expected async edges = WARNING

## 18. Sampling Strategy Server Unreachable Causing Silent Sampling Rate Change

**Symptoms:** After a Jaeger sampling strategy server outage or misconfiguration, agents across all services silently fall back to default probabilistic sampling (1%); trace volume in Jaeger drops by 80–95% without any alert firing; SLO measurements relying on traces are now based on 1% of traffic instead of the configured 10–50%; the change is invisible in dashboards until someone notices missing traces for a specific service; when strategy server recovers, sampling rates may not immediately restore for already-running agent processes.

**Root Cause Decision Tree:**
- If `WARN: ... sampling strategy not found` appears in Jaeger agent logs: → agent cannot reach sampling server; falling back to default
- If sampling server is running but agent cannot reach it: → network policy blocking agent → sampling server communication; verify service DNS and port
- If sampling server returns an error for a specific service: → service name in the sampling config does not match the service name in the trace; case-sensitive match
- If sampling rates dropped after a Jaeger version upgrade: → sampling server API may have changed; agent and collector/sampling-server versions are mismatched
- If remote sampling is configured but `--reporter.grpc.host-port` is wrong: → agent is reporting to a non-existent collector and also cannot reach sampling server if both share the same misconfigured endpoint

**Diagnosis:**
```bash
# Check Jaeger agent logs for sampling warnings
kubectl logs -l app.kubernetes.io/component=agent --tail=50 | grep -iE "sampling|strategy|warn" | tail -20

# Check if agent can reach sampling server (collector serves sampling on port 5778, not 14268)
kubectl exec -it $(kubectl get pod -l app.kubernetes.io/component=agent -o name | head -1) -- \
  wget -qO- http://jaeger-collector:5778/sampling?service=my-service 2>&1

# Compare current effective sampling rate vs configured
curl -s http://jaeger-collector:5778/sampling?service=my-service | jq .

# Check trace volume trend (sharp drop = sampling rate changed)
curl -s 'http://localhost:16686/api/services' | jq '.data | length'

# Check agent sampling configuration
kubectl exec -it $(kubectl get pod -l app.kubernetes.io/component=agent -o name | head -1) -- \
  cat /proc/1/cmdline | tr '\0' '\n' | grep -E "sampling|strategy"
```

**Thresholds:**
- `jaeger_agent_reporter_batches_failures_total` rate > 0 = WARNING (agent cannot reach collector/sampling server)
- Trace ingestion rate drop > 50% without corresponding traffic drop = WARNING (sampling rate changed)
- Agent log showing `sampling strategy not found` = WARNING

# Capabilities

1. **Collector operations** — Scaling, queue tuning, span processing
2. **Storage backend management** — Cassandra/ES health, retention, indexing
3. **Sampling configuration** — Adaptive sampling, per-service strategies
4. **Trace analysis** — Service dependency graphs, latency investigation
5. **Query optimization** — Index tuning, time range management
6. **Migration** — Backend migration, version upgrades

# Critical Metrics to Check First

1. `jaeger_collector_spans_dropped_total` (> 0 = data loss)
2. `jaeger_collector_queue_length` vs `jaeger_collector_queue_capacity` (> 80 % = drops imminent)
3. `jaeger_collector_save_latency_seconds` p99 (> 1 s = storage bottleneck)
4. `jaeger_cassandra_errors_total` or `jaeger_es_bulk_requests_total{result="errors"}` (storage failures)
5. `jaeger_query_requests_total{result="err"}` (query service errors)

# Output

Standard diagnosis/mitigation format. Always include: collector drop/queue metrics,
storage backend status, write latency p99, sampling configuration review,
and scaling recommendations.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| Traces missing for recent time window | Elasticsearch disk watermark hit — indices set to read-only; collector writes silently rejected | `curl -s http://es:9200/_cat/allocation?v` and `curl -s http://es:9200/_cluster/health?pretty | grep status` |
| Collector queue filling (`jaeger_collector_queue_length` near capacity) | Kafka consumer lag for the Jaeger topic — collector is processing a backlog from a broker catch-up burst | Check Kafka consumer lag: `kafka-consumer-groups.sh --bootstrap-server <broker>:9092 --describe --group jaeger-collector` |
| `jaeger_collector_save_latency_seconds` p99 > 2s | Cassandra/ES GC pause on backend storage node — write requests stack up during GC stop-the-world | Check backend GC logs: `journalctl -u cassandra | grep -E "GC.*pause|stop-the-world" | tail -20` or `curl -s http://es:9200/_nodes/stats/jvm | jq '.nodes[].jvm.gc'` |
| Sampling strategy server unreachable; agents fall back to 1% | Network policy change blocking agent → collector sampling port 5778 after a security hardening deployment | `kubectl exec <jaeger-agent-pod> -- curl -v http://jaeger-collector:5778/sampling?service=test 2>&1 | head -20` |
| Span propagation broken — child spans appearing as root traces | Kubernetes ingress or API gateway stripping `uber-trace-id` / `traceparent` headers as part of a security WAF rule update | Check ingress controller logs and headers: `kubectl exec <client-pod> -- curl -v http://<service>/ 2>&1 | grep -i "trace\|jaeger\|traceparent"` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N Jaeger collector pods with a full queue (others draining normally) | Load balancer not distributing evenly; one collector pod's `jaeger_collector_queue_length` near `jaeger_collector_queue_capacity` while peers are low | Spans routed to the overloaded collector are dropped; spans to healthy collectors are saved; sampling bias — high-traffic services most affected | `for pod in $(kubectl get pods -l app.kubernetes.io/component=collector -o name); do echo "$pod: $(kubectl exec $pod -- curl -s localhost:14269/metrics | grep '^jaeger_collector_queue_length ')"; done` |
| 1 Elasticsearch data node with a degraded shard | ES cluster yellow; one shard unassigned or replica missing; other shards healthy | Jaeger queries for traces stored on the degraded shard return empty or timeout; queries for other time ranges succeed | `curl -s http://es:9200/_cat/shards?v | grep -E "UNASSIGNED|jaeger"` |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Span drop rate | > 0.1% | > 1% | `curl -s localhost:14269/metrics | grep jaeger_collector_spans_dropped_total` |
| Collector queue length | > 1000 | > 5000 | `curl -s localhost:14269/metrics | grep jaeger_collector_queue_length` |
| Collector queue capacity utilization | > 70% | > 90% | `curl -s localhost:14269/metrics | grep -E "jaeger_collector_queue_length|jaeger_collector_queue_capacity"` |
| Span ingestion rate (spans/sec) | > 50,000 | > 100,000 | `curl -s localhost:14269/metrics | grep jaeger_collector_spans_received_total` |
| Elasticsearch index write latency p99 | > 200ms | > 1s | `curl -s http://es:9200/_nodes/stats/indices | jq '.nodes[].indices.indexing.index_time_in_millis'` |
| Trace query latency p99 | > 1s | > 5s | `curl -s localhost:16687/metrics | grep jaeger_query_requests_total` |
| Jaeger agent UDP packet loss | > 0.5% | > 2% | `curl -s localhost:14271/metrics | grep thrift_udp_server_packets_dropped` |
| Elasticsearch disk usage | > 70% | > 85% | `curl -s http://es:9200/_cat/allocation?v | awk '{print $5}'` |
| 1 of N Jaeger agent DaemonSet pods crashlooping on a specific node | `kubectl get pods -l app.kubernetes.io/component=agent -A | grep -v Running` shows one pod in CrashLoopBackOff | Applications on that Kubernetes node cannot ship spans; all other nodes unaffected; invisible unless per-node span rate is monitored | `kubectl get pods -A -l app.kubernetes.io/component=agent -o wide | grep -v Running` — note the node, then `kubectl logs <agent-pod> --previous` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Elasticsearch disk usage | Jaeger index data consuming >65% of total ES disk (`curl -s "http://elasticsearch:9200/_cat/allocation?v"`) | Enable ILM rollover policy; delete indices older than retention window; add ES data nodes | 2–3 days |
| Collector span queue depth | `jaeger_collector_queue_length` consistently >80% of `--collector.queue-size` | Scale collector replicas (`kubectl scale deployment/jaeger-collector --replicas=N`); increase `--collector.queue-size` | 1–2 days |
| Collector spans dropped rate | `jaeger_collector_spans_dropped_total` rate > 0 | Immediately scale collectors; increase Elasticsearch write throughput (bulk size, concurrency) | Hours |
| Elasticsearch index shard count | Total shard count >50% of ES cluster's recommended max (cluster_max_shards_per_node) | Reduce index rollover frequency; increase `number_of_shards` or add ES nodes | 1 week |
| Jaeger query response latency (p99) | Query API p99 >2 s (`jaeger_query_requests_total` with latency histogram) | Add query replicas; tune Elasticsearch query cache; add ES coordinating nodes | 3–5 days |
| Collector CPU utilisation | Collector pods at >70% CPU requests (`kubectl top pod -n observability -l app.kubernetes.io/component=collector`) | Scale horizontally; tune `--collector.num-workers` to match CPU cores | 2–3 days |
| Elasticsearch JVM heap usage | ES node JVM heap >75% (`curl -s "http://elasticsearch:9200/_cat/nodes?v&h=name,heap.percent"`) | Increase ES heap (max 50% of node RAM); add ES data nodes; reduce field mapping bloat | 2–3 days |
| Ingester Kafka consumer lag | `kafka_consumer_group_lag` for Jaeger ingester consumer group rising over time | Scale ingester replicas; increase Kafka partition count for the spans topic | 1–2 days |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check Jaeger collector pod status and recent restarts
kubectl get pods -n observability -l app=jaeger,app.kubernetes.io/component=collector -o wide

# Tail collector logs for span ingestion errors
kubectl logs -n observability -l app.kubernetes.io/component=collector --tail=200 | grep -iE "error|drop|queue|overflow"

# Query Elasticsearch for total span document count across all daily indices
curl -s "http://elasticsearch:9200/_cat/indices/jaeger-span-*?v&h=index,docs.count,store.size" | sort -k3 -rh | head -20

# Check Elasticsearch cluster health
curl -s "http://elasticsearch:9200/_cluster/health?pretty" | jq '{status,number_of_nodes,active_shards,unassigned_shards}'

# Verify Jaeger query service can reach Elasticsearch
kubectl exec -n observability deploy/jaeger-query -- curl -s "http://elasticsearch:9200/_cat/health"

# Check collector queue depth and span receive rate (Prometheus)
kubectl exec -n observability deploy/prometheus -- curl -sg 'http://localhost:9090/api/v1/query?query=jaeger_collector_queue_length'

# List recent traces for a service via Jaeger HTTP API
curl -s "http://jaeger-query:16686/api/traces?service=<service-name>&limit=5&lookback=1h" | jq '.data[].traceID'

# Check Kafka consumer lag for Jaeger ingester (if using Kafka)
kubectl exec -n observability deploy/kafka -- kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group jaeger-ingester

# Inspect Jaeger agent DaemonSet rollout status
kubectl rollout status daemonset/jaeger-agent -n observability

# Count spans dropped vs received in the last 5 minutes
kubectl exec -n observability deploy/prometheus -- curl -sg 'http://localhost:9090/api/v1/query?query=rate(jaeger_collector_spans_dropped_total[5m])'
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Span ingestion success rate | 99.5% | `1 - (rate(jaeger_collector_spans_dropped_total[5m]) / rate(jaeger_collector_spans_received_total[5m]))` | 3.6 hr | >7.2x |
| Trace query availability | 99.9% | Probe success rate against `http://jaeger-query:16686/api/services` (`probe_success{job="jaeger-query"}`) | 43.8 min | >14.4x |
| Trace query latency p99 | 99% requests <3s | `histogram_quantile(0.99, rate(jaeger_query_latency_bucket[5m])) < 3` | 7.3 hr | >3.6x |
| Elasticsearch index health | 99.5% | `elasticsearch_cluster_health_status{color="green"}` == 1 sustained over 30 days | 3.6 hr | >7.2x |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| TLS enabled for collector ingestion endpoints | `kubectl get deploy jaeger-collector -n observability -o jsonpath='{.spec.template.spec.containers[0].env[*]}' \| grep -i tls` | `COLLECTOR_GRPC_TLS_ENABLED=true` and cert/key paths configured |
| Authentication enabled on Jaeger Query UI | `kubectl get ingress -n observability -l app=jaeger -o yaml \| grep -i auth` | Ingress has OAuth2/OIDC annotation or basic-auth; UI not publicly exposed without auth |
| Elasticsearch index retention policy set | `kubectl exec -n observability deploy/jaeger-collector -- env \| grep ES_INDEX_MAX_SPAN_AGE` | Retention set (e.g. `168h` / 7 days); no unbounded index growth |
| Elasticsearch replication factor >= 1 | `curl -s http://elasticsearch:9200/_cat/indices/jaeger-* \| awk '{print $5}'` | Replica count >= 1 for all jaeger indices; no `0` replicas in production |
| Resource limits set on all Jaeger components | `kubectl get deploy -n observability -l app.kubernetes.io/name=jaeger -o jsonpath='{range .items[*]}{.metadata.name}{": "}{.spec.template.spec.containers[0].resources}{"\n"}{end}'` | All components have `limits.cpu` and `limits.memory` defined |
| Sampling strategy configured appropriately | `kubectl get configmap jaeger-sampling-config -n observability -o yaml` | Sampling rate ≤ 10% for high-traffic services; adaptive or per-service strategies in place |
| Backup/snapshot schedule configured for Elasticsearch | `curl -s http://elasticsearch:9200/_snapshot` | At least one snapshot repository registered; policy runs daily |
| Network policy restricts Jaeger collector ingress | `kubectl get networkpolicy -n observability` | Only mesh-enrolled namespaces can reach collector ports 14250/14268 |
| Jaeger images pinned to specific versions | `kubectl get deploy -n observability -l app.kubernetes.io/name=jaeger -o jsonpath='{range .items[*]}{.spec.template.spec.containers[0].image}{"\n"}{end}'` | All images use explicit semver tags, not `latest` |
| Sensitive env vars use Secrets, not ConfigMaps | `kubectl get deploy -n observability -l app.kubernetes.io/name=jaeger -o yaml \| grep -i 'secretKeyRef\|valueFrom'` | ES credentials, TLS keys referenced from Secrets, not plaintext in ConfigMaps |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `{"level":"error","msg":"Failed to write span","error":"no space left on device"}` | Critical | Elasticsearch disk full; span indexing halted | Free disk space or expand ES PVC; enable ILM rollover policy immediately |
| `{"level":"warn","msg":"Dropping span; span queue full","queueSize":1000,"dropped":1}` | Warning | Collector span queue saturated; ingest rate exceeds write throughput | Scale collector replicas; increase `--collector.queue-size`; check ES write latency |
| `{"level":"error","msg":"Failed to store span","error":"index_not_found_exception"}` | Error | Jaeger daily index not yet created; ES index template missing | Apply Jaeger ES index template: `kubectl exec jaeger-collector -- /es-index-cleaner 0`; verify template |
| `WARN[0045] The sampling strategy store is not available` | Warning | Collector cannot reach sampling server or remote strategy endpoint | Check `--sampling.strategies-file` path; verify remote HTTP endpoint is reachable |
| `{"level":"error","ts":"...","msg":"Failed to get trace","error":"EOF"}` | Error | Elasticsearch returned empty response; possible ES node restart or timeout | Check ES node status; verify `--es.server-urls` points to healthy nodes |
| `{"level":"warn","msg":"ES cluster health is RED"}` | Critical | Elasticsearch cluster has unassigned shards; data may be incomplete | `curl -s http://es:9200/_cluster/health`; re-route or reallocate shards |
| `msg="Agent UDP packet dropped" reason="packet too large" size=65535` | Warning | Span payload exceeds UDP buffer; typically from oversized baggage or logs | Switch clients to use gRPC (port 14250) instead of UDP (6831/6832) |
| `{"level":"error","msg":"gRPC connection failed","target":"jaeger-collector:14250"}` | Error | Agent cannot reach collector; DNS or network policy issue | Verify NetworkPolicy allows agent→collector on 14250; check DNS resolution |
| `time="..." level=error msg="HTTP request failed" status=429` | Error | Elasticsearch rejecting writes due to bulk queue overflow | Reduce collector batch size `--es.bulk.size`; add ES data nodes |
| `{"level":"warn","msg":"Index already exists; skipping creation"}` | Info | Index template re-applied on restart; benign but noisy | Suppress by pre-creating indices or using `--es.create-index-templates=false` |
| `level=error msg="Failed to close span writer" error="context deadline exceeded"` | Error | Shutdown timeout exceeded; ES write flushing stalled | Increase pod `terminationGracePeriodSeconds`; check ES write latency under load |
| `{"level":"error","msg":"Archive storage is not configured"}` | Warning | Query service received archive trace request with no archive backend set | Configure archive storage (`--archive.storage.enabled=true`) or suppress archive UI tab |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `index_not_found_exception` | Elasticsearch index for the queried date does not exist | Traces for that time period not queryable | Run index initialisation job; check ILM policy; verify date-suffixed indices exist |
| `es_bulk_rejected_execution_exception` | ES bulk thread pool queue full; writes rejected | Spans dropped; trace data lost for affected window | Scale ES data nodes; reduce `--es.bulk.workers`; add back-pressure to collector |
| `too_many_requests (429)` from ES | ES circuit breaker or bulk rejection | Collector write throughput reduced; spans queued or dropped | Monitor ES JVM heap; add data nodes; enable adaptive replica selection |
| `RESOURCE_EXHAUSTED` (gRPC) | Collector gRPC server overloaded; queue full | Client spans rejected; tracing data loss for affected services | Increase `--collector.grpc-server.max-message-size`; scale collector horizontally |
| `UNAVAILABLE` (gRPC from agent) | Collector endpoint unreachable at connection time | All spans from that agent node lost | Check collector pod readiness; verify Service and NetworkPolicy; check DNS |
| `EOF` on ES query | Elasticsearch closed connection before full response | Query returns empty or partial trace | Check ES node heap usage; increase `--es.timeout`; check ES slow log |
| `query timeout` in Jaeger Query UI | Trace retrieval exceeded `--query.timeout` | User sees "error fetching trace" in UI | Increase `--query.timeout`; optimise ES query with proper index sorting |
| `Span context not found / invalid` | Trace context propagation broken; span cannot be correlated | Trace appears broken or single-span only | Verify instrumentation libraries propagate `uber-trace-id` or W3C `traceparent` headers |
| `archive trace not found` | No archive storage backend or trace TTL exceeded | Old traces not retrievable via UI | Configure archive ES backend; adjust `--es.max-span-age` or ILM max age |
| `SAMPLING_STRATEGY_TYPE_NOT_FOUND` | Requested sampling strategy type unknown to remote server | Service falls back to default probabilistic 0.001 | Update remote sampling config; verify strategy JSON schema |
| `collector queue drain timeout` | Graceful shutdown: queue not empty when deadline hit | In-flight spans may be lost on pod restart | Increase `terminationGracePeriodSeconds`; reduce queue size; scale before draining |
| `cluster_block_exception [FORBIDDEN/8/index write]` | ES index is read-only (disk watermark triggered) | All new spans rejected; trace ingest halted | Free disk space; `PUT /_settings {"index.blocks.write":false}`; expand PVC |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Span Ingest Blackout | `jaeger_collector_spans_received_total` flat; `jaeger_collector_spans_dropped_total` rising | `Failed to write span: index_write_blocked`; `cluster_block_exception` | `JaegerCollectorSpanDropRateHigh`; `ElasticsearchDiskUsageCritical` | ES disk full; write block auto-engaged | Delete old indices; expand PVC; apply ILM policy; re-enable writes |
| Collector Queue Saturation | `jaeger_collector_queue_length` at max; `jaeger_collector_spans_dropped_total` increasing | `Dropping span; span queue full`; repeated queue overflow messages | `JaegerQueueSaturation` | Spike in trace ingest rate; ES write throughput insufficient | Scale collector replicas; increase `--collector.num-workers`; tune ES bulk settings |
| ES Cluster Degraded | `elasticsearch_cluster_health_status{status="red"}` = 1; ES query latency p99 > 5 s | `ES cluster health is RED`; `EOF on ES query` | `ElasticsearchClusterHealthRed`; `JaegerQueryLatencyHigh` | Unassigned shards; ES data node failure | Reallocate shards; restart failed ES node; check PVC health |
| Sampling Misconfiguration | Per-service trace volume in Prometheus drops to near zero | `SAMPLING_STRATEGY_TYPE_NOT_FOUND`; `sampling strategy not available` | `JaegerTraceSamplingRateLow` unexpectedly | Remote sampling server down; wrong strategy config deployed | Restore sampling server; validate strategy JSON; use probabilistic fallback |
| Agent UDP Packet Loss | `jaeger_agent_reporter_batch_failures_total` rising; no corresponding collector receive errors | `UDP packet dropped: packet too large` | `JaegerAgentBatchFailureHigh` | Large spans exceed the UDP datagram limit (max 65507 bytes payload); oversized baggage/logs | Migrate clients to gRPC port 14250; reduce span payload size |
| Index Rollover Failure | `jaeger-span-<date>` indices not created daily; `index_not_found_exception` for new dates | `Failed to store span: index_not_found_exception` | `JaegerIndexCreationFailed` | ILM rollover policy misconfigured; ES permissions missing | Check ILM policy; verify `jaeger` user has `manage_index_templates` privilege |
| Query Authentication Failure | `jaeger_query_requests_total{result="error"}` spike; 401/403 from query HTTP API | `Authentication failed`; `invalid token` in query logs | `JaegerQueryErrorRateHigh` | OAuth2 proxy misconfigured after Keycloak secret rotation | Update OAuth2 proxy client secret; restart jaeger-query; re-test login flow |
| gRPC Collector Crash Loop | `jaeger_collector` pod `restartCount` > 3; `CrashLoopBackOff` in pod status | `panic: runtime error`; `gRPC server failed to start` | `JaegerCollectorCrashLooping` | Bad config flag or incompatible ES version on upgrade | Check `kubectl logs`; rollback Helm release; validate ES version compatibility matrix |
| Trace Context Propagation Break | `jaeger_tracer_traces_started_total` high but multi-span traces absent; all traces single-span | `Span context not found`; `invalid trace ID format` | `JaegerTracesFragmented` | Library version mismatch; W3C vs B3 header incompatibility after client deploy | Audit propagation format across services; align all clients on single propagation standard |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `connection refused` on span submit | OpenTelemetry SDK, Jaeger client | jaeger-agent or collector pod down | `kubectl get pods -n tracing | grep jaeger`; telnet port 6831/14268 | Set SDK `WithInsecure()` + retry; deploy agent as DaemonSet to survive collector outages |
| `context deadline exceeded` on span export | OTel Go/Java SDK | Collector overloaded; queue full | `jaeger_collector_queue_length` at max; `jaeger_collector_spans_dropped_total` rising | Increase `--collector.queue-size`; scale collector replicas; add backpressure at SDK |
| Traces missing in UI but no SDK error | All Jaeger clients | Sampling rate set to 0; remote sampler server unreachable | `jaeger_sampler_queries_total{result="err"}` rising | Fall back to `probabilistic` sampler in SDK config; restore sampling server |
| `index_not_found_exception` in query results | Jaeger UI / HTTP API | Daily ES index for requested date not created | `curl -s http://elasticsearch:9200/_cat/indices | grep jaeger-span` | Run Jaeger index initializer job; check ILM rollover policy |
| HTTP 401 from Jaeger Query API | Custom dashboard / API client | OAuth2 proxy token expired; Keycloak secret rotated | `kubectl logs deploy/jaeger-query | grep "Authentication failed"` | Rotate OAuth2 proxy client secret; restart jaeger-query pod |
| HTTP 504 / timeout loading trace | Browser, Jaeger UI | ES query timeout on large trace; too many spans | `jaeger_query_requests_total{result="error"}` spike; ES slow-log entries | Add `--query.max-clock-skew-adjustment`; set ES `search.max_buckets` higher; paginate large traces |
| Partial trace — spans from one service missing | Distributed app | UDP MTU exceeded by large span payload | `jaeger_agent_reporter_batch_failures_total` rising on agent for that service | Switch client transport to gRPC (port 14250); reduce span log/tag size |
| `failed to save span: StatusCode=429` | OTel SDK | ES bulk indexing rate-limited or throttled | ES `_nodes/stats` shows `bulk.rejected_threads` > 0 | Increase ES bulk thread pool size; scale ES data nodes; reduce collector flush frequency |
| Incorrect service names in UI | Jaeger UI users | SDK resource attribute `service.name` not set | Query `GET /api/services` — check for `unknown` entries | Set `OTEL_SERVICE_NAME` env var in each pod; validate OTel resource attributes at startup |
| Spans exported but not linked (broken trace graph) | Jaeger UI | Trace context not propagated across async boundary (Kafka, gRPC) | Trace shows isolated spans; parent span ID missing | Enable W3C TraceContext propagation in SDK; instrument async producers/consumers |
| `transport: Error while dialing` | Go gRPC Jaeger client | TLS mismatch between client and collector | `kubectl logs deploy/jaeger-collector | grep "TLS handshake"` | Match TLS config on both sides; use `--collector.grpc.tls.enabled` consistently |
| Old traces not found (>7 days) | Jaeger UI / API client | ILM or TTL deleting indices before expected retention | `curl http://elasticsearch:9200/_cat/indices?v | grep jaeger` | Increase ES ILM `min_age`; verify `--es.max-span-age` on collector |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Elasticsearch disk fill | Disk usage growing 2–3% daily; no ILM delete phase firing | `curl -s http://elasticsearch:9200/_cat/nodes?v&h=name,disk.used_percent | sort -k2 -rn` | Days (depends on ingest rate) | Add ILM delete phase; expand PVC; reduce trace sampling rate |
| Collector queue depth creep | `jaeger_collector_queue_length` trending upward during business hours | `kubectl exec deploy/jaeger-collector -- curl -s localhost:14269/metrics | grep queue_length` | Hours to days | Scale collector replicas; increase `--collector.num-workers`; tune ES bulk batch size |
| ES shard count proliferation | Daily index creation without old index cleanup; cluster-state size growing | `curl http://elasticsearch:9200/_cat/shards?v | wc -l` | Weeks | Enable ILM; set `--es.num-shards` and `--es.num-replicas` appropriately; consolidate indices |
| Sampling strategy drift | Trace volume declining gradually as services restart with cached stale strategies | `jaeger_sampler_updates_total` low or flat; compare expected vs actual sampling rates in Prometheus | Days | Force sampler refresh; validate strategy JSON via `/api/sampling?service=<svc>`; restart sampling server |
| Agent UDP buffer overflow | Periodic silent span drops at high request rates; no obvious errors | `netstat -s | grep 'receive buffer errors'` on agent pod's node | Hours; worst during traffic spikes | Increase UDP receive buffer (`net.core.rmem_max`); migrate to gRPC transport |
| Elasticsearch GC pressure | JVM old-gen usage creeping; GC pause p99 increasing week-over-week | `curl http://elasticsearch:9200/_nodes/stats/jvm | python3 -m json.tool | grep heap_percent` | Days before OOM | Reduce heap field data cache; add ES data nodes; enable ILM to reduce active index count |
| Trace ID collision (very rare) | Duplicate trace IDs causing merged/corrupted traces in UI | Query specific trace ID — returns multiple unrelated traces | Weeks to months | Ensure 128-bit trace IDs enabled in SDK; audit SDKs generating 64-bit IDs |
| Query service memory leak | `container_memory_working_set_bytes` for jaeger-query grows without bound | `kubectl top pod -n tracing | grep jaeger-query` watched over days | Days to weeks | Restart jaeger-query pod; update to latest Jaeger release; set memory limit + OOMKill restart policy |
| Network buffer exhaustion between agent and collector | Agent `reporter_batch_failures_total` slowly climbing | `kubectl exec <agent-pod> -- curl -s localhost:14271/metrics | grep batch_failures` | Hours under load | Increase collector replicas; switch to gRPC with flow control; tune agent `--reporter.grpc.retry.max` |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Collects: pod status, span ingest rates, ES health, queue depth, sampling status

NS=${1:-"tracing"}
echo "=== Jaeger Pod Status ==="
kubectl get pods -n "$NS" -o wide

echo -e "\n=== Collector Span Ingest Rate (last 60s) ==="
COLLECTOR=$(kubectl get pod -n "$NS" -l app=jaeger,app.kubernetes.io/component=collector \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "$COLLECTOR" ]; then
  kubectl exec -n "$NS" "$COLLECTOR" -- \
    curl -s localhost:14269/metrics 2>/dev/null \
    | grep -E 'spans_received|spans_dropped|queue_length|in_queue_latency'
fi

echo -e "\n=== Elasticsearch Cluster Health ==="
kubectl exec -n "$NS" deploy/jaeger-query -- \
  curl -s "http://elasticsearch:9200/_cluster/health?pretty" 2>/dev/null \
  || curl -s "http://elasticsearch.elastic-system:9200/_cluster/health?pretty" 2>/dev/null | head -20

echo -e "\n=== Elasticsearch Index Summary ==="
curl -s "http://elasticsearch:9200/_cat/indices/jaeger*?v&h=index,health,docs.count,store.size" 2>/dev/null | sort -k4 -rh | head -15

echo -e "\n=== Sampling Strategy Endpoint ==="
kubectl port-forward -n "$NS" svc/jaeger-query 16686:16686 &>/dev/null &
PF_PID=$!; sleep 1
curl -s "http://localhost:16686/api/sampling?service=test" 2>/dev/null | python3 -m json.tool | head -20
kill "$PF_PID" 2>/dev/null

echo -e "\n=== Recent Jaeger Errors ==="
kubectl logs -n "$NS" -l app=jaeger --since=15m 2>/dev/null | grep -iE 'error|warn|panic|fail' | tail -20
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Triage: span throughput, drop rate, ES write latency, query latency

NS=${1:-"tracing"}
COLLECTOR=$(kubectl get pod -n "$NS" -l app.kubernetes.io/component=collector \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
QUERY=$(kubectl get pod -n "$NS" -l app.kubernetes.io/component=query \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

echo "=== Collector Throughput Metrics ==="
[ -n "$COLLECTOR" ] && kubectl exec -n "$NS" "$COLLECTOR" -- \
  curl -s localhost:14269/metrics | grep -E 'spans_received|spans_dropped|save_latency|queue'

echo -e "\n=== Query Service Latency Metrics ==="
[ -n "$QUERY" ] && kubectl exec -n "$NS" "$QUERY" -- \
  curl -s localhost:16687/metrics | grep -E 'request_duration|requests_total'

echo -e "\n=== Elasticsearch Indexing Stats ==="
curl -s "http://elasticsearch:9200/jaeger-span-*/_stats/indexing?pretty" 2>/dev/null \
  | python3 -c "import json,sys; d=json.load(sys.stdin); s=d['_all']['total']['indexing']; \
    print(f'index_total={s[\"index_total\"]} index_failed={s[\"index_failed\"]} throttle_time_ms={s[\"throttle_time_in_millis\"]}')" 2>/dev/null

echo -e "\n=== Top Services by Span Volume ==="
curl -s "http://localhost:16686/api/services" 2>/dev/null | python3 -m json.tool 2>/dev/null | grep -c '"' && \
  echo "(Use Jaeger UI → Search to compare per-service trace counts)"

echo -e "\n=== Collector Pod Resources ==="
kubectl top pod -n "$NS" --containers 2>/dev/null | grep jaeger
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Audit: ES connectivity, UDP/gRPC ports, storage usage, ILM policy status

NS=${1:-"tracing"}

echo "=== Collector → ES Connectivity ==="
COLLECTOR=$(kubectl get pod -n "$NS" -l app.kubernetes.io/component=collector \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
[ -n "$COLLECTOR" ] && kubectl exec -n "$NS" "$COLLECTOR" -- \
  sh -c 'nc -zv elasticsearch 9200 2>&1 || echo "ES UNREACHABLE"'

echo -e "\n=== Jaeger Service Ports ==="
kubectl get svc -n "$NS" -o custom-columns=\
'NAME:.metadata.name,PORTS:.spec.ports[*].port,PROTOCOL:.spec.ports[*].protocol'

echo -e "\n=== Elasticsearch Storage per Node ==="
curl -s "http://elasticsearch:9200/_cat/nodes?v&h=name,disk.used,disk.avail,disk.used_percent" 2>/dev/null | head -10

echo -e "\n=== ILM Policy Status for Jaeger Indices ==="
curl -s "http://elasticsearch:9200/jaeger-span-*/_ilm/explain?only_errors=false&only_managed=true&pretty" 2>/dev/null \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(k,v.get('phase'),v.get('step')) for k,v in d.get('indices',{}).items()]" 2>/dev/null | head -20

echo -e "\n=== UDP Buffer Sizes (DaemonSet agent nodes) ==="
kubectl get pods -n "$NS" -l app.kubernetes.io/component=agent -o wide 2>/dev/null | head -5
echo "Check node: sysctl net.core.rmem_max net.core.rmem_default"

echo -e "\n=== Jaeger ConfigMap / Secret Summary ==="
kubectl get configmap,secret -n "$NS" | grep jaeger
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| High-volume service flooding collector | `jaeger_collector_spans_dropped_total` rising; other services' traces missing | `GET /api/services` + compare span rates in Jaeger UI per service | Set per-service sampling rate via remote sampling strategy; reduce noisy service's sample rate to 0.01 | Enforce per-service sampling caps in sampling server config |
| Elasticsearch heap pressure from field data cache | ES query latency spikes for all Jaeger users; heap old-gen full | `GET /_nodes/stats/jvm` — check `heap_used_percent` and `fielddata.memory_size_in_bytes` | Evict field data cache: `POST /_cache/clear?fielddata=true`; reduce `indices.fielddata.cache.size` | Avoid analyzed string fields in span tags; set field data circuit breaker limit |
| Collector CPU saturation from span decode | All services' spans queued; `jaeger_collector_queue_length` rises uniformly | `kubectl top pod -n tracing | grep collector` — CPU near limit | Scale collector deployment; increase CPU request/limit | HPA on collector based on `jaeger_collector_queue_length` metric |
| ES disk I/O contention with other indices | Jaeger write latency spikes correlating with other app index activity | `GET /_nodes/stats/indices` — check `indexing.index_time_in_millis` per index | Assign Jaeger indices to dedicated ES data nodes via index routing (`index.routing.allocation.require`) | Use dedicated ES cluster or dedicated data tier for Jaeger |
| Shared Kubernetes node with memory-hungry pods | jaeger-agent DaemonSet pod OOMKilled; agent pod restarts cause span loss | `kubectl describe node <node> | grep -A5 "Allocated resources"` | Set guaranteed QoS on jaeger-agent (`requests == limits`); add `priorityClassName` | Use node labels to co-locate jaeger-agent with pods that have low memory usage |
| Large span baggage from one service bloating ES | Average document size growing; ES storage fill rate accelerates | Check ES index `_stats` for `store.size` vs `docs.count` ratio | Enforce baggage size limits in SDK (`BaggageRestrictionManager`); drop oversized spans at collector | Add collector `--collector.tags-as-fields.all=false`; limit baggage keys per service |
| Query service fetch starving under concurrent UI users | Jaeger UI slow for all users when one user runs a large trace query | `jaeger_query_requests_total` and `request_duration` histogram; check for long-running queries | Set `--query.max-clock-skew-adjustment` timeout; add query concurrency limit in proxy | Put Nginx rate limiting in front of Jaeger Query API; separate read/write ES endpoints |
| UDP port 6831 contention on shared host network | Services on same node interfere with each other's agent UDP routing | Check `netstat -su` for UDP receive errors on node; compare per-pod span loss rates | Switch affected services to gRPC transport (port 14250) with flow control | Prefer gRPC transport for all new services; treat UDP as legacy fallback only |
| ES segment merge I/O blocking writes | Span ingest latency spikes periodically; `merge.current` in ES stats elevated | `curl http://elasticsearch:9200/_nodes/stats/indices | grep merge` | Force merge during off-peak: `POST /jaeger-span-<old-date>/_forcemerge?max_num_segments=1` | Set `index.merge.scheduler.max_thread_count=1` on Jaeger indices; schedule force-merge via cron |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Elasticsearch cluster red/unavailable | Collector cannot write spans → collector queue fills → spans dropped → all trace data lost | All teams lose distributed tracing visibility | `jaeger_collector_spans_dropped_total` spikes; `curl -s http://elasticsearch:9200/_cluster/health | jq .status` returns `"red"` | Set `--span-storage.type=badger` as fallback; reduce sampling rates to 0.001 to limit ingest load |
| Elasticsearch disk full (>95%) | ILM rollover fails → indices read-only → collector writes rejected with 403 | No new spans ingested; stale trace data only | ES log: `flood stage disk watermark exceeded`; `curl http://elasticsearch:9200/_cat/allocation?v` shows `>95%` disk | Delete oldest Jaeger indices: `curl -X DELETE http://elasticsearch:9200/jaeger-span-$(date -d '-30 days' +%Y-%m-%d)`; trigger ILM policy manually |
| jaeger-collector pod OOMKilled | Spans buffered in agent UDP queues → UDP overflow → span loss at sender | Partial traces; high-cardinality services lose data first | `kubectl describe pod -n tracing <collector> | grep OOMKilled`; `jaeger_collector_queue_length` near max | Scale collector replicas; increase memory limit; enable `--collector.queue-size-memory` cap |
| jaeger-agent DaemonSet evicted from node | Services on node emit spans to dead UDP port → dropped silently | Services on that node invisible in traces | `kubectl get pods -n tracing -o wide | grep agent` shows missing nodes; spans from node IPs absent in Jaeger UI | Restart DaemonSet pod: `kubectl rollout restart daemonset/jaeger-agent -n tracing`; check node resource pressure |
| Kafka topic `jaeger-spans` lag accumulates | Ingestion pipeline stalls; older spans expire before being written | Delayed trace visibility; eventual span loss if retention exceeded | `kafka-consumer-groups.sh --describe --group jaeger-ingester` — lag in millions; `jaeger_ingester_spans_ingested_total` rate drops | Scale jaeger-ingester; increase Kafka partition count; bump consumer group parallelism |
| jaeger-query pod unresponsive | Developers cannot investigate incidents; UI returns 502 | Loss of observability tooling during active incidents | `kubectl get pod -n tracing -l app.kubernetes.io/component=query`; HTTP probe on port 16686 fails | Restart query pod; temporarily expose ES directly for kibana fallback; check ES query timeout `--es.timeout` |
| Remote sampling server unreachable | Client-side agents fall back to default sampling (100% or 0%) | Either trace volume explosion overloading collector, or complete trace blackout | `jaeger_sampler_queries_total{result="err"}` rising; apps logging `failed to fetch sampling strategy` | Ensure `--sampling.strategies-file` fallback is configured on collector; redeploy sampling server |
| Clock skew >5 min between services | Spans appear out-of-order or in wrong parent-child relationships; trace assembly fails | Misleading traces; root cause analysis incorrect | Jaeger UI shows negative durations; `jaeger_query_requests_total{operation="FindTraces",result="err"}` up | Sync NTP on all nodes: `chronyc makestep`; adjust `--query.max-clock-skew-adjustment=1m` on query service |
| Network policy blocks collector→ES on 9200 | All span writes fail; collector logs `connection refused` to ES | Complete tracing blackout | `kubectl logs -n tracing deployment/jaeger-collector | grep "connection refused"`; `kubectl exec collector -- nc -zv elasticsearch 9200` | Update NetworkPolicy to allow egress 9200; verify ES service DNS resolves inside tracing namespace |
| Upstream microservice SDK misconfigured (wrong agent host) | Spans never reach agent; service invisible in Jaeger | Single service missing from traces; parent services show gaps | Absence of service in `GET /api/services`; check service logs for `dial udp: no route to host` | Fix SDK config: `JAEGER_AGENT_HOST=jaeger-agent.tracing.svc.cluster.local`; redeploy service |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Jaeger version upgrade (e.g., 1.x → 1.y) | Incompatible ES index mapping; collector crashes with `mapping conflict` | Immediate on first write | `kubectl logs -n tracing deployment/jaeger-collector | grep "mapping"` | Rollback image: `kubectl set image deployment/jaeger-collector collector=jaegertracing/jaeger-collector:<prev>`; re-run ES schema migration |
| Elasticsearch version upgrade (6→7 or 7→8) | Jaeger ES client uses deprecated APIs; `404` or `400` errors on span writes | Immediate | Collector logs `[unsupported]` or `[removed] API endpoint`; ES deprecation log `/var/log/elasticsearch/deprecation.log` | Pin Jaeger release compatible with ES version; check Jaeger release notes for ES version matrix |
| Changing `--es.num-shards` or `--es.num-replicas` | New indices created with different shard count; query performance degrades or ES goes red | Next ILM rollover (~24h) | `curl http://elasticsearch:9200/_cat/indices/jaeger-span-*?v | awk '{print $5,$6}'` — inconsistent shard counts | Delete misconfigured index template; restore from `jaeger-es-index-cleaner` snapshot; re-apply correct `--es.num-shards` |
| Sampling strategy config change | Sudden spike or drop in ingested span volume; collector overloaded or traces disappear | Seconds (client-side polling interval) | Compare `jaeger_collector_spans_received_total` rate before/after config push; check sampling server access log | Revert `sampling-strategies.json` ConfigMap: `kubectl rollout undo deployment/jaeger-sampling` |
| Enabling `--es.tags-as-fields.all=true` | ES field count explosion → mapping explosion → index creation fails with `limit of total fields exceeded` | Hours to days (gradual) | `curl http://elasticsearch:9200/jaeger-span-*/_mapping | jq '.[].mappings.properties | keys | length'` > 1000 | Disable flag; delete bloated indices; add `index.mapping.total_fields.limit=2000` temporarily |
| Adding new Jaeger collector behind load balancer | Some collectors not receiving Kafka config; split ingestion → duplicate or missing spans | Minutes | Verify all collectors registered in service mesh; check `jaeger_collector_spans_received_total` by pod | Remove new collectors; fix deployment ConfigMap; redeploy with verified config |
| TLS certificate rotation for ES client auth | Collector fails to connect with `certificate has expired` or `unknown CA` | At cert expiry | Collector logs `tls: failed to verify certificate`; `openssl s_client -connect elasticsearch:9200` shows cert chain | Mount renewed cert Secret; `kubectl rollout restart deployment/jaeger-collector -n tracing` |
| Changing `--es.index-prefix` | New indices created with different prefix; old traces unreachable; query returns empty | Immediate for new spans | `curl http://elasticsearch:9200/_cat/indices?v | grep jaeger` shows split prefixes | Revert prefix change; use Jaeger's `--es.index-prefix` consistently across all components |
| Kubernetes resource limit reduction | Collector throttled on CPU; span processing latency increases; queue grows | Minutes under load | `kubectl top pod -n tracing | grep collector` shows CPU throttled; `kubectl describe pod` shows `Throttling` events | Revert resource patch: `kubectl patch deployment jaeger-collector -n tracing --patch '{"spec":{"template":{"spec":{"containers":[{"name":"collector","resources":{"limits":{"cpu":"2"}}}]}}}}'` |
| Upgrading Jaeger Helm chart values (env var rename) | Collector starts with default (wrong) config; spans written to wrong ES host | Immediate on rollout | `kubectl exec -n tracing <collector> -- env | grep ES` — verify `SPAN_STORAGE_TYPE`, `ES_SERVER_URLS` correct | `helm rollback jaeger <previous-revision> -n tracing`; diff values files |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| ES primary shard unassigned (split-brain after node loss) | `curl http://elasticsearch:9200/_cluster/health?pretty` — `unassigned_shards > 0` | Jaeger writes partial; some traces missing shards | Partial trace data loss; query returns incomplete results | `curl http://elasticsearch:9200/_cluster/reroute?retry_failed=true`; investigate with `/_cluster/allocation/explain` |
| Duplicate trace IDs from clock rollback | Traces with identical IDs overwrite each other in ES | Jaeger UI shows truncated or merged traces | Incorrect tracing data; misleading performance analysis | Enable NTP sync; Jaeger does not deduplicate — purge affected time window indices and re-ingest if possible |
| Span order inversion from async Kafka consumer | Spans arrive out of order; child spans written before parent | Broken trace trees in UI; orphaned span nodes | Root cause analysis incorrect | Ensure `--ingester.deadlockInterval=0` is not set; Kafka consumer ordering guarantees per partition; co-locate parent/child spans in same partition by traceID |
| Config drift between collector replicas (different ES endpoints) | Some traces written to secondary ES; queries return partial results | Non-deterministic trace completeness depending on which collector received spans | Silent data inconsistency | `kubectl get deploy jaeger-collector -o yaml | grep -A5 env` across all replicas; enforce GitOps single-source config |
| ILM policy divergence (different policies on same index) | Old spans not rolled over; index grows unbounded; disk fills | Storage exhaustion; ES performance degradation | Eventual complete ingestion failure | `curl http://elasticsearch:9200/_ilm/policy/jaeger-ilm-policy` — verify single canonical policy; delete duplicates; force rollover: `curl -X POST http://elasticsearch:9200/jaeger-span-write/_rollover` |
| Stale read from ES replica lag | Jaeger query returns traces that are seconds behind real-time | Developers see traces missing the latest spans during active debugging | Minor — debugging latency only | Set `--es.sniffer=false` to query primary only; or accept eventual consistency for near-real-time use |
| jaeger-agent running old binary with new collector API | Spans dropped silently; `gRPC status 12 UNIMPLEMENTED` errors | Missing traces from nodes running old agent | Services on outdated nodes invisible | `kubectl rollout restart daemonset/jaeger-agent -n tracing`; verify all pods using same image tag |
| Routing inconsistency: two Jaeger deployments in same cluster | Services hitting different Jaeger instances; no unified trace view | Traces split across two backends; impossible to correlate cross-service | Investigation paralyzed during incidents | Audit `kubectl get svc -A | grep jaeger`; decommission duplicate; update all service `JAEGER_AGENT_HOST` env vars |
| ES field mapping conflict after SDK upgrade (new tag types) | Index rejects documents with `mapper_parsing_exception` | Collector logs `failed to index span`; affected services' traces dropped | Data loss for services with new span attributes | Add field mapping to ES template before deploying new SDK; use dynamic mapping strict mode to fail fast |
| Cert mismatch between collector and ES (mutual TLS) | Collector rejects connection with `certificate signed by unknown authority` | Complete ingestion failure | Full tracing blackout | `kubectl get secret jaeger-es-tls -n tracing -o yaml`; verify `ca.crt` matches ES CA; redeploy with correct secret |

## Runbook Decision Trees

### Decision Tree 1: Spans Being Dropped / Traces Incomplete

```
Is jaeger_collector_spans_dropped_total rate > 0?
├── YES → Is jaeger_collector_queue_capacity at 100%? (check: kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep queue_capacity)
│         ├── YES → Is ES write latency p99 > 2s? (check: curl -s http://elasticsearch:9200/_nodes/stats/indices | jq '.nodes[].indices.indexing.index_time_in_millis')
│         │         ├── YES → Root cause: ES write bottleneck → Fix: increase ES indexing threadpool (PUT /_cluster/settings -d '{"persistent":{"thread_pool.write.queue_size":1000}}'); scale collector replicas: kubectl scale deployment jaeger-collector -n tracing --replicas=5
│         │         └── NO  → Root cause: Collector CPU/memory throttled → Fix: kubectl edit deployment jaeger-collector -n tracing; raise resource limits; check HPA: kubectl get hpa -n tracing
│         └── NO  → Is collector pod count < expected? (check: kubectl get pods -n tracing -l app.kubernetes.io/component=collector --no-headers | wc -l)
│                   ├── YES → Root cause: Pod crash-loop or pending → Fix: kubectl describe pods -n tracing -l app.kubernetes.io/component=collector; check Events; fix image/config issue; kubectl rollout restart deployment/jaeger-collector -n tracing
│                   └── NO  → Root cause: Sampling rate too high for current capacity → Fix: adjust adaptive sampling: kubectl edit configmap jaeger-sampling-config -n tracing; lower default_sampling_probability
└── NO  → Is jaeger_query_requests_total{result="err"} rising? (check: kubectl exec -n tracing deploy/jaeger-query -- wget -qO- http://localhost:16687/metrics | grep requests_total)
          ├── YES → Root cause: Query pod ES connectivity issue → Fix: kubectl rollout restart deployment/jaeger-query -n tracing; verify ES index exists: curl http://elasticsearch:9200/_cat/indices/jaeger-span-*?v
          └── NO  → Escalate: Jaeger maintainer + ES admin; bring span drop rate graph, ES slow log, collector pod logs from last 30min
```

### Decision Tree 2: Jaeger UI Returns No Traces / Empty Results

```
Is jaeger-query pod Running and Ready? (check: kubectl get pods -n tracing -l app.kubernetes.io/component=query)
├── NO  → Is pod CrashLoopBackOff?
│         ├── YES → kubectl logs -n tracing deploy/jaeger-query --previous | grep -i "error\|fatal\|panic"
│         │         → If ES connection refused: verify ES service DNS: kubectl exec -n tracing deploy/jaeger-query -- nslookup elasticsearch
│         │         → Fix: kubectl rollout restart deployment/jaeger-query -n tracing
│         └── NO  → Pod pending: kubectl describe pod -n tracing <query-pod> | grep -A10 Events; fix node resource issue or image pull
└── YES → Does ES contain recent indices? (check: curl -s http://elasticsearch:9200/_cat/indices/jaeger-span-*?v | sort -k3 -r | head -5)
          ├── NO  → Root cause: Collectors not writing to ES → Fix: verify collector ES_SERVER_URLS env var; check STORAGE_TYPE=elasticsearch; kubectl describe deployment jaeger-collector -n tracing | grep -A5 Env
          └── YES → Is the time range in the UI covering an active period?
                    ├── NO  → User error: guide to correct time range in Jaeger UI; check that NTP sync is correct on nodes: chronyc tracking
                    └── YES → Root cause: ES index mapping or alias mismatch → Fix: curl -s http://elasticsearch:9200/_alias/jaeger-span-read; recreate alias if missing; check jaeger-es-index-cleaner cron job hasn't deleted live data: kubectl get cronjob -n tracing
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| ES index size explosion | High-cardinality service/operation tags; tags with UUIDs or user IDs in span metadata | `curl -s http://elasticsearch:9200/_cat/indices/jaeger-span-*?v&s=store.size:desc | head -10` | ES disk full → all span writes fail; cluster RED | Enable `index.routing.allocation.require._tier_preference=data_hot`; delete oldest indices via jaeger-es-index-cleaner | Tag sanitization in instrumentation; reject high-cardinality tags at collector via `--collector.tags-as-fields.all=false` |
| Runaway trace volume from one service | Buggy service emitting spans in tight loop | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'spans_received_total{svc'` | Collector queue saturation; span drops for all other services | Set per-service rate limit in sampling config: `operationStrategies` with `rateLimit`; kubectl edit configmap jaeger-sampling-config -n tracing | Per-service sampling rate caps; adaptive sampling with max operations limit |
| Collector CPU spike from regex tag filtering | Overly complex tag filter regex in collector config | `kubectl top pods -n tracing -l app.kubernetes.io/component=collector` | Collector throughput degraded; span drops increase | Remove/simplify tag filter regex: kubectl edit deployment jaeger-collector -n tracing; remove --collector.tags-as-fields.dotted-fields-separator if set | Test regex filters in staging; use simple string matching over regex |
| ES _bulk request rejection (429) | ES indexing queue full due to burst ingestion | `curl -s http://elasticsearch:9200/_nodes/stats | jq '.nodes[].thread_pool.write.rejected'` | Spans permanently lost; collector logs show bulk errors | Scale ES data nodes; increase `indices.memory.index_buffer_size` in ES; temporarily increase collector batch flush interval `--reporter.grpc.retry.max=10` | Auto-scaling ES data nodes; collector-side buffering with Kafka as intermediary |
| Jaeger agent DaemonSet UDP buffer overflow | High pod density on nodes; Jaeger agents receiving more spans than UDP buffer allows | `kubectl exec -n tracing <jaeger-agent-pod> -- wget -qO- http://localhost:14271/metrics | grep 'reporter_queue_length'` | Spans silently dropped at agent layer before reaching collector | Increase agent UDP buffer: `--processor.jaeger-compact.server-max-packet-size=65000`; switch to gRPC transport | Migrate from UDP to gRPC in SDKs; use OTLP exporter directly to collector |
| ES shard count explosion | Many small daily indices; shard count exceeds ES 1000-shard-per-node limit | `curl -s http://elasticsearch:9200/_cat/shards | wc -l` | New index creation fails; no new spans written | Run jaeger-es-index-cleaner immediately: `kubectl create job -n tracing --from=cronjob/jaeger-es-index-cleaner jaeger-clean-now` | Set `--es.num-shards=1` for low-volume deployments; enforce index TTL via ILM policy |
| Query pod OOM from deep time-range searches | Users querying full 30-day window with no service filter | `kubectl top pods -n tracing -l app.kubernetes.io/component=query` | Query pod OOM-killed; UI unavailable | kubectl rollout restart deployment/jaeger-query -n tracing; set `--query.max-clock-skew-adjustment` and add query timeouts | Set `--es.max-doc-count=10000` in query config; add Nginx/Envoy rate limiting on `/api/traces` endpoint |
| Disk I/O saturation from ES merge operations | After large bulk ingestion, ES segment merges saturate disk | `curl -s http://elasticsearch:9200/_nodes/stats/indices | jq '.nodes[].indices.merges'` | ES write latency spikes; span save latency p99 > 5s | Throttle merge: `PUT /_cluster/settings -d '{"transient":{"indices.store.throttle.max_bytes_per_sec":"100mb"}}'` | Use SSDs for ES data nodes; set `index.merge.policy.max_merge_at_once=5` |
| Sampling configuration causing 100% trace rate | Misconfigured adaptive sampling; all operations sampled at 1.0 | `curl -s http://jaeger-query.tracing:16686/api/sampling?service=<service>` | 10-100x span volume; ES and collector overwhelmed | Edit sampling configmap: `kubectl edit configmap jaeger-sampling-config -n tracing`; set `default_sampling_probability: 0.01` | GitOps control for sampling config; PR review gate for any sampling probability > 0.1 |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot service flooding collector | One service dominates `spans_received_total`; other services experience drops | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'jaeger_collector_spans_received_total' | sort -t= -k2 -rn | head -10` | Single high-cardinality or buggy service sending disproportionate span volume | Apply per-service rate-limit sampling: `kubectl edit configmap jaeger-sampling-config -n tracing`; set `rateLimit` under `operationStrategies` |
| Collector connection pool to ES exhausted | Span save latency p99 > 5 s; `jaeger_collector_save_latency_bucket` upper buckets filling | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'save_latency'` | `--es.bulk.workers` too low for throughput; ES indexing backpressure | Increase `--es.bulk.workers=4` and `--es.bulk.size=5000000`; check ES thread pool: `curl -s http://elasticsearch:9200/_nodes/stats | jq '.nodes[].thread_pool.write'` |
| Collector JVM GC pressure | Collector CPU spikes periodically; `spans_dropped_total` rate increases during GC | `kubectl top pods -n tracing -l app.kubernetes.io/component=collector`; `kubectl logs -n tracing -l app.kubernetes.io/component=collector | grep -i "GC\|pause"` | Insufficient heap for in-flight span batch buffers | Increase collector JVM heap: add `JAVA_OPTS=-Xmx1g -Xms1g` env var; or limit `--collector.queue-size=10000` to reduce buffer memory |
| Query thread pool saturation | Jaeger UI search returns 504 or hangs; query pod CPU at limit | `kubectl exec -n tracing deploy/jaeger-query -- wget -qO- http://localhost:16687/metrics | grep 'query_requests_total'` | Concurrent deep time-range ES searches consuming all query goroutines | Set `--query.max-clock-skew-adjustment=0`; add `--es.max-doc-count=10000`; scale query pods: `kubectl scale deploy/jaeger-query -n tracing --replicas=3` |
| Slow ES query on large span documents | `api/traces` endpoint p99 > 10 s for any service; browser loading spinner | `curl -s http://elasticsearch:9200/_nodes/stats | jq '.nodes[].indices.search.query_time_in_millis'` | Missing ES index on `traceID` field; large process tag objects bloating documents | Run `PUT /jaeger-span-*/_mapping -d '{"properties":{"traceID":{"type":"keyword"}}}'`; enable `--span-storage.type=grpc-plugin` if using custom storage |
| CPU steal from ES node co-tenancy | Collector save latency spikes 2-5× at irregular intervals; no obvious cause in Jaeger | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'save_latency_bucket'`; check node: `kubectl describe node <es-node> | grep cpu` | ES nodes on noisy-neighbor VMs; CPU steal reduces ES I/O throughput | Move ES data nodes to dedicated node pool: add node affinity; or switch to managed ES (OpenSearch Service) on dedicated instances |
| Lock contention in Jaeger ES bulk indexer | Intermittent save latency spikes; bulk worker threads serializing on shared channel | `kubectl logs -n tracing -l app.kubernetes.io/component=collector | grep -i "timeout\|retry\|flush"` | Jaeger Go bulk indexer uses mutex on flush; high span rate causes contention | Increase `--es.bulk.workers` to match ES data node count; reduce `--es.bulk.flush-interval=200ms` to flush more frequently |
| Span serialization overhead for large tags | Spans with large tag payloads (stack traces, HTTP bodies) take 10-50× longer to serialize | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'in_queue_latency'` | Unbounded tag values in application instrumentation | Set collector tag length limits: add `--collector.tags-as-fields.all=false`; drop tags > 1 KB in OTel Collector processor: `filter/truncate` |
| Batch size misconfiguration causing ES 413 | ES bulk endpoint returns 413 Request Entity Too Large; spans dropped | `kubectl logs -n tracing -l app.kubernetes.io/component=collector | grep "413\|request entity"` | `--es.bulk.size` set too high; individual spans with large payloads push batch over ES `http.max_content_length` | Reduce `--es.bulk.size=3000000`; set ES `http.max_content_length: 200mb`; verify with `curl -s http://elasticsearch:9200/_cluster/settings | jq '.persistent["http.max_content_length"]'` |
| Downstream ES latency cascading to query latency | All Jaeger UI lookups slow; ES latency visible but not Jaeger-internal | `curl -s http://elasticsearch:9200/_cat/nodes?v&h=name,load_1m,cpu,heap.percent,disk.used_percent` | ES segment merges, GC, or shard imbalance causing high query latency | Rebalance ES shards: `curl -s -X POST http://elasticsearch:9200/_cluster/reroute?retry_failed=true`; force merge old indices: `curl -X POST http://elasticsearch:9200/jaeger-span-$(date -d '2 days ago' +%Y-%m-%d)/_forcemerge?max_num_segments=1` |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on ES-to-collector connection | Collector logs: `x509: certificate has expired`; spans not saving | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'save_latency'`; `openssl s_client -connect elasticsearch:9200 2>&1 | grep 'notAfter'` | Expired TLS certificate on ES endpoint used by Jaeger collector | Rotate ES cert; update Kubernetes secret: `kubectl create secret tls es-tls -n tracing --cert=new.crt --key=new.key --dry-run=client -o yaml | kubectl apply -f -`; restart collector |
| mTLS rotation failure for collector-to-agent gRPC | Agent logs: `transport: authentication handshake failed`; spans not reaching collector | `kubectl logs -n tracing daemonset/jaeger-agent | grep -i "handshake\|tls\|authentication"` | mTLS client cert on agent pod expired or rotated without matching CA update on collector | Sync cert rotation: update both agent and collector TLS secrets simultaneously; use cert-manager with automated rotation and `--collector.grpc.tls.*` flags |
| DNS resolution failure for ES service | Collector logs: `dial tcp: lookup elasticsearch on <dns>: no such host` | `kubectl exec -n tracing deploy/jaeger-collector -- nslookup elasticsearch.elastic.svc.cluster.local` | ES service DNS entry missing (service deleted/renamed during upgrade) | Verify ES service exists: `kubectl get svc -n elastic`; update `--es.server-urls` in collector deployment to match current service FQDN |
| TCP connection exhaustion to ES | Collector starts dropping spans; ES `http.max_keep_alive_requests` reached | `ss -tn | grep :9200 | wc -l` on collector pod via `kubectl exec`; `curl -s http://elasticsearch:9200/_nodes/stats | jq '.nodes[].transport.rx_count'` | Collector opens too many persistent TCP connections; ES connection queue full | Reduce `--es.bulk.workers`; enable HTTP keep-alive pooling; check ES `network.tcp.no_delay=true` |
| Load balancer misconfiguration dropping Thrift UDP | Jaeger agents emit spans but nothing appears in collector `spans_received`; UDP spans silently dropped | `kubectl exec -n tracing daemonset/jaeger-agent -- wget -qO- http://localhost:14271/metrics | grep 'reporter_queue_length'`; compare to collector received count | L4 LB in front of collectors not configured for UDP; or collector Service type changed from ClusterIP | Ensure collector UDP ports (6831, 6832) are NodePort or hostPort on DaemonSet; use gRPC (14250) instead of UDP: `--reporter.grpc.host-port=<collector>:14250` |
| Packet loss between agent DaemonSet and collector | Intermittent span gaps; no errors in logs; Prometheus shows periodic drops | `kubectl exec -n tracing <jaeger-agent-pod> -- wget -qO- http://localhost:14271/metrics | grep 'reporter_spans_failed_total'` | Network policy or CNI misconfiguration causing packet drops on UDP port 6831 | Switch from UDP Thrift to gRPC reporter: set `--reporter.type=grpc --reporter.grpc.host-port=jaeger-collector.tracing:14250`; verify NetworkPolicy allows 14250/TCP |
| MTU mismatch causing fragmented gRPC spans | Large spans (> 1500 bytes) silently dropped; only small spans appear in UI | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'spans_dropped'`; filter by size | Container overlay network MTU (1450) lower than default; gRPC frames fragmented and reassembled incorrectly | Set gRPC `--reporter.grpc.connection-timeout=30s`; verify CNI MTU: `kubectl exec -n tracing <pod> -- ip link show eth0 | grep mtu`; align MTU across all network layers |
| Firewall rule change blocking 14250/TCP | gRPC reporter connections refused after network change; collector receives zero spans | `kubectl exec -n tracing daemonset/jaeger-agent -- wget -qO- http://localhost:14271/metrics | grep 'reporter_connected'` | Firewall or security group rule updated to block gRPC collector port | Restore firewall rule allowing pods to reach collector on 14250/TCP; validate: `kubectl exec -n tracing <agent-pod> -- nc -zv jaeger-collector.tracing 14250` |
| SSL handshake timeout on slow LDAP/OIDC for Jaeger UI | Jaeger UI login hangs; OAuth callback returns 504 | `kubectl logs -n tracing deploy/jaeger-query | grep -i "oauth\|timeout\|tls"` | Keycloak/OIDC provider unreachable or slow; SSL handshake exceeds query default timeout | Check OIDC provider connectivity: `kubectl exec -n tracing deploy/jaeger-query -- curl -v https://<oidc-provider>/auth`; increase `--query.ui-config` timeout; disable OIDC temporarily if provider is down |
| Connection reset from ES after idle timeout | Collector bulk request returns `connection reset by peer` after idle period | `kubectl logs -n tracing -l app.kubernetes.io/component=collector | grep "connection reset\|EOF\|broken pipe"` | ES `http.keep_alive_timeout` (default 75s) lower than collector idle period between flushes | Set `--es.bulk.flush-interval=30s`; or configure ES `http.keep_alive_timeout: 300s`; ensure collector sends keep-alive pings |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| Collector pod OOM kill | Collector pod restarted; spans lost during restart window; `OOMKilled` in pod status | `kubectl describe pod -n tracing -l app.kubernetes.io/component=collector | grep -A5 "OOMKilled\|Last State"` | Increase memory limit: `kubectl set resources deploy/jaeger-collector -n tracing --limits=memory=1Gi`; reduce `--collector.queue-size=5000` | Set `--collector.queue-size` proportional to memory limit; HPA on `jaeger_collector_queue_length` metric |
| ES data partition disk full | ES cluster status RED; new span writes return 429/507; index creation fails | `curl -s http://elasticsearch:9200/_cat/allocation?v&s=disk.percent:desc | head -5` | Delete oldest Jaeger indices: `kubectl create job -n tracing --from=cronjob/jaeger-es-index-cleaner jaeger-clean-now`; expand ES PVC | Set ILM policy for Jaeger indices; alert at 75% disk; configure `jaeger-es-index-cleaner` to run daily |
| ES log partition disk full | ES node logs fill `/var/log`; ES process may crash; Kubernetes disk pressure | `kubectl exec -n elastic <es-pod> -- df -h /var/log` | Rotate/truncate ES logs: `kubectl exec -n elastic <es-pod> -- find /var/log -name "*.log" -mtime +1 -delete`; restart ES pod | Configure ES `log4j2.properties` with rolling file appender: `appender.rolling.policies.size.size=100MB`; ship logs to external aggregator |
| Collector file descriptor exhaustion | Collector logs: `too many open files`; new ES connections refused | `kubectl exec -n tracing deploy/jaeger-collector -- cat /proc/$(pgrep jaeger)/limits | grep 'open files'` | Restart collector pod; pre-restart: `kubectl exec -n tracing deploy/jaeger-collector -- ls /proc/$(pgrep jaeger)/fd | wc -l` | Set `ulimit -n 65536` via pod securityContext; add `spec.containers[].resources` with container-level limit |
| ES inode exhaustion from many small index files | ES cannot create new segment files; write failures; `No space left on device` despite disk space available | `kubectl exec -n elastic <es-pod> -- df -i /usr/share/elasticsearch/data` | Force merge to reduce segment count: `curl -X POST http://elasticsearch:9200/jaeger-span-*/_forcemerge?max_num_segments=1`; delete old indices | Set `index.merge.policy.segments_per_tier=5`; use fewer, larger shards (1 shard for low-volume daily indices) |
| Collector CPU throttle from CFS limits | Collector throughput degraded; `spans_dropped` grows despite queue not full; throttled CPU | `kubectl top pods -n tracing -l app.kubernetes.io/component=collector`; check: `kubectl describe pod -n tracing <collector-pod> | grep cpu` | Raise CPU limit: `kubectl set resources deploy/jaeger-collector -n tracing --limits=cpu=2`; or remove CPU limit if on dedicated nodes | Set CPU requests ≥ 500m; avoid hard CPU limits on latency-sensitive collectors; use Guaranteed QoS class |
| Query pod swap exhaustion | Query pod extremely slow; Java GC thrashing; swap I/O visible on node | `kubectl exec -n tracing deploy/jaeger-query -- cat /proc/meminfo | grep Swap`; node-level: `vmstat 1 5` | Restart query pod; increase memory limit to eliminate swap: `kubectl set resources deploy/jaeger-query -n tracing --limits=memory=2Gi` | Set pod `spec.containers[].resources.requests.memory` equal to limits (Guaranteed class); enable JVM `-Xmx` matching container limit |
| ES kernel PID/thread limit hit | ES node unresponsive; logs: `unable to create native thread`; JVM thread creation fails | `kubectl exec -n elastic <es-pod> -- cat /proc/sys/kernel/pid_max`; `ps -T -p $(pgrep java) | wc -l` | Restart ES pod; increase `kernel.pid_max`: `kubectl debug node/<node> -- chroot /host sysctl -w kernel.pid_max=131072` | Set ES pod `resources.limits` to prevent runaway threads; use `threadPool.search.size` and `threadPool.write.size` to cap ES threads |
| Network socket buffer exhaustion on high-throughput collector | UDP Thrift spans dropped at kernel level; `netstat -su` shows UDP receive errors | `kubectl exec -n tracing daemonset/jaeger-agent -- cat /proc/net/snmp | grep -i 'rcvbufErrors\|sndBufErrors'` | Increase UDP buffer: `sysctl -w net.core.rmem_max=26214400` on collector nodes; switch to gRPC transport | Migrate all SDKs to gRPC or OTLP/gRPC exporter; UDP is unreliable at high volume — phase out Thrift UDP |
| Ephemeral port exhaustion on collector-to-ES connections | Collector logs: `dial tcp: bind: address already in use`; new ES connections fail | `kubectl exec -n tracing deploy/jaeger-collector -- cat /proc/sys/net/ipv4/ip_local_port_range`; `ss -tn | wc -l` | Restart collector; on node: `sysctl -w net.ipv4.ip_local_port_range="1024 65535"`; enable TCP TIME_WAIT reuse: `sysctl -w net.ipv4.tcp_tw_reuse=1` | Use persistent ES bulk connections (keep-alive); reduce connection churn by tuning `--es.bulk.flush-interval` |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Duplicate trace IDs from clock skew causing span merge collision | Two unrelated spans assigned identical traceID; Jaeger UI shows garbled trace with wrong parent-child relationships | `curl -s "http://jaeger-query.tracing:16686/api/traces?service=<svc>&limit=20" | jq '.data[].spans | group_by(.traceID) | map(select(length > 1))'` | Incorrect distributed traces; RCA from trace data unreliable | Fix NTP sync on affected nodes: `chronyc tracking`; enforce W3C TraceContext propagation in all SDKs to use globally unique 128-bit IDs; update instrumentation libraries |
| Partial trace — orphaned spans with no parent | Spans appear in Jaeger with `parentSpanID` referencing non-existent span; broken trace tree | `curl -s "http://jaeger-query.tracing:16686/api/traces/<traceID>" | jq '.data[0].spans[] | select(.references == [])' | wc -l` | Incomplete traces; root cause analysis gaps; SLO measurement errors for distributed latency | Root spans from upstream service not collected (sampling mismatch or service not instrumented); align sampling decisions using head-based sampling with `x-b3-sampled: 1` propagation; ensure all services in call chain are instrumented |
| Cross-service trace context dropped at async boundary | Trace breaks at Kafka/message queue boundary; downstream service starts new trace with no parent | `kubectl logs -n tracing -l app.kubernetes.io/component=collector | grep 'parentSpanID="0000000000000000"' | wc -l` per minute (elevated) | Full distributed traces unavailable across async services; latency attribution impossible | Inject W3C `traceparent` header into Kafka message headers at producer; extract at consumer; use OpenTelemetry Kafka instrumentation libraries |
| Out-of-order span arrival causing incorrect trace duration | Jaeger reports negative child span duration or impossible timeline; spans arrive out of clock order | `curl -s "http://jaeger-query.tracing:16686/api/traces/<traceID>" | jq '[.data[0].spans[] | .startTime] | sort | to_entries | map(.value - .value)'` — check for timestamp inversions | Misleading latency data; false SLO violations or false clears | Enable Jaeger clock skew adjustment: `--query.max-clock-skew-adjustment=500ms`; ensure all pods use same NTP source: `chronyc sources` |
| Saga partial failure: span emitted for failed step but compensating transaction not traced | Saga shows completed steps but missing rollback spans; trace appears successful despite failure | `curl -s "http://jaeger-query.tracing:16686/api/traces?service=<saga-svc>&tags=error%3Dtrue&limit=50" | jq '.data[].spans[] | select(.tags[] | select(.key=="error" and .value==true))'` | Silent saga failures; inconsistent data state not visible in traces; alert gaps | Instrument compensating transactions with spans tagged `saga.step=compensate`; use `span.setStatus(ERROR)` on failed steps; add baggage item `saga.status=compensating` |
| At-least-once span delivery causing duplicate trace entries in ES | Same traceID appears twice in ES with slightly different spans; Jaeger UI shows duplicate trace | `curl -s "http://elasticsearch:9200/jaeger-span-*/_search?q=traceID:<traceID>" | jq '.hits.total.value'` (> expected) | Duplicate traces inflate span count; query results show same trace twice confusing operators | Caused by collector retry on ES bulk failure; enable ES deduplication: set `--es.create-index-templates=true`; use span hash as ES `_id`; upgrade to Jaeger 1.35+ which uses ES document IDs to deduplicate |
| Distributed lock expiry during trace flush under load | Collector batch flush extends beyond lock TTL; concurrent collector tries to flush same batch to ES | `kubectl logs -n tracing -l app.kubernetes.io/component=collector | grep -i "conflict\|409\|version conflict"` | Duplicate or partial span batches in ES; elevated `jaeger_collector_save_latency` | Reduce lock contention by sharding collector per service: use `--collector.tags-as-fields.include=service.name` based routing; or switch to stateless ES writes without distributed locks |
| Compensating trace (rollback) span merged with forward-path trace | Rollback spans share same traceID as original operation; Jaeger timeline becomes unreadable | `curl -s "http://jaeger-query.tracing:16686/api/traces/<traceID>" | jq '.data[0].spans | length'` significantly higher than expected | Trace timeline too complex to read; MTTR increased as SREs struggle to isolate failure path | Tag rollback spans with `rollback=true` and `saga.direction=compensating`; use separate traceID for compensation flow linked via baggage reference |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| High-cardinality service flooding shared ES index | One service emits 100K+ unique operation names; ES mapping explosion on `operationName` field | All other services' queries slow; ES mapping update queue blocks; Jaeger UI search unresponsive | `curl -s 'http://elasticsearch:9200/jaeger-span-*/_mapping' | jq 'to_entries[].value.mappings.properties | keys | length'` — compare across indices | Enable OTel Collector `filter/truncate_operations` processor to limit operation names per service to 1000; set ES `index.mapping.total_fields.limit=2000` |
| Memory pressure from large span batches per service | One service sending 10 MB+ span batches; collector JVM heap consumed; GC frequency increases | Other services' spans dropped when GC pause causes queue overflow | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'collector_queue_length'` | Add per-service max span size in OTel Collector: `processors.transform.metric_statements` to drop spans with tag value size > 10 KB; set `--collector.queue-size=5000` |
| Disk I/O saturation from one service's ES indexing rate | ES data node `iowait > 80%`; one service has 100× the span volume of others | All tenant trace queries slow; Jaeger UI timeouts for all services | `curl -s http://elasticsearch:9200/_nodes/stats/indices | jq '.nodes[] | {name:.name, indexing_rate:.indices.indexing.index_total}'` | Apply per-service sampling via Jaeger remote sampling: set `rateLimit: 100` for high-volume service in `sampling.strategies.json`; per-service rate limiting in OTel Collector |
| Network bandwidth monopoly from span bulk upload | One service's agent bulk-uploads cached spans on reconnect; saturates collector ingress NIC | Spans from other services dropped at network layer; brief total tracing blackout | `kubectl exec -n tracing daemonset/jaeger-agent -- wget -qO- http://localhost:14271/metrics | grep 'reporter_batch_size'` — identify agents with large batches | Add OTel Collector per-service `tail_sampling` with `num_traces=1000` limit; apply Kubernetes NetworkPolicy with egress rate shaping annotations; restart agent: `kubectl rollout restart daemonset/jaeger-agent -n tracing` |
| Connection pool starvation from high-frequency service | One service opens 100 concurrent gRPC streams to collector; ES bulk worker threads all occupied by that service | Other services' spans queue indefinitely; `spans_received_total` drops for other services | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'jaeger_collector_spans_received_total' | grep -v '#'` | Limit per-service gRPC connections via OTel Collector `extensions.ratelimit`; separate high-volume service to dedicated collector instance with its own ES index |
| Quota enforcement gap: no per-service span retention limit | High-volume service's spans fill ES indices; ILM policy deletes all services' old traces equally | Long-tail services lose historical traces while noisy service has redundant recent coverage | `curl -s 'http://elasticsearch:9200/_cat/indices/jaeger-span-*?v&h=index,docs.count,store.size&s=docs.count:desc' | head -10` | Create per-service ES index aliases with separate ILM policies: `PUT jaeger-span-<service>-alias/_ilm/policy`; route high-volume service to short-retention index |
| Cross-tenant data leak risk via shared ES index | Single ES index contains spans from all services; compromised ES client can query any service's traces | Any service with ES read access can enumerate other services' endpoint names, latencies, and payloads | `curl -s 'http://elasticsearch:9200/jaeger-span-*/_search?q=process.serviceName:<other-service>&size=1' | jq '.hits.total.value'` | Implement ES field-level security: separate index per service namespace; or use `document_level_security` with `{"term":{"process.serviceName":"<tenant-service>"}}`; require per-tenant ES credentials |
| Rate limit bypass via multiple agent connections | Service deploys with 10 agent sidecars instead of 1; bypasses per-agent rate limit; saturates collector | Other services' agents queued; collector throughput consumed | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'reporter_connected'` — count connections | Enforce per-namespace agent count via LimitRange/admission webhook; apply OTel Collector per-source-IP rate limiting; enable Jaeger remote sampling with service-level `maxOperationsPerService: 2000` |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Prometheus scrape failure for Jaeger collector metrics | No `jaeger_collector_*` metrics in Grafana; alerting on span drops silent during incidents | Collector pod IP changed after restart; Prometheus ServiceMonitor selector stale or namespace label missing | `kubectl get servicemonitor -n tracing jaeger-collector-metrics -o yaml | grep selector`; `curl -s http://prometheus:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="jaeger-collector") | .health'` | Fix ServiceMonitor: `kubectl patch servicemonitor jaeger-collector-metrics -n tracing --type merge -p '{"spec":{"namespaceSelector":{"matchNames":["tracing"]}}}'`; verify labels match pod: `kubectl get pod -n tracing -l app.kubernetes.io/component=collector` |
| Trace sampling gap silently missing high-latency incidents | SLO dashboard shows no trace data for 5% of requests; alert never fires on missing traces | Head-based sampling at 1% probabilistically skips slow outlier requests; no tail-based sampler | Compare `jaeger_collector_spans_received_total` rate to application request rate in Prometheus: `rate(http_server_requests_total[5m])` vs `rate(jaeger_collector_spans_received_total[5m])` — ratio reveals sampling gap | Switch to tail-based sampling in OTel Collector: configure `tail_sampling` processor with `latency` policy: `threshold_ms: 1000`; or use Jaeger adaptive sampling: `--sampling.strategies-file=/etc/jaeger/adaptive-sampling.json` |
| Log pipeline silent drop: Fluentd dropping Jaeger collector ERROR logs | Jaeger collector errors not appearing in centralized log system; only visible in `kubectl logs` | Fluentd buffer overflow during high-throughput period drops logs without alerting; no backpressure metric exposed | `kubectl exec -n logging <fluentd-pod> -- tail -100 /var/log/fluentd/fluentd.log | grep "buffer full\|drop\|overflow"`; compare `kubectl logs -n tracing deploy/jaeger-collector | grep ERROR | wc -l` vs log aggregator count | Add Fluentd buffer overflow metric to Prometheus: `monitor_agent` plugin; set `overflow_action block` instead of `drop_oldest_chunk`; increase Fluentd buffer: `total_limit_size 2GB` |
| Alert rule misconfiguration: `spans_dropped_total` alert fires on restarts only | Alert fires when pod restarts (counter resets to 0 then jumps) but not on sustained drops | Alert uses `increase()` not `rate()`; counter reset causes false spike; sustained drops with no reset never alert | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'spans_dropped'`; check manually during high load | Fix alert rule: use `rate(jaeger_collector_spans_dropped_total[5m]) > 100` instead of `increase()`; add separate alert for `jaeger_collector_queue_length / jaeger_collector_queue_capacity > 0.9` |
| Cardinality explosion blinding Jaeger dashboards | Grafana Jaeger dashboard shows no data or 500; Prometheus cardinality limit reached | Service using dynamic operation names (e.g., `GET /users/12345` instead of `GET /users/:id`) creates millions of unique metric labels | `curl -s http://prometheus:9090/api/v1/label/__name__/values | jq '.data | length'` (>100K means cardinality explosion); `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'jaeger_collector_spans_received_total' | wc -l` | Configure OTel Collector `spanmetrics` processor with `operation_label_truncation_length: 100`; add `filter/normalize_operations` to replace dynamic path segments with `:param` |
| Missing health endpoint: Jaeger agent DaemonSet not in Kubernetes readiness checks | Pod shows Ready but spans not forwarded; agent unhealthy silently; no health check failure | DaemonSet pod spec has no `readinessProbe` on agent health port 14271 | `kubectl get daemonset jaeger-agent -n tracing -o jsonpath='{.spec.template.spec.containers[0].readinessProbe}'` — empty means no probe | Add readinessProbe: `kubectl patch daemonset jaeger-agent -n tracing --type json -p '[{"op":"add","path":"/spec/template/spec/containers/0/readinessProbe","value":{"httpGet":{"path":"/","port":14271},"initialDelaySeconds":10,"periodSeconds":10}}]'` |
| Instrumentation gap in critical path: async Kafka consumer spans not traced | Distributed trace breaks at Kafka boundary; consumer-side spans have no parent; RCA impossible for async errors | OpenTelemetry Kafka instrumentation not added to consumer service; W3C `traceparent` not extracted from message headers | `curl -s "http://jaeger-query.tracing:16686/api/traces?service=<consumer-svc>&limit=50" | jq '[.data[].spans[] | select(.references == [])] | length'` — orphan root spans indicate missing propagation | Add OTel Kafka consumer instrumentation library to consumer service; extract `traceparent` header: `tracer.extract(Format.TEXT_MAP, new KafkaHeadersExtractAdapter(record.headers()))` |
| Alertmanager outage causing Jaeger span drop alerts silently failing | Span drop rate high for 2 hours but no PagerDuty notification; incident discovered manually | Alertmanager pod OOM-killed; Prometheus continues evaluating rules but cannot route alerts | `kubectl get pods -n monitoring -l app.kubernetes.io/name=alertmanager`; test route: `amtool --alertmanager.url=http://alertmanager:9093 alert add alertname=test` | Restart Alertmanager: `kubectl rollout restart statefulset/alertmanager-main -n monitoring`; add Deadman's snitch (external health check that pages if Alertmanager stops firing): configure `watchdog` alert in Prometheus |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor Jaeger version upgrade breaks ES index template | After upgrade, new spans fail to index; ES returns `illegal_argument_exception: mapper [tag] cannot be changed from type [string] to [keyword]` | `kubectl logs -n tracing -l app.kubernetes.io/component=collector | grep "illegal_argument\|mapper\|mapping"`; `curl -s http://elasticsearch:9200/_cat/templates/jaeger-*?v` | Rollback Jaeger image: `kubectl rollout undo deployment/jaeger-collector -n tracing`; delete conflicting ES template: `curl -X DELETE http://elasticsearch:9200/_index_template/jaeger-span`; let new version recreate | Test ES index template changes in staging with same ES version; run `curl -X PUT http://elasticsearch:9200/_index_template/jaeger-span` dry-run before upgrade |
| ES schema migration partial completion: index alias not updated | After Jaeger upgrade, old alias `jaeger-span-read` still points to old index; new spans in new index invisible in UI | `curl -s 'http://elasticsearch:9200/_cat/aliases/jaeger-*?v'` — verify alias points to new index | Re-point alias: `curl -X POST http://elasticsearch:9200/_aliases -d '{"actions":[{"remove":{"index":"jaeger-span-old-*","alias":"jaeger-span-read"}},{"add":{"index":"jaeger-span-new-*","alias":"jaeger-span-read"}}]}'` | Add alias verification step to upgrade runbook; automate with: `curl -s 'http://elasticsearch:9200/_cat/aliases/jaeger-span-read?v'` post-upgrade health check |
| Rolling upgrade version skew: old collector and new collector simultaneously | Spans written in two different formats; Jaeger UI shows garbled or duplicate traces during upgrade | `kubectl get pods -n tracing -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'` — shows mixed versions | Complete upgrade immediately: `kubectl rollout status deployment/jaeger-collector -n tracing`; if stuck, force rollout: `kubectl rollout restart deployment/jaeger-collector -n tracing` | Use `maxSurge=0, maxUnavailable=1` for Jaeger collector upgrades to prevent mixed-version windows; upgrade during low-traffic period |
| Zero-downtime migration from Badger to ES gone wrong | Badger local traces not migrated; historical traces missing after switch; only post-migration traces visible | `kubectl exec -n tracing deploy/jaeger-query -- wget -qO- http://localhost:16687/metrics | grep 'storage_type'`; `curl -s "http://jaeger-query.tracing:16686/api/traces?service=<svc>&start=<pre-migration>&end=<pre-migration>"` — empty results | Revert to Badger storage: update Jaeger deployment env `SPAN_STORAGE_TYPE=badger`; roll back: `kubectl rollout undo deployment/jaeger-query -n tracing` | Run Jaeger `jaeger-migrate` tool before cutover: `docker run jaegertracing/jaeger-migrate --badger.directory-value=/data --es.server-urls=http://es:9200`; verify counts match before switching production |
| Config format change: `--sampling.strategies-file` JSON schema incompatible with new version | Jaeger collector starts but sampling config silently ignored; all services fall back to default rate | `kubectl logs -n tracing deploy/jaeger-collector | grep -i "sampling\|strategies\|error"` — look for parse errors; `curl -s http://jaeger-query.tracing:16686/api/sampling?service=<svc>` — check actual strategy returned | Roll back collector to previous version: `kubectl rollout undo deployment/jaeger-collector -n tracing`; or update sampling config JSON to new schema format per upgrade changelog | Validate sampling config against new version schema before upgrade: test with `docker run --rm jaegertracing/jaeger-collector:<new> --sampling.strategies-file=/tmp/strategies.json` |
| Data format incompatibility: Protobuf span format not readable by old query | After collector upgraded to gRPC/Protobuf output, old query pod cannot deserialize spans; UI shows 0 results | `kubectl get pods -n tracing -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'` — query on old version while collector on new | Upgrade query to match collector version: `kubectl set image deployment/jaeger-query -n tracing jaeger-query=jaegertracing/jaeger-query:<new-version>`; or roll back collector | Always upgrade collector and query to same version in same deployment; use Helm chart to pin all component versions together: `helm upgrade jaeger jaegertracing/jaeger --set tag=<version>` |
| Feature flag rollout: adaptive sampling causing regression | Enabling adaptive sampling causes collector CPU to spike 10×; sampling rates become erratic; drops increase | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'sampler'`; CPU: `kubectl top pod -n tracing -l app.kubernetes.io/component=collector` | Disable adaptive sampling: `kubectl set env deployment/jaeger-collector -n tracing SAMPLING_CONFIG_TYPE=file`; revert to static strategies file: update ConfigMap | Enable adaptive sampling in staging first; monitor `jaeger_sampler_*` metrics for 24h; set `--sampling.initial-sampling-probability=0.001` for initial conservative rollout |
| Dependency version conflict: Jaeger operator upgrade incompatible with existing CRD version | `Jaeger` CR fails to reconcile after operator upgrade; collector not updated; stale version running | `kubectl describe jaeger jaeger-prod -n tracing | grep "Events\|Status"`; `kubectl logs -n tracing -l name=jaeger-operator | grep -i "error\|version\|CRD"` | Roll back operator: `helm rollback jaeger-operator -n tracing`; restore previous CRD: `kubectl apply -f jaeger-operator-<previous-version>/crds/` | Run `kubectl convert -f jaeger.yaml --output-version jaegertracing.io/v1` to validate CRD compatibility before operator upgrade; check operator upgrade guide for CRD migration steps |

## Kernel/OS & Host-Level Failure Patterns
**Minimum cross-cutting cases to evaluate here:** OOM killer false kill, inode exhaustion, CPU steal, NTP skew affecting locks, leases, or coordination, file descriptor exhaustion, and TCP conntrack table saturation.

| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|------------------|--------------|-------------|----------------------|
| OOM killer terminates jaeger-collector process | `kubectl describe pod -n tracing -l app.kubernetes.io/component=collector | grep -A5 "OOMKilled\|Reason"` | Collector in-memory span queue (`--collector.queue-size`) too large for container memory limit; ES bulk buffer accumulation | Span loss during restart window; gaps in distributed traces; all tracing dark until pod recovers | `kubectl set resources deploy/jaeger-collector -n tracing --limits=memory=2Gi`; reduce queue: `kubectl set env deploy/jaeger-collector -n tracing COLLECTOR_QUEUE_SIZE=2000`; add `--collector.num-workers=50` to reduce per-worker heap |
| Inode exhaustion on ES data node hosting Jaeger indices | `kubectl exec -n elastic <es-pod> -- df -i /usr/share/elasticsearch/data` shows 100% inode usage | Jaeger creates one ES index per day per index prefix; many small Lucene segment files exhaust inodes without filling disk bytes | ES cannot create new segment files; Jaeger span writes fail with `IOException: No space left on device`; new indices uncreateable | Force merge to reduce segments: `curl -X POST 'http://elasticsearch:9200/jaeger-span-*/_forcemerge?max_num_segments=1'`; delete oldest indices: `kubectl create job -n tracing --from=cronjob/jaeger-es-index-cleaner clean-now`; increase inode count requires filesystem recreation — migrate to larger volume |
| CPU steal spike degrading Jaeger collector throughput | `kubectl exec -n tracing deploy/jaeger-collector -- top -bn1 | grep "Cpu\|st"` ; node-level: `kubectl debug node/<node> -it --image=busybox -- chroot /host top -bn1 | grep st` | Noisy neighbor VMs on shared hypervisor; cloud provider CPU credit exhaustion (T-series burstable instances) | Collector throughput drops; span processing latency rises; queue fills and spans are dropped | Move collector pods to dedicated nodes: `kubectl label node <node> jaeger-role=collector`; add nodeSelector to deployment; switch to non-burstable instance type (C/M series); verify: `kubectl top pod -n tracing -l app.kubernetes.io/component=collector` |
| NTP clock skew causing span timestamp inversion | `kubectl exec -n tracing deploy/jaeger-collector -- chronyc tracking | grep "System time"` ; cross-node: `kubectl debug node/<node> -it --image=busybox -- chroot /host chronyc sources -v` | NTP daemon not running or network-blocked; different nodes have diverged clocks | Jaeger UI shows child spans starting before parent spans; trace timelines unreadable; `--query.max-clock-skew-adjustment` threshold exceeded silently | Fix NTP: `kubectl debug node/<node> -- chroot /host systemctl restart chronyd`; enable Jaeger clock skew compensation: `kubectl set env deploy/jaeger-query -n tracing JAEGER_MAX_CLOCK_SKEW_ADJUSTMENT=1s`; validate: `curl -s 'http://jaeger-query.tracing:16686/api/traces/<traceID>' | jq '[.data[0].spans[].startTime] | sort'` |
| File descriptor exhaustion on jaeger-collector pod | `kubectl exec -n tracing deploy/jaeger-collector -- cat /proc/$(pgrep jaeger-collector)/limits | grep "open files"` ; current usage: `ls /proc/$(pgrep jaeger-collector)/fd | wc -l` | Each gRPC client connection and ES bulk HTTP connection consumes an FD; default ulimit 1024 too low at high connection count | New ES connections refused with `too many open files`; gRPC reporters from agents cannot connect; span ingestion stops | Increase FD limit via pod securityContext: `kubectl patch deploy/jaeger-collector -n tracing --type json -p '[{"op":"add","path":"/spec/template/spec/containers/0/resources/limits/ephemeral-storage","value":"1Gi"}]'`; set `ulimit -n 65536` in container startup; verify with `kubectl exec -- ulimit -n` |
| TCP conntrack table full blocking collector-to-ES connections | `kubectl debug node/<node> -- chroot /host sysctl net.netfilter.nf_conntrack_count`; `kubectl debug node/<node> -- chroot /host cat /proc/net/nf_conntrack | wc -l` | High connection churn from Jaeger collector creating new HTTP connections per ES bulk flush instead of reusing keep-alive | New TCP connections to ES fail silently; bulk writes return connection refused; span loss at ES write layer | Increase conntrack table: `kubectl debug node/<node> -- chroot /host sysctl -w net.netfilter.nf_conntrack_max=524288`; enable TCP keep-alive on ES client: `kubectl set env deploy/jaeger-collector -n tracing ES_TLS_SKIP_HOST_VERIFY=false`; configure `--es.bulk.flush-interval=10s` to reduce connection churn |
| Kernel panic / node crash hosting jaeger DaemonSet agent | `kubectl get events -n tracing --field-selector reason=NodeNotReady | head -20`; `kubectl get node <node> -o jsonpath='{.status.conditions[?(@.type=="Ready")].message}'` | Memory pressure triggering kernel OOM; hardware fault; kubelet crash; kernel bug triggered by eBPF tracing tools alongside Jaeger | Jaeger DaemonSet agent on affected node goes dark; all pods on node lose span forwarding; no spans from those pods visible in Jaeger | Cordon and drain node: `kubectl cordon <node> && kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`; DaemonSet automatically reschedules on healthy nodes; check `/var/log/kern.log` via node debug: `kubectl debug node/<node> -- chroot /host dmesg | tail -50` |
| NUMA memory imbalance degrading ES JVM GC on Jaeger storage node | `kubectl exec -n elastic <es-pod> -- numastat -p java | grep -E "Node|Huge"` ; node-level: `kubectl debug node/<node> -- chroot /host numactl --hardware` | JVM heap allocated across multiple NUMA nodes; cross-NUMA memory access increases GC pause times; ES bulk indexing of Jaeger spans triggers frequent GC | ES indexing throughput halved; Jaeger span write latency increases 3–5×; `jaeger_collector_save_latency` p99 spikes | Pin ES JVM to single NUMA node: add `numactl --cpunodebind=0 --membind=0` to ES startup script; set JVM flag `-XX:+UseNUMA`; configure ES node with `node.attr.rack: numa0`; verify heap locality: `kubectl exec -n elastic <es-pod> -- jcmd $(pgrep java) VM.native_memory` |

## Deployment Pipeline & GitOps Failure Patterns
**Minimum cross-cutting cases to evaluate here:** image pull failure (rate limit or auth), Helm drift, ArgoCD sync stuck, PodDisruptionBudget-blocked rollout, blue-green cutover failure, and ConfigMap or Secret drift.

| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Image pull rate limit blocking new Jaeger collector pod | `kubectl describe pod -n tracing -l app.kubernetes.io/component=collector | grep "toomanyrequests\|rate limit\|ImagePullBackOff"` | Docker Hub anonymous pull limit (100/6h per IP) exhausted on shared egress NAT; all cluster nodes share one IP | New Jaeger pods stuck in `ImagePullBackOff`; rollouts stall; scaling events fail; incident response delayed | `kubectl rollout undo deployment/jaeger-collector -n tracing`; configure image pull secret for authenticated Docker Hub: `kubectl create secret docker-registry dockerhub-creds -n tracing --docker-username=<user> --docker-password=<token>`; migrate to ECR/GCR mirror |
| Image pull auth failure after registry credential rotation | `kubectl describe pod -n tracing <pod> | grep "unauthorized\|401\|imagePullSecret"` | Registry credentials in `imagePullSecret` expired or rotated without updating Kubernetes Secret | All Jaeger component pods fail to pull updated images; deployments frozen; no new rollouts possible | Recreate pull secret: `kubectl delete secret jaeger-registry-creds -n tracing && kubectl create secret docker-registry jaeger-registry-creds -n tracing --docker-server=<registry> --docker-username=<user> --docker-password=<newtoken>`; patch deployment to reference secret |
| Helm chart drift: manual kubectl edits overwritten by Helm release | `helm diff upgrade jaeger jaegertracing/jaeger -n tracing -f values.yaml` shows unexpected changes | SRE applied hotfix directly via `kubectl set env`; Helm release reconciles and reverts change | Jaeger configuration reverts silently; hotfix lost; incident recurrence | Check drift before apply: `helm diff upgrade jaeger jaegertracing/jaeger -n tracing -f values.yaml`; roll back Helm: `helm rollback jaeger -n tracing`; encode all config changes in `values.yaml` and `helm upgrade` |
| ArgoCD sync stuck due to Jaeger CRD validation error | `kubectl get application jaeger -n argocd -o jsonpath='{.status.sync.status}'` shows `OutOfSync`; `kubectl describe application jaeger -n argocd | grep "SyncError\|ComparisonError"` | New Jaeger CRD version in GitOps repo incompatible with existing cluster CRD schema; ArgoCD cannot apply diff | Jaeger operator not updated; existing running version persists but diverges from Git source of truth | Force ArgoCD sync with replace: `argocd app sync jaeger --force --replace`; or manually apply CRD: `kubectl apply -f jaeger-operator-crds.yaml --server-side`; rollback Git commit in jaeger manifests repo |
| PodDisruptionBudget blocking Jaeger collector rolling update | `kubectl rollout status deployment/jaeger-collector -n tracing` hangs; `kubectl describe pdb jaeger-collector -n tracing | grep "Disruptions Allowed: 0"` | PDB requires minimum 2 available replicas; only 2 replicas total; rolling update leaves 1 available which violates PDB | Rolling update stalls indefinitely; old Jaeger version keeps running; new config never applied | Temporarily patch PDB: `kubectl patch pdb jaeger-collector -n tracing --type json -p '[{"op":"replace","path":"/spec/minAvailable","value":1}]'`; complete rollout; restore PDB; or scale up first: `kubectl scale deploy/jaeger-collector -n tracing --replicas=3` |
| Blue-green traffic switch failure: old Jaeger query still receiving traffic | `kubectl get svc jaeger-query -n tracing -o jsonpath='{.spec.selector}'` points to old deployment label | Service selector not updated after blue-green switch; new Jaeger query running but traffic still routed to old version | Users hitting stale Jaeger UI; traces written to new ES schema not queryable via old query format | Patch service selector: `kubectl patch svc jaeger-query -n tracing --type json -p '[{"op":"replace","path":"/spec/selector/version","value":"green"}]'`; verify: `kubectl get endpoints jaeger-query -n tracing`; rollback by switching selector back to `blue` |
| ConfigMap/Secret drift: sampling strategies file not reloaded | `kubectl get configmap jaeger-sampling-config -n tracing -o yaml | grep "last-applied"` shows stale timestamp; but collector still using old strategy | Jaeger collector caches sampling strategies in memory; ConfigMap update not watched; pod not restarted after ConfigMap change | Wrong sampling rates applied; SLO gaps or over-sampling without anyone noticing; change appears deployed but is not active | Force reload: `kubectl rollout restart deployment/jaeger-collector -n tracing`; verify new strategy active: `curl -s http://jaeger-query.tracing:16686/api/sampling?service=<svc>`; use Reloader operator to auto-restart on ConfigMap change |
| Feature flag stuck: adaptive sampling enabled in GitOps but not taking effect | `kubectl exec -n tracing deploy/jaeger-collector -- wget -qO- http://localhost:14269/metrics | grep 'jaeger_sampler_type'` shows `probabilistic` not `adaptive` | Environment variable override or CLI flag in deployment spec taking precedence over ConfigMap value; flag evaluation order issue | Intended sampling strategy not applied; cost overruns or trace gaps depending on which direction the mismatch goes | Audit all env vars: `kubectl set env deploy/jaeger-collector -n tracing --list | grep -i sampl`; remove conflicting CLI flags from deployment args; sync GitOps source: `argocd app sync jaeger --prune` |

## Service Mesh & API Gateway Edge Cases
**Minimum cross-cutting cases to evaluate here:** circuit breaker false positives, rate limiting on legitimate traffic, stale service discovery endpoints, mTLS rotation interruption, retry storm amplification, gRPC keepalive or max-message failures, and trace context loss.

| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive isolating Jaeger collector | Istio circuit breaker opens on collector; agent pods receive `503 Connection refused`; `jaeger_reporter_spans_submitted_total` drops to 0 | Transient ES bulk timeout causes collector to return slow 5xx; Envoy outlier detection ejects collector upstream prematurely | All spans from mesh-enrolled services stop flowing; tracing dark during the open circuit window | Check Istio outlier detection: `kubectl exec -n tracing deploy/jaeger-collector -c istio-proxy -- pilot-agent request GET clusters | grep jaeger`; tune: `kubectl edit destinationrule jaeger-collector -n tracing` — increase `consecutiveErrors: 10` and `interval: 60s`; restart Envoy: `kubectl rollout restart deploy/jaeger-collector -n tracing` |
| Rate limit hitting legitimate Jaeger agent traffic | `kubectl exec -n tracing daemonset/jaeger-agent -- wget -qO- http://localhost:14271/metrics | grep 'reporter_batch_size'`; Envoy access log shows 429 responses on port 14250 | Istio/Envoy rate limit policy set too low for peak span volume; all agents share same route-level limit | Spans dropped at mesh layer; `jaeger_agent_reporter_spans_failures_total` increases; legitimate high-traffic services lose traces | Check rate limit: `kubectl get envoyfilter -n tracing -o yaml | grep ratelimit`; increase limit or exempt tracing namespace: `kubectl annotate namespace tracing ratelimit.istio.io/exclude=true`; apply per-source rate limit keyed by pod IP instead of global |
| Stale service discovery endpoints: Jaeger collector removed from mesh registry | `istioctl proxy-config endpoints <agent-pod>.tracing | grep jaeger-collector`; Envoy EDS shows 0 endpoints for collector service | Collector pod IP changed after restart; xDS cache not yet updated; agent Envoy still routing to old IP | Spans sent to dead endpoint; TCP connect fails; agent queues fill; spans dropped after queue timeout | Force xDS resync: `istioctl proxy-config endpoints -n tracing <agent-pod> --cluster outbound|14250||jaeger-collector.tracing.svc.cluster.local`; restart pilot: `kubectl rollout restart deploy/istiod -n istio-system`; verify: `kubectl get endpoints jaeger-collector -n tracing` |
| mTLS rotation breaking Jaeger agent-to-collector gRPC connections | `kubectl exec -n tracing daemonset/jaeger-agent -c istio-proxy -- openssl s_client -connect jaeger-collector.tracing:14250 -cert /etc/certs/cert-chain.pem` returns handshake error | Istio cert rotation overlaps with long-lived gRPC connections; cert renewed but old connection held by agent not renegotiated | gRPC streams return `transport: authentication handshake failed`; spans queue in agent; OOM risk on agent pod | Restart agent DaemonSet to force new TLS handshake: `kubectl rollout restart daemonset/jaeger-agent -n tracing`; verify cert validity: `istioctl proxy-config secret <agent-pod>.tracing`; increase cert rotation grace period in Istio: `kubectl edit meshconfig -n istio-system` |
| Retry storm: Envoy retrying failed ES bulk requests and amplifying ES load | `kubectl logs -n tracing -l app.kubernetes.io/component=collector -c istio-proxy | grep '"response_code":"503"' | wc -l` — count per minute | Envoy retryPolicy configured for 5xx; ES under disk/memory pressure returns 503; Envoy retries 3× per request; 15× effective load on ES | ES overwhelmed by retry amplification; cascading failure worsens ES health; all Jaeger writes fail | Disable retries on collector-to-ES VirtualService: `kubectl edit virtualservice jaeger-collector-es -n tracing` — set `retries: attempts: 0`; add circuit breaker in DestinationRule; rate-limit retries: `retries.retryOn: "gateway-error"` not `5xx` |
| gRPC keepalive / max message size failure between agent and collector | `kubectl logs -n tracing daemonset/jaeger-agent | grep "RESOURCE_EXHAUSTED\|max frame size\|grpc: received message larger than max"` | Large batch of spans with many tags exceeds gRPC default max message size (4 MB); or keepalive pings disabled — idle gRPC stream closed by load balancer after timeout | Agent spans rejected with `RESOURCE_EXHAUSTED`; no retry in Jaeger agent by default; spans silently lost | Increase max message size: `kubectl set env daemonset/jaeger-agent -n tracing REPORTER_GRPC_MAX_MESSAGE_SIZE=67108864`; enable keepalive: `--reporter.grpc.connection-timeout=25s`; configure LB idle timeout > 30s; verify: `kubectl logs -n tracing daemonset/jaeger-agent | grep "grpc"` |
| Trace context propagation gap at Istio ingress gateway | Spans from external requests have no `x-b3-traceid`/`traceparent` header; Jaeger shows disconnected traces for external traffic | Istio ingress gateway not configured to generate and forward trace headers; or `sampling: 0` in gateway Envoy tracing config | External user requests untraceable end-to-end; RCA for customer-impacting latency impossible; only internal service-to-service traces visible | Enable tracing at gateway: `kubectl edit configmap istio -n istio-system` — set `tracing.sampling: 100`; configure Jaeger endpoint: `tracing.zipkin.address: jaeger-collector.tracing:9411`; verify headers: `kubectl exec -n istio-system deploy/istio-ingressgateway -- curl -v http://backend/ 2>&1 | grep -i trace` |
| Load balancer health check misconfiguration marking healthy Jaeger query as unhealthy | AWS ALB or GCP LB shows Jaeger query targets as `unhealthy`; 503 returned to Grafana/users; `kubectl get pods -n tracing -l app.kubernetes.io/component=query` shows `Running` | LB health check targeting wrong port (e.g., HTTP/16687 admin vs HTTP/16686 UI); or health check path `/` returns 404 instead of 200 on query | Jaeger UI intermittently returns 503 from LB even when pods healthy; SRE cannot investigate incidents | Check LB health check config in cloud console; patch Ingress annotation: `kubectl annotate ingress jaeger-query -n tracing nginx.ingress.kubernetes.io/healthcheck-path=/`; verify directly: `kubectl exec -n tracing deploy/jaeger-query -- wget -qO- http://localhost:16686/` returns 200; use port 16686 not 16687 |
