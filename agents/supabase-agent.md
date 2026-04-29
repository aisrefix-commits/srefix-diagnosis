---
name: supabase-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-supabase-agent
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
# Supabase SRE Agent

## Role
This agent owns the full operational health of Supabase-hosted projects, covering the entire stack: PostgreSQL database backend, PostgREST auto-generated REST API, GoTrue authentication service, Realtime WebSocket server, Storage service (S3-compatible), and Edge Functions (Deno runtime). It monitors API rate limits, JWT lifecycle errors, Row Level Security (RLS) policy performance, connection pool saturation via the Supabase-managed pgBouncer, WebSocket connection health, and edge function cold starts. It triages incidents across all five service layers and provides runbooks for the failure modes unique to Supabase's managed multi-tenant architecture.

## Architecture Overview
Supabase wraps standard open-source components behind a unified project endpoint. Each project gets a dedicated PostgreSQL 15 instance (single-tenant on Pro/Team plans) with pgBouncer in transaction mode in front of it. PostgREST translates HTTP requests into SQL, authenticating callers via JWT validation against the GoTrue service. Realtime streams database change events (INSERT/UPDATE/DELETE) over WebSocket connections using a Phoenix-based Elixir cluster. Storage proxies uploads and downloads through an S3-compatible API backed by the underlying object store. Edge Functions run on a global Deno network at the CDN edge.

```
Client
  ├── REST     → <project-ref>.supabase.co/rest/v1/*      → PostgREST → pgBouncer → PostgreSQL
  ├── Auth     → <project-ref>.supabase.co/auth/v1/*      → GoTrue    → PostgreSQL (auth schema)
  ├── Realtime → <project-ref>.supabase.co/realtime/v1/*  → Realtime (Elixir) → PostgreSQL (replication)
  ├── Storage  → <project-ref>.supabase.co/storage/v1/*   → Storage API → S3-compatible backend
  └── Edge Fn  → <project-ref>.supabase.co/functions/v1/* → Deno Edge runtime
```

## Key Metrics to Monitor

| Metric | Warning Threshold | Critical Threshold | Notes |
|--------|------------------|--------------------|-------|
| PostgREST `db_pool_available` | < 20% of pool size | < 5% | Via Supabase dashboard → Database → Connections |
| Active DB connections (pgBouncer) | > 60 of default 60 pool | = max (all queued) | `select count(*) from pg_stat_activity where state != 'idle'` |
| GoTrue `auth.users` sign-in rate | > 1,000 req/min (Pro) | Rate limit 429 responses | Plan-dependent; monitor HTTP 429 rate |
| JWT expiry (`exp` claim) | JWTs within 5 min of expiry in active sessions | Expired JWTs causing 401 storms | Monitor `auth.sessions` age |
| Realtime concurrent connections | > 200 (Pro) | > 500 — channel evictions | Plan limit; check `realtime.channels` |
| Storage upload latency (P99) | > 2 s | > 10 s | For objects < 50 MB |
| Edge Function cold start latency | > 500 ms | > 2 s | Measured as first-byte latency after idle |
| RLS policy execution time (per query overhead) | > 10 ms added overhead | > 100 ms added overhead | Profile via `EXPLAIN (ANALYZE, BUFFERS)` with `SET role` |
| PostgreSQL replication lag (Realtime) | > 5 s | > 30 s | Realtime uses logical replication; lag delays change events |
| `pg_stat_statements` calls/sec for PostgREST user | > 500 req/s | > 2,000 req/s | May indicate RLS misconfiguration causing expensive plans |

## Alert Runbooks

### Alert: PostgRESTDatabasePoolExhausted
**Condition:** `pg_stat_activity` count for `authenticator` role ≥ pool_size for > 2 min, OR HTTP 503 responses from `/rest/v1/` > 1% of requests
**Triage:**
1. Check active DB connections: `psql postgresql://postgres:<pass>@db.<ref>.supabase.co:5432/postgres -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY state;"`
2. Identify long-running queries from PostgREST: `psql ... -c "SELECT pid, now()-query_start AS duration, query FROM pg_stat_activity WHERE usename = 'authenticator' AND state = 'active' ORDER BY duration DESC LIMIT 10;"`
3. Check for RLS policies doing sequential scans: `psql ... -c "SELECT query, calls, mean_exec_time, total_exec_time FROM pg_stat_statements WHERE query ILIKE '%rls%' OR mean_exec_time > 100 ORDER BY mean_exec_time DESC LIMIT 10;"`
4. Review Supabase dashboard → API → Request Logs for 503 pattern (which endpoints).
### Alert: GoTrue401Storm
**Condition:** HTTP 401 error rate from `/auth/v1/` > 5% of requests for > 5 min
**Triage:**
1. Check Supabase dashboard → Auth → Logs for error patterns — distinguish `invalid JWT` vs `JWT expired` vs `user not found`.
2. Verify JWT secret rotation: if the `JWT_SECRET` was rotated without updating client SDKs, all existing tokens become invalid simultaneously.
3. Check `auth.sessions` for recent mass invalidation: `psql ... -c "SELECT count(*) FROM auth.sessions WHERE created_at > now() - interval '1 hour';"`
4. Confirm Supabase project `anon` and `service_role` keys match what clients are using: check project Settings → API.
### Alert: RealtimeHighLag
**Condition:** Realtime WebSocket message delivery delay > 30 s OR channel evictions > 0
**Triage:**
1. Check logical replication slot lag for Realtime: `psql ... -c "SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS lag FROM pg_replication_slots WHERE slot_name LIKE 'supabase_realtime%';"`
2. Check active WebSocket connection count in Supabase dashboard → Realtime → Inspector.
3. Identify long-running transactions blocking WAL advancement: `psql ... -c "SELECT pid, now()-xact_start AS age, query FROM pg_stat_activity WHERE xact_start IS NOT NULL ORDER BY age DESC LIMIT 10;"`
4. Confirm Realtime is filtering channels correctly — too many broadcast subscriptions can overload the cluster.
### Alert: EdgeFunctionColdStartSurge
**Condition:** Edge Function P99 latency > 2 s or invocation error rate > 2%
**Triage:**
1. Check Edge Function logs in Supabase dashboard → Edge Functions → Logs — look for `Boot timeout`, `Memory limit exceeded`, or unhandled exceptions.
2. Check function bundle size: `supabase functions download <function-name> --project-ref <ref>` and inspect.
3. Review Deno import map — cold starts are worse with large dependency trees or `npm:` specifiers.
4. Check invocation count spike pattern — a sudden burst causes queue buildup and cascading cold starts.
## Common Issues & Troubleshooting

### Issue: RLS Policy Causing Full Table Scans
**Symptoms:** Simple `SELECT` queries via PostgREST take > 500 ms; table size is not large; `EXPLAIN` shows `Seq Scan`.
**Diagnosis:**
```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM public.messages WHERE auth.uid() = user_id LIMIT 10;
```
### Issue: JWT "invalid signature" Errors in Production
**Symptoms:** All API calls return `401 {"message":"invalid JWT"}` suddenly; was working before.
**Diagnosis:** `psql ... -c "SHOW app.settings.jwt_secret;"` — verify the secret matches the project's JWT secret in Settings → API.
### Issue: `auth.users` Table Grows Unbounded
**Symptoms:** Storage alert; auth schema taking up gigabytes; `auth.sessions` and `auth.refresh_tokens` are huge.
**Diagnosis:** `psql ... -c "SELECT count(*) FROM auth.users; SELECT count(*) FROM auth.sessions WHERE not_after < now(); SELECT count(*) FROM auth.refresh_tokens WHERE revoked = true;"`
### Issue: Realtime Not Receiving Changes for a Table
**Symptoms:** Client subscribed to a table channel receives no events; database changes confirmed present.
**Diagnosis:** `psql ... -c "SELECT schemaname, tablename FROM pg_publication_tables WHERE pubname = 'supabase_realtime';"` — verify the table is in the publication.
### Issue: Storage Upload Fails with 403
**Symptoms:** `supabase-js` Storage `upload()` returns `{"error":"Unauthorized"}` for authenticated users.
**Diagnosis:** Check the Storage bucket policy in Supabase dashboard → Storage → Policies. Run: `psql ... -c "SELECT * FROM storage.buckets WHERE name = '<bucket_name>';"` to confirm `public` flag.
### Issue: Edge Function Cannot Reach External URL
**Symptoms:** Edge Function returns error or timeout when calling external API; works locally.
**Diagnosis:** Check Edge Function logs for `ConnectRefused` or `Timeout`. Test with a known-good external endpoint. Verify no egress firewall restriction on the Supabase project.
## Key Dependencies

