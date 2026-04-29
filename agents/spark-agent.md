---
name: spark-agent
description: >
  Apache Spark specialist agent. Handles distributed compute operations,
  executor management, shuffle optimization, Structured Streaming, dynamic
  allocation, and Spark SQL tuning.
model: sonnet
color: "#E25A1C"
skills:
  - spark/spark
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-spark-agent
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

You are the Spark Agent — the distributed compute expert. When any alert
involves Spark (executor failures, shuffle issues, streaming lag, memory
pressure, job failures), you are dispatched.

# Activation Triggers

- Alert tags contain `spark`, `executor`, `shuffle`, `streaming`
- Executor count drops unexpectedly
- Stage or job failures detected
- Disk spill exceeding thresholds
- Structured Streaming processing lag
- GC pressure on executors
- Shuffle fetch failures
- Application stuck with no progress

---

## Key Metrics Reference

Metrics are exposed via JMX (object name format shown), Prometheus servlet
(`/metrics/prometheus/`), or the Spark REST API (`/api/v1/`). Enable with:

```properties
# conf/metrics.properties
*.sink.jmx.class=org.apache.spark.metrics.sink.JmxSink
*.sink.prometheus.class=org.apache.spark.metrics.sink.PrometheusServlet
*.sink.prometheus.path=/metrics/prometheus
driver.source.jvm.class=org.apache.spark.metrics.source.JvmSource
executor.source.jvm.class=org.apache.spark.metrics.source.JvmSource
```

### DAGScheduler Metrics (Scope: driver)

| Metric Name (JMX key) | Type | Description | Alert Threshold |
|---|---|---|---|
| `DAGScheduler.job.activeJobs` | Gauge | Currently active Spark jobs | 0 when work expected → stuck app |
| `DAGScheduler.job.allJobs` | Gauge | Total jobs submitted since app start | Monitor growth rate |
| `DAGScheduler.stage.waitingStages` | Gauge | Stages waiting to be submitted | >10 sustained → resource contention |
| `DAGScheduler.stage.runningStages` | Gauge | Stages currently executing | 0 when job active → scheduler stall |
| `DAGScheduler.stage.failedStages` | Gauge | Failed stages count | >0 → investigate |
| `DAGScheduler.messageProcessingTime` | Timer | Time to process DAGScheduler messages | p99 > 1000 ms → scheduler overhead |

**JMX object name:** `metrics:<app-id>.driver.DAGScheduler:name=<metric>`

### BlockManager Memory Metrics (Scope: driver)

| Metric Name (JMX key) | Type | Description | Alert Threshold |
|---|---|---|---|
| `BlockManager.memory.maxMem_MB` | Gauge | Total memory available for storage (MB) | Reference |
| `BlockManager.memory.memUsed_MB` | Gauge | Storage memory in use (MB) | >90% of maxMem_MB → eviction pressure |
| `BlockManager.memory.remainingMem_MB` | Gauge | Free storage memory (MB) | <10% of maxMem_MB → CRITICAL |
| `BlockManager.memory.maxOnHeapMem_MB` | Gauge | Max on-heap storage memory (MB) | Reference |
| `BlockManager.memory.onHeapMemUsed_MB` | Gauge | On-heap storage memory in use (MB) | >90% → WARNING |
| `BlockManager.memory.maxOffHeapMem_MB` | Gauge | Max off-heap storage memory (MB) | Reference |
| `BlockManager.memory.offHeapMemUsed_MB` | Gauge | Off-heap storage memory in use (MB) | >90% → WARNING |
| `BlockManager.disk.diskSpaceUsed_MB` | Gauge | Disk space for RDD storage (MB) | Unbounded growth → cache leak |

**JMX object name:** `metrics:<app-id>.driver.BlockManager:name=<metric>`

### Executor Task Execution Metrics (Scope: executor — per-executor JMX)

| Metric Name (JMX key) | Type | Description | Alert Threshold |
|---|---|---|---|
| `executor.succeededTasks` | Counter | Successfully completed tasks | Monitor rate |
| `executor.jvmGCTime` | Counter | Cumulative JVM GC time (ms) | jvmGCTime/runTime > 10% → WARNING |
| `executor.runTime` | Counter | Cumulative task execution time (ms) | Basis for GC ratio |
| `executor.cpuTime` | Counter | Cumulative CPU time (ns) | cpuTime/runTime < 0.5 → I/O bound |
| `executor.deserializeTime` | Counter | Task deserialization time (ms) | High ratio → large task binaries |
| `executor.resultSerializationTime` | Counter | Task result serialization time (ms) | High ratio → large result objects |

**JMX object name:** `metrics:<app-id>.<executor-id>.executor:name=<metric>`

### Executor JVM / Memory Metrics (Scope: ExecutorMetrics — via REST `executors` endpoint)

| Metric Name | Type | Description | Alert Threshold |
|---|---|---|---|
| `JVMHeapMemory` | Gauge | Peak JVM heap memory (bytes) | >90% of `--executor-memory` → OOM risk |
| `JVMOffHeapMemory` | Gauge | Peak JVM off-heap memory (bytes) | >300 MB unexpectedly → native leak |
| `OnHeapExecutionMemory` | Gauge | Peak on-heap execution memory (bytes) | Approaching `spark.memory.fraction` limit |
| `OffHeapExecutionMemory` | Gauge | Peak off-heap execution memory (bytes) | Monitor when off-heap enabled |
| `OnHeapStorageMemory` | Gauge | Peak on-heap storage/cache memory (bytes) | Monitor cache size |
| `DirectPoolMemory` | Gauge | Peak direct buffer pool memory (bytes) | >heap_size * 0.3 → WARNING |
| `MinorGCCount` | Gauge | Total minor GC collections | Rate increase → allocation pressure |
| `MinorGCTime` | Gauge | Total minor GC time (ms) | Track with MajorGCTime |
| `MajorGCCount` | Gauge | Total major GC (full GC) count | >2/min → severe GC pressure |
| `MajorGCTime` | Gauge | Total major GC time (ms) | >10% of runTime → GC-dominated |

### Executor Shuffle Metrics (Scope: executor)

| Metric Name (JMX key) | Type | Description | Alert Threshold |
|---|---|---|---|
| `executor.shuffleRemoteBlocksFetched` | Counter | Remote shuffle blocks fetched | remoteBlocks >> localBlocks → data locality issue |
| `executor.shuffleLocalBlocksFetched` | Counter | Local shuffle blocks fetched | Should dominate |
| `executor.shuffleRemoteBytesRead` | Counter | Bytes read from remote shuffle (bytes) | Spike → heavy remote shuffle |
| `executor.shuffleRemoteBytesReadToDisk` | Counter | Remote shuffle bytes spilled to disk (bytes) | >10% of total → disk spill in shuffle |
| `executor.shuffleFetchWaitTime` | Counter | Total shuffle block fetch wait time (ms) | >5 s per task → network bottleneck |
| `executor.shuffleBytesWritten` | Counter | Bytes written in shuffle (bytes) | Unusually large → partitioning issue |
| `executor.shuffleWriteTime` | Counter | Shuffle write time (ns) | High ratio to runTime → I/O bound |

### Structured Streaming Metrics (Scope: driver — requires `spark.sql.streaming.metricsEnabled=true`)

| Metric Name (JMX key) | Type | Description | Alert Threshold |
|---|---|---|---|
| `streaming.<query-name>.inputRate-total` | Gauge | Records/sec arriving from all sources | Drop to 0 → source stalled |
| `streaming.<query-name>.processingRate-total` | Gauge | Records/sec processed | processingRate < inputRate → backlog growing |
| `streaming.<query-name>.latency` | Gauge | End-to-end trigger latency (ms) | >2× trigger interval → falling behind |
| `streaming.<query-name>.eventTime-watermark` | Gauge | Current event-time watermark (epoch ms) | Stalled for >5 min → idle partition |
| `streaming.<query-name>.states-rowsTotal` | Gauge | Rows in state store | Unbounded growth → missing state cleanup |
| `streaming.<query-name>.states-usedBytes` | Gauge | State store memory (bytes) | Growing without bound → memory leak |

**JMX object name:** `metrics:<app-id>.driver.spark.streaming.<query-name>:name=<metric>`

### LiveListenerBus Metrics (Scope: driver)

| Metric Name (JMX key) | Type | Description | Alert Threshold |
|---|---|---|---|
| `LiveListenerBus.queue.appStatus.numDroppedEvents` | Counter | Events dropped from appStatus queue | >0 → increase `spark.scheduler.listenerbus.eventqueue.appStatus.capacity` |
| `LiveListenerBus.queue.eventLog.numDroppedEvents` | Counter | Events dropped from event-log queue | >0 → event log writer too slow |
| `LiveListenerBus.queue.appStatus.size` | Gauge | Current appStatus queue depth | >10 000 → listener bottleneck |

---

## PromQL Expressions (Prometheus sink)

```promql
# Stage failure alert
metrics_DAGScheduler_stage_failedStages{app_id=~"application_.*"} > 0

# Waiting stages saturation
metrics_DAGScheduler_stage_waitingStages{app_id=~"application_.*"} > 10

# BlockManager memory pressure (remaining < 10% of max)
(metrics_BlockManager_memory_remainingMem_MB
  / metrics_BlockManager_memory_maxMem_MB) < 0.10

# Streaming processing rate behind input rate (backlog growing)
metrics_streaming_processingRate_total < metrics_streaming_inputRate_total

# Streaming latency > 2× trigger interval (fill in batch_ms)
metrics_streaming_latency > <batch_ms> * 2

# GC time ratio > 10% (computed from executor metrics)
rate(metrics_executor_jvmGCTime_total[5m])
  / rate(metrics_executor_runTime_total[5m]) > 0.10

# Dropped listener events (should always be 0)
increase(metrics_LiveListenerBus_queue_appStatus_numDroppedEvents_total[5m]) > 0
```

---

## Cluster Visibility

```bash
# List YARN Spark applications
yarn application -list -appTypes SPARK -appStates RUNNING,ACCEPTED,FAILED

# Spark History Server — completed applications
curl -s http://<history-server>:18080/api/v1/applications | python3 -m json.tool

# Per-application stage summary
curl -s http://<history-server>:18080/api/v1/applications/<app-id>/stages | python3 -m json.tool

# Spark on Kubernetes — executor pods
kubectl get pods -n spark-apps -l spark-role=executor --sort-by=.status.startTime

# Structured Streaming query status (live driver UI)
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/streaming/statistics

# Executor list with memory and GC per executor
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/executors | python3 -m json.tool

# Prometheus metrics endpoint on live driver
curl -s http://<driver-host>:4040/metrics/prometheus/

# YARN ResourceManager queue
yarn queue -status spark-queue

# Web UI key pages
# Spark UI (live):     http://<driver-host>:4040/
# History Server:      http://<history-server>:18080/
# YARN RM:             http://<rm-host>:8088/cluster/apps/RUNNING
```

---

## Global Diagnosis Protocol

**Step 1: Infrastructure health**
```bash
# Executor count
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/executors | python3 -c "
import sys, json
exs = json.load(sys.stdin)
active = [e for e in exs if not e.get('isBlacklisted') and e['id'] != 'driver']
print(f'Active executors: {len(active)}')
"
# YARN: NodeManagers healthy
yarn node -list -all | grep -c RUNNING
# Kubernetes: pending executor pods
kubectl get pods -n spark-apps | grep Pending
```

**Step 2: Job/workload health**
```bash
# Jobs and their status
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/jobs | python3 -c "
import sys, json
[print(j['jobId'], j['status'], 'stages:', j['numActiveTasks']) for j in json.load(sys.stdin)]
"
# Failed stages
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages?status=FAILED" | python3 -m json.tool
# Streaming: processing vs batch interval
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/streaming/statistics | python3 -c "
import sys, json
s = json.load(sys.stdin)
print('Avg proc ms:', s.get('avgProcessingTime'), '| Batch ms:', s.get('batchDuration'))
"
```

**Step 3: Resource utilization**
```bash
# Disk spill per stage
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/stages | python3 -c "
import sys, json
for s in json.load(sys.stdin):
    if s.get('diskBytesSpilled', 0) > 0:
        print('Stage', s['stageId'], 'diskSpill MB:', round(s['diskBytesSpilled']/1e6, 1))
"
# GC % per executor
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/executors | python3 -c "
import sys, json
for e in json.load(sys.stdin):
    dur = max(e.get('totalDuration', 1), 1)
    gc_pct = round(e.get('totalGCTime', 0) * 100 / dur, 1)
    print('Executor', e['id'], 'GC%:', gc_pct)
"
```

**Step 4: Data pipeline health**
```bash
# Last 5 Structured Streaming batches
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/streaming/batches" | python3 -c "
import sys, json
for b in json.load(sys.stdin)[:5]:
    print('Batch', b['batchId'], 'procMs:', b.get('processingTime'), 'schedDelayMs:', b.get('schedulingDelay'))
"
# Checkpoint directory accessible
hdfs dfs -ls hdfs://<checkpoint-path>/
```

**Severity:**
- CRITICAL: all executors lost, streaming stopped (processingRate = 0), repeated stage failures, driver OOM
- WARNING: disk spill > 10 GB, GC time > 20% of task time, streaming latency > 2× batch interval, executor count < 50% expected
- OK: executors stable, spill = 0, streaming processing time < batch interval, GC < 5%

---

## Diagnostic Scenario 1: Executor OOM / Repeated Executor Loss

**Symptom:** Executors repeatedly crash; `JVMHeapMemory` at max; tasks fail with `java.lang.OutOfMemoryError`.

**Step 1 — Confirm OOM as root cause:**
```bash
# YARN executor logs
yarn logs -applicationId application_<id> 2>/dev/null | grep -E "(OutOfMemoryError|GC overhead|Executor lost)" | head -20
# K8s executor logs
kubectl logs -n spark-apps spark-<app-id>-exec-<n> --tail=200 | grep -E "(OOM|OutOfMemory|Exception)"
# REST: executor memory snapshot
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/executors | python3 -c "
import sys, json
for e in json.load(sys.stdin):
    if e.get('maxMemory', 0) > 0:
        used_pct = round(e.get('memoryUsed', 0) * 100 / e['maxMemory'], 1)
        print('Executor', e['id'], 'used%:', used_pct, 'maxGB:', round(e['maxMemory']/1e9, 2))
"
```

**Step 2 — Identify what is consuming memory (cache vs execution):**
```bash
# BlockManager storage from driver JMX / REST
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/storage/rdd | python3 -m json.tool
# Stages with high peak execution memory
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/stages | python3 -c "
import sys, json
for s in sorted(json.load(sys.stdin), key=lambda x: x.get('peakExecutionMemory',0), reverse=True)[:5]:
    print('Stage', s['stageId'], 'peakExecMem MB:', round(s.get('peakExecutionMemory',0)/1e6, 1))
"
```

**Step 3 — Remediation:**
```bash
# Increase executor heap
# spark-submit --executor-memory 12g

# Reduce storage fraction to give more room to execution
# --conf spark.memory.storageFraction=0.2

# Enable off-heap to avoid GC on large datasets
# --conf spark.memory.offHeap.enabled=true --conf spark.memory.offHeap.size=4g

# For join-heavy workloads, reduce shuffle partitions and enable spill
# --conf spark.sql.shuffle.partitions=400
# --conf spark.memory.fraction=0.75
```

---

## Diagnostic Scenario 2: Data Skew / Long-Tail Tasks

**Symptom:** One task in a stage takes 10× longer than median; `stage.waitingStages` counts are high because of stragglers.

**Step 1 — Measure task duration variance:**
```bash
STAGE_ID=<id>
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages/$STAGE_ID/taskList?sortBy=-duration" | python3 -c "
import sys, json
tasks = json.load(sys.stdin)
durs = [t.get('taskMetrics', {}).get('executorRunTime', 0) for t in tasks if 'taskMetrics' in t]
if durs:
    print('min:', min(durs), 'ms | median:', sorted(durs)[len(durs)//2], 'ms | max:', max(durs), 'ms')
    print('Skew ratio (max/median):', round(max(durs) / max(sorted(durs)[len(durs)//2], 1), 1))
"
```

**Step 2 — Identify the skewed key:**
```bash
# Look at input/output records per task to spot the fat partition
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages/$STAGE_ID/taskList" | python3 -c "
import sys, json
for t in sorted(json.load(sys.stdin), key=lambda x: x.get('taskMetrics',{}).get('inputMetrics',{}).get('recordsRead',0), reverse=True)[:5]:
    m = t.get('taskMetrics', {})
    print('Task', t['taskId'], 'records:', m.get('inputMetrics',{}).get('recordsRead',0),
          'runTimeMs:', m.get('executorRunTime',0))
"
```

**Step 3 — Remediation:**
```bash
# Enable AQE skew join handling (Spark 3.x)
# --conf spark.sql.adaptive.enabled=true
# --conf spark.sql.adaptive.skewJoin.enabled=true
# --conf spark.sql.adaptive.skewJoin.skewedPartitionThresholdInBytes=256mb

# Manual salting for known skewed keys (add random salt prefix to join key)
# Increase parallelism to split large partitions
# --conf spark.sql.shuffle.partitions=1000

# Broadcast the smaller table to avoid shuffle entirely
# spark.sql.autoBroadcastJoinThreshold=100mb
```

---

## Diagnostic Scenario 3: Shuffle Fetch Failure / FetchFailed Exception

**Symptom:** Stage retries with `FetchFailed` exceptions; `shuffleFetchWaitTime` spikes; executor logs show `Connection refused` to shuffle service.

**Step 1 — Confirm FetchFailed root cause:**
```bash
yarn logs -applicationId application_<id> 2>/dev/null | \
  grep -iE "(FetchFailed|shuffle read|Connection refused|lost executor)" | head -30
# K8s
kubectl logs -n spark-apps spark-<app-id>-exec-<n> | grep -i "FetchFailed"
```

**Step 2 — Check if the source executor for those blocks is still alive:**
```bash
# Failed stage will show which executor hosted the missing blocks
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages?status=FAILED" | python3 -m json.tool | grep -E "(executorId|failureReason)"
# If that executor is gone, shuffle data was lost → increase replication or use shuffle service
```

**Step 3 — Remediation:**
```bash
# Increase retry count and backoff
# --conf spark.shuffle.io.maxRetries=10
# --conf spark.shuffle.io.retryWait=30s

# Enable external shuffle service to decouple shuffle data from executor lifecycle (YARN)
# --conf spark.shuffle.service.enabled=true
# (requires yarn-site.xml: spark_shuffle aux service)

# For K8s: use remote shuffle service (e.g., Uniffle/RSS) or increase executor replicas
# --conf spark.kubernetes.executor.volumes.persistentVolumeClaim...

# Increase network timeout for slow storage backends
# --conf spark.network.timeout=600s
```

---

## Diagnostic Scenario 4: Structured Streaming Backlog Growing

**Symptom:** `processingRate-total < inputRate-total`; trigger processing time > batch interval; consumer lag visible in Kafka.

**Step 1 — Measure processing vs arrival rate:**
```bash
curl -s http://<driver-host>:4040/api/v1/applications/<app-id>/streaming/statistics | python3 -c "
import sys, json
s = json.load(sys.stdin)
print('Input rate (rec/s):', s.get('avgInputRate'))
print('Processing rate (rec/s):', s.get('avgProcessingRate'))
print('Avg processing time (ms):', s.get('avgProcessingTime'))
print('Batch duration (ms):', s.get('batchDuration'))
"
# Recent batch latencies
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/streaming/batches" | python3 -c "
import sys, json
for b in json.load(sys.stdin)[:10]:
    print('Batch', b['batchId'],
          'input:', b.get('numInputRows'),
          'procMs:', b.get('processingTime'),
          'schedDelayMs:', b.get('schedulingDelay'))
"
```

**Step 2 — Check state store growth (stateful queries):**
```bash
# JMX via prometheus: states-usedBytes growing unbounded → missing state expiry
curl -s http://<driver-host>:4040/metrics/prometheus/ | grep -E "streaming.*states"
```

**Step 3 — Kafka source: check per-partition lag:**
```bash
# If using Kafka source with readStream
kafka-consumer-groups.sh --bootstrap-server <broker>:9092 \
  --describe --group spark-<app-id>-<query-name>
```

**Step 4 — Remediation:**
```bash
# Increase maxOffsetsPerTrigger to process more per batch
# .option("maxOffsetsPerTrigger", 500000)

# Increase shuffle parallelism for complex stateful operations
# --conf spark.sql.shuffle.partitions=200

# Add watermark + state TTL to bound state store size
# .withWatermark("eventTime", "10 minutes")
# .stateTimeout(GroupStateTimeout.EventTimeTimeout())

# For corrupt checkpoint: reset by pointing to new checkpoint path
# .option("checkpointLocation", "/new/path")
```

---

## Diagnostic Scenario 5: Driver OOM from collect() on Large Result Set

