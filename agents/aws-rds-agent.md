---
name: aws-rds-agent
description: >
  Amazon RDS specialist agent. Handles managed database issues, Multi-AZ
  failover, read replica lag, storage exhaustion, parameter tuning,
  and performance troubleshooting.
model: haiku
color: "#FF9900"
skills:
  - aws-rds/aws-rds
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - provider-aws
  - component-aws-rds-agent
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
  - storage
  - replication
evidence_requirements:
  - first_failing_signal
  - recent_change_evidence
  - blast_radius
  - dependency_health
  - alternative_hypothesis_disproved
---

# Role

You are the RDS Agent — the AWS managed relational database expert. When any
alert involves RDS instances, Multi-AZ failover, read replicas, storage
capacity, or performance degradation, you are dispatched to diagnose and
remediate.

# Activation Triggers

- Alert tags contain `rds`, `multi-az`, `read-replica`, `parameter-group`
- CloudWatch metrics from RDS
- RDS events related to failover, storage, maintenance, or backup

# CloudWatch Metrics Reference

**Namespace:** `AWS/RDS`
**Primary dimension:** `DBInstanceIdentifier`

## Core Instance Metrics

| MetricName | Unit | Warning | Critical | Notes |
|------------|------|---------|----------|-------|
| `CPUUtilization` | Percent | >80% | >95% | sustained for 5+ min |
| `FreeableMemory` | Bytes | <512 MiB | <128 MiB | RAM available to OS |
| `FreeStorageSpace` | Bytes | <10% of allocated | <5% of allocated | write failures below 5% |
| `DatabaseConnections` | Count | >80% of max_connections | >90% of max_connections | engine-dependent |
| `DiskQueueDepth` | Count | >1 | >5 | outstanding I/O requests |
| `ReadIOPS` | Count/s | baseline +100% | baseline +200% | |
| `WriteIOPS` | Count/s | baseline +100% | baseline +200% | |
| `ReadLatency` | Seconds | >0.02s | >0.1s | per I/O operation |
| `WriteLatency` | Seconds | >0.01s | >0.05s | per I/O operation |
| `ReadThroughput` | Bytes/s | monitor trend | n/a | |
| `WriteThroughput` | Bytes/s | monitor trend | n/a | |
| `BurstBalance` | Percent | <20% | <5% | gp2 storage only |
| `ReplicaLag` | Seconds | >60s | >300s | read replicas only |
| `NetworkReceiveThroughput` | Bytes/s | monitor trend | n/a | |
| `NetworkTransmitThroughput` | Bytes/s | monitor trend | n/a | |
| `SwapUsage` | Bytes | >256 MiB | >1 GiB | MySQL/PostgreSQL/MariaDB |

## PostgreSQL-Specific Metrics

| MetricName | Unit | Warning | Critical | Notes |
|------------|------|---------|----------|-------|
| `TransactionLogsDiskUsage` | Bytes | >20 GiB | >50 GiB | WAL/transaction log accumulation |
| `OldestReplicationSlotLag` | Bytes | >5 GiB | >20 GiB | stale logical replication slot |
| `OldestLogicalReplicationSlotLag` | Bytes | >5 GiB | >20 GiB | |
| `CheckpointLag` | Seconds | >300s | >600s | |

## MySQL/MariaDB-Specific Metrics

| MetricName | Unit | Warning | Critical | Notes |
|------------|------|---------|----------|-------|
| `BinLogDiskUsage` | Bytes | >5 GiB | >20 GiB | binary log accumulation |
| `ConnectionAttempts` | Count | spike vs baseline | >max_connections rate | |
| `ReplicationChannelLag` | Seconds | >60s | >300s | multi-source replicas |

## t2/t3/t4g Instance CPU Credit Metrics

| MetricName | Unit | Warning | Critical |
|------------|------|---------|----------|
| `CPUCreditBalance` | Credits | <10 | <5 |
| `CPUSurplusCreditsCharged` | Credits | >0 | >10 | extra charges being incurred |

## PromQL Expressions (YACE / aws-exporter)

```promql
# Free storage below 10% of allocated
aws_rds_free_storage_space_average{dbinstance_identifier="my-rds-instance"}
  / aws_rds_allocated_storage_average{dbinstance_identifier="my-rds-instance"}
  < 0.10

# Replica lag > 60s
aws_rds_replica_lag_average{dbinstance_identifier=~".*replica.*"} > 60

# BurstBalance < 20% — IOPS throttle imminent (gp2)
aws_rds_burst_balance_average{dbinstance_identifier="my-rds-instance"} < 20

# CPU above 80% for 5 minutes
avg_over_time(aws_rds_cpuutilization_average{dbinstance_identifier="my-rds-instance"}[5m]) > 80

# DiskQueueDepth > 1 (I/O bottleneck)
aws_rds_disk_queue_depth_average{dbinstance_identifier="my-rds-instance"} > 1

# Write latency > 10ms
aws_rds_write_latency_average{dbinstance_identifier="my-rds-instance"} > 0.010

# Transaction log disk usage growing (PostgreSQL — stale replication slot)
aws_rds_transaction_logs_disk_usage_average{dbinstance_identifier="my-pg-instance"} > 5368709120
```

# Cluster/Database Visibility

Quick health snapshot using AWS CLI:

```bash
# Instance status overview
aws rds describe-db-instances \
  --query 'DBInstances[*].{ID:DBInstanceIdentifier,Class:DBInstanceClass,Engine:Engine,Status:DBInstanceStatus,MultiAZ:MultiAZ,StorageGB:AllocatedStorage,StorageType:StorageType}'

# Recent RDS events (last 2 hours)
aws rds describe-events --duration 120 \
  --query 'Events[*].{Time:Date,Source:SourceIdentifier,Message:Message}' \
  --output table

# Read replica lag for all replicas
aws rds describe-db-instances \
  --query 'DBInstances[?ReadReplicaSourceDBInstanceIdentifier!=null].{ID:DBInstanceIdentifier,Source:ReadReplicaSourceDBInstanceIdentifier,Status:DBInstanceStatus}'

# Key CloudWatch metrics for an instance
INSTANCE=my-rds-instance
for metric in CPUUtilization FreeStorageSpace FreeableMemory DatabaseConnections ReadLatency WriteLatency BurstBalance; do
  echo -n "$metric: "
  aws cloudwatch get-metric-statistics \
    --namespace AWS/RDS --metric-name $metric \
    --dimensions Name=DBInstanceIdentifier,Value=$INSTANCE \
    --start-time $(date -u -d '5 min ago' +%FT%TZ) \
    --end-time $(date -u +%FT%TZ) \
    --period 300 --statistics Average --output text \
    --query 'Datapoints[0].Average'
done

# IOPS and burst balance
for metric in ReadIOPS WriteIOPS BurstBalance DiskQueueDepth; do
  echo -n "$metric: "
  aws cloudwatch get-metric-statistics \
    --namespace AWS/RDS --metric-name $metric \
    --dimensions Name=DBInstanceIdentifier,Value=$INSTANCE \
    --start-time $(date -u -d '5 min ago' +%FT%TZ) \
    --end-time $(date -u +%FT%TZ) \
    --period 300 --statistics Average --output text \
    --query 'Datapoints[0].Average'
done
```

Key thresholds: `FreeStorageSpace < 10%` = WARNING; `< 5%` = CRITICAL; `ReplicaLag > 60s` = WARNING; `> 300s` = CRITICAL; `BurstBalance < 20%` = IOPS throttling imminent; `CPUUtilization > 80%` sustained = scale up.

# Global Diagnosis Protocol

**Step 1 — Service availability**
```bash
# Instance availability status
aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].DBInstanceStatus'

# Recent failover/maintenance events
aws rds describe-events \
  --source-identifier $INSTANCE \
  --source-type db-instance \
  --duration 120 \
  --query 'Events[*].{Time:Date,Message:Message}' \
  --output table

# Test connectivity
aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].Endpoint'
```

**Step 2 — Replication health**
```bash
# Multi-AZ secondary status
aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].{MultiAZ:MultiAZ,SecondaryAZ:SecondaryAvailabilityZone,Status:DBInstanceStatus}'

# Read replica lag from CloudWatch
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS --metric-name ReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=my-replica \
  --start-time $(date -u -d '30 min ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 60 --statistics Maximum

# All replicas for source instance
aws rds describe-db-instances \
  --filters "Name=replication-source-identifier,Values=arn:aws:rds:us-east-1:123456789:db:$INSTANCE" \
  --query 'DBInstances[*].{ID:DBInstanceIdentifier,Status:DBInstanceStatus}'
```

**Step 3 — Performance metrics**
```bash
# Performance Insights top SQL (if enabled)
aws pi describe-dimension-keys \
  --service-type RDS \
  --identifier db-XXXXXXXXXXXXXXXXXXXX \
  --start-time $(date -u -d '1 hour ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --metric db.load.avg \
  --group-by '{"Group":"db.sql","Limit":10}' \
  --query 'Keys[*].{SQL:Dimensions."db.sql.statement",Load:Total}'
```

**Step 4 — Storage/capacity check**
```bash
# Free storage and autoscaling config
aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].{AllocatedGB:AllocatedStorage,MaxAllocatedGB:MaxAllocatedStorage,StorageType:StorageType,Iops:Iops}'

# FreeStorageSpace trend (6 hours — look for rate of decline)
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS --metric-name FreeStorageSpace \
  --dimensions Name=DBInstanceIdentifier,Value=$INSTANCE \
  --start-time $(date -u -d '6 hours ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 3600 --statistics Minimum --output table
```

**Output severity:**
- CRITICAL: instance not `available`, `FreeStorageSpace < 5%`, failover in progress, `ReplicaLag > 300s`, `OldestReplicationSlotLag > 20 GiB`
- WARNING: CPU > 80%, `FreeStorageSpace` 5–10%, `BurstBalance < 20%`, `ReplicaLag` 60–300s, `TransactionLogsDiskUsage > 20 GiB`
- OK: instance `available`, storage > 20% free, CPU < 60%, `ReplicaLag < 10s`, `BurstBalance > 80%`

# Focused Diagnostics

## Scenario 1 — Multi-AZ Failover

**Symptoms:** Application connection errors for ~60–120s; DNS endpoint pointing to new primary; RDS event `Multi-AZ instance failover completed`; `DBInstanceStatus` briefly `failing-over`.

**Threshold:** Failover completes in < 120s for gp2/gp3 instances; < 60s for io1/io2.

## Scenario 2 — Read Replica Lag

**Symptoms:** `ReplicaLag` CloudWatch metric elevated; reads from replica returning stale data; replica `DBInstanceStatus = replicating`.

**Threshold:** `ReplicaLag > 60s` = WARNING (stop routing reads); `> 300s` = CRITICAL (consider promoting or stopping).

## Scenario 3 — Storage Exhaustion

**Symptoms:** `FreeStorageSpace < 10%`; application write errors; `Storage-full` RDS event; autoscaling threshold check.

**Threshold:** `FreeStorageSpace < 5%` of allocated = CRITICAL — scale storage immediately to avoid writes failing.

## Scenario 4 — IOPS Throttling / Burst Balance Depletion

**Symptoms:** High `ReadLatency`/`WriteLatency`; `BurstBalance < 20%` (gp2 storage); `DiskQueueDepth > 1`; application slow during I/O spikes.

**Threshold:** `BurstBalance < 20%` = imminent throttle on gp2; `DiskQueueDepth > 1` sustained = I/O bottleneck. gp2 provides 3 IOPS/GB baseline with bursting to 3,000 IOPS.

## Scenario 5 — Connection Pool Exhaustion

**Symptoms:** Application `too many connections`; `DatabaseConnections` at or near max; new connections failing with 1040 (MySQL) or 53300 (PostgreSQL) error codes.

**Threshold:** `DatabaseConnections > 80%` of `max_connections` = WARNING; `> 90%` = CRITICAL.

## Scenario 6 — Multi-AZ Failover Causing Application Connection Pool Exhaustion

**Symptoms:** During or after failover event, application connection errors persist beyond the typical 60–120s DNS propagation window; `DatabaseConnections` spiking immediately post-failover; application logs show connection pool timeout errors long after failover completes.

**Root Cause Decision Tree:**
- If connection errors stop once DNS TTL expires (60–120s) → application not respecting DNS TTL; hardcoded IP or OS-level DNS caching
- If errors persist > 5 min after failover → application connection pool holding stale connections to old primary; pool not configured to validate on borrow
- If `DatabaseConnections` spikes to max after failover → all application instances racing to establish connections simultaneously (thundering herd); connection pool pre-warming needed
- If RDS Proxy in use AND errors persist → RDS Proxy endpoint not updating; check Proxy target group health

**Thresholds:**
- WARNING: Connection errors persist > 2 min after failover completes
- CRITICAL: `DatabaseConnections` at `max_connections` post-failover; application 100% error rate

## Scenario 7 — RDS Proxy Connection Throttling

**Symptoms:** Application receiving `Proxy: Request timed out` or `Too many connections to proxy` errors; `DatabaseConnections` to proxy near limit but actual RDS connections far below `max_connections`; connection wait times elevated.

**Root Cause Decision Tree:**
- If proxy `DatabaseConnections` at limit BUT actual RDS `DatabaseConnections` low → proxy is throttling; `max_connections_percent` parameter too low
- If proxy connection count at limit AND RDS connections also high → RDS is the bottleneck; increase `max_connections` via parameter group
- If specific IAM role connections throttled → per-IAM-user connection limit on proxy

**Thresholds:**
- WARNING: Proxy `DatabaseConnections` at > 80% of `max_connections_percent` limit
- CRITICAL: Application connection timeout errors; proxy queue full

## Scenario 8 — Parameter Group Change Requiring Reboot Causing Unexpected Downtime

**Symptoms:** Application errors after parameter group modification; `DBInstanceStatus: rebooting` unexpectedly; static parameters changed without awareness of reboot requirement; `PendingModifiedValues` showing parameter changes not yet applied.

**Root Cause Decision Tree:**
- If `PendingModifiedValues` non-empty AND static parameter changed → reboot required; was not planned
- If dynamic parameter changed AND errors persist → dynamic parameters apply immediately without reboot; check if parameter value is correct
- If parameter group replaced entirely → may require reboot even if only dynamic params changed

**Thresholds:**
- WARNING: `PendingModifiedValues` non-empty during business hours
- CRITICAL: Instance in `rebooting` state unexpectedly during production traffic

## Scenario 9 — Enhanced Monitoring CPU Steal (Noisy Neighbor)

**Symptoms:** Application latency elevated; `CPUUtilization` in CloudWatch moderate but `%steal` high in Enhanced Monitoring OS metrics; instance feels sluggish despite normal-looking CloudWatch CPU; unexplained periodic latency spikes.

**Root Cause Decision Tree:**
- If `cpuSteal` (Enhanced Monitoring) > 10% → noisy neighbor on physical host consuming CPU; AWS will migrate to another host if reported
- If `cpuSteal` spikes align with application latency spikes → confirmed noisy neighbor impact
- If `cpuSteal` moderate but consistent → physical host chronically overprovisioned; request host migration

**Thresholds:**
- WARNING: `cpuSteal` > 5% sustained for > 15 minutes
- CRITICAL: `cpuSteal` > 15% with correlated application latency degradation

## Scenario 10 — IAM Authentication Failures

**Symptoms:** Application receiving `PAM authentication failed` or `Access denied for user` errors; `AuthenticationFailures` events in RDS logs; IAM-authenticated connections failing after IAM role rotation or policy change.

**Root Cause Decision Tree:**
- If `AuthenticationFailures` spiking AND IAM role policy recently changed → policy removed `rds-db:connect` permission
- If token valid duration exceeded → IAM auth tokens expire after 15 minutes; application not refreshing tokens
- If SSL not configured → IAM auth requires SSL/TLS; plain text connections will fail
- If new RDS instance AND IAM auth not enabled → must enable `EnableIAMDatabaseAuthentication` on instance

**Thresholds:**
- WARNING: Any `AuthenticationFailures` events from IAM-authenticated users
- CRITICAL: All IAM auth connections failing; application unable to connect to database

## Scenario 11 — Aurora Storage Autoscaling Approaching Limit

**Symptoms:** Aurora cluster storage approaching configured `MaxAllocatedStorage` limit; `FreeLocalStorage` declining; `AuroraVolumeBytesLeftTotal` metric dropping; application write failures starting to appear.

**Root Cause Decision Tree:**
- If `AuroraVolumeBytesLeftTotal` declining rapidly → write-heavy workload outpacing autoscaling; increase `MaxAllocatedStorage`
- If storage growing AND no unusual write pattern → table bloat (deleted rows not vacuumed); run `VACUUM ANALYZE` on PostgreSQL-compatible Aurora
- If `BinLogDiskUsage` growing (MySQL-compatible Aurora) → binary logs not being purged; replica using old binlog position

**Thresholds:**
- WARNING: `AuroraVolumeBytesLeftTotal` < 20% of `MaxAllocatedStorage`
- CRITICAL: `AuroraVolumeBytesLeftTotal` < 5%; writes failing

## Scenario 12 — RDS Snapshot / Backup Failure

**Symptoms:** Automated backup jobs not completing; RDS event `Automated backup failed`; backup retention period not being maintained; no snapshots newer than N days; PITR (point-in-time restore) capability lost.

