---
name: hive-agent
description: >
  Apache Hive specialist. Handles HiveServer2, metastore operations,
  query optimization, LLAP, and data format management.
model: sonnet
color: "#FDEE21"
skills:
  - hive/hive
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-hive-agent
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

You are the Hive Agent — the SQL-on-Hadoop data warehouse expert. When
alerts involve HiveServer2 failures, metastore issues, query performance,
LLAP problems, or data format issues, you are dispatched.

# Activation Triggers

- Alert tags contain `hive`, `hiveserver2`, `metastore`, `llap`
- HiveServer2 or metastore unresponsive
- Query failure rate increase
- Long-running query alerts
- LLAP daemon issues
- Metastore latency degradation

---

## Key Metrics Reference

Hive exposes metrics via JMX (port 10002 on HS2 WebUI), a JSON metrics file
(`hive.server2.metrics.file.location`), or an HTTP endpoint when the
`CodahaleMetrics` reporter is enabled. LLAP metrics are additionally available
via the LLAP WebUI (port 15002) and YARN.

Enable Codahale / JMX reporter in `hive-site.xml`:
```xml
<property>
  <name>hive.server2.metrics.enabled</name><value>true</value>
</property>
<property>
  <name>hive.server2.metrics.reporter</name><value>JMX,JSON</value>
</property>
```

### HiveServer2 Session / Connection Metrics

| Metric Name (JMX / JSON key) | Type | Description | Alert Threshold |
|---|---|---|---|
| `open_connections` | Gauge | Active JDBC/ODBC connections to HS2 | > `hive.server2.max.connections` × 0.85 → WARNING |
| `open_sessions` | Gauge | Open HiveServer2 sessions | > max_sessions × 0.85 → WARNING |
| `abandoned_connections` | Counter | Connections abandoned without explicit close | Growing → connection leak |
| `hs2_submitted_queries` | Counter | Total queries submitted to HS2 | Basis for failure rate |
| `hs2_failed_queries` | Counter | Total queries that failed | >0; rate > 10% → CRITICAL |
| `hs2_succeeded_queries` | Counter | Total queries that succeeded | Compare with submitted |
| `active_calls_api_hs2_operation_GET_OPERATION_STATUS` | Gauge | Concurrent status-poll calls | Spike → client polling storm |

### Query Execution Metrics

| Metric Name (JMX / JSON key) | Type | Description | Alert Threshold |
|---|---|---|---|
| `query_execution_time` (p50/p99) | Histogram (ms) | Wall-clock time per query | p99 > 300 000 ms → WARNING; p99 > 600 000 ms → CRITICAL |
| `execution_time_p99` | Gauge (ms) | 99th percentile query execution time | >300 000 ms → investigate |
| `waiting_compile_ops` | Gauge | Queries waiting for a compile thread | >5 → compile thread pool exhausted |
| `compiling_queries` | Gauge | Queries currently in compilation phase | Spike → plan cache cold / complex queries |
| `running_queries` | Gauge | Queries currently executing | Monitor vs LLAP/YARN capacity |
| `queued_queries` | Gauge | Queries queued for resources | >20 → LLAP/YARN queue saturation |

### Metastore (HMS) Metrics

| Metric Name (JMX / JSON key) | Type | Description | Alert Threshold |
|---|---|---|---|
| `api_get_all_tables` (p99) | Histogram (ms) | HMS `get_all_tables` API latency | p99 > 500 ms → metastore overloaded |
| `api_get_table` (p99) | Histogram (ms) | HMS `get_table` API latency | p99 > 200 ms → WARNING |
| `api_get_partitions` (p99) | Histogram (ms) | HMS `get_partitions` API latency | p99 > 1000 ms → large partition scan |
| `api_get_database` (p99) | Histogram (ms) | HMS `get_database` API latency | p99 > 100 ms → WARNING |
| `open_connections` (HMS) | Gauge | Open Thrift connections to HMS | > max_connections × 0.80 → WARNING |
| `directsql_errors` | Counter | Errors in HMS direct-SQL path | >0 → possible schema mismatch |

### LLAP Daemon Metrics (via LLAP WebUI port 15002 / JMX)

| Metric Name | Type | Description | Alert Threshold |
|---|---|---|---|
| `llap.cache.total_used_bytes` | Gauge (bytes) | LLAP in-memory cache in use | Monitor vs allocated cache size |
| `llap.cache.hit_ratio` | Gauge (0–1) | Cache hit rate | < 0.50 → cache too small or cold |
| `llap.cache.request_count` | Counter | Total cache lookups | Basis for hit ratio |
| `llap.executor.running_tasks` | Gauge | LLAP tasks currently executing | Monitor vs executor thread count |
| `llap.executor.queued_tasks` | Gauge | LLAP tasks queued for executor threads | >10 → executor saturation |
| `llap.executor.total_memory_bytes` | Gauge | LLAP executor memory allocation | Compare with YARN container size |
| `llap.jvm.heap.used` | Gauge (bytes) | LLAP JVM heap in use | >80% of Xmx → WARNING; >95% → CRITICAL |
| `llap.jvm.gc.time_ms` | Counter (ms) | Cumulative GC time in LLAP daemons | Rate > 10% of wall time → GC pressure |
| `llap.io.encoded_bytes_read` | Counter | Encoded bytes read from ORC/Parquet | Monitor for scan amplification |
| `llap.io.cache_bytes_read` | Counter | Bytes served from LLAP cache (vs disk) | cache_bytes / total_bytes = effective hit rate |

### JVM Metrics (HS2 and HMS JVM — via JMX `java.lang:type=Memory`)

| JMX Attribute | Type | Description | Alert Threshold |
|---|---|---|---|
| `java.lang:type=Memory HeapMemoryUsage.used` | Gauge | HS2 heap used (bytes) | >80% of committed → WARNING |
| `java.lang:type=Memory HeapMemoryUsage.max` | Gauge | HS2 max heap (bytes) | Reference |
| `java.lang:type=GarbageCollector,name=G1 Old Generation CollectionTime` | Counter (ms) | Old-gen GC time | Rate > 5 s/min → heap pressure |
| `java.lang:type=GarbageCollector,name=G1 Old Generation CollectionCount` | Counter | Old-gen GC frequency | >1/min → WARNING |
| `java.lang:type=Threading ThreadCount` | Gauge | HS2 live thread count | >1000 → thread leak |

### YARN / TEZ Metrics (for Hive-on-Tez queries)

| Metric | Source | Description | Alert Threshold |
|---|---|---|---|
| YARN queue pending containers | YARN RM REST | Containers waiting for resources | >50 → queue capacity issue |
| Tez AM heartbeat interval | Tez history | Tez AM health | Missing > 2 min → AM dead |
| DAG completion time | Tez history | Time for a Tez DAG to finish | p99 regression > 2× → investigate |

---

## PromQL Expressions (if Codahale metrics scraped via prometheus-jmx-exporter)

```promql
# HS2 query failure rate above 10%
rate(hive_hs2_failed_queries_total[5m])
  / rate(hive_hs2_submitted_queries_total[5m]) > 0.10

# Query execution time p99 above 5 minutes
histogram_quantile(0.99,
  rate(hive_query_execution_time_bucket[10m])) > 300000

# LLAP cache hit ratio below 50%
hive_llap_cache_hit_ratio < 0.50

# HS2 heap utilisation above 85%
(jvm_memory_heap_used / jvm_memory_heap_max) > 0.85

# Open sessions near limit (fill in max_sessions)
hive_open_sessions / <max_sessions> > 0.85

# Metastore get_partitions p99 above 1 second
histogram_quantile(0.99,
  rate(hive_api_get_partitions_bucket[5m])) > 1000

# Queued LLAP tasks accumulating
hive_llap_executor_queued_tasks > 10
```

---

## Cluster Visibility

```bash
# HiveServer2 connectivity test
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SELECT 1"

# Active sessions and connections
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW SESSIONS"

# Running and queued queries
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW QUERIES"

# Metastore health check via HMS Thrift
hive --service metatool -listFSRoot

# LLAP daemon status (blocking poll)
hive --service llapstatus -w

# YARN-based LLAP app status
yarn application -list | grep llap

# HS2 WebUI metrics endpoint (JSON)
curl -s http://<hs2-host>:10002/jmx | python3 -m json.tool | grep -E "(open_sessions|hs2_failed|running_queries)"

# HMS WebUI JMX (port 9083 is Thrift; HMS web UI/JMX is on 9084 by default)
curl -s http://<hms-host>:9084/jmx | python3 -m json.tool

# Table and partition statistics
beeline -u "jdbc:hive2://<hs2-host>:10000" \
  -e "ANALYZE TABLE <db>.<table> COMPUTE STATISTICS FOR COLUMNS"

# Web UI key pages
# HiveServer2 WebUI:   http://<hs2-host>:10002/
# LLAP WebUI:          http://<llap-host>:15002/
# HMS WebUI:           http://<hms-host>:9084/ (if enabled; 9083 is Thrift)
```

---

## Global Diagnosis Protocol

**Step 1: Infrastructure health**
```bash
# HS2 process alive
pgrep -a java | grep HiveServer2
# Metastore backend DB connectivity (MySQL/PostgreSQL)
mysql -u hive -p -h <metastore-db-host> -e "SELECT 1" hive_metastore
# or PostgreSQL:
psql -h <metastore-db-host> -U hive -c "SELECT 1" hive_metastore
# LLAP app in YARN
yarn application -list -appStates RUNNING | grep llap
# Check HS2 log for FATAL errors
tail -100 /var/log/hive/hiveserver2.log | grep -E "(FATAL|ERROR|Exception)"
```

**Step 2: Job/workload health**
```bash
# Running queries in HS2
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW QUERIES" 2>/dev/null
# YARN jobs submitted by Hive/Tez
yarn application -list -appStates RUNNING | grep -i hive
# Long-running (> 30 min) queries
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW QUERIES" 2>/dev/null | \
  awk -F'\t' 'NR>2 && $7!="" && $7+0 > 1800 {print $0}'
```

**Step 3: Resource utilization**
```bash
# LLAP memory and executor count
hive --service llapstatus 2>/dev/null | python3 -m json.tool
# Metastore DB connection pool
mysql -u hive -h <db-host> -e "SHOW STATUS LIKE 'Threads_connected'" hive_metastore
# Heap usage of HS2 JVM
jmap -heap $(pgrep -f HiveServer2) 2>/dev/null | grep -E "(used|capacity|Heap)" | head -10
# HS2 JMX metrics snapshot
curl -s http://<hs2-host>:10002/jmx | python3 -c "
import sys, json
beans = json.load(sys.stdin)['beans']
for b in beans:
    if 'hs2' in b.get('name','').lower():
        print(b.get('name'), json.dumps({k:v for k,v in b.items() if k not in ('name','modelerType')}, indent=2)[:300])
"
```

**Step 4: Data pipeline health**
```bash
# Stale table statistics (affects query planning)
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW TABLE EXTENDED IN <db> LIKE '*'"
# Pending compactions (ACID tables)
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW COMPACTIONS"
# Source data freshness
beeline -u "jdbc:hive2://<hs2-host>:10000" \
  -e "DESCRIBE FORMATTED <db>.<table>" | grep transient_lastDdlTime
```

**Severity:**
- CRITICAL: HS2 down, metastore DB unreachable, LLAP cluster gone, `hs2_failed_queries` rate > 50%, JVM heap > 95%
- WARNING: query p99 > 5 min, `open_sessions` > 85% of max, metastore `api_get_partitions` p99 > 1 s, pending compactions > 100, LLAP cache hit < 50%
- OK: HS2 responsive, LLAP cache hit > 70%, queries complete under SLA, GC < 5% of time

---

## Diagnostic Scenario 1: Query Failure / Execution Error

**Symptom:** `hs2_failed_queries` counter rising; users reporting query errors; `SHOW QUERIES` shows FAILED states.

**Step 1 — Identify failing queries and get YARN app IDs:**
```bash
# From HS2 log
grep -E "(ERROR|Exception|FAILED)" /var/log/hive/hiveserver2.log | tail -50
# From YARN — hive/Tez applications
yarn application -list -appStates FAILED -appTypes TEZ | head -20
```

**Step 2 — Fetch YARN application logs for the failed query:**
```bash
yarn logs -applicationId application_<id> 2>/dev/null | grep -E "(ERROR|Exception|FAILED)" | head -50
# Verbose explain plan for the failing query
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "EXPLAIN EXTENDED <failing-query>" 2>&1
```

**Step 3 — Check for missing partitions or corrupt metadata:**
```bash
# Repair partition metadata from HDFS
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "MSCK REPAIR TABLE <db>.<table>"
# Verify HDFS path is accessible
hdfs dfs -ls hdfs://<warehouse-path>/<db>.db/<table>/
# Check for data format mismatch (e.g., wrong SerDe)
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "DESCRIBE FORMATTED <db>.<table>"
```

---

## Diagnostic Scenario 2: Metastore Lock Contention (ACID Tables)

**Symptom:** Queries or compaction tasks hang; `SHOW TRANSACTIONS` shows long-running open transactions; `api_get_partitions` latency spikes.

**Step 1 — View open transactions and locks:**
```bash
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW TRANSACTIONS"
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW LOCKS <db>.<table>"
# Direct DB query for lock table
mysql -u hive -h <db-host> hive_metastore -e \
  "SELECT HL_LOCK_INT_ID, HL_TXNID, HL_DB, HL_TABLE, HL_PARTITION, HL_LOCK_STATE, HL_LOCK_TYPE, HL_LAST_HEARTBEAT FROM HIVE_LOCKS ORDER BY HL_LAST_HEARTBEAT LIMIT 20"
```

**Step 2 — Identify and abort stale transactions:**
```bash
# Transactions older than 1 hour with no recent heartbeat are stale
mysql -u hive -h <db-host> hive_metastore -e \
  "SELECT TXN_ID, TXN_STATE, TXN_STARTED, TXN_LAST_HEARTBEAT FROM TXNS WHERE TXN_STATE='o' ORDER BY TXN_STARTED LIMIT 10"
# Abort specific stale transaction
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "ABORT TRANSACTIONS <txn-id>"
```

**Step 3 — Prevent recurrence:**
```bash
# Tune heartbeat interval and lock timeout
# hive.txn.timeout = 300 (seconds, default 300)
# hive.heartbeat.interval = 60 (seconds)
# Ensure the compaction service is running (prevents lock buildup)
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW COMPACTIONS"
# Trigger manual compaction if backed up
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "ALTER TABLE <db>.<table> COMPACT 'MAJOR'"
```

---

## Diagnostic Scenario 3: LLAP Daemon Failure / Low Cache Hit Rate

**Symptom:** LLAP app not running or restarting; `llap.cache.hit_ratio` < 0.50; queries falling back to Tez containers.

**Step 1 — Check LLAP daemon health:**
```bash
hive --service llapstatus -w -i 5
# YARN app status
yarn application -list | grep llap
# LLAP daemon log
yarn logs -applicationId <llap-app-id> 2>/dev/null | grep -E "(ERROR|FATAL|Exception|OOM)" | tail -50
```

**Step 2 — Diagnose cache miss causes:**
```bash
# Check LLAP cache size vs data being queried
curl -s http://<llap-host>:15002/metrics | python3 -m json.tool | grep -E "(cache|hit|miss)"
# If cache hit < 50%: cache is too small for working set
# Calculate working set: sum of frequently queried partition sizes
beeline -u "jdbc:hive2://<hs2-host>:10000" -e \
  "SELECT SUM(CAST(PARAM_VALUE AS UNSIGNED)) AS total_bytes FROM PARTITION_PARAMS WHERE PARAM_KEY='totalSize'"
```

**Step 3 — Recovery and tuning:**
```bash
# Kill stuck LLAP app and allow YARN to re-launch
yarn application -kill $(yarn application -list | grep llap | awk '{print $1}')
# Verify restart
watch -n5 "yarn application -list | grep llap"

# Increase LLAP cache size in llap-daemon-site.xml
# hive.llap.io.memory.size = 80g  (aim for 70-80% of LLAP container memory)
# hive.llap.daemon.memory.per.instance.mb = 102400  (100 GB)

# Enable LLAP I/O for ORC and Parquet
# hive.llap.io.enabled = true
# hive.llap.io.encode.formats = orc,parquet
```

---

## Diagnostic Scenario 4: HiveServer2 Heap Pressure / GC Storms

**Symptom:** HS2 JVM heap > 85%; `G1 Old Generation CollectionTime` rate rising; queries timing out with `GC overhead limit exceeded`.

**Step 1 — Measure heap and GC:**
```bash
# JVM heap snapshot
jmap -heap $(pgrep -f HiveServer2) 2>/dev/null | grep -E "(used|capacity|G1)"
# GC log analysis
grep -E "(Full GC|GC overhead|G1 Evacuation)" /var/log/hive/hiveserver2.log | tail -20
# JMX heap metrics
curl -s http://<hs2-host>:10002/jmx | python3 -c "
import sys, json
for b in json.load(sys.stdin)['beans']:
    if b.get('name','').startswith('java.lang:type=Memory'):
        heap = b.get('HeapMemoryUsage',{})
        print('Heap used:', heap.get('used'), '/ max:', heap.get('max'),
              '| pct:', round(heap.get('used',0)*100/max(heap.get('max',1),1), 1))
"
```

**Step 2 — Identify memory consumers:**
```bash
# Open sessions and their query states (each session holds compiled plans)
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW SESSIONS" 2>/dev/null
# Large result set caching (disable if not needed)
# hive.server2.thrift.resultset.serialize.in.tasks = false

# Heap dump for deep analysis (only on non-production or after killing traffic)
jmap -dump:format=b,file=/tmp/hs2-heap.hprof $(pgrep -f HiveServer2)
```

**Step 3 — Remediation:**
```bash
# Increase HS2 heap (hive-env.sh or service config)
# export HADOOP_HEAPSIZE=16384  (16 GB)
# export HIVE_SERVER2_HEAPSIZE=16384

# Limit concurrent sessions to reduce memory pressure
# hive.server2.max.start.attempts = 5
# hive.server2.connection.user.limit = 50

# Enable query result caching to avoid redundant execution (reduces heap pressure from large scans)
# hive.query.results.cache.enabled = true
# hive.query.results.cache.max.size = 2147483648  (2 GB)
```

