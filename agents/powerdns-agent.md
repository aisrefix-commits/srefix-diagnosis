---
name: powerdns-agent
description: >
  PowerDNS specialist agent. Handles DNS server issues including
  authoritative backend failures, DNSSEC key management, recursor
  cache problems, and query performance optimization.
model: haiku
color: "#0067C5"
skills:
  - powerdns/powerdns
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-powerdns-agent
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

You are the PowerDNS Agent — the DNS infrastructure expert. When any alert
involves PowerDNS authoritative server, recursor, DNSSEC, backend database,
or DNS resolution, you are dispatched to diagnose and remediate.

# Activation Triggers

- Alert tags contain `powerdns`, `pdns`, `dns`, `dnssec`, `recursor`
- Metrics from PowerDNS statistics or Prometheus exporter
- Error messages contain PowerDNS terms (SERVFAIL, backend, DNSSEC, cache-miss)

# Prometheus Metrics Reference

PowerDNS exposes native Prometheus metrics at `GET /metrics` (HTTP API, default port 8081). Metric names use `pdns_auth_` prefix for authoritative and `pdns_recursor_` for recursor, with hyphens converted to underscores.

## Authoritative Server Metrics

Source: PowerDNS Authoritative statistics endpoint (`pdns_control show *` or `/metrics`).

| Metric | Type | Description | Alert Threshold |
|--------|------|-------------|-----------------|
| `pdns_auth_queries_total` | Counter | Total UDP+TCP queries received | — |
| `pdns_auth_udp_queries_total` | Counter | Queries over UDP | — |
| `pdns_auth_tcp_queries_total` | Counter | Queries over TCP | — |
| `pdns_auth_servfail_packets_total` | Counter | Packets answered with SERVFAIL (backend problems) | rate > 0.01 of total → WARNING; > 0.05 → CRITICAL |
| `pdns_auth_corrupt_packets_total` | Counter | Malformed packets received | rate > 0 → WARNING |
| `pdns_auth_overload_drops_total` | Counter | Queries dropped due to backend overload | rate > 0 → CRITICAL |
| `pdns_auth_latency` | Gauge | Average µs a packet spends in PowerDNS | > 5 000 µs (5 ms) → WARNING; > 20 000 µs (20 ms) → CRITICAL |
| `pdns_auth_receive_latency` | Gauge | Average µs to receive a packet | > 1 000 µs → WARNING |
| `pdns_auth_send_latency` | Gauge | Average µs to send a response | > 1 000 µs → WARNING |
| `pdns_auth_packetcache_hit_total` | Counter | Packet cache hits | — |
| `pdns_auth_packetcache_miss_total` | Counter | Packet cache misses | hit rate < 0.70 → WARNING |
| `pdns_auth_packetcache_size` | Gauge | Entries in packet cache | — |
| `pdns_auth_query_cache_hit_total` | Counter | Query cache hits | — |
| `pdns_auth_query_cache_miss_total` | Counter | Query cache misses | — |
| `pdns_auth_query_cache_size` | Gauge | Entries in query cache | — |
| `pdns_auth_qsize_q` | Gauge | Packets waiting for backend (queue depth) | > 10 → WARNING; > 50 → CRITICAL |
| `pdns_auth_uptime_seconds` | Gauge | Daemon uptime | sudden drop to 0 → CRITICAL (restart) |
| `pdns_auth_rd_queries_total` | Counter | Queries with RD bit set (recursion desired) | high rate on auth server → WARNING (misconfigured clients) |

## Recursor Metrics

Source: PowerDNS Recursor statistics (`rec_control get-all` or `/metrics`).

| Metric | Type | Description | Alert Threshold |
|--------|------|-------------|-----------------|
| `pdns_recursor_questions_total` | Counter | End-user queries with RD bit set | — |
| `pdns_recursor_servfail_answers_total` | Counter | SERVFAIL responses sent to clients | rate/questions > 0.01 → WARNING; > 0.05 → CRITICAL |
| `pdns_recursor_noerror_answers_total` | Counter | NOERROR responses sent | — |
| `pdns_recursor_nxdomain_answers_total` | Counter | NXDOMAIN responses sent | rate/questions > 0.30 → WARNING (possible flood/attack) |
| `pdns_recursor_cache_hits_total` | Counter | Record cache hits (not packet-cache) | — |
| `pdns_recursor_cache_misses_total` | Counter | Record cache misses | hit rate < 0.85 → WARNING; < 0.70 → CRITICAL |
| `pdns_recursor_cache_entries` | Gauge | Current record cache entries | — |
| `pdns_recursor_packetcache_hits_total` | Counter | Packet cache hits | — |
| `pdns_recursor_packetcache_misses_total` | Counter | Packet cache misses | — |
| `pdns_recursor_qa_latency` | Gauge | Exponentially weighted average latency in µs | > 100 000 µs (100 ms) → WARNING; > 500 000 µs (500 ms) → CRITICAL |
| `pdns_recursor_outgoing_timeouts_total` | Counter | Outgoing query timeouts | rate > 0.05 of questions → WARNING |
| `pdns_recursor_outgoing4_timeouts_total` | Counter | IPv4 outgoing query timeouts | — |
| `pdns_recursor_outgoing6_timeouts_total` | Counter | IPv6 outgoing query timeouts | — |
| `pdns_recursor_answers_slow_total` | Counter | Answers taking > 1 000 ms | rate > 0 → WARNING |
| `pdns_recursor_answers100_1000_total` | Counter | Answers in 100–1 000 ms bucket | — |
| `pdns_recursor_concurrent_queries` | Gauge | Current concurrent mthreads in use | > 80% of `max-mthreads` → WARNING |
| `pdns_recursor_over_capacity_drops_total` | Counter | Dropped queries (mthread pool exhausted) | rate > 0 → CRITICAL |

## PromQL Alert Expressions

```promql
# CRITICAL: authoritative SERVFAIL ratio > 5%
(
  rate(pdns_auth_servfail_packets_total[5m])
  /
  rate(pdns_auth_queries_total[5m])
) > 0.05

# WARNING: authoritative SERVFAIL ratio > 1%
(
  rate(pdns_auth_servfail_packets_total[5m])
  /
  rate(pdns_auth_queries_total[5m])
) > 0.01

# CRITICAL: authoritative latency p99-equivalent (avg gauge) > 20 ms
pdns_auth_latency > 20000

# WARNING: authoritative latency > 5 ms
pdns_auth_latency > 5000

# CRITICAL: authoritative backend queue depth > 50
pdns_auth_qsize_q > 50

# CRITICAL: queries dropped due to backend overload
rate(pdns_auth_overload_drops_total[5m]) > 0

# CRITICAL: recursor SERVFAIL ratio > 5%
(
  rate(pdns_recursor_servfail_answers_total[5m])
  /
  rate(pdns_recursor_questions_total[5m])
) > 0.05

# WARNING: recursor SERVFAIL ratio > 1%
(
  rate(pdns_recursor_servfail_answers_total[5m])
  /
  rate(pdns_recursor_questions_total[5m])
) > 0.01

# WARNING: recursor cache hit rate < 85%
(
  rate(pdns_recursor_cache_hits_total[5m])
  /
  (rate(pdns_recursor_cache_hits_total[5m]) + rate(pdns_recursor_cache_misses_total[5m]))
) < 0.85

# CRITICAL: recursor average latency > 500 ms
pdns_recursor_qa_latency > 500000

# WARNING: recursor average latency > 100 ms
pdns_recursor_qa_latency > 100000

# CRITICAL: recursor dropping queries (capacity exhausted)
rate(pdns_recursor_over_capacity_drops_total[5m]) > 0

# WARNING: outgoing timeout rate > 5% of questions
(
  rate(pdns_recursor_outgoing_timeouts_total[5m])
  /
  rate(pdns_recursor_questions_total[5m])
) > 0.05
```

# Service/Pipeline Visibility

Quick health overview — run these first:

```bash
# Authoritative server status
systemctl status pdns
pdns_control version
pdns_control uptime

# All authoritative statistics
pdns_control show \*

# Key auth metrics
pdns_control show queries
pdns_control show servfail-packets
pdns_control show latency
pdns_control show packetcache-hit
pdns_control show packetcache-miss
pdns_control show qsize-q

# Recursor status
systemctl status pdns-recursor
rec_control version
rec_control uptime

# All recursor statistics
rec_control get-all

# Key recursor metrics
rec_control get questions
rec_control get servfail-answers
rec_control get cache-hits
rec_control get cache-misses
rec_control get qa-latency

# Test resolution
dig @localhost example.com SOA +norecurse     # auth
dig @localhost google.com A                    # recursor
```

# Global Diagnosis Protocol

**Step 1 — Service health**
```bash
systemctl is-active pdns pdns-recursor
pdns_control ping       # Should respond "PONG"
rec_control ping        # Should respond "PONG"
```

**Step 2 — Identify role and check SERVFAIL rate**
```bash
# Authoritative
pdns_control show queries
pdns_control show servfail-packets

# Recursor
rec_control get questions
rec_control get servfail-answers
```

**Step 3 — Backend health (authoritative)**
```bash
# Test database backend connectivity
pdns_control list-zones | head -5
# If list fails: backend (MySQL/PostgreSQL) unreachable

# Check backend query queue
pdns_control show qsize-q

# Latency
pdns_control show latency
```

**Step 4 — Cache performance**
```bash
# Auth packet cache ratio
pdns_control show packetcache-hit
pdns_control show packetcache-miss
# ratio = hit / (hit + miss) — should be > 0.80

# Recursor record cache
rec_control get cache-hits
rec_control get cache-misses
rec_control get cache-entries
```

**Severity output:**
- CRITICAL: pdns/pdns-recursor process down; SERVFAIL ratio > 5%; backend unreachable (`qsize-q` growing, `servfail-packets` spiking); recursor latency > 500 ms
- WARNING: SERVFAIL ratio > 1%; recursor SERVFAIL ratio > 1%; cache hit rate < 70%; latency 5–20 ms; outgoing timeouts > 5%
- OK: processes running; SERVFAIL < 0.1%; cache hit > 85%; latency < 5 ms; no overload drops

# Focused Diagnostics

### Scenario 1 — Authoritative SERVFAIL Spike (Backend Database Issue)

**Symptoms:** `pdns_auth_servfail_packets_total` rate rising; `pdns_auth_qsize_q` growing; application DNS resolution failing; database CPU/connection errors.

**PromQL to confirm:**
```promql
rate(pdns_auth_servfail_packets_total[5m]) / rate(pdns_auth_queries_total[5m]) > 0.01
```

**Diagnosis:**
```bash
# Confirm SERVFAIL rate
pdns_control show servfail-packets
pdns_control show qsize-q

# Test resolution against this auth server
dig @localhost example.com A +norecurse | grep -E 'status:|ANSWER'

# Check backend database connectivity (MySQL/PostgreSQL)
# MySQL:
mysql -h dbhost -u pdns -p pdns -e "SELECT COUNT(*) FROM domains LIMIT 1;"
# PostgreSQL:
psql -h dbhost -U pdns -d pdns -c "SELECT COUNT(*) FROM domains LIMIT 1;"

# Inspect pdns logs for backend errors
journalctl -u pdns --since "10 minutes ago" | grep -iE 'error|backend|mysql|postgresql|timeout'

# Check backend overload drops
pdns_control show overload-drops
```
### Scenario 2 — Recursor SERVFAIL Spike

**Symptoms:** `pdns_recursor_servfail_answers_total` rate rising; clients getting SERVFAIL; upstream DNS unreachable; DNSSEC validation failures.

**PromQL to confirm:**
```promql
rate(pdns_recursor_servfail_answers_total[5m]) / rate(pdns_recursor_questions_total[5m]) > 0.01
```

**Diagnosis:**
```bash
# SERVFAIL rate
rec_control get servfail-answers
rec_control get questions

# Test upstream resolution
dig @8.8.8.8 google.com A +time=2 | grep status

# Check outgoing timeouts (upstream unresponsive)
rec_control get outgoing-timeouts

# Check for DNSSEC validation failures
rec_control get dnssec-result-bogus
rec_control get dnssec-result-servfail

# Inspect recursor logs
journalctl -u pdns-recursor --since "10 minutes ago" | grep -iE 'error|timeout|servfail|refused'

# Test specific failing domain
dig @localhost failing-domain.com A
```
### Scenario 3 — Recursor Cache Hit Rate Degradation

**Symptoms:** `pdns_recursor_cache_misses_total` rate rising relative to hits; increased outgoing queries; higher latency; upstream load increasing.

**PromQL to confirm:**
```promql
rate(pdns_recursor_cache_hits_total[5m]) /
(rate(pdns_recursor_cache_hits_total[5m]) + rate(pdns_recursor_cache_misses_total[5m])) < 0.85
```

**Diagnosis:**
```bash
# Cache stats
rec_control get cache-hits
rec_control get cache-misses
rec_control get cache-entries
rec_control get max-cache-entries

# Packet cache (if hit rate also low here = different issue)
rec_control get packetcache-hits
rec_control get packetcache-misses

# Check if cache was recently flushed (uptime short)
rec_control get uptime

# Queries causing recursion vs cached
rec_control get questions
rec_control get noerror-answers
```
### Scenario 4 — Recursor Latency Spike / Outgoing Timeouts

**Symptoms:** `pdns_recursor_qa_latency` > 100 000 µs; `pdns_recursor_outgoing_timeouts_total` rate > 0; clients experiencing slow DNS resolution; `answers-slow` bucket filling.

**PromQL to confirm:**
```promql
pdns_recursor_qa_latency > 100000
rate(pdns_recursor_outgoing_timeouts_total[5m]) / rate(pdns_recursor_questions_total[5m]) > 0.05
```

**Diagnosis:**
```bash
# Latency and timeout metrics
rec_control get qa-latency
rec_control get outgoing-timeouts
rec_control get outgoing4-timeouts
rec_control get outgoing6-timeouts

# Answer time distribution
rec_control get answers0-1
rec_control get answers1-10
rec_control get answers10-100
rec_control get answers100-1000
rec_control get answers-slow

# Concurrent query threads
rec_control get concurrent-queries

# Test resolution time
time dig @localhost google.com A
time dig @localhost slowly-resolving.example.com A

# Upstream latency check
time dig @8.8.8.8 google.com A
```
### Scenario 5 — Backend Database Connection Pool Exhaustion

**Symptoms:** `pdns_auth_qsize_q` > 50 and growing; `pdns_auth_overload_drops_total` rate > 0; `pdns_auth_latency` > 20 000 µs; database shows max connections reached; pdns logs showing backend timeout errors.

**PromQL to confirm:**
```promql
pdns_auth_qsize_q > 50
rate(pdns_auth_overload_drops_total[5m]) > 0
```

**Root Cause Decision Tree:**
- MySQL/PostgreSQL `max_connections` limit reached — too many concurrent PowerDNS threads holding connections
- Backend connection leak: PowerDNS not returning connections to pool after errors
- Database server overloaded (slow queries, lock contention) causing connections to hold longer
- `max-queue-length` in pdns.conf too low, causing premature drops before database recovers

**Diagnosis:**
```bash
# Check queue depth and overload drops
pdns_control show qsize-q
pdns_control show overload-drops
pdns_control show latency

# Database connection count (MySQL)
mysql -h dbhost -u pdns -p pdns \
  -e "SHOW STATUS LIKE 'Threads_connected'; SHOW VARIABLES LIKE 'max_connections';"

# Database connection count (PostgreSQL)
psql -h dbhost -U pdns -d pdns \
  -c "SELECT count(*), state FROM pg_stat_activity WHERE datname='pdns' GROUP BY state;"

# Check pdns.conf for connection pool settings
grep -E 'launch|gmysql|gpgsql|max-queue|default-ttl' /etc/powerdns/pdns.conf

# Inspect pdns logs for backend errors
journalctl -u pdns --since "10 minutes ago" | grep -iE 'backend|timeout|mysql|pgsql|connect|pool'

# Check slow query log on database
# MySQL: SHOW PROCESSLIST; -- look for queries in 'Locked' or 'Query' state > 5s
mysql -h dbhost -u root -e "SHOW FULL PROCESSLIST;" | awk '$6 > 5'
```

