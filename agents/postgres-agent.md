---
name: postgres-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-postgres-agent
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
# PostgreSQL SRE Agent

## Role
This agent owns all operational aspects of self-managed and cloud-managed PostgreSQL deployments (RDS, CloudSQL, Supabase, Aurora). It monitors replication health, connection pool saturation, autovacuum behavior, bloat accumulation, WAL archiving, and lock contention. It responds to incidents across the full stack — from individual query regressions caught in pg_stat_statements to cluster-level failovers — and provides data-driven capacity guidance to prevent the most common PostgreSQL failure modes before they become outages.

## Architecture Overview
A typical production PostgreSQL topology consists of a primary write node and one or more hot-standby replicas connected via streaming replication. A connection pooler (pgBouncer in transaction mode) sits in front of the primary to multiplex thousands of application connections onto a small number of server connections. Read replicas service analytical queries and reporting. WAL archiving ships write-ahead log segments to object storage (S3 / GCS) for point-in-time recovery. A monitoring stack (Prometheus `postgres_exporter`, Grafana) scrapes system and database-level metrics. On managed platforms the topology is the same but failover, patching, and storage expansion are handled by the cloud provider.

```
App Tier
  └── pgBouncer (connection pool, port 6432)
        ├── Primary PG (port 5432)  ──WAL──► S3/GCS (PITR archive)
        │       └── streaming replication
        ├── Replica 1 (hot standby, read-only)
        └── Replica 2 (hot standby, read-only)
```

## Key Metrics to Monitor

| Metric | Warning Threshold | Critical Threshold | Notes |
|--------|------------------|--------------------|-------|
| `pg_stat_activity` active connections | > 80% of `max_connections` | > 95% | Count only non-idle states |
| Replication lag (`pg_replication_slots` / `pg_stat_replication`) | > 30 s | > 5 min | Monitor `write_lag`, `flush_lag`, `replay_lag` |
| Transaction wraparound (`age(datfrozenxid)`) | > 1.5 B XID | > 1.8 B XID | Autovacuum FREEZE must run before 2.1 B |
| Table bloat (dead tuple ratio) | > 20% | > 40% | Per-table `n_dead_tup / n_live_tup` |
| Cache hit ratio (`blks_hit / (blks_hit + blks_read)`) | < 95% | < 90% | Low ratio = excessive disk I/O |
| Lock wait time (`pg_stat_activity wait_event_type = Lock`) | > 5 s | > 30 s | Long waits indicate contention |
| Autovacuum workers running | = `autovacuum_max_workers` for > 2 min | All workers stuck for > 5 min | Check `pg_stat_activity` for autovacuum PIDs |
| WAL archive queue depth (`pg_stat_archiver` `failed_count`) | > 0 | > 5 failures in 1 min | Failed archiving blocks PITR recovery |
| Query P99 latency (from `pg_stat_statements`) | > 500 ms | > 2 s | Baseline per workload |
| Checkpoint write time (`checkpoint_write_time`) | > 30 s | > 60 s | Indicates I/O saturation |

## Alert Runbooks

### Alert: ReplicationLagCritical
**Condition:** `pg_replication_lag_seconds > 300` for any replica for > 2 min
**Triage:**
1. Check the replication slot lag on the primary: `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS lag FROM pg_replication_slots;`
2. Identify if the replica is applying WAL or stalled: `SELECT pid, state, sent_lsn, write_lsn, flush_lsn, replay_lsn, write_lag, flush_lag, replay_lag FROM pg_stat_replication;`
3. Check replica OS-level disk I/O: `iostat -xz 1 5` — if write throughput is saturated the replica is falling behind on applying WAL.
4. Check for long-running queries on the replica blocking HOT standby apply: `SELECT pid, now() - query_start AS duration, query FROM pg_stat_activity WHERE state != 'idle' ORDER BY duration DESC;`
### Alert: ConnectionPoolExhaustion
**Condition:** `pgbouncer_pools_sv_maxwait_us > 5000000` (any pool waited > 5 s) **OR** `pgbouncer_pools_cl_waiting > 50` (clients queued)
**Triage:**
1. Connect to pgBouncer admin: `psql -h 127.0.0.1 -p 6432 -U pgbouncer pgbouncer -c "SHOW POOLS;"` — look for `cl_waiting > 0`.
2. Check server connection utilization: `psql ... -c "SHOW STATS_TOTALS;"` and compare `total_query_count` trend.
3. Identify long-held transactions on PostgreSQL: `SELECT pid, usename, application_name, state, now() - xact_start AS xact_duration, query FROM pg_stat_activity WHERE state != 'idle' AND xact_start IS NOT NULL ORDER BY xact_duration DESC LIMIT 20;`
4. Check pgBouncer `pool_mode` — `session` mode is the most likely cause of pool exhaustion.
### Alert: TransactionIDWraparoundRisk
**Condition:** `age(datfrozenxid) > 1500000000` on any database
**Triage:**
1. Check all databases: `SELECT datname, age(datfrozenxid) AS xid_age, datfrozenxid FROM pg_database ORDER BY xid_age DESC;`
2. Find tables that need FREEZE most urgently: `SELECT schemaname, relname, age(relfrozenxid) AS table_xid_age FROM pg_class JOIN pg_namespace ON relnamespace = pg_namespace.oid WHERE relkind = 'r' ORDER BY table_xid_age DESC LIMIT 20;`
3. Confirm autovacuum is running: `SELECT pid, query, now() - query_start AS runtime FROM pg_stat_activity WHERE query LIKE 'autovacuum:%' ORDER BY runtime DESC;`
4. Check `pg_stat_user_tables` for tables where autovacuum is being blocked: `SELECT schemaname, relname, last_autovacuum, last_autoanalyze, n_dead_tup FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 20;`
### Alert: WALArchivingFailing
**Condition:** `pg_stat_archiver.failed_count` increasing for > 5 min
**Triage:**
1. Check archiver status: `SELECT archived_count, failed_count, last_archived_wal, last_failed_wal, last_failed_time FROM pg_stat_archiver;`
2. Check PostgreSQL logs for archiving errors: `tail -f /var/log/postgresql/postgresql-*.log | grep -i archive`
3. Verify archive destination connectivity and permissions (S3 bucket policy, IAM role, network).
4. Check available disk on `pg_wal` directory: `df -h $(pg_lsclusters | awk '{print $6}')/pg_wal` — if full, archiving backpressure builds.
## Common Issues & Troubleshooting

### Issue: Slow Queries After Autovacuum or Statistics Update
**Symptoms:** Specific queries suddenly take 10-100x longer after a maintenance event; `EXPLAIN` shows wrong row estimates.
**Diagnosis:** `SELECT schemaname, relname, last_analyze, last_autoanalyze, n_live_tup, n_dead_tup FROM pg_stat_user_tables WHERE relname = '<table>';`
### Issue: Lock Contention / Blocked Queries
**Symptoms:** Application timeouts; `pg_stat_activity` shows many sessions in `Lock` wait state.
**Diagnosis:**
```sql
SELECT blocked.pid, blocked.query, blocking.pid AS blocking_pid, blocking.query AS blocking_query
FROM pg_stat_activity blocked
JOIN pg_stat_activity blocking ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))
WHERE cardinality(pg_blocking_pids(blocked.pid)) > 0;
```
### Issue: Table / Index Bloat
**Symptoms:** Table size grows far beyond `pg_relation_size`; sequential scans are slow; storage alarms fire.
**Diagnosis:** `SELECT schemaname, relname, pg_size_pretty(pg_total_relation_size(relid)) AS total, pg_size_pretty(pg_relation_size(relid)) AS table, round(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_pct FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 20;`
### Issue: pgBouncer "no more connections allowed"
**Symptoms:** Applications receive `ERROR: no more connections allowed (max_client_conn)`.
**Diagnosis:** `psql -h 127.0.0.1 -p 6432 -U pgbouncer pgbouncer -c "SHOW CONFIG;" | grep max_client_conn`
### Issue: Disk Full on pg_wal Directory
**Symptoms:** PostgreSQL stops accepting writes; logs show `could not write to file "pg_wal/..."`.
**Diagnosis:** `du -sh /var/lib/postgresql/*/main/pg_wal/` and `ls -lt /var/lib/postgresql/*/main/pg_wal/ | head -20`
### Issue: Out-of-Memory / OOM Killed Postgres Backend
**Symptoms:** Backend PIDs disappear from `pg_stat_activity`; `dmesg` shows OOM kills.
**Diagnosis:** `dmesg -T | grep -i oom | tail -20` and `SELECT pid, datname, usename, application_name, state, query FROM pg_stat_activity WHERE state = 'active' ORDER BY pid;`
## Key Dependencies

- **pgBouncer**: Connection multiplexing. If pgBouncer crashes, all application connections to PostgreSQL fail immediately. Monitor `pgbouncer_up` and configure `server_check_query` heartbeats.
- **WAL Archive Storage (S3/GCS)**: Required for PITR. If unreachable for extended time, `pg_wal` fills and primary can become read-only.
- **Streaming Replicas**: Read replicas absorb read traffic. Loss of all replicas forces all queries to primary, risking overload.
- **DNS / Service Discovery**: Applications resolve the primary endpoint via DNS or a VIP. DNS TTL misconfiguration after a failover causes stale connection routing.
- **OS Storage (IOPS/throughput)**: PostgreSQL is write-heavy; IOPS exhaustion causes checkpoint stalls, WAL write delays, and cascading query timeouts.
- **Authentication (LDAP/PAM/pg_hba.conf)**: Misconfigured `pg_hba.conf` after an upgrade or migration blocks all connections.

## Cross-Service Failure Chains

**Chain 1: Long-Running ETL → Replication Lag → Replica Read Timeout**
An ETL job holds an open transaction for 45 minutes. Streaming replication applies WAL but replica hot-standby conflict resolution (`hot_standby_feedback = off`) causes the replica to cancel queries. Monitoring dashboards (Grafana reading replica) show blank panels. Application read replicas fall behind 30 minutes and are removed from the load balancer pool. All read traffic hits the primary. Primary IOPS are saturated; write latency climbs past SLO thresholds. Alert fires on both replication lag and primary P99 latency.

**Chain 2: pgBouncer OOM → Connection Spike → Primary Max Connections**
A memory leak in pgBouncer (or misconfigured `server_idle_timeout`) causes pgBouncer to be OOM-killed. Applications fall back to direct PostgreSQL connections. Within seconds `max_connections` (typically 100-200) is exhausted. PostgreSQL rejects new connections with `FATAL: remaining connection slots are reserved`. Web tier returns 500 errors. Recovery requires restarting pgBouncer and gracefully shedding direct connections.

**Chain 3: Replication Slot Accumulation → pg_wal Disk Full → Write Outage**
A downstream consumer (logical replication subscriber or Debezium CDC connector) goes offline but does not drop its replication slot. The primary retains all WAL since the slot's `restart_lsn`. Over hours the `pg_wal` directory fills the disk. PostgreSQL cannot write new WAL segments, halts all writes, and enters read-only mode. Recovery requires identifying and dropping the stale slot, which may cause data loss on the subscriber.

## Partial Failure Patterns

- **Reads succeed, writes timeout**: Primary disk I/O saturated; checkpoints are delayed; WAL writes block. Cache hit ratio remains high (reads from shared_buffers) but write latency spikes.
- **Some queries fast, others hang**: Lock contention on a hot table. Most queries proceed normally; queries touching the contended table pile up behind a single blocker.
- **Connection errors only from new clients**: pgBouncer pool exhausted. Existing pooled connections work; new connections queue or fail. Application logs show intermittent `connection refused` only for new sessions.
- **Analytics slow, OLTP unaffected**: Autovacuum and bloat accumulation on reporting tables only. OLTP tables are fine; large sequential scans on bloated tables are slow.
- **Replica reads stale**: Replication lag > application read-after-write tolerance. Application shows stale data without errors; users see rows that appear to have reverted.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|---------|
| Point-lookup by primary key (PK index scan) | < 1 ms | 1–10 ms | > 10 ms |
| Single-row INSERT (with WAL sync) | < 2 ms | 2–20 ms | > 20 ms |
| Bulk INSERT 10K rows (COPY) | < 500 ms | 500 ms–2 s | > 2 s |
| Sequential scan 10M-row table | < 5 s | 5–30 s | > 30 s |
| VACUUM on 100M-row table | < 5 min | 5–20 min | > 20 min |
| Connection acquisition from pgBouncer | < 5 ms | 5–100 ms | > 100 ms |
| WAL flush (synchronous_commit = on) | < 10 ms | 10–50 ms | > 50 ms |
| Streaming replication apply lag | < 10 s | 10 s–2 min | > 5 min |

