---
name: planetscale-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-planetscale-agent
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
# PlanetScale SRE Agent

## Role
This agent owns operational responsibility for PlanetScale database deployments — a serverless MySQL-compatible database platform built on Vitess. It monitors the branching and deploy request lifecycle, tracks query performance via Query Insights, manages connection limits across serverless workloads, diagnoses Vitess sharding behavior, and handles import/export operations. It understands the unique failure modes of PlanetScale's non-blocking schema change system, sleeping branch behavior, and the nuances of Vitess's scatter/gather query execution that differ from vanilla MySQL. The agent provides runbooks covering everything from routine deploy request failures to production query regressions and connection exhaustion.

## Architecture Overview
PlanetScale is a Vitess-backed distributed MySQL-compatible database. Each "database" consists of branches (main is production, others are for development/staging). Schema changes flow through deploy requests — a non-blocking online schema change mechanism. Underneath, Vitess manages one or more keyspaces (logical databases) and shards (physical MySQL instances). Connections go through the VTGate proxy layer which routes queries to the appropriate shard. PlanetScale's serverless connection model (HTTP-based with `@planetscale/database` SDK or standard MySQL driver) supports extremely high concurrency by multiplexing over persistent VTGate connections. Branches not accessed within a period are put to sleep to save resources.

```
Application
  ├── MySQL Driver  → connect-<region>.psdb.cloud:3306  → VTGate (Proxy)
  └── HTTP SDK      → aws.connect.psdb.cloud (HTTPS/2)  → VTGate (Proxy)
                                                              │
                                                     ┌────────┼─────────┐
                                                   Shard-0  Shard-1  Shard-N
                                                   (MySQL)  (MySQL)  (MySQL)
                                                        │
                                                  PlanetScale
                                                  Managed Storage
```

## Key Metrics to Monitor

| Metric | Warning Threshold | Critical Threshold | Notes |
|--------|------------------|--------------------|-------|
| Active connections | > 75% of plan limit | > 90% of plan limit | Scaler: ~10K; Scaler Pro / Enterprise: higher / custom |
| Query rows read per second | > 50M rows/s | > 100M rows/s (Scaler: billing spike) | Rows read ≠ rows returned; full scans are expensive |
| Query P99 latency | > 200 ms | > 1 s | Via Query Insights; baseline varies by query type |
| Failed queries rate | > 0.1% | > 1% | Watch for `1040 Too many connections`, `1205 Lock wait timeout` |
| Deploy request completion time | > 30 min | > 2 hours | Online schema change duration depends on table size |
| Branch sleeping status | Branch accessed 0 times in 7 days | Branch auto-sleeps | Sleeping branches block connections with a wake-up delay |
| Rows written per second | > 5K rows/s | > 50K rows/s | Depends on table structure and secondary indexes |
| VTGate scatter query ratio | > 20% of queries | > 50% of queries | Scatter queries hit all shards — expensive on large databases |
| Import job progress stalled | No progress for > 30 min | No progress for > 2 hours | Import via PlanetScale import tool |
| Deploy request `diff_error` count | Any | > 3 in 1 hour | Schema diff failures often indicate migration tooling issues |

## Alert Runbooks

### Alert: ConnectionLimitApproaching
**Condition:** Active connections > 90% of plan limit for > 5 min
**Triage:**
1. Check current connection count via PlanetScale dashboard → Branches → main → Usage, or via `pscale` CLI:
   `pscale branch show <database> main --org <org>`
2. Connect to the branch and inspect active connections:
   `pscale shell <database> main --org <org>` then:
   `SELECT user, host, db, command, time, state, info FROM information_schema.processlist WHERE command != 'Sleep' ORDER BY time DESC LIMIT 50;`
3. Identify connection sources by application or host:
   `SELECT LEFT(host, LOCATE(':', host)-1) AS client_host, count(*) AS connections FROM information_schema.processlist GROUP BY client_host ORDER BY connections DESC;`
4. Check for long-running transactions holding connections:
   `SELECT trx_id, trx_state, trx_started, trx_mysql_thread_id, LEFT(trx_query, 100) FROM information_schema.innodb_trx ORDER BY trx_started ASC LIMIT 10;`
### Alert: DeployRequestStuck
**Condition:** Deploy request in `running` state for > 2 hours without progress
**Triage:**
1. Check deploy request status: `pscale deploy-request show <database> <dr-number> --org <org>`
2. Review deploy request details in PlanetScale dashboard — check current step (copy data, cleanup, cut over).
3. If the deploy request is in the `copy` phase for a very large table (> 100M rows), extended time is expected. Check rows copied vs total.
4. Check if there is a blocking long-running transaction on the target table that is preventing cut-over: `SELECT trx_state, trx_started, trx_mysql_thread_id FROM information_schema.innodb_trx WHERE trx_state = 'LOCK WAIT';`
5. Verify no schema conflicts exist: `pscale deploy-request diff <database> <dr-number> --org <org>`
### Alert: QueryPerformanceRegression
**Condition:** P99 query latency increase > 3× over 1-hour baseline from Query Insights
**Triage:**
1. Open PlanetScale dashboard → Query Insights → sort by P99 latency descending.
2. Identify the regressed query; check query fingerprint and compare before/after EXPLAIN plans.
3. Run EXPLAIN on the production branch: `pscale shell <database> main --org <org>` then `EXPLAIN <query>;`
4. Check for missing indexes: look for `type: ALL` (full scan) or `rows` estimate > expected.
5. Correlate with recent deploy requests — was a schema change deployed that removed or changed an index?
### Alert: BranchSleepingInProduction
**Condition:** Connection attempts to a branch result in delayed responses (> 5 s); dashboard shows branch status `sleeping`
**Triage:**
1. Check branch status: `pscale branch show <database> <branch-name> --org <org>` — look for `sleeping` state.
2. Verify this is not the `main` branch (production branches do not sleep on paid plans).
3. If a non-main branch is being hit by production traffic unexpectedly, this is a misconfiguration.
4. Access the branch via dashboard or CLI to wake it: `pscale shell <database> <branch-name> --org <org>` — connection wakes the branch.
## Common Issues & Troubleshooting

### Issue: `ERROR 1040 (HY000): Too many connections`
**Symptoms:** Applications receive MySQL error 1040; new connections rejected; existing queries may succeed.
**Diagnosis:** `pscale shell <database> main --org <org>` → `SELECT count(*) FROM information_schema.processlist;` and `SHOW STATUS LIKE 'Threads_connected';`
### Issue: Schema Change Fails with `diff_error` in Deploy Request
**Symptoms:** Deploy request shows `diff_error` state; schema change cannot be applied.
**Diagnosis:** `pscale deploy-request show <database> <dr-number> --org <org>` — inspect the error field. Common causes: column rename (PlanetScale blocks destructive changes), type change incompatible with online schema change, FK constraint addition.
### Issue: Scatter Query Causing High Rows Read
**Symptoms:** Query Insights shows extremely high `rows_read` for a seemingly simple query; billing spikes.
**Diagnosis:** `EXPLAIN <query>` in `pscale shell` — look for `/* scatter */` comment in the plan or `keyspace_ranges: -` indicating all shards are hit.
### Issue: `Lock wait timeout exceeded` on High-Traffic Tables
**Symptoms:** `ERROR 1205 (HY000): Lock wait timeout exceeded`; intermittent write failures during peak load.
**Diagnosis:** `SELECT r.trx_id waiting_trx, r.trx_mysql_thread_id waiting_thread, r.trx_query waiting_query, b.trx_id blocking_trx, b.trx_mysql_thread_id blocking_thread, b.trx_query blocking_query FROM information_schema.innodb_lock_waits w JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id;`
### Issue: Import Job Stalls or Fails
**Symptoms:** PlanetScale import from external MySQL shows no progress for hours; import job errors out.
**Diagnosis:** Check import status in PlanetScale dashboard → Import. Verify source database accessibility from PlanetScale's import service IPs.
### Issue: `ERROR 1146 (42S02): Table doesn't exist` After Deploy Request
**Symptoms:** Application errors after a schema deploy; table name has changed; query references old name.
**Diagnosis:** `SHOW TABLES LIKE '<old_table_name>';` — verify table was renamed or dropped in the deploy request diff.
## Key Dependencies

- **VTGate Proxy Layer**: All MySQL connections go through VTGate. VTGate degradation causes query routing failures and timeout storms. PlanetScale manages this; monitor via their status page.
- **DNS / Regional Endpoints**: PlanetScale uses regional connection strings (`aws-us-east-1.connect.psdb.cloud`). DNS resolution failures or regional endpoint outages affect all connections in that region.
- **PlanetScale Platform API**: The `pscale` CLI and deploy request system depend on the PlanetScale control plane API. During API degradation, schema changes and branch operations are unavailable but production query traffic continues.
- **Application Connection Pool**: Client-side connection pooling is critical. Serverless runtimes (Lambda, Vercel) must use the HTTP-based `@planetscale/database` SDK or connection count grows unbounded.
- **Vitess Vschema**: The vschema defines sharding keys and vindexes. An incorrect vschema migration can cause all queries to scatter (hit all shards), dramatically increasing row reads and latency.
- **PlanetScale Backups**: Automated daily backups. Backup availability is required for `pscale database restore`. Verify backup completion in the dashboard.

## Cross-Service Failure Chains

**Chain 1: Missing Sharding Key in Query → Scatter Queries → Row Read Spike → Billing Alarm**
A new feature ships with a query that filters by a non-sharding-key column without a covering index. In the single-shard development branch, the query runs fine. In a sharded production database, VTGate must send the query to all shards (scatter). With 4 shards, every such query reads 4× as many rows as expected. During peak traffic, the rows-read metric spikes to 100M+ per second, triggering a billing alarm. Query Insights shows the query clearly but the root cause (missing vindex coverage) is only visible in the `EXPLAIN` output showing scatter routing.

**Chain 2: Deploy Request Cut-Over → DDL Lock → Application Timeout Storm**
A deploy request for a large `ALTER TABLE` on a 500M-row table enters the cut-over phase. The cut-over requires a brief table-level lock. Simultaneously, a high-throughput write workload is hitting that table. The MDL (metadata lock) acquired for cut-over blocks all concurrent writes. Writes queue up. Connection count spikes as threads wait. Connection limit is reached. New connections are refused with error 1040. Application returns 500 errors for the 2–5 seconds of cut-over lock time. Mitigation: schedule deploy request cut-over during low-traffic windows.

**Chain 3: Sleeping Development Branch in CI/CD → Cold Start Delay → CI Timeout → Deployment Blocked**
A CI/CD pipeline runs integration tests against a PlanetScale development branch. The branch sleeps after 7 days of inactivity. The next CI run attempts to connect; the branch is asleep. The wake-up takes 10–30 seconds. The CI MySQL client has a 10-second connection timeout and fails. CI reports database connection failure, blocks deployments, and engineers waste time investigating a "database outage" that is actually a sleeping branch. Fix: set a CI-specific keep-alive ping or migrate CI to use a non-sleeping branch.

## Partial Failure Patterns