**Thresholds:**
- Warning: `pdns_auth_qsize_q` > 10, database connections > 80% of max
- Critical: `pdns_auth_overload_drops_total` rate > 0, `pdns_auth_qsize_q` > 50, database at max connections

### Scenario 6 — Zone Transfer Failing Due to TSIG Key Mismatch

**Symptoms:** Slave zones not updating; `pdns_control list-zones` shows stale serials on slave; logs show "TSIG signature mismatch" or "REFUSED" on zone transfer attempts; zone serial on slave lags behind master.

**Root Cause Decision Tree:**
- TSIG key on master and slave are different (name matches but secret differs)
- TSIG key name mismatch (master uses `key1` but slave references `key2`)
- TSIG key recently rotated on one side but not the other
- No TSIG configured on slave but master requires it (`only-notify` with TSIG)

**Diagnosis:**
```bash
# Check slave zone serial vs master
dig @<master-ip> example.com SOA +short
dig @localhost example.com SOA +short

# Test AXFR from slave without TSIG (should fail if required)
dig @<master-ip> example.com AXFR +time=5

# Test AXFR with TSIG key (from slave)
dig @<master-ip> example.com AXFR -y hmac-sha256:<key-name>:<base64-secret> +time=5

# Check pdns.conf for TSIG-related settings
grep -E 'slave|tsig|notify|also-notify|xfr-max' /etc/powerdns/pdns.conf
pdns_control list-zones | head -10

# Check TSIG keys in database
# MySQL:
mysql -h dbhost -u pdns -p pdns -e "SELECT name, algorithm FROM tsigkeys;"

# PowerDNS API key list
curl -s -H "X-API-Key: $PDNS_API_KEY" http://localhost:8081/api/v1/servers/localhost/tsigkeys | \
  python3 -m json.tool | grep -E '"name"|"algorithm"'

# Inspect pdns logs for TSIG errors
journalctl -u pdns --since "30 minutes ago" | grep -iE 'tsig|refused|transfer|axfr|ixfr'
```

**Thresholds:**
- Warning: Zone serial mismatch primary vs slave by more than 1 version
- Critical: All zone transfers failing; slave serving stale data > 1 hour

### Scenario 7 — API Authentication Failure After Token Rotation

**Symptoms:** External tools (Terraform, ExternalDNS, cert-manager) failing to manage zones; HTTP 401 or 403 responses from PowerDNS API; DNS record automation stopped; `external_dns_provider_errors_total` rising (if using ExternalDNS).

**Root Cause Decision Tree:**
- `api-key` in pdns.conf changed but external tool Secret/ConfigMap not updated
- API not enabled on the server (`api=yes` missing or `api-readonly=yes` blocking writes)
- Web server binding to wrong interface (`webserver-address`) — API not reachable from pod network
- API client using wrong port (default 8081 but may differ)

**Diagnosis:**
```bash
# Test API connectivity and authentication
curl -v -H "X-API-Key: <current-key>" http://localhost:8081/api/v1/servers/localhost 2>&1 | \
  grep -E 'HTTP|401|403|200|connected'

# Check API configuration in pdns.conf
grep -E 'api|webserver' /etc/powerdns/pdns.conf

# List all zones via API (tests read access)
curl -s -H "X-API-Key: $PDNS_API_KEY" http://localhost:8081/api/v1/servers/localhost/zones | \
  python3 -m json.tool | grep -c '"id"'

# Test write access (create then delete a test zone)
curl -s -X POST -H "X-API-Key: $PDNS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"api-test.example.","kind":"Native","nameservers":["ns1.example."]}' \
  http://localhost:8081/api/v1/servers/localhost/zones

# Verify webserver is listening on correct interface
ss -tlnp | grep 8081
grep 'webserver-address\|webserver-port' /etc/powerdns/pdns.conf

# Check pdns logs for API access errors
journalctl -u pdns --since "10 minutes ago" | grep -iE 'api|webserver|401|403|unauthorized'
```

**Thresholds:**
- Warning: API returns 401/403; automation tools failing to sync
- Critical: All DNS record automation stopped; `api=no` or webserver not running

### Scenario 8 — Slave Zone Not Updating from Master (NOTIFY Not Reaching Slave)

**Symptoms:** Slave zones have stale data; serial number on slave lags master; no NOTIFY received on slave (`journalctl` shows no notify logs); zone changes on master not propagating despite correct TSIG configuration.

**Root Cause Decision Tree:**
- Firewall blocking UDP/TCP 53 from master IP to slave (or reverse for NOTIFY)
- Master's `also-notify` list missing slave IP
- Slave not in master's `allow-axfr-ips` or `allow-dnsupdate-from`
- NOTIFY sending from master working but slave's `slave-cycle-interval` too long (polling not trigger-based)
- Master and slave using different zone names (trailing dot issue)

**Diagnosis:**
```bash
# Check master also-notify and allow-axfr settings
grep -E 'also-notify|allow-axfr|allow-notify' /etc/powerdns/pdns.conf

# Manually send NOTIFY from master
pdns_control notify example.com

# Check slave received NOTIFY (on slave)
journalctl -u pdns --since "5 minutes ago" | grep -iE 'notify|retrieve|transfer'

# Check network connectivity master → slave on port 53
# On master:
nc -u -z <slave-ip> 53 && echo "UDP 53 OPEN" || echo "UDP 53 BLOCKED"
nc -z <slave-ip> 53 && echo "TCP 53 OPEN" || echo "TCP 53 BLOCKED"

# Force zone retrieval on slave
pdns_control retrieve example.com
journalctl -u pdns -f &  # watch for transfer result

# Check slave-cycle-interval (polling interval as backup)
grep 'slave-cycle-interval' /etc/powerdns/pdns.conf
# Default 60s — if NOTIFY not working, zone updates come here

# Verify zone kind on slave
pdns_control list-zones | grep example.com
# Should show 'Slave' kind
```

**Thresholds:**
- Warning: Zone serial mismatch > 1; no NOTIFY received in > 5 minutes after master change
- Critical: Slave serving data > 1 hour old; clients receiving outdated records

### Scenario 9 — DNSSEC Signing Queue Backup

**Symptoms:** `pdns_auth_latency` rising; zone changes not immediately visible with DNSSEC; `pdnsutil check-zone` shows unsigned records; RRSIG records missing for recently added/modified records.

**Root Cause Decision Tree:**
- DNSSEC signing key unavailable (key material missing from database)
- Backend database slow — signing queue not draining fast enough
- `default-soa-edit` or `soa-edit-api` misconfigured causing serial not advancing for DNSSEC
- Zone has inline signing enabled but `presigned=false` incorrectly set
- Large zone with many records and insufficient signer threads

**Diagnosis:**
```bash
# Check DNSSEC status for zone
pdnsutil show-zone example.com

# Check for unsigned records
pdnsutil check-zone example.com
# Look for: "RRSIG missing" or "unsigned delegation"

# Verify DNSSEC keys are active
pdnsutil list-keys example.com

# Check signing queue depth (via API)
curl -s -H "X-API-Key: $PDNS_API_KEY" \
  http://localhost:8081/api/v1/servers/localhost/zones/example.com. | \
  python3 -m json.tool | grep -E 'dnssec|edited_serial|serial'

# Test DNSSEC from external validator
dig @8.8.8.8 example.com A +dnssec | grep -E 'RRSIG|ad|BOGUS'

# Check pdns logs for DNSSEC signing errors
journalctl -u pdns --since "30 minutes ago" | grep -iE 'dnssec|sign|rrsig|key|nsec'

# Force zone rectify (regenerates NSEC/RRSIG chain)
pdnsutil rectify-zone example.com
```

**Thresholds:**
- Warning: `pdnsutil check-zone` reports issues; signing latency > 60s
- Critical: RRSIG records missing; external validators returning BOGUS/SERVFAIL

### Scenario 10 — Authoritative Server Returning SERVFAIL for Delegated Zones

**Symptoms:** Queries for delegated subdomains returning SERVFAIL from PowerDNS authoritative server; `pdns_auth_servfail_packets_total` rate elevated specifically for delegated zones; parent zone NS records exist but child zone NS servers unreachable or returning errors.

**Root Cause Decision Tree:**
- NS records in parent zone pointing to authoritative servers that are down or unreachable
- Glue records (A/AAAA for NS hosts) missing or incorrect in parent zone
- Child zone NS servers changed but parent zone delegation records not updated
- `default-soa-edit` causing SOA serial confusion between parent and child zones

**Diagnosis:**
```bash
# Identify which zone is returning SERVFAIL
pdns_control show servfail-packets
dig @localhost delegated-sub.example.com A | grep -E 'status:|ANSWER|AUTHORITY'

# Check delegation records in parent zone
dig @localhost example.com NS +short
dig @localhost delegated-sub.example.com NS +short

# Test child zone NS servers directly
dig @<child-ns-ip> delegated-sub.example.com A +norecurse

# Check glue records
dig @localhost delegated-sub.example.com NS +additional
# 'ADDITIONAL' section should show A/AAAA for NS hosts

# Verify records in database
mysql -h dbhost -u pdns -p pdns \
  -e "SELECT name, type, content FROM records WHERE name LIKE '%delegated-sub%' ORDER BY type;"

# Test full delegation chain
dig +trace delegated-sub.example.com A | tail -20

# Check pdns logs for delegation-specific errors
journalctl -u pdns --since "10 minutes ago" | grep -iE 'servfail|delegation|ns\b|glue'
```

**Thresholds:**
- Warning: `pdns_auth_servfail_packets_total` > 0.01 of queries, specific subzone affected
- Critical: All queries for delegated zone returning SERVFAIL; glue records missing

### Scenario 11 — pdnsutil check-zone Finding Zone Data Inconsistency

**Symptoms:** `pdnsutil check-zone` reports errors; zone partially serving (some records work, others return SERVFAIL or NXDOMAIN); recently imported zone from another DNS server has issues; `pdns_auth_corrupt_packets_total` rate > 0.

**Root Cause Decision Tree:**
- Zone imported from BIND with BIND-specific syntax not supported by PowerDNS
- DNSSEC-enabled zone missing NSEC/NSEC3 records or zone not rectified after bulk import
- Duplicate records in database (same name, type, content) causing parser confusion
- SOA record missing or malformed (zone cannot be served without valid SOA)
- Records with trailing dot vs without causing resolution mismatch

**Diagnosis:**
```bash
# Run full zone check
pdnsutil check-zone example.com
pdnsutil check-all-zones 2>&1 | grep -v "^$"

# Check for specific issues
pdnsutil check-zone example.com 2>&1 | grep -iE 'error|warning|missing|duplicate|nsec'

# Check SOA record
dig @localhost example.com SOA +short
# Must return exactly one SOA record

# Check for duplicate records in database
mysql -h dbhost -u pdns -p pdns -e "
  SELECT name, type, content, COUNT(*) as cnt
  FROM records r
  JOIN domains d ON r.domain_id = d.id
  WHERE d.name = 'example.com'
  GROUP BY name, type, content
  HAVING cnt > 1;"

# List all records for inspection
pdnsutil list-zone example.com 2>/dev/null | head -50

# Check DNSSEC record presence
pdnsutil show-zone example.com | grep -E 'NSEC|DNSKEY|RRSIG|signed'

# Rectify zone (fixes NSEC chain, ordering, and DNSSEC records)
pdnsutil rectify-zone example.com
pdnsutil check-zone example.com   # Re-run check after rectify
```

**Thresholds:**
- Warning: `pdnsutil check-zone` reports any error or warning
- Critical: SOA missing; DNSSEC zone failing validation; `pdns_auth_corrupt_packets_total` > 0

### Scenario 12 — TSIG-Authenticated Zone Transfer Failing in Production Due to Kerberos/LDAP Integration and Audit Logging Overhead

**Symptoms:** Zone transfers (`AXFR`/`IXFR`) succeed in staging but fail with `Transfer of 'example.com' from <primary>: FAILED` on production secondaries; primary logs show `Sending NOTIFY to <secondary>` but secondary never acknowledges; `pdnsutil list-zone` on secondary shows stale serial; DNSSEC signatures on secondary are expiring because zone has not updated; production primary has audit logging and LDAP-backed authentication enabled; staging uses plain MySQL backend without audit hooks.

**Root Cause Decision Tree:**
- Audit logging plugin adding >500ms latency per AXFR record lookup → TSIG HMAC timestamp validation window (default 300s) not exceeded, but per-record latency causes TCP connection timeout on secondary
- LDAP-backed `bind-dn` account password rotated in production → TSIG key lookup via LDAP returns `LDAP bind failed`; secondary cannot retrieve shared secret to verify transfer MAC
- Production firewall/NetworkPolicy allows TCP 53 for normal queries but blocks large TCP payloads (> 64KB) used in AXFR responses → only small zones transfer; large zones with many records fail mid-transfer
- Audit log plugin writing synchronously to slow NFS mount → each record write blocks the transfer thread; secondary TCP timeout fires
- Secondary IP not in production primary's `allow-axfr-ips` (updated for staging CIDR, not production)

**Diagnosis:**
```bash
# Test AXFR directly from secondary to primary (run on secondary)
dig @<primary-ip> example.com AXFR +time=30 2>&1 | tail -5
# Look for: "Transfer failed", "connection timed out", "REFUSED", "NOTAUTH"

# Check primary pdns log for AXFR attempts and TSIG errors
journalctl -u pdns -n 100 --no-pager | grep -iE "axfr|tsig|transfer|ldap|auth" | tail -30

# Verify TSIG key is present and matches on both sides
pdnsutil list-tsig-keys
# On secondary: same command — key names and algorithms must match exactly

# Check allow-axfr-ips setting includes secondary production IP
pdnsutil show-zone example.com | grep -i "axfr\|also-notify\|slave"
grep -iE "allow-axfr-ips|also-notify|slave-ip" /etc/powerdns/pdns.conf

# Measure LDAP bind latency (if LDAP integration is active)
ldapsearch -H ldap://<ldap-host> -D "cn=pdns,dc=example,dc=com" -w "$LDAP_PASS" \
  -b "dc=example,dc=com" "(objectClass=*)" 1.1 2>&1 | grep "# numEntries\|result:"

# Check if audit log mount is causing latency
df -h /var/log/pdns-audit 2>/dev/null
iostat -x 1 3 2>/dev/null | grep -E "Device|$(stat -c %m /var/log/pdns-audit 2>/dev/null | tr '/' '_' | sed 's/^_//')"

# Force manual AXFR from primary perspective
pdns_control retrieve example.com   # on secondary — triggers immediate transfer attempt
pdns_control notify example.com     # on primary — sends NOTIFY to all secondaries

# Check TCP 53 connectivity and payload size limits
nc -vz <primary-ip> 53
# Transfer a known-small zone to test TCP path:
dig @<primary-ip> small-test.example.com AXFR +tcp
```