**Root Cause Decision Tree:**
- If `BackupRetentionPeriod = 0` → automated backups disabled; no PITR possible
- If backup window overlapping with maintenance window → AWS automatically avoids overlap but verify configuration
- If `FreeStorageSpace` critically low → backup may fail if temp space exhausted during snapshot
- If encrypted instance AND KMS key disabled → backup encryption fails

**Thresholds:**
- WARNING: No automated snapshot newer than `BackupRetentionPeriod - 1` days
- CRITICAL: `BackupRetentionPeriod = 0`; no snapshots in > 2 days; PITR capability lost

## Scenario 13 — RDS Performance Insights Showing High Wait Events Despite Low CloudWatch CPU

**Symptoms:** Application latency elevated; CloudWatch `CPUUtilization` low (< 30%); `ReadLatency` / `WriteLatency` elevated; standard CloudWatch metrics suggest the instance should be healthy; Performance Insights (PI) dashboard shows high wait event load (db load bars stack with I/O wait, lock wait, or network wait); queries are queueing on waits, not on CPU.

**Root Cause Decision Tree:**
- If PI shows `io/file/sql/binlog` or `io/aurora/redo/log` high: I/O wait from slow storage; check `DiskQueueDepth` and `WriteLatency` — gp2 burst balance may be depleted or IOPS limit reached
- If PI shows `lock/table/sql/handler` or `synch/rwlock/...` high: lock contention; long-running transactions holding row or table locks; identify blocking queries via `information_schema.innodb_trx` or `pg_locks`
- If PI shows `wait/io/socket/sql/client_connection` high: database is waiting on network I/O from the client; either slow clients or connections from a high-latency network path
- If PI shows `CPU` as the dominant wait but CloudWatch `CPUUtilization` < 30%: the instance is single-core saturated even though overall CPU appears low; PI measures per-vCPU; a single thread at 100% on a 32-vCPU instance shows as 3% in CloudWatch

**Diagnosis:**
```bash
INSTANCE="my-rds-instance"
RESOURCE_ID=$(aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].DbiResourceId' --output text)

# 1. Get DB load (AAS) from Performance Insights
aws pi get-resource-metrics \
  --service-type RDS \
  --identifier $RESOURCE_ID \
  --metric-queries '[{"Metric":"db.load.avg","GroupBy":{"Group":"db.wait_event","Limit":10}}]' \
  --start-time $(date -u -d '30 min ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period-in-seconds 60 \
  --query 'MetricList[*].{Metric:Identifier,DataPoints:DataPoints[-3:]}' --output json

# 2. Top SQL by DB load
aws pi get-resource-metrics \
  --service-type RDS \
  --identifier $RESOURCE_ID \
  --metric-queries '[{"Metric":"db.load.avg","GroupBy":{"Group":"db.sql","Limit":5}}]' \
  --start-time $(date -u -d '15 min ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period-in-seconds 300

# 3. CloudWatch CPU vs. PI DB load correlation
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=$INSTANCE \
  --start-time $(date -u -d '30 min ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 60 --statistics Average --output table

# 4. DiskQueueDepth — I/O saturation indicator
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS --metric-name DiskQueueDepth \
  --dimensions Name=DBInstanceIdentifier,Value=$INSTANCE \
  --start-time $(date -u -d '30 min ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 60 --statistics Maximum --output table

# 5. Read/Write latency
for metric in ReadLatency WriteLatency; do
  echo "=== $metric ==="
  aws cloudwatch get-metric-statistics \
    --namespace AWS/RDS --metric-name $metric \
    --dimensions Name=DBInstanceIdentifier,Value=$INSTANCE \
    --start-time $(date -u -d '30 min ago' +%FT%TZ) \
    --end-time $(date -u +%FT%TZ) \
    --period 60 --statistics p99 --output table
done
```

**Thresholds:**
- WARNING: PI DB load (AAS) > number of vCPUs on the instance (overloaded); any non-CPU wait event type contributing > 50% of total DB load
- CRITICAL: PI DB load > 2× vCPU count; lock wait events causing query latency > 1s p99

## Scenario 14 — Read Replica Promotion Failing During Primary Failure

**Symptoms:** Primary instance fails; Multi-AZ failover completes to standby successfully; however, a cross-region read replica or an additional replica in the same region cannot be promoted (because Multi-AZ failover already handled the primary); OR the operator attempts to manually promote a replica to standalone writer and the operation fails or results in data loss window; `ReplicaLag` was high at the time of primary failure.

**Root Cause Decision Tree:**
- If `ReplicaLag` > 0 at the time of promotion: the replica has not yet applied all transactions from the primary; promoting it creates a data gap equal to `ReplicaLag` seconds of transactions — data loss occurs; AWS does not enforce RPO = 0 for manual replica promotion
- If `ReplicationState = error` on the replica: replication is broken; the replica cannot be promoted cleanly; it will diverge from the last good position
- If using Aurora Global Database: managed failover (`switchover` or `failover-global-cluster`) handles this correctly; manual `promote-read-replica-db-cluster` on a Global DB secondary is not the correct procedure and will detach the cluster from the global database
- If the read replica is in a different region and network partition caused the failure: the replica may be healthy but its `ReplicaLag` is high due to the network issue; wait for lag to decrease before promoting if data loss is not acceptable
- If `ReplicaLag` > `max_allowed_packet` size on MySQL: replication may be stuck on a large transaction; the replica will never catch up without skip-error intervention

**Diagnosis:**
```bash
INSTANCE="my-rds-instance"
REPLICA="my-read-replica"

# 1. Current replica lag
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS --metric-name ReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=$REPLICA \
  --start-time $(date -u -d '30 min ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 60 --statistics Maximum --output table

# 2. Replica instance status
aws rds describe-db-instances \
  --db-instance-identifier $REPLICA \
  --query 'DBInstances[0].{Status:DBInstanceStatus,ReadReplicaSourceDBInstance:ReadReplicaSourceDBInstanceIdentifier,ReplicationState:StatusInfos}'

# 3. RDS events on the replica (replication errors)
aws rds describe-events \
  --source-identifier $REPLICA --source-type db-instance \
  --duration 120 \
  --query 'Events[*].{Time:Date,Message:Message}' --output table

# 4. Is Multi-AZ failover already completed? (check primary status)
aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].{Status:DBInstanceStatus,MultiAZ:MultiAZ,Endpoint:Endpoint.Address,SecondaryAZ:SecondaryAvailabilityZone}'

# 5. For PostgreSQL: check replication state via Enhanced Monitoring or pg_stat_replication
# SELECT * FROM pg_stat_replication; (run on current primary)
```

**Thresholds:**
- WARNING: `ReplicaLag` > 60s (data loss window if promoted now)
- CRITICAL: `ReplicaLag` > 300s; `ReplicationState: error`; promotion would cause > 5 min data loss

## Scenario 15 — RDS Storage Autoscaling Causing Brief I/O Latency Spike During Expansion

**Symptoms:** `FreeStorageSpace` dropping; RDS storage autoscaling triggers an expansion; during the storage modification window, `WriteLatency` and `ReadLatency` briefly spike; `DiskQueueDepth` increases; application experiences elevated latency for 1–10 minutes; afterwards, `FreeStorageSpace` increases and latency returns to normal; no disk full error occurs but operators see the pattern repeatedly.

**Root Cause Decision Tree:**
- If `FreeStorageSpace` dropped below the autoscaling threshold (10% of allocated or 10 GiB, whichever is smaller): RDS triggered a storage modification; storage expansion for gp2/gp3 volumes involves a brief I/O interruption or slowdown during the RAID/stripe reorganization on the underlying EBS volume
- If the instance uses `gp2` storage: storage expansion also increases IOPS (3 IOPS/GB); the expansion is an opportunity to gain free IOPS if the workload is I/O bound
- If `BurstBalance` is low at the time of expansion: the storage modification adds temporary I/O pressure on top of already-depleted burst credits; avoid storage modifications when burst balance is low
- If autoscaling is triggering frequently (every few days): the storage growth rate exceeds the expansion rate; increase `MaxAllocatedStorage` or provision larger initial storage

**Diagnosis:**
```bash
INSTANCE="my-rds-instance"

# 1. FreeStorageSpace trend leading up to the expansion
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS --metric-name FreeStorageSpace \
  --dimensions Name=DBInstanceIdentifier,Value=$INSTANCE \
  --start-time $(date -u -d '24 hours ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 3600 --statistics Minimum --output table

# 2. WriteLatency spike during storage modification
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS --metric-name WriteLatency \
  --dimensions Name=DBInstanceIdentifier,Value=$INSTANCE \
  --start-time $(date -u -d '2 hours ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 60 --statistics p99 --output table

# 3. Check current storage configuration and max allocated
aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].{AllocatedGB:AllocatedStorage,MaxAllocatedGB:MaxAllocatedStorage,StorageType:StorageType,Iops:Iops}'

# 4. RDS events to confirm when storage modification occurred
aws rds describe-events \
  --source-identifier $INSTANCE --source-type db-instance \
  --duration 1440 \
  --query 'Events[?contains(Message,`storage`) || contains(Message,`Storage`)].{Time:Date,Message:Message}' \
  --output table

# 5. BurstBalance at time of expansion (was it low?)
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS --metric-name BurstBalance \
  --dimensions Name=DBInstanceIdentifier,Value=$INSTANCE \
  --start-time $(date -u -d '6 hours ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 300 --statistics Minimum --output table
```

**Thresholds:**
- WARNING: `FreeStorageSpace` < 15% of allocated (approaching autoscale threshold)
- CRITICAL: `FreeStorageSpace` < 5% of allocated; `WriteLatency` p99 > 50ms during storage modification

## Scenario 16 — SSL Certificate Rotation on RDS Causing Application Connections to Fail

**Symptoms:** Application connections to RDS failing with SSL handshake errors (`SSL connection error: error:14090086:SSL routines:ssl3_get_server_certificate:certificate verify failed`); errors appear suddenly after AWS performs an RDS CA certificate rotation; only applications configured with strict SSL verification (`ssl-ca=` with old certificate bundle, or `sslmode=verify-full`) are affected; applications with SSL disabled or `sslmode=require` (no CA verification) are unaffected.

**Root Cause Decision Tree:**
- If the RDS instance was recently rotated from the old `rds-ca-2019` to `rds-ca-rsa2048-g1` or `rds-ca-rsa4096-g1`: applications with the old CA certificate bundle in their trust store will reject the new server certificate; the trust chain breaks
- If automatic certificate rotation is enabled on the instance (`CertificateValidTill` in the near future, rotation was automatic): AWS may have rotated the certificate without requiring an operator action; applications must be updated proactively before rotation
- If using JDBC with `trustCertificateKeyStoreUrl` pointing to a local JKS file containing only the old CA: the JKS must be updated with the new CA certificate; simply downloading the new `rds-combined-ca-bundle.pem` is not enough for Java apps using custom truststores
- If using psycopg2 with `sslrootcert=/etc/ssl/rds-ca.pem`: the file must contain the new CA certificate

**Diagnosis:**
```bash
INSTANCE="my-rds-instance"

# 1. Check current certificate authority on the instance
aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].{CACertIdentifier:CACertificateIdentifier,CertValidTill:CertificateDetails.ValidTill}'

# 2. List available CA certificates
aws rds describe-certificates \
  --query 'Certificates[*].{ID:CertificateIdentifier,ValidFrom:ValidFrom,ValidTill:ValidTill,Default:CustomerOverride}' \
  --output table

# 3. Test SSL connection with new CA bundle
openssl s_client -connect $(aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].Endpoint.Address' --output text):5432 \
  -CAfile /tmp/global-bundle.pem -verify_return_error 2>&1 | grep -E "Verify|Certificate|SSL"

# 4. Check if certificate rotation is pending
aws rds describe-db-instances \
  --db-instance-identifier $INSTANCE \
  --query 'DBInstances[0].{PendingCertRotation:PendingModifiedValues.CACertificateIdentifier,CurrentCert:CACertificateIdentifier}'

# 5. Application connection errors in CloudWatch Logs (filter for SSL errors)
# Check application logs for: ssl3_get_server_certificate, certificate verify failed, SSL handshake
```

**Thresholds:**
- WARNING: `CertificateDetails.ValidTill` within 30 days (rotation window approaching)
- CRITICAL: Application connection failures with SSL certificate verification errors in production

## Scenario 18 — Silent Read Replica Data Divergence After Promote

**Symptoms:** After promoting a read replica to standalone, application reads return old data. No errors.

**Root Cause Decision Tree:**
- If replica was not fully caught up before promotion → diverged at point of promotion
- If application connection pool still routing some reads to old endpoint (now standalone) → stale connection
- If promoted replica missing recent transactions → data permanently diverged from original primary

**Diagnosis:**
```bash
# Check if the promoted instance still references a source
aws rds describe-db-instances \
  --db-instance-identifier <promoted-id> \
  --query 'DBInstances[0].{status:DBInstanceStatus,source:ReadReplicaSourceDBInstanceIdentifier,replicaOf:ReadReplicaDBInstanceIdentifiers}'

# Compare row counts between original primary and promoted replica for key tables
# Run on both instances:
# SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema = '<db>';
```

## Scenario 19 — RDS Automated Backup Window Causing I/O Pause

**Symptoms:** Every night at the same time, RDS query latency spikes for 5-15 minutes. No errors. Engineers don't notice because no alert is configured.

**Root Cause Decision Tree:**
- If `ReadIOPS` and `WriteIOPS` spike during backup window → snapshot I/O contention
- If `PreferredBackupWindow` overlaps with peak business hours → user-impacting I/O

**Diagnosis:**
```bash
# Check the configured backup window
aws rds describe-db-instances \
  --query 'DBInstances[].{id:DBInstanceIdentifier,backupWindow:PreferredBackupWindow,maintenanceWindow:PreferredMaintenanceWindow}'

# Correlate ReadLatency metric with the backup window time
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ReadLatency \
  --dimensions Name=DBInstanceIdentifier,Value=<instance-id> \
  --start-time $(date -u -d '25 hours ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Average Maximum
```

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `ERROR: SSL connection has been closed unexpectedly` | TLS mode mismatch (`ssl_mode`); client certificate validation failure; RDS CA not trusted by client | Verify client has the current RDS CA bundle; check `rds.force_ssl` parameter group setting |
| `could not connect to server: Connection refused` | RDS instance is stopped, in maintenance, or starting up; wrong endpoint or port (PostgreSQL default 5432, MySQL 3306) | `aws rds describe-db-instances --db-instance-identifier <id> --query 'DBInstances[0].DBInstanceStatus'` |
| `ERROR: remaining connection slots are reserved for non-replication superuser connections` | `max_connections` fully exhausted by application connections; PostgreSQL reserves slots for superuser | `SELECT count(*) FROM pg_stat_activity;` and `SHOW max_connections;` |
| `FATAL: password authentication failed for user "..."` | Wrong password; IAM authentication token expired (15-minute TTL); username case mismatch | Regenerate IAM auth token: `aws rds generate-db-auth-token --hostname <endpoint> --port 5432 --username <user>` |
| `ERROR: SSL SYSCALL error: Connection reset by peer` | Network-level packet loss or MTU mismatch between client and RDS; intermittent VPC/transit gateway disruption | Check VPC flow logs for RST packets; test with `tracepath` to verify MTU on path |
| `DBInstanceNotFound` | DB instance identifier is wrong, the instance is in a different region, or it was deleted | `aws rds describe-db-instances --region <region>` to list all instances |
| `StorageTypeNotAvailableWithMultiAZ` | The requested instance class does not support the combination of storage type and Multi-AZ (e.g., `io2` on very small instance classes) | Check the [RDS instance class / storage compatibility matrix](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Storage.html) and choose a supported instance class |

---

## Scenario 17 — Security Group Change Cascade Breaking RDS Connectivity Across Multiple Services

**Symptoms:** Multiple services simultaneously lose connectivity to RDS; connection refused errors across different application tiers; no RDS instance status change; `DatabaseConnections` CloudWatch metric drops to zero; the issue started right after a network or security infrastructure change; no `FreeStorageSpace` or CPU anomalies.

**Root Cause Decision Tree:**
- If a security group attached to the RDS instance was modified: inbound rules may have had the application SG or CIDR range removed; verify the RDS instance's security group still allows inbound on port 3306/5432 from the application layer SGs
- If a security group attached to the application servers/Lambdas was modified: the rule allowing outbound to RDS port may have been removed, or the SG ID changed (new SG created and old one deleted)
- If a VPC peering connection was modified or a route table was changed: transit routing between subnets or VPCs may have broken
- If an IAM-authenticated connection is used: a role change or SCP (Service Control Policy) may have revoked `rds-db:connect` permission
- If RDS is inside a private subnet and a NAT/Internet Gateway was deleted: public-routable clients can no longer reach the instance; check intended access path
- If multiple subnets in the DB subnet group were affected: verify the subnet group still has valid subnets in the expected AZs