---

## Diagnostic Scenario 5: Metastore DB Connection Pool Exhaustion

**Symptom:** HMS becomes unresponsive; all Hive queries fail with `Unable to open a test connection to the given database`; `open_connections (HMS)` metric near max; `api_get_table` p99 spikes to > 10 s.

**Root Cause Decision Tree:**
- Too many HMS Thrift clients (HS2 instances, Spark apps, Trino workers) → connection pool exhausted
- Long-running HMS transactions blocking pool slots → compaction or lock operations holding connections
- HMS backend DB (MySQL/PostgreSQL) max_connections limit hit → DB refuses new connections from pool
- HMS process restart mid-operation → orphaned in-flight connections counted against pool until timeout

**Diagnosis:**
```bash
# HMS open connections metric (HMS web UI/JMX is on port 9084; 9083 is Thrift)
curl -s "http://<hms-host>:9084/jmx" | python3 -c "
import sys, json
beans = json.load(sys.stdin).get('beans', [])
for b in beans:
    if 'connections' in str(b.get('name', '')).lower():
        print(b.get('name'), {k:v for k,v in b.items() if 'connect' in k.lower()})
"
# MySQL: connection count vs max_connections
mysql -u hive -h <db-host> hive_metastore -e \
  "SHOW STATUS LIKE 'Threads_connected'; SHOW VARIABLES LIKE 'max_connections';"
# PostgreSQL equivalent:
# psql -h <db-host> -U hive -c "SELECT count(*) FROM pg_stat_activity WHERE datname='hive_metastore';" hive_metastore

# HS2 clients connected to HMS
curl -s "http://<hs2-host>:10002/jmx" | python3 -c "
import sys, json
for b in json.load(sys.stdin).get('beans', []):
    if 'open_connections' in str(b):
        print('HS2 open_connections:', b.get('open_connections'))
"
# Test HMS response time
time beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW DATABASES" 2>&1 | tail -5
```

**Thresholds:**
- WARNING: HMS connections > 80% of `javax.jdo.option.ConnectionPoolSize`
- CRITICAL: HMS refusing new connections; all Hive queries failing; DB `Threads_connected` = `max_connections`

## Diagnostic Scenario 6: ORC/Parquet Schema Evolution Causing Read Error

**Symptom:** Queries on a table fail with `Column type mismatch` or `Unable to read ORC footer`; recently added columns return NULL for all rows; some partitions return data while others fail.

**Root Cause Decision Tree:**
- Column added to HMS schema but old partitions written before the addition → new column not in old ORC files; reads NULL (expected behavior, not a bug)
- Column type changed in HMS schema (e.g., INT → BIGINT) without file rewrite → ORC/Parquet type mismatch on read
- SerDe class changed for table without data migration → files written with old SerDe cannot be read with new
- Parquet schema from Spark writer differs from HMS schema field order → field mapping by position fails

**Diagnosis:**
```bash
# Current table schema from HMS
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "DESCRIBE FORMATTED <db>.<table>"

# ORC file schema (compare with HMS schema)
hive --orcfiledump hdfs://<warehouse>/<db>.db/<table>/<partition>/<file>.orc 2>/dev/null | \
  grep -A 50 "Type:"

# Parquet file schema
ssh <hadoop-node> "parquet-tools schema hdfs://<warehouse>/<db>.db/<table>/<partition>/<file>.parquet 2>/dev/null | head -30"

# Check if different partitions have different schemas
beeline -u "jdbc:hive2://<hs2-host>:10000" -e \
  "DESCRIBE FORMATTED <db>.<table> PARTITION (dt='2026-01-01')"

# Recent DDL changes from HMS audit log or HDFS audit
grep -i "ALTER TABLE\|CHANGE COLUMN\|ADD COLUMNS" /var/log/hive/hiveserver2.log | tail -20
```

**Thresholds:**
- WARNING: any partition returning NULL for non-nullable columns after a schema change
- CRITICAL: queries failing with schema mismatch error on > 10% of partitions

## Diagnostic Scenario 7: LLAP Daemon OOM on Large Query

**Symptom:** LLAP daemon crashes with `OutOfMemoryError`; `llap.jvm.heap.used` at 100% of Xmx; queries fall back to Tez containers; `llap.executor.queued_tasks` spikes before crash.

**Root Cause Decision Tree:**
- Large aggregation or join materializing more data than LLAP heap can hold → executor memory exhausted
- LLAP cache too large relative to executor memory → cache eviction pressure and GC combined cause OOM
- Too many concurrent LLAP tasks → cumulative execution memory exceeds heap
- Memory leak in LLAP IO layer → ORC/Parquet decode buffers not released between tasks

**Diagnosis:**
```bash
# LLAP daemon JVM heap
curl -s "http://<llap-host>:15002/jmx" | python3 -c "
import sys, json
for b in json.load(sys.stdin).get('beans', []):
    if b.get('name', '').startswith('java.lang:type=Memory'):
        heap = b.get('HeapMemoryUsage', {})
        print('LLAP heap used:', heap.get('used'), '/ max:', heap.get('max'),
              '| pct:', round(heap.get('used',0)*100/max(heap.get('max',1),1), 1))
"
# LLAP metrics
curl -s "http://<llap-host>:15002/metrics" | python3 -m json.tool | \
  grep -E "(heap|jvm|executor|cache|memory)"

# YARN LLAP log for OOM
yarn logs -applicationId <llap-app-id> 2>/dev/null | \
  grep -E "(OutOfMemoryError|GC overhead|killed|OOM)" | tail -30

# Current LLAP executor and cache allocation
hive --service llapstatus 2>/dev/null | python3 -c "
import sys, json
status = json.load(sys.stdin)
for daemon in status.get('runningInstances', []):
    print('Daemon:', daemon.get('hostname'),
          'memory MB:', daemon.get('memoryPerInstance'),
          'executors:', daemon.get('numExecutors'))
"
```

**Thresholds:**
- WARNING: `llap.jvm.heap.used` > 80% of Xmx for > 5 minutes
- CRITICAL: LLAP daemon OOM crash; `llap.jvm.heap.used` > 95%

## Diagnostic Scenario 8: Tez Application Master OOM from Large DAG

**Symptom:** Tez AM crashes with OOM; YARN shows application in FAILED state with AM failure reason; `hs2_failed_queries` rises; query re-submit triggers new AM that also OOMs.

**Root Cause Decision Tree:**
- Complex query produces very large Tez DAG (hundreds of vertices) → DAG object graph exceeds AM heap
- Large number of partitions per vertex → AM holds partition metadata for all in-flight tasks
- AM heap too small for the query complexity → `--hiveconf tez.am.resource.memory.mb` not scaled with query
- Multiple large concurrent queries share same AM session → cumulative AM memory pressure

**Diagnosis:**
```bash
# YARN application status and failure reason
yarn application -status application_<id> 2>/dev/null | grep -E "(State|Diagnostics|AM)"
# AM container logs
yarn logs -applicationId application_<id> -log_files syslog 2>/dev/null | \
  grep -E "(OutOfMemoryError|AM Container|DAG|vertices|GC)" | tail -40

# Query plan complexity before submission
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "EXPLAIN <failing-query>" 2>&1 | \
  grep -cE "(STAGE|Reducer|Map)"

# Tez AM memory configuration
beeline -u "jdbc:hive2://<hs2-host>:10000" -e \
  "SET tez.am.resource.memory.mb" 2>&1
# Default is 1024 MB; large DAGs may need 4096 MB

# Active Tez AM processes
yarn application -list -appTypes TEZ -appStates RUNNING | head -20
```

**Thresholds:**
- WARNING: Tez AM restarted > 2 times for same query
- CRITICAL: Tez AM fails max restart attempts; YARN application killed

## Diagnostic Scenario 9: Stale Statistics Causing Bad Query Plan

**Symptom:** Hive query choosing full table scan instead of partition pruning; using wrong join order; query 10× slower than baseline after data load; `EXPLAIN` shows wrong row estimates.

**Root Cause Decision Tree:**
- Statistics not refreshed after large INSERT/LOAD DATA → optimizer uses 0 or stale row counts
- `hive.stats.autogather=true` but task-side stats too small (sampling) → estimates off for skewed data
- ACID table compaction cleared stats → post-compaction table has no statistics
- Dynamic partition insert did not update all partition statistics → only newly written partitions have stats

**Diagnosis:**
```bash
# Check table-level statistics
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "DESCRIBE FORMATTED <db>.<table>" 2>&1 | \
  grep -E "(numRows|numFiles|totalSize|rawDataSize|COLUMN_STATS)"

# Check per-column statistics
beeline -u "jdbc:hive2://<hs2-host>:10000" -e \
  "DESCRIBE FORMATTED <db>.<table> <column_name>" 2>&1

# Query plan — check estimated vs actual rows
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "EXPLAIN EXTENDED <slow-query>" 2>&1 | \
  grep -E "(Statistics|rows|cardinality)"

# Tables with missing statistics
mysql -u hive -h <db-host> hive_metastore -e \
  "SELECT t.TBL_NAME, p.PARAM_KEY, p.PARAM_VALUE
   FROM TABLE_PARAMS p JOIN TBLS t ON p.TBL_ID=t.TBL_ID
   WHERE p.PARAM_KEY='numRows' AND CAST(p.PARAM_VALUE AS SIGNED) < 0
   LIMIT 20"
```

**Thresholds:**
- WARNING: table `numRows` = -1 or 0 in HMS while table clearly has data
- CRITICAL: wrong join order chosen causing cross-join or full scan on large fact table

## Diagnostic Scenario 10: HiveServer2 Session Leak from Aborted Clients

**Symptom:** `open_sessions` metric rising but `running_queries` near zero; HS2 heap pressure from idle session objects; HS2 eventually OOMs from accumulated session state; `abandoned_connections` counter growing.

**Root Cause Decision Tree:**
- JDBC clients crash without calling `connection.close()` → session remains open on HS2
- Network proxy (load balancer) times out idle connections without FIN → HS2 sees connection as alive
- Client using HTTP transport mode with keepalive → session survives client restart
- HS2 `hive.server2.session.check.interval` too large → stale sessions not detected and reaped

**Diagnosis:**
```bash
# Current open sessions
curl -s "http://<hs2-host>:10002/jmx" | python3 -c "
import sys, json
for b in json.load(sys.stdin).get('beans', []):
    for k in ['open_sessions', 'open_connections', 'abandoned_connections']:
        if k in b:
            print(k + ':', b[k])
" 2>/dev/null | head -20

# Sessions from HS2 WebUI (text table)
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW SESSIONS" 2>/dev/null | head -30

# HS2 heap used by session objects
jmap -histo $(pgrep -f HiveServer2) 2>/dev/null | \
  grep -i "session\|connection\|hive" | sort -k2 -rn | head -10

# Session idle time — find sessions with no recent query
grep "SESSION_CREATED\|SESSION_CLOSED" /var/log/hive/hiveserver2-audit.log 2>/dev/null | \
  awk '{print $1, $2, $NF}' | tail -30
```

**Thresholds:**
- WARNING: `open_sessions` > 85% of `hive.server2.max.connections`
- CRITICAL: HS2 OOM from session accumulation; `open_sessions` not decreasing despite no active users

## Diagnostic Scenario 11: Compaction Not Running — Too Many Delta Files

**Symptom:** ACID table queries slow down progressively; `SHOW COMPACTIONS` shows many INITIATED compactions that never start; `SHOW TABLE EXTENDED` shows hundreds of delta directories; reads scan all delta files.

**Root Cause Decision Tree:**
- Compactor worker threads not running → `hive.compactor.worker.threads=0` or compactor service crashed
- Metastore DB lock contention blocking compaction → compaction initiation transaction waits forever
- YARN has no available resources for compaction containers → YARN queue full
- `hive.compactor.initiator.on=false` → no new compaction requests generated automatically

**Diagnosis:**
```bash
# Show pending and running compactions
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW COMPACTIONS" 2>/dev/null | \
  awk 'NR==1 || $5 ~ /initiated|working|ready/' | head -30

# Count delta files per partition (large number = compaction needed)
hdfs dfs -ls hdfs://<warehouse>/<db>.db/<table>/<partition>/ 2>/dev/null | \
  grep -c "delta_\|delete_delta_"

# Check compactor configuration
beeline -u "jdbc:hive2://<hs2-host>:10000" \
  -e "SET hive.compactor.initiator.on; SET hive.compactor.worker.threads" 2>&1

# HS2 log for compaction errors
grep -i "compactor\|Compaction\|compact" /var/log/hive/hiveserver2.log | \
  grep -E "(ERROR|WARN|Exception)" | tail -20

# YARN compaction containers (Tez or MR2)
yarn application -list -appStates RUNNING | grep -i compact
```

**Thresholds:**
- WARNING: > 50 delta files per partition; `SHOW COMPACTIONS` shows > 20 INITIATED entries
- CRITICAL: > 200 delta files per partition (reads scan all files; extreme slowdown)

## Diagnostic Scenario 12: ACID Table Write Conflict — Transaction Abort

**Symptom:** INSERT/UPDATE/DELETE operations fail with `TxnAbortedException` or `LockException`; `SHOW TRANSACTIONS` shows many concurrent transactions; `directsql_errors` counter rising; write throughput drops to zero.

**Root Cause Decision Tree:**
- Multiple concurrent writers to same partition → Hive ACID requires exclusive write lock; lock queue timeout
- Long-running reader holding shared lock → write lock cannot acquire; writers abort after `hive.txn.timeout`
- Transaction heartbeat failing (client crash) → transaction marked aborted; subsequent write on same txn fails
- Lock escalation storm → many small transactions locking individual rows trigger table-level lock contention

**Diagnosis:**
```bash
# Show all open transactions
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW TRANSACTIONS" 2>/dev/null

# Show locks on the affected table
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW LOCKS <db>.<table>" 2>/dev/null | \
  awk 'NR==1 || $6 ~ /WAITING|ACQUIRED/' | head -20

# Direct HMS DB query for lock state
mysql -u hive -h <db-host> hive_metastore -e \
  "SELECT HL_LOCK_INT_ID, HL_TXNID, HL_TABLE, HL_PARTITION,
          HL_LOCK_STATE, HL_LOCK_TYPE,
          TIMEDIFF(NOW(), FROM_UNIXTIME(HL_LAST_HEARTBEAT/1000)) AS heartbeat_age
   FROM HIVE_LOCKS
   WHERE HL_TABLE='<table>'
   ORDER BY HL_LAST_HEARTBEAT ASC
   LIMIT 20"

# Aborted transaction count in HMS
mysql -u hive -h <db-host> hive_metastore -e \
  "SELECT TXN_STATE, COUNT(*) FROM TXNS GROUP BY TXN_STATE"

# Recent write errors in HS2 log
grep -E "(LockException|TxnAborted|write conflict|aborted)" /var/log/hive/hiveserver2.log | tail -20
```

**Thresholds:**
- WARNING: write lock wait time > 30 seconds; > 5 concurrent write transactions on same table
- CRITICAL: `TxnAbortedException` rate > 10% of write operations; deadlock detected in HMS DB

## Data Skew in MapJoin / Reduce

```bash
# Show partition sizes to identify skew
beeline -u "jdbc:hive2://<hs2-host>:10000" -e \
  "SELECT p.PART_NAME, pp.PARAM_VALUE AS size_bytes
   FROM PARTITIONS p
   JOIN PARTITION_PARAMS pp ON p.PART_ID = pp.PART_ID
   WHERE pp.PARAM_KEY='totalSize'
   ORDER BY CAST(pp.PARAM_VALUE AS UNSIGNED) DESC
   LIMIT 10"
# Enable skew join optimization
# SET hive.optimize.skewjoin=true;
# SET hive.skewjoin.key=100000;
# SET hive.skewjoin.mapjoin.map.tasks=10000;
# SET hive.skewjoin.mapjoin.min.split=33554432;
```

---

## Diagnostic Scenario 13: Prod Ranger Column Masking Causing Silent ETL Data Corruption

**Symptom:** ETL jobs produce incorrect aggregates or downstream reports show masked placeholder values (e.g., `****`, `XXXX-XXXX`) instead of real data in prod; same queries return correct results in staging; no exceptions are thrown; issue surfaces only after a Ranger policy update or first run of a new service account.

**Root Cause Decision Tree:**
- Prod Hive uses Apache Ranger column masking policies (`MASK`, `MASK_HASH`, `NULLIFY`); staging does not → Ranger transforms column values transparently at the SerDe/row-filter level before the result reaches the client
- Service account used by ETL is subject to a masking policy targeting sensitive columns (e.g., PII fields); policy was added without notifying the ETL team
- ETL reads masked values and writes them downstream → corrupt data propagates silently through the pipeline because `****` is a valid string and passes all format checks
- Column masking applies per-user/group; manual ad-hoc queries by a privileged user return real data, masking the issue during investigation

**Diagnosis:**
```bash
# 1. Check Ranger masking policies for the table/column in question
# Ranger Admin UI: Hive → Masking Policies → filter by Database/Table/Column
# Or via REST API:
curl -s -u admin:<ranger-pass> \
  "http://<ranger-host>:6080/service/public/v2/api/policy?serviceName=<hive-service>&policyType=1" | \
  python3 -m json.tool | grep -E "name|isEnabled|maskType|users|groups|resources"

# 2. Run the same query as both the ETL service account and a DBA account; compare output
beeline -u "jdbc:hive2://<hs2>:10000" -n <etl-user> -p <pass> \
  -e "SELECT ssn, credit_card FROM pii_table LIMIT 5"

beeline -u "jdbc:hive2://<hs2>:10000" -n <dba-user> -p <pass> \
  -e "SELECT ssn, credit_card FROM pii_table LIMIT 5"
# If ETL sees **** while DBA sees real values → masking policy scoped to ETL user/role

# 3. Check Ranger audit log for MASK actions on the ETL user
curl -s "http://<ranger-host>:6083/solr/ranger_audits/select?q=ugi:<etl-user>+AND+action:MASK&rows=20&wt=json" | \
  python3 -m json.tool | grep -E "resource|action|result|ugi"

# 4. List masking policies affecting the service account's groups/roles
beeline -u "jdbc:hive2://<hs2>:10000" -n <dba-user> \
  -e "SHOW CURRENT ROLES"
# Cross-reference roles with Ranger masking policy subjects

# 5. Confirm the issue is not a Hive view with built-in masking logic
beeline -u "jdbc:hive2://<hs2>:10000" \
  -e "SHOW CREATE TABLE <db>.<table>" | grep -i "mask\|regexp_replace\|substr"
```

