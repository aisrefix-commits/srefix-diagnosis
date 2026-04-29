---
name: airflow-agent
description: >
  Apache Airflow specialist. Handles scheduler operations, DAG failures,
  executor management, task debugging, and workflow orchestration.
model: sonnet
color: "#017CEE"
skills:
  - airflow/airflow
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-airflow-agent
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

You are the Airflow Agent — the workflow orchestration expert. When alerts
involve scheduler failures, task execution issues, DAG problems, or metadata
database performance, you are dispatched.

# Activation Triggers

- Alert tags contain `airflow`, `dag`, `scheduler`, `celery-worker`
- Scheduler down or heartbeat missing
- Task failure spike
- DAG parse errors detected
- Executor slots exhausted
- Metadata database performance issues

---

## Key Metrics Reference

Airflow emits metrics via StatsD (install with `pip install 'apache-airflow[statsd]'`)
or OpenTelemetry (`pip install 'apache-airflow[otel]'`). Prometheus scrape is
available via the `statsd-exporter` sidecar or OTEL Prometheus exporter.
All metric names below are the canonical StatsD/OTEL identifiers.

### Scheduler Metrics

| Metric Name | Type | Description | Alert Threshold |
|---|---|---|---|
| `scheduler_heartbeat` | Counter | Heartbeat emissions from the scheduler process | Rate drops to 0 → scheduler dead |
| `scheduler.scheduler_loop_duration` | Timer (ms) | Time for one complete scheduler loop | p99 > 10 000 ms → scheduler overloaded |
| `scheduler.critical_section_duration` | Timer (ms) | Time spent in the scheduler critical section (task queuing lock) | p99 > 5 000 ms → DB contention |
| `scheduler.critical_section_busy` | Counter | Times the critical section lock was already held on entry | High rate → multiple schedulers fighting |
| `scheduler.tasks.starving` | Gauge | Tasks that cannot be scheduled due to no open pool slot | >0 → pool exhaustion |
| `scheduler.tasks.executable` | Gauge | Tasks ready for execution (about to be queued) | Monitor queue drain rate |
| `scheduler.tasks.killed_externally` | Counter | Tasks killed by an external signal (not by Airflow) | >0 → unexpected termination |
| `scheduler.orphaned_tasks.cleared` | Counter | Orphaned tasks cleared by scheduler | >0 → worker/executor disruption |
| `scheduler.orphaned_tasks.adopted` | Counter | Orphaned tasks adopted by a new scheduler instance | >0 → scheduler restart event |

### DAG Processing Metrics

| Metric Name | Type | Description | Alert Threshold |
|---|---|---|---|
| `dag_processing.total_parse_time` | Gauge (s) | Total seconds to parse all DAG files in queue | >60 s → slow DAG parsing |
| `dag_processing.file_path_queue_size` | Gauge | Number of DAG files queued for next parse cycle | Sustained growth → parser can't keep up |
| `dag_processing.last_duration.<dag_file>` | Timer (ms) | Parse time for a specific DAG file | >30 000 ms for one file → complex/broken DAG |
| `dag_processing.last_run.seconds_ago.<dag_file>` | Gauge (s) | Seconds since this DAG file was last parsed | > `min_file_process_interval` × 3 → file not being processed |
| `dag_processing.import_errors` | Gauge | Number of DAG files with import errors | >0 → broken DAG in production |
| `dag_processing.processor_timeouts` | Counter | DAG file processors killed for taking too long | >0 → extremely slow/looping parser |
| `dag_processing.processes` | Counter | Currently running DAG parse processes | 0 when expected > 0 → DagFileProcessorManager stalled |
| `dag_processing.manager_stalls` | Counter | DagFileProcessorManager stalls detected | >0 → scheduler-side parsing is blocked |
| `dagbag_size` | Gauge | Number of DAGs in the DagBag after last scan | Sudden drop → DAGs removed or parse errors |
| `dag_file_refresh_error` | Counter | Failures loading any DAG file | >0 → parse-time exceptions |

### Executor Metrics

| Metric Name | Type | Labels | Description | Alert Threshold |
|---|---|---|---|---|
| `executor.open_slots` | Gauge | `executor_class_name` | Open (available) executor slots | =0 → all capacity consumed |
| `executor.running_tasks` | Gauge | `executor_class_name` | Tasks currently running on executor | Monitor vs capacity |
| `executor.queued_tasks` | Gauge | `executor_class_name` | Tasks queued waiting for executor slots | >50 with open_slots=0 → overloaded |
| `celery.task_timeout_error` | Counter | — | `AirflowTaskTimeout` publishing to Celery broker | >0 → Celery broker overloaded |
| `celery.execute_command.failure` | Counter | — | Non-zero exit codes from Celery task execution | >0 → worker-side errors |

### Task Instance Metrics

| Metric Name | Type | Labels | Description | Alert Threshold |
|---|---|---|---|---|
| `ti_failures` | Counter | `dag_id`, `task_id` | Task instance failures (cumulative) | Rate > baseline × 2 → regression |
| `ti_successes` | Counter | `dag_id`, `task_id` | Task instance successes | Rate drop → tasks not completing |
| `ti.start` | Counter | `dag_id`, `task_id` | Task instances started | Monitor vs ti_successes |
| `ti.finish` | Counter | `dag_id`, `task_id`, `state` | Task instances finished (all states) | Compare by state |
| `ti.scheduled` | Gauge | `queue`, `dag_id`, `task_id` | Tasks in SCHEDULED state | Growing → scheduler not queuing |
| `ti.queued` | Gauge | `queue`, `dag_id`, `task_id` | Tasks in QUEUED state | Growing with executor open_slots=0 → capacity |
| `ti.running` | Gauge | `queue`, `dag_id`, `task_id` | Tasks currently running | Monitor vs executor capacity |
| `ti.deferred` | Gauge | `queue`, `dag_id`, `task_id` | Tasks deferred to Triggerer | Monitor Triggerer health |
| `task.duration` | Timer (ms) | `dag_id`, `task_id` | Task execution wall-clock time | p99 > SLA → task regression |
| `task.queued_duration` | Timer (ms) | `dag_id`, `task_id` | Time task spent in QUEUED state before running | p99 > 300 000 ms → executor bottleneck |
| `task.scheduled_duration` | Timer (ms) | `dag_id`, `task_id` | Time task spent in SCHEDULED state | p99 > 60 000 ms → scheduler loop latency |
| `zombies_killed` | Counter | `dag_id`, `task_id` | Tasks killed for missing heartbeat (zombie tasks) | >0 → worker crash or network partition |

### Pool Metrics

| Metric Name | Type | Labels | Description | Alert Threshold |
|---|---|---|---|---|
| `pool.open_slots` | Gauge | `pool_name` | Open slots in a named pool | =0 → pool fully consumed |
| `pool.running_slots` | Gauge | `pool_name` | Running slots in pool | Compare with pool size |
| `pool.queued_slots` | Gauge | `pool_name` | Queued tasks waiting for pool slot | >0 sustained → pool too small |
| `pool.starving_tasks` | Gauge | `pool_name` | Tasks that cannot run due to pool exhaustion | >0 → increase pool size |

### DAG Run Metrics

| Metric Name | Type | Labels | Description | Alert Threshold |
|---|---|---|---|---|
| `dagrun.duration.success` | Timer (ms) | `dag_id` | Total runtime for successful DAG runs | p99 regression > 50% → investigate |
| `dagrun.duration.failed` | Timer (ms) | `dag_id` | Total runtime for failed DAG runs | Monitor |
| `dagrun.schedule_delay` | Timer (ms) | `dag_id` | Delay between scheduled start and actual start | p99 > 300 000 ms → scheduler overloaded |
| `dagrun.first_task_scheduling_delay` | Timer (ms) | `dag_id` | Delay until first task scheduled in a run | p99 > 120 000 ms → scheduling pressure |
| `sla_email_notification_failure` | Counter | — | SLA notification email failures | >0 → SLA notification pipeline broken |

### Triggerer Metrics

| Metric Name | Type | Labels | Description | Alert Threshold |
|---|---|---|---|---|
| `triggerer_heartbeat` | Counter | — | Triggerer process heartbeat | Rate = 0 → Triggerer down |
| `triggers.running` | Gauge | `hostname` | Active triggers on this Triggerer | Monitor vs capacity |
| `triggerer.capacity_left` | Gauge | — | Remaining trigger capacity | =0 → Triggerer at capacity |
| `triggers.blocked_main_thread` | Counter | — | Triggers that blocked the async event loop | >0 → synchronous code in trigger |
| `triggers.failed` | Counter | — | Triggers that errored before firing | >0 → deferred tasks will never resume |

---

## PromQL Expressions

```promql
# Scheduler dead (heartbeat rate drops to 0)
rate(airflow_scheduler_heartbeat_total[2m]) == 0

# DAG import errors exist
airflow_dag_processing_import_errors > 0

# All executor slots consumed
airflow_executor_open_slots{executor_class_name=~".*"} == 0

# Task failure rate spike — >5 failures per 5 minutes on any (dag, task) pair
increase(airflow_ti_failures_total[5m]) > 5

# Tasks stuck in QUEUED for a long time (proxy: queued count persistently high)
airflow_executor_queued_tasks > 50

# Scheduler loop too slow
histogram_quantile(0.99,
  rate(airflow_scheduler_scheduler_loop_duration_bucket[5m])) > 10000

# DAG parse time above 60 seconds
airflow_dag_processing_total_parse_time > 60

# Pool exhausted
airflow_pool_open_slots{pool_name=~".*"} == 0

# DAG run schedule delay (p99 > 5 min)
histogram_quantile(0.99,
  rate(airflow_dagrun_schedule_delay_bucket[10m])) > 300000

# Triggerer down
rate(airflow_triggerer_heartbeat_total[2m]) == 0
```

---

## Cluster Visibility

```bash
# Scheduler heartbeat (last check-in time)
airflow jobs check --job-type SchedulerJob --hostname <scheduler-host>

# DAG list and paused/active status
airflow dags list
airflow dags list-runs --dag-id <dag-id> --state running

# Task instance status for a DAG run
airflow tasks states-for-dag-run <dag-id> <execution-date>

# Executor slot utilization
celery -A airflow.executors.celery_executor inspect active        # Celery executor
kubectl get pods -n airflow --field-selector=status.phase=Failed  # K8s executor: stale pod check

# Celery worker queue depths
celery -A airflow.executors.celery_executor inspect reserved

# Metadata DB pool stats
airflow db check

# REST API (Airflow 2.x stable REST API)
# List all DAG runs in RUNNING state
curl -s "http://<webserver>:8080/api/v1/dags/~/dagRuns?state=running&limit=100" \
  -H "Authorization: Bearer <token>" | python3 -m json.tool

# List import errors
curl -s "http://<webserver>:8080/api/v1/importErrors" \
  -H "Authorization: Bearer <token>" | python3 -m json.tool

# Health endpoint (scheduler + metadatabase status)
curl -s http://<webserver>:8080/health | python3 -m json.tool

# Web UI key pages
# Airflow Web:    http://<webserver>:8080/
# DAG runs:       http://<webserver>:8080/dagrun/list/
# Task logs:      http://<webserver>:8080/log/list/
# Browse tasks:   http://<webserver>:8080/taskinstance/list/
```

---

## Global Diagnosis Protocol

**Step 1: Infrastructure health**
```bash
# Scheduler alive (checks heartbeat in metadata DB)
airflow jobs check --job-type SchedulerJob 2>&1 | grep -E "(healthy|running|ERROR)"
# Webserver health endpoint (includes scheduler + DB status)
curl -sf http://<webserver>:8080/health | python3 -m json.tool
# Metadata DB connectivity
airflow db check 2>&1 | grep -E "(Connected|ERROR)"
# Celery workers alive (if applicable)
celery -A airflow.executors.celery_executor inspect ping 2>&1 | grep -E "(pong|ERROR|timeout)"
```

**Step 2: Job/workload health**
```bash
# Running DAG runs (per DAG; --dag-id is required, use a wrapper loop for all DAGs)
airflow dags list-runs --dag-id <dag-id> --state running 2>&1 | head -30
# Failed task instances in last 24h (via REST API, since no global CLI)
curl -s "http://<webserver>:8080/api/v1/dags/~/dagRuns?state=failed&limit=50" \
  -H "Authorization: Bearer <token>" | python3 -m json.tool | head -40
# Stuck tasks (queued for > 30 min) — via REST API
curl -s "http://<webserver>:8080/api/v1/tasks?state=queued&limit=50" \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
# Parse errors
curl -s "http://<webserver>:8080/api/v1/importErrors" \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
```

**Step 3: Resource utilization**
```bash
# Executor slots (Celery)
celery -A airflow.executors.celery_executor inspect stats 2>/dev/null | grep -E "(pool|concurrency)"
# Metadata DB connection pool configuration
airflow config get-value database sql_alchemy_pool_size
airflow config get-value database sql_alchemy_max_overflow
# Scheduler process resources
ps aux | grep "airflow scheduler" | awk '{print "CPU%:",$3,"MEM%:",$4}'
```

**Step 4: Data pipeline health**
```bash
# SLA misses in last 24h (no CLI; query the metadata DB directly)
psql -h <db-host> -U airflow -c \
  "SELECT dag_id, task_id, execution_date FROM sla_miss
   WHERE timestamp > NOW() - INTERVAL '24 hours'
   ORDER BY timestamp DESC LIMIT 20;"
# DAG run schedule delay (scheduled vs actual start)
curl -s "http://<webserver>:8080/api/v1/dags/<dag-id>/dagRuns?state=success&limit=10&order_by=-start_date" \
  -H "Authorization: Bearer <token>" | python3 -c "
import sys, json
from datetime import datetime, timezone
runs = json.load(sys.stdin)['dag_runs']
for r in runs:
    scheduled = r.get('logical_date') or r.get('execution_date')
    start = r.get('start_date')
    if scheduled and start:
        from dateutil import parser as dp
        delay_s = (dp.parse(start) - dp.parse(scheduled)).total_seconds()
        print(r['dag_run_id'], 'schedule_delay_s:', round(delay_s, 1))
"
```

**Severity:**
- CRITICAL: `scheduler_heartbeat` rate = 0, metadata DB unreachable, `dag_processing.import_errors` > 0, `executor.open_slots` = 0 for > 10 min
- WARNING: `ti_failures` rate > 2× baseline, `dag_processing.total_parse_time` > 60 s, `dagrun.schedule_delay` p99 > 5 min, `pool.starving_tasks` > 0
- OK: scheduler healthy, workers responding, DAGs parsing cleanly, tasks executing within SLA

---

## Diagnostic Scenario 1: Scheduler Down / Missing Heartbeat

**Symptom:** `scheduler_heartbeat` counter rate = 0; `/health` endpoint shows `scheduler.status: "unhealthy"`.

**Step 1 — Check scheduler process:**
```bash
# Systemd
systemctl status airflow-scheduler
journalctl -u airflow-scheduler --since "10 min ago" | grep -E "(ERROR|FATAL|Exception|Traceback)"

# Kubernetes
kubectl get pods -n airflow -l component=scheduler
kubectl logs -n airflow deploy/airflow-scheduler --tail=200 | grep -E "(ERROR|FATAL|Exception)"

# Check health endpoint
curl -s http://<webserver>:8080/health | python3 -m json.tool
```

**Step 2 — Identify why the scheduler crashed:**
```bash
# Common causes: metadata DB connection exhausted, OOM, parsing deadlock
# Check DB connections used
psql -h <db-host> -U airflow -c \
  "SELECT count(*), state FROM pg_stat_activity WHERE datname='airflow' GROUP BY state"

# Check memory on scheduler host
free -h && ps aux --sort=-%mem | head -10
```

**Step 3 — Restart and monitor:**
```bash
# Restart scheduler
systemctl restart airflow-scheduler
# or K8s:
kubectl rollout restart deployment/airflow-scheduler -n airflow

# Watch heartbeat recover (should see counter increment within 10s)
watch -n5 "airflow jobs check --job-type SchedulerJob"

# If false alarms: tune the health check threshold
# [scheduler] scheduler_health_check_threshold = 30
airflow config get-value scheduler scheduler_health_check_threshold
```

---

## Diagnostic Scenario 2: Task Failure Spike

**Symptom:** `ti_failures` counter rate increases sharply; user reports DAG runs failing.

**Step 1 — Identify which DAGs/tasks are failing:**
```bash
# Via REST API
curl -s "http://<webserver>:8080/api/v1/tasks?state=failed&limit=50" \
  -H "Authorization: Bearer <token>" | python3 -c "
import sys, json
for ti in json.load(sys.stdin)['task_instances']:
    print(ti['dag_id'], '>', ti['task_id'], '| run:', ti['dag_run_id'])
"
# Via CLI for a specific DAG
airflow tasks states-for-dag-run <dag-id> <execution-date>
```

**Step 2 — Fetch task logs:**
```bash
# REST API (no CLI command for fetching task logs in Airflow 2.x)
curl -s "http://<webserver>:8080/api/v1/dags/<dag-id>/dagRuns/<run-id>/taskInstances/<task-id>/logs/1" \
  -H "Authorization: Bearer <token>"

# Or read directly from the log folder:
# {AIRFLOW_HOME}/logs/dag_id=<dag>/run_id=<run>/task_id=<task>/attempt=<n>.log

# For K8s executor: directly from pod logs
kubectl logs -n airflow <task-pod-name> --tail=100
```

**Step 3 — Remediation:**
```bash
# Retry a failed task (clear marks it for re-execution)
airflow tasks clear <dag-id> --task-regex <task-id> --start-date <date> --end-date <date> --yes

# Mark a task instance with a specific state (skipped/success/failed) — REST API
curl -X PATCH \
  "http://<webserver>:8080/api/v1/dags/<dag-id>/dagRuns/<run-id>/taskInstances/<task-id>" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"new_state": "skipped"}'

# Re-run only the failed tasks of past DAG runs
airflow dags backfill --rerun-failed-tasks --start-date <date> --end-date <date> <dag-id>
```

---

## Diagnostic Scenario 3: Executor Capacity Exhaustion

**Symptom:** `executor.open_slots` = 0; `executor.queued_tasks` growing; tasks stuck in QUEUED with `task.queued_duration` p99 > 5 min.

**Step 1 — Measure current executor state:**
```bash
# Celery: active + reserved tasks per worker
celery -A airflow.executors.celery_executor inspect active 2>/dev/null
celery -A airflow.executors.celery_executor inspect stats 2>/dev/null | grep -E "(pool|concurrency|total)"

# K8s executor: check pod states
kubectl get pods -n airflow | grep -E "(Pending|Evicted|OOMKilled)"
kubectl describe nodes | grep -E "(Allocatable:|  cpu:|  memory:)" | head -20
```