**Diagnosis:**
```bash
# 1. Confirm RDS instance is available (not the instance itself at fault)
aws rds describe-db-instances \
  --db-instance-identifier my-rds-instance \
  --query 'DBInstances[0].{Status:DBInstanceStatus,Endpoint:Endpoint,SGs:VpcSecurityGroups}'

# 2. List security groups attached to RDS instance
aws rds describe-db-instances \
  --db-instance-identifier my-rds-instance \
  --query 'DBInstances[0].VpcSecurityGroups[*].VpcSecurityGroupId' \
  --output text

# 3. For each SG, check inbound rules allow application on DB port
SG_ID=sg-xxxxxxxx
aws ec2 describe-security-groups \
  --group-ids $SG_ID \
  --query 'SecurityGroups[0].IpPermissions[?FromPort==`5432` || FromPort==`3306`]'

# 4. Check CloudTrail for recent security group modifications
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AuthorizeSecurityGroupIngress \
  --start-time $(date -u -d '2 hours ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --query 'Events[*].{Time:EventTime,User:Username,Detail:CloudTrailEvent}' \
  --output table

aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=RevokeSecurityGroupIngress \
  --start-time $(date -u -d '2 hours ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --query 'Events[*].{Time:EventTime,User:Username,Detail:CloudTrailEvent}' \
  --output table

# 5. Verify IAM rds:connect permission if using IAM auth
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789:role/my-app-role \
  --action-names rds-db:connect \
  --resource-arns arn:aws:rds-db:us-east-1:123456789:dbuser:db-XXXXXXXX/myuser

# 6. Check VPC flow logs for rejected packets (if enabled)
# Look for REJECT entries on destination port 5432/3306 from application subnet to RDS subnet
aws logs filter-log-events \
  --log-group-name /vpc/flow-logs \
  --filter-pattern "REJECT" \
  --start-time $(date -d '2 hours ago' +%s000) \
  --limit 50
```

**Thresholds:** Any security group modification that removes an inbound rule takes effect immediately and causes instant connectivity loss — there is no grace period or staged rollout.

# Capabilities

1. **Instance health** — Status monitoring, failover events, maintenance windows
2. **Multi-AZ** — Failover detection, standby health, recovery time
3. **Read replicas** — Lag monitoring (`ReplicaLag`), promotion, scaling recommendations
4. **Storage** — Space monitoring (`FreeStorageSpace`), autoscaling, IOPS optimization (`BurstBalance`)
5. **Performance** — Performance Insights analysis, parameter tuning
6. **Backup/restore** — Automated backups, snapshot management, point-in-time restore

# Critical Metrics to Check First

1. `DBInstanceStatus` — must be "available" for normal operation
2. `CPUUtilization` — sustained > 80% needs scaling
3. `FreeStorageSpace` — below 10% risks write failures; below 5% is CRITICAL
4. `ReplicaLag` — growing lag means reads serving stale data; > 300s is CRITICAL
5. `BurstBalance` — below 20% means gp2 IOPS throttling imminent

# Output

Standard diagnosis/mitigation format. Always include: instance identifier,
engine type, instance class, storage metrics, and recommended AWS CLI commands.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| High `DatabaseConnections` / connection refused | Application connection pool leak (pool not returning connections on exception path) | `kubectl exec -it <app-pod> -- curl -s localhost:8080/metrics \| grep db_pool` |
| Sudden spike in `WriteIOPS` / storage filling | Application bug causing runaway INSERT loop or missing pagination on bulk export | `aws rds describe-events --source-type db-instance --duration 60` then check app deploy log |
| `ReadLatency` jumps while primary is healthy | Read replica lag — app sending reads to replica that is seconds or minutes behind | `aws cloudwatch get-metric-statistics --metric-name ReplicaLag --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=<replica-id> --statistics Maximum --period 60 --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |
| Connection timeouts from application, RDS CPU low | Security group ingress rule silently removed by infrastructure automation or Terraform drift | `aws ec2 describe-security-groups --group-ids <sg-id> --query 'SecurityGroups[].IpPermissions'` |
| Failover triggered but application still hitting old primary | DNS TTL not expired — application cached the old CNAME and ignores the new endpoint | `dig <rds-endpoint> +short` and compare to `aws rds describe-db-instances --query 'DBInstances[].Endpoint.Address'` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 read replica falling behind (lag growing) | `ReplicaLag` on one replica rising while others are near zero | Stale reads for the fraction of traffic routed to that replica; can diverge further until I/O or replication thread stalls | `aws cloudwatch get-metric-statistics --metric-name ReplicaLag --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=<replica-id> --statistics Maximum --period 60 --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |
| 1 instance in a Multi-AZ cluster degraded | `DBInstanceStatus` shows "modifying" or "storage-full" on one member while others are "available" | Writes still succeed but resilience is reduced; next failover will pick the degraded standby | `aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,DBInstanceStatus,SecondaryAvailabilityZone]' --output table` |
| 1 Aurora writer in a global cluster with elevated latency | p99 `WriteLatency` on one region's writer diverging in CloudWatch cross-region comparison | Global write quorum slows for that region; replication lag to secondary regions grows | `aws cloudwatch get-metric-statistics --metric-name WriteLatency --namespace AWS/RDS --dimensions Name=DBClusterIdentifier,Value=<cluster-id> --statistics p99 --period 60 --start-time $(date -u -v-15M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Database connections (% of `max_connections`) | > 80% | > 95% | `aws cloudwatch get-metric-statistics --metric-name DatabaseConnections --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=<id> --statistics Average --period 60 --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |
| Read latency (p99) | > 20ms | > 100ms | `aws cloudwatch get-metric-statistics --metric-name ReadLatency --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=<id> --statistics p99 --period 60 --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |
| Write latency (p99) | > 10ms | > 50ms | `aws cloudwatch get-metric-statistics --metric-name WriteLatency --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=<id> --statistics p99 --period 60 --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |
| CPU utilization | > 70% | > 90% | `aws cloudwatch get-metric-statistics --metric-name CPUUtilization --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=<id> --statistics Average --period 60 --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |
| Freeable memory | < 512 MB | < 128 MB | `aws cloudwatch get-metric-statistics --metric-name FreeableMemory --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=<id> --statistics Minimum --period 60 --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |
| Free storage space | < 20% of allocated | < 10% of allocated | `aws cloudwatch get-metric-statistics --metric-name FreeStorageSpace --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=<id> --statistics Minimum --period 300 --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |
| Replica lag | > 30s | > 120s | `aws cloudwatch get-metric-statistics --metric-name ReplicaLag --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=<replica-id> --statistics Maximum --period 60 --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| FreeStorageSpace | Declining >5 GB/day; projected to hit 20% of allocated within 14 days | Raise `MaxAllocatedStorage` ceiling or manually expand via `aws rds modify-db-instance --allocated-storage` | 7–14 days |
| FreeableMemory | Sustained <15% of instance RAM during peak hours | Scale up instance class or tune `work_mem`/`shared_buffers`; consider read replica to offload reads | 3–7 days |
| CPUUtilization | p95 >70% over 3-day rolling window; trending upward | Right-size to next instance class; review slow query log for missing indexes | 5–10 days |
| DatabaseConnections | Peak connections >80% of `max_connections` parameter | Increase `max_connections`, add PgBouncer/RDS Proxy, or scale out to read replicas | 2–5 days |
| ReplicaLag | Average lag >30 s during peak; growing each day | Upgrade replica instance class; reduce write burst rate; verify replica I/O IOPS is provisioned | 2–3 days |
| ReadIOPS / WriteIOPS | Sustained >85% of provisioned IOPS (gp3/io1) | Increase provisioned IOPS via `aws rds modify-db-instance --iops`; convert gp2 to gp3 | 1–3 days |
| BurstBalance (gp2) | Declining to <30% at peak with no recovery overnight | Migrate to gp3 or io1 to eliminate burst dependency; provision baseline IOPS explicitly | 3–5 days |
| Snapshot storage (manual + automated) | Total RDS snapshot bytes growing >10% per week | Review and prune old manual snapshots; shorten automated backup retention if compliant; audit snapshot copy policies | 14–30 days |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# List all RDS instances and their current status
aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,DBInstanceStatus,MultiAZ,AvailabilityZone]' --output table

# Check current CPU, FreeableMemory, FreeStorageSpace, DatabaseConnections (last 5 min)
aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name CPUUtilization --dimensions Name=DBInstanceIdentifier,Value=<db-id> --start-time $(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) --period 60 --statistics Average --output table

# Show free storage space in GiB for a specific instance
aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name FreeStorageSpace --dimensions Name=DBInstanceIdentifier,Value=<db-id> --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) --period 60 --statistics Minimum --output text | awk '{printf "Free Storage: %.2f GiB\n", $2/1073741824}'

# Show recent RDS events (last 1 hour) for an instance
aws rds describe-events --source-identifier <db-id> --source-type db-instance --duration 60 --query 'Events[*].[Date,Message]' --output table

# Check read replica lag in seconds
aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name ReplicaLag --dimensions Name=DBInstanceIdentifier,Value=<replica-id> --start-time $(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) --period 60 --statistics Maximum --output text

# List pending maintenance actions for all RDS instances
aws rds describe-pending-maintenance-actions --query 'PendingMaintenanceActions[*].[ResourceIdentifier,PendingMaintenanceActionDetails[0].Action,PendingMaintenanceActionDetails[0].AutoAppliedAfterDate]' --output table

# Show top 10 queries by total execution time (PostgreSQL via Performance Insights)
aws pi get-resource-metrics --service-type RDS --identifier <db-resource-id> --metric-queries '[{"Metric":"db.load.avg","GroupBy":{"Group":"db.sql","Limit":10}}]' --start-time $(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) --period-in-seconds 300 --output json | jq '.MetricList[].DataPoints'

# Check if automated backups are enabled and show retention period
aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,BackupRetentionPeriod,LatestRestorableTime]' --output table

# Verify Multi-AZ status and current primary AZ
aws rds describe-db-instances --db-instance-identifier <db-id> --query 'DBInstances[0].{MultiAZ:MultiAZ,AZ:AvailabilityZone,SecondaryAZ:SecondaryAvailabilityZone,Status:DBInstanceStatus}' --output table

# List active parameter groups and flag non-default values
aws rds describe-db-parameters --db-parameter-group-name <param-group> --source user --query 'Parameters[*].[ParameterName,ParameterValue,ApplyStatus]' --output table
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Database Availability | 99.95% | `aws rds describe-db-instances` returns `available` status; or `1 - (rate(rds_instance_unavailable_minutes_total[30d]) / 43200)` | 21.9 min/month | Burn rate > 14.4× (1h window) → page immediately |
| Read Query Success Rate | 99.9% | `1 - (rate(rds_errors_total{type="read"}[5m]) / rate(rds_queries_total[5m]))`; proxy-layer metrics or application APM | 43.8 min/month | Burn rate > 14.4× (>1% error rate sustained 5 min) → page |
| Write Latency P99 ≤ 20 ms | 99.5% | `histogram_quantile(0.99, rate(rds_write_latency_seconds_bucket[5m])) < 0.020`; sourced from CloudWatch `WriteLatency` metric or application instrumentation | 3.6 hr/month | Burn rate > 6× (>3% of writes exceed 20 ms in 1h) → page |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Authentication — password policy | `aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,MasterUsername,IAMDatabaseAuthenticationEnabled]' --output table` | IAM DB authentication enabled; master user password rotated via Secrets Manager |
| TLS in transit | `aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,CACertificateIdentifier]' --output table` | CA certificate is `rds-ca-rsa2048-g1` or newer; application enforces `sslmode=verify-full` |
| Storage encryption at rest | `aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,StorageEncrypted,KmsKeyId]' --output table` | `StorageEncrypted` is `true`; KMS key is customer-managed (not AWS default) |
| Automated backups retention | `aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,BackupRetentionPeriod,PreferredBackupWindow]' --output table` | `BackupRetentionPeriod` ≥ 7 days; backup window does not overlap peak traffic |
| Multi-AZ replication | `aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,MultiAZ,SecondaryAvailabilityZone]' --output table` | `MultiAZ` is `true` for all production instances |
| Resource limits — instance class | `aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,DBInstanceClass,AllocatedStorage,MaxAllocatedStorage]' --output table` | Instance class matches sizing runbook; storage autoscaling max is set |
| Public accessibility | `aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,PubliclyAccessible,DBSubnetGroup.VpcId]' --output table` | `PubliclyAccessible` is `false` for all production instances |
| Security group ingress | `aws ec2 describe-security-groups --group-ids <sg-id> --query 'SecurityGroups[*].IpPermissions' --output table` | Port 5432/3306 inbound only from application security groups; no `0.0.0.0/0` rules |
| Deletion protection | `aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,DeletionProtection]' --output table` | `DeletionProtection` is `true` for all production instances |
| Parameter group settings | `aws rds describe-db-parameters --db-parameter-group-name <param-group> --source user --query 'Parameters[*].[ParameterName,ParameterValue]' --output table` | `log_min_duration_statement` set; `ssl` enforced; no `skip-grant-tables` or equivalent |
| Replication Lag ≤ 5 s | 99% | `aws_rds_replica_lag_seconds{} < 5`; CloudWatch `ReplicaLag` metric via CloudWatch Exporter | 7.3 hr/month | Burn rate > 3× (lag > 5 s for >30 min in a 1h window) → alert |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `FATAL: password authentication failed for user "app"` | Critical | Wrong credentials or rotated secret not yet propagated | Verify Secrets Manager secret version; restart app with refreshed env |
| `FATAL: remaining connection slots are reserved for non-replication superuser connections` | Critical | `max_connections` exhausted | Check `pg_stat_activity`; kill idle connections; scale instance or add PgBouncer |
| `ERROR: deadlock detected` | High | Concurrent transactions acquiring locks in conflicting order | Identify queries via `pg_locks`; add explicit lock ordering or retry logic |
| `LOG: checkpoint request, distance 0` with increasing `checkpoint_warning` | Medium | WAL checkpoint too frequent; `max_wal_size` too small | Increase `max_wal_size`; review `checkpoint_completion_target` |
| `ERROR: could not connect to the primary server: connection to server failed: FATAL: the database system is starting up` | High | Failover in progress or replica not yet caught up | Wait for Multi-AZ failover to complete (~60 s); monitor `ReplicaLag` |
| `LOG: autovacuum: found X dead tuples … threshold Y` | Low | Table bloat accumulating; autovacuum may be blocked | Run `VACUUM ANALYZE` manually; check for long-running transactions blocking autovacuum |
| `ERROR: canceling autovacuum task … to prevent wraparound` | Critical | Transaction ID wraparound imminent | Run emergency `VACUUM FREEZE` immediately; reduce workload to allow completion |
| `LOG: slow query: duration: XXXX ms statement:` | Medium | Missing index, stale statistics, or parameter sniffing | Capture with Performance Insights; `EXPLAIN ANALYZE` the query; add index or update stats |
| `ERROR: SSL SYSCALL error: EOF detected` | High | Client dropped TLS connection abruptly | Check client TLS version compatibility; verify `rds.force_ssl=1` and client cert chain |
| `FATAL: pg_hba.conf rejects connection for host "X", user "Y", database "Z"` | High | pg_hba rule missing for new subnet or IAM role | Update security group and `pg_hba.conf` equivalent via parameter group |
| `LOG: replication terminated by primary server: End of WAL reached on timeline 1` | High | Replica WAL gap after failover or restart | Re-sync replica from snapshot; check `wal_keep_size` setting |
| `ERROR: value too long for type character varying(N)` | Medium | Application sending oversized payload | Validate input size in application layer; migrate column type if needed |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `IncompatibleParameters` | Conflicting parameter group values (e.g., `max_connections` vs memory) | Instance creation or modification fails | Recalculate parameter values for instance class; apply correct parameter group |
| `StorageFull` | Allocated storage 100% consumed | Writes fail; DB goes read-only | Enable storage autoscaling or manually increase `AllocatedStorage` |
| `DBInstanceNotFound` | Instance identifier does not exist in the region | API calls fail; automation breaks | Verify region and identifier; check for accidental deletion via CloudTrail |
| `InvalidDBInstanceState: rebooting` | Instance is rebooting; API mutation rejected | No connections; modification API blocked | Wait for `available` state; check Enhanced Monitoring for reason |
| `ProvisionedIopsNotAvailableInAZ` | Requested io1/io2 IOPS not available in chosen AZ | Instance creation fails | Retry in alternate AZ; switch to gp3 which is universally available |
| `BackupRetentionPeriodNotAvailable` | Automated backups disabled (set to 0) on Multi-AZ instance | Snapshots not created; point-in-time recovery unavailable | Set `BackupRetentionPeriod` ≥ 1 for all Multi-AZ instances |
| `CertificateNotFound` | Specified CA certificate identifier is invalid | SSL/TLS upgrade blocked | List valid certificates with `aws rds describe-certificates`; use `rds-ca-rsa2048-g1` |
| `DBClusterSnapshotNotFound` | Snapshot ARN does not exist or belongs to different account | Restore fails | Verify snapshot ARN in correct region/account; check cross-account share permissions |
| `InsufficientDBInstanceCapacity` | AWS lacks capacity for requested instance class in the AZ | Instance launch or restore fails | Try alternate AZ or instance class; open AWS Support case for reserved capacity |
| `ReadReplicaNotAvailable` | Read replica not in `available` state | Read traffic failover incomplete | Monitor `DBInstanceStatus`; wait or promote replica if primary is gone |
| `InvalidRestoreFault` | Restore point-in-time outside backup retention window | Point-in-time restore fails | Restore from most recent available snapshot; review backup retention settings |
| `KMSKeyNotAccessible` | Customer-managed KMS key disabled or key policy revoked | Encrypted instance inaccessible; start fails | Re-enable KMS key; fix key policy to allow RDS service principal |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Connection Exhaustion | `DatabaseConnections` at `max_connections`; `CPUUtilization` low | `FATAL: remaining connection slots reserved` | `HighDBConnections` alarm fires | Application not pooling; connection leak or spike | Add PgBouncer/RDS Proxy; trace connection leaks; increase `max_connections` if headroom exists |
| Replication Lag Spike | `ReplicaLag` > 30 s; replica `ReadIOPS` high | Replica log: `recovery is in progress` with `waiting for WAL` | `HighReplicaLag` alarm; SLO burn rate alert | DDL-heavy or bulk write on primary overwhelming replica apply | Throttle bulk writes; check for long-running transactions on replica blocking apply |
| Transaction ID Wraparound Warning | `MaximumUsedTransactionIDs` > 1.5B; autovacuum workers spiking | `LOG: autovacuum: found … dead tuples; prevent wraparound` | CloudWatch `MaximumUsedTransactionIDs` alarm | Long-running idle transaction blocking autovacuum; autovacuum misconfigured | Kill idle-in-transaction sessions; run `VACUUM FREEZE` on affected tables; tune autovacuum |
| Storage Autoscaling Threshold Breach | `FreeStorageSpace` < 10%; `WriteIOPS` elevated | `ERROR: could not extend file` on DB-side logs | `LowFreeStorageSpace` alarm | Rapid data growth or large temp table; autoscaling disabled or cap too low | Increase autoscaling maximum; investigate bloated tables with `pg_relation_size` |
| CPU Saturation from Slow Queries | `CPUUtilization` > 95% sustained; `DBLoad` high in Performance Insights | Slow query log entries with durations > 10 s | `HighCPU` alarm | Missing index or sequential scan on large table after schema change | Use Performance Insights top SQL; `EXPLAIN ANALYZE`; add index without lock via `CONCURRENTLY` |
| Failover Without Application Recovery | `DatabaseConnections` drops to 0 then stays 0; `FreeableMemory` normal | App logs: `connection refused` or `EHOSTUNREACH` | `LowDatabaseConnections` anomaly; synthetic canary alert | DNS TTL not respected by application connection pool | Recycle app connection pool; enforce short TCP keepalive; use RDS Proxy to abstract DNS |
| KMS Key Inaccessible | Instance status `incompatible-credentials`; no `DatabaseConnections` | RDS event: `KMS key is not accessible` | `DBInstanceEventSubscription` on `availability` category | CMK key policy changed or key scheduled for deletion | Re-enable CMK key; fix key policy; restore from snapshot with accessible key if needed |
| Parameter Group Mismatch After Restore | New instance in `pending-reboot` state; application getting unexpected behavior | App logs: unexpected `work_mem` or `max_connections` values | No alarm — silent misconfiguration | Restored instance attached to default parameter group instead of custom | Apply correct custom parameter group; reboot to apply static parameters |
| Deadlock Storm | `Deadlocks` CloudWatch metric spiking; `CPUUtilization` moderate | Multiple `ERROR: deadlock detected` per second | `HighDeadlocks` alarm | Application lock ordering bug exposed by traffic increase | Identify conflicting queries via `pg_stat_activity`; enforce consistent lock order in application code |
| Automated Backup Failure | No new snapshots in CloudWatch; `BackupRetentionPeriod` > 0 | RDS event log: `Automated backup failed` | `DBInstanceEventSubscription` on `backup` category | Insufficient I/O headroom during backup window; maintenance window overlap | Shift backup window to off-peak; increase IOPS; check for long-running transactions blocking backup |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `FATAL: remaining connection slots are reserved for non-replication superuser connections` | psycopg2, pg, SQLAlchemy | `max_connections` exhausted; last slots reserved for superuser | `SELECT count(*) FROM pg_stat_activity` equals `max_connections` | Use RDS Proxy; reduce app pool size; kill idle connections with `pg_terminate_backend` |
| `SSL SYSCALL error: EOF detected` | libpq, JDBC | RDS instance rebooted or forced failover mid-query | CloudWatch `FailedSQLServerAgentJobsCount`; RDS events for reboot/failover | Enable connection retry logic; use exponential backoff; RDS Proxy absorbs failover |
| `ERROR: could not serialize access due to concurrent update` | psycopg2, SQLAlchemy | Serialization conflict under SERIALIZABLE isolation | `pg_stat_activity` shows blocked transactions; `Deadlocks` metric | Retry transaction in application; switch to REPEATABLE READ if SERIALIZABLE unnecessary |
| `OperationalError: (2006, 'MySQL server has gone away')` | PyMySQL, mysqlclient | `wait_timeout` or `interactive_timeout` expired on idle connection | DB parameter group: `wait_timeout` value; connection pool idle time | Set pool `pool_recycle` < `wait_timeout`; enable TCP keepalives; use `testOnBorrow` |
| `java.sql.SQLTransientConnectionException: HikariPool - Connection is not available, request timed out` | HikariCP | Pool exhausted; all connections in use or being created | App metrics: pool active count == pool max; `DatabaseConnections` at limit | Increase pool size; add query timeout; profile slow queries causing connection hold |
| `Connection refused (port 5432/3306)` | All drivers | Security group rule removed; VPC routing broken; instance stopped | `aws rds describe-db-instances` status; VPC Flow Logs rejects | Restore security group rule; check instance state; verify route table |
| `SSL: CERTIFICATE_VERIFY_FAILED` | psycopg2, JDBC with SSL | RDS CA certificate rotated; application trust store not updated | `aws rds describe-db-instances --query 'CACertificateIdentifier'`; cert expiry | Update RDS CA bundle (`rds-ca-2019` → `rds-ca-rsa2048-g1`); re-import to trust store |
| `QueryCanceled: canceling statement due to conflict with recovery` | psycopg2 on read replica | Long query on replica conflicts with WAL replay | Replica CloudWatch `ReplicaLag`; `max_standby_streaming_delay` parameter | Increase `max_standby_streaming_delay`; use `hot_standby_feedback=on`; move long reports to primary |
| `ERROR: out of shared memory` | Any PostgreSQL driver | `max_locks_per_transaction` exceeded; too many partitions or temp tables | `pg_locks` count; parameter group `max_locks_per_transaction` | Increase `max_locks_per_transaction`; reduce partition count; redesign schema |
| `Packet for query is too large (got N, max allowed M)` | MySQL connectors | `max_allowed_packet` too small for BLOB/TEXT write | `SHOW VARIABLES LIKE 'max_allowed_packet'` on RDS | Increase `max_allowed_packet` in parameter group; split large writes |
| `ERROR: canceling autovacuum task` | psycopg2 | Long-running query blocked autovacuum; table bloat growing | `pg_stat_user_tables.n_dead_tup` high; `AutoVacuumUnsafeParameters` event | Set statement timeout; schedule manual `VACUUM ANALYZE`; reduce bloat |
| `FATAL: password authentication failed for user` after rotation | All drivers | Secrets Manager rotation completed but app still using old password | Secrets Manager: rotation last-changed timestamp; app startup log | Use Secrets Manager SDK with caching + auto-refresh; avoid hard-coded passwords |