- **Reads work, writes fail**: Table-level or row-level lock contention. SELECT queries succeed; INSERT/UPDATE/DELETE timeout or receive `Lock wait timeout` errors.
- **Simple queries fast, complex queries slow**: Scatter queries affecting sharded tables. Point lookups by PK are fast (single shard); range scans or non-PK queries scatter to all shards and are slow.
- **Production fine, deploy requests failing**: PlanetScale control plane API degraded or vschema conflict. Production query traffic (data plane) continues; schema change management (control plane) is unavailable.
- **New connections slow, existing fast**: Branch waking from sleep. First connection after idle period incurs wake-up latency; subsequent connections are fast.
- **Some regions slow, others fast**: Regional endpoint degradation. PlanetScale routes to the nearest region; traffic in the affected region is degraded while other regions are healthy.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|---------|
| Point SELECT by primary key | < 2 ms | 2–20 ms | > 20 ms |
| Single-row INSERT (with index update) | < 5 ms | 5–50 ms | > 50 ms |
| Bulk INSERT 1K rows (batched) | < 100 ms | 100 ms–1 s | > 1 s |
| Scatter query (all shards, with index) | < 50 ms | 50–500 ms | > 500 ms |
| Full table scan (10M rows, single shard) | < 2 s | 2–10 s | > 10 s |
| Deploy request (ADD INDEX, 10M rows) | < 15 min | 15–60 min | > 2 hours |
| Branch wake from sleep | < 10 s | 10–30 s | > 30 s |
| HTTP SDK query (serverless) | < 10 ms | 10–100 ms | > 100 ms |

## Capacity Planning Indicators

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Active connections (peak) | Regularly > 70% of plan limit | Optimize connection pools; consider plan upgrade | 1 week |
| Rows read per month | Trending toward plan tier overage | Audit scatter queries; add indexes; upgrade plan | 1 month |
| Rows written per month | Trending toward plan tier overage | Review write amplification; batch writes | 1 month |
| Storage per branch (main) | > 50% of plan limit | Implement data archival; upgrade storage tier | 2 weeks |
| Number of active branches | > 10 per database | Clean up stale branches; enforce branch lifecycle policy | 1 week |
| Deploy request duration (trending) | P95 deploy time doubling month-over-month | Shard migration planning; review table sizes | 1 month |
| Scatter query ratio (Query Insights) | Increasing week-over-week | Vschema optimization review; add vindexes | 2 weeks |
| Failed query rate | > 0.05% baseline | Root cause analysis on error types; query hardening | 3 days |

## Diagnostic Cheatsheet

```bash
# List all databases in org
pscale database list --org <org>

# Show all branches for a database
pscale branch list <database> --org <org>

# Connect to production shell
pscale shell <database> main --org <org>

# Show all open deploy requests
pscale deploy-request list <database> --org <org>

# Show schema diff for a deploy request
pscale deploy-request diff <database> <dr-number> --org <org>

# Show active connections (inside pscale shell)
# SELECT id, user, host, command, time, state, LEFT(info, 100) FROM information_schema.processlist ORDER BY time DESC;

# Show slow queries from InnoDB status (inside pscale shell)
# SHOW ENGINE INNODB STATUS\G

# Show index usage statistics (inside pscale shell)
# SELECT table_name, index_name, stat_value FROM mysql.innodb_index_stats WHERE database_name = DATABASE() AND stat_name = 'n_diff_pfx01' ORDER BY stat_value DESC LIMIT 20;

# Create development branch from main
pscale branch create <database> dev-<feature> --from main --org <org>

# Promote deploy request (after review)
pscale deploy-request deploy <database> <dr-number> --org <org>
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Query Success Rate | 99.95% | 1 - (failed queries / total queries) from Query Insights | 21.6 min/month | Burn rate > 14.4× |
| P99 Write Latency ≤ 20 ms | 99% of 5-min windows | Measured via application-side instrumentation on INSERT/UPDATE | 4.3 hr/month | Burn rate > 3× |
| Connection Availability | 99.9% | Successful MySQL connection from health-check client every 30 s | 43.8 min/month | Burn rate > 6× |
| Deploy Request Success Rate | 95% | Successful deploys / total deploy requests | N/A (best-effort) | Alert if 3 consecutive deploy requests fail |

## Configuration Audit Checklist

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Production uses `main` branch | `pscale branch list <database> --org <org>` | Application credentials point to `main` |
| Service token has minimum permissions | `pscale service-token list --org <org>` | Tokens scoped to specific database + actions |
| Connection pool size ≤ 25 per app replica | Application config review | `pool_size <= 25` (TCP driver) or using HTTP SDK |
| Serverless functions use HTTP SDK | Code review | `@planetscale/database` SDK used for Lambda/Vercel/Cloud Functions |
| Deploy requests require review before deploy | Dashboard → Settings → Deploy Requests | `require_approval` enabled for `main` branch |
| Automated backups confirmed | Dashboard → Backups | Daily backup completed within last 25 hours |
| No stale inactive branches | `pscale branch list <database> --org <org>` | No branches not accessed in > 30 days (unless intentional) |
| Foreign key constraints handled at app layer | `pscale shell <database> main` → `SHOW CREATE TABLE <table>` | FK constraints supported since 2024 (opt-in per database); legacy databases historically required FK enforcement at the app layer |
| Passwords rotated in last 90 days | Dashboard → Settings → Passwords | All passwords rotated within policy window |
| `safe migrations` enabled on main branch | Dashboard → Settings → Branches → main | `safe migrations` toggle enabled |

## Log Pattern Library

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `ERROR 1040 (HY000): Too many connections` | Critical | Connection limit exhausted | Kill sleeping connections; reduce pool size |
| `ERROR 1205 (HY000): Lock wait timeout exceeded` | High | Row or table lock held by long transaction | Kill blocking transaction; review lock acquisition order |
| `ERROR 1213 (40001): Deadlock found when trying to get lock` | Medium | Two transactions in circular lock dependency | Application must retry; review transaction scope |
| `ERROR 1146 (42S02): Table 'X' doesn't exist` | High | Table dropped in deploy request or wrong branch | Verify branch; check deploy request diff |
| `ERROR 1062 (23000): Duplicate entry for key 'PRIMARY'` | Medium | Duplicate primary key insert | Check for race condition; use INSERT IGNORE or ON DUPLICATE KEY |
| `ERROR 1264 (22003): Out of range value for column` | Medium | Integer overflow or wrong data type | Review schema; use BIGINT for high-cardinality IDs |
| `ERROR 2006 (HY000): MySQL server has gone away` | High | Connection dropped (idle timeout or restart) | Implement connection retry logic; reduce idle time |
| `ERROR 1227 (42000): Access denied` | High | Insufficient permissions for operation | Verify service token permissions; check user grants |
| `VTGate: target: X/0/primary is not serving` | Critical | Vitess shard unavailable (PlanetScale infra issue) | Check PlanetScale status page; contact support |
| `/* scatter */ SELECT` in slow query log | Medium | Query not using sharding key (Vitess scatter) | Add sharding key to WHERE clause; review vschema |
| `deploy request diff_error` | High | Schema change conflicts with existing schema | Review diff; resolve conflicts in development branch |
| `branch is sleeping` in connection error | Medium | Development branch auto-slept | Access branch to wake; or disable sleep in settings |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `1040` Too many connections | Plan connection limit reached | All new connections refused | Kill sleeping connections; upgrade plan |
| `1205` Lock wait timeout | Row/table lock not acquired within timeout | Write/update fails | Kill blocking transaction; retry |
| `1213` Deadlock | Circular lock dependency detected | Transaction rolled back automatically | Application-level retry required |
| `1146` Table doesn't exist | Table not found in current branch | Queries to that table fail | Verify correct branch; restore if accidentally dropped |
| `1062` Duplicate entry | Unique/PK constraint violation | INSERT fails | Use UPSERT or check before insert |
| `2006` Server has gone away | TCP connection dropped | Query fails; must reconnect | Implement reconnect logic; check idle timeout |
| `HY000 VTGate scatter` | Query routed to all shards | High latency and row-read cost | Add WHERE clause with sharding key |
| `deploy_request: diff_error` | Schema diff cannot be applied | Schema change blocked | Resolve schema conflict in dev branch |
| `deploy_request: running` (stuck) | Deploy request not completing | Schema change delayed | Kill blocking transactions; close and re-open DR |
| `branch: sleeping` | Branch auto-slept due to inactivity | Connections delayed 10–30 s | Wake branch; disable sleep for critical branches |
| `service_token: unauthorized` | Token lacks required permission | API/CLI operation fails | Re-issue token with correct permissions |
| `1264` Out of range | Column type overflow | INSERT/UPDATE fails | Migrate to BIGINT; use schema change |