**Thresholds:**
- Warning: Zone serial on secondary lagging primary by > 1 hour; TSIG verification warnings in log
- Critical: Secondary serving stale zone > TTL; DNSSEC signatures expiring; `pdns_control retrieve` failing; TSIG `NOTAUTH` errors

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `Backend reported permanent error which prevented lookup` | Backend database (MySQL/PostgreSQL) unreachable or query failed | `mysql -h <pdns-db-host> -u pdns -p -e "SELECT 1;"` |
| `Unable to launch, no backends configured` | `launch=` line missing or commented out in pdns.conf | `grep launch /etc/pdns/pdns.conf` |
| `Error in zone xxx: CNAME and other data` | CNAME record coexists with an A or other record at the same name | `pdnsutil list-zone <zone> \| grep <name>` |
| `Duplicate key in database` | Duplicate zone or record inserted into backend DB | `SELECT * FROM records WHERE name='<name>' AND type='<type>';` in pdns DB |
| `gsql: Fatal error: Error connecting to the database` | DB connection failure; wrong host, port, credentials in pdns.conf | `grep gmysql /etc/pdns/pdns.conf` |
| `Authoritative server: Recursion not enabled` | Recursive query sent to an authoritative-only server | `grep recursor /etc/pdns/pdns.conf` |
| `Could not find a slave for zone xxx` | AXFR slave not configured; `also-notify` or `slave=yes` missing | `grep -E 'slave|also-notify|allow-axfr' /etc/pdns/pdns.conf` |
| `Transfer to xxx failed: DNS query timeout` | Zone transfer blocked by firewall on port 53/TCP | `nc -zv <slave-ip> 53` |
| `pdns_control: Unable to connect to remote 'pdns_control'` | pdns service is not running or control socket path mismatch | `systemctl status pdns` |
| `No DNSSEC data for xxx` | Zone not secured or DNSKEY not published after `pdnsutil secure-zone` | `pdnsutil show-zone <zone>` |

# Capabilities

1. **Authoritative server** — Zone management, backend health, packet cache
2. **Recursor** — Cache tuning, DNSSEC validation, upstream resolution
3. **DNSSEC** — Key management, rollover, DS record coordination
4. **Backend database** — MySQL/PostgreSQL connectivity, query optimization
5. **API management** — REST API zone CRUD, security, rate limiting
6. **Lua scripting** — Custom query handling, filtering, response modification

# Critical Metrics to Check First

1. `pdns_auth_servfail_packets_total` rate / `pdns_auth_queries_total` rate > 1% → backend issue
2. `pdns_recursor_servfail_answers_total` rate / `pdns_recursor_questions_total` rate > 1% → upstream issue
3. `pdns_auth_latency` > 5 000 µs → slow backend database
4. `pdns_recursor_qa_latency` > 100 000 µs → upstream DNS slow
5. `pdns_recursor_cache_hits_total` / (hits + misses) < 0.85 → cache underperforming
6. `pdns_auth_qsize_q` > 10 → backend query backlog

# Output

Standard diagnosis/mitigation format. Always include: server role (auth/recursor),
affected zones, cache stats, backend status, and recommended remediation steps.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| PowerDNS authoritative returning SERVFAIL for all zones | Backend database (MySQL/PostgreSQL) replication lag on read replica; PowerDNS configured to read from replica which is severely behind primary | `mysql -h <pdns-replica-host> -u pdns -p -e "SHOW SLAVE STATUS\G" \| grep -E "Seconds_Behind_Master\|Slave_IO_Running"` |
| Recursor SERVFAIL rate elevated for external domains only | Upstream ISP or corporate DNS resolver blocking UDP/TCP 53 outbound from the recursor host; root hints unreachable | `dig @8.8.8.8 google.com A +time=2 \| grep status` and `nc -u -z 198.41.0.4 53 && echo "UDP 53 to root OK"` |
| ExternalDNS or cert-manager failing to create DNS records; PowerDNS API returning 401 | Kubernetes secret holding the PowerDNS API key was rotated but the ExternalDNS deployment not restarted to pick up the new value | `kubectl get secret pdns-api-key -o jsonpath='{.data.key}' \| base64 -d` vs `grep api-key /etc/powerdns/pdns.conf` |
| Zone transfer failing with TSIG mismatch only in production | Production secondary's TSIG key was auto-rotated by a secrets manager (Vault/AWS Secrets Manager) but the PowerDNS database was not updated with the new secret | `pdnsutil list-tsig-keys` on primary vs secondary, compare algorithm and secret hash |
| DNSSEC validation failing (`BOGUS`) for internal zones after database migration | During DB migration, DNSSEC key material rows were not copied from old backend to new; `pdnsutil list-keys` shows no keys despite zone being marked as DNSSEC-enabled | `pdnsutil show-zone <zone> \| grep -E 'DNSKEY\|KSK\|ZSK\|signed'` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N backend database replicas has high replication lag; PowerDNS using round-robin across replicas; ~1/N queries hitting stale data | `pdns_auth_latency` bimodal; some queries fast (fresh replica) and some slow or returning stale records; replication lag visible only on the lagging replica | ~1/N DNS responses return outdated records for recently changed zones; intermittent, hard to reproduce consistently | `mysql -h <each-replica> -u pdns -p -e "SHOW SLAVE STATUS\G" \| grep Seconds_Behind_Master` for each replica in the pool |
| 1 of N recursor instances has a stale negative cache entry for a domain that was recently corrected; other recursors serving correct answer | `dig @<specific-recursor> fixed-domain.com A` returns NXDOMAIN; `dig @<other-recursor> fixed-domain.com A` returns correct A record; Prometheus shows `pdns_recursor_cache_hits_total` healthy on both | ~1/N users (load-balanced to that recursor) get NXDOMAIN for a domain that should resolve; appears as intermittent DNS failure | `rec_control --socket-dir=/var/run/pdns-recursor get-query-ring queries \| grep fixed-domain` and `rec_control flush-cache fixed-domain.com` on the specific instance |
| 1 of N authoritative servers not receiving zone transfer NOTIFYs from primary; serving stale zone serial | `dig @<secondary-1> example.com SOA +short` vs `dig @<secondary-2> example.com SOA +short` show different serials; primary's `also-notify` list missing one secondary IP | Clients load-balanced to stale secondary get outdated records; inconsistent DNS responses depending on which server is hit | `pdns_control notify example.com` on primary then `journalctl -u pdns -n 20` on each secondary to confirm NOTIFY received |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Authoritative query latency (µs) | > 1,000 | > 5,000 | `curl -s http://localhost:8081/api/v1/servers/localhost/statistics \| jq '.[] \| select(.name=="latency")'` |
| Recursor cache hit ratio (%) | < 85 | < 70 | `curl -s http://localhost:8082/api/v1/servers/localhost/statistics \| jq '.[] \| select(.name=="cache-hits","cache-misses")'` |
| Authoritative queries per second | > 50,000 | > 100,000 | `pdns_control show queries` or `curl -s http://localhost:8081/api/v1/servers/localhost/statistics \| jq '.[] \| select(.name=="udp-queries")'` |
| Recursor outgoing TCP connections | > 100 | > 500 | `curl -s http://localhost:8082/api/v1/servers/localhost/statistics \| jq '.[] \| select(.name=="tcp-outqueries")'` |
| Backend database query latency (ms) | > 10 | > 50 | `pdns_control show backend-queries` and cross-reference with MySQL `SHOW STATUS LIKE 'Slow_queries'` |
| Zone transfer (AXFR/IXFR) failures total | > 5 | > 20 | `curl -s http://localhost:8081/api/v1/servers/localhost/statistics \| jq '.[] \| select(.name=="servfail-answers")'` |
| Packet cache hit ratio (%) | < 80 | < 60 | `curl -s http://localhost:8081/api/v1/servers/localhost/statistics \| jq '.[] \| select(.name=="packetcache-hit","packetcache-miss")'` |
| Recursor throttled outgoing queries | > 50 | > 500 | `curl -s http://localhost:8082/api/v1/servers/localhost/statistics \| jq '.[] \| select(.name=="throttled-out")'` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| `pdns_auth_backend_queries_total` rate | >500 queries/s sustained and rising week-over-week | Add read replicas to the backend database (MySQL/PostgreSQL); consider in-memory query cache | 2–3 weeks |
| `pdns_auth_cache_entries` / `cache-max-entries` ratio | >80% cache fill | Increase `cache-max-entries` in `pdns.conf` or add RAM; scale horizontally with a second authoritative instance | 1 week |
| Backend DB disk usage | >70% and growing at >5%/week | Expand volume or archive old/unused zones; move to a dedicated DB host | 3 weeks |
| `pdns_recursor_cache_entries` / `max-cache-entries` ratio | >75% fill on recursor | Increase `max-cache-entries`; if memory-constrained, add a second recursor behind a load balancer | 1–2 weeks |
| `pdns_auth_qsize_q` queue depth | Average >50 or spikes >200 | Backend is too slow; add index on zone lookup columns; consider backend connection pooling (PgBouncer) | Days |
| DNSSEC signature expiry window | Any zone with RRSIG valid-until < 14 days away | Run `pdnsutil rectify-all-zones` and verify auto-sign timer; renew expiring keys | 2 weeks |
| `pdns_auth_latency` (µs) p99 | Rising trend past 2 ms | Profile backend queries; check CPU saturation; consider caching backend or Lua scripting for hot paths | 1 week |
| DNS query rate vs. thread count (`distributor-threads`) | CPU per thread >70% at peak | Increase `distributor-threads` and `receiver-threads` in `pdns.conf`; scale to additional auth instances | 1 week |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check authoritative server health and version
pdns_control show '*' | grep -E 'uptime|version|qsize|latency'

# Live query rate and latency on auth server
watch -n2 'pdns_control show "udp-queries" && pdns_control show "latency"'

# List all zones and their serial numbers
pdnsutil list-all-zones | head -20 && pdnsutil zone-statistics $(pdnsutil list-all-zones | head -1)

# Check recursor cache hit ratio
rec_control get cache-hits cache-misses | awk '{print "Hit ratio:", $1/($1+$2)*100"%"}'

# Verify DNSSEC for a zone (check RRSIG validity window)
pdnsutil check-all-zones 2>&1 | grep -E 'WARN|ERROR|expired'

# Show current query distribution by qtype
pdns_control show 'servfail-answers' && rec_control get all | grep -E 'servfails|timeouts'

# Dump recursor cache for a specific domain
rec_control dump-cache /dev/stdout | grep -i "example.com" | head -20

# Check TSIG keys and associated zones
pdnsutil list-tsig-keys

# Detect open resolvers (should return error or refuse)
dig +short @127.0.0.1 google.com A

# Inspect recent API calls for unauthorized zone modifications
journalctl -u pdns --since "30 minutes ago" | grep -E 'PUT|POST|DELETE|api' | tail -20
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| DNS Query Success Rate | 99.9% | `1 - (rate(pdns_auth_servfail_answers_total[5m]) / rate(pdns_auth_questions_total[5m]))` | 43.8 min | >14.4x (burn rate alert if error ratio >1.44%) |
| Recursor Cache Hit Rate | 99.5% | `rate(pdns_recursor_cache_hits[5m]) / (rate(pdns_recursor_cache_hits[5m]) + rate(pdns_recursor_cache_misses[5m]))` | 3.6 hr | >7.2x |
| Auth Query Latency p99 ≤ 5 ms | 99% | `histogram_quantile(0.99, rate(pdns_auth_latency_bucket[5m])) < 5000` (µs) | 7.3 hr | >2.4x |
| DNSSEC Validation Success | 99.95% | `1 - (rate(pdns_recursor_dnssec_result_bogus[5m]) / rate(pdns_recursor_dnssec_queries_total[5m]))` | 21.9 min | >28.8x |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Local address binding | `pdns_control show 'local-address'` | Bound to expected IPs only, not 0.0.0.0 unless intentional |
| Recursor allow-from | `grep allow-from /etc/powerdns/recursor.conf` | Restricted to internal subnets, not `0.0.0.0/0` |
| DNSSEC enabled | `pdnsutil show-zone <zone> \| grep 'Secured'` | All production zones show `Secured: Yes` |
| API key set and non-default | `grep api-key /etc/powerdns/pdns.conf` | Key present, not empty or default value |
| AXFR transfer ACL | `grep allow-axfr /etc/powerdns/pdns.conf` | Restricted to known secondary IPs only |
| Max recursion depth | `grep max-recursion-depth /etc/powerdns/recursor.conf` | Set to 40 or lower (default 40) |
| Cache TTL limits | `grep max-cache-ttl /etc/powerdns/recursor.conf` | max-cache-ttl ≤ 86400 to prevent stale poisoning |
| NSEC3 iteration count | `pdnsutil show-zone <zone> \| grep nsec3param` | Iteration count ≤ 100 (per RFC 9276 guidance) |
| Logging verbosity | `grep loglevel /etc/powerdns/pdns.conf` | loglevel=4 (default) in prod; not 7 (debug) |
| Version hiding | `pdns_control version` and `dig +short @127.0.0.1 version.bind TXT CH` | version.bind query returns REFUSED or empty |

---

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `Backend reported unhandled exception` | ERROR | Backend (MySQL/PostgreSQL/LMDB) threw an unexpected error during query | Check DB connectivity; inspect backend logs for constraint or connection errors |
| `Unable to launch, binding to port 53 failed` | FATAL | Port 53 already in use (e.g., systemd-resolved conflict) | `ss -tulpn | grep :53`; stop conflicting service; restart pdns |
| `Zone ... has no NS records` | WARNING | Zone is misconfigured — missing authoritative NS records | `pdnsutil check-zone <zone>`; add missing NS records |
| `Backend unable to find zone for ... while adding SOA` | ERROR | Zone exists in DB but SOA record is absent | Re-add SOA via `pdnsutil rectify-zone <zone>` or SQL insert |
| `Timeout on waiting for SOA serial from ... skipping zone` | WARNING | AXFR/IXFR zone transfer timed out from primary | Check primary reachability; verify `allow-axfr-ips` on primary |
| `Failed to re-sign zone ... DNSSEC key unavailable` | ERROR | Signing key deleted or inaccessible in key backend | `pdnsutil check-zone <zone>`; reimport or regenerate key |
| `Packet from ... not signed, disallowed by allow-unsigned-notify` | WARNING | NOTIFY received without TSIG signature from unrecognised peer | Validate notify source; add TSIG key or restrict `allow-notify-from` |
| `Seriously: the zone ... has no SOA` | CRITICAL | SOA completely absent; zone is broken | `pdnsutil rectify-zone <zone>`; add SOA immediately |
| `Cache hit ratio below threshold` | INFO | Query cache not warming; high unique query volume | Increase `cache-ttl`; check for cache poisoning attempts |
| `Master ... unreachable for zone transfer` | WARNING | Primary DNS unreachable; secondary serving stale zone | Verify network to primary; check firewall on TCP/UDP 53 |
| `NSEC3 param too heavy for ... iterations` | WARNING | NSEC3 iteration count exceeds RFC 9276 recommendation | `pdnsutil set-nsec3 <zone> '1 0 0 -'` to reduce iterations |
| `Lua script compilation failed` | ERROR | Syntax error in Lua policy/hook script | Review Lua file for syntax errors; reload with `pdns_control reload-lua-script` |

