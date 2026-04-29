---
name: questdb-agent
description: >
  QuestDB specialist agent. Handles high-performance time series ingestion,
  SQL queries, WAL management, partitioning, and columnar storage operations.
model: haiku
color: "#D14671"
skills:
  - questdb/questdb
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-questdb-agent
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

You are the QuestDB Agent — the high-performance time series expert. When any
alert involves QuestDB instances (ingestion, WAL, queries, disk, memory), you
are dispatched.

# Activation Triggers

- Alert tags contain `questdb`, `qdb`, `ilp`
- WAL backlog or ingestion stall alerts
- Disk usage alerts
- Query latency alerts
- Instance health check failures

## Prometheus Metrics Reference

QuestDB exposes Prometheus metrics at `http://<questdb-host>:9003/metrics`. QuestDB 7.x+ exposes these natively; earlier versions require enabling via config (`metrics.enabled=true`).

| Metric | Description | Warning Threshold | Critical Threshold |
|--------|-------------|-------------------|--------------------|
| `questdb_committed_rows_total` | Total rows committed to storage (rate = rows/sec) | rate drop > 50% | rate = 0 for > 1m |
| `questdb_commit_latency_seconds` | Commit latency histogram | p99 > 1s | p99 > 5s |
| `questdb_wal_apply_row_count` | Rows applied per WAL apply iteration | — | — |
| `questdb_memory_tag_bytes{tag="rss"}` | JVM/native process RSS memory | > 75% of RAM | > 90% of RAM |
| `questdb_memory_tag_bytes{tag="java_heap"}` | JVM heap used | > 70% of `-Xmx` | > 90% of `-Xmx` |
| `questdb_open_files` | Open file descriptor count | > 80% of `ulimit -n` | > 95% of `ulimit -n` |
| `questdb_gc_major_count_total` | JVM major GC event count (rate indicates memory pressure) | rate > 1/min | rate > 5/min |
| `questdb_gc_major_pause_seconds_total` | Cumulative major GC pause time | — | p99 > 1s per GC |
| `process_cpu_seconds_total` | CPU time consumed by QuestDB process | > 80% of CPU quota | > 95% of CPU quota |
| `questdb_http_requests_total` | HTTP query requests by status | error rate > 1% | error rate > 5% |
| `questdb_http_request_latency_seconds` | HTTP query latency histogram | p99 > 5s | p99 > 30s |

### Key Diagnostic SQL Functions (via HTTP REST or psql port 8812)

| SQL | Purpose |
|-----|---------|
| `SELECT * FROM wal_tables()` | WAL sequencer and transaction lag per table |
| `SELECT * FROM all_tables()` | All tables with write lock status |
| `SELECT * FROM table_partitions('<table>')` | Partition list with disk sizes |
| `SELECT * FROM reader_pool()` | Active query readers (long-running = leak) |
| `SELECT * FROM writer_pool()` | Active writers (locked = ingestion stall) |
| `SHOW SERVER ERRORS` | Recent server-side errors |
| `EXPLAIN SELECT ...` | Query execution plan (checks for full-scan) |
| `ALTER TABLE ... DETACH PARTITION LIST '<month>'` | Detach old partition to free space |
| `ALTER TABLE ... DROP PARTITION LIST '<month>'` | Drop detached partition permanently |

## PromQL Alert Expressions

```yaml
# CRITICAL — Committed rows rate dropped to zero (ingestion stalled)
- alert: QuestDBIngestionStalled
  expr: rate(questdb_committed_rows_total[2m]) == 0
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "QuestDB ingestion stalled on {{ $labels.instance }}"
    description: "No rows committed in 2 minutes. Check ILP port 9009, WAL apply lag, and JVM health."

# WARNING — Commit latency high
- alert: QuestDBCommitLatencyHigh
  expr: histogram_quantile(0.99, rate(questdb_commit_latency_seconds_bucket[5m])) > 1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "QuestDB p99 commit latency > 1s on {{ $labels.instance }}"

# CRITICAL — Commit latency very high (severe I/O pressure or lock contention)
- alert: QuestDBCommitLatencyCritical
  expr: histogram_quantile(0.99, rate(questdb_commit_latency_seconds_bucket[5m])) > 5
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "QuestDB p99 commit latency > 5s on {{ $labels.instance }}"

# CRITICAL — RSS memory critically high
- alert: QuestDBMemoryCritical
  expr: questdb_memory_tag_bytes{tag="rss"} / node_memory_MemTotal_bytes > 0.90
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "QuestDB RSS memory > 90% of system RAM on {{ $labels.instance }}"
    description: "OOM killer risk. Reduce o3.max.uncommitted.rows or add memory."

# WARNING — Heap usage high
- alert: QuestDBHeapHigh
  expr: questdb_memory_tag_bytes{tag="java_heap"} / questdb_memory_tag_bytes{tag="java_heap_limit"} > 0.80
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "QuestDB Java heap > 80% on {{ $labels.instance }}"

# WARNING — Major GC rate elevated (memory pressure indicator)
- alert: QuestDBMajorGCFrequent
  expr: rate(questdb_gc_major_count_total[5m]) > 0.1
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "QuestDB frequent major GC on {{ $labels.instance }}"
    description: "{{ $value | humanize }} major GCs/sec. Consider increasing heap or reducing memory pressure."

# CRITICAL — Open file descriptors near limit
- alert: QuestDBOpenFilesHigh
  expr: questdb_open_files / process_open_fds_limit > 0.85
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "QuestDB open file descriptors > 85% of limit on {{ $labels.instance }}"
    description: "Increase ulimit -n or drop/detach old partitions."

# WARNING — HTTP query error rate elevated
- alert: QuestDBHTTPErrorRate
  expr: >
    rate(questdb_http_requests_total{status!~"2.."}[5m])
    / rate(questdb_http_requests_total[5m]) > 0.05
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "QuestDB HTTP error rate > 5% on {{ $labels.instance }}"

# WARNING — HTTP query latency high
- alert: QuestDBQueryLatencyHigh
  expr: histogram_quantile(0.99, rate(questdb_http_request_latency_seconds_bucket[5m])) > 5
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "QuestDB HTTP query p99 > 5s on {{ $labels.instance }}"
```

### Cluster Visibility

```bash
# QuestDB health check endpoint
curl -sf http://<questdb-host>:9003/ && echo "QuestDB OK" || echo "QuestDB UNREACHABLE"

# All Prometheus metrics
curl -s http://<questdb-host>:9003/metrics

# Committed rows rate (should be non-zero if ingesting)
curl -s http://<questdb-host>:9003/metrics | grep questdb_committed_rows_total | grep -v '#'

# Commit latency histogram
curl -s http://<questdb-host>:9003/metrics | grep questdb_commit_latency_seconds | grep -v '#'

# Memory usage (RSS and heap)
curl -s http://<questdb-host>:9003/metrics | grep questdb_memory_tag_bytes | grep -v '#'

# GC activity
curl -s http://<questdb-host>:9003/metrics | grep -E 'questdb_gc_(major|minor)' | grep -v '#'

# Open file descriptors
curl -s http://<questdb-host>:9003/metrics | grep questdb_open_files | grep -v '#'

# Active WAL transactions and apply lag
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20table_name%2C%20wal_sequencer%2C%20min_txn%2C%20max_txn%2C%20ready_txn%20FROM%20wal_tables()" | python3 -m json.tool

# Table list and partition info
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20TABLES" | python3 -m json.tool
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20*%20FROM%20table_partitions('<table>')" | python3 -m json.tool

# Disk usage
df -h /var/lib/questdb/

# JVM process memory
ps aux | grep java | grep questdb | awk '{print $4, "% mem", $6/1024, "MB RSS"}'

# Web UI key pages
# QuestDB Console: http://<questdb-host>:9000/
# Metrics:         http://<questdb-host>:9003/metrics
# WAL tables:      Run: SELECT * FROM wal_tables() in console
```

### Global Diagnosis Protocol

**Step 1: Infrastructure health**
```bash
# HTTP API reachable
curl -sf "http://<questdb-host>:9000/exec?query=SELECT%201" | python3 -c "import sys,json; print('DB OK:', json.load(sys.stdin)['dataset'])"

# ILP/TCP port open (line protocol ingestion)
nc -zv <questdb-host> 9009 && echo "ILP port open"

# PostgreSQL wire protocol port
pg_isready -h <questdb-host> -p 8812 -U admin

# JVM process alive
pgrep -a java | grep questdb

# Committed rows rate non-zero?
curl -s http://<questdb-host>:9003/metrics | grep questdb_committed_rows_total | grep -v '#'
```

**Step 2: Job/workload health**
```bash
# WAL apply status for all tables (pending = max_txn - ready_txn)
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20table_name%2C%20(max_txn%20-%20ready_txn)%20AS%20pending_txns%20FROM%20wal_tables()%20ORDER%20BY%20pending_txns%20DESC" | python3 -m json.tool

# Writer pool (any locked writers = potential stall)
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20*%20FROM%20writer_pool()" | python3 -m json.tool

# Active query readers (long-running = resource leak)
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20*%20FROM%20reader_pool()" | python3 -m json.tool
```

**Step 3: Resource utilization**
```bash
# Memory (RSS and heap from Prometheus metrics)
curl -s http://<questdb-host>:9003/metrics | grep questdb_memory_tag_bytes | grep -v '#'

# GC pressure
curl -s http://<questdb-host>:9003/metrics | grep questdb_gc_major | grep -v '#'

# Open file descriptors
curl -s http://<questdb-host>:9003/metrics | grep questdb_open_files | grep -v '#'
lsof -p $(pgrep -f questdb) 2>/dev/null | wc -l

# Disk usage for data directory
du -sh /var/lib/questdb/db/*/ 2>/dev/null | sort -rh | head -10
```

**Step 4: Data pipeline health**
```bash
# WAL backlog growing?
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20table_name%2C%20max_txn-ready_txn%20lag%20FROM%20wal_tables()%20ORDER%20BY%20lag%20DESC" | python3 -m json.tool

# Server errors
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20SERVER%20ERRORS" | python3 -m json.tool
```

**Severity:**
- CRITICAL: QuestDB process down, `questdb_committed_rows_total` rate = 0, disk > 95%, WAL apply completely stalled (pending txns growing, ready_txn static), RSS > 90% RAM
- WARNING: WAL pending txns > 1000 and growing, heap > 80% of `-Xmx`, query p99 > 5s, major GC > 1/min
- OK: health endpoint responding, WAL caught up, disk < 70%, `questdb_committed_rows_total` rate healthy

### Focused Diagnostics

## Scenario 1: WAL Backlog / Ingestion Stall

**Trigger:** `questdb_committed_rows_total` rate = 0; WAL `pending_txns` growing; ILP clients reporting connection resets or timeouts.

## Scenario 2: Query Latency Spike

**Trigger:** `questdb_http_request_latency_seconds` p99 > 5s; application timeouts; UI unresponsive.

## Scenario 3: Disk Full / Partition Lifecycle

**Trigger:** Disk > 90%; `questdb_open_files` near limit; ingestion failing with I/O errors.

## Scenario 4: Out-of-Order / Late Data Causing OOM

**Trigger:** RSS memory growing with spikes during ingestion; major GC frequent; `questdb_memory_tag_bytes{tag="rss"}` > 75% RAM; OOM kills.

## Scenario 5: Out-of-Order Data Causing Partition Rewrite Storm

**Symptoms:** `questdb_memory_tag_bytes{tag="rss"}` spikes during ingestion bursts; major GC frequency elevated (`questdb_gc_major_count_total` rate > 1/min); disk write IOPS spikes with multiple partition rewrites visible; ingestion throughput (`questdb_committed_rows_total` rate) drops during rewrite activity.

**Root Cause Decision Tree:**
- Is the client sending data with timestamps that are significantly older than the current time?
  - Yes → Out-of-order data is triggering O3 (out-of-order) partition rewrites
    - Is the lag between client timestamps and wall clock > `cairo.o3.max.lag`?
      - Yes → QuestDB treats data as out-of-order and must merge it into existing partitions
    - Is `cairo.o3.max.uncommitted.rows` set very high?
      - Yes → Large in-memory sort buffer is consuming excess RAM before flush
    - Are many different tables receiving out-of-order data simultaneously?
      - Yes → Concurrent rewrite storms amplifying I/O and memory pressure
  - No → Timestamps are in order; OOM is from another cause (high cardinality, heap settings)

**Diagnosis:**
```bash
# Current RSS and heap memory
curl -s http://<questdb-host>:9003/metrics | grep questdb_memory_tag_bytes | grep -v '#'

# Major GC rate (elevated = memory pressure from O3 buffer)
curl -s http://<questdb-host>:9003/metrics | grep questdb_gc_major_count_total | grep -v '#'

# O3 configuration
grep -E "(o3|out.of.order|uncommitted|lag)" /var/lib/questdb/conf/server.conf

# Check timestamp range in incoming data vs current time
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20max(ts)%2C%20min(ts)%2C%20now()%2C%20now()-max(ts)%20AS%20data_lag%20FROM%20<table>" | python3 -m json.tool

# Disk write activity
iostat -x 1 5 | grep -E "sda|nvme"

# O3 specific metrics
curl -s http://<questdb-host>:9003/metrics | grep -E 'o3|out_of_order' | grep -v '#'
```

**Thresholds:**
- Warning: `data_lag` (now - max(ts)) > 60s with active writes
- Critical: RSS > 75% of RAM, major GC > 1/min, `questdb_committed_rows_total` rate dropping > 50%

## Scenario 6: Column Type Mismatch Causing Silent Write Rejection

**Symptoms:** ILP ingestion appears successful (no TCP errors, no `questdb_committed_rows_total` drop) but rows are missing from the table; `SHOW SERVER ERRORS` shows type mismatch errors; no Prometheus metric for rejected rows by default; data silently dropped.

**Root Cause Decision Tree:**
- Are rows missing from a table despite ILP clients reporting no write errors?
  - Yes → Silent rejection at the schema level
    - Is the ILP client sending a value as a float for a column that was created as an integer (or vice versa)?
      - Yes → QuestDB's ILP schema-on-write rejects rows where the column type conflicts with the established schema
    - Was the table's schema created manually (CREATE TABLE) with strict types and the ILP client is sending incompatible types?
      - Yes → Pre-existing schema constraint violation; fix the client data types
    - Is the client sending a tag as a field or a field as a tag?
      - Yes → Tags and fields are mapped to indexed `SYMBOL` vs `VARCHAR`/numeric columns; swapping them causes type conflict

**Diagnosis:**
```bash
# Check server errors for type mismatch
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20SERVER%20ERRORS" | python3 -m json.tool

# Inspect the table schema
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20COLUMNS%20FROM%20<table>" | python3 -m json.tool

# Count rows in recent time window to spot missing data
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20count()%2C%20max(ts)%2C%20min(ts)%20FROM%20<table>%20WHERE%20ts%20%3E%20now()-5m" | python3 -m json.tool

# Confirm committed rows metric is incrementing (writes reaching QuestDB)
watch -n5 'curl -s http://<questdb-host>:9003/metrics | grep questdb_committed_rows_total | grep -v "#"'

# Check ILP server log for rejection messages
journalctl -u questdb --since "30 min ago" | grep -iE "type|mismatch|reject|column"
```

**Thresholds:**
- Critical: Any confirmed silent row rejection (data loss); especially critical when rows disappear without error responses to the client

## Scenario 7: Memory Map File Limit Exhaustion

**Symptoms:** QuestDB fails to open new partitions with errors like `Cannot open file` or `mmap failed`; `questdb_open_files` approaches `ulimit -n`; ingestion stops for affected tables; JVM logs show `java.io.IOException: Too many open files`.