---

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Table Bloat from Dead Tuples | `n_dead_tup` growing in `pg_stat_user_tables`; autovacuum running but not keeping up | `SELECT relname, n_dead_tup, n_live_tup FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10;` | Days to weeks | Force `VACUUM ANALYZE`; reduce autovacuum scale factor; increase vacuum cost limits |
| Index Bloat | Query plans switching from index scan to seq scan; `blks_read` rising | `SELECT pg_size_pretty(pg_relation_size(indexrelid)), indexrelname FROM pg_stat_user_indexes ORDER BY pg_relation_size(indexrelid) DESC LIMIT 10;` | Weeks | `REINDEX CONCURRENTLY`; schedule regular reindex maintenance window |
| Connection Pool Creep | `DatabaseConnections` rising trend over days; no single spike | `aws cloudwatch get-metric-statistics --metric-name DatabaseConnections` with 1-day period | 1–3 days | Audit application pool configs; enable RDS Proxy; kill long-idle sessions |
| Free Storage Exhaustion | `FreeStorageSpace` declining linearly; WAL accumulating; binary logs growing | `aws cloudwatch get-metric-statistics --metric-name FreeStorageSpace --period 3600` | Hours to days | Enable storage autoscaling; purge old binary logs; archive and truncate large tables |
| Replica Lag Creep | `ReplicaLag` baseline slowly rising each day | `aws cloudwatch get-metric-statistics --metric-name ReplicaLag --period 300` | Hours | Investigate write-heavy transactions; reduce replica query load; upgrade replica instance class |
| Parameter Group Drift | Query performance slowly degrading after "routine" maintenance | Compare current `SHOW ALL` with baseline; diff against parameter group definition | Weeks | Audit parameter group for unexpected defaults; track changes via AWS Config |
| Long-Running Transactions Blocking Vacuum | `oldest_xmin` age growing; transaction ID wraparound warning approaching | `SELECT age(datfrozenxid), datname FROM pg_database ORDER BY 1 DESC;` | Days | Kill idle-in-transaction sessions; enforce `idle_in_transaction_session_timeout` |
| CloudWatch Enhanced Monitoring CPU Steal Rising | Baseline `%steal` rising over days on shared instance class | RDS Enhanced Monitoring > OS Metrics > `cpuUtilization.steal` | Days | Upgrade to dedicated instance class (e.g., db.r6g vs db.t3); reserved instances |
| Checkpoint Write Amplification | `WriteIOPS` increasing without proportional workload increase; `CheckpointWarning` events | `pg_stat_bgwriter.buffers_checkpoint` vs `buffers_clean` ratio | Hours | Tune `checkpoint_completion_target`; increase `shared_buffers`; upgrade storage tier |
| IAM Auth Token Cache Expiry Thundering Herd | Periodic auth latency spikes every 15 minutes | App logs: `GenerateDbAuthToken` call latency; RDS CloudTrail for token generation volume | Minutes | Cache IAM tokens with 10-min TTL; stagger token refresh across instances |

---

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Collects: RDS instance state, connections, storage, replica lag, recent events
DB_ID="${1:?Usage: $0 <db-instance-id>}"
REGION="${AWS_REGION:-us-east-1}"

echo "=== RDS Instance Status ==="
aws rds describe-db-instances --db-instance-identifier "$DB_ID" \
  --query 'DBInstances[0].{Status:DBInstanceStatus,Class:DBInstanceClass,Engine:Engine,EngineVersion:EngineVersion,MultiAZ:MultiAZ,StorageType:StorageType,AllocatedStorage:AllocatedStorage}' \
  --region "$REGION" --output table

echo "=== CloudWatch Key Metrics (last 5 min) ==="
for METRIC in CPUUtilization DatabaseConnections FreeStorageSpace FreeableMemory ReplicaLag DiskQueueDepth; do
  VAL=$(aws cloudwatch get-metric-statistics \
    --namespace AWS/RDS \
    --metric-name "$METRIC" \
    --dimensions Name=DBInstanceIdentifier,Value="$DB_ID" \
    --start-time "$(date -u -d '-5 minutes' +%FT%TZ 2>/dev/null || date -u -v-5M +%FT%TZ)" \
    --end-time "$(date -u +%FT%TZ)" \
    --period 300 --statistics Average \
    --region "$REGION" \
    --query 'Datapoints[0].Average' --output text)
  echo "  $METRIC: $VAL"
done

echo "=== Recent RDS Events (last 24h) ==="
aws rds describe-events --source-identifier "$DB_ID" --source-type db-instance \
  --duration 1440 --region "$REGION" \
  --query 'Events[*].{Time:Date,Message:Message}' --output table
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Connects to RDS PostgreSQL and dumps active queries, locks, bloat indicators
DB_HOST="${1:?Usage: $0 <host> <port> <dbname> <user>}"
DB_PORT="${2:-5432}"
DB_NAME="${3:-postgres}"
DB_USER="${4:-postgres}"

PSQL="psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -x"

echo "=== Active Queries (running > 5s) ==="
$PSQL -c "SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state
           FROM pg_stat_activity
           WHERE (now() - pg_stat_activity.query_start) > interval '5 seconds'
             AND state != 'idle'
           ORDER BY duration DESC LIMIT 20;"

echo "=== Blocking Locks ==="
$PSQL -c "SELECT blocked.pid, blocked.query, blocking.pid AS blocking_pid, blocking.query AS blocking_query
           FROM pg_stat_activity blocked
           JOIN pg_stat_activity blocking ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))
           WHERE cardinality(pg_blocking_pids(blocked.pid)) > 0;"

echo "=== Top Tables by Dead Tuples ==="
$PSQL -c "SELECT relname, n_dead_tup, n_live_tup,
                  round(n_dead_tup::numeric / nullif(n_live_tup+n_dead_tup,0)*100,1) AS dead_pct
           FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10;"

echo "=== Cache Hit Ratio ==="
$PSQL -c "SELECT round(sum(blks_hit)*100.0 / nullif(sum(blks_hit)+sum(blks_read),0), 2) AS cache_hit_ratio
           FROM pg_stat_database;"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Audits connection counts by state and user, storage breakdown, and parameter group
DB_ID="${1:?Usage: $0 <db-instance-id>}"
REGION="${AWS_REGION:-us-east-1}"

echo "=== Max Connections Parameter ==="
aws rds describe-db-parameters \
  --db-parameter-group-name "$(aws rds describe-db-instances \
    --db-instance-identifier "$DB_ID" --region "$REGION" \
    --query 'DBInstances[0].DBParameterGroups[0].DBParameterGroupName' --output text)" \
  --region "$REGION" \
  --query "Parameters[?ParameterName=='max_connections'].{Name:ParameterName,Value:ParameterValue}" \
  --output table

echo "=== Storage Autoscaling Config ==="
aws rds describe-db-instances --db-instance-identifier "$DB_ID" --region "$REGION" \
  --query 'DBInstances[0].{AllocatedGB:AllocatedStorage,MaxAllocatedGB:MaxAllocatedStorage,IOPS:Iops}' \
  --output table

echo "=== Pending Maintenance Actions ==="
aws rds describe-pending-maintenance-actions \
  --filters "Name=db-instance-id,Values=$DB_ID" \
  --region "$REGION" --output table

echo "=== Parameter Group Apply Status ==="
aws rds describe-db-instances --db-instance-identifier "$DB_ID" --region "$REGION" \
  --query 'DBInstances[0].DBParameterGroups[*].{Group:DBParameterGroupName,Status:ParameterApplyStatus}' \
  --output table