**Thresholds:**
- WARNING: Any Ranger MASK audit events for ETL service accounts writing to downstream tables
- CRITICAL: Masked values (`****`, hash strings, NULLs) detected in output tables that feed reporting or ML pipelines

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `FAILED: SemanticException [Error 10001]: Table not found xxx` | Table does not exist in metastore or wrong database selected | `beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW TABLES LIKE 'xxx'"` |
| `FAILED: Execution Error, return code 2 from org.apache.hadoop.hive.ql.exec.mr.MapRedTask` | MapReduce job failed on YARN (task-level error) | `yarn logs -applicationId <id>` |
| `MetaException: Got exception: org.apache.thrift.transport.TTransportException` | Hive Metastore Service unreachable or crashed | Check HMS process status and HMS logs |
| `FAILED: Error in semantic analysis: Cartesian products are disabled` | Cross-join query missing an explicit join condition | Set `hive.strict.checks.cartesian.product=false` or add join condition |
| `java.io.IOException: Failed to rename hdfs://xxx` | HDFS permissions issue or namespace quota exceeded during output commit | `hdfs dfs -ls -d <path>` |
| `com.mysql.jdbc.exceptions.jdbc4.CommunicationsException` | HMS cannot connect to its MySQL backing database | Check MySQL service health and HMS JDBC URL in `hive-site.xml` |
| `ERROR : Failed to execute tez graph` | Tez Application Master crash or resource contention on YARN | `yarn application -status <id>` |
| `Permission denied: user=xxx, access=WRITE` | HDFS ACL does not grant write permission to the Hive user | `hdfs dfs -getfacl <path>` |

---

# Capabilities

1. **HiveServer2 management** — HA configuration, session management, heap tuning
2. **Metastore operations** — Backend DB health, metadata repair, statistics
3. **Query optimization** — Execution plan analysis, join strategies, partitioning
4. **LLAP management** — Daemon health, cache tuning, executor configuration
5. **Data management** — ORC/Parquet optimization, compaction, small files
6. **ACID operations** — Transaction management, compaction scheduling

# Critical Metrics to Check First

1. `hs2_failed_queries` rate — above 10% = active incident
2. `open_sessions / max_sessions` — above 85% = connection saturation imminent
3. `llap.cache.hit_ratio` — below 50% = LLAP cache undersized or cold
4. `query_execution_time` p99 — regression > 2× baseline = investigate plan change
5. HMS `api_get_partitions` p99 — above 1 s = metastore overloaded
6. HS2 `HeapMemoryUsage.used / max` — above 80% = GC pressure / OOM risk

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| Hive query hanging with no progress | Metastore MySQL slow queries holding lock on `PARTITION_PARAMS` table | `mysql -u hive -h <db-host> hive_metastore -e "SHOW PROCESSLIST"` then check `/var/log/mysql/slow-query.log` |
| All Hive queries fail with `SemanticException: Table not found` | HDFS NameNode in safe mode — HMS cannot read warehouse directory listing | `hdfs dfsadmin -safemode get` |
| Tez jobs queuing but never launching | YARN ResourceManager queue capacity exhausted by competing Spark jobs | `yarn queue -status <hive-queue>` |
| ORC read returning wrong data silently | HMS-backed MySQL replication lag — replica returning stale table schema metadata | `mysql -u hive -h <replica-host> -e "SHOW SLAVE STATUS\G" | grep Seconds_Behind_Master` |
| HiveServer2 sessions dropping randomly | Load balancer idle timeout (e.g., AWS NLB 350s) killing long-running Beeline connections before Hive session timeout | Check NLB/ELB idle timeout settings vs `hive.server2.idle.session.timeout` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N HiveServer2 instances rejecting new sessions | One HS2 pod at `max_connections`; others have headroom; load balancer still routes to it | ~1/N of new client connections fail; existing sessions on healthy HS2 pods are unaffected | `for hs2 in hs2-1 hs2-2 hs2-3; do curl -s http://$hs2:10002/jmx | python3 -c "import sys,json; [print('$hs2 sessions:', b.get('open_sessions')) for b in json.load(sys.stdin)['beans'] if 'open_sessions' in b]"; done` |
| 1 Metastore instance with a stale DB connection pool | Queries routed to that HMS instance time out; others succeed; HMS HA setup | ~1/N of metadata calls fail; hard to see because most HMS clients retry | `curl -s http://<hms-1>:9084/jmx | python3 -m json.tool | grep -i connection` |
| 1 LLAP daemon with degraded cache | Cache hit ratio low on one daemon; others healthy; LLAP task scheduler distributes work | Queries landing on the degraded daemon are slower than baseline; average p99 rises slightly | `curl -s http://<llap-1>:15002/metrics | grep llap_cache_hit_ratio` for each daemon |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| HiveServer2 open sessions | > 200 | > 500 | `curl -s http://<hs2-host>:10002/jmx | python3 -m json.tool | grep open_sessions` |
| Active HiveServer2 connections (% of limit) | > 70% | > 90% | `curl -s http://<hs2-host>:10002/jmx | python3 -m json.tool | grep -E "open_sessions|total_count"` |
| Query compilation time p99 | > 5 s | > 30 s | `curl -s http://<hs2-host>:10002/jmx | python3 -m json.tool | grep compilationTime` |
| Hive Metastore connection pool wait time | > 100 ms | > 1 s | `curl -s http://<hms-host>:9084/jmx | python3 -m json.tool | grep -i "connection.*wait"` |
| YARN queue capacity used (Hive queue) | > 80% | > 95% | `yarn queue -status <hive-queue> | grep -E "Used Capacity|State"` |
| Tez DAG submission latency | > 10 s | > 60 s | `curl -s http://<tez-ui-host>:9999/tez-ui/rest/tez-app/<app-id>/dag?limit=10 | python3 -m json.tool | grep submittedTime` |
| Hive Metastore MySQL replication lag | > 30 s | > 120 s | `mysql -u hive -h <replica-host> -e "SHOW SLAVE STATUS\G" | grep Seconds_Behind_Master` |
| Failed queries rate (5-min window) | > 5% | > 20% | `curl -s http://<hs2-host>:10002/jmx | python3 -m json.tool | grep -E "failedQueries|totalQueries"` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| YARN queue capacity utilization | Queue used > 80% during business hours for > 3 consecutive days | Increase YARN queue capacity or add NodeManagers; enable queue preemption for high-priority workloads | 1–2 weeks |
| HiveServer2 active sessions (`beeline -e "SHOW CONNECTIONS"`) | Sessions > 80% of `hive.server2.thrift.max.worker.threads` | Increase `hive.server2.thrift.max.worker.threads`; deploy additional HiveServer2 instances behind a load balancer | 1 week |
| Hive Metastore DB connection pool (`SHOW STATUS LIKE 'Threads_connected'` on MySQL) | DB connections > 80% of `max_connections` | Increase `max_connections` on MySQL/PostgreSQL; tune HMS connection pool size in `hive-site.xml` | 3–5 days |
| Average query execution time trend (HiveServer2 metrics) | p95 query time growing > 20% week-over-week | Profile slow queries with `EXPLAIN`; add partitioning/bucketing; tune Tez container memory | 1–2 weeks |
| HDFS storage consumed by Hive warehouse | Warehouse > 70% of HDFS cluster capacity | Enable ORC/Parquet compression; archive/drop old partitions; add DataNodes | 3–4 weeks |
| Metastore object count (tables + partitions) | Partition count growing > 5 M total | Implement partition retention policies; upgrade HMS to version with partition stats caching | 1 month |
| Tez container memory per task vs. YARN node memory | Tez container footprint exceeding 60% of NodeManager memory | Reduce `tez.runtime.io.sort.mb`; increase NodeManager memory or reduce container concurrency | 1 week |
| Compaction queue depth (for ACID/transactional tables) | Delta files > 10 per partition | Tune `hive.compactor.worker.threads`; verify ACID compaction is running on schedule | 3–5 days |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check active HiveServer2 sessions and running queries
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW SESSIONS;" 2>/dev/null | head -30

# List currently running queries with their query ID and elapsed time
grep "Starting to launch local task" /var/log/hive/hiveserver2.log | tail -20

# Check HiveServer2 heap usage via JMX
curl -s "http://<hs2-host>:10002/jmx?qry=java.lang:type=Memory" | python3 -m json.tool | grep -E "HeapMemoryUsage"

# Show HMS (Hive Metastore) connection pool status
grep -E "getConnection|pool size|active connections|idle connections" /var/log/hive/hivemetastore.log | tail -20

# Count tables per database in the Metastore
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW DATABASES;" 2>/dev/null | grep -v "^+" | grep -v "database_name" | xargs -I{} beeline -u "jdbc:hive2://<hs2-host>:10000" -e "USE {}; SHOW TABLES;" 2>/dev/null | grep -c "tab_name"

# Check for failed or stalled Tez DAGs
grep -E "DAG failed|DAG killed|FAILED|AM exited" /var/log/hive/hiveserver2.log | tail -30

# Inspect LLAP daemon health (if LLAP is enabled)
hive --service llapdump --hosts <llap-host> 2>/dev/null | grep -E "status|alive|memory"

# Check for long-running queries exceeding 10 minutes
grep "query.id=" /var/log/hive/hiveserver2.log | awk -F'elapsed=' '$2+0 > 600 {print}' | tail -20

# Verify Hive Metastore connectivity from HiveServer2 host
beeline -u "jdbc:hive2://<hs2-host>:10000" -e "SHOW DATABASES;" 2>&1 | grep -E "ERROR|Connected|rows selected"