**Symptom:** Spark application fails with `java.lang.OutOfMemoryError: Java heap space` on the driver; stack trace shows `collect()`, `toPandas()`, or `show(n)` with large n; driver JVM heap metric at 100%.

**Root Cause Decision Tree:**
- `collect()` called on a large RDD/DataFrame → entire dataset materialized in driver JVM heap
- `show(n)` called with very large n, or `take(n)` on unfiltered large table → pulls rows to driver
- Driver heap configured too small for the application's result size (`--driver-memory` default 1g)
- Broadcast join variable exceeds `spark.driver.maxResultSize` → broadcast serialization OOM on driver

**Diagnosis:**
```bash
# Driver executor memory in executor list (driver is executor 'driver')
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/executors" | python3 -c "
import sys, json
for e in json.load(sys.stdin):
    if e.get('id') == 'driver':
        print('Driver maxMem:', e.get('maxMemory'), 'used:', e.get('memoryUsed'))
        print('Driver GC time ms:', e.get('totalGCTime'))
"
# Failed job / stage details
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/jobs?status=FAILED" | python3 -m json.tool
# Driver stdout/stderr (YARN)
yarn logs -applicationId application_<id> -log_files stdout 2>/dev/null | grep -E "(OOM|OutOfMemory|heap)" | head -20
# Driver stdout/stderr (K8s)
kubectl logs -n spark-apps <spark-driver-pod> --tail=100 | grep -E "(OOM|OutOfMemory|Exception)"
# Check result size limit
# spark.driver.maxResultSize default is 1g — if exceeded, action fails with different error
```

**Thresholds:**
- WARNING: driver JVM heap > 85% of `--driver-memory`
- CRITICAL: driver OOM crash; application terminates

## Diagnostic Scenario 6: Shuffle Service Disk Pressure from Large Intermediate Data

**Symptom:** Executor tasks fail with `DiskSpaceManager: No space left`; stage retries exhausted; `diskBytesSpilled` very large in stage metrics; NodeManager disk alerts firing.

**Root Cause Decision Tree:**
- Insufficient local disk on NodeManager/worker nodes → shuffle write fills disk before data is consumed
- Too many shuffle partitions all writing simultaneously → burst disk usage exceeds capacity
- Shuffle data not being cleaned up from previous stages → orphaned shuffle files accumulating
- External shuffle service `leveldb` metadata directory on small volume → metadata fills disk even if data is small

**Diagnosis:**
```bash
# Stages with high disk spill
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages" | python3 -c "
import sys, json
stages = sorted(json.load(sys.stdin), key=lambda s: s.get('diskBytesSpilled', 0), reverse=True)
for s in stages[:10]:
    spill_gb = round(s.get('diskBytesSpilled', 0) / 1e9, 2)
    if spill_gb > 0:
        print('Stage', s['stageId'], 'diskSpill GB:', spill_gb,
              'shuffleWrite GB:', round(s.get('shuffleWriteBytes', 0)/1e9, 2),
              'partitions:', s.get('numTasks'))
"
# Disk usage on worker/NodeManager nodes
# YARN:
yarn node -list -all | awk '{print $1}' | grep node | \
  xargs -I{} yarn node -status {} 2>/dev/null | grep -E "(Node|Used|Available|Health)"

# K8s: check executor pod disk
kubectl exec -n spark-apps spark-<app-id>-exec-1 -- df -h /tmp 2>/dev/null

# External shuffle service directory
ssh <nodemanager-host> "df -h /data/spark/; du -sh /data/spark/shuffle/ 2>/dev/null"

# Shuffle partition count (high count = more files, more inodes)
# Check spark.sql.shuffle.partitions in application config
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" | python3 -c "
import sys, json
env = json.load(sys.stdin)
for k, v in env.get('sparkProperties', []):
    if 'shuffle' in k.lower() and 'partition' in k.lower():
        print(k, '=', v)
"
```

**Thresholds:**
- WARNING: `diskBytesSpilled` > 10 GB per stage; worker disk > 80% full
- CRITICAL: worker disk > 95% full; stage failing with `No space left on device`

## Diagnostic Scenario 7: Stage Retry Storm from Flaky Executor

**Symptom:** Application makes no progress; `DAGScheduler.stage.failedStages` incrementing repeatedly; same stage retrying > 4 times; specific executor repeatedly losing tasks; `speculation` not helping.

**Root Cause Decision Tree:**
- Single executor on a degraded node → hardware issue (bad disk, memory ECC errors) causing persistent task failures
- Task failure limit exceeded on one executor → Spark blacklists executor but respawns it on same node
- Executor blacklisting disabled → same bad executor keeps getting tasks assigned
- GC storm on specific executor → tasks time out but executor stays alive; appears as task failure

**Diagnosis:**
```bash
# Failed stage details and which executor is failing
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages?status=FAILED" | python3 -c "
import sys, json
for s in json.load(sys.stdin)[:10]:
    print('Stage:', s.get('stageId'), 'attempt:', s.get('attemptId'))
    print('Failure reason:', s.get('failureReason', '')[:300])
    print('Failed tasks:', s.get('numFailedTasks'))
"
# Executor failure rates
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/executors" | python3 -c "
import sys, json
for e in json.load(sys.stdin):
    if e.get('id') != 'driver':
        failed = e.get('failedTasks', 0)
        total = max(e.get('completedTasks', 0) + failed, 1)
        if failed > 0:
            print('Executor', e['id'], 'host:', e.get('hostPort','?')[:30],
                  'failedTasks:', failed, 'failRate:', round(failed*100/total, 1), '%',
                  'blacklisted:', e.get('isBlacklisted', False))
"
# Task failure details for the problematic stage
STAGE_ID=<id>
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages/$STAGE_ID/taskList?status=FAILED" | python3 -c "
import sys, json
for t in json.load(sys.stdin)[:10]:
    print('Task', t['taskId'], 'executor:', t.get('executorId'),
          'reason:', str(t.get('taskMetrics',{}) or t.get('errorMessage',''))[:200])
"
```

**Thresholds:**
- WARNING: stage retry count > 2; executor task failure rate > 10%
- CRITICAL: stage failing > 4 attempts (Spark application will abort); executor blacklisted

## Diagnostic Scenario 8: Structured Streaming Checkpoint Lag Behind Watermark

**Symptom:** `streaming.<query>.eventTime-watermark` metric stalls or moves backward; `states-rowsTotal` grows unbounded; late data accumulating; output missing records expected to be complete.

**Root Cause Decision Tree:**
- One Kafka partition has no new events → watermark cannot advance past idle partition; all partitions block watermark
- State store checkpoint write is slow → trigger latency increases; watermark appears stalled
- `withWatermark` delay too conservative → valid records held in state much longer than needed
- HDFS/S3 checkpoint directory throttled → slow checkpoint writes cause trigger backpressure

**Diagnosis:**
```bash
# Streaming query status including watermark
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/streaming/statistics" | python3 -c "
import sys, json
s = json.load(sys.stdin)
print('Input rate (rec/s):', s.get('avgInputRate'))
print('Processing rate (rec/s):', s.get('avgProcessingRate'))
print('Avg batch proc time (ms):', s.get('avgProcessingTime'))
print('Avg scheduling delay (ms):', s.get('avgSchedulingDelay'))
"
# Watermark from JMX / Prometheus
curl -s "http://<driver-host>:4040/metrics/prometheus/" | grep -E "streaming.*watermark|streaming.*states"

# Recent batch details — look for long commit times
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/streaming/batches?count=10" | python3 -c "
import sys, json
for b in json.load(sys.stdin):
    print('Batch', b['batchId'],
          'input:', b.get('numInputRows', 0),
          'proc ms:', b.get('processingTime'),
          'sched delay ms:', b.get('schedulingDelay'),
          'trigger delay ms:', b.get('triggerExecution'))
"
# Check for idle Kafka partitions (watermark blocker)
kafka-consumer-groups.sh --bootstrap-server <broker>:9092 \
  --describe --group spark-<query-consumer-group> 2>/dev/null | \
  awk 'NR>2 && $5 == 0 {print "Idle partition:", $3}'

# Checkpoint directory write latency
hdfs dfs -ls hdfs://<checkpoint-path>/offsets/ | tail -5   # should advance each batch
```

**Thresholds:**
- WARNING: watermark not advancing for > 2× trigger interval
- CRITICAL: `states-rowsTotal` growing > 10% per hour; state store > 10 GB

## Diagnostic Scenario 9: Dynamic Allocation Not Scaling Down Idle Executors

**Symptom:** Application holds many executors even when no tasks are running; YARN queue utilization stays high between job waves; `spark.dynamicAllocation.enabled=true` but executor count never drops.

**Root Cause Decision Tree:**
- Cached RDD/DataFrame blocks prevent executor release → executors holding cached data are exempt from scale-down
- `spark.dynamicAllocation.executorIdleTimeout` too large → executors wait too long before being released
- Shuffle data retained on executor → external shuffle service not enabled; executor has shuffle files that may be needed
- Dynamic allocation misconfigured (both `--num-executors` and dynamic allocation set) → static count overrides dynamic

**Diagnosis:**
```bash
# Current executor count and idle time
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/executors" | python3 -c "
import sys, json
execs = [e for e in json.load(sys.stdin) if e.get('id') != 'driver']
active = [e for e in execs if not e.get('isBlacklisted')]
print(f'Total executors: {len(active)}')
idle = [e for e in active if e.get('activeTasks', 0) == 0]
print(f'Idle executors: {len(idle)}')
for e in idle[:5]:
    print('  Exec', e['id'], 'host:', e.get('hostPort','?')[:30],
          'cached blocks:', e.get('memoryUsed',0) // max(1,1))
"
# Cached RDDs holding executors
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/storage/rdd" | python3 -c "
import sys, json
for rdd in json.load(sys.stdin):
    if rdd.get('numCachedPartitions', 0) > 0:
        print('RDD:', rdd.get('name'), 'cachedPartitions:', rdd.get('numCachedPartitions'),
              'memSize MB:', round(rdd.get('memoryUsed',0)/1e6, 1))
"
# Dynamic allocation config
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" | python3 -c "
import sys, json
env = json.load(sys.stdin)
for k, v in env.get('sparkProperties', []):
    if 'dynamicAllocation' in k or 'num-executors' in k:
        print(k, '=', v)
"
```

**Thresholds:**
- WARNING: > 50% executors idle for > 10 minutes with no active jobs
- CRITICAL: YARN queue at 100% utilization because idle executors are not released

## Diagnostic Scenario 10: Executor Lost from YARN Preemption

**Symptom:** Executors suddenly disappear mid-stage; YARN logs show `Container preempted`; `FetchFailed` exceptions follow as shuffle data is lost; application retries stage.

**Root Cause Decision Tree:**
- Higher-priority YARN queue requesting resources → resource manager preempts lower-priority containers
- Cluster memory pressure from other applications → YARN preempts containers to balance cluster
- Container memory limit exceeded → NodeManager kills container (appears same as preemption to Spark)
- `spark.executor.memoryOverhead` too low → container total memory > YARN limit; NodeManager kills it

**Diagnosis:**
```bash
# YARN application log — preemption events
yarn logs -applicationId application_<id> 2>/dev/null | \
  grep -E "(preempt|PREEMPT|Container killed|Container released)" | tail -20

# NodeManager logs on affected host
ssh <nm-host> "grep -E '(killed|preempt|memoryLimit|exceeded)' \
  /var/log/hadoop-yarn/yarn-yarn-nodemanager-*.log | tail -30"

# YARN queue preemption config
yarn queue -status <spark-queue-name> 2>/dev/null | grep -i preempt

# Container memory overhead vs allocation
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" | python3 -c "
import sys, json
env = json.load(sys.stdin)
for k, v in env.get('sparkProperties', []):
    if 'memory' in k.lower() or 'overhead' in k.lower():
        print(k, '=', v)
"
# Lost executors
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/executors" | python3 -c "
import sys, json
lost = [e for e in json.load(sys.stdin) if not e.get('isActive', True)]
print('Lost executors:', len(lost))
for e in lost[-5:]:
    print('  Exec', e['id'], 'reason:', e.get('removeReason', 'unknown'))
"
```

**Thresholds:**
- WARNING: > 2 executors lost to preemption per 10 minutes
- CRITICAL: stage fails due to shuffle data lost from preempted executors; application aborts

## Diagnostic Scenario 11: Broadcast Join Threshold Exceeded — Revert to Sort-Merge Join

**Symptom:** Query plan switches from broadcast join to sort-merge join on a small table; execution time 10× longer; `shuffleBytesWritten` spike for a stage that was previously fast.

**Root Cause Decision Tree:**
- Table size estimation by CBO > `spark.sql.autoBroadcastJoinThreshold` → Spark chooses sort-merge join
- Statistics not collected or stale → optimizer over-estimates table size; refuses broadcast
- AQE disabled → runtime actual size not used to upgrade plan to broadcast join mid-query
- Table is actually large (data grew) → broadcast threshold legitimately exceeded; need to raise or restructure query

**Diagnosis:**
```bash
# Check join strategy in query plan
trino --server http://<coordinator>:8080 --execute "EXPLAIN <query>" 2>/dev/null || \
  spark-sql --master local -e "EXPLAIN EXTENDED <query>" 2>/dev/null

# Via Spark UI REST — stage shuffle write bytes
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages" | python3 -c "
import sys, json
for s in sorted(json.load(sys.stdin), key=lambda x: x.get('shuffleWriteBytes',0), reverse=True)[:5]:
    print('Stage', s['stageId'],
          'shuffleWrite GB:', round(s.get('shuffleWriteBytes',0)/1e9, 2),
          'inputRows:', s.get('inputRecords', 0),
          'numTasks:', s.get('numTasks'))
"
# Table size from Spark catalog statistics
# In spark-shell / pyspark:
# spark.table("db.small_table").queryExecution.optimizedPlan.stats.sizeInBytes

# AQE config
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" | python3 -c "
import sys, json
for k, v in json.load(sys.stdin).get('sparkProperties', []):
    if 'broadcast' in k.lower() or 'adaptive' in k.lower():
        print(k, '=', v)
"
```

**Thresholds:**
- WARNING: join query 5× slower than baseline without plan change justification
- CRITICAL: cluster-wide shuffle storm from unexpected sort-merge joins on many concurrent queries

## Diagnostic Scenario 12: Spark Application Stuck — No Progress on Active Job

**Symptom:** `DAGScheduler.job.activeJobs` > 0 but `DAGScheduler.stage.runningStages` = 0 for > 10 minutes; application is alive but making no progress; tasks not submitted despite available executors; Spark UI shows a job perpetually in RUNNING state.

**Root Cause Decision Tree:**
- All executors lost and dynamic allocation waiting for new ones → cluster resource manager (YARN/K8s) cannot schedule new containers (queue full, node pressure)
- Driver LiveListenerBus queue full (`numDroppedEvents` > 0) → driver cannot process task completion events; scheduler stalls
- Deadlock in driver due to `SparkContext.stop()` called concurrently with a job → application hangs
- Barrier stage waiting for all tasks to register → one task slot unavailable due to preemption; barrier never satisfied
- Checkpoint operation blocking on slow HDFS write → DAGScheduler waits for checkpoint before submitting next stage

**Diagnosis:**
```bash
# Check active jobs vs running stages
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/jobs" | python3 -c "
import sys, json
for j in json.load(sys.stdin):
    if j.get('status') == 'RUNNING':
        print('Job', j['jobId'], 'activeTasks:', j.get('numActiveTasks'),
              'completedTasks:', j.get('numCompletedTasks'),
              'failedTasks:', j.get('numFailedTasks'),
              'numStages:', j.get('numActiveStages'))
"
# Executor count
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/executors" | python3 -c "
import sys, json
exs = [e for e in json.load(sys.stdin) if e.get('id') != 'driver' and e.get('isActive')]
print('Active executors:', len(exs))
"
# LiveListenerBus dropped events
curl -s "http://<driver-host>:4040/metrics/prometheus/" | \
  grep -E "LiveListenerBus.*dropped|numDroppedEvents"

# YARN: container allocation status (are new executor containers being granted?)
yarn application -status application_<id> 2>/dev/null | grep -E "(State|Running|Pending|Containers)"

# K8s: pending executor pods
kubectl get pods -n spark-apps -l spark-role=executor | grep -E "(Pending|ContainerCreating)"

# Barrier stage check — look for RDDs using barrier mode
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages?status=ACTIVE" | python3 -c "
import sys, json
for s in json.load(sys.stdin):
    print('Stage', s.get('stageId'), 'tasks active:', s.get('numActiveTasks'),
          'pending:', s.get('numTasks', 0) - s.get('numActiveTasks', 0) - s.get('numCompletedTasks', 0))
"
```

**Thresholds:**
- WARNING: `runningStages` = 0 with active jobs for > 5 minutes
- CRITICAL: application making no progress for > 15 minutes; executor count = 0

## Diagnostic Scenario 13: Shuffle Service OOM Causing Tasks to Hang Indefinitely

**Symptoms:** Tasks remain in RUNNING state indefinitely with no progress; executor logs show `ExternalBlockStoreClient connection refused` or `Failed to connect to external shuffle service`; `spark.shuffle.service.enabled=true` but shuffle service process is dead or OOM-killed; INTERMITTENT — occurs under high shuffle load, resolves when shuffle data is cleaned up on node restart.

**Root Cause Decision Tree:**
- If `spark.shuffle.service.enabled=true` AND NodeManager auxiliary service log shows OOM → external shuffle service JVM heap too small; default is 256 MB, insufficient for many concurrent shuffle readers
- If shuffle service process is alive AND tasks hang → shuffle service disk directory (`spark.local.dir`) is full; shuffle service cannot serve blocks it cannot read
- If `spark.shuffle.service.enabled=false` AND tasks hang after executor loss → embedded shuffle data is lost when executor is killed; tasks must retry but cannot fetch; switch to external shuffle service
- If shuffle service is healthy AND disk is healthy → network partition between executor and shuffle service host; check NodeManager firewall rules on shuffle service port (default 7337)

**Diagnosis:**
```bash
# Step 1: Check if external shuffle service is running on each worker
# YARN: check NodeManager auxiliary service process
yarn node -list -all 2>/dev/null | grep -v LOST | awk '{print $1}' | while read node; do
  ssh "$node" "ps aux | grep -i 'ShuffleService\|ExternalShuffleService' | grep -v grep || echo 'MISSING on $node'"
done

# Step 2: Check shuffle service JVM heap (if using Spark auxiliary service in NodeManager)
# NodeManager config: yarn.nodemanager.aux-services.spark_shuffle.classpath
# Heap tunable via SPARK_SHUFFLE_OPTS in spark-env.sh
grep -r 'SPARK_SHUFFLE_OPTS\|spark_shuffle.*memory' /etc/spark/ /etc/hadoop/ 2>/dev/null

# Step 3: Disk space on shuffle directories
# spark.local.dir typically /tmp or /mnt/data/spark-local
spark_local_dirs=$(curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" 2>/dev/null \
  | python3 -c "import sys,json; env=json.load(sys.stdin);
[print(v) for k,v in env.get('sparkProperties',[]) if 'local.dir' in k]")
echo "Shuffle dirs: $spark_local_dirs"
df -h $spark_local_dirs 2>/dev/null || df -h /tmp

# Step 4: Tasks stuck in RUNNING with no output records
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages?status=ACTIVE" | python3 -c "
import sys, json
for stage in json.load(sys.stdin):
    print('Stage', stage.get('stageId'), 'active_tasks:', stage.get('numActiveTasks'),
          'runtime_ms_p75:', stage.get('taskMetrics', {}).get('executorRunTime', 'N/A'))"

# Step 5: Shuffle service port connectivity from a worker node
curl -v telnet://<worker-host>:7337 2>&1 | head -5

# Step 6: NodeManager logs for shuffle service OOM
ssh <worker-node> "grep -i 'OutOfMemory\|GC overhead\|ExternalShuffleService' \
  /var/log/hadoop-yarn/yarn-yarn-nodemanager-*.log 2>/dev/null | tail -20"
```

**Thresholds:**
- WARNING: Any task in RUNNING state with zero output records for > 5 min = shuffle service investigation
- CRITICAL: >10 tasks hanging with no shuffle service connectivity = cluster-wide shuffle outage; kill application

## Diagnostic Scenario 14: Speculative Task Launch Causing Duplicate Output

**Symptoms:** Output sink contains duplicate records after job completion; job completes successfully but downstream systems observe double-counted rows; `spark.speculation=true` is set; INTERMITTENT — only triggers when slow tasks are detected (p75 task duration much higher than median).