- **PostgreSQL backend**: All Supabase services ultimately depend on PG. DB downtime takes down PostgREST, GoTrue, Realtime, and Storage metadata simultaneously.
- **pgBouncer (Supabase-managed)**: Transaction-mode pool in front of PostgreSQL. Pool exhaustion causes 503 from the REST API.
- **GoTrue**: Issues and validates JWTs. If GoTrue is degraded, new logins fail and token refreshes fail, eventually logging out all active sessions as tokens expire.
- **Supabase Realtime cluster**: Uses a logical replication slot. If the slot falls behind, WAL accumulates on the primary, risking disk exhaustion.
- **Storage object store (S3-compatible backend)**: If the backing object store is degraded, file uploads/downloads fail while the database tier remains healthy.
- **Deno Edge runtime**: Edge Functions are globally distributed but depend on correct secret injection (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`). Misconfigured secrets cause silent auth failures.

## Cross-Service Failure Chains

**Chain 1: Long-Running Transaction → Realtime Slot Lag → pg_wal Full → Database Writes Blocked**
An analytics query in a long transaction (or an idle-in-transaction connection) holds an XID. The Supabase Realtime logical replication slot (`supabase_realtime_*`) cannot advance past the transaction's snapshot. WAL accumulates. On smaller plans (Pro, 8 GB database disk) the `pg_wal` directory can fill within hours on write-heavy projects. PostgreSQL enters read-only mode. This simultaneously kills PostgREST writes, GoTrue (which writes to `auth.sessions`), and Storage (which writes to `storage.objects`). The Realtime service continues to broadcast based on buffered changes but new writes are impossible.

**Chain 2: JWT Secret Rotation Without Client Update → Auth 401 Storm → API Cascade**
A project owner rotates the JWT secret via Settings → API for security reasons but does not redeploy API services or update client SDK initialization. All existing sessions immediately fail JWT validation. Every authenticated API request (PostgREST, Storage, Realtime channel join) returns 401. Clients retry in a tight loop, exhausting the PostgREST connection pool. The retry storm causes database connections to spike, which triggers pgBouncer pool exhaustion, making the issue appear to be a database problem rather than an auth configuration issue.

**Chain 3: RLS Policy Missing Index → Query Fanout → Connection Pool Exhaustion → 503**
A new table is created with an RLS policy `USING (auth.uid() = user_id)` but no index on `user_id`. Each PostgREST API request triggers a full sequential scan on the table. At 100 concurrent users, 100 sequential scans run simultaneously, each holding a server connection for 200–500 ms. The default pgBouncer pool (60 connections) exhausts within seconds. PostgREST returns HTTP 503. The fix (adding a `CREATE INDEX CONCURRENTLY`) resolves the issue without downtime.

## Partial Failure Patterns

- **Auth works, REST fails**: PostgREST pool exhausted or PostgREST process crashed while GoTrue is healthy. New logins succeed but authenticated API calls return 503.
- **REST works, Realtime delayed**: Logical replication slot lag. Database reads and writes work normally; Realtime change event delivery is delayed by seconds to minutes.
- **Uploads fail, reads succeed**: Storage RLS policy too restrictive for writes. Existing objects downloadable but new uploads rejected.
- **Auth slow but functional**: GoTrue is processing; `auth.users` table has extreme bloat. Login takes 2–10 s instead of < 200 ms.
- **Edge Functions fail, everything else works**: Deno runtime issue or secret misconfiguration. REST/Auth/Realtime unaffected.
- **Some tables not in Realtime**: Table not added to `supabase_realtime` publication. Subscriptions to those channels receive no events while other channels work normally.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|---------|
| PostgREST GET (single row by PK) | < 10 ms | 10–100 ms | > 100 ms |
| PostgREST POST (INSERT with RLS) | < 20 ms | 20–200 ms | > 200 ms |
| GoTrue sign-in (email/password) | < 300 ms | 300 ms–1 s | > 1 s |
| GoTrue token refresh | < 100 ms | 100–500 ms | > 500 ms |
| Realtime change event delivery | < 500 ms | 500 ms–5 s | > 5 s |
| Storage upload (1 MB file) | < 500 ms | 500 ms–2 s | > 2 s |
| Storage download (1 MB file, cached) | < 200 ms | 200–500 ms | > 500 ms |
| Edge Function invocation (warm) | < 100 ms | 100–500 ms | > 500 ms |
| Edge Function invocation (cold start) | < 500 ms | 500 ms–2 s | > 2 s |

## Capacity Planning Indicators

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Database size | > 50% of plan limit and growing | Upgrade plan or enable archival strategy | 2 weeks |
| Active DB connections (daily peak) | Regularly > 80% of pool size | Optimize RLS indexes; consider connection pooling tuning | 1 week |
| `auth.users` row count | > 500K on Free/Pro plan | Prune inactive sessions; consider SMTP rate limits | 2 weeks |
| Realtime concurrent connections (peak) | > 80% of plan limit | Upgrade plan; audit duplicate subscriptions | 1 week |
| Edge Function invocations/day | Approaching plan quota | Upgrade plan or optimize to reduce invocations | 3 days |
| Storage usage | > 70% of plan quota | Implement lifecycle policies; upgrade storage tier | 1 week |
| Slow query count (P99 > 200 ms) | Trending upward week-over-week | Query optimization sprint, index review | 1 week |
| WAL size (logical replication slot lag) | Slot consistently > 1 GB behind | Reduce long-running transactions; optimize Realtime filters | 2–3 days |

## Diagnostic Cheatsheet

```bash
# Check current DB connection state breakdown
psql postgresql://postgres:<pass>@db.<ref>.supabase.co:5432/postgres -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY state ORDER BY count DESC;"

# Show top slow queries via pg_stat_statements
psql ... -c "SELECT round(mean_exec_time::numeric,2) AS mean_ms, calls, LEFT(query,120) FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;"

# Check Realtime replication slot lag
psql ... -c "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS lag FROM pg_replication_slots;"

# Verify which tables are in the Realtime publication
psql ... -c "SELECT schemaname, tablename FROM pg_publication_tables WHERE pubname = 'supabase_realtime';"

# Check for tables missing RLS policies (potential data exposure)
psql ... -c "SELECT schemaname, tablename FROM pg_tables WHERE schemaname = 'public' AND tablename NOT IN (SELECT tablename FROM pg_policies WHERE schemaname = 'public');"

# Inspect auth.sessions for expired or old sessions
psql ... -c "SELECT count(*), date_trunc('day', created_at) AS day FROM auth.sessions GROUP BY day ORDER BY day DESC LIMIT 7;"

# Check storage bucket visibility
psql ... -c "SELECT name, public, created_at FROM storage.buckets ORDER BY created_at;"

# Check GoTrue health endpoint
curl https://<ref>.supabase.co/auth/v1/health

# Test PostgREST with anon key
curl "https://<ref>.supabase.co/rest/v1/<table>?select=id&limit=1" -H "apikey: <anon_key>" -H "Authorization: Bearer <anon_key>"

# Check Edge Function logs via Supabase CLI
supabase functions logs <function-name> --project-ref <ref>
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| PostgREST API Availability | 99.9% | HTTP 2xx rate on `/rest/v1/*` (excluding rate-limit 429) | 43.8 min/month | Burn rate > 6× |
| GoTrue Auth Availability | 99.9% | HTTP 2xx rate on `/auth/v1/token` and `/auth/v1/user` | 43.8 min/month | Burn rate > 6× |
| Realtime Event Delivery | 99.5% | % of DB changes delivered to active subscribers within 10 s | 3.6 hr/month | Burn rate > 3× |
| Storage Upload Success Rate | 99.9% | HTTP 2xx rate on `PUT /storage/v1/object/*` | 43.8 min/month | Burn rate > 6× |

## Configuration Audit Checklist

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| RLS enabled on all public tables | `psql ... -c "SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname='public';"` | `rowsecurity = t` for all user tables |
| No tables accessible without RLS policy | `psql ... -c "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN (SELECT tablename FROM pg_policies WHERE schemaname='public');"` | Empty result set |
| Realtime publication contains correct tables | `psql ... -c "SELECT tablename FROM pg_publication_tables WHERE pubname='supabase_realtime';"` | Only intentionally subscribed tables listed |
| No inactive replication slots | `psql ... -c "SELECT slot_name, active FROM pg_replication_slots WHERE active = false;"` | No inactive slots |
| Storage buckets privacy settings correct | `psql ... -c "SELECT name, public FROM storage.buckets;"` | Public only for intentionally public buckets |
| JWT secret not default | Supabase Dashboard → Settings → API | JWT secret is a strong random value (not default) |
| `service_role` key not used in client-side code | `grep -r 'service_role' <frontend_repo>` | No matches in client-side code |
| Auth email confirmations enabled | Supabase Dashboard → Auth → Email Templates | Email confirmation is enabled for production |
| Database password meets complexity | Supabase Dashboard → Settings → Database | Password rotated from initial value |
| PITR enabled (Pro plan+) | Supabase Dashboard → Settings → Database → PITR | Enabled with appropriate retention |

## Log Pattern Library

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `JWT expired` in GoTrue logs | Medium | Client not refreshing tokens | Implement `supabase.auth.onAuthStateChange` refresh logic |
| `invalid JWT` in GoTrue logs | High | Wrong JWT secret or token tampered | Verify JWT secret; check for client-side secret exposure |
| `new row violates row-level security policy` | Medium | RLS policy too restrictive or incorrect | Review policy with `SET ROLE <user_role>; EXPLAIN SELECT ...` |
| `remaining connection slots are reserved` | Critical | pgBouncer pool or max_connections exhausted | Terminate idle connections; check RLS index coverage |
| `could not obtain lock on relation` (PostgREST) | High | DDL lock held while API traffic running | Defer DDL to maintenance window; use `lock_timeout` |
| `Realtime: subscribing to table without RLS` | Warning | Realtime subscription on table with RLS disabled | Enable RLS and add appropriate policy |
| `function body cannot start a transaction` (Edge Fn) | High | Edge Function trying to use transaction blocks | Refactor to use Supabase client SDK calls instead |
| `Boot timeout` (Edge Function) | High | Function takes too long to initialize | Reduce imports; lazy-load heavy modules |
| `Memory limit exceeded` (Edge Function) | High | Function using > 512 MB | Profile memory; reduce data processing; paginate results |
| `rate limit exceeded` (Auth) | Medium | Too many auth requests from single IP/user | Implement client-side rate limiting; review bot detection |
| `relation "auth.users" does not exist` | Critical | Auth schema migration failed | Check migration logs; restore from backup if schema corrupt |
| `replication slot supabase_realtime_* does not exist` | High | Slot dropped accidentally | Re-enable Realtime in dashboard; slot recreated automatically |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| HTTP 401 `invalid JWT` | JWT signature invalid or secret mismatch | All authenticated requests fail | Verify JWT secret; re-issue tokens |
| HTTP 401 `JWT expired` | Token past `exp` claim | Session expired | Implement token refresh; reduce token lifetime |
| HTTP 403 `new row violates row-level security` | RLS policy blocks operation | Write fails for user | Review RLS policy; add policy if missing |
| HTTP 429 | Rate limit exceeded (GoTrue or API) | Requests throttled | Implement backoff; review rate limit quotas |
| HTTP 503 `Service Unavailable` | PostgREST/pgBouncer pool exhausted | All REST calls fail | Kill long queries; add RLS indexes |
| `PGRST116` (PostgREST) | Multiple rows returned where single expected | Query returns wrong cardinality | Add `.single()` or `.maybeSingle()` call |
| `PGRST301` (PostgREST) | JWT secret does not match | Auth completely broken | Rotate or restore JWT secret |
| `AuthApiError: Email not confirmed` | User has not confirmed email | Login blocked | Resend confirmation email; or disable email confirmation |
| `AuthRetryableFetchError` | Network issue reaching GoTrue | Auth requests fail | Check client network; verify Supabase project status |
| `StorageError: Object not found` | Object does not exist in bucket | Download fails | Verify bucket name and object path |
| `FunctionsFetchError` | Edge Function network error | Function invocation fails | Check function logs; verify URL and keys |
| `23505 unique_violation` | Unique constraint violated | INSERT fails | Handle conflict in application or use upsert |

## Known Failure Signatures

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| RLS Scan Bomb | DB CPU 100%; PostgREST mean query time > 500 ms | `autovacuum`, no errors | PostgRESTDatabasePoolExhausted | Missing index on RLS policy column | `CREATE INDEX CONCURRENTLY` on RLS column |
| Auth Token Cliff | GoTrue 401 rate spikes at predictable time | `JWT expired` in auth logs | GoTrue401Storm | Token lifetime expiry not handled by client | Implement `onAuthStateChange` token refresh |
| Realtime Silent | Realtime slot lag growing; 0 events delivered | No Realtime errors (silent failure) | RealtimeHighLag | Table removed from publication or REPLICA IDENTITY wrong | Add table to publication; set `REPLICA IDENTITY FULL` |
| Connection Leak | Active connection count grows monotonically; never decreases | `idle in transaction` sessions accumulating | ConnectionPoolExhaustion | Application not closing connections; missing `await` in async code | Set `idle_in_transaction_session_timeout = 30000`; fix app code |
| Storage 403 Flood | Storage `PUT` 403 rate > 50% | `new row violates row-level security policy` | StorageUploadErrors | Missing RLS INSERT policy on `storage.objects` | Add INSERT policy for authenticated users |
| Edge Fn Boot Storm | Cold start P99 > 2 s; error rate > 5% | `Boot timeout` | EdgeFunctionColdStartSurge | Large bundle size; npm: specifiers | Reduce imports; use URL imports; lazy-load |
| pg_wal Fill via Realtime | Disk usage alert; pg_wal > 5 GB | `archive command failed` | WALDiskUsage | Realtime slot not advancing (long transaction) | Terminate blocking transaction; monitor slot lag |
| JWT Secret Mismatch | All PostgREST/Auth calls returning 401/403 | `invalid JWT` storm across all services | GoTrue401Storm | JWT secret rotated in one place but not all | Synchronize JWT secret across all services |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `401 {"message":"invalid JWT"}` | supabase-js, PostgREST client | JWT secret mismatch or tampered token | `SHOW app.settings.jwt_secret;` in psql; compare with Settings → API | Rotate JWT secret; force re-auth for all users |
| `401 {"message":"JWT expired"}` | supabase-js | Token `exp` claim past; client not calling `refreshSession` | Decode JWT `exp` claim: `echo <token> \| cut -d. -f2 \| base64 -d` | Implement `onAuthStateChange` with token refresh |
| `503 {"message":"Service Unavailable"}` | supabase-js REST | pgBouncer pool exhausted; no server connections available | `SELECT count(*) FROM pg_stat_activity WHERE usename='authenticator';` | Kill long-running PostgREST queries; add RLS column indexes |
| `403 {"message":"new row violates row-level security policy"}` | supabase-js, PostgREST | RLS USING / WITH CHECK clause rejects operation | `SET ROLE authenticated; EXPLAIN SELECT * FROM <table>;` | Correct the RLS policy; add permissive policy |
| `PGRST116 — JSON object requested, multiple (or 0) rows returned` | supabase-js `.single()` | Query returns more than one row (or zero) when `.single()` called | Review the query filter; check for duplicate rows | Use `.maybeSingle()` for zero-or-one case; fix unique constraint |
| `429 Too Many Requests` | supabase-js Auth | GoTrue rate limit hit (sign-up / password reset storm) | Auth Logs in dashboard; filter for 429 | Implement client-side rate limiting; use CAPTCHA on auth forms |
| `StorageError: {"statusCode":"403","error":"Unauthorized"}` | supabase-js Storage | Missing RLS INSERT policy on `storage.objects` or bucket is private | `SELECT * FROM storage.buckets WHERE name='<bucket>';` | Add correct RLS policy; set bucket public if intended |
| `FunctionsFetchError: Failed to send a request to the Edge Function` | supabase-js | Edge Function runtime error or cold start timeout | `supabase functions logs <fn> --project-ref <ref>` | Fix function error; reduce bundle size for cold starts |
| `AuthRetryableFetchError` | supabase-js | Network issue reaching GoTrue; ephemeral connectivity loss | `curl https://<ref>.supabase.co/auth/v1/health` | Implement retry with exponential backoff |
| `23505 unique_violation` | supabase-js, PostgREST | Duplicate INSERT against unique/PK constraint | `\d+ <table>` in psql to see unique indexes | Use `.upsert()` method; handle conflict in application code |
| `Realtime: channel closed — channel limit exceeded` | supabase-js Realtime | Plan concurrent channel limit reached | Dashboard → Realtime → check channel count vs plan | Reduce duplicate subscriptions; upgrade plan |
| `relation "auth.users" does not exist` | supabase-js, direct psql | Auth schema migration failed; schema corrupt | `SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema='auth';` | Restore from PITR backup; contact Supabase support |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| RLS sequential scan accumulation | PostgREST P99 rising week-over-week as data grows; no errors yet | `SELECT round(mean_exec_time::numeric,2) AS ms, LEFT(query,120) FROM pg_stat_statements WHERE query ILIKE '%authenticator%' ORDER BY mean_exec_time DESC LIMIT 10;` | 1–3 weeks | Add indexes on RLS policy columns (`user_id`, `owner`) |
| pgBouncer pool saturation drift | Peak `active` connection count trending toward 60 (default pool); `cl_waiting` occasionally > 0 | `SELECT state, count(*) FROM pg_stat_activity WHERE usename = 'authenticator' GROUP BY state;` | 1 week | Optimize RLS indexes; reduce long-held transactions in API routes |
| `auth.sessions` table bloat | auth schema storage growing; GoTrue sign-in response time rising gradually | `SELECT count(*) FROM auth.sessions WHERE not_after < now();` | 2 weeks | Schedule periodic cleanup Edge Function: `DELETE FROM auth.sessions WHERE not_after < now() - interval '30 days';` |
| Realtime WAL slot lag growth | `pg_wal` size growing; slot consistently > 500 MB behind | `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS lag FROM pg_replication_slots WHERE slot_name LIKE '%realtime%';` | 2–3 days | Identify and terminate long-running transactions; optimize Realtime publication filters |
| Edge Function cold start latency creep | P99 cold-start latency rising as bundle dependencies grow across deploys | `supabase functions logs <fn> --project-ref <ref> \| grep "Boot"` — note boot time trend | 1–2 weeks | Audit and reduce imports; replace `npm:` specifiers with URL imports |
| Storage object count growth vs index health | Object listing queries slowing as `storage.objects` table grows | `SELECT count(*) FROM storage.objects WHERE bucket_id = '<bucket>';` — compare week-over-week | 2–4 weeks | Implement lifecycle deletion for stale objects; archive to cheaper tier |
| GoTrue response time degradation from auth.users bloat | Login time increasing from < 200 ms toward 1 s; no errors | `SELECT count(*), pg_size_pretty(pg_total_relation_size('auth.users')) AS table_size FROM auth.users;` | 2 weeks | Prune inactive users; `VACUUM ANALYZE auth.users;` |
| Unused replication slots accumulating WAL | `pg_wal` growing steadily; multiple inactive slots | `SELECT slot_name, active FROM pg_replication_slots WHERE active = false;` | 3–5 days | Drop unused slots: `SELECT pg_drop_replication_slot('<slot_name>');` |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Supabase Full Health Snapshot
# Usage: export PG_DSN="postgresql://postgres:<pass>@db.<ref>.supabase.co:5432/postgres"
#        export SUPABASE_URL="https://<ref>.supabase.co"
#        export ANON_KEY="<anon_key>"
#        ./supabase-health-snapshot.sh

PG="psql $PG_DSN -tAq"
echo "=== Supabase Health Snapshot: $(date -u) ==="

echo ""
echo "--- PostgREST Health ---"
curl -sf -o /dev/null -w "PostgREST HTTP status: %{http_code}\n" \
  "$SUPABASE_URL/rest/v1/" -H "apikey: $ANON_KEY"

echo ""
echo "--- GoTrue Health ---"
curl -sf "$SUPABASE_URL/auth/v1/health" | python3 -m json.tool 2>/dev/null || echo "GoTrue unreachable"

echo ""
echo "--- DB Connection Breakdown ---"
$PG -c "SELECT state, usename, count(*) FROM pg_stat_activity GROUP BY state, usename ORDER BY count DESC LIMIT 15;"

echo ""
echo "--- Realtime Replication Slot Lag ---"
$PG -c "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS lag FROM pg_replication_slots;"

echo ""
echo "--- Auth Session Count (last 7 days) ---"
$PG -c "SELECT date_trunc('day', created_at) AS day, count(*) FROM auth.sessions GROUP BY day ORDER BY day DESC LIMIT 7;"

echo ""
echo "--- Storage Bucket Summary ---"
$PG -c "SELECT name, public, (SELECT count(*) FROM storage.objects o WHERE o.bucket_id = b.id) AS object_count FROM storage.buckets b;"

echo ""
echo "--- Tables Missing RLS ---"
$PG -c "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN (SELECT tablename FROM pg_policies WHERE schemaname='public');"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Supabase Performance Triage
# Usage: PG_DSN="postgresql://..." ./supabase-perf-triage.sh

PG="psql $PG_DSN -tAq"
echo "=== Supabase Performance Triage: $(date -u) ==="

echo ""
echo "--- Top Slow Queries from pg_stat_statements ---"
$PG -c "SELECT round(mean_exec_time::numeric,2) AS mean_ms, calls, LEFT(query,160) FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;"

echo ""
echo "--- PostgREST/authenticator Long Queries ---"
$PG -c "SELECT pid, round(extract(epoch FROM now()-query_start)) AS sec, LEFT(query,120) FROM pg_stat_activity WHERE usename='authenticator' AND state='active' ORDER BY sec DESC LIMIT 10;"

echo ""
echo "--- RLS Policy Tables with Potential Missing Indexes ---"
$PG -c "SELECT p.tablename, p.cmd, p.qual FROM pg_policies p JOIN pg_tables t ON p.tablename = t.tablename WHERE t.schemaname='public';" | head -30

echo ""
echo "--- Current Lock Contention ---"
$PG -c "SELECT blocked.pid, blocked_activity.query AS blocked_q, blocker.pid AS blocker_pid, blocker_activity.query AS blocker_q FROM pg_locks blocked JOIN pg_stat_activity blocked_activity ON blocked_activity.pid=blocked.pid JOIN pg_locks blocker ON blocker.relation=blocked.relation AND blocker.granted AND NOT blocked.granted JOIN pg_stat_activity blocker_activity ON blocker_activity.pid=blocker.pid LIMIT 10;"

echo ""
echo "--- Table Bloat (auth + public) ---"
$PG -c "SELECT schemaname, relname, n_dead_tup, round(n_dead_tup*100.0/NULLIF(n_live_tup+n_dead_tup,0),1) AS dead_pct FROM pg_stat_user_tables WHERE schemaname IN ('auth','public') AND n_live_tup > 1000 ORDER BY dead_pct DESC LIMIT 10;"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Supabase Connection and Resource Audit
# Usage: PG_DSN="postgresql://..." ./supabase-connection-audit.sh

PG="psql $PG_DSN -tAq"
echo "=== Supabase Connection & Resource Audit: $(date -u) ==="

echo ""
echo "--- Connection Count by Role ---"
$PG -c "SELECT usename, state, count(*) FROM pg_stat_activity GROUP BY usename, state ORDER BY count DESC;"

echo ""
echo "--- Idle-in-Transaction (>10s) ---"
$PG -c "SELECT pid, usename, round(extract(epoch FROM now()-xact_start)) AS idle_xact_sec, LEFT(query,100) FROM pg_stat_activity WHERE state='idle in transaction' AND xact_start < now()-interval '10 seconds' ORDER BY idle_xact_sec DESC;"

echo ""
echo "--- auth.sessions and auth.refresh_tokens Size ---"
$PG -c "SELECT 'auth.sessions' AS tbl, count(*) AS total, count(*) FILTER (WHERE not_after < now()) AS expired FROM auth.sessions UNION ALL SELECT 'auth.refresh_tokens', count(*), count(*) FILTER (WHERE revoked=true) FROM auth.refresh_tokens;"

echo ""
echo "--- storage.objects Count per Bucket ---"
$PG -c "SELECT bucket_id, count(*), pg_size_pretty(sum(coalesce((metadata->>'size')::bigint,0))) AS total_size FROM storage.objects GROUP BY bucket_id ORDER BY count DESC;"

echo ""
echo "--- Replication Slots Health ---"
$PG -c "SELECT slot_name, slot_type, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS wal_retained FROM pg_replication_slots ORDER BY pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) DESC;"
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Expensive RLS policy consuming all pgBouncer connections | PostgREST 503s; DB connections pegged at 60; no obvious single query | `SELECT query, calls, mean_exec_time FROM pg_stat_statements WHERE query ILIKE '%auth.uid%' ORDER BY mean_exec_time DESC LIMIT 5;` | Kill runaway PostgREST connections; add index on RLS column | Index all columns referenced in RLS `USING` expressions |
| Long-running analytics transaction blocking Realtime WAL slot | Realtime slot lag growing; WAL accumulating; pg_wal size alert | `SELECT pid, xact_start, query FROM pg_stat_activity WHERE xact_start IS NOT NULL ORDER BY xact_start ASC LIMIT 5;` | Terminate the long transaction | Set `idle_in_transaction_session_timeout = 30000`; route analytics to replica |
| Edge Function burst causing GoTrue DDoS | GoTrue 429 rate spikes; auth operations backing up | `supabase functions logs <fn> --project-ref <ref> \| grep -c 'signIn\|signUp'` — count auth calls from function | Add per-user rate limiting inside Edge Function | Implement exponential backoff in function; cache auth tokens |
| Storage upload flood from high-concurrency clients | Storage API latency spikes; `pg_stat_activity` shows many `storage.objects` inserts | `SELECT count(*) FROM pg_stat_activity WHERE query ILIKE '%storage.objects%' AND state='active';` | Add upload rate limiting at API gateway or Supabase storage policy | Implement client-side concurrency limit; use resumable upload API for large files |
| auth.sessions bloat slowing all GoTrue operations | Login latency gradually rising over weeks; GoTrue CPU rising | `SELECT count(*), pg_size_pretty(pg_total_relation_size('auth.sessions')) FROM auth.sessions WHERE not_after < now();` | `DELETE FROM auth.sessions WHERE not_after < now() - interval '30 days';` | Schedule cron Edge Function for periodic expired session cleanup |
| Idle PostgREST connections holding pgBouncer slots | Connections near limit despite low traffic; many `idle` authenticator connections | `SELECT state, count(*) FROM pg_stat_activity WHERE usename='authenticator' GROUP BY state;` | Restart PostgREST to drain idle connections (self-hosted); reduce PostgREST pool size | Set `pool_size` in PostgREST config to match actual workload |
| Realtime channel subscription fan-out on large table | Realtime cluster CPU high; change event delivery delayed for all channels | Dashboard → Realtime → channel count; check for wildcard subscriptions | Limit subscription to specific filters; remove wildcard `*` table subscriptions | Use column-level filtering in Realtime subscriptions; avoid `REPLICA IDENTITY FULL` on very large tables |
| GoTrue signup storm consuming auth.users writes | Write contention on auth schema; all services affected | `SELECT count(*) FROM auth.users WHERE created_at > now()-interval '5 min';` | Enable CAPTCHA on signup; temporarily restrict signup to allowlist | Enable CAPTCHA (Supabase Turnstile integration); implement rate limiting on `/auth/v1/signup` |
| Unfiltered Edge Function invoking PostgREST for every event | PostgREST connections saturate; DB CPU spikes correlated with function invocations | `supabase functions logs <fn> \| grep -c 'supabase.from'` — count DB calls per invocation | Add caching layer in function; batch DB queries | Use Supabase admin client with connection reuse; cache common queries |

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| PostgreSQL max_connections exhausted | PostgREST, GoTrue, Storage, and Realtime all compete for remaining connections; new connection attempts fail; services begin returning 503 | All Supabase services that use direct DB connections; client apps receive "connection refused" or "too many clients" | `SELECT count(*) FROM pg_stat_activity;` hits `max_connections`; PostgREST logs: `FATAL: sorry, too many clients already`; Dashboard connection graph at ceiling | Enable PgBouncer: `ALTER ROLE authenticator CONNECTION LIMIT 50`; kill idle connections: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < now()-interval '5 min'` |
| Realtime WAL replication slot lag growing | WAL segments accumulate; PostgreSQL cannot reclaim disk space; disk fills; DB crashes | Entire Supabase instance — once disk is full, all writes fail including auth, storage, and API | `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS lag FROM pg_replication_slots;` shows large lag | Drop stale replication slot: `SELECT pg_drop_replication_slot('<slot_name>');`; or disconnect idle Realtime subscribers | Monitor `pg_replication_slots` lag; set `max_slot_wal_keep_size` to limit WAL accumulation |
| GoTrue JWT secret rotation without coordinated app restart | All existing JWT tokens become invalid simultaneously; every API call returns 401 until apps get new secret | All authenticated API consumers — web, mobile, edge functions | Spike in 401 errors in PostgREST and Edge Function logs; `supabase auth` log: `invalid signature` | Revert JWT secret to previous value via Supabase dashboard → Settings → API → JWT Secret; rolling restart services | Use Supabase key rotation feature with dual-key grace period; coordinate secret rotation with app deployments |
| Storage bucket policy set to private during migration | All public image URLs become 403; CDN cache expires; users see broken images across app | All users loading images from public storage buckets; affects every page with user-generated content | CDN `403` spike; browser console `net::ERR_FAILED` on `<project>.supabase.co/storage/v1/object/public/...`; correlate with storage policy change | Revert bucket to public: Supabase Dashboard → Storage → Bucket → Make public; or set `BUCKET_PUBLIC=true` via API | Audit bucket policies before migration; test with staging environment first |
| Edge Function timeout cascade killing PostgREST pool | Slow Edge Functions holding DB connections open until 150s timeout; connection pool depleted | All clients using PostgREST while Edge Function connections are held | PostgREST: `ERROR: remaining connection slots are reserved for non-replication superuser connections`; Edge Function logs showing repeated slow DB queries | Deploy fix to Edge Function with reduced DB query timeout (`statement_timeout`); kill long-running function invocations in Supabase dashboard | Set `statement_timeout = '10s'` in Edge Function DB session; enforce max execution time |
| Supabase Realtime decoder crash looping due to unsupported column type | Realtime service crash-loops; all channel subscriptions drop; reconnection attempts overwhelm WS endpoint | All real-time features (live queries, presence, broadcast) across all connected clients | `supabase realtime logs` shows: `Protocol encoder failed`; channel subscriptions all returning `CHANNEL_ERROR` | Disable `REPLICA IDENTITY FULL` on the offending table: `ALTER TABLE <table> REPLICA IDENTITY DEFAULT`; restart Realtime service | Test Realtime subscriptions in staging before enabling on tables with complex column types |
| Storage upload storm filling Supabase storage quota | Bulk upload job uploading thousands of large files without rate limiting | Storage quota exhausted; new uploads return 413; application fails silently; users cannot upload profile photos | Dashboard → Storage → Usage shows near limit; `POST /storage/v1/object/<bucket>` returning 413 | Pause bulk upload job; delete large unused files; increase storage plan | Implement client-side rate limiting on uploads; set per-user upload quotas via Storage policies |
| Auth email provider rate limit triggering signup failures | Supabase SMTP provider throttled; email confirmations not delivered; users cannot complete signup | New user registrations partially completed (user created, but unverified); support tickets spike | GoTrue logs: `error sending confirmation email: 429 Too Many Requests`; Dashboard → Auth → Email provider metrics | Temporarily disable email confirmation requirement; switch to alternative SMTP provider | Use custom SMTP (Resend, SendGrid) with higher rate limits; implement signup queue with exponential email send rate |
| pg_cron job failing silently causing stale data accumulation | Scheduled cleanup or aggregation jobs no longer run; stale data grows; query performance degrades gradually | Dependent features reading stale aggregation tables; gradual slowdown over days | `SELECT * FROM cron.job_run_details ORDER BY start_time DESC LIMIT 20;` shows failed status with no alert | Fix job and backfill: `SELECT cron.schedule('cleanup', '*/5 * * * *', 'DELETE FROM stale_records WHERE created_at < now()-interval ''7 days''')` | Alert on `cron.job_run_details` failure count > 3 consecutive; test jobs in staging |
| RLS policy misconfiguration after schema migration | Row-level security policies referencing old column names return errors; all data access fails for affected tables | All application queries on affected tables return 500; auth is unaffected but data reads/writes fail | PostgREST logs: `ERROR: column <old_name> does not exist`; correlate with recent migration timestamp | Temporarily disable RLS on affected table (brief window only): `ALTER TABLE <table> DISABLE ROW LEVEL SECURITY`; fix policy column names; re-enable | Run `\d+ <table>` after every migration to verify RLS policy column references; include RLS verification in migration CI |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Adding a new index on large table with `CREATE INDEX` (blocking) | Table locked during index build; all writes to table fail; PostgREST returns 409 or timeout | During index creation (minutes to hours on large tables) | `SELECT query, state, wait_event FROM pg_stat_activity WHERE query LIKE 'CREATE INDEX%'`; correlate with deploy time | Cancel index creation: `SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE query LIKE 'CREATE INDEX%'`; use `CREATE INDEX CONCURRENTLY` instead |
| Enabling `REPLICA IDENTITY FULL` on high-write table | WAL write volume doubles or triples; Realtime lag increases; replication slot WAL accumulates | Immediately after `ALTER TABLE`; visible within minutes | `SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) FROM pg_replication_slots;` — WAL lag grows | Revert: `ALTER TABLE <table> REPLICA IDENTITY DEFAULT`; monitor WAL lag recovery | Use `REPLICA IDENTITY FULL` only for tables with no primary key that need full row change events |
| Upgrading PostgREST configuration without schema cache reload | New columns or tables not visible in API; PostgREST returns 404 for new resources | Immediately after config change if cache not reloaded | `curl https://<project>.supabase.co/rest/v1/<new_table>` returns 404; compare with `\dt` in psql | Reload schema cache: `NOTIFY pgrst, 'reload schema'` | Add schema cache reload to deployment runbook for every migration |
| Changing `anon` or `authenticated` role grants | PostgREST requests return 403 for previously working endpoints | Immediately after `REVOKE` or policy change | PostgREST logs: `permission denied for table <name>`; correlate with recent `REVOKE` in migration | Re-grant permissions: `GRANT SELECT ON <table> TO anon;` | Use `GRANT` audit in migration review; test with `anon` role before deploying |
| Rotating Supabase `service_role` key without updating backend services | Backend services using old `service_role` JWT receive 401 on all admin API calls | Immediately after key rotation | 401 spike in server-to-Supabase API calls; correlate with key rotation in Supabase dashboard | Revert to previous `service_role` key if dual-key rotation not available; update all services with new key | Coordinate key rotation; use secrets manager (Vault, AWS Secrets Manager) with automated rotation push |
| Deploying Edge Function with missing environment variable | Edge Function returns 500 or incorrect behavior for all invocations | Immediately on first function invocation after deploy | `supabase functions logs <fn-name>` shows `undefined` or `ReferenceError: <VAR> is not defined`; correlate with deploy | Redeploy with correct secrets: `supabase secrets set VAR=value && supabase functions deploy <fn>` | Validate required env vars in CI before deployment; use `supabase secrets list` to verify |
| Enabling Supabase MFA enforcement without notifying existing users | All users without MFA enrolled are immediately locked out; support tickets spike | Immediately after enabling MFA enforcement in Auth settings | Auth login attempts returning `mfa_required` for users without factors enrolled; spike in support requests | Temporarily disable MFA enforcement; run enrollment campaign before re-enabling | Use soft enforcement (prompt but don't block) first; give existing users 30-day enrollment window |
| Adding a NOT NULL column without a DEFAULT in a migration | Migration fails mid-run; database in inconsistent state; some tables updated, others not | During migration execution | `psql` migration output: `ERROR: column of relation violates not-null constraint`; deploy fails | Roll back migration; add `DEFAULT` or use `ALTER TABLE ADD COLUMN ... DEFAULT NULL` then backfill | Always add NOT NULL columns as nullable first; backfill; then add constraint; test migrations against production-size data |
| Changing PostgREST `db-schema` search path | Previously working table references break; API returns 404 for resources in non-default schemas | Immediately after config change | PostgREST logs: `relation <table> does not exist`; compare with previous `db-schema` config | Restore previous `db-schema` value in Supabase dashboard → Settings → API → DB Schema | Document all schemas used by API; test schema changes against staging |
| Supabase client library major version upgrade in mobile app | JWT refresh behavior changes; users get logged out after token expiry due to refresh logic differences | On next token refresh (typically 1 hour after update) | Spike in `auth.users` logout events; app logs showing session refresh failure; correlate with library version bump | Hotfix: revert to previous client library version; push emergency update | Pin library versions; read migration guide before upgrading; test auth flows in staging |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Read replica serving stale data after heavy write burst | `SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;` (run on replica) | Clients reading from replica see old data immediately after writes; forms show outdated state | Users experience phantom rollbacks; duplicate submissions; incorrect balance displays | Route time-sensitive reads to primary: set PostgREST `db-use-legacy-gucs=false`; add `prefer=head` hint for strong consistency reads |
| Storage metadata and file object out of sync after failed upload | `SELECT name FROM storage.objects WHERE bucket_id = '<bucket>'` lists file but object not in S3 | File appears in Supabase Storage UI and API but download returns 404; orphaned metadata rows | Broken download links; confused users; incorrect storage usage accounting | Remove orphaned metadata: `DELETE FROM storage.objects WHERE name = '<file>' AND bucket_id = '<bucket>'`; resync via Storage API `POST /object/<bucket>/<path>` |
| Auth user record exists but profile table row missing (trigger failed) | `SELECT u.id FROM auth.users u LEFT JOIN public.profiles p ON u.id = p.id WHERE p.id IS NULL;` | User can log in but app crashes on profile fetch; `null` reference errors in app | Broken user experience; app unusable for affected accounts | Backfill missing profiles: `INSERT INTO public.profiles (id, ...) SELECT id, ... FROM auth.users WHERE id NOT IN (SELECT id FROM public.profiles)` |
| Edge Function and PostgREST writing same record concurrently (no transaction) | Check for `updated_at` timestamp anomalies: `SELECT id, updated_at FROM <table> ORDER BY updated_at DESC LIMIT 20` | Race condition: last writer wins; data from one source silently overwritten | Intermittent data loss; audit trail gaps; user sees their update reverted | Add optimistic locking: include `updated_at` in `WHERE` clause; use PostgreSQL advisory locks for critical sections |
| RLS policy allowing cross-tenant data reads after tenant ID column added | `SET ROLE authenticated; SET request.jwt.claim.sub TO '<tenant1_user_id>'; SELECT * FROM <table>;` — returns tenant2 rows | Users can see other tenants' data; privacy and compliance violation | Critical security incident; potential data breach; immediate escalation required | Immediately disable public access: `ALTER TABLE <table> DISABLE ROW LEVEL SECURITY` only if fixing takes > 5 min; fix RLS policy; re-enable; notify affected tenants |
| Realtime events delivered out of order due to WAL decoder buffering | Subscribe to table with `REPLICA IDENTITY FULL`; insert rows with sequence numbers; verify received order in Realtime client | Downstream systems processing events in wrong order; event sourcing state machine corruption | Data integrity issues in event-driven workflows; incorrect aggregations | Switch to polling with `order=id.asc` for order-critical data; use PostgreSQL `sequence` column for ordering verification |
| Migration applied to production but not to read replica | `SELECT schemaname, tablename FROM pg_tables WHERE tablename = '<new_table>';` — run on primary vs replica | Read replica missing new table; queries routed to replica fail with `relation does not exist` | 50% of reads fail (if 50% routed to replica); intermittent 404 errors | Force replica sync: verify `pg_last_xact_replay_timestamp()` is current; restart replica if lagging | Monitor replication lag after every migration; add post-migration verification step |
| Storage RLS policy and bucket policy in conflict | `SELECT * FROM storage.buckets WHERE name = '<bucket>';` — check `public` flag vs RLS policy | Public bucket with restrictive RLS returns 403; private bucket with permissive RLS leaks files | Either data exposure or broken functionality depending on conflict direction | Audit and align: if intended public → remove RLS restriction; if intended private → set `public=false` on bucket | Treat bucket-level and RLS policy as the same access control layer; document intent for each bucket |
| pg_cron job and application both modifying same aggregation table | Compare `updated_at` and row counts before/after cron execution: `SELECT count(*) FROM <agg_table>;` | Aggregation table shows incorrect totals; values oscillate between correct and stale on refresh | Business metric dashboards show wrong numbers; decisions based on stale aggregations | Add distributed lock: `SELECT pg_advisory_lock(12345)` at start of cron job; application uses same lock before batch writes | Use write-once aggregation pattern; separate raw events table from aggregated view updated only by cron |

## Runbook Decision Trees

### Decision Tree 1: PostgREST API Returning 500 Errors

```
Is the error consistent across all endpoints or specific tables?
├── ALL ENDPOINTS → Is PostgreSQL reachable? (`curl https://<project>.supabase.co/rest/v1/`)
│                   ├── 503 → Database is down → Check Dashboard → Database → Status
│                   │         ├── Disk full → Upgrade storage or delete data: `DELETE FROM <large_table> WHERE created_at < now()-interval '90 days'`
│                   │         └── DB crashed → Trigger restore from backup in Dashboard → Database → Backups
│                   └── 500 → PostgREST service issue → Check Supabase status page (status.supabase.com)
│                             └── Restart PostgREST via Dashboard → Database → Restart (if self-hosted: `docker restart supabase-rest`)
└── SPECIFIC TABLE/ENDPOINT → Check RLS policies: `SELECT * FROM pg_policies WHERE tablename = '<table>';`
                              ├── Policy syntax error → Fix and reload: `DROP POLICY <name> ON <table>; CREATE POLICY ...`
                              └── Column reference error (after migration) → `NOTIFY pgrst, 'reload schema'`
                                  ├── Schema reload fixes it → Deployment step missing schema reload; add to CI
                                  └── Still failing → Test as role directly: `SET ROLE authenticated; SELECT * FROM <table>;`
                                                      └── Permission denied → Regrant: `GRANT SELECT ON <table> TO authenticated;`
```

### Decision Tree 2: Supabase Realtime Subscriptions Dropping or Not Receiving Events

```
Are all channels affected or specific tables only?
├── ALL CHANNELS → Is Realtime service healthy? (`curl https://<project>.supabase.co/realtime/v1/api/health`)
│                  ├── NOT HEALTHY → Check replication slot lag: `SELECT slot_name, pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) FROM pg_replication_slots;`
│                  │                 ├── Lag > 1GB → Drop stale slot: `SELECT pg_drop_replication_slot('<slot>');`; Realtime auto-reconnects
│                  │                 └── Lag normal → Realtime service crash → Check Supabase status page; contact support
│                  └── HEALTHY → Client-side disconnect? Check browser network tab for WebSocket close frames
│                                └── 1006 close code → Network issue or client timeout → increase heartbeat interval in client config
└── SPECIFIC TABLE → Does table have a primary key? (`\d+ <table>` in psql — check for PRIMARY KEY)
                     ├── NO PRIMARY KEY → Add primary key or use `REPLICA IDENTITY FULL`: `ALTER TABLE <table> REPLICA IDENTITY FULL`
                     └── HAS PRIMARY KEY → Is `REPLICA IDENTITY` set correctly?
                                           ├── DEFAULT (no full row) → Old values in UPDATE events missing → `ALTER TABLE <table> REPLICA IDENTITY FULL` if old values needed
                                           └── FULL → Check RLS: is the Realtime subscription user authorized to read the table?
                                                       └── `SET ROLE authenticated; SELECT * FROM <table>;` — if fails, fix RLS policy for Realtime user
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Storage egress cost spike from unoptimized image serving | Large images served directly without CDN caching or image transformation | Supabase Dashboard → Storage → Egress; or check `SELECT sum(metadata->>'size')::bigint/1024/1024 AS total_mb FROM storage.objects WHERE bucket_id='<bucket>'` | Egress charges spike; storage bandwidth quota consumed; plan upgrade required | Enable Supabase Image Transformations to serve resized images; add `Cache-Control: public, max-age=3600` response headers | Use Supabase Transform API for all image serving; configure CDN in front of Storage |
| Edge Function invocation storm from webhook replay | Webhook provider replaying all events due to delivery failure; function invoked millions of times | `supabase functions logs <fn> --tail` — count invocations per minute; Dashboard → Edge Functions → Invocations graph | Function invocation quota exhausted; unexpected compute charges; downstream services overwhelmed | Add idempotency key check in function: `SELECT id FROM processed_webhooks WHERE idempotency_key = $1` before processing | Implement idempotency table; verify webhook signatures; add deduplication with `ON CONFLICT DO NOTHING` |
| Unindexed column in RLS policy causing full table scan per request | RLS policy uses `auth.uid() = user_id` on unindexed `user_id` column; every request scans entire table | `EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM <table>;` — check for `Seq Scan` with RLS filter | CPU and I/O spike proportional to table size × request rate; DB CPU pegged | `CREATE INDEX CONCURRENTLY idx_<table>_user_id ON <table>(user_id);` | Include index creation for RLS policy columns in migration; run `EXPLAIN` on RLS-filtered queries in CI |
| Realtime presence state bloat from abandoned connections | Presence state accumulates for disconnected clients never cleaned up; large presence payloads on every heartbeat | `SELECT count(*), pg_size_pretty(sum(pg_column_size(payload))) FROM realtime.presences;` | Presence broadcast payload size grows; network bandwidth spikes; slow presence events | Clear stale presence: Realtime handles cleanup on disconnect — ensure `presenceKey` is unique per client and connections are properly closed | Set explicit presence timeout in client; use Realtime v2 which has improved presence cleanup |
| pg_cron job running expensive query every minute | Misconfigured cron schedule (every minute instead of every hour); DB CPU saturated | `SELECT jobname, schedule, active FROM cron.job;` — check schedules; `SELECT avg(run_duration) FROM cron.job_run_details GROUP BY jobid` | DB CPU continuously high; all user-facing queries degraded; connection pool exhaustion | Update schedule: `SELECT cron.alter_job(job_id := <id>, schedule := '0 * * * *')` | Review all cron schedules before enabling; set cron job execution time alert |
| Storage multipart upload orphans accumulating | Failed large file uploads leaving multipart upload parts that are never completed or aborted | `SELECT count(*), sum(upload_metadata->>'size')::bigint/1024/1024 AS mb FROM storage.s3_multipart_uploads WHERE status='in_progress' AND insert_at < now()-interval '24h'` | Storage quota consumed by invisible incomplete uploads; billing increases | Abort stale multipart uploads: `DELETE FROM storage.s3_multipart_uploads WHERE status='in_progress' AND insert_at < now()-interval '24h'` | Implement upload completion guarantee in client; set multipart upload TTL cleanup job |
| Supabase Auth `sign_in_with_otp` email OTP abuse | Attacker using OTP endpoint to send spam emails; thousands of OTPs sent per minute | `SELECT count(*), created_at::date FROM auth.audit_log_entries WHERE payload->>'action'='user_signedup' GROUP BY 2 ORDER BY 1 DESC` | SMTP quota exhausted; legitimate users cannot receive OTP emails; email provider account flagged | Enable Captcha: Dashboard → Auth → Email → Enable CAPTCHA; add rate limiting: `max_request_frequency` in auth config | Implement CAPTCHA on all auth flows; monitor OTP send rate; set rate limit per IP |
| Large transaction log from heavy update workload bloating WAL | High-frequency UPDATE statements on wide tables with `REPLICA IDENTITY FULL` generating 10× WAL volume | `SELECT pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), pg_current_wal_flush_lsn())) AS pending_wal` | WAL disk usage spikes; replication slot lag grows; potential disk exhaustion | Revert `REPLICA IDENTITY` to DEFAULT: `ALTER TABLE <table> REPLICA IDENTITY DEFAULT`; batch updates to reduce WAL churn | Use `REPLICA IDENTITY FULL` only on tables that Realtime actually subscribes to; prefer partial row updates (UPDATE only changed columns) |
| Connection string leaked in client-side code exposing `service_role` key | `service_role` key used in browser JavaScript; attackers using key to bypass RLS | `curl -H "apikey: <service_role_key>" https://<project>.supabase.co/rest/v1/<table>` from external — if succeeds, key is compromised | All RLS policies bypassed; all data accessible; data exfiltration risk | Immediately rotate `service_role` key: Dashboard → Settings → API → Service Role → Regenerate; audit recent `service_role` usage in logs | Never expose `service_role` key to client-side code; use Edge Functions as a proxy for admin operations |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot row / hot table contention in PostgreSQL | INSERT/UPDATE on a single high-traffic table slow; `pg_stat_activity` shows many sessions waiting | `psql $DATABASE_URL -c "SELECT wait_event_type, wait_event, count(*) FROM pg_stat_activity WHERE state='active' GROUP BY 1,2 ORDER BY 3 DESC"` | Missing index; full table lock from DDL migration; autovacuum not keeping up with dead tuples | Run `ANALYZE <table>` immediately; `REINDEX TABLE CONCURRENTLY <table>`; defer migrations to low-traffic window |
| PostgREST connection pool exhaustion | API returns `503 Service Unavailable`; Supabase logs show `connection pool full` | `psql $DATABASE_URL -c "SELECT count(*), usename FROM pg_stat_activity GROUP BY usename ORDER BY count DESC"` — check PostgREST user connections | PostgREST `db-pool` too small for traffic; connections not returned after request | Increase pool: set `db-pool=20` in PostgREST config or Supabase Dashboard → Settings → API; add connection timeout |
| JIT compilation overhead on complex Supabase queries | Dashboard queries slow; `pg_stat_statements` shows high `jit_compilation_time` | `psql $DATABASE_URL -c "SELECT query, jit_generation_time, jit_inlining_time, jit_optimization_time FROM pg_stat_statements ORDER BY (jit_generation_time + jit_optimization_time) DESC LIMIT 10"` | PostgreSQL JIT triggers on complex queries; compilation overhead exceeds execution benefit for short queries | Disable JIT for problematic queries: `SET jit = off` in query; or globally: `ALTER DATABASE <db> SET jit = off` |
| Supabase Realtime WAL decoder backlog | Realtime subscriptions deliver events with 10+ second delay; replication slot lag growing | `psql $DATABASE_URL -c "SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS lag FROM pg_replication_slots WHERE slot_name LIKE 'supabase_realtime%'"` | Realtime consumer not keeping pace with WAL production; large transactions blocking WAL decoding | Reduce Realtime channel filter breadth; disable Realtime on high-write tables; increase Supabase Realtime pod resources |
| Row-Level Security (RLS) policy evaluation overhead | Authenticated queries slow with RLS enabled; `EXPLAIN (ANALYZE, BUFFERS)` shows expensive policy checks | `psql $DATABASE_URL -c "EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM <table> WHERE auth.uid() = user_id LIMIT 10"` | Complex RLS policies with subqueries re-evaluated per row; missing index on RLS filter column | Add index on RLS filter column: `CREATE INDEX ON <table>(user_id)`; simplify policy to avoid correlated subqueries; use security definer functions for complex auth logic |
| CPU steal on Supabase shared infrastructure | Query latency spikes intermittently; Supabase Dashboard shows high CPU but no query explains it | `psql $DATABASE_URL -c "SELECT pg_stat_reset(); SELECT pg_sleep(60); SELECT * FROM pg_stat_bgwriter"` — observe; check Supabase Dashboard CPU metric trends | Shared infrastructure noisy-neighbor effect (Pro plan) | Upgrade to dedicated compute add-on; schedule heavy batch jobs during off-peak; add `pg_sleep` jitter to scheduled queries |
| pg_cron lock contention from overlapping scheduled jobs | Scheduled jobs run longer than their interval; multiple instances of same job run concurrently; table locks pile up | `psql $DATABASE_URL -c "SELECT jobid, jobname, status, start_time, end_time FROM cron.job_run_details WHERE start_time > now() - interval '2 hours' ORDER BY start_time DESC"` | pg_cron jobs not checking for overlapping executions; long-running cleanup job blocks table for next scheduled run | Add advisory lock check at start of each pg_cron job: `IF NOT pg_try_advisory_lock(<job_id>) THEN RETURN; END IF`; increase job interval |
| Edge Function cold start latency | First request to Edge Function after idle period takes 2-5s; users see timeout on initial load | `supabase functions logs --project-ref <ref> | grep -E "cold_start\|boot_time\|duration"` | Deno runtime cold start; large Edge Function bundle size; many npm dependencies | Reduce bundle size; split large functions; use `supabase functions serve` locally to benchmark; keep functions warm with scheduled ping |
| Storage large object upload blocking queries via WAL | Supabase Storage uploads slow; PostgreSQL queries slow during concurrent uploads | `psql $DATABASE_URL -c "SELECT pid, state, wait_event, query FROM pg_stat_activity WHERE query LIKE '%storage%' OR query LIKE '%lo_%'"` | Large object storage uses PostgreSQL `lo_` functions generating heavy WAL; blocks vacuum and checkpoints | Use `storage.objects` table metadata only; serve files from direct S3-compatible endpoint; avoid PostgreSQL large objects for file storage |
| Downstream Supabase Auth (GoTrue) latency on JWT verification | API requests slow to authenticate; Supabase Auth logs show high JWT verification time | `supabase functions logs --project-ref <ref> | grep -E "auth\|jwt\|verify"` — check verification latency; `psql $DATABASE_URL -c "SELECT count(*) FROM auth.sessions WHERE created_at > now() - interval '1 hour'"` | Too many active sessions; JWT secret rotation causing cache miss; Auth service under load | Reduce session TTL in GoTrue config; enable JWT caching; clean stale sessions: `DELETE FROM auth.sessions WHERE not_after < now()` |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on Supabase project custom domain | Browser and clients show `ERR_CERT_DATE_INVALID`; Supabase custom domain HTTPS fails | `echo | openssl s_client -connect <custom-domain>:443 2>/dev/null | openssl x509 -noout -dates` | Let's Encrypt cert auto-renewal failed for custom domain; DNS misconfiguration preventing ACME challenge | Verify DNS CNAME points to Supabase; re-verify custom domain in Dashboard → Settings → Custom Domains; trigger cert re-issuance |
| mTLS failure for Supabase database direct connection (SSL required) | Application with `sslmode=require` fails; `psql $DATABASE_URL` fails with `SSL connection required` | `psql "postgresql://<user>:<pass>@<host>:5432/<db>?sslmode=require" -c "SELECT version()"` — check TLS handshake error | Application SSL cert/key files expired or path misconfigured; wrong CA cert for Supabase project | Download fresh connection string and CA cert from Supabase Dashboard → Settings → Database → Connection string |
| DNS resolution failure for Supabase project database host | Application cannot resolve `<project>.supabase.co`; `nslookup <project>.supabase.co` fails | `nslookup db.<project-ref>.supabase.co`; `dig db.<project-ref>.supabase.co` | Corporate DNS blocking Supabase domain; local DNS cache stale | Flush DNS: `sudo dscacheutil -flushcache` (macOS) or `systemd-resolve --flush-caches` (Linux); check corporate firewall egress rules for `*.supabase.co` |
| TCP connection exhaustion on direct PostgreSQL port (5432) | Application connection timeouts; PostgREST connections fail to acquire from pool | `psql $DATABASE_URL -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state ORDER BY count DESC"` — check if `max_connections` hit | Too many application connections + PostgREST connections + Realtime connections sharing `max_connections` | Use Supabase connection pooler (PgBouncer) on port 6543 instead of direct 5432; set `PGRST_DB_POOL` lower; `ALTER SYSTEM SET max_connections=200` (requires restart) |
| Supabase Realtime WebSocket connection drops | Realtime subscribers disconnected; browser console shows WebSocket `1006 Abnormal Closure` | Supabase Dashboard → Logs → Realtime — filter for `disconnect` events; `wscat -c wss://<project>.supabase.co/realtime/v1/websocket?apikey=<anon>` | Load balancer idle timeout shorter than Realtime heartbeat interval; WebSocket not upgraded correctly | Verify WebSocket upgrade headers in application; check Supabase status page; re-establish subscription with exponential backoff in client SDK |
| Packet loss between Edge Function and external HTTP dependency | Edge Function requests to external APIs fail intermittently; timeout errors in function logs | `supabase functions logs --project-ref <ref> | grep -E "fetch error\|ETIMEDOUT\|ECONNRESET"` | Network path from Supabase edge node to external service lossy; external service rate-limiting | Add retry with exponential backoff in Edge Function; set explicit `fetch` timeout: `await fetch(url, {signal: AbortSignal.timeout(5000)})`; cache responses where possible |
| MTU mismatch causing truncated PostgREST JSON responses | PostgREST API returns malformed JSON for large result sets; client JSON parse error | `curl -s "https://<project>.supabase.co/rest/v1/<table>?limit=100" | python3 -m json.tool` — check for parse error on large responses | Large JSON payloads exceed MTU causing fragmentation and truncation at Supabase CDN layer | Use pagination: add `Range` header or `?limit=20&offset=0`; use Supabase client SDK `.range(0,19)`; enable PostgREST response compression |
| Firewall blocking Supabase Studio database connections | Supabase Dashboard Studio SQL editor returns `connection refused`; Table Editor shows no data | Check Supabase Dashboard → Settings → Network → allowed IP ranges | IP allowlist configured on Supabase project blocking Studio IPs; or corporate firewall blocking Supabase management endpoints | Add Supabase Studio IP ranges to allowed IPs in Dashboard → Settings → Network; or temporarily disable IP allowlist for diagnosis |
| SSL handshake timeout from Supabase Auth (GoTrue) to SMTP provider | Password reset emails not delivered; Supabase Auth logs show SMTP TLS failure | Supabase Dashboard → Logs → Auth — filter for `smtp\|email\|tls` | SMTP provider TLS certificate issue; wrong SMTP port configured (465 vs 587); SMTP server overloaded | Verify SMTP settings in Dashboard → Settings → Auth → SMTP; test with `swaks --tls --server <smtp-host> --port 587 --auth <user>`; use different SMTP provider temporarily |
| Connection reset between Supabase PostgREST and PostgreSQL | PostgREST returns `Connection reset by peer` errors; intermittent 500 errors on API calls | Supabase Dashboard → Logs → API — filter for `error` level; `psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity WHERE application_name='PostgREST'"` — check for connection cycling | PostgreSQL restarted or statement timeout killed PostgREST connections; idle connection timeout in pg_bouncer | Configure PostgREST connection keep-alive; set `db-pool-timeout` appropriately; verify `idle_in_transaction_session_timeout` not terminating PostgREST sessions |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill on Supabase PostgreSQL pod | Database unavailable; Supabase Dashboard shows project paused or restarting; `pg_stat_activity` connections drop to zero | Supabase Dashboard → Settings → Reports → Database health — check restart events; `psql $DATABASE_URL -c "SELECT pg_postmaster_start_time()"` after restore | Upgrade compute add-on for more RAM; identify memory-intensive queries: `SELECT * FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10`; lower `work_mem` |
| Disk full on Supabase project database | INSERT/UPDATE operations fail with `could not extend file: No space left on device`; project may pause | `psql $DATABASE_URL -c "SELECT pg_size_pretty(pg_database_size(current_database()))"` — compare to plan limit; Supabase Dashboard → Settings → Billing shows disk usage | Delete large tables or rows; run `VACUUM FULL` to reclaim space; `SELECT pg_size_pretty(pg_total_relation_size('<table>'))` to find largest tables; upgrade storage add-on | Enable Supabase disk usage alerts; use `pg_partman` for time-partitioned tables with automatic old-partition archival |
| WAL disk full from replication slot lag | Supabase project disk fills with WAL files due to inactive replication slot holding WAL | `psql $DATABASE_URL -c "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS wal_behind FROM pg_replication_slots"` | Drop inactive slots: `SELECT pg_drop_replication_slot('<slot_name>')` — after confirming no active consumer; reduce `wal_keep_size` if needed | Monitor replication slot lag; set `max_slot_wal_keep_size=5GB` in `postgresql.conf` to auto-drop lagging slots |
| File descriptor exhaustion in PostgreSQL | Postgres cannot open new relation files; `FATAL: could not open file ... too many open files` in logs | Supabase Dashboard → Logs → Database — filter for `too many open files`; `psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_file_list()"` | Upgrade compute plan for higher FD limits; close idle connections: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < now() - interval '10 minutes'` | Set `idle_in_transaction_session_timeout=30000`; use connection pooler (port 6543) to reduce direct connections |
| Inode exhaustion on Supabase project storage volume | New table creation fails; `pg_tablespace` operations fail; similar symptom to disk full but `df -h` shows space available | Contact Supabase support — inode metrics not exposed in Dashboard; symptoms: DDL failures with disk errors while `pg_database_size` is low | Upgrade project plan; drop tables with excessive dead tuple fragmentation; run `VACUUM FULL` | Avoid creating millions of small tables; use schemas and partitioning instead of per-tenant tables |
| CPU exhaustion from autovacuum on large tables | Query latency spikes; `pg_stat_activity` shows multiple `autovacuum worker` sessions; user queries waiting | `psql $DATABASE_URL -c "SELECT relname, n_dead_tup, last_autovacuum, last_autoanalyze FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10"` | Reduce autovacuum cost delay: `ALTER TABLE <table> SET (autovacuum_vacuum_cost_delay=2)`; manually run `VACUUM ANALYZE <table>` during off-peak | Tune autovacuum per table for high-update tables: `autovacuum_vacuum_scale_factor=0.01` for large tables |
| Supabase project connection limit hit (plan-based) | API returns `remaining connection slots reserved`; PostgREST returns 503 | `psql $DATABASE_URL -c "SELECT current_setting('max_connections'), count(*) FROM pg_stat_activity"` | Use PgBouncer connection pooler on port 6543 instead of direct 5432; kill idle connections: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle'` | Always use Supabase pooler URL (port 6543) in application; configure PostgREST to use pooler; set `statement_timeout` to prevent long-idle connections |
| Supabase Edge Function memory limit hit | Edge Function returns 500 with `Memory limit exceeded` in logs | `supabase functions logs --project-ref <ref> | grep -E "Memory limit\|OOM\|memory"` | Edge Function processing large payloads; memory leak in Deno runtime; large npm modules | Reduce payload size; stream large responses with `ReadableStream`; split logic into smaller functions; check for retained closures causing memory leaks |
| Supabase Storage bucket quota exhaustion | Storage uploads fail with `storage quota exceeded`; `storage.objects` count at plan limit | `psql $DATABASE_URL -c "SELECT b.name, count(o.id), pg_size_pretty(sum((o.metadata->>'size')::bigint)) FROM storage.buckets b LEFT JOIN storage.objects o ON b.id=o.bucket_id GROUP BY b.name ORDER BY sum((o.metadata->>'size')::bigint) DESC NULLS LAST"` | Delete unused objects: `supabase storage rm --project-ref <ref> 's3://<bucket>/<path>'`; upgrade storage add-on | Set bucket file size limits in RLS; implement object lifecycle cleanup in pg_cron; monitor storage usage in Dashboard |
| Ephemeral port exhaustion in Edge Function execution environment | Edge Functions making many outbound fetch calls exhaust available ports; `EADDRINUSE` errors | `supabase functions logs --project-ref <ref> | grep -E "EADDRINUSE\|port"` | Many sequential `fetch()` calls not reusing connections; keep-alive not enabled | Reuse `fetch` with keep-alive: create single `Deno.HttpClient` with connection pooling; batch API calls; avoid creating new fetch clients per request |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation from Supabase Realtime duplicate events | Realtime `INSERT` event delivered twice for a single database INSERT; client processes same event twice | `psql $DATABASE_URL -c "SELECT id, created_at FROM <table> ORDER BY created_at DESC LIMIT 20"` — check for duplicate IDs within milliseconds of each other; Supabase Realtime logs for `duplicate_message` | Client-side state corrupted; UI shows duplicate items; at-least-once delivery in Realtime WAL decoder | Add deduplication in client: track processed event IDs in `useRef`/`useState`; add `UNIQUE` constraint on business key; use `upsert` semantics on client state |
| Supabase Edge Function partial saga failure leaving orphaned storage objects | Edge Function uploads file to Storage, then fails to write metadata to `storage.objects` / custom table; file orphaned in bucket | `psql $DATABASE_URL -c "SELECT o.name FROM storage.objects o LEFT JOIN <metadata_table> m ON o.name=m.storage_path WHERE m.id IS NULL AND o.created_at < now() - interval '1 hour'"` | Storage cost for orphaned objects; inconsistent application state; users see missing attachments | Delete orphaned objects: `SELECT supabase_storage_admin.delete_object('<bucket>', o.name) FROM storage.objects o LEFT JOIN <metadata_table> m ON o.name=m.storage_path WHERE m.id IS NULL`; implement cleanup pg_cron job |
| Out-of-order Realtime event delivery causing stale UI state | Client receives `UPDATE` event before `INSERT` event for same row; row appears in wrong state | Supabase Dashboard → Logs → Realtime — check event sequence numbers; `psql $DATABASE_URL -c "SELECT lsn, data FROM pg_logical_slot_peek_changes('supabase_realtime_replication_slot', NULL, 10, 'proto_version', '1', 'publication_names', 'supabase_realtime')"` | UI shows inconsistent data; optimistic updates conflict with server state | Use `commit_timestamp` from Realtime event payload to order updates client-side; rebuild state from REST API poll when ordering conflict detected |
| PostgreSQL at-least-once trigger execution causing duplicate Edge Function calls | `pg_net` (database webhooks) or `supabase_functions.http_request` trigger fires twice for one transaction on retry | `psql $DATABASE_URL -c "SELECT * FROM net.requests WHERE url='https://<project>.supabase.co/functions/v1/<fn>' ORDER BY created DESC LIMIT 10"` — check for duplicate calls with same payload | Edge Function side-effects (external API calls, emails) executed twice | Add idempotency key to trigger payload using row ID + transaction timestamp; check for key in Edge Function before processing; use `INSERT ... ON CONFLICT DO NOTHING` in trigger guard table |
| Distributed lock expiry during Supabase DB migration via `supabase db push` | Migration acquires `ACCESS EXCLUSIVE` lock; migration times out mid-DDL; table left in inconsistent state | `psql $DATABASE_URL -c "SELECT pid, query, state, wait_event FROM pg_stat_activity WHERE query LIKE 'ALTER TABLE%' OR query LIKE 'CREATE INDEX%'"` | Table locked or in partially migrated state; application writes fail | Check `pg_stat_activity` for lock holders: `SELECT * FROM pg_locks JOIN pg_stat_activity USING(pid) WHERE relation::regclass='<table>'::regclass`; kill blocking PIDs; complete or rollback migration manually |
| pg_cron job race condition causing duplicate data processing | pg_cron scheduled job starts before previous run completes; both instances process same data window | `psql $DATABASE_URL -c "SELECT jobname, status, start_time, end_time FROM cron.job_run_details WHERE jobname='<job>' AND start_time > now() - interval '2 hours' ORDER BY start_time"` — look for overlapping time ranges | Duplicate records created; aggregation tables double-count; downstream metrics wrong | Add advisory lock at job start: `IF NOT pg_try_advisory_xact_lock(hashtext('<job-name>')) THEN RAISE NOTICE 'Job already running, skipping'; RETURN; END IF`; delete duplicate records |
| Supabase Auth (GoTrue) JWT rotation race condition | Some in-flight requests using old JWT rejected after rotation; users briefly logged out | Supabase Dashboard → Logs → Auth — filter for `invalid_token` or `jwt_expired` around rotation time; `psql $DATABASE_URL -c "SELECT count(*) FROM auth.sessions WHERE expires_at BETWEEN now() AND now() + interval '5 minutes'"` | Users mid-session get 401 errors; must re-authenticate; support ticket spike | Implement JWT refresh with overlap window: keep old secret valid for 60s post-rotation; enable Supabase client SDK auto-refresh; configure `autoRefreshToken=true` in client |
| Compensating transaction failure in Supabase RPC function mid-rollback | `CALL` or `PERFORM` of stored procedure fails mid-execution after partial writes; PostgreSQL transaction not fully rolled back due to exception handling error | `psql $DATABASE_URL -c "SELECT * FROM pg_stat_activity WHERE query LIKE '%<fn_name>%' AND state='idle in transaction'"` — look for stale transactions | Partial writes visible if function used `EXCEPTION` block that swallowed errors; data inconsistency | Kill idle-in-transaction sessions: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle in transaction' AND state_change < now() - interval '5 minutes'`; fix function to use proper exception handling with explicit `ROLLBACK` |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor on Supabase shared compute | One project running heavy analytics query consuming shared PostgreSQL CPU; other projects on same host experience latency | Shared Pro plan projects see increased query latency; Supabase Dashboard shows CPU spike | `psql $DATABASE_URL -c "SELECT pid, query, state, wait_event FROM pg_stat_activity WHERE state='active' ORDER BY query_start ASC"` — kill expensive query: `psql $DATABASE_URL -c "SELECT pg_cancel_backend(<pid>)"` | Upgrade to dedicated compute add-on; schedule heavy analytics via `pg_cron` during off-peak hours; add `statement_timeout` per role: `ALTER ROLE anon SET statement_timeout='5s'` |
| Memory pressure from adjacent tenant's large working set | Shared PostgreSQL buffer cache evicted by neighbor's large table scan; cache hit rate drops for all tenants | Query latency increases 5-10x as data must be re-read from disk; Supabase Dashboard shows buffer cache miss spike | `psql $DATABASE_URL -c "SELECT round(blks_hit::numeric/(blks_hit+blks_read+1)*100,2) AS cache_hit_pct FROM pg_stat_database WHERE datname=current_database()"` | Upgrade to dedicated plan; add `pg_prewarm` extension to restore critical tables to buffer cache: `SELECT pg_prewarm('<table>')` |
| Disk I/O saturation from Storage bucket upload storm | Another project on same infrastructure uploading large files saturating shared disk I/O; PostgREST queries slow due to I/O wait | API response times spike; `pg_stat_activity` shows I/O wait; uploads also slow | `psql $DATABASE_URL -c "SELECT sum(blks_read), sum(blks_written) FROM pg_stat_user_tables"` — compare to baseline | Throttle bulk uploads: add artificial delay between Storage uploads; use `pg_cron` to stagger large object processing; upgrade to dedicated compute with dedicated disk |
| Network bandwidth monopoly from Realtime WAL streaming | High-write project on shared infrastructure flooding WAL; Realtime subscriptions on neighboring projects lag | Realtime events delivered with 30+ second delay; `pg_replication_slots` shows lag growing for all projects | `psql $DATABASE_URL -c "SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS wal_lag FROM pg_replication_slots"` | Disable Realtime on high-write tables: `ALTER PUBLICATION supabase_realtime DROP TABLE <high_write_table>`; upgrade to dedicated compute plan |
| Connection pool starvation from one project hogging PgBouncer slots | High-connection application on shared infrastructure consuming most PgBouncer transaction slots; other projects get connection refused | `psql $DATABASE_URL -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state"` — check if `max_connections` nearly hit; API returns `remaining connection slots reserved` | Kill idle connections: `psql $DATABASE_URL -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < now() - interval '5 minutes'"` | Switch all applications to connection pooler (port 6543); reduce per-application pool size; upgrade plan for higher `max_connections` |
| Storage quota enforcement gap — bucket without size limit consuming all project disk | One application's unrestricted upload bucket filling project storage; database disk also threatened | `psql $DATABASE_URL -c "SELECT b.name, count(o.id), pg_size_pretty(sum((o.metadata->>'size')::bigint)) FROM storage.buckets b JOIN storage.objects o ON b.id=o.bucket_id GROUP BY b.name ORDER BY 3 DESC NULLS LAST"` | Delete large objects: `supabase storage rm --project-ref <ref> 's3://<bucket>/<path>'` | Set bucket file size limit via RLS: add policy `(metadata->>'size')::bigint < 10485760`; implement `pg_cron` storage cleanup job |
| Cross-tenant data leak risk via shared Supabase schema | `public` schema accessible to all authenticated users; Row-Level Security not enforced on new migration-added tables | `psql $DATABASE_URL -c "SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname='public' AND rowsecurity=false"` — find tables without RLS | Enable RLS immediately on all discovered tables: for each table run `ALTER TABLE <table> ENABLE ROW LEVEL SECURITY; ALTER TABLE <table> FORCE ROW LEVEL SECURITY` | Add RLS check to CI pipeline: `psql $DATABASE_URL -c "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND rowsecurity=false"` should be 0 |
| Rate limit bypass via Supabase REST API pagination abuse | One application iterating through all rows via PostgREST `Range` header, exhausting shared PostgREST workers | Other applications receive 503 from PostgREST; API gateway shows high latency | `psql $DATABASE_URL -c "SELECT count(*), application_name FROM pg_stat_activity WHERE application_name='PostgREST' GROUP BY application_name"` — check connection count | Add `statement_timeout` for PostgREST role; limit result rows: add `max_rows=1000` to PostgREST config in Supabase Dashboard → Settings → API |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Prometheus scrape failure — Supabase project metrics unavailable | No PostgreSQL metrics in Grafana; autovacuum and connection alerts not firing | Supabase does not expose a native Prometheus endpoint for managed projects; pg_prometheus extension not installed | `psql $DATABASE_URL -c "SELECT * FROM pg_stat_activity WHERE state='active'"` — manual check; use Supabase Dashboard → Reports for CPU/memory | Install `pg_prometheus` extension if available on plan; use Supabase Metrics API via `supabase db remote changes`; deploy pgbadger as offline analysis tool |
| Trace sampling gap — Edge Function traces missing for failed cold starts | Cold start failures not appearing in distributed tracing; only successful invocations tracked | OpenTelemetry SDK not initialized until Edge Function handler runs; cold start errors in Deno runtime never reach instrumentation | `supabase functions logs --project-ref $PROJECT_REF | grep -E "error\|cold_start\|boot"` — check logs directly | Add initialization tracing before handler: wrap entire Edge Function in try/catch with `console.error()` for boot errors; use Supabase log explorer for cold start analysis |
| Log pipeline silent drop — Supabase 7-day log retention window missed | Security incident discovered 10 days later; Auth and PostgREST logs no longer available | Supabase log explorer retains logs only 7 days; no automatic export configured; investigation blocked | Check `auth.audit_log_entries` table (stored in DB, permanent): `psql $DATABASE_URL -c "SELECT * FROM auth.audit_log_entries WHERE created_at > now() - interval '14 days' ORDER BY created_at"` | Set up automatic log export: use Supabase Edge Function + pg_cron to export logs to S3/GCS daily; or use Supabase Logflare integration for longer retention |
| Alert rule misconfiguration — database size alert using wrong metric | Project disk fills silently; no alert fires despite 95% disk usage | Alert configured on `pg_database_size()` which returns logical data size; actual disk usage includes WAL, temp files, and dead tuples bloat | `psql $DATABASE_URL -c "SELECT pg_size_pretty(pg_database_size(current_database())) AS logical_size"` — compare to Supabase Dashboard actual disk; `psql $DATABASE_URL -c "SELECT pg_size_pretty(pg_total_relation_size('<bloated_table>')) AS total"` | Monitor actual disk via Supabase Dashboard → Settings → Billing; add `pg_cron` check: alert when `pg_database_size() > 0.8 * plan_disk_limit` via `pg_net` webhook |
| Cardinality explosion from PostgREST request labels | If using external Prometheus with PostgREST metrics, high-cardinality `path` labels from dynamic route parameters cause TSDB explosion | PostgREST exposes request metrics with full URL path including row IDs: `/rest/v1/items?id=eq.12345` creates unique label per row ID | `curl https://<project>.supabase.co/rest/v1/rpc/pg_stat_statements_reset -H "apikey: <service_role>"` — reset; then check query patterns | Add Prometheus `metric_relabel_configs` to normalize PostgREST path labels: replace numeric IDs with `:id` placeholder |
| Missing health endpoint — Supabase project paused state not detectable until request fails | Supabase project auto-paused due to inactivity; external monitoring shows healthy; first real request fails | Supabase projects on Free plan auto-pause after 7 days inactivity; external HTTP monitor checks `/rest/v1/` which returns 503, but may be misconfigured to check a different endpoint | `curl -s -o /dev/null -w "%{http_code}" "https://<project>.supabase.co/rest/v1/?apikey=<anon>"` — check for 503 or `Supabase project is not active` | Configure monitoring to check `https://<project>.supabase.co/rest/v1/` with `apikey` header; add keep-alive request via Supabase Cron to prevent auto-pause on free plan |
| Instrumentation gap in RLS policy evaluation overhead | Slow queries reported by users but `pg_stat_statements` shows fast mean execution time | RLS policy evaluation time is included in total query duration but not separately tracked; complex policies adding 50ms invisible in aggregate stats | `psql $DATABASE_URL -c "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT * FROM <table> WHERE true"` — check if RLS filter adds significant cost; compare with `SET row_security=off` execution plan | Add separate `pg_stat_user_functions` monitoring for security definer functions used in RLS; use `auto_explain.log_min_duration=100` to capture slow queries with plans |
| Alertmanager outage — Supabase webhook alert delivery fails during database overload | Database running out of connections; `pg_net` webhook alerts not delivered; on-call engineer not paged | `pg_net` extension sends HTTP requests from within PostgreSQL; during connection exhaustion, `pg_net` background worker cannot acquire connection to send alert | `psql $DATABASE_URL -c "SELECT id, status, error_msg FROM net.requests WHERE created_at > now() - interval '1 hour' ORDER BY created_at DESC LIMIT 10"` — check failed webhook deliveries | Configure external monitoring (UptimeRobot/Better Uptime) directly checking Supabase project health endpoint independent of database; use Supabase Dashboard email alerts as secondary notification channel |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| `supabase db push` migration failure midway through DDL | Table left in partially migrated state; `supabase_migrations.schema_migrations` has incomplete entry; application queries fail | `psql $DATABASE_URL -c "SELECT version, statements FROM supabase_migrations.schema_migrations ORDER BY version DESC LIMIT 5"` — check last applied; `psql $DATABASE_URL -c "SELECT * FROM pg_locks WHERE NOT granted"` | Manually rollback: `psql $DATABASE_URL -c "BEGIN; <reverse-DDL>; DELETE FROM supabase_migrations.schema_migrations WHERE version='<failed>'; COMMIT"` | Wrap migrations in explicit transactions; use `supabase db diff` to preview changes; test with `supabase db reset` in local environment first |
| PostgreSQL major version upgrade (e.g., 14 → 15) on Supabase — extension incompatibility | After Supabase upgrades managed PostgreSQL, extension functions change signatures; application queries fail | `psql $DATABASE_URL -c "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE installed_version IS NOT NULL"` — check extension versions | Contact Supabase support for rollback; temporarily disable affected extension: `psql $DATABASE_URL -c "DROP EXTENSION <ext> CASCADE; CREATE EXTENSION <ext>"` | Test all extension-dependent queries against new PostgreSQL version in Supabase local dev: `supabase start --db-image supabase/postgres:15`; subscribe to Supabase changelog |
| Schema migration partial completion — `CREATE INDEX CONCURRENTLY` interrupted | Index in `pg_indexes` but marked invalid; queries do not use index; `pg_stat_user_indexes` shows `idx_scan=0` | `psql $DATABASE_URL -c "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='<table>'"` — check for invalid indexes; `psql $DATABASE_URL -c "SELECT * FROM pg_class WHERE relkind='i' AND relpages=0 AND relname LIKE '<index>'"` | Drop invalid index: `psql $DATABASE_URL -c "DROP INDEX CONCURRENTLY <invalid_index>"`; re-run migration | Always use `CREATE INDEX CONCURRENTLY` for zero-downtime; add `IF NOT EXISTS`; monitor with `psql $DATABASE_URL -c "SELECT relname, indisvalid FROM pg_index JOIN pg_class ON pg_class.oid=indexrelid WHERE NOT indisvalid"` |
| Rolling migration version skew — `supabase db push` applied to prod but not all Edge Functions redeployed | Edge Functions using old schema; queries fail on renamed/dropped columns | `supabase functions list --project-ref <ref>`; compare deployed function version timestamps to migration timestamps; `supabase functions logs <fn> | grep -E "column.*not exist\|ERROR"` | Redeploy all Edge Functions: `supabase functions deploy --project-ref <ref>` for each function; or rollback migration if functions cannot be redeployed quickly | Coordinate `supabase db push` and `supabase functions deploy` in a single CI step to deploy migrations + functions together; add schema validation to Edge Function startup |
| Zero-downtime RLS policy migration gone wrong — policy gap during transition | Between `DROP POLICY old_policy` and `CREATE POLICY new_policy`, table has no RLS protection; data briefly visible to all users | `psql $DATABASE_URL -c "SELECT count(*) FROM pg_policies WHERE tablename='<table>'"` — check policy count is 0 during migration; `psql $DATABASE_URL -c "SELECT * FROM auth.audit_log_entries WHERE created_at > '<migration_time>'"` | Immediately add temporary permissive policy: `psql $DATABASE_URL -c "CREATE POLICY temp_block ON <table> USING (false)"`; investigate if any data was accessed during gap | Always create new policy before dropping old: `CREATE POLICY new_policy ... ; DROP POLICY old_policy`; never drop policy before replacement is live |
| Supabase Auth (GoTrue) version upgrade breaking JWT format | After Supabase updates GoTrue, JWTs contain new claims; existing RLS policies checking old claim format fail | `psql $DATABASE_URL -c "SELECT auth.jwt()"` — decode current JWT structure; `psql $DATABASE_URL -c "SELECT current_setting('request.jwt.claims', true)::json"` in RLS context | Add compatibility shim in RLS policy to handle both old and new JWT formats; contact Supabase support for rollback | Subscribe to Supabase changelog; test JWT claim changes in Supabase local dev before production update |
| Feature flag rollout — enabling `pgvector` extension causing index build regression | After enabling `pgvector` and creating vector index, all queries on that table slow due to `ivfflat` index misconfiguration | `psql $DATABASE_URL -c "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='<table>'"` — check vector index type and params; `psql $DATABASE_URL -c "EXPLAIN SELECT * FROM <table> ORDER BY embedding <-> '[1,2,3]' LIMIT 10"` | Drop misconfigured index: `psql $DATABASE_URL -c "DROP INDEX CONCURRENTLY <vector_index>"`; rebuild with correct params: `CREATE INDEX CONCURRENTLY ON <table> USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)` | Benchmark `ivfflat` vs `hnsw` for dataset size before production deployment; set `lists = rows/1000` for optimal recall vs speed |
| Supabase CLI version conflict — `supabase db push` generates incompatible migration SQL | After upgrading Supabase CLI locally, generated migration SQL uses syntax not supported by project's PostgreSQL version | `supabase --version`; `psql $DATABASE_URL -c "SELECT version()"` — check PostgreSQL version; `supabase db diff 2>&1 | head -30` — check for syntax errors | Pin Supabase CLI version: `npm install -g supabase@<prev-version>`; regenerate migration with pinned version | Pin Supabase CLI version in CI: `npm install -g supabase@1.x.x`; test migrations in `supabase local` before pushing to production |

## Kernel/OS & Host-Level Failure Patterns

| Failure Pattern | Symptom | Why It Happens | Detection Command | Remediation |
|----------------|---------|---------------|-------------------|-------------|
| OOM killer targets PostgreSQL backend on self-hosted Supabase | PostgreSQL process killed; all Supabase APIs return 500; `dmesg` shows OOM kill for `postgres` | Supabase PostgreSQL `shared_buffers` + `work_mem` * active connections exceeds available RAM; kernel OOM killer selects postgres as top RSS process | `dmesg -T \| grep -E 'oom-kill.*postgres'`; `cat /proc/$(pgrep -xf 'postgres' \| head -1)/oom_score`; `psql $DATABASE_URL -c "SHOW shared_buffers; SHOW work_mem;"` | Set `oom_score_adj=-1000` for postgres; tune `shared_buffers` to 25% of RAM; limit `work_mem` per connection: `ALTER SYSTEM SET work_mem='8MB'`; set `max_connections` to match available RAM |
| Inode exhaustion from Supabase Storage local filesystem | Supabase Storage API returns `ENOSPC` for new uploads; existing files accessible | Supabase Storage using local filesystem backend; millions of small files (avatars, thumbnails) exhaust inodes on ext4 | `df -i /var/lib/supabase/storage`; `find /var/lib/supabase/storage -type f \| wc -l` | Migrate to S3-compatible storage backend: set `STORAGE_BACKEND=s3` in Supabase config; or reformat with XFS (dynamic inodes); clean orphaned files |
| CPU steal causing PostgREST timeout on shared cloud VMs | Supabase API requests timeout; PostgREST health check passes but query execution slow; `CPU steal` >20% | Shared VM infrastructure; PostgREST PostgreSQL queries CPU-bound; hypervisor stealing cycles | `top -bn1 \| grep '%st'`; `psql $DATABASE_URL -c "SELECT pid, state, wait_event_type, query FROM pg_stat_activity WHERE state='active'"` | Upgrade to dedicated CPU instance; or migrate to Supabase managed platform; reduce query complexity; add indexes for slow queries |
| NTP skew causing Supabase Auth JWT validation failure | Users getting 401 errors; JWT `exp` claim appears expired despite being fresh; intermittent auth failures | Clock skew between Supabase Auth (GoTrue) container and PostgreSQL; JWT `iat`/`exp` validation uses system clock; skewed clock rejects valid tokens | `docker exec supabase-auth date +%s`; `docker exec supabase-db date +%s`; compare timestamps; `psql $DATABASE_URL -c "SELECT now(), current_timestamp"` | Sync NTP across all Supabase containers: `docker exec supabase-auth ntpd -q -p pool.ntp.org`; add `--privileged` for NTP access in Docker; or use host network mode for time sync |
| File descriptor exhaustion on PostgREST under connection surge | PostgREST returns 503; `Too many open files` in PostgREST logs; Supabase REST API completely unavailable | PostgREST holds FD per PostgreSQL connection + HTTP client connection; default `ulimit -n 1024` exhausted during traffic spike | `cat /proc/$(pgrep -f postgrest)/limits \| grep 'Max open files'`; `ls /proc/$(pgrep -f postgrest)/fd \| wc -l`; `docker logs supabase-rest 2>&1 \| grep 'Too many open files'` | Increase FD limit in Docker compose: `ulimits: {nofile: {soft: 65536, hard: 65536}}`; configure PostgREST `db-pool-size` to match available FDs; add connection pooling via PgBouncer |
| TCP conntrack saturation from Supabase Realtime WebSocket connections | New Realtime subscriptions fail; existing WebSocket connections work; `Connection timed out` for new clients | Thousands of persistent WebSocket connections from Realtime clients fill conntrack table on host running Supabase | `sysctl net.netfilter.nf_conntrack_count`; `sysctl net.netfilter.nf_conntrack_max`; `dmesg \| grep 'nf_conntrack: table full'`; `ss -s \| grep estab` | Increase conntrack: `sysctl -w net.netfilter.nf_conntrack_max=524288`; increase conntrack timeout for established connections: `sysctl -w net.netfilter.nf_conntrack_tcp_timeout_established=86400` |
| NUMA imbalance causing PostgreSQL vacuum performance degradation | Autovacuum takes 10x longer than expected; dead tuple count growing; table bloat increasing | PostgreSQL `autovacuum_workers` allocated across NUMA nodes; cross-NUMA memory access slows heap scan and index cleanup | `numactl --hardware`; `numastat -p $(pgrep -xf 'postgres' \| head -1)`; `psql $DATABASE_URL -c "SELECT relname, n_dead_tup, last_autovacuum FROM pg_stat_user_tables WHERE n_dead_tup > 10000 ORDER BY n_dead_tup DESC"` | Start PostgreSQL with NUMA interleaving: `numactl --interleave=all postgres`; or set `autovacuum_max_workers` to match single NUMA node core count |
| Cgroup memory pressure causing Supabase Edge Function cold start regression | Edge Functions take >5s to cold start (baseline 500ms); `container_memory_working_set_bytes` near limit | Deno runtime for Edge Functions shares cgroup with other Supabase services; memory pressure causes kernel page reclaim during function initialization | `docker stats supabase-functions --no-stream`; `cat /sys/fs/cgroup/memory/docker/<container-id>/memory.stat \| grep pgmajfault` | Isolate Edge Functions in separate container with dedicated memory limit; increase memory allocation for functions container; pre-warm functions with scheduled invocations |

## Deployment Pipeline & GitOps Failure Patterns

| Failure Pattern | Symptom | Why It Happens | Detection Command | Remediation |
|----------------|---------|---------------|-------------------|-------------|
| Supabase Docker image pull failure during self-hosted upgrade | `docker compose up` fails with image pull error; Supabase services not updated; old containers still running | Docker Hub rate limit for `supabase/` images; or GitHub Container Registry (ghcr.io) auth expired | `docker compose pull 2>&1 \| grep -E 'error\|denied\|rate'`; `docker compose ps` — check image versions | Mirror Supabase images to private registry; add Docker Hub credentials: `docker login`; pin image versions in `docker-compose.yml` instead of using `latest` |
| Helm chart drift between Git and live Supabase Kubernetes config | `helm diff` shows no changes but Supabase behavior differs; PostgreSQL settings changed via `psql` directly | DBA ran `ALTER SYSTEM SET` directly on PostgreSQL; Helm values not updated; next Helm upgrade may reset settings | `helm diff upgrade supabase <chart> -f values.yaml`; `psql $DATABASE_URL -c "SELECT name, setting, source FROM pg_settings WHERE source='configuration file'"` | Enforce all PostgreSQL config via Helm values; use ConfigMap for `postgresql.conf` overrides; add CI check comparing live settings to Git |
| ArgoCD sync stuck on Supabase PVC resize | ArgoCD `OutOfSync` for Supabase PostgreSQL PVC; resize pending; pod restart required | PVC resize requires pod restart on some storage classes; ArgoCD cannot trigger StatefulSet pod deletion | `kubectl get pvc -n supabase \| grep postgres`; `kubectl describe pvc <pvc> -n supabase \| grep -E 'Resize\|Condition'`; `argocd app get supabase --show-operation` | Restart PostgreSQL pod: `kubectl delete pod <postgres-pod> -n supabase`; ensure StorageClass has `allowVolumeExpansion: true` |
| PDB blocking Supabase PostgreSQL pod upgrade | PostgreSQL pod cannot be evicted during upgrade; PDB `minAvailable: 1` with single replica | Only 1 PostgreSQL replica in Supabase self-hosted; PDB prevents eviction; upgrade blocked indefinitely | `kubectl get pdb -n supabase`; `kubectl describe pdb supabase-db-pdb -n supabase` | Temporarily delete PDB for upgrade window; or add read replica before upgrade for HA; `kubectl delete pdb supabase-db-pdb -n supabase`; upgrade; recreate PDB |
| Blue-green cutover failure during Supabase platform migration | Green Supabase instance has stale data; logical replication lag not verified before DNS cutover; users see old data | `pg_logical` replication slot behind; green database not caught up; health check passes but data stale | `psql $OLD_DATABASE_URL -c "SELECT slot_name, confirmed_flush_lsn, pg_current_wal_lsn() FROM pg_replication_slots"`; compare LSN positions | Verify replication caught up before cutover: `confirmed_flush_lsn = pg_current_wal_lsn()`; add application-level data integrity check; keep old instance available for 24h rollback |
| ConfigMap drift — Supabase `.env` config differs from running containers | Supabase services using environment variables from old `.env`; new config in Git not applied | `docker compose up` was run without `--force-recreate`; containers using cached env vars from previous run | `docker compose config \| grep <key>`; compare to `docker exec <container> env \| grep <key>` | Run `docker compose up -d --force-recreate` to apply new env; or `docker compose down && docker compose up -d`; add CI check verifying container env matches `.env` file |
| Secret rotation breaks Supabase JWT secret | All API requests return 401; PostgREST, GoTrue, and Realtime all reject tokens; new tokens also fail | `JWT_SECRET` rotated in `.env` but only some containers restarted; GoTrue issuing tokens with new secret, PostgREST validating with old | `docker exec supabase-rest env \| grep JWT_SECRET \| md5sum`; `docker exec supabase-auth env \| grep JWT_SECRET \| md5sum` — compare hashes | Restart ALL Supabase containers atomically: `docker compose down && docker compose up -d`; never partially restart; verify: `curl -H "Authorization: Bearer <token>" https://<project>/rest/v1/` |
| `supabase db push` migration runs against wrong environment | Production database modified by migration intended for staging; data schema changed unexpectedly | `DATABASE_URL` environment variable points to production; developer ran `supabase db push` without `--linked` flag verification | `psql $DATABASE_URL -c "SELECT version, name FROM supabase_migrations.schema_migrations ORDER BY version DESC LIMIT 5"`; check migration matches staging expectations | Add environment guards: `supabase db push --linked` requires explicit project ref; add `SUPABASE_DB_PUSH_CONFIRM=production` env var check in CI; use separate `.env.staging` and `.env.production` |

## Service Mesh & API Gateway Edge Cases

| Failure Pattern | Symptom | Why It Happens | Detection Command | Remediation |
|----------------|---------|---------------|-------------------|-------------|
| Istio circuit breaker ejects Supabase PostgREST during slow query | REST API returns 503; Envoy marks PostgREST as unhealthy; all API requests fail | Single slow PostgreSQL query causes PostgREST response >30s; Istio outlier detection ejects PostgREST backend | `istioctl proxy-config endpoint <app-pod> --cluster 'outbound\|3000\|\|supabase-rest' \| grep UNHEALTHY`; `kubectl logs -l app=supabase-rest -c istio-proxy \| grep outlier` | Increase outlier tolerance: `outlierDetection: {consecutiveGatewayErrors: 10, interval: 60s}` in DestinationRule; add query timeout in PostgREST: `db-statement-timeout=30000` |
| Rate limiting blocks Supabase Realtime WebSocket connections | New Realtime subscriptions rejected with 429; existing connections work; real-time updates stop for new clients | API gateway rate limit counts WebSocket upgrade requests same as REST API calls; Realtime connection burst exceeds limit | `kubectl logs -l app=supabase-gateway -c istio-proxy \| grep '429\|rate_limit'`; `curl -H 'Upgrade: websocket' https://<project>/realtime/v1/websocket -v 2>&1 \| grep 429` | Exempt WebSocket upgrade path from rate limiting; create separate rate limit tier for `/realtime/v1/websocket`; use dedicated ingress for Realtime traffic |
| Stale service discovery after Supabase PostgREST pod reschedule | API requests routed to terminated PostgREST pod IP; 502 errors; some requests succeed (new pod), others fail (old IP) | Kubernetes endpoint updated but Kong/Envoy cached old IP; DNS TTL not expired; connection pool holds stale entry | `kubectl get endpoints supabase-rest -n supabase -o yaml`; `kubectl get pods -l app=supabase-rest -o wide` — compare IPs | Reduce DNS TTL; configure ingress controller upstream health checks with short interval; add PostgREST `terminationGracePeriodSeconds: 30` for graceful drain |
| mTLS rotation breaks Supabase internal service communication | PostgREST cannot connect to PostgreSQL; GoTrue cannot reach PostgreSQL; all APIs return 500 | Istio mTLS cert rotation coincides with PostgreSQL connection refresh; new cert not trusted by PostgreSQL `pg_hba.conf` | `kubectl logs -l app=supabase-rest \| grep -E 'SSL\|certificate\|connection refused'`; `istioctl proxy-status \| grep supabase` | Exclude PostgreSQL port 5432 from mesh: `traffic.sidecar.istio.io/excludeOutboundPorts: "5432"` on all Supabase service pods; or configure PostgreSQL to trust mesh-issued certs |
| Retry storm on Supabase Auth during password reset spike | GoTrue overwhelmed with retry requests; database connection pool exhausted; all auth operations fail | Envoy retries failed auth requests; each retry triggers database lookup + email send; cascading overload | `kubectl logs -l app=supabase-auth -c istio-proxy \| grep -c retry`; `psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity WHERE application_name='gotrue'"` | Disable retries for auth endpoints: `retries: {attempts: 0}` in VirtualService for `/auth/v1/` routes; implement rate limiting in GoTrue: `GOTRUE_RATE_LIMIT_HEADER=X-Real-IP` |
| gRPC keepalive mismatch between Kong and Supabase Realtime | Realtime WebSocket connections dropped every 60s; clients reconnect with subscription loss; real-time data gaps | Kong proxy timeout shorter than Supabase Realtime heartbeat interval; Kong kills idle WebSocket connections | `docker logs supabase-kong 2>&1 \| grep -E 'timeout\|websocket\|idle'`; check Realtime reconnection rate in client logs | Increase Kong WebSocket timeout: set `proxy_read_timeout 86400s` and `proxy_send_timeout 86400s` in Kong config; configure Realtime heartbeat: `REALTIME_HEARTBEAT_INTERVAL=30` (seconds) |
| Trace context propagation lost between Supabase Edge Function and PostgREST | Edge Function calls PostgREST internally but trace spans disconnected; cannot correlate function execution with database query | Edge Function Deno runtime does not automatically propagate trace headers in `fetch()` calls to PostgREST | `supabase functions logs <fn> --project-ref <ref> \| grep trace`; check Jaeger/Zipkin for disconnected spans between function and REST API | Manually propagate trace headers in Edge Function: `fetch(url, {headers: {...headers, 'traceparent': Deno.env.get('_X_AMZN_TRACE_ID')}})` or use OpenTelemetry Deno SDK |
| API gateway path rewrite breaks Supabase Auth callback URL | OAuth callback redirects to wrong URL; login loop; `redirect_uri_mismatch` error from OAuth provider | API gateway rewrites `/auth/v1/callback` path; OAuth provider callback URL doesn't match rewritten path; GoTrue rejects redirect | `curl -v https://<gateway>/auth/v1/callback?code=test 2>&1 \| grep -E 'redirect\|Location\|302'`; check GoTrue logs: `docker logs supabase-auth \| grep redirect` | Configure gateway to preserve `/auth/v1/` prefix; set `GOTRUE_SITE_URL` and `GOTRUE_URI_ALLOW_LIST` to match gateway URL; update OAuth provider redirect URI to gateway URL |