**Step 2 — Find the tasks consuming most slots:**
```bash
curl -s "http://<webserver>:8080/api/v1/tasks?state=running&limit=100" \
  -H "Authorization: Bearer <token>" | python3 -c "
import sys, json, collections
tis = json.load(sys.stdin)['task_instances']
by_dag = collections.Counter(ti['dag_id'] for ti in tis)
for dag, cnt in by_dag.most_common(10):
    print(dag, cnt)
"
```

**Step 3 — Scale out / unblock:**
```bash
# Celery: add more workers
airflow celery worker --queues default,high_priority &
# or scale the worker Deployment in K8s
kubectl scale deployment airflow-worker -n airflow --replicas=8

# Increase worker concurrency per instance (celery config)
# [celery] worker_concurrency = 16

# For K8s executor: ensure node autoscaler is provisioning
kubectl get nodes | grep -v Ready
kubectl describe pod <pending-task-pod> | grep -A5 "Events:"
```

---

## Diagnostic Scenario 4: DAG Parse Errors / Import Failures

**Symptom:** `dag_processing.import_errors` > 0; specific DAGs not visible in UI; parse time increasing.

**Step 1 — Identify broken DAG files:**
```bash
# REST API import errors
curl -s "http://<webserver>:8080/api/v1/importErrors" \
  -H "Authorization: Bearer <token>" | python3 -c "
import sys, json
for err in json.load(sys.stdin)['import_errors']:
    print('File:', err['filename'])
    print('Error:', err['stack_trace'][:500])
    print('---')
"
# CLI report
airflow dags report 2>&1 | grep -E "(broken|import_error|error|Exception)"
```

**Step 2 — Reproduce the parse error locally:**
```bash
# Run the DAG file directly to get the full traceback
python3 /opt/airflow/dags/<dag_file>.py 2>&1

# Check for missing Python packages
pip show <required-package> 2>&1

# Check for missing Airflow connections or variables referenced at parse time
# (Best practice: use lazy connection references, not parse-time lookups)
```

**Step 3 — Fix and verify:**
```bash
# After fixing the DAG file, force a re-parse
# Either wait for next parse cycle or trigger via:
airflow dags reserialize

# Verify error is cleared
curl -s "http://<webserver>:8080/api/v1/importErrors" \
  -H "Authorization: Bearer <token>" | python3 -c "
import sys, json; e = json.load(sys.stdin); print('Import errors:', e['total_entries'])
"
# If a DAG is slow to parse (dag_processing.last_duration > 10s):
# — Move heavy imports inside the task callable (not module level)
# — Use dynamic task mapping instead of runtime DAG generation
# — Reduce airflow.cfg [core] dag_file_processor_timeout if needed
```

---

## Metadata DB Slow / Connection Pool Exhausted

```bash
# Check DB connection pool settings
airflow config get-value database sql_alchemy_pool_size
# Purge old log entries to reduce DB size (keep last 30 days)
airflow db clean --clean-before-timestamp "30 days ago" --yes
# Check DB slow queries (PostgreSQL)
psql -h <db-host> -U airflow -c \
  "SELECT pid, now()-query_start AS duration, left(query,120) AS query
   FROM pg_stat_activity
   WHERE state='active' AND datname='airflow'
   ORDER BY duration DESC LIMIT 10"
# Check connection count approaching max_connections
psql -h <db-host> -U airflow -c \
  "SELECT count(*) AS conns, max_conn FROM pg_stat_activity, (SELECT setting::int AS max_conn FROM pg_settings WHERE name='max_connections') s WHERE datname='airflow' GROUP BY max_conn"
# Increase pool size for high-concurrency deployments (airflow.cfg)
# [database] sql_alchemy_pool_size = 10
# [database] sql_alchemy_max_overflow = 20
# [database] sql_alchemy_pool_recycle = 1800
```

---

## Diagnostic Scenario 5: Zombie Task Proliferation

**Symptoms:** `zombies_killed` counter growing; tasks stuck in `running` state for > 30 minutes with no heartbeat; scheduler log shows zombie-clearing activity.

**Root Cause Decision Tree:**
- If worker OOM or crash occurred while tasks were running: → task processes killed without clean shutdown; Airflow never received a state transition → tasks remain `running`
- If network partition separated scheduler from workers: → scheduler cannot receive task heartbeats → zombie detection triggers after timeout
- If `zombies_killed` is incrementing but tasks not clearing: → scheduler is detecting zombies but the clear is not completing (DB contention or scheduler overload)

**Diagnosis:**
```bash
# Find tasks stuck in running state for > 30 minutes
psql -h <db-host> -U airflow -c \
  "SELECT task_id, dag_id, run_id, start_date, now() - start_date AS age
   FROM task_instance
   WHERE state = 'running'
     AND updated_at < NOW() - INTERVAL '30 minutes'
   ORDER BY age DESC LIMIT 20;"

# Check scheduler zombie detection log output
grep -i "zombie" /var/log/airflow/scheduler.log 2>/dev/null | tail -20
# K8s:
kubectl logs -n airflow deploy/airflow-scheduler --since=30m | grep -i zombie

# Worker health check (Celery)
celery -A airflow.executors.celery_executor inspect ping 2>&1 | grep -E "(pong|timeout|error)"
```

**Thresholds:** Tasks in `running` state > 30 minutes without heartbeat = zombie candidate; `zombies_killed` rate > 0 = active issue; `zombies_killed` > 5 per hour = systematic worker instability.

## Diagnostic Scenario 6: DAG Parse Error Preventing Scheduler Progress

**Symptoms:** `dag_processing.import_errors > 0`; specific DAGs not visible in UI or missing from `airflow dags list`; `dagbag_size` dropped; scheduler logs show repeated Python import errors.

**Root Cause Decision Tree:**
- If error is `ModuleNotFoundError` or `ImportError`: → Python dependency missing in the Airflow environment; pip install the package on all workers and scheduler
- If error is `SyntaxError`: → Python syntax error in the DAG file; fix and re-deploy
- If error is `AirflowException` or connection reference at parse time: → DAG references `Variable.get()` or `Connection.get()` at module level (not inside task); move to lazy evaluation inside the task callable

**Diagnosis:**
```bash
# List all import errors via CLI
airflow dags list-import-errors

# Or via REST API
curl -s "http://<webserver>:8080/api/v1/importErrors" \
  -H "Authorization: Bearer <token>" | python3 -c "
import sys, json
for err in json.load(sys.stdin)['import_errors']:
    print('File:', err['filename'])
    print('Error:', err['stack_trace'][:600])
    print('---')
"

# Reproduce the error locally
python3 /opt/airflow/dags/<broken_dag>.py 2>&1

# Check parse time per DAG file (find slow files causing timeouts)
airflow dags report 2>&1 | grep -E "(duration|error|broken)"
```

**Thresholds:** `dag_processing.import_errors > 0` = CRITICAL (broken DAG blocks future schedule updates); parse time per file > 30s = WARNING (may trigger processor timeout).

## Diagnostic Scenario 7: Celery Worker Desync

**Symptoms:** Workers appear in `celery inspect active` output but tasks are not being consumed from the queue; `executor.queued_tasks` growing; `executor.open_slots` > 0 according to Airflow but workers show no activity.

**Root Cause Decision Tree:**
- If `celery inspect ping` times out for specific workers: → worker-broker connectivity broken (Redis/RabbitMQ connection dropped); worker process is alive but disconnected from broker
- If `celery inspect active` shows stale tasks from a previous run: → worker registered with Celery control bus but consuming from wrong queue or stale connection
- If broker (Redis) is overloaded or restarted: → all worker connections invalidated; workers need reconnection

**Diagnosis:**
```bash
# Ping all workers — timeout indicates connectivity issue
celery -A airflow.executors.celery_executor.app inspect ping \
  --timeout 10 2>&1 | grep -E "(pong|timeout|error)"

# Check what workers are actively processing
celery -A airflow.executors.celery_executor.app inspect active 2>&1

# Check reserved (queued but not yet processing) tasks
celery -A airflow.executors.celery_executor.app inspect reserved 2>&1

# Verify broker connectivity from scheduler
redis-cli -h <redis-host> -p 6379 ping
# or RabbitMQ:
rabbitmqctl status 2>&1 | grep -E "(uptime|connections|queues)"

# Check Celery queue depth directly in Redis
redis-cli -h <redis-host> llen celery
```

**Thresholds:** `celery inspect ping` timeout = CRITICAL (worker disconnected); queue depth in Redis > 1000 with workers not consuming = CRITICAL; worker reconnect attempts in logs > 10 = broker instability.

## Diagnostic Scenario 8: Task Retry Storm

**Symptoms:** One DAG's failing task consuming all available retry slots; `executor.queued_tasks` spike dominated by `up_for_retry` tasks from a single DAG; healthy DAGs' tasks starved; `pool.starving_tasks` growing.

**Root Cause Decision Tree:**
- If `ti_failures` spike is concentrated on one `(dag_id, task_id)` pair: → unhandled exception in that task's code; application-level bug
- If retries are consuming pool slots: → pool size too small relative to concurrent retry traffic; or `max_active_runs` too high allowing many concurrent failing runs
- If retries are infinite (task has `retries` set very high): → retry budget not bounded; task will keep retrying until operator timeout

**Diagnosis:**
```bash
# Find the top retry consumers
psql -h <db-host> -U airflow -c \
  "SELECT dag_id, task_id, count(*) AS retry_count
   FROM task_instance
   WHERE state = 'up_for_retry'
   GROUP BY 1, 2
   ORDER BY 3 DESC
   LIMIT 10;"

# Check the task's retry configuration
airflow tasks test <dag-id> <task-id> <execution-date> 2>&1 | head -20

# View the actual failure reason for the retrying task (REST API)
curl -s "http://<webserver>:8080/api/v1/dags/<dag-id>/dagRuns/<run-id>/taskInstances/<task-id>/logs/1" \
  -H "Authorization: Bearer <token>" | tail -50

# Check pool utilization
airflow pools list
```

**Thresholds:** `pool.starving_tasks > 0` sustained = CRITICAL (other tasks blocked); single `(dag_id, task_id)` pair with > 50 retries in flight = WARNING.

## Diagnostic Scenario 9: Database Connection Pool Exhaustion

**Symptoms:** `QueuePool limit of size N overflow N reached, connection timed out` in Airflow logs; scheduler and workers logging `OperationalError: could not connect to server`; Airflow UI slow or returning 500 errors; `scheduler.critical_section_duration` p99 spiking.

**Root Cause Decision Tree:**
- If `max_active_tasks_per_dag × number_of_active_dags × connections_per_task > pool_size`: → pool mathematically undersized for the workload; increase `sql_alchemy_pool_size`
- If pool exhaustion correlates with a specific DAG: → that DAG opens connections in tasks without closing them; connection leak
- If short burst of pool exhaustion: → `sql_alchemy_max_overflow` too low; increase to handle transient spikes
- If persistent exhaustion: → add PgBouncer in transaction-pooling mode between Airflow and PostgreSQL

**Diagnosis:**
```bash
# Check current pool configuration
airflow config get-value database sql_alchemy_pool_size
airflow config get-value database sql_alchemy_max_overflow
airflow config get-value database sql_alchemy_pool_recycle

# Count active DB connections from Airflow
psql -h <db-host> -U airflow -c \
  "SELECT application_name, state, count(*)
   FROM pg_stat_activity
   WHERE datname = 'airflow'
   GROUP BY 1, 2
   ORDER BY 3 DESC;"

# Check max_connections on the PostgreSQL server
psql -h <db-host> -U airflow -c \
  "SHOW max_connections;"

# Find connection leaks: long-idle connections from specific Airflow components
psql -h <db-host> -U airflow -c \
  "SELECT pid, application_name, state, now() - state_change AS idle_duration
   FROM pg_stat_activity
   WHERE datname = 'airflow'
     AND state = 'idle'
     AND now() - state_change > INTERVAL '10 minutes'
   ORDER BY idle_duration DESC LIMIT 20;"
```

**Thresholds:** `sql_alchemy_pool_size` default = 5 per process; connection count approaching PostgreSQL `max_connections` = CRITICAL; `QueuePool limit reached` in logs = CRITICAL.

## Diagnostic Scenario 10: Scheduler Heartbeat Timeout Causing All DAGs to Stop Being Scheduled

**Symptoms:** `scheduler_heartbeat` metric rate drops to 0; all DAG runs stop being created even for schedules that should have triggered; existing running tasks continue (worker picks them up), but no NEW dag runs are queued; the count of running DAG runs in the metadata DB (`SELECT count(*) FROM dag_run WHERE state='running'`) stays at last-known value, never increases; alert fires on `rate(scheduler_heartbeat[5m]) == 0`.

**Root Cause Decision Tree:**
- If scheduler process is alive (PID exists) but heartbeat stopped: → scheduler loop is blocked — usually on database contention (critical section lock) or a Python exception in the scheduling loop that is being swallowed
- If scheduler process has exited: → unhandled exception or OOM; check `journalctl` or pod logs
- If this is HA setup with multiple schedulers: → all scheduler pods have a network partition to the DB; or only the "leader" scheduler is down and failover hasn't occurred
- If `scheduler.critical_section_duration` p99 is > 10s before heartbeat stops: → DB deadlock held the critical section lock; scheduler eventually aborted

```bash
# Check scheduler process state
pgrep -a airflow 2>/dev/null | grep scheduler
# Kubernetes:
kubectl get pods -n airflow -l component=scheduler 2>/dev/null

# Recent scheduler logs
kubectl logs -n airflow -l component=scheduler --tail=100 2>/dev/null \
  | grep -iE "error|exception|heartbeat|critical|died|killed"

# Last heartbeat timestamp in DB
psql -h <db-host> -U airflow -c \
  "SELECT hostname, latest_heartbeat, (NOW() - latest_heartbeat) AS age
   FROM scheduler_job
   ORDER BY latest_heartbeat DESC LIMIT 5;"

# Scheduler loop duration (is it getting slow before failing?)
# PromQL: histogram_quantile(0.99, rate(scheduler_loop_duration_bucket[5m]))

# Critical section hold time
psql -h <db-host> -U airflow -c \
  "SELECT pid, now() - query_start AS duration, state, LEFT(query, 100) AS query
   FROM pg_stat_activity
   WHERE datname='airflow' AND state != 'idle'
   ORDER BY duration DESC LIMIT 10;"
```

**Thresholds:** `rate(scheduler_heartbeat[5m]) == 0` for > 2 min = CRITICAL; scheduler heartbeat age > 5 min in DB = CRITICAL; `scheduler.critical_section_duration` p99 > 10s = WARNING.

## Diagnostic Scenario 11: XCom Table Bloat Causing Scheduler Performance Degradation

**Symptoms:** Scheduler loop duration (`scheduler.scheduler_loop_duration`) p99 gradually increasing over weeks; `dag_processing.total_parse_time` increasing; PostgreSQL `VACUUM` taking increasingly long on `xcom` table; task completion slowing even though task code is unchanged; queries against `task_instance` join are slow.

**Root Cause Decision Tree:**
- If `xcom` table has millions of rows: → tasks storing large values in XCom (DataFrames, large JSON) accumulate across all historical runs; Airflow's built-in XCom cleanup only runs with DAG run cleanup
- If `max_active_runs` is high and DAGs run frequently: → XCom entries multiply with each run; no automatic TTL on XCom entries
- If tasks are using `ti.xcom_push(value=<large_dataframe>)`: → each XCom entry can be MBs in size; millions of rows × large values = very large table

```bash
# Check xcom table size
psql -h <db-host> -U airflow -c \
  "SELECT pg_size_pretty(pg_total_relation_size('xcom')) AS xcom_size,
     (SELECT count(*) FROM xcom) AS row_count;"

# Largest XCom entries
psql -h <db-host> -U airflow -c \
  "SELECT dag_id, task_id, key,
     pg_size_pretty(octet_length(value::text)::bigint) AS value_size,
     execution_date
   FROM xcom
   ORDER BY octet_length(value::text) DESC LIMIT 20;"

# Distribution by DAG (which DAGs are storing most XComs)
psql -h <db-host> -U airflow -c \
  "SELECT dag_id, count(*) cnt,
     pg_size_pretty(SUM(octet_length(value::text))::bigint) AS total_size
   FROM xcom
   GROUP BY dag_id ORDER BY total_size DESC LIMIT 10;"

# Scheduler query time on xcom (shows up in pg_stat_statements if enabled)
psql -h <db-host> -U airflow -c \
  "SELECT query, calls, mean_exec_time, total_exec_time
   FROM pg_stat_statements
   WHERE query ILIKE '%xcom%'
   ORDER BY total_exec_time DESC LIMIT 10;" 2>/dev/null
```

**Thresholds:** `xcom` table > 1 GB = WARNING; > 10 GB = CRITICAL; XCom entry > 1 MB = WARNING (anti-pattern); scheduler loop duration p99 growing > 20% week-over-week = WARNING.

## Diagnostic Scenario 12: Database Migration Failure During Airflow Upgrade

**Symptoms:** Airflow upgrade (e.g., 2.7 → 2.8) fails mid-migration; scheduler won't start, logging `Table 'dag_run' doesn't have a column named 'triggered_by'` or similar schema errors; web server returns 500 on all pages; `airflow db check` reports schema version mismatch.

**Root Cause Decision Tree:**
- If `airflow db migrate` (or `airflow db upgrade`) was interrupted: → partial migration; some Alembic revisions applied, others not; DB in inconsistent state
- If multiple Airflow instances ran migration simultaneously: → race condition on Alembic version table; duplicate revision attempts
- If migration ran out of disk space mid-alter: → `ALTER TABLE` rolled back but Alembic revision table marked as applied
- If downgrade was attempted after partial upgrade: → Alembic downgrade scripts may not cleanly reverse complex schema changes

```bash
# Check current DB schema version vs expected
airflow db check 2>&1 | head -20

# Check Alembic revision history
psql -h <db-host> -U airflow -c \
  "SELECT version_num FROM alembic_version;"

# Compare current revision against the expected head revision shipped with this Airflow version
# (Airflow 2.x has no `airflow db show-migrations`; use Alembic via the Airflow shell or `airflow db check-migrations`)
airflow db check-migrations 2>&1

# Check for pending migrations
airflow db check-migrations 2>&1

# Inspect what migration is stuck
psql -h <db-host> -U airflow -c \
  "SELECT pid, query_start, state, LEFT(query, 120) AS query
   FROM pg_stat_activity
   WHERE datname='airflow' AND state != 'idle'
   ORDER BY query_start LIMIT 5;"

# Check Airflow logs for migration error
journalctl -u airflow-webserver --since "1 hour ago" 2>/dev/null \
  | grep -iE "error|migration|alembic|column|table" | tail -30