**Root Cause Decision Tree:**
- Is `questdb_open_files` approaching `process_open_fds_limit`?
  - Yes → File descriptor exhaustion
    - Is the number of partitions (and thus memory-mapped files) growing without bound?
      - Yes → Missing or insufficient retention policy; old partitions accumulate
    - Is `vm.max_map_count` set to the system default (65530)?
      - Yes → Default is too low for QuestDB with many partitions and large columnar files; each column per partition uses a memory map
    - Is QuestDB running many tables with fine-grained partition intervals (hourly vs daily)?
      - Yes → Too many partitions multiplied by column count = excessive mmap count
  - No → File descriptor is not the issue; look at actual error messages

**Diagnosis:**
```bash
# Current open file count vs limit
curl -s http://<questdb-host>:9003/metrics | grep questdb_open_files | grep -v '#'
cat /proc/$(pgrep -f questdb)/limits | grep "open files"

# System mmap count vs vm.max_map_count
cat /proc/$(pgrep -f questdb)/maps | wc -l
cat /proc/sys/vm/max_map_count

# Number of partitions across all tables
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20table_name%2C%20count()%20AS%20partition_count%20FROM%20(SELECT%20table_name%20FROM%20all_tables()%20WHERE%20partitionBy%20!%3D%20'NONE')%20GROUP%20BY%20table_name%20ORDER%20BY%20partition_count%20DESC" | python3 -m json.tool

# Total columns per table (more columns = more file handles per partition)
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20COLUMNS%20FROM%20<large_table>" | python3 -m json.tool | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d['dataset']),'columns')"

# QuestDB error log for mmap failures
journalctl -u questdb --since "1 hour ago" | grep -iE "mmap|open file|too many|Cannot open"
```

**Thresholds:**
- Warning: `questdb_open_files` > 80% of `ulimit -n`
- Critical: `questdb_open_files` > 95% of `ulimit -n` or `vm.max_map_count` nearly exhausted

## Scenario 8: Table Reader/Writer Contention Causing Timeout

**Symptoms:** Queries timing out with `query timeout` errors; `questdb_http_request_latency_seconds` p99 spikes; `writer_pool()` shows tables with `locked=true` for extended periods; ILP writes stall while a query is running against the same table.

**Root Cause Decision Tree:**
- Does `writer_pool()` show a table locked for > 30 seconds?
  - Yes → A write operation or schema change is holding the writer lock
    - Is a large data migration or `INSERT INTO ... SELECT` running?
      - Yes → Long-running write transaction blocking reads; it will release on commit
    - Is a `DROP PARTITION` or `DETACH PARTITION` in progress?
      - Yes → These acquire exclusive locks; complete the operation or kill it
  - Does `reader_pool()` show many long-running readers on the same table?
    - Yes → Readers are blocking writers (QuestDB uses reader-writer coordination)
    - Is a query scanning the entire table without a time filter?
      - Yes → Full table scan holding reader reference across all partitions

**Diagnosis:**
```bash
# Check for locked writers
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20*%20FROM%20writer_pool()" | python3 -m json.tool

# Check for long-running readers
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20*%20FROM%20reader_pool()" | python3 -m json.tool

# Query latency histogram
curl -s http://<questdb-host>:9003/metrics | grep questdb_http_request_latency_seconds_bucket | tail -10

# Server errors for timeout messages
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20SERVER%20ERRORS" | python3 -m json.tool

# Disk I/O (heavy writes can cause I/O contention that extends lock duration)
iostat -x 1 5
```

**Thresholds:**
- Warning: Writer locked for > 10s; reader pool showing queries > 60s
- Critical: `questdb_http_request_latency_seconds` p99 > 30s; ILP ingestion stalled

## Scenario 9: WAL Segment Stuck Causing Replication Lag

**Symptoms:** WAL apply lag growing (`max_txn - ready_txn` in `wal_tables()` increasing); specific table's `ready_txn` not advancing despite new data arriving; no disk full condition; QuestDB process is alive and accepting ILP connections.

**Root Cause Decision Tree:**
- Is `max_txn - ready_txn` growing for a specific table only?
  - Yes → WAL apply is stalled for that table, not a global issue
    - Is there a corrupt WAL segment preventing the apply worker from advancing?
      - Check QuestDB logs for WAL-related errors on that table
    - Is a schema change in flight (e.g., a new column being added) blocking WAL replay?
      - Yes → Schema changes acquire exclusive locks during WAL apply; check writer_pool()
    - Is the WAL apply worker count set to 1 and overwhelmed by a single large transaction?
      - Yes → Increase `cairo.wal.apply.worker.count`
  - Is `max_txn - ready_txn` growing for ALL tables?
    - Yes → Global WAL apply slowdown: disk I/O, O3 rewrite storm, or JVM GC pause

**Diagnosis:**
```bash
# WAL apply status per table
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20table_name%2C%20max_txn%2C%20ready_txn%2C%20(max_txn-ready_txn)%20AS%20pending_txns%20FROM%20wal_tables()%20ORDER%20BY%20pending_txns%20DESC" | python3 -m json.tool

# Is it growing over time? (two readings 30s apart)
# First reading:
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20table_name%2C%20(max_txn-ready_txn)%20lag%20FROM%20wal_tables()" | python3 -m json.tool

# WAL directory for the stuck table (look for anomalous segment files)
ls -lh /var/lib/questdb/db/<table>/wal/ 2>/dev/null | tail -20

# QuestDB logs for WAL errors
journalctl -u questdb --since "30 min ago" | grep -iE "wal|apply|segment|replay|stuck"

# Writer pool for the table
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20*%20FROM%20writer_pool()%20WHERE%20name%3D'<table>'" | python3 -m json.tool

# GC pauses that could delay WAL apply
curl -s http://<questdb-host>:9003/metrics | grep questdb_gc_major_pause | grep -v '#'
```

**Thresholds:**
- Warning: `pending_txns` > 100 and growing for a specific table
- Critical: `pending_txns` growing for > 5 minutes without relief; ingestion queue backing up

## Scenario 10: SQL Query Plan Choosing Wrong Index

**Symptoms:** A query with a `WHERE` clause on an indexed column is slow; `EXPLAIN` shows a full table scan (`Df`) instead of an index scan (`Ix`); `questdb_http_request_latency_seconds` p99 high for that specific query pattern; the same query on a smaller time range is fast but on a longer range is slow.

**Root Cause Decision Tree:**
- Does `EXPLAIN` show a full scan (`Df`) on a column with an index?
  - Yes → QuestDB is not using the available index
    - Is the column a `SYMBOL` type (only SYMBOL columns support indexed scans in QuestDB)?
      - No → Only SYMBOL columns can be indexed; VARCHAR, DOUBLE, LONG cannot use index scans
    - Is the indexed SYMBOL column inside a function call in the WHERE clause?
      - Yes → Function wrapping prevents index usage; rewrite to direct equality check
    - Is the query using `LIKE '%value%'` (contains) rather than `= 'value'` (equality)?
      - Yes → Contains patterns on SYMBOL do not use the index; use equality or `IN`
    - Is the cardinality of the SYMBOL column very high?
      - Yes → High-cardinality SYMBOL columns have less effective index selectivity

**Diagnosis:**
```bash
# Run EXPLAIN on the slow query
curl -s "http://<questdb-host>:9000/exec?query=EXPLAIN%20SELECT%20count()%20FROM%20trades%20WHERE%20symbol%20%3D%20'AAPL'%20AND%20ts%20IN%20'2024-01'" | python3 -m json.tool
# Look for: "Df" = full scan, "Ix" = index scan, "DeferredSingleSymbolFilterDataFrame" = index used

# Check if the column is SYMBOL type and indexed
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20COLUMNS%20FROM%20trades" | python3 -m json.tool
# Look for "indexed": true in the column definition

# Check SYMBOL cardinality
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20count_distinct(symbol)%20FROM%20trades" | python3 -m json.tool

# Compare query latency with and without index
# With index (should be fast):
curl -sw "\nTime: %{time_total}s\n" "http://<questdb-host>:9000/exec?query=SELECT%20count()%20FROM%20trades%20WHERE%20symbol%3D'AAPL'%20AND%20ts%20IN%20'2024-01'"
# Without time filter (should be slow):
curl -sw "\nTime: %{time_total}s\n" "http://<questdb-host>:9000/exec?query=SELECT%20count()%20FROM%20trades%20WHERE%20symbol%3D'AAPL'"
```

**Thresholds:**
- Warning: Query latency p99 > 5s for a point-lookup on an indexed SYMBOL column
- Critical: Full table scan on a table > 100M rows without time-range filter

## Scenario 11: Disk Full Mid-Write Corrupting WAL

**Symptoms:** QuestDB process crashes or becomes unresponsive when disk fills to 100%; after restart, some tables fail to open with `WAL error` or `cannot replay WAL` messages; `questdb_committed_rows_total` rate = 0 after restart; specific tables show errors for any query against them.

**Root Cause Decision Tree:**
- Did the disk reach 100% during an active write operation?
  - Yes → WAL segment written partially; replay will fail to find complete transaction record
    - Is only one or a few tables affected (those being written to at the time of disk full)?
      - Yes → Partial WAL write isolated to those tables; other tables should recover normally
    - Does the error mention a specific WAL segment number?
      - Yes → Identify the corrupt segment file and remove it to allow recovery
  - Did the disk fill gradually and QuestDB logged warnings before crashing?
    - Yes → Check QuestDB logs for `No space left on device` warnings prior to the crash

**Diagnosis:**
```bash
# Disk usage
df -h /var/lib/questdb/

# Tables that fail to open
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20TABLES" | python3 -m json.tool

# WAL directory for affected table
ls -lh /var/lib/questdb/db/<table>/wal/

# QuestDB startup log for WAL replay errors
journalctl -u questdb -b | grep -iE "wal|replay|corrupt|error|segment"

# Check server errors after restart
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20SERVER%20ERRORS" | python3 -m json.tool

# dmesg for OOM or I/O errors at time of disk full
dmesg | grep -iE "ext4|I/O error|no space|killed" | tail -20
```

**Thresholds:**
- Critical: Any disk-full event on the QuestDB data partition; WAL corruption renders affected tables read-only or inaccessible

## Scenario 12: Network Disconnection Causing Partial Transaction Commit

**Symptoms:** ILP clients report TCP connection resets or timeouts during write operations; duplicate data appearing in tables (client retried a partially committed batch); `questdb_committed_rows_total` shows partial increments; data integrity checks show row counts inconsistent with expected values.

**Root Cause Decision Tree:**
- Is the client seeing TCP connection resets or timeouts mid-write?
  - Yes → Network disconnection during an ILP write
    - Did the client retry the same batch after the disconnection?
      - Yes → If QuestDB committed part of the batch before the disconnect, the retry creates duplicates
    - Is QuestDB acknowledging writes only after full commit (it does by default for ILP)?
      - Yes → If the ACK was not received by the client, the client cannot know if data was committed
    - Is the disconnect happening consistently at the same batch size?
      - Yes → TCP timeout (SO_TIMEOUT) is too short for large batches; increase client timeout

**Diagnosis:**
```bash
# Check for duplicate rows (compare count to expected)
curl -s "http://<questdb-host>:9000/exec?query=SELECT%20count()%2C%20count_distinct(ts)%20FROM%20<table>%20WHERE%20ts%20%3E%20now()-5m" | python3 -m json.tool
# If count() significantly > count_distinct(ts), duplicates may exist

# ILP connection metrics
curl -s http://<questdb-host>:9003/metrics | grep -E 'questdb_(connections|bytes_received)' | grep -v '#'

# Network stats on ILP port
ss -tp | grep ':9009' | head -20

# QuestDB error log for connection issues
journalctl -u questdb --since "1 hour ago" | grep -iE "connection|disconnect|reset|timeout|ilp"

# Server errors
curl -s "http://<questdb-host>:9000/exec?query=SHOW%20SERVER%20ERRORS" | python3 -m json.tool
```

**Thresholds:**
- Warning: ILP connection reset rate > 0.1/min
- Critical: Confirmed duplicate data or data gap in a table caused by partial commit/retry

## Scenario 13: TLS/mTLS and NetworkPolicy Blocking ILP Ingestion in Production

**Symptoms:** ILP (InfluxDB Line Protocol) writes succeed from staging but fail silently or with `connection refused` / `SSL handshake failed` in production; QuestDB write throughput drops to zero while HTTP REST queries still work; Prometheus metrics show `questdb_line_tcp_connections_current` at 0 despite active producers.

**Root Cause Decision Tree:**
- Production QuestDB has `line.tcp.tls.enabled=true` in `server.conf` but the client is writing to port 9009 with a plain TCP connection, which the server immediately closes after TLS negotiation fails
- A Kubernetes NetworkPolicy in production restricts ingress to the QuestDB namespace on port 9009 (ILP TCP) to only labeled namespaces/pods — staging has no NetworkPolicy
- The production TLS certificate presented by QuestDB uses an internal CA not trusted by the producer client's JVM/Python truststore
- `line.tcp.auth.db.path` is set in production (token-based auth required) but producers are not sending the authentication challenge/response handshake

**Diagnosis:**
```bash
# 1. Check server.conf for TLS and auth configuration
kubectl exec -n <questdb-ns> deploy/questdb -- \
  grep -E "tls|auth|line.tcp" /var/lib/questdb/conf/server.conf

# 2. Test plain TCP vs TLS connection to ILP port
kubectl run nettest -n <questdb-ns> --image=busybox --rm -it -- \
  sh -c "echo 'test,tag=a val=1i' | nc <questdb-svc> 9009; echo exit:$?"

# 3. Test TLS handshake
kubectl run tlstest -n <questdb-ns> --image=alpine/curl --rm -it -- \
  openssl s_client -connect <questdb-svc>:9009 -CAfile /tmp/ca.crt 2>&1 | grep -E "Verify|error|CONNECTED"

# 4. Check NetworkPolicy allowing ILP port 9009 ingress
kubectl get networkpolicy -n <questdb-ns> -o yaml | grep -B5 -A10 "9009"

# 5. Verify ILP connection metrics
curl -s "http://<questdb-host>:9000/metrics" | grep -E "line_tcp_connections|line_tcp_auth"

# 6. Check QuestDB server log for TLS/auth rejection
kubectl logs -n <questdb-ns> deploy/questdb --tail=100 | \
  grep -iE "tls|ssl|auth|reject|handshake|connection.*refused"

# 7. If auth is required — check if auth DB file is mounted and readable
kubectl exec -n <questdb-ns> deploy/questdb -- \
  ls -la /var/lib/questdb/conf/auth.txt 2>/dev/null && cat /var/lib/questdb/conf/auth.txt | head -3
```

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `could not open read-write [file=xxx.d]: No space left on device` | Disk full on QuestDB data directory | `df -h <questdb_data_dir>` |
| `table busy [table=xxx]` | Concurrent DDL or import operation holding a write lock | Wait and retry; check for stuck imports via QuestDB web console |
| `query timeout [xxx ms]` | Long-running query exceeded server-side timeout | Add an index on the filtered column or optimize the WHERE clause |
| `too many open files` | File descriptor exhaustion in QuestDB process | `ulimit -n` and increase via `fs.file-max` or systemd `LimitNOFILE` |
| `column does not exist [name=xxx]` | Column not present in the target table | `SELECT column_name FROM information_schema.columns WHERE table_name='xxx'` |
| `out of memory [limit=xxx, used=xxx]` | QuestDB JVM heap exhausted | Increase `-Xmx` in `conf/jvm.options` |
| `Connection refused: xxx:9000` | QuestDB not running or listening on wrong port | `curl http://questdb:9000/imp` |
| `ERROR: bind: address already in use :9009` | InfluxDB line protocol port 9009 already taken by another process | Change `line.tcp.port` in `conf/server.conf` |
| `invalid table name [table=xxx]` | Table name contains illegal characters or is reserved | Rename the table following QuestDB identifier rules |
| `timestamp out of order` | Row being inserted has a timestamp earlier than the table's last row | Ensure data is pre-sorted by timestamp before ingestion |

# Capabilities