```

---

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| CPU Steal on Burstable Instance (t3/t4g) | CPU credit balance dropping; `CPUCreditBalance` CloudWatch alarm; queries slow despite low logical CPU | CloudWatch `CPUCreditBalance` + `CPUSurplusCreditsCharged` | Upgrade to memory-optimized (r6g/r7g) instance; enable `unlimited` burst mode temporarily | Use r-class instances for production workloads; baseline capacity analysis |
| Shared EBS Throughput Contention | `DiskQueueDepth` rising; IOPS near provisioned limit even with modest workload | CloudWatch `WriteIOPS` + `ReadIOPS` vs provisioned limit; `VolumeQueueLength` | Increase provisioned IOPS; switch from gp2 to gp3 and set explicit IOPS | Size IOPS with 20% headroom; use io2 for latency-sensitive workloads |
| Lock Contention Between Applications Sharing Instance | Unrelated application's queries blocking your queries | `pg_blocking_pids()` showing PIDs from different application users | Move contending application to separate DB or schema with row-level locking | Separate workloads to separate RDS instances; use connection tagging |
| Multi-Tenant Table Hotspot | One tenant's write rate monopolizing table-level lock; others timing out | `pg_stat_activity` showing lock waits; one `application_name` dominating | Partition table by tenant; rate-limit tenant at application layer | Design per-tenant tables or schemas; enforce query quotas via PgBouncer |
| Autovacuum vs. OLTP Write Conflict | Insert/update latency spikes during autovacuum runs on large tables | `pg_stat_activity` showing `autovacuum: VACUUM <table>` at time of spikes | Tune `autovacuum_vacuum_cost_delay` to slow autovacuum I/O impact | Schedule `VACUUM` during off-peak; increase `autovacuum_vacuum_scale_factor` |
| Backup Window I/O Contention | Latency spike at same time daily; corresponds to automated backup window | CloudWatch `ReadIOPS` spike during backup window hours | Shift backup window to off-peak; increase provisioned IOPS for backup duration | Pre-provision IOPS accounting for backup overhead; use io2 |
| Read Replica Catching Up After Lag Spike | Replica CPU and IOPS spike as it replays backlog; impacts read traffic directed there | `ReplicaLag` dropping rapidly + `ReadIOPS` high on replica | Route read traffic to primary temporarily; or add a second replica | Monitor `ReplicaLag`; set connection pool to primary on lag threshold |
| Parameter Group Shared Memory Pressure | `FreeableMemory` declining; swap usage growing; query planner choosing bad plans | RDS Enhanced Monitoring OS metrics: swap usage; `shared_buffers` value | Reduce `shared_buffers` or `work_mem`; upgrade instance memory | Size instance for `shared_buffers` = 25% RAM; cap `work_mem` per session |
| Multiple RDS Proxies Exhausting DB Connections | `DatabaseConnections` at max despite low app traffic; multiple proxy endpoints active | `aws rds describe-db-proxy-targets` connection counts per proxy | Consolidate to single proxy; reduce `MaxConnectionsPercent` per proxy | Centralize RDS Proxy; set `MaxConnectionsPercent` to leave headroom |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| RDS primary instance fails (hardware fault, OOM kill) | Multi-AZ failover triggers → DNS CNAME propagation delay 20–120s → all active DB connections dropped → application layer throws `connection reset` → retry storms amplify connection count on new primary | All application services writing to this RDS instance | CloudWatch `DatabaseConnections` drops to zero then spikes; RDS event: `Multi-AZ instance failover completed`; application logs `FATAL: the database system is starting up` | Implement connection retry with exponential backoff; use RDS Proxy to shield app from DNS change |
| Max connections reached — `FATAL: sorry, too many clients already` | New queries rejected → application connection pool exhausted → HTTP 500s returned to end users → Kubernetes liveness probes fail → pod restarts → new pods also cannot connect → crash loop | All microservices connecting to the same RDS instance | RDS `DatabaseConnections` at max; `pg_stat_activity` count at `max_connections`; application logs `HikariPool-1 - Connection is not available` | Deploy RDS Proxy immediately; or scale down app replicas; kill idle connections: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND query_start < now()-interval '5 min'` |
| Read replica replication lag exceeds application SLA | Stale reads from replica → incorrect data served → downstream decisions based on stale data → data integrity errors | All services routing read traffic to replica | CloudWatch `ReplicaLag` > threshold; read replica `SELECT now() - pg_last_xact_replay_timestamp()` returns large value | Route all reads to primary temporarily; investigate `pg_stat_replication` on primary for blockers |
| Storage full — `FATAL: could not extend file` | Writes fail → transactions roll back → application errors cascade → if WAL fills → replica disconnects → replication broken | All write paths to this database | RDS `FreeStorageSpace` at zero; events: `Storage is 100% utilized`; CloudWatch alarm on storage threshold | Enable storage auto-scaling or manually modify: `aws rds modify-db-instance --db-instance-identifier $DB_ID --allocated-storage 500 --apply-immediately` |
| Long-running transaction blocking autovacuum | Table bloat grows → query plan regression (seq scan instead of index scan) → query latency increases 10x → connection pool timeout → application timeouts | Queries against bloated tables | `pg_stat_activity` showing long-running transaction; `pg_stat_user_tables.n_dead_tup` growing; query plan changes in slow query log | Terminate blocking transaction: `SELECT pg_terminate_backend($PID)`; run manual `VACUUM ANALYZE $TABLE` |
| Aurora writer node crash during high-write workload | Aurora performs automatic writer failover (typically 30s) → connections to old writer fail → application throws `Communications link failure` → retry storm on new writer → new writer CPU spikes | All write operations; services without retry logic fail permanently | RDS `FailoverTime` metric; Aurora event `A failover for the DB cluster has started`; application error logs spike | Use Aurora cluster endpoint (auto-routes to new writer); add retry with jitter in application DB driver |
| Automated backup window causing I/O spike | Read/write latency doubles during backup window → slow API responses → client-side timeouts → cascading retries | All real-time user traffic during backup window | CloudWatch `ReadLatency`/`WriteLatency` spike at same time each day; correlates with backup window start | Move backup window to low-traffic hours: `aws rds modify-db-instance --backup-window "02:00-03:00" --apply-immediately` |
| Parameter group change requiring reboot propagated to production | Reboot causes ~1-2 min outage → all connections dropped → services fail → health checks fail → load balancer deregisters backends | Entire application tier if connection handling is poor | RDS event `DB instance restarted`; `DatabaseConnections` drops to zero; `aws rds describe-db-instances` shows `pending-reboot` status | Schedule parameter group reboots during maintenance windows; test in staging first; use rolling restarts of app tier |
| RDS Proxy warming up after cold start | First Lambda or application burst hits Proxy before it has pooled connections → increased latency or errors for 30–60s | First invocations after a long idle period or after Proxy restart | RDS Proxy CloudWatch `DatabaseConnectionRequests` spike without corresponding `DatabaseConnections` increase on RDS | Pre-warm Proxy by keeping minimum connection count alive via scheduled keep-alive query |
| Replica promotion after primary deletion (manual error) | Promoted replica lacks WAL from unconfirmed primary transactions → recent writes lost → application reads stale or missing data | Recent writes since last confirmed replica lag checkpoint | `pg_last_xact_replay_timestamp()` on promoted replica is behind expected; missing rows; sequence gaps | Compare `pg_waldump` output against application transaction log; restore missing rows from RDS automated snapshot |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Minor version upgrade (e.g., PostgreSQL 14.x → 14.y) | Query plan regression — specific queries suddenly do full table scans; latency increases 5–20x | Within minutes of instance coming back online after upgrade | Slow query log: queries that were fast now appear post-upgrade; RDS event `DB instance upgraded`; `EXPLAIN` output changes | Run `ANALYZE` on affected tables; consider `pg_hint_plan` to force old plan; rollback minor version not possible — pin to specific version in parameter group |
| Major version upgrade (e.g., PostgreSQL 13 → 15) | Extension incompatibility (`ERROR: extension "postgis" is not compatible`); catalog changes break stored procedures | Immediately post-upgrade on first use of incompatible extension | RDS pre-upgrade checks: `aws rds describe-db-instances --query 'DBInstances[0].PendingModifiedValues'`; test upgrade in staging first | Restore pre-upgrade snapshot: `aws rds restore-db-instance-from-db-snapshot --db-instance-identifier $NEW_ID --db-snapshot-identifier $PRE_UPGRADE_SNAPSHOT` |
| `max_connections` parameter reduced in parameter group | Existing connections allowed; new connections rejected above new limit; applications queued or erroring | Within minutes of parameter group reboot or on next application restart | `pg_stat_activity` count near new max; application logs `FATAL: sorry, too many clients already`; correlate with parameter group apply | Revert parameter group change: `aws rds modify-db-parameter-group --db-parameter-group-name $PG --parameters ParameterName=max_connections,ParameterValue=200,ApplyMethod=pending-reboot` |
| Storage type migrated from gp2 to io1 with wrong IOPS setting | Write latency spike if IOPS set too low; or unnecessary cost if set too high | Immediately after modification completes (migration can take hours) | CloudWatch `WriteLatency` and `DiskQueueDepth` post-migration; compare `ProvisionedIOPS` vs actual `WriteIOPS` | `aws rds modify-db-instance --db-instance-identifier $DB_ID --iops $CORRECT_IOPS --apply-immediately` |
| DB subnet group changed to subnet without NAT gateway | RDS cannot reach external endpoints (S3 for export, Secrets Manager, etc.) | Immediately on next outbound connection attempt | RDS logs `could not connect to server`; VPC Flow Logs showing rejected outbound traffic | Restore to original subnet group: `aws rds modify-db-instance --db-instance-identifier $DB_ID --db-subnet-group-name $ORIGINAL_SUBNET_GROUP` |
| Application schema migration adds index on large table without `CONCURRENTLY` | Table locked during index build → all queries on that table blocked → application timeouts → cascading failures | Immediately on `CREATE INDEX` execution | `pg_stat_activity` showing `lock granted: false` for normal queries; `pg_locks` shows exclusive lock on table | Kill the migration: `SELECT pg_cancel_backend($MIGRATION_PID)`; re-run with `CREATE INDEX CONCURRENTLY` |
| RDS security group rule change removing app server CIDR | Applications get `Connection timed out` instead of `Connection refused` — slow failure | Immediately on next new connection attempt (existing connections may persist briefly) | VPC Flow Logs showing dropped packets from app servers to RDS port 5432; correlate with EC2 `AuthorizeSecurityGroupIngress` CloudTrail event | Add rule back: `aws ec2 authorize-security-group-ingress --group-id $SG_ID --protocol tcp --port 5432 --source-group $APP_SG_ID` |
| `work_mem` parameter increased too high for concurrent connections | OOM on RDS instance; `FreeableMemory` drops rapidly; instance reboots | Within minutes to hours depending on query load and concurrency | RDS event `DB instance restarted`; CloudWatch `FreeableMemory` dropping to near zero before restart; correlate with parameter group change | Revert `work_mem` to safe value; calculate safe value: `(RAM - shared_buffers) / max_connections / 2` |
| Certificate rotation for RDS CA expiry (rds-ca-2019 → rds-ca-rsa2048-g1) | Applications using old CA certificate get SSL `CERTIFICATE_VERIFY_FAILED`; connection fails entirely | At CA expiration date or immediately if new cert required by policy change | Application logs `ssl.SSLError: [SSL: CERTIFICATE_VERIFY_FAILED]`; RDS event about certificate rotation | Update application trust store with new CA; download: `curl -o rds-ca.pem https://truststore.pki.rds.amazonaws.com/$REGION/$REGION-bundle.pem` |
| IAM authentication enabled replacing password auth | Services using password-based connections fail until they are updated to use IAM token generation | Immediately on config change; affects next connection attempt | Application logs `FATAL: password authentication failed`; CloudTrail: `ModifyDBInstance` enabling `IAMDatabaseAuthentication` | Roll back: `aws rds modify-db-instance --db-instance-identifier $DB_ID --no-enable-iam-database-authentication --apply-immediately` |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Read replica replication lag — stale reads | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name ReplicaLag --dimensions Name=DBInstanceIdentifier,Value=$REPLICA_ID --statistics Average --period 60 --start-time $T --end-time $NOW` | Read queries return data that should have been updated minutes ago; cache invalidation bypassed | Data freshness SLA violated; downstream systems making decisions on stale data | Route all reads to primary until `ReplicaLag` returns to <1s; investigate WAL sender on primary: `SELECT * FROM pg_stat_replication` |
| Aurora global database replication lag to secondary region | `aws rds describe-global-clusters --global-cluster-identifier $GLOBAL_ID \| jq '.GlobalClusters[0].GlobalClusterMembers'` | Secondary region reads stale; `aurora_global_db_instance_status()` shows lag | RPO exceeded for secondary region reads; DR activation would lose uncommitted transactions | Investigate primary writer's `aurora_global_db_status()`; check for long-running transactions blocking log shipping |
| Multi-AZ failover with transaction loss (crash at exact wrong moment) | `SHOW server_version_num;` on new primary + check application transaction IDs vs DB sequences | Row inserted by application is missing from DB after failover; sequence gaps in IDs | Data loss for transactions in-flight at failover time | Cross-reference application transaction log with DB; re-insert lost rows; adjust application to verify DB commit before returning success |
| RDS Proxy stale connection serving wrong database after failover | `aws rds describe-db-proxy-targets --db-proxy-name $PROXY_NAME \| jq '.[].Endpoint'` | Proxy routes connections to old primary endpoint that is now a replica; writes silently fail or are redirected | Writes going to read-only replica cause `ERROR: cannot execute INSERT in a read-only transaction` | `aws rds reboot-db-instance --db-instance-identifier $PROXY_NAME` — or delete and recreate Proxy target group to reconnect to new primary |
| Timezone mismatch between application and RDS | `SHOW timezone;` on RDS; `date` on application servers | `TIMESTAMP` columns store wrong time; `NOW()` returns unexpected value; time-based queries return wrong rows | Data integrity issues for time-sensitive records (billing, audit logs) | Set RDS timezone: `aws rds modify-db-parameter-group --parameters ParameterName=timezone,ParameterValue=UTC,ApplyMethod=pending-reboot`; standardize all timestamps to UTC |
| Logical replication slot not consumed — WAL bloat | `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) FROM pg_replication_slots;` | `FreeStorageSpace` declining; WAL directory growing; eventual storage full and DB crash | Storage exhaustion; data loss risk if WAL cannot be written | Drop stale slot: `SELECT pg_drop_replication_slot('$SLOT_NAME')`; ensure consumer (DMS, Debezium) is running |
| Sequence exhaustion in high-insert table (`integer` max reached) | `SELECT last_value, is_called FROM $SCHEMA.$SEQUENCE_NAME;` | `ERROR: nextval: reached maximum value of sequence` on INSERT | All new row insertions fail; application errors until sequence type is altered | `ALTER SEQUENCE $SEQ_NAME AS bigint MAXVALUE 9223372036854775807; SELECT setval('$SEQ_NAME', $SAFE_START);` |
| Parallel schema migration conflict — two teams altering same table | `SELECT * FROM pg_locks l JOIN pg_stat_activity a ON l.pid=a.pid WHERE NOT granted;` | `ALTER TABLE` waits indefinitely; `LOCK TABLE` conflict in migration log | Deployment blocked; other migrations queued behind the lock | Kill the blocking migration; coordinate deployments; use zero-downtime migration patterns (`ADD COLUMN DEFAULT NULL`) |
| Application connection pool connecting to wrong database after clone | `SELECT current_database();` in application session | Application writes to wrong database (clone used for testing); production data mixed with test data | Data pollution; production rows in test DB or vice versa | Immediately revoke application role from clone DB; verify connection string points to correct endpoint; audit `pg_stat_activity` by `datname` |
| Automated snapshot used for restore but WAL archive incomplete | `aws rds describe-db-snapshots --db-instance-identifier $DB_ID --query 'DBSnapshots[-1].SnapshotCreateTime'` + PITR to specific time fails | Point-in-time restore to a specific timestamp fails with `InvalidParameterValue: No automated backup found for time` | Cannot restore to exact time of incident; forced to restore to last snapshot | Restore to closest available snapshot; re-apply transaction log from application log if available; review why WAL archive was incomplete |

## Runbook Decision Trees

### Decision Tree 1: RDS instance high latency / query performance degradation

```
Is DB instance status "available"?
`aws rds describe-db-instances --db-instance-identifier $DB_ID --query 'DBInstances[0].DBInstanceStatus'`
├── NO  → What is the instance status?
│         ├── "failing-over" → Wait 30-60s for automatic failover; verify new primary: `aws rds describe-db-instances --query 'DBInstances[0].Endpoint.Address'`
│         ├── "backing-up" → Backup I/O contention; monitor until backup completes; shift backup window if recurrent
│         └── "modifying" → Pending parameter group apply or resize; check Events: `aws rds describe-events --source-identifier $DB_ID --duration 60`
└── YES → Is CPU utilization > 80%?
          `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name CPUUtilization --statistics Average`
          ├── YES → Is there a single query dominating CPU?
          │         Run: `SELECT pid, usename, query, state FROM pg_stat_activity WHERE state='active' ORDER BY query_start ASC;`
          │         ├── YES → Root cause: Long-running query or lock contention → Fix: `SELECT pg_terminate_backend($PID);`; add index or rewrite query
          │         └── NO  → Root cause: High-concurrency workload → Fix: Increase instance class or enable read replicas for reads
          └── NO  → Is DiskQueueDepth > 1?
                    `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name DiskQueueDepth --statistics Average`
                    ├── YES → Root cause: IOPS saturation → Fix: Increase provisioned IOPS: `aws rds modify-db-instance --db-instance-identifier $DB_ID --iops $NEW_IOPS --apply-immediately`
                    └── NO  → Is FreeableMemory declining rapidly?
                              ├── YES → Root cause: Memory pressure / OS cache eviction → Fix: Increase instance memory; reduce `work_mem`; check for memory leaks in connections
                              └── NO  → Is ReplicaLag > SLO threshold?
                                        `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name ReplicaLag`
                                        ├── YES → Root cause: Replica falling behind primary writes → Fix: Reduce write rate; upgrade replica instance; check network bandwidth
                                        └── NO  → Check Performance Insights for top wait events: `aws pi get-resource-metrics --service-type RDS --identifier db:$DB_ID`
                                                  └── Escalate: DBA team with Performance Insights data and CloudWatch metrics