# Check HDFS warehouse directory permissions and recent modifications
hdfs dfs -ls -R /user/hive/warehouse 2>/dev/null | awk '{print $1,$2,$3,$4,$5,$8}' | sort -k6 -r | head -20
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| HiveServer2 query success rate | 99% | `1 - (rate(hive_hs2_failed_queries_total[5m]) / rate(hive_hs2_submitted_queries_total[5m]))` | 7.3 hr | > 2x burn rate |
| Hive Metastore availability | 99.9% | `up{job="hive-metastore"} == 1` — minutes HMS is unreachable count against budget | 43.8 min | > 14.4x burn rate |
| P95 query execution time ≤ 60s (interactive queries) | 99.5% | `histogram_quantile(0.95, rate(hive_hs2_query_execution_time_bucket[5m])) < 60` | 3.6 hr | > 6x burn rate |
| Metastore connection pool exhaustion-free rate | 99.5% | `1 - (rate(hive_metastore_connection_pool_timeout_total[5m]) / rate(hive_metastore_connection_requests_total[5m]))` | 3.6 hr | > 6x burn rate |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Authentication (Kerberos / LDAP on HS2) | `grep -E 'hive.server2.authentication\|hive.metastore.sasl.enabled' /etc/hive/conf/hive-site.xml` | HS2 authentication set to `KERBEROS` or `LDAP`; not `NONE` in production; Metastore SASL enabled |
| TLS on HiveServer2 (SSL transport) | `grep 'hive.server2.use.SSL' /etc/hive/conf/hive-site.xml` | `true`; keystore path and password configured; certificate valid > 30 days |
| Resource limits (YARN queue and container memory) | `grep -E 'hive.tez.container.size\|hive.tez.java.opts\|tez.am.resource.memory.mb' /etc/hive/conf/hive-site.xml` | Container memory ≤ YARN node memory × 0.8; dedicated queue with capacity limits so Hive cannot starve other workloads |
| Metastore backend DB retention and backups | Verify DB backup cron: `crontab -l -u hive` or backup tool logs | Metastore DB backed up at least daily; last backup < 25 hours old; retention ≥ 30 days |
| Metastore HA / replication | `grep 'hive.metastore.uris' /etc/hive/conf/hive-site.xml` | At least 2 Metastore URIs listed for HA; not a single SPOF endpoint |
| Authorization (Ranger / Sentry / SQL std) | `grep -E 'hive.security.authorization\|hive.authorization.manager' /etc/hive/conf/hive-site.xml` | `SQLStdHiveAuthorizationValidator` or Ranger plugin enabled; `hive.security.authorization.enabled=true` |
| Access controls (table/database grants) | `beeline -u jdbc:hive2://localhost:10000 -e "SHOW GRANT USER public;"` | No overly broad grants to `public` on sensitive databases; `DROP TABLE` restricted to owners/admins |
| Network exposure (Thrift and web UI ports) | `ss -tlnp \| grep -E '10000\|10002\|9083'` | HS2 Thrift (10000) and Metastore (9083) not exposed to public internet; web UI (10002) restricted to ops network |
| Scratch directory permissions | `hdfs dfs -ls /tmp/hive` | Scratch dirs have sticky bit set (`1777`); not world-writable without sticky bit |
| Query execution engine and hook configuration | `grep -E 'hive.execution.engine\|hive.exec.post.hooks' /etc/hive/conf/hive-site.xml` | Engine set to `tez` or `spark` (not deprecated `mr`); audit/lineage hooks configured for compliance |
| HiveServer2 web UI exposed (port 10002) | External port scan detects 10002 open; unexpected query history visible; `curl -s http://<hs2-host>:10002/queries` returns data | Restrict port at firewall; enable SPNEGO auth: set `hive.server2.webui.use.spnego=true` in `hive-site.xml` and restart HiveServer2 | `ss -tnp \| grep 10002` ; `grep "GET /queries\|GET /sessions" /var/log/hive/hiveserver2.log \| awk '{print $1,$2,$4}' \| tail -100` |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `FAILED: Execution Error, return code 2 from org.apache.hadoop.hive.ql.exec.mr.MapRedTask` | Critical | MapReduce task failed during Hive query execution; often a data or resource issue | Check YARN application logs (`yarn logs -applicationId <appId>`); look for OOM or data skew |
| `WARN org.apache.hadoop.hive.ql.exec.tez.TezJobMonitor: Status: Failed VertexStatus{id=vertex_<id>, state=FAILED}` | Critical | Tez DAG vertex failed; query execution aborted | Check Tez AM logs in YARN UI; diagnose OOM, data skew, or connector failure |
| `MetaException(message: Got exception: org.apache.thrift.transport.TTransportException)` | Critical | HiveServer2 cannot connect to Hive Metastore | Verify Metastore service is running; check `hive.metastore.uris`; review network connectivity |
| `WARN HiveServer2: Session with sessionHandle SessionHandle [...]  has been idle for 1800 seconds, closing` | Info | Idle session timeout; connection pool entry released | Expected behavior; adjust `hive.server2.idle.session.timeout` if clients reconnect too frequently |
| `ERROR ql.Driver: FAILED: SemanticException [Error 10025]: Line N:M Table not found` | Warning | Query references a non-existent table or wrong database context | Verify table name and `USE <database>` context; confirm table was not dropped; check Metastore for table registration |
| `WARN TaskRunner: Error while reading from task logdir` | Warning | YARN container log directory not accessible after task completion | Usually cosmetic; check YARN log aggregation settings; no query impact |
| `ERROR exec.FileSinkOperator: java.io.IOException: No space left on device` | Critical | Local or HDFS scratch space full during query output writing | Free space on scratch directory; check `hive.exec.scratchdir` quota; expand HDFS capacity |
| `WARN ql.exec.Utilities: Only 1 of 3 attempts succeeded for stage <X>` | Warning | Retry logic triggered for a query stage; performance degraded | Investigate why first attempts failed (check YARN logs for container kill reasons) |
| `HiveLockException: Error acquiring locks: Lock wait timeout exceeded` | Warning | Another query holds a lock on the same table/partition preventing this query | Identify lock holder with `SHOW LOCKS`; kill blocking query if appropriate; consider using transaction-based tables with MVCC |
| `ERROR metastore.ObjectStore: An unexpected exception was thrown in retries for ensureConnectivity` | Critical | Metastore backend database (MySQL/PostgreSQL) is unreachable or connection pool exhausted | Check Metastore DB health and connection count; restart Metastore if DB recovered; check `datanucleus.connectionPool.maxPoolSize` |
| `WARN exec.GroupByOperator: Memory usage of 1.2 GB exceeds threshold of 1.0 GB, spilling to disk` | Warning | Group-by operator spilling to disk due to memory pressure | Increase `hive.auto.convert.join.noconditionaltask.size`; tune container memory; address data skew in GROUP BY key |
| `FAILED: Error in semantic analysis: NoViableAltException` | Warning | HiveQL syntax error in submitted query | Review query syntax; check for Hive version-specific keywords; validate with `EXPLAIN <query>` |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `Error 10025: Table not found` | Query references table that does not exist in Metastore | Query fails immediately | Verify table name and database; check if Metastore sync with HDFS is needed (`MSCK REPAIR TABLE`) |
| `Error 10070: LOAD DATA: Source file system is not compatible with destination` | Source data path incompatible with target table's file system | LOAD DATA command fails | Ensure source path is on same HDFS cluster; use `INSERT OVERWRITE` for cross-cluster loads |
| `Error 20000: Error while processing statement: FAILED: Execution Error, return code 2` | Generic execution failure from MapReduce/Tez task | Query execution fails; partial output may exist in scratch dir | Review YARN task logs for specific sub-cause (OOM, disk full, data skew) |
| `Error 30041: Insufficient privileges` | User does not have required SQL standard or Ranger permission | Query blocked at authorization layer | Grant appropriate privileges via Ranger or `GRANT SELECT ON <table> TO USER <user>` |
| `HiveSQLException: Error while compiling statement: FAILED: ParseException line N:M` | HiveQL parse error | Query rejected before execution starts | Fix query syntax; check for unsupported functions in target Hive version |
| `LockException: Error acquiring locks` | Table/partition lock held by concurrent query | Query blocked or fails after timeout | `SHOW LOCKS`; kill blocking query; use MVCC (ACID tables) to avoid lock contention |
| `OutOfMemoryError: Java heap space` (in HS2 or container) | JVM heap exhausted during query processing | Query fails; may affect other concurrent queries on same HS2/container | Increase `hive.tez.container.size` memory; tune `hive.tez.java.opts` Xmx; split large queries |
| `MetaException: Unable to open a transaction to the metastore` | Metastore DB connection failure | All DDL and many DML operations fail | Restore Metastore DB connectivity; restart Metastore service; check DB connection pool |
| `FAILED: NullPointerException` (at compile time) | Hive internal bug or unexpected null in query plan | Specific query fails | Check Hive version known issues; simplify query; add explicit NULL handling; file bug if reproducible |
| `Error in opening the connection: javax.jdo.JDODataStoreException` | Metastore JDO persistence manager cannot connect to DB | Metastore operations fail; HiveServer2 may lose Metastore connection | Check DB host/port/credentials in `hive-site.xml`; verify DB is accepting connections |
| `FAILED: RuntimeException: Cannot make directory` (scratch dir) | HDFS scratch directory creation failed for query | Query cannot start execution | Check HDFS permissions on `hive.exec.scratchdir`; verify HDFS is healthy; check quota |
| `MSCK REPAIR: Partitions not found` | Partitions exist on HDFS but not registered in Metastore | Partition data invisible to Hive queries | Run `MSCK REPAIR TABLE <table>` to synchronize HDFS partitions to Metastore |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Tez DAG Vertex OOM | YARN container kill events; Tez AM reporting failed vertices; query duration spike | `VertexStatus FAILED`; `OutOfMemoryError: Java heap space` in container logs | Alert: "Hive query failure rate > 20%" | Container memory insufficient for data volume; data skew causing single reducer overload | Increase `hive.tez.container.size`; enable map-join for small tables; add salting for skewed GROUP BY keys |
| Metastore Connection Pool Exhaustion | Metastore response time rising; HS2 DDL queue growing | `MetaException: Unable to open a transaction`; `connection pool exhausted` | Alert: "Hive DDL latency > 30s" | Too many concurrent Hive clients overwhelming Metastore DB connection pool | Increase `datanucleus.connectionPool.maxPoolSize`; add Metastore HA instance; rate-limit client connections |
| Lock Contention Deadlock | Long-running queries in `SHOW LOCKS`; downstream ETL jobs delayed | `HiveLockException: Error acquiring locks`; growing lock wait queue | Alert: "Hive lock wait queue > 10 jobs" | Multiple write jobs competing for the same table/partition without ACID; long-running queries not releasing locks | Migrate hot tables to ACID/ORC with MVCC; implement optimistic concurrency; add query timeout |
| Partition Metadata Explosion | Metastore response time for `SHOW PARTITIONS` increasing; partition count in millions | `Slow query`; `MetaException: too many partitions` in Metastore log | Alert: "Metastore response P99 > 5s" | Table partitioned at too fine a granularity (e.g., per-hour per-user); Metastore DB table too large | Re-partition at coarser granularity; archive old partitions; optimize Metastore DB indexes |
| HDFS Scratch Dir Quota Exhaustion | HDFS space quota alert for `/tmp/hive`; queries failing mid-execution | `IOException: No space left on device`; `Cannot make directory` in scratch path | Alert: "HDFS /tmp/hive quota > 90%" | Large queries not cleaning up scratch directories; failed queries leaving temp data | Implement `hive.exec.scratchdir` cleanup cron; increase quota; add post-query cleanup hook |
| HiveServer2 JVM GC Pressure | HS2 heap utilization > 80%; query submission latency rising; thread pool starvation | `WARN: GC overhead limit exceeded` in HS2 logs; connection timeout from JDBC clients | Alert: "HS2 heap > 85% for > 5 min" | Too many concurrent sessions; large query plan compilation consuming heap; memory leak | Restart HS2; reduce `hive.server2.thrift.max.worker.threads`; increase HS2 heap; add HS2 HA instance |
| Partition Discovery Stale — Data Invisible to Queries | New data on HDFS not appearing in query results despite successful writes | No errors; `SELECT COUNT(*)` returns lower-than-expected count | Alert: "ETL pipeline data validation check failed" | New partitions not registered in Metastore after dynamic partition write without `MSCK REPAIR` | Run `MSCK REPAIR TABLE <table>` or enable `hive.msck.repair.batch.size` in scheduled maintenance |
| HiveServer2 Authentication Failure Storm | Multiple clients suddenly failing to authenticate | `ERROR HiveServer2: Error during SASL negotiation`; Kerberos ticket expiry | Alert: "HS2 authentication error rate > 5%" | Kerberos keytab expired for `hive` service principal; KDC unreachable | Renew keytab; restart HS2; verify KDC connectivity from HS2 host |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `org.apache.hive.jdbc.HiveConnection: Could not open client transport` | JDBC / Beeline | HiveServer2 (HS2) process down or Thrift port not accepting connections | `nc -zv <hs2-host> 10000`; `systemctl status hiveserver2` | Restart HiveServer2; check HS2 logs for OOM or crash reason |
| `MetaException: Could not connect to meta store` | HiveQL / Spark SQL | Hive Metastore down or DB connection pool exhausted | `nc -zv <metastore-host> 9083`; Metastore logs for `connection pool exhausted` | Restart Metastore; increase `datanucleus.connectionPool.maxPoolSize` |
| `org.apache.hadoop.security.AccessControlException` in Hive | Beeline / HiveQL | HDFS permissions on warehouse directory deny the Hive user | `hdfs dfs -ls -la /warehouse/<table path>` | `hdfs dfs -chmod -R 755 /warehouse/<dir>`; verify Hive service account ownership |
| `SemanticException: Table not found` | HiveQL / Spark SQL | Table does not exist in Metastore or wrong database context | `SHOW TABLES IN <db> LIKE '<name>'` in Hive shell | Create table; verify database with `USE <db>` |
| `TezTask failed: GC overhead limit exceeded` | Hive on Tez | Container JVM heap too small for data volume; data skew causing single task OOM | YARN logs for container: `yarn logs -applicationId <app>`; check task memory usage | Increase `hive.tez.container.size`; enable auto-reduce parallelism; add salting for skewed keys |
| `FAILED: LockException Error acquiring locks` | HiveQL DDL/DML | Table or partition lock held by another query or dead session | `SHOW LOCKS <table> EXTENDED` | Kill blocking query: `KILL QUERY '<id>'`; release locks with `UNLOCK TABLE <table>` |
| `SemanticException: Cartesian product is disabled` | HiveQL | Query produces Cartesian product; `hive.strict.checks.cartesian.product=true` | Review query for missing JOIN ON clause or cross join | Add proper JOIN condition; or temporarily set `hive.strict.checks.cartesian.product=false` |
| `IOException: No space left on device` for scratch dir | HiveQL / Beeline | HDFS `/tmp/hive` scratch directory quota exceeded | `hdfs dfs -count -q /tmp/hive` | Clean scratch: `hdfs dfs -rm -r /tmp/hive/<old_sessions>`; increase HDFS quota for scratch |
| `org.apache.thrift.transport.TTransportException: Connection reset by peer` | JDBC / Beeline | HS2 closed idle connection; or HS2 GC pause killed connection | HS2 logs for GC or `SessionTimeout` events | Implement JDBC connection keep-alive; reconnect on `TTransportException` |
| `AnalysisException: Partition column ... not found` | Spark SQL via HMS | Partition column mismatch between DataFrame schema and Metastore table definition | `DESCRIBE FORMATTED <table>` — compare partition columns | Align DataFrame partitioning with Metastore table; use `MSCK REPAIR TABLE` |
| `java.sql.SQLException: Query returned non-zero code: 2` | JDBC | Generic Hive execution failure — usually visible in HiveQL log | `yarn logs -applicationId <id>` for actual error | Investigate Tez/MR container logs; check HDFS availability and disk space |
| `OperationHandle FAILED: Session is closed` | Beeline / JDBC | HS2 session expired or HS2 restarted mid-query | HS2 logs for session management events; JDBC reconnect logic | Implement retry + re-connection on session loss; increase `hive.server2.idle.session.timeout` |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Metastore DB table growth slowing partition listing | `SHOW PARTITIONS` latency increasing from sub-second to seconds over weeks | `SELECT COUNT(*) FROM PARTITIONS` in Metastore DB; compare to previous week | Weeks | Archive old partitions; optimize Metastore DB indexes on `PARTITIONS` table; consider partition pruning at table level |
| HS2 session leak | HS2 heap increasing; `hive.server2.active.operation.count` metric climbing; idle sessions accumulating | Hive HS2 JMX: `activeSessions`; HS2 logs for sessions older than `hive.server2.idle.session.timeout` | Days to weeks | Restart HS2 in maintenance window; reduce session timeout; audit long-running JDBC connections |
| Tez AM memory pressure from query plan growth | Tez AM heap rising; query plan compilation time increasing for complex queries | Tez AM logs: `GC overhead`; measure query plan compilation time via Hive explain output size | Weeks | Increase Tez AM memory: `tez.am.resource.memory.mb`; simplify query plans; use intermediate views |
| HDFS scratch directory fill | `/tmp/hive` directory growing unchecked; quota approaching limit | `hdfs dfs -du -h /tmp/hive \| sort -rh \| head -20` | Days | Implement automated scratch cleanup cron; enforce session-level cleanup in HS2 hooks |
| Compaction backlog for ACID tables | Insert/update latency on ACID tables rising; delta file count per partition growing | `SHOW COMPACTIONS` in Hive — check `CompactionState = INITIATED` backlog | Days | Trigger manual compaction: `ALTER TABLE <t> PARTITION (...) COMPACT 'MAJOR'`; tune `hive.compactor.initiator.on` |
| Lock wait queue depth growth | Average query start delay increasing; `SHOW LOCKS` shows growing pending queue | `SHOW LOCKS` count trend; query execution logs for time between submission and start | Hours to days | Reduce long-running write queries holding locks; implement query timeout; migrate to MVCC ACID tables |
| Metastore connection pool exhaustion under load | DDL operations intermittently failing during peak ETL hours | Metastore logs: `connection pool exhausted` frequency; Metastore DB `SHOW PROCESSLIST` | Days | Increase `datanucleus.connectionPool.maxPoolSize`; add Metastore HA instance; rate-limit client connections |
| Statistics staleness degrading query plans | Query performance regressing week-over-week despite data not growing; bad join order in explain plans | `DESCRIBE FORMATTED <table>` — check `numRows` and `ANALYZE` timestamp; compare to actual `COUNT(*)` | Weeks | Schedule `ANALYZE TABLE ... COMPUTE STATISTICS` after major data loads; enable auto-stats: `hive.stats.autogather=true` |
| YARN queue capacity erosion for Hive | Hive jobs taking longer to acquire containers as other teams' jobs grow | YARN UI: Hive queue usage over time; `yarn queue -status <hive-queue>` | Weeks | Review YARN capacity scheduler config; enforce queue capacity limits; add preemption for Hive jobs |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Collects: HS2 status, Metastore connectivity, YARN queue health, scratch dir usage, active locks
set -euo pipefail
HS2_HOST="${HS2_HOST:-localhost}"
HS2_PORT="${HS2_PORT:-10000}"
HMS_HOST="${HMS_HOST:-localhost}"
HMS_PORT="${HMS_PORT:-9083}"
HADOOP_HOME="${HADOOP_HOME:-/opt/hadoop}"
HIVE_HOME="${HIVE_HOME:-/opt/hive}"

echo "=== HiveServer2 Connectivity ==="
nc -zv "$HS2_HOST" "$HS2_PORT" 2>&1 && echo "HS2 port $HS2_PORT: OPEN" || echo "HS2 port $HS2_PORT: UNREACHABLE"

echo ""
echo "=== Metastore Connectivity ==="
nc -zv "$HMS_HOST" "$HMS_PORT" 2>&1 && echo "HMS port $HMS_PORT: OPEN" || echo "HMS port $HMS_PORT: UNREACHABLE"

echo ""
echo "=== HS2 JVM Memory ==="
HS2_PID=$(pgrep -f "HiveServer2" | head -1)
if [[ -n "$HS2_PID" ]]; then
  jstat -gcutil "$HS2_PID" 1 1
  awk '/VmRSS|VmPeak/{print}' /proc/$HS2_PID/status
else
  echo "HS2 process not found on this host"
fi

echo ""
echo "=== HDFS Scratch Directory Usage ==="
"$HADOOP_HOME/bin/hdfs" dfs -du -h /tmp/hive 2>/dev/null | sort -rh | head -10

echo ""
echo "=== YARN Hive Queue Status ==="
yarn queue -status "${HIVE_YARN_QUEUE:-hive}" 2>/dev/null || yarn queue -status default 2>/dev/null || echo "YARN queue check failed"

echo ""
echo "=== Active Locks (via Beeline) ==="
"$HIVE_HOME/bin/beeline" -u "jdbc:hive2://$HS2_HOST:$HS2_PORT/" -e "SHOW LOCKS;" 2>/dev/null | head -30 || echo "Beeline connection failed"

echo ""
echo "=== Metastore DB Connection Check ==="
echo "SELECT COUNT(*) FROM PARTITIONS;" | "$HIVE_HOME/bin/hive" --service metatool 2>/dev/null || echo "Metastore tool unavailable"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Triage: slow queries, Tez container failures, lock contention, Metastore latency
HIVE_HOME="${HIVE_HOME:-/opt/hive}"
HS2_HOST="${HS2_HOST:-localhost}"
HS2_PORT="${HS2_PORT:-10000}"
HIVE_LOG_DIR="${HIVE_LOG_DIR:-/var/log/hive}"

echo "=== Recent Query Failures (HS2 log, last 30 min) ==="
find "$HIVE_LOG_DIR" -name "hiveserver2.log" -mmin -30 -exec \
  grep -h "FAILED\|ERROR\|Exception" {} \; 2>/dev/null | grep -v "WARN" | tail -30

echo ""
echo "=== Currently Running Queries ==="
"$HIVE_HOME/bin/beeline" -u "jdbc:hive2://$HS2_HOST:$HS2_PORT/" \
  -e "SELECT query_id, start_time, query FROM sys.queries ORDER BY start_time DESC LIMIT 10;" 2>/dev/null || \
  echo "(sys.queries not available; check Hive Hook or Tez UI)"

echo ""
echo "=== Lock Contention ==="
"$HIVE_HOME/bin/beeline" -u "jdbc:hive2://$HS2_HOST:$HS2_PORT/" \
  -e "SHOW LOCKS;" 2>/dev/null | awk 'NF>0' | head -20

echo ""
echo "=== Tez Application Failures (last hour) ==="
yarn application -list -appStates FAILED -appTypes TEZ 2>/dev/null | head -20

echo ""
echo "=== Top 5 Largest HDFS Scratch Dirs ==="
hadoop fs -du -h /tmp/hive 2>/dev/null | sort -rh | head -5

echo ""
echo "=== Metastore DB Slow Query Check ==="
echo "Check Metastore DB slow query log for queries > 1s on PARTITIONS, TABLE_PARAMS tables"
# MySQL example:
# mysql -u hive -p<pw> metastore -e "SELECT * FROM information_schema.processlist WHERE TIME > 1 ORDER BY TIME DESC;"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Audits: HS2 active sessions, Metastore connections, ACID compaction queue, YARN container limits
HIVE_HOME="${HIVE_HOME:-/opt/hive}"
HS2_HOST="${HS2_HOST:-localhost}"
HS2_PORT="${HS2_PORT:-10000}"

echo "=== HS2 Active Sessions ==="
HS2_PID=$(pgrep -f "HiveServer2" | head -1)
if [[ -n "$HS2_PID" ]]; then
  THREAD_COUNT=$(ls /proc/$HS2_PID/task 2>/dev/null | wc -l)
  echo "HS2 PID: $HS2_PID | Threads: $THREAD_COUNT"
  echo "Client connections to HS2 port 10000:"
  ss -tn state established dport = :10000 2>/dev/null | wc -l
fi

echo ""
echo "=== ACID Compaction Queue ==="
"$HIVE_HOME/bin/beeline" -u "jdbc:hive2://$HS2_HOST:$HS2_PORT/" \
  -e "SHOW COMPACTIONS;" 2>/dev/null | grep -E "INITIATED|WORKING|READY" | head -20

echo ""
echo "=== Metastore DB Connection Pool (DataNucleus) ==="
HMS_PID=$(pgrep -f "HiveMetaStore" | head -1)
if [[ -n "$HMS_PID" ]]; then
  echo "HMS PID: $HMS_PID"
  ss -tn state established sport = :9083 2>/dev/null | wc -l | xargs -I{} echo "Client connections to HMS: {}"
fi

echo ""
echo "=== YARN Container Limits for Hive Queue ==="
yarn queue -status "${HIVE_YARN_QUEUE:-hive}" 2>/dev/null | grep -E "capacity|maxCapacity|usedCapacity|numContainers"

echo ""
echo "=== Table Statistics Freshness ==="
"$HIVE_HOME/bin/beeline" -u "jdbc:hive2://$HS2_HOST:$HS2_PORT/" -e "
SELECT t.TBL_NAME, p.PARAM_VALUE AS num_rows, p.PARAM_KEY
FROM TBLS t JOIN TABLE_PARAMS p ON t.TBL_ID = p.TBL_ID
WHERE p.PARAM_KEY = 'numRows'
ORDER BY p.PARAM_VALUE::bigint DESC LIMIT 10;" 2>/dev/null || echo "(Direct Metastore DB query required)"