---

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `SERVFAIL` (RCODE 2) | Server failed to complete query | Clients receive resolution failure; apps may break | Check backend connectivity; inspect pdns logs for root cause |
| `REFUSED` (RCODE 5) | Query refused by policy | Clients blocked from zone data | Verify `allow-recursion` and `allow-axfr-ips` ACLs |
| `NXDOMAIN` (RCODE 3) | Name does not exist | Clients get negative response; may cause app errors if misconfigured | Confirm zone contents; check `also-search-domains` and backend records |
| `NOTAUTH` (RCODE 9) | Server is not authoritative for zone | Clients redirected; may cause resolution loops | Add zone to backend; verify SOA and NS records |
| `NOTIMPL` (RCODE 4) | Query type not implemented | Clients get unsupported-type response | Check if query type is valid; verify pdns build includes necessary modules |
| `BADSIG` (DNSSEC) | TSIG or SIG record failed verification | Zone transfers or dynamic updates rejected | Re-sync TSIG keys; check system clock skew (`chronyc tracking`) |
| `Slave zone out of date` | Serial on secondary lags primary | Stale DNS data served to clients | Force `pdns_control retrieve <zone>`; check NOTIFY delivery |
| `Backend connection pool exhausted` | All DB connections in use | Queries queue/fail until connection freed | Increase `max-backend-connections`; investigate slow DB queries |
| `Lua hook returned DENY` | Policy script explicitly denied query | Client receives REFUSED | Review Lua policy for unintended deny rules |
| `DNSSEC validation failed` | Signature chain broken or expired | DNSSEC-aware resolvers reject zone data | `pdnsutil check-zone <zone>`; re-sign zone; check key expiry |
| `AXFR refused` | Transfer request denied | Secondary cannot replicate zone | Add secondary IP to `allow-axfr-ips` on primary |
| `packetcache-hit=0` | Packet cache not functioning | Every query hits backend; high DB load | Verify `cache-ttl` > 0; check `disable-packetcache` not set |

---

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Backend DB Overload | `pdns_backend_queries` spike; `pdns_packetcache_hit` drops to ~0 | `Backend unable to find zone`; `Timeout on backend query` | Query latency alert | DB connection saturation or slow queries | Increase connection pool; add DB indexes on `name` column; enable packet cache |
| Zone Transfer Breakdown | `pdns_incoming_notifications_ignored` rising; `pdns_slave_updates_run` stalled | `Master ... unreachable for zone transfer`; `Timeout on waiting for SOA serial` | Stale zone alert | Network partition to primary or AXFR ACL misconfiguration | Check firewall on TCP 53; verify `allow-axfr-ips`; manual `pdns_control retrieve` |
| DNSSEC Signing Failure | `pdns_dnssec_sign_requests` dropping to 0 | `Failed to re-sign zone ... DNSSEC key unavailable` | DNSSEC validation failures in upstream resolvers | Key deleted from key backend or key expiry | `pdnsutil show-zone`; regenerate expired/missing keys; rectify zone |
| Port 53 Binding Conflict | Process fails to start; no metrics exposed | `Unable to launch, binding to port 53 failed` | Health check DOWN alert | `systemd-resolved` or another DNS service holding port 53 | `ss -tulpn | grep :53`; disable conflicting service; restart pdns |
| Lua Script Crash Loop | `pdns_exceptions` counter rising per query | `Lua script compilation failed`; `Lua hook threw exception` | High error rate alert | Syntax/runtime error introduced in Lua policy script | Roll back Lua script to last known good; `pdns_control reload-lua-script` |
| Cache Exhaustion | `pdns_packetcache_size` at max; hit ratio declining | `Cache nearly full, evicting entries` | Memory usage alert | Unusually high unique query volume or too-small cache | Increase `max-packet-cache-entries`; shorten TTL for high-volume zones |
| Notify Storm | `pdns_incoming_notifications` very high | `Packet from ... not signed, disallowed` repeatedly | Unusual traffic alert | Misconfigured or malicious source sending unsolicited NOTIFYs | Restrict `allow-notify-from`; rate-limit on firewall |
| SOA-less Zone Breakage | `pdns_servfail_packets` spike for specific zone | `Seriously: the zone ... has no SOA`; `Backend reported unhandled exception` | Zone health check alert | SOA row deleted from DB or replication lag on DB replica | `pdnsutil rectify-zone <zone>`; re-add SOA; check DB replication status |
| API Key Exposure / Unauthorized Access | Unexpected record changes; unknown zones appearing | `Received unauthorized API request` | Audit alert on record modification | API key leaked or `api-readonly=no` on exposed interface | Rotate `api-key`; set `webserver-allow-from` to internal IPs only |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `NXDOMAIN` for valid name | Any DNS resolver | Zone not loaded in backend; DB replication lag | `pdnsutil check-zone <zone>`; query backend DB directly | Force zone reload: `pdns_control reload <zone>` |
| `SERVFAIL` response | Any DNS resolver | Backend database unreachable; Lua policy exception | Check `pdns_backend_query_errors` metric; inspect error log | Fix DB connectivity; roll back Lua script |
| DNS query timeout (no response) | Any resolver / application using `getaddrinfo` | pdns process OOM-killed; port 53 binding lost | `systemctl status pdns`; `ss -tulpn | grep :53` | Restart pdns; fix binding conflict |
| Stale/wrong IP returned | Application cached result | Zone change not applied; packet cache serving old answer | `pdns_control purge <name>`; compare DB record vs live query | Flush packet cache; shorten TTL before cutover |
| `REFUSED` response | dig / nslookup | `allow-recursion` or `allow-query` ACL blocking client CIDR | `dig @<pdns-ip> <name>` from client; check ACL logs | Add client IP to `allow-query`; reload config |
| DNSSEC validation failure | Validating resolver (DNSSEC-aware) | Broken DNSSEC chain; expired or missing KSK/ZSK | `dig +dnssec <name>`; `dnsviz` graph | `pdnsutil rectify-zone`; re-sign zone; update DS at registrar |
| Truncated UDP response forced to TCP | Stub resolver | Large answer set exceeding 512-byte UDP limit | `dig +bufsize=512 <name>`; count answer section | Enable EDNS0 large UDP; set `edns-udp-size=4096` |
| HTTP 502/503 from app behind DNS-based LB | HTTP client library | DNS lookup returns nothing due to zone transfer failure | Check `pdns_slave_updates_run` metric on authoritative server | Manually trigger `pdns_control retrieve <zone>` |
| Intermittent resolution failure (flapping) | Any DNS resolver | Primary-secondary drift; inconsistent packet cache entries | Compare SOA serial across replicas: `pdns_control list-zones` | Increase SOA serial discipline; unify TTLs |
| AXFR refused from secondary | `dig axfr` / zone transfer tool | `allow-axfr-ips` does not include secondary IP | `pdns_control show allow-axfr-ips` | Add secondary CIDR to `allow-axfr-ips`; reload |
| Empty answer (`NOERROR` with no records) | Application DNS library | Empty non-terminal in DB; typo in zone record | Query DB for exact owner name; `pdnsutil check-zone` | Insert missing records; rectify zone |
| Certificate SNI mismatch (DNS-01 ACME) | ACME client (certbot, acme.sh) | `_acme-challenge` TXT record not propagated to all servers | `dig TXT _acme-challenge.<domain>` against each server | Ensure API writes land on all backends; wait for propagation |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Backend DB connection pool exhaustion | `pdns_backend_queries` latency p99 rising; hit ratio declining | `pdns_control show backend-query-latency` | 30–60 min | Increase `pdns-backend-session-timeout`; add DB read replicas |
| Packet cache memory growth | `pdns_packetcache_size` trending toward `max-packet-cache-entries` | `pdns_control show packetcache-size` | Hours | Increase `max-packet-cache-entries`; reduce answer TTL for large zones |
| Lua policy script memory leak | pdns process RSS growing 1–5 MB/hour; no restart | `ps aux | grep pdns` RSS column over time | 4–12 h | Patch Lua script; schedule periodic `pdns_control reload-lua-script` |
| SOA serial drift between primary and secondaries | `pdns_incoming_notifications_ignored` rising slowly | `for s in $secondaries; do dig @$s SOA $zone +short; done` | 1–4 h | Force `pdns_control notify <zone>`; investigate replication network |
| DNSSEC key expiry approaching | Days-to-expiry metric not emitted; resolver validation failure | `pdnsutil show-zone <zone> | grep -i expire` | Days to weeks | Automate key rollover; set calendar alert 30 days before expiry |
| Log file fill (unbounded logging) | Disk usage on log volume growing steadily | `du -sh /var/log/pdns/` | Hours | Rotate logs; set `logging-facility` to syslog; cap log verbosity |
| Zone count growth overwhelming backend | `pdns_backend_queries` rising proportionally to zone count | `SELECT COUNT(*) FROM domains` on backend DB | Weeks | Partition zones across backends; add DB index on `name`; cache zone list |
| TCP handler thread saturation | `pdns_tcp-queries` high relative to thread count; latency rising | `pdns_control show tcp-connections` | 20–40 min | Increase `distributor-threads`; increase `receiver-threads`; add pdns instances |
| Notify storm building | `pdns_incoming_notifications` increasing hourly | `pdns_control show incoming-notifications-per-second` | 1–2 h | Restrict `allow-notify-from`; add firewall rate-limit on UDP 53 NOTIFY |
| Memory growth from large DNSSEC zone signing | Heap increasing after zone re-sign jobs | `pdns_control show memory-usage` before/after sign | Per-signing-cycle | Schedule signing during low-traffic windows; batch large zones |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# pdns-health-snapshot.sh — Point-in-time health overview for PowerDNS Authoritative
set -euo pipefail
PDNS_API_URL="${PDNS_API_URL:-http://localhost:8081}"
PDNS_API_KEY="${PDNS_API_KEY:-changeme}"

echo "=== PowerDNS Health Snapshot $(date -u) ==="

echo -e "\n--- Process Status ---"
systemctl is-active pdns 2>/dev/null || echo "systemd not available"
pgrep -a pdns || echo "no pdns process found"

echo -e "\n--- Version & Uptime ---"
pdns_control version 2>/dev/null || true
pdns_control uptime 2>/dev/null || true

echo -e "\n--- Key Metrics ---"
pdns_control show '*' 2>/dev/null | grep -E 'latency|cache|query|error|fail|packet|notify' | sort || true

echo -e "\n--- Zone Count ---"
curl -sf -H "X-API-Key: $PDNS_API_KEY" "$PDNS_API_URL/api/v1/servers/localhost/zones" | python3 -c "import sys,json; z=json.load(sys.stdin); print(f'Total zones: {len(z)}')" 2>/dev/null || echo "API unavailable"

echo -e "\n--- Recent Errors (last 50 lines) ---"
journalctl -u pdns -n 50 --no-pager 2>/dev/null | grep -iE 'error|fail|warn|unable' || tail -50 /var/log/pdns/pdns.log 2>/dev/null | grep -iE 'error|fail|warn' || echo "No log source found"

echo -e "\n--- Port 53 Binding ---"
ss -tulpn | grep ':53' || echo "Nothing on port 53"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# pdns-perf-triage.sh — Identify latency and cache performance issues
PDNS_API_KEY="${PDNS_API_KEY:-changeme}"
PDNS_API_URL="${PDNS_API_URL:-http://localhost:8081}"

echo "=== PowerDNS Performance Triage $(date -u) ==="

echo -e "\n--- Query Rate (qps) ---"
pdns_control show udp-queries 2>/dev/null || true
pdns_control show tcp-queries 2>/dev/null || true

echo -e "\n--- Packet Cache Hit Ratio ---"
HITS=$(pdns_control show packetcache-hits 2>/dev/null | awk '{print $2}' || echo 0)
MISS=$(pdns_control show packetcache-miss 2>/dev/null | awk '{print $2}' || echo 1)
echo "PacketCache hits=$HITS misses=$MISS ratio=$(echo "scale=2; $HITS * 100 / ($HITS + $MISS + 1)" | bc)%"

echo -e "\n--- Backend Query Latency ---"
pdns_control show backend-query-latency 2>/dev/null || true

echo -e "\n--- SERVFAILs and Errors ---"
pdns_control show servfail-packets 2>/dev/null || true
pdns_control show query-logging 2>/dev/null || true

echo -e "\n--- Top 10 Zones by Query Count (from API stats) ---"
curl -sf -H "X-API-Key: $PDNS_API_KEY" "$PDNS_API_URL/api/v1/servers/localhost/statistics" 2>/dev/null \
  | python3 -c "import sys,json; s=json.load(sys.stdin); [print(x['name'],x.get('value','')) for x in s if 'query' in x['name'].lower()][:10]" 2>/dev/null || true

echo -e "\n--- DNSSEC Signing Stats ---"
pdns_control show dnssec-sign-requests 2>/dev/null || true
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# pdns-resource-audit.sh — File descriptors, DB connections, zone replication state
echo "=== PowerDNS Resource Audit $(date -u) ==="

PDNS_PID=$(pgrep -f 'pdns_server' | head -1)
if [ -n "$PDNS_PID" ]; then
  echo -e "\n--- Open File Descriptors (PID $PDNS_PID) ---"
  ls /proc/$PDNS_PID/fd 2>/dev/null | wc -l | xargs -I{} echo "FD count: {}"
  echo "FD limit: $(cat /proc/$PDNS_PID/limits | grep 'open files' | awk '{print $4}')"

  echo -e "\n--- RSS Memory Usage ---"
  cat /proc/$PDNS_PID/status | grep -E 'VmRSS|VmPeak|VmSize'
fi

echo -e "\n--- Zone Transfer Status (last 10 zones) ---"
pdns_control list-zones 2>/dev/null | head -10 || true

echo -e "\n--- Backend DB Connections (MySQL example) ---"
mysql -e "SHOW STATUS LIKE 'Threads_connected';" 2>/dev/null || \
  psql -c "SELECT count(*) AS pdns_connections FROM pg_stat_activity WHERE application_name='pdns';" 2>/dev/null || \
  echo "No DB client available in PATH"

echo -e "\n--- Network Sockets on Port 53 ---"
ss -s
ss -tulpn | grep ':53'