## Capacity Planning Indicators

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| `pg_database_size(datname)` | > 10% growth/week | Evaluate storage tier upgrade or archival | 2–4 weeks |
| `max_connections` utilization | Avg > 60% | Add pgBouncer node or increase `max_connections` (requires restart) | 1 week |
| `age(datfrozenxid)` trajectory | Approaching 1.5 B XID/month | Schedule VACUUM FREEZE window | 2 weeks |
| `n_dead_tup` growth rate (top 10 tables) | Doubling week-over-week | Tune `autovacuum_vacuum_scale_factor`, add index | 1 week |
| WAL archive storage | > 50% full and growing | Increase retention policy or move to cheaper tier | 1 week |
| IOPS utilization | Avg > 70% provisioned IOPS | Scale up storage IOPS or migrate to higher-tier instance | 2 weeks |
| Checkpoint completion ratio | `checkpoint_completion_target` frequently exceeded | Increase `shared_buffers`, tune `checkpoint_timeout` | 1–2 weeks |
| `pg_stat_statements` total execution time growth | P99 growing > 20%/week | Query review, index additions, query plan pinning | 1 week |

## Diagnostic Cheatsheet

```bash
# Check all active connections and their state
psql -c "SELECT datname, state, count(*) FROM pg_stat_activity GROUP BY datname, state ORDER BY count DESC;"

# Show top 10 slowest queries by total execution time
psql -c "SELECT round(total_exec_time::numeric, 2) AS total_ms, calls, round(mean_exec_time::numeric, 2) AS mean_ms, query FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;"

# Show replication lag for all replicas
psql -c "SELECT application_name, state, write_lag, flush_lag, replay_lag FROM pg_stat_replication;"

# Show tables with the most dead tuples (bloat candidates)
psql -c "SELECT schemaname, relname, n_dead_tup, n_live_tup, round(100.0*n_dead_tup/NULLIF(n_live_tup+n_dead_tup,0),1) AS dead_pct FROM pg_stat_user_tables WHERE n_dead_tup > 10000 ORDER BY n_dead_tup DESC LIMIT 15;"

# Show transaction ID age per database (wraparound risk)
psql -c "SELECT datname, age(datfrozenxid) AS xid_age FROM pg_database ORDER BY xid_age DESC;"

# Show current lock waits (who is blocked and by whom)
psql -c "SELECT blocked.pid, blocked.query, blocking.pid AS blocking_pid, blocking.query FROM pg_stat_activity blocked JOIN pg_stat_activity blocking ON blocking.pid = ANY(pg_blocking_pids(blocked.pid));"

# Show replication slots and WAL retained
psql -c "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS lag FROM pg_replication_slots;"

# Show largest tables including indexes
psql -c "SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total_size FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 20;"

# Show autovacuum activity
psql -c "SELECT pid, now()-query_start AS runtime, query FROM pg_stat_activity WHERE query LIKE 'autovacuum:%' ORDER BY runtime DESC;"

# Check WAL archiver status
psql -c "SELECT archived_count, failed_count, last_archived_wal, last_failed_wal, last_failed_time FROM pg_stat_archiver;"
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Write Availability | 99.95% | `pg_isready` every 30 s; primary accepts writes | 21.6 min/month | Burn rate > 14.4× (alert if 5 min window shows > 5% error) |
| Read Availability (replica) | 99.9% | Replica responds to `SELECT 1` within 1 s | 43.8 min/month | Burn rate > 6× |
| Write P99 Latency ≤ 20 ms | 99% of 5-min windows | `pg_stat_statements` `mean_exec_time` for INSERT/UPDATE | 4.3 hr/month | Burn rate > 3× (5-min window where P99 > 20 ms) |
| Replication Lag ≤ 30 s | 99.5% | `replay_lag` on all replicas | 3.6 hr/month | Burn rate > 6× |

## Configuration Audit Checklist

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| `max_connections` appropriate | `psql -c "SHOW max_connections;"` | ≤ 200 when pgBouncer is in use |
| `shared_buffers` is 25% of RAM | `psql -c "SHOW shared_buffers;"` | 25% of total RAM |
| `wal_level` for replication | `psql -c "SHOW wal_level;"` | `replica` or `logical` |
| `archive_mode` enabled | `psql -c "SHOW archive_mode;"` | `on` or `always` |
| `synchronous_commit` set correctly | `psql -c "SHOW synchronous_commit;"` | `on` for financial data; `local` acceptable for analytics |
| `log_min_duration_statement` captures slow queries | `psql -c "SHOW log_min_duration_statement;"` | 500–1000 ms |
| `autovacuum` enabled | `psql -c "SHOW autovacuum;"` | `on` |
| `pg_hba.conf` — no `trust` entries | `grep trust /etc/postgresql/*/main/pg_hba.conf` | No `trust` except `local` loopback |
| `ssl` enabled | `psql -c "SHOW ssl;"` | `on` |
| Stale replication slots | `psql -c "SELECT slot_name, active FROM pg_replication_slots WHERE active = false;"` | No inactive slots |

## Log Pattern Library

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `FATAL: remaining connection slots are reserved for non-replication superuser connections` | Critical | `max_connections` exhausted | Kill idle connections, scale pgBouncer |
| `ERROR: deadlock detected` | High | Two transactions hold conflicting locks | Review application transaction order; auto-resolved by PG |
| `PANIC: could not write to file "pg_wal/..."` | Critical | Disk full on WAL directory | Drop stale replication slots, expand disk |
| `LOG: autovacuum: found X removable versions in table` | Info | Normal autovacuum activity | No action; monitor frequency |
| `ERROR: canceling statement due to conflict with recovery` | Medium | Hot standby query conflicts with replica WAL apply | Enable `hot_standby_feedback = on` on replica or reduce query duration |
| `WARNING: out of shared memory` | High | Too many lock objects (`max_locks_per_transaction` too low) | Increase `max_locks_per_transaction`, restart required |
| `LOG: checkpoints are occurring too frequently` | Medium | `max_wal_size` too small or write storm | Increase `max_wal_size`, tune `checkpoint_completion_target` |
| `FATAL: password authentication failed for user` | High | Bad credentials or pg_hba.conf mismatch | Verify credentials, check `pg_hba.conf` |
| `ERROR: could not serialize access due to concurrent update` | Medium | Serializable isolation conflict | Application must retry the transaction |
| `LOG: temporary file: path "base/pgsql_tmp/pgsql_tmp*.0", size NNNN` | Medium | Query spilling to disk (insufficient `work_mem`) | Increase `work_mem` for session or tune query |
| `ERROR: invalid page in block N of relation base/...` | Critical | Data corruption or storage error | Initiate PITR restore from backup immediately |
| `LOG: archive command failed with exit code 1` | High | WAL archiving broken | Check archive command, S3 permissions, network |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `08006` Connection Failure | Backend process died or network failure | Application gets connection error | Reconnect; check PostgreSQL process list |
| `53300` Too Many Connections | `max_connections` reached | New connections refused | Kill idle connections, scale pgBouncer |
| `40P01` Deadlock Detected | Circular lock dependency | Transaction rolled back automatically | Application retry logic required |
| `40001` Serialization Failure | Concurrent update conflict (SERIALIZABLE) | Transaction aborted | Retry transaction |
| `57P04` Database Dropped | `DROP DATABASE` while connections active | All connections to DB fail | Restore from backup or re-create |
| `22P02` Invalid Text Representation | Type cast failure | Individual query fails | Fix application-side data formatting |
| `XX000` Internal Error | PG assertion or storage corruption | Potentially severe | Check logs, restore from backup if corruption |
| `idle in transaction` (state) | Session started transaction, went idle | Holds locks, blocks autovacuum | Set `idle_in_transaction_session_timeout` |
| `idle` (state, large count) | Connection pool leak | Exhausting `max_connections` | Audit application connection handling |
| `waiting` (lock) | Backend waiting for row/table lock | Query queued behind blocker | Identify and terminate root blocker |
| `autovacuum worker` (query) | Autovacuum processing table | Normal; abnormal if stuck for hours | Check for long transactions blocking vacuum |
| `WAL sender` (application_name) | Streaming replication active | Normal; check `sent_lsn` vs `replay_lsn` | Monitor lag metrics |

## Known Failure Signatures

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Connection Tsunami | `pg_stat_activity` count spikes 0→max in < 30 s | `FATAL: remaining connection slots` | ConnectionPoolExhaustion | Application restart or pgBouncer crash causes direct connection flood | Restart pgBouncer, add connection rate limiting at app layer |
| Autovacuum Starvation | `n_dead_tup` grows unbounded; `last_autovacuum` stale for > 1 hr | `autovacuum: found 0 removable versions` | BloatWarning | Long-running transaction prevents dead tuple removal | Terminate long transaction; consider `vacuum_defer_cleanup_age` |
| XID Wraparound Race | `age(datfrozenxid)` > 2 B on any DB | `WARNING: database "X" must be vacuumed within N transactions` | XIDWraparoundRisk | Autovacuum freeze not keeping up | Emergency `VACUUM FREEZE` on all tables in affected DB |
| WAL Slot Leak | `pg_wal` directory grows > 100 GB | `archive command failed` | WALDiskUsage | Inactive replication slot retaining WAL | Drop inactive slots: `SELECT pg_drop_replication_slot(...)` |
| Lock Cascade | `pg_stat_activity` shows tree of blocked PIDs | `ERROR: deadlock detected` | LockContention | Single long transaction holding table lock (often DDL) | Terminate root blocker; review DDL deployment process |
| Checkpoint Storm | `checkpoint_write_time` > 30 s repeatedly | `LOG: checkpoints are occurring too frequently` | CheckpointStorm | Burst write workload exceeding `max_wal_size` | Increase `max_wal_size`; throttle batch write jobs |
| Stats Staleness | Specific queries regress 50×+ after VACUUM | None (silent) | QueryRegression | ANALYZE not run after large data change | `ANALYZE <table>` or increase `default_statistics_target` |
| Replica Apply Conflict | Replica lag flatlines; apply stops | `ERROR: canceling statement due to conflict` | ReplicationLagCritical | Hot standby query blocking WAL apply | Enable `hot_standby_feedback`; reduce long reads on replica |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `FATAL: remaining connection slots are reserved for non-replication superuser connections` | psycopg2, asyncpg, node-postgres | `max_connections` fully consumed; no slots left | `SELECT count(*) FROM pg_stat_activity;` vs `SHOW max_connections;` | Reduce pool size; add pgBouncer; increase `max_connections` |
| `ERROR: deadlock detected` | All drivers | Two transactions holding locks the other needs; PostgreSQL chose one as victim | `SELECT * FROM pg_stat_activity WHERE wait_event_type = 'Lock';` | Application must retry on deadlock; fix lock acquisition order |
| `ERROR: canceling statement due to conflict with recovery` | asyncpg, JDBC | Hot-standby replica received conflicting WAL; long read on replica blocked apply | Check replica: `SELECT * FROM pg_stat_activity WHERE wait_event = 'RecoveryConflictDatabase';` | Enable `hot_standby_feedback = on`; reduce query duration on replicas |
| `SSL connection has been closed unexpectedly` | libpq-based drivers | Connection dropped at pgBouncer or PG; TCP keepalive expired or server restart | Check `pg_log` for `connection reset` messages | Set `tcp_keepalives_idle = 60` in connection string; add reconnect logic |
| `ERROR: could not serialize access due to concurrent update` | All drivers | Serializable isolation conflict; transaction must be retried | Only occurs with `ISOLATION LEVEL SERIALIZABLE`; check `pg_stat_user_tables.n_xact_rollback` trend | Implement retry loop with exponential backoff on serialization errors |
| `ERROR: value too long for type character varying(N)` | ORM (SQLAlchemy, ActiveRecord) | Application sending data exceeding column constraint | Check column definition: `SELECT character_maximum_length FROM information_schema.columns WHERE table_name = '<table>';` | Truncate at application layer; increase column size via migration |
| `ERROR: duplicate key value violates unique constraint "<constraint>"` | All ORMs | Race condition on INSERT; concurrent transactions inserting same unique key | `EXPLAIN ANALYZE` the INSERT; check `pg_stat_user_tables.n_xact_rollback` | Use `INSERT ... ON CONFLICT DO UPDATE` (upsert) |
| `ERROR: relation "<table>" does not exist` | All drivers | Wrong schema in search_path; table in different schema; migration not run | `SHOW search_path;` on the connection; `\dt` in psql | Set explicit `search_path` in connection string; verify migration status |
| `statement timeout` / `QueryCanceled` | psycopg2, asyncpg, JDBC | `statement_timeout` GUC exceeded; long-running query killed | `SHOW statement_timeout;`; check `pg_stat_activity` for cancelled PIDs | Tune `statement_timeout` per role; optimize slow queries |
| `FATAL: password authentication failed for user "<user>"` | All drivers | Wrong password; pg_hba.conf mismatch; user does not exist | `SELECT rolname FROM pg_roles WHERE rolname = '<user>';`; check `pg_hba.conf` | Verify credentials; check pg_hba.conf md5 vs scram-sha-256 |
| `ERROR: out of shared memory` | All drivers during DDL | `max_locks_per_transaction` exceeded during large migration | `SELECT count(*) FROM pg_locks;`; count distinct objects locked | Increase `max_locks_per_transaction`; split migration into smaller steps |
| Connection timeout (no error, just hang) | All connection pool libs | `max_connections` reached but pool is waiting; PG unresponsive | `psql -h <host> -c "SELECT 1"` with a short timeout to confirm reachability | Check pgBouncer `cl_waiting`; check PG availability; scale up pool |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Table bloat accumulation | Dead tuple ratio trending up week-over-week; autovacuum running more frequently | `SELECT relname, n_dead_tup, n_live_tup, round(n_dead_tup*100.0/(n_live_tup+n_dead_tup),1) AS dead_pct FROM pg_stat_user_tables WHERE n_live_tup > 10000 ORDER BY dead_pct DESC LIMIT 10;` | 2–4 weeks | Schedule manual VACUUM; review autovacuum scale factor settings |
| Transaction ID wraparound approach | `age(datfrozenxid)` growing beyond 1.5 B | `SELECT datname, age(datfrozenxid) AS xid_age FROM pg_database ORDER BY xid_age DESC;` | 1–2 weeks | Force `VACUUM FREEZE` on oldest-XID tables; monitor autovacuum_freeze_max_age |
| Index bloat from high-churn tables | Index size diverging from table size; index scans getting slower over months | `SELECT schemaname, tablename, indexname, pg_size_pretty(pg_relation_size(indexrelid)) FROM pg_stat_user_indexes ORDER BY pg_relation_size(indexrelid) DESC LIMIT 10;` | 2–4 weeks | `REINDEX CONCURRENTLY` during low traffic |
| Connection count creep | Peak connections growing 10% per week without traffic increase | Daily max from `pg_stat_activity` count; compare 7-day rolling max | 1–2 weeks | Audit application connection pool settings; look for connection leaks |
| Cache hit ratio decline | `blks_hit / (blks_hit + blks_read)` dropping from 99% toward 95% over weeks | `SELECT sum(blks_hit)::float / (sum(blks_hit) + sum(blks_read)) AS cache_ratio FROM pg_stat_database WHERE datname != 'template1';` | 1–2 weeks | Add RAM (`shared_buffers` / instance size); archive cold data |
| Replication slot lag growth | Inactive or slow replica consuming WAL; `pg_wal` directory size growing | `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS lag FROM pg_replication_slots;` | 2–3 days | Drop unused replication slots; ensure replica is keeping up |
| Checkpoint frequency increase | `checkpoint_warning` log messages increasing; `checkpoints_req` rising | `SELECT checkpoints_timed, checkpoints_req, checkpoint_write_time FROM pg_stat_bgwriter;` | 3–5 days | Increase `max_wal_size`; check for write-heavy batch jobs |
| Stats staleness causing plan regressions | Specific queries gradually slowing as data skew grows without ANALYZE | `SELECT schemaname, relname, last_analyze, n_mod_since_analyze FROM pg_stat_user_tables WHERE n_mod_since_analyze > 10000 ORDER BY n_mod_since_analyze DESC LIMIT 10;` | Days to weeks | Reduce `default_statistics_target` for key tables; increase autovacuum analyze scale factor |
| pg_wal directory growth | `pg_wal` directory size growing over days due to archiver failure | `SELECT archived_count, failed_count, last_failed_time FROM pg_stat_archiver;` | 1–3 days | Fix WAL archive destination; check S3/GCS credentials; verify `archive_command` |
| Lock wait time creeping up | Mean lock wait duration rising in `pg_stat_activity` over weeks | `SELECT count(*), avg(now()-query_start) FROM pg_stat_activity WHERE wait_event_type = 'Lock';` | 1 week | Identify lock-heavy workloads; review transaction scope; add advisory locks |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# PostgreSQL Full Health Snapshot
# Usage: PG_DSN="postgresql://user:pass@host:5432/db" ./pg-health-snapshot.sh

PG="psql $PG_DSN -tAq"

echo "=== PostgreSQL Health Snapshot: $(date -u) ==="
echo ""
echo "--- Server Version ---"
$PG -c "SELECT version();"

echo ""
echo "--- Connection State Breakdown ---"
$PG -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY state ORDER BY count DESC;"

echo ""
echo "--- Top 5 Long-Running Queries ---"
$PG -c "SELECT pid, round(extract(epoch FROM now()-query_start)) AS sec, LEFT(query,120) FROM pg_stat_activity WHERE state='active' AND query_start IS NOT NULL ORDER BY sec DESC LIMIT 5;"

echo ""
echo "--- Replication Slots Lag ---"
$PG -c "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS lag FROM pg_replication_slots;"

echo ""
echo "--- Transaction ID Age (Wraparound Risk) ---"
$PG -c "SELECT datname, age(datfrozenxid) AS xid_age FROM pg_database ORDER BY xid_age DESC LIMIT 5;"

echo ""
echo "--- Table Bloat (Top 5) ---"
$PG -c "SELECT relname, n_dead_tup, round(n_dead_tup*100.0/NULLIF(n_live_tup+n_dead_tup,0),1) AS dead_pct FROM pg_stat_user_tables WHERE n_live_tup > 1000 ORDER BY dead_pct DESC LIMIT 5;"

echo ""
echo "--- Cache Hit Ratio ---"
$PG -c "SELECT round(sum(blks_hit)*100.0/(sum(blks_hit)+sum(blks_read)),2) AS cache_hit_pct FROM pg_stat_database WHERE datname=current_database();"

echo ""
echo "--- WAL Archiver Status ---"
$PG -c "SELECT archived_count, failed_count, last_archived_time, last_failed_time FROM pg_stat_archiver;"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# PostgreSQL Performance Triage — run when P99 latency has regressed
# Usage: PG_DSN="postgresql://..." ./pg-perf-triage.sh

PG="psql $PG_DSN -tAq"
echo "=== Performance Triage: $(date -u) ==="

echo ""
echo "--- Top 10 Slowest Query Fingerprints (pg_stat_statements) ---"
$PG -c "SELECT round(mean_exec_time::numeric,2) AS mean_ms, round(stddev_exec_time::numeric,2) AS stddev_ms, calls, LEFT(query,140) AS query FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;"

echo ""
echo "--- Queries with High Rows Read:Returned Ratio (potential full scans) ---"
$PG -c "SELECT round(mean_exec_time::numeric,2) AS mean_ms, rows/NULLIF(calls,0) AS avg_rows, LEFT(query,120) AS query FROM pg_stat_statements WHERE calls > 50 ORDER BY (rows/NULLIF(calls,0)) DESC LIMIT 10;"

echo ""
echo "--- Current Lock Waits ---"
$PG -c "SELECT blocked.pid, blocked_activity.query AS blocked_query, blocker.pid AS blocker_pid, blocker_activity.query AS blocker_query FROM pg_catalog.pg_locks blocked JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked.pid JOIN pg_catalog.pg_locks blocker ON blocker.relation = blocked.relation AND blocker.granted AND NOT blocked.granted JOIN pg_catalog.pg_stat_activity blocker_activity ON blocker_activity.pid = blocker.pid LIMIT 10;"

echo ""
echo "--- Checkpoint Stats ---"
$PG -c "SELECT checkpoints_timed, checkpoints_req, checkpoint_write_time, checkpoint_sync_time, buffers_checkpoint FROM pg_stat_bgwriter;"

echo ""
echo "--- Index Usage: Unused Indexes (seq scans >> idx scans) ---"
$PG -c "SELECT schemaname, relname, indexrelname, idx_scan, seq_scan FROM pg_stat_user_indexes JOIN pg_stat_user_tables USING(relname) WHERE idx_scan < 50 AND seq_scan > 500 ORDER BY seq_scan DESC LIMIT 10;"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# PostgreSQL Connection and Resource Audit
# Usage: PG_DSN="postgresql://..." ./pg-connection-audit.sh

PG="psql $PG_DSN -tAq"
echo "=== Connection & Resource Audit: $(date -u) ==="

echo ""
echo "--- Connections by Application and State ---"
$PG -c "SELECT application_name, state, count(*) FROM pg_stat_activity GROUP BY application_name, state ORDER BY count DESC LIMIT 20;"

echo ""
echo "--- Idle-in-Transaction Sessions (>30s) ---"
$PG -c "SELECT pid, usename, application_name, round(extract(epoch FROM now()-xact_start)) AS idle_xact_sec, LEFT(query,100) AS last_query FROM pg_stat_activity WHERE state = 'idle in transaction' AND xact_start < now() - interval '30 seconds' ORDER BY idle_xact_sec DESC;"

echo ""
echo "--- max_connections vs Current Usage ---"
$PG -c "SELECT setting::int AS max_connections FROM pg_settings WHERE name='max_connections';"
$PG -c "SELECT count(*) AS total_connections FROM pg_stat_activity;"

echo ""
echo "--- Database-Level I/O Stats ---"
$PG -c "SELECT datname, blks_read, blks_hit, round(blks_hit*100.0/NULLIF(blks_read+blks_hit,0),1) AS hit_pct, tup_fetched, tup_returned FROM pg_stat_database WHERE datname NOT IN ('template0','template1') ORDER BY blks_read DESC LIMIT 5;"

echo ""
echo "--- Table Size vs Index Size (Top 10 by Total) ---"
$PG -c "SELECT relname, pg_size_pretty(pg_table_size(oid)) AS table_size, pg_size_pretty(pg_indexes_size(oid)) AS idx_size, pg_size_pretty(pg_total_relation_size(oid)) AS total_size FROM pg_class WHERE relkind='r' ORDER BY pg_total_relation_size(oid) DESC LIMIT 10;"

echo ""
echo "--- Autovacuum Running (Currently Active) ---"
$PG -c "SELECT pid, round(extract(epoch FROM now()-query_start)) AS sec, LEFT(query,100) AS query FROM pg_stat_activity WHERE query LIKE 'autovacuum%' ORDER BY sec DESC;"
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Long-running analytics query blocking autovacuum | Table bloat accumulating; autovacuum waiting; xid age creeping up | `SELECT pid, query FROM pg_stat_activity WHERE wait_event = 'autovacuum';` | Kill the analytics query; let autovacuum run | Move analytics to read replica; set `statement_timeout` on analytics role |
| Batch INSERT/UPDATE consuming shared_buffers | Cache hit ratio drops during batch window; OLTP queries slow | `SELECT pid, round(extract(epoch FROM now()-query_start)) AS sec, query FROM pg_stat_activity WHERE query ILIKE '%INSERT%' ORDER BY sec DESC LIMIT 5;` | Throttle batch with `pg_sleep()` between chunks; limit batch size | Use `SET LOCAL synchronous_commit = off` for bulk; schedule batch in off-peak window |
| DDL lock holding exclusive lock (ALTER TABLE) | All queries to the table hang; `pg_stat_activity` shows many `Lock` wait events | `SELECT pid, query FROM pg_stat_activity WHERE wait_event_type = 'Lock';` identify the DDL PID | Terminate DDL if unintended; use `lock_timeout = '3s'` in migration scripts | Set `lock_timeout` before all DDL; use `pg_try_advisory_lock` pattern |
| Heavyweight checkpoint I/O saturating disk | Write latency spikes; `checkpoint_write_time > 30s` | `SELECT checkpoint_write_time, checkpoint_sync_time, buffers_checkpoint FROM pg_stat_bgwriter;` | Set `checkpoint_completion_target = 0.9`; reduce `checkpoint_timeout` | Spread I/O with `checkpoint_completion_target`; use provisioned IOPS storage |
| Connection slot exhaustion by idle pool members | Low active workload but connections near max; new app instances cannot connect | `SELECT application_name, state, count(*) FROM pg_stat_activity WHERE state='idle' GROUP BY application_name, state ORDER BY count DESC;` | Kill idle connections for over-pooled app; lower pool max | Enforce global max pool with pgBouncer; set `idle_in_transaction_session_timeout` |
| Full-table sequential scan on shared table | I/O spikes correlated with specific report queries; other queries slow | `SELECT query, calls, total_exec_time FROM pg_stat_statements WHERE query ILIKE '%seq scan%' OR total_exec_time > 10000 ORDER BY total_exec_time DESC LIMIT 5;` | Cancel the scan if possible; add index | Create index for the column; route reporting to replica |
| VACUUM and OLTP competing for I/O | VACUUM seen in `pg_stat_activity`; autovacuum running > 10 min on same table; write latency elevated | `SELECT relname, n_dead_tup, last_autovacuum, autovacuum_count FROM pg_stat_user_tables WHERE last_autovacuum > now()-interval '30 min' ORDER BY n_dead_tup DESC;` | Lower `autovacuum_vacuum_cost_delay` temporarily to speed up VACUUM; set `vacuum_cost_limit` | Tune autovacuum per-table storage parameters; prevent bloat accumulation |
| Write amplification from unneeded indexes | High write latency on INSERT-heavy tables; excessive B-tree page splits visible in `pg_stat_bgwriter` | `SELECT relname, indexrelname, idx_scan FROM pg_stat_user_indexes WHERE idx_scan < 10 ORDER BY idx_scan;` | Drop unused indexes (test first in staging) | Audit indexes before each release; use `pg_stat_user_indexes` weekly |
| Logical replication producer holding WAL | `pg_wal` directory growing; producers sending to lagging subscribers | `SELECT slot_name, plugin, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS lag FROM pg_replication_slots ORDER BY lag DESC;` | Drop inactive slots; set `max_slot_wal_keep_size` | Set `max_slot_wal_keep_size = 10GB`; alert when any slot exceeds 2 GB |
| XID-heavy workload accelerating wraparound | `age(datfrozenxid)` rising faster than expected; autovacuum_freeze_max_age close to age | `SELECT datname, age(datfrozenxid) FROM pg_database ORDER BY age DESC;` | Manually `VACUUM FREEZE` largest tables | Lower `autovacuum_freeze_max_age`; avoid very-long transactions |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Primary PostgreSQL crash | Streaming standbys detect disconnect → Patroni/Repmgr initiates leader election → 30–60s failover window → all writes fail → read replicas stop receiving WAL → connection pool reports `FATAL: terminating connection` | All read and write operations during failover window; downstream services timeout | `patronictl list` shows primary in `stopped`; `pg_isready -h <primary>` fails; application error rate 100% | Trigger immediate failover: `patronictl failover <cluster> --master <old-primary> --force`; enable read-only mode at app |
| Connection pool exhaustion (PgBouncer pool full) | New connection attempts queue → queue fills → `connection timeout` errors → application threads block waiting → HTTP request timeouts → upstream load balancer health checks fail | All database-backed services; may cascade to API gateway marking backends unhealthy | `psql pgbouncer -c "SHOW POOLS;"` shows `cl_waiting` > 10; application logs `connection timeout`; load balancer reports backends unhealthy | Increase PgBouncer `pool_size` temporarily; kill idle backend connections: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND query_start < now()-interval '10m'` |
| WAL receiver falling behind on replica | Replica lag grows → replica serves increasingly stale reads → application logic depending on freshness breaks → if `hot_standby_feedback=off`, replica cancels queries → cascade of `ERROR: canceling statement due to conflict with recovery` | All reads from the lagging replica; query cancellations cause retry storm | `SELECT replay_lag FROM pg_stat_replication` > 30s; replica logs: `LOG: recovery conflict resolved`; application `ERROR: canceling statement due to conflict` | Enable `hot_standby_feedback = on`; route reads to primary temporarily; reduce write load |
| Bloated table causing autovacuum lock-out | Dead tuples accumulate → xid age approaches `autovacuum_freeze_max_age` → `VACUUM FREEZE` triggered → holds `ShareUpdateExclusiveLock` → long-running queries see lock wait → connection queue builds | All queries on bloated table; risk of transaction ID wraparound if VACUUM cannot complete | `SELECT relname, age(relfrozenxid) FROM pg_class WHERE relkind='r' ORDER BY 2 DESC LIMIT 5` shows age > 1.5B; `pg_stat_activity` shows autovacuum waiting | `VACUUM FREEZE VERBOSE <table>` in dedicated session; cancel blocking long-running queries |
| `pg_wal` directory disk full | New WAL segments cannot be written → PostgreSQL enters `PANIC` mode → shuts down → `FATAL: could not write to file "pg_wal/..."` | Complete PostgreSQL outage; all services dependent on DB | `df -h /var/lib/postgresql` at 100%; PostgreSQL log: `PANIC: could not write to file "pg_wal/..."` | Free space: remove old archived WAL; `pg_archivecleanup`; increase volume; restart PostgreSQL after space freed |
| `max_connections` reached | All new connection attempts return `FATAL: sorry, too many clients already` → application pools exhaust retry budget → services mark themselves unhealthy | All new database-dependent requests; services without connection pooling | `SELECT count(*) FROM pg_stat_activity` equals `SHOW max_connections`; application logs `too many clients` | Kill idle connections; `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' ORDER BY backend_start LIMIT 50`; enable PgBouncer if not present |
| Long-running transaction blocking DDL | `ALTER TABLE` waits for `AccessExclusiveLock` → all subsequent queries on the table queue → queue grows → application request backlog → memory pressure | All queries to the locked table; may cascade to related tables via foreign keys | `SELECT pid, query, now()-query_start AS duration FROM pg_stat_activity WHERE wait_event_type='Lock' ORDER BY duration DESC` | Cancel the DDL or the blocking transaction: `SELECT pg_cancel_backend(<pid>)` on the longest-running blocker |
| Checkpoint storm saturating I/O | Frequent checkpoints write large dirty buffers → I/O saturated → query latency spikes → checkpoint_warning logs flood | All queries experience elevated latency during checkpoint I/O spike | `SELECT checkpoints_req, buffers_checkpoint FROM pg_stat_bgwriter`; `checkpoints_req` rising fast; `iostat` shows 100% utilization | `ALTER SYSTEM SET checkpoint_completion_target = 0.9; SELECT pg_reload_conf();`; reduce `max_wal_size` trigger frequency |
| Logical replication slot accumulating unread WAL | `pg_wal` fills → disk full → PostgreSQL panics (see above); or replication consumer is dead but slot stays active | Primary disk fills; may cause full outage if WAL exceeds disk | `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) FROM pg_replication_slots ORDER BY 2 DESC` | Drop idle slot: `SELECT pg_drop_replication_slot('<slot_name>')` if subscriber confirmed gone; set `max_slot_wal_keep_size = 10GB` |
| Standby promoted before primary fully down (split-brain) | Both primary and old-standby accept writes → client can write to both → data diverges → when partition heals, WAL conflict detected | Data integrity for any writes made to both nodes; replication breaks | `SELECT pg_is_in_recovery()` returns `false` on both nodes; replication connection error on old primary | Fence old primary (block port 5432 via firewall); promote new primary only; restore old primary as standby with `pg_rewind` |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| PostgreSQL minor version upgrade (e.g., 15.3 → 15.6) | Query plan regression: previously fast queries now do sequential scans; `ERROR: could not read block` if binary incompatibility | Seconds (startup) to hours (plan regression) | Compare `EXPLAIN` output before/after; check `pg_stat_statements` for queries with increased `total_exec_time`; correlate with upgrade timestamp | For plan regression: `ALTER TABLE <t> SET (autovacuum_enabled = off); ANALYZE <t>;`; for binary issue: restore previous minor version |
| `shared_buffers` increase requiring restart | PostgreSQL fails to start: `FATAL: could not resize shared memory segment` if hugepages not configured | On restart | `dmesg | grep shm`; `SHOW shared_buffers` after start | Reduce `shared_buffers`; configure hugepages: `vm.nr_hugepages` in sysctl; or set `huge_pages = off` in `postgresql.conf` |
| Adding `pg_stat_statements` extension | `ERROR: could not load library "$libdir/pg_stat_statements"` if `shared_preload_libraries` not set | On restart if library not preloaded | Check `SHOW shared_preload_libraries` — must include `pg_stat_statements` | Add to `shared_preload_libraries`, restart; then `CREATE EXTENSION IF NOT EXISTS pg_stat_statements` |
| Schema migration adding NOT NULL column without default | `ERROR: column "new_col" of relation "t" contains null values` blocking migration; table locked during migration attempt | Immediately on `ALTER TABLE` | Check migration script; `SELECT count(*) FROM t WHERE new_col IS NULL` before migration | Use `ADD COLUMN new_col type DEFAULT <val>` then backfill then `ALTER COLUMN ... SET NOT NULL`; or use `NOT VALID` constraint |
| Changing `wal_level` (e.g., `replica` → `logical`) | PostgreSQL requires restart; during restart window all connections drop; after restart, logical replication slots can be created | On restart | `SHOW wal_level` before/after; correlate application downtime with restart | Requires restart — plan maintenance window; set `wal_level = logical` in `postgresql.conf` and restart |
| `work_mem` increase system-wide | OOM killer triggered on host when many parallel queries each allocate full `work_mem` | Under concurrent query load, within minutes | `dmesg | grep oom_kill`; correlate with `work_mem` change in `postgresql.conf` | `ALTER SYSTEM SET work_mem = '64MB'; SELECT pg_reload_conf();`; set per-role instead of globally |
| `pg_hba.conf` change mistyped CIDR | Legitimate clients cannot connect: `FATAL: no pg_hba.conf entry for host "<ip>", user "<u>", database "<db>"` | Immediately on next new connection (reload applied without restart) | `SELECT pg_reload_conf()` timestamp in logs; test connection from app host | Fix CIDR in `pg_hba.conf`; `SELECT pg_reload_conf()` — no restart required |
| Upgrading PgBouncer version with changed default `pool_mode` | `SET` commands fail in transaction pooling mode; application prepared statements fail in statement mode | Immediately after PgBouncer restart | `SHOW CONFIG` in PgBouncer console; compare `pool_mode` before/after | Explicitly set `pool_mode = transaction` in `pgbouncer.ini`; restart PgBouncer |
| Adding a new index on production table (without CONCURRENTLY) | Full `AccessShareLock` blocks all writes for duration of index build | Immediately on `CREATE INDEX` (not concurrent) | `SELECT pid, query, wait_event FROM pg_stat_activity WHERE wait_event_type='Lock'`; lock waiter spike | Kill the index build: `SELECT pg_cancel_backend(<idx_build_pid>)`; re-run with `CREATE INDEX CONCURRENTLY` |
| Enabling `ssl = on` with missing certificate files | PostgreSQL fails to start: `FATAL: could not load server certificate file "server.crt"` | On restart | `ls -la $(SHOW data_directory)/server.{crt,key}`; file missing or wrong permissions | Copy valid certificate; set permissions `chmod 600 server.key`; ensure correct CN; restart PostgreSQL |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Replication lag causing stale reads on replica | `SELECT now() - pg_last_xact_replay_timestamp() AS lag` on replica | Reads from replica return rows committed minutes ago on primary; time-sensitive queries return wrong data | Incorrect data-dependent decisions; user-visible stale state | Route critical reads to primary; set `synchronous_standby_names` for writes requiring sync replication |
| Split-brain after premature promotion | `SELECT pg_is_in_recovery()` returns `false` on both old primary and promoted standby | Both nodes accept writes; data diverges; replication connection errors | Data loss or duplication for writes made to both nodes | Fence old primary (block port 5432); use `pg_rewind --target-pgdata=<data_dir> --source-server='...'` to re-sync old primary as standby |
| Sequence out-of-sync after failover | `SELECT last_value FROM <table>_id_seq` on new primary is behind actual MAX(id) | `INSERT` fails with `ERROR: duplicate key value violates unique constraint` | Failed inserts; application error spike | `SELECT setval('<seq>', (SELECT MAX(id) FROM <t>) + 1);` on new primary |
| Logical replication subscriber out of sync | `SELECT confirmed_flush_lsn, sent_lsn FROM pg_replication_slots WHERE slot_type='logical'`; compare LSNs | Subscriber table diverges from publisher; downstream analytics or search indexes stale | Incorrect analytics; search index inconsistency | Drop and recreate subscription: `DROP SUBSCRIPTION <sub>; CREATE SUBSCRIPTION <sub> CONNECTION '...' PUBLICATION <pub>` |
| `TRUNCATE` on replicated table not visible on subscriber | `SELECT count(*) FROM t` differs between publisher and subscriber after TRUNCATE | Subscriber still holds old rows; publisher has empty table | Data divergence; stale data served from subscriber | Verify `TRUNCATE` replication: `ALTER PUBLICATION <pub> SET (publish_via_partition_root = true)`; re-sync subscriber table |
| Clock skew causing wrong `now()` based partitioning | `SELECT now()` on primary vs replica shows > 1s difference | New rows inserted into wrong partition based on incorrect `now()`; queries miss recent data | Silent data routing error; missing rows in time-range queries | Sync clocks with `chronyc makestep`; verify: `SELECT now()` on all nodes within 100ms; re-route misplaced rows |
| `hot_standby_feedback=off` causing query cancellations | `SELECT pg_conf_info('hot_standby_feedback')` or `SHOW hot_standby_feedback` returns `off` on replica | `ERROR: canceling statement due to conflict with recovery` under WAL apply pressure | Replica queries fail unpredictably; retry storm | `ALTER SYSTEM SET hot_standby_feedback = on; SELECT pg_reload_conf()` on replica |
| XID wraparound imminent | `SELECT datname, age(datfrozenxid) FROM pg_database ORDER BY 2 DESC` shows age > 1.5 billion | PostgreSQL emits `WARNING: database with OID <n> must be vacuumed within <N> transactions`; if unhandled, DB enters read-only mode | Potential read-only emergency shutdown to prevent wraparound | `VACUUM FREEZE` all tables urgently: `psql -c "VACUUM FREEZE VERBOSE <largest_table>;"` during low-traffic window |
| `pg_rewind` applied but timeline history diverged | `pg_rewind` fails: `ERROR: target server needs to use either data checksums or "wal_log_hints" option` | Old primary cannot rejoin cluster; manual base backup required | Standby must be rebuilt from scratch | Enable `wal_log_hints = on` proactively; rebuild: `pg_basebackup -h <new-primary> -U replication -D <data_dir> -R` |
| Orphaned prepared transactions (`PREPARE TRANSACTION`) | `SELECT count(*) FROM pg_prepared_xacts` > 0 with old transactions | Prepared transactions hold locks and prevent VACUUM from cleaning dead tuples; xid age rises | Table bloat; autovacuum blocked; eventual wraparound risk | `ROLLBACK PREPARED '<transaction_id>'` for each orphan; investigate two-phase commit coordinator failure |

## Runbook Decision Trees

### Decision Tree 1: High query latency alert fires

```
Is `pg_stat_activity` showing queries with `now()-query_start > 30s`?
├── YES → Are they waiting on a lock? (`wait_event_type = 'Lock'`)
│   ├── YES → Who holds the lock?
│   │   ├── Find blocker: SELECT pid, query FROM pg_stat_activity WHERE pid IN (
│   │   │                   SELECT blocking_pid FROM pg_blocking_pids(<waiting_pid>));
│   │   ├── Blocker is idle transaction → `SELECT pg_terminate_backend(<blocker_pid>);`
│   │   └── Blocker is a long DDL → wait if it is a deploy; escalate if unexpected
│   └── NO → Are they waiting on I/O? (`wait_event_type = 'IO'`)
│       ├── YES → Check disk saturation: `iostat -x 1 5`
│       │   ├── `%util > 90%` → Identify top I/O queries; add index or upgrade storage
│       │   └── I/O normal → Possible buffer cache miss; check `hit_rate` in pg_stat_bgwriter
│       └── NO → CPU-bound? Check `top` for postgres processes near 100%
│                 → Run `EXPLAIN (ANALYZE, BUFFERS)` on top CPU query; look for SeqScan on large table
└── NO  → Is the alert from external latency (app-to-PG round trip)?
          ├── YES → Is PgBouncer/connection pool involved?
          │   ├── Check PgBouncer wait time: `psql -p 6432 pgbouncer -c "SHOW POOLS;"`
          │   │   → If `sv_idle = 0`: pool exhausted → increase `max_client_conn` or add replica
          │   └── Direct connection: check network RTT: `ping -c 5 <pg_host>`
          └── NO  → Phantom alert: verify query in pg_stat_statements; check alert query correctness
```

### Decision Tree 2: Replication is broken

```
Is `pg_stat_replication` on primary returning rows for all expected standbys?
├── NO (standby missing) → Is the standby process running?
│   ├── NO → `systemctl start postgresql` on standby; watch `pg_log` for errors
│   │   ├── "requested WAL segment has been removed" → WAL archived? Restore from archive
│   │   └── "FATAL: could not connect to the primary server" → Check `primary_conninfo`; firewall
│   └── YES → Standby process running but not replicating → check `pg_stat_wal_receiver` on standby
│             ├── status = 'stopped' → `SELECT pg_wal_replay_resume();` if paused
│             └── status = 'streaming' but lag growing → check network bandwidth: `iftop -i eth0`
└── YES (rows present) → Is replay_lag > 30s?
    ├── YES → Is standby CPU/IO saturated?
    │   ├── CPU > 80%: `top` on standby → check for runaway query on standby
    │   └── IO high: check if `wal_compression = on` on primary; if not, enable to reduce WAL volume
    └── NO (lag low) → Alert is false positive? Verify monitoring query uses correct units (seconds vs bytes)
                       → If write_lag ≠ 0 but replay_lag ≈ 0: network jitter; monitor for trend
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Unbounded autovacuum running continuously on large table | CPU 40–60% from postgres workers; query latency rises | `SELECT pid, query, now()-query_start AS age FROM pg_stat_activity WHERE query LIKE 'autovacuum%' ORDER BY age DESC;` | Background CPU contention; OLTP latency increases | `SELECT pg_cancel_backend(<autovacuum_pid>);`; reduce `autovacuum_vacuum_cost_limit` temporarily | Set `autovacuum_vacuum_scale_factor=0.01` for large tables; schedule manual `VACUUM` during low-traffic windows |
| Long-running `pg_dump` consuming I/O and old transaction snapshot | `pg_dump` holds `AccessShareLock`; disk read-I/O saturated; dead tuples not cleared | `SELECT pid, now()-query_start, query FROM pg_stat_activity WHERE application_name='pg_dump';` | I/O saturation; autovacuum blocked; storage grows | `pg_cancel_backend(<pg_dump_pid>)`; reschedule backup for off-peak | Set `--no-tablespaces --no-privileges` to reduce dump size; use `pg_basebackup` for physical backup |
| Excessive table bloat from high-churn tables without vacuum | Table size 3–5× logical data size; `n_dead_tup` in millions | `SELECT relname, n_dead_tup, n_live_tup, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10;` | Disk cost overrun; I/O amplification; slow queries | `VACUUM (VERBOSE, ANALYZE, PARALLEL 2) <table>;` | Set per-table `autovacuum_vacuum_scale_factor=0.01`; use `pg_partman` to partition high-churn tables |
| Logical replication slot accumulating WAL | `pg_wal` growing > 20 GB; disk usage alert | `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS wal_behind FROM pg_replication_slots;` | Disk full → PostgreSQL crash | Drop inactive slot: `SELECT pg_drop_replication_slot('<slot>');` | Set `max_slot_wal_keep_size='10GB'` in `postgresql.conf` |
| `pg_stat_statements` tracking every unique query string | `pg_stat_statements.max` entries full; new queries not tracked; `pg_stat_statements.pg_stat_statements_dealloc` rising | `SELECT dealloc FROM pg_stat_statements_info;` | Loss of query performance visibility | `SELECT pg_stat_statements_reset();`; increase `pg_stat_statements.max=10000` in `postgresql.conf` | Normalize queries in application (use parameterized queries, not string interpolation) |
| `work_mem` set too high globally causing RAM exhaustion during parallel sorts | Server RAM exhausted during bulk analytical queries; OOM kills | `SHOW work_mem;`; `SHOW max_parallel_workers_per_gather;`; `free -h` on host | OOM-killed backends; service interruption | `SET work_mem = '4MB';` at session level for analytical users; `ALTER ROLE analyst SET work_mem='32MB';` | Never set global `work_mem > 64MB`; use `ALTER ROLE` to grant more only to analytical roles |
| Unpartitioned append-only table growing unbounded | Table size > 100 GB; queries full-scan entire history | `SELECT pg_size_pretty(pg_total_relation_size('events'));` growing daily | Storage cost overrun; query latency grows linearly | Add `WHERE created_at > now()-interval '90d'` filter to all queries; archive old rows to cold storage | Partition by `created_at` using `pg_partman`; set retention policy to drop old partitions |
| Too many indexes on OLTP table causing write amplification | `pg_stat_user_indexes.idx_scan` shows indexes with < 100 scans in 30 days; write latency rising | `SELECT indexrelname, idx_scan FROM pg_stat_user_indexes WHERE relname='<table>' ORDER BY idx_scan;` | Write throughput degraded; storage higher | `DROP INDEX CONCURRENTLY <unused_index>;` | Monthly index usage audit: drop any with `idx_scan < 100` over 30 days |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot key / partition skew from monotonic PK | One table partition or index page heavily contended; insert latency p99 rising | `SELECT schemaname, tablename, seq_scan, idx_scan FROM pg_stat_user_tables ORDER BY seq_scan DESC LIMIT 10;` | Auto-increment or timestamp PK causing all inserts to hit the last B-tree leaf page | Switch to UUIDv7 or hash-partitioned PK; use `pg_partman` range partitioning with many subpartitions |
| Connection pool exhaustion | `FATAL: sorry, too many clients already`; PgBouncer shows `cl_waiting` rising | `SELECT count(*), state FROM pg_stat_activity GROUP BY state ORDER BY count DESC;` and `psql -p 6432 pgbouncer -c "SHOW POOLS;"` | Application opening connections without pooler; `max_connections` hit | Deploy PgBouncer in transaction mode; kill idle connections: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND query_start < now()-interval '5 min';` |
| GC / memory pressure from work_mem × parallel workers | PostgreSQL OOM-killed; `dmesg` shows OOM; queries using `Sort` nodes slow | `SHOW work_mem; SHOW max_parallel_workers_per_gather;` and `free -h` | Global `work_mem` × parallel workers × connections exceeds available RAM | Reduce `work_mem` to 4–16 MB globally; use `ALTER ROLE analyst SET work_mem='256MB'` for specific roles only |
| Thread pool saturation from long idle transactions | `Threads_running` analog: `pg_stat_activity` shows many `idle in transaction` connections; new queries queue | `SELECT pid, now()-xact_start AS age, state, query FROM pg_stat_activity WHERE state='idle in transaction' ORDER BY age DESC LIMIT 10;` | Application opened transaction but not committing promptly; holding row locks | Set `idle_in_transaction_session_timeout=30s` in `postgresql.conf`; terminate offending sessions |
| Slow query from missing index on high-cardinality filter column | `seq_scan` on large table visible in `EXPLAIN`; `pg_stat_user_tables.seq_scan` rising | `SELECT relname, seq_scan, idx_scan FROM pg_stat_user_tables WHERE seq_scan > 100 ORDER BY seq_scan DESC LIMIT 10;` | Missing index; planner choosing sequential scan | `CREATE INDEX CONCURRENTLY idx_<table>_<col> ON <table>(<col>);`; run `ANALYZE` |
| CPU steal from cloud burstable instance | Query latency rises without load increase; `vmstat %st` > 5% | `vmstat 1 10 | awk 'NR>2 {print $15}'` (steal column) and `SELECT query, total_exec_time FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 5;` | Cloud burstable instance out of CPU credits | Upgrade to non-burstable (`m5`/`c5`) instance; or wait for credit refresh and reduce query load |
| Lock contention from long-running DDL or explicit table lock | Queries piling up behind ALTER TABLE or LOCK TABLE statement | `SELECT pid, wait_event_type, wait_event, query FROM pg_stat_activity WHERE wait_event_type='Lock';` | DDL holding `AccessExclusiveLock`; all DML waiting | Kill blocking DDL: `SELECT pg_cancel_backend(<pid>);`; use `lock_timeout='5s'` for DDL statements |
| Serialization overhead from JSONB storage in hot-path query | High CPU on JSON parsing; `mean_exec_time` high for queries touching JSONB column | `SELECT query, mean_exec_time FROM pg_stat_statements WHERE query ILIKE '%::jsonb%' ORDER BY mean_exec_time DESC LIMIT 10;` | Deserializing large JSONB on every row fetch | Extract frequently-queried JSON fields into native columns with generated columns |
| Batch size misconfiguration: single-row INSERT in loop | WAL volume high; `pg_stat_wal.wal_bytes` growing fast; low throughput | `SELECT wal_bytes, wal_records FROM pg_stat_wal;` and check `pg_stat_statements` for `calls >> rows` | ORM issuing one INSERT per row instead of multi-row INSERT | Use `INSERT INTO t VALUES (?,?),(?,?)...` batching 500+ rows; or use `COPY FROM STDIN` |
| Downstream standby latency from heavy checkpoint writes | Replication lag rising during bulk load; checkpoint I/O saturating disk | `SELECT write_lag, flush_lag, replay_lag FROM pg_stat_replication;` and `SELECT * FROM pg_stat_bgwriter;` | `checkpoint_completion_target` too low; burst I/O during checkpoint | Set `checkpoint_completion_target=0.9`; `max_wal_size=4GB`; enable `wal_compression=on` |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on PostgreSQL server | `psql: error: SSL error: certificate has expired`; `openssl s_client -connect <host>:5432 -starttls postgres 2>/dev/null | grep notAfter` shows past date | Expired `server.crt` in `$PGDATA` | All `sslmode=verify-full` connections fail | Renew cert; place new `server.crt`/`server.key` in `$PGDATA`; `pg_ctl reload`; automate with cert-manager |
| mTLS rotation failure for PgBouncer-to-PostgreSQL | PgBouncer log: `TLS handshake failed: certificate verify failed` | `psql -p 6432 pgbouncer -c "SHOW SERVERS;"` — shows `sv_ssl_cipher` empty; `openssl verify -CAfile /etc/pgbouncer/ca.crt /etc/postgresql/server.crt` | New PostgreSQL CA not updated in PgBouncer `ca-cert` config | Add new CA to `pgbouncer.ini ca-cert`; `psql -p 6432 pgbouncer -c "RELOAD;"` |
| DNS resolution failure for replica endpoint | `ERROR: could not translate host name`; application cannot open standby connections | `dig +short <replica-hostname>` from app host; `nslookup <hostname> <dns-server>` | DNS record for replica removed or stale after failover | Update DNS A record for replica; `systemd-resolve --flush-caches`; use IP-based connection string as fallback |
| TCP connection exhaustion on PostgreSQL port | `FATAL: sorry, too many clients already`; `ss -tn sport = :5432 | wc -l` near `max_connections` | Short-lived connections; no connection pooler; `max_connections` insufficient | Kill idle connections; deploy PgBouncer immediately | Mandatory PgBouncer; set `max_connections=100` for PostgreSQL + PgBouncer handles app scale |
| HAProxy / NLB misconfiguration sending traffic to non-primary | Write queries returning `ERROR: cannot execute in a read-only transaction` | `psql -h <lb-vip> -c "SELECT pg_is_in_recovery();"` — if `t`, LB is routing writes to standby | Writes routed to standby; all write operations fail | Update HAProxy backend: `server primary <primary-ip>:5432 check`; verify Patroni/repmgr topology |
| Packet loss on primary-to-standby replication link | Replication lag growing; standby eventually requests archived WAL; potential `restore_command` errors | `ping -c 100 <standby-ip>` from primary; `SELECT replay_lag FROM pg_stat_replication;` trending up | Standby lag increases; failover takes longer; risk of data loss | Check NIC: `ethtool -S <nic> | grep error`; verify network path; use `wal_compression=on` to reduce WAL volume |
| MTU mismatch on WAL streaming network | Replication stalls intermittently for large WAL segments; small transactions replicate fine | `ping -M do -s 8972 <standby-ip>` — failure confirms path MTU < 9000 | Intermittent WAL streaming stalls; standby lag spikes | `ip route change <standby-subnet> mtu 1500`; or disable jumbo frames on replication interface |
| Firewall rule blocking port 5432 after security group change | All new connections time out; `telnet <pg-host> 5432` hangs | `nc -zv <pg-host> 5432` from app host; `iptables -L -n | grep 5432` | Complete PostgreSQL outage | Add ingress rule for app subnet to port 5432; check AWS security groups and NACLs |
| SSL handshake timeout from oversized certificate chain | Connection latency p99 > 2 s on first connect; subsequent connections from pool are fast | `openssl s_client -connect <host>:5432 -starttls postgres 2>/dev/null | grep -c "^-----BEGIN CERTIFICATE-----"` | Intermediate CA certs included in chain; TLS negotiation slow | Trim `server.crt` to only server cert + one intermediate; remove root from chain file |
| Connection reset by peer during pg_dump or COPY | `pg_dump: error: query failed: SSL SYSCALL error: EOF detected`; partial dump | `ps aux | grep pg_dump`; `SELECT now()-query_start, state FROM pg_stat_activity WHERE application_name='pg_dump';` | TCP keepalive timeout shorter than pg_dump runtime | Set `tcp_keepalives_idle=60 tcp_keepalives_interval=10` in `postgresql.conf`; `pg_dump --no-sync` for speed |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of PostgreSQL backend | Missing backend in `pg_stat_activity`; client gets `connection to server was lost`; `dmesg` shows kill | `journalctl -k --since "1h ago" | grep -iE "oom|postgres|killed"` | Surviving backends continue; client reconnects; investigate query that triggered OOM | Set `vm.overcommit_memory=2`; use cgroup memory limit; reduce `work_mem` and `max_connections` |
| Disk full on data partition | `FATAL: could not write to file "base/...": No space left on device` | `df -h $(psql -Atc "SHOW data_directory;")` | `VACUUM FULL` bloated table; drop unused indexes; extend EBS volume; move tablespace | Monitor at 70%/85%; use `pg_partman` to drop old partitions; enable `autovacuum` |
| Disk full on WAL partition | `PANIC: could not write to file "pg_wal/..."` | `df -h $(psql -Atc "SHOW data_directory;")/pg_wal` | Drop inactive replication slots: `SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE active=false;` | `max_slot_wal_keep_size='10GB'`; `wal_keep_size='1GB'`; monitor WAL dir size |
| File descriptor exhaustion | `FATAL: could not open file "...": Too many open files` | `ls /proc/$(pgrep -x postgres | head -1)/fd | wc -l`; `cat /proc/sys/fs/file-max` | Increase `LimitNOFILE=65536` in PostgreSQL systemd unit; restart PostgreSQL | Set `LimitNOFILE=65536` in `/etc/systemd/system/postgresql.service.d/override.conf` |
| Inode exhaustion on data partition | `touch` returns `No space left` but `df` shows free space; `df -i` at 100% | `df -i $(psql -Atc "SHOW data_directory;")` | `find $PGDATA -name "t*_*" -mtime +1 -delete` to remove stale temp files | Monitor inodes at 80%; avoid storing files in `$PGDATA` outside PostgreSQL's management |
| CPU steal / throttle on cloud instance | Query latency rises without query load increase; `vmstat %st` elevated | `vmstat 1 10` (column 15 = steal); compare `SELECT total_exec_time FROM pg_stat_statements` trends | Upgrade to non-burstable instance; offload analytics queries to read replica | Use `m5`/`c5` non-burstable EC2 for production; monitor `CPUCreditBalance` CloudWatch metric |
| Swap exhaustion | PostgreSQL query time 10–100× slower; `free -h` shows swap 100%; `vmstat si`/`so` non-zero | `free -h`; `vmstat 1 5` | `swapoff -a && swapon -a` to flush stale swap; restart heaviest backends | `vm.swappiness=1`; size RAM for `shared_buffers + max_connections * work_mem + OS overhead` |
| Kernel PID/thread limit | `FATAL: pre-existing shared memory block still in use` or fork fails on new connection | `cat /proc/sys/kernel/pid_max`; `ps aux | grep postgres | wc -l` | `sysctl -w kernel.pid_max=131072`; restart PostgreSQL cleanly | Set `kernel.pid_max=131072` in `/etc/sysctl.d/99-postgres.conf`; cap `max_connections` |
| Network socket buffer exhaustion | Replication lag spikes; TCP window fill; application socket sends stall | `ss -nm | grep postgres`; `sysctl net.core.rmem_max net.core.wmem_max` | `sysctl -w net.core.rmem_max=134217728 net.core.wmem_max=134217728` | Tune socket buffers for replication-heavy environments before load |
| Ephemeral port exhaustion from connection-per-query pattern | `Cannot assign requested address`; new `psql` connections fail | `ss -s | grep TIME-WAIT`; `cat /proc/sys/net/ipv4/ip_local_port_range` | `sysctl -w net.ipv4.tcp_fin_timeout=15 net.ipv4.tcp_tw_reuse=1` | Mandatory PgBouncer connection pooling; never open one TCP connection per query |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation causing duplicate rows from retried INSERT | `SELECT COUNT(*), COUNT(DISTINCT idempotency_key) FROM events;` diverges | `SELECT idempotency_key, COUNT(*) FROM events GROUP BY idempotency_key HAVING COUNT(*) > 1 ORDER BY 2 DESC LIMIT 20;` | Duplicate records; billing / inventory errors | `DELETE FROM events a USING events b WHERE a.id < b.id AND a.idempotency_key = b.idempotency_key;`; add `UNIQUE` on `idempotency_key` |
| Saga partial failure: service A committed, service B rolled back | Service A has committed row; service B transaction failed; saga stuck in partial state | `SELECT id FROM orders WHERE status='payment_pending' AND created_at < now()-interval '5 min';` | Resource locked; user-visible inconsistency | Trigger compensating transaction in service A to reverse write; use outbox pattern with `pending_saga_events` table |
| Message replay causing stale row overwrite | Kafka consumer replayed from old offset; older event's UPDATE overwrites fresher state | `SELECT updated_at FROM <table> WHERE id = ? ;` — `updated_at` older than expected | Silent data regression; difficult to detect without version tracking | Add `WHERE updated_at < $event_ts` guard to all consumer UPDATEs; add `version` column with optimistic locking |
| Cross-service deadlock from inconsistent lock ordering | PostgreSQL deadlock log: two backends each waiting for the other's row lock | `grep "deadlock detected" /var/log/postgresql/postgresql-$(date +%Y-%m-%d).log | tail -10` | One transaction auto-rolled back by PostgreSQL deadlock detector | Enforce consistent row-lock acquisition order across services; use `SELECT ... FOR UPDATE SKIP LOCKED` for job queues |
| Out-of-order event processing from parallel consumer workers | Newer event processed before older; final state reflects older event's write | `SELECT event_seq, processed_at FROM event_log WHERE entity_id = ? ORDER BY processed_at;` — check `event_seq` not monotone | Stale state for affected entity; reconciliation required | Partition Kafka by entity ID for ordered delivery; or use `UPDATE ... WHERE version < $new_version` guard |
| At-least-once delivery duplicate from Kafka consumer retry | Same event processed twice; INSERT executes twice before idempotency check | `SELECT event_id, COUNT(*) FROM processed_events GROUP BY event_id HAVING COUNT(*) > 1;` | Duplicate side effects (charges, notifications) | Use `INSERT INTO processed_events(event_id) VALUES(?) ON CONFLICT DO NOTHING` as first step; abort if 0 rows inserted |
| Compensating transaction failure after partial saga rollback | Rollback event consumed but compensating UPDATE fails due to constraint; saga stuck | `SELECT id, status, error FROM saga_state WHERE status='compensation_failed';` | Workflow permanently stuck; requires manual intervention | Implement dead-letter queue for failed compensations; alert on `compensation_failed` status; manual SQL fix + re-trigger |
| Distributed lock expiry mid-operation via PostgreSQL advisory lock | `pg_try_advisory_lock()` acquired; long operation takes > lock TTL equivalent; second process acquires same lock | `SELECT pid, granted, classid, objid FROM pg_locks WHERE locktype='advisory';` | Two processes executing critical section simultaneously; data corruption | Use `pg_advisory_lock()` (blocking, no expiry) for critical sections; or implement heartbeat to refresh lock |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: one tenant role monopolising query executor | `SELECT usename, count(*), sum(extract(epoch FROM now()-query_start)) AS cpu_seconds FROM pg_stat_activity WHERE state='active' GROUP BY usename ORDER BY cpu_seconds DESC;` | Adjacent tenants see query latency spike | `ALTER ROLE <tenant_role> CONNECTION LIMIT 3;`; `SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE usename='<tenant_role>';` | Move tenant to read replica; set per-tenant `statement_timeout = '5s'` via `ALTER ROLE` |
| Memory pressure: one tenant's large `work_mem` sort filling RAM | `SHOW work_mem;` too high globally; `free -h` shows low available RAM during tenant query; `vmstat si > 0` | Other tenants' backends swapping; query latency increases 10× | `ALTER ROLE <tenant_role> SET work_mem='16MB';`; `SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE usename='<tenant_role>' AND wait_event_type IS NOT NULL;` | Reduce global `work_mem`; set generous per-role overrides only for analytics roles |
| Disk I/O saturation from tenant bulk data load | `iostat -xz 1 5` — `%util` near 100% during tenant's batch job; `SELECT * FROM pg_stat_bgwriter;` shows elevated writes | All tenants see write amplification; checkpoint storms | `SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE usename='<tenant_role>' AND query ILIKE '%INSERT%';` | Schedule tenant bulk loads during off-peak; throttle via application-layer rate limiter; use `pg_partman` for incremental loading |
| Network bandwidth monopoly from tenant cross-region replication | `SELECT application_name, write_lag, replay_lag FROM pg_stat_replication;` — one replica lagging more than others | Standby replication lag increases; failover recovery time grows | `SELECT pg_terminate_backend(pid) FROM pg_stat_replication WHERE application_name='<tenant_replica>';` | Enable `wal_compression=on`; rate-limit `wal_sender` per connection with `wal_sender_timeout` |
| Connection pool starvation: tenant leaking idle connections | `SELECT usename, count(*) FROM pg_stat_activity WHERE state='idle' GROUP BY usename ORDER BY count DESC LIMIT 10;` | Other tenants get `FATAL: too many clients already` | `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='<tenant>' AND state='idle';` | Set `idle_in_transaction_session_timeout=30s`; per-tenant PgBouncer pool with `max_client_conn` |
| Quota enforcement gap: tenant bypassing row-limit via direct psql | `SELECT schemaname, tablename, n_live_tup FROM pg_stat_user_tables WHERE schemaname='tenant_<id>' ORDER BY n_live_tup DESC;` | Tenant storing more data than allocated quota | `REVOKE INSERT ON ALL TABLES IN SCHEMA tenant_<id> FROM <tenant_role>;` (emergency) | Implement row quota via trigger; or use `pg_partman` with automatic partition archiving |
| Cross-tenant data leak risk via row-level security misconfiguration | `SET ROLE <tenant_role>; SELECT * FROM shared_table LIMIT 5;` — check if rows from other tenants visible | Tenant can read other tenants' data in shared table | `ALTER TABLE shared_table ENABLE ROW LEVEL SECURITY; CREATE POLICY tenant_isolation ON shared_table USING (tenant_id = current_setting('app.tenant_id')::int);` | Enforce RLS on all shared tables; test with `EXPLAIN (ANALYZE) SELECT * FROM shared_table;` to verify policy applied |
| Rate limit bypass: tenant opening many roles to circumvent per-role connection limit | `SELECT usename, count(*) FROM pg_stat_activity GROUP BY usename ORDER BY count DESC LIMIT 10;` — many tenant_<id>_* roles each at limit | Shared `max_connections` exhausted | `ALTER ROLE <tenant_role_pattern> CONNECTION LIMIT 2;` for each variant | Enforce connection limit by source IP at PgBouncer; use single credential per tenant with per-IP limits in `pg_hba.conf` |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure for pg_stat_statements | `postgresql_query_duration_seconds` absent in Grafana; dashboard gaps | `postgres_exporter` pod crash; or `pg_stat_statements` extension not loaded; or `track_activity_query_size` too small | `psql -c "SELECT count(*) FROM pg_stat_statements;"` directly; `psql -c "SHOW shared_preload_libraries;"` | Ensure `pg_stat_statements` in `shared_preload_libraries`; add `up{job="postgres_exporter"}==0` alert |
| Trace sampling gap: missing slow lock-wait transactions | APM shows no traces for the transactions that were blocked and timed out | Head-based sampling discards transactions that are queued before starting | `psql -c "SELECT pid, wait_event_type, wait_event, query_start FROM pg_stat_activity WHERE wait_event_type='Lock';"` post-hoc | Set `log_lock_waits=on` and `deadlock_timeout=1s` to log all lock waits > 1 s to PostgreSQL log |
| Log pipeline silent drop during high-write period | Missing PostgreSQL log entries in Splunk during bulk load window; errors not visible | Filebeat/Fluentd buffer overflow; PostgreSQL writing logs faster than pipeline can ship | `tail -f /var/log/postgresql/postgresql-$(date +%Y-%m-%d).log` on host to verify logs being written | Increase Fluentd buffer; switch to `overflow_action block`; use `log_destination=syslog` for direct OS pipeline |
| Alert rule misconfiguration: replication lag alert fires in wrong unit | Alert fires constantly at `lag > 0` or never fires at `lag > 3600000` | `pg_stat_replication.write_lag` is an `interval` type; Prometheus exporter may expose as microseconds or seconds depending on version | `psql -c "SELECT extract(epoch FROM write_lag) FROM pg_stat_replication;"` to verify unit | Normalize alert rule: `pg_stat_replication_lag_seconds > 30` vs `pg_replication_slots_wal_is_active_seconds > 30` |
| Cardinality explosion from per-query-hash label | Prometheus OOM; `pg_stat_statements_total` metric has millions of series | Developer added `query_hash` as Prometheus label to per-query metrics | `curl http://localhost:9090/api/v1/label/query_hash/values | jq length` | Remove `query_hash` label; aggregate metrics by `query_type` (SELECT/INSERT/UPDATE) only; use recording rules |
| Missing health endpoint for PgBouncer pool | PgBouncer pool exhausted but no alert fires | PgBouncer not instrumented; only PostgreSQL backend monitored | `psql -p 6432 pgbouncer -c "SHOW POOLS;"` manually; check `cl_waiting` column | Deploy `pgbouncer_exporter`; alert on `pgbouncer_pools_cl_waiting > 5` |
| Instrumentation gap in critical path: VACUUM progress not tracked | Bloat accumulates silently; table scan performance degrades over weeks | VACUUM progress not emitted as a metric; only visible in `pg_stat_progress_vacuum` live view | `psql -c "SELECT relname, n_dead_tup, last_autovacuum FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10;"` | Add custom Prometheus gauge scraping `pg_stat_progress_vacuum` and `n_dead_tup`; alert if `n_dead_tup > 1000000` |
| Alertmanager outage silencing PostgreSQL failover alerts | Patroni failover undetected; no on-call page; application writing to demoted primary | `amtool alert query alertname=PostgreSQLReplicationLag`; `kubectl get pod -l app=alertmanager` | Run Alertmanager in HA mode with two replicas; add dead-man's-switch to external monitoring service |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| PostgreSQL minor version upgrade rollback | Specific query returns different results or error after patch upgrade | `psql -c "SELECT version();"` compare before/after; `pg_dumpall --globals-only` to export roles | `apt-get install postgresql-16=<old-ver>`; `pg_ctl stop`; restore `PGDATA` from pre-upgrade snapshot | Test minor upgrade in staging with `pgTAP` regression tests; take EBS snapshot before upgrading |
| PostgreSQL major version upgrade: extension incompatibility | `pg_upgrade --check` reports incompatible extensions; or post-upgrade queries fail | `psql -c "SELECT name, installed_version FROM pg_available_extensions WHERE installed_version IS NOT NULL;"` | `pg_upgrade` stores old cluster at `PGDATA_OLD`; revert by stopping new, starting old: `pg_ctl -D $PGDATA_OLD start` | Run `pg_upgrade --check` before any upgrade; verify all extensions support new major version |
| Schema migration partial completion: backfill interrupted mid-run | Half of rows have new column populated; app requiring it fails for un-migrated rows | `SELECT count(*) FILTER (WHERE new_col IS NULL) AS missing, count(*) AS total FROM <table>;` | `UPDATE <table> SET new_col = <default> WHERE new_col IS NULL;` resume; or `ALTER TABLE <table> DROP COLUMN new_col;` revert | Deploy app changes only after 100% backfill verified; use `pg_partman`-batched backfills with progress tracking |
| Rolling upgrade version skew: old app using old column name, new app using renamed column | Half of pods return `column does not exist`; other half work normally | `kubectl get pods -o jsonpath='{range .items[*]}{.spec.containers[*].image}{"\n"}{end}' | sort | uniq -c` | `kubectl rollout undo deployment/<app>` | Keep old column name valid for one release cycle; use two-phase rename: add new column, dual-write, migrate reads, drop old |
| Zero-downtime migration gone wrong: lock acquired, long query holding it | Migration `ALTER TABLE` blocks; all DML waiting; system-wide latency spike | `SELECT pid, wait_event_type, query, now()-query_start FROM pg_stat_activity WHERE wait_event_type='Lock' ORDER BY query_start;` | `SELECT pg_cancel_backend(<migration_pid>);` to abort DDL; restore application availability | Set `lock_timeout='5s'` for all DDL migrations; use `CREATE INDEX CONCURRENTLY`; schedule DDL during low-traffic window |
| Config format change breaking standby: parameter removed in new version | Standby fails to start after upgrade; log shows `unrecognized configuration parameter` | `diff <(pg_dumpall --globals-only) <(cat $PGDATA/postgresql.conf)` | Restore `postgresql.conf` from backup; remove offending parameter; `pg_ctl start -D $PGDATA` | Use `ALTER SYSTEM SET` (stored in `postgresql.auto.conf`); review release notes for removed GUCs before upgrading |
| Data format incompatibility: `jsonb` operator changed behavior in new version | JSON path queries returning different results after PostgreSQL version upgrade | `psql -c "SELECT '{}' @? '$.key';"` — compare output between versions | Pin application to old PostgreSQL version; or rewrite queries to use version-agnostic syntax | Run full query regression suite in staging against new version; document all JSON operators used |
| Feature flag rollout regression: `enable_partitionwise_join` causing plan change | After `ALTER SYSTEM SET enable_partitionwise_join=on`, critical query plan changes; performance regresses 10× | `psql -c "EXPLAIN (ANALYZE) <query>"` — compare plan before/after flag change | `ALTER SYSTEM SET enable_partitionwise_join=off; SELECT pg_reload_conf();` | A/B test planner flags on a single session first: `SET enable_partitionwise_join=on; EXPLAIN ANALYZE <query>;` |
| Extension version conflict: `pgvector` incompatible with upgraded PostgreSQL | `CREATE EXTENSION vector` fails: `ERROR: incompatible library` after upgrading PostgreSQL | `psql -c "SELECT version();"` and `dpkg -l | grep -E "postgresql|pgvector"` — check version pairing | Downgrade extension: `apt-get install postgresql-<ver>-pgvector=<compatible-ver>` | Consult extension compatibility matrix before upgrading PostgreSQL; test in staging with same extension versions |

## Kernel/OS & Host-Level Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| OOM killer targets PostgreSQL shared buffer process | PostgreSQL crashes; all connections drop; `FATAL: the database system is shutting down` in client logs | `dmesg -T | grep -i 'oom.*postgres'`; `journalctl -k --since "1h ago" | grep -i killed`; `pg_isready -h localhost` | Complete database outage; all active transactions aborted; WAL recovery required on restart; replication lag spike on replicas | Set `vm.overcommit_memory=2` and `vm.overcommit_ratio=90`; tune `shared_buffers` to < 25% of RAM; set `oom_score_adj=-1000` for postmaster process |
| Inode exhaustion from WAL segment accumulation | `pg_basebackup` fails; WAL archiving stops; `LOG: could not create file` in PostgreSQL logs | `df -i /var/lib/postgresql/*/main/pg_wal`; `ls /var/lib/postgresql/*/main/pg_wal/ | wc -l`; `psql -c "SELECT count(*) FROM pg_ls_waldir();"` | WAL archiving halts causing replication lag growth; PITR window lost; eventually PostgreSQL shuts down to prevent data loss | Clean orphaned WAL: `pg_archivecleanup /var/lib/postgresql/*/main/pg_wal <oldest_needed_wal>`; verify archive_command works; add inode monitoring alert |
| CPU steal causes checkpoint timeout and WAL buildup | `LOG: checkpoints are occurring too frequently` in PostgreSQL logs; replication lag grows | `sar -u 1 5 | grep steal`; `psql -c "SELECT * FROM pg_stat_bgwriter;"` — check `checkpoints_timed` vs `checkpoints_req` | Checkpoints cannot complete within `checkpoint_timeout`; WAL accumulates; disk fills; query performance degrades from dirty buffer pressure | Migrate to dedicated instance; increase `checkpoint_timeout` and `max_wal_size`; reduce `checkpoint_completion_target` to spread I/O |
| NTP clock skew breaks statement_timeout and pg_cron jobs | `pg_cron` jobs fire at wrong times; `statement_timeout` kills queries prematurely or not at all | `chronyc tracking | grep "System time"`; `psql -c "SELECT now(), clock_timestamp();"` — compare with host `date -u` | Scheduled maintenance windows misfire; pg_cron vacuum jobs run during peak hours; audit timestamps in `pg_stat_activity` incorrect | `chronyc makestep`; enable `chronyd`; alert on `abs(clock_skew_seconds) > 0.5`; use `clock_timestamp()` for critical timing |
| File descriptor exhaustion blocks new PostgreSQL connections | `FATAL: could not open file` in PostgreSQL logs; new connections refused; `too many open files` | `ls /proc/$(head -1 /var/lib/postgresql/*/main/postmaster.pid)/fd | wc -l`; `ulimit -n`; `psql -c "SHOW max_files_per_process;"` | New client connections refused; backend processes cannot open table files; COPY and large queries fail | Set `LimitNOFILE=65536` in `postgresql.service`; `ALTER SYSTEM SET max_files_per_process = 5000; SELECT pg_reload_conf();`; reduce `max_connections` or use pgBouncer |
| TCP conntrack table full drops pgBouncer-to-PostgreSQL connections | pgBouncer logs `server_connect_failed`; client queries queue then timeout; PostgreSQL itself is healthy | `dmesg | grep "nf_conntrack: table full"`; `sysctl net.netfilter.nf_conntrack_count`; `psql -p 6432 pgbouncer -c "SHOW POOLS;" | grep cl_waiting` | pgBouncer cannot establish new server connections; client queue grows; application-wide latency spike | `sysctl -w net.netfilter.nf_conntrack_max=524288`; reduce pgBouncer `server_idle_timeout` to close idle connections; use Unix sockets between pgBouncer and PostgreSQL |
| Transparent Huge Pages (THP) causing PostgreSQL latency spikes | Random p99 latency spikes of 100ms+; `pg_stat_activity` shows queries in `active` state but not progressing | `cat /sys/kernel/mm/transparent_hugepage/enabled` — if `[always]`, THP is active; `perf stat -e dTLB-load-misses -p $(pgrep -o postgres) sleep 10` | Periodic latency spikes during THP compaction; checkpoint performance unpredictable; autovacuum stalls | `echo never > /sys/kernel/mm/transparent_hugepage/enabled`; persist in GRUB: `transparent_hugepage=never`; verify with `grep HugePages /proc/meminfo` |
| NUMA imbalance causes asymmetric PostgreSQL backend performance | Some queries consistently 2-3x slower; `pg_stat_activity` shows active backends on remote NUMA node | `numactl --hardware`; `numastat -p $(pgrep -o postgres)`; `cat /proc/$(pgrep -o postgres)/numa_maps | grep -c "N1="` | Backends allocated on remote NUMA node access memory with higher latency; unpredictable query times; p99 latency elevated | Start PostgreSQL with `numactl --interleave=all`; or pin to single NUMA node: `numactl --cpunodebind=0 --membind=0`; set in systemd unit `ExecStart=numactl ...` |

## Deployment Pipeline & GitOps Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| Flyway/Liquibase migration fails mid-apply in CI pipeline | CI pipeline fails; migration lock held in `schema_version` table; subsequent runs blocked | `psql -c "SELECT * FROM flyway_schema_history ORDER BY installed_rank DESC LIMIT 5;"` — check for `success=false` | Schema in inconsistent state; application deployment blocked; all subsequent migrations queued behind failed one | `psql -c "DELETE FROM flyway_schema_history WHERE success=false;"` to clear lock; fix migration SQL; rerun with `flyway repair && flyway migrate` |
| Helm chart deploys app before migration job completes | Application pods start referencing columns/tables that don't exist; `ERROR: relation does not exist` | `kubectl get job <migration-job> -o jsonpath='{.status.conditions}'`; `kubectl get pods -l app=<service> --sort-by=.status.startTime` | 500 errors on all queries referencing new schema objects; partial rollback needed | Add init container that waits for migration job: `kubectl wait --for=condition=complete job/<migration-job> --timeout=300s`; use Helm hooks with `pre-install` weight |
| ArgoCD sync applies PostgreSQL ConfigMap but not Secret | PostgreSQL restarts with new `postgresql.conf` settings but old `pg_hba.conf` from Secret; auth failures | `argocd app diff <app> | grep -A5 Secret`; `kubectl get secret <pg-secret> -o jsonpath='{.metadata.resourceVersion}'`; `psql -c "SELECT pg_reload_conf();"` | Authentication failures for new connection patterns; or new settings conflict with old auth rules | Apply Secret and ConfigMap atomically; use ArgoCD sync waves: Secret at wave 0, ConfigMap at wave 1; verify with `psql -c "SHOW all;" | grep <changed-param>` |
| PDB blocks node drain during PostgreSQL pod rescheduling | Kubernetes node drain hangs; Patroni PostgreSQL pod protected by PDB; cluster upgrade stalls | `kubectl get pdb -n <ns> | grep postgres`; `kubectl describe pdb <pg-pdb>`; `patronictl list` to check cluster state | Database pod cannot be evicted; node maintenance window exceeded; if primary pod, no failover occurs | Set PDB `maxUnavailable=1` (not `minAvailable=N`); trigger Patroni switchover first: `patronictl switchover --leader <primary> --candidate <replica>` then drain |
| Blue-green cutover fails: new app version incompatible with current schema | Green deployment queries fail with `column does not exist`; blue still running old schema | `kubectl logs -l app=<service>,version=green | grep -i "column\|relation\|does not exist"`; `psql -c "\d <table>"` | Green environment fails health checks; traffic stays on blue; deployment blocked until schema aligned | Apply expand-contract migration pattern: add new columns in expand phase; deploy green; drop old columns in contract phase; never couple schema changes with app deployment |
| ConfigMap drift: PostgreSQL parameters overridden by stale Helm values | `psql -c "SHOW work_mem;"` returns value different from Git-committed Helm values | `kubectl get configmap <pg-config> -o yaml | grep work_mem`; `diff <(helm get values <release> -a) <(cat values.yaml)` | Performance regression or improvement not matching expected config; drift accumulates over time | `helm upgrade <release> --values values.yaml` to reconcile; add ArgoCD auto-sync with `selfHeal: true`; add config drift detection alert |
| Secrets Manager rotation changes PostgreSQL password; app not restarted | Application connection pool exhausted; new connections fail with `FATAL: password authentication failed` | `kubectl get secret <pg-secret> -o jsonpath='{.metadata.annotations}'`; `psql -p 6432 pgbouncer -c "SHOW POOLS;" | grep cl_waiting` | Connection pool drains as existing connections die; all new connections fail; complete application outage | Use stakater/Reloader to restart pods on Secret change; or use pgBouncer `auth_query` with dynamic password lookup; implement dual-password transition |
| pg_basebackup in CI fails due to max_wal_senders exhaustion | Backup job fails: `FATAL: number of requested standby connections exceeds max_wal_senders` | `psql -c "SELECT count(*) FROM pg_stat_replication;"` vs `psql -c "SHOW max_wal_senders;"` | Backup pipeline broken; new replicas cannot be provisioned; PITR chain at risk | `ALTER SYSTEM SET max_wal_senders = 10; SELECT pg_reload_conf();`; stagger backup schedules; use `pg_dump` for CI instead of `pg_basebackup` |

## Service Mesh & API Gateway Edge Cases
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| Envoy sidecar circuit breaker trips on pgBouncer connection pool | Application receives `503 UC` from Envoy; pgBouncer and PostgreSQL are healthy | `istioctl proxy-config cluster <pod> | grep pgbouncer`; `kubectl logs <pod> -c istio-proxy | grep "overflow\|circuit"` | Database queries rejected at mesh layer; false positive outage; application errors despite healthy database | Increase Envoy `circuitBreakers.maxConnections` to match pgBouncer `max_client_conn`; set `outlierDetection.consecutive5xx` higher than pgBouncer queue depth |
| Rate limiting on API gateway blocks database-backed health check | `/health` endpoint queries PostgreSQL; rate limiter returns 429; liveness probe fails; pod killed | `kubectl logs -l app=api-gateway | grep "429.*health"`; `kubectl describe pod <pod> | grep -A3 "Liveness"` | Pod killed by liveness probe despite healthy database; restart causes connection pool rebuild; cascading latency | Exempt `/health` from rate limiting; use TCP liveness probe instead; cache health check result for 5s: `SELECT 1` result cached in application |
| Stale service discovery for PostgreSQL read replicas | Application DNS resolves to terminated replica pod; reads timeout; write path unaffected | `kubectl get endpoints <pg-replica-svc>`; `nslookup <pg-replica-svc>.<ns>.svc.cluster.local`; `psql -h <replica-ip> -c "SELECT 1;"` | Read queries timeout or error; read-heavy workloads degraded; write path through primary unaffected | Use headless Service with client-side load balancing; reduce DNS TTL to 5s; configure pgBouncer with health check query against replicas |
| mTLS rotation interrupts pgBouncer connection to PostgreSQL | pgBouncer logs `ERROR: SSL connection failed`; client queries queue; new connections fail | `istioctl proxy-config secret <pod> | grep "valid\|expire"`; `psql -p 6432 pgbouncer -c "SHOW POOLS;" | grep cl_waiting` | pgBouncer-to-PostgreSQL server connections broken during cert rotation; client queue grows until pgBouncer timeout | Configure pgBouncer `server_tls_sslmode=prefer` with fallback; set Istio cert rotation overlap period; use Unix sockets between pgBouncer and PostgreSQL to bypass TLS entirely |
| Retry storm amplifies PostgreSQL connection exhaustion | Mesh retries failed queries; each retry opens new pgBouncer connection; `max_client_conn` exhausted in seconds | `psql -p 6432 pgbouncer -c "SHOW POOLS;"` — check `cl_waiting` > 100; `istioctl proxy-config route <pod> | grep retries` | Connection pool completely exhausted; all applications blocked; cascading failure across all services sharing the database | Disable mesh retries for database upstream; implement application-level retry with `SET statement_timeout`; add circuit breaker at application layer |
| gRPC health check via mesh fails for PostgreSQL-backed service | gRPC health endpoint returns `SERVING` but mesh marks upstream unhealthy due to non-standard health check | `grpc_health_probe -addr=<pod>:5432`; `istioctl proxy-config endpoint <pod> | grep UNHEALTHY` | Service removed from mesh load balancing despite being healthy; traffic shifts to fewer backends; overload | Configure Istio `DestinationRule` with custom health check matching PostgreSQL-backed gRPC service; use `portLevelSettings` for database port |
| Trace context lost through pgBouncer connection multiplexing | Distributed traces end at pgBouncer; no correlation to specific PostgreSQL backend query | `psql -p 6432 pgbouncer -c "SHOW CONFIG;" | grep application_name`; check Jaeger for spans ending at pgBouncer | Cannot attribute slow queries to specific traces; performance debugging requires manual log correlation; SLO measurement incomplete | Set `application_name` per-query with trace ID: `SET application_name='trace-<id>'`; use `pg_stat_activity.application_name` to correlate; or bypass pgBouncer for traced queries |
| API gateway timeout shorter than long-running PostgreSQL report query | Report endpoint returns 504; PostgreSQL query still running; client retries trigger duplicate report generation | `psql -c "SELECT pid, query, now()-query_start AS duration FROM pg_stat_activity WHERE state='active' AND query LIKE '%report%';"` | Gateway kills connection; PostgreSQL continues wasting resources on orphaned query; client retries multiply load | Set `statement_timeout` per-session for reports: `SET statement_timeout = '30s'`; increase gateway timeout for `/report` path; use async job pattern with polling |