1. **Ingestion** — ILP tuning, out-of-order handling, commit batching
2. **WAL management** — Backlog monitoring, apply optimization
3. **Partitioning** — Partition strategy, detach/drop, lifecycle
4. **Query optimization** — JIT compilation, SQL tuning, EXPLAIN analysis
5. **Storage** — Disk management, memory-mapped file tuning

# Critical Metrics to Check First

1. `questdb_committed_rows_total` rate — 0 = ingestion stopped
2. `questdb_commit_latency_seconds` p99 — high = I/O pressure or lock contention
3. WAL pending txns (`max_txn - ready_txn`) — growing = WAL apply stalled
4. `questdb_memory_tag_bytes{tag="rss"}` — near RAM limit = OOM risk
5. `questdb_gc_major_count_total` rate — > 1/min = memory pressure
6. `questdb_open_files` — near `ulimit -n` = partition count too high
7. Disk usage on `/var/lib/questdb/` — > 90% = critical

# Output

Standard diagnosis/mitigation format. Always include: table info, WAL status,
partition stats, and recommended SQL/config commands.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| ILP ingestion stopped; TCP connections accepted but rows not committed | Disk full on WAL volume; WAL cannot flush, blocking all new writes | `df -h /var/lib/questdb` and `du -sh /var/lib/questdb/db/.wal*` |
| Query latency spike for a specific table | Out-of-order ILP writes triggering expensive re-sort during WAL apply; sender clock skewed | `SELECT * FROM telemetry_wal_stats WHERE table_name='<table>';` and check sender `date` vs QuestDB host |
| REST API `/exec` returning 500 on joins | Memory-mapped file limit hit after many partitions attached; `ulimit -n` exhausted | `lsof -p $(pgrep -f questdb) | wc -l` and compare to `ulimit -n` |
| ILP rows silently dropped; no TCP errors on sender | QuestDB WAL apply lag > max uncommitted rows limit; broker-side back-pressure dropping new ILP frames | `SELECT * FROM wal_tables();` check `sequencerTxn` vs `writerTxn` gap |
| Scheduled SQL jobs not executing | JVM thread pool exhausted by long-running analytical queries; scheduler threads starved | `curl -s 'http://localhost:9000/exec?query=select+*+from+query_activity()'` and check blocked threads |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N partitions detached after a manual maintenance task; queries silently exclude it | `SELECT count() FROM table` returns fewer rows than expected; no error thrown | Time-range queries covering the detached partition return incomplete aggregates | `SELECT * FROM table_partitions('<table_name>');` look for `detached = true` rows |
| 1 ILP writer thread stuck on a table lock; other tables ingesting normally | `questdb_committed_rows_total` rate drops for one table tag; overall system rate looks healthy | Data gap only in the affected table; alerting on aggregate ingestion rate misses it | `curl -s 'http://localhost:9000/exec?query=select+*+from+writer_locks()'` |
| 1 of multiple QuestDB instances in read-replica setup has stale replication | Replica lag metric diverges; direct queries to that replica return old data | Clients load-balanced across replicas see non-deterministic results | `curl -s 'http://replica-1:9000/exec?query=select+max(timestamp)+from+<table>'` and compare to primary |
| 1 column index corrupted after abrupt shutdown; table still queryable | Queries using `WHERE indexed_col = X` are slow or return wrong counts; full scans work correctly | Index-assisted queries silently fall back to full scan; latency SLO breached for those queries | `curl -s 'http://localhost:9000/exec?query=REINDEX+TABLE+<table>+COLUMN+<col>'` (dry-run first with EXPLAIN) |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| WAL apply lag (seconds) | > 5 | > 60 | `curl -s 'http://localhost:9000/metrics' \| grep questdb_wal_lag_seconds` |
| ILP ingestion rate (rows/sec) | > 500,000 | > 1,000,000 (capacity ceiling) | `curl -s 'http://localhost:9000/metrics' \| grep questdb_committed_rows_total` |
| Query execution latency p99 (ms) | > 500 | > 5,000 | `curl -s 'http://localhost:9000/metrics' \| grep 'questdb_query_execution_time_ms{quantile="0.99"}'` |
| Open file descriptors (% of ulimit) | > 70 | > 90 | `curl -s 'http://localhost:9000/metrics' \| grep questdb_open_files` and compare to `ulimit -n` |
| WAL segment count (unapplied) | > 100 | > 1,000 | `curl -s 'http://localhost:9000/exec?query=wal_tables()' \| jq '.dataset[] \| select(.[3] > 100)'` |
| JVM GC pause duration p99 (ms) | > 200 | > 1,000 | `curl -s 'http://localhost:9000/metrics' \| grep 'jvm_gc_pause_seconds{quantile="0.99"}'` |
| Disk write latency p99 (ms) | > 10 | > 50 | `iostat -x 1 3 \| awk '/sda|nvme/{print $10}'` on the QuestDB data volume |
| HTTP connection pool utilization (%) | > 80 | > 95 | `curl -s 'http://localhost:9000/metrics' \| grep questdb_http_connections_active` vs `questdb_http_connections_max` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Disk usage on data volume | >70% and growing >5%/week | Partition by shorter intervals (e.g., DAY instead of MONTH); add `DROP PARTITION` automation for old data; expand volume | 2–3 weeks |
| WAL segment count (`questdb_wal_lag_seconds` or WAL dir size) | WAL lag > 60 s sustained or WAL directory >10 GB | Tune `cairo.wal.apply.worker.count` to increase WAL apply throughput; check for slow `ALTER TABLE` blocking WAL apply | Days |
| JVM heap usage (`jvm_memory_used_bytes` / `jvm_memory_max_bytes`) | >80% heap fill between GC cycles | Increase `-Xmx` in `jvm.options`; tune GC: add `-XX:+UseG1GC -XX:MaxGCPauseMillis=200`; offload query concurrency | 1 week |
| ILP TCP connection count | Number of active ILP connections approaching `line.tcp.connection.pool.capacity` (default 10) | Increase `line.tcp.connection.pool.capacity` in `server.conf`; scale ingestion writers; consider batching at producer | 1 week |
| `questdb_committed_rows_total` growth rate | Row count growing at >100 M/day per table | Evaluate columnar compression settings; add partitioning; pre-plan volume expansion | 3 weeks |
| Page cache pressure (`node_memory_Cached_bytes` low on QuestDB host) | OS page cache < 20% of RAM; frequent disk reads on hot partitions | Increase host RAM; reduce partition count in memory via `cairo.sql.max.mmap.pages`; move hot partitions to faster storage | 1–2 weeks |
| Query thread pool saturation (`questdb_queries_active`) | Active query count sustained at thread pool limit | Increase `shared.worker.count` in `server.conf`; add read replicas; kill long-running queries | 1 week |
| Partition count per table | >500 partitions per table | Switch to coarser partition interval; add automated `DROP PARTITION WHERE timestamp < dateadd('y', -1, now())` job | 2 weeks |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check QuestDB health endpoint and version
curl -s http://localhost:9000/status && curl -s 'http://localhost:9000/exec?query=SELECT+build()' | jq '.dataset[0][0]'

# List all tables with row counts and partition info
curl -s 'http://localhost:9000/exec?query=SELECT+name,+partitionBy,+maxUncommittedRows+FROM+tables()+ORDER+BY+name' | jq '.dataset'

# Show active queries and their execution time
curl -s 'http://localhost:9000/exec?query=SELECT+*+FROM+query_activity()' | jq '.dataset'

# Check partition sizes for a time-series table
curl -s 'http://localhost:9000/exec?query=SELECT+partition,+rows,+diskSize+FROM+table_partitions(%27your_table%27)+ORDER+BY+partition+DESC+LIMIT+10' | jq '.dataset'

# Measure ILP ingest throughput (rows committed per second)
curl -s http://localhost:9000/metrics | grep -E 'questdb_committed_rows_total|questdb_row_count'

# Identify tables with highest write-ahead log (WAL) backlog
curl -s 'http://localhost:9000/exec?query=SELECT+tableName,+seqTxn,+dirtyTxn+FROM+wal_tables()' | jq '.dataset'

# Check JVM heap and GC metrics
curl -s http://localhost:9000/metrics | grep -E 'jvm_memory_used_bytes|jvm_gc_collection_seconds'

# Find the slowest queries over the past hour via logs
grep -E 'execute|slow' /var/log/questdb/questdb.log | grep "$(date +%Y-%m-%dT%H)" | sort -t'=' -k2 -rn | head -10

# Check ILP active connections and ingestion errors
curl -s http://localhost:9000/metrics | grep -E 'questdb_ilp|line_tcp'