## Known Failure Signatures

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Scatter Bomb | rows_read spikes 100×; query P99 > 500 ms | `/* scatter */` in query plan | QueryPerformanceRegression | Non-PK query on sharded table missing vindex | Add WHERE clause with sharding key; add covering index |
| Connection Leak | Connection count grows monotonically; `Sleep` count > 80% | `Too many connections` | ConnectionLimitApproaching | Application not releasing connections (missing pool close) | Fix connection pool; set `wait_timeout = 60` |
| DDL Cut-Over Spike | Write latency spikes 2–5 s; error count increases briefly | Lock wait timeouts during cut-over | WriteLatencySpike | Deploy request cut-over acquiring MDL lock | Schedule deploys during low-traffic; monitor cut-over timing |
| Index Drop Regression | Query P99 increases 10× after deploy | `type: ALL` in EXPLAIN | QueryPerformanceRegression | Index dropped as part of column change deploy request | Re-add index via new deploy request immediately |
| Sleep Branch Hit | New connections take 15–30 s; first query very slow | `branch is sleeping` | BranchSleepingInProduction | CI/CD or staging hitting sleeping development branch | Wake branch; disable sleep for non-production critical branches |
| Deadlock Storm | Deadlock rate > 1/min; application retry storms | `ERROR 1213 Deadlock` | DeadlockRate | High-concurrency writes with inconsistent row access order | Standardize row access order; reduce transaction scope |
| Import Stall | Import progress 0% for > 1 hour | No progress in import tool | ImportJobStalled | Source DB unreachable or rate-limiting PlanetScale import IPs | Whitelist PlanetScale import IPs; check source DB connectivity |
| WAL-Equivalent Lag | Query latency rising on replicas; read-after-write issues | No explicit error | ReplicationLag | Vitess replica behind primary (internal) | Contact PlanetScale support; route critical reads to primary via `/* @primary */` query annotation (HTTP SDK) |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `ERROR 1040 (HY000): Too many connections` | MySQL drivers (mysql2, JDBC, PyMySQL) | Plan connection limit exhausted | `SELECT count(*) FROM information_schema.processlist;` vs plan limit | Kill sleeping connections; reduce app pool size to ≤ 25; use HTTP SDK for serverless |
| `ERROR 1205 (HY000): Lock wait timeout exceeded` | All MySQL drivers | Row/table lock not released within `innodb_lock_wait_timeout` | `SELECT * FROM information_schema.innodb_lock_waits;` | Kill blocking transaction; retry at application layer |
| `ERROR 1213 (40001): Deadlock found when trying to get lock` | All MySQL drivers | Circular lock dependency between two transactions | `SHOW ENGINE INNODB STATUS\G` — look for LATEST DETECTED DEADLOCK | Application must retry on `1213`; fix lock acquisition order |
| `ERROR 2006 (HY000): MySQL server has gone away` | All MySQL TCP drivers | TCP connection dropped (idle timeout, VTGate restart) | Check `wait_timeout`: `SHOW VARIABLES LIKE 'wait_timeout';` | Implement `reconnect: true` in connection pool; validate connections on borrow |
| `ERROR 1146 (42S02): Table '<name>' doesn't exist` | All drivers | Deploy request applied drop or rename; wrong branch connected | `SHOW TABLES LIKE '<name>';`; verify branch in connection string | Review deploy request diff before applying; check app is connecting to `main` |
| `ERROR 1062 (23000): Duplicate entry '<val>' for key 'PRIMARY'` | All ORMs, drivers | Race condition inserting same PK; idempotency violation | `SELECT * FROM <table> WHERE id = '<val>';` | Use `INSERT ... ON DUPLICATE KEY UPDATE` or idempotency key |
| `VTGate: target: X/0/primary is not serving` | mysql2, go-sql-driver | Vitess shard unhealthy (PlanetScale infrastructure incident) | Check https://status.planetscale.com | Wait for PlanetScale recovery; implement retry with backoff |
| `Connection timed out` (15–30s delay on first connect) | All drivers | Development branch woken from sleep | `pscale branch show <db> <branch> --org <org>` — check for `sleeping` | Keep branch alive with periodic ping; disable sleep for important non-prod branches |
| `ERROR 1227 (42000): Access denied; you need (at least one of) the SUPER privilege(s)` | Flyway, Liquibase migration tools | Operation requires super privilege not available in PlanetScale | Review the DDL operation attempted | Replace DDL with PlanetScale deploy request workflow |
| HTTP `{"code":"unauthorized", "message":"..."}` | `@planetscale/database` SDK | Invalid or expired database password | Confirm password in Dashboard → Settings → Passwords | Rotate and redeploy credentials; check secret manager for stale values |
| `scatter` query returning millions of rows, high latency | `@planetscale/database` SDK, mysql2 | Query not using sharding key; VTGate sends to all shards | `EXPLAIN <query>;` — look for scatter routing in plan | Add sharding key to WHERE clause; add covering index |
| `ERROR 1292 (22007): Incorrect datetime value '0000-00-00'` | All drivers after schema change | Deploy request changed column type; existing data incompatible | `SELECT count(*) FROM <table> WHERE <col> = '0000-00-00';` | Backfill NULL before schema change; use `NO_ZERO_DATE` sql_mode aware app logic |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Scatter query ratio creeping up | Rows-read metric growing faster than rows-returned; billing rising | Query Insights → sort by `rows_read / rows_returned` ratio descending | 2–4 weeks | Add WHERE clause with sharding key; update vschema vindexes |
| Connection pool leak from serverless functions | Connection count rising monotonically despite constant request rate | `SELECT LEFT(host, LOCATE(':',host)-1) AS host, count(*) FROM information_schema.processlist GROUP BY host ORDER BY count DESC;` | 1–2 weeks | Switch serverless to `@planetscale/database` HTTP SDK; verify pool.end() called |
| Deploy request duration increasing over months | Average schema change time trending from 5 min to 60 min as tables grow | `pscale deploy-request list <db> --org <org>` — note recent durations | 4–6 weeks | Plan shard migration; archive old data; review index count before ALTER |
| Index fragmentation on high-churn tables | INSERT/UPDATE latency gradually rising; `ibdata1` growing | `SELECT table_name, data_free, data_length FROM information_schema.tables WHERE table_schema=DATABASE() ORDER BY data_free DESC LIMIT 10;` | 3–4 weeks | `OPTIMIZE TABLE <table>` during low-traffic (or rely on deploy request COPY phase) |
| Lock wait timeout frequency increasing | Error rate for `1205` errors rising week-over-week in Query Insights | Filter Query Insights for `Lock wait timeout` errors; track frequency | 1–2 weeks | Identify and fix long transaction patterns; add row-level lock hints |
| Sleep connection accumulation during off-peak | Morning connection count consistently higher than previous day; never fully draining | `SELECT command, count(*) FROM information_schema.processlist GROUP BY command;` — track Sleep count trend | 1 week | Set `wait_timeout = 60` in connection pool; enforce pool.end() in app shutdown hooks |
| Storage branch creep approaching plan limit | Number of branches growing; stale dev branches never deleted | `pscale branch list <db> --org <org>` — count and check last-access dates | 2 weeks | Enforce branch lifecycle policy; delete branches older than 30 days automatically |
| Row-read billing overage drift | Monthly rows-read approaching plan tier ceiling | PlanetScale dashboard → Usage → Rows Read (monthly trend) | 1 month | Audit and fix scatter queries; add LIMIT clauses; cache common reads |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# PlanetScale Full Health Snapshot
# Usage: export PS_ORG="my-org"; export PS_DB="my-db"; export MYSQL_HOST="aws-us-east-1.connect.psdb.cloud"
#        export MYSQL_USER="<user>"; export MYSQL_PASS="<pass>"; ./ps-health-snapshot.sh

MYSQL="mysql -h $MYSQL_HOST -u $MYSQL_USER -p$MYSQL_PASS -e"
echo "=== PlanetScale Health Snapshot: $(date -u) ==="

echo ""
echo "--- PlanetScale Platform Status ---"
curl -sf "https://status.planetscale.com/api/v2/status.json" | python3 -c "import sys,json; d=json.load(sys.stdin); print('Status:', d['status']['description'])"

echo ""
echo "--- Active Connection Count ---"
$MYSQL "SELECT count(*), command FROM information_schema.processlist GROUP BY command ORDER BY count(*) DESC;"

echo ""
echo "--- Long-Running Queries (>5s) ---"
$MYSQL "SELECT id, user, host, command, time, LEFT(info,120) AS query FROM information_schema.processlist WHERE time > 5 ORDER BY time DESC LIMIT 10;"

echo ""
echo "--- Open Deploy Requests ---"
pscale deploy-request list $PS_DB --org $PS_ORG 2>/dev/null || echo "pscale CLI not available"

echo ""
echo "--- InnoDB Engine Status (lock summary) ---"
$MYSQL "SHOW ENGINE INNODB STATUS\G" 2>&1 | grep -A 20 "TRANSACTIONS"

echo ""
echo "--- Active Long Transactions ---"
$MYSQL "SELECT trx_id, trx_state, trx_started, trx_mysql_thread_id, LEFT(trx_query,120) AS query FROM information_schema.innodb_trx ORDER BY trx_started ASC LIMIT 10;"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# PlanetScale Performance Triage
# Usage: export MYSQL_HOST/USER/PASS; ./ps-perf-triage.sh

MYSQL="mysql -h $MYSQL_HOST -u $MYSQL_USER -p$MYSQL_PASS -e"
echo "=== PlanetScale Performance Triage: $(date -u) ==="

echo ""
echo "--- Current Lock Waits ---"
$MYSQL "SELECT r.trx_mysql_thread_id AS waiting_thread, r.trx_query AS waiting_query, b.trx_mysql_thread_id AS blocking_thread, b.trx_query AS blocking_query FROM information_schema.innodb_lock_waits w JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id LIMIT 10;"

echo ""
echo "--- Table Index Statistics (high n_diff = high cardinality) ---"
$MYSQL "SELECT database_name, table_name, index_name, stat_value AS est_rows FROM mysql.innodb_index_stats WHERE stat_name='n_diff_pfx01' ORDER BY stat_value DESC LIMIT 15;"

echo ""
echo "--- Tables with High data_free (fragmentation) ---"
$MYSQL "SELECT table_name, ROUND(data_length/1024/1024,1) AS data_mb, ROUND(data_free/1024/1024,1) AS free_mb, ROUND(data_free*100.0/NULLIF(data_length+data_free,0),1) AS frag_pct FROM information_schema.tables WHERE table_schema=DATABASE() ORDER BY data_free DESC LIMIT 10;"

echo ""
echo "--- EXPLAIN for a Key Query (edit query below) ---"
# MYSQL="mysql ..." $MYSQL "EXPLAIN FORMAT=JSON SELECT * FROM orders WHERE customer_id = 1 LIMIT 10\G"
echo "Edit script to add specific query EXPLAIN"

echo ""
echo "--- Index Usage Check ---"
$MYSQL "SELECT table_name, index_name, column_name FROM information_schema.statistics WHERE table_schema=DATABASE() ORDER BY table_name, index_name, seq_in_index;"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# PlanetScale Connection and Resource Audit
# Usage: export MYSQL_HOST/USER/PASS; ./ps-connection-audit.sh

MYSQL="mysql -h $MYSQL_HOST -u $MYSQL_USER -p$MYSQL_PASS -e"
echo "=== PlanetScale Connection & Resource Audit: $(date -u) ==="

echo ""
echo "--- Connections by Client Host ---"
$MYSQL "SELECT LEFT(host, LOCATE(':',host)-1) AS client_host, command, count(*) AS conn_count FROM information_schema.processlist GROUP BY client_host, command ORDER BY conn_count DESC LIMIT 20;"

echo ""
echo "--- Sleeping Connections >60s ---"
$MYSQL "SELECT id, user, host, db, time FROM information_schema.processlist WHERE command='Sleep' AND time > 60 ORDER BY time DESC LIMIT 20;"

echo ""
echo "--- KILL statements for long sleeping connections ---"
$MYSQL "SELECT CONCAT('KILL ', id, ';') AS kill_cmd FROM information_schema.processlist WHERE command='Sleep' AND time > 300;" | grep KILL

echo ""
echo "--- Table Sizes (Top 10) ---"
$MYSQL "SELECT table_name, ROUND((data_length+index_length)/1024/1024,1) AS total_mb, ROUND(data_length/1024/1024,1) AS data_mb, ROUND(index_length/1024/1024,1) AS idx_mb FROM information_schema.tables WHERE table_schema=DATABASE() ORDER BY (data_length+index_length) DESC LIMIT 10;"