```

**Thresholds:** Any schema version mismatch = CRITICAL (Airflow will not start); partial migration detected = CRITICAL; migration running > 30 min = WARNING (table lock).

## Diagnostic Scenario 13: Worker Task Stuck on Heavy Python Library Import

**Symptoms:** Task instances in `running` state for much longer than expected; worker log shows no output after `[2024-XX-XX] INFO - Executing...`; task never reaches user code; worker CPU is low (not executing) but task heartbeat is still being sent; tasks eventually timeout; issue reproducible on first run after worker restart but not on subsequent runs (import cached in memory).

**Root Cause Decision Tree:**
- If task hangs immediately on worker startup: → cold import of heavy library (e.g., `import torch`, `import tensorflow`, `import scipy`) taking > 60s; watchdog timeout fires before import completes
- If the issue only affects the first task after a new worker pod starts: → Python import cache is empty in a fresh container; subsequent tasks use cached imports
- If hang is on specific DAG only: → that DAG imports a module at the top level with a blocking side effect (network call, DB init, or heavy computation at import time)

```bash
# Find tasks stuck in running state for > expected duration
psql -h <db-host> -U airflow -c \
  "SELECT dag_id, task_id, run_id, hostname,
     NOW() - start_date AS running_for,
     try_number
   FROM task_instance
   WHERE state = 'running'
     AND start_date < NOW() - INTERVAL '30 minutes'
   ORDER BY running_for DESC LIMIT 10;"

# Check worker logs for the stuck task
# Airflow 2.x log layout: {AIRFLOW_HOME}/logs/dag_id=<dag>/run_id=<run>/task_id=<task>/attempt=<n>.log
# Or via REST API (no CLI to fetch task logs in 2.x):
curl -s "http://<webserver>:8080/api/v1/dags/<dag-id>/dagRuns/<run-id>/taskInstances/<task-id>/logs/1" \
  -H "Authorization: Bearer <token>" | tail -30

# Profile import time manually on a worker pod
kubectl exec -n airflow -l component=worker -it -- \
  python3 -c "import time; t=time.time(); import <heavy_module>; print(f'Import took: {time.time()-t:.2f}s')"

# Check if DAG file has top-level imports that cause side effects
head -50 /opt/airflow/dags/<dag_file>.py | grep "^import\|^from"
```

**Thresholds:** Task with no log output for > 5 min after start = WARNING; Python import time > 30s = WARNING; task running > 3× expected duration = WARNING.

## Diagnostic Scenario 14: DAG Bag Parsing Failure Causing All DAGs to Disappear from UI

**Symptoms:** All DAGs suddenly disappear from the Airflow web UI; `dagbag_size` metric drops to 0; existing running tasks continue to completion but no new runs are scheduled; `dag_processing.import_errors` counter jumps from 0 to a large number; rollback of last DAG deployment restores visibility.

**Root Cause Decision Tree:**
- If `dag_processing.import_errors` spike coincides with a DAG deployment: → a newly deployed DAG file has a syntax error or import that raises an exception at parse time; Airflow's DagBag catches the exception per-file but if the error is in a shared module imported by all DAGs, all DAGs fail to parse
- If a shared utility module (`utils/common.py`) changed: → all DAG files importing that module fail with ImportError simultaneously
- If Python version changed on workers: → syntax that was valid in old Python version is invalid in new version (e.g., walrus operator, f-string syntax)
- If `dag_processing.processor_timeouts` > 0: → one DAG file's parse is hanging; processor timeout kills it; if it holds a shared lock, other parsers may also time out

```bash
# Check current DagBag size
# Via metric: dagbag_size should be > 0
# Via API:
curl -s http://localhost:8080/api/v1/dags?only_active=true \
  -H "Authorization: Basic $(echo -n 'admin:airflow' | base64)" \
  | jq '.total_entries'

# Check import errors
airflow dags list-import-errors 2>/dev/null

# Via DB:
psql -h <db-host> -U airflow -c \
  "SELECT filename, stacktrace, timestamp
   FROM import_error
   ORDER BY timestamp DESC LIMIT 20;"

# Parse a specific DAG file manually to see the error
python3 /opt/airflow/dags/<dag_file>.py 2>&1 | tail -20

# Check recently modified DAG files
find /opt/airflow/dags -name "*.py" -newer /tmp/ref_timestamp \
  -mmin -60 2>/dev/null

# Processor timeout count
grep "processor_timeout\|timed out\|killed" \
  /var/log/airflow/dag_processor_manager.log 2>/dev/null | tail -10
```

**Thresholds:** `dagbag_size == 0` for > 5 min = CRITICAL (all scheduling stopped); `dag_processing.import_errors > 0` = WARNING; all DAGs disappearing after deployment = CRITICAL.

## Diagnostic Scenario 15: Silent Task Success with Data Loss (Idempotency)

**Symptoms:** DAG shows all tasks `success`. Data pipeline output incomplete. No errors in logs.

**Root Cause Decision Tree:**
- If task uses `INSERT INTO ... SELECT` without idempotency check → on retry, inserts duplicate rows; first run may have partial data if the query was interrupted mid-flight
- If `on_failure_callback` not configured AND exception is caught inside task code → task failure swallowed; Airflow sees a clean exit code and marks the task `success`
- If `provide_context=True` but `execution_date` not used for partitioned writes → task overwrites the wrong partition on every run regardless of logical date

**Diagnosis:**
```bash
# Step 1: Compare actual row counts written vs expected
# Check task logs for any "rows affected" or "records written" output (REST API or filesystem)
curl -s "http://<webserver>:8080/api/v1/dags/<dag_id>/dagRuns/<run_id>/taskInstances/<task_id>/logs/1" \
  -H "Authorization: Bearer <token>"

# Step 2: Verify output table counts match source
# Run this against your target DB:
# SELECT COUNT(*) FROM output_table WHERE partition_dt = '<execution_date>';
# Compare against source: SELECT COUNT(*) FROM source_table WHERE event_date = '<execution_date>';

# Step 3: Look for swallowed exceptions in task source code
grep -r "except.*pass\|except.*logger\|except.*log\." dags/ | grep -v ".pyc"

# Step 4: Check if task retried (retry count > 0 indicates prior failure)
psql -h <db-host> -U airflow -c \
  "SELECT task_id, try_number, state, start_date FROM task_instance
   WHERE dag_id='<dag_id>' AND try_number > 1
   ORDER BY start_date DESC LIMIT 20;"

# Step 5: Verify idempotency — run task again and check row count didn't double
# SELECT COUNT(*), partition_dt FROM output_table GROUP BY partition_dt ORDER BY partition_dt DESC LIMIT 5;
```

**Thresholds:** Any task that writes data without a `DELETE WHERE partition = execution_date` guard before insert = idempotency risk. Any `except` block that does not re-raise = silent failure risk.

## Diagnostic Scenario 16: Cross-Service Chain — Slow Metadata DB Causing DAG Slowdown

**Symptoms:** Airflow DAG latency growing day over day. No individual task fails. Scheduler shows healthy heartbeat. SLA misses accumulating.

**Root Cause Decision Tree:**
- Alert: Airflow DAG SLA miss / DAG run schedule delay growing
- Real cause: PostgreSQL metadata DB query latency growing → `SELECT` on `dag_run`/`task_instance` tables increasingly slow
- If `task_instance` table has millions of rows and no periodic cleanup → table bloat slowing every scheduler loop iteration
- If autovacuum is falling behind on `task_instance` → dead tuples accumulate → sequential scans degrade
- If `dag_run` records for finished runs are never archived → scheduler `find_queued_dagruns` query scans entire history

**Diagnosis:**
```bash
# Step 1: Check Airflow metadata DB connectivity and response time
time airflow db check

# Step 2: Check table bloat on task_instance and dag_run
psql -U airflow -d airflow -c "
SELECT relname, n_live_tup, n_dead_tup,
       round(n_dead_tup::numeric / NULLIF(n_live_tup + n_dead_tup, 0) * 100, 1) AS dead_pct
FROM pg_stat_user_tables
WHERE relname LIKE 'task%' OR relname LIKE 'dag%'
ORDER BY n_dead_tup DESC;"

# Step 3: Check total row counts (millions = cleanup needed)
psql -U airflow -d airflow -c "
SELECT 'task_instance' AS tbl, COUNT(*) FROM task_instance
UNION ALL
SELECT 'dag_run', COUNT(*) FROM dag_run
UNION ALL
SELECT 'log', COUNT(*) FROM log;"

# Step 4: Check scheduler loop duration metric
# airflow_scheduler_loop_duration_seconds{quantile="0.99"} > 10s = DB is the bottleneck

# Step 5: Check for missing indexes causing slow queries
psql -U airflow -d airflow -c "
SELECT schemaname, tablename, attname, n_distinct, correlation
FROM pg_stats WHERE tablename = 'task_instance'
ORDER BY tablename, attname;"
```

**Thresholds:** `task_instance` table > 5 million rows without cleanup = scheduler loop degradation. `dag_run` table dead tuple percentage > 20% = vacuum needed immediately.

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `Task exited with return code Negsignal.SIGKILL` | Worker OOM killed by kernel | `kubectl top pod -l component=worker` |
| `ERROR - Executor reports task instance finished (failed) although the task says its still running` | Zombie task — executor and task state diverged | `airflow tasks clear <dag_id> -t <task_id> --yes` |
| `CRITICAL - Callback Failed` | Callback URL unreachable from worker network | Check Flower UI and worker-to-webserver connectivity |
| `OperationalError: (psycopg2.OperationalError) server closed the connection unexpectedly` | Metadata DB connection lost (pool exhaustion or restart) | `airflow db check` |
| `DagFileProcessingStats: Unable to parse dag file xxx` | Syntax or import error in DAG file | `python <dag_file>.py` |
| `Task is in the running state but the pod is not there` | Pod evicted mid-run (node pressure or spot preemption) | `kubectl get events -n airflow --sort-by='.lastTimestamp'` |
| `[celery.backends.database] ERROR: retrying` | Celery result backend (Redis or DB) unreachable | Check Redis/DB backend config and connectivity |
| `Worker exited prematurely: signal 15 (SIGTERM)` | Worker scaled down or restarted while task was running | Set `AIRFLOW__CELERY__WORKER_SHUTDOWN_WAIT_TIMEOUT` |
| `ERROR - DAG <dag_id> is not found in DagBag` | DAG file not yet synced to scheduler or parser error | `airflow dags list \| grep <dag_id>` |

---

# Capabilities

1. **Scheduler management** — Health monitoring, parsing tuning, HA configuration
2. **Task debugging** — Log analysis, failure diagnosis, dependency resolution
3. **Executor operations** — Local/Celery/K8s executor management
4. **DAG management** — Parse error resolution, optimization, dependency design
5. **Connection management** — External system connectivity, credential rotation
6. **Database maintenance** — Cleanup, pool sizing, migration

# Critical Metrics to Check First

1. `scheduler_heartbeat` rate — 0 = scheduler dead, all orchestration stopped
2. `executor.open_slots` — 0 = no capacity for new tasks
3. `ti_failures` rate vs `ti_successes` rate — ratio spike = regression
4. `dag_processing.import_errors` — any non-zero = broken DAGs in production
5. `task.queued_duration` p99 — above 5 min = executor bottleneck
6. `dagrun.schedule_delay` p99 — above 5 min = scheduler loop too slow

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| DAG tasks failing with `OperationalError: could not connect` | PostgreSQL metadata DB connection pool exhausted due to long-running analytical queries from another app sharing the same DB | `psql -h <db-host> -U airflow -c "SELECT application_name, state, count(*) FROM pg_stat_activity WHERE datname='airflow' GROUP BY 1,2 ORDER BY 3 DESC;"` |
| Celery workers not picking up tasks despite `open_slots > 0` | Redis broker restarted and workers have stale connections; workers are alive but disconnected from the task queue | `redis-cli -h <redis-host> ping && redis-cli -h <redis-host> llen celery` |
| Scheduler heartbeat stopping | PostgreSQL `max_connections` hit because a separate service opened a connection storm; Airflow scheduler cannot acquire a DB connection to write heartbeat | `psql -h <db-host> -U airflow -c "SELECT count(*), max_conn FROM pg_stat_activity, (SELECT setting::int AS max_conn FROM pg_settings WHERE name='max_connections') s GROUP BY max_conn;"` |
| Tasks completing successfully but output data wrong or missing | Downstream S3/GCS bucket permissions revoked; task exits 0 but writes silently fail | `aws s3 ls s3://<bucket>/<prefix>/ 2>&1 | head -5` or `gsutil ls gs://<bucket>/<prefix>/` |
| All sensor tasks stuck in `poking` state | External system (API or DB) the sensors poll is down or returning 5xx; sensors keep polling indefinitely | `curl -s "http://<webserver>:8080/api/v1/dags/<dag_id>/dagRuns/<run_id>/taskInstances/<sensor_task>/logs/1" -H "Authorization: Bearer <token>" | tail -20` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N Celery worker pods unable to connect to Redis | `celery inspect ping` times out for specific workers while others respond; overall queue consumption slowed but not stopped | Tasks assigned to that worker time out; retry eventually lands on a healthy worker; increased latency for affected tasks | `celery -A airflow.executors.celery_executor.app inspect ping --timeout 10 2>&1 | grep -E "pong|timeout|error"` |
| 1 of N DAG file parsers hitting timeout on a heavy DAG | `dag_processing.processor_timeouts` counter incrementing; most DAGs parse fine but one DAG always near parse timeout | That DAG's schedule updates are slow or miss intervals; other DAGs unaffected | `airflow dags report 2>&1 | sort -t'|' -k3 -rn | head -10` |
| 1 pool exhausted while other pools have capacity | `pool.starving_tasks > 0` for one specific pool name; tasks in that pool queue up while other pools are idle | DAGs using the exhausted pool stall; unrelated DAGs using other pools run fine | `airflow pools list` |
| 1 DAG with connection leak degrading metadata DB for all other DAGs | Most DAGs healthy; one specific DAG's tasks correlate with DB connection count spikes; indirect impact on all scheduler operations | Scheduler loop slows during that DAG's runs; cascade to `dagrun.schedule_delay` p99 | `psql -h <db-host> -U airflow -c "SELECT application_name, state, count(*) FROM pg_stat_activity WHERE datname='airflow' GROUP BY 1,2 ORDER BY 3 DESC;"` |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| DAG scheduler heartbeat age | > 30s since last heartbeat | > 60s since last heartbeat | `airflow jobs check --job-type SchedulerJob --hostname $(hostname) 2>&1` |
| DAG file parse time (slowest DAG) | > 30s | > 60s | `airflow dags report 2>&1 | sort -t'|' -k3 -rn | head -10` |
| Task queue depth (queued + scheduled tasks) | > 200 tasks | > 1,000 tasks | `airflow tasks states-for-dag-run --help 2>/dev/null; airflow db check && psql -U airflow -c "SELECT state, count(*) FROM task_instance WHERE state IN ('queued','scheduled') GROUP BY state;"` |
| Celery worker open slots | < 10% of total slots | 0 open slots | `celery -A airflow.executors.celery_executor.app inspect stats 2>/dev/null | grep -E "pool|max-concurrency|total"` |
| Zombie task count (running but heartbeat stale) | > 5 zombies | > 20 zombies | `airflow tasks list --dag-id <dag_id> 2>/dev/null; psql -U airflow -c "SELECT count(*) FROM task_instance WHERE state='running' AND latest_heartbeat < now() - interval '300 seconds';"` |
| DAG run failure rate (last 1 hour) | > 10% of runs failing | > 30% of runs failing | `psql -U airflow -c "SELECT dag_id, state, count(*) FROM dag_run WHERE execution_date > now() - interval '1 hour' GROUP BY dag_id, state ORDER BY dag_id, state;"` |
| Metadata DB connection pool utilization | > 70% of pool used | > 90% of pool used (or connections pending) | `psql -h <db-host> -U airflow -c "SELECT count(*), state FROM pg_stat_activity WHERE datname='airflow' GROUP BY state;"` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Celery queue depth (`airflow_celery_task_timeout_error` + `SELECT count(*) FROM task_instance WHERE state='queued'`) | Queued task count > 2× worker concurrency for > 15 min | Scale up Celery worker replicas: `kubectl scale deployment/airflow-worker --replicas=<N+2>`; increase worker concurrency | 1–2 hours |
| Metadata DB connection pool (`SELECT count(*) FROM pg_stat_activity WHERE datname='airflow'`) | Active connections > 80% of `max_connections` | Tune SQLAlchemy pool settings (`pool_size`, `max_overflow`); add PgBouncer in front of the DB | 1–2 hours |
| Metadata DB disk usage | Fill rate projects full within 5 days | Archive and purge old `dag_run`, `task_instance`, `log` rows: `airflow db clean --clean-before-timestamp "$(date -d '90 days ago' +%Y-%m-%dT%H:%M:%S)"` | 1–2 days |
| Log storage volume (`du -sh /opt/airflow/logs`) | > 70% of mounted volume | Enable remote logging (S3/GCS); set `log_retention_days` in `airflow.cfg`; add log rotation | 1 week |
| Scheduler heartbeat latency (`airflow_scheduler_heartbeat` Prometheus metric) | Heartbeat interval > 10 s sustained | Check scheduler CPU/memory; reduce DAG parse frequency (`min_file_process_interval`); increase scheduler pods | 1–2 hours |
| DAG parse duration (`airflow_dag_processing_total_parse_time_s`) | > 30 s for any DAG or growing week-over-week | Profile DAG files for import-time side effects (DB calls, network); consider DAG serialization | 1 week |
| Worker memory (`kubectl top pods -l component=worker`) | Any worker pod > 80% memory request | Increase worker memory limits; reduce `worker_concurrency`; profile memory-heavy tasks | 1–2 days |
| `task_instance` table row count | > 10M rows (query: `SELECT count(*) FROM task_instance`) | Schedule regular `airflow db clean` job; partition or archive historical runs | 1 week |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check scheduler heartbeat — last seen timestamp (stale > 60s = scheduler down)
airflow jobs check --job-type SchedulerJob --hostname "$(hostname)" 2>/dev/null || airflow db check

# Count tasks by state across all DAGs in the last 24 hours
airflow tasks states-for-dag-run --help &>/dev/null && \
  python3 -c "from airflow.models import TaskInstance; from airflow.utils.session import create_session; from datetime import datetime, timedelta; \
from collections import Counter; \
with create_session() as s: tis = s.query(TaskInstance).filter(TaskInstance.execution_date >= datetime.utcnow()-timedelta(hours=24)).all(); \
print(Counter(ti.state for ti in tis))"