# Verify disk usage for the data directory
du -sh /var/lib/questdb/db/* 2>/dev/null | sort -rh | head -10
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Query Success Rate | 99.9% | `1 - (rate(questdb_queries_error_total[5m]) / rate(questdb_queries_total[5m]))` | 43.8 min | >14.4x |
| ILP Ingest Success Rate | 99.5% | `1 - (rate(questdb_ilp_messages_malformed_total[5m]) / rate(questdb_ilp_messages_received_total[5m]))` | 3.6 hr | >7.2x |
| Query Latency p99 ≤ 1 s | 99% | `histogram_quantile(0.99, rate(questdb_query_duration_seconds_bucket[5m])) < 1.0` | 7.3 hr | >2.4x |
| HTTP API Availability | 99.95% | `avg(up{job="questdb"})` and `rate(questdb_queries_error_total{type="http_5xx"}[5m]) < 0.0005` | 21.9 min | >28.8x |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Data directory path | `grep cairo.root /etc/questdb/server.conf` | Points to a persistent, dedicated volume (not the OS root filesystem) |
| JVM heap size | `grep -E 'Xms|Xmx' /etc/questdb/jvm.options` | Xmx set to ≤ 80% of available RAM; Xms == Xmx to avoid resizing pauses |
| ILP bind address | `grep line.tcp.net.bind.to /etc/questdb/server.conf` | Restricted to internal interface, not `0.0.0.0:9009` on internet-facing hosts |
| HTTP authentication | `grep http.security.enabled /etc/questdb/server.conf` | `true` in production to protect REST and web console |
| WAL enabled per table | `curl -s 'http://localhost:9000/exec?query=SELECT+tableName,+walEnabled+FROM+tables()' \| jq '.dataset[] \| select(.[1]==false)'` | High-throughput tables should have WAL enabled for out-of-order handling |
| Commit lag | `grep -E 'cairo.max.uncommitted.rows|writer.data.append.page.size' /etc/questdb/server.conf` | `cairo.max.uncommitted.rows` set (e.g., 1000) to bound in-memory uncommitted data |
| Replication / backup schedule | `grep backup.root /etc/questdb/server.conf` | Backup root configured; verify snapshots exist in last 24 h |
| Max open files | `ulimit -n` (as questdb user) | ≥ 65536; low limits cause "too many open files" under high partition counts |
| Postgres wire protocol bind address | `grep pg.net.bind.to /etc/questdb/server.conf` | Bound to internal address only if PG wire clients are internal |
| Telemetry disabled | `grep telemetry.enabled /etc/questdb/server.conf` | `false` in air-gapped or compliance-sensitive environments |

---

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `could not open read-write [fd=... errno=28, No space left on device]` | FATAL | Data directory disk full | Free disk space immediately; add volume; verify `cairo.root` path |
| `could not complete transaction. out of memory [table=...]` | ERROR | JVM heap exhausted during write transaction | Increase `-Xmx`; reduce `cairo.max.uncommitted.rows`; check for memory leak |
| `partition missing or corrupt [...] ignoring` | ERROR | Table partition file deleted or corrupt on disk | Restore partition from backup; run `REPAIR TABLE` equivalent; check WAL log |
| `line protocol error ... invalid field format` | WARN | Malformed ILP row from sender | Inspect client sending to port 9009; validate ILP payload format |
| `timestamp out of order` | WARN | ILP record timestamp earlier than last committed row (non-WAL table) | Enable WAL on table; or fix timestamp ordering in sender |
| `[-1] socket error ... ECONNRESET` | WARN | Client closed connection during ILP ingestion | Check client retry logic; normal for ephemeral senders |
| `table writer is busy [table=...]` | WARN | Concurrent write attempt while table writer locked | Serialise writes; use WAL-enabled table for concurrent ingestion |
| `Query timeout. ... was running for ... ms` | WARN | SQL query exceeded `query.timeout.default` | Add index or partition filter to query; increase timeout for specific workloads |
| `wal segment apply error [table=...]` | ERROR | WAL segment cannot be applied; data in staging area | Check disk I/O errors; restart QuestDB to retry WAL replay |
| `replication lag ... seconds behind primary` | WARN | Replica falling behind primary; writes piling up | Check network bandwidth to replica; scale replica resources |
| `cannot allocate ... bytes for column [table=...]` | ERROR | Column memory map cannot be extended | Check available RAM and mapped memory; increase OS `vm.max_map_count` |
| `failed to purge detached partitions [table=...]` | WARN | Old detached partitions not cleaned up; disk growing | Manually drop detached partitions via SQL; check `cairo.detach.partition.suffix` |

---

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| HTTP 400 `Bad Request` on REST/exec | Malformed SQL or ILP payload | Query/insert rejected | Validate SQL syntax; use `questdb-rs` or official client libs |
| HTTP 500 `Internal Server Error` on `/exec` | Server-side exception during query execution | Query fails; partial data may be returned | Check server logs for Java stack trace; retry with simplified query |
| `errno=28 ENOSPC` | No space left on device during write | All writes to affected table fail | Free disk space; add storage; QuestDB resumes writes on next transaction |
| `TableWriterBusy` | Table writer locked by another transaction | Concurrent write rejected | Retry write; use WAL-enabled tables which allow multiple writers |
| `OutOfMemory (Java heap space)` | JVM heap exhausted | Server OOM; process may crash | Increase `-Xmx`; reduce `cairo.max.uncommitted.rows`; check for large query result sets |
| `CairoException: timestamp is out of order` | Non-WAL table receives out-of-order timestamp | Row dropped | Enable WAL on table; reorder ingestion; use `DEDUP` if supported |
| `WAL_REPLAY_ERROR` | WAL segment apply failed on startup or runtime | Table in inconsistent state until fixed | Restart QuestDB (retries WAL replay); if persistent, restore table from backup |
| `errno=12 ENOMEM` on mmap | OS virtual address space exhausted for memory-mapped columns | Column becomes inaccessible; table partially readable | Increase `vm.max_map_count`; reduce number of open partitions; add RAM |
| `net bind error: address already in use` | Port 9000/9009/8812 already bound by another process | QuestDB fails to start | Identify conflicting process; stop it; adjust QuestDB port config |
| `partition detached [table=...]` | Partition moved to `.detached` directory | Data in that partition not queryable | `ATTACH PARTITION` SQL to re-attach; or drop if data not needed |
| `replication: primary not reachable` | Replica lost connection to primary | Replica serving stale data | Check network to primary; verify primary is healthy; reconnect replica |
| `query cache miss: plan not found` | Query cache evicted plan; re-planning required | Slightly higher query latency on cold queries | Tune `cairo.sql.query.cache.size`; increase JVM heap to retain cache |

---

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Disk Full Write Halt | ILP ingest rate drops to 0; REST insert errors spike | `errno=28 No space left on device`; `could not open read-write` | DiskUsageHigh | Data volume exhausted by table partitions or snapshots | Drop old tables/partitions; extend volume; add tiered storage |
| JVM Heap OOM | JVM memory at `-Xmx`; GC pause time high; server unresponsive | `out of memory [table=...]`; `Java heap space` in stack trace | JVMHeapHigh | Large query result materialised in heap; too many uncommitted rows | Increase `-Xmx`; reduce `cairo.max.uncommitted.rows`; add `LIMIT` to queries |
| Out-of-Order Ingestion Drop | ILP row count below expected; gaps in time-series data | `timestamp is out of order` repeatedly | DataGapAlert | Non-WAL table receiving late-arriving events from sender | Migrate table to WAL; fix sender clock or reorder buffer |
| WAL Replay Loop | QuestDB start time abnormally long; tables inaccessible | `wal segment apply error [table=...]` | ServiceStartTimeout | Corrupt or unapplicable WAL segment blocking replay | Identify failing segment from logs; back up and remove it; restart |
| mmap Exhaustion | Queries on wide tables failing; column inaccessible errors | `cannot allocate ... bytes for column`; `errno=12 ENOMEM` | ColumnAccessError | `vm.max_map_count` too low; too many open partitions | `sysctl -w vm.max_map_count=1048576`; reduce active partitions; add RAM |
| Table Writer Contention | High latency on ILP ingestion; `TableWriterBusy` errors | `table writer is busy [table=...]` | IngestionLatencyHigh | Multiple writers to non-WAL table; legacy single-writer model | Enable WAL on hot tables; serialise writers; migrate to WAL-enabled schema |
| Query Timeout Storm | Web console queries hanging; REST `/exec` HTTP 504s | `Query timeout ... was running for ... ms` | QueryTimeoutHigh | Full table scans without partition filter; missing indexes | Add `WHERE timestamp BETWEEN ...` partition filter; create symbol index on filter columns |
| Replication Lag | Replica row count lagging primary; `SELECT max(ts)` differs | `replication lag ... seconds behind primary` | ReplicationLagHigh | Network bandwidth saturation or replica under-resourced | Increase replica resources; check WAN bandwidth; reduce ingest rate temporarily |
| Corrupt Partition | Queries on specific time range returning errors; partial results | `partition missing or corrupt ... ignoring` | DataIntegrityAlert | Disk hardware error or incomplete write during crash | Restore partition from backup; re-ingest missing data from source if available |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| ILP TCP connection refused / timeout | Telegraf, Vector, custom ILP client | QuestDB ILP listener not bound; process crashed | `ss -tulpn | grep 9009`; `curl http://localhost:9000/health` | Restart QuestDB; verify `line.tcp.enabled=true` in server.conf |
| `table writer is busy` HTTP 400 from REST `/exec` | JDBC, REST HTTP client | Concurrent writers on non-WAL table; lock contention | `questdb_table_writer_wait_seconds` metric rising | Enable WAL on hot tables; serialize writes; migrate to WAL-enabled schema |
| JDBC `Connection reset by peer` | QuestDB JDBC / PGWire clients | QuestDB process OOM-killed during query | `dmesg | grep -i oom`; check JVM heap | Increase `-Xmx`; add `LIMIT` to queries; reduce `cairo.max.uncommitted.rows` |
| `timestamp is out of order` silent ILP drops | Telegraf, Prometheus remote write via ILP | Non-WAL table rejecting out-of-order rows | `questdb_out_of_order_rows_total` counter | Migrate table to WAL; use designated timestamp with deduplication |
| HTTP 500 on `/exec` with `java.lang.OutOfMemoryError` | REST client, Grafana | Query materialising large result set in heap | QuestDB logs: `Java heap space` | Add `LIMIT`; use `SAMPLE BY` aggregation; increase `-Xmx` |
| Query hangs indefinitely (no response) | psql, JDBC, HTTP client | Full table scan without partition filter; query stuck | Check active queries: `SELECT * FROM query_activity()` in PGWire | Kill query: `SELECT cancel_query(query_id)`; add `WHERE timestamp BETWEEN ...` |
| `errno=28 No space left on device` in ILP logs | ILP client (silent drop) | Disk full; no writes possible | `df -h /var/lib/questdb` | Delete old partitions; extend volume; drop unused tables |
| `column not found` error from REST `/exec` | REST / JDBC client | Column name case mismatch; schema change deployed without client update | `SHOW COLUMNS FROM <table>` in PGWire | Match exact column name case; coordinate schema changes with client deploys |
| Grafana panel shows no data / gap in time-series | Grafana data source | WAL replay lag after restart; partition not yet applied | Check WAL status: `SELECT * FROM wal_tables()` | Wait for WAL apply to catch up; reduce `wal.apply.workers` backlog |
| `symbol capacity exceeded` error on ILP | ILP / REST client | Symbol column cardinality exceeds configured capacity | QuestDB logs: `symbol capacity overflow` | Increase `symbolCapacity` at table creation; use `VARCHAR` for high-cardinality fields |
| Slow PGWire query response from Grafana | Grafana | Missing partition filter triggering full table scan | Query runtime in QuestDB log; compare filtered vs unfiltered `EXPLAIN` | Add `WHERE timestamp BETWEEN ...` in Grafana query; create symbol index |
| ILP rows silently deduplicated | ILP client (no error) | WAL deduplication removing duplicate timestamps | Check row count before/after; `cairo.wal.enabled=true` and `dedupEnabled` | Set unique timestamps; disable dedup if not needed: `ALTER TABLE ... DEDUP DISABLE` |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Partition count growth causing mmap exhaustion | Partition-per-day tables accumulate; queries on wide date ranges slow | `SELECT count() FROM information_schema.tables`; `sysctl vm.max_map_count` | Weeks | Increase `vm.max_map_count`; use `PARTITION BY MONTH` or `YEAR` for historical tables |
| Uncommitted row accumulation in ILP buffer | `cairo.max.uncommitted.rows` approaching limit; ILP commit latency rising | QuestDB logs: `commit triggered by row count`; check `questdb_uncommitted_rows` | Hours | Reduce `cairo.max.uncommitted.rows`; tune `cairo.commit.lag` |
| WAL segment backlog growth | `SELECT * FROM wal_tables()` shows segments applied_rows lagging written_rows | `SELECT * FROM wal_tables() WHERE sequencerTxn > writerTxn` | Hours | Increase `wal.apply.workers`; check disk I/O on WAL volume |
| JVM old-gen heap growth | GC pause duration increasing week-over-week; heap after GC not reclaiming to baseline | `jstat -gcutil <pid> 5s 20`; GC log analysis | Days to weeks | Heap dump and analyze leaks; set `-XX:+UseG1GC`; upgrade QuestDB |
| Disk fill rate acceleration from write amplification | Disk usage growing faster than ingest rate suggests | `du -sh /var/lib/questdb/db/` per table over time | Days | Identify large tables; drop old partitions; enable `PARTITION BY MONTH` |
| Symbol dictionary growth consuming heap | `VmRSS` of QuestDB process growing; GC not reclaiming symbol memory | `jmap -histo <pid> | grep Symbol` | Weeks | Limit symbol cardinality; use `VARCHAR` for unbounded strings |
| ILP writer thread saturation | ILP throughput plateau; ingestion latency rising; `questdb_ilp_writer_queue_depth` high | `curl http://localhost:9000/metrics | grep ilp_writer_queue` | 30–60 min | Increase `line.tcp.writer.queue.size`; add ILP writer threads; batch messages at sender |
| Query cache eviction rate rising | Repeated queries with identical SQL showing no cache benefit; CPU rising | QuestDB metrics: `questdb_query_cache_evictions_total` | Hours | Increase `cairo.sql.query.cache.capacity`; identify queries causing eviction churn |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# questdb-health-snapshot.sh — Point-in-time health overview
set -euo pipefail
QDB_HTTP="${QDB_HTTP:-http://localhost:9000}"
QDB_USER="${QDB_USER:-admin}"
QDB_PASS="${QDB_PASS:-quest}"

echo "=== QuestDB Health Snapshot $(date -u) ==="

echo -e "\n--- Health Check ---"
curl -sf "$QDB_HTTP/health" && echo " [HEALTHY]" || echo " [UNHEALTHY — check process]"

echo -e "\n--- Build Info ---"
curl -sf -u "$QDB_USER:$QDB_PASS" "$QDB_HTTP/exec?query=SELECT+version()" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('dataset',[['']])[0][0])" 2>/dev/null || true

echo -e "\n--- ILP Listener ---"
ss -tulpn | grep ':9009' && echo "ILP port 9009 bound" || echo "ILP port 9009 NOT bound"

echo -e "\n--- PGWire Listener ---"
ss -tulpn | grep ':8812' && echo "PGWire port 8812 bound" || echo "PGWire port 8812 NOT bound"

echo -e "\n--- Disk Usage ---"
df -h /var/lib/questdb 2>/dev/null || df -h . 2>/dev/null | tail -1
QDB_PID=$(pgrep -f questdb | head -1)
[ -n "$QDB_PID" ] && grep -E 'VmRSS|VmPeak' /proc/$QDB_PID/status 2>/dev/null || true

echo -e "\n--- WAL Tables Status ---"
curl -sf -u "$QDB_USER:$QDB_PASS" "$QDB_HTTP/exec?query=SELECT+name,sequencerTxn,writerTxn,writerLagTxnCount+FROM+wal_tables()+ORDER+BY+writerLagTxnCount+DESC+LIMIT+10" \
  | python3 -c "
import sys, json
r = json.load(sys.stdin)
cols = [c['name'] for c in r.get('columns', [])]
for row in r.get('dataset', []):
    print(dict(zip(cols, row)))
" 2>/dev/null || echo "WAL query failed — may not be running"

echo -e "\n--- Recent Errors in Log ---"
journalctl -u questdb -n 30 --no-pager 2>/dev/null | grep -iE 'error|exception|fail|oom' | tail -15 || \
  find /var/log/questdb /opt/questdb/log -name "*.log" 2>/dev/null | xargs grep -iE 'ERROR|exception' 2>/dev/null | tail -15 || \
  echo "Log not found"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# questdb-perf-triage.sh — Query latency, ILP throughput, slow query identification
QDB_HTTP="${QDB_HTTP:-http://localhost:9000}"
QDB_USER="${QDB_USER:-admin}"
QDB_PASS="${QDB_PASS:-quest}"

EXEC() {
  curl -sf -u "$QDB_USER:$QDB_PASS" "$QDB_HTTP/exec" --data-urlencode "query=$1" \
    | python3 -c "
import sys, json
r = json.load(sys.stdin)
cols = [c['name'] for c in r.get('columns', [])]
for row in r.get('dataset', []):
    print('  ', dict(zip(cols, row)))
" 2>/dev/null
}

echo "=== QuestDB Performance Triage $(date -u) ==="

echo -e "\n--- Active Queries ---"
EXEC "SELECT queryId, threadId, state, sql FROM query_activity() LIMIT 20"

echo -e "\n--- Table Sizes (top 10) ---"
EXEC "SELECT table_name, partition_count, row_count, disk_size FROM tables() ORDER BY disk_size DESC LIMIT 10"

echo -e "\n--- WAL Lag (tables with pending transactions) ---"
EXEC "SELECT name, sequencerTxn - writerTxn AS lag_txns, writerLagTxnCount FROM wal_tables() WHERE sequencerTxn > writerTxn ORDER BY lag_txns DESC LIMIT 10"

echo -e "\n--- ILP Metrics ---"
curl -sf "$QDB_HTTP/metrics" 2>/dev/null | grep -E 'questdb_ilp|questdb_line' | grep -v '^#' | head -20 || echo "Prometheus metrics endpoint not available"

echo -e "\n--- JVM Memory (from /metrics) ---"
curl -sf "$QDB_HTTP/metrics" 2>/dev/null | grep -E 'jvm_memory|jvm_gc' | grep -v '^#' | head -15 || true

echo -e "\n--- Partitions at Risk of mmap Exhaustion ---"
EXEC "SELECT table_name, partition_count FROM tables() WHERE partition_count > 1000 ORDER BY partition_count DESC LIMIT 10"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# questdb-resource-audit.sh — FDs, JVM heap, ILP/PGWire connections
QDB_HTTP="${QDB_HTTP:-http://localhost:9000}"
QDB_USER="${QDB_USER:-admin}"
QDB_PASS="${QDB_PASS:-quest}"

echo "=== QuestDB Resource Audit $(date -u) ==="

QDB_PID=$(pgrep -f questdb | head -1)
if [ -n "$QDB_PID" ]; then
  echo -e "\n--- JVM Process (PID $QDB_PID) ---"
  grep -E 'VmRSS|VmPeak|VmSwap|VmSize' /proc/$QDB_PID/status 2>/dev/null || true

  echo -e "\n--- Open File Descriptors ---"
  FD=$(ls /proc/$QDB_PID/fd 2>/dev/null | wc -l)
  FD_LIM=$(awk '/Max open files/{print $4}' /proc/$QDB_PID/limits 2>/dev/null || echo "?")
  echo "FDs open: $FD / limit: $FD_LIM"

  echo -e "\n--- mmap regions (partition files) ---"
  wc -l /proc/$QDB_PID/maps 2>/dev/null | awk '{print "mmap regions:", $1}'
  echo "vm.max_map_count = $(sysctl -n vm.max_map_count 2>/dev/null || echo '?')"

  echo -e "\n--- JVM GC Quick Stats ---"
  jstat -gcutil "$QDB_PID" 2>/dev/null | tail -2 || echo "jstat not available"
fi

echo -e "\n--- Network Connections ---"
echo "ILP (9009 TCP):"
ss -tnp | grep ':9009' | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -10 || true
echo "PGWire (8812):"
ss -tnp | grep ':8812' | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -10 || true
echo "HTTP (9000):"
ss -tnp | grep ':9000' | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -10 || true

echo -e "\n--- Disk Usage by Component ---"
QDB_DATA="${QDB_DATA_DIR:-/var/lib/questdb}"
for d in db conf log snapshot; do
  [ -d "$QDB_DATA/$d" ] && du -sh "$QDB_DATA/$d" 2>/dev/null | xargs -I{} echo "  $d: {}" || true
done
df -h "$QDB_DATA" 2>/dev/null | tail -1 || true
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| High-frequency ILP writer flooding table lock | Other ILP writers experiencing `table writer is busy`; ingest latency rising | Monitor `questdb_table_writer_wait_seconds` per table; identify hottest table | Enable WAL on the contended table; serialise non-WAL writers | Migrate all hot tables to WAL mode at design time; never share non-WAL tables between producers |
| Full-table-scan query blocking ILP commit on shared table | ILP ingest stalled; `query_activity()` shows long-running SELECT on same table | `SELECT queryId, sql FROM query_activity()` — find SELECT without partition filter | Kill the offending query: `SELECT cancel_query(id)`; add `WHERE timestamp BETWEEN ...` | Enforce partition filter in application queries; set `cairo.sql.max.negative.limit` |
| JVM GC pause from large in-memory query starving ILP threads | ILP throughput drops during GC pauses; query response time spikes | JVM GC log: `pause GC` duration; correlate with ILP metric drops | Increase `-Xmx`; use G1GC with `-XX:MaxGCPauseMillis=200`; limit result set size | Add `LIMIT` and `SAMPLE BY` to analytics queries; separate analytical and ingest workloads |
| Disk I/O saturation from concurrent WAL apply + ILP write | Write latency rising; `iostat` showing 100% disk utilization | `iostat -x 1 10`; `iotop` to identify ILP vs WAL apply I/O | Separate WAL apply volume from ILP write volume using different mount points | Provision high-IOPS SSD for WAL path; use separate disks for DB files and WAL |
| Shared host disk filling from unrelated application logs | QuestDB write errors (`errno=28`) due to full volume | `du -sh /var/log/*`; identify non-QuestDB disk consumers | Move QuestDB to dedicated volume; clean up log files | Mount QuestDB data on a dedicated block device with separate filesystem; monitor disk usage |
| Too many concurrent HTTP query connections exhausting QDB thread pool | REST queries queuing; HTTP response latency rising | `ss -tn | grep ':9000' | wc -l`; compare to `shared.worker.count` | Reduce client connection pool size; add `LIMIT` to queries | Set `http.connection.pool.initial.capacity`; use connection pooling in application (PgBouncer) |
| Symbol column cardinality growth consuming JVM heap | Heap usage growing proportionally to unique symbol values ingested | `jmap -histo <pid> | head -30`; identify symbol storage objects | Change high-cardinality columns to `VARCHAR`; drop and recreate table | Audit schema design at creation: use `SYMBOL` only for columns with < 10K distinct values |
| Snapshot operation locking table during backup window | Queries returning errors during snapshot; ILP ingest paused | Check active snapshot: `SELECT * FROM snapshot_status()` if available; look for lock in query_activity | Schedule snapshots during off-peak; reduce snapshot frequency | Use volume-level snapshot (LVM/cloud snapshot) instead of QuestDB table snapshots for minimal lock time |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| QuestDB JVM OOM kill | All active ILP TCP connections dropped; ILP clients retry; write queue floods on reconnect; active SQL queries lost | All ingesting services lose data for the OOM window; all SQL clients disconnected | `dmesg | grep -i oom` shows questdb kill; metrics from QuestDB go dark; ILP clients log `Connection refused` | Reduce `-Xmx` to leave 2GB headroom for OS; add swap; reduce concurrent query limit |
| Disk full on QuestDB data volume | WAL writes fail; ILP ingestion returns errors; SQL INSERT fails; new partition creation blocked | All write operations; reads continue on existing data | QuestDB logs `errno=28: no space left on device`; ILP clients receive `ERROR protocol buffer too short` | Purge old partitions: `ALTER TABLE <name> DROP PARTITION LIST '<date>'`; extend volume |
| Table writer lock contention (non-WAL table) | ILP writers serialise behind single lock; downstream time-series pipeline shows latency; queue backlog grows | All ILP producers writing to the same non-WAL table | `SELECT queryId, sql FROM query_activity()` shows ILP writers waiting; ingest rate drops suddenly | Migrate to WAL: `ALTER TABLE <name> SET PARAM walEnabled=true`; reduces contention |
| Long-running analytical query blocking WAL apply | WAL apply on table paused; ILP ingest succeeds but committed data not visible to queries; readers see stale data | All readers of affected table see stale data; may trigger SLO breaches for real-time dashboards | `SELECT * FROM wal_tables()` shows `sequencerTxn` not advancing; active query from `query_activity()` is blocking | Kill blocking query: `SELECT cancel_query(<id>)`; enforce query timeout in application |
| Network partition between ILP clients and QuestDB TCP 9009 | ILP batches lost on client side; data gap in time-series tables; clients buffer until reconnect or drop | All metrics/IoT data ingested via ILP during partition window | ILP client logs `Connection timed out: 9009`; no new rows in affected tables when queried | Enable ILP client-side buffering / retry with backpressure; monitor table lag |
| PGWire connection pool exhausted | SQL API calls begin queuing; Grafana dashboards time out; application queries return `FATAL: too many connections` | All SQL-based readers and dashboards; ILP ingest unaffected | `ss -tn | grep ':8812' | wc -l` at configured max; QuestDB logs `connection refused: max connections reached` | Reduce application connection pool size; add PgBouncer in front of port 8812 |
| Upstream Kafka consumer falling behind, re-ingesting old messages via ILP | Duplicate rows ingested into designated timestamp table; duplicate-timestamp constraint violation possible | Specific tables receiving Kafka replay; out-of-order ingestion may break ordered reads | Row count growing faster than expected; `SELECT count() FROM <table> SAMPLE BY 1h` shows historical spike | Pause Kafka consumer at topic; deduplicate before re-ingesting using `INSERT INTO ... SELECT DISTINCT ...` |
| Cold startup after crash — mmap re-mapping large tables | QuestDB startup takes 5–15 minutes remapping large partition files; HTTP and ILP endpoints not ready | All services depending on QuestDB; readiness probe fails; Kubernetes may restart pod during startup | `journalctl -u questdb | grep -i mmap`; port 9000 health check returns 503 during startup | Increase readiness probe `initialDelaySeconds`; pre-warm by reading a small query after startup confirmation |
| Snapshot blocking ILP writes on large table | ILP clients timeout during snapshot window; data gap during backup | ILP ingest to snapshotted table | Ingest client logs `write timeout`; `questdb_table_writer_wait_seconds` elevated during backup window | Use LVM/cloud volume snapshot instead of QuestDB `SNAPSHOT PREPARE`; lower contention |
| Stale ILP connection holding writer lock after client crash | Table becomes unwritable for new ILP clients; ingest silently fails | All new ILP writers for that table until QuestDB detects dead connection | New ILP clients connect but `insert` hangs; no rows added; `query_activity()` shows idle writer | Restart QuestDB or wait for TCP keepalive to detect dead connection; reduce `cairo.max.uncommitted.rows` |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| QuestDB version upgrade (minor) | Partition file format incompatible; startup fails with `partition format version mismatch` or data read errors | Immediately on first startup with new binary | Compare log error to release notes for storage format changes | Roll back binary; QuestDB data files are generally backward compatible within minor versions |
| Adding designated timestamp column to existing table | `ALTER TABLE` fails if existing rows violate timestamp ordering; rewrite required | Immediately on DDL execution | DDL returns `ERROR: timestamp column already exists` or `out-of-order timestamp` | Use `CREATE TABLE ... AS SELECT ... ORDER BY ts` to create a new ordered table and swap |
| Reducing `cairo.max.uncommitted.rows` | WAL commits more frequently; higher I/O per second; disk IOPS may saturate on large ingestion rates | Immediately on restart with new config | `iostat -x 1 10` shows elevated disk write after restart; compare I/O before and after | Revert to original value in `server.conf`; restart QuestDB |
| Increasing partition period from `DAY` to `MONTH` | Existing daily partitions remain; new data goes into monthly; mixed granularity confuses partition-level TTL management | From next partition boundary | `SHOW PARTITIONS FROM <table>` shows mixed `DAY`/`MONTH` partition names | Cannot change partition period in-place; recreate table with new partition scheme and backfill |
| Enabling SSL/TLS on PGWire port 8812 | Existing plain-text clients fail to connect; `FATAL: SSL connection required` | Immediately on restart | Client logs `javax.net.ssl.SSLException: Unrecognized SSL message`; PgBouncer breaks if configured as plain TCP | Distribute CA cert to all clients; update JDBC URL to `ssl=true`; or configure SSL optional mode |
| Rotating IAM credentials used for S3 cold storage | `COPY TO` and cold storage queries fail with `AccessDenied` | Immediately when old key expires | QuestDB logs `S3Exception: The AWS Access Key Id you provided does not exist`; S3-backed queries return errors | Update `server.conf` with new key ID and secret; restart QuestDB |
| Changing `line.tcp.recv.buffer.size` to smaller value | ILP clients sending large batches get `Buffer overflow` errors; partial batch writes | Immediately on first large batch after restart | ILP client logs `protocol buffer too short` or `connection reset`; partial data in table | Revert to default (1MB or larger); restart QuestDB |
| Dropping and recreating table with different column types | Existing Grafana dashboards break; queries referencing old column type fail; ILP clients writing to dropped table name get schema mismatch error | Immediately on table drop | Grafana logs `column type mismatch`; ILP error `cannot cast DOUBLE to LONG` | Restore original table from snapshot; or update all downstream consumers to new schema before recreating |
| Reducing `shared.worker.count` | SQL queries queue; REST API p99 latency rises; dashboard response slow during peak | Immediately on restart | `ss -tn | grep ':9000'` shows connection backlog; REST API latency metric rises | Increase `shared.worker.count` back to default (core count); restart |
| Updating `cairo.sql.copy.buffer.size` to very large value | JVM heap pressure from large copy buffers; GC pauses more frequent; risk of OOM on concurrent COPY operations | Minutes after first COPY operation with new config | GC pause duration in JVM logs; heap usage spike visible in JVM monitoring | Revert `cairo.sql.copy.buffer.size` to default (2MB); restart QuestDB |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| WAL apply lag — committed data not yet visible to readers | `SELECT * FROM wal_tables() WHERE sequencerTxn > writerTxn` | Rows ingested via ILP appear missing in SELECT queries for seconds to minutes | Dashboards show data gap that self-heals; real-time monitoring appears delayed | This is normal WAL apply behaviour; reduce `cairo.max.uncommitted.rows` for faster visibility; or use `mat` (materialised) tables |
| Duplicate rows from ILP client retry after network error | `SELECT count(), timestamp FROM <table> SAMPLE BY 1s` — find seconds with double the expected row count | Row counts per time window are doubled; aggregations over-count | Inflated metrics; SUM/AVG return incorrect values; capacity planning overestimates | Design ILP clients with idempotency keys; deduplicate with `SELECT DISTINCT` into a new table; use WAL with dedup logic |
| Out-of-order timestamp rows causing partition skip | `SELECT min(timestamp), max(timestamp) FROM <table>` — gap in timestamp range | Some rows not returned in time-range queries; anomalies in time-series charts | Missing data in dashboards for affected time range; SLO burn rate calculations wrong | Enable out-of-order ingestion: `cairo.commit.lag` setting; or discard OOO rows at ILP layer |
| QuestDB crash during partition write leaves partial partition | After restart, query over partial partition returns fewer rows or checksum error | Partition appears in `SHOW PARTITIONS` but query returns error or truncated results | Data loss for the partial partition; time-series has gap for that period | Drop partial partition: `ALTER TABLE <name> DROP PARTITION LIST '<date>'`; re-ingest from source if available |
| Cold storage (S3) returning stale object version | `SELECT ... FROM read_parquet('s3://<bucket>/...')` — returns old data | Query against cold tier returns data from before last update | Historical analytics returns incorrect values for reprocessed time windows | Invalidate S3 object: check S3 versioning and confirm latest version; disable S3 caching if configured in QuestDB |
| Schema evolution — new column added by one ILP client, not others | `SHOW COLUMNS FROM <table>` — new column present; older clients sending data without that column | New column has `null` for rows from legacy clients; queries expecting the column break for those rows | Incomplete data for new field; joins/aggregations on new column unreliable | Coordinate schema changes across all producers; use `ALTER TABLE ADD COLUMN IF NOT EXISTS` from all clients simultaneously |
| Table exists in meta but files missing from disk | `SHOW TABLES` lists table; `SELECT count() FROM <name>` returns `ERROR: file not found` | Table metadata intact but actual partition/column files deleted or corrupted | Reads fail; table appears healthy in list view but is actually empty or broken | Drop table metadata and restore from snapshot: `DROP TABLE <name>; RESTORE TABLE <name> FROM '<snapshot>'` |
| `COPY` command importing CSV with wrong timestamp format | Rows inserted with epoch `0` or wrong date; time-series order broken | `SELECT min(timestamp), max(timestamp) FROM <table>` shows year 1970 or unexpected range | All time-range queries return empty; partition structure based on wrong timestamps | Drop affected partitions; re-run COPY with correct `timestampFormat` parameter |
| Concurrent DDL and DML causing lock acquisition deadlock | QuestDB logs `timeout waiting for writer lock`; both DDL and DML stall | Table unwritable until timeout; ILP clients buffer data during the deadlock window | Data gap during deadlock window; potential ILP client overflow | Always perform DDL in maintenance windows with ILP paused; avoid concurrent ALTER TABLE during active ingestion |
| Checkpoint (SNAPSHOT COMPLETE) missed — tables inconsistent post-restore | After restoring snapshot, `SELECT * FROM wal_tables()` shows `sequencerTxn` mismatch | Some tables have data from after snapshot checkpoint; others do not; cross-table joins give inconsistent results | Inconsistent state across tables; foreign-key equivalent queries return wrong results | Re-ingest data from authoritative source (Kafka replay) for the gap window after snapshot timestamp |

## Runbook Decision Trees

### Decision Tree 1: ILP Ingest Failure Triage

```
Is QuestDB process running?
(check: systemctl status questdb OR curl -s http://localhost:9000/health)
├── NO → Service down
│   ├── Check OOM: dmesg | grep -i 'oom\|killed' | grep questdb
│   │   ├── OOM found → Reduce -Xmx in /var/lib/questdb/conf/jvm.conf; restart questdb
│   │   └── No OOM → Check startup error: journalctl -u questdb -n 50 | grep -E 'ERROR|Exception'
│   │       ├── WAL replay error → Remove corrupt WAL segment (see DR Scenario 3); restart
│   │       └── Config error → Restore server.conf from backup; restart
└── YES → Is port 9009 (ILP) accepting connections?
    (check: nc -zv localhost 9009 2>&1)
    ├── Connection refused → ILP listener not started → Check 'line.tcp.enabled=true' in server.conf
    │   Restart questdb; verify: ss -tnlp | grep 9009
    └── Connection accepted → Is disk space available?
        (check: df -h /var/lib/questdb)
        ├── Disk > 85% full → ILP writes failing silently or with error
        │   Free space: ALTER TABLE <name> DROP PARTITION LIST '<old_date>'
        │   Extend volume; then: systemctl restart questdb to clear write queue
        └── Disk OK → Check WAL table status: SELECT * FROM wal_tables()
            ├── sequencerTxn >> writerTxn (large delta) → WAL apply lagging
            │   Check blocking query: SELECT * FROM query_activity()
            │   Kill blocker: SELECT cancel_query(<id>); verify apply catches up
            └── WAL apply OK → Check ILP error in QuestDB log
                grep -i 'ILP\|line.tcp\|cannot cast\|schema' /var/log/questdb/questdb.log | tail -20
                ├── Schema mismatch error → ILP sending wrong types → fix producer schema
                └── No errors → Reproduce: echo "test,host=a v=1.0 $(date +%s)000000000" | nc localhost 9009
```

### Decision Tree 2: SQL Query Returning Unexpected or Stale Results

```
Are SQL errors returned (HTTP 500 or SQL error in response body)?
(check: curl -G http://localhost:9000/exec --data-urlencode "query=SELECT 1")
├── YES → Error response
│   ├── "file not found" → Table metadata exists but files missing
│   │   Verify: ls /var/lib/questdb/db/<table>/
│   │   → Restore from snapshot or drop and recreate table
│   ├── "timestamp out of order" → Insert violates designated timestamp ordering
│   │   → Enable OOO ingestion via cairo.commit.lag setting; or fix producer
│   └── "too many open files" / "ulimit" → OS file descriptor limit hit
│       → ulimit -n 65536; update /etc/security/limits.conf; restart questdb
└── NO → Query succeeds but results are wrong or empty
    Is the time range correct in your query WHERE clause?
    (check: SELECT min(ts), max(ts), count() FROM <table>)
    ├── min/max range is wrong (e.g. epoch 0) → Bad timestamp on ingest
    │   Dropped partition or COPY with wrong format → re-ingest with correct timestampFormat
    └── Time range looks right but counts are low
        Is WAL apply current?
        SELECT * FROM wal_tables() WHERE sequencerTxn > writerTxn
        ├── WAL lagging → Recent data not yet visible → wait or kill blocking queries
        └── WAL current → Check for OOO rows: SELECT count() FROM <table> WHERE ts < now()-interval 5m
            ├── Count lower than expected → Rows may have been dropped by OOO policy
            │   → Review cairo.commit.lag and out-of-order ingestion settings
            └── Count matches → Verify application is reading from correct table/partition
                Check: SHOW PARTITIONS FROM <table>; verify expected partition dates exist
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Partition retention not set — all data kept forever | Disk growing without bound; no automated partition expiry | `SHOW PARTITIONS FROM <table>` — list grows indefinitely; `df -h /var/lib/questdb` | Disk full → all writes fail | Drop old partitions: `ALTER TABLE <name> DROP PARTITION WHERE ts < dateadd('y', -1, now())`  | Implement automated TTL job; schedule daily cron to drop partitions older than retention window |
| High-frequency ILP sending too many unique symbol values | Symbol column cardinality explodes; memory growth per symbol column | `SELECT count() FROM (SELECT DISTINCT <symbol_col> FROM <table>)` | JVM heap pressure; slow queries; risk of OOM | Switch high-cardinality column from `SYMBOL` to `STRING` type; reduce symbol cache size in server.conf | Audit ILP schema; use SYMBOL only for low-cardinality fields (< 10K distinct values) |
| Cold storage COPY TO S3 running continuously without rate limiting | S3 API call costs spike; QuestDB I/O saturated during COPY | `SELECT * FROM query_activity() WHERE sql LIKE '%COPY%'` | QuestDB I/O starved for ILP writes; S3 egress costs | Limit COPY frequency; schedule COPY during off-peak hours; use `cairo.sql.copy.buffer.size` to throttle | Schedule COPY jobs via cron at fixed intervals; set S3 lifecycle policy to archive old data |
| Too many concurrent SQL sessions from BI tools | Connection pool exhausted; new connections refused; QuestDB CPU saturated | `ss -tn | grep ':8812' | wc -l` vs `pg.net.connection.limit` in server.conf | All SQL readers blocked; no capacity for application queries | Kill idle sessions; add PgBouncer in front of port 8812; increase `pg.net.connection.limit` | Set connection limits per BI tool; use read replicas or query federation for analytics workloads |
| Uncompacted WAL from high-OOO ingestion rate | WAL directory grows; restart time exceeds readiness probe timeout | `du -sh /var/lib/questdb/db/<table>/wal/` | Slow cold start; probe restarts pod repeatedly; data visible only after long WAL replay | Lower `cairo.max.uncommitted.rows` to trigger more frequent commits; reduce OOO commit lag | Tune ILP producer to send data in order where possible; monitor WAL directory size as a metric |
| Analytical queries with no SAMPLE BY clause scanning full table | CPU 100%; other queries starved; ILP write latency rises | `SELECT * FROM query_activity()` — full-scan queries with large elapsed time | QuestDB becomes unresponsive for writes and other reads | Kill runaway query: `SELECT cancel_query(<id>)`; add `SAMPLE BY` or `WHERE ts > now() - interval 1h` | Enforce query review before production access; set `query.timeout.default` in server.conf |
| Snapshot (SNAPSHOT PREPARE) blocking on large table during backup window | ILP writes stall; clients timeout during snapshot | `journalctl -u questdb | grep 'snapshot'` — PREPARE phase duration | Data gap during snapshot window for ILP writers | Use LVM snapshot or cloud volume snapshot instead; or schedule snapshots during low-traffic period | Prefer file-system-level snapshots for large QuestDB deployments; document expected backup window duration |
| S3-backed cold storage query scanning full bucket with no partition pruning | S3 GET costs spike; query latency very high; S3 rate limit errors | `SELECT * FROM query_activity()` — queries against cold storage with wide time range | S3 API rate limit errors; high egress costs; query timeouts | Add time-range filter to queries: `WHERE ts BETWEEN '2024-01-01' AND '2024-01-31'`; abort wide scans | Enforce partition pruning at application layer; document cold storage query cost expectations |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot table from single high-frequency ILP sender | One table's write worker CPU saturated; ILP latency rising for that table only | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT * FROM query_activity()"` during spike; `iostat -x 1 5` on QuestDB data disk | Single-threaded ILP writer per table; all writes from one producer serialized | Shard ILP writes across multiple tables by time range or tenant; increase `line.tcp.io.worker.count` in server.conf |
| Connection pool exhaustion on PostgreSQL wire port 8812 | Application gets `connection refused` or hangs; SQL queries fail for new connections | `ss -tn \| grep ':8812' \| grep ESTABLISHED \| wc -l`; `cat /etc/questdb/conf/server.conf \| grep pg.net.connection.limit` | BI tool or application opening connections without pooling; `pg.net.connection.limit` reached | Add PgBouncer in front of port 8812; increase `pg.net.connection.limit`; kill idle sessions with `SELECT pg_terminate_backend(pid)` |
| GC pressure from large JVM heap during heavy SQL analytics | QuestDB query latency spikes every few minutes; GC pause visible in JVM logs | `grep 'Full GC' /var/log/questdb/gc.log`; JVM JFR: `jcmd $(pgrep -f questdb) JFR.start duration=60s filename=/tmp/qdb.jfr` | Analytical queries materializing large intermediate result sets in JVM heap | Set `cairo.sql.sort.key.page.size` and `cairo.sql.sort.value.page.size`; enable off-heap sorting; tune `-Xmx` for query workload |
| Thread pool saturation from concurrent SAMPLE BY queries | Multiple SAMPLE BY queries queue up; query execution time multiplied | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT * FROM query_activity()"` — many `state=active` rows | QuestDB worker thread pool shared across all query types; CPU-bound analytics starve ILP writes | Separate ILP writers from SQL workers; set `line.tcp.io.worker.count` and `shared.worker.count` explicitly; kill long-running queries |
| Slow full-table scan due to missing timestamp predicate | Query scan all partitions; duration in minutes; other queries blocked | `EXPLAIN SELECT * FROM <table> WHERE non_timestamp_col = 'value'`; partition scan count in EXPLAIN output | Query without `WHERE timestamp BETWEEN ...`; all partitions read sequentially | Rewrite query with time predicate: `WHERE ts > dateadd('d', -7, now())`; add `LIMIT` clause; create column index: `CREATE INDEX ON <table>(col)` |
| CPU steal on shared VM hosting QuestDB | Write and query latency high without visible CPU pressure locally; steal time present | `sar -u 1 5 \| grep -v '^$'`; `top \| grep '%st'`; `node_cpu_seconds_total{mode="steal"}` | Noisy neighbor VMs; hypervisor CPU scheduling delays for QuestDB process | Migrate to dedicated bare metal or CPU-pinned VM; request burst CPU credits on cloud provider |
| Lock contention during concurrent WAL apply and SQL queries | Query latency spikes when WAL is applying; `wal_tables()` shows `suspendedReason` | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT * FROM wal_tables()"` — check `sequencerTxn` vs `writerTxn` | WAL apply holding table write lock; concurrent reads blocked during apply | Tune `cairo.max.uncommitted.rows` to apply WAL more frequently in smaller batches; avoid long-running read queries during peak write periods |
| Serialization overhead from wide table with many symbol columns | Query response slow for wide `SELECT *`; CPU on query worker high | Time query: `\timing` in QuestDB console; `SELECT count() FROM <table>` vs `SELECT * FROM <table> LIMIT 1000` | Symbol column deserialization for many distinct values; large row width | Narrow SELECT to only needed columns; convert rarely-queried SYMBOL columns to STRING; use column projection in application queries |
| ILP batch size too small causing per-batch overhead | ILP write throughput lower than expected; many small batches visible in ILP metrics | QuestDB log: `grep 'line.tcp' /var/log/questdb/questdb.log \| grep 'batch'`; sender-side: check batch size config | ILP client flushing every row instead of buffering; round-trip overhead dominates | Set ILP client `auto_flush_rows: 75000` and `auto_flush_interval: 1000` (ms); buffer multiple rows per TCP send |
| Downstream S3 COPY latency cascading to partition archival | `COPY <table> TO 's3://...'` blocking; S3 GET latency high during query | `SELECT * FROM query_activity() WHERE sql LIKE '%COPY%'`; S3 latency from cloud console | S3 regional latency spike; S3 throttling due to too many objects in prefix | Use S3 transfer acceleration; reduce COPY frequency; add hash prefix to S3 keys to avoid hotspot; abort and retry during low-traffic window |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on HTTP API port 9000 | REST clients get `x509: certificate has expired`; Grafana datasource fails | `openssl x509 -enddate -noout -in /etc/questdb/conf/server.crt`; `curl -v https://questdb:9000/health 2>&1 \| grep expire` | All REST, Grafana, and web console access fails | Rotate cert; update `tls.cert.path` in server.conf; restart: `systemctl restart questdb` |
| mTLS failure between QuestDB and BI/app clients | mTLS-configured clients rejected with `certificate unknown`; unprotected clients unaffected | `openssl verify -CAfile /etc/questdb/conf/ca.crt /etc/questdb/conf/client.crt`; `curl --cert client.crt --key client.key https://questdb:9000/health` | mTLS clients cannot query; data pipeline blocked | Re-issue client certs from correct CA; update server `tls.ca.path` with new CA bundle; restart QuestDB |
| DNS resolution failure for S3 cold storage endpoint | `COPY TO 's3://bucket'` fails with `no such host`; archival job errors | `dig s3.amazonaws.com` from QuestDB host; `curl -I https://s3.amazonaws.com` | Cold storage COPY fails; data archival blocked; disk fills if no cleanup | Fix DNS: check `/etc/resolv.conf`; use S3 endpoint IP as fallback; verify VPC DNS resolver settings |
| TCP connection exhaustion on ILP port 9009 | ILP clients fail to connect; `connection refused`; QuestDB log: `accept: too many open files` | `ss -tn \| grep ':9009' \| grep ESTABLISHED \| wc -l`; `cat /proc/$(pgrep -f questdb)/limits \| grep 'open files'` | ILP ingest stops; metrics/time-series data lost | Increase `LimitNOFILE` for QuestDB process; set `line.tcp.net.connection.limit` in server.conf; recycle idle ILP connections |
| Load balancer misconfiguration breaking long-lived ILP TCP connections | ILP clients reconnect every few minutes; data gaps visible | Client log: repeated reconnection events; `netstat -tn \| grep ':9009'` shows TIME_WAIT pattern | ILP reconnection causes write gaps; potential duplicate detection issues | Set LB TCP idle timeout > ILP keepalive interval; use NLB with TCP passthrough; or bypass LB for ILP with direct pod/host access |
| Packet loss on ILP write path | ILP write latency high; `errno: EINTR` or retransmit in ILP client logs | `ping -c 100 <questdb-host>`; `tcpdump -i eth0 port 9009 -n -c 1000 \| grep 'retransmit'` | Network path congestion; packet drops triggering TCP retransmit | Investigate network path; reduce ILP batch interval; enable ILP TCP keepalive; route ILP traffic on separate NIC |
| MTU mismatch causing large ILP batch failures | Large ILP batches silently dropped or partially written; data gaps | `ping -M do -s 8972 <questdb-host>` from ILP producer; `tcpdump -i eth0 -n port 9009 \| grep fragment` | Large ILP messages fragmented; partial writes cause row rejection | Align MTU on all network interfaces: `ip link set dev eth0 mtu 1450`; reduce ILP `auto_flush_rows` to send smaller TCP segments |
| Firewall rule blocking PG wire port 8812 | SQL queries fail; `connection refused` on port 8812; HTTP API on 9000 still works | `telnet questdb 8812`; `nc -zv questdb 8812`; check firewall: `iptables -L INPUT -n \| grep 8812` | All PostgreSQL-compatible clients and BI tools disconnected | Restore firewall rule for port 8812; verify with `psql -h questdb -p 8812 -U admin -c '\l'` |
| SSL handshake timeout on PostgreSQL wire with TLS | psql client hangs on connect; `sslmode=require` times out | `timeout 5 openssl s_client -connect questdb:8812 -starttls postgres 2>&1 \| head`; check `pg.tls.*` settings in server.conf | BI tools and application DB connections fail; SQL queries unavailable | Verify TLS config in server.conf; test with `psql "host=questdb port=8812 sslmode=verify-ca sslrootcert=ca.crt"`; check cipher compatibility |
| Connection reset during long-running COPY TO S3 | COPY job fails mid-transfer with `broken pipe`; partial S3 object written | QuestDB log: `grep 'COPY\|broken pipe' /var/log/questdb/questdb.log`; check S3 for incomplete multipart uploads | Partial S3 export; corrupted archive if job not retried cleanly | Abort and retry COPY; add S3 lifecycle policy to abort incomplete multipart uploads after 1 day; increase `cairo.sql.copy.buffer.size` |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of QuestDB JVM | QuestDB process disappears; all connections dropped; `OOMKilled` in systemd or pod events | `dmesg -T \| grep -i 'oom\|killed' \| grep -i java`; `journalctl -u questdb \| grep 'Killed'` | Analytical query materializing large in-memory sort; heap too small | Restart with higher `-Xmx`: edit `JVM_OPTS` in `/etc/questdb/conf/jvm.options`; kill runaway query first: `SELECT cancel_query(<id>)` | Set `query.timeout.default` and `cairo.sql.parallel.filter.threshold`; alert when heap > 80% |
| Disk full on data partition (`/var/lib/questdb/db`) | ILP writes fail with `no space left`; WAL cannot be applied; QuestDB log shows disk error | `df -h /var/lib/questdb`; `du -sh /var/lib/questdb/db/*/` | Old partitions never dropped; no retention policy; compaction not freeing space | Drop old partitions: `ALTER TABLE <name> DROP PARTITION WHERE ts < dateadd('y', -1, now())`; extend disk | Set retention job (cron); alert at 70% disk; size disk for 2× expected data growth rate |
| Disk full on WAL partition | WAL segments accumulate; cannot apply new writes; ILP blocks | `du -sh /var/lib/questdb/db/*/wal/`; `df -h /var/lib/questdb` | WAL apply stalled (e.g., due to suspended WAL table); uncommitted WAL segments growing | Fix WAL suspension: `ALTER TABLE <name> RESUME WAL`; increase disk; reduce `cairo.max.uncommitted.rows` to flush more often | Monitor WAL table status: `SELECT * FROM wal_tables()` for suspended tables; alert on `writerTxn - sequencerTxn > 1000` |
| File descriptor exhaustion | QuestDB log: `too many open files`; partition files cannot be opened; queries fail | `lsof -p $(pgrep -f questdb) \| wc -l`; `cat /proc/$(pgrep -f questdb)/limits \| grep 'open files'` | Each open partition and column file consumes FDs; many active partitions with many columns | Increase `LimitNOFILE=1048576` in systemd unit file; restart QuestDB; reduce active partition count by dropping old partitions | Monitor `process_open_fds`; set `LimitNOFILE` in systemd before deployment |
| Inode exhaustion on data partition | New partition or column file creation fails; `no space left on device` with disk space available | `df -i /var/lib/questdb`; `find /var/lib/questdb/db -type f \| wc -l` | Wide tables with many columns × many partitions creating too many files | Drop old partitions; reduce partition granularity (YEAR instead of DAY): `PARTITION BY YEAR`; consolidate narrow tables | Use XFS for QuestDB data volume; partition by YEAR for archival data; MONTH for medium-frequency data |
| CPU steal/throttle in containerized deployment | Query latency high but container CPU appears low; wall clock time >> CPU time | `kubectl top pod <questdb-pod>`; `node_cpu_seconds_total{mode="steal"}`; `cat /sys/fs/cgroup/cpu/cpu.stat \| grep throttled` | CPU throttled by Kubernetes `resources.limits.cpu`; or noisy neighbor | Remove CPU limit (keep request only) for QuestDB; migrate to bare metal for time-series production workloads | Set `resources.requests.cpu = resources.limits.cpu` or remove limits; use Guaranteed QoS |
| Swap exhaustion from large in-memory sort | QuestDB query goes from fast to very slow; high disk I/O on swap partition; system thrashing | `free -h`; `vmstat 1 5`; `cat /proc/$(pgrep -f questdb)/status \| grep VmSwap` | Analytical query using more heap than available RAM; OS swapping JVM pages | Kill runaway query; disable swap: `swapoff -a`; increase JVM heap with off-heap sort: enable `cairo.sql.sort.value.page.size` | Disable swap on QuestDB hosts; set JVM heap large enough for typical query; enable off-heap sorting |
| Kernel PID limit from parallel query workers | QuestDB can't spawn query worker threads; queries queue indefinitely | `cat /proc/sys/kernel/threads-max`; `ps -T -p $(pgrep -f questdb) \| wc -l` | Parallel query workers + ILP workers + JVM threads exceeding OS thread limit | Increase: `sysctl -w kernel.threads-max=4194304`; reduce `shared.worker.count` and `line.tcp.io.worker.count` | Pre-set `kernel.threads-max` in `/etc/sysctl.d/`; tune worker counts based on CPU core count |
| Network socket buffer exhaustion on ILP port | ILP client stalls; throughput caps below expected; kernel: `send buffer overflow` | `sysctl net.core.wmem_max net.core.rmem_max`; `ss -tnp \| grep ':9009' \| awk '{print $3}'` | Default socket buffers too small for high-throughput ILP batches | `sysctl -w net.core.wmem_max=16777216 net.core.rmem_max=16777216`; persist in `/etc/sysctl.d/` | Tune socket buffers in node bootstrap; test sustained ILP throughput in staging before go-live |
| Ephemeral port exhaustion from concurrent S3 COPY jobs | COPY jobs fail with `cannot assign requested address`; S3 connections rejected | `ss -s \| grep TIME-WAIT`; `sysctl net.ipv4.ip_local_port_range` | Multiple parallel COPY operations creating many short-lived HTTPS connections to S3 | `sysctl -w net.ipv4.tcp_tw_reuse=1`; run COPY jobs sequentially; use S3 multipart upload via single connection | Set `net.ipv4.ip_local_port_range=1024 65535`; serialize COPY operations via cron schedule |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation: ILP duplicate on retry causes duplicate rows | Same timestamp + symbol combination inserted twice; COUNT > expected | `SELECT ts, symbol, count() FROM <table> WHERE ts BETWEEN '...' AND '...' SAMPLE BY 1s HAVING count() > 1` | Inflated metrics; incorrect aggregations; dashboards show doubled values | QuestDB does not deduplicate ILP by default — enable `DEDUP UPSERT KEYS(ts, symbol)` on WAL table: `ALTER TABLE <name> SET PARAM dedupEnabled = true` |
| WAL partial apply: sequencer committed but writer crashed mid-apply | `wal_tables()` shows `suspended = true`; table reads return data only up to suspend point | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT name, suspended, suspendedReason FROM wal_tables() WHERE suspended = true"` | New ILP writes queue in WAL but are not visible in queries; data appears stale | Resume WAL: `ALTER TABLE <name> RESUME WAL`; if corruption suspected: `ALTER TABLE <name> RESUME WAL FROM TRANSACTION <txn>` |
| Out-of-order timestamp ingestion exceeding OOO commit lag | Rows with old timestamps silently dropped; data gap for the OOO window | `SELECT min(ts), max(ts) FROM <table> WHERE ts < now() - interval 1h`; check server.conf `cairo.commit.lag` setting | Historical data never appears; queries show gap for backfill window | Increase OOO window: `ALTER TABLE <name> SET PARAM maxUncommittedRows = 500000`; set `cairo.commit.lag=600000` (ms) for larger OOO window |
| Cross-service deadlock: two writers locking same partition via concurrent DDL | `ALTER TABLE DROP PARTITION` and `INSERT` simultaneously timeout; both return error | QuestDB log: `grep 'writer locked\|TableBusy' /var/log/questdb/questdb.log`; check for concurrent DDL: `SELECT * FROM query_activity() WHERE sql LIKE '%ALTER%'` | DDL and DML operations both fail; table temporarily inaccessible | Serialize DDL operations via application-level lock (Redis); retry with exponential backoff; never run DDL during peak ILP ingestion |
| Saga partial failure: ILP write succeeds but downstream SQL read returns stale data | Data written via ILP not yet committed to WAL; SQL SELECT immediately after write returns 0 rows | `SELECT * FROM wal_tables() WHERE name = '<table>'` — check if `writerTxn = sequencerTxn` (committed) | Race condition in application between write and read; inconsistent state | Add `commit lag` awareness in application; poll `wal_tables()` until `writerTxn = sequencerTxn` or use HTTP `/exec` with explicit commit confirmation |
| At-least-once ILP delivery duplicate after network retry | Network timeout causes ILP client to resend last buffer; duplicate data for the retry window | `SELECT ts, count() FROM <table> SAMPLE BY 1s ORDER BY count() DESC LIMIT 20` — timestamps with 2× expected count | Doubled metric values for the retry window; affects rate calculations | Enable WAL deduplication: `ALTER TABLE <name> SET PARAM dedupEnabled = true` with `UPSERT KEYS(ts, <primary_key>)` |
| Compensating `DROP PARTITION` fails after bad data ingestion | Partition drop returns `TableWriter is busy`; bad data remains visible | `curl -G http://localhost:9000/exec --data-urlencode "query=ALTER TABLE <name> DROP PARTITION WHERE ts = '2024-01-15T00:00:00Z'"` — check for writer busy error | Bad data partition not removed; queries return corrupt results | Wait for active writes to stop; retry DROP PARTITION during off-peak; if stuck: restart QuestDB to release writer locks; then retry |
| Distributed lock expiry: two QuestDB instances writing same table file (no HA guard) | Filesystem-level file corruption; QuestDB log shows file lock errors | `fuser /var/lib/questdb/db/<table>/_meta`; check for multiple QuestDB processes: `pgrep -f questdb \| wc -l` | Table metadata corruption; data loss | Stop the second QuestDB process immediately; restore from snapshot; QuestDB does not support multi-writer — enforce single-writer architecture |
| Backfill via COPY corrupting active partition being written by ILP | `COPY INTO <table>` overlapping with live ILP writes to same partition | QuestDB log: `grep 'partition in use\|overlap' /var/log/questdb/questdb.log`; check `query_activity()` for concurrent COPY + ILP | Partition data inconsistency; queries return incorrect row counts | Stop ILP writers before running backfill COPY into active partitions; use separate table for backfill then `INSERT INTO <live_table> SELECT * FROM <backfill_table>` |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: analytical SAMPLE BY query monopolizing worker threads | `SELECT * FROM query_activity() WHERE type='select' AND elapsed > 5000` shows one long-running query; other queries queued | ILP ingest worker threads starved; write latency rising; real-time dashboards stall | Kill noisy query: `SELECT cancel_query(<id>)` from `query_activity()` | Set `query.timeout.default=60000` in server.conf; add `LIMIT` to all analytical queries; educate BI users on time-range predicates |
| Memory pressure from adjacent tenant's large in-memory sort | One tenant's `ORDER BY` query without partition predicate consuming all JVM heap; other queries OOM-failing | All SQL queries fail with `out of memory`; ILP writes unaffected but query API down | Kill runaway query: `SELECT cancel_query(<id>)`; if QuestDB unresponsive, restart service | Enforce query memory limits: tune `cairo.sql.sort.key.page.size` for off-heap sorting; add `LIMIT 10000` defaults in BI tool query templates |
| Disk I/O saturation from one tenant's high-frequency ILP partition writes | `iostat -x 1 5` shows disk util 100% correlated with one table's ILP write rate; WAL apply stalling | Other tables' WAL apply delayed; query freshness degraded for unaffected tables | Throttle ILP producer rate at application side; reduce `auto_flush_rows` in ILP client config | Separate high-throughput tables onto dedicated disk mount; set `line.tcp.io.worker.count` to isolate ILP workers per table |
| Network bandwidth monopoly from concurrent `COPY TO S3` jobs | Multiple tenants running COPY export simultaneously; network egress saturated; ILP TCP backpressure | ILP producers experience write timeouts; new metric data dropped or buffered | `SELECT cancel_query(<id>)` for lower-priority COPY jobs; allow only one COPY at a time | Serialize COPY operations via application-level queue; schedule large exports during off-peak; set AWS S3 transfer rate limit |
| Connection pool starvation from BI tool opening too many PG wire connections | `ss -tn \| grep ':8812' \| grep ESTABLISHED \| wc -l` near `pg.net.connection.limit`; new BI connections fail | Application SQL queries cannot connect; operational dashboards down while BI tools monopolize connections | Kill idle BI connections: `SELECT pg_terminate_backend(<pid>)` via PG wire | Deploy PgBouncer in front of port 8812; set per-user connection limit in `server.conf`; configure BI tool max connections |
| Quota enforcement gap: no partition retention per table | One tenant's high-frequency table accumulating partitions indefinitely; disk fills for all tenants | Disk alarm fires; all ILP ingest across all tables blocked | Immediately add retention: `ALTER TABLE <noisy-table> DROP PARTITION WHERE ts < dateadd('y',-1, now())` | Enforce partition retention policy at table creation; add `CRON` job for automated partition drops; alert when any table's partition count exceeds threshold |
| Cross-tenant data leak risk via shared QuestDB instance | Application A queries `SELECT * FROM tenant_b_metrics` on shared QuestDB with no row-level security | No database-level tenant isolation; all tables visible to any authenticated user | Create read-only user with table-level restriction; revoke cross-tenant access via PG wire user permissions | Deploy per-tenant QuestDB instances; or use schema separation with strict PG wire user privileges per schema |
| Rate limit bypass via ILP batch amplification | Single ILP producer sending batches of 1M rows per flush, bypassing per-connection rate limits | ILP write worker monopolized; other tables' writers starved during large batch apply | Check batch stats: `grep 'line.tcp\|commit' /var/log/questdb/questdb.log \| tail -50`; reduce producer batch size | Set `line.tcp.max.uncommitted.rows` per connection; enforce application-side batch size limits; monitor `questdb_ilp_received_rows_total` rate per client IP |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure for QuestDB Prometheus endpoint | `questdb_ilp_received_rows_total` absent from Prometheus; no alert fires for ingestion halt | QuestDB HTTP port 9000 accessible but Prometheus scrape job uses wrong path or bearer token | `curl http://prometheus:9090/api/v1/query?query=absent(questdb_ilp_received_rows_total)` returns value | Add `absent(questdb_ilp_received_rows_total)` alert; verify Prometheus scrape target uses path `/metrics` on port 9000 |
| Trace sampling gap missing slow partition scan incidents | SQL query latency p99 high in application APM but no trace showing QuestDB full-scan path | Application does not instrument QuestDB JDBC calls with OpenTelemetry; no span for SQL execution | `SELECT * FROM query_activity() ORDER BY elapsed DESC LIMIT 10` to find slow queries manually | Add OpenTelemetry instrumentation around JDBC/PG wire calls; use SQL execution time from `query_activity()` as proxy for trace data |
| Log pipeline silent drop for WAL suspension errors | `wal_tables()` shows `suspended=true`; table data stale; no alert fires | WAL suspension events logged to QuestDB log but Fluentd buffer overflow drops the ERROR lines | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT name,suspended,suspendedReason FROM wal_tables() WHERE suspended=true"` | Add cron-based monitoring script polling `wal_tables()` for suspended tables; alert via Prometheus gauge updated by the script |
| Alert rule misconfiguration for ILP write rate drop | ILP ingest stops but alert never fires; alert rule uses `rate(questdb_ilp_received_rows_total[1m])` but QuestDB restarts reset counter | Rate calculation crosses zero at restart; alert computes 0 which matches normal state during low traffic | Use `increase()` instead of `rate()` with longer window: `increase(questdb_ilp_received_rows_total[5m]) == 0` combined with `up{job="questdb"} == 1` | Rewrite alert to detect absence of ingest when QuestDB is up: `rate(questdb_ilp_received_rows_total[5m]) == 0 and up{job="questdb"} == 1` |
| Cardinality explosion from per-symbol ILP metrics blinding Prometheus | Prometheus scrape of QuestDB metrics times out; `questdb_table_row_count` metric has millions of unique table labels | Dynamic table names created by ILP producers create unbounded label cardinality in Prometheus | `curl http://localhost:9000/metrics \| grep -c questdb_table_row_count` — if > 10000, cardinality problem | Add metric relabeling to drop or aggregate per-table metrics; enforce ILP producer table naming conventions to limit unique table names |
| Missing QuestDB WAL table health in monitoring | WAL tables silently suspended after schema error; data accumulates in WAL but never committed | No Prometheus metric exposes `wal_tables()` suspended state; only queryable via SQL API | Deploy synthetic monitor: script running `SELECT count() FROM wal_tables() WHERE suspended=true` and pushing metric to Prometheus pushgateway | Implement a dedicated QuestDB health exporter that polls `wal_tables()`, `query_activity()`, and `table_writer_state()` and exposes Prometheus metrics |
| Instrumentation gap in ILP write-to-query freshness path | Data written via ILP appears stale in dashboards by minutes but no metric measures commit lag | No standard metric for WAL apply lag; `writerTxn - sequencerTxn` gap only visible via SQL | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT name, sequencerTxn - writerTxn as lag FROM wal_tables() ORDER BY lag DESC"` | Create custom Prometheus metric from WAL lag SQL query; alert when `sequencerTxn - writerTxn > 1000` for any table |
| Alertmanager outage during QuestDB disk full incident | Disk alarm fires; all ILP writes blocked; on-call not paged | Alertmanager pod on same node as QuestDB; node disk pressure evicts Alertmanager pod before alert can be delivered | `curl http://prometheus:9090/api/v1/alertmanagers` — if empty, all AMs down; check `df -h /var/lib/questdb` directly | Deploy Alertmanager on a node separate from QuestDB storage nodes; configure dead-man's switch to external heartbeat service |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor QuestDB version upgrade rollback (e.g., 8.1 → 8.2) | New version fails to start; error reading WAL written by old version; ILP ingest stops | `journalctl -u questdb \| grep -E 'error\|Exception\|failed'`; check data dir compatibility: `ls -la /var/lib/questdb/db/` | Stop new version; restore old JAR: `cp /opt/questdb/questdb.jar.bak /opt/questdb/questdb.jar`; restart with same data directory | Test upgrade with production data copy in staging; backup data directory before upgrade: `tar czf /backup/questdb_$(date +%s).tar.gz /var/lib/questdb/`; read changelog |
| Major version upgrade rollback (e.g., 7.x → 8.x) | Table metadata format changed; old tables cannot be read by new version; `_meta` file incompatible | `grep -r 'metadata version\|incompatible' /var/log/questdb/questdb.log`; test: `curl -G http://localhost:9000/exec --data-urlencode "query=SHOW TABLES"` returns error | Stop new version; restore from snapshot taken before upgrade; restart old version | Take full snapshot before major upgrade: `SNAPSHOT PREPARE` via SQL API; test new version against snapshot copy in isolated environment |
| Schema migration partial completion (column type change) | Column type changed from `SYMBOL` to `STRING`; old ILP producers fail to write; existing data intact | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT column, type FROM table_columns('<table>')"` to verify current schema | Re-add old column type with alias; cannot revert column type in-place; recreate table from backup if necessary | Test schema changes in staging with production-equivalent ILP traffic; use `ALTER TABLE <name> ADD COLUMN new_col STRING` to add alongside old column |
| Rolling upgrade version skew in multi-node QuestDB Enterprise | Primary and replica on different versions; replication protocol incompatible; replica falls out of sync | `questdb-cli replication status` (Enterprise); compare versions: `curl http://primary:9000/exec?query=SELECT+build` vs replica | Stop rolling upgrade; downgrade new node to match primary version; complete replication sync before resuming upgrade | Upgrade primary and replica atomically; test replication with mixed versions in Enterprise staging environment |
| Zero-downtime migration to partitioned table from unpartitioned | Migration script creates new partitioned table; backfill running; ILP still writing to old unpartitioned table; data in two places | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT count() FROM old_table"` vs `new_partitioned_table`; check backfill progress | Stop migration; continue writing to old table; complete backfill without live traffic | Use atomic cutover: pause ILP; backfill new table; point ILP to new table; resume; never dual-write to both tables simultaneously |
| Config format change in `server.conf` after upgrade | New QuestDB version introduces renamed configuration key; old key silently ignored; feature reverts to default | `grep 'Unknown configuration\|unrecognized' /var/log/questdb/questdb.log`; diff `/etc/questdb/conf/server.conf` against new version's reference config | Revert to old server.conf with only known-valid keys; update to new key names from release notes; restart QuestDB | Diff server.conf against new version's sample config before deployment; test with new binary in staging to detect unrecognized key warnings |
| Data format incompatibility after column index version change | Queries using indexed column filter return incorrect or empty results after upgrade | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT count() FROM <table> WHERE indexed_col = 'value'"` vs expected count | Rebuild index: `ALTER TABLE <name> DROP INDEX ON <col>`; then `ALTER TABLE <name> ALTER COLUMN <col> ADD INDEX`; verify results | Validate index query results against known test data after every upgrade; include indexed-column query in upgrade smoke test |
| Feature flag regression after enabling `cairo.parallel.indexing.enabled` | Parallel index build corrupts some symbol column indexes; queries return wrong rows for indexed lookups | `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT count() FROM <table> WHERE symbol_col = 'known-value'"` returns wrong count | Set `cairo.parallel.indexing.enabled=false` in server.conf; rebuild affected index; restart QuestDB | Test parallel indexing with production-scale data in staging before enabling; verify index correctness with full table scan after index rebuild |
| Dependency version conflict (QuestDB JDK version mismatch) | QuestDB fails to start after JDK upgrade; `UnsupportedClassVersionError` in startup log; JVM incompatibility | `java -version`; `grep 'UnsupportedClassVersionError\|JDK\|java.version' /var/log/questdb/questdb.log` | Downgrade JDK to supported version: check QuestDB docs for minimum JDK requirement (QuestDB 8.x requires JDK 11+); restart | Pin JDK version in deployment manifest; test JDK upgrade in staging before applying to QuestDB nodes; document JDK ↔ QuestDB version matrix |

## Kernel/OS & Host-Level Failure Patterns

| Failure | Symptom | Why It Hits QuestDB | Detection Command | Remediation |
|---------|---------|---------------------|-------------------|-------------|
| OOM killer targets QuestDB JVM process | QuestDB process disappears; ILP ingest stops; `dmesg` shows `oom-kill` for java PID | QuestDB uses off-heap memory for column storage and mmap; JVM RSS plus off-heap exceeds cgroup limit under heavy ingestion | `dmesg -T \| grep -i 'oom.*java'`; `journalctl -u questdb --since "10 min ago" \| grep -i killed`; `cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes` | Set `-Xmx` to 50% of container memory leaving room for off-heap; tune `cairo.sql.jit.mode=off` to reduce memory; increase pod memory limit; set `QDB_CAIRO_MAX_UNCOMMITTED_ROWS` to limit WAL buffer |
| Inode exhaustion on QuestDB data directory | QuestDB fails to create new partitions; ILP writes return errors; `Cannot create file` in logs | Each QuestDB partition creates per-column files (`.d`, `.i`, `.k`, `.v`); tables with many columns and fine-grained partitioning exhaust inodes | `df -i /var/lib/questdb/db/`; `find /var/lib/questdb/db/ -type f \| wc -l`; `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT count() FROM tables()"` | Drop old partitions: `ALTER TABLE <name> DROP PARTITION LIST '<date>'`; use `HOUR` or `DAY` partitioning instead of `SECOND`/`MINUTE`; reformat volume with more inodes or use XFS |
| CPU steal time causing query latency spikes | SQL queries that normally take <100ms intermittently take >2s; ILP ingestion throughput drops | QuestDB vectorized query engine and JIT-compiled SQL depend on sustained CPU; steal time interrupts tight computation loops | `cat /proc/stat \| awk '/^cpu / {print "steal:", $9}'`; `mpstat -P ALL 1 5`; `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT avg(query_duration) FROM query_activity()"` | Migrate to dedicated instance type; set CPU affinity: `taskset -cp 0-7 $(pgrep -f questdb)`; use `nodeSelector` for dedicated node pool in Kubernetes |
| NTP clock skew corrupting time-series ingestion | ILP data written with future/past timestamps; queries with `WHERE ts > now()` return unexpected results; partitions created for wrong time range | QuestDB partitions data by timestamp; if host clock drifts, ILP `server` timestamp mode uses wrong wall clock; out-of-order data triggers costly O3 merges | `chronyc tracking \| grep 'System time'`; `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT min(ts), max(ts), now() FROM <table> WHERE ts > dateadd('h', 1, now())"` | Sync NTP: `chronyc makestep`; use ILP with explicit timestamps from producer instead of `server` timestamp mode; alert on `node_timex_offset_seconds > 0.05` |
| File descriptor exhaustion | QuestDB rejects new ILP connections; HTTP API returns connection errors; log shows `Too many open files` | Each WAL table opens per-column FDs; ILP TCP connections hold FDs; QuestDB also opens FDs for parallel SQL readers | `ls -la /proc/$(pgrep -f questdb)/fd \| wc -l`; `cat /proc/$(pgrep -f questdb)/limits \| grep 'Max open files'`; `ss -tunap \| grep ':9009\|:9000\|:8812' \| wc -l` | Increase ulimit: `LimitNOFILE=1048576` in systemd unit; reduce `cairo.writer.fd.cache.size` if too many idle table writers; consolidate small tables |
| TCP conntrack table saturation from ILP producers | New ILP TCP connections on port 9009 fail with `nf_conntrack: table full`; existing connections unaffected | High-frequency ILP producers open/close TCP connections rapidly instead of using persistent connections; conntrack table fills | `dmesg \| grep 'nf_conntrack: table full'`; `cat /proc/sys/net/netfilter/nf_conntrack_count`; `ss -s \| grep 'TCP:'` | Increase conntrack max: `sysctl -w net.netfilter.nf_conntrack_max=524288`; configure ILP producers to use persistent TCP connections; enable ILP over HTTP (port 9000) which multiplexes |
| Transparent Huge Pages stalling QuestDB mmap operations | QuestDB query latency spikes correlated with `compact_stall` in vmstat; ILP ingestion pauses periodically | THP defragmentation stalls mmap calls when QuestDB opens column files; kernel compaction blocks the JVM thread | `cat /sys/kernel/mm/transparent_hugepage/enabled`; `grep -i 'compact_stall\|thp' /proc/vmstat`; `vmstat 1 5 \| awk '{print $15}'` | Disable THP: `echo never > /sys/kernel/mm/transparent_hugepage/enabled`; `echo never > /sys/kernel/mm/transparent_hugepage/defrag`; add to kernel boot params |
| NUMA imbalance causing asymmetric ILP throughput | ILP ingestion rate varies 2-3x between restart cycles; some worker threads consistently slower | QuestDB shared worker threads scheduled across NUMA nodes; cross-node memory access for column files adds latency to hot path | `numactl --hardware`; `numastat -p $(pgrep -f questdb)`; `perf stat -e node-loads,node-load-misses -p $(pgrep -f questdb) sleep 5` | Pin QuestDB to single NUMA node: `numactl --cpunodebind=0 --membind=0 java -jar questdb.jar`; set `shared.worker.count` to match cores on single NUMA node |

## Deployment Pipeline & GitOps Failure Patterns

| Failure | Symptom | Why It Hits QuestDB | Detection Command | Remediation |
|---------|---------|---------------------|-------------------|-------------|
| Image pull failure during QuestDB deployment | QuestDB pod stuck in `ImagePullBackOff`; ILP ingestion pipeline stops | Docker Hub rate limit hit when pulling `questdb/questdb:<tag>`; no pull secret for private registry | `kubectl describe pod <questdb-pod> \| grep -A3 'Events'`; `kubectl get events -n questdb --field-selector reason=Failed \| grep pull` | Mirror image to private registry: `docker pull questdb/questdb:latest && docker tag ... && docker push`; add `imagePullSecrets` to deployment |
| Helm drift between Git and live QuestDB config | QuestDB running with `cairo.max.uncommitted.rows=500000` from manual edit but Helm values say `100000`; next upgrade reverts to 100000; ILP ingestion slows | Operator manually tuned QuestDB for production load; change not in Git | `helm diff upgrade questdb questdb/questdb -n questdb -f values.yaml`; `curl -G http://localhost:9000/exec --data-urlencode "query=SHOW PARAMETERS" \| grep max.uncommitted` | Commit production tuning to values.yaml; run `helm upgrade` to reconcile; add ArgoCD drift detection |
| ArgoCD sync stuck on QuestDB StatefulSet | ArgoCD shows `OutOfSync`; QuestDB pods not updated; running old version with known bug | StatefulSet `volumeClaimTemplates` immutable field changed in Git; ArgoCD cannot apply the diff | `argocd app get questdb-app --show-operation`; `argocd app diff questdb-app`; `kubectl get statefulset questdb -n questdb -o yaml` | Add `ignoreDifferences` for `volumeClaimTemplates` in ArgoCD app; for PVC resize, create new StatefulSet; migrate data with `SNAPSHOT` |
| PodDisruptionBudget blocking QuestDB rolling restart | QuestDB config change requires restart but PDB blocks eviction; stale config running indefinitely | PDB `minAvailable: 1` on single-instance QuestDB; cannot evict the only pod | `kubectl get pdb -n questdb -o yaml \| grep -E 'disruptionsAllowed\|currentHealthy'`; `kubectl rollout status statefulset/questdb` | Temporarily delete PDB: `kubectl delete pdb questdb-pdb -n questdb`; restart pod; recreate PDB; for production, run QuestDB Enterprise with replicas |
| Blue-green cutover failure during QuestDB migration | Green QuestDB instance has empty tables; traffic switched; dashboards show no data; Grafana alerts fire | Blue-green script switched load balancer before data migration completed; ILP producers not redirected | `curl -G http://questdb-green:9000/exec --data-urlencode "query=SELECT count() FROM <table>"` — returns 0 | Gate cutover on data validation: verify `count()` matches between blue and green for all tables; redirect ILP producers first; wait for WAL apply before switching query traffic |
| ConfigMap drift causing QuestDB server.conf mismatch | QuestDB using stale `server.conf` with old `cairo.commit.lag` value; WAL commit performance degraded | ConfigMap updated in Git but pod not restarted; QuestDB reads config only at startup | `kubectl get configmap questdb-config -n questdb -o yaml \| grep cairo.commit.lag`; compare with runtime: `curl -G http://localhost:9000/exec --data-urlencode "query=SHOW PARAMETERS" \| grep commit.lag` | Add ConfigMap hash annotation to StatefulSet: `checksum/config: {{ sha256sum }}`; use Reloader to auto-restart on ConfigMap change |
| Secret rotation breaking QuestDB PostgreSQL wire protocol auth | PostgreSQL wire protocol connections (port 8812) fail with auth error after Secret rotation; Grafana dashboards break | Kubernetes Secret updated with new `pg.password` but QuestDB not restarted; QuestDB caches password from config at startup | `psql -h questdb -p 8812 -U admin -c "SELECT 1"` — auth failure; `kubectl get secret questdb-pg-secret -o jsonpath='{.data.password}' \| base64 -d` | Restart QuestDB after Secret rotation: `kubectl rollout restart statefulset/questdb`; use stakater Reloader for auto-restart on Secret change |
| CronJob backup using SNAPSHOT fails silently | `SNAPSHOT PREPARE` succeeds but snapshot files not copied to S3; no backup for days; DR drill fails | CronJob runs `curl` to trigger snapshot but S3 upload step fails; no error checking in script; CronJob shows `Complete` | `kubectl get cronjob questdb-backup -n questdb`; `kubectl logs job/questdb-backup-<ts>`; `curl -G http://localhost:9000/exec --data-urlencode "query=SNAPSHOT PREPARE" \| jq '.ddl'` | Add error checking to backup script: verify S3 upload with `aws s3 ls`; alert on CronJob failure; validate backup by restoring to staging monthly |

## Service Mesh & API Gateway Edge Cases

| Failure | Symptom | Why It Hits QuestDB | Detection Command | Remediation |
|---------|---------|---------------------|-------------------|-------------|
| Envoy circuit breaker blocking ILP TCP connections | ILP producers get connection refused through mesh; direct connection to port 9009 works; Envoy shows `upstream_cx_overflow` | Burst ILP ingestion opens hundreds of TCP connections simultaneously; Envoy default `max_connections: 1024` hit | `kubectl exec <sidecar> -- curl http://localhost:15000/stats \| grep questdb \| grep cx_overflow`; `curl http://questdb:9000/exec?query=SELECT+1` — works directly | Increase circuit breaker: `DestinationRule` with `connectionPool.tcp.maxConnections: 8192`; configure ILP producers to use persistent connections |
| Rate limiting blocking ILP burst ingestion | ILP producers receive 429 from API gateway; data loss during high-frequency market data ingestion | API gateway applies global rate limit to QuestDB ILP HTTP path (port 9000 `/write`); burst ingestion exceeds limit | `kubectl logs deploy/api-gateway \| grep -c '429.*questdb'`; check rate limit: `kubectl get configmap ratelimit-config -o yaml \| grep questdb` | Exempt ILP ingestion path from rate limiting; or increase per-route limit for `/write` endpoint; use ILP TCP (port 9009) which bypasses API gateway |
| Stale service discovery endpoint for QuestDB | Queries routed to terminated QuestDB pod; HTTP 503 errors; Grafana dashboards intermittently fail | QuestDB pod terminated during restart but Endpoints not yet updated; mesh sidecar caches stale endpoint | `kubectl get endpoints questdb -n questdb -o yaml`; `istioctl proxy-config endpoint <client-pod> \| grep questdb` | Add `terminationGracePeriodSeconds: 60`; configure `preStop` hook running `SNAPSHOT PREPARE` before shutdown; reduce Envoy EDS refresh interval |
| mTLS certificate rotation breaking PostgreSQL wire protocol | Grafana datasource using PostgreSQL wire protocol (port 8812) fails TLS handshake after cert rotation | cert-manager rotated mTLS certs but QuestDB PG wire protocol does not support dynamic TLS cert reload; requires restart | `psql "host=questdb port=8812 sslmode=require user=admin" -c "SELECT 1"` — TLS error; `kubectl logs <questdb-pod> -c istio-proxy \| grep tls` | Exclude PG wire protocol port 8812 from mTLS: annotate Service with `traffic.sidecar.istio.io/excludeInboundPorts: "8812"`; use password auth only for PG wire |
| Retry storm amplifying QuestDB query load | QuestDB CPU saturated; query queue full; HTTP API returns 503; upstream retries make it worse | Envoy retries SQL queries on 503; each retry adds to QuestDB's already-overloaded query queue; positive feedback loop | `kubectl exec <sidecar> -- curl http://localhost:15000/stats \| grep questdb \| grep retry`; `curl -G http://localhost:9000/exec --data-urlencode "query=SELECT count() FROM query_activity()"` | Set retry policy to `retryOn: connect-failure` only; disable retries for SQL query path; implement client-side query timeout: `statement.timeout=5000` in JDBC URL |
| gRPC keepalive conflict with QuestDB health probes | Mesh health probe via gRPC fails; pod marked unhealthy; QuestDB restarted unnecessarily | QuestDB does not expose gRPC health endpoint; mesh assumes gRPC health check on non-gRPC port; probe always fails | `kubectl describe pod <questdb-pod> \| grep -A5 'Liveness'`; check probe config for gRPC vs HTTP | Use HTTP health probe: `httpGet: { path: "/exec?query=SELECT+1", port: 9000 }`; exclude QuestDB from mesh gRPC health checks |
| Trace context lost in ILP ingestion pipeline | Distributed traces show gap between ILP producer and QuestDB; cannot correlate slow ingestion to source | ILP TCP protocol (port 9009) is a line protocol without HTTP headers; no mechanism to propagate W3C `traceparent` | `curl -H "traceparent: 00-<trace-id>-<span-id>-01" -G http://questdb:9000/exec --data-urlencode "query=SELECT 1"` — trace ID not in QuestDB logs | Use ILP over HTTP (port 9000 `/write`) which supports HTTP headers for trace propagation; correlate by timestamp in Jaeger for TCP ILP |
| WebSocket proxy timeout cutting long-running QuestDB queries | Long-running analytical SQL queries through API gateway timeout at 60s; query killed mid-execution | API gateway default WebSocket/HTTP timeout of 60s; QuestDB analytical queries on large partitions take >60s | `curl -G --max-time 120 http://questdb:9000/exec --data-urlencode "query=SELECT ... FROM large_table"` — times out through gateway, succeeds directly | Increase gateway timeout for QuestDB SQL path: `proxy_read_timeout 300s`; set QuestDB `query.timeout.sec=120` to bound queries server-side |