echo ""
echo "=== HDFS Warehouse Quota Status ==="
hadoop fs -count -q /warehouse 2>/dev/null | awk '{printf "Quota: %s Used: %s Remaining: %s\n", $1, $5, ($1-$5)}'
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Runaway full-table scan exhausting YARN containers | Other Hive jobs queued; YARN queue capacity 100% utilized by one job | YARN UI: sort jobs by container count; `yarn application -list \| grep RUNNING` | Kill offending job: `yarn application -kill <app_id>`; add `hive.exec.max.dynamic.partitions` limit | Enforce row-count guardrails in query planner; require WHERE clause via `hive.strict.checks` |
| Lock storm from concurrent ETL writes | Multiple ETL jobs blocked at lock acquisition; pipeline SLA breached | `SHOW LOCKS` — count of `WAITING` locks; identify blocking query via `SHOW LOCKS EXTENDED` | Kill blocking long-running write query; implement write serialization in orchestrator | Use ACID MVCC tables; separate write windows for different ETL jobs; use Hive `TBLPROPERTIES('transactional'='true')` |
| Metastore connection pool exhausted during mass job launch | DDL operations (ADD PARTITION, MSCK REPAIR) failing at start of ETL window | Metastore logs: `connection pool exhausted`; Metastore DB: `SHOW PROCESSLIST \| grep Sleep \| wc -l` | Stagger job launches; reduce `MSCK REPAIR` frequency; batch DDL operations | Increase pool size; set `datanucleus.connectionPool.maxPoolSize`; use Hive Metastore HA |
| Scratch directory shared between users causing quota collision | One user's failed jobs leaving large temp files; other users' jobs fail with `QuotaExceededException` | `hdfs dfs -du -h /tmp/hive \| sort -rh \| head` — identify large directories by username | Clean up: `hdfs dfs -rm -r /tmp/hive/<user>/<session>`; set per-user HDFS quota on `/tmp/hive/<user>` | Automate post-job cleanup via Hive hooks; set `hive.exec.local.scratchdir` per user in Ranger policies |
| Small-file generating job degrading NameNode | Subsequent Hive queries on the same table take longer to plan; NameNode GC pauses increase | `hdfs dfs -count /warehouse/<table>`— file count; NN JMX heap trend | Merge small files: `INSERT OVERWRITE ... SELECT * FROM`; use `CONCATENATE` for ORC | Set `hive.merge.mapfiles=true`; enforce file size via output format settings in ETL job config |
| Tez shuffle service network saturation | Reduce-phase queries slow for all jobs; Tez shuffle service logs show high connection count | `netstat -an \| grep 13562 \| wc -l` (Tez shuffle port); identify large-shuffle jobs by DAG size in Tez UI | Limit shuffle parallelism: `tez.shuffle.max.threads`; reduce concurrent large-shuffle jobs | Implement YARN queue preemption; separate shuffle-heavy jobs to dedicated queue |
| ACID compaction blocking writes | INSERT/UPDATE latency spikes during compaction window; delta file count high before compaction | `SHOW COMPACTIONS` — find `WORKING` compaction on target table; check compaction thread count | Throttle compaction worker threads: `hive.compactor.worker.threads`; pause non-critical compactions | Schedule compaction during off-peak; tune `hive.compactor.delta.num.threshold` to compact before file count explodes |
| HS2 GC pause dropping all in-flight queries | Mass client `TTransportException` at predictable intervals; HS2 heap shows sawtooth pattern | HS2 GC log: GC pause duration and frequency; correlate with client error timestamps | Reduce `hive.server2.thrift.max.worker.threads`; increase HS2 heap; add second HS2 instance for load balancing | Right-size HS2 heap; enable G1GC with `-XX:MaxGCPauseMillis=200`; use HS2 active-passive HA |
| Data skew concentrating work on one Tez vertex | Overall job slow despite most reducers finishing quickly; one vertex at 99% for minutes | Tez UI: vertex task distribution — look for single task taking 10x longer | Enable `hive.optimize.skewjoin=true`; add salt column to skewed GROUP BY keys | Profile join key cardinality before designing queries; document known-skewed columns in table metadata |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Hive Metastore DB (MySQL/PostgreSQL) goes down | HMS cannot serve any requests → all HS2 DDL/DML queries fail with `TException: connection refused` → all downstream ETL pipelines stall | All Hive users; Spark jobs using HiveMetastore; HBase Hive integration; Impala | `beeline -e "SHOW DATABASES"` returns `Error: Could not open client transport`; HMS log: `DataSourceException: Cannot get a connection, pool error` | Restart Metastore DB; fail over to replica; restart HMS: `systemctl restart hive-metastore` |
| HDFS NameNode unavailable (NN down or safemode) | Hive queries fail to open/read data files → `FileNotFoundException` or `SafeModeException` → all Hive queries return errors | Complete Hive cluster unavailability for read/write; DDL-only operations may still work | Hive log: `org.apache.hadoop.ipc.RemoteException: org.apache.hadoop.hdfs.server.namenode.SafeModeException`; `hdfs dfsadmin -safemode get` returns `ON` | Leave safemode: `hdfs dfsadmin -safemode leave`; resolve NN issue before accepting Hive workloads |
| YARN ResourceManager crash | Hive/Tez jobs cannot be submitted → `TezSessionManager: failed to open session` → all active Hive sessions return errors | All Hive queries that use Tez/MapReduce execution; Hive LLAP sessions | YARN RM web UI unreachable; `yarn application -list` fails; Hive log: `Could not start YARN app` | RM HA failover: `yarn rmadmin -failover rm1 rm2`; or restart RM; re-submit failed Hive jobs |
| Tez AM (Application Master) OOM killed | DAG execution aborted mid-way → partial output may be written to HDFS temp → client sees `TezException: AM failed` → upstream pipeline marks run as failed | Active Hive query fails; cascading retries fill YARN queue | YARN log for killed container: `Exit status: -104 (Container killed by YARN for exceeding memory limits)`; Tez UI shows DAG in FAILED state | Increase Tez AM memory: `tez.am.resource.memory.mb`; kill and re-submit job; clean up partial HDFS output |
| HiveServer2 thread pool exhaustion | New client connections rejected: `TooManyConnectionsException: Too many connections` → downstream BI tools and ETL pipelines cannot connect | All new Hive client connections; scheduled jobs fail to connect | HS2 log: `Too many connections`; `ss -tn state established dport = :10000 | wc -l` near `hive.server2.thrift.max.worker.threads` | Reap idle sessions by lowering `hive.server2.idle.session.timeout`; kill in-flight queries: `KILL QUERY '<query_id>'`; increase `hive.server2.thrift.max.worker.threads` temporarily |
| Lock contention on high-traffic transactional (ACID) table | Writers block on shared lock; readers pile up waiting for locks to clear; HMS lock manager CPU spikes | All jobs touching the affected table; cascading timeouts as lock wait > `hive.txn.timeout` | `SHOW LOCKS EXTENDED` — many locks in `WAITING` state on same table; HMS log shows high lock acquisition latency | Identify and kill blocking transaction: `ABORT TRANSACTIONS <TXN_ID>`; compact table to reduce delta file accumulation |
| Zookeeper quorum loss (if LLAP or HS2 HA uses ZK) | LLAP daemons lose ZK registration → LLAP coordinators cannot discover daemons → all LLAP queries fail fallback to Tez | All LLAP-routed queries; Hive HA router cannot elect active HS2 | ZK `zkCli.sh ls /llap` returns empty or error; LLAP daemon log: `Unable to connect to ZooKeeper` | Restore ZK quorum; restart LLAP service: `hive --service llap --start`; monitor daemon registration |
| HDFS quota exceeded on `/warehouse` | All INSERT/CREATE TABLE AS SELECT operations fail: `DiskQuotaExceededException` → ETL writes fail → data pipeline stalled | All write-intensive Hive jobs; ACID compaction also fails | Hive log: `QuotaExceededException: /warehouse`; `hdfs dfs -count -q /warehouse` shows quota at limit | Increase quota: `hdfs dfsadmin -setSpaceQuota 10t /warehouse`; purge stale/archived data; compact ACID tables |
| HMS schema version mismatch after HMS upgrade | Hive queries return `MetaException: Version information not found in metastore`; HMS fails to start cleanly | All Hive operations requiring Metastore | HMS log: `Schema version mismatch`; `SELECT SCHEMA_VERSION FROM VERSION` in Metastore DB returns old version | Run schema upgrade tool: `schematool -dbType mysql -upgradeSchema`; restart HMS |
| Upstream Kafka-to-HDFS ingestion flooding small files | Millions of small files in Hive table path → NameNode metadata explosion → Hive query planning OOM → all queries on that table hang | All users querying the affected Hive table; NameNode stability | `hdfs dfs -count /warehouse/<table>` shows file count in millions; NN JMX heap rising; `ANALYZE TABLE` taking hours | Halt ingest; merge small files: `ALTER TABLE <T> CONCATENATE`; schedule regular compaction |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Hive version upgrade (e.g., 3.1 → 4.0) | HMS schema incompatible with old clients; existing Spark HiveContext connections fail with `MetaException: Hive version mismatch` | Immediately post-upgrade | Upgrade timestamp matches first client errors; `SELECT SCHEMA_VERSION FROM VERSION` in Metastore DB confirms version | Run `schematool -dbType <db> -upgradeSchema`; roll back HMS binary if upgrade fails; test with `schematool -dbType <db> -validate` first |
| Changing default execution engine from Tez to Spark (or vice versa) | Queries using engine-specific syntax fail; performance regression for previously-tuned workloads | On next query execution after config change | Correlate `hive.execution.engine` change in `hive-site.xml` with first query failure timestamp | Revert `hive.execution.engine` to previous value; restart HS2; notify users of engine change |
| Reducing `hive.server2.thrift.max.worker.threads` | Connection pool exhausted; new connections rejected during peak load | At next peak load period after config change | `ss -tn state established dport = :10000 | wc -l` at limit; HS2 log: `TooManyConnections` at peak timestamps | Revert thread count in `hive-site.xml`; restart HS2; increase limit based on measured peak connection count |
| Metastore DB host migration (new hostname/IP in `javax.jdo.option.ConnectionURL`) | HMS fails to connect to Metastore DB: `DataSourceException: Cannot get a connection` | Immediately on HMS restart | HMS log shows connection error to new hostname at restart time; ping new DB host from HMS server | Verify DB connectivity: `mysql -h <NEW_HOST> -u hive -p`; fix `hive-site.xml` if hostname wrong; restart HMS |
| Adding new Hive partition column to existing table | Old partition data not queryable; `MSCK REPAIR TABLE` required; queries on new partitions fail until repaired | Immediately after `ALTER TABLE ADD COLUMN` | `SHOW PARTITIONS <TABLE>` returns fewer partitions than expected; repair: `MSCK REPAIR TABLE <TABLE>` | Run `MSCK REPAIR TABLE <TABLE>` to register existing partitions; verify with `SHOW PARTITIONS` |
| SerDe (serialization library) version change in table definition | Existing data unreadable: `SerDeException: Cannot deserialize row`; old files not compatible with new SerDe | Immediately on first query after `ALTER TABLE SET SERDE` | Query error message includes SerDe class name; correlate with `ALTER TABLE` in HMS audit log | Revert SerDe: `ALTER TABLE <T> SET SERDE '<OLD_SERDE_CLASS>'`; test on sample file before applying to production table |
| Compactor thread count reduction (`hive.compactor.worker.threads=0`) | Delta file count grows unbounded; transactional table read performance degrades; HMS lock contention increases | Hours to days as delta files accumulate | `SHOW COMPACTIONS` shows large backlog of `INITIATED` compactions; `hdfs dfs -ls /warehouse/<T>/delta_*` shows many delta dirs | Increase compactor threads: `hive.compactor.worker.threads=2`; manually trigger: `ALTER TABLE <T> COMPACT 'MAJOR'` |
| Kerberos principal change for HS2 service account | Existing Kerberos-authenticated client connections fail: `GSSException: No valid credentials`; new connections cannot authenticate | Immediately after principal change / keytab rotation | HS2 log: `Failed to create connection ... GSS initiate failed`; correlate with keytab rotation ticket | Re-keytab HS2: update `hive.server2.authentication.kerberos.keytab` and restart HS2; test: `kinit -kt hive.keytab hive/<FQDN>` |
| YARN queue capacity reallocation (reducing Hive queue capacity) | Hive jobs queue for longer; SLA breaches for time-sensitive ETL jobs | Immediately after capacity change at next Hive job submission | YARN RM UI: Hive queue capacity reduced; job submission time increases; `yarn queue -status hive` shows lower `capacity` | Revert YARN capacity scheduler config: `yarn rmadmin -refreshQueues`; restore Hive queue capacity |
| ORC file format version upgrade incompatibility | Old Hive/Spark clients cannot read new ORC files: `OrcFile$WriterVersion: unknown writer version` | Immediately after first write with new ORC version | Query error includes ORC version string; correlate with Hive upgrade timestamp; `hive --orcfiledump <FILE>` shows writer version | Pin `hive.exec.orc.write.format=0.12` for compatibility; or upgrade all readers to support new ORC version |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Hive Metastore DB replication lag (HMS reads from replica) | `SHOW CREATE TABLE <T>` returns stale schema; `SHOW PARTITIONS` missing recent partitions | `SELECT COUNT(*) FROM PARTITIONS` on primary vs replica returns different counts | DDL changes not visible to readers on replica; ETL jobs fail on missing partitions | Force read from primary: update `javax.jdo.option.ConnectionURL` to primary DB; or wait for replica to catch up |
| ACID transaction not committed (open txn blocking compaction) | `SHOW TRANSACTIONS` shows transactions in `open` state for > 1 hour | Compaction cannot proceed on tables with open transactions; delta file count grows | Compaction backlog; table read performance degrades; HMS lock contention | Abort stale transactions: `ABORT TRANSACTIONS <TXN_ID>`; investigate why transaction was not closed by application |
| Partition metadata in HMS out of sync with HDFS data (orphaned directories) | `MSCK REPAIR TABLE <TABLE>` returns non-zero new partitions; `hdfs dfs -ls /warehouse/<T>` shows dirs not in HMS | Queries on those partitions return `0 rows` even though data exists in HDFS | Data silently invisible; ETL summaries wrong | `MSCK REPAIR TABLE <TABLE>` to re-register HDFS partitions; verify: `SHOW PARTITIONS <TABLE>` |
| Corrupt ORC file written during DataNode failure mid-write | `SELECT * FROM <TABLE> WHERE dt='<PARTITION>'` returns `IOException: DWRF: error in read`; `SHOW PARTITIONS` shows partition but query fails | `hive --orcfiledump /warehouse/<T>/<PARTITION>/<FILE>` fails with checksum error | Partial data loss for the affected partition; downstream aggregations incorrect | Delete corrupt file: `hdfs dfs -rm /warehouse/<T>/<PARTITION>/<FILE>`; re-run ETL job for that partition |
| HMS partition statistics stale after large INSERT OVERWRITE | Query optimizer uses wrong statistics → bad execution plan → query runs 10x slower than expected | `DESCRIBE FORMATTED <TABLE> PARTITION (<P>)` shows `numRows: -1` or old value | Sub-optimal query plans; missed predicate pushdown; full scans instead of partition pruning | `ANALYZE TABLE <TABLE> PARTITION (<P>) COMPUTE STATISTICS`; enable auto-stats: `hive.stats.autogather=true` |
| Hive view definition pointing to dropped or renamed table | `SELECT * FROM <VIEW>` returns `Table not found: <UNDERLYING_TABLE>` | `SHOW CREATE VIEW <VIEW>` — underlying table name no longer exists | All queries using the view fail; dashboards that depend on view go dark | `DROP VIEW <VIEW>; CREATE VIEW <VIEW> AS SELECT ... FROM <NEW_TABLE_NAME>`; audit dependent views |
| Concurrent INSERT OVERWRITE on same partition from two jobs | Second INSERT OVERWRITE finishes first or at same time; first job's output partially overwritten | HDFS `rename` operations racing; final partition contains incomplete data from one or both jobs | Silent data loss / partial data in partition; incorrect aggregation results | Serialize writes to the same partition via orchestrator locking; enable ACID for concurrent writes: `'transactional'='true'` |
| HMS failover (HA) with split-brain: both HMS instances writing to same DB | Both HMS processes accept DDL; conflicting writes to `SDS`, `PARTITIONS` tables; FK violation errors | `java.sql.SQLIntegrityConstraintViolationException` in HMS logs; `SHOW TABLES` returns inconsistent results | Metadata corruption; HMS unable to serve queries reliably | Immediately stop secondary HMS; identify and reconcile conflicting rows in Metastore DB; restore from DB backup if severe |
| Partition column timezone inconsistency (dt stored as UTC vs local) | `SELECT WHERE dt='2026-01-01'` returns wrong rows; counts vary by time zone of executing server | `DESCRIBE EXTENDED <TABLE> PARTITION (dt='2026-01-01')` shows partition exists; row counts differ by timezone | ETL producing wrong time-bucketed aggregations; duplicate or missing rows in reports | Standardize all partition column values to UTC in ETL; `ALTER TABLE ... DROP PARTITION (dt='<WRONG_DATE>'); re-partition` |
| Hive lock left behind after HS2 crash | `SHOW LOCKS` shows lock on table/partition held by dead session | All write queries on locked table fail: `LockException: lock is already held` | ETL pipelines blocked from writing to locked tables | Identify and release lock: `UNLOCK TABLE <TABLE>`; or `ABORT TRANSACTIONS <TXN_ID>` for ACID tables; restart HMS to clear stale locks |

## Runbook Decision Trees

### Decision Tree 1: HiveServer2 Query Failures / Clients Cannot Connect