# Show all currently running task instances with duration
airflow tasks list --help &>/dev/null; \
  python3 -c "from airflow.models import TaskInstance; from airflow.utils.session import create_session; \
with create_session() as s: [print(ti.dag_id, ti.task_id, ti.state, ti.duration) for ti in s.query(TaskInstance).filter(TaskInstance.state=='running').all()]"

# Find DAGs with failed tasks in the last 6 hours
python3 -c "from airflow.models import DagRun; from airflow.utils.session import create_session; from datetime import datetime, timedelta; \
with create_session() as s: [print(dr.dag_id, dr.run_id, dr.state, dr.execution_date) for dr in s.query(DagRun).filter(DagRun.state=='failed', DagRun.execution_date >= datetime.utcnow()-timedelta(hours=6)).all()]"

# Check Celery worker queue depth (number of pending tasks)
celery -A airflow.executors.celery_executor.app inspect active 2>/dev/null | python3 -m json.tool | grep -c "task_id" || echo "Celery not in use"

# Show scheduler lag — difference between scheduled and actual execution times
python3 -c "from airflow.models import DagRun; from airflow.utils.session import create_session; from datetime import datetime, timedelta; \
with create_session() as s: drs=s.query(DagRun).filter(DagRun.start_date >= datetime.utcnow()-timedelta(hours=1)).all(); \
[print(dr.dag_id, round((dr.start_date - dr.execution_date).total_seconds()/60, 1), 'min lag') for dr in drs if dr.start_date]"

# Check Airflow metadata DB connectivity
airflow db check && echo "DB OK" || echo "DB FAILED"

# Show DAGs that are paused (may explain missing runs)
airflow dags list --output=table 2>/dev/null | grep "True" | awk '{print $1}'

# Inspect worker pod logs for exceptions (Kubernetes executor)
kubectl logs -n airflow -l component=worker --since=15m 2>/dev/null | grep -E "ERROR|Exception|Traceback" | tail -30