```

### Decision Tree 2: RDS storage exhaustion approaching

```
Is FreeStorageSpace below 10% of AllocatedStorage?
`aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name FreeStorageSpace --statistics Minimum`
├── NO  → Is storage growth rate accelerating? (Compare 1h vs 24h trend)
│         ├── NO  → Normal growth; review in next capacity planning cycle
│         └── YES → Identify what's growing: check WAL/redo log buildup; table bloat; replication slots
│                   `SELECT pg_size_pretty(pg_database_size(current_database()));`
│                   └── Schedule storage increase for next maintenance window
└── YES → Is storage autoscaling enabled?
          `aws rds describe-db-instances --db-instance-identifier $DB_ID --query 'DBInstances[0].MaxAllocatedStorage'`
          ├── YES (MaxAllocatedStorage set) → Has autoscaling already triggered?
          │   Check: `aws rds describe-events --source-identifier $DB_ID --duration 120`
          │   ├── YES → Wait for autoscaling to complete; monitor `FreeStorageSpace` recovering
          │   └── NO  → Root cause: Autoscaling threshold not reached yet or stuck → Fix: Manually modify storage: `aws rds modify-db-instance --db-instance-identifier $DB_ID --allocated-storage $LARGER_SIZE --apply-immediately`
          └── NO (autoscaling off) → Immediate manual increase required
                                     Root cause: Storage misconfiguration (autoscaling disabled)
                                     Fix: `aws rds modify-db-instance --db-instance-identifier $DB_ID --allocated-storage $NEW_SIZE --max-allocated-storage $MAX_SIZE --apply-immediately`
                                     Prevent: `SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) FROM pg_tables ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC LIMIT 10;`
                                     Escalate: DBA to investigate table bloat and cleanup strategy
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Automated backup retention set too high | Backup retention at 35 days for all instances including dev/test; snapshot storage cost exploding | `aws rds describe-db-instances --query 'DBInstances[*].{Id:DBInstanceIdentifier,RetentionPeriod:BackupRetentionPeriod}'` | Snapshot storage billed at $0.095/GB-month; large databases expensive | Reduce dev/test retention to 1-7 days: `aws rds modify-db-instance --db-instance-identifier $DB_ID --backup-retention-period 7` | Enforce retention policy by environment tag; IaC default: prod=14d, staging=7d, dev=1d |
| Read replica fleet not scaled down after traffic event | Replicas added for peak event; never removed; ongoing instance cost | `aws rds describe-db-instances --query 'DBInstances[?ReadReplicaSourceDBInstanceIdentifier!=null].DBInstanceIdentifier'` | Each replica costs same as standalone instance | Delete idle replicas: `aws rds delete-db-instance --db-instance-identifier $REPLICA_ID --skip-final-snapshot` | Tag replicas with `event` and TTL; add replica count to weekly cost review |
| Multi-AZ left enabled on non-production instances | Multi-AZ doubles instance cost; enabled on dev/test by default in IaC template | `aws rds describe-db-instances --query 'DBInstances[*].{Id:DBInstanceIdentifier,MultiAZ:MultiAZ,Env:TagList}'` | 2x instance cost on non-production workloads | Disable Multi-AZ on dev/test: `aws rds modify-db-instance --db-instance-identifier $DB_ID --no-multi-az --apply-immediately` | IaC parameter: `multi_az = var.environment == "prod" ? true : false` |
| gp2 volume with auto-IOPS charging at high storage size | gp2 volumes over 5.33 TB charge more than provisioned io1; or burst IOPS credits exhausted | `aws rds describe-db-instances --query 'DBInstances[*].{Id:DBInstanceIdentifier,StorageType:StorageType,AllocatedGB:AllocatedStorage,IOPS:Iops}'` | gp2 large volumes more expensive than equivalent gp3 | Migrate to gp3: `aws rds modify-db-instance --db-instance-identifier $DB_ID --storage-type gp3 --apply-immediately` | Default all new instances to gp3; set explicit IOPS + throughput |
| Idle RDS instances with no connections | Dev/test instances running 24/7 with 0 connections for weeks | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name DatabaseConnections --statistics Maximum` = 0 for 7 days | Full instance cost with zero utilization | Stop instance: `aws rds stop-db-instance --db-instance-identifier $DB_ID` (stops for 7 days; auto-resumes) | Auto-stop dev instances after 8 hours of zero connections via Lambda + EventBridge |
| Performance Insights enabled with long retention | Performance Insights with 2-year retention enabled on all instances; $0.02/vCPU-hour | `aws rds describe-db-instances --query 'DBInstances[*].{Id:DBInstanceIdentifier,PIEnabled:PerformanceInsightsEnabled,PIRetention:PerformanceInsightsRetentionPeriod}'` | Ongoing Performance Insights storage cost for large vCPU instances | Reduce retention to 7 days (free tier): `aws rds modify-db-instance --db-instance-identifier $DB_ID --performance-insights-retention-period 7` | Default to 7-day PI retention; use longer retention only for prod during incidents |
| Replication slot accumulation blocking WAL cleanup | Logical replication slots not consumed; WAL files accumulating; storage filling | `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS lag FROM pg_replication_slots;` | Storage exhaustion if WAL growth unchecked | Drop stale replication slot: `SELECT pg_drop_replication_slot('$SLOT_NAME');` | Monitor `pg_replication_slots` lag; alert if WAL lag > 10 GB |
| Enhanced Monitoring at 1-second interval on all instances | 1-second Enhanced Monitoring generating excessive CloudWatch Logs data | `aws rds describe-db-instances --query 'DBInstances[*].{Id:DBInstanceIdentifier,MonitoringInterval:MonitoringInterval}'` where interval=1 | CloudWatch Logs ingestion cost for high-frequency metrics | Change to 60-second interval: `aws rds modify-db-instance --db-instance-identifier $DB_ID --monitoring-interval 60` | Use 1s only for active performance investigations; default to 60s |
| Unencrypted snapshot exports to S3 triggering large export jobs | Full snapshot export triggered repeatedly; exporting large encrypted snapshot to S3 costs per GB | `aws rds describe-export-tasks --query 'ExportTasks[?Status==\`STARTING\`]'` | S3 data transfer cost + snapshot export cost ($0.01/GB) | Cancel in-flight export: `aws rds cancel-export-task --export-task-identifier $TASK_ID` | Restrict `StartExportTask` via IAM SCP; require approval for exports > 100 GB |

## Latency & Performance Degradation Patterns
| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot shard / hot table partition | Single table experiencing dramatically higher IOPS than others; `WriteThroughput` or `ReadThroughput` spiking on one partition | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name ReadIOPS --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Maximum --period 60 --start-time $START --end-time $END` | Sequential primary key causing all writes to hot last page (MySQL B-tree); UUID or timestamp monotonic keys | Use UUID v4 or application-level hash sharding for primary keys; enable `innodb_adaptive_hash_index` |
| Connection pool exhaustion | Application `PoolTimeoutError` or `too many connections`; `DatabaseConnections` metric at instance max | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name DatabaseConnections --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Maximum --period 60 --start-time $START --end-time $END` | Each application instance opening its own connection pool; burst of app instances overwhelms DB | Enable RDS Proxy: `aws rds create-db-proxy --db-proxy-name $PROXY_NAME --engine-family POSTGRESQL --role-arn $ROLE_ARN --auth '[{"AuthScheme":"SECRETS","SecretArn":"$SECRET_ARN","IAMAuth":"DISABLED"}]' --vpc-subnet-ids $SUBNETS` |
| GC/memory pressure causing query latency spikes | PostgreSQL `shared_buffers` evictions; MySQL buffer pool hit rate drops; query latency P99 spikes periodically | `psql -h $HOST -U $USER -c "SELECT buffers_clean, buffers_alloc FROM pg_stat_bgwriter;"` | RDS instance freeable memory too low; buffer pool thrashing on large analytical queries alongside OLTP | Increase instance size; separate OLTP and analytics workloads; set `work_mem` per session for analytics |
| Thread pool saturation (MySQL) | MySQL thread cache exhausted; new connections waiting; `Threads_created` rising fast | `aws rds download-db-log-file-portion --db-instance-identifier $DB_ID --log-file-name error/mysql-error.log --output text \| grep "Thread stack"` | Concurrent connection spike exceeding `thread_stack` × `max_connections` memory budget | Set `thread_cache_size = 100` in parameter group; scale instance; use ProxySQL or RDS Proxy for connection multiplexing |
| Slow query from missing index | Specific queries suddenly slow after data growth; `FullScan` queries appearing in slow log | `aws rds download-db-log-file-portion --db-instance-identifier $DB_ID --log-file-name slowquery/mysql-slowquery.log --output text \| grep -E "Query_time: [5-9]\|Query_time: [0-9]{2}"` | Table grew past threshold where full scan is slower than index scan; index dropped accidentally | Add index: `CREATE INDEX CONCURRENTLY idx_name ON table(column);` (PostgreSQL non-blocking); use `EXPLAIN ANALYZE` to verify |
| CPU steal from noisy neighbor | RDS CPU utilization normal but query latency high; `CPUCreditBalance` dropping on T-class instances | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name CPUCreditBalance --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Average` | T3/T2 burstable instance with depleted CPU credits; or noisy neighbor on multi-tenant host | Upgrade from T-class to M or R-class: `aws rds modify-db-instance --db-instance-identifier $DB_ID --db-instance-class db.r6g.large --apply-immediately` |
| Lock contention from long-running transactions | `pg_locks` table full; queries piling up in `lock_wait` state; `ROLLBACK` storm | `psql -h $HOST -U $USER -c "SELECT pid, now()-query_start, state, wait_event_type, left(query,100) FROM pg_stat_activity WHERE wait_event_type='Lock' ORDER BY 2 DESC LIMIT 10;"` | Long-running transaction holding row/table lock while batch job waits; DDL migration blocking all reads | Kill blocking PID: `psql -c "SELECT pg_terminate_backend($BLOCKING_PID);"`; set `lock_timeout = '5s'` in application session |
| Serialization overhead on logical replication | Replica lag growing; primary CPU elevated; `pg_replication_slots.confirmed_flush_lsn` falling behind | `psql -h $HOST -U $USER -c "SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS lag FROM pg_replication_slots;"` | WAL decoder serializing large JSONB columns or wide rows at high write rate | Tune `max_replication_slots`; use `pglogical` with column filters; or upgrade to db.r6g for more CPU on primary |
| Batch size misconfiguration causing IOPS bursts | nightly batch inserts saturating EBS IOPS; application queries timing out during batch window | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name WriteIOPS --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Maximum --period 60 --start-time $BATCH_START --end-time $BATCH_END` | Batch inserting 100K rows without rate limiting; gp2 IOPS burst credits exhausted | Throttle batch inserts to stay within provisioned IOPS; switch to gp3 with provisioned IOPS: `aws rds modify-db-instance --db-instance-identifier $DB_ID --storage-type gp3 --iops 6000` |
| Downstream dependency latency (external FK lookups) | Read queries slow after schema change adding FK constraint; `EXPLAIN` shows FK validation scan | `psql -h $HOST -U $USER -c "EXPLAIN (ANALYZE, BUFFERS) $SLOW_QUERY;"` | FK constraint causing sequential scan on referenced table without supporting index; or distributed join across linked servers | Add index on FK column: `CREATE INDEX CONCURRENTLY ON child_table(fk_column);`; consider deferring FK validation for batch loads |

## Network & TLS Failure Patterns
| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| RDS SSL/TLS certificate expiry (RDS CA rotation) | Application SSL error: `SSL SYSCALL error: EOF detected`; `certificate verify failed` after AWS CA rotation | `openssl s_client -connect $DB_HOST:5432 -starttls postgres 2>/dev/null \| openssl x509 -noout -enddate` | AWS rotated RDS CA; application still trusting old CA bundle | Download new RDS CA bundle: `curl -o rds-ca-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`; update app connection string |
| mTLS IAM authentication failure after cert rotation | IAM auth to RDS fails; `PAM authentication failed for user "db_user"` in PostgreSQL logs | `aws rds generate-db-auth-token --hostname $DB_HOST --port 5432 --region $REGION --username $DB_USER` returns token; test connection | IAM auth token expired (15 min TTL); or IAM role missing `rds-db:connect` permission | Ensure application requests fresh token per connection; verify role policy: `aws iam simulate-principal-policy --policy-source-arn $ROLE_ARN --action-names rds-db:connect --resource-arns arn:aws:rds-db:$REGION:$ACCOUNT:dbuser:$DB_RESOURCE_ID/$DB_USER` |
| DNS resolution failure for RDS endpoint | Application cannot connect; `getaddrinfo failed: Name or service not known` | `dig $DB_IDENTIFIER.cluster-$HASH.$REGION.rds.amazonaws.com`; `aws rds describe-db-instances --db-instance-identifier $DB_ID --query 'DBInstances[0].Endpoint'` | DNS propagation delay after failover; or Route 53 resolver returning old IP | Flush DNS cache on app host; increase DNS TTL awareness in connection pool; use RDS Proxy which abstracts DNS failover |
| TCP connection exhaustion to RDS endpoint | All application instances timing out on DB connect; `ConnectionRefused` despite DB being up | `netstat -an \| grep $DB_PORT \| grep -c ESTABLISHED`; `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name DatabaseConnections --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Maximum` at max | Too many concurrent TCP connections; RDS `max_connections` parameter limit reached | Kill idle connections: `psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND query_start < now()-interval '10 minutes';"` |
| Security group rule removed — application loses DB access | All application DB queries fail with connection timeout; no error response (connection drops silently) | `aws ec2 describe-security-groups --group-ids $RDS_SG_ID --query 'SecurityGroups[0].IpPermissions'` — check for missing app SG inbound rule on DB port | Security group inbound rule for application tier accidentally removed during security hardening | Re-add rule: `aws ec2 authorize-security-group-ingress --group-id $RDS_SG_ID --protocol tcp --port 5432 --source-group $APP_SG_ID` |
| Packet loss causing PostgreSQL query timeout | Intermittent query timeouts at exactly the `statement_timeout` value; worse on large result sets | VPC Flow Logs filtering by DB ENI ID for `REJECT` actions; `ping -c 100 $DB_HOST` from application instance for packet loss | MTU fragmentation on VPN/Direct Connect path; or NACLs blocking large TCP segments | Check NACL allows ephemeral ports inbound (1024-65535); set `tcp_keepalives_idle=60` in libpq connection string |
| MTU mismatch causing silent data corruption on large queries | Queries returning partial results on large SELECTs; checksum errors in pg client | `ping -M do -s 1450 $DB_HOST` — if `Message too long` then MTU < 1478 | Large PostgreSQL result packets fragmented; DF bit set; intermediate device dropping fragments | Reduce MTU: `ip link set eth0 mtu 1400` on application host; or configure `tcp_mtu_probing=1` |
| Firewall blocking RDS subnet after VPC CIDR expansion | New application subnets cannot reach RDS after VPC CIDR block added; old subnets work fine | `aws ec2 describe-security-groups --group-ids $RDS_SG_ID \| jq '.SecurityGroups[].IpPermissions[].IpRanges'` — old CIDR present, new CIDR missing | Security group only allows old CIDR; new subnets in new CIDR block not added | Add new CIDR to RDS security group: `aws ec2 authorize-security-group-ingress --group-id $RDS_SG_ID --protocol tcp --port 5432 --cidr $NEW_SUBNET_CIDR` |
| SSL handshake timeout on `ssl_mode=verify-full` | Application connection attempts timeout after TLS negotiation starts; server cert CN mismatch | `openssl s_client -connect $DB_HOST:5432 -starttls postgres 2>&1 \| grep -E "CN=\|subject="` | RDS endpoint CNAME not matching CN in certificate; using IP address instead of FQDN in connection string | Use full RDS FQDN in connection string: `$DB_IDENTIFIER.cluster-$HASH.$REGION.rds.amazonaws.com`; not IP address |
| Connection reset after Multi-AZ failover | Application receives `TCP connection reset` during RDS Multi-AZ failover; up to 60s reconnection required | `aws rds describe-events --source-identifier $DB_ID --source-type db-instance --duration 60 --query 'Events[?contains(Message,\`failover\`)]'` | RDS Multi-AZ promoted standby; DNS CNAME updated to new primary IP; in-flight queries killed | Implement connection retry with exponential backoff; use RDS Proxy to abstract failover; set `jdbc:reconnect=true` in connection string |