echo ""
echo "--- Branch Sleep Status ---"
pscale branch list $PS_DB --org $PS_ORG 2>/dev/null || echo "pscale CLI not configured"
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Serverless function connection flood | Connection count spikes on every deploy or cold start; `1040` errors during deployments | `SELECT LEFT(host,LOCATE(':',host)-1) AS host, count(*) FROM information_schema.processlist GROUP BY host ORDER BY count DESC;` | Reduce Lambda concurrency; switch to `@planetscale/database` HTTP SDK | Always use HTTP SDK for serverless; never use TCP MySQL driver in Lambda/Cloud Functions |
| Analytics scatter query starving OLTP | OLTP write latency spikes during reporting hours; `rows_read` metric sky-high | Query Insights → filter by time window → identify high `rows_read` queries | `KILL <thread_id>` on scatter query; add `MAX_EXECUTION_TIME` hint | Route analytics to a read-only branch; add sharding key to all analytics WHERE clauses |
| Deploy request cut-over MDL lock | Write latency spikes 2–5s during cut-over; some writes fail with `1205` | `SHOW PROCESSLIST;` — look for waiting threads referencing the migrating table during cut-over | Wait out cut-over (typically < 5s); retry failed writes at application layer | Schedule deploy request cut-over during low-traffic window; use retry logic for `1205` |
| Long transaction blocking DDL and writes | All queries to a table queue up; `innodb_lock_waits` grows | `SELECT trx_id, trx_started, trx_mysql_thread_id, trx_query FROM information_schema.innodb_trx ORDER BY trx_started ASC LIMIT 5;` | `KILL <blocking_thread_id>;` | Set `innodb_lock_wait_timeout = 5` for batch jobs; use `SET SESSION innodb_lock_wait_timeout = 5` |
| Bulk INSERT from data pipeline exhausting write IOPS | Write latency rising for all tables; `innodb_buffer_pool_wait_free` rising | Identify bulk-inserting connections: `SELECT user, LEFT(info,80) FROM information_schema.processlist WHERE command='Query' AND info LIKE '%INSERT%' ORDER BY time DESC;` | Add `SLEEP(0.01)` between INSERT batches; reduce batch size | Use chunked INSERT with row count limits; schedule bulk loads during off-peak |
| Index creation via deploy request consuming all IOPS | All queries slow during `COPY` phase of deploy request | `pscale deploy-request show <db> <dr> --org <org>` — check for `copy` phase active | No direct mitigation during copy; plan for slowdown window | Schedule large `ADD INDEX` deploy requests outside business hours |
| Deadlock storm from concurrent upserts | `ERROR 1213` appearing in logs at high rate; transaction rollback rate rising | `SHOW ENGINE INNODB STATUS\G` — review LATEST DETECTED DEADLOCK | Reduce concurrency of the upsert operation; add retry logic | Normalize row access order; use `INSERT ON DUPLICATE KEY UPDATE` consistently |
| Connection pool misconfigured ORM (n+1 per request) | Connection count equals concurrent request count; scales linearly | `SELECT LEFT(host,LOCATE(':',host)-1) AS host, count(*) FROM information_schema.processlist GROUP BY host;` — see many connections per host | Limit ORM pool size in config (`pool: 10`); add connection wait timeout | Set ORM pool size to 5–25 per app instance; use query batching to eliminate N+1 |
| Stale branch connections blocking VTGate routing | Slow queries to development branches affecting shared VTGate; routing latency rising | `pscale branch list <db> --org <org>` — check for sleeping or very old branches | Wake or delete stale branches | Enforce branch lifecycle: auto-delete branches inactive > 30 days |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| PlanetScale VTGate routing unavailable | All MySQL connections fail with `ERROR 2003: Can't connect to MySQL server` → application connection pool exhausts wait timeout → HTTP 503 cascade | All services using PlanetScale DB for that region | `mysql -h $MYSQL_HOST -u $MYSQL_USER -p$MYSQL_PASS -e "SELECT 1"` hangs or fails; `status.planetscale.com` shows incident | Enable application read-only mode; serve cached responses; trigger PlanetScale support ticket |
| Serverless Lambda cold-start connection flood | 500+ Lambda instances start simultaneously → each opens MySQL TCP connection → PlanetScale connection limit (`1040 Too many connections`) hit → all requests fail | All API endpoints backed by the affected DB branch | PlanetScale console shows connection spike; application logs: `ERROR 1040: Too many connections`; API error rate 100% | Switch Lambda functions to `@planetscale/database` HTTP SDK immediately to eliminate TCP connections |
| Deploy request cut-over blocking all writes for 5–30s | MDL (Metadata Lock) held during VTGate cut-over → all writes to migrating table queue → queue timeout (`innodb_lock_wait_timeout`) → `ERROR 1205` errors propagate to clients | All writes to the table under migration during cut-over window | `SHOW PROCESSLIST` shows dozens of threads `Waiting for table metadata lock`; write error spike in APM | Application must implement retry logic for `1205` errors; schedule cut-over during low-traffic window |
| Primary key exhaustion (INT overflow) | INSERT operations fail with `ERROR 1062: Duplicate entry '2147483647' for key 'PRIMARY'` → writes fail → data gap | All new record creation in the affected table | Application logs showing `ERROR 1062`; new row count stops increasing | `ALTER TABLE t MODIFY id BIGINT NOT NULL AUTO_INCREMENT;` via deploy request immediately |
| Branching feature leaving long-running transactions open | `innodb_trx` accumulates old transactions → undo log grows → all queries to affected tables slow → InnoDB lock waits cascade | All queries on tables touched by the long transaction | `SELECT trx_id, trx_started FROM information_schema.innodb_trx ORDER BY trx_started ASC LIMIT 5;` shows transactions hours old | `KILL <thread_id>` for the long-running transaction; investigate ORM transaction management |
| Database branch deleted with active connections | Active connections receive `ERROR 1049: Unknown database` → application connection pool retries against non-existent branch → repeated failures fill error logs | All services configured to connect to that specific branch | `pscale branch list $PS_DB --org $PS_ORG` no longer shows branch; application log flood of `ERROR 1049` | Recreate branch from main or redirect connection string to valid branch |
| Slow query causing full table scan on high-traffic table | Table locked in shared-read mode → concurrent writes queue → lock timeout cascade → write failures → reads also degrade | All reads and writes on the affected table; secondary tables via application logic | `SHOW PROCESSLIST` shows many threads `Waiting for table metadata lock`; query insights shows outlier `rows_read` query | `KILL <blocking_pid>`; add `MAX_EXECUTION_TIME(1000)` query hint; add index via deploy request |
| PlanetScale Boost (query caching) returning stale data | Application reads cached results after writes → data inconsistency visible to users → downstream workflows act on stale state | All read queries served by Boost for the affected query pattern | Results mismatch between `SELECT ... SQL_NO_CACHE` and cached path; user-reported stale data | Disable Boost for the affected query pattern via the PlanetScale dashboard (Boost tab) or annotate the query with `SQL_NO_CACHE` |
| Schema change via deploy request in wrong order (FK constraint) | Deploy request fails midway: `ERROR 1215: Cannot add foreign key constraint` → partial schema state → subsequent migrations fail | Schema consistency; data import may also fail | Deploy request status shows `Error`; `pscale deploy-request show $PS_DB <dr_number>` | Revert via new deploy request that drops the partially created object; fix FK order in migration |
| Region failover causing read-replica DNS flip | Application read replica connection resolves to new host → TLS certificate mismatch → `SSL: certificate verify failed` | All read-optimized queries until TLS is re-negotiated | Application logs: `SSL certificate problem: unable to get local issuer certificate`; read query error rate spikes | Disable certificate pinning temporarily; ensure `sslmode=verify-ca` not `verify-full` during failover window |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Deploy request applying ALTER TABLE ADD INDEX | All queries to table slow during COPY phase (up to hours for large tables) | During deploy request execution | `pscale deploy-request show $PS_DB <dr>` shows `copy` status; correlate with latency spike in APM | No rollback during COPY; wait for completion; schedule during off-peak for future large indexes |
| Switching from TCP MySQL driver to HTTP SDK | `AUTO_INCREMENT` last insert ID behavior changes; some ORM features unavailable | Immediately after SDK swap | Compare query behavior between TCP and HTTP SDK; check ORM compatibility notes | Revert to TCP driver if HTTP SDK incompatibility is critical; use `@planetscale/database` only for serverless |
| PlanetScale branch promotion (merge into main) | New schema live on main branch; application using old schema assumptions — missing column errors | Immediately after merge | Application logs: `ERROR 1054: Unknown column 'new_col'`; correlate with merge timestamp | Roll forward: deploy application version expecting new schema; or add default to allow old app to work |
| Connection string change (new branch for environment) | Application connects to wrong branch; data operations affect wrong dataset | Immediately after deploy | Compare `$MYSQL_HOST` branch identifier in connection string vs intended branch | Update connection string environment variable; verify with `SELECT DATABASE()` |
| `innodb_lock_wait_timeout` change | Applications that previously succeeded now get `ERROR 1205` on contested writes | Within minutes under concurrent write load | Compare lock timeout value before/after in DB settings; correlate with increased `ERROR 1205` rate | Revert timeout to previous value; investigate actual lock contention cause |
| Enable/disable PlanetScale Boost | Query plans change; some queries faster, some slower; cache invalidation on write may lag | Immediately for new queries; seconds for cache invalidation | Compare query latency before/after Boost toggle; use `SQL_NO_CACHE` to test uncached path | Disable Boost for regressed query patterns from the PlanetScale dashboard Boost tab; or annotate individual queries with `SQL_NO_CACHE` |
| Max connections limit change | Either connections queued (too low) or host resource pressure (too high) | Under normal traffic load immediately after change | Check connection count vs new limit in PlanetScale console; correlate with `ERROR 1040` rate | Revert max connections setting in PlanetScale dashboard |
| Password/credential rotation | All existing connection pools fail on reconnect with `ERROR 1045: Access denied` | On next connection recycle (often minutes) | `mysql -h $MYSQL_HOST -u $MYSQL_USER -p$NEW_PASS -e "SELECT 1"` works; old pass fails; correlate with rotation event | Update credentials in all app configs and secret manager; trigger rolling restart |
| Read-only region added to connection routing | Application writes hitting read replica → `ERROR 1290: read-only mode` | Immediately on first write to misrouted connection | Check application logs for `ERROR 1290`; `SHOW VARIABLES LIKE 'read_only'` on connected host | Force write connection to `@primary` annotation (PlanetScale HTTP SDK) or fix connection routing config |
| Schema migration that drops a column still read by application | Application queries fail with `ERROR 1054: Unknown column 'old_col' in 'field list'` | Immediately after deploy if app not updated first | Correlate deploy request completion with application `ERROR 1054` spike | Add column back via new deploy request; enforce column removal only after all app versions stop reading it |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Read-after-write inconsistency on read replicas | `SELECT * FROM orders WHERE id=LAST_INSERT_ID()` on replica returns empty immediately after write | Application reads from replica before replication propagates; user sees stale data after their own write | User-facing stale state; support tickets for "my update didn't save" | Use `/* @primary */` annotation (PlanetScale HTTP SDK) for reads that must reflect recent writes |
| Replication lag during peak write load | `pscale branch show $PS_DB main --org $PS_ORG` (look for lag metric); `SELECT TIMEDIFF(NOW(), MAX(updated_at)) FROM orders` on replica vs primary | Read replicas serving data minutes behind primary; stale inventory, stale user profiles | Incorrect data-dependent decisions (e.g., double-booking) | Route all critical reads to primary temporarily; reduce write load; monitor lag to recover |
| Deploy request partially applied (failed migration) | `pscale deploy-request show $PS_DB <dr>` shows `error` state; `SHOW TABLES` shows partial new schema | Some tables have new schema, others do not; application queries may fail on mixed state | Schema inconsistency between tables; referential integrity broken | Submit revert deploy request to undo partial changes; PlanetScale supports rollback for most DDL |
| Branch data drift from manual DML on non-main branches | `SELECT count(*) FROM users` differs between main and feature branch | Feature branch testing produces results inconsistent with production assumptions | Test results invalid; incorrect performance benchmarks | Delete drifted branch; re-branch from main: `pscale branch create $PS_DB <new-name> --from main` |
| Keyspace routing sending writes to wrong shard (Vitess misconfiguration) | `SHOW VITESS_SHARDS` shows unexpected routing; write to row shows up on wrong shard | Data inserted but not found by subsequent reads using primary key | Silent data loss — row exists on wrong shard but queries go to correct shard | Verify `vschema` (Vitess schema) for table sharding key; move row to correct shard via extract-delete-insert |
| Connection returning cached prepared statement for changed schema | `ERROR 1054: Unknown column 'col' in 'field list'` on previously working queries after schema change | Prepared statement cache holds old column references; application fails on schema change rollout | Write failures using prepared statements; data cannot be persisted | Reset connection pool: cycle all application instances; clear ORM prepared statement cache |
| Duplicate writes from retry logic after partial failure | `SHOW CREATE TABLE t` confirms no `UNIQUE` constraint; `SELECT count(*) FROM t WHERE request_id='X'` returns > 1 | Idempotency key not enforced at DB level; retry on timeout creates duplicate rows | Double charges, duplicate orders, or duplicate user records | Add unique constraint via deploy request; deduplicate existing rows: `DELETE FROM t WHERE id NOT IN (SELECT MIN(id) FROM t GROUP BY request_id)` |
| PlanetScale Boost cache serving stale post-write | `SELECT col FROM t WHERE id=1 SQL_NO_CACHE` vs same query without `SQL_NO_CACHE` returns different values | Writes succeed but reads return old values from Boost cache | User sees incorrect data after updates | Disable Boost for affected query pattern; force cache invalidation via PlanetScale Boost settings |
| Configuration drift between PlanetScale branches | `pscale branch show $PS_DB feature --org $PS_ORG` shows different `max_connections` vs main | Development branch behaves differently from production under load; bugs not caught in staging | Unexpected production failures not reproducible in development | Align branch settings via the PlanetScale dashboard (Branches → Settings) or by recreating the branch from `main`; standardize settings across all branches |
| Auto-increment gap after rollback | `SELECT MAX(id) FROM orders` shows gap after a failed batch insert rollback | Auto-increment counter advanced even though rows were not committed; gaps in IDs | Non-sequential IDs; may confuse pagination logic assuming contiguous IDs | Redesign pagination to use keyset (`WHERE id > X`) not offset; gaps in auto-increment are normal and not a data loss |