# Check disk usage on the dag and log volumes
df -h /opt/airflow/dags /opt/airflow/logs 2>/dev/null || df -h $(airflow config get-value core dags_folder) $(airflow config get-value logging base_log_folder)
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Scheduler Availability | 99.9% | `up{job="airflow-scheduler"}` — scheduler heartbeat fresher than 60 s | 43.8 min | > 14.4x baseline |
| Task Success Rate | 99.5% | `rate(airflow_task_instance_created_Failed[5m]) / (rate(airflow_task_instance_created_Success[5m]) + rate(airflow_task_instance_created_Failed[5m]))` | 3.6 hr | > 6x baseline |
| DAG Processing Latency p99 | < 30 s from scheduled time to first task running | `histogram_quantile(0.99, rate(airflow_dagrun_schedule_delay_bucket[5m]))` | 43.8 min | > 14.4x baseline |
| Zombie Task Rate | < 0.01% of running tasks become zombies | `airflow_zombies_killed` counter rate over 30 days | 43.8 min | > 14.4x baseline |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Authentication enabled (web UI) | `airflow config get-value webserver authenticate 2>/dev/null || grep -E "AUTH_BACKEND\|auth_backend" ~/airflow/webserver_config.py` | Auth backend is not `airflow.api.auth.backend.deny_all`; LDAP/OAuth/password configured |
| Fernet key set (secret encryption) | `airflow config get-value core fernet_key 2>/dev/null | wc -c` | Non-empty key present; default empty key means connection passwords stored in plaintext |
| Executor configured for scale | `airflow config get-value core executor` | `CeleryExecutor` or `KubernetesExecutor` in production; `SequentialExecutor` is single-threaded dev-only |
| Metadata DB connection pool sized | `airflow config get-value database sql_alchemy_pool_size 2>/dev/null` | `sql_alchemy_pool_size >= 5`; default 5 may be too low under heavy parallelism |
| Parallelism and max active tasks | `airflow config get-value core parallelism && airflow config get-value core max_active_tasks_per_dag` | Values match worker capacity; `parallelism` should not exceed total worker slots |
| DAG serialization enabled | `airflow config get-value core min_serialized_dag_update_interval 2>/dev/null` | Serialization on (default in 2.x); reduces DB load from repeated DAG parsing |
| Log remote storage configured | `airflow config get-value logging remote_logging && airflow config get-value logging remote_base_log_folder` | `remote_logging = True` with S3/GCS path; local-only logs are lost when pods restart |
| Scheduler health check threshold | `airflow config get-value scheduler scheduler_health_check_threshold` | ≤ 30 s; a high value delays detection of a dead scheduler |
| Default pool size adequate | `airflow pools get default_pool -o json 2>/dev/null | python3 -c "import sys,json; p=json.load(sys.stdin); print(p.get('slots','?'))"` | Slot count matches expected concurrency; `default_pool` with 128 slots is the default — verify it isn't the bottleneck |
| DAG folder permissions | `ls -la $(airflow config get-value core dags_folder 2>/dev/null || echo /opt/airflow/dags)` | Readable by the `airflow` user; no world-writable DAG directory that could allow code injection |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `Broken DAG: [/path/to/dag.py]` | ERROR | Python syntax error or import failure in a DAG file; DAG will not appear in the UI | Fix the Python error in the DAG file; check imports and dependencies |
| `Task exited with return code 1` | ERROR | Task process exited non-zero; operator command or Python callable failed | Check task logs for the specific error; inspect operator configuration |
| `ERROR - Failed to import` | ERROR | A DAG's Python import failed (missing package or broken dependency) | Install the missing package in the Airflow environment; check `requirements.txt` |
| `Cannot execute, we have more workers than DAGs` | WARN | More Celery workers than expected; workers running stale code or duplicate workers | Check for zombie workers; restart Celery workers after deployments |
| `Scheduler heartbeat not received` | WARN | Scheduler process is not sending heartbeats; scheduler may be dead or stuck | Check scheduler process; restart `airflow scheduler`; look for OOM kills |
| `DagRun is stuck in running state` | WARN | DAG run has been in RUNNING state longer than expected; tasks may be hung | Check running task logs; manually clear or mark tasks failed; kill stuck executors |
| `Task is in up_for_retry state` | INFO | Task failed and is waiting for its retry interval to elapse | Expected behavior; escalate if max retries reached without success |
| `Unable to find dag` | ERROR | Scheduler received a task but the corresponding DAG was not serialized in the DB | Trigger DAG file re-parse; ensure DAG is in the `dags_folder` and has no errors |
| `Connection refused` (to metadata DB) | ERROR | Airflow cannot reach the PostgreSQL/MySQL metadata database | Check DB connectivity; verify connection string; check DB health and connection pool |
| `celery.backends.database.DatabaseBackend` | ERROR | Celery result backend DB write failed; task state not persisted | Check DB credentials for Celery result backend; verify DB is not full |
| `MemoryError` in task log | FATAL | Task consumed all available memory in the worker pod/process | Increase worker memory limits; optimize task data handling; avoid loading full datasets |
| `DAG is paused` | INFO | DAG was administratively paused; no runs will be scheduled | Intentional; if unexpected, check for automated pause scripts or UI access |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `AirflowTaskTimeout` | Task exceeded its `execution_timeout` parameter | Task marked as failed; downstream tasks blocked | Increase `execution_timeout`; optimize task logic; add checkpointing |
| `AirflowSensorTimeout` | Sensor operator timed out waiting for an external condition | Sensor task fails; dependent tasks blocked | Investigate the external condition; increase `timeout`; use `reschedule` mode |
| `AirflowSkipException` | Task intentionally skipped via `raise AirflowSkipException()` | Task marked SKIPPED; downstream `all_success` tasks also skipped | Expected behavior; verify branching logic is correct |
| `AirflowException: Task force-failed` | Task was manually marked as failed from the UI or API | Run state updated; downstream tasks may cascade-fail | Investigate why manual intervention was needed; check task logic |
| `UP_FOR_RETRY` | Task failed and has retries remaining | Task not yet terminal; will re-execute after delay | Monitor retries; if all retries fail, investigate root cause |
| `ZOMBIE` (task state) | Task process disappeared without updating state; scheduler reaps it | Run stalls until reaper clears zombie | Scheduler will auto-clear after `scheduler_zombie_task_threshold`; check worker health |
| `UPSTREAM_FAILED` | At least one upstream task failed; this task will not run | Cascade failure through the DAG | Fix the upstream task failure; clear and re-run the DAG |
| `DEFERRED` | Task is using the Triggerer to wait for an async event | Task slot released; Triggerer holds state | Verify `airflow triggerer` process is running; check trigger logs |
| `PoolNotFound` | Task references a non-existent Airflow pool | All tasks in that pool fail immediately | Create the pool via UI or `airflow pools set <name> <slots> ""` |
| `IntegrityError` (metadata DB) | Duplicate key or constraint violation in the Airflow metadata DB | Specific DAG run or task instance may fail to persist | Usually transient; investigate if persistent; may indicate a bug in DAG code |
| `InvalidDagId` | DAG ID contains invalid characters or exceeds length limit | DAG rejected by the scheduler | Rename the DAG to use only alphanumeric, dashes, and underscores; max 250 chars |
| `AirflowConfigException` | `airflow.cfg` / environment variable contains an invalid configuration value | Airflow component fails to start | Validate config; check for typos in env vars; review Airflow docs for valid values |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Scheduler Stall | `airflow_scheduler_heartbeat` flatlines; `airflow_dag_processing_last_run_seconds` stale | `Scheduler heartbeat not received` | `AirflowSchedulerDown` | Scheduler process dead or deadlocked on metadata DB | Restart scheduler; check for DB connection exhaustion; inspect OOM kills |
| DAG Import Cascade Failure | `airflow_import_errors` count rises; multiple DAGs disappear from UI | `Broken DAG: [path]`, `Failed to import` | `AirflowImportErrors` | Bad commit introduced syntax/import error in shared DAG module | Revert bad file; run `airflow dags reserialize`; gate deploys with `airflow dags test` |
| Celery Worker Starvation | `airflow_pool_open_slots{pool="default_pool"}` = 0; queued tasks not starting | `Cannot execute, we have more workers than DAGs` or no log (tasks just queue) | `AirflowPoolSlotsExhausted` | Pool slots exhausted by long-running or zombie tasks | Identify and clear zombie tasks; increase pool slots; optimize task execution time |
| Zombie Task Accumulation | `airflow_zombies_killed` counter rising; runs stuck in RUNNING state | `DagRun is stuck in running state` | `AirflowZombieTasksHigh` | Worker processes dying without updating task state | Check worker node health; increase `scheduler_zombie_task_threshold` cautiously |
| Metadata DB Connection Exhaustion | `airflow_dag_processing_last_run_seconds` high; new runs fail to persist | `Connection refused`, `too many connections` to metadata DB | `AirflowMetadataDBConnectionsHigh` | Too many Airflow components competing for DB connections | Implement PgBouncer connection pooler; increase `sql_alchemy_pool_size` cautiously |
| Sensor Timeout Cascade | Many sensor tasks in `FAILED` state; downstream tasks `UPSTREAM_FAILED` | `AirflowSensorTimeout` across multiple DAGs | `AirflowSensorTimeoutHigh` | External dependency (S3 file, API, DB) not available; sensors all timing out | Check external dependency; switch sensors to `reschedule` mode; add circuit breaker |
| Worker OOM Kill Loop | `airflow_task_failed` spike; pods restarting repeatedly in Kubernetes | `MemoryError` in task logs; pod `OOMKilled` in kubectl describe | `KubernetesOOMKillHigh` | Tasks consuming more memory than worker pod limits | Increase pod memory limits; add chunked processing to tasks; add memory profiling |
| DAG Backlog After Scheduler Restart | Massive spike in `airflow_dag_run_dependency_check` duration; hundreds of runs triggered | Scheduler logs show rapid DagRun creation on startup | `AirflowDagRunCountHigh` | `catchup=True` on a DAG with long history; catchup triggered after scheduler downtime | Set `catchup=False` on non-critical DAGs; use `max_active_runs` to throttle backlog |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `airflow.exceptions.AirflowNotFoundException: DAG not found` | Airflow REST API client | DAG file not parsed yet or has an import error preventing registration | `airflow dags list`; check `airflow_import_errors` metric | Wait for scheduler DAG processor cycle; fix import errors; manually trigger `airflow dags reserialize` |
| `airflow.exceptions.AirflowException: Task exited with return code 1` | Airflow CLI / REST API trigger | Task-level failure in user code | REST API `GET /api/v1/dags/<dag>/dagRuns/<run_id>/taskInstances/<task>/logs/<try>` | Inspect task logs; fix underlying task code; retry with `airflow tasks run` |
| `sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) too many connections` | Airflow scheduler/worker (SQLAlchemy) | Metadata DB connection pool exhausted; too many Airflow components | Check `pg_stat_activity` count vs `max_connections` on Postgres | Add PgBouncer in front of metadata DB; reduce `sql_alchemy_pool_size` per component |
| `celery.exceptions.OperationalError: Error 111 connecting to redis` | Celery workers (Redis broker) | Redis broker for Celery is down or unreachable | `redis-cli -h <host> ping`; check Redis service health | Restore Redis; use Redis Sentinel/Cluster for HA; fall back to database executor for critical DAGs |
| `airflow.exceptions.AirflowSensorTimeout` | Airflow DAG code using `BaseSensorOperator` | External dependency (file, API, DB row) not ready within `timeout` | Check external system availability; look at sensor logs for last check time | Switch sensor to `mode='reschedule'`; increase `timeout`; add upstream monitoring alert |
| HTTP 502 / 503 on Airflow UI / REST API | Browser / API clients | Gunicorn webserver process crashed or scheduler overloaded with API requests | `systemctl status airflow-webserver`; check Gunicorn worker count | Restart webserver; increase `workers` in webserver config; separate API traffic from UI |
| `airflow.exceptions.AirflowTaskTimeout` | Airflow task execution (any operator) | Task exceeded `execution_timeout` set in DAG definition | Compare `execution_timeout` to actual task runtime in logs | Increase `execution_timeout`; optimize task; split into smaller tasks |
| `DagRunAlreadyExists: A DAG Run already exists for DAG ID` | Airflow REST API / external trigger | Trigger attempted for a run_id that already exists | Query `airflow dags list-runs -d <dag>` | Use unique `run_id` per trigger; check if triggering system has deduplication logic |
| `airflow.models.taskinstance.TaskInstanceNotFound` | REST API (`/api/v1/dags/{dag_id}/dagRuns/{run_id}/taskInstances/{task_id}`) | Task instance not yet created (scheduler hasn't scheduled it) or wrong run_id | Poll until task state is `running` or `success`; verify `run_id` format | Add polling retry in API client; use event-driven trigger via REST API with retry |
| `ConnectionError: Failed to connect to Airflow metadata database` during scheduler startup | Airflow scheduler process | Metadata DB unreachable at startup; DB migration not yet complete | `airflow db check`; verify DB connectivity | Wait for DB to become available; run `airflow db migrate` if upgrading version |
| `ImportError: No module named '<package>'` in task logs | Airflow worker Python environment | Python dependency missing in worker environment after deployment | `pip list` on worker; compare to `requirements.txt` | Re-deploy worker with correct image; use `PythonVirtualenvOperator` to isolate dependencies |
| `Task is in the 'running' state` never completes (zombie) | Airflow REST API / monitoring | Worker process died without updating task state in metadata DB | `airflow tasks states-for-dag-run`; check `airflow_zombies_killed` metric | Manually mark task as `failed`; restart worker; investigate worker node OOM |

## Slow Degradation Patterns

Gradual failure modes that don't trigger immediate alerts but lead to incidents:

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Metadata DB table bloat from task instance rows | Query latency on Airflow UI slowly rising; `dag_run` and `task_instance` table sizes growing | `SELECT pg_size_pretty(pg_total_relation_size('task_instance'));` | Weeks to months | Enable and configure `airflow db clean` to purge old runs; set `max_dagruns_per_dag_in_active_runs` |
| Scheduler DAG file parsing time increase | `airflow_dag_processing_last_run_seconds` p95 creeping up; new DAG deployments trigger scheduler lag | `airflow dags report` showing increasing parse times | Days to weeks | Limit DAG file complexity; split monolithic DAG files; use `dags_are_paused_at_creation=True` for inactive DAGs |
| Connection pool creep from long-running DAGs | `sql_alchemy_pool_size` connections all in use at certain hours; API latency rises | `SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE '%airflow%';` | Hours to days | Reduce pool `recycle` time; set `pool_pre_ping=True`; add connection timeout |
| Celery worker queue depth accumulation | `airflow_pool_open_slots` metric slowly declining; task start latency increasing | `celery -A airflow.executors.celery_executor inspect active` | Hours | Scale out Celery workers; increase pool slot count; identify slow tasks consuming slots |
| Log disk exhaustion on worker nodes | `/var/log/airflow/` or log volume usage climbing; no log rotation configured | `du -sh /var/log/airflow/` or `df -h` on worker nodes | Weeks | Configure log rotation; enable remote log storage (S3/GCS); set `log_retention_days` |
| XCom table growth from large return values | XCom queries in UI/API getting slower; `xcom` table row count growing | `SELECT COUNT(*), pg_size_pretty(SUM(pg_column_size(value))) FROM xcom;` | Weeks | Set `xcom_backend` to object store; never push DataFrames via XCom; push file paths instead |
| Stale DAG schedules after timezone config change | DAGs running at wrong times after DST change or TZ config update | Compare `next_dagrun` in `dag` table to expected schedule | Hours (seasonal) | Always use UTC for `schedule_interval`; test schedule math with `airflow dags next-execution` |
| Sensor poke flood blocking worker pool | Many sensors in `poke` mode occupying slots permanently; task start latency rises | `airflow tasks states-for-dag-run` showing many sensors in `running` | Hours to days | Migrate all sensors to `mode='reschedule'`; audit sensor `poke_interval` settings |
| Alert fatigue from flapping DAG SLA misses | SLA miss emails increasing week over week; on-call ignoring them | `SELECT dag_id, COUNT(*) FROM sla_miss GROUP BY dag_id ORDER BY 2 DESC;` | Weeks | Calibrate SLA timers to realistic p95 runtimes; fix genuinely slow DAGs first |

## Diagnostic Automation Scripts

Run these scripts during incidents to gather all relevant info at once:

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Collects: scheduler heartbeat age, import errors, pool slot status, running/failed task counts, DB connectivity
AIRFLOW_HOME="${AIRFLOW_HOME:-/opt/airflow}"

echo "=== Airflow Health Snapshot $(date) ==="

echo "--- Scheduler Heartbeat ---"
airflow jobs check --job-type SchedulerJob --hostname "$(hostname)" 2>&1 || \
  airflow jobs check --job-type SchedulerJob 2>&1

echo "--- Import Errors ---"
airflow dags list-import-errors 2>/dev/null || echo "No import errors command available"

echo "--- Pool Status ---"
airflow pools list 2>/dev/null

echo "--- Running Tasks (last 50) ---"
airflow tasks states-for-dag-run 2>/dev/null || \
  python3 -c "
from airflow import settings
from airflow.models import TaskInstance
from sqlalchemy import func
session = settings.Session()
rows = session.query(TaskInstance.state, func.count()).group_by(TaskInstance.state).all()
[print(f'  {state}: {cnt}') for state, cnt in rows]
session.close()
" 2>/dev/null

echo "--- DB Connectivity ---"
airflow db check 2>&1

echo "--- Active DAG Runs ---"
airflow dags list-runs --state running 2>/dev/null | head -30
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Collects: slowest DAGs by duration, task failure rates, zombie count, metadata DB slow queries
PGHOST="${AIRFLOW_DB_HOST:-localhost}"
PGPORT="${AIRFLOW_DB_PORT:-5432}"
PGDATABASE="${AIRFLOW_DB_NAME:-airflow}"
PGUSER="${AIRFLOW_DB_USER:-airflow}"

echo "=== Airflow Performance Triage $(date) ==="

echo "--- DAG Parse Times (slow parsers) ---"
airflow dags report 2>/dev/null | sort -t '|' -k3 -rn | head -15

echo "--- Top 10 Failing DAGs (last 7 days) ---"
PGPASSWORD="${AIRFLOW_DB_PASS}" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "
  SELECT dag_id, state, COUNT(*) as cnt
  FROM dag_run
  WHERE execution_date > NOW() - INTERVAL '7 days'
  GROUP BY dag_id, state
  ORDER BY cnt DESC
  LIMIT 20;
" 2>/dev/null

echo "--- Zombie Task Count ---"
PGPASSWORD="${AIRFLOW_DB_PASS}" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "
  SELECT COUNT(*) as zombie_candidates
  FROM task_instance
  WHERE state = 'running'
    AND last_heartbeat_at < NOW() - INTERVAL '5 minutes';
" 2>/dev/null

echo "--- Long-Running Tasks (>1 hour) ---"
PGPASSWORD="${AIRFLOW_DB_PASS}" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "
  SELECT dag_id, task_id, state, start_date,
    NOW() - start_date AS duration
  FROM task_instance
  WHERE state = 'running'
    AND start_date < NOW() - INTERVAL '1 hour'
  ORDER BY duration DESC;
" 2>/dev/null
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Collects: DB connection count by app, celery worker status, log disk usage, XCom table size
PGHOST="${AIRFLOW_DB_HOST:-localhost}"
PGPORT="${AIRFLOW_DB_PORT:-5432}"
PGDATABASE="${AIRFLOW_DB_NAME:-airflow}"
PGUSER="${AIRFLOW_DB_USER:-airflow}"

echo "=== Airflow Connection & Resource Audit $(date) ==="

echo "--- DB Connections by Application ---"
PGPASSWORD="${AIRFLOW_DB_PASS}" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "
  SELECT application_name, state, COUNT(*)
  FROM pg_stat_activity
  WHERE datname = '${PGDATABASE}'
  GROUP BY application_name, state
  ORDER BY 3 DESC;
" 2>/dev/null

echo "--- Celery Worker Status ---"
celery -A airflow.executors.celery_executor inspect active 2>/dev/null | head -40

echo "--- Log Disk Usage ---"
LOGDIR="${AIRFLOW_HOME}/logs"
[ -d "$LOGDIR" ] && du -sh "${LOGDIR}" && du -sh "${LOGDIR}"/*/ 2>/dev/null | sort -rh | head -20

echo "--- XCom Table Size ---"
PGPASSWORD="${AIRFLOW_DB_PASS}" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "
  SELECT
    pg_size_pretty(pg_total_relation_size('xcom')) AS xcom_total_size,
    COUNT(*) AS xcom_row_count
  FROM xcom;
" 2>/dev/null

echo "--- Table Sizes (top 10) ---"
PGPASSWORD="${AIRFLOW_DB_PASS}" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -c "
  SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
  FROM pg_catalog.pg_statio_user_tables
  ORDER BY pg_total_relation_size(relid) DESC
  LIMIT 10;
" 2>/dev/null
```

## Noisy Neighbor & Resource Contention Patterns

Multi-tenant and shared-resource contention scenarios:

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Pool slot monopoly by one DAG's sensor tasks | All `default_pool` slots occupied; other DAGs' tasks queued indefinitely | `SELECT dag_id, COUNT(*) FROM task_instance WHERE state='running' GROUP BY dag_id ORDER BY 2 DESC;` | Move sensors to `reschedule` mode; create dedicated pool for sensor-heavy DAGs | Assign all sensor tasks to a separate `sensor_pool`; cap sensors per DAG via `pool_slots` param |
| Memory-heavy task killing worker pod (OOM) | Celery worker pod restarting; other tasks on same worker killed mid-execution | `kubectl describe pod <worker-pod>`; look for `OOMKilled` in events | Isolate memory-heavy tasks to a dedicated Celery queue and worker pool with higher memory limits | Set `executor_config` memory limits per task; use `KubernetesPodOperator` to isolate heavy tasks |
| Long-running DAG holding DB connection open | DB connection count slowly climbs; other scheduler operations time out | `SELECT * FROM pg_stat_activity WHERE state = 'idle in transaction' AND query_start < NOW() - INTERVAL '10 minutes';` | Kill idle-in-transaction connections; set `idle_in_transaction_session_timeout` on Postgres | Set `AUTOCOMMIT` on Airflow DB pool; use `pool_recycle` to reclaim stale connections |
| CPU-intensive tasks monopolizing shared Celery workers | Other quick tasks take minutes instead of seconds to start; worker CPU at 100% | `celery -A airflow.executors.celery_executor inspect active` to see running tasks per worker | Route CPU-heavy tasks to dedicated `heavy_queue`; scale out dedicated workers | Classify tasks by resource profile; use separate Celery queues and worker autoscaling groups |
| DAG file scan spike from large repo commit | Scheduler DAG processing time spikes after every deploy; causes scheduler heartbeat miss | `airflow_dag_processing_last_run_seconds` metric spike; correlate with deploy timestamps | Use `dag_discovery_safe_mode=False`; stage DAG deploys gradually | Validate DAG files in CI before merge; use `airflow dags test` in pre-merge checks |
| Shared NFS log volume I/O contention | Task log writes slow; tasks appear to hang on log flush; NFS errors in worker logs | `iostat -x 1 5` on NFS client; check NFS server load | Switch to remote logging (S3/GCS/ElasticSearch); remount NFS with `noatime,nodiratime` | Eliminate NFS for task logs; use cloud object storage as the primary log backend |
| Runaway task with unbounded output flooding log volume | Single task filling disk; other tasks fail to write logs; worker disk full | `du -sh /opt/airflow/logs/*` to find largest log files | Kill runaway task; truncate or rotate log; increase disk or redirect to object store | Set `execution_timeout` on all tasks; pipe task output to files with size cap; enable log rotation |
| Multiple DAG owners all scheduling at minute zero | Worker queue floods every hour at :00; all other DAG runs delayed for 2-5 minutes | `SELECT execution_date, COUNT(*) FROM dag_run GROUP BY execution_date ORDER BY 2 DESC LIMIT 20;` | Stagger DAG schedules using `timedelta` offsets from hour boundary | Enforce scheduling policy that distributes cron triggers; use `schedule_interval` offsets |
| Shared Celery Redis broker overwhelmed during mass retry | Redis CPU at 100%; Celery worker reconnect storms; tasks re-queue in a loop | `redis-cli info stats | grep -E 'ops_per_sec|connected_clients'` | Pause retrying DAGs; scale Redis; enable retry backoff in Celery config | Set `max_tries` per task; use exponential backoff on retries; limit concurrency of retry-heavy DAGs |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Airflow metadata DB (PostgreSQL) unreachable | Scheduler cannot read DAG states → stops submitting tasks → all workers idle → no DAGs run | All DAGs; every scheduled task stops | Scheduler log: `sqlalchemy.exc.OperationalError: could not connect to server`; `airflow_scheduler_heartbeat` metric flatlines | Restore DB connectivity; scheduler auto-recovers; check `airflow db check` |
| Celery broker (Redis/RabbitMQ) down | Workers lose task queue connection → tasks not dequeued → all Celery workers idle → DAG runs stay in `queued` state forever | All task instances using Celery executor; DAG SLAs violated | Worker log: `kombu.exceptions.OperationalError: [Errno 111] Connection refused`; `airflow_task_instance_created_running` flatlines | Restore broker; workers auto-reconnect and resume consuming queue |
| Scheduler crash loop | DAGs not scheduled → task instances pile up in `scheduled` state → triggered DAGs never run | All DAGs across all owners | `airflow_scheduler_heartbeat` not updated; `SELECT state, COUNT(*) FROM dag_run GROUP BY state;` shows growing `running` count | Restart scheduler pod; check OOM: `kubectl describe pod <scheduler-pod> | grep -A5 OOMKilled` |
| Worker pod OOM kill during task execution | Task killed mid-execution → task marked `failed` → upstream tasks that depended on it not triggered → DAG SLA missed | Tasks on affected worker; dependent downstream tasks | `kubectl describe pod <worker-pod>` shows `OOMKilled`; task log ends abruptly; Celery `inspect active` returns empty | Set explicit memory `resources.limits` in Helm values; route memory-heavy tasks to dedicated queue |
| XCom table bloat causing DB slow queries | XCom reads during task start become slow → task launch latency grows → scheduler backlog builds → heartbeat misses | All tasks that read XComs; scheduler throughput degrades | `SELECT pg_size_pretty(pg_total_relation_size('xcom'));` growing large; slow query log shows XCom SELECT taking >1s | Purge old XComs: `DELETE FROM xcom WHERE dag_id NOT IN (SELECT dag_id FROM dag_run WHERE state='running');`; enforce XCom size limits in DAGs |
| DAG file parse error preventing all DAG discovery | Single broken DAG file causes `DagFileProcessorManager` to exit → all DAGs disappear from UI | All DAGs hidden from scheduler until broken file removed | Scheduler log: `ERROR DagFileProcessorManager - Fatal error in manager process`; `airflow_dag_processing_last_run_seconds` flatlines | Remove or quarantine broken DAG file; restart file processor; validate DAGs with `airflow dags list` |
| Airflow webserver unresponsive due to session DB overload | Webserver workers queue DB session queries → response timeout → 504 from load balancer | All users; operational visibility lost | Webserver log: `gunicorn worker timeout`; `SELECT COUNT(*) FROM session;` very high | Increase webserver workers; clear stale sessions: `DELETE FROM session WHERE expiry < NOW();` |
| Upstream data pipeline delayed — triggering sensor timeouts | ExternalTaskSensors polling for upstream DAG completion timeout → downstream DAG fails → dependent reports/jobs not produced | All DAGs downstream of delayed pipeline | `timeout` errors in sensor task logs; `airflow_task_instance_duration` metric high for sensor tasks | Increase sensor `timeout` or switch to `reschedule` mode; alert on sensor waiting > 2× expected upstream SLA |
| Log volume disk full | Task instances cannot write logs → tasks appear stuck → log server errors → operators cannot diagnose failures | All running tasks on affected worker | `df -h` on log volume at 100%; task log shows `IOError: [Errno 28] No space left on device` | Free disk or expand log volume; redirect to cloud storage: set `[logging] remote_logging = True` |
| Database connection pool exhaustion | New tasks cannot acquire DB connection → task start fails → `queued` tasks never transition to `running` | All task instances waiting to start | `airflow_scheduler_tasks_starving` metric > 0; DB logs: `remaining connection slots are reserved for non-replication superuser connections` | Increase `[database] sql_alchemy_pool_size`; add PgBouncer; kill idle DB connections |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Airflow version upgrade (e.g., 2.6 → 2.7) | DB migration fails or partial: `airflow db upgrade` exits non-zero; scheduler fails with `sqlalchemy.exc.ProgrammingError: column does not exist` | Immediately on `airflow db upgrade` or on first scheduler start | Check migration log; `SELECT * FROM alembic_version;` to see current schema version | Roll back image to previous version; restore DB from pre-upgrade snapshot; re-run `airflow db upgrade` cleanly |
| Celery executor → KubernetesExecutor migration | Tasks stuck in `queued`; no pods spawned; executor config mismatch | Immediate on restart with new config | Check `airflow_executor_running_tasks` metric; `kubectl get pods -n airflow | grep task-runner` returns nothing | Revert `[core] executor` in `airflow.cfg`; restart scheduler and workers |
| Python dependency upgrade in DAG runtime environment | Existing DAGs fail import: `ModuleNotFoundError` or version conflict; DAG disappears from scheduler | On next DAG file parse (within 60 s of deployment) | `airflow dags list-import-errors`; DAG processor log: `ImportError` with package name | Revert dependency version; rebuild Docker image; run `pip check` in CI before deploy |
| `dag_dir_list_interval` or `min_file_process_interval` change | DAG changes take longer to appear or scheduler overwhelmed by too-frequent parsing | Minutes after config change | `airflow_dag_processing_last_run_seconds` metric; `airflow_dag_processing_total_parse_time` | Revert interval values; balance between freshness and CPU overhead |
| Pool size reduction in Airflow pools | Running tasks suddenly `queued` due to reduced slot count; operators investigate backlog | Immediate on pool size change | `airflow pools list` shows reduced size; `airflow tasks states-for-dag-run` shows sudden `queued` wave | Restore pool size via Admin UI or `airflow pools set <pool> <old-size> 'description'` |
| `parallelism` or `max_active_runs_per_dag` reduction | DAG runs serialized; throughput drops; SLA misses | Immediate on scheduler restart with new config | Compare `max_active_runs` in `airflow.cfg` before/after; correlate with SLA miss timestamps | Revert config; restart scheduler |
| Fernet key rotation without re-encryption | Connections and Variables using old Fernet key fail to decrypt: `InvalidToken` in logs; tasks requiring secrets fail | Immediate on scheduler/worker restart with new key | Scheduler/worker log: `cryptography.fernet.InvalidToken`; task launch failures for all connection-dependent tasks | Restore old Fernet key; run `airflow rotate-fernet-key` correctly before deploying new key |
| Connection string update for a database hook | DAGs using that connection fail at runtime with `OperationalError`; previously passing tasks now fail | On next task execution using that connection | Correlate task failure timestamp with connection edit in audit log; `airflow connections get <conn_id>` | Revert connection via CLI: `airflow connections delete <conn_id>` and re-add with correct credentials |
| Kubernetes executor namespace or RBAC change | Task pods fail to start: `Forbidden: pods is forbidden`; Kubernetes executor shows `Error creating pod` | Immediate on RBAC change | `kubectl auth can-i create pods --as=system:serviceaccount:airflow:airflow -n airflow` | Restore RBAC role binding; verify with `kubectl describe rolebinding` |
| Log remote storage (S3/GCS) credentials rotation | Task log writes fail silently; task logs missing from UI; `BotoClientError: Access Denied` in worker logs | Immediately after credential rotation | Worker log: `botocore.exceptions.ClientError: Access Denied on s3://bucket/logs/`; correlate with secret rotation time | Update Airflow connection or env var with new credentials; restart workers |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Scheduler split-brain (two schedulers running simultaneously) | `SELECT * FROM scheduler_job ORDER BY latest_heartbeat DESC LIMIT 5;` — two active rows; `airflow jobs check --job-type SchedulerJob` | DAGs scheduled twice; duplicate task instances; conflicting state transitions | Duplicate task runs; idempotency broken; DB row version conflicts | Kill duplicate scheduler; ensure only one scheduler pod runs: `kubectl scale deployment airflow-scheduler --replicas=1` |
| DAG state stuck in `running` after worker crash | `SELECT dag_id, state, execution_date FROM dag_run WHERE state='running' AND last_scheduling_decision < NOW() - INTERVAL '1 hour';` | DAG appears running but no tasks active; next scheduled run blocked by `max_active_runs` | Subsequent DAG runs blocked; monitoring shows perpetual running run | Mark stuck run failed via REST API `PATCH /api/v1/dags/<dag_id>/dagRuns/<run_id>` with `{"state":"failed"}`, or via DB: `UPDATE dag_run SET state='failed' WHERE run_id='<id>';` |
| Task instance state divergence (DB says running, worker says done) | `airflow tasks states-for-dag-run <dag_id> <execution_date>`; compare with `celery -A airflow inspect active` | Zombie task holding a pool slot; new tasks cannot start | Pool slot leakage; starvation of legitimate tasks | Clear zombie task: `airflow tasks clear -y <dag_id> -t <task_id> -s <execution_date> -e <execution_date>` |
| XCom data written by one task not visible to downstream | `airflow tasks test <dag_id> <downstream_task> <execution_date>` fails on XCom pull; `SELECT * FROM xcom WHERE dag_id='<id>' AND key='<key>';` empty | Downstream tasks fail with `KeyError` or return `None`; upstream task claimed success | Pipeline produces wrong output silently | Verify XCom was pushed: check upstream task log for `xcom_push`; re-run upstream task if XCom row missing |
| Variable or Connection edited mid-run affecting in-flight tasks | `SELECT * FROM variable WHERE key='<key>';` shows new value; tasks started before edit used old value | Some task instances use old config, some use new; inconsistent run results | Non-deterministic pipeline output | Lock Variables during DAG run; use `execution_date`-scoped params instead of shared Variables for critical configs |
| Metadata DB replica lag causing stale reads | `SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))` on replica > 5 s | Scheduler reads stale task states from replica; tasks re-queued unnecessarily | Duplicate task execution attempts; wasted compute | Force scheduler to use primary DB: set `sql_alchemy_conn` to primary endpoint; investigate replica lag root cause |
| Log filesystem inconsistency (NFS stale file handle) | `ls /opt/airflow/logs/<dag_id>/<task_id>/` fails with `Stale file handle`; log reads from UI return 404 | Task logs inaccessible from UI; operators cannot investigate failures | Operational blindness; SLA miss goes undiagnosed | Remount NFS: `umount -l /opt/airflow/logs && mount -a`; switch to remote logging to eliminate NFS dependency |
| Pool usage counter desync after scheduler restart | `airflow pools list` shows slots `used` > actual running tasks | Pool appears full; new tasks blocked despite no actual usage | Task starvation despite available capacity | Reset pool usage: restart scheduler; or directly update DB: `UPDATE slot_pool SET used_slots=0 WHERE pool='<pool>';` |
| Config drift between scheduler and workers (different airflow.cfg) | `airflow config get-value core executor` returns different values on scheduler vs worker pods | Tasks routed to wrong executor queue; Kubernetes tasks may bypass resource limits | Security or resource policy bypass; tasks failing with wrong executor | Enforce config consistency via ConfigMap; all pods must mount same configuration |
| DAG serialization lag (new DAG version not yet in DB) | `SELECT fileloc, last_parsed_dttm FROM dag WHERE dag_id='<id>';` shows old timestamp | Tasks running against old DAG version; operators see wrong task graph | Wrong task dependencies executed if structure changed | Wait for `dag_dir_list_interval` cycle; force re-parse: `airflow dags reserialize` (Airflow 2.4+) |

## Runbook Decision Trees

### Decision Tree 1: DAG Tasks Stuck in Queued State — Not Executing

```
Are tasks stuck in 'queued' state for > 5 minutes?
(check: airflow tasks states-for-dag-run <dag_id> <run_id>  OR  SELECT * FROM task_instance WHERE state='queued' AND queued_dttm < NOW() - INTERVAL '5 min';)
├── YES → Are Celery workers alive and consuming from broker?
│         (check: celery -A airflow.executors.celery_executor inspect ping)
│         ├── NO workers respond → Root cause: All Celery workers down or disconnected from broker
│         │   Check: systemctl status airflow-worker  OR  kubectl get pods -l component=worker
│         │   Fix: Restart worker services; verify Redis/RabbitMQ broker connectivity:
│         │        redis-cli -h <broker_host> ping
│         └── WORKERS respond → Is the task assigned to a full pool?
│                   (check: airflow pools list; airflow pools get <pool_name>)
│                   ├── YES — pool slots 0 available → Root cause: Pool slot exhaustion
│                   │         Fix: Increase pool size: airflow pools set <pool_name> <new_slots> ""
│                   │         Or kill stale running tasks occupying slots
│                   └── NO  → Is the queue empty on the broker?
│                             (check: redis-cli -h <host> LLEN airflow  OR  celery -A airflow.executors.celery_executor inspect reserved)
│                             ├── Queue full but not processing → Root cause: Worker memory/CPU saturated
│                             │   Fix: Scale out workers; check worker resource limits
│                             └── Queue empty, tasks queued in DB → Root cause: Scheduler not pushing tasks to broker
│                                 Fix: Restart scheduler: systemctl restart airflow-scheduler
│                                      Check scheduler logs: journalctl -u airflow-scheduler -n 100
└── NO  → Verify: Are tasks running (state='running')?
          If yes — monitor; tasks are progressing normally
```

### Decision Tree 2: Scheduler Heartbeat Missing — DAGs Not Triggering

```
Is airflow_scheduler_heartbeat metric stale (> 60s since last update)?
(check: airflow jobs check --job-type SchedulerJob --allow-multiple --limit 1  OR  SELECT latest_heartbeat FROM job WHERE job_type='SchedulerJob' ORDER BY latest_heartbeat DESC LIMIT 1;)
├── YES — scheduler heartbeat stale → Is scheduler process running?
│         (check: ps aux | grep 'airflow scheduler' | grep -v grep)
│         ├── NO — process dead → Root cause: Scheduler crashed (OOM, uncaught exception)
│         │   Fix: Check crash log: journalctl -u airflow-scheduler -n 200 --no-pager
│         │         Restart: systemctl restart airflow-scheduler
│         │         Monitor: watch -n5 "airflow jobs check --job-type SchedulerJob"
│         └── YES — process running but heartbeat stale → Is DB reachable?
│                   (check: PGPASSWORD=$AIRFLOW_DB_PASS psql -h $PGHOST -U $PGUSER -d $PGDATABASE -c 'SELECT 1;')
│                   ├── DB unreachable → Root cause: Postgres failure or network partition
│                   │   Fix: Restore Postgres; check connection pool; restart scheduler after DB recovery
│                   └── DB reachable → Root cause: Scheduler thread deadlock or DAG parse error
│                         Fix: Restart scheduler; check dag-processor logs for import errors:
│                              grep -E 'ERROR|Exception|SyntaxError' $(airflow config get-value logging base_log_folder)/../dag_processor_manager/dag_processor_manager.log
└── NO — heartbeat fresh → Are specific DAGs not triggering on schedule?
          (check: SELECT dag_id, next_dagrun, next_dagrun_create_after FROM dag WHERE is_paused=false AND next_dagrun < NOW() - INTERVAL '10 min';)
          ├── Missed runs found → Root cause: DAG paused, catchup disabled, or next_dagrun not calculated
          │   Fix: Unpause DAG: airflow dags unpause <dag_id>; trigger manually: airflow dags trigger <dag_id>
          └── No missed runs → Monitor; system healthy
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| `xcom` table unbounded growth — large XCom values | Tasks pushing DataFrames or large payloads into XCom; table grows GB-scale | `SELECT pg_size_pretty(pg_total_relation_size('xcom')), COUNT(*) FROM xcom;` | Postgres disk full; scheduler and task DB queries slow | Delete old XCom entries: `DELETE FROM xcom WHERE execution_date < NOW() - INTERVAL '7 days';` | Never XCom large data; use S3/GCS intermediary; set `xcom_backend` to custom backend with size limit |
| `log` table / task log files filling disk | Task logging set to local filesystem; high-concurrency tasks write large logs | `du -sh $(airflow config get-value logging base_log_folder)` | Worker disk full; new task log writes fail; tasks marked failed | Delete logs older than 30 days: `find $(airflow config get-value logging base_log_folder) -mtime +30 -delete` | Configure remote logging (S3/GCS); set `log_retention_days`; use log rotation policy |
| `dag_run` / `task_instance` table unbounded growth | No cleanup policy; tables accumulate years of history | `SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_statio_user_tables ORDER BY 2 DESC LIMIT 10;` | Postgres query performance degrades; scheduler slow to compute next run | Enable Airflow DB cleanup: `airflow db clean --clean-before-timestamp <date> --tables task_instance,dag_run` | Configure `airflow db clean` as scheduled DAG; set `clean_tis_without_dagrun=True` |
| Runaway sensor tasks monopolizing pool slots | Poke-mode sensors running continuously; all `default_pool` slots occupied | `SELECT dag_id, task_id, COUNT(*) FROM task_instance WHERE state='running' AND operator='<Sensor>' GROUP BY 1,2 ORDER BY 3 DESC;` | Other DAGs starved; data pipeline SLA breaches | Kill stale sensor tasks: `airflow tasks clear --only-running --yes <dag_id>`; switch sensors to reschedule mode | Set all sensors to `mode='reschedule'`; create dedicated sensor pool; cap `poke_interval` |
| Worker compute runaway — infinite retries on failing task | Task set to `retries=9999` or no retry limit; worker slots consumed by retrying tasks | `SELECT task_id, try_number, max_tries FROM task_instance WHERE state='up_for_retry' ORDER BY try_number DESC LIMIT 20;` | Worker queue blocked; legitimate tasks delayed | Mark task as failed via REST API `PATCH /api/v1/dags/<dag_id>/dagRuns/<run_id>/taskInstances/<task_id>` with `{"new_state":"failed"}`; pause DAG | Enforce `retries <= 3` and `retry_delay >= 300s` in all DAG definitions |
| Celery broker (Redis) memory explosion | Unacknowledged tasks queued in Redis beyond memory limit; tasks re-queued in loop | `redis-cli -h <broker_host> info memory | grep used_memory_human`; `redis-cli llen airflow` | Redis OOM → broker crash; task loss | Drain stalled tasks; restart Redis; set `maxmemory-policy allkeys-lru` | Set Redis `maxmemory` with appropriate eviction policy; monitor `redis_memory_used_bytes` |
| Kubernetes executor — pod count explosion | DAG with `max_active_tasks` not set creates thousands of pods simultaneously | `kubectl get pods -n airflow --field-selector=status.phase=Running | wc -l` | Kubernetes API server overloaded; node scaling triggers; cloud compute cost spike | Pause DAG: `airflow dags pause <dag_id>`; delete excess pods: `kubectl delete pods -n airflow -l dag_id=<dag_id>` | Set `max_active_tasks_per_dag` and `max_active_runs_per_dag` on all DAGs; use task concurrency limits |
| Backfill over large date range consuming all resources | `airflow dags backfill` triggered over years of data; spawns thousands of task instances | `SELECT COUNT(*) FROM dag_run WHERE run_type='backfill' AND state='running';` | Production task queue starved; backfill tasks competing with scheduled runs | Kill backfill: `airflow dags backfill --reset-dagruns --mark-success <dag_id> -s <start> -e <end>`; pause offending DAG | Run backfills with `--delay-on-limit 60`; use separate worker pool for backfills; cap `max_active_tasks` during backfill |
| Long-running DAG holding DB transaction open | DAG with unbounded SQL query or slow operator holding DB connection in `idle in transaction` | `SELECT pid, state, query_start, query FROM pg_stat_activity WHERE state='idle in transaction' AND query_start < NOW() - INTERVAL '5 min';` | DB connection pool exhausted; scheduler and worker DB operations time out | Kill idle DB connection: `SELECT pg_terminate_backend(<pid>);` | Set `idle_in_transaction_session_timeout='5min'` on Airflow Postgres database |
| External API task hammering rate limits during parallel runs | Multiple tasks calling same external API concurrently; rate limit triggered; all tasks fail and retry | Check task failure logs for `429 Too Many Requests`; count concurrent API-calling tasks | External API client blocked; downstream DAGs fail; retry storm worsens the problem | Reduce DAG concurrency by editing the DAG `max_active_tasks` arg or setting `[core] max_active_tasks_per_dag` in airflow.cfg; add backoff in task code | Use Airflow connections with `pool` connection limiting; implement exponential backoff in operators |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot DAG — single DAG with thousands of tasks monopolizing scheduler | Scheduler heartbeat slows; other DAGs not scheduled on time; scheduler CPU maxed | `airflow jobs check --job-type SchedulerJob`; `SELECT dag_id, COUNT(*) FROM task_instance WHERE state='queued' GROUP BY dag_id ORDER BY 2 DESC LIMIT 10;` | Scheduler processes one DAG's task instances in single loop iteration; O(n) per DAG per cycle | Break large DAGs into smaller sub-DAGs or use Dynamic Task Mapping with bounded parallelism; set `max_active_tasks_per_dag` |
| DB connection pool exhaustion | Scheduler or workers throw `OperationalError: too many connections`; tasks fail to update state | `SELECT count(*) FROM pg_stat_activity WHERE datname='airflow';`; compare with `sql_alchemy_pool_size` in airflow.cfg | `sql_alchemy_pool_size` too small for number of scheduler + worker threads | Increase `sql_alchemy_pool_size` and `sql_alchemy_max_overflow`; add pgBouncer in front of Postgres |
| GC / memory pressure in scheduler process | Scheduler restarts periodically; OOM kill in systemd journal; DAGs take longer to process | `journalctl -u airflow-scheduler --no-pager | grep -i 'killed\|memory'`; `ps aux | grep airflow` for RSS | DAG processor manager loading many large DAGs; Python objects not GC'd between parse cycles | Increase `dag_file_processor_timeout`; set `min_file_process_interval` to reduce parse frequency; limit DAG count per scheduler |
| Celery worker thread pool saturation | Tasks queue in Celery broker but workers show full concurrency; no new tasks picked up | `celery -A airflow.executors.celery_executor inspect active`; `celery -A airflow.executors.celery_executor inspect stats | grep pool` | Worker `celery.worker_concurrency` set too low for workload; or tasks consuming all threads with I/O wait | Increase `celery.worker_concurrency`; use prefork pool for CPU-bound or gevent for I/O-bound tasks |
| Slow Postgres query on `task_instance` table | Scheduler loop latency increases over time; task state updates lag | `SELECT query, state, query_start FROM pg_stat_activity WHERE datname='airflow' AND state='active' ORDER BY query_start;`; `EXPLAIN ANALYZE SELECT * FROM task_instance WHERE state='running';` | Missing index on `task_instance.state`; table bloat from no vacuum | Run `VACUUM ANALYZE task_instance;`; add index: `CREATE INDEX CONCURRENTLY ON task_instance(state, dag_id);` |
| CPU steal on shared Celery worker VM | Task execution time increases without code change; workers report higher runtimes | `sar -u 1 30 | grep -v '^$'`; compare task `duration` in `task_instance` table across time periods | Noisy neighbor on shared VM stealing CPU from worker processes | Move workers to dedicated instances; use CPU-pinned instances; consider Kubernetes executor for isolation |
| Lock contention on `dag_run` table during concurrent DAG triggers | DAG trigger API calls queue up; `INSERT INTO dag_run` slow; scheduler and API server compete | `SELECT pid, state, wait_event_type, wait_event, query FROM pg_stat_activity WHERE wait_event_type='Lock';` | Row-level lock contention on `dag_run` table with many concurrent triggers | Serialize DAG triggers via API gateway rate limiting; batch triggers; upgrade to Airflow 2.6+ which reduced lock scope |
| Serialization overhead for large XCom values | Tasks spend significant time serializing/deserializing XCom; scheduler DB size grows | `SELECT dag_id, task_id, pg_size_pretty(length(value)) FROM xcom ORDER BY length(value) DESC LIMIT 10;` | Large objects (DataFrames, file contents) stored in XCom Postgres; each retrieval deserializes full blob | Use XCom backend (S3/GCS) for large values; store only keys/paths in XCom; enforce XCom size limit |
| Oversized DAG file parse causing scheduler stall | DAGs with large inline data or slow imports take > `dag_file_processor_timeout` seconds to parse | `airflow dags list-import-errors`; `time python3 /opt/airflow/dags/<dag_file>.py`; scheduler log: `grep 'DAG file processing stats'` | DAG files importing heavy libraries or performing DB calls at parse time | Move imports inside task functions; avoid DB calls at module level; set `dag_file_processor_timeout=120` |
| Downstream operator latency — slow external system call blocking worker | Task duration increases proportionally with upstream service latency; worker threads blocked | Check `duration` column: `SELECT task_id, AVG(duration) FROM task_instance WHERE state='success' GROUP BY task_id ORDER BY 2 DESC LIMIT 10;` | Worker threads blocked synchronously waiting for external DB/API; no timeout set | Add timeout to all operators: `HttpOperator(timeout=30)`, `PostgresOperator` with `connect_args={'connect_timeout': 10}`; use async operators |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on Airflow webserver HTTPS | Browser shows `NET::ERR_CERT_EXPIRED`; API clients get `SSL: CERTIFICATE_VERIFY_FAILED` | `openssl s_client -connect <airflow-host>:8080 2>/dev/null | openssl x509 -noout -dates`; check webserver startup logs | Web UI inaccessible; API clients cannot trigger DAGs or read status | Renew TLS certificate; update `ssl_cert` and `ssl_key` in `[webserver]` section of airflow.cfg; restart webserver |
| mTLS / Celery broker TLS rotation failure | Celery workers disconnect from broker; tasks stop being consumed; `kombu.exceptions.OperationalError` in worker logs | `celery -A airflow.executors.celery_executor inspect ping`; worker logs: `journalctl -u airflow-worker --no-pager | grep -i 'ssl\|tls\|cert'` | Expired or mismatched TLS cert on Celery broker (Redis/RabbitMQ) | Update broker TLS certificate; update `broker_use_ssl` config in airflow.cfg with new cert paths; restart workers |
| DNS resolution failure for Celery broker or metadata DB | Workers fail to connect to Redis/RabbitMQ; scheduler cannot reach Postgres; `Name or service not known` errors | `dig +short <broker-hostname>`; `dig +short <postgres-hostname>`; `python3 -c "import socket; socket.getaddrinfo('<broker-host>', 6379)"` | DNS record missing or TTL expired; VPC DNS resolver failure | Fix DNS record; use IP address as temporary fallback in `broker_url` and `sql_alchemy_conn`; restart affected services |
| TCP connection exhaustion to Postgres metadata DB | All DB operations fail; tasks cannot update state; `too many connections` error | `SELECT count(*) FROM pg_stat_activity WHERE datname='airflow';`; `ss -tnp 'dport = :5432' | wc -l` | Airflow workers + scheduler opening connections beyond Postgres `max_connections` | Deploy pgBouncer; reduce `sql_alchemy_pool_size`; increase Postgres `max_connections` |
| Celery broker Redis not reachable from worker pod | Workers report `CRITICAL/Worker` down; tasks remain in `queued` state indefinitely | `celery -A airflow.executors.celery_executor inspect ping`; `redis-cli -h <broker_host> ping`; `telnet <broker_host> 6379` | Network policy or firewall blocking port 6379 from worker pods; Redis crashed | Restore network policy allowing port 6379; restart Redis; verify with `redis-cli ping` |
| Packet loss between scheduler and DB causing transaction timeout | Scheduler shows `OperationalError: could not receive data from server` intermittently | `ping -c 100 <postgres-host> | tail -3`; `sar -n DEV 1 10`; check PG logs for unexpected disconnects | Network instability between scheduler host and RDS/Postgres | Use connection retry: `sql_engine_collation_for_ids`; set `keepalives_idle=30` in `sql_alchemy_conn` string |
| MTU mismatch causing large SQL query fragmentation | Large `task_instance` bulk queries fail or time out; smaller queries succeed | `ping -M do -s 1472 <postgres-host>`; Postgres log for `incomplete message` errors | MTU mismatch between Airflow host and DB host (e.g., VPN with lower MTU) | Set MTU on Airflow host NIC: `ip link set eth0 mtu 1500`; or configure `tcp_adjust_mss` in network device |
| Firewall rule change blocking Flower / monitoring port | Celery Flower UI unreachable; monitoring system cannot scrape worker metrics | `curl -f http://<flower-host>:5555/`; `iptables -L -n | grep 5555`; `kubectl get netpol -n airflow` | Operator visibility into task queue lost; alerting on worker health degraded | Restore firewall/network policy rule for port 5555; apply `kubectl apply -f flower-netpol.yaml` |
| SSL handshake timeout with external data source connection | Tasks using `PostgresHook` or `S3Hook` timeout on connection establishment; `SSLError: The handshake operation timed out` | `openssl s_client -connect <remote-db-host>:<port> -timeout 10`; task logs via REST API `GET /api/v1/dags/<dag_id>/dagRuns/<run_id>/taskInstances/<task_id>/logs/<try>` | Tasks fail during execution; retry storm; pipeline SLA missed | Increase connection timeout in hook: `PostgresHook(connect_args={'connect_timeout': 30})`; check remote endpoint health |
| Connection reset during large result set fetch from external DB | `OperationalError: server closed the connection unexpectedly` during SQL operator execution | Task logs for connection reset; `SELECT query, state FROM pg_stat_activity WHERE application_name='airflow'` on remote DB | Task fails mid-execution; data partially written; downstream tasks run on incomplete input | Add `keepalives_idle=60` to connection string; paginate large queries; use `fetch_many` instead of `fetchall` |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of Celery worker process | Worker disappears; running tasks marked `zombie` or abandoned; `dmesg` shows OOM kill | `dmesg | grep -i 'oom\|killed process' | tail -20`; `journalctl -u airflow-worker --no-pager | grep -i 'memory'` | Restart worker: `systemctl restart airflow-worker`; clear zombie tasks: `airflow tasks clear <dag_id>` | Set worker memory limits via systemd `MemoryMax`; use Kubernetes executor with `resources.limits.memory` per task |
| Postgres metadata DB disk full | All Airflow operations fail; `no space left on device` in scheduler logs; DB goes read-only | `df -h <postgres-data-dir>`; `SELECT pg_size_pretty(pg_database_size('airflow'));` | Unbounded `log`, `task_instance`, or `xcom` table growth; no Airflow DB cleanup configured | Free space: `airflow db clean --clean-before-timestamp <date> --tables task_instance,xcom,log`; expand disk | Enable `airflow db clean` in a scheduled DAG; configure Postgres autovacuum; monitor `pg_database_size` |
| Log partition full on worker or scheduler host | Worker cannot write task logs; tasks fail with `IOError`; log directory disk usage 100% | `df -h $(airflow config get-value logging base_log_folder)`; `du -sh $(airflow config get-value logging base_log_folder)` | No log rotation; remote logging not configured; verbose tasks writing gigabytes of output | Delete old logs: `find $(airflow config get-value logging base_log_folder) -mtime +7 -delete`; configure remote log storage (S3/GCS) | Set `remote_logging = True` in airflow.cfg; configure S3/GCS log destination; rotate local logs with logrotate |
| File descriptor exhaustion on scheduler | Scheduler crashes with `OSError: [Errno 24] Too many open files`; DAG file processor hits FD limit | `cat /proc/$(pgrep -f 'airflow scheduler')/limits | grep 'open files'`; `ls /proc/$(pgrep -f 'airflow scheduler')/fd | wc -l` | Large number of DAG files held open simultaneously by DAG processor | Increase FD limit: `ulimit -n 65536`; set `LimitNOFILE=65536` in systemd unit; reduce `parsing_processes` |
| Inode exhaustion on log partition | Writes fail despite available disk space; task log creation fails | `df -i $(airflow config get-value logging base_log_folder)`; `find $(airflow config get-value logging base_log_folder) -maxdepth 4 | wc -l` | Millions of small log files from high-frequency tasks exhausting inode table | Delete old log files; `find <log_folder> -name '*.log' -mtime +3 -delete`; use remote logging to avoid local file creation | Use remote logging (S3/GCS); set `log_retention_days` if using Airflow managed log cleanup |
| CPU throttle on Kubernetes executor worker pods | Tasks run slowly; P99 task duration increases; pod CPU throttle metric nonzero | `kubectl top pods -n airflow`; `kubectl describe pod <task-pod> -n airflow | grep -A5 'Limits'`; `rate(container_cpu_throttled_seconds_total[5m])` | CPU `limits` set too low relative to `requests`; tasks CPU-bound during peak | Increase CPU limit on task pod template: `executor.kubernetes.worker_container_resources.limits.cpu`; or remove CPU limit |
| Swap exhaustion on scheduler host | Scheduler response time degrades severely; GC pauses increase; swapping visible in vmstat | `free -h`; `vmstat 1 10`; `cat /proc/$(pgrep -f 'airflow scheduler')/status | grep VmSwap` | Scheduler Python process swapped out; insufficient RAM for DAG parsing + scheduling | Add RAM; `swapoff -a && swapon -a`; reduce `parsing_processes` to lower memory footprint | Set `vm.swappiness=1`; provision scheduler with adequate RAM (8GB+ for >500 DAGs) |
| Kernel PID limit exhaustion from Celery worker fork-per-task | New task processes cannot spawn; `fork: retry: Resource temporarily unavailable` | `cat /proc/sys/kernel/pid_max`; `ps aux | grep -c celery` | Default `kernel.pid_max=32768` too low if many concurrent Celery workers are forking | `sysctl -w kernel.pid_max=131072`; persist in `/etc/sysctl.d/airflow.conf` | Set `kernel.pid_max=131072` at provisioning; use threading mode (`-P threads`) for I/O-bound workers |
| Redis Celery broker memory exhaustion | Celery tasks stop being queued; Redis returns `OOM command not allowed`; workers idle | `redis-cli -h <broker_host> info memory | grep used_memory_human`; `redis-cli -h <broker_host> info keyspace` | Redis `maxmemory` reached with `noeviction` policy; backlogged tasks consuming memory | Set `maxmemory-policy allkeys-lru`; increase Redis `maxmemory`; clear stale tasks: `celery -A airflow.executors.celery_executor purge` | Set Redis `maxmemory` to 80% of available RAM with `allkeys-lru`; monitor `redis_memory_used_bytes` |
| Ephemeral port exhaustion on scheduler making outbound DB connections | `FATAL: password authentication failed` or `Cannot assign requested address` on DB connect | `ss -s | grep TIME-WAIT`; `sysctl net.ipv4.ip_local_port_range`; `ss -tnp 'dport = :5432' | grep TIME-WAIT | wc -l` | High connection churn; `TIME_WAIT` sockets exhausting port range | `sysctl -w net.ipv4.tcp_tw_reuse=1`; use pgBouncer to pool connections and reduce churn | Deploy pgBouncer; set `net.ipv4.tcp_tw_reuse=1`; configure in `/etc/sysctl.d/airflow.conf` |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation — task re-runs duplicate side effects | Task marked `success` then manually re-triggered; downstream system receives duplicate inserts or API calls | `SELECT run_id, execution_date, state FROM dag_run WHERE dag_id='<dag>' ORDER BY execution_date DESC LIMIT 10;`; check downstream DB for duplicate rows | Duplicate records, double charges, or duplicate notifications in downstream systems | Add idempotency key check in task code (query DB for existing record before insert); use `INSERT ... ON CONFLICT DO NOTHING` |
| Saga/workflow partial failure — upstream task succeeds, downstream task fails | DAG shows mixed `success`/`failed` task states; data written by successful tasks not rolled back | `SELECT dag_id, task_id, state FROM task_instance WHERE run_id='<run>' ORDER BY start_date;`; check downstream DB for orphaned partial data | Inconsistent pipeline state; downstream consumers read partial results | Implement compensating tasks as `on_failure_callback`; use `TriggerDagRunOperator` to trigger cleanup DAG; mark affected rows with `status='rollback'` |
| Message replay causing data corruption — DagRun backfill over already-processed dates | `airflow dags backfill` reprocesses historical dates; idempotent tasks overwrite correct current data with stale values | `SELECT run_type, execution_date, state FROM dag_run WHERE dag_id='<dag>' AND run_type='backfill' ORDER BY execution_date DESC;` | Production data corrupted by stale backfill values; dashboards show historical data for current periods | Immediately pause DAG; identify affected date partitions; restore from upstream snapshot; validate downstream data |
| Cross-service deadlock — two DAGs writing to same DB table in opposite order | Both DAGs stall at DB write step; Postgres shows lock wait; DAGs exceed `execution_timeout` | `SELECT pid, wait_event_type, wait_event, query FROM pg_stat_activity WHERE wait_event_type='Lock';`; check both DAGs' `task_instance.state` | Both DAGs timeout; data in inconsistent state; downstream DAGs waiting on both | Kill blocking DB sessions: `SELECT pg_terminate_backend(<pid>);`; redesign DAGs to write to same table in consistent row order |
| Out-of-order event processing from parallel task instances | Dynamic tasks with `expand()` processing partitions in parallel; ordering-sensitive aggregation downstream produces wrong results | `SELECT task_id, map_index, start_date, end_date FROM task_instance WHERE dag_id='<dag>' AND run_id='<run>' ORDER BY start_date;` | Aggregation or ranking query downstream produces incorrect results due to race on shared state | Enforce ordering by serializing the final aggregation step; use a single `reduce` task after all `map` tasks complete |
| At-least-once delivery duplicate from sensor re-trigger | Sensor marks `success` but callback triggers twice due to race in scheduler state update; downstream DAG triggered twice | `SELECT run_id, state FROM dag_run WHERE dag_id='<triggered_dag>' AND execution_date='<date>';`; check for duplicate `dag_run` rows | Downstream pipeline runs twice; data doubled or idempotency logic must handle duplicate run | Add `TriggerDagRunOperator` with `reset_dag_run=False` to prevent duplicate triggers; add unique constraint on trigger key |
| Compensating transaction failure — cleanup DAG fails silently | DAG with `on_failure_callback` triggers cleanup DAG; cleanup DAG fails without alerting; orphaned resources persist | `SELECT dag_id, state FROM dag_run WHERE dag_id='<cleanup_dag>' ORDER BY execution_date DESC LIMIT 5;`; check for silently failed cleanup runs | Orphaned cloud resources (S3 temp files, DB temp tables, external API state) accumulate; cost and correctness issues | Fix cleanup DAG; manually run cleanup: `airflow dags trigger <cleanup_dag> --conf '{"target_run_id": "<failed_run>"}'`; add alerting on cleanup DAG failures |
| Distributed lock expiry mid-task — two workers process same partition | Redis or DB-based distributed lock expires while task is running; second task starts on same partition | `redis-cli -h <redis_host> keys 'lock:*'`; check `task_instance` for two simultaneous `running` states on same `dag_id+task_id+execution_date` | Duplicate processing; data race; downstream data corrupted | Kill duplicate task via REST API `PATCH .../taskInstances/<task_id>` with `{"new_state":"failed"}`; investigate lock TTL vs. task duration | Set lock TTL > max expected task duration; use `airflow.models.dagrun.DagRun.get_task_instance` locking pattern; monitor concurrent task execution |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — one team's DAG with heavy Pandas operations monopolizing Celery worker CPU | `celery -A airflow.executors.celery_executor inspect active`; `ps aux --sort=-%cpu | head -10` on worker host | Other teams' tasks queue behind CPU-bound tasks; P99 task start latency increases | Move CPU-intensive tasks to dedicated worker queue: `queue='heavy_compute'` in task; route to isolated worker: `celery -A airflow.executors.celery_executor worker -Q heavy_compute` | Assign queues per team/workload type; configure dedicated Celery workers per queue; use KubernetesExecutor for CPU isolation |
| Memory pressure from adjacent tenant's large DataFrame tasks | Worker OOMKilled; other tenants' tasks on same worker fail; `dmesg | grep oom` on worker host | Team's task loading multi-GB DataFrames into memory; no memory limit enforced | Pin memory-heavy DAG to isolated worker pool: add `executor_config={"KubernetesExecutor": {"request_memory": "8Gi", "limit_memory": "8Gi"}}` | Use KubernetesExecutor with per-task resource limits; enforce `MemoryMax` per worker in systemd; set Python memory limits via `resource` module |
| Disk I/O saturation — one team writing large files to shared worker ephemeral storage | `iostat -x 1 10 | grep -E 'Device|sda'` on worker; `du -sh /tmp/ | sort -rh | head -10` | Adjacent tasks slow due to I/O wait; task execution time increases for all workers on node | Clean up disk: `find /tmp -name '*.csv' -mmin +60 -delete`; pause offending DAG: `airflow dags pause <dag_id>` | Enforce task output size limits; configure `tmp_file_cleanup` in DAG; use remote storage (S3) for large outputs; set `ephemeral-storage` limits in KubernetesExecutor |
| Network bandwidth monopoly — one team's DAG downloading large datasets from S3/GCS | `sar -n DEV 1 10 | grep eth0` on worker host; task logs showing `boto3` download throughput | Adjacent tasks experience network I/O starvation; external API calls from other tasks slow | Rate-limit S3 downloads in offending task: use `boto3` transfer config with `max_bandwidth=50 * 1024 * 1024`; move to dedicated worker | Use KubernetesExecutor to schedule bandwidth-intensive tasks on isolated nodes; enforce network QoS via Kubernetes NetworkPolicy bandwidth annotations |
| Connection pool starvation — one team's DAG opening many DB connections via `PostgresHook` | `SELECT count(*) FROM pg_stat_activity WHERE datname='<target-db>';`; `SELECT usename, count(*) FROM pg_stat_activity GROUP BY usename;` | Other teams' DB-dependent tasks fail with `too many connections`; downstream pipelines blocked | Limit connections for offending DAG: add pgBouncer pool per team; reduce `sql_alchemy_pool_size` for that team's Airflow instance | Deploy pgBouncer; enforce per-team connection pool limits; use connection pool manager per DAG |
| Quota enforcement gap — one team's pool consuming all Airflow slots | `airflow pools list`; `SELECT pool, running_slots, queued_slots FROM slot_pool;` | Other teams' tasks queue indefinitely despite available workers | Reduce offending team's pool: `airflow pools set <team-pool> <lower-slots> "reduced due to quota"`; increase other teams' pool slots | Define dedicated Airflow pools per team; enforce slot budgets in CI/CD DAG validation; alert when pool utilization > 80% |
| Cross-tenant data leak risk — shared XCom table exposing sensitive data across DAGs | `SELECT dag_id, task_id, key, length(value) FROM xcom WHERE execution_date > NOW() - INTERVAL '1 day' ORDER BY length(value) DESC LIMIT 20;` | XCom not namespaced per team; any DAG can read another team's XCom values via `xcom_pull(dag_id=<other>)` | Remove sensitive XCom entries: `airflow tasks clear <dag_id>` then delete: `DELETE FROM xcom WHERE dag_id='<sensitive_dag>';` | Enforce XCom naming conventions; use external XCom backends (S3) with per-team bucket policies; audit XCom access patterns |
| Rate limit bypass — one team triggering DAG runs at unlimited rate via API | `SELECT dag_id, COUNT(*), MIN(execution_date), MAX(execution_date) FROM dag_run WHERE execution_date > NOW() - INTERVAL '1h' GROUP BY dag_id ORDER BY 2 DESC LIMIT 10;` | Scheduler backlog grows; metadata DB CPU elevated from insert rate | Pause DAG: `airflow dags pause <dag_id>`; clear pending runs: `airflow dags backfill --reset-dagruns <dag_id>` | Set `max_active_runs` per DAG; apply API rate limiting at reverse proxy layer; enforce concurrency limits via Airflow pools |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| StatsD/Prometheus metric scrape failure | Airflow dashboards dark; `airflow_scheduler_heartbeat` flatlines; DAG duration metrics missing | StatsD exporter sidecar crashed; `statsd_host` misconfigured in airflow.cfg | `airflow jobs check --job-type SchedulerJob`; manual DB check: `SELECT MAX(latest_heartbeat) FROM job WHERE job_type='SchedulerJob';` | Restart StatsD exporter; verify `statsd_host` and `statsd_port` in `[metrics]` section of airflow.cfg; add liveness probe |
| Trace sampling gap missing slow operator incidents | APM shows normal average; slow `HttpOperator` or `S3Hook` incidents invisible in traces | OpenTelemetry instrumentation not installed; `opentelemetry-api` not in Airflow dependencies | Task duration outliers detectable via: `SELECT task_id, MAX(duration) FROM task_instance WHERE state='success' GROUP BY task_id ORDER BY 2 DESC LIMIT 10;` | Install `apache-airflow-providers-opentelemetry`; configure OTEL exporter; increase trace sampling to 10% |
| Log pipeline silent drop — task logs not reaching remote logging backend | Operators complain they cannot see task logs in UI; logs show "Log file does not exist" | S3/GCS remote logging configured but credentials expired; `remote_base_log_folder` misconfigured | Check if local logs exist: `ls $(airflow config get-value logging base_log_folder)/<dag>/<task>/`; test S3 write: `aws s3 cp /tmp/test.txt s3://<log-bucket>/test.txt` | Rotate AWS credentials; verify `remote_base_log_folder` and `remote_logging` settings; restart webserver to reload config |
| Alert rule misconfiguration — `airflow_ti_failures` alert never fires | Tasks failing silently; SLA misses accumulate without page | Alert expression uses wrong label; Prometheus scrape of Airflow StatsD uses different metric name | `curl -G 'http://prometheus:9090/api/v1/query' --data-urlencode 'query=airflow_ti_failures'` to verify metric exists and labels | Check correct metric name via `curl -sf http://statsd-exporter:9102/metrics | grep airflow`; fix alert rule label matchers |
| Cardinality explosion — per-task-instance labels creating millions of time series | Grafana dashboards timeout; Prometheus slow; StatsD exporter scrape takes > 30s | DAG emitting per-run-id or per-execution-date labels in custom metrics | `curl -G 'http://prometheus:9090/api/v1/query' --data-urlencode 'query=count({__name__=~"airflow.*"})'` | Remove high-cardinality labels (`run_id`, `execution_date`) from custom metrics; aggregate at DAG level |
| Missing health endpoint — scheduler liveness not monitored | Scheduler dead (no heartbeat) but no alert fires for minutes | `airflow_scheduler_heartbeat` metric not configured in Prometheus; alert on `latest_heartbeat` not set up | `SELECT latest_heartbeat, state FROM job WHERE job_type='SchedulerJob' ORDER BY latest_heartbeat DESC LIMIT 1;` | Add alert: `time() - airflow_scheduler_last_heartbeat_seconds > 60`; configure Prometheus scrape of Airflow StatsD |
| Instrumentation gap — DagRun SLA miss not monitored | SLA breaches occur silently; business partners not notified | Airflow SLA miss callback not configured; `sla_miss` table not monitored | `SELECT dag_id, task_id, execution_date FROM sla_miss ORDER BY execution_date DESC LIMIT 20;`; set up SLA miss email/callback in DAG definition | Add `sla_miss_callback` to DAG definition; monitor `sla_miss` table with alert; configure email notifications for SLA breaches |
| Alertmanager/PagerDuty outage silencing Airflow critical alerts | Scheduler down; tasks failing; no on-call page | Alertmanager pod OOMKilled; PagerDuty webhook timeout | `curl -sf http://alertmanager:9093/-/healthy`; direct DB check: `airflow jobs check --job-type SchedulerJob` | Implement dead-man's switch: `absent(airflow_scheduler_heartbeat) for 2m` with email fallback receiver independent of PagerDuty |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor version upgrade (e.g., 2.7.x → 2.8.x) | Scheduler fails to start; DB migration errors in log; `airflow db migrate` fails mid-run | `airflow db check`; `alembic history | head -10`; `journalctl -u airflow-scheduler --no-pager | grep -i 'error\|alembic' | head -20` | Restore previous Airflow version from package; restore DB from pre-upgrade backup: `pg_restore -d airflow /backup/airflow-pre-upgrade.dump`; re-run `airflow db migrate` with old version | Always backup Postgres before upgrade: `pg_dump airflow > /backup/airflow-$(date +%Y%m%d).dump`; run DB migration in staging first |
| Major version upgrade (e.g., 1.10.x → 2.x) | DAGs fail to parse; operators renamed or moved; `ImportError` in DAG processor | `airflow dags list-import-errors`; `journalctl -u airflow-scheduler | grep ImportError | head -20` | Roll back to 1.10.x; restore previous virtualenv; point back to pre-migration DB | Run `airflow upgrade check` before major upgrade; test all DAGs in staging; migrate DAGs to new API incrementally |
| Schema migration partial completion — alembic migration interrupted mid-transaction | DB in inconsistent state; `airflow db migrate` fails on next run with constraint error | `airflow db check`; `SELECT version_num FROM alembic_version;`; `SELECT * FROM information_schema.columns WHERE table_name='task_instance' ORDER BY column_name` | Restore from pre-upgrade DB dump; re-run migration from known-good state | Run `airflow db migrate` in transaction; test migration on DB copy before production; never interrupt during migration |
| Rolling upgrade version skew — scheduler on 2.8, workers on 2.7 | Tasks serialized by new scheduler cannot be deserialized by old workers; `TaskDeserializationError` in worker logs | `celery -A airflow.executors.celery_executor inspect active`; worker logs: `journalctl -u airflow-worker | grep Deserialization | head -10` | Downgrade scheduler to 2.7; or upgrade all workers to 2.8 simultaneously | Upgrade all Airflow components in same deployment window; use blue-green deployment for zero-downtime major upgrades |
| Zero-downtime migration gone wrong — DAG moved between Airflow instances with shared DB | Duplicate `dag_run` records from two schedulers managing same DAG; tasks execute twice | `SELECT dag_id, run_id, state, COUNT(*) FROM dag_run GROUP BY 1,2,3 HAVING COUNT(*) > 1;` | Pause DAG on old instance: `airflow dags pause <dag_id>`; clean duplicate runs: `DELETE FROM dag_run WHERE ...`; verify before unpausing on new instance | Pause DAGs on source before activating on destination; use `dag_id` namespace conventions per Airflow instance |
| Config format change — deprecated `airflow.cfg` setting removed in new version | Scheduler startup fails; `AirflowConfigException: Unknown option` in logs | `airflow config get-value <section> <key>`; check deprecation warnings: `journalctl -u airflow-scheduler | grep -i 'deprecated\|removed' | head -20` | Restore previous `airflow.cfg` from backup; downgrade Airflow version | Review migration guide for removed config options before upgrade; run `airflow config` in staging to catch deprecations |
| Data format incompatibility — XCom serialization format changed between versions | Tasks fail to pull XCom values from runs executed on previous Airflow version | `SELECT dag_id, task_id, key FROM xcom WHERE execution_date < '<upgrade-date>' LIMIT 10;`; task logs for `pickle.UnpicklingError` or JSON decode errors | Clear affected XCom: `airflow tasks clear -s <start_date> <dag_id>`; re-run affected task instances | Set `xcom_backend` to JSON-only backend before upgrade; clear XCom table before major version upgrade; test XCom across versions |
| Feature flag rollout causing regression — new `dag_processor_timeout` too aggressive | DAG import errors spike; large DAGs marked as failed parsing; operators lose DAGs from UI | `airflow dags list-import-errors`; `grep 'dag_file_processor_timeout\|Timed out' $(airflow config get-value logging base_log_folder)/../dag_processor_manager/dag_processor_manager.log | tail -20` | Increase timeout in airflow.cfg `[core] dag_file_processor_timeout = 300` (or env var `AIRFLOW__CORE__DAG_FILE_PROCESSOR_TIMEOUT=300`); restart scheduler | Profile DAG parse time in staging: `time python3 <dag_file>.py`; set `dag_file_processor_timeout` based on actual worst-case parse time |
| Dependency version conflict — provider package upgrade breaking operator | Tasks using updated provider fail with `ImportError` or `AttributeError`; other tasks unaffected | `pip show apache-airflow-providers-<name>`; task logs via REST API `GET /api/v1/dags/<dag_id>/dagRuns/<run_id>/taskInstances/<task_id>/logs/<try>`; `grep -i 'ImportError\|AttributeError' <task_log>` | Pin previous provider version: `pip install apache-airflow-providers-<name>==<prev>`; rebuild container image; redeploy workers | Pin all provider versions in `requirements.txt`; test provider upgrades in staging with full DAG test suite |
| Worker resource metrics | Prometheus `node_exporter` or k8s metrics | `kubectl top pods -n airflow --sort-by=cpu` or Prometheus query | 15d default; low risk |

## Kernel/OS & Host-Level Failure Patterns

| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|-------------------|--------------|-------------|----------------------|
| OOM killer terminates Airflow scheduler or worker process | `dmesg -T | grep -i 'oom.*airflow\|killed process'`; `journalctl -k | grep -i oom` | Scheduler loading too many DAGs into memory; worker processing large XCom payloads; insufficient memory limits in cgroup | Scheduler down — no new tasks scheduled; worker down — running tasks orphaned and eventually marked failed by scheduler | Increase memory limit in systemd unit or Kubernetes resource request; reduce `parallelism` and `dag_concurrency`; enable DAG serialization to reduce scheduler memory; restart killed process: `systemctl restart airflow-scheduler` |
| Inode exhaustion on DAG directory or log volume | `df -i /opt/airflow/dags`; `df -i /opt/airflow/logs`; check if inode usage > 95% | Thousands of DAG files with per-task log files never rotated; DAG processor creating `.pyc` cache files | New DAGs cannot be written; log files cannot be created; tasks fail with `OSError: [Errno 28] No space left on device` even though disk has free bytes | Clean old log files: `find /opt/airflow/logs -name '*.log' -mtime +30 -delete`; configure `[logging] base_log_folder` rotation; set `max_log_age_in_days` in `airflow.cfg` |
| CPU steal time spike on Airflow scheduler host | `vmstat 1 5 | awk '{print $16}'`; `cat /proc/stat | grep cpu | head -1`; `top -bn1 | grep 'st'` | Noisy neighbor on shared hypervisor; cloud provider CPU throttling on burstable instance (t3/t3a) | DAG parsing slows dramatically; scheduler heartbeat delayed; tasks queue but never get scheduled | Migrate scheduler to dedicated or compute-optimized instance; switch from burstable (t3) to fixed (m5/c5); if Kubernetes, set CPU requests equal to limits to guarantee CPU |
| NTP clock skew on Airflow scheduler or worker nodes | `chronyc tracking | grep 'System time'`; `timedatectl status | grep 'synchronized'`; `ntpq -p` | NTP daemon stopped or unreachable; VM clock drift after live migration | Task execution_date calculations incorrect; scheduler marks tasks as late or skips intervals; SLA miss callbacks trigger incorrectly; Celery task timeouts fire prematurely | Restart chrony/ntpd: `systemctl restart chronyd`; force sync: `chronyc makestep`; verify: `chronyc tracking`; if persistent, check security group allows NTP (UDP 123) outbound |
| File descriptor exhaustion on scheduler or webserver | `cat /proc/$(pgrep -f 'airflow scheduler')/limits | grep 'open files'`; `ls /proc/$(pgrep -f 'airflow scheduler')/fd | wc -l` | Scheduler opening file handles for each DAG file and database connection; webserver maintaining many client connections; connection pool leak | Scheduler cannot open new DAG files; database connections fail with `Too many open files`; webserver returns 500 errors | Increase fd limit: `ulimit -n 65536` or set in systemd unit `LimitNOFILE=65536`; check for connection pool leaks: `SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE '%airflow%'`; restart affected process |
| TCP conntrack table full on Airflow webserver or Celery broker host | `dmesg | grep 'nf_conntrack: table full'`; `cat /proc/sys/net/netfilter/nf_conntrack_count` vs `cat /proc/sys/net/netfilter/nf_conntrack_max` | High number of short-lived connections from Celery workers to broker (Redis/RabbitMQ); webserver handling many API polling clients | New TCP connections dropped silently; Celery workers cannot reach broker; webserver clients get connection timeouts | Increase conntrack max: `sysctl -w net.netfilter.nf_conntrack_max=524288`; reduce conntrack timeout for established connections: `sysctl -w net.netfilter.nf_conntrack_tcp_timeout_established=600`; persist in `/etc/sysctl.d/` |
| Kernel panic or node crash hosting Airflow scheduler | `last -x shutdown reboot`; `journalctl --list-boots`; cloud provider event log for instance | Kernel bug, hardware failure, or hypervisor maintenance event | Scheduler stops; no new tasks scheduled; running tasks on workers continue but have no scheduler to report to; heartbeat check eventually detects scheduler down | Verify scheduler restarted on new node; check `airflow jobs check --job-type SchedulerJob`; if HA scheduler, verify standby took over; manually trigger missed DAG runs: `airflow dags backfill -s <start> -e <end> <dag_id>` |
| NUMA memory imbalance on multi-socket Airflow worker host | `numactl --hardware`; `numastat -p $(pgrep -f 'airflow worker')`; check if one NUMA node memory > 90% while other has free | Worker process bound to one NUMA node; memory allocation not balanced across sockets | Worker tasks slow due to remote NUMA memory access; intermittent OOM kills on overloaded NUMA node even though total system memory available | Set NUMA interleave for Airflow worker: `numactl --interleave=all airflow celery worker`; or configure systemd unit with `NUMAPolicy=interleave`; distribute workers across NUMA nodes |

## Deployment Pipeline & GitOps Failure Patterns

| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Image pull rate limit — Docker Hub throttling Airflow image pull | `kubectl describe pod <airflow-pod> -n airflow | grep -A5 'Events'`; look for `ErrImagePull` or `ImagePullBackOff` with `429 Too Many Requests` | `kubectl get events -n airflow --field-selector reason=Failed | grep -i 'pull\|rate'` | Switch to cached image in private registry: `kubectl set image deploy/airflow-scheduler airflow=<ecr-registry>/airflow:<tag> -n airflow` | Mirror Airflow images to private ECR/ACR/GCR; configure `imagePullPolicy: IfNotPresent`; use Helm `images.airflow.repository` to point to private registry |
| Image pull auth failure — private registry credentials expired | `kubectl describe pod <airflow-pod> -n airflow | grep 'unauthorized\|401\|auth'` | `kubectl get secret regcred -n airflow -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq '.auths'`; check token expiry | Re-create registry secret: `kubectl create secret docker-registry regcred -n airflow --docker-server=<registry> --docker-username=<user> --docker-password=<pass> --dry-run=client -o yaml | kubectl apply -f -` | Automate registry credential rotation with CronJob or external-secrets operator; set calendar reminder for credential expiry |
| Helm chart drift — Airflow Helm release differs from Git-declared values | `helm get values airflow -n airflow -o yaml > /tmp/live.yaml`; `diff /tmp/live.yaml values-production.yaml` | `helm diff upgrade airflow apache-airflow/airflow -f values-production.yaml -n airflow` | Reconcile: `helm upgrade airflow apache-airflow/airflow -f values-production.yaml -n airflow`; or rollback: `helm rollback airflow <revision> -n airflow` | Enforce GitOps — all Helm upgrades via ArgoCD or Flux; deny manual `helm upgrade` with RBAC; enable `helm diff` in CI pipeline |
| ArgoCD sync stuck on Airflow application | `argocd app get airflow --output json | jq '.status.sync.status'`; look for `OutOfSync` or `Unknown` | `argocd app get airflow --show-operation`; `kubectl logs -n argocd deploy/argocd-repo-server | grep airflow | tail -20` | Force sync: `argocd app sync airflow --force --prune`; if stuck on hook: `argocd app terminate-op airflow` then re-sync | Set sync timeout in ArgoCD Application spec; add `retry` policy with backoff; ensure `argocd-repo-server` has sufficient memory for Helm template rendering |
| PDB blocking Airflow worker rollout | `kubectl get pdb -n airflow`; `kubectl describe pdb airflow-worker -n airflow | grep 'Allowed disruptions: 0'` | `kubectl rollout status deploy/airflow-worker -n airflow`; pods stuck in `Terminating` with PDB preventing eviction | Temporarily adjust PDB: `kubectl patch pdb airflow-worker -n airflow -p '{"spec":{"minAvailable":1}}'`; or delete PDB, complete rollout, re-apply | Set PDB `maxUnavailable: 1` instead of `minAvailable: N-1` for rolling updates; coordinate maintenance windows with PDB settings |
| Blue-green traffic switch failure during Airflow webserver upgrade | Old webserver pods terminated before new pods ready; Airflow UI returns 502 for several minutes | `kubectl get endpoints airflow-webserver -n airflow`; check if endpoint list is empty during switchover | Route traffic back to old deployment: `kubectl patch svc airflow-webserver -n airflow -p '{"spec":{"selector":{"version":"old"}}}'`; keep old pods running until new confirmed healthy | Use `readinessProbe` with Airflow health endpoint `/health`; set `minReadySeconds: 30` on deployment; use Istio traffic shifting for gradual cutover |
| ConfigMap/Secret drift — `airflow.cfg` ConfigMap modified manually, diverges from Git | `kubectl get configmap airflow-config -n airflow -o yaml | diff - <(cat airflow-config.yaml)` | Manual `kubectl edit` bypassed GitOps; Airflow behavior differs from declared config | Restore from Git: `kubectl apply -f airflow-config.yaml -n airflow`; restart scheduler: `kubectl rollout restart deploy/airflow-scheduler -n airflow` | Enforce GitOps for all ConfigMaps; use ArgoCD `selfHeal: true` to auto-revert manual changes; set RBAC to deny `kubectl edit configmap` in production |
| Feature flag stuck — Airflow `[core] executor` changed in config but workers still running old executor type | Scheduler expecting CeleryExecutor; workers running KubernetesExecutor; tasks never execute | `airflow config get-value core executor`; compare across scheduler and worker pods: `kubectl exec deploy/airflow-scheduler -n airflow -- airflow config get-value core executor` | Revert executor config in ConfigMap; restart all Airflow components: `kubectl rollout restart deploy -n airflow -l component=airflow` | Change executor type only during maintenance window; validate executor consistency across all Airflow components in CI; use single source of truth for `airflow.cfg` |

## Service Mesh & API Gateway Edge Cases

| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive — Airflow webserver marked unhealthy by Istio | Airflow UI intermittently unavailable; Istio returns 503 for webserver requests while webserver pods are healthy | Airflow webserver slow to respond during DAG refresh (> 5s); Istio `outlierDetection` ejecting healthy pods after consecutive 5xx from slow responses | Users see intermittent 503 errors on Airflow UI; API calls from CI/CD pipelines fail | Increase Istio outlier detection thresholds: `consecutiveErrors: 10`, `interval: 30s`; tune Airflow `webserver_timeout` in `airflow.cfg`; add dedicated health endpoint excluding DAG parsing |
| Rate limit false positive — Airflow REST API calls throttled by API gateway | CI/CD pipelines triggering DAGs via REST API receive 429; DAG triggers delayed | API gateway rate limit too aggressive for burst DAG trigger patterns; multiple CI pipelines hitting `/api/v1/dags/{dag_id}/dagRuns` simultaneously | Scheduled pipelines miss trigger windows; data freshness SLAs breached | Increase rate limit for `/api/v1/dags/*/dagRuns` endpoint; use API key-based rate limiting to give CI/CD higher quota; batch DAG triggers where possible |
| Stale service discovery — Celery workers cannot reach Redis/RabbitMQ broker after broker pod reschedule | Workers log `ConnectionError: Error connecting to broker`; tasks queue but never execute | Service discovery (DNS or Kubernetes Service) returns old pod IP for broker after pod migration | All task execution stops; scheduler queues tasks indefinitely; DAG runs stuck in `running` state | Restart workers: `kubectl rollout restart deploy/airflow-worker -n airflow`; verify broker endpoint: `kubectl get endpoints <broker-svc> -n airflow`; flush DNS cache in worker pods |
| mTLS rotation break — Airflow webserver-to-metadata-DB TLS cert rotation breaks connections | Webserver returns 500; scheduler logs `SSL: certificate verify failed` connecting to Postgres | Istio or cert-manager rotated mTLS cert; Airflow connection string using pinned cert that is now expired | All Airflow components lose database connectivity; UI down; scheduler and workers stop | Update Airflow DB connection to use new cert: `airflow connections edit postgres_default --conn-extra '{"sslrootcert":"/path/to/new-ca.pem"}'`; or disable cert pinning and rely on Istio mTLS; restart all components |
| Retry storm — Airflow tasks with aggressive retry hitting overwhelmed downstream API | Downstream API returns 500; each failed Airflow task retries 5 times with short delay; total load on downstream = tasks * retries | DAG `default_args` set `retries=5, retry_delay=timedelta(seconds=10)` creating multiplicative load on degraded downstream | Downstream API completely overwhelmed; cascading failure across all DAGs calling same API | Set exponential backoff: `retry_delay=timedelta(minutes=5), retry_exponential_backoff=True, max_retry_delay=timedelta(hours=1)`; add circuit breaker sensor before API-calling tasks; pause offending DAGs: `airflow dags pause <dag_id>` |
| gRPC keepalive/max-message issue — Airflow KubernetesExecutor gRPC to API server exceeds max message size | KubernetesExecutor fails to list pods; tasks stuck in `queued` state; logs show `ResourceExhausted: grpc: received message larger than max` | Large number of pods in namespace; Kubernetes API response exceeds gRPC max message size (default 4MB) | New tasks cannot be scheduled on Kubernetes; executor effectively stalled | Increase gRPC max message size in KubernetesExecutor config; use label selectors to reduce pod list response size; set `kubernetes_executor.delete_worker_pods = True` to reduce pod count |
| Trace context gap — Airflow task spans not linked to parent DAG run trace | Distributed traces show DAG run span but individual task spans are orphaned; cannot trace end-to-end data pipeline | Airflow OpenTelemetry integration not propagating trace context to CeleryExecutor/KubernetesExecutor worker processes | Cannot correlate slow tasks to specific DAG runs in tracing UI; debugging cross-service data pipelines requires manual log correlation | Configure OpenTelemetry propagator in Airflow: set `[traces] otel_task_log_event = True`; ensure `OTEL_PROPAGATORS=tracecontext` environment variable set in worker pods; use `airflow.providers.opentelemetry` provider |
| LB health check misconfiguration — Load balancer marking Airflow webserver as unhealthy | Airflow UI unreachable via load balancer; direct pod access works; LB target shows `unhealthy` | LB health check path set to `/` which returns 302 redirect to login; LB expects 200 | All user traffic to Airflow UI blocked at load balancer level | Change LB health check path to `/health` which returns 200 with JSON body; set expected status code to 200; verify: `curl -sf http://localhost:8080/health` |