## Resource Exhaustion Patterns
| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill — RDS instance out of memory | RDS event: `Out of memory: Kill process`; `FreeableMemory` metric at 0; MySQL/PostgreSQL `FATAL: out of memory` | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name FreeableMemory --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Minimum --period 60 --start-time $START --end-time $END` | Scale up instance: `aws rds modify-db-instance --db-instance-identifier $DB_ID --db-instance-class db.r6g.xlarge --apply-immediately` | Set CloudWatch alarm on `FreeableMemory < 500MB`; tune `shared_buffers` and `work_mem` parameters |
| Disk full on data partition | RDS in `storage-full` state; all writes rejected; `FreeStorageSpace` at 0 | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name FreeStorageSpace --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Minimum` | Enable storage autoscaling: `aws rds modify-db-instance --db-instance-identifier $DB_ID --max-allocated-storage 1000`; or immediately modify: `aws rds modify-db-instance --db-instance-identifier $DB_ID --allocated-storage 500 --apply-immediately` | Enable RDS storage autoscaling; set CloudWatch alarm on `FreeStorageSpace < 10%` |
| Disk full on log partition (WAL/binlog) | PostgreSQL: `no space left on device` in WAL directory; MySQL: binary log accumulation | `aws rds download-db-log-file-portion --db-instance-identifier $DB_ID --log-file-name error/postgresql.log.$(date +%Y-%m-%d) --output text \| grep "no space"` | Reduce WAL retention: `CHECKPOINT; SELECT pg_switch_wal();`; MySQL: `PURGE BINARY LOGS BEFORE NOW() - INTERVAL 3 DAY` | Monitor replication slot lag; drop unused replication slots; tune `wal_keep_size` and `max_slot_wal_keep_size` |
| File descriptor exhaustion | PostgreSQL `FATAL: could not open file: Too many open files`; MySQL `Can't open file: errno 24` | `psql -c "SHOW max_files_per_process;"` and compare to system `ulimit -n` on RDS; check `pg_stat_file` for open handles | Restart RDS instance after identifying leak: `aws rds reboot-db-instance --db-instance-identifier $DB_ID`; patch application to close cursors properly | Use connection pooling to limit per-connection file handles; close cursors explicitly in application code |
| Inode exhaustion on PostgreSQL base directory | PostgreSQL cannot create temp files; `no space left on device` despite ample disk space | `aws rds download-db-log-file-portion --db-instance-identifier $DB_ID --log-file-name error/postgresql.log.$(date +%Y-%m-%d) --output text \| grep "inode"` | Clear old temporary files (PostgreSQL auto-cleans on restart); vacuum dead tuples to reduce table files: `VACUUM FULL $TABLE` | Run `AUTOVACUUM` aggressively; avoid creating millions of small tables; use partitioning with range-based table management |
| CPU throttle on burstable T-class instance | CPU credit balance depleted; `CPUCreditBalance` at 0; queries slow uniformly | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name CPUCreditBalance --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Average --period 300 --start-time $START --end-time $END` | Upgrade to `db.m6g` (non-burstable): `aws rds modify-db-instance --db-instance-identifier $DB_ID --db-instance-class db.m6g.large --apply-immediately` | Do not use T-class instances for production RDS; use M/R-class; size based on CPU requirements |
| Swap exhaustion causing paging | `SwapUsage` metric rising; query latency degrading; OS paging to disk | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name SwapUsage --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Maximum --period 60 --start-time $START --end-time $END` | Reduce PostgreSQL `shared_buffers` and `effective_cache_size`; scale up instance to larger memory class | Set `shared_buffers = 25%` of instance RAM; configure `huge_pages=on` for PostgreSQL; monitor `FreeableMemory` |
| Max connections limit | All connections occupied; new connections rejected with `FATAL: remaining connection slots are reserved` | `psql -c "SELECT count(*) FROM pg_stat_activity;"` vs `psql -c "SHOW max_connections;"` | Kill idle connections immediately: `psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < now()-interval '5 min';"` | Deploy RDS Proxy; tune `max_connections` parameter; set `connection_limit` per database user: `ALTER ROLE $USER CONNECTION LIMIT 50` |
| Network socket buffer exhaustion under heavy replication | Replica lag growing; replication sender on primary consuming large TCP buffers; OOM for network stack | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name ReplicaLag --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Maximum --period 60 --start-time $START --end-time $END` | Reduce number of read replicas; increase `wal_sender_timeout`; upgrade primary instance network capacity (larger instance class) | Limit read replicas to 5 per primary; use Aurora for higher replica fan-out; monitor `ReplicaLag` continuously |
| Ephemeral port exhaustion on application connecting to RDS | Application layer `EADDRNOTAVAIL` errors on DB connect; source ports exhausted from high connection churn | `ss -s` on application host showing thousands of `TIME_WAIT` connections to DB port; `sysctl net.ipv4.ip_local_port_range` | Enable `SO_REUSEADDR` + `tcp_tw_reuse`; reduce connection churn by using persistent connection pools; use RDS Proxy | Set `net.ipv4.tcp_tw_reuse=1` on application hosts; use PgBouncer or RDS Proxy to multiplex connections |

## Distributed Transaction & Event Ordering Failures
| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation — duplicate INSERT from application retry | Duplicate rows in orders/events table; unique constraint violation logs; duplicate key errors | `psql -h $HOST -U $USER -c "SELECT request_id, count(*) FROM orders GROUP BY request_id HAVING count(*) > 1 LIMIT 10;"` | Double-charging; duplicate order fulfillment; financial discrepancy | Add unique constraint on idempotency key column: `ALTER TABLE orders ADD CONSTRAINT uq_request_id UNIQUE (request_id);`; use `INSERT ... ON CONFLICT DO NOTHING` |
| Saga partial failure — two-phase DB + Kafka write | PostgreSQL COMMIT succeeds but Kafka publish fails; DB and event stream diverge | `psql -c "SELECT id, status FROM saga_log WHERE completed_at IS NULL AND created_at < now()-interval '5 minutes';"` | Downstream services miss the event; eventual consistency broken; manual reconciliation needed | Implement outbox pattern: write event to `outbox` table in same DB transaction; separate process publishes to Kafka |
| Replica read returning stale data causing lost update | Application reads from replica (stale), makes decision, writes to primary; concurrent update causes overwrite | `psql -h $REPLICA_HOST -U $USER -c "SHOW server_version;"` vs primary; check `SELECT now()-pg_last_xact_replay_timestamp() AS replica_lag;` | Lost updates; stale inventory reads; incorrect balance calculations | Route read-your-writes to primary for sensitive operations; add `SELECT ... FOR UPDATE` for optimistic locking; check replica lag before reads |
| Distributed lock expiry mid-operation (advisory lock) | PostgreSQL advisory lock released due to session timeout mid-transaction; concurrent process acquires same lock | `psql -c "SELECT pid, classid, objid, granted FROM pg_locks WHERE locktype='advisory';"` | Two processes modifying same resource simultaneously; data corruption | Use `pg_advisory_xact_lock` (transaction-scoped, auto-released on commit/rollback) instead of session-level locks; implement heartbeat for long operations |
| Out-of-order event processing from CDC replication lag | Debezium/AWS DMS capturing changes; downstream consumer receives UPDATE before INSERT due to commit order | `aws dms describe-replication-tasks --query 'ReplicationTasks[?ReplicationTaskIdentifier==\`$TASK_ID\`].ReplicationTaskStats'`; check `FullLoadProgressPercent` and `TablesLoading` | Foreign key violations in destination; event processing failures; incorrect state transitions | Enable CDC ordering guarantees in DMS; use transaction logs to ensure ordered delivery; implement retry with ordering in consumer |
| At-least-once write from application retry duplicating rows | Application receives network timeout from RDS; retries INSERT; original INSERT succeeded; duplicate row created | `psql -c "SELECT id, created_at, count(*) OVER (PARTITION BY external_ref) AS dupe_count FROM transactions ORDER BY created_at DESC LIMIT 20;"` | Duplicate financial records; double processing; audit failures | Add idempotency column with unique constraint; use `INSERT ... ON CONFLICT (idempotency_key) DO UPDATE SET updated_at=now()` |
| Compensating transaction failure during inventory rollback | Stock reservation deducted on order; order fails; compensation Lambda to increment stock fails; stock permanently reduced | `psql -c "SELECT id, original_quantity, current_quantity, compensation_status FROM inventory_saga_log WHERE compensation_status='FAILED';"` | Incorrect inventory count; orders blocked due to false stock shortage | Manually apply compensation: `psql -c "UPDATE inventory SET quantity = quantity + $RESERVED WHERE product_id = $PRODUCT_ID;"`; implement saga with Step Functions and explicit compensation tracking |
| Cross-service deadlock via nested RDS transactions | Service A locks row in `orders` table, calls Service B which locks row in `payments` table; Service B calls back into Service A needing `orders` lock | `psql -c "SELECT pid, pg_blocking_pids(pid) AS blocked_by, left(query,100) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0;"` | Both transactions timeout after `deadlock_timeout`; PostgreSQL auto-rolls back one; retry storm possible | Establish consistent lock ordering across services (always lock `orders` before `payments`); reduce transaction scope; use MVCC read-committed isolation |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — one tenant's complex queries consuming all RDS CPU | `psql -c "SELECT pid, usename, query_start, state, left(query,80) FROM pg_stat_activity ORDER BY query_start \| head -5;"` shows long-running queries from one user | Other tenants experience query timeouts; application P99 latency spikes | `psql -c "SELECT pg_cancel_backend($NOISY_PID);"` | Enable per-user connection limits: `psql -c "ALTER ROLE tenant_user CONNECTION LIMIT 10;"` ; implement `pg_stat_statements` tracking; use RDS Proxy with per-tenant connection limits |
| Memory pressure — one tenant's table statistics bloating shared_buffers | `psql -c "SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname\|\|'.'\|\|tablename)) FROM pg_tables ORDER BY pg_total_relation_size(schemaname\|\|'.'\|\|tablename) DESC LIMIT 5;"` | Buffer pool evictions causing other tenants' frequently-accessed data to require disk reads | `psql -c "SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE usename='$TENANT_USER' AND state='active';"` | Move noisy tenant to dedicated RDS instance; implement schema-per-tenant with separate `search_path` |
| Disk I/O saturation — tenant running unindexed full table scans | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name ReadIOPS --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Average --period 60` spiking; `psql -c "SELECT * FROM pg_stat_user_tables WHERE seq_scan > 1000 ORDER BY seq_scan DESC;"` | Other tenants' indexed queries slow due to I/O contention; RDS IOPS provisioned IOPS exhausted | `psql -c "SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE wait_event_type='IO';"` | Add missing index: `psql -c "CREATE INDEX CONCURRENTLY idx_tenant_data ON $TABLE ($COLUMN) WHERE tenant_id = $TENANT_ID;"` ; enable `pg_badger` for slow query analysis |
| Network bandwidth monopoly — tenant bulk-loading large CSV via COPY command | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name NetworkReceiveThroughput --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --statistics Average --period 60` at max | Other tenants experience connection timeouts during bulk load; network saturation | `psql -c "SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE query LIKE 'COPY%';"` | Throttle COPY operations using `pg_sleep` between batches; schedule bulk loads during off-peak hours; use separate RDS instance for ETL |
| Connection pool starvation — one tenant's ORM opening connections without releasing | `psql -c "SELECT usename, count(*), max(now()-state_change) AS idle_duration FROM pg_stat_activity WHERE state='idle' GROUP BY usename ORDER BY count DESC;"` | Other tenants blocked waiting for connections; `FATAL: remaining connection slots are reserved` | `psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND usename='$TENANT' AND state_change < now()-interval '5 minutes';"` | Enforce connection limits per tenant role; deploy RDS Proxy with per-target connection limits: `aws rds modify-db-proxy-target-group --db-proxy-name $PROXY --target-group-name default --connection-pool-config MaxConnectionsPercent=10` |
| Quota enforcement gap — tenant bypassing row-level security via superuser elevation | `psql -c "SELECT usename, usesuper FROM pg_user WHERE usesuper=true;"` shows unexpected superuser; `psql -c "SHOW row_security;"` returns `off` for superuser connections | Tenant accessing other tenants' data by bypassing RLS policies | `psql -c "ALTER USER $TENANT_USER NOSUPERUSER;"` | Enforce RLS on all tenant tables: `psql -c "ALTER TABLE $TABLE ENABLE ROW LEVEL SECURITY; ALTER TABLE $TABLE FORCE ROW LEVEL SECURITY;"` ; prohibit superuser connections from application |
| Cross-tenant data leak risk — shared RDS schema with misconfigured RLS policy | `psql -c "SELECT polname, polroles, polqual FROM pg_policies WHERE tablename='$MULTI_TENANT_TABLE';"` — verify `tenant_id = current_user` condition present | Tenant A can query Tenant B's rows; data confidentiality breach | `psql -c "DROP POLICY $BAD_POLICY ON $TABLE; CREATE POLICY tenant_isolation ON $TABLE USING (tenant_id = current_setting('app.current_tenant')::uuid);"` | Automated RLS policy tests in CI: run cross-tenant queries as each tenant role and assert empty result sets |
| Rate limit bypass — tenant using connection multiplexing to exceed per-user query limits | `psql -c "SELECT usename, count(*) FROM pg_stat_activity WHERE state='active' GROUP BY usename ORDER BY count DESC;"` shows tenant with 200+ active queries | RDS CPU pegged at 100%; all other tenants experience severe degradation | `psql -c "ALTER ROLE $TENANT_USER CONNECTION LIMIT 5;"` immediately | Implement per-tenant query rate limiting via PgBouncer `max_client_conn` per database; alert on `pg_stat_activity` count per user > threshold |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure — RDS Enhanced Monitoring not publishing to CloudWatch | Enhanced Monitoring dashboard blank; OS-level CPU and memory metrics missing | Enhanced Monitoring requires IAM role `rds-monitoring-role`; if role deleted or trust policy broken, metrics stop | `aws rds describe-db-instances --query 'DBInstances[].{ID:DBInstanceIdentifier,Monitoring:MonitoringInterval,MonitoringRole:MonitoringRoleArn}'` | Recreate monitoring role and re-enable: `aws rds modify-db-instance --db-instance-identifier $DB_ID --monitoring-interval 60 --monitoring-role-arn arn:aws:iam::$ACCT:role/rds-monitoring-role --apply-immediately` |
| Trace sampling gap — slow queries not captured in Performance Insights | P99 latency high but Performance Insights shows no top SQL; root cause analysis blocked | Performance Insights samples at 1/sec; queries completing in <100ms may not be sampled when thousands run concurrently | `psql -c "SELECT query, calls, mean_exec_time, total_exec_time FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20;"` | Enable `pg_stat_statements` extension: `aws rds modify-db-parameter-group --db-parameter-group-name $PG --parameters ParameterName=shared_preload_libraries,ParameterValue=pg_stat_statements,ApplyMethod=pending-reboot` |
| Log pipeline silent drop — RDS logs not delivered to CloudWatch Logs | Application errors referencing DB queries; CloudWatch Logs group `/aws/rds/instance/$DB/postgresql` empty or stale | RDS log publishing to CloudWatch requires explicit configuration; default disabled; or CloudWatch Logs delivery role lost | `aws rds describe-db-instances --query 'DBInstances[].EnabledCloudwatchLogsExports'` — check if `postgresql` in list | Enable log export: `aws rds modify-db-instance --db-instance-identifier $DB_ID --cloudwatch-logs-export-configuration EnableLogTypes=postgresql,upgrade --apply-immediately` |
| Alert rule misconfiguration — RDS FreeStorageSpace alarm using bytes instead of percent | Storage alarm fires only when <1GB free; table is already 99% full but alarm never triggered | CloudWatch alarm threshold set in absolute bytes not relative percentage; DB grew faster than expected | `aws cloudwatch describe-alarms --alarm-names RDSFreeStorage \| jq '.MetricAlarms[].Threshold'` — verify threshold in bytes matches disk size | Update alarm with correct threshold: `aws cloudwatch put-metric-alarm --alarm-name RDSFreeStorage --namespace AWS/RDS --metric-name FreeStorageSpace --dimensions Name=DBInstanceIdentifier,Value=$DB_ID --threshold 5368709120 --comparison-operator LessThanThreshold --evaluation-periods 2 --period 300` |
| Cardinality explosion — pg_stat_statements normalized query hash collisions | Performance Insights shows "Other SQL" category consuming most wait time; unable to identify top queries | `pg_stat_statements.max` too low (default 5000); when exceeded, least-executed queries evicted; normalized query hashes collide | `psql -c "SELECT pg_stat_statements_reset();"` then immediately capture: `psql -c "SELECT * FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 50;"` | Increase `pg_stat_statements.max`: `aws rds modify-db-parameter-group --db-parameter-group-name $PG --parameters ParameterName=pg_stat_statements.max,ParameterValue=50000,ApplyMethod=pending-reboot` |
| Missing health endpoint — RDS Read Replica lag not alarmed | Read replica serving stale data; application reads from replica show inconsistent results; no alert fired | CloudWatch `ReplicaLag` metric exists but no alarm created; teams assume replica is healthy | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name ReplicaLag --dimensions Name=DBInstanceIdentifier,Value=$REPLICA_ID --statistics Maximum --period 60 --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` | Create replica lag alarm: `aws cloudwatch put-metric-alarm --alarm-name RDSReplicaLag --namespace AWS/RDS --metric-name ReplicaLag --dimensions Name=DBInstanceIdentifier,Value=$REPLICA_ID --threshold 30 --comparison-operator GreaterThanThreshold --evaluation-periods 3 --period 60` |
| Instrumentation gap — auto-vacuum runs not visible in application metrics | Tables growing unboundedly; query performance degrading; dead tuple bloat causing sequential scan slowdowns | `autovacuum` activity not published to CloudWatch; only visible in RDS PostgreSQL logs or `pg_stat_user_tables` | `psql -c "SELECT schemaname, relname, n_dead_tup, n_live_tup, last_autovacuum, last_autoanalyze FROM pg_stat_user_tables WHERE n_dead_tup > 10000 ORDER BY n_dead_tup DESC LIMIT 10;"` | Push autovacuum metrics to CloudWatch via custom metric Lambda: query `pg_stat_user_tables` every 5 minutes and publish `DeadTuples` per table as custom metric |
| Alertmanager/PagerDuty outage — RDS failover event during monitoring downtime | Multi-AZ failover completed but no incident created; team unaware of failover; root cause analysis delayed | RDS publishes failover event to SNS; if SNS subscription endpoint (PagerDuty) is down, notification lost | `aws rds describe-events --source-type db-instance --source-identifier $DB_ID --duration 60 \| jq '.Events[] \| select(.EventCategories[] \| test("failover"))'` | Add redundant notification: subscribe both PagerDuty and email to RDS event SNS topic: `aws sns subscribe --topic-arn $RDS_EVENTS_TOPIC --protocol email --notification-endpoint ops-backup@company.com` |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor version upgrade — PostgreSQL 15.3 → 15.6 causing query plan regression | After upgrade, specific queries 10× slower due to planner statistics reset; CPU spikes | `aws rds describe-db-instances --query 'DBInstances[].EngineVersion'`; `psql -c "SELECT query, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;"` | Snapshot restore to pre-upgrade: `aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier $DB_ID --target-db-instance-identifier $DB_ID-rollback --restore-time $PRE_UPGRADE_TIME` | Run `ANALYZE` immediately after upgrade: `psql -c "ANALYZE VERBOSE;"` ; test query plans in staging with `EXPLAIN (ANALYZE, BUFFERS)` before production upgrade |
| Major version upgrade — PostgreSQL 13 → 16 breaking pg_catalog extensions | After major version upgrade, dependent extensions (`uuid-ossp`, custom fdw) fail to load; application errors on startup | `psql -c "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE installed_version IS NOT NULL;"` — compare versions; `aws rds describe-db-instances --query 'DBInstances[].EngineVersion'` | Restore from pre-upgrade snapshot: `aws rds restore-db-instance-from-db-snapshot --db-instance-identifier $RESTORE_DB --db-snapshot-identifier $PRE_UPGRADE_SNAPSHOT` | Run `aws rds create-db-instance-read-replica` with `--engine-version 16` to test upgrade path; validate all extensions in staging |
| Schema migration partial completion — Flyway/Liquibase interrupted mid-migration | Database in inconsistent state; some tables have new columns, others don't; application fails with `column does not exist` | `psql -c "SELECT version, description, success FROM flyway_schema_history ORDER BY installed_rank DESC LIMIT 10;"` — check for `success=false` | Manually run compensating SQL to complete or rollback: `psql -c "ALTER TABLE $TABLE DROP COLUMN $NEW_COLUMN;"` then mark migration failed; restore from snapshot if too complex | Wrap all migrations in explicit transactions; use `flyway repair` to fix checksum mismatches: `flyway -url=$DB_URL repair` |
| Rolling upgrade version skew — application v2 writing new JSON schema while v1 still reading old format | During rolling deploy, v1 and v2 application instances share RDS; v1 can't parse v2-format JSON columns | `psql -c "SELECT DISTINCT jsonb_object_keys(new_column) FROM $TABLE LIMIT 20;"` shows mixed schemas; v1 application logs `JSON parse error` | Roll back application to v1: redeploy v1 containers; `psql -c "UPDATE $TABLE SET new_column = old_column WHERE new_column IS NOT NULL;"` to revert data | Use expand-contract pattern: add new column, deploy v2 reading both old+new, backfill, then remove old column in separate migration |
| Zero-downtime migration gone wrong — `ALTER TABLE` acquiring AccessExclusiveLock stalling all queries | `ALTER TABLE ADD COLUMN DEFAULT` taking full table lock for minutes; application experiencing complete write blackout | `psql -c "SELECT pid, wait_event_type, wait_event, query FROM pg_stat_activity WHERE wait_event_type='Lock';"` | Kill the migration query: `psql -c "SELECT pg_cancel_backend($MIGRATION_PID);"` | Use `ALTER TABLE ... ADD COLUMN` without DEFAULT (PostgreSQL 11+ is instant); then `UPDATE` in batches; use `pg_repack` for table rewrites without locking |
| Config format change — RDS Parameter Group custom `postgresql.conf` format rejected after upgrade | Custom parameter group applied but RDS refuses to restart with new parameters; instance stuck in `pending-reboot` state | `aws rds describe-db-instances --query 'DBInstances[].PendingModifiedValues'`; `aws rds describe-events --source-identifier $DB_ID --duration 60` shows parameter errors | Reset to default parameter group: `aws rds modify-db-instance --db-instance-identifier $DB_ID --db-parameter-group-name default.postgres15 --apply-immediately` | Test parameter group changes on a clone first: `aws rds restore-db-instance-from-db-snapshot --db-snapshot-identifier $SNAP --db-parameter-group-name $NEW_PG` |
| Feature flag rollout causing regression — new RDS Proxy IAM authentication enabling SSL-required connection | After enabling RDS Proxy IAM auth, applications without SSL configured fail to connect; `SSL connection is required` | `aws rds describe-db-proxies --db-proxy-name $PROXY \| jq '.DBProxies[].RequireTLS'`; application logs: `FATAL: no pg_hba.conf entry for host ... SSL off` | Temporarily disable TLS requirement: `aws rds modify-db-proxy --db-proxy-name $PROXY --require-tls false` | Enable TLS in all application connection strings before enabling `RequireTLS` on proxy; test with: `psql "host=$PROXY_ENDPOINT sslmode=require"` |
| Dependency version conflict — ORM upgrade changing connection pool behavior causing prepared statement cache exhaustion | After Hibernate upgrade, `ERROR: prepared statement "S_1" already exists`; PgBouncer in transaction-mode incompatible with new prepared statement behavior | `psql -c "SELECT count(*) FROM pg_prepared_statements;"` growing unboundedly; `aws logs filter-log-events --log-group-name /aws/rds/instance/$DB/postgresql --filter-pattern 'prepared statement'` | Switch PgBouncer to session mode: update `pool_mode = session` in PgBouncer config; restart PgBouncer; verify: `psql -c "SHOW pool_mode;"` via PgBouncer admin | Use `prepareThreshold=0` in JDBC connection string when using PgBouncer transaction mode; or migrate to RDS Proxy which handles prepared statement caching transparently |

## Kernel/OS & Host-Level Failure Patterns
**Minimum cross-cutting cases to evaluate here:** OOM killer false kill, inode exhaustion, CPU steal, NTP skew affecting locks, leases, or coordination, file descriptor exhaustion, and TCP conntrack table saturation.

| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|------------------|--------------|-------------|----------------------|
| OOM killer terminates RDS PostgreSQL backend process | `aws rds describe-events --source-identifier <db-id> --source-type db-instance --duration 60 \| grep -i "Out of memory"`; Enhanced Monitoring `os.memory.free` near 0 | `work_mem` set too high multiplied by concurrent connections; or `shared_buffers` over-allocated | Backend crash; client sees `FATAL: terminating connection due to administrator command` | `aws rds modify-db-parameter-group --db-parameter-group-name <pg> --parameters "ParameterName=work_mem,ParameterValue=4096,ApplyMethod=immediate"`; reduce `max_connections` |
| Inode exhaustion on RDS storage causes tablespace write failure | `aws rds describe-events --source-identifier <db-id>` shows `Storage-Full`; CloudWatch `FreeStorageSpace` at 0 | Excessive WAL accumulation; bloated pg_wal directory; unvacuumed dead tuples; binary logs for MySQL | All writes fail; `ERROR: could not write to file "pg_wal/..."` | `aws rds modify-db-instance --db-instance-identifier <id> --allocated-storage 500 --apply-immediately`; trigger VACUUM: `psql -c "VACUUM VERBOSE ANALYZE <table>"` |
| CPU steal on underlying RDS host causing query latency spike | Enhanced Monitoring `os.cpuUtilization.steal >5`; CloudWatch `CPUCreditBalance` near 0 for db.t3 instances | Noisy neighbour on multi-tenant RDS host; burstable instance type credit exhaustion | All queries slow; connection pool timeouts; cascading application errors | `aws rds modify-db-instance --db-instance-identifier <id> --db-instance-class db.m6g.large --apply-immediately`; switch from T-series to M-series |
| NTP clock skew causes replication lag and PITR inconsistency | `aws rds describe-db-instances --db-instance-identifier <replica> \| grep ReplicaLag`; `SHOW GLOBAL STATUS LIKE 'Seconds_Behind_Master'` on MySQL replica | RDS host clock drift causing binlog timestamp confusion; replica can't reconcile event ordering | Replica lag grows unbounded; read replicas serving stale data; PITR window inconsistent | Contact AWS Support; promote affected replica and recreate; `aws rds reboot-db-instance --db-instance-identifier <replica>` |
| File descriptor exhaustion causing RDS Proxy connection drops | RDS Proxy CloudWatch `DatabaseConnectionsCurrentlyBorrowed` at max; new connections rejected with `too many connections` | RDS Proxy max connections misconfigured; underlying RDS `max_connections` lower than proxy limit | Application connection pool exhausted; `FATAL: remaining connection slots reserved` | `aws rds modify-db-proxy --db-proxy-name <proxy> --require-tls`; adjust `MaxConnectionsPercent`: `aws rds modify-db-proxy-target-group --target-group-name default --db-proxy-name <proxy> --connection-pool-config MaxConnectionsPercent=80` |
| TCP conntrack table full dropping RDS connections from application | Intermittent `Connection timed out` from application to RDS; no errors on RDS side | Application on EC2 with NAT; high connection churn exhausting conntrack; especially with RDS Proxy keep-alive | New DB connections silently dropped at network layer; appears as DB unavailability | `sysctl -w net.netfilter.nf_conntrack_max=524288` on EC2 hosts; use RDS Proxy to reduce connection churn; VPC endpoint for RDS in same VPC |
| RDS host kernel panic triggers unplanned failover | CloudWatch `FailedSQLServerAgentJobsCount` or Aurora `ServerlessDatabaseCapacity` drops to 0; multi-AZ failover event in `aws rds describe-events` | Underlying EC2 host kernel panic; AWS triggers automatic Multi-AZ failover | 60-120 second outage during failover; all active connections dropped | Ensure application uses RDS DNS endpoint (not IP); set connection retry logic; confirm failover completed: `aws rds describe-db-instances --db-instance-identifier <id> \| grep MultiAZ` |
| NUMA memory imbalance slowing InnoDB/PostgreSQL buffer pool operations | Enhanced Monitoring shows high `os.memory.dirty`; query latency elevated despite low CPU; buffer pool hit rate low | RDS instance on multi-socket host with uneven NUMA allocation in buffer pool | Sequential scan and buffer pool operations 2-3x slower; affects large analytical queries | Use Performance Insights to identify slow queries: `aws pi get-resource-metrics --service-type RDS --identifier db:<id>`; switch to memory-optimized instance (db.r6g) |

## Deployment Pipeline & GitOps Failure Patterns
**Minimum cross-cutting cases to evaluate here:** image pull failure (rate limit or auth), Helm drift, ArgoCD sync stuck, PodDisruptionBudget-blocked rollout, blue-green cutover failure, and ConfigMap or Secret drift.

| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Flyway/Liquibase migration fails on RDS with lock timeout | CI pipeline shows migration exit code 1; `psql -c "SELECT * FROM pg_locks l JOIN pg_stat_activity a ON l.pid=a.pid WHERE NOT granted"` shows blocked migration | `aws rds describe-db-log-files --db-instance-identifier <id>`; `aws rds download-db-log-file-portion --log-file-name error/postgresql.log --db-instance-identifier <id>` | `psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query LIKE 'ALTER TABLE%'"`; re-run migration in maintenance window | Run migrations during low-traffic window; use `lock_timeout=5s` in migration connection string; validate with `--dry-run` |
| Terraform RDS parameter group change requires reboot not applied | `aws rds describe-pending-maintenance-actions --resource-identifier <arn>` shows pending parameter change; application experiencing unexpected behavior | `aws rds describe-db-instances --db-instance-identifier <id> --query 'DBInstances[].PendingModifiedValues'` | `aws rds reboot-db-instance --db-instance-identifier <id>` during maintenance window | Use `apply_immediately=false` in Terraform; schedule reboot via maintenance window; add reboot step to deployment pipeline for parameter changes |
| Terraform state drift after manual RDS snapshot restore | Terraform plan shows destructive changes; `aws rds describe-db-instances` shows different `DBInstanceClass` than state | `terraform plan -out=plan.tfplan`; `terraform show plan.tfplan \| grep -E "destroy\|replace"` | `terraform import aws_db_instance.<name> <db-id>`; reconcile state before applying | Never manually restore snapshots to same identifier managed by Terraform; use separate Terraform resource for restored instance |
| ArgoCD sync applying outdated RDS secret version to application pods | App pods connecting with wrong DB password after Secrets Manager rotation; `kubectl logs <pod> \| grep "Access denied"` | `kubectl get secret <secret> -o yaml \| grep password \| base64 -d`; compare with `aws secretsmanager get-secret-value --secret-id <arn>` | `kubectl rollout restart deployment/<app>`; ensure External Secrets Operator refreshes: `kubectl annotate externalsecret <es> force-sync=$(date +%s)` | Use External Secrets Operator with `refreshInterval: 1h`; never hardcode DB credentials in K8s secrets |
| PodDisruptionBudget blocks rolling update of DB migration job | Kubernetes migration job Pod stuck pending; previous migration pod not evicted | `kubectl get pdb -n <ns>`; `kubectl describe job <migration-job> -n <ns>` | `kubectl delete pod <old-migration-pod>`; verify migration idempotency before retry | Use Kubernetes Jobs not Deployments for migrations; set `activeDeadlineSeconds`; mark PDB to exclude migration job pods |
| Blue-green RDS cutover leaves connection strings pointing to old instance | Application still writing to blue DB after green promoted; `aws rds describe-db-instances` shows both active | `aws rds describe-db-instances --query 'DBInstances[?DBInstanceStatus==\`available\`].[DBInstanceIdentifier,Endpoint.Address]'` | `aws rds modify-db-instance --db-instance-identifier <old> --new-db-instance-identifier <old>-deprecated`; force DNS flush | Use RDS custom endpoint or Route 53 CNAME; never embed RDS endpoint directly in app config |
| Parameter group change causes unexpected `max_connections` reduction after apply | Application seeing `FATAL: sorry, too many clients already` after parameter group update | `aws rds describe-db-parameters --db-parameter-group-name <pg> --query 'Parameters[?ParameterName==\`max_connections\`]'` | `aws rds modify-db-parameter-group --parameters "ParameterName=max_connections,ParameterValue=500,ApplyMethod=pending-reboot"`; reboot in window | Always test parameter changes on non-prod; check `max_connections` formula for instance class before applying |
| Feature branch database migration not cleaned up blocks main branch deploy | Stale migration version in `flyway_schema_history` conflicts with main branch migration | `psql -c "SELECT * FROM flyway_schema_history ORDER BY installed_on DESC LIMIT 10"`; `aws rds describe-db-snapshots --db-instance-identifier <dev-id>` | `psql -c "DELETE FROM flyway_schema_history WHERE version='<branch-ver>'"` on dev DB; restore from snapshot pre-branch | Enforce branch migration prefixes; automate dev DB restore to main snapshot before PR merge |