## Runbook Decision Trees

### Decision Tree 1: Write Latency Spike or Write Errors

```
Are writes returning errors or p99 write latency > 500 ms?
├── YES → Are errors `ERROR 1213 (40001): Deadlock`?
│         ├── YES → Root cause: concurrent upserts with conflicting row-access order
│         │         Check: pscale shell <db> <branch> -- "SHOW ENGINE INNODB STATUS\G" | grep -A 40 "LATEST DETECTED DEADLOCK"
│         │         Fix: normalize row access order in application; add retry logic for 1213;
│         │              use INSERT ON DUPLICATE KEY UPDATE consistently
│         └── NO  → Are errors `ERROR 1205 (HY000): Lock wait timeout`?
│                   ├── YES → Root cause: long-running transaction holding row lock
│                   │         Check: SELECT trx_id, trx_started, trx_mysql_thread_id, LEFT(trx_query,100)
│                   │                FROM information_schema.innodb_trx ORDER BY trx_started ASC LIMIT 5;
│                   │         Fix: KILL <blocking_thread_id>; set innodb_lock_wait_timeout = 5 for batch jobs
│                   └── NO  → Is a deploy request cut-over in progress?
│                             Check: pscale deploy-request list <db> --org <org> | grep -i "in_progress\|deploying"
│                             ├── YES → Expected transient MDL lock; wait up to 10 s for cut-over to complete
│                             └── NO  → Check VTGate health in PlanetScale dashboard;
│                                       escalate to PlanetScale support with connection string + error details
└── NO  → Is query latency high but no errors?
          Run: pscale shell <db> main -- "SHOW PROCESSLIST;"
          ├── Many rows with State='Sending data' → missing index, full scatter query
          │   Fix: add sharding-key index; run EXPLAIN on offending query
          └── Many rows with State='Locked' or 'Waiting for lock' →
              Identify blocker: SELECT r.trx_id waiting, b.trx_id blocking,
              b.trx_mysql_thread_id blocking_thread FROM information_schema.innodb_lock_waits w
              JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id
              JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id;
              Fix: KILL <blocking_thread_id>;
```

### Decision Tree 2: Connection Exhaustion (`ERROR 1040`)