**Root Cause Decision Tree:**
- If speculation enabled AND output sink is non-idempotent (HDFS append, JDBC INSERT without ON CONFLICT, Kafka producer without exactly-once) → speculative and original task both write to sink before one is killed; original task killed after speculative completes but output was already written
- If output uses overwrite mode (HDFS directory overwrite, Delta Lake) → speculative tasks write to same final path with race condition
- If speculation enabled AND source is Kafka → speculative task may commit Kafka offset twice; verify `spark.sql.streaming.kafka.useDeprecatedOffsetFetching`
- If sink is idempotent (unique key upserts, S3 atomic part files) → duplicates not possible; investigation misdirected

**Diagnosis:**
```bash
# Step 1: Confirm speculation is enabled and has fired
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" | python3 -c "
import sys, json
env = json.load(sys.stdin)
for k, v in env.get('sparkProperties', []):
    if 'speculation' in k.lower():
        print(k, '=', v)"

# Step 2: Check for tasks that were killed (speculative kills the original)
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages" | python3 -c "
import sys, json
for stage in json.load(sys.stdin):
    killed = stage.get('numKilledTasks', 0)
    if killed > 0:
        print('Stage', stage.get('stageId'), 'killedTasks:', killed, '— speculation likely fired')"

# Step 3: Per-task duration to see if speculation triggered correctly
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages/<stage-id>/0/taskList?length=200" \
  | python3 -c "
import sys, json
tasks = json.load(sys.stdin)
durations = sorted([t.get('taskMetrics', {}).get('executorRunTime', 0) for t in tasks])
if durations:
    median = durations[len(durations)//2]
    p90 = durations[int(len(durations)*0.9)]
    print(f'Median task runtime: {median}ms  P90: {p90}ms  ratio: {p90/max(1,median):.1f}x')"

# Step 4: Verify duplicate count in output
# For HDFS output — check for files from both speculative and original tasks
hdfs dfs -ls -R <output-path> 2>/dev/null | grep -E '_temporary|\.tmp'

# Step 5: Check sink type
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/sql" \
  | python3 -c "import sys,json; [print(q.get('description','')[:200]) for q in json.load(sys.stdin)[:5]]" 2>/dev/null
```

**Thresholds:**
- WARNING: `numKilledTasks > 0` in any stage with non-idempotent sink = duplicate risk
- CRITICAL: Confirmed duplicates in output = data integrity violation; requires immediate deduplication

## Diagnostic Scenario 15: Spark UI OOM from Accumulating Completed Task Metrics

**Symptoms:** Driver JVM heap growing over time without corresponding increase in active task count; `go_memstats_heap_inuse_bytes` (JVM equivalent: `driver_JVMHeapMemory`) rising; Spark UI becomes slow or unresponsive; driver eventually OOMs with `java.lang.OutOfMemoryError: Java heap space`; INTERMITTENT — worsens with job duration and total task count.

**Root Cause Decision Tree:**
- If `spark.ui.retainedJobs` and `spark.ui.retainedStages` are set to high values → completed job/stage metadata never evicted from memory; each task accumulates ~1–5 KB of metrics
- If driver heap grows proportional to number of completed tasks → `SparkListenerTaskEnd` events accumulate in `AppStatusStore`; with 1M tasks × 3 KB = ~3 GB retained
- If GC pressure correlates with UI page loads → JVM materializing large listener bus data structures for UI rendering; reduce retention or disable UI for production jobs
- If driver heap grows even with retention limits → executor heartbeat storm filling `LiveListenerBus` event queue; check `spark.scheduler.listenerbus.eventqueue.*.capacity`

**Diagnosis:**
```bash
# Step 1: Driver JVM heap over time (Prometheus or JMX)
curl -sg 'http://<prometheus>:9090/api/v1/query_range?query=spark_driver_JVMHeapMemory&start=<1h_ago>&end=now&step=60' \
  | jq '.data.result[0].values[-10:]'

# Step 2: Total task count for the application
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages?status=COMPLETE" | python3 -c "
import sys, json
stages = json.load(sys.stdin)
total_tasks = sum(s.get('numCompleteTasks', 0) for s in stages)
print(f'Total completed tasks: {total_tasks}  stages: {len(stages)}')"

# Step 3: Current retention settings
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" | python3 -c "
import sys, json
env = json.load(sys.stdin)
for k, v in env.get('sparkProperties', []):
    if any(x in k for x in ['retainedJobs','retainedStages','retainedTasks','listenerbus']):
        print(k, '=', v)"

# Step 4: Driver GC pressure
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/executors" | python3 -c "
import sys, json
for e in json.load(sys.stdin):
    if e.get('id') == 'driver':
        gc = e.get('totalGCTime', 0)
        runtime = e.get('totalDuration', 1)
        print(f'Driver GC ratio: {100*gc/max(1,runtime):.1f}%  GCTime: {gc}ms')"

# Step 5: LiveListenerBus dropped events (metric exposed via JMX)
curl -s "http://<driver-host>:4040/metrics/json" 2>/dev/null \
  | python3 -c "import sys,json,re; d=json.load(sys.stdin);
[print(k,v) for k,v in d.get('gauges',{}).items() if 'listenerbus' in k.lower() or 'dropped' in k.lower()]"
```

**Thresholds:**
- WARNING: Driver GC ratio > 10% of total runtime = retention-related memory pressure
- CRITICAL: Driver heap > 80% of `--driver-memory` with growing trend = OOM imminent; reduce retention or restart driver

## Diagnostic Scenario 16: Dynamic Allocation Returning Executors Mid-Stage Causing Data Locality Miss

**Symptoms:** Job throughput drops intermittently during long stages; executor log shows `Moving task from executor <X> to <Y> due to data locality timeout`; `DATA_LOCAL` task percentage drops; `RACK_LOCAL` or `ANY` locality increases; overall stage duration increases 2–3×; INTERMITTENT — only occurs when `spark.dynamicAllocation.executorIdleTimeout` fires during data-local stage.

**Root Cause Decision Tree:**
- If `spark.dynamicAllocation.executorIdleTimeout` is short (default 60s) AND stage has uneven task distribution → fast executors finish early, become idle, get returned; remaining data is only local to returned executor; tasks must run as RACK_LOCAL or ANY
- If HDFS caching is used (`hdfs cacheadmin`) AND executor is on the same node as cached block → executor release removes data locality benefit for subsequent stages reading same data
- If `spark.locality.wait` is too short → tasks do not wait for local executor to become available and immediately fall back to rack/any locality
- If K8s deployment AND `spark.kubernetes.allocation.batch.size` is large → when scaling up, new executors are on remote nodes; data locality never achieved

**Diagnosis:**
```bash
# Step 1: Locality distribution per stage
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages" | python3 -c "
import sys, json
for stage in json.load(sys.stdin):
    local = stage.get('taskLocalityLevel', {})
    if local:
        total = sum(local.values())
        data_local = local.get('DATA_LOCAL', 0)
        pct = 100*data_local/max(1,total)
        print(f'Stage {stage.get(\"stageId\")}: DATA_LOCAL={data_local}/{total} ({pct:.0f}%)')" 2>/dev/null

# Step 2: Dynamic allocation timeline — executor add/remove events
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/executors?status=dead" | python3 -c "
import sys, json
for e in json.load(sys.stdin)[:10]:
    print('Executor', e.get('id'), 'removed at:', e.get('removeTime', 'N/A'),
          'reason:', e.get('removeReason', 'N/A')[:60])"

# Step 3: Locality wait config
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" | python3 -c "
import sys, json
env = json.load(sys.stdin)
for k, v in env.get('sparkProperties', []):
    if 'locality' in k.lower() or 'IdleTimeout' in k or 'cachedExecutor' in k:
        print(k, '=', v)"

# Step 4: Task locality from event log (if available)
# spark.eventLog.dir — grep for TaskLocality
grep -h 'taskLocality\|DATA_LOCAL\|RACK_LOCAL\|ANY' \
  <spark-event-log-path> 2>/dev/null | python3 -c "
import sys, json
counts = {}
for line in sys.stdin:
    try:
        e = json.loads(line)
        loc = e.get('Task Info', {}).get('Locality', '')
        if loc: counts[loc] = counts.get(loc, 0) + 1
    except: pass
print(counts)"
```

**Thresholds:**
- WARNING: `DATA_LOCAL` task percentage < 50% for HDFS-based jobs = locality miss; check dynamic allocation timing
- CRITICAL: All tasks running as `ANY` in a stage with cached data = executor release race; consider disabling dynamic allocation for data-local workloads

## Diagnostic Scenario 17: Structured Streaming Watermark Not Advancing Causing State Store Unbounded Growth

**Symptoms:** `states-rowsTotal` metric in Spark UI Structured Streaming tab grows indefinitely; state store checkpoint size increases every micro-batch; driver memory grows; queries using `groupBy().agg()` or `flatMapGroupsWithState` accumulate state for old keys; INTERMITTENT — triggered by late-arriving data or idle Kafka partitions holding back watermark.

**Root Cause Decision Tree:**
- If watermark is set via `.withWatermark("eventTime", "X hours")` AND some Kafka partitions have no recent events → Spark uses min watermark across all partitions; an idle partition freezes the global watermark at the last event time seen on that partition
- If trigger interval is very short (< 1 s) AND event time clock skew between producers → out-of-order events arrive after watermark advances; counted as late and dropped, but state for old windows remains
- If `flatMapGroupsWithState` is used with `GroupStateTimeout.EventTimeTimeout()` AND watermark not advancing → timeout never fires; groups accumulate indefinitely
- If `spark.sql.streaming.stateStore.maintenanceInterval` is too large → state store cleanup runs infrequently; state appears to grow even when watermark advances

**Diagnosis:**
```bash
# Step 1: Check watermark advance from Spark UI streaming query progress
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/streaming/statistics" | python3 -c "
import sys, json
stats = json.load(sys.stdin)
print('Watermark:', stats.get('watermark', 'N/A'))
print('Input rate:', stats.get('inputRate', 'N/A'), 'rows/sec')
print('Batch duration:', stats.get('batchDuration', 'N/A'), 'ms')" 2>/dev/null

# Step 2: State store row count growth
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/streaming/statistics" | python3 -c "
import sys, json
stats = json.load(sys.stdin)
state_ops = stats.get('stateOperators', [])
for op in state_ops:
    print(f'StateOp: numRowsTotal={op.get(\"numRowsTotal\",0)} numRowsUpdated={op.get(\"numRowsUpdated\",0)} memoryUsedBytes={op.get(\"memoryUsedBytes\",0)}')" 2>/dev/null

# Step 3: Check for idle Kafka partitions (watermark blocker)
kafka-consumer-groups.sh --bootstrap-server <broker>:9092 \
  --describe --group spark-<query-consumer-group> 2>/dev/null | \
  awk 'NR>2 {print "partition:", $3, "lag:", $5, "consumer:", $7}'

# Step 4: Event time vs processing time gap (in query progress JSON)
# Streaming query progress is logged to driver logs — check for eventTime watermark field
grep -i '"eventTime"\|"watermark"\|"stateOperators"' /path/to/driver.log 2>/dev/null | tail -20

# Step 5: State store disk size (checkpoint directory)
hdfs dfs -du -h <checkpoint-path>/state/ 2>/dev/null | sort -rh | head -10
```

**Thresholds:**
- WARNING: `numRowsTotal` in state store growing > 10% per hour = watermark not advancing
- CRITICAL: State store memory > 10 GB OR driver heap > 80% from state = imminent OOM; restart query with reduced watermark delay

## Diagnostic Scenario 18: YARN Application Master OOM During Large Spark SQL Query Plan Optimization

**Symptoms:** Application Master (AM) process is killed by YARN with `Container killed by the ApplicationMaster`; driver log shows `java.lang.OutOfMemoryError` in `SparkSqlParser` or `Analyzer` or `Optimizer`; job fails during planning phase before any tasks execute; INTERMITTENT — triggered by complex multi-join SQL queries with many subqueries or large IN-lists.

**Root Cause Decision Tree:**
- If OOM occurs in `CostBasedOptimizer` or `JoinReorder` → join reordering enumerates exponential plan combinations; large star-schema queries with >8 tables and CBO enabled cause plan explosion
- If OOM occurs in `AnalyzeColumnCommand` or statistics collection → collecting column statistics on very wide tables holds all column data in driver memory
- If OOM in `CodeGenerator` → generated code for complex queries exceeds JVM code cache; `spark.sql.codegen.hugeMethodLimit` causes fallback but intermediate structures are large
- If AM memory is set equal to driver memory → YARN overhead (`spark.yarn.am.memoryOverhead`) not accounted for; AM container exceeds physical limit

**Diagnosis:**
```bash
# Step 1: Check AM/driver OOM in YARN logs
yarn logs -applicationId application_<id> 2>/dev/null | \
  grep -E 'OutOfMemoryError|Container killed|GC overhead|plan optimization' | head -20

# Step 2: Query complexity — join count and subquery depth
# Capture the SQL query from the slow log or application code
# Count number of JOIN clauses and nested subqueries:
echo "<your-sql>" | grep -oi '\bJOIN\b' | wc -l
echo "<your-sql>" | grep -oi '\bSELECT\b' | wc -l

# Step 3: CBO and join reorder settings
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" | python3 -c "
import sys, json
env = json.load(sys.stdin)
for k, v in env.get('sparkProperties', []):
    if any(x in k for x in ['cbo','joinReorder','codegen','planChangeLog','am.memory']):
        print(k, '=', v)"

# Step 4: Physical plan size (if query starts)
spark-sql -e "EXPLAIN EXTENDED <your-query>" 2>/dev/null | wc -l

# Step 5: YARN container memory vs actual AM usage
yarn application -status application_<id> 2>/dev/null | grep -E 'Memory|AM'
```

**Thresholds:**
- WARNING: SQL query with > 8 tables in FROM/JOIN clause AND CBO enabled = join reorder OOM risk
- CRITICAL: AM container OOM during planning = job never runs; increase AM memory or disable CBO

## Cross-Service Failure Chains