echo -e "\n--- Notify Queue ---"
pdns_control show incoming-notifications 2>/dev/null || true
pdns_control show outgoing-notifications 2>/dev/null || true
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Shared backend DB CPU contention | pdns backend query latency spikes when other apps query the same DB server | `SHOW PROCESSLIST` on MySQL; `pg_stat_activity` on PG — find non-pdns long-running queries | Separate pdns onto a dedicated DB read replica | Provision a dedicated DB instance or read replica for pdns |
| DNS query flood from misconfigured client | CPU high on pdns; `udp-queries` counter spiking | `tcpdump -i any port 53 -n | awk '{print $3}' | sort | uniq -c | sort -rn | head` | Block offending source IP at firewall; rate-limit UDP/53 per IP | Implement per-IP rate limiting with `pdns_control set-max-tcp-connections` and OS iptables |
| Colocated high-I/O process exhausting disk bandwidth | Backend DB slow on same host; zone query latency high | `iostat -x 1 5`; identify PID via `iotop` | Move pdns or the noisy process to separate host | Separate pdns backend DB onto dedicated storage tier |
| Port 53 conflict with systemd-resolved | pdns fails to start; existing socket occupied | `ss -tulpn | grep :53`; `systemctl status systemd-resolved` | Disable systemd-resolved stub listener: set `DNSStubListener=no` | Configure OS DNS stub listener to bind only to loopback `127.0.0.53` |
| Packet cache memory pressure from large neighbor processes | OS evicting pdns cache pages; cache hit ratio drops | `free -m`; compare pdns RSS vs available | Reduce `max-packet-cache-entries` to free OS page cache for DB | Set memory limits via cgroups; run pdns on a node with reserved RAM |
| Log write contention on shared filesystem | Log flushes delayed; `pdns_latency` p99 spikes briefly | `iostat` on log volume; check if multiple services write same NFS/EFS | Direct pdns logs to local disk or syslog socket | Use `logging-facility=local0` to emit to syslog instead of file |
| AXFR traffic saturating NIC shared with application traffic | Zone transfers slow; application DNS lookups degraded simultaneously | `iftop -i <nic>`; filter for port 53 TCP | Schedule AXFR transfers during off-peak; limit with `max-tcp-connections` | Provision dedicated NIC or VLAN for zone-transfer traffic |
| CPU steal from co-tenanted VM | pdns query latency variably high; correlates with host activity | `top` — check `%st` (steal) column | Migrate to dedicated physical host or bare-metal | Use bare-metal or CPU-pinned VM for latency-sensitive authoritative DNS |
| Shared ZooKeeper/etcd ensemble (PowerDNS gSlb scenario) | Meta-data reads slow; health-check propagation delayed | Check ZK/etcd latency from pdns host | Isolate pdns metadata store from application ZK ensemble | Run dedicated coordination service for pdns; set strict ensemble quotas |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| `pdns_server` process crash | All authoritative DNS queries for hosted zones return `SERVFAIL` → resolvers retry and get no answer → clients receive resolution failures → web/API traffic drops | All domains hosted on this PowerDNS instance; every service depending on those domains | `systemctl status pdns` shows `failed`; `dig @<host> <zone> SOA` returns `SERVFAIL`; monitoring alert on port 53 reachability | `systemctl restart pdns`; failover traffic to secondary authoritative NS via registrar if available |
| Backend database unavailable | PowerDNS cannot resolve queries → all zones return `SERVFAIL` → DNS resolution chain breaks → downstream HTTP/TLS failures for all hosted domains | All DNS zones served from the backend DB; if packet cache is empty: immediate; if warm: delayed by `cache-ttl` | pdns log: `Backend reported permanent error: Can't connect to MySQL server`; `dig @localhost example.com` → `SERVFAIL` | Restart DB or failover DB; if packet cache configured, it will serve cached answers during DB outage (`cache-ttl` duration) |
| Zone transfer (AXFR) failure from hidden primary | Secondary PowerDNS servers stop receiving updates → zone data becomes stale → records changed on primary not propagated → new subdomains not resolvable | All secondary NS servers for the zone; clients using those secondaries | `pdns_control retrieve <zone>` returns error; secondary zone serial stays behind primary: `dig @<secondary> <zone> SOA` vs `dig @<primary> <zone> SOA` | Force AXFR from primary: `pdns_control retrieve <zone>`; check TCP port 53 connectivity primary→secondary |
| DNSSEC signing key expiry | Resolvers with DNSSEC validation receive `SERVFAIL` for DNSSEC-enabled zones → only non-DNSSEC resolvers can still answer | All DNSSEC-validating resolvers (most modern resolvers); domains become unreachable for most users | `pdnsutil check-zone <zone>` shows signature expiry warnings; `dig @8.8.8.8 <zone> SOA +dnssec` returns `SERVFAIL` with `ad` flag absent | Roll over signing keys immediately: `pdnsutil activate-zone-key <zone> <new-key-id>`; reduce TTL on DNSKEY records before expiry |
| PowerDNS Recursor forwarding to failed upstream resolver | All recursive queries fail → clients cannot resolve external domains → egress traffic breaks (package updates, external API calls, etc.) | All internal clients using this Recursor for recursive resolution | `pdns_recursor --config-dir=/etc/pdns-recursor show forward-zones`; `dig @<recursor> 8.8.8.8.in-addr.arpa PTR` → `SERVFAIL` | Update `forward-zones-recurse` to use healthy upstream resolvers; `rec_control reload-zones` |
| Packet cache full with NXDOMAIN responses (negative cache poisoning) | Legitimate queries return `NXDOMAIN` from cache long after DNS record added | All queries for cached NXDOMAIN names until TTL expires | `pdns_control show packetcache-hit`; `pdns_control show packetcache-size` near max; `dig @<host> <newrecord>` returns NXDOMAIN but record exists on backend | `pdns_control purge-zone <zone>` or `pdns_control purge` to flush entire packet cache |
| Notify flood from misconfigured zone update automation | `NOTIFY` storm saturates pdns notify queue → legitimate zone transfers delayed → inbound UDP/TCP 53 partially consumed | Zone propagation to secondaries delayed; potential port 53 saturation | `pdns_control show incoming-notifications` rising unboundedly; `tcpdump -i any port 53 -n | grep NOTIFY` showing flood | Block NOTIFY source at firewall: `iptables -A INPUT -s <bad_src> -p udp --dport 53 -j DROP`; fix automation |
| Registrar NS delegation pointing to decommissioned server | DNS queries for zone go to dead IP → all resolution fails for the domain → HTTPS/API traffic drops | The entire domain; all services under it | `dig <domain> NS @8.8.8.8` returns NS pointing to unreachable IP; `dig @<ns_ip> <domain> SOA` times out | Update NS records at registrar immediately; propagation takes up to 48h — bring back old server IP or repoint quickly |
| PowerDNS API authentication failure after key rotation | Zone management automation fails → no new records can be created/updated → dynamic DNS records stale | Automated zone management; DNS-01 ACME challenges fail; dynamic service discovery breaks | `curl -H "X-API-Key: $PDNS_API_KEY" http://localhost:8081/api/v1/servers` returns `401 Unauthorized`; cert renewal alerts | Update API key in `pdns.conf`: `api-key=<new-key>`; restart or `pdns_control reload` if key can be reloaded |
| TTL set too low on all zone records | Resolver cache expires frequently → query rate to PowerDNS multiplies → DB backend overwhelmed → latency rises | All zones with low TTL; DB backend CPU; cache hit ratio collapses | `pdns_control show packetcache-hit` drops dramatically; backend query rate spikes; DB `SHOW PROCESSLIST` shows pdns queries queueing | `pdns_control purge`; issue DB UPDATE to raise TTL: `UPDATE records SET ttl=3600 WHERE ttl < 60`; reload zones |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| PowerDNS version upgrade | `pdns_server` fails to start if config options removed/renamed: `Unable to launch, because: Unknown option 'old-option'` | Immediately on service restart post-upgrade | `pdns_server --version`; check upgrade changelog for removed options; correlate with package upgrade timestamp | `grep "old-option" /etc/pdns/pdns.conf` and rename/remove it; `apt install pdns-server=<old_version>` to roll back |
| Backend schema migration (gsqlite3/gmysql/gpgsql) | Queries fail with `Backend reported permanent error: Table 'dns.records' doesn't exist` | Immediately after schema migration if incompatible | Check pdns backend schema version vs installed pdns version schema; `pdns_server --list-modules` | Apply correct schema from `/usr/share/doc/pdns-backend-<backend>/schema.sql`; or revert DB schema migration |
| Changing `launch=` backend in `pdns.conf` | pdns starts but serves empty zones (new backend has no data) | Immediately on restart | `pdns_server --config` shows new backend; `dig @localhost <zone> SOA` returns NXDOMAIN | Revert `launch=` to previous backend; restart pdns |
| `api-key` rotation in `pdns.conf` | All automation using old key gets `401 Unauthorized`; cert auto-renewal (ACME DNS-01) fails; zone update pipelines break | Immediately after `pdns_control reload` or service restart | Correlate automation failures with `pdns.conf` change timestamp; check `curl -H "X-API-Key: $OLD_KEY" http://localhost:8081/api/v1/servers` | Update API key in all consuming automation; `pdns_control reload` after fixing consumers |
| Reducing `max-cache-entries` or `max-packet-cache-entries` | Cache eviction increases; backend query rate rises; DB CPU spikes | Within minutes under normal load after config reload | `pdns_control show packetcache-hit` ratio drops; `pdns_control show backend-queries` rises; correlate with `pdns.conf` change | Increase cache size back: `pdns_control set-max-packet-cache-entries 1000000`; or edit `pdns.conf` and `pdns_control reload` |
| Adding `webserver=yes` without firewall rules | PowerDNS API/metrics port (8081) exposed to internet → unauthorized access to zone management | Immediately on restart with new config | `ss -tlnp | grep 8081`; attempt `curl http://<public_ip>:8081/api/v1/servers` | Add `webserver-address=127.0.0.1` in `pdns.conf`; `pdns_control reload`; block 8081 at firewall |
| Zone SOA serial not incremented after manual edit | Secondary servers do not pull updated zone data → stale NS/A records on secondaries | Seconds for NOTIFY, minutes for poll-based refresh | Compare `dig @<primary> <zone> SOA` vs `dig @<secondary> <zone> SOA` serial numbers | `pdnsutil increase-serial <zone>`; `pdns_control notify <zone>` to trigger AXFR on secondaries |
| DNSSEC key algorithm change (e.g., RSA to ECDSA) | During rollover window, resolvers that cached old DNSKEY reject answers signed with new key → `SERVFAIL` from DNSSEC validators | Immediately on first query after key activation if TTL not respected during rollover | `dig @8.8.8.8 <zone> DNSKEY` shows only new key; `pdnsutil check-zone <zone>` shows rollover status | Follow RFC 6840 rollover procedure: add new key, wait one TTL, then deactivate old key; check `pdnsutil show-zone <zone>` |
| `cache-ttl` increased globally | Stale records served long after changes; new A/CNAME records not visible to clients | Immediately for records changed after TTL increase | `pdns_control show cache-ttl`; correlate with `pdns.conf` change; test: `dig @<host> <changed-record>` | `pdns_control set-cache-ttl 60`; `pdns_control purge` to flush stale entries; revert `cache-ttl` in config |
| Enabling `log-dns-queries=yes` under high query volume | Disk I/O saturated by log writes → query latency rises → `pdns_server` thread pool stalls | Within minutes under production query rate | `iostat -x 1 5` shows 100% on log volume; `df -h /var/log` growing rapidly; pdns latency rising | `pdns_control set-log-dns-queries 0`; disable query logging immediately; rotate/truncate log file |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Zone serial mismatch — primary ahead of secondary | `dig @<primary> <zone> SOA` vs `dig @<secondary> <zone> SOA` — compare serial numbers | Secondary serves stale records; new subdomains not resolvable from secondary NS | Inconsistent DNS responses depending on which NS resolvers query; new records not globally reachable | `pdns_control notify <zone>` on primary to trigger AXFR; verify with `pdns_control retrieve <zone>` on secondary |
| Split-brain — two hidden primaries both updating the same zone | Zone serials diverge; secondaries alternate between two versions based on which primary notified last | `dig @<secondary1> <zone> SOA` serial oscillates; conflicting A records returned | Intermittent DNS failures; A/B record resolution depending on which secondary answers | Designate one authoritative primary; fence the other: `pdns_control disable-master <zone>` on the demoted node |
| Packet cache serving NXDOMAIN after record creation | `dig @<pdns-host> <new-record> A` returns NXDOMAIN despite record in DB | Record created in DB but cached negative response still served | New service endpoints not resolvable until cache TTL expires | `pdns_control purge <new-record>` to remove specific entry; or `pdns_control purge-zone <zone>` |
| DNSSEC signature validity gap during key rollover | `dig @8.8.8.8 <zone> A +dnssec` returns SERVFAIL with `RRSIG` missing or expired | DNSSEC-validating resolvers refuse to answer zone queries | Domain unreachable for DNSSEC-validating resolvers; only plain-DNS resolvers work | `pdnsutil sign-zone <zone>`; verify: `pdnsutil check-zone <zone>`; check `pdns_control show dnssec-sign-requests` |
| Backend DB record duplicates (multiple records same name/type) | `dig @<pdns-host> <name> A` returns multiple conflicting A records | DNS responses contain unexpected extra records; load balancing broken; services pointing to wrong IP | Traffic misdirected; TLS cert validation may fail if SAN mismatch | `psql -c "SELECT id, name, type, content FROM records WHERE name='<name>' AND type='A';"` — delete duplicates by ID; `pdns_control purge <name>` |
| Glue record inconsistency (NS records without matching A records) | `dig <zone> NS` returns NS names; `dig @<ns-name> <zone> SOA` fails with lame delegation | Delegation chain broken; resolvers cannot reach authoritative server | Zone appears down to resolvers following full delegation chain | Add A records for NS hostnames: `pdnsutil add-record <ns-name-zone> @ A <ip>`; `pdns_control notify <zone>` |
| Config drift between PowerDNS nodes (different `cache-ttl`, `max-cache-entries`) | `pdns_control show cache-ttl` differs between nodes | Different cache behavior per node; some nodes serve stale data longer than others | Non-deterministic DNS responses; harder to debug and predict cache invalidation | Standardize config via config management (Ansible/Chef); compare `diff <(pdns_control show cache-ttl on node1) <(...)>` |
| AXFR producing truncated zone (TCP timeout mid-transfer) | `pdns_control retrieve <zone>` completes but zone record count on secondary < primary | Secondary serves partial zone; some records missing | Missing DNS records for services in the zone; intermittent resolution failures | Re-trigger full AXFR: `pdns_control retrieve <zone>`; compare: `dig @<secondary> <zone> AXFR | wc -l` vs `dig @<primary> <zone> AXFR | wc -l` |
| Stale DS records at parent after DNSSEC key rollover | Child zone signed with new KSK but parent DS record still references old KSK | `dig @8.8.8.8 <zone> DS` returns old key tag; `SERVFAIL` for all DNSSEC validating queries | Zone completely broken for DNSSEC validators | Update DS record at registrar/parent zone: `pdnsutil show-zone <zone>` → get new DS hash → submit to registrar |
| `pdns_recursor` forwarding stale cached NXDOMAIN for internal zone | `rec_control get neg-cache-size` shows large negative cache; internal zone record not resolving | Internal DNS records invisible to Recursor clients until negative TTL expires | Internal service discovery broken for negative-cached names | `rec_control wipe-cache <name>` for specific entries; or `rec_control reload-zones` to flush forward zones |

## Runbook Decision Trees

### Decision Tree 1: DNS queries returning SERVFAIL

```
Is `pdns_control ping` responding with PONG?
├── NO → pdns process is down
│   ├── Check: `systemctl status pdns` — is it failed or activating?
│   │   ├── Failed: `journalctl -u pdns -n 50` → look for backend startup error
│   │   │   ├── "Unable to launch, because: gsqlite3" → DB file missing/corrupt:
│   │   │   │   `sqlite3 /var/lib/pdns/pdns.db "PRAGMA integrity_check;"` → restore from backup
│   │   │   └── "Address already in use" → stale pidfile or duplicate process:
│   │   │       `kill $(cat /var/run/pdns/pdns.pid)`; `systemctl start pdns`
│   │   └── Start: `systemctl start pdns && journalctl -u pdns -f`
│   └── Ping works but queries fail → continue below
└── YES → What does `pdns_control get servfail-packets` show increasing?
    ├── YES → Backend returning errors?
    │   ├── `pdns_control backend-cmd gsqlite3-0 "SELECT count(*) FROM records;"` fails
    │   │   ├── DB locked: `lsof /var/lib/pdns/pdns.db` → kill locking process
    │   │   └── DB corrupt: restore from backup; `pdns_control reload`
    │   └── Backend query succeeds → Zone data issue?
    │       ├── `pdns_control list-zones | grep <zone>` — is zone present?
    │       │   ├── NO → `pdnsutil load-zone <zone> <file>` to load missing zone
    │       │   └── YES → `pdnsutil check-zone <zone>` → fix reported errors
    └── NO SERVFAIL increase → Is the alert from NXDOMAIN spike?
        └── `pdns_control get nxdomain-packets` — if high: check for missing records or typos in zone
            → `dig @127.0.0.1 <failing_name> A` to reproduce; fix missing record
```

### Decision Tree 2: Recursor not resolving external names