```
Are applications getting `ERROR 1040 (08004): Too many connections`?
├── YES → Check current connection count vs PlanetScale tier limit:
│         SELECT count(*) FROM information_schema.processlist;
│         (limits vary by tier — check current limit in PlanetScale console; Scaler ~10k, Scaler Pro / Enterprise higher)
│         ├── Count near limit → Are there many idle/sleep connections?
│         │   Check: SELECT LEFT(host,LOCATE(':',host)-1) AS app_host, state, count(*)
│         │          FROM information_schema.processlist GROUP BY app_host, state ORDER BY count DESC;
│         │   ├── YES, idle connections dominant → Root cause: TCP MySQL driver in serverless functions
│         │   │   Fix: migrate to @planetscale/database HTTP SDK; reduce connection pool max_size
│         │   └── NO → Root cause: app connection pool misconfigured (no upper bound)
│         │            Fix: set pool size = min(25, (tier_limit / app_instance_count));
│         │                 add connection wait timeout to pool config
│         └── Count well below limit → Root cause: VTGate connection routing issue
│                                       Check: pscale branch show <db> <branch> --org <org>
│                                       If branch sleeping → wake by establishing a connection (e.g. pscale shell <db> <branch> --org <org>); branches wake on first access
└── NO  → Are intermittent `2006 MySQL server has gone away` errors appearing?
          ├── YES → Root cause: idle connections timing out at PlanetScale proxy (8-hour idle timeout)
          │         Fix: set connection pool `keepalive_timeout = 3600`;
          │              add `wait_timeout` ping in pool health check
          └── NO  → Escalate with: SHOW STATUS LIKE 'Threads_%'; output + error log sample
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Scatter query exhausting row-reads quota | Monthly row-read billing units spike; Query Insights shows `rows_read` in billions | PlanetScale console → Query Insights → sort by `rows_read` | Tier overage charges; potential query throttling | Add `MAX_EXECUTION_TIME` hint; kill runaway query; add sharding-key index | Require sharding-key in all WHERE clauses; enforce via query linting in CI |
| Runaway batch INSERT without row-limit guard | Storage and row-count billing grow unbounded | `SELECT COUNT(*) FROM <table>;` vs expected count | Storage cost overrun; row-count tier violation | Pause ingestion pipeline; delete duplicates | Add idempotency check in pipeline; set max-rows-per-run guard |
| Unused development branches accumulating storage | Branch storage cost growing; multiple stale branches visible | `pscale branch list <db> --org <org>` — check age of each branch | Monthly storage cost overrun | `pscale branch delete <db> <branch> --org <org>` for each stale branch | Enforce branch lifecycle: auto-delete after 30 days of inactivity |
| Deploy request leaving shadow table after failed migration | `_<table>_new` tables consuming duplicate storage during incomplete deploy | `SHOW TABLES LIKE '\_%';` in affected database | Storage cost × 2 for affected tables | `pscale deploy-request revert <db> <dr> --org <org>`; drop shadow table manually if revert unavailable | Always monitor deploy request phase; set alerting on `deploy_request_status != completed` |
| Analytics query via TCP driver causing full table scan | `rows_examined` / `rows_read` metric sky-high; OLTP latency rises | `pscale shell <db> main -- "SHOW PROCESSLIST;"` — look for long-running SELECT with high rows_read | Row-reads quota exhaustion | Kill query: `KILL <thread_id>;`; route analytics to read-only branch | Provision dedicated read-only branch for analytics; enforce read-only role |
| N+1 query pattern multiplying connection and row-read count | Connection count equals request count; row-reads × N | PlanetScale Query Insights → filter last 1 h → sort by `execution_count` | Row-reads quota; connection exhaustion | Add Redis/Memcached cache layer; batch queries | ORM query auditing: use `EXPLAIN` in staging; disallow queries inside loops |
| Large transaction holding open for minutes | `innodb_trx` shows transaction open > 2 min; other writers queue up | `SELECT trx_id, trx_started, now()-trx_started AS age FROM information_schema.innodb_trx ORDER BY trx_started;` | Write throughput blocked | `KILL <thread_id>`; implement transaction timeout in app | Set `innodb_lock_wait_timeout = 10`; break large transactions into smaller chunks |
| Excessive deploy requests via CI/CD without review gate | Multiple simultaneous DDL changes; VTGate routing instability | `pscale deploy-request list <db> --org <org>` — count open requests | Schema instability; potential data loss if conflicting DDLs | Pause CI/CD deploy-request creation; resolve conflicts manually | Require human approval gate before `pscale deploy-request deploy`; serialize schema changes |
| Unindexed foreign-key column causing lock escalation | Lock wait time increases; writes blocked during INSERT/UPDATE on child table | `EXPLAIN SELECT ... FROM child WHERE fk_column = ?;` — look for `type: ALL` | Write blocking for all rows in child table | Add index: `ALTER TABLE child ADD INDEX idx_fk (fk_column);` via deploy request | Enforce FK columns are always indexed; add migration linter to CI |
| PlanetScale HTTP SDK retry storm from misconfigured exponential backoff | API call count × 100; rate-limit errors cascade | App logs: `grep "429\|retry" /var/log/app/*.log \| wc -l` | API quota exhaustion; cascading latency | Deploy circuit breaker; temporarily increase backoff base delay | Implement proper exponential backoff with jitter; set max retry count = 3 |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot shard from non-uniform sharding key | One VTTablet CPU high; other shards idle; write latency rising | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "SHOW STATUS LIKE 'Queries';"` per branch; PlanetScale console → Query Insights → sort by `rows_examined` | All writes hitting a single keyspace range due to monotonically increasing PK | Switch to UUID or hash-based sharding key; avoid auto-increment for high-volume tables |
| Connection pool exhaustion from long-running transactions | VTGate logs `no available connection`; new connections refused | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "SELECT count(*), command FROM information_schema.processlist GROUP BY command;"` | Application holding connections open across HTTP request lifecycle | Use transaction pooling; ensure `BEGIN`/`COMMIT` are in same function scope; set `wait_timeout=30` |
| GC/memory pressure in VTGate row materialisation | VTGate pod OOM-restarting; memory spike on large SELECT | PlanetScale console metrics → Memory; `pscale shell $PS_DB $PS_BRANCH -- "EXPLAIN SELECT ..."` to check rows estimate | Large result set materialised in VTGate memory; no server-side cursor | Add `LIMIT`; use cursor-based pagination with `WHERE id > :last_id ORDER BY id LIMIT 1000` |
| Thread pool saturation in MySQL backend from heavy analytics query | OLTP queries queueing; `Threads_running` near `thread_pool_size` | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "SHOW STATUS LIKE 'Threads_running';"` | Analytical full-table-scan occupying thread pool slots | Kill query: `pscale shell ... -- "KILL <thread_id>;"` ; route analytics to read-only branch |
| Slow query from missing index on foreign key column | `rows_examined` >> `rows_returned` in Query Insights; write blocking on child table inserts | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "EXPLAIN SELECT * FROM child WHERE fk_col = 1;"` | InnoDB lock propagation on unindexed FK column | Add index via deploy request: `ALTER TABLE child ADD INDEX idx_fk (fk_col);` |
| CPU steal from PlanetScale shared infrastructure | Latency p99 intermittently high without query load change; correlates with other tenants' peak | PlanetScale console → Performance → compare latency heatmap vs baseline; check `SHOW STATUS LIKE 'Queries';` rate vs latency | Shared infrastructure noisy neighbour on MySQL backend | Upgrade PlanetScale plan for dedicated compute; open support ticket with latency evidence |
| Lock contention during deploy request shadow table cutover | Query latency spike for 100–500 ms during schema migration cutover | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "SELECT r.trx_id, b.trx_mysql_thread_id, r.trx_query FROM information_schema.innodb_lock_waits w JOIN information_schema.innodb_trx r ON r.trx_id=w.requesting_trx_id JOIN information_schema.innodb_trx b ON b.trx_id=w.blocking_trx_id;"` | PlanetScale deploy request cutover acquires brief table lock during rename | Schedule cutover during low-traffic window; set `--wait-for-active-connections` option in deploy request settings |
| Serialization overhead from large TEXT/JSON column in hot-path query | Query CPU cost high despite small row count; `bytes_sent` metric elevated | `pscale shell $PS_DB $PS_BRANCH -- "SELECT avg(length(json_col)) FROM hot_table LIMIT 1000;"` | Returning large JSON blob in every row of high-frequency query | Use generated columns to extract frequently-queried JSON fields; return only needed columns |
| Batch size misconfiguration: single-row INSERT in loop | Write throughput 100× below capability; `Com_insert` count high relative to rows inserted | `pscale shell $PS_DB $PS_BRANCH -- "SHOW STATUS LIKE 'Com_insert'; SHOW STATUS LIKE 'Handler_write';"` | ORM or application issuing one INSERT per record | Batch inserts: `INSERT INTO t (a,b) VALUES (?,?),(?,?),...` with 500 rows per statement |
| Downstream VTGate dependency latency from cross-region deploy | Latency rises after branch promotion or regional failover | `pscale branch list $PS_DB --org $PS_ORG` — check region field; compare app deployment region vs branch region | Application and database branch in different PlanetScale regions after promotion | Create new branch in same region as application; update connection string region parameter |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on PlanetScale connection | `mysql: SSL connection error: error:14090086`: certificate verify failed | `openssl s_client -connect <ps-host>:3306 -starttls mysql 2>/dev/null | openssl x509 -noout -dates` | All application connections to PlanetScale fail | PlanetScale manages server certs; if custom CA pinned, update `ssl_ca` path in connection string; verify with `pscale connect` |
| mTLS rotation failure for private connections (Business tier) | Connection to private endpoint fails; `pscale connect` returns TLS handshake error | `pscale connect $PS_DB $PS_BRANCH --org $PS_ORG --port 3309` verbose output for TLS errors | Application cannot reach database via private IP | Re-run `pscale auth login` to refresh tokens; check `~/.config/planetscale/config.json` for expired credentials |
| DNS resolution failure for PlanetScale host | `ERROR 2005 (HY000): Unknown MySQL server host`; `dig +short $PS_HOST` returns nothing | `dig +short $PS_HOST` and `nslookup $PS_HOST 8.8.8.8` | All application connections fail | Check `/etc/resolv.conf`; `systemd-resolve --flush-caches`; verify `$PS_HOST` is correct from `pscale connect` output |
| TCP connection exhaustion from connection leak | `pscale shell` returns `Too many connections`; new app instances fail to connect | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "SELECT count(*), host, state FROM information_schema.processlist GROUP BY host, state ORDER BY count(*) DESC;"` | Application not closing connections; missing connection pool or pool misconfigured | Kill idle connections: `pscale shell ... -- "KILL <id>;"` for each idle; deploy connection pool (e.g., ProxySQL) |
| Load balancer misconfiguration on PlanetScale proxy endpoint | Connections routed to wrong branch; queries return wrong data | `pscale connect $PS_DB $PS_BRANCH --org $PS_ORG --port 3309` and verify `SELECT @@hostname;` returns expected branch hostname | Wrong data returned; potential cross-environment data exposure | Verify connection string branch name; `pscale branch list $PS_DB --org $PS_ORG`; update `DATABASE_URL` in app env |
| Packet loss between app and PlanetScale proxy | Intermittent query failures; MySQL `errno 2013` (Lost connection); no pattern in query type | `mtr --report --report-cycles 30 <ps-proxy-host>` to find lossy hop | ISP or cloud transit network packet loss | `ping -c 100 <ps-proxy-host>` to quantify; increase `net_read_timeout` and `net_write_timeout` in connection string |
| MTU mismatch causing large query results to fragment silently | Large SELECT succeeds locally but fails over VPN; no error, connection drops | `ping -M do -s 1400 <ps-proxy-host>` — failure indicates MTU < 1428 | Queries with large result sets fail intermittently | Set `ip link set dev <vpn-iface> mtu 1350`; configure TCP MSS clamping on VPN gateway |
| Firewall rule blocking port 3306/3309 to PlanetScale | All connections time out; `telnet <ps-host> 3306` hangs | `nc -zv <ps-host> 3306` and `nc -zv <ps-host> 3309` | Complete database outage from application | Add egress rule for `<ps-proxy-ip>/32:3306` and `3309`; check security group and NACL if AWS |
| SSL handshake timeout for PlanetScale via corporate proxy | MySQL connection timeout > 10 s; `SSL: errno 35` or similar in MySQL client | `mysql -h $PS_HOST -u $PS_USER -p --ssl-mode=REQUIRED --connect-timeout=5` — if fails, proxy is intercepting | Database completely unreachable for application | Add PlanetScale proxy hosts to SSL inspection bypass list in corporate proxy |
| Connection reset mid-query for large INSERT payload | `MySQL server has gone away` during bulk INSERT > 64 MB | `pscale shell $PS_DB $PS_BRANCH -- "SHOW VARIABLES LIKE 'max_allowed_packet';"` | Partial write; data integrity risk | Increase `max_allowed_packet` via PlanetScale support request; split bulk INSERT into ≤ 16 MB chunks |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of application connection pool | App container restarts; `kubectl describe pod` shows `OOMKilled`; DB connections orphaned | `kubectl describe pod <app-pod> | grep -A5 OOMKilled`; `pscale shell $PS_DB $PS_BRANCH -- "SELECT count(*) FROM information_schema.processlist WHERE host LIKE '<app-ip>%';"` | Kill orphaned connections from killed pod's IP; reduce pool `max_connections` in app config | Set `resources.limits.memory` in Kubernetes; tune pool `max_open_conns` to match PlanetScale plan limit |
| Row-reads quota exhaustion (PlanetScale billing) | HTTP 429 or query throttling; PlanetScale console shows row-reads near plan limit | PlanetScale console → Billing → Usage → row reads; `pscale shell ... -- "SHOW STATUS LIKE 'Handler_read%';"` | Kill runaway queries: `pscale shell ... -- "KILL QUERY <id>;"` | Add index on all WHERE columns; set `MAX_EXECUTION_TIME` hint on analytical queries |
| Storage quota exhaustion | Deploy requests fail; branch creation fails; `pscale branch list` shows storage near limit | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "SELECT table_schema, ROUND(SUM(data_length+index_length)/1073741824,2) AS gb FROM information_schema.tables GROUP BY table_schema;"` | Delete stale development branches: `pscale branch delete $PS_DB <stale-branch> --org $PS_ORG` | Enforce branch TTL; archive old data to cold storage; use MySQL native partitioning with periodic `ALTER TABLE ... DROP PARTITION` |
| File descriptor exhaustion in PlanetScale proxy client | `OSError: Too many open files` in application; `pscale connect` fails | `ls -l /proc/$(pgrep -f "pscale connect")/fd | wc -l` | Restart `pscale connect` proxy; `ulimit -n 65536` for process | Set `LimitNOFILE=65536` in systemd unit; use managed PlanetScale connection string directly in production |
| Inode exhaustion from `pscale` CLI temporary files | `pscale` commands fail; `touch` returns no space; disk has free space | `df -i ~/.config/planetscale`; `ls -la ~/.config/planetscale/tmp/ | wc -l` | `rm -rf ~/.config/planetscale/tmp/*`; restart pscale CLI | Add `tmpwatch` or cron to clean `~/.config/planetscale/tmp` weekly |
| CPU throttle on app host from runaway query fan-out | Application CPU 100%; MySQL thread CPU also high; N+1 query pattern | `pscale shell $PS_DB $PS_BRANCH -- "SELECT count(*), LEFT(info,80) AS query FROM information_schema.processlist GROUP BY LEFT(info,80) ORDER BY count(*) DESC LIMIT 10;"` | Kill redundant queries; add Redis cache layer | Add query middleware to log and block queries executing > 10× per request |
| Swap exhaustion from large in-memory result sets | Application OOM-killed after swap fills; `vmstat` shows sustained swap activity | `free -h`; `vmstat 1 5` | Restart application; reduce query page size | Stream large result sets with server-side cursors; set `innodb_buffer_pool_size` appropriately |
| Kernel PID limit from forked database migration workers | Fork fails during schema migration; `ENOSPC` on process creation | `cat /proc/sys/kernel/pid_max`; `ps aux | grep pscale | wc -l` | `sysctl -w kernel.pid_max=131072` | Serialize migrations; use `pscale deploy-request` workflow instead of direct DDL workers |
| Network socket buffer exhaustion during bulk data load | Bulk INSERT stalls; socket send buffer full; throughput drops to near zero | `ss -m | grep skmem`; `sysctl net.core.wmem_max` | `sysctl -w net.core.wmem_max=16777216 net.core.rmem_max=16777216` | Tune socket buffers before large migrations; use `LOAD DATA LOCAL INFILE` with throttling |
| Ephemeral port exhaustion from connection-per-query pattern | `Cannot assign requested address`; all new MySQL connections fail | `ss -s | grep TIME-WAIT`; `cat /proc/sys/net/ipv4/ip_local_port_range` | `sysctl -w net.ipv4.tcp_fin_timeout=15 net.ipv4.tcp_tw_reuse=1` | Mandatory connection pooling (ProxySQL or app-level pool); never open connection per query |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation causing duplicate rows from retry | `SELECT COUNT(*), COUNT(DISTINCT idempotency_key) FROM orders;` shows count > distinct keys | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "SELECT idempotency_key, COUNT(*) FROM orders GROUP BY idempotency_key HAVING COUNT(*) > 1 LIMIT 20;"` | Duplicate orders/events; billing or inventory errors | `DELETE t1 FROM orders t1 JOIN orders t2 WHERE t1.id < t2.id AND t1.idempotency_key = t2.idempotency_key;`; add `UNIQUE` constraint on `idempotency_key` via deploy request |
| Saga partial failure: payment charged but order not created | Payment service committed; order service rolled back; customer charged with no order | `pscale shell $PS_DB main -- "SELECT id FROM payments WHERE order_id NOT IN (SELECT id FROM orders);"` | Data inconsistency; customer support escalation | Create compensating order record or trigger refund; implement outbox pattern with `pending_events` table |
| Message replay causing stale state overwrite | Event consumer replays Kafka partition from offset 0; old events overwrite current DB state | `pscale shell $PS_DB $PS_BRANCH -- "SELECT updated_at FROM <table> WHERE id = ? ;"` compare to event timestamp | Data rolled back to old state; loss of recent user actions | Add `WHERE updated_at < $event_ts` guard to all consumer UPDATEs; use optimistic locking with version column |
| Cross-service deadlock between two microservices updating same rows in opposite order | `SHOW ENGINE INNODB STATUS\G` shows deadlock between two different application hosts | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "SHOW ENGINE INNODB STATUS\G" | grep -A30 "LATEST DETECTED DEADLOCK"` | One transaction rolled back per InnoDB deadlock detection; silent retry may cascade | Enforce consistent row-lock ordering across services; use advisory locking via `GET_LOCK('resource_key', 10)` |
| Out-of-order event processing from Kafka partition rebalance | Newer event processed before older one; final DB state reflects older event | `kafka-consumer-groups.sh --bootstrap-server <broker> --describe --group <group>` check `LAG` and `CURRENT-OFFSET` | Stale state for affected records; requires reconciliation | Add `version` column; `UPDATE t SET ... WHERE id = ? AND version < ?`; log skipped events for reconciliation |
| At-least-once delivery duplicate from Kafka consumer retry | Same event processed twice; `INSERT` runs twice creating duplicate records | `pscale shell $PS_DB $PS_BRANCH -- "SELECT event_id, COUNT(*) FROM processed_events GROUP BY event_id HAVING COUNT(*) > 1;"` | Duplicate records; potential double-billing | Use `INSERT ... ON DUPLICATE KEY UPDATE processed_at = VALUES(processed_at)` with `event_id` as unique key |
| Compensating transaction failure after failed deploy request revert | Deploy request reverted but shadow table data partially applied; schema inconsistent | `pscale deploy-request list $PS_DB --org $PS_ORG`; `pscale shell $PS_DB $PS_BRANCH -- "SHOW TABLES LIKE '\\_%';"` for orphaned shadow tables | Schema inconsistency; queries may fail or return wrong results | `pscale deploy-request revert $PS_DB <number> --org $PS_ORG`; drop orphaned shadow tables: `pscale shell ... -- "DROP TABLE _<table>_new;"` |
| Distributed lock expiry mid-migration: two deploy requests active simultaneously | Two deploy requests in `running` state for same table; shadow table conflict | `pscale deploy-request list $PS_DB --org $PS_ORG | grep -i running` | Schema corruption risk; deploy requests may conflict | Cancel the later request: `pscale deploy-request cancel $PS_DB <number> --org $PS_ORG`; serialize all DDL changes via single deploy request pipeline |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: one tenant's analytical query monopolising VTTablet | PlanetScale console → Query Insights shows one query consuming 90% of execution time | All tenants see elevated query latency | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "KILL QUERY <thread_id>;"` | Route analytics to read-only branch; add `MAX_EXECUTION_TIME(5000)` hint to tenant queries |
| Memory pressure: one tenant materialising large result sets in VTGate | VTGate pod OOM-restarting; all tenants lose connections during restart | Connection drops for all tenants; queries fail mid-execution | No direct kill per-tenant; throttle tenant's query rate at application proxy layer | Enforce `LIMIT` on all tenant queries at application layer; add row-count quota check before executing query |
| Disk I/O saturation from tenant bulk write | PlanetScale console → Insights → Storage shows one table growing rapidly; write latency rising for all | Insert/update latency increases across all tenant tables | `pscale shell $PS_DB $PS_BRANCH -- "SELECT table_name, data_length, index_length FROM information_schema.tables WHERE table_schema='$PS_DB' ORDER BY data_length DESC LIMIT 5;"` | Throttle tenant ingest rate at application layer; defer bulk writes to off-peak window |
| Network bandwidth monopoly from tenant large row reads | PlanetScale console → Insights shows high `rows_read` for single tenant; response payload bytes elevated | Network egress throttled; query response times rise for other tenants | Block tenant's heavy query: `pscale shell ... -- "KILL QUERY <thread_id>;"` | Add pagination enforcement at application layer; alert on `rows_read > 100000` per query |
| Connection pool starvation: tenant leaking idle connections | `pscale shell $PS_DB $PS_BRANCH -- "SELECT user, host, count(*) FROM information_schema.processlist WHERE command='Sleep' GROUP BY user, host ORDER BY count(*) DESC LIMIT 10;"` | Other tenants get `Too many connections` errors | `pscale shell ... -- "KILL <process_id>;"` for idle connections from offending tenant | Set `wait_timeout=30` to auto-kill idle connections; enforce per-tenant connection pool max |
| Quota enforcement gap: tenant bypassing row-read limits via `SELECT *` | `pscale shell ... -- "SELECT count(1) FROM large_table;"` reveals tenant accessing > 10M rows per query | Row-read billing quota exhausted; service throttled for all tenants | Add `MAX_EXECUTION_TIME(3000)` at DB level: open PlanetScale support to set query advisor rules | Implement application-layer row-read quota tracking per tenant; block queries exceeding daily row budget |
| Cross-tenant data leak risk via shared `tenant_id` filter omission | `pscale shell ... -- "SELECT * FROM orders WHERE id = <cross-tenant-id>;"` — check if row belongs to another tenant | Tenant A can access Tenant B's data if application omits WHERE tenant_id filter | No DB-level enforcement without RLS; check application code for missing filter | Enable PlanetScale's Column-Level Encryption for sensitive fields; enforce `WHERE tenant_id = ?` via ORM middleware |
| Rate limit bypass: tenant using multiple connection strings to exceed plan limits | Multiple `DATABASE_URL` secrets configured for same tenant, all hammering DB | Shared plan row-read quota exhausted; all tenants throttled | Revoke duplicate passwords: PlanetScale console → Settings → Passwords | Issue one password per tenant with clear naming; track usage via Audit Log; enforce plan limits |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure for PlanetScale connection count | `planetscale_connections_total` absent in Grafana; gaps in dashboard | PlanetScale does not expose Prometheus endpoint natively; custom scraper failing | `pscale shell $PS_DB $PS_BRANCH -- "SELECT count(*) FROM information_schema.processlist;"` manually | Deploy custom scraper polling PlanetScale API and `SHOW STATUS`; alert on `up{job="planetscale_scraper"}==0` |
| Trace sampling gap: missing slow deploy-request cutover latency | Application APM shows no traces for the 100–500 ms latency spike during schema cutover | APM head-based sampling at 1% misses short-duration but high-impact spikes | `pscale deploy-request list $PS_DB --org $PS_ORG` check `started_at`/`completed_at` timestamps for cutover duration | Switch to tail-based sampling in APM; add PlanetScale deploy request webhook to emit event to APM on cutover start/end |
| Log pipeline silent drop during bulk write burst | Application MySQL error logs missing from Splunk during high-write event | Filebeat/Fluentd buffer overflow during burst; logs dropped silently | `pscale shell $PS_DB $PS_BRANCH -- "SHOW STATUS LIKE 'Com_insert';"` to verify insert count vs application-side counter | Increase Fluentd buffer queue; switch to `overflow_action block`; add rate counter at app layer |
| Alert rule misconfiguration: PlanetScale row-read quota alert never fires | Tenant exhausts row-read quota; service throttled; no alert triggered | Alert watching PlanetScale API response for `429` but quota exhaustion returns `503` | `curl -H "Authorization: $PS_SERVICE_TOKEN" "https://api.planetscale.com/v1/organizations/$PS_ORG/databases/$PS_DB/branches/$PS_BRANCH"` — inspect quota fields | Add alert for both `429` and `503` from PlanetScale; subscribe to PlanetScale webhook for quota breach events |
| Cardinality explosion: per-query-id metrics label | Prometheus OOM; dashboard load > 30 s after adding query hash label to metrics | Developer added SQL query hash as Prometheus label; millions of unique queries = millions of series | `curl http://localhost:9090/api/v1/label/__name__/values | jq length` to check series count | Remove query-level cardinality labels; aggregate by operation type (`read`/`write`/`ddl`) only |
| Missing health endpoint for pscale proxy sidecar | `pscale connect` sidecar crashes; app cannot reach DB; no alert fires | `pscale connect` runs as background process with no HTTP health endpoint | `pgrep -a pscale`; `nc -zv 127.0.0.1 3309` to verify proxy alive | Wrap `pscale connect` in systemd unit with `RestartAlways`; add TCP health check to monitoring; alert on port 3309 unreachable |
| Instrumentation gap: deploy request duration not tracked | Long-running deploy requests blocking team velocity; no SLO on migration time | PlanetScale console shows deploy status but does not push metrics to APM | `pscale deploy-request list $PS_DB --org $PS_ORG --json | jq '.[] | {id,state,started_at,completed_at}'` | Scrape PlanetScale API for deploy request state; publish `deploy_request_duration_seconds` Prometheus gauge |
| Alertmanager outage silencing PlanetScale availability alerts | PlanetScale failover event undetected for 15 min; no on-call page | Alertmanager pod OOM-killed; all alert notifications queued but not delivered | `amtool alert query alertname=PlanetScaleUnreachable`; check Alertmanager pod: `kubectl get pod -l app=alertmanager` | Deploy Alertmanager in HA mode; configure dead-man's-switch alert to external healthcheck service |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| PlanetScale MySQL minor version upgrade rollback | Queries relying on previous-version behavior fail after a MySQL 8.0 minor-version bump; `pscale shell` returns syntax errors or behavior differences | `pscale shell $PS_DB $PS_BRANCH --org $PS_ORG -- "SELECT @@version;"` | Open PlanetScale support ticket to roll back cluster version; no self-service rollback available | Test all queries against the new MySQL version in a staging branch first: `pscale branch create $PS_DB staging-upgrade --org $PS_ORG` |
| Schema migration partial completion via deploy request | Deploy request shows `running` but stops mid-migration; half of rows have new column value | `pscale deploy-request list $PS_DB --org $PS_ORG`; `pscale shell ... -- "SELECT SUM(new_col IS NULL) FROM table;"` | `pscale deploy-request revert $PS_DB <number> --org $PS_ORG` | Always test deploy requests on staging branch first; monitor `deploy-request` state via webhook |
| Rolling upgrade version skew: old app using old column, new app using renamed column | Mixed deployment returning `Unknown column` errors for half of requests | `kubectl get pods -o jsonpath='{range .items[*]}{.spec.containers[*].image}{"\n"}{end}' | sort | uniq -c` | `kubectl rollout undo deployment/<app>` to revert to old app version | Keep old column alive during transition with `RENAME` + keep old name as alias; deploy app migration in two phases |
| Zero-downtime migration gone wrong: ghost table cutover fails | `pscale deploy-request` in `error` state; table swap failed; `_<table>_new` orphaned | `pscale shell $PS_DB $PS_BRANCH -- "SHOW TABLES LIKE '\_%';"` — orphaned shadow tables present | `pscale deploy-request revert $PS_DB <number> --org $PS_ORG`; `pscale shell ... -- "DROP TABLE _<table>_new;"` if safe | Enable `--wait-for-active-connections` in deploy request; test with production-sized table in staging |
| Config format change: `utf8mb3` to `utf8mb4` charset breaking old queries | Queries with exact byte-length string comparisons fail after charset migration | `pscale shell $PS_DB $PS_BRANCH -- "SHOW TABLE STATUS WHERE Name='<table>';"` — check `Collation` field | Open new deploy request to revert charset; or add `COLLATE utf8mb3_general_ci` to affected queries | Audit all queries for charset assumptions before migration; test in staging branch with real data |
| Data format incompatibility: DATETIME precision change causing app errors | Application receives `1292 Incorrect datetime value` after column type changed from `DATETIME` to `DATETIME(6)` | `pscale shell $PS_DB $PS_BRANCH -- "SHOW COLUMNS FROM <table> WHERE Field='created_at';"` | Revert column type via deploy request: `ALTER TABLE t MODIFY created_at DATETIME;` | Test all application datetime parsing against new precision in staging; update ORM mappings before deploying |
| Feature flag rollout causing regression: query plan change after index addition | New index added via deploy request causes optimizer to choose different plan; query regression | `pscale shell $PS_DB $PS_BRANCH -- "EXPLAIN SELECT ..."` — compare execution plan before/after index | `pscale deploy-request revert $PS_DB <number> --org $PS_ORG` to remove new index | Use `FORCE INDEX` hint in affected queries during transition; validate query plan in staging before promoting deploy request |
| Dependency version conflict: ORM version incompatible with PlanetScale's vitess dialect | `SQLSTATE[42000]: Syntax error` for queries using MySQL features not supported by Vitess (e.g., multi-table DELETE) | `pscale shell $PS_DB $PS_BRANCH -- "<failing-query>"` to confirm Vitess dialect restriction | Rewrite query to Vitess-compatible syntax: replace multi-table DELETE with subquery or single-table form | Check PlanetScale Vitess compatibility docs before ORM upgrade; run full integration test suite against PlanetScale staging |

## Kernel/OS & Host-Level Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| OOM killer targets pscale connect proxy | Application loses DB connectivity; `pscale connect` process killed; queries return `connection refused` on localhost:3306 | `dmesg -T | grep -i 'oom.*pscale'`; `journalctl -k --since "1h ago" | grep -i killed` | All application queries routed through local proxy fail; reconnection storm when proxy restarts | Set `oom_score_adj=-500` for pscale connect process; wrap in systemd unit with `OOMScoreAdjust=-500 MemoryMax=256M`; add TCP health check on proxy port |
| Inode exhaustion from PlanetScale query cache temp files | `pscale shell` commands fail with `No space left on device` despite disk having free space | `df -i /tmp`; `find /tmp -name 'pscale*' -type f | wc -l` | Query Insights exports, deploy request diffs, and local shell sessions all fail; branch operations return OS errors | Add cron to clean `/tmp/pscale*` files older than 1h; mount `/tmp` on separate filesystem; set `TMPDIR` to dedicated volume |
| CPU steal on shared cloud instance delays VTGate response parsing | PlanetScale queries show elevated p99 latency; application timeout errors despite healthy PlanetScale dashboard | `sar -u 1 5 | grep steal`; `vmstat 1 5` — check `st` column; `pscale shell $PS_DB $PS_BRANCH -- "SHOW STATUS LIKE 'Slow_queries';"` | Client-side timeout fires before VTGate response arrives; application retries amplify load on PlanetScale cluster | Migrate to dedicated/burstable instance type; increase client-side query timeout; add `%steal` > 10% alert |
| NTP clock skew breaks PlanetScale API token validation | `pscale` CLI returns `401 Unauthorized` or `token expired` despite valid credentials | `chronyc tracking | grep "System time"`; `timedatectl status | grep "synchronized"` | All `pscale` CLI operations fail: branch creation, deploy requests, shell sessions; CI/CD pipelines using PlanetScale stall | `chronyc makestep`; enable `chronyd` or `ntpd`; alert on `abs(clock_skew_seconds) > 1` |
| File descriptor exhaustion from leaked pscale connect sessions | New `pscale connect` invocations fail with `too many open files`; existing connections remain functional | `ls /proc/$(pgrep pscale)/fd | wc -l`; `ulimit -n`; `ss -tnp | grep pscale | wc -l` | Cannot establish new proxy tunnels to PlanetScale branches; deploy request previews and staging branch access blocked | Set `LimitNOFILE=65536` in systemd unit; fix application connection leak closing `pscale connect` sessions; add fd count monitoring |
| TCP conntrack table full drops PlanetScale MySQL connections | Intermittent `Can't connect to MySQL server` errors; `pscale connect` proxy shows no issues | `dmesg | grep "nf_conntrack: table full"`; `sysctl net.netfilter.nf_conntrack_count`; `cat /proc/sys/net/netfilter/nf_conntrack_max` | Short-lived query connections via MySQL protocol fill conntrack table; HTTP SDK connections (persistent HTTP/2) unaffected | `sysctl -w net.netfilter.nf_conntrack_max=524288`; switch to `@planetscale/database` HTTP SDK to reduce connection churn |
| Kernel TCP keepalive mismatch causes silent PlanetScale connection drops | Application queries hang for 15+ minutes before timeout; no error in PlanetScale logs | `sysctl net.ipv4.tcp_keepalive_time`; `pscale shell $PS_DB $PS_BRANCH -- "SHOW VARIABLES LIKE 'wait_timeout';"` | Idle connections through NAT/firewall silently dropped; application pool considers connection alive but packets blackholed | Set `sysctl net.ipv4.tcp_keepalive_time=60 net.ipv4.tcp_keepalive_intvl=10 net.ipv4.tcp_keepalive_probes=6`; configure MySQL driver `tcp_keepalive=true` |
| cgroup memory pressure throttles pscale CLI operations | `pscale deploy-request create` times out; `pscale branch create` hangs; container memory pressure events in logs | `cat /sys/fs/cgroup/memory/memory.pressure`; `kubectl top pod -l app=pscale-proxy`; `journalctl -u pscale-connect | grep -i throttl` | All PlanetScale administrative operations slow to unusable; deploy requests queue but never complete | Increase container memory limit; set `memory.high` cgroup threshold with early warning; move pscale CLI operations to dedicated sidecar with guaranteed resources |

## Deployment Pipeline & GitOps Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| PlanetScale deploy request stuck in CI pipeline | GitHub Actions workflow waiting on `pscale deploy-request deploy` indefinitely; pipeline times out after 30 min | `pscale deploy-request list $PS_DB --org $PS_ORG --json | jq '.[] | select(.state=="open")'`; check GitHub Actions run log | Schema changes blocked; feature branch merges stalled; development velocity halted | Add `--wait=false` to deploy command; poll with `pscale deploy-request show $PS_DB <number> --org $PS_ORG` in retry loop with backoff |
| Helm chart deploys app before PlanetScale schema migration completes | Application pods start with new code referencing columns that don't exist yet; `Unknown column` errors | `kubectl get pods -l app=<service> --sort-by=.status.startTime`; `pscale deploy-request show $PS_DB <number> --org $PS_ORG --json | jq '.state'` | 500 errors on all queries referencing new schema; rollback needed for both app and schema | Add init container that polls `pscale deploy-request show` until state is `complete` before allowing app container to start |
| ArgoCD sync deploys ConfigMap with wrong PlanetScale branch name | Application connects to `main` branch instead of `staging`; writes go to production database | `kubectl get configmap <app-config> -o jsonpath='{.data.PS_BRANCH}'`; `argocd app diff <app>` | Production data corruption from staging workload; or staging reads production data violating isolation | Use Sealed Secrets for branch config; add ArgoCD PreSync hook that validates `PS_BRANCH` matches target environment |
| PDB blocks node drain; pscale-connect proxy pod not evicted | Kubernetes node drain hangs; `pscale connect` proxy pod protected by PDB; cluster upgrade stalls | `kubectl get pdb -A | grep pscale`; `kubectl get pod -l app=pscale-proxy -o wide` | Cluster maintenance window exceeded; other workloads on node cannot be rescheduled | Set PDB `minAvailable` to N-1 for pscale proxy; deploy proxy as DaemonSet or with topology spread constraints |
| Blue-green cutover fails: new deployment uses expired PlanetScale service token | Green deployment starts; all DB queries fail with `401 Unauthorized`; health checks fail; traffic stays on blue | `kubectl logs -l app=<service>,version=green | grep -i "401\|unauthorized\|token"`; `pscale service-token show-access $PS_TOKEN_ID --org $PS_ORG` | Zero downtime deployment fails; green never becomes healthy; blue continues serving stale version | Rotate service tokens in Vault/Secrets Manager before cutover; add pre-deployment hook that validates token: `pscale org list --service-token $TOKEN --service-token-id $ID` |
| ConfigMap drift: PlanetScale connection string points to deleted branch | Application returns `Unknown database branch` errors after sleeping branch garbage-collected | `kubectl get configmap <app-config> -o yaml | grep PS_BRANCH`; `pscale branch list $PS_DB --org $PS_ORG --json | jq '.[].name'` | All queries fail; application cannot connect to database; cascading failures to dependent services | Add GitOps validation webhook that checks branch existence via `pscale branch show $PS_DB <branch> --org $PS_ORG` before applying ConfigMap changes |
| Image pull failure during PlanetScale proxy sidecar update | New pod stuck in `ImagePullBackOff`; old proxy pod terminated; application has no DB connectivity | `kubectl describe pod <pod> | grep -A5 "Events"`; `kubectl get events --field-selector reason=Failed | grep -i pull` | Gap in proxy availability; all queries fail until image pull succeeds; manual intervention required | Pre-pull sidecar images via DaemonSet; use `imagePullPolicy: IfNotPresent` with pinned digest; configure private registry mirror |
| Secret rotation for PlanetScale credentials breaks running connections | Application mid-query when Vault rotates `PS_SERVICE_TOKEN`; subsequent queries fail; existing connections unaffected until pool recycles | `kubectl get secret <ps-secret> -o jsonpath='{.metadata.annotations.vault\.hashicorp\.com/secret-version}'`; application logs: `grep -i "auth\|token\|401"` | Gradual connection failures as pool recycles; new connections fail immediately; complete outage within connection pool TTL | Use Vault agent sidecar with grace period; implement dual-token validation in proxy layer; stagger secret rotation across pods |

## Service Mesh & API Gateway Edge Cases
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| Envoy sidecar circuit breaker trips on PlanetScale connection pool | Application receives `503 UC` (upstream connection failure) from Envoy instead of DB response | `istioctl proxy-config cluster <pod> | grep planetscale`; `kubectl logs <pod> -c istio-proxy | grep "overflow\|circuit"` | Database queries rejected at mesh layer even though PlanetScale is healthy; false positive outage detection | Increase Envoy `circuitBreakers.maxConnections` for PlanetScale upstream; tune `outlierDetection` thresholds to match PlanetScale's connection limits |
| Rate limiting on API gateway blocks PlanetScale webhook callbacks | PlanetScale deploy request webhook never reaches application; deploy automation stalls | `kubectl logs -l app=api-gateway | grep -i "rate\|429\|planetscale"`; check gateway rate limit config for webhook path | Automated schema deployment pipeline freezes; deploy requests remain in `open` state indefinitely | Exempt PlanetScale webhook source IPs from rate limiting; or route webhooks through dedicated ingress without rate limits |
| Stale service discovery for pscale-connect proxy after pod reschedule | Application DNS resolves to old pod IP; `pscale connect` proxy moved to new node; connections time out | `nslookup pscale-proxy.<namespace>.svc.cluster.local`; `kubectl get endpoints pscale-proxy`; `dig +short pscale-proxy.svc` | All database queries hang until DNS TTL expires; cascading timeouts to upstream services | Set `publishNotReadyAddresses: false` on Service; reduce DNS TTL; use headless Service with client-side load balancing |
| mTLS certificate rotation interrupts PlanetScale proxy tunnel | `pscale connect` tunnel drops during Istio cert rotation; application receives `connection reset by peer` | `istioctl proxy-config secret <pod> | grep -i "valid\|expire"`; `kubectl logs <pod> -c istio-proxy | grep "TLS\|handshake\|rotate"` | Brief connection drop during cert swap; in-flight queries fail; connection pool marks backend unhealthy | Configure `pscale connect` to reconnect on TLS errors; set Istio `meshConfig.certificates` rotation grace period to overlap |
| Retry storm amplifies PlanetScale row-read quota consumption | Application retries failed queries through mesh; each retry counts against PlanetScale row-read quota; quota exhausted rapidly | `curl -H "Authorization: $PS_SERVICE_TOKEN" "https://api.planetscale.com/v1/organizations/$PS_ORG/databases/$PS_DB" | jq '.usage'`; `istioctl proxy-config route <pod> | grep retries` | Row-read quota burned 3-5x faster than expected; PlanetScale throttles with 503; all queries fail | Disable Envoy retries for database upstream; implement application-level retry with exponential backoff and circuit breaker |
| gRPC keepalive conflicts with PlanetScale HTTP/2 SDK connections | `@planetscale/database` SDK connections drop with `GOAWAY` frames; intermittent query failures | `kubectl logs <pod> -c istio-proxy | grep "GOAWAY\|keepalive\|HTTP2"`; `ss -tnpi | grep planetscale | grep keepalive` | HTTP/2-based serverless connections broken by mesh keepalive enforcement; SDK falls back to connection-per-query mode (slower) | Align Envoy `http2_protocol_options.connection_keepalive` with PlanetScale's server-side timeout; set `max_connection_age` > 300s |
| Trace context lost crossing pscale-connect proxy boundary | Distributed traces end at pscale-connect proxy; no visibility into PlanetScale query execution time | `kubectl logs <pod> -c istio-proxy | grep "traceparent\|x-b3"`; check Jaeger: trace stops at proxy span | Cannot attribute latency to PlanetScale vs network vs proxy; SLO reporting incomplete | Inject trace context as MySQL query comment using `/*traceparent=...*/` prefix; parse in application-side trace exporter |
| API gateway timeout shorter than PlanetScale deploy request completion time | Long-running deploy request (large table migration) triggers gateway 504; webhook callback lost | `kubectl logs -l app=api-gateway | grep "504\|timeout\|planetscale"`; `pscale deploy-request show $PS_DB <number> --org $PS_ORG --json | jq '.state'` | Deploy automation reports failure but migration is still running on PlanetScale side; operator manually intervenes causing double-apply | Set async webhook pattern: gateway returns 202 immediately; poll deploy request status separately; increase gateway timeout for `/webhook/planetscale` path |