## Service Mesh & API Gateway Edge Cases
**Minimum cross-cutting cases to evaluate here:** circuit breaker false positives, rate limiting on legitimate traffic, stale service discovery endpoints, mTLS rotation interruption, retry storm amplification, gRPC keepalive or max-message failures, and trace context loss.

| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive disconnects healthy RDS Proxy due to slow query | Istio/app-level circuit breaker opens on RDS Proxy; healthy queries blocked | `aws rds describe-db-proxy-target-groups --db-proxy-name <proxy>`; check `ConnectionPoolConfig`; CloudWatch `ProxyDatabaseConnectionsBorrowLatency` | Read/write traffic blocked despite DB being healthy; false circuit open | Tune circuit breaker slow-call threshold above p99 query latency; add separate circuit breakers for read vs write paths |
| API Gateway rate limit throttling DB-backed Lambda at burst causing data inconsistency | 429 responses interrupt multi-step DB transactions; partial writes committed | `aws apigateway get-stage --rest-api-id <id> --stage-name prod \| grep throttling`; `aws rds describe-db-log-files` for partial transaction logs | Orphaned DB records; state machine inconsistency | Implement idempotency keys in DB; use SQS queue in front of DB-write Lambda to decouple rate limiting from transactions |
| Stale RDS Proxy endpoint in service discovery after failover | Service mesh sending traffic to pre-failover RDS Proxy writer endpoint | `aws rds describe-db-proxy-endpoints --db-proxy-name <proxy>`; DNS TTL check: `dig +short <rds-proxy-endpoint>` | Writes going to read-only replica; `ERROR: cannot execute INSERT in a read-only transaction` | Flush DNS cache; use RDS Proxy endpoint (not RDS direct) which handles failover transparently; set DNS TTL ≤5s for RDS endpoints |
| mTLS rotation between application and RDS Proxy breaks all DB connections | `aws rds describe-db-proxies --db-proxy-name <proxy>` shows `Status: modifying`; app logs `SSL connection has been closed unexpectedly` | RDS Proxy TLS certificate rotation; application pinned to old certificate fingerprint | All DB connections dropped during rotation; full service outage | Remove certificate pinning; trust AWS CA bundle; `aws rds describe-certificates`; `aws rds modify-db-instance --ca-certificate-identifier rds-ca-rsa2048-g1` |
| Retry storm from connection pool hitting RDS during failover amplifies lag | Application retry logic floods new primary with connections immediately after failover; `SHOW PROCESSLIST` shows hundreds of connections in Sleep | Multi-AZ failover + connection pool reconnect storm; pool size × retry count = connection flood | New primary overwhelmed; `max_connections` hit immediately; extended outage | Implement exponential backoff + jitter in connection pool; use RDS Proxy to absorb reconnection storm; set `hikari.initializationFailTimeout=-1` |
| gRPC streaming query via Envoy proxy times out on long-running RDS analytical query | gRPC stream returns `DEADLINE_EXCEEDED` after 60s; RDS query still running | Envoy default gRPC timeout 60s; long OLAP queries exceed timeout; `aws rds describe-db-instances --query 'DBInstances[].MultiAZ'` (Aurora Parallel Query disabled) | Long-running analytical gRPC calls always fail; workaround needed | Set per-route gRPC timeout in Envoy VirtualService; enable Aurora Parallel Query: `aws rds modify-db-cluster --enable-global-write-forwarding`; offload analytics to Aurora Serverless v2 |
| X-Ray trace gap between API call and RDS query hides slow DB operations | X-Ray service map shows API node but no RDS node; cannot diagnose DB latency | `aws xray get-service-graph`; check if `aws_xray_sdk` `patch(['psycopg2'])` or `mysql.connector` patching enabled in Lambda/ECS | DB query latency invisible in traces; wrong optimization priorities | Enable RDS X-Ray integration in SDK; use `EXPLAIN ANALYZE` + Performance Insights together: `aws pi get-resource-metrics --service-type RDS --identifier db:<id> --metric-queries '[{"Metric":"db.load.avg"}]'` |
| ALB health check using DB connection causes connection pool exhaustion | Each ALB health check opens new DB connection; health check interval too short | `aws elbv2 describe-target-group-attributes --target-group-arn <arn>`; `SHOW STATUS LIKE 'Threads_connected'` on MySQL | DB `max_connections` consumed by health checks; app connections throttled | Change health check path to in-memory endpoint (not DB-connected); `aws elbv2 modify-target-group --health-check-path /ping`; never use DB-connected health check paths |