```
Is `rec_control ping` responding?
├── NO → Recursor process down
│   ├── `systemctl start pdns-recursor && journalctl -u pdns-recursor -n 30`
│   └── Check: `rec_control get-all` after restart to confirm metrics flowing
└── YES → Can recursor reach root servers?
    ├── Test: `dig @127.0.0.1 www.google.com A` — does it return SERVFAIL?
    │   ├── YES → Check outbound connectivity: `dig @8.8.8.8 www.google.com A` from recursor host
    │   │   ├── Fails → Firewall blocking port 53 outbound: `iptables -L OUTPUT -n | grep 53`
    │   │   │   → Add rule or open firewall; `rec_control reload-zones`
    │   │   └── Succeeds → Root hints stale: `rec_control reload-hints`
    │   └── NO (NOERROR but wrong answer) → DNSSEC bogus?
    │       ├── `rec_control get dnssec-validations-bogus` increasing?
    │       │   ├── YES → `rec_control add-negative-trust-anchor <zone> "debug"`
    │       │   │        → Contact zone owner about DNSSEC key rollover
    │       │   └── NO → Check RPZ/response policy zone for unexpected overrides:
    │       │            `grep "rpz" /etc/pdns/recursor.conf`
    └── Specific domain not resolving?
        ├── Check `rec_control dump-nsspeeds` for unreachable nameservers for that TLD
        └── `rec_control trace-regex <domain>` to trace resolution path
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Recursor NXDOMAIN cache flooded by random subdomain attack (NXNS) | `rec_control get nxdomain-packets` growing rapidly; cache fills; memory usage rising | `rec_control get-all | grep "cache-size\|nxdomain-packets\|unique-sub-domains"`; `rec_control get packetcache-size` | Recursor CPU and memory exhaustion; legitimate queries delayed | Enable QPS rate limiting: `rec_control set-max-qps 1000`; add rate limit per-source: `dnsdist -c` | Configure `max-negative-ttl=60`; use Dnsdist in front of recursor for per-client rate limiting |
| Authoritative backend DB connections leaking (MySQL/PostgreSQL backend) | DB `max_connections` exhausted; `pdns_control backend-cmd` times out | `pdns_control backend-cmd gmysql-0 "SHOW PROCESSLIST;"` — count pdns connections | Authoritative server returns SERVFAIL for all queries | `pdns_control reload` to cycle connections; check `pdns.conf` for `gmysql-port` and connection pool size | Set `gmysql-dnssec=no` if DNSSEC unused (reduces queries by 3×); use connection pooling via ProxySQL |
| Zone data explosion from misconfigured dynamic update | Zone grows by millions of records; DB storage growing daily | `pdns_control list-zones`; `pdnsutil show-zone <zone> | wc -l`; `SELECT count(*) FROM records WHERE domain_id=?` | DB storage exhaustion; AXFR to secondaries fails; high memory on load | `pdnsutil delete-zone <zone>`; restore clean zone from backup | Restrict dynamic update: `allow-dnsupdate-from=127.0.0.1/8`; require TSIG key for all updates |
| AXFR transfer storm: all secondaries transferring simultaneously | Network saturation; `pdns_control get axfr-out` spike; authoritative CPU high | `pdns_control get axfr-out`; `ss -n -p | grep ":53" | grep ESTABLISHED | wc -l` | Network bandwidth exhaustion; AXFR transfers for other zones delayed | `pdns_control decrease-axfr-in-progress-counter <zone>` (if available); temporarily block AXFR in firewall | Stagger secondary `refresh` intervals; use IXFR (incremental) via `pdns_control notify <zone>` |
| Recursive lookup amplification: many queries for large TXT records | High outbound bandwidth; large TXT responses amplifying traffic | `pdns_control get outgoing-queries`; `tcpdump -i eth0 port 53 -n | awk '{print $10}' | sort | uniq -c | sort -rn | head` | Network bandwidth cost; potential abuse vector | Block recursive queries from external IPs: `allow-recursion=127.0.0.0/8,10.0.0.0/8` in `pdns.conf` or recursor | Never run authoritative and recursive on same IP; use `dnsdist` for query filtering |
| DNSSEC signing re-signing loop after algorithm mismatch | CPU high from constant re-signing; `pdnsutil show-zone` shows very recent `Last signed` timestamp | `pdnsutil show-zone <zone> | grep "Last signed"`; `pdns_control get key-cache-size` | High CPU on authoritative server | `pdnsutil disable-dnssec <zone>` to stop re-signing; investigate key algorithm mismatch | Use `pdnsutil rectify-all-zones` after zone imports; ensure uniform algorithm across all keys |
| Packet cache bloat from wildcard queries with unique sub-labels | Packet cache grows to GB; memory exhaustion on recursor | `rec_control get packetcache-size`; `rec_control get-all | grep cache` | Recursor OOM-killed; DNS outage | `rec_control wipe-cache .`; reduce `packetcache-ttl=60` | Set `max-packetcache-entries=500000`; monitor growth rate via Prometheus |
| Secondary zone proliferation from abandoned projects | Hundreds of slave zones still transferring from decommissioned primaries | `pdns_control list-zones | grep SLAVE | wc -l` growing; `pdns_control show-queries` shows repeated AXFR-failed | Wasted CPU/network for failed transfers | `pdnsutil delete-zone <zone>` for each decommissioned zone | Automate zone lifecycle: remove slave zones when project is decommissioned via IaC |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot zone from high-query-rate domain served by single authoritative | One zone accounts for 90% of query load; `pdns_control get udp-queries` climbing; CPU high on single thread | `pdns_control get-all | grep -E "^(udp-queries|tcp-queries|latency)"` and `tcpdump -i eth0 port 53 -n | awk '{print $10}' | sort | uniq -c | sort -rn | head -20` | Single high-traffic domain not distributed across multiple authoritative servers | Add secondary nameservers; enable recursor-side packet cache: `pdns_control set-max-packetcache-entries 1000000` |
| Connection pool exhaustion for MySQL/PostgreSQL backend | `pdns_control backend-cmd` times out; SERVFAIL rate rises | `pdns_control backend-cmd gmysql-0 "SHOW STATUS LIKE 'Threads_connected';"` and `grep "Unable to launch query" /var/log/pdns/pdns.log | tail -20` | Database backend connection limit hit; PDNs not recycling connections | Increase `gmysql-extra-connection-flags` pool size; `pdns_control reload` to cycle connections; add ProxySQL in front |
| Memory pressure from large packet cache on recursor | Recursor RAM usage > 80%; OOM risk; GC-like pauses | `rec_control get-all | grep -E "cache-size|memory"` and `rec_control get cache-bytes` | `max-cache-entries` set too high; many unique query patterns filling cache | `rec_control wipe-cache .`; reduce `max-cache-entries=500000`; set `max-packetcache-entries=500000` |
| Thread pool saturation on authoritative from TCP query burst | TCP query backlog growing; UDP queries still answered; TCP clients timing out | `pdns_control get tcp-queries tcp-answers` diff growing; `ss -tn dport = :53 | wc -l` | Default `receiver-threads` too low for TCP-heavy workload (DNSSEC, large TXT) | Increase `receiver-threads=4` in `pdns.conf`; enable `tcp-fast-open=1` |
| Slow query from backend DB index missing on `name` column | SERVFAIL for specific zones; MySQL slow query log shows full scan on `records` table | `pdns_control backend-cmd gmysql-0 "EXPLAIN SELECT * FROM records WHERE name='example.com' AND type='A';"` | Missing index on `records.name`; full table scan on large zone | `CREATE INDEX idx_records_name ON records(name)` via database; `pdns_control reload` |
| CPU steal from VM host affecting query response time | Query latency p99 > 100 ms intermittently; load average low; `vmstat %st` > 5% | `vmstat 1 10 | awk '{print $15}'` and `pdns_control get latency` | Hypervisor CPU contention on shared cloud instance | Migrate to dedicated host; use `pdns_control set-max-qps` to shed load during steal periods |
| Lock contention in SQLite backend under concurrent writes | SERVFAIL for zones in SQLite backend during zone update; `pdns.log` shows `database is locked` | `journalctl -u pdns --since "1h ago" | grep "database is locked\|SQLiteBacked"` | SQLite write lock held by one thread; others time out | Migrate to MySQL/PostgreSQL backend for any write-heavy deployments; or use WAL mode: `PRAGMA journal_mode=WAL` |
| Serialization overhead from DNSSEC signing on every query | CPU high; latency rising for DNSSEC-enabled zones; `pdns_control get signatures` climbing | `pdns_control get signatures`; `pdns_control get-all | grep key-cache` | DNSSEC key cache too small; signing every query | Increase `key-cache-ttl=600`; `pdns_control purge`; ensure `pre-signed` zones where possible |
| Batch NOTIFY backlog after large zone update | `pdns_control notify <zone>` takes > 10 s; secondary transfers queued; stale records on secondaries | `pdns_control get-all | grep "notify\|axfr"` and `ss -n | grep ":53" | grep ESTABLISHED | wc -l` | Too many secondary servers notified simultaneously; small `notify-threads` setting | Increase `notify-threads=4`; stagger secondary refresh intervals; use IXFR to reduce transfer size |
| Downstream recursor dependency latency from slow upstream resolver | Recursive resolution for new names > 2 s; cached names fast | `rec_control get outgoing-queries`; `rec_control trace-regex .*` | Upstream authoritative servers slow; recursor not caching negative answers aggressively | Set `max-negative-ttl=300`; configure `forward-zones-recurse` to faster resolvers; enable `aggressive-nsec-cache-size=100000` |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on DNS-over-HTTPS / DNS-over-TLS endpoint | Clients report `certificate has expired`; `openssl s_client -connect <pdns-host>:853 2>/dev/null | openssl x509 -noout -dates` shows past `notAfter` | DoT/DoH TLS certificate not renewed | All DoT/DoH clients cannot connect; fallback to plain DNS if configured | Renew cert; update `tls-certificate` and `tls-key` in `pdns-recursor.conf`; `rec_control reload-lua-config` |
| mTLS rotation failure between primary and secondary (TSIG) | Zone transfer fails with `TSIG record not valid` in pdns log | `pdns_control notify <zone>`; `journalctl -u pdns | grep "TSIG\|tsig"` | TSIG key mismatch after rotation; secondary still using old key | `pdnsutil import-tsig-key <name> <algo> <key>` on both primary and secondary; `pdns_control reload` |
| DNS resolution failure: recursor cannot reach root hints | All recursive queries fail; `rec_control get servfail-answers` rising; cache empty | `rec_control trace-regex .*`; `dig @<recursor-ip> google.com`; `dig @127.0.0.1 . NS` | Firewall blocking outbound UDP/TCP port 53 to internet; or root hints file stale | Check egress firewall: `nc -uzv 198.41.0.4 53`; update root hints: `rec_control reload-lua-config`; verify `/etc/powerdns/root.hints` |
| TCP connection exhaustion for zone transfer (AXFR) | Secondary servers cannot initiate AXFR; `dig AXFR <zone> @<primary>` times out | `ss -tn sport = :53 | grep ESTABLISHED | wc -l`; `pdns_control get-all | grep tcp` | `max-tcp-connections` too low; too many concurrent AXFRs | Increase `max-tcp-connections=200` in `pdns.conf`; `pdns_control reload`; stagger secondary refresh intervals |
| Load balancer misconfiguration routing DNS to wrong PDNS instance | Queries for authoritative zone returning REFUSED or SERVFAIL from wrong instance | `dig @<lb-vip> <zone> SOA`; compare result to `dig @<direct-pdns-ip> <zone> SOA` | Clients receiving wrong or empty answers; authoritative zone unreachable | Verify LB health check uses `dig @<backend> . SOA` not just TCP; correct backend pool to point to authoritative instances |
| Packet loss on UDP path causing query retries | DNS query RTT p99 > 500 ms; resolver retry visible in `tcpdump`; `rec_control get over-capacity-drops` rising | `tcpdump -i eth0 port 53 -n -c 1000 | awk '{print $10}' | sort | uniq -c | sort -rn | head`; `ping -c 100 <upstream-ns>` | Network path packet loss; ISP or cloud transit issue | Enable `pdns_control set-max-qps` to reduce load; use TCP fallback: `prefer-tcp=yes` in recursor; contact network team |
| MTU mismatch causing large DNSSEC responses to be truncated | DNSSEC-enabled zones return `TRUNCATED` flag over UDP; TCP fallback working | `dig +dnssec <zone> <type> @<pdns-ip>`; check `tc` (truncated) flag in response; `ping -M do -s 1472 <client-ip>` | DNSSEC responses > 1500 bytes truncated; resolution fails for clients without TCP fallback | Reduce `udp-truncation-threshold=1232` (EDNS0 default); enable `edns-padding` for DoT; ensure firewalls allow DNS over TCP |
| Firewall rule blocking DNS query to backend authoritative | Recursor returns SERVFAIL for specific zone; authoritative unreachable | `nc -uzv <authoritative-ip> 53`; `dig @<authoritative-ip> <zone> SOA`; `iptables -L -n | grep 53` | Resolution failure for zones hosted on affected authoritative | Add firewall rule: `iptables -A OUTPUT -p udp --dport 53 -d <authoritative-ip> -j ACCEPT`; reload pdns |
| SSL handshake timeout for DoT/DoH under certificate load | DoT/DoH connection p99 > 2 s; plain UDP DNS fast | `time openssl s_client -connect <pdns-host>:853 < /dev/null 2>/dev/null | grep "SSL handshake"` | DoT/DoH clients timing out; security-conscious clients falling back to plain DNS | Enable TLS 1.3: `tls-min-version=tls1.3` in recursor config; reduce cert chain length; enable TLS session resumption |
| Connection reset for long-lived AXFR over slow WAN link | AXFR of large zone fails mid-transfer; `dig AXFR` returns partial zone | `dig AXFR <zone> @<primary-ip> | wc -l` vs `pdnsutil show-zone <zone> | grep "Records:"` | Secondary receives partial zone; serves stale or inconsistent records | Enable IXFR: `disable-axfr=no` with NOTIFY+IXFR; increase `axfr-fetch-timeout=20`; or use zone file backup transfer |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of pdns or pdns-recursor | `pdns` or `pdns-recursor` process disappears; DNS outage; `dmesg` shows OOM kill | `journalctl -k --since "1h ago" | grep -iE "oom|pdns|killed"`; `systemctl status pdns` shows `Active: failed` | `systemctl restart pdns`; reduce cache sizes: `pdns_control set-max-packetcache-entries 200000` | Set systemd `MemoryMax=2G`; monitor cache size via Prometheus `pdns_recursor_cache_size`; alert at 80% RAM |
| Disk full on zone data partition | New zone creation fails; backend DB write errors; pdns log shows `database error` | `df -h /var/lib/pdns`; `SELECT table_schema, ROUND(SUM(data_length+index_length)/1073741824,2) AS gb FROM information_schema.tables WHERE table_schema='pdns' GROUP BY table_schema;` | Zone record explosion or stale zone accumulation | Delete stale slave zones: `pdnsutil delete-zone <zone>`; archive old zone data; extend partition | Monitor `/var/lib/pdns` at 70%/85%; automate zone lifecycle cleanup via IaC |
| Disk full on PDNS log partition | pdns stops logging; disk full error in systemd journal; query visibility lost | `df -h /var/log`; `journalctl --disk-usage`; `du -sh /var/log/pdns/*.log 2>/dev/null | sort -rh | head` | Verbose query logging (`log-dns-queries=yes`) without log rotation | `journalctl --vacuum-size=2G`; disable verbose query logging: `pdns_control set log-dns-queries no`; configure `logrotate` | Set `log-dns-queries=no` in production; use Prometheus metrics instead of log-based monitoring |
| File descriptor exhaustion | `pdns` fails to open new backend connections; SERVFAIL for all zones; `Too many open files` in log | `ls -l /proc/$(pgrep -x pdns)/fd | wc -l`; `cat /proc/sys/fs/file-max` | Increase `LimitNOFILE=65536` in pdns systemd unit; `systemctl daemon-reload && systemctl restart pdns` | Set `LimitNOFILE=65536` in `/etc/systemd/system/pdns.service.d/override.conf` |
| Inode exhaustion from zone file accumulation | `touch` fails despite free disk; `df -i /var/lib/pdns` at 100%; zone file imports fail | `df -i /var/lib/pdns`; `find /var/lib/pdns -type f | wc -l` | `find /var/lib/pdns -name "*.bak" -mtime +7 -delete`; clean up orphaned zone files | Prefer database backend over file backend; clean up `.bak` and temp files with cron |
| CPU steal/throttle on VM affecting query response time | Query latency p99 > 50 ms intermittently; load average low; `vmstat %st` high | `vmstat 1 10 | tail -5`; `pdns_control get latency` | Upgrade to dedicated VM; `pdns_control set-max-qps 5000` to shed load during steal | Use dedicated non-burstable instance for authoritative DNS; DNS latency SLO < 10 ms |
| Swap exhaustion from recursor cache growth | Recursor page-faulting on cache lookups; latency increases 10–100×; `vmstat si`/`so` non-zero | `free -h`; `vmstat 1 5`; `rec_control get cache-bytes` | `rec_control wipe-cache .`; restart recursor; reduce `max-cache-entries` | `vm.swappiness=1`; size RAM for full recursor cache + OS; set `max-cache-entries` to fit in RAM |
| Kernel PID limit from pdns thread explosion | `clone: Resource temporarily unavailable`; pdns cannot spawn new backend threads | `cat /proc/sys/kernel/pid_max`; `ps -eLf | grep pdns | wc -l` | `sysctl -w kernel.pid_max=131072`; `systemctl restart pdns` | Set `kernel.pid_max=131072` in `/etc/sysctl.d/99-pdns.conf`; limit `receiver-threads` and `distributor-threads` |
| Network socket buffer exhaustion under high UDP query rate | Kernel dropping UDP packets at socket; `pdns_control get udp-recvbuf-errors` rising | `pdns_control get udp-recvbuf-errors`; `sysctl net.core.rmem_max net.core.wmem_max` | `sysctl -w net.core.rmem_max=26214400 net.core.wmem_max=26214400` | Pre-tune: `sysctl -w net.core.rmem_max=26214400` in `/etc/sysctl.d/99-pdns.conf`; set `udp-recvbuf=26214400` in `pdns.conf` |
| Ephemeral port exhaustion from recursor outgoing query storm | `rec_control get resource-limits` shows socket errors; `Cannot assign requested address` in recursor log | `ss -s | grep TIME-WAIT`; `cat /proc/sys/net/ipv4/ip_local_port_range` | `sysctl -w net.ipv4.tcp_fin_timeout=10 net.ipv4.tcp_tw_reuse=1 net.ipv4.ip_local_port_range="1024 65535"` | Configure `udp-source-port-min=1025 udp-source-port-max=65534` in recursor; monitor `resource-limits` metric |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation: zone record upserted twice via API race condition | `SELECT name, type, content, COUNT(*) FROM records GROUP BY name, type, content HAVING COUNT(*) > 1;` in backend DB | `pdns_control backend-cmd gmysql-0 "SELECT name, type, content, COUNT(*) FROM records GROUP BY name, type, content HAVING COUNT(*) > 1 ORDER BY 4 DESC LIMIT 20;"` | Duplicate records returned in DNS answers; undefined resolver behavior | Delete duplicates via backend DB; `pdnsutil rectify-zone <zone>`; add `UNIQUE(domain_id, name, type, content, prio)` constraint |
| Saga partial failure: zone created in app DB but not in PDNS backend | Application shows zone as active; `dig @<pdns-ip> <zone> SOA` returns NXDOMAIN | `pdns_control list-zones | grep <zone>`; `pdnsutil show-zone <zone>` | Zone not resolvable; DNS outage for new zone | `pdnsutil create-zone <zone>`; add SOA, NS records; `pdns_control notify <zone>` to trigger secondary transfer |
| Out-of-order NOTIFY causing secondary to transfer stale zone version | Secondary received NOTIFY for version N but primary is already at N+2; AXFR returns stale data | `dig @<secondary-ip> <zone> SOA` — compare serial to `dig @<primary-ip> <zone> SOA`; `pdns_control show-queries` | Secondary serves stale records after propagation delay | `pdns_control notify <zone>` to re-trigger; `pdnsutil increase-zone-version <zone>`; force AXFR from secondary: `rndc retransfer <zone>` |
| Cross-service deadlock: simultaneous DDNS update and zone AXFR | AXFR blocked; DDNS update queued; `SHOW ENGINE INNODB STATUS` (MySQL backend) shows mutual lock wait | `pdns_control backend-cmd gmysql-0 "SHOW ENGINE INNODB STATUS\G" | grep -A30 "LATEST DETECTED DEADLOCK"` | AXFR or DDNS update fails; one transaction rolled back | Set `innodb_lock_wait_timeout=10` in MySQL backend; serialise DDNS updates via queue; avoid AXFR during bulk update windows |
| Out-of-order dynamic DNS update causing record regression | DDNS update for version N+1 processed before N; old IP stored | `SELECT name, content, change_date FROM records WHERE name='<hostname>' ORDER BY change_date DESC LIMIT 5;` | Client resolves to old IP after update; service unreachable | Manual UPDATE to correct value: `pdnsutil replace-rrset <zone> <name> A 60 <correct-ip>`; add sequence number to DDNS update key |
| At-least-once delivery: zone import processed twice from webhook retry | Zone record count doubles; duplicate records in backend | `pdns_control backend-cmd gmysql-0 "SELECT domain_id, COUNT(*) FROM records GROUP BY domain_id ORDER BY 2 DESC LIMIT 10;"` | DNS answers contain duplicate records; some resolvers reject or mishandle | Delete duplicates; `pdnsutil rectify-zone <zone>`; add idempotency key to zone import webhook handler |
| Compensating transaction failure after failed zone delegation change | Parent zone NS records updated but child zone SOA not updated; delegation broken | `dig NS <zone> @<parent-ns>` vs `dig SOA <zone> @<child-pdns>`; check serial mismatch | NXDOMAIN or SERVFAIL for child zone; DNS delegation broken | Update child zone SOA serial: `pdnsutil increase-zone-version <zone>`; re-notify secondaries; verify with `dig +trace <zone>` |
| Distributed lock expiry mid-zone-rectify: two pdnsutil processes rectifying simultaneously | `pdnsutil rectify-zone` taking > 5 min; second invocation starts from cron; DNSSEC auth data corrupted | `pgrep -fa "pdnsutil rectify"` — check for two concurrent processes | DNSSEC signature inconsistency; SERVFAIL for DNSSEC-validating resolvers | Kill duplicate: `pkill -f "pdnsutil rectify <zone>"`; re-run single: `pdnsutil rectify-zone <zone>`; verify: `pdnsutil check-zone <zone>` | Use file lock before rectify: `flock /tmp/rectify-<zone>.lock pdnsutil rectify-zone <zone>` in cron |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: one tenant's high-query-rate zone monopolising PDNS thread | `pdns_control get-all | grep udp-queries`; `tcpdump -i eth0 port 53 -n | awk '{print $10}' | sort | uniq -c | sort -rn | head` — one zone dominates | Other tenants' zones see increased query latency | `pdns_control set-max-qps 2000` to rate-limit globally; escalate to increase `receiver-threads` | Increase `receiver-threads=4`; move high-traffic zone to dedicated PDNS instance |
| Memory pressure from one tenant's large zone filling packet cache | `rec_control get cache-bytes`; `pdns_control get-all | grep "packetcache"` — cache near capacity | Cache eviction increases cache miss rate for all tenants' zones | `pdns_control purge <noisy-zone>.*` to evict noisy zone's cache entries | Set per-zone max TTL to limit cache footprint: `max-cache-ttl=300` in `pdns.conf` |
| Disk I/O saturation from tenant bulk zone record import | `iostat -xz 1 5` shows `%util` near 100% during tenant's zone import; `pdns_control backend-cmd gmysql-0 "SHOW PROCESSLIST;"` shows active INSERT | Zone record reads slow for all tenants during bulk write | Pause tenant import: kill importing process; resume during off-peak | Schedule bulk zone imports off-peak; use `pdnsutil load-zone` with rate limiting via application wrapper |
| Network bandwidth monopoly from tenant's large DNSSEC responses | `tcpdump -i eth0 port 53 -n | awk '{print length($0)}' | awk '{sum+=$1} END {print sum/NR "B avg"}'` — average response size high | UDP truncation (`TC=1`) for other tenants' queries; TCP fallback latency | No per-zone bandwidth limit in PDNS; reduce tenant's DNSSEC response size | Reduce RRSIG count: `default-soa-content` tuning; enable NSEC3 narrow mode; set `udp-truncation-threshold=1232` |
| Connection pool starvation from tenant's excessive TCP zone transfers | `ss -tn dport = :53 | grep ESTABLISHED | wc -l`; `pdns_control get tcp-queries` rising | Other tenants' AXFR requests queued or dropped | Limit tenant's transfer rate: `axfr-fetch-timeout=5` and `max-tcp-connections=50` in `pdns.conf` | Set `allow-axfr-ips` to whitelist only legitimate secondaries; add `max-tcp-connections` limit |
| Quota enforcement gap: tenant adding unlimited records via API | `curl -H "X-API-Key: $PDNS_API_KEY" http://localhost:8081/api/v1/servers/localhost/zones/<zone> | jq '.rrsets | length'` — record count exploding | Backend DB storage quota overrun; PDNS response size increases for zone | `curl -X DELETE -H "X-API-Key: $PDNS_API_KEY" http://localhost:8081/api/v1/servers/localhost/zones/<zone>/rrsets` to clean up bulk records | Enforce record count limit at API gateway layer before forwarding to PDNS API |
| Cross-tenant data leak risk via shared recursor cache | `rec_control dump-cache /tmp/cache.txt`; `grep -i "<other-tenant-hostname>" /tmp/cache.txt` — cached data visible to all | Tenant can infer internal hostnames of other tenants via cache inspection if recursor is shared | `rec_control wipe-cache <specific-hostname>` for sensitive entries | Separate recursor instances per tenant tier; or enable `serve-stale-extensions` carefully |
| Rate limit bypass: tenant using forged source IPs to exceed per-IP query rate | `pdns_control get-all | grep "throttled\|rrl"` — RRL not enabled; high per-source query rate | Shared RRL budget exhausted; legitimate queries from other tenants blocked | `pdns_control set-max-qps 5000` globally; enable RRL: `rrl-limit=50 rrl-slip=2 rrl-size=65535` | Enable Response Rate Limiting (RRL) in `pdns.conf`; configure per-source IP limits |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure for PDNS stats | `pdns_queries_total` absent in Grafana; dashboard shows stale last-known values | `prometheus_pdns_exporter` process crashed; or `pdns_control` returning errors; network partition to metrics port | `pdns_control get-all` directly on host to verify stats accessible; `curl http://localhost:9120/metrics | grep pdns_queries` | Add `up{job="pdns_exporter"}==0` alert; configure systemd watchdog for exporter; use `pdns_control` as backup metrics source |
| Trace sampling gap: missing DNSSEC validation failure traces | DNSSEC validation errors causing SERVFAIL not appearing in APM traces | Recursor DNSSEC failures are fast-path errors; head-based sampling at 1% discards them | `rec_control get-all | grep "dnssec\|bogus\|servfail"`; `dig +dnssec <zone> @<recursor-ip>` to trigger validation check | Configure `log-servfail=yes` in recursor; set `dnssec=validate`; APM: always sample error responses |
| Log pipeline silent drop for high-rate DNS query logging | DNS query logs missing from Splunk during DDoS event; attack invisible in logs | `log-dns-queries=yes` combined with high QPS (>100K QPS) overwhelms Fluentd buffer | `tail -f /var/log/pdns/pdns.log` on host to verify PDNS is logging; check Fluentd `buffer_queue_length` | Disable `log-dns-queries=yes` in production; use `tcpdump` or `pcap`-based logging for traffic analysis instead |
| Alert rule misconfiguration: SERVFAIL rate alert never fires | SERVFAIL rate at 50%; no PagerDuty page | Alert threshold set on `servfail_packets_total` rate but metric name changed between exporter versions | `pdns_control get servfail-packets` manually; `curl http://localhost:9120/metrics | grep servfail` to find actual metric name | Reconcile metric name with current exporter version; update Prometheus alert to match; test with `pdnsutil check-zone <zone>` breaking a zone |
| Cardinality explosion from per-zone-name metrics label | Prometheus OOM; dashboard load > 30 s; millions of series from tenants with many zones | Developer added `zone_name` as Prometheus label; each zone = new series | `curl http://localhost:9090/api/v1/label/zone_name/values | jq length` | Remove `zone_name` label from per-query metrics; use recording rules to aggregate by zone at scrape time |
| Missing health endpoint for PDNS backend DB connection | PDNS returning SERVFAIL for all zones due to backend DB down; no alert fires | PDNS has no HTTP health endpoint; only DNS-level check in place, which returns SERVFAIL (not timeout) | `dig @<pdns-ip> <zone> SOA`; `pdns_control backend-cmd gmysql-0 "SELECT 1;"` to test backend directly | Add `pdns_control` backend connectivity check to monitoring; alert on `SERVFAIL` rate > 5% of total queries |
| Instrumentation gap: zone propagation delay to secondaries not tracked | Secondary serves stale records after primary update; no SLO on propagation time | NOTIFY/AXFR timing not exported as a Prometheus metric | `dig @<secondary-ip> <zone> SOA` — compare serial to primary's serial; `pdns_control notify <zone>` and measure delta | Add custom Prometheus gauge measuring `time_since_last_successful_axfr_seconds` per secondary; alert if > 300 s |
| Alertmanager outage silencing PDNS availability alerts | PDNS crash undetected for 10 min; all DNS resolution failing; no on-call page | Alertmanager container on same host as PDNS OOM-killed when PDNS crashed | `amtool alert query alertname=PDNSDown`; check Alertmanager from external monitoring host | Run Alertmanager on separate host from PDNS; use external uptime monitoring (e.g., UptimeRobot) as redundant check |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| PowerDNS authoritative minor version upgrade rollback | Zone queries return SERVFAIL after upgrade; `pdns.log` shows backend schema mismatch | `pdns_control version`; `journalctl -u pdns --since "30 min ago" | grep ERROR` | `apt-get install pdns-server=<prev-ver>`; restore schema from backup if migrated; `systemctl restart pdns` | Test upgrade in staging with production zone snapshot; verify `pdnsutil check-all-zones` passes post-upgrade |
| DNSSEC key algorithm migration partial completion | DNSSEC validation fails for zone during algorithm rollover; resolvers return BOGUS | `pdnsutil show-zone <zone>`; `dig +dnssec <zone> DNSKEY @<pdns-ip>` — check both old and new KSK present | `pdnsutil deactivate-zone-key <zone> <new-key-id>`; re-publish parent DS for old algorithm only | Follow RFC 6781 algorithm rollover procedure; maintain both algorithms for two TTL periods before removing old |
| Backend schema migration partial completion (MySQL backend) | `pdns_control list-zones` fails; `pdns.log` shows SQL errors on missing column | `pdns_control backend-cmd gmysql-0 "DESCRIBE records;"` — check schema matches expected version | `mysql pdns < /usr/share/doc/pdns-backend-mysql/schema.mysql.sql` to restore schema; or rollback DB from backup | Always test schema migration on staging; backup DB before running `pdnsutil upgrade-crypto-hash`; use deploy request pattern |
| Rolling upgrade version skew: primary on new version, secondaries on old | Zone transfers failing: secondary rejects new AXFR format; `journalctl -u pdns | grep "AXFR"` shows format error | `dig @<secondary-ip> <zone> SOA`; `pdns_control version` on each node | Downgrade primary to match secondary version: `apt-get install pdns-server=<old-ver>` on primary | Upgrade secondaries before primary; test AXFR compatibility between versions in lab |
| Zero-downtime zone migration from file backend to MySQL gone wrong | Some zones missing after migration; clients get NXDOMAIN | `pdnsutil list-zones | wc -l` vs `ls /etc/pdns/zones/ | wc -l`; `diff <(pdnsutil list-zones | sort) <(ls /etc/pdns/zones/ | sed 's/.zone//' | sort)` | Re-import missing zones: `for z in <missing>; do pdnsutil load-zone $z /etc/pdns/zones/$z.zone; done` | Verify zone count matches before cutting over; run parallel serving for 24 h before removing file backend |
| Config format change: `gmysql-host` parameter renamed between versions | PDNS fails to start after upgrade; `pdns.log` shows `Unknown option: gmysql-host` | `pdns_control version`; `journalctl -u pdns | grep "Unknown option"` | Restore old `pdns.conf`; `systemctl restart pdns`; consult migration guide for renamed parameters | Diff `pdns.conf` against new version's example config before upgrading; use `pdns --config-check` |
| Data format incompatibility: NSEC3 iterations count change in new version | DNSSEC-validating resolvers reject zone after `nsec3param` change | `dig +dnssec +cd <zone> NSEC3PARAM @<pdns-ip>`; `pdnsutil show-zone <zone> | grep NSEC3` | `pdnsutil unset-nsec3 <zone>`; `pdnsutil set-nsec3 <zone> "1 0 10 auto"` to revert iterations | Follow NSEC3 iteration count recommendations (RFC 9276); test DNSSEC validation with `dig +dnssec +cd` before rollout |
| Feature flag rollout regression: `distributor-threads` increase causing MySQL connection exhaustion | After increasing `distributor-threads=8`, MySQL `max_connections` exhausted; PDNS SERVFAILs | `pdns_control backend-cmd gmysql-0 "SHOW STATUS LIKE 'Threads_connected';"` — near `max_connections` | `pdns_control set distributor-threads 2` (if supported at runtime); otherwise `systemctl restart pdns` with old config | Calculate MySQL connections needed: `distributor-threads × pdns-instances + overhead`; ensure MySQL `max_connections` can accommodate before increasing threads |

## Kernel/OS & Host-Level Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| OOM killer targets pdns_server process | All DNS resolution stops; clients receive SERVFAIL or timeout; `pdns_server` process gone | `dmesg -T | grep -i 'oom.*pdns'`; `journalctl -k --since "1h ago" | grep -i killed`; `systemctl status pdns` shows `inactive (dead)` | Complete authoritative DNS outage; all zones unresolvable; dependent services fail DNS lookups; secondary nameservers serve stale data until SOA expiry | Set `oom_score_adj=-1000` for pdns_server; add `OOMScoreAdjust=-1000` in `pdns.service`; tune `max-cache-entries` to cap memory usage; monitor RSS with `ps -o rss -p $(pgrep pdns_server)` |
| Inode exhaustion from AXFR zone transfer temp files | Zone transfers fail; `pdns.log` shows `Cannot create temporary file`; NOTIFY processing stops | `df -i /var/spool/pdns`; `find /tmp -name 'pdns*' -type f | wc -l`; `journalctl -u pdns | grep "temporary\|inode"` | Secondary nameservers cannot receive zone updates; stale zone data served; new zone additions fail | Clean orphaned temp files: `find /tmp -name 'pdns*' -mmin +60 -delete`; mount `/var/spool/pdns` on separate filesystem with higher inode count |
| CPU steal delays DNS query processing beyond resolver timeout | Clients receive SERVFAIL from recursors because authoritative response arrives after 2s resolver timeout | `sar -u 1 5 | grep steal`; `pdns_control get latency` — if > 500ms, steal is likely cause; `dig +time=5 @<pdns-ip> <zone> SOA` | Resolvers mark authoritative server as unresponsive; queries fail over to secondary; increased SERVFAIL rate from resolvers | Migrate to dedicated instance; increase recursor timeout for this authoritative: `forward-zones-recurse=<zone>=<pdns-ip>:53;timeout=5000` on recursor side |
| NTP clock skew breaks DNSSEC signature validation | DNSSEC-signed zones return BOGUS to validators; `pdnsutil check-zone <zone>` shows valid but external validators fail | `chronyc tracking | grep "System time"`; `dig +dnssec <zone> SOA @<pdns-ip>` — check RRSIG inception/expiration timestamps vs actual UTC | DNSSEC-validating resolvers reject all responses from this server; zones appear down to DNSSEC-aware clients | `chronyc makestep`; enable `chronyd`; verify: `pdnsutil check-zone <zone>`; re-sign zones: `pdnsutil rectify-all-zones`; alert on `abs(clock_skew_seconds) > 1` |
| File descriptor exhaustion under high QPS DDoS | PowerDNS stops accepting new UDP/TCP queries; existing processing continues; `pdns.log` shows `Too many open files` | `ls /proc/$(pgrep pdns_server)/fd | wc -l`; `ulimit -n`; `pdns_control get udp-queries` — compare with `pdns_control get latency` | DNS resolution capacity drops; legitimate queries queue or timeout; DDoS amplification risk if open resolver | Set `LimitNOFILE=65536` in `pdns.service`; `pdns_control set max-tcp-connections 500`; implement `allow-from` ACL to restrict query sources |
| TCP conntrack table full drops DNS-over-TCP and AXFR transfers | Zone transfers fail; `dig +tcp @<pdns-ip> <zone> AXFR` times out; UDP queries still work | `dmesg | grep "nf_conntrack: table full"`; `sysctl net.netfilter.nf_conntrack_count`; `ss -tn state established | grep ":53" | wc -l` | AXFR zone transfers to secondaries fail; DNS-over-TCP fallback for large responses broken; DNSSEC key rollover blocked | `sysctl -w net.netfilter.nf_conntrack_max=524288`; reduce AXFR concurrent connections: `pdns_control set max-tcp-connections 200`; use IXFR instead of AXFR where possible |
| Kernel UDP buffer overflow drops DNS queries silently | `pdns_control get udp-overruns` increasing; packet loss under high QPS; no error in pdns.log | `cat /proc/net/udp | awk '{print $7}' | sort -n | tail`; `ss -lunp | grep ":53"`; `pdns_control get udp-overruns` | DNS queries silently dropped by kernel before reaching PowerDNS; SERVFAIL rate increases; monitoring shows healthy server but clients report failures | `sysctl -w net.core.rmem_max=8388608 net.core.rmem_default=4194304`; set `udp-truncation-threshold=1232` in pdns.conf; enable `SO_REUSEPORT` with `reuseport=yes` |
| NUMA imbalance causes asymmetric DNS query latency | Some queries take 5-10x longer; `pdns_control get latency` shows high variance; packet capture shows uneven response times | `numastat -p $(pgrep pdns_server)`; `perf stat -e cache-misses -p $(pgrep pdns_server) sleep 5` | DNS latency variance causes resolver to prefer other authoritative servers; uneven load distribution across anycast nodes | Start pdns_server with `numactl --interleave=all`; or pin distributor threads to local NUMA node; set in systemd: `ExecStart=/usr/bin/numactl --interleave=all /usr/sbin/pdns_server` |

## Deployment Pipeline & GitOps Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| Zone file deployment via Git push breaks DNSSEC chain | Zone updated via GitOps; DNSSEC signatures not regenerated; validators return BOGUS | `pdnsutil check-zone <zone>` shows `Signature(s) have expired`; `dig +dnssec <zone> SOA @<pdns-ip>` — RRSIG expiry in past | DNSSEC-validating resolvers reject zone; effectively zone is down for DNSSEC-aware clients | Add post-deploy hook: `pdnsutil rectify-zone <zone> && pdnsutil increase-serial <zone>`; automate signing in CI pipeline |
| Helm chart deploys PowerDNS config before backend DB schema migration | PowerDNS starts with new config but old DB schema; queries return SERVFAIL; `pdns.log` shows SQL errors | `journalctl -u pdns | grep "SQL\|column\|schema"`; `pdns_control backend-cmd gmysql-0 "DESCRIBE records;"` | All DNS resolution fails; SERVFAIL for every query; dependent services cascade | Add init container that runs schema migration before PowerDNS starts; use Helm hooks: `pre-install` for DB migration, `post-install` for config |
| ArgoCD sync partially applies PowerDNS ConfigMap but not Secret | PowerDNS restarts with new settings but old backend DB password; backend connection fails; SERVFAIL | `argocd app diff <app>`; `kubectl get events -n <ns> | grep pdns`; `pdns_control backend-cmd gmysql-0 "SELECT 1;"` returns error | Complete DNS outage; all zones return SERVFAIL; no backend connectivity | Apply Secret and ConfigMap in same sync wave; use ArgoCD sync waves with Secret at wave 0; verify backend connectivity post-deploy |
| PDB blocks node drain during PowerDNS pod rescheduling | Node drain hangs; PowerDNS pod protected by PDB; cluster upgrade stalls; DNS still serving from this pod | `kubectl get pdb -n <ns> | grep pdns`; `kubectl describe pdb <pdns-pdb>`; `dig @<pod-ip> <zone> SOA` | Node maintenance blocked; if forced, DNS pod evicted without graceful handoff; queries in flight dropped | Set PDB `maxUnavailable=1`; ensure secondary nameservers are healthy before draining: `dig @<secondary-ip> <zone> SOA`; use anti-affinity to spread pods |
| Blue-green cutover fails: new PowerDNS version has incompatible zone format | Green deployment starts but returns NXDOMAIN for zones; blue still serving correctly | `dig @<green-pod-ip> <zone> SOA` returns NXDOMAIN; `kubectl logs -l version=green -n <ns> | grep -i "zone\|error"` | DNS resolution fails if traffic shifted to green; NXDOMAIN breaks all dependent services | Test zone loading in staging: `pdnsutil list-zones` on green; verify zone count matches blue; validate with `pdnsutil check-all-zones` before cutover |
| ConfigMap drift: PowerDNS recursor forward zones outdated | Recursor forwards queries to decommissioned upstream; SERVFAIL for specific zones | `kubectl get configmap <pdns-recursor-config> -o yaml | grep forward`; `rec_control get-forwards` | Specific domain queries fail; not all zones affected; intermittent failures as recursor tries multiple forwarders | `rec_control add-forward <zone>=<new-upstream>`; update ConfigMap in Git; enable ArgoCD self-heal to prevent drift |
| Secret rotation changes PDNS backend DB password; PowerDNS not reloaded | PowerDNS backend connections fail on reconnect; gradually all queries start returning SERVFAIL | `pdns_control backend-cmd gmysql-0 "SELECT 1;"` — returns auth error; `journalctl -u pdns | grep "Access denied"` | DNS resolution degrades as backend connections die; complete outage when all connections have recycled | `systemctl reload pdns` after secret rotation; use stakater/Reloader in Kubernetes; implement dual-password transition in MySQL backend |
| DNS zone import CI job fails due to zone file syntax error | `pdnsutil load-zone <zone> <file>` fails; zone not updated; stale records served | `pdnsutil check-zone <zone>`; `named-checkzone <zone> <file>` for pre-validation; CI job exit code non-zero | Zone stuck at old version; records not updated; stale data served potentially indefinitely | Add `named-checkzone` or `pdnsutil check-zone` validation step before `load-zone` in CI; fail pipeline on syntax error; alert on zone serial not advancing |

## Service Mesh & API Gateway Edge Cases
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| Envoy sidecar intercepts DNS UDP traffic causing resolution failure | PowerDNS pod receives no queries; Envoy captures port 53 traffic; all DNS resolution via this pod fails | `istioctl proxy-config listener <pod> | grep 53`; `kubectl logs <pod> -c istio-proxy | grep "udp\|dns"`; `dig @<pod-ip> <zone> SOA` times out | Complete DNS outage for this pod; mesh captures DNS traffic intended for authoritative server | Exclude port 53 from Istio sidecar: add `traffic.sidecar.istio.io/excludeInboundPorts: "53"` and `excludeOutboundPorts: "53"` annotation to pod |
| Rate limiting on API gateway blocks DNS management API | DNS record updates via REST API rate-limited; zone changes delayed; TTL expires before update lands | `kubectl logs -l app=api-gateway | grep "429.*dns\|429.*zone"`; `pdns_control show <zone> | grep "Serial"` — serial not advancing | DNS record updates delayed; stale records served beyond intended TTL; service discovery updates lag behind deployments | Exempt DNS management API paths from rate limiting; route zone management through dedicated ingress; use direct `pdnsutil` CLI for emergency updates |
| Stale service discovery returns decommissioned PowerDNS pod IP | Clients send queries to terminated pod IP; queries timeout; healthy pods receive reduced traffic | `kubectl get endpoints <pdns-svc>`; `dig @<stale-ip> <zone> SOA` — timeout; `nslookup <pdns-svc>.<ns>.svc.cluster.local` | Fraction of DNS queries fail; clients experience intermittent SERVFAIL; resolver marks this IP as slow | Set `publishNotReadyAddresses: false`; reduce `spec.publishNotReadyAddresses` on Service; add readiness probe: `dig @localhost <zone> SOA` |
| mTLS rotation breaks DNS-over-TLS (DoT) endpoint | DNS-over-TLS clients receive TLS handshake failure; fall back to plain DNS or fail completely | `openssl s_client -connect <pdns-ip>:853 2>&1 | grep "verify error"`; `istioctl proxy-config secret <pod>` | DoT clients cannot resolve; privacy-sensitive clients lose DNS service; monitoring using DoT shows false outage | Configure separate TLS cert for DoT port 853 outside mesh mTLS; use `cert-manager` with dedicated issuer for DNS TLS; pin certificate rotation to maintenance window |
| Retry storm from mesh amplifies DNS query load during attack | DDoS on DNS; mesh retries failed queries; PowerDNS receives 3x actual query volume; CPU saturated | `pdns_control get udp-queries`; `pdns_control get latency`; `istioctl proxy-config route <pod> | grep retries` | DNS server overwhelmed by combination of attack traffic and mesh retries; legitimate queries dropped | Disable mesh retries for DNS upstream; implement DNS-level rate limiting: `pdns_control set max-qps-per-ip 100`; add response rate limiting (RRL) in pdns.conf |
| gRPC-based DNS management service disrupted by mesh keepalive | gRPC management API for zone CRUD drops connections with GOAWAY; zone update operations timeout | `kubectl logs <pod> -c istio-proxy | grep "GOAWAY\|keepalive"`; `grpcurl -d '{}' <pdns-mgmt>:8081 dns.ZoneService/ListZones` fails intermittently | Cannot manage zones via API; automated record updates fail; service discovery DNS records go stale | Set `http2_protocol_options.connection_keepalive.interval=30s` in Envoy config; increase gRPC client keepalive timeout; add gRPC retry policy |
| Trace context lost in DNS query/response (no HTTP layer) | Cannot trace DNS resolution path in distributed tracing; DNS latency invisible in trace waterfall | Check Jaeger for gaps between client DNS lookup and service connection; `pdns_control get latency` shows high but traces show no DNS span | DNS latency not attributed in traces; SLO reporting misses DNS contribution; debugging requires separate DNS monitoring | Implement DNS tap with dnstap-enabled PowerDNS; correlate dnstap output with trace IDs via timestamp matching; export as synthetic spans to Jaeger |
| API gateway health check queries PowerDNS directly; false healthy during backend DB failure | Gateway marks PowerDNS healthy (responds to `dig SOA`); but all record queries return stale data from cache | `pdns_control backend-cmd gmysql-0 "SELECT 1;"` — fails; but `dig @<pdns-ip> <cached-zone> SOA` succeeds from cache | Gateway routes traffic to unhealthy PowerDNS; stale cached records served; new/updated records missing; silent data staleness | Use deep health check: `dig @<pdns-ip> _health.<zone> TXT` for a record that bypasses cache; or health-check the backend DB directly; set `query-cache-ttl=0` for health zone |