| Spark Symptom | Actual Root Cause | First Check |
|---------------|------------------|-------------|
| OOM executor failures | Input data format change — new fields with large strings not handled by current schema → memory spike | Check input data sample for schema drift |
| Job fails after midnight | Partition-based date filtering queries empty partition (today's data not yet arrived) | Check source data freshness before Spark job |
| Driver OOM on `collect()` | Code calling `.collect()` on large DataFrame — fine in test, fails in prod with full data | `df.count()` before `.collect()` to validate size |
| YARN resource allocation failure | Cluster running other batch jobs simultaneously — resource contention | `yarn application -list` — check concurrent jobs |
| Slow shuffle stage | Network bandwidth saturated by another Spark job's shuffle simultaneously | Check cluster network utilization during job overlap |
| S3A commit failures | S3 eventual consistency window between `_temporary` write and rename (S3 is not atomic) | Use `s3a://` with `PathOutputCommitProtocol` and `magic` committer |

---

## Common Error Messages & Root Causes

| Error Message | Root Cause | Action |
|---|---|---|
| `org.apache.spark.SparkException: Job aborted due to stage failure` | Task failure rate exceeded `spark.task.maxFailures` (default 4); stage cannot complete | Check executor logs for root exception; identify failing task via Spark UI → Stages → Failed Tasks; look for OOM, disk full, or data corruption |
| `java.lang.OutOfMemoryError: Java heap space` (driver) | `collect()` or `toPandas()` on a large dataset materialized the full result in driver JVM heap | Replace `collect()` with `write()` to storage; use `take(n)` for sampling; increase driver memory: `--driver-memory 16g` only as temporary fix |
| `org.apache.spark.shuffle.FetchFailedException` | Shuffle service OOM or executor was lost after producing shuffle data; fetch from dead executor fails | Check if shuffle service is separate (`spark.shuffle.service.enabled=true`); if executor-local shuffle, increase executor memory; enable external shuffle service for stability |
| `ExecutorLostFailure (executor N exited caused by one of the running tasks) Reason: Container killed by YARN for exceeding memory limits` | Executor container exceeded YARN memory limit including off-heap and overhead; actual memory > `--executor-memory` + overhead | Increase `spark.executor.memoryOverhead` (default 10% of executor-memory, min 384 MiB); or reduce executor memory fraction: `--conf spark.memory.fraction=0.6` |
| `org.apache.spark.sql.AnalysisException: ... already exists` | Table or view name conflict; a table with that name exists in the catalog or temp view registry | Drop the existing table/view first or use `CREATE OR REPLACE`; check for naming collisions in multi-tenant environments |
| `org.apache.spark.sql.streaming.StreamingQueryException: ... checkpoint incompatible` | Schema changed between runs and the existing checkpoint cannot be deserialized with the new schema | Delete checkpoint directory and restart from scratch, or use a new checkpoint path; plan schema evolution carefully before deploying |
| `GC overhead limit exceeded` | Executor or driver JVM spending > 98% of time in GC; heap is nearly full, allocation failing | Reduce data cached in memory; increase heap or switch to G1GC: `--conf spark.executor.extraJavaOptions=-XX:+UseG1GC`; check for memory leaks in UDFs or accumulators |

---

## Diagnostic Scenario 19: Broadcast Join Threshold Causing Silent Job Failure at Scale

**Symptoms:** A Spark job that processed 10 GB input correctly fails or produces incorrect results when input grows to 1 TB; `EXPLAIN` shows `BroadcastHashJoin` in the query plan; executors are killed with OOM (`Container killed by YARN for exceeding memory limits`); driver logs show `SparkException: Exception thrown in awaitResult`; no obvious error in business logic; INTERMITTENT — only occurs when AQE is disabled or broadcast threshold is misconfigured.

**Root Cause Decision Tree:**
- If `spark.sql.autoBroadcastJoinThreshold` is set to a value (e.g., 100 MiB) AND the broadcasted table grows beyond that threshold → AQE will switch to SortMergeJoin automatically IF `spark.sql.adaptive.enabled=true`; if AQE is off, the broadcast proceeds and executors OOM holding the full broadcast table
- If AQE is enabled but `spark.sql.adaptive.autoBroadcastJoinThreshold` is also set → the explicit threshold overrides the adaptive decision; large table is still broadcast
- If the table was 10 GB at plan time but AQE statistics are stale → AQE uses runtime statistics but if the estimate is wrong (e.g., partitioned table with skewed partitions), the broadcast decision may still be wrong
- If spill is occurring silently → job succeeds but takes 10× longer due to disk spill; check `shuffle_bytes_spilled_to_disk` in stage metrics; no OOM but significant latency regression

**Diagnosis:**
```bash
# Step 1: Confirm broadcast join in plan
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/sql" | python3 -c "
import sys, json
for q in json.load(sys.stdin):
    if 'BroadcastHashJoin' in q.get('planDescription', ''):
        print('Query', q['id'], 'uses BroadcastHashJoin, duration:', q.get('duration'), 'ms')
" 2>/dev/null

# Step 2: Check autoBroadcastJoinThreshold setting
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/environment" | python3 -c "
import sys, json
env = json.load(sys.stdin)
for k, v in env.get('sparkProperties', []):
    if 'broadcast' in k.lower() or 'adaptive' in k.lower():
        print(k, '=', v)"

# Step 3: Executor OOM evidence
yarn logs -applicationId application_<id> 2>/dev/null | \
  grep -E '(OutOfMemoryError|killed by YARN|broadcast|BroadcastExchange)' | head -20

# Step 4: Disk spill in stages with broadcast
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages" | python3 -c "
import sys, json
for s in json.load(sys.stdin):
    spill = s.get('diskBytesSpilled', 0)
    if spill > 0:
        print('Stage', s['stageId'], 'spill MB:', round(spill/1e6, 1),
              'input MB:', round(s.get('inputBytes',0)/1e6, 1))"

# Step 5: Actual table size vs broadcast threshold
spark-sql -e "ANALYZE TABLE <db>.<table> COMPUTE STATISTICS NOSCAN;" 2>/dev/null
spark-sql -e "DESCRIBE EXTENDED <db>.<table>;" 2>/dev/null | grep -i "Statistics"
```

**Thresholds:**
- WARNING: `spark.sql.autoBroadcastJoinThreshold` set > 200 MiB AND input data volume is growing = risk of executor OOM as data grows past threshold
- CRITICAL: Executor OOM during `BroadcastExchange` stage = broadcast table exceeded executor heap; disable broadcast for this join immediately

## Diagnostic Scenario 20: Silent Data Skew Causing Partial or Dominated Results

**Symptoms:** Spark job completes successfully. Output record count appears correct. But aggregate results are wrong — one region/category/key's data dominates the output, or some keys are missing entirely from aggregations.

**Root Cause Decision Tree:**
- If one task in a stage takes 10× longer than the median task duration → data skew on a join or `groupBy` key; one partition holds the majority of records
- If `null` values are concentrated in one partition → all nulls route to the same reducer for `groupBy(col)` or `join(col)`; null key partition is orders of magnitude larger than others
- If output is partitioned by the skewed key → downstream consumers reading specific partitions only see data from the "fat" partition; other partitions appear empty
- If skew manifests on a join → the "small" side may actually have a few very frequent keys that explode the output size for those keys

**Diagnosis:**
```bash
# Step 1: Check max vs median task duration in Spark UI
# Navigate to: Spark UI > Stages > Click the slow stage > Tasks tab
# Sort by Duration DESC — if max/median > 5x = skew present

# Step 2: Find the skewed key distribution
# In Spark shell or spark-sql:
# df.groupBy("skew_key").count().orderBy(desc("count")).show(20)

# Step 3: Check for null key concentration
# df.filter(col("join_key").isNull).count()   # How many nulls?
# df.filter(col("join_key").isNotNull).groupBy("join_key").count().describe().show()

# Step 4: Spark UI stage metrics — compare across tasks
curl -s "http://<driver-host>:4040/api/v1/applications/<app-id>/stages/<stage-id>/taskList" | python3 -c "
import sys, json
tasks = json.load(sys.stdin)
durations = sorted([t.get('taskMetrics', {}).get('executorRunTime', 0) for t in tasks])
print('min:', durations[0], 'median:', durations[len(durations)//2], 'max:', durations[-1], 'p99:', durations[int(len(durations)*0.99)])"

# Step 5: Verify output partition sizes are balanced
# hadoop fs -ls -h /output/path/  (check file sizes — skewed = one file much larger)
# Or: df.write.parquet("/tmp/debug_output"); hadoop fs -ls /tmp/debug_output/
```

**Thresholds:** Max task duration > 5× median task duration in a stage = skew WARNING. Max task input bytes > 10× median = CRITICAL skew requiring immediate salting or repartitioning.

## Diagnostic Scenario 21: Cross-Service Chain — S3 Throttling Causing Spark Job Slowness

**Symptoms:** Spark job runs fine in development or with small datasets, but is dramatically slower in production. No Spark executor OOM or failures. Executors appear healthy in Spark UI. Stage durations are 5–20× longer than expected.

**Root Cause Decision Tree:**
- Alert: Spark job SLA breach / stage duration regression in production
- Real cause: S3 requests being throttled (HTTP 503 SlowDown) during heavy read or write phase → Spark retries each throttled request with backoff → stage wall time dominated by S3 retry delays, not compute
- If reading many small files from S3 → excessive LIST and GET API calls per task → throttle rate exceeded quickly even with moderate parallelism
- If writing many small output partitions → PUT request volume exceeds S3 bucket request rate limit → write phase throttled
- If multiple Spark jobs or services access the same S3 bucket concurrently → aggregate request rate exceeds per-prefix limit

**Diagnosis:**
```bash
# Step 1: Search executor logs for S3 throttling evidence
yarn logs -applicationId application_<id> 2>/dev/null | \
  grep -iE "throttl|SlowDown|503|RetryableException|AmazonS3Exception" | head -30

# Step 2: Check Spark UI for tasks with unusually high "Shuffle Read Time" or "Task Deserialization Time"
# Navigate to: Spark UI > Stages > Expand slow stage > look for tasks with high fetch wait time

# Step 3: Check number of input files (many small files = many API calls)
hadoop fs -count "s3a://<bucket>/<path>/*" 2>/dev/null | awk '{print "dirs:", $1, "files:", $2, "size:", $3}'
# If file count > 10x partition count = small file problem

# Step 4: S3 request metrics via CloudWatch (if AWS access available)
aws cloudwatch get-metric-statistics \
  --namespace AWS/S3 \
  --metric-name 5xxErrors \
  --dimensions Name=BucketName,Value=<bucket> \
  --start-time "$(date -u -d '1 hour ago' +%FT%TZ)" \
  --end-time "$(date -u +%FT%TZ)" \
  --period 300 \
  --statistics Sum 2>/dev/null

# Step 5: Check S3A retry configuration in Spark (interactive — paste into spark-shell):
# sc.hadoopConfiguration.get("fs.s3a.retry.limit")
# sc.hadoopConfiguration.get("fs.s3a.retry.interval")
# sc.hadoopConfiguration.get("fs.s3a.attempts.maximum")
```

**Thresholds:** Any `503 SlowDown` in executor logs = S3 throttling confirmed. Input file count > 100,000 for a single job = small file problem requiring compaction. Task fetch wait time > 30s = likely I/O bottleneck (S3 or shuffle).

# Capabilities

1. **Executor management** — OOM diagnosis, memory tuning, dynamic allocation
2. **Shuffle optimization** — Fetch failure debugging, partition tuning
3. **Structured Streaming** — Backpressure, checkpoint recovery, offset management
4. **Spark SQL** — AQE, CBO, broadcast joins, partition pruning
5. **Resource management** — YARN/K8s resource allocation, queue configuration
6. **Data skew** — Detection, salting, repartitioning strategies

# Critical Metrics to Check First

1. `DAGScheduler.stage.failedStages` — any non-zero value requires investigation
2. `BlockManager.memory.remainingMem_MB` — below 10% of max = eviction / spill imminent
3. `streaming.<query>.latency` — above 2× trigger interval = backlog accumulating
4. `executor.jvmGCTime / executor.runTime` — above 10% = GC-dominated execution
5. `executor.shuffleRemoteBytesReadToDisk` — non-zero = shuffle spilling to disk
6. Active executor count vs expected — sudden drop = cluster instability

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Task failure rate % | > 1% | > 10% | Spark Web UI: `GET /api/v1/applications/<id>/stages` — compute `numFailedTasks / numTasks` per stage |
| GC time as % of executor run time | > 10% | > 25% | Spark Web UI: `GET /api/v1/applications/<id>/executors` — `totalGCTime / totalDuration` per executor |
| Shuffle spill to disk (bytes per stage) | > 500 MB | > 5 GB | Spark Web UI stage detail: `diskBytesSpilled` field; or Prometheus `spark_stage_diskBytesSpilled` |
| Executor memory utilization % | > 80% | > 95% | Spark Web UI: `GET /api/v1/applications/<id>/executors` — `memoryUsed / maxMemory`; or Prometheus `spark_executor_memoryUsed_bytes / spark_executor_maxMemory_bytes` |
| Streaming micro-batch processing latency (seconds) | > 2× trigger interval | > 5× trigger interval | Spark Structured Streaming: `GET /api/v1/applications/<id>/streaming/statistics` — `processingRate` vs `inputRate`; or Prometheus `spark_streaming_batch_processing_time_ms` |
| Pending tasks in scheduler backlog | > 200 tasks | > 1000 tasks | Spark Web UI Jobs tab — tasks queued waiting for executor slots; or Prometheus `spark_scheduler_activeJobs` combined with `spark_scheduler_pendingStages` |
| Shuffle read remote fetch wait time p99 (ms) | > 500ms | > 2000ms | Spark Web UI stage metrics: `shuffleFetchWaitTime`; or `spark.metrics.conf` with Prometheus sink — `spark_executor_shuffleRemoteBlocksFetched` / fetch time ratio |
| Driver JVM heap utilization % | > 70% | > 90% | Prometheus `jvm_memory_used_bytes{area="heap",instance="driver"} / jvm_memory_max_bytes{area="heap",instance="driver"}`; or Spark Web UI `/environment` page for driver heap config |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Executor disk usage (shuffle / spill) | `df -h /tmp` on worker nodes above 60% during peak job windows | Add additional `spark.local.dir` paths on larger volumes; enable `spark.shuffle.compress=true`; pre-clean stale shuffle data between jobs | 1–3 days |
| Driver heap utilization | Spark UI driver memory gauge above 70% during large aggregations or collects | Increase `spark.driver.memory`; replace `collect()` with writes to object storage; reduce broadcast join thresholds | Hours |
| Executor heap utilization (GC overhead) | Spark UI shows GC time > 10% of executor task time | Increase `spark.executor.memory` or reduce `spark.default.parallelism`; switch to off-heap storage with `spark.memory.offHeap.enabled=true` | 1–3 days |
| Task serialization / shuffle read time | Stage details show shuffle read time consistently > 50% of total stage time | Increase shuffle partitions (`spark.sql.shuffle.partitions`); use bucketed joins to eliminate shuffle; add faster network or increase executor count | 1–2 weeks |
| YARN / Kubernetes pending container requests | `yarn application -list -appStates RUNNING \| grep "pending resources"` showing sustained pending > 5 containers | Scale the YARN NodeManager pool or Kubernetes worker node group; review resource queue limits | 1–2 weeks |
| Kafka consumer lag (Structured Streaming) | `kafka-consumer-groups.sh --describe --group <spark-group>` showing lag > 1M messages growing over time | Increase micro-batch trigger interval or add more executor cores to the streaming job; partition Kafka topics more finely for higher parallelism | Hours |
| Object storage API error rate (S3/GCS reads) | AWS CloudWatch `4xxErrors` or `5xxErrors` on S3 buckets used by Spark > 1% of requests | Implement retry logic with exponential backoff in Spark I/O; request S3 request rate limit increases via AWS Support; enable S3 Transfer Acceleration | 1–3 days |
| Metastore (Hive/Glue) response time | Glue / Hive Metastore API calls taking > 2s (visible in Spark SQL plan with long `ListPartitions` nodes) | Enable Spark's `spark.sql.hive.metastorePartitionPruning=true`; cache partition metadata with `MSCK REPAIR TABLE`; shard the Metastore database | 1–2 weeks |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# List all running Spark applications on YARN with their state and resource usage
yarn application -list -appStates RUNNING | awk 'NR>2 {print $1, $2, $6, $7}'

# Show executor memory usage, GC time, and task failure counts from the Spark REST API
curl -s 'http://localhost:4040/api/v1/applications/<app-id>/executors' | python3 -c "import sys,json; [print(e['id'], 'memUsed:', e['memoryUsed']//1e6, 'MB', 'GC:', e['totalGCTime'], 'ms', 'failed:', e['failedTasks']) for e in json.load(sys.stdin)]"

# List all active Spark stages with their task counts and shuffle write sizes
curl -s 'http://localhost:4040/api/v1/applications/<app-id>/stages?status=active' | python3 -m json.tool | grep -E '"stageId"|"numActiveTasks"|"shuffleWriteBytes"|"inputBytes"'

# Check Kafka consumer group lag for Structured Streaming jobs
kafka-consumer-groups.sh --bootstrap-server <kafka-brokers> --describe --group <spark-streaming-group> | awk 'NR>1 {print $1,$2,$4,$5,$6}' | sort -k5 -rn | head -20

# Show Spark event log for task failures and exception messages (local mode)
grep -E "TaskEnd|ExceptionFailure|reason" /var/log/spark/eventlog/<app-id> | head -50

# Identify driver GC pressure from Spark driver JVM metrics
curl -s 'http://localhost:4040/api/v1/applications/<app-id>/environment' | python3 -m json.tool | grep -i "spark.driver.memory\|gc\|extraJavaOptions"

# Get YARN NodeManager resource utilization across all worker nodes
yarn node -list -states RUNNING 2>/dev/null | awk 'NR>3 {print $1, $3, $4}' | column -t

# Check for spill-to-disk in recent Spark SQL queries via the SQL metrics endpoint
curl -s 'http://localhost:4040/api/v1/applications/<app-id>/sql' | python3 -c "import sys,json; [print(q['id'], q['description'][:60], q.get('duration','?')) for q in json.load(sys.stdin) if q.get('status')=='RUNNING']"

# Show structured streaming query progress (lag, input rate, processing rate)
curl -s 'http://localhost:4040/api/v1/applications/<app-id>/streaming/statistics' | python3 -m json.tool | grep -E '"avgInputRate"|"avgProcessingRate"|"numInactiveReceivers"'

# Tail Spark driver logs for OOM errors, task failures, or lost executors
kubectl logs -n spark <driver-pod-name> --tail=100 -f 2>/dev/null | grep -E "ERROR|Lost executor|OutOfMemory|killed by external signal"
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Batch job success rate (successful job runs / total job submissions) | 99% | `1 - (rate(spark_job_failed_total[5m]) / rate(spark_job_total[5m]))` | 7.3 hr | > 6× burn rate over 1h window |
| Structured Streaming processing lag p95 ≤ 60s (trigger-to-output latency) | 99.5% | `histogram_quantile(0.95, rate(spark_streaming_trigger_processing_time_seconds_bucket[5m]))` ≤ 60 | 3.6 hr | > 6× burn rate over 1h window |
| Executor availability (fraction of allocated executors active vs. lost) | 99.5% | `1 - (rate(spark_executor_failed_total[5m]) / rate(spark_executor_added_total[5m]))` | 3.6 hr | > 6× burn rate over 1h window |
| Stage task failure rate ≤ 1% (failed tasks / total tasks across all stages) | 99% | `rate(spark_task_failed_total[5m]) / rate(spark_task_total[5m])` ≤ 0.01 | 7.3 hr | > 6× burn rate over 1h window |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Driver and executor memory with overhead | Review job submission script or `spark-defaults.conf` for `spark.driver.memory`, `spark.executor.memory`, `spark.executor.memoryOverhead` | `memoryOverhead` ≥ 10% of `executor.memory` or ≥ 384 MB; driver memory sized for result set and broadcast variables |
| Dynamic allocation configured | `grep -E 'dynamicAllocation\|initialExecutors\|minExecutors\|maxExecutors' /etc/spark/conf/spark-defaults.conf` | `spark.dynamicAllocation.enabled=true`; `minExecutors` > 0 for streaming jobs; `maxExecutors` set to prevent cluster monopolization |
| Shuffle service enabled for dynamic allocation | `grep 'spark.shuffle.service.enabled' /etc/spark/conf/spark-defaults.conf` | `spark.shuffle.service.enabled=true` when dynamic allocation is on; shuffle service running on all NodeManagers |
| Speculation enabled for long tail tasks | `grep 'spark.speculation' /etc/spark/conf/spark-defaults.conf` | `spark.speculation=true` for batch jobs; `spark.speculation.multiplier` ≤ 1.5 to avoid excessive duplicate work |
| Kryo serialization configured | `grep 'spark.serializer' /etc/spark/conf/spark-defaults.conf` | `spark.serializer=org.apache.spark.serializer.KryoSerializer`; `spark.kryo.registrationRequired=false` unless all classes are registered |
| Checkpoint directory on fault-tolerant storage | Review streaming job code or config for `streamingContext.checkpoint()` or `df.writeStream.option("checkpointLocation",...)` | Checkpoint path is on HDFS, S3, or GCS — not local filesystem; path unique per streaming query |
| Event log and history server configured | `grep -E 'eventLog\|historyServer' /etc/spark/conf/spark-defaults.conf` | `spark.eventLog.enabled=true`; `spark.eventLog.dir` on durable storage; Spark History Server pointing to same directory |
| Broadcast join threshold sized appropriately | `grep 'spark.sql.autoBroadcastJoinThreshold' /etc/spark/conf/spark-defaults.conf` | Value ≤ 100 MB (default 10 MB) to avoid broadcasting large tables to all executors; set to `-1` to disable if large broadcasts cause OOM |
| Adaptive Query Execution enabled (Spark 3.x) | `grep 'spark.sql.adaptive.enabled' /etc/spark/conf/spark-defaults.conf` | `spark.sql.adaptive.enabled=true`; `spark.sql.adaptive.coalescePartitions.enabled=true` for efficient post-shuffle partition sizing |

---

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `SparkContext: ERROR Failed to connect to master` | CRITICAL | Driver cannot reach the Spark master or YARN ResourceManager | Check master/ResourceManager availability; verify network connectivity and port 7077/8032 |
| `TaskSetManager: Lost task X in stage Y (TID Z): ExecutorLostFailure` | ERROR | Executor process died or was killed by OOM or preemption | Check executor logs for OOM; increase `spark.executor.memory`; check YARN node health |
| `DAGScheduler: Resubmitting failed stages` | WARN | One or more tasks failed and the stage is being retried | Investigate task failure cause in executor logs; check for data skew or corrupt input files |
| `ShuffleBlockFetcherIterator: Failed to get block` | ERROR | Shuffle block missing because executor that wrote it died before its blocks were read | Ensure shuffle service is enabled; increase `spark.shuffle.service.enabled=true`; retry the job |
| `java.lang.OutOfMemoryError: Java heap space` (executor) | CRITICAL | Executor heap exhausted; often caused by large broadcast variables, skewed partitions, or collect() calls | Increase `spark.executor.memory`; enable off-heap with `spark.memory.offHeap`; eliminate large in-driver collects |
| `SpillWriter: Task is spilling X bytes to disk` | WARN | Shuffle or aggregation exceeding on-heap memory; spilling to local disk | Increase executor memory; tune `spark.sql.shuffle.partitions` to reduce partition size; check for data skew |
| `ContextCleaner: Error cleaning accumulator` | WARN | Accumulator reference cleanup failed; possible memory leak over long-running jobs | Monitor driver memory; restart SparkContext if memory grows unboundedly; check for accumulator reference retention |
| `org.apache.spark.sql.AnalysisException: Table or view not found` | ERROR | DataFrame or SQL query references a catalog table not registered in the current SparkSession | Verify table exists in the metastore; check database context (`USE <db>`); confirm Hive metastore connectivity |
| `Streaming query made no progress in X seconds` | WARN | Structured Streaming micro-batch stalled; source may be empty or trigger interval too long | Check Kafka/source for messages; verify trigger interval; look for slow UDFs blocking batch processing |
| `TaskSchedulerImpl: Removed 0 executors due to idle timeout` | INFO | Dynamic allocation scaling down idle executors | Normal behavior; verify `minExecutors` is set appropriately for streaming jobs to prevent complete scale-down |
| `BlockManagerMaster: BlockManager removed due to heartbeat timeout` | ERROR | Executor lost contact with driver's BlockManager; blocks on that executor now inaccessible | Investigate network partition or GC pause on executor; restart executor; check GC logs for stop-the-world events |
| `DataSourceException: Path does not exist: s3a://bucket/path` | ERROR | Input path missing in S3 or HDFS; often caused by a failed upstream pipeline stage | Verify input data exists before job runs; add existence check in pipeline; investigate upstream producer failures |

---

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `FAILED` (Job state) | One or more stages in the job failed after all retry attempts | Job produces no output; downstream pipelines blocked | Inspect failed stage in Spark UI; check executor logs; fix data or configuration issue and resubmit |
| `KILLED` (Job state) | Job explicitly killed by user, timeout, or resource manager preemption | Job produces no output | Determine kill reason (preemption vs. manual); resubmit at lower priority or during off-peak window |
| `ExecutorLostFailure` | Executor process terminated unexpectedly | Tasks assigned to that executor are re-queued; if all attempts exhausted, stage fails | Check YARN container logs; look for OOM kills in `dmesg`; increase memory or reduce executor count |
| `FetchFailed` | Shuffle fetch from a dead executor failed; shuffle data unreachable | Entire stage requiring that shuffle data must be re-run | Enable external shuffle service; increase `spark.task.maxFailures`; investigate executor stability |
| `org.apache.spark.SparkException: Task failed while writing rows` | Write task failed during output commit; partial output may exist | Output data may be incomplete or corrupt | Check for `_SUCCESS` marker in output; clean partial output; rerun job; verify output path permissions |
| `AnalysisException: Resolved attribute(s) missing` | Column referenced in a DataFrame transformation does not exist in the schema | Job fails at planning stage; no data processed | Check column name spelling and case; verify schema before applying transformations; print schema for debugging |
| `StreamingQueryException: Query terminated with exception` | Structured Streaming query crashed due to an unhandled exception | Stream processing halted; data accumulating in source | Identify exception in query logs; fix root cause; restart query from checkpoint; add exception handling in UDFs |
| `YARN container exceeded memory limits` | Executor container exceeded its YARN memory allocation and was killed | Executor lost; tasks re-queued or job fails | Increase `spark.executor.memoryOverhead`; reduce executor memory per container; check for memory leak |
| `FileAlreadyExistsException` | Spark attempting to write to an output path that already exists | Write job fails before producing output | Delete existing output path or use `SaveMode.Overwrite`; check for duplicate job submissions |
| `MetadataFetchFailedException` | Shuffle metadata missing from MapOutputTracker / shuffle service; shuffle blocks lost | Stage requiring shuffle data cannot complete | Restart the external shuffle service; clear shuffle temp directories; resubmit the job |
| `CheckpointException: Failed to write checkpoint` | Streaming checkpoint write to HDFS/S3 failed | Streaming query cannot recover from failure; checkpoint integrity at risk | Check write permissions on checkpoint path; verify storage backend health; switch to durable checkpoint location |
| `NullPointerException in UDF` | User-defined function received null input without null-checking | Tasks fail for rows with null values; job may succeed with dropped rows if configured | Add null checks in UDF logic; use `Option` or `try/catch` in Scala UDFs; filter nulls upstream |

---

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Data Skew — Straggler Tasks | One or two tasks per stage taking 10x median duration; stage completion time dominated by stragglers | `SpillWriter: Task is spilling X bytes`; `ShuffleBlockFetcherIterator: huge block` | `SparkTaskSkewDetected` | Hot keys in groupBy or join causing unequal partition sizes | Salt skewed keys; use `repartition` on join key; enable AQE skew join optimization (`spark.sql.adaptive.skewJoin.enabled=true`) |
| Executor Memory Pressure Loop | Executor GC time > 20% of task time; OOM kills cycling; job makes no forward progress | `OutOfMemoryError: Java heap space`; `BlockManagerMaster removed`; GC overhead warnings | `SparkExecutorOOM` | Insufficient executor heap for data volume; large broadcast variables or uncached RDDs re-materialized | Increase executor memory; reduce broadcast threshold; cache intermediate DataFrames; increase partition count |
| Shuffle Service Down — FetchFailed Loop | All tasks in shuffle-read stages failing with FetchFailed; job retries exhausted | `ShuffleBlockFetcherIterator: Failed to get block`; `MetadataFetchFailedException` | `SparkShuffleFetchFailure` | External shuffle service stopped or unreachable on one or more NodeManagers | Restart NodeManager shuffle service; verify `spark.shuffle.service.enabled=true`; check NodeManager logs |
| Driver Out-of-Memory — collect() Abuse | Driver JVM heap growing to GC limit; job fails with driver OOM | `collect() called on large DataFrame`; `OutOfMemoryError` in driver logs | `SparkDriverOOM` | Application calling `collect()` or `toPandas()` on a large DataFrame, pulling all data to driver | Replace `collect()` with `write()` to disk; use `take(N)` for sampling; increase driver memory as stopgap |
| Checkpoint Write Failure — Stream Stall | Streaming query micro-batch rate drops to zero; checkpoint warnings in logs | `CheckpointException: Failed to write checkpoint`; `IOException: No space left on device` | `SparkStreamingCheckpointFailed` | Checkpoint directory storage full or permission revoked | Free storage space; fix permissions; switch checkpoint path; restart streaming query with intact checkpoint |
| S3A Throttling — Slow Input | Job input throughput drops; tasks taking 10x longer than normal; S3 429 errors in logs | `AmazonS3Exception: Slow Down`; `S3AInputStream: read timeout retry` | `SparkS3InputThrottled` | S3 request rate limit (3,500 PUT/s or 5,500 GET/s per prefix) exceeded | Add prefix randomization to S3 paths; reduce parallelism on S3 reads; use S3 Transfer Acceleration; batch reads with larger block sizes |
| Metastore Connectivity Loss | SparkSQL queries fail with AnalysisException; table registry unavailable | `MetaException: Unable to connect to metastore server`; `TTransportException` | `SparkMetastoreConnectionLost` | Hive Metastore service down or network partition between Spark cluster and metastore | Restart Hive Metastore service; check `hive.metastore.uris` config; verify network policies |
| Broadcast Timeout on Large Table Join | Join stage fails with timeout; executor logs show large broadcast serialization time | `SparkException: Could not execute broadcast in X secs`; `TimeoutException in broadcast` | `SparkBroadcastTimeout` | Table being broadcast exceeds the time limit for serialization and distribution to all executors | Increase `spark.sql.broadcastTimeout`; set `spark.sql.autoBroadcastJoinThreshold=-1` to force sort-merge join for the large table |
| YARN Preemption Loop | Job executors repeatedly killed and re-requested; job makes slow progress | `ExecutorLostFailure (executor X exited caused by: YarnPreemptionException)`; re-add executor events | `SparkYarnPreemptionHigh` | Spark application running in YARN queue with preemption enabled; higher-priority applications claiming resources | Submit to a dedicated queue with preemption disabled; reduce executor count; use resource reservations |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `org.apache.spark.SparkException: Job aborted due to stage failure` | PySpark, Spark Scala API | Task failures exhausting retry limit; executor OOM or host failure | Spark UI → Stages → Failed Tasks → exception detail | Fix root cause (OOM, data skew, host failure); increase `spark.task.maxFailures` |
| `AnalysisException: Table or view not found` | PySpark DataFrame API, Spark SQL | Metastore catalog out of sync; table dropped externally; database not set | `spark.catalog.tableExists('<table>')` | Refresh catalog: `spark.catalog.refreshTable()`; verify metastore connectivity |
| `FileNotFoundException: Input path does not exist` | PySpark, Spark SQL | Source file or partition deleted before job ran; S3 eventual consistency | `hadoop fs -ls <path>` or `aws s3 ls <path>` | Add pre-check for path existence; use S3 strong consistency (S3:ListObjectsV2) |
| `ClassCastException` in task | PySpark, Java/Scala | Schema mismatch between serialized data and expected schema; incompatible Kryo serialization | Spark UI task exception detail; check schema inference | Enforce explicit schema on read; avoid schema inference on production data |
| `OutOfMemoryError: Java heap space` (driver) | PySpark | `collect()` or `toPandas()` called on large DataFrame; broadcast variable too large | Spark UI → Driver logs; driver heap metrics | Replace `collect()` with `write()`; reduce broadcast threshold |
| `SparkException: Task not serializable` | PySpark, Scala | Lambda capturing non-serializable object (DB connection, logger) | Stack trace shows `NotSerializableException` | Move non-serializable code inside task function; use `@transient lazy val` in Scala |
| `ExecutorLostFailure` | PySpark, Spark Scala | Executor OOM-killed by YARN/K8s; node failure | YARN ResourceManager logs; K8s pod eviction events | Increase executor memory; check GC overhead; add more executors |
| `FetchFailed exception` | PySpark, Spark Scala | Shuffle data lost from executor; external shuffle service down | Spark UI shows FetchFailed in shuffle-read stage | Restart NodeManager shuffle service; enable `spark.shuffle.service.enabled` |
| `Connection refused` to Spark history server | Spark History Server UI | History server down or port not exposed | `curl http://<history-server>:18080` | Restart history server; check `spark.history.fs.logDirectory` mount |
| `Py4JJavaError: An error occurred while calling` | PySpark | JVM side threw exception propagated to Python; see nested Java exception | Stack trace inner `java.lang.` exception | Resolve inner Java exception; ensure JVM heap is adequate |
| `GpuSemaphore timeout` | RAPIDS Accelerator for Spark | GPU memory deadlock between concurrent Spark tasks on same GPU | GPU utilization: `nvidia-smi`; RAPIDS log `GpuSemaphore` messages | Reduce `spark.rapids.sql.concurrentGpuTasks`; add more GPU nodes |
| `IllegalStateException: SparkContext has been shutdown` | PySpark | Driver crashed and restarted; or `SparkContext.stop()` called prematurely | Spark driver logs; check for unhandled exception before `stop()` | Add exception handling around driver code; use `spark.stop()` in `finally` block only |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Data skew accumulating over time | Stage median vs max task duration diverging; shuffle partition size distribution widening | Spark UI → Stage details → Task Duration histogram | Per job | Enable AQE skew join: `spark.sql.adaptive.skewJoin.enabled=true`; add salting |
| Shuffle spill to disk growing | Job duration increasing despite constant data volume; disk IOPS rising on executors | Spark UI → Stage metrics: `Shuffle Spill (Disk)` column | Hours | Increase executor memory or partition count; tune `spark.sql.shuffle.partitions` |
| Driver memory leak from accumulator abuse | Driver JVM old-gen growing over multiple job runs; eventual GC pauses | Driver JVM heap via `jstat -gcutil <driver-pid>` or Spark UI | Across job runs | Limit accumulator registration; clear accumulators after use; restart driver periodically |
| S3 throttling under growing partition count | Job input read throughput decreasing; S3 429 errors increasing; retries accumulating | AWS CloudWatch `S3 ThrottledRequests` metric | Weeks | Add prefix randomization; increase partition granularity; use S3 Transfer Acceleration |
| Executor GC time rising | GC time fraction growing toward 20% of task CPU time; throughput dropping | Spark UI → Executor tab: `GC Time` column | Hours to days | Increase executor heap; reduce broadcast table size; tune G1GC settings |
| Metastore connection pool exhaustion | Spark SQL queries hanging at metadata fetch; Hive Metastore logs showing max connections | Hive Metastore logs: `Connection pool exhausted`; `spark.hadoop.hive.metastore.client.socket.timeout` | Hours | Increase metastore pool size; reduce concurrent Spark SQL sessions |
| Checkpoint directory bloat | Checkpoint storage filling; streaming micro-batch latency increasing as checkpoint write slows | `hadoop fs -du -s <checkpoint-dir>` | Days | Trim old checkpoint data; increase checkpoint storage; adjust checkpoint interval |
| YARN resource queue saturation | Jobs queuing for resources; queue wait time trending upward during business hours | YARN ResourceManager UI → Queue metrics; `yarn queue -status <queue>` | Hours | Expand queue capacity; pre-emptively schedule large jobs off-peak |
| Result cache filling in Spark Thrift Server | Repeated queries not returning from cache; result cache hit rate declining | Spark Thrift Server metrics: `ThriftServerPool`; `resultCacheSize` | Days | Tune `spark.sql.server.response.cache.size`; reduce result TTL |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Spark full health snapshot
set -euo pipefail
SPARK_MASTER="${SPARK_MASTER_URL:-http://localhost:8080}"
HISTORY_SERVER="${SPARK_HISTORY_URL:-http://localhost:18080}"
echo "=== Spark Health Snapshot: $(date) ==="
echo "--- Master Status ---"
curl -sf "${SPARK_MASTER}/json/" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('Status:', d.get('status','?'))
print('Workers:', len(d.get('workers',[])))
print('Running Apps:', len(d.get('activeapps',[])))
print('Waiting Apps:', len(d.get('waitingapps',[])))
" 2>/dev/null || echo "Cannot reach Spark Master (may be running on YARN/K8s)"
echo "--- YARN Application Status ---"
yarn application -list 2>/dev/null | head -30 || echo "YARN CLI not available"
echo "--- K8s Spark Pods ---"
kubectl get pods -n spark --field-selector=status.phase!=Running 2>/dev/null | head -20 || true
echo "--- History Server Recent Apps ---"
curl -sf "${HISTORY_SERVER}/api/v1/applications?limit=10&status=failed" 2>/dev/null | \
  python3 -c "
import sys, json
apps = json.load(sys.stdin)
for a in apps:
    att = a.get('attempts',[{}])[-1]
    print(f'App: {a.get(\"name\",\"?\")} ID: {a.get(\"id\",\"?\")} Duration: {att.get(\"duration\",0)//1000}s Completed: {att.get(\"completed\",\"?\")}')
" 2>/dev/null || echo "History server unreachable"
echo "--- Executor Count (YARN) ---"
yarn application -list -appStates RUNNING 2>/dev/null | grep -E 'application_' | while read line; do
  APP_ID=$(echo "$line" | awk '{print $1}')
  echo "$APP_ID: $(yarn applicationattempt -list "$APP_ID" 2>/dev/null | grep -c 'container'|| echo '?') executors"
done || true
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Spark performance triage via Spark REST API
HISTORY_SERVER="${SPARK_HISTORY_URL:-http://localhost:18080}"
APP_ID="${1:-}"
echo "=== Spark Performance Triage: $(date) ==="
if [ -z "$APP_ID" ]; then
  echo "Usage: $0 <app-id>   (or set APP_ID env var)"
  echo "--- Listing recent failed apps ---"
  curl -sf "${HISTORY_SERVER}/api/v1/applications?limit=5&status=failed" | \
    python3 -c "import sys,json; [print(a['id'], a.get('name','?')) for a in json.load(sys.stdin)]" 2>/dev/null
  exit 0
fi
echo "--- Stage Summary for ${APP_ID} ---"
curl -sf "${HISTORY_SERVER}/api/v1/applications/${APP_ID}/stages" | python3 -c "
import sys, json
stages = json.load(sys.stdin)
for s in stages:
    print(f'Stage {s[\"stageId\"]} ({s[\"status\"]}): inputBytes={s.get(\"inputBytes\",0)//1024//1024}MB, shuffleWriteBytes={s.get(\"shuffleWriteBytes\",0)//1024//1024}MB, spillDisk={s.get(\"diskBytesSpilled\",0)//1024//1024}MB, maxTaskDuration={s.get(\"maxTaskDuration\",0)//1000}s')
" 2>/dev/null
echo "--- Executor Summary ---"
curl -sf "${HISTORY_SERVER}/api/v1/applications/${APP_ID}/executors" | python3 -c "
import sys, json
execs = json.load(sys.stdin)
for e in execs[:20]:
    print(f'Exec {e[\"id\"]}: tasks={e[\"totalTasks\"]}, failed={e[\"failedTasks\"]}, gcTime={e[\"totalGCTime\"]//1000}s, peakMem={e.get(\"peakMemoryMetrics\",{}).get(\"JVMHeapMemory\",0)//1024//1024}MB')
" 2>/dev/null
echo "--- Failed Tasks ---"
curl -sf "${HISTORY_SERVER}/api/v1/applications/${APP_ID}/stages?status=FAILED" | python3 -c "
import sys, json
stages = json.load(sys.stdin)
for s in stages:
    print(f'Failed Stage {s[\"stageId\"]}: {s.get(\"failureReason\",\"no reason\")}')
" 2>/dev/null
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Spark connection and resource audit
echo "=== Spark Connection & Resource Audit: $(date) ==="
echo "--- YARN Node Manager Status ---"
yarn node -list 2>/dev/null | head -30 || echo "YARN not available"
echo "--- K8s Executor Pods ---"
kubectl get pods -n spark -l spark-role=executor 2>/dev/null | \
  awk '{print $1, $3, $4}' | sort -k2 | head -30 || true
echo "--- Driver JVM Heap (if local) ---"
DRIVER_PID=$(pgrep -f 'SparkSubmit\|spark-submit' | head -1)
if [ -n "$DRIVER_PID" ]; then
  jstat -gcutil "$DRIVER_PID" 1 3 2>/dev/null || echo "jstat not available"
  echo "Open FDs: $(ls /proc/$DRIVER_PID/fd 2>/dev/null | wc -l)"
fi
echo "--- HDFS / S3 Quota Check ---"
hadoop fs -df -h / 2>/dev/null | head -5 || echo "Hadoop FS not configured"
echo "--- Hive Metastore Connectivity ---"
beeline -u "jdbc:hive2://${METASTORE_HOST:-localhost}:10000" -e "SHOW DATABASES;" 2>/dev/null | head -10 || \
  echo "Cannot connect to Hive Metastore via beeline"
echo "--- Shuffle Service Ports ---"
ss -tnlp | grep -E '7337|7338' || echo "External shuffle service ports not found"
echo "--- Spark Event Log Directory Size ---"
hadoop fs -du -s "${SPARK_EVENTLOG_DIR:-/spark-logs}" 2>/dev/null || \
  du -sh "${SPARK_EVENTLOG_DIR:-/tmp/spark-events}" 2>/dev/null || echo "Event log dir not accessible"
echo "--- ZooKeeper Connectivity (Spark HA) ---"
ZK="${SPARK_ZK:-}"
if [ -n "$ZK" ]; then
  echo ruok | nc "${ZK%%:*}" "${ZK##*:}" && echo "ZK OK" || echo "ZK UNREACHABLE"
fi
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Data skew hot partition monopolizing executor | One task per stage runs 10–100x longer than others; overall stage dominated by straggler | Spark UI → Stage task distribution; identify max duration task vs median | Salt skewed join/group-by keys; use AQE skew join (`spark.sql.adaptive.skewJoin.enabled=true`) | Profile key distribution before writing joins; use approximate count-distinct to detect skew early |
| Broadcast variable exceeding executor heap | Executors OOM after broadcast join; GC time spiking on all executors | Spark UI executor tab: high GC time; task OOM exceptions with broadcast variable in stack | Set `spark.sql.autoBroadcastJoinThreshold=-1` to disable broadcast; use sort-merge join | Profile table sizes before joins; set broadcast threshold conservatively (< 100 MB) |
| YARN queue over-subscription | Multiple Spark jobs fighting for containers; frequent container pre-emption; job slowdown | YARN RM UI: queue utilization > 100%; pre-emption events in YARN logs | Enable YARN pre-emption policy per queue; assign capacity guarantees per team | Define capacity scheduler queues with max-capacity limits per team/environment |
| Shuffle write amplification from too-small partitions | Excessive small shuffle files; NameNode metadata pressure; task scheduling overhead | Spark UI: thousands of shuffle partitions of < 1 MB each; NameNode RPC latency rising | Increase `spark.sql.shuffle.partitions`; use AQE coalesce (`spark.sql.adaptive.coalescePartitions.enabled`) | Set `spark.sql.shuffle.partitions` based on data size (target 100–200 MB per partition) |
| S3 prefix hot-spot from poor partitioning | S3 throttling errors; input throughput dropping; 429 errors in S3A logs | AWS CloudWatch `S3 ThrottledRequests` per prefix; Spark task retry logs | Randomize prefix path (`/year=2024/month=01/rand=<hash>/`); increase S3 request rate via prefix spreading | Design partition scheme with multiple prefixes; avoid date-only prefix at high ingest rates |
| Shared cluster resource starvation during ETL peak | Interactive Spark SQL sessions timing out during nightly batch jobs | YARN RM: all cluster containers consumed by batch application IDs | Pre-empt lower-priority jobs; reserve capacity for interactive queue | Use YARN fair scheduler with minimum shares for interactive queue; schedule batch jobs with lower priority |
| Executor JVM old-gen pressure from caching | Cached DataFrames consuming old-gen heap; GC overhead > 15% of task time | Spark UI executor: high GC time; `SHOW SPARK CACHED DATA` or `sc.getRDDStorageInfo()` | Unpersist stale caches: `df.unpersist()`; use `MEMORY_AND_DISK` storage level | Limit RDD/DataFrame cache to frequently reused DataFrames; set `spark.memory.fraction` appropriately |
| Hive Metastore connection storm on job start | Metastore connection pool exhausted at start of parallel Spark SQL jobs; metadata fetch hangs | Hive Metastore logs: `Too many connections`; Spark executor logs: `MetaException timeout` | Stagger job launches; increase Metastore `hive.server2.thrift.max.worker.threads` | Use a Metastore connection pool proxy (e.g., HiveServer2 with connection pooling); add Metastore read replicas |
| Driver accumulator registration leak | Driver heap filling over long-running streaming session; GC frequency growing | Driver JVM heap trend; Spark UI: large number of accumulators registered | Clear accumulators at end of each micro-batch; restart driver periodically | Avoid registering accumulators inside streaming `foreachBatch`; use Spark metrics sinks instead |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Hive Metastore becomes unavailable | Spark SQL jobs fail at table resolution; all jobs requiring metadata hang at `HiveMetaStoreClient` init | All Spark SQL workloads cluster-wide; streaming jobs block on schema fetch | `MetaException: Could not connect to meta store`; Metastore JVM OOM or network timeout in driver logs | Switch to fallback `--conf spark.sql.catalogImplementation=in-memory`; cache schema locally for recovery |
| HDFS NameNode enters safe mode | RDD reads and shuffle writes fail with `org.apache.hadoop.ipc.RemoteException: NameNode in safemode`; tasks retry until stage fails | All cluster Spark jobs; S3-backed jobs unaffected | `hdfs dfsadmin -safemode get` returns ON; HDFS exception in driver logs | Route reads to S3/object store fallback; pause job submissions until `hdfs dfsadmin -safemode leave` |
| YARN ResourceManager crash | Pending container requests unserviced; running executors lose heartbeat and are blacklisted; Driver throws `ApplicationMaster failed` | All YARN-mode Spark jobs on the cluster | YARN RM process absent from `yarn node -list`; `spark.yarn.maxAppAttempts` exhausted in driver logs | Enable YARN RM HA with automatic failover via ZooKeeper; re-submit jobs after failover completes |
| ZooKeeper quorum loss (Spark HA mode) | Spark Standalone master unable to elect leader; worker registration fails; running jobs continue until executor heartbeat timeout | Spark Standalone cluster management; new job submissions blocked | `zk: session expired` in Spark master logs; `spark://master:7077` unreachable | Restore ZooKeeper quorum (requires majority of nodes); Spark masters will auto-elect after ZK recovery |
| External shuffle service crash on worker node | All tasks with shuffle dependencies on that node fail with `FetchFailed`; stage retried up to `spark.stage.maxConsecutiveAttempts` times | Spark jobs using sort-merge join or `groupByKey` with data resident on failed worker | `FetchFailed(BlockManagerId(...))` in executor logs; worker node removed from application | Restart external shuffle service (`/etc/init.d/spark-shuffle restart`); enable `spark.shuffle.service.enabled=true` with retries |
| Kafka topic partition leader election lag during Structured Streaming | Consumer stalls; `OffsetOutOfRangeException` or `LeaderNotAvailableException`; micro-batch trigger delays cascade into checkpoint lag | Structured Streaming jobs consuming that topic; downstream data consumers see delays | `WARN KafkaDataConsumer: Retrying`; Kafka consumer group lag rising in `kafka-consumer-groups.sh --describe` | Increase `spark.streaming.kafka.consumer.pollTimeoutMs`; enable `startingOffsets=latest` for recovery; scale Kafka brokers to finish election faster |
| Driver host OOM (runs out of memory collecting large results) | Driver crashes with `java.lang.OutOfMemoryError: Java heap space`; job fails; dependent downstream jobs skip or fail | Single Spark job plus all jobs consuming its output or waiting on its completion | Driver pod evicted in K8s; YARN AppMaster container killed; `exit code 137` in application logs | Avoid `collect()` on large DataFrames; use `df.write` to storage instead; increase `spark.driver.memory` | 
| S3 eventual consistency race during write-then-read | Downstream job reads stale/empty partition immediately after write completes; zero-record outputs propagate to BI layer | Any Spark job chained immediately after a write to S3 without a committed marker check | Downstream job producing zero rows; `_SUCCESS` file present but partition files missing | Use Delta Lake / Iceberg to provide ACID commit semantics; add `Thread.sleep` + retry on empty partition detection |
| Executor heartbeat timeout under GC pressure | Executors removed by driver after `spark.executor.heartbeatInterval` missed; tasks re-scheduled; data locality lost; job slowdown or failure | Individual job losing executors; cascades to increased load on remaining executors triggering more GC | `ExecutorLostFailure: Executor heartbeat timed out after 120000 ms` in driver logs | Increase `spark.executor.heartbeatInterval` and `spark.network.timeout`; tune GC with `-XX:+UseG1GC -XX:G1HeapRegionSize` | 
| Metastore Derby (embedded) file lock contention in single-node testing | Only one Spark context can hold the Derby lock; second job fails at startup | Local Spark development environments; not production with MySQL/PostgreSQL Metastore | `ERROR DataNucleus.Datastore: Error thrown executing... derby.log`; `Failed to start database 'metastore_db'` | Stop other SparkContext before starting new one; delete and recreate `metastore_db`; switch to Hive remote Metastore |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Spark version upgrade (e.g. 3.3 → 3.4) | Breaking SQL behavior change: `ANSI mode` defaults changed; decimal arithmetic precision differs; job silently produces wrong results | Immediately on first job run post-upgrade | Compare job output row counts and sample values before/after; check Spark migration guide for behavior changes | Pin `spark.sql.ansi.enabled=false`; revert JAR to prior version in `SPARK_HOME`; re-run affected jobs |
| Executor memory config increase without YARN max container adjustment | Executors fail to allocate; `InvalidResourceRequestException: Requested executor memory ... exceeds maximum allowed`; jobs hang waiting for containers | Immediately on job submission | `yarn application -status <app_id>` showing ACCEPTED but no containers launched; YARN RM logs | Reduce `spark.executor.memory` to fit within `yarn.scheduler.maximum-allocation-mb`; or increase YARN max allocation |
| Shuffle partition count change (`spark.sql.shuffle.partitions`) | Jobs produce too-small or too-large shuffle files; downstream joins run OOM or performance regresses dramatically | First job run post-change | Compare task metrics in Spark UI before/after; check shuffle bytes and partition count in Stage tab | Revert to prior `spark.sql.shuffle.partitions` value; use AQE to auto-tune instead |
| Delta Lake table schema evolution (new column added with NULL constraint) | Existing Spark streaming jobs fail with `AnalysisException: Column constraints not met` on append | On first micro-batch after schema change | Check Delta Lake schema with `DESCRIBE TABLE delta.\`/path/to/table\``; correlate with deployment timestamp | Use `spark.databricks.delta.schema.autoMerge.enabled=true`; add explicit schema evolution handling in stream |
| Python/PySpark dependency change in virtualenv | PySpark UDFs fail with `ModuleNotFoundError`; Spark workers cannot deserialize function | Immediately on UDF execution | Executor logs show `ModuleNotFoundError: No module named <pkg>`; correlate with requirements.txt change | Rebuild and redistribute `--archives` with correct virtualenv; or use `spark.files` to ship env |
| Hadoop configuration change (core-site.xml, hdfs-site.xml) | S3A authentication failures (`AccessDeniedException`); HDFS block replication factor changes causing write errors | On next job submission picking up new config | `hdfs getconf -confKey <key>` to compare; check Spark driver logs for HDFS errors after config change | Revert `core-site.xml`/`hdfs-site.xml` to previous version; redistribute config to all nodes |
| Kryo serializer added / changed registration | Tasks fail with `KryoException: Class is not registered`; streaming jobs fail on checkpoint restore | Immediately on task execution with the changed class | Executor logs: `KryoException: Class is not registered: com.example.MyClass`; correlate with code change | Register missing class: `conf.registerKryoClasses(Array(classOf[MyClass]))`; or revert to Java serialization temporarily |
| YARN capacity scheduler queue config change | Jobs land in wrong queue; priority inversions; previously fast jobs now queued behind lower-priority workloads | Immediately on next scheduler refresh cycle (seconds to minutes) | `yarn queue -status <queue>` to inspect capacity; check submitted application queue assignment | Revert `capacity-scheduler.xml` via YARN ResourceManager config reload; `yarn rmadmin -refreshQueues` |
| Broadcast join threshold increased | OOM on executors when large table is broadcast; `SparkException: Could not execute broadcast in 300 secs` | On first job encountering a table between old and new threshold size | Correlate OOM time with threshold change; check `spark.sql.autoBroadcastJoinThreshold` value | Reduce `spark.sql.autoBroadcastJoinThreshold` back to previous value or `-1`; increase executor memory |
| Log4j / logging framework upgrade in Spark assembly JAR | `NoClassDefFoundError` or class loading conflicts at job startup; or log appender silently dropping events | Immediately at driver/executor startup | Driver STDERR: `ClassNotFoundException: org.apache.log4j.XXX`; correlate with fat JAR rebuild | Revert logging JAR version; use `spark.jars` to override specific logging JARs; ensure log4j2 bridge is present |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Delta Lake concurrent write conflict (optimistic concurrency clash) | `DESCRIBE HISTORY delta.\`/path/to/table\`` — look for concurrent `WRITE` operations with overlapping versions | `ConcurrentAppendException` or `ConcurrentDeleteReadException` in Spark logs; some writer jobs fail and data is partially missing | Data loss for the failing write; downstream reads may be consistent but incomplete | Implement retry logic in writer catching `ConcurrentModificationException`; use `MERGE` with `WHEN NOT MATCHED` to avoid duplication |
| Checkpoint corruption in Structured Streaming | Stream fails at restart with `StreamingQueryException: Stream failed to restart from checkpoint`; mismatched offset state | Job refuses to restart; attempting to re-process events causes duplicate data | Possible duplicate processing or data loss depending on source offset position | Delete corrupted checkpoint directory; restart stream with `startingOffsets=latest` accepting potential gap; or restore checkpoint from backup |
| Hive partition metadata out of sync with actual HDFS/S3 data | Spark SQL queries return zero rows or stale data for partitions that physically exist | `SELECT count(*) FROM tbl WHERE dt='2024-01-01'` returns 0 despite data files present in HDFS path | Silently wrong query results; downstream dashboards show missing data | Run `MSCK REPAIR TABLE <tbl>` to sync Metastore partition metadata with actual storage |
| Clock skew between Spark driver and workers causing watermark errors | Structured Streaming watermark advancing too fast or too slow; events being dropped as late data | Driver and worker timestamps diverge > `spark.sql.streaming.statefulOperator.allowMultipleStatefulOperators`; late-data metrics growing | Events incorrectly classified as late; data silently dropped from aggregations | Enable NTP sync across all cluster nodes (`chronyc tracking`); increase `withWatermark` delay to tolerate clock skew |
| Delta Lake transaction log truncation before old checkpoint reads | Concurrent job reading an old snapshot fails with `VersionNotFoundException`; log cleanup ran while job was in-flight | `DeltaAnalysisException: Cannot time travel Delta table to version X` in job logs | Long-running jobs fail mid-execution; incomplete output written | Increase `delta.logRetentionDuration` and `delta.deletedFileRetentionDuration`; use `RESTORE TABLE` to recover |
| Kafka offset commit lag causing duplicate processing | Structured Streaming restarts process already-committed micro-batches; duplicate rows appear in sink | `SELECT count(*) - count(DISTINCT event_id) AS dupes FROM sink_table` | Duplicate records in downstream tables; idempotency violation | Use idempotent sinks (Delta `MERGE` or Kafka transactional producer); verify checkpoint integrity before restart |
| RDD caching stale data after source updated | Cached RDD returns old data after source table updated; job computes on stale snapshot | `spark.catalog.isCached("table_name")` returns true; query results differ from direct source read | Analytics queries return outdated results until cache is manually invalidated | `spark.catalog.refreshTable("table_name")`; uncache with `spark.catalog.uncacheTable("table_name")` |
| Parquet schema mismatch on partition read (schema evolution not enabled) | `SparkException: Parquet column name mapping failed`; reads on evolving schema throw exception | Column type change in upstream writer; Parquet files have incompatible schemas across partitions | Job fails or silently returns NULLs for mismatched columns | Enable `spark.sql.parquet.mergeSchema=true`; rewrite partition with consistent schema using `INSERT OVERWRITE` |
| S3 multi-part upload partial commit from killed executor | Some tasks succeed but others are killed mid-upload; final `_SUCCESS` written but files incomplete | `SELECT count(*) FROM tbl PARTITION (dt='...')` vs `hadoop fs -count` of actual files disagree | Partially populated partition; downstream aggregations silently undercounted | Abort incomplete multipart uploads: `aws s3api list-multipart-uploads --bucket <b> && aws s3api abort-multipart-upload`; rerun the failed partition |
| Broadcast variable out-of-sync across executors after update | All executors use the first broadcast value; updated value not propagated; join returns wrong matches | Custom code that calls `broadcast.destroy()` and re-broadcasts is missing; stale variable reference | Silent wrong-result bug in joins or lookups using the broadcast | Always call `broadcast.unpersist(blocking=true)` before re-broadcasting; use accumulator or external store for mutable state |

## Runbook Decision Trees

### Decision Tree 1: Spark Job Failure / Stage Hung
```
Is the Spark driver process running?
├── YES → Is the stage making progress? (check: spark UI http://<driver>:4040/stages/)
│         ├── YES → Check for data skew: tasks with runtime >3x median? (Spark UI → Stage → Task Metrics)
│         │         ├── YES → Root cause: data skew → Fix: repartition(N) or salt join keys; increase spark.sql.shuffle.partitions
│         │         └── NO  → Check speculation: `curl http://<driver>:4040/api/v1/applications/<id>/stages` look for speculativeTasks
│         └── NO  → Is executor count dropping? (check: `yarn application -status <app_id>`)
│                   ├── YES → Root cause: executor eviction (low YARN memory) → Fix: yarn.nodemanager.vmem-check-enabled=false or increase executor memoryOverhead
│                   └── NO  → Check shuffle service: `journalctl -u spark-shuffle -n 50`; restart if errors present
└── NO  → Is driver log showing OOM? (check: `yarn logs -applicationId <id> | grep -i "OutOfMemory\|GC overhead"`)
          ├── YES → Root cause: driver OOM → Fix: increase spark.driver.memory; move aggregations to executor side
          └── NO  → Is driver log showing connection refused? (check: `yarn logs -applicationId <id> | grep "Connection refused"`)
                    ├── YES → Root cause: ResourceManager unreachable → Fix: `yarn rmadmin -getServiceState rm1`; failover if needed
                    └── NO  → Escalate: on-call data platform team with app_id, driver logs, YARN event log location
```

### Decision Tree 2: Slow Job / Performance Regression
```
Is job duration >2x historical baseline? (check: Spark History Server http://<history>:18080)
├── YES → Is input data volume proportionally larger?
│         ├── YES → Expected regression: trigger autoscaling or increase num-executors; document in SLO notes
│         └── NO  → Is there a long GC pause in executor logs? (check: `yarn logs -applicationId <id> | grep "GC pause"`)
│                   ├── YES → Root cause: JVM GC pressure → Fix: switch to G1GC (`-XX:+UseG1GC`); increase executor memory
│                   └── NO  → Are shuffle spill metrics high? (check: Spark UI → Executors → Shuffle Spill (Disk))
│                             ├── YES → Root cause: insufficient executor memory → Fix: increase spark.executor.memory or reduce partition size
│                             └── NO  → Escalate: check network throughput between nodes (`iperf3`); storage I/O latency (`iostat -xz 5`)
└── NO  → Monitor: set alert threshold at 1.5x baseline duration; re-evaluate in next sprint
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Executor count explosion | Autoscaler loop / misconfigured max | `yarn application -list \| awk '{print $1}' \| xargs -I{} yarn application -status {} \| grep "Num Used Containers"` | Exhausts cluster YARN capacity, blocks other jobs | `yarn application -kill <app_id>`; set `spark.dynamicAllocation.maxExecutors` | Always set `maxExecutors`; review autoscaler logs weekly |
| Runaway shuffle storage | Large skewed shuffle not cleaned up | `hdfs dfs -du -s /tmp/hadoop-yarn/nm-local-dir/usercache/*/appcache/` | Fills local disk on all worker nodes, causes OOM evictions | Delete stale shuffle dirs: `find /tmp/hadoop-yarn -name "shuffle_*" -mmin +120 -delete` | Enable `spark.shuffle.service.index.cache.size`; set TTL on temp dirs |
| Driver memory leak | Long-running structured streaming job accumulating state | `jmap -histo:live <driver_pid> \| head -30` | Driver OOM → job crash, potential duplicate processing | Restart driver with checkpoint; set `spark.streaming.stopGracefullyOnShutdown=true` | Set `spark.sql.streaming.statefulOperator.checkCorrectness.enabled=true`; add state TTL |
| Excessive S3 API calls | Partition discovery on deeply nested path | `aws s3api get-bucket-metrics-configuration --bucket <bucket>` + CloudWatch S3 RequestCount | S3 request throttling (503), cost spike | Add `basePath` hint; use `spark.sql.hive.manageFilesourcePartitions=true` | Partition your data; use manifest files instead of directory listing |
| Job resubmission storm | Retry loop in orchestrator without backoff | `yarn application -list -appStates ACCEPTED \| wc -l` | YARN scheduler queue saturation | Drain queue: kill accepted apps; fix orchestrator retry logic | Implement exponential backoff + max retry cap in job scheduler |
| Broadcast join OOM | Large broadcast threshold misconfigured | `Spark UI → SQL → plan → BroadcastExchange size` | Executor OOM across all nodes simultaneously | Set `spark.sql.autoBroadcastJoinThreshold=-1`; replan with sort-merge join | Review join plans in CI; enforce broadcast size limits in code review |
| Checkpoint accumulation | Streaming job writing checkpoints without cleanup | `hdfs dfs -du -h /checkpoints/<job>/` | HDFS quota exhaustion | Delete old checkpoint versions: `hdfs dfs -rm -r /checkpoints/<job>/offsets/<old>` | Set checkpoint retention policy; use Kafka offset reset instead of HDFS checkpoints |
| Dynamic partition overwrite storm | INSERT OVERWRITE with dynamic partitions touching all partitions | `Spark UI → SQL → numOutputRows vs numOutputPartitions` | S3/HDFS write throttling, slow metastore updates | Switch to static partition writes; reduce parallelism with `spark.sql.shuffle.partitions` | Use `spark.sql.sources.partitionOverwriteMode=dynamic` only where required |
| Speculation task multiplication | Speculation enabled on skewed jobs | `Spark UI → Stage → speculative tasks count` | 2-3x compute cost on affected stages | Disable speculation for this job: `--conf spark.speculation=false` | Enable speculation only for embarrassingly parallel jobs; disable for skewed ETL |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot partition / data skew | Single task takes 100x longer than median; stage never completes | `Spark UI → Stages → Task Duration distribution → sort by Max` | Non-uniform key distribution; skewed join key or GROUP BY column | Salt skewed keys: `df.withColumn("key", concat(col("key"), lit("_"), (rand()*10).cast("int")))`; use AQE `spark.sql.adaptive.skewJoin.enabled=true` |
| Executor connection pool exhaustion | JDBC source tasks fail with "No available connection"; executor logs show connection timeout | `Spark UI → Executors → stderr → grep "HikariPool\|connection timeout"` | `numPartitions` > JDBC pool `maximumPoolSize`; too many concurrent JDBC reads | Lower `numPartitions` in JDBC read; set `connectionTimeout` and `maximumPoolSize` in JDBC URL params; use connection pooling proxy (PgBouncer) |
| GC / JVM memory pressure | Long GC pauses in executor logs; tasks fail with `java.lang.OutOfMemoryError` | `yarn logs -applicationId <id> | grep "GC pause"` or `Spark UI → Executors → GC Time` | Insufficient `spark.executor.memory`; high object churn from UDFs | Switch to G1GC: `--conf spark.executor.extraJavaOptions="-XX:+UseG1GC"`; increase `spark.executor.memory` or tune `spark.memory.fraction`; vectorize UDFs |
| Thread pool saturation on driver | Driver unresponsive; heartbeat timeouts on executors | `curl http://<driver>:4040/api/v1/applications/ | python3 -m json.tool`; check driver stderr for `ThreadPoolExecutor` warnings | Too many concurrent actions submitted to driver; event loop blocked | Reduce parallelism on driver; increase `spark.driver.cores`; avoid `.collect()` patterns in loops |
| Slow shuffle / shuffle spill | Stage progress stalls; disk I/O spikes on worker nodes | `Spark UI → Stages → Shuffle Spill (Disk) column`; `iostat -xz 1` on worker nodes | Partition sizes exceed `spark.executor.memory * spark.memory.fraction`; `spark.sql.shuffle.partitions` too low | Increase `spark.sql.shuffle.partitions`; reduce partition size; enable `spark.shuffle.compress=true`; use SSDs for shuffle |
| CPU steal on shared YARN cluster | Tasks run slower than benchmarks; no GC or I/O explanation | `vmstat 1 10` on worker nodes — check `st` column; `mpstat -P ALL 1 5` | Noisy neighbor VMs on same hypervisor; over-provisioned YARN node resources | Request dedicated node pool for Spark jobs; reduce `yarn.nodemanager.resource.cpu-vcores` to match physical cores |
| Lock contention in Delta Lake writes | Concurrent writers stall; `_delta_log` transaction retry log fills | `hdfs dfs -ls /data/table/_delta_log/ | wc -l`; executor logs: `grep "TransactionConflict\|ConcurrentAppendException"` | Multiple concurrent Delta writers competing on same partition path | Use `MERGE` instead of `INSERT OVERWRITE`; partition data to reduce writer overlap; enable `optimisticConcurrencyControl` |
| Java serialization overhead | Shuffle-heavy jobs slow despite adequate memory; executor CPU high on shuffle | `Spark UI → Executors → Shuffle Write Time vs Shuffle Write Size`; enable `spark.eventLog.logStageExecutorMetrics=true` | Default Java serialization for non-primitive objects; UDFs using non-Kryo serializable types | Enable Kryo: `spark.serializer=org.apache.spark.serializer.KryoSerializer`; register custom classes with `spark.kryo.registrationRequired=false` |
| Batch size misconfiguration on Kafka source | Structured Streaming micro-batches process 1 record at a time; throughput far below Kafka write rate | `Spark UI → Streaming → Input Rate vs Processing Rate`; check `maxOffsetsPerTrigger` | `maxOffsetsPerTrigger` set too low; `trigger(processingTime="1 second")` with large lag | Increase `maxOffsetsPerTrigger`; use `Trigger.AvailableNow()` for catch-up; set `minPartitions` on Kafka source |
| Downstream S3/HDFS dependency latency | Tasks complete slowly; executor logs show long write times | `Spark UI → Stage Details → Task Metrics → Result Serialization Time + Write Time`; `s3-benchmark` or `hdfs dfs -count` timing | S3 request throttling (503 SlowDown); HDFS NameNode under load; cross-region writes | Enable S3 multipart upload (`spark.hadoop.fs.s3a.multipart.size=128M`); use `committer=magic` for S3; reduce output partition count |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on Spark History Server | Browser shows `NET::ERR_CERT_EXPIRED`; `curl -sv https://<history>:18480` shows certificate dates | Automated cert renewal missed; self-signed cert not rotated | Developers cannot access job history; post-incident forensics blocked | Renew cert: replace keystore at `spark.ssl.historyServer.keyStore`; restart history server |
| mTLS rotation failure between executors and driver | Executors fail to register; driver log: `SSLHandshakeException` | Spark's internal `spark.authenticate` shared secret mismatch after rolling restart | All executors unable to join app; job fails immediately | Set consistent `spark.authenticate.secret` across driver and executor launch configs; redeploy app |
| DNS resolution failure for YARN ResourceManager | `spark-submit` fails with `UnknownHostException: resourcemanager`; `nslookup resourcemanager` returns NXDOMAIN | DNS entry removed or YARN RM hostname changed after cluster upgrade | All job submissions fail | Update `yarn-site.xml` `yarn.resourcemanager.hostname`; flush DNS cache: `systemctl restart nscd`; verify with `nslookup` |
| TCP connection exhaustion on executor hosts | Executors timeout connecting to shuffle service or other executors; `ss -s` shows `TIME_WAIT` accumulation | Too many short-lived shuffle connections without `SO_REUSEADDR`; kernel `net.ipv4.tcp_fin_timeout` too high | Shuffle fetch failures; task retries cascade; job slowdown | `sysctl -w net.ipv4.tcp_tw_reuse=1`; `sysctl -w net.ipv4.tcp_fin_timeout=15`; increase `net.ipv4.ip_local_port_range` |
| Load balancer misconfiguration dropping Spark UI connections | Spark UI returns 502 sporadically; driver is running and port 4040 responds locally | Load balancer idle timeout shorter than long-polling Spark UI connections | DevOps cannot monitor running jobs via LB-exposed UI | Increase LB idle timeout to 3600s; or bypass LB and use SSH tunnel to driver host: `ssh -L 4040:localhost:4040 <driver>` |
| Packet loss causing shuffle fetch retries | Tasks fail with `FetchFailedException`; stage retries loop; job eventually aborts | Network congestion between racks; faulty NIC or switch port | Stage failures; long retry delays; potential job abortion after `spark.stage.maxConsecutiveAttempts` | Identify lossy path: `ping -f -c 1000 <peer-node>`; escalate to network team; lower `spark.reducer.maxBlocksInFlightPerAddress` to reduce burst traffic |
| MTU mismatch between YARN nodes and network fabric | Intermittent `java.io.IOException: Connection reset by peer` on shuffle or RPC; no pattern by node | VXLAN/overlay network (Kubernetes or cloud VPC) with 1500 MTU but frames exceed path MTU | Random task failures across all stages; very hard to reproduce | Set MTU on Spark nodes to 1450 for overlay: `ip link set eth0 mtu 1450`; enable PMTUD: `sysctl -w net.ipv4.ip_no_pmtu_disc=0` |
| Firewall rule change blocking executor-to-driver RPC | Executors register then immediately disconnect; driver log: `Executor lost: remote RPC client disassociated` | New firewall rule blocks `spark.driver.port` (default ephemeral) or `spark.blockManager.port` | All executors lost; job cannot proceed | Pin ports: `--conf spark.driver.port=4041 --conf spark.blockManager.port=4042`; open firewall for those ports explicitly |
| SSL handshake timeout to external JDBC/S3 source | Tasks hang at shuffle-read or data-source-read phase; executor logs: `javax.net.ssl.SSLException: Read timed out` | TLS negotiation stalled due to overloaded TLS terminator or OCSP stapling delay | JDBC or S3 data source tasks timeout; stage failure with retries | Set `spark.executor.extraJavaOptions=-Dcom.sun.net.ssl.checkRevocation=false`; increase JDBC `socketTimeout`; use connection pool with pre-validated connections |
| Connection reset on long-running shuffle fetch | `FetchFailedException: Connection from ... has been closed`; affects only large shuffles | Shuffle data fetch exceeds network device idle timeout (common on AWS NLB default 350s) | Shuffle re-fetch triggers stage re-execution; cascading retries | Increase `spark.network.timeout` and `spark.shuffle.io.connectionTimeout` beyond NLB idle timeout; enable `spark.shuffle.service.enabled=true` (external shuffle service survives executor restart) |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill on executor | Task fails with `ExecutorLostFailure`; YARN container log shows `Container killed by YARN for exceeding memory limits` | `yarn logs -applicationId <id> | grep "Container killed\|Physical memory"` | Increase `spark.executor.memory` + `spark.executor.memoryOverhead`; reduce `spark.executor.cores` to lower concurrent task count per executor | Set `spark.executor.memoryOverhead` to at least 10% of executor memory; enable AQE |
| Disk full on YARN local dir (shuffle data) | Tasks fail with `java.io.IOException: No space left on device`; shuffle spill fails | `df -h /mnt/yarn/nm-local-dir` on all worker nodes | Delete stale app directories: `find /mnt/yarn/nm-local-dir/usercache -name "appcache" -mmin +240 -exec rm -rf {} \;`; enable external shuffle service for cleanup | Monitor `df` on YARN local dirs; set `yarn.nodemanager.local-dirs` across multiple mount points; set auto-cleanup TTL |
| Disk full on HDFS (event logs / output) | Job output write fails; Spark History Server cannot write event log | `hdfs dfs -df -h /`; `hdfs dfsadmin -report | grep "DFS Remaining"` | Delete old event logs: `hdfs dfs -rm -r /spark-history/<old-app-ids>`; archive output to cold storage | Set `spark.history.fs.cleaner.enabled=true`; automate HDFS quota enforcement per path |
| File descriptor exhaustion on driver | Driver logs `java.io.IOException: Too many open files`; new connections to executors refused | `lsof -p $(pgrep -f SparkSubmit) | wc -l`; `cat /proc/$(pgrep -f SparkSubmit)/limits | grep "open files"` | Restart driver after increasing limits: `ulimit -n 65536` in Spark launch script | Set `LimitNOFILE=65536` in YARN container or Spark launch wrapper; monitor with `lsof` count metric |
| Inode exhaustion on worker node | New file creation fails even when disk has space; JVM temp file creation errors | `df -i /mnt/yarn/nm-local-dir` — check `IUse%` column | Delete many small files in shuffle dirs: `find /mnt/yarn/nm-local-dir -name "*.index" -delete`; restart NodeManager | Avoid generating millions of small shuffle files; tune `spark.sql.shuffle.partitions`; use larger block sizes |
| CPU throttle on containerized Spark (Kubernetes) | Tasks run slower than bare-metal; CFS throttling in cgroup metrics | `kubectl top pod <executor-pod>` — CPU at limit; `cat /sys/fs/cgroup/cpu/cpuacct.throttled_time` in executor pod | Increase `spark.kubernetes.executor.limit.cores`; remove CPU limit or raise to match request | Set CPU request = limit for predictable scheduling; avoid over-committing executor CPU on K8s |
| JVM swap exhaustion | Executor performance degrades severely; GC pause time spikes to 10+ seconds | `free -m` on worker nodes — swap usage high; `vmstat 1 5 | grep -v '^[0-9]'` — si/so columns > 0 | Disable swap on all YARN nodes: `swapoff -a`; restart affected executors via YARN | Pin `vm.swappiness=0` in `/etc/sysctl.conf`; ensure physical memory ≥ sum of all executor memory allocations on node |
| Kernel thread limit (pid_max) | New executor processes fail to fork; YARN NodeManager logs `java.io.IOException: Cannot run program` | `sysctl kernel.pid_max`; `cat /proc/sys/kernel/threads-max`; `ps -eLf | wc -l` | `sysctl -w kernel.pid_max=4194304`; kill zombie Spark processes | Set `kernel.pid_max=4194304` in `/etc/sysctl.d/`; limit `spark.executor.cores` to prevent thread explosion per executor |
| Network socket buffer exhaustion | Shuffle reads stall; executor logs `RecvBufferSizeTooSmall` or `socket buffer full` | `ss -m | grep -c "rcvbuf"` — high buffer counts; `netstat -s | grep "receive buffer errors"` | `sysctl -w net.core.rmem_max=134217728`; `sysctl -w net.core.wmem_max=134217728`; restart shuffle service | Add socket buffer tuning to YARN node bootstrap; set `net.ipv4.tcp_rmem` and `tcp_wmem` appropriately |
| Ephemeral port exhaustion | Shuffle fetch connections fail with `Cannot assign requested address`; TIME_WAIT sockets fill port range | `ss -s | grep TIME-WAIT`; `cat /proc/sys/net/ipv4/ip_local_port_range` | `sysctl -w net.ipv4.ip_local_port_range="10000 65535"`; `sysctl -w net.ipv4.tcp_tw_reuse=1` | Increase port range; enable external shuffle service to reduce direct executor-to-executor connections; use connection keep-alive for shuffle |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation causing duplicate output rows | Delta Lake target table has duplicate rows for same business key; COUNT(*) > expected | `SELECT key, count(*) FROM delta.\`/data/table\` GROUP BY key HAVING count(*) > 1` | Downstream analytics double-count metrics; SLA breach | Deduplicate with: `MERGE INTO target USING source ON target.key = source.key WHEN MATCHED THEN UPDATE ... WHEN NOT MATCHED THEN INSERT`; restore from Delta snapshot with `RESTORE TABLE delta.\`/path\` TO VERSION AS OF <n>` |
| Saga / multi-stage ETL partial failure | Pipeline failed mid-run leaving partial output in target path; schema of target is inconsistent | `hdfs dfs -ls /data/table/_delta_log/ | tail -5` — check for incomplete transaction markers; `DESCRIBE HISTORY delta.\`/path\`` | Corrupt or partial data visible to downstream readers | Use Delta Lake transactions: write to staging table, then `MERGE`; re-run failed stage from last checkpoint; `RESTORE TABLE` to pre-run version |
| Kafka message replay causing data re-ingestion | Structured Streaming job re-processes old offsets after checkpoint delete; duplicate records appear in Delta table | `SELECT MIN(kafka_offset), MAX(kafka_offset) FROM delta.\`/data/table\`` vs current Kafka offsets via `kafka-consumer-groups.sh --bootstrap-server <broker>:9092 --describe --group spark-<app>` | Duplicate events written to data lake; downstream ML models trained on skewed data | Re-checkpoint at current offset: set `startingOffsets=latest` on fresh start; deduplicate via `dropDuplicates("kafka_offset", "kafka_partition")`; restore Delta table to pre-replay version |
| Cross-service deadlock (Spark + Hive Metastore) | Spark jobs hang indefinitely waiting for Metastore lock; HMS logs show `LockException` | `hive --service metatool -listLocks`; `SELECT * FROM hive_locks` in HMS DB; Spark executor logs: `org.apache.hadoop.hive.ql.lockmgr.LockException` | All jobs needing partition metadata blocked; YARN queue fills with stuck applications | Release stale locks: `hive --service metatool -unlockTable <table>`; restart HMS; kill deadlocked Spark app via `yarn application -kill <id>` |
| Out-of-order event processing in Structured Streaming | Watermark-based aggregations drop late events that arrive after watermark threshold | `Spark UI → Streaming → Event Time Watermark`; query: `SELECT window, count(*) FROM stream_table GROUP BY window ORDER BY window DESC LIMIT 20` — compare with upstream event counts | Late-arriving sensor or clickstream events lost; undercounted time-window metrics | Increase `withWatermark` delay: `.withWatermark("event_time", "2 hours")`; switch from `append` to `update` mode where deduplication not needed; use Delta Lake with `MERGE` for late-arriving corrections |
| At-least-once Kafka delivery causing duplicate Delta writes | Exactly-once not configured; restarted job re-processes last micro-batch and writes duplicates | `DESCRIBE HISTORY delta.\`/data/table\`` — look for two `WRITE` operations with identical `operationParameters.numOutputRows` near checkpoint boundary | Duplicate rows in Delta table propagate to downstream queries and dashboards | Enable idempotent writes: configure `spark.sql.streaming.forEachBatchSink` with `MERGE`; use `foreachBatch` with Delta `MERGE` for exactly-once semantics; deduplicate with `dropDuplicates` keyed on Kafka offset+partition |
| Compensating transaction failure in Lambda architecture | Batch recalculation job fails to overwrite Speed Layer results; stale real-time data persists | `DESCRIBE HISTORY delta.\`/data/speed_table\`` — last batch write timestamp; compare with `SELECT MAX(batch_run_ts) FROM delta.\`/data/batch_table\`` | Users see stale real-time data indefinitely; batch corrections never applied | Force overwrite of speed layer: `df.write.format("delta").mode("overwrite").option("overwriteSchema","true").save("/data/speed_table")`; verify with `SELECT * FROM delta.\`/data/speed_table\` LIMIT 10` |
| Distributed lock expiry mid-operation on Delta table | Concurrent writers conflict; `ConcurrentWriteException` or `ProtocolChangedException` in executor logs | `DESCRIBE HISTORY delta.\`/data/table\`` — look for `WRITE` operations with `operationMetrics.numFilesAdded=0` indicating aborted transactions; `hdfs dfs -ls /data/table/_delta_log/*.json | wc -l` | Partial write leaves orphan data files not referenced in transaction log; storage waste accumulates | Run `VACUUM delta.\`/data/table\` RETAIN 168 HOURS DRY RUN` to identify orphan files; then `VACUUM` without DRY RUN; enable `delta.deletedFileRetentionDuration` |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor on shared YARN NodeManager | One tenant's executor consuming all vCores; other tenants' tasks preempted | Tenant jobs run 3-5x slower; stage timeouts; SLA breach | `yarn application -kill <noisy-app-id>` then `yarn queue -status <queue>` to verify vCore release | Enable YARN preemption: `yarn.resourcemanager.scheduler.monitor.enable=true`; configure per-queue vCore limits in `capacity-scheduler.xml` |
| Memory pressure from adjacent tenant | Tenant A's high `spark.executor.memoryOverhead` causes OOM kills on Tenant B's containers | Tenant B containers killed by YARN; retry storms; job failures | `yarn node -list -showDetails | grep "Used Memory\|Available Memory"` per node — identify saturated nodes | Set per-queue memory limits in capacity scheduler; enable `yarn.scheduler.capacity.maximum-am-resource-percent`; separate high-memory tenants onto dedicated node labels |
| Disk I/O saturation from shuffle-heavy tenant | One tenant writing massive shuffle data to shared `/mnt/yarn/nm-local-dir`; other tenant disk writes stalled | Tenant B jobs fail with `IOException: No space left on device` or extreme shuffle latency | `iostat -x 1 5` — identify saturated devices; `du -sh /mnt/yarn/nm-local-dir/usercache/*/appcache/` per tenant | Assign tenants to separate disk volumes via `yarn.nodemanager.local-dirs` disk selection; enable cgroups blkio for IO throttling; reduce `spark.shuffle.file.buffer` for all tenants |
| Network bandwidth monopoly from large Spark shuffle | Tenant A's 10TB shuffle consuming all NIC bandwidth; Tenant B's RPC calls timing out | Tenant B stage failures; heartbeat timeouts; executor loss | `iftop -i eth0 -t -s 10` — identify top consumer IP; `yarn application -status <id> | grep "Shuffle Write"` | Enable traffic shaping with `tc qdisc add dev eth0 root handle 1: htb default 10`; separate tenant traffic onto VLANs; reduce `spark.reducer.maxBlocksInFlightPerAddress` for large shuffle jobs |
| Connection pool starvation to shared Hive Metastore | Multiple tenants simultaneously fetching metadata; HMS connection pool exhausted | Tenant jobs fail with `MetaException: Unable to connect to metastore`; all schema discovery blocked | `ss -tn | grep ":9083" | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head 10` | Increase HMS `hive.metastore.max.thrift.connections`; cache Spark-side metadata: `spark.sql.hive.metastorePartitionPruning=true`; deploy per-tenant HMS instances for critical tenants |
| YARN quota enforcement gap allowing resource overspend | Tenant submitted application exceeds queue capacity; YARN allows it due to `allow-undeclared-pools=true` | Other tenants starved of resources; their jobs queue indefinitely | `yarn queue -status <queue>` — check `absoluteCapacity` vs `absoluteUsedCapacity`; `yarn scheduler -format-scheduler-state -output /tmp/sched.json` | Set `yarn.scheduler.capacity.maximum-capacity=<n>` per queue; disable `allow-undeclared-pools`; enforce submission ACLs: `yarn.scheduler.capacity.<queue>.acl_submit_applications=<group>` |
| Cross-tenant data leak risk via shared Delta Lake path | Two tenants' Spark jobs writing to overlapping HDFS paths; Tenant B can read Tenant A's data | Data confidentiality breach; compliance violation; incorrect query results | `hdfs dfs -ls -R /data/ | awk '{print $3, $8}' | sort | uniq -d` — find shared ownership paths | Enforce HDFS path per-tenant with POSIX permissions: `hdfs dfs -chmod 700 /data/tenant-a/`; use Ranger policies for fine-grained access control; audit with `hdfs dfsadmin -report` |
| Rate limit bypass via YARN label scheduling | Tenant exploits node labels to submit to nodes reserved for other tenants | Reserved-node tenants lose guaranteed capacity; high-priority production jobs miss SLA | `yarn node -list -showDetails | grep "Node-Labels"` — verify label assignment; `yarn application -status <id> | grep "Node-Label"` | Enforce node label access ACLs: `yarn.scheduler.capacity.<queue>.accessible-node-labels=<label>`; remove label access for unauthorized queues; audit queue config with `yarn scheduler -format-scheduler-state` |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Spark History Server metric scrape failure | Grafana Spark job dashboards show no data; alerts not firing for OOM or stage failures | History Server event log cleaner purged logs before Prometheus scraper pulled metrics | `curl http://<history>:18080/api/v1/applications?limit=100` — verify events present; check `spark.history.fs.cleaner.maxAge` vs scrape interval | Increase `spark.history.fs.cleaner.maxAge=30d`; configure Prometheus pushgateway for in-flight job metrics instead of pull-from-history |
| Trace sampling gap missing long-running stage incidents | Distributed tracing shows only short spans; multi-hour Spark stages never appear | Jaeger/Zipkin default sampling rate (1%) discards most traces for long jobs; short span timeout truncates traces | `curl http://<driver>:4040/api/v1/applications/<id>/stages` — pull stage data directly from Spark UI API | Set Spark trace sampling to 100% for jobs over 10 minutes: configure `spark.opentelemetry.sampler.ratio=1.0`; lower sampling for sub-minute queries |
| Log pipeline silent drop for executor stderr | Executor OOM errors never reach Elasticsearch/Splunk; post-incident forensics impossible | YARN log aggregation failed due to HDFS NameNode unavailability during incident; logs written locally but not aggregated | SSH to worker nodes and collect manually: `for n in $(yarn node -list | awk 'NR>2{print $1}' | cut -d: -f1); do ssh $n "find /mnt/yarn/nm-local-dir -name stderr -newer /tmp/incident_start"` | Enable redundant log aggregation to S3: `yarn.log-aggregation-enable=true` + `yarn.nodemanager.remote-app-log-dir=s3a://log-bucket/`; add log aggregation health check to monitoring |
| Alert rule misconfiguration — OOM alert never fires | `executor_oom_count` alert defined but never fires despite repeated OOM kills | Alert queries `container_killed_by_yarn` metric which only exists in YARN RM; Prometheus scraping Spark executors, not YARN RM | `yarn application -list -appStates KILLED | grep "Memory"` — manual check; `yarn logs -applicationId <id> | grep "Physical memory"` | Add YARN ResourceManager JMX metrics to Prometheus: `jmxExporter.yaml` for YARN; alert on `yarn_application_running_applications` drop + `FabricNodeHealthChecker` |
| Cardinality explosion blinding Spark Prometheus dashboards | Grafana dashboards lag 10+ minutes; Prometheus query timeout; TSDB head chunk too large | Spark emits `executor_id` and `app_id` as metric labels creating millions of unique time series on long-running multi-tenant clusters | `curl http://<prometheus>:9090/api/v1/label/__name__/values | python3 -m json.tool | grep -c spark` — count Spark series | Enable label dropping in Prometheus scrape config: `metric_relabel_configs` to drop high-cardinality `executor_id` label; use recording rules to pre-aggregate |
| Missing health endpoint on Spark Structured Streaming jobs | Streaming job silently falls behind Kafka; no alert fires; consumers accumulate lag | Spark Structured Streaming has no native HTTP health endpoint; Grafana streaming dashboard scrapes History Server which shows in-flight jobs as healthy | `curl http://<driver>:4040/api/v1/applications/<id>/streams` — check `batchDuration` and `avgProcessingTime`; `kafka-consumer-groups.sh --bootstrap-server <broker>:9092 --describe --group spark-<app>` | Add custom health check: expose `/health` via Spark's `StreamingQueryListener` writing to a side-channel file; scrape with Blackbox Exporter |
| Instrumentation gap in critical Spark SQL plan path | Query plan changes (e.g., broadcast join threshold crossed) cause 10x regression not detected | AQE plan changes emit no metrics; only Spark event log records plan decisions which are not scraped by Prometheus | `Spark UI → SQL → Details → Adaptive Execution`; `curl http://<history>:18080/api/v1/applications/<id>/sql` | Instrument AQE events via custom `SparkListener`: hook `onSparkListenerSQLAdaptiveExecutionUpdate`; emit metric when plan changes; alert on broadcast join fallback |
| Alertmanager outage during Spark cluster failure | Multiple executor failures and OOM kills occurred but no pages sent to on-call engineer | Alertmanager pods on same Kubernetes cluster that was impacted by node failures; single-cluster monitoring anti-pattern | Check PagerDuty directly: `pd incident list --urgency high --status triggered --since 2h`; use Spark History Server API as backup: `curl http://<history>:18080/api/v1/applications?status=failed` | Deploy Alertmanager outside the monitored Spark cluster; use Prometheus remote_write to external monitoring stack; add dead man's switch alert via Cronitor or Healthchecks.io |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Spark minor version upgrade (e.g., 3.4 → 3.5) rollback | New Spark version crashes on startup with `ClassNotFoundException` for custom UDF; jobs fail immediately | `spark-submit --version`; executor logs: `yarn logs -applicationId <id> | grep "ClassNotFound\|NoSuchMethod"` | Repoint Spark home: `export SPARK_HOME=/opt/spark-3.4.3`; restart History Server with old version; jobs auto-use reverted version on resubmit | Run upgrade on 5% of jobs via YARN label routing before full rollout; use `--conf spark.driver.extraClassPath` to test JAR compat |
| Spark major version upgrade (e.g., 2.x → 3.x) schema migration rollback | DataFrame column resolution changed; `AnalysisException: Resolved attribute missing from child` on column ambiguity | `grep -r "AnalysisException\|ambiguous column" /var/log/spark/` after upgrade; `yarn logs -applicationId <id> | grep "AnalysisException"` | Revert Spark version in cluster config; Delta Lake tables remain intact (Delta format is backward compatible); redeploy with old Spark image | Run `df.explain()` against all production queries on new Spark version in staging before migration; check for column name ambiguity in joins |
| Delta Lake schema migration partial completion | `ALTER TABLE delta.\`/path\` ADD COLUMN <col>` succeeded on one partition but timed out on others; readers see schema mismatch | `DESCRIBE HISTORY delta.\`/path\`` — check for incomplete schema evolution commit; `SELECT * FROM delta.\`/path\` LIMIT 1` — check for `SchemaEvolutionException` | `RESTORE TABLE delta.\`/path\` TO VERSION AS OF <n>` to pre-migration version; re-run migration with `mergeSchema=true` option | Use Delta Lake `ALTER TABLE` with retry; wrap schema migration in explicit transaction; test on staging Delta table first |
| Rolling upgrade version skew between Spark driver and executors | Jobs submitted during upgrade fail with `SparkException: Failed to serialize: class version mismatch`; executors on new version cannot deserialize old driver RDD | `Spark UI → Executors → Version column` — check for mixed executor versions; `yarn logs -applicationId <id> | grep "version mismatch\|deserialize"` | Kill all in-flight jobs: `for id in $(yarn application -list 2>/dev/null | awk 'NR>2{print $1}'); do yarn application -kill $id; done`; complete upgrade of all NodeManagers before resuming submissions | Drain active jobs before NodeManager upgrade: `yarn node -decommission <node>`; use blue/green YARN clusters for zero-downtime upgrades |
| Zero-downtime Delta Lake migration gone wrong (partition overwrite) | `df.write.mode("overwrite").partitionBy(...)` partially completed; concurrent readers see mixed old/new partition data | `DESCRIBE HISTORY delta.\`/path\`` — look for `WRITE` with partial `numOutputRows`; `SELECT count(*) FROM delta.\`/path\` VERSION AS OF <n>` vs current | `RESTORE TABLE delta.\`/path\` TO VERSION AS OF <n>` to last known good version; verify row count matches pre-migration baseline | Use Delta Lake `MERGE` instead of `overwrite` for atomic partition replacement; stage data to temp table, validate, then rename |
| Hadoop config format change breaking old SparkContext | After `yarn-site.xml` property rename in Hadoop 3.x, Spark job fails at context init with `Configuration property not found` | `spark-submit --conf spark.hadoop.yarn.resourcemanager.address=<rm>:8032 --class TestApp` — check for config lookup errors; `$SPARK_HOME/bin/spark-submit --verbose 2>&1 | grep "WARN.*deprecated\|No value"` | Revert `yarn-site.xml` changes; add deprecated property aliases alongside new names for transition period | Run `hadoop deprecation-properties check` before upgrade; test all Spark jobs in lower env after `yarn-site.xml` changes |
| Parquet/ORC data format incompatibility after Spark upgrade | Existing Parquet files written with Spark 2.x cannot be read by Spark 3.x due to timestamp precision change | `spark-submit --class <reader> --conf spark.sql.legacy.parquet.datetimeRebaseModeInRead=CORRECTED` — check for `SparkUpgradeException: reading dates before 1582-10-15`; logs: `yarn logs -applicationId <id> | grep "SparkUpgradeException"` | Add compatibility flag: `--conf spark.sql.legacy.parquet.datetimeRebaseModeInRead=CORRECTED --conf spark.sql.legacy.parquet.int96RebaseModeInRead=CORRECTED` | Set migration flags globally in `spark-defaults.conf` during upgrade window; run `CONVERT TO DELTA` on all Parquet tables to normalize format |
| Feature flag rollout (AQE) causing query regression | Enabling `spark.sql.adaptive.enabled=true` fleet-wide causes specific queries to choose suboptimal plans; P99 latency doubles | `Spark UI → SQL → Details` — check adaptive execution plan decisions; `SELECT query_text, total_elapsed_time FROM pg_stat_statements ORDER BY total_elapsed_time DESC LIMIT 20` (if using JDBC source) | Disable AQE for specific job: `--conf spark.sql.adaptive.enabled=false`; or fleet-wide: `sed -i 's/adaptive.enabled=true/adaptive.enabled=false/' spark-defaults.conf` | Roll out AQE via YARN queue label: enable for dev queue first; use Query Store baselines to compare plan choices before/after |
| Dependency version conflict after PySpark upgrade | `ModuleNotFoundError: No module named 'pyspark'` in executor Python environment after upgrading `pyspark` pip package | `yarn logs -applicationId <id> | grep "ModuleNotFoundError\|import error"`; `python3 -c "import pyspark; print(pyspark.__version__)"` on worker nodes | Pin PySpark version: `--conf spark.submit.pyFiles=pyspark-3.4.3-py3-none-any.whl`; re-broadcast correct virtualenv: `--conf spark.archives=env.tar.gz#environment` | Use `conda-pack` or `venv-pack` to create deterministic Python environment archive; test PySpark version on all executor nodes before cluster-wide upgrade |

## Kernel/OS & Host-Level Failure Patterns
**Minimum cross-cutting cases to evaluate here:** OOM killer false kill, inode exhaustion, CPU steal, NTP skew affecting locks, leases, or coordination, file descriptor exhaustion, and TCP conntrack table saturation.

| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|------------------|--------------|-------------|----------------------|
| OOM killer terminates Spark executor JVM | `dmesg -T | grep -i "oom\|killed process"` on worker node; executor vanishes from Spark UI with no graceful exit | YARN container memory limit set lower than executor JVM + overhead; GC not releasing memory fast enough | Executor lost; all tasks on that executor must re-run; shuffle data lost | Increase `spark.executor.memoryOverhead` to 20% of executor memory; set `--conf spark.memory.fraction=0.6`; add `--conf spark.executor.extraJavaOptions=-XX:+UseG1GC` to reduce GC pressure |
| Inode exhaustion on YARN local dir blocking new task launch | `df -i /mnt/yarn/nm-local-dir` shows `IUse% = 100%` while `df -h` shows space available | Millions of small shuffle index and data files created by many short Spark tasks | New file creation fails; NodeManager cannot write temp files; tasks fail at launch | `find /mnt/yarn/nm-local-dir -name "*.index" -mmin +60 -delete`; reduce shuffle file count: `--conf spark.sql.shuffle.partitions=200`; restart NodeManager after cleanup |
| CPU steal spike degrading executor task throughput | `top` or `vmstat 1 5` on worker node shows `%st > 10%`; executor tasks take 2-3x longer than expected | Shared hypervisor over-provisioning vCPUs; noisy neighbor VMs consuming physical CPU cycles | Spark stage latency inflates; SLA breach; YARN heartbeat may miss triggering executor loss | Migrate to dedicated or CPU-pinned instances; set YARN cgroup CPU enforcement: `yarn.nodemanager.linux-container-executor.cgroups.strict-resource-usage=true`; schedule Spark jobs on non-peak hours |
| NTP clock skew causing Spark event log timestamp corruption | `chronyc tracking` shows offset > 500ms; Spark History Server event log shows events out of order; Structured Streaming watermark miscalculates | ntpd/chronyd drift on one or more worker nodes | Watermark-based streaming drops valid events; History Server sorting and stage timeline broken | `chronyc makestep` to force immediate NTP sync; verify all nodes: `for n in $(yarn node -list | awk 'NR>2{print $1}' | cut -d: -f1); do ssh $n chronyc tracking | grep "System time"; done` |
| File descriptor exhaustion on Spark driver | `lsof -p $(pgrep -f SparkSubmit) | wc -l` approaching `ulimit -n` limit; driver logs `Too many open files`; new executor registrations refused | Each Spark shuffle connection holds open file descriptors; large jobs with many executors exhaust driver FD limit | Driver cannot accept new connections; job stalls then fails | `ulimit -n 65536` in Spark launch script; set `LimitNOFILE=65536` in systemd unit or YARN container; use external shuffle service: `--conf spark.shuffle.service.enabled=true` |
| TCP conntrack table full blocking executor-to-executor shuffle | `dmesg | grep "nf_conntrack: table full"` on worker nodes; shuffle fetch fails with `Connection refused`; `ss -s` shows large `TIME-WAIT` count | YARN cluster with many executors generating shuffle connections saturates kernel conntrack table (default 65536) | All new TCP connections refused; Spark shuffle completely blocked; job hangs | `sysctl -w net.netfilter.nf_conntrack_max=1048576`; `sysctl -w net.ipv4.tcp_tw_reuse=1`; reduce conntrack pressure with external shuffle service; persist fix in `/etc/sysctl.d/spark.conf` |
| Kernel panic / node crash losing all in-memory shuffle data | Worker node disappears from YARN; all executors on that node show `LOST`; driver logs `Lost executor <id> on <host>: Remote RPC client disassociated` | Hardware failure, kernel bug, or OOM + kernel panic triggered by executor memory pressure | All shuffle data on the crashed node lost; running stages must be recomputed; if external shuffle service not enabled, entire job re-runs | Enable external shuffle service on all nodes: `spark.shuffle.service.enabled=true`; configure YARN node health checker: `yarn.nodemanager.health-checker.script.path`; use spot/preemptible node groups with checkpointing |
| NUMA memory imbalance degrading executor GC performance | `numastat -p $(pgrep -f SparkSubmit)` shows high `numa_miss` on executor JVMs; GC pauses spike to 5+ seconds | JVM allocating memory across NUMA nodes due to missing NUMA binding on multi-socket worker nodes | Executor GC pauses cause task heartbeat timeouts; YARN kills executor as unresponsive | Pin executor JVMs to local NUMA node: `numactl --localalloc --cpunodebind=0 java ...` in executor launch config; set `--conf spark.executor.extraJavaOptions=-XX:+UseNUMA`; verify with `numastat` after restart |

## Deployment Pipeline & GitOps Failure Patterns
**Minimum cross-cutting cases to evaluate here:** image pull failure (rate limit or auth), Helm drift, ArgoCD sync stuck, PodDisruptionBudget-blocked rollout, blue-green cutover failure, and ConfigMap or Secret drift.

| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Spark Docker image pull rate limit (Docker Hub) | Kubernetes executor pods stuck in `ImagePullBackOff`; events show `toomanyrequests` | `kubectl describe pod <executor-pod> -n spark | grep -A5 "Events"` — look for `rate limit` or `toomanyrequests` | Switch to private registry mirror: `--conf spark.kubernetes.container.image=<private-registry>/spark:3.5.0`; configure `imagePullSecrets` | Mirror Spark images to ECR/GCR/ACR; set `imagePullPolicy: IfNotPresent` in Spark operator config; never rely on Docker Hub for production |
| Image pull auth failure for private Spark executor image | Executor pods fail with `ImagePullBackOff: unauthorized: authentication required` | `kubectl get events -n spark --field-selector reason=Failed | grep "pull\|auth"` | Create and attach image pull secret: `kubectl create secret docker-registry spark-regcred --docker-server=<registry> --docker-username=<u> --docker-password=<p> -n spark`; add `--conf spark.kubernetes.container.imagePullSecrets=spark-regcred` | Automate registry credential rotation with ExternalSecrets; use IRSA/Workload Identity for cloud registry auth instead of static credentials |
| Helm chart drift between deployed Spark operator and values | `helm diff upgrade spark-operator spark-operator/spark-operator -n spark-operator -f values.yaml` shows unexpected resource diffs; deployed config diverges from Git | `helm get values spark-operator -n spark-operator > /tmp/live.yaml && diff /tmp/live.yaml values.yaml` | `helm rollback spark-operator 0 -n spark-operator` (0 = previous revision) | Enable Helm drift detection in CI: run `helm diff` as pre-merge check; use ArgoCD `Application` resource to reconcile Helm charts automatically |
| ArgoCD sync stuck on Spark Application CRD update | ArgoCD `spark-apps` Application shows `OutOfSync` but sync never completes; SparkApplication CRD schema validation error | `argocd app get spark-apps --refresh`; `kubectl get events -n spark | grep "SparkApplication\|CRD\|validation"` | Force resource update: `argocd app sync spark-apps --force`; if CRD schema error: `kubectl replace --force -f sparkapplication-crd.yaml` | Pin CRD versions in ArgoCD `ignoreDifferences`; validate SparkApplication manifests against CRD schema in CI with `kubeconform` |
| PodDisruptionBudget blocking Spark operator rolling update | `kubectl rollout status deployment/spark-operator -n spark-operator` hangs; `kubectl get pdb -n spark-operator` shows `DISRUPTIONS ALLOWED: 0` | `kubectl describe pdb spark-operator-pdb -n spark-operator` — check `Disruptions Allowed`; `kubectl get pods -n spark-operator` — verify no pods in `Terminating` | Temporarily patch PDB: `kubectl patch pdb spark-operator-pdb -n spark-operator -p '{"spec":{"maxUnavailable":1}}'`; complete rollout; restore PDB | Set PDB `maxUnavailable=1` instead of `0` for operator deployments; ensure PDB minAvailable aligns with replica count |
| Blue-green traffic switch failure for Spark History Server | Users routed to new History Server pod that hasn't loaded event logs yet; UI shows empty application list | `kubectl get svc spark-history-server -n spark -o yaml | grep -A5 selector`; `curl http://spark-history-server:18080/api/v1/applications` — check empty response | Revert service selector: `kubectl patch svc spark-history-server -n spark -p '{"spec":{"selector":{"version":"blue"}}}'` | Add readiness probe to History Server checking `/api/v1/applications` returns non-empty; only switch traffic after probe succeeds |
| ConfigMap drift — spark-defaults.conf diverges from Git | Jobs using wrong executor memory or shuffle config; `spark-submit` picks up stale values from ConfigMap | `kubectl get configmap spark-defaults -n spark -o jsonpath='{.data.spark-defaults\.conf}'` vs git-tracked `spark-defaults.conf` | `kubectl create configmap spark-defaults --from-file=spark-defaults.conf -n spark --dry-run=client -o yaml | kubectl apply -f -` | Manage ConfigMaps via ArgoCD or Flux; add `kubectl diff` step in CI pipeline to detect config drift before merge |
| Feature flag stuck — AQE enabled in Helm values but not in running SparkApplications | AQE disabled in production despite being merged to main; live SparkApplication CRs have old spec | `kubectl get sparkapplication -n spark -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.sparkConf.spark\.sql\.adaptive\.enabled}{"\n"}{end}'` | Patch all SparkApplications: `kubectl get sparkapplication -n spark -o name | xargs -I{} kubectl patch {} -n spark --type merge -p '{"spec":{"sparkConf":{"spark.sql.adaptive.enabled":"true"}}}'` | Use Kustomize overlays to inject feature flags; validate SparkApplication specs in CI with `kustomize build | kubectl apply --dry-run=server` |

## Service Mesh & API Gateway Edge Cases
**Minimum cross-cutting cases to evaluate here:** circuit breaker false positives, rate limiting on legitimate traffic, stale service discovery endpoints, mTLS rotation interruption, retry storm amplification, gRPC keepalive or max-message failures, and trace context loss.

| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive tripping Spark History Server API | Istio circuit breaker opens on History Server; Grafana shows 503s despite History Server being healthy | `kubectl exec -n istio-system <istiod-pod> -- pilot-agent request GET /stats | grep "history.*cx_open"`; History Server `/api/v1/applications` returns 200 but Envoy rejects | All Spark job dashboards break; SRE cannot access job history | Tune circuit breaker: `kubectl edit destinationrule spark-history-server -n spark` — increase `consecutiveGatewayErrors` threshold; add `/api/v1/applications` to outlier detection exclusion |
| Rate limiting blocking legitimate Spark job submissions via API Gateway | `spark-submit --master k8s://...` calls throttled; Kubernetes API server returns `429 Too Many Requests` | API Gateway rate limit too low for burst of job submissions during peak ETL window | Multiple jobs fail to submit; data pipeline SLA breach | `kubectl get --raw /apis/ | grep ratelimit`; increase Kong/Istio rate limit for Spark operator service account; use `kubectl --request-timeout=300s` for large job submissions |
| Stale service discovery endpoints for Spark executor services | Shuffle fetch to dead executor IP that hasn't been cleaned from Envoy EDS cache; `FetchFailedException` with no matching pod | Envoy EDS update delay after executor pod deletion; stale endpoint persists beyond `termination_drain_duration` | Shuffle re-fetch storms; stage failures; increased job latency | `istioctl proxy-config endpoint <driver-pod> -n spark | grep <stale-ip>` to confirm; force EDS refresh: `istioctl x internal-debug eds | grep spark`; reduce `PILOT_DEBOUNCE_AFTER` in Istiod |
| mTLS rotation breaking Spark driver-to-executor RPC | After cert rotation, driver cannot connect to executors; `SSLHandshakeException: PKIX path building failed` in executor logs | Old executor pods have stale mTLS certs not yet rotated; driver has new cert; SPIFFE cert mismatch | All executors on nodes with old certs inaccessible; job fails | `istioctl proxy-config secret <executor-pod> -n spark | grep "Valid Until"`; force cert rotation: `kubectl rollout restart daemonset/istio-cni-node -n istio-system`; kill old executor pods to force re-issue |
| Retry storm — Envoy retries amplifying failed Spark SQL queries | Single slow Spark SQL query triggers Envoy retry; multiple retries hit already-overloaded History Server; cascade | Istio VirtualService retry policy set to 3 retries with no backoff for 5xx on History Server path | History Server CPU saturates; all `/api/v1/` endpoints time out; monitoring blind | `kubectl get virtualservice spark-history -n spark -o yaml | grep -A10 retries`; add `perTryTimeout: 30s` and `retryOn: "gateway-error"` only; disable retries for `/api/v1/applications/<id>/stages` (large responses) |
| gRPC keepalive misconfiguration causing Spark Thrift Server connection drops | Beeline / BI tool connections to Spark Thrift Server drop after 30s idle; `TTransportException: null` | Envoy proxy idle timeout (default 1h) fine but `grpc_keepalive_time_ms` not set; HiveServer2 protocol over HTTP drops idle connections at load balancer | BI dashboards lose Spark SQL connections; users see errors; reconnect storms | `kubectl edit configmap spark-thrift-config -n spark` — set `hive.server2.idle.operation.timeout=0`; set Envoy `idle_timeout: 3600s` in listener; add keepalive: `--conf spark.sql.thriftServer.incrementalCollect=true` |
| Trace context propagation gap between Spark driver and executors | Jaeger/Tempo shows broken traces: driver span has no child executor spans; distributed trace incomplete | Spark does not natively propagate W3C TraceContext headers across JVM serialization boundaries to executors | Distributed traces useless for Spark job debugging; latency attribution impossible | Inject trace context via `spark.executor.extraJavaOptions=-Dopentelemetry.exporter.otlp.traces.endpoint=<url>`; use SparkListener-based custom tracing that propagates `traceparent` via task metadata |
| Load balancer health check misconfiguration causing Spark History Server traffic loss | HAProxy / ALB marks History Server as unhealthy and stops routing; `curl http://history:18080/` works from inside cluster | Health check path misconfigured to `/health` (404) instead of `/`; History Server has no `/health` endpoint | All external access to Spark History Server blocked; SREs cannot see job status | Fix health check path: set ALB target group health check to `HTTP:18080/`; or add nginx sidecar with `/health` → `proxy_pass http://localhost:18080/` mapping; verify with `aws elbv2 describe-target-health --target-group-arn <arn>` |