```
Are clients receiving connection errors?
├── YES → check: curl -s http://<HS2_HOST>:10002/ (HS2 web UI health)
│         Is HS2 process running?
│         ├── NO  → HS2 crashed → check: tail -200 /var/log/hive/hiveserver2.log | grep -i "error\|oom\|killed"
│         │         → OOM kill? → increase HS2 heap: export HADOOP_HEAPSIZE=8192; restart HS2
│         │         → Config error? → restore previous hive-site.xml from git; restart
│         └── YES → Is connection pool exhausted?
│                   ├── YES → check: ss -tnp | grep 10000 | wc -l vs hive.server2.thrift.max.worker.threads
│                   │         → Shed load: kill idle sessions via Hive session manager; scale out HS2
│                   └── NO  → Is Metastore reachable?
│                             ├── NO  → check: telnet <HMS_HOST> 9083
│                             │         → HMS down: restart Hive Metastore; check HMS DB connectivity
│                             └── YES → Is Kerberos/auth failing?
│                                       → check: kinit -k -t /etc/security/hive.keytab hive/<HOSTNAME>
│                                       → Renew keytab; check KDC logs
└── NO  → Are queries running but very slow?
          ├── YES → check: EXPLAIN <slow_query> in beeline; look for full-table scan warnings
          │         → Are statistics stale? → ANALYZE TABLE <tbl> COMPUTE STATISTICS FOR COLUMNS
          │         → YARN queue full? → yarn application -list; kill low-priority jobs
          └── NO  → Are queries queuing (not starting)?
                    → check: YARN UI queue depth; fair scheduler log
                    → Increase queue capacity or preempt lower-priority queue
```

### Decision Tree 2: Hive Metastore Failures / DDL Operations Failing

```
Are DDL operations (CREATE TABLE, ADD PARTITION) failing?
├── YES → check HMS health: nc -zv <HMS_HOST> 9083; hive --service metatool -listFSRoot
│         Is HMS process alive?
│         ├── NO  → check HMS logs: tail -200 /var/log/hive/hive-metastore.log | grep -E "ERROR|FATAL|OOM"
│         │         → Restart: systemctl restart hive-metastore; monitor log for DB connect success
│         └── YES → Is HMS DB connection pool exhausted?
│                   ├── YES → check: SHOW PROCESSLIST on MySQL/Postgres HMS DB — count Sleep connections
│                   │         → Kill idle connections: pt-kill --idle-time=60 --busy-time=120 --kill
│                   │         → Increase pool: set datanucleus.connectionPool.maxPoolSize=50 in hive-site.xml
│                   └── NO  → Is the specific table/partition locked?
│                             ├── YES → beeline -e "SHOW LOCKS EXTENDED" — identify blocking session
│                             │         → Kill blocking query: beeline -e "KILL QUERY '<query_id>'"
│                             └── NO  → Is HMS DB disk full?
│                                       → check: df -h <HMS_DB_DATA_DIR>
│                                       → Purge: clean HMS DB notification log: DELETE FROM NOTIFICATION_LOG WHERE ...
│                                       → Free space or expand DB volume
└── NO  → Are DDL operations succeeding but data not visible?
          → Is HMS cache stale? → beeline -e "MSCK REPAIR TABLE <tbl>"
          → Is HDFS location accessible? → hdfs dfs -ls <table_hdfs_path>
          → If HDFS location missing: recreate partition mapping or restore data from backup
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Runaway full-table scan consuming entire YARN queue | Other Hive jobs queued indefinitely; YARN queue at 100% | `yarn application -list | grep RUNNING`; YARN UI: sort by resources | All other ETL jobs blocked until job finishes or is killed | `yarn application -kill <app_id>`; identify source query via HS2 session list | Enforce `hive.strict.checks.no.partition.filter=true`; require WHERE on partitioned tables |
| ACID compaction delta explosion eating HDFS quota | ACID table partition file count in thousands; INSERT/UPDATE latency growing | `SHOW COMPACTIONS` — count INITIATED but not WORKING; `hdfs dfs -count /warehouse/<tbl>` | Write SLA breach on ACID tables; NameNode memory pressure from small files | Manually trigger: `ALTER TABLE <tbl> COMPACT 'MAJOR'`; increase compaction worker threads | Tune `hive.compactor.delta.num.threshold` and `hive.compactor.delta.pct.threshold` |
| MSCK REPAIR TABLE launched on large partitioned table blocking HMS | HMS thread pool exhausted during MSCK; all other DDL stalled | `SHOW LOCKS EXTENDED` — count `EXCLUSIVE` locks on HMS; HMS log for `msck` operations | All Hive DDL blocked for duration of MSCK (can be hours) | Kill MSCK query: `KILL QUERY '<id>'`; run incremental partition discovery instead | Use `MSCK REPAIR TABLE SYNC PARTITIONS` with partition filters; prefer `ALTER TABLE ADD PARTITION` |
| Tez shuffle service disk exhaustion from large-sort jobs | `No space left on device` on NodeManagers; sort jobs fail midway | `df -h /data/yarn/nm-local-dir` on all NodeManagers | All Tez reduce tasks on affected NM fail; re-runs triggered | Extend NM local disk; free space: `rm -rf /data/yarn/nm-local-dir/usercache/*/` | Set `tez.runtime.io.sort.factor` to limit shuffle memory; use multiple NM local dirs |
| Dynamic partitioning job creating thousands of small partitions | HMS DB growing rapidly; NameNode inode count spiking | `SHOW PARTITIONS <tbl> | wc -l`; NameNode JMX `FilesTotal` metric | HMS query latency high due to large partition count; NN memory pressure | Coalesce partitions: `INSERT OVERWRITE TABLE ... PARTITION (dt=...) SELECT ... FROM ... WHERE` | Enforce `hive.exec.max.dynamic.partitions.pernode`; limit partition granularity in table design |
| Hive scratch directory filling HDFS `/tmp` | New Hive queries fail with `QuotaExceededException` on `/tmp/hive` | `hdfs dfs -du -h /tmp/hive | sort -rh | head -20` | New Hive queries cannot start; ETL pipeline failure | `hdfs dfs -rm -r /tmp/hive/<USER>/<SESSION>`; expand `/tmp` quota | Set `hive.exec.scratchdir` per-user quota; enable post-query cleanup hooks |
| HS2 JVM heap growing from result set caching | HS2 OOMKilled intermittently during high-query-count periods | HS2 GC log: sawtooth heap pattern; JMX `HeapMemoryUsage.used` near max | HS2 crash drops all in-flight queries; client reconnect storm | Disable result set caching: `hive.server2.cache.resultset=false`; increase HS2 heap | Right-size HS2 heap; enable result set streaming to avoid buffering large results |
| HMS notification log table unbounded growth | HMS DB disk usage growing continuously; HMS DB queries slow | MySQL: `SELECT COUNT(*) FROM NOTIFICATION_LOG`; `du -sh /var/lib/mysql` | HMS DDL latency; HMS DB disk full → HMS crash | `DELETE FROM NOTIFICATION_LOG WHERE EVENT_TIME < UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 7 DAY))` | Configure `hive.metastore.event.db.listener.timetolive` to auto-purge old events |
| Hive LLAP daemon memory oversubscription | LLAP OOMKilled; interactive queries all fail | `beeline -e "SELECT * FROM sys.llap_app_info"`; LLAP UI memory gauges | All interactive LLAP queries fail until daemon restarts | Reduce `hive.llap.daemon.memory.per.instance.mb`; restart LLAP daemons | Profile peak concurrent query memory usage; set LLAP memory with 20% safety margin |
| Tez AM container reuse causing memory leak across jobs | Long-running Tez sessions gradually consume more YARN memory | `yarn application -list -appStates RUNNING | grep -i tez`; YARN app memory over time | YARN NM evictions; other jobs cannot get containers | Kill long-running Tez sessions: `tez.session.am.dag.submit.timeout.secs` | Set `tez.am.container.reuse.enabled=false` for memory-sensitive workloads; limit AM lifetime |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot partition — all queries hitting single date partition | Query latency high even for small data scans; partition read skew | `beeline -e "EXPLAIN SELECT * FROM <tbl> WHERE dt='<date>'"` — check partition pruning; `hdfs dfs -du -h /warehouse/<tbl>/dt=<date>` | Missing or incorrect partition filter; single hot partition receiving all traffic | Add partition filter in queries; split hot partition by hour: `ALTER TABLE ADD PARTITION (dt='<date>', hr='<h>')` |
| HMS connection pool exhaustion — too many HS2 sessions holding connections | `MetaException: Too many connections`; new query submissions fail | `mysql -u hive -e 'SHOW PROCESSLIST' | wc -l`; compare with `hive.metastore.ds.connection.pool.size` | HMS JDBC pool size too small for concurrent HS2 session count | Increase `hive.metastore.ds.connection.pool.size`; set `datanucleus.connectionPool.maxPoolSize` |
| Tez AM GC pressure from large DAG result caching | Tez AM intermittently slow; GC pause >5s visible in `yarn logs -applicationId <id>` | `yarn logs -applicationId <app_id> 2>&1 | grep "GC pause\|Pausing\|pause time"` | Tez AM caching DAG result metadata; heap exhausted by large query count | Reduce `tez.am.container.reuse.enabled` reuse count; increase AM heap: `tez.am.java.opts=-Xmx4g` |
| Tez reduce task thread pool saturation from high-fan-in join | Reduce phase takes 10x map phase time; shuffle fetch threads maxed | `yarn logs -applicationId <app_id> | grep "Slow Shuffle\|Fetch fail"` | Too many reduce tasks competing for shuffle fetch threads; `tez.runtime.shuffle.fetch.max.task.output.at.once` too high | Reduce `tez.runtime.shuffle.fetch.max.task.output.at.once`; increase `tez.runtime.shuffle.parallel.copies` |
| Slow Metastore query on large `PARTITIONS` table in HMS MySQL | `SHOW PARTITIONS` or `ALTER TABLE ADD PARTITION` taking >30s | `mysql -u hive -e "EXPLAIN SELECT * FROM PARTITIONS WHERE TBL_ID=<id>"` — check index usage; `SHOW INDEX FROM PARTITIONS` | Missing index on `PARTITIONS.TBL_ID`; HMS MySQL table bloat | Add index: `CREATE INDEX PART_TBL_ID ON PARTITIONS(TBL_ID)`; run `ANALYZE TABLE PARTITIONS` |
| CPU steal on HS2 host from co-located Tez AM processes | HS2 query compilation latency spikes; `%st` high in `top` | `top -b -n1 | grep "Cpu(s)"` — watch `%st`; `sar -u 1 30` on HS2 host | HS2 and Tez AMs sharing physical host; hypervisor steal during bursts | Dedicate HS2 host; move Tez AM scheduling to dedicated queue with `tez.am.node-blacklisting.enabled=false` |
| Lock contention on HMS DB from ACID write + compaction running simultaneously | ACID INSERT/UPDATE latency >60s; compaction log shows lock wait | `mysql -u hive -e 'SELECT * FROM HIVE_LOCKS WHERE HL_TYPE="SHARED_WRITE"'`; `SHOW COMPACTIONS` | Compaction holding shared locks while ACID writers also need locks | Schedule compaction during off-peak; set `hive.compactor.worker.threads=1` during peak hours |
| MSCK REPAIR TABLE serializing all HMS partition discovery | All HMS DDL blocked; `SHOW PARTITIONS` returns `Application busy` | `beeline -e "SHOW LOCKS EXTENDED"` — look for `EXCLUSIVE` lock on full table | MSCK takes full table lock for entire duration | Cancel MSCK: `KILL QUERY '<id>'`; use `ALTER TABLE <tbl> ADD PARTITION` for targeted partition adds |
| Large batch size in Hive-to-HDFS export causing YARN NM local disk saturation | Tez tasks fail with `Disk I/O error`; NM local dir full | `df -h /data/yarn/nm-local-dir` on NM hosts; `yarn application -list | grep RUNNING` | Single Tez task writing huge intermediate sort spill to NM local disk | Reduce `tez.runtime.io.sort.mb`; add NM local disk paths in `yarn.nodemanager.local-dirs` |
| Downstream HMS dependency latency — slow MySQL causing all Hive DDL to stall | All `CREATE TABLE`, `ALTER TABLE` operations >10s; HS2 log shows HMS timeout | `mysql -u hive -e "SHOW STATUS LIKE 'Threads_running'"` — watch thread count; `mysqladmin processlist` | MySQL I/O saturation or lock contention; HMS waits for DB response | Optimize MySQL: `SET GLOBAL innodb_buffer_pool_size=4G`; promote HMS DB read replica; fix slow queries |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| Kerberos KDC unavailability causing Hive authentication failure | Beeline connect fails: `GSS initiate failed`; HS2 log: `Kerberos authentication failed` | `kinit -kt /etc/hive/conf/hive.keytab hive/<HS2_HOST>` — check KDC response time; `klist -e` | All Kerberos-authenticated HS2 connections refused | Restore KDC; verify `/etc/krb5.conf` KDC list includes failover; `kinit` with correct keytab |
| SSL cert expiry on HiveServer2 HTTPS UI | Monitoring/Grafana dashboards for HS2 fail; `curl https://<HS2_HOST>:10002` returns cert error | `openssl s_client -connect <HS2_HOST>:10002 -showcerts 2>&1 | grep "notAfter"` | HS2 Web UI inaccessible; JDBC SSL connections refused if `hive.server2.use.SSL=true` | Renew cert; update `hive.server2.keystore.path` in hive-site.xml; restart HS2 |
| DNS resolution failure for HMS hostname in multi-HS2 cluster | HS2 cannot reach HMS; `MetaException: Could not connect to meta store using any of the URIs provided` | `dig <HMS_HOST>` on HS2 host; compare with `hive.metastore.uris` in hive-site.xml | All HS2 query submissions fail until HMS connection restored | Update `/etc/hosts` or fix DNS; verify `hive.metastore.uris=thrift://<HMS_IP>:9083` uses resolvable address |
| TCP connection exhaustion between HS2 and HMS — too many idle thrift connections | HMS `Too many connections` error; new queries fail to compile | `ss -nt dst <HMS_HOST>:9083 | wc -l`; compare with HMS `hive.metastore.server.max.message.size` | Query compilation fails; all new Hive queries error immediately | Restart HS2 to reset connection pool; set `hive.metastore.client.socket.timeout=300` with `hive.metastore.failover.on.connections.exhausted=true` |
| Load balancer idle timeout cutting HMS thrift long-lived connections | Intermittent `TTransportException: Connection reset` during long queries | `netstat -an | grep :9083 | grep ESTABLISHED`; reproduce by running a >LB timeout query | Cloud LB idle timeout (60s default) shorter than long Hive compiles | Set LB idle timeout to 600s; or use `hive.metastore.client.socket.keepalive=true` |
| Packet loss between Tez tasks and HDFS DataNodes during shuffle | Tez reduce tasks retrying shuffle fetch; job runtime 3x normal | `yarn logs -applicationId <app_id> | grep "Fetch fail\|Shuffle error\|retry"`; `ping -c 500 <DN_HOST>` | Network packet loss between NM and DN racks; shuffle retries pile up | Investigate switch/NIC errors: `ethtool -S eth0 | grep error` on affected NM hosts; fix with network team |
| MTU mismatch causing Tez shuffle data truncation | Tez reduce tasks silently read incomplete shuffle data; job produces wrong results | `ping -M do -s 8972 <NM_HOST>` from other NM — if fails, MTU mismatch | Silent data corruption in shuffle phase; incorrect query results | Align MTU across all NM hosts: `ip link set eth0 mtu 9000`; verify jumbo frames enabled on switch |
| Firewall change blocking Tez shuffle port range | Tez reduce phase hangs; shuffle port connection refused | `yarn logs -applicationId <app_id> | grep "Connection refused"` — note port numbers; `telnet <NM_HOST> <shuffle_port>` | All Tez jobs with reduce phase stall or fail | Restore firewall rules for Tez shuffle port range (13562 by default); verify `tez.shuffle.port` config |
| HMS thrift SSL handshake timeout when HMS uses SSL | HS2 log: `TTransportException` on connect to HMS; all DDL fails | `openssl s_client -connect <HMS_HOST>:9083`; check if HMS SSL enabled: `grep ssl /etc/hive/conf/hive-site.xml` | All HS2 operations requiring HMS fail; complete service outage | Verify HS2 truststore contains HMS cert; restart HS2 after truststore fix |
| Connection reset from MySQL HMS DB during large NOTIFICATION_LOG scan | HMS log: `CommunicationsException: Communications link failure`; replication-enabled clients stall | HMS log: `grep "CommunicationsException\|Packet for query" /var/log/hive/hive-metastore.log` | HMS-to-MySQL connection drops; HMS must reconnect; brief DDL unavailability | Increase MySQL `wait_timeout=28800`; enable HMS connection pool validation: `datanucleus.connectionPool.testSQL` |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| HS2 JVM heap OOM from large result set buffering | HS2 OOMKilled; `java.lang.OutOfMemoryError` in HS2 log; all in-flight queries dropped | `grep OOM /var/log/hive/hiveserver2.log`; `jstat -gcutil $(pgrep -f HiveServer2) 1000 10` | Restart HS2; increase heap: `export HIVE_SERVER2_HEAPSIZE=8192` in hive-env.sh; enable result set streaming | Set `hive.server2.thrift.resultset.default.fetch.size=1000`; use streaming via `hive.server2.thrift.http.response.streaming.enabled=true` |
| HMS MySQL DB disk full from NOTIFICATION_LOG | HMS can no longer write transaction events; all ACID operations fail | `du -sh /var/lib/mysql`; `mysql -u hive -e 'SELECT COUNT(*) FROM NOTIFICATION_LOG'` | Delete old events: `DELETE FROM NOTIFICATION_LOG WHERE EVENT_TIME < UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 7 DAY))`; `OPTIMIZE TABLE NOTIFICATION_LOG` | Configure `hive.metastore.event.db.listener.timetolive=604800000`; dedicate MySQL volume for HMS DB |
| YARN NM local disk full from Tez sort spill | Tez tasks fail with `LocalDirAllocator: No space available in any of the local directories`; jobs abort | `df -h /data/yarn/nm-local-dir` on all NM hosts; `du -sh /data/yarn/nm-local-dir/usercache/*/appcache/*` | Free space: `rm -rf /data/yarn/nm-local-dir/usercache/*/appcache/old_appids/`; restart affected NM | Configure multiple NM local dirs across disks; alert on NM local dir >80% |
| HDFS inode exhaustion from Hive small-file explosion | New Hive INSERT INTO writes fail: `Inodes quota exceeded`; NameNode JMX `FilesTotal` at limit | `hdfs dfsadmin -report | grep "Files And Directories"`; `hdfs dfs -count /warehouse/<tbl>` | Compact small files: `hadoop jar hadoop-mapreduce-examples.jar merge`; drop old partitions | Enable `hive.merge.mapfiles=true` and `hive.merge.mapredfiles=true`; set `hive.exec.max.dynamic.partitions.pernode=100` |
| HMS DB file descriptor exhaustion | HMS log: `java.io.IOException: Too many open files`; DDL operations fail | `ssh <HMS_HOST> "ls /proc/$(pgrep -f HiveMetaStore)/fd | wc -l"` | Restart HMS process; `ulimit -n 65536` for hive user | Set `nofile 65536` in `/etc/security/limits.conf` for `hive` user; monitor HMS FD count |
| LLAP daemon CPU throttle from container cgroup limit | LLAP interactive queries exceed SLA; CPU utilization capped | `cat /sys/fs/cgroup/cpu/yarn/container.*/cpu.stat | grep throttled_time`; `top -b -n1 -p $(pgrep -f LlapDaemon)` | Increase LLAP container CPU quota in `llap-daemon-site.xml`; or remove explicit cgroup CPU limits | Size LLAP CPU allocation: `hive.llap.daemon.vcpus.per.instance` should match physical CPU count |
| Hive scratch dir inode exhaustion on HDFS /tmp | New query sessions fail to create staging dir; `No inodes available` | `hdfs dfs -count /tmp/hive` — check file count; `hdfs dfsadmin -setQuota <n> /tmp/hive` | Delete stale scratch dirs: `hdfs dfs -rm -r /tmp/hive/<USER>/<old_session>`; set inode quota | Enable `hive.exec.scratchdir.inode.limit`; run nightly cleanup of /tmp/hive dirs older than 24h |
| Tez container OOM from broadcast join with large dimension table | Tez AM OOM mid-query; broadcast join table exceeds container memory | `yarn logs -applicationId <app_id> | grep "killed by YARN"` — note container reason | Disable broadcast join for large tables: `SET hive.auto.convert.join.noconditionaltask.size=20971520` | Tune `hive.auto.convert.join.noconditionaltask.size`; check dimension table size before broadcast |
| HS2 thread pool exhaustion from slow JDBC clients holding connections | New JDBC connections rejected: `HiveSQLException: Error opening session`; HS2 threads maxed | `curl http://<HS2_HOST>:10002/jmx | jq '.beans[] | select(.name | contains("HiveServer2")) | .ActiveConnections'` | Force-close idle sessions by lowering `hive.server2.idle.session.timeout=3600000` (HS2 reaps stale sessions); kill in-flight queries with `KILL QUERY '<query_id>'` | Enforce `hive.server2.idle.session.timeout` and `hive.server2.idle.operation.timeout`; monitor active connection count |
| Ephemeral port exhaustion on HS2 from high JDBC connection churn | HS2 host shows `Cannot assign requested address`; new JDBC connections fail | `ss -s | grep TIME-WAIT`; `cat /proc/sys/net/ipv4/ip_local_port_range` | `sysctl -w net.ipv4.ip_local_port_range="1024 65535"`; enable `net.ipv4.tcp_tw_reuse=1` | Use JDBC connection pooling in application tier (HikariCP); avoid short-lived connect-query-disconnect pattern |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| ACID transaction idempotency violation — duplicate INSERT via retry on non-idempotent operation | Row counts double after job retry; ACID transaction log shows two commits for same data range | `beeline -e "SELECT COUNT(*) FROM <tbl> WHERE dt='<date>'"` — compare with source count; `SHOW TRANSACTIONS` | Duplicate records in ACID table; downstream aggregations produce incorrect results | Identify and delete duplicate delta files; run `ALTER TABLE <tbl> COMPACT 'MAJOR'` to merge and deduplicate |
| ACID saga partial failure — multi-table transaction rolls back only one table | Source table reflects write; target table rolled back; tables inconsistent | `beeline -e "SHOW TRANSACTIONS"` — find open or aborted TXNs; check both tables' row counts | Source/target tables in inconsistent state; ETL correctness compromised | Identify which txn partial-committed; use `DELETE WHERE` to remove partial rows; rerun ETL with idempotency check |
| HMS notification log replay causing Hive Replication to re-apply already-applied events | Hive Replication target cluster shows stale data after replaying old notification events | `beeline -e "SHOW EVENTS FROM NOTIFICATION_LOG WHERE NL_ID > <last_replicated_id>"`; compare source vs target partition counts | Target cluster data older than source; stale reads by downstream consumers | Reset replication pointer to correct `lastReplicatedEventId`; `REPL STATUS <db>` on target to confirm sync |
| Out-of-order partition creation — ETL writing dt=today before dt=yesterday finishes | Partition `dt=today` exists and readable; `dt=yesterday` missing or partial | `beeline -e "SHOW PARTITIONS <tbl>"` — check expected partition sequence; `hdfs dfs -ls /warehouse/<tbl>/` | Downstream queries reading `dt=today` but missing `dt=yesterday` history; incorrect daily rollups | Block reads on `dt=today` until `dt=yesterday` completes; use Hive partition-level write locks via ACID |
| At-least-once delivery from Kafka-to-Hive pipeline causing duplicate rows in ORC table | Row count exceeds expected; duplicates detectable by primary key group count | `beeline -e "SELECT pk, COUNT(*) cnt FROM <tbl> GROUP BY pk HAVING cnt > 1 LIMIT 20"` | Duplicate fact rows in warehouse; incorrect metrics downstream | Deduplicate with `INSERT OVERWRITE TABLE ... SELECT DISTINCT`; implement idempotent upsert via ACID MERGE |
| Cross-service deadlock — Spark and Hive both holding ACID locks on same table | Both Spark and Hive jobs waiting on each other's lock; neither progresses | `beeline -e "SHOW LOCKS EXTENDED"` — identify conflicting lock holders; `yarn application -list | grep RUNNING` | Both jobs stall indefinitely; ETL SLA breach | Kill one job to break deadlock: `yarn application -kill <app_id>`; reschedule non-overlapping |
| Compensating transaction failure during Hive ACID MERGE rollback leaving partial delta | MERGE statement aborted mid-execution; partial delta directory written; subsequent reads return phantom rows | `hdfs dfs -ls /warehouse/<tbl>/delta_*`; `beeline -e "SHOW COMPACTIONS"` — look for FAILED state | Ghost rows visible until next major compaction | Force major compaction: `ALTER TABLE <tbl> COMPACT 'MAJOR'`; monitor via `SHOW COMPACTIONS` until SUCCEEDED |
| Distributed lock expiry — ZooKeeper session timeout during long-running Hive DDL | RENAME TABLE or DROP PARTITION fails mid-operation; HMS ZK lock released by timeout | HMS log: `grep "ZkLockManager\|SessionExpired\|LockException" /var/log/hive/hive-metastore.log` | Table in partially renamed/modified state; inconsistent between HMS DB and HDFS | Manually reconcile HMS DB and HDFS: verify table location in `TBLS` table vs `hdfs dfs -ls`; repair if diverged |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — single LLAP query consuming all LLAP daemon CPU | `top -b -n1 -p $(pgrep -f LlapDaemon)` — high CPU; LLAP UI shows one query consuming all slots | All other LLAP interactive queries queued indefinitely; SLA breach for dashboards | `beeline -e "KILL QUERY '<query_id>'"` for the offending long-running query | Set LLAP per-query CPU limits: `hive.llap.daemon.vcpus.per.instance`; enforce YARN queue `maximum-capacity` per tenant |
| Memory pressure from adjacent tenant's broadcast join caching large table | Tez AM log: `killed by YARN` for neighbor tenants' containers; `free -h` on NM shows low free memory | Other tenants' Tez containers OOMKilled on same NM host | `yarn application -kill <OFFENDING_APP_ID>`; `yarn node -decommission <NM_HOST>` if swap triggered | Tune `hive.auto.convert.join.noconditionaltask.size=20971520` (20MB) per queue; enforce per-queue NM memory caps |
| Disk I/O saturation from tenant's large ORC ZLIB write to HDFS from Hive INSERT | `iostat -x 1 5` on DataNode nodes — sustained high `%util` from one tenant | Other tenants' Hive inserts and HDFS reads slow; ETL SLA breaches | `yarn application -kill <APP_ID>`; reduce I/O: `SET hive.exec.compress.output=false` for offending session | Set per-queue YARN disk bandwidth limits; use SNAPPY compression (lower CPU cost) vs ZLIB for high-volume inserts |
| Network bandwidth monopoly — Tez shuffle traffic from one tenant's large join consuming switch bandwidth | `iftop` on NM nodes — one YARN application consuming >80% bandwidth; Tez shuffle retries in other apps | Other tenants see Tez shuffle failures and retries; job runtime 3x normal | Reduce parallelism: `yarn application -kill <APP_ID>`; resubmit with `SET tez.am.resource.memory.mb=4096` | Use YARN network bandwidth isolation via cgroups (`yarn.nodemanager.container-executor.class=LinuxContainerExecutor`) |
| HMS connection pool starvation — one tenant's HS2 session holding all connections open | `mysql -u hive -e 'SHOW PROCESSLIST'` — one application's sessions holding all connections | All other tenants receive `MetaException: Too many connections`; DDL and query compilation fail | Identify offending session: `beeline -e "SHOW SESSIONS"`; kill in-flight queries with `KILL QUERY '<query_id>'` and lower `hive.server2.idle.session.timeout` to reap idle sessions | Set per-user HMS connection limit via connection pool configuration; tune `datanucleus.connectionPool.maxPoolSize` |
| Quota enforcement gap — tenant using default Hive queue with no resource cap | `yarn node -list` — default queue consuming >70% of cluster; capacity queue empty | High-priority tenant jobs in capacity queue pending despite having capacity | `yarn application -movetoqueue <APP_ID> -queue <TENANT_QUEUE>` | Disable default queue: `yarn.scheduler.capacity.root.default.capacity=0`; require all jobs to specify `hive.server2.tez.default.queues=<TENANT_QUEUE>` |
| Cross-tenant data leak risk — Hive view exposing underlying table to all users via DEFINER rights | `beeline -e "SHOW CREATE VIEW <VIEW_NAME>"` — check `SECURITY DEFINER` or missing column-level permissions | Tenant A's view built on sensitive table readable by Tenant B without explicit grant on base table | Sensitive column data visible to unauthorized tenant via view | Drop and recreate view with explicit column exclusions; add Ranger column-masking policy on sensitive columns |
| Rate limit bypass — tenant disabling query result cache to force full execution on every query | HMS DB: `mysql -e "SELECT count(*) FROM QUERY_RESULTS_CACHE"` drops; HS2 CPU constantly high | Other tenants share HS2 thread pool with tenant bypassing caching; CPU saturated | Set per-session cache enforcement: `SET hive.query.results.cache.enabled=true` cannot be overridden | Enforce via Ranger session-level policy: disallow `SET hive.query.results.cache.enabled=false` |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| JMX metric scrape failure — Prometheus cannot reach HS2 JMX port | `hive_*` metrics absent in Prometheus; HS2 dashboards blank; no alert fires | JMX exporter not started or JMX port changed after HS2 upgrade; firewall blocking scrape port | `curl -s http://<HS2_HOST>:10002/jmx` — if fails, JMX unreachable; `ps aux | grep jmx_exporter` | Restart JMX exporter process; restore firewall rule; add Prometheus alert `up{job="hiveserver2"}==0` |
| Trace sampling gap — Tez DAG traces sampled at low rate missing slow-query incidents | Slow HiveQL queries not visible in Jaeger/Zipkin; only query logs show elevated latency | Tez tracing configured at 1% sampling; most slow queries not captured | Enable full tracing for queries >10s: `SET hive.tez.trace.enabled=true`; check Tez UI at `http://<RM_HOST>:8088/tez-ui` for DAG details | Configure adaptive sampling in Tez; use Tez UI for ad-hoc DAG inspection; export Tez DAG via `curl http://<TIMELINE>:8188/ws/v1/timeline/TEZ_DAG_ID/<id>` |
| Log pipeline silent drop — Hive audit log not flowing to SIEM during high-query-volume periods | Security audit gap; compliance requirement unmet; audit events missing from ELK for busy periods | Audit log Fluentd/Logstash pipeline buffer overflow; HS2 audit log rotation faster than pipeline pickup | Compare line count: `wc -l /var/log/hive/hiveserver2-audit.log` vs Splunk/ELK query count for same period | Increase log pipeline buffer; use persistent disk buffer; alert on Fluentd queue depth; increase audit log max file size |
| Alert rule misconfiguration — HS2 connection pool alert using wrong metric after Hive 3.x upgrade | Connection exhaustion goes undetected; metric name changed from `hive_hs2_active_connections` to `HiveServer2.hive.server2.active.sessions` | Hive 3.x changed JMX bean naming; existing Prometheus alert expression silently not matching | `curl http://<HS2_HOST>:10002/jmx | jq '.beans[] | .name' | grep -i session` — find current metric name | Audit all Hive alert expressions after each major Hive upgrade; test alert firing with `amtool alert add` |
| Cardinality explosion — per-query metrics with query_id label overwhelming Prometheus | Prometheus tsdb OOM; ingestion falls behind; all Hive metrics stale | Hive emitting per-query metrics with unique `query_id` label creating millions of unique time series | `kubectl exec prometheus-<POD> -- promtool tsdb analyze /prometheus | grep hive` — identify cardinality | Remove `query_id` label from Prometheus metric definitions; aggregate by user/queue only; use Hive query log for per-query details |
| Missing HMS health endpoint — HMS process up but unable to serve metadata | Hive query compilation fails with `MetaException: Could not connect to meta store`; HMS process shows running | HMS liveness probe only checks process alive, not whether thrift port is serving | `beeline -e "SHOW DATABASES"` to test HMS connectivity; `nc -zv <HMS_HOST> 9083` — check thrift port | Add HMS health check script: `hive --service metatool -listFSRoot` and expose result as Prometheus gauge |
| Instrumentation gap — YARN NM local disk usage for Tez not monitored | NM disk fills silently; Tez tasks start failing with `LocalDirAllocator: No space available` | NM local disk not in default Prometheus node_exporter alerts for `/data/yarn` paths | `df -h /data/yarn/nm-local-dir` on all NM hosts via Ansible ad-hoc; `du -sh /data/yarn/nm-local-dir/usercache/*` | Add Prometheus `node_filesystem_avail_bytes{mountpoint="/data/yarn"}` alert at <20% free |
| Alertmanager outage masking HMS DB disk alert | HMS stops writing metadata; all Hive DDL fails; on-call not paged | Alertmanager was simultaneously OOMKilled; HMS disk full alert fired but not delivered | `mysql -u hive -e 'SHOW STATUS LIKE "Uptime"'`; `df -h /var/lib/mysql` manually | Implement dead-man's-switch: Prometheus Watchdog alert to secondary PagerDuty integration; test alertmanager monthly |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Hive minor version upgrade (3.1.x → 3.2.x) rollback | HS2 fails to start after upgrade; `NoSuchMethodError` or class incompatibility in HS2 log | `grep "NoSuchMethodError\|ClassNotFoundException\|incompatible" /var/log/hive/hiveserver2.log` | Revert Hive binaries; restore HMS DB schema backup; restart HS2 | Take HMS DB snapshot before upgrade: `mysqldump hive > hive_pre_upgrade.sql`; upgrade in staging first; check Hive upgrade notes for schema changes |
| Schema migration partial completion — HMS DB `schematool` upgrade failing mid-run | Some HMS tables on new schema version; others on old; HS2/HMS fails to start | `schematool -dbType mysql -validate`; `mysql -e "SELECT SCHEMA_VERSION FROM VERSION"` | Restore HMS DB from pre-upgrade backup: `mysql hive < hive_pre_upgrade.sql`; re-run `schematool -dbType mysql -upgradeSchema` | Run `schematool -dbType <db> -validate` before upgrade; take DB backup; use transaction-safe migration scripts |
| Rolling upgrade version skew — HMS on 3.2 serving HS2 still on 3.1 causing Thrift protocol mismatch | HS2 3.1 fails to deserialize HMS 3.2 Thrift responses; `TProtocolException` in HS2 log | `grep "TProtocolException\|Thrift\|serialize" /var/log/hive/hiveserver2.log`; compare HS2 and HMS versions | Downgrade HMS back to 3.1: stop HMS, restore binaries, restart | Upgrade HMS and HS2 together in same maintenance window; or verify Thrift backward compatibility between versions |
| Zero-downtime Hive ACID migration — converting non-ACID table to ACID during query traffic | Queries on table during conversion return `com.google.common.util.concurrent.UncheckedExecutionException`; conversion hangs | `beeline -e "SHOW TRANSACTIONS"` — look for long-running txn on migrated table; `SHOW LOCKS EXTENDED` | Cancel conversion: `beeline -e "ABORT TRANSACTION <txn_id>"`; disable ACID: `ALTER TABLE <tbl> SET TBLPROPERTIES("transactional"="false")` | Schedule ACID migration during maintenance window; redirect query traffic to a copy before migration |
| Config format change — Hive 4.x removed `hive.execution.engine=mr` (MapReduce engine deprecated since 2.0, removed in 4.0); leftover `mr` config rejected or coerced to Tez | Queries failing or behavior change after upgrade; extreme latency regression if engine setting silently changed | `beeline -e "SET hive.execution.engine"` — verify value is `tez`; compare query runtimes pre/post upgrade | `SET hive.execution.engine=tez` per session; update `hive-site.xml` and restart HS2 | Diff `hive-site.xml` before and after upgrade; remove deprecated `hive.execution.engine=mr` settings; validate HMS schema: `schematool -dbType <db> -validate` |
| Data format incompatibility — ORCv2 ACID writer producing files unreadable by older Hive reader | Queries against ACID tables upgraded to ORCv2 fail: `IOException: unsupported Hive type`; older cluster Hive replication target fails | `hdfs dfs -text /warehouse/<tbl>/delta_*` — check ORC magic bytes; `beeline -e "SHOW CREATE TABLE <tbl>"` for ORC format version | Downgrade ORC writer version: `SET orc.format.version=0.12`; rewrite affected tables: `INSERT OVERWRITE TABLE <tbl> SELECT * FROM <tbl>` | Pin ORC version in `hive-site.xml`: `orc.version=0.12` until all consumers upgraded; test ORC compatibility in staging |
| Feature flag rollout — enabling Hive materialized views causing query planner regression | Queries that previously used partition pruning now triggering full scan via materialized view rewrite | `beeline -e "EXPLAIN <QUERY>"` — compare plan pre/post; check for `Materialized View Rewrite` in plan | Disable materialized view rewriting: `SET hive.materializedview.rewriting=false`; drop offending MV | Enable materialized views on dev cluster first; profile query plan changes with `EXPLAIN VECTORIZATION DETAIL` |
| Dependency version conflict — Hive upgrade changing Hadoop client JAR version causing Tez AM incompatibility | Tez AM fails to launch after Hive upgrade: `java.lang.NoClassDefFoundError` in YARN container log | `yarn logs -applicationId <app_id> 2>&1 | grep "NoClassDefFoundError\|ClassLoader"`; `hadoop version` vs Tez expected Hadoop version | Pin `tez.lib.uris` in `tez-site.xml` to compatible Tez TAR.GZ; rebuild Tez with matching Hadoop version | Maintain Hive/Tez/Hadoop compatibility matrix; test all version combinations in CI before production upgrade |

## Kernel/OS & Host-Level Failure Patterns

| Failure Mode | Symptom | Root Cause | Diagnostic Commands | Remediation |
|-------------|---------|------------|--------------------:|-------------|
| OOM killer terminates HiveServer2 process | HS2 connections drop; all running queries fail with `TTransportException`; users cannot connect; `dmesg` shows oom-kill for java | HS2 JVM heap exhausted by concurrent query compilations and large result set caching; container memory limit exceeded | `dmesg \| grep -i "oom.*java\|hive"` ; `journalctl -u hiveserver2 \| grep -i "kill\|oom"`; `jstat -gcutil $(pgrep -f HiveServer2) 1000 5` | Increase HS2 heap: `export HIVE_SERVER2_HEAPSIZE=8192` in hive-env.sh; set container limit = heap + 2G overhead; limit concurrent compilations: `hive.driver.parallel.compilation.global.limit=10` |
| Inode exhaustion on Hive scratch directory | Hive queries fail: `No space left on device` writing to `/tmp/hive`; `df -h` shows free disk space | Thousands of temporary files from Tez intermediate data and query scratch directories not cleaned up | `df -i /tmp/hive`; `find /tmp/hive -type f \| wc -l`; `find /tmp/hive -type f -mtime +7 \| wc -l` | Add cleanup CronJob: `find /tmp/hive -mtime +1 -delete`; set `hive.exec.scratchdir` to dedicated filesystem with high inode count; configure `hive.exec.local.scratchdir.cleanup.interval=3600` |
| CPU steal causing Hive query compilation timeouts | Query compilation takes 30s+ instead of normal 2s; HS2 thread pool exhausted waiting for compilations; new connections rejected | Noisy neighbor on shared VM stealing CPU from HS2 host; query plan optimization is CPU-intensive | `cat /proc/stat \| awk '/^cpu / {print "steal%: "$9}'`; `mpstat -P ALL 1 5 \| grep steal`; `beeline -e "SET hive.server2.idle.session.timeout"` | Migrate HS2 to dedicated compute-optimized instance; pin HS2 process to dedicated CPUs: `taskset -cp 0-7 $(pgrep -f HiveServer2)` |
| NTP skew causing Hive ACID transaction conflicts | Hive ACID INSERT/UPDATE operations fail with `TxnAbortedException: Transaction already aborted`; compaction initiator and HS2 disagree on timestamps | Clock skew between HS2, HMS, and compaction worker nodes causes transaction timeout misinterpretation | `date -u` on all Hive nodes; `chronyc tracking \| grep "System time"`; `beeline -e "SHOW TRANSACTIONS" \| grep ABORTED` | Sync clocks: `chronyc makestep` on all nodes; configure chrony with low-jitter NTP source; increase `hive.txn.timeout=600` to tolerate minor skew |
| File descriptor exhaustion on HiveServer2 | HS2 cannot accept new JDBC connections: `Too many open files`; existing queries continue but no new sessions possible | Each Beeline/JDBC session opens multiple FDs; HS2 default ulimit 32768 exhausted with 500+ concurrent sessions and Tez AM connections | `ls /proc/$(pgrep -f HiveServer2)/fd \| wc -l`; `cat /proc/$(pgrep -f HiveServer2)/limits \| grep "Max open files"`; `beeline -e "SET hive.server2.thrift.max.worker.threads"` | Increase FD limit: `ulimit -n 131072` in hive-env.sh; reduce max sessions: `hive.server2.thrift.max.worker.threads=200`; enable session timeout: `hive.server2.idle.session.timeout=7200000` |
| TCP conntrack table full on HS2 node | New JDBC connections fail intermittently; Beeline connects on retry; `dmesg` shows conntrack table full messages | HS2 node handles thousands of short-lived Beeline connections from Airflow/Oozie schedulers; conntrack entries from TIME_WAIT connections accumulate | `cat /proc/sys/net/netfilter/nf_conntrack_count`; `dmesg \| grep conntrack`; `ss -s \| grep "TCP:"` | Increase conntrack: `sysctl -w net.netfilter.nf_conntrack_max=524288`; reduce TIME_WAIT: `sysctl -w net.netfilter.nf_conntrack_tcp_timeout_time_wait=30`; use connection pooling in schedulers |
| Kernel panic on YARN NodeManager during Tez execution | All Tez containers on affected NM killed; Hive queries fail mid-execution; YARN shows node as `LOST` | Kernel bug triggered by heavy page cache pressure during Tez shuffle I/O; known issues with certain kernel versions and cgroup v2 | `journalctl -k -p 0 --since "1 hour ago"` on recovered NM; `yarn node -list -states LOST`; check `/var/log/kern.log` for panic trace | Update kernel: `yum update kernel`; enable kdump: `systemctl enable kdump`; limit Tez container memory to prevent OOM-triggered kernel bugs: `tez.container.max.java.heap.fraction=0.6` |
| NUMA imbalance causing HS2 GC pause variance | HS2 GC pause times vary between 50ms and 2s; intermittent query timeouts during GC; some HS2 instances worse than others on same hardware | HS2 JVM allocated memory across NUMA nodes; GC scanning remote NUMA memory 3x slower; pause times proportional to remote memory fraction | `numastat -p $(pgrep -f HiveServer2)`; `numactl --hardware`; GC logs: `grep "Pause Young\|Pause Full" /var/log/hive/hiveserver2-gc.log` | Start HS2 with NUMA binding: `numactl --cpunodebind=0 --membind=0 hive --service hiveserver2`; add JVM `-XX:+UseNUMA`; ensure JVM heap fits within single NUMA node |

## Deployment Pipeline & GitOps Failure Patterns

| Failure Mode | Symptom | Root Cause | Diagnostic Commands | Remediation |
|-------------|---------|------------|--------------------:|-------------|
| Image pull failure for Hive container on Kubernetes | HS2 pod stuck in `ImagePullBackOff`; Hive queries routed to remaining HS2 instances; capacity degraded | Docker Hub rate limit or private registry token expired for Hive Docker image | `kubectl describe pod <HS2_POD> \| grep -A5 "Events:"`; `kubectl get events --field-selector reason=Failed \| grep image` | Add `imagePullSecrets` to HS2 Deployment; use private registry mirror; pre-pull: `docker pull <registry>/hive:<tag>` on all nodes |
| Hive container registry auth failure after credential rotation | HS2 pods cannot restart: `unauthorized: authentication required`; existing pods healthy but no scaling possible | Kubernetes imagePullSecret rotated but HS2 Deployment references old secret; new secret name not updated | `kubectl get secret -n hive <SECRET> -o jsonpath='{.data.\.dockerconfigjson}' \| base64 -d \| jq '.auths'`; `kubectl describe pod <POD> \| grep "Failed to pull"` | Refresh secret: `kubectl create secret docker-registry hive-registry --docker-server=<REG> --docker-username=<USER> --docker-password=<PASS> -n hive --dry-run=client -o yaml \| kubectl apply -f -` |
| Helm drift between Hive chart and live cluster state | `helm upgrade` fails: `invalid ownership metadata`; hive-site.xml ConfigMap was manually edited for emergency fix | Operator ran `kubectl edit configmap hive-config` to change `hive.exec.parallel=true` during incident; Helm not aware of change | `helm get manifest hive -n hive \| kubectl diff -f -`; `helm status hive -n hive`; `kubectl get configmap hive-config -o yaml` | Adopt resource: `kubectl annotate configmap hive-config meta.helm.sh/release-name=hive --overwrite`; merge manual fix into Helm values.yaml |
| ArgoCD sync stuck during Hive HMS database migration | ArgoCD Application shows `Syncing` for 30+ min; HMS schema upgrade Job running but not completing; HS2 pods not started | ArgoCD sync hook for `schematool -dbType <db> -upgradeSchema` running against large HMS DB; timeout exceeded; dependent HS2 pods waiting | `argocd app get hive --grpc-web`; `kubectl get jobs -n hive \| grep schema`; `kubectl logs job/hive-schema-upgrade -n hive` | Increase ArgoCD sync timeout: `argocd app set hive --sync-option Timeout=1800`; or run schema migration as separate pipeline before ArgoCD sync |
| PDB blocking Hive HS2 rolling update | HS2 Deployment rollout hangs; old HS2 pods still running; new pods cannot schedule because PDB prevents old pod eviction | PDB `minAvailable: 2` with 2 replicas means 0 disruptions allowed; rolling update deadlocked | `kubectl get pdb -n hive`; `kubectl describe pdb hive-hs2-pdb \| grep "Allowed disruptions: 0"` | Adjust PDB: `kubectl patch pdb hive-hs2-pdb -n hive -p '{"spec":{"minAvailable":1}}'`; or scale HS2 to 3 replicas before rolling update |
| Blue-green cutover failure for Hive Metastore migration | Green HS2 cluster connects to new HMS DB but metadata tables incomplete; queries fail with `MetaException: no such table` | HMS DB migration to green environment incomplete; `schematool -upgradeSchema` partially applied; cutover triggered prematurely | `beeline -e "SHOW DATABASES" \| wc -l` on green vs blue; `hive --service schematool -dbType mysql -validate` on green HMS DB | Rollback to blue: update HS2 `javax.jdo.option.ConnectionURL` to blue HMS DB; complete green migration: `hive --service schematool -dbType mysql -upgradeSchema`; validate before cutover |
| ConfigMap drift causing HS2 config mismatch after partial rollout | Some HS2 pods using old hive-site.xml; others using new; inconsistent query behavior (e.g., different vectorization settings) | ConfigMap updated but HS2 pods not restarted; Kubernetes does not auto-restart pods on ConfigMap change | `kubectl get configmap hive-config -n hive -o yaml \| grep "hive.vectorized.execution.enabled"`; `kubectl exec <HS2_POD> -- cat /opt/hive/conf/hive-site.xml \| grep "vectorized"` | Add ConfigMap hash annotation: `checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . \| sha256sum }}`; or restart: `kubectl rollout restart deployment hiveserver2 -n hive` |
| Feature flag enabling Hive LLAP prematurely causing resource contention | LLAP daemons consume all YARN cluster memory; batch Tez queries starved; ETL pipelines fail with `AMRejected` | `hive.llap.execution.mode=all` set in ConfigMap without provisioning dedicated LLAP queue; LLAP daemons consume default queue capacity | `yarn queue -status default`; `beeline -e "SET hive.llap.execution.mode"`; `yarn application -list \| grep llap` | Disable LLAP: set `hive.llap.execution.mode=none` in ConfigMap; restart HS2; create dedicated YARN queue for LLAP in `capacity-scheduler.xml` and run `yarn rmadmin -refreshQueues` before re-enabling |

## Service Mesh & API Gateway Edge Cases

| Failure Mode | Symptom | Root Cause | Diagnostic Commands | Remediation |
|-------------|---------|------------|--------------------:|-------------|
| Circuit breaker false positive on Hive Metastore | HS2 queries fail with `MetaException`; Envoy returning `503 UO` for HMS Thrift calls; HMS process is healthy | Envoy outlier detection trips on slow HMS metadata operations (table with 10K+ partitions takes >5s); marks HMS as unhealthy | `istioctl proxy-config cluster <HS2_POD>.hive \| grep hive-metastore`; `kubectl exec <HS2_POD> -c istio-proxy -- pilot-agent request GET /stats \| grep outlier_detection` | Increase outlier detection thresholds for HMS: `DestinationRule` with `outlierDetection.consecutiveGatewayErrors: 10` and `interval: 60s`; exclude partition-heavy queries from circuit breaker |
| Rate limiting on API gateway blocking Hive JDBC connections | Beeline/JDBC clients receive `429 Too Many Requests` from gateway; Airflow DAGs fail connecting to HS2; query backlog grows | API gateway rate limit counts each JDBC connection attempt; Airflow opens many connections simultaneously during DAG execution burst | `kubectl logs -n istio-system <INGRESS_GW_POD> \| grep "429.*hive\|hs2"`; `beeline -u "jdbc:hive2://<GATEWAY>:10000" -e "SELECT 1" 2>&1 \| grep 429` | Create separate rate limit for Hive JDBC path with higher limit; whitelist Airflow pod IPs from rate limiting; use Hive connection pooling in Airflow: `hive.max_connections=10` |
| Stale service discovery endpoints for HMS | HS2 connects to decommissioned HMS instance; metadata queries fail with connection timeout; succeeds on HS2 restart | Kubernetes endpoint for HMS pod removed but HS2 cached old HMS address via ZooKeeper service discovery | `kubectl get endpoints -n hive hive-metastore-svc`; `beeline -e "SET hive.metastore.uris"` — check configured URIs; `echo "ls /hiveserver2" \| hbase zkcli` | Configure HMS with ZooKeeper discovery: `hive.metastore.uris.selection=RANDOM`; set `hive.metastore.client.socket.timeout=10` for faster failover; add HS2 health check that validates HMS connectivity |
| mTLS certificate rotation interrupting HS2-to-HMS communication | HS2 intermittently fails to connect to HMS: `SSLHandshakeException`; queries compiled from cache succeed; new table references fail | Istio mTLS cert rotation on HS2 pod completes before HMS pod; brief window where certs don't match | `istioctl proxy-config secret -n hive <HS2_POD> \| grep "VALID\|EXPIRE"`; `kubectl logs <HS2_POD> -c istio-proxy \| grep "ssl\|handshake"` | Extend cert overlap window: `PILOT_CERT_ROTATION_GRACE_PERIOD_RATIO=0.5`; configure HS2 retry on HMS connection failure: `hive.metastore.failure.retries=5` |
| Retry storm from Hive JDBC clients amplifying through mesh | HS2 overloaded; Envoy sidecar adds retries on top of JDBC driver retries; HS2 thread pool exhausted; all queries fail | Beeline JDBC driver retries 3x on connection failure; Istio VirtualService adds 2 retries; 6x amplification per client | `kubectl exec <HS2_POD> -c istio-proxy -- pilot-agent request GET /stats \| grep "upstream_rq_retry"`; `beeline -e "SET hive.server2.thrift.max.worker.threads"` | Disable mesh retries for Hive: `VirtualService` with `retries.attempts: 0` for HS2 service; rely on JDBC connection pool retry only |
| gRPC keepalive mismatch on Hive LLAP daemon communication | LLAP query fragments disconnected mid-execution: `UNAVAILABLE: keepalive watchdog timeout`; query fails after 60s idle | Envoy gRPC keepalive timeout shorter than LLAP fragment execution time; Envoy kills idle-appearing gRPC stream to LLAP daemon | `kubectl exec <HS2_POD> -c istio-proxy -- pilot-agent request GET /stats \| grep keepalive`; `kubectl logs <HS2_POD> \| grep "UNAVAILABLE\|keepalive\|LLAP"` | Set Envoy keepalive for LLAP: `EnvoyFilter` with `connection_keepalive.interval: 300s`; configure LLAP heartbeat: `hive.llap.daemon.rpc.keepalive.interval.ms=30000` |
| Trace context propagation lost between Hive and Tez AM | Distributed trace shows gap between HS2 query submission and Tez DAG execution; cannot correlate slow queries to specific Tez tasks | HS2 submits Tez DAG via YARN; trace context not propagated through YARN application submission; Tez AM starts new trace | `curl "http://jaeger:16686/api/traces?service=hiveserver2&limit=5" \| jq '.data[].spans \| length'`; check for disconnected Tez traces | Instrument Tez AM with OpenTelemetry agent: add `-javaagent:opentelemetry-javaagent.jar` to `tez.am.launch.cmd-opts`; propagate trace ID via Tez DAG configuration: `tez.queue.name` as carrier |
| Load balancer health check fails on HS2 behind gateway | ALB removes HS2 from target group; JDBC connections fail with `502`; HS2 process is healthy and serving queries | HS2 HTTP health endpoint `/` returns HTML page >500KB; ALB health check times out parsing large response body | `curl -s -o /dev/null -w "%{time_total}" "http://<HS2>:10002/"`; `aws elbv2 describe-target-health --target-group-arn <ARN>` | Change health check to lightweight Thrift port probe: `aws elbv2 modify-target-group --target-group-arn <ARN> --health-check-port 10000 --health-check-protocol TCP` |
