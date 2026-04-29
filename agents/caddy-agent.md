---
name: caddy-agent
description: >
  Caddy Server specialist agent. Handles automatic HTTPS, reverse proxy,
  certificate management, Caddyfile configuration, and admin API operations.
model: haiku
color: "#22B638"
skills:
  - caddy/caddy
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-caddy-agent
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

You are the Caddy Agent — the automatic HTTPS and reverse proxy expert. When any
alert involves Caddy (certificate failures, upstream issues, config errors), you
are dispatched.

# Activation Triggers

- Alert tags contain `caddy`, `reverse_proxy`, `tls`, `acme`
- Certificate provisioning or renewal failures
- Reverse proxy upstream health failures
- 502/503 error spikes
- Configuration load errors

# Prometheus Metrics Reference

Caddy exposes Prometheus metrics via the `caddy.metrics` module (enabled by
default since Caddy 2.5.0+, or explicitly with `metrics` directive in
Caddyfile). Default metrics port is 2019 (admin API) at `/metrics`, or a
dedicated listener if configured.

Note: Caddy does not have an official Prometheus exporters list equivalent to
HAProxy/Envoy. Metrics are emitted by the Go runtime and Caddy's internal
instrumentation. All metric names use `caddy_` prefix.

## HTTP Request Metrics

| Metric | Type | Labels | Alert Threshold |
|--------|------|--------|-----------------|
| `caddy_http_requests_in_flight` | Gauge | `server`, `handler` | Sustained spike → WARNING |
| `caddy_http_request_errors_total` | Counter | `server`, `handler` | rate > 0 → WARNING |
| `caddy_http_request_duration_seconds` | Histogram | `server`, `handler` | p99 > 0.5 s → WARNING; > 2 s → CRITICAL |
| `caddy_http_request_size_bytes` | Histogram | `server`, `handler` | Informational |
| `caddy_http_response_size_bytes` | Histogram | `server`, `handler` | Informational |

Note: Caddy's metrics are handler-scoped and do not expose per-status-code
counters natively. For detailed per-code monitoring, enable structured JSON
access logging and parse `status` field via log pipeline.

## Reverse Proxy Upstream Metrics

Exposed via the admin API `/reverse_proxy/upstreams` (not Prometheus):

| Field | Type | Alert Threshold |
|-------|------|-----------------|
| `healthy` | bool | `false` → CRITICAL |
| `fails` | int | > 0 → WARNING |
| `num_requests` | int | Sustained high → saturation |
| `consecutive_fails` | int | > 3 → WARNING |

## TLS / ACME Metrics

No native Prometheus metric for cert expiry — use blackbox exporter or
`probe_ssl_earliest_cert_expiry`. Caddy's cert storage is at
`/var/lib/caddy/.local/share/caddy/certificates/`.

| Prometheus Metric (via blackbox exporter) | Alert Threshold |
|------------------------------------------|-----------------|
| `probe_ssl_earliest_cert_expiry` | < now+604800 (7d) → WARNING; < now+259200 (3d) → CRITICAL |
| `probe_success{job="caddy-https"}` | == 0 → CRITICAL |

## Go Runtime Metrics (from `/metrics`)

| Metric | Type | Alert Threshold |
|--------|------|-----------------|
| `go_goroutines` | Gauge | Unbounded growth → goroutine leak WARNING |
| `go_memstats_alloc_bytes` | Gauge | > 1 GB → WARNING |
| `process_open_fds` | Gauge | > 80% of `process_max_fds` → WARNING |
| `process_max_fds` | Gauge | Reference limit |

## PromQL Alert Expressions

```promql
# --- Request Error Rate ---
# WARNING: any handler errors
rate(caddy_http_request_errors_total[5m]) > 0.01

# CRITICAL: sustained error rate
rate(caddy_http_request_errors_total[5m]) > 1

# --- p99 Request Latency ---
# WARNING: p99 > 500 ms
histogram_quantile(0.99,
  sum by (server, handler, le) (
    rate(caddy_http_request_duration_seconds_bucket[5m])
  )
) > 0.5

# CRITICAL: p99 > 2 s
histogram_quantile(0.99,
  sum by (server, handler, le) (
    rate(caddy_http_request_duration_seconds_bucket[5m])
  )
) > 2

# --- Requests In Flight Spike ---
# WARNING: sudden spike in concurrent requests (compare to baseline)
caddy_http_requests_in_flight > 1000

# --- TLS Cert Expiry (via blackbox exporter) ---
# CRITICAL: cert expiry < 3 days
(probe_ssl_earliest_cert_expiry{job="caddy-tls"} - time()) < 259200

# WARNING: cert expiry < 7 days
(probe_ssl_earliest_cert_expiry{job="caddy-tls"} - time()) < 604800

# --- File Descriptor Exhaustion ---
# WARNING: open fds > 80% of max
(process_open_fds{job="caddy"} / process_max_fds{job="caddy"}) > 0.80

# --- Goroutine Leak ---
# WARNING: goroutines growing over time
deriv(go_goroutines{job="caddy"}[1h]) > 10

# --- HTTPS Probe Failure ---
# CRITICAL: endpoint not responding via HTTPS
probe_success{job="caddy-https"} == 0
```

# Cluster/Service Visibility

Quick commands for immediate health overview:

```bash
# Caddy admin API (default localhost:2019)
ADMIN=http://localhost:2019

# Caddy version and status
caddy version
curl -s $ADMIN/config/ | jq 'keys'

# Process health
systemctl status caddy 2>/dev/null
ps aux | grep caddy | grep -v grep

# Reverse proxy upstream health (primary health signal)
curl -s "$ADMIN/reverse_proxy/upstreams" | jq '.[] | {dial, healthy, num_requests, fails, consecutive_fails}'

# Unhealthy upstreams only
curl -s "$ADMIN/reverse_proxy/upstreams" | jq '.[] | select(.healthy == false or .fails > 0)'

# All configured apps
curl -s $ADMIN/config/apps | jq 'keys'

# TLS automation policies
curl -s $ADMIN/config/apps/tls | jq '{policies: .automation.policies[].subjects, on_demand: .automation.on_demand}'

# Prometheus metrics
curl -s http://localhost:2019/metrics | grep -E "caddy_http_requests_in_flight|caddy_http_request_errors_total|caddy_http_request_duration" | head -20

# Certificate expiry on disk
find /var/lib/caddy/.local/share/caddy/certificates -name "*.crt" | while read f; do
  domain=$(basename $(dirname $f))
  expiry=$(openssl x509 -noout -enddate -in "$f" 2>/dev/null | cut -d= -f2)
  echo "$domain: $expiry"
done
```

# Global Diagnosis Protocol

**Step 1 — Is Caddy itself healthy?**
```bash
caddy validate --config /etc/caddy/Caddyfile 2>&1 | tail -5
systemctl is-active caddy
curl -sf http://localhost:2019/config/ > /dev/null && echo "ADMIN OK" || echo "ADMIN DOWN"
# Check for process restarts
journalctl -u caddy --since "1 hour ago" | grep -E "Started|start|stop|killed" | tail -10
```

**Step 2 — Backend health status**
```bash
curl -s http://localhost:2019/reverse_proxy/upstreams | \
  jq '.[] | select(.healthy == false or .fails > 0)'
# Count healthy vs total upstreams
curl -s http://localhost:2019/reverse_proxy/upstreams | \
  jq 'group_by(.healthy) | map({healthy: .[0].healthy, count: length})'
# Current load on each upstream
curl -s http://localhost:2019/reverse_proxy/upstreams | \
  jq '.[] | {dial, healthy, active_requests: .num_requests}'
```

**Step 3 — Traffic metrics**
```bash
# Error rate from Prometheus
curl -s http://localhost:2019/metrics | grep "caddy_http_request_errors_total"
# Latency histogram
curl -s http://localhost:2019/metrics | grep "caddy_http_request_duration_seconds_bucket" | tail -20
# JSON access log (5xx)
journalctl -u caddy --since "5 minutes ago" | \
  jq -r 'select(.status >= 500) | "\(.ts) \(.status) \(.request.uri)"' 2>/dev/null | tail -20
grep '"status":5[0-9][0-9]' /var/log/caddy/access.log 2>/dev/null | tail -20
```

**Step 4 — Configuration validation**
```bash
caddy validate --config /etc/caddy/Caddyfile
caddy adapt --config /etc/caddy/Caddyfile 2>&1 | head -20
curl -s http://localhost:2019/config/ | jq '.apps.http.servers | keys'
```

**Output severity:**
- CRITICAL: caddy process down, all upstreams unhealthy, cert provisioning blocked, config load failed
- WARNING: some upstreams failing, cert expiring < 7 days, ACME challenge failures, error rate > 1%
- OK: upstreams healthy, certs auto-renewing, config valid

# Diagnostic Scenarios

---

### Scenario 1: High 502/503 Error Rate — Upstream Failure

**Symptoms:** 502/503 responses to clients; `healthy == false` for upstreams; dial failures in Caddy log

**Triage with Prometheus:**
```promql
# Error rate spiking
rate(caddy_http_request_errors_total[5m]) > 0.1

# p99 latency degraded (upstream slow before failing)
histogram_quantile(0.99,
  sum by (server, le) (
    rate(caddy_http_request_duration_seconds_bucket[5m])
  )
) > 2

# HTTPS probe failing
probe_success{job="caddy-https"} == 0
```

### Scenario 2: ACME / Let's Encrypt Certificate Provisioning Failure

**Symptoms:** ACME challenge errors in log; cert not auto-renewed; TLS clients see expired cert; `probe_ssl_earliest_cert_expiry` firing

**Triage:**
```promql
# Cert expiry < 7 days
(probe_ssl_earliest_cert_expiry{job="caddy-tls"} - time()) < 604800

# HTTP probe to caddy failing
probe_success{job="caddy-https"} == 0
```

### Scenario 3: Caddyfile / JSON Config Load Failure

**Symptoms:** Config reload failing; Caddy serving stale config; syntax error in Caddyfile after update

### Scenario 4: On-Demand TLS Abuse / ACME Rate Limit Exhaustion

**Symptoms:** ACME rate limit errors (`429` / "too many certificates"); unexpected domains getting certs; storage growing unbounded

### Scenario 5: Upstream Connection Exhaustion / High Latency

**Symptoms:** Requests timing out; high `caddy_http_requests_in_flight`; `process_open_fds` approaching limit; `num_requests` high on all upstreams

**Triage with Prometheus:**
```promql
# In-flight requests spike
caddy_http_requests_in_flight > 500

# p99 latency > 2 s (upstream slow)
histogram_quantile(0.99,
  sum by (server, le) (
    rate(caddy_http_request_duration_seconds_bucket[5m])
  )
) > 2

# File descriptor usage > 80%
(process_open_fds{job="caddy"} / process_max_fds{job="caddy"}) > 0.80
```

### Scenario 6: ACME HTTP-01 Challenge Failure Due to Firewall Blocking Port 80

**Symptoms:** Certificate provisioning fails with `acme: error: 400 :: urn:ietf:params:acme:error:connection`; Caddy logs show `solving challenge` then failure; TLS cert not issued or renewed; `probe_ssl_earliest_cert_expiry` firing.

**Root Cause Decision Tree:**
- Firewall/security group blocking inbound TCP port 80 from ACME CA validation servers
- Caddy not listening on port 80 (missing `http://` block or `auto_https` redirects disabled)
- Another process (Apache, Nginx) bound to port 80, preventing Caddy from answering the challenge
- CDN or load balancer in front of Caddy intercepting port 80 traffic before it reaches Caddy
- DNS not yet propagated for the domain — ACME CA cannot resolve domain to Caddy's IP

**Diagnosis:**
```bash
# 1. Confirm port 80 is bound by Caddy
ss -tlnp | grep ':80'
# Expected output includes Caddy's PID

# 2. Check ACME challenge errors in Caddy log
journalctl -u caddy | grep -iE "acme|challenge|http-01|solving|error" | tail -30

# 3. Test HTTP-01 challenge path reachability from external network
curl -v http://<your_domain>/.well-known/acme-challenge/connectivity-test
# From a remote machine, not localhost

# 4. Check firewall rules
iptables -L INPUT -n | grep -E "80|dport 80"
# Cloud: check security group / NSG rules allow 0.0.0.0/0:80 inbound

# 5. Check DNS resolution matches Caddy's IP
dig +short <your_domain>
curl -s https://api.ipify.org  # Caddy host public IP

# 6. Verify no other process holds port 80
fuser -n tcp 80
lsof -i TCP:80

# 7. If behind a load balancer, confirm LB forwards port 80 to Caddy
# Some LBs terminate HTTP and never forward .well-known paths
```

**Thresholds:**
- CRITICAL: `probe_ssl_earliest_cert_expiry < now + 3d` and ACME challenge failing
- WARNING: Any ACME challenge failure (cert will fail to renew before expiry)

### Scenario 7: Reverse Proxy Buffering Causing Large Upload Timeout

**Symptoms:** Large file uploads (> 100 MB) time out mid-transfer; clients receive 502/504 during upload; `caddy_http_request_duration_seconds` p99 spikes for specific handlers; small requests succeed normally; upstream service never receives the full request body.

**Root Cause Decision Tree:**
- Caddy default `read_timeout` or `write_timeout` too short for large upload over slow connection
- `request_body.max_size` directive rejecting body before upstream receives it
- Upstream service has its own request body size limit (Nginx `client_max_body_size`)
- Network bandwidth between Caddy and upstream is a bottleneck during buffering
- Transport `response_header_timeout` fires while upstream is processing the large body

**Diagnosis:**
```bash
# 1. Check current timeout configuration
curl -s http://localhost:2019/config/ | jq '
  .apps.http.servers[] |
  {read_header_timeout, idle_timeout, timeouts: .timeouts,
   routes: [.routes[].handle[]? | select(.handler=="reverse_proxy") | .transport]}'

# 2. Reproduce with timed upload
time curl -X POST -F "file=@/tmp/100mb_test.bin" https://<domain>/upload -v 2>&1 | tail -20

# 3. Check Caddy error log during upload
journalctl -u caddy -f &
# Trigger the upload in another terminal
# Look for: context deadline exceeded, write: broken pipe, request body too large

# 4. Check max body size setting
curl -s http://localhost:2019/config/ | jq '.. | .max_size? | select(. != null)'

# 5. Check upstream response (did upstream receive and reject large body?)
journalctl -u caddy | grep -E "413|too large|max_size|body" | tail -10

# 6. Monitor in-flight requests during upload
watch -n1 'curl -s http://localhost:2019/metrics | grep caddy_http_requests_in_flight'
```

**Thresholds:**
- WARNING: `caddy_http_request_duration_seconds` p99 > 30s for upload handlers
- CRITICAL: Upload requests consistently failing with 502/504

### Scenario 8: Access Log Format Misconfiguration Causing Log Parsing Failure

**Symptoms:** Log aggregation pipeline (Loki, Splunk, ELK) stops ingesting Caddy logs; parsing errors in log shipper; log dashboards show no data despite Caddy being healthy; `jq` on log entries fails with parse error.

**Root Cause Decision Tree:**
- Caddyfile `log format` changed from `json` to `console` (human-readable, not machine-parseable)
- Custom log format template using `{placeholders}` emitting invalid JSON (unescaped characters in URI)
- Log file rotated but log shipper still reading old inode (stale file handle)
- Log output directed to `stderr` instead of a file after config reload — log shipper monitoring wrong path
- Multiple `log` directives producing duplicate/interleaved entries breaking line-delimited JSON parsing

**Diagnosis:**
```bash
# 1. Check current log configuration
curl -s http://localhost:2019/config/ | jq '.logging.logs'
# Or in Caddyfile
grep -A10 "^log " /etc/caddy/Caddyfile

# 2. Validate log output format
journalctl -u caddy -n 5 | head -3 | jq '.' 2>&1
# If jq fails: log is not valid JSON

# 3. Check log file path and recent entries
ls -la /var/log/caddy/
tail -3 /var/log/caddy/access.log | jq '.' 2>&1

# 4. Verify log file is being written to (not stderr)
ls -la /proc/$(pgrep caddy)/fd | grep "/var/log/caddy"

# 5. Check for interleaved/duplicate log entries
grep -c "^{" /var/log/caddy/access.log
wc -l /var/log/caddy/access.log
# If grep count << wc count: non-JSON lines present

# 6. Test log shipper file handle
lsof +D /var/log/caddy/  # Check which process has the log file open
```

**Thresholds:**
- WARNING: Any non-JSON lines in access log when `format json` is configured
- CRITICAL: Log ingestion broken — observability gap for compliance/security

### Scenario 9: Header Manipulation Causing Downstream Authentication Failure

**Symptoms:** Authenticated requests from upstream services fail with 401 after passing through Caddy reverse proxy; `Authorization` or `X-API-Key` headers missing at upstream; JWT tokens corrupted; CORS preflight requests fail.

**Root Cause Decision Tree:**
- `header` directive stripping `Authorization` header (e.g., `header -Authorization`)
- `header_up` in `reverse_proxy` block deleting or overwriting auth headers before forwarding
- `encode` or `rewrite` directive mangling base64-encoded tokens containing special characters
- Caddy adding `X-Forwarded-For` which upstream validates against an allowlist, but Caddy's IP not whitelisted
- CORS `header` directives setting `Access-Control-Allow-Origin` too restrictively, blocking browser auth flows

**Diagnosis:**
```bash
# 1. Add a debug upstream to echo headers
# Temporarily route to httpbin or echoserver to see what Caddy forwards
# In Caddyfile: reverse_proxy /debug/* httpbin.org

# 2. Check header manipulation directives in config
curl -s http://localhost:2019/config/ | jq '
  .apps.http.servers[].routes[].handle[]? |
  select(.handler == "headers" or .handler == "reverse_proxy") |
  {handler, request: .request, response: .response, upstreams: .upstreams}'

# 3. Test header pass-through manually
curl -v -H "Authorization: Bearer test-token" http://localhost/api/test 2>&1 \
  | grep -E "Authorization|X-Api|< HTTP"

# 4. Check Caddyfile for header directives
grep -n "header\|header_up\|header_down" /etc/caddy/Caddyfile

# 5. Inspect Caddy error log for header-related errors
journalctl -u caddy | grep -iE "header|auth|forbidden|401|403" | tail -20

# 6. Check if hop-by-hop headers are being stripped (Connection, Upgrade)
# These are stripped by default per HTTP/1.1 spec — expected behavior
```

**Thresholds:**
- CRITICAL: Authentication systematically broken for all requests through Caddy
- WARNING: Intermittent auth failures correlating with specific header patterns

### Scenario 10: Caddy Process OOM from Large Number of TLS Sessions

**Symptoms:** Caddy process killed by OOM killer; `go_memstats_alloc_bytes` growing unbounded; many concurrent TLS connections from clients; `dmesg` shows `oom-kill` for Caddy PID; service restarts with cold TLS session cache.

**Root Cause Decision Tree:**
- High TLS session count with large `ssl_session_cache` consuming heap (each session ~4 KB)
- On-demand TLS issuing certificates for thousands of domains — each cert stored in memory
- Goroutine leak: each stalled TLS handshake holds a goroutine; `go_goroutines` climbing unbounded
- Large number of reverse proxy upstreams each maintaining keep-alive connection pools
- `go_memstats_alloc_bytes` growing due to large log buffer if log output is backed up

**Diagnosis:**
```bash
# 1. Check current memory usage
curl -s http://localhost:2019/metrics | grep -E "go_memstats_alloc_bytes|go_memstats_sys_bytes|go_goroutines"

# 2. Check OOM in kernel log
dmesg | grep -i "oom\|killed process" | grep -i caddy | tail -10
journalctl -k | grep -i "oom" | tail -10

# 3. Goroutine count trend (growing = leak)
for i in {1..5}; do
  curl -s http://localhost:2019/metrics | grep go_goroutines
  sleep 10
done

# 4. Count active TLS sessions
# If Caddy's pprof is enabled (for debug builds):
curl -s http://localhost:2019/debug/pprof/heap > /tmp/caddy_heap.pprof
go tool pprof -top /tmp/caddy_heap.pprof | head -20

# 5. Count managed certificates
find /var/lib/caddy/.local/share/caddy/certificates -name "*.crt" | wc -l

# 6. Check process RSS vs limits
cat /proc/$(pgrep caddy)/status | grep -E "VmRSS|VmPeak|VmSwap"
cat /sys/fs/cgroup/memory/system.slice/caddy.service/memory.max_usage_in_bytes 2>/dev/null
```

**Thresholds:**
- WARNING: `go_memstats_alloc_bytes > 1 GB`
- WARNING: `go_goroutines` increasing > 10/hour (`deriv(go_goroutines[1h]) > 10`)
- CRITICAL: OOM kill event for Caddy process

### Scenario 11: Dynamic DNS / On-Demand TLS Certificate Storm

**Symptoms:** Let's Encrypt rate limit errors (`429 urn:ietf:params:acme:error:rateLimited`); certificate storage growing rapidly; Caddy log flooded with `obtaining certificate` messages; legitimate domains failing to get certificates.

**Root Cause Decision Tree:**
- On-demand TLS enabled without an `ask` endpoint — any domain gets a certificate attempt
- Scanning bots or DNS wildcard pointing thousands of subdomains to Caddy's IP
- Rapid deployment cycles creating many subdomains, each triggering a new cert request
- Shared `ask` endpoint returning 200 for too broad a set of domains
- Certificate `duplicate-certificate` rate limit: > 5 certs for same set of SANs in 7 days

**Diagnosis:**
```bash
# 1. Check on-demand TLS config and ask endpoint
curl -s http://localhost:2019/config/apps/tls | jq '.automation.on_demand'

# 2. Count certificates currently stored
find /var/lib/caddy/.local/share/caddy/certificates -name "*.crt" | wc -l
# List domains with most recently issued certs
find /var/lib/caddy/.local/share/caddy/certificates -name "*.crt" \
  -newer /tmp/1h_ago -exec openssl x509 -noout -subject -in {} \; 2>/dev/null \
  | sort | uniq -c | sort -rn | head -20

# 3. Check rate limit errors in log
journalctl -u caddy | grep -iE "rate.limit|too many certificate|rateLimited|429" | tail -20

# 4. Count cert requests per minute (from log timestamps)
journalctl -u caddy | grep "obtaining certificate" \
  | awk '{print $1, $2}' | cut -c1-16 | sort | uniq -c | sort -rn | head -10

# 5. Check ask endpoint (is it too permissive?)
curl -s "http://localhost:5555/check?domain=notmydomain.example.com"
# Should return non-2xx for unauthorized domains
```

**Thresholds:**
- CRITICAL: Let's Encrypt rate limit hit — no new certs possible for up to 1 week
- WARNING: Cert issuance rate > 10/hour (approaching limits)

### Scenario 12: Caddyfile Syntax Error Causing Silent Reload Failure

**Symptoms:** Config change deployed but old behavior persists; `caddy reload` exits 0 but changes not applied; or `caddy reload` exits non-zero with no user notification; admin API still serves old config JSON.

**Root Cause Decision Tree:**
- `caddy reload` silently swallowed error (piped in CI without checking exit code)
- Caddyfile has duplicate site block for same address — Caddy rejects config silently in some versions
- Environment variable referenced in Caddyfile (`{$MY_VAR}`) is not set — directive silently ignored
- JSON config loaded via admin API `POST /load` returned 4xx but CI pipeline ignored HTTP status
- Config file byte-order mark (BOM) or Windows line endings causing parse error

**Diagnosis:**
```bash
# 1. Validate before reloading (always)
caddy validate --config /etc/caddy/Caddyfile
echo "Exit code: $?"
# Non-zero = invalid config

# 2. Adapt to JSON to see fully parsed config
caddy adapt --config /etc/caddy/Caddyfile 2>&1 | python3 -m json.tool > /dev/null
echo "Adapt exit: $?"

# 3. Compare running config to expected
curl -s http://localhost:2019/config/ | jq '.apps.http.servers | keys'
# Should show servers matching your Caddyfile site blocks

# 4. Check for environment variable substitution
env | grep -E "CADDY_|MY_VAR|DOMAIN"
grep '{$' /etc/caddy/Caddyfile

# 5. Check file encoding
file /etc/caddy/Caddyfile
hexdump -C /etc/caddy/Caddyfile | head -2  # Check for BOM: EF BB BF at start

# 6. Check journald for reload errors
journalctl -u caddy --since "5 minutes ago" | grep -iE "error|fail|invalid|config" | tail -20

# 7. Force reload via admin API and check HTTP response code
caddy adapt --config /etc/caddy/Caddyfile 2>/dev/null \
  | curl -sf -XPOST http://localhost:2019/load -H "Content-Type: application/json" -d @-
echo "HTTP reload status: $?"
```

**Thresholds:**
- CRITICAL: Running config diverges from file config without operator awareness
- WARNING: Any `caddy validate` non-zero exit in CI pipeline

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `dial tcp xxx: connect: connection refused` | Upstream backend is down or not listening on expected port | `curl -v http://<backend-host>:<port>/health` |
| `tls: failed to verify certificate` | Upstream TLS certificate untrusted or self-signed without explicit trust | `openssl s_client -connect <upstream>:443 -showcerts` |
| `context deadline exceeded` | Upstream response time exceeds Caddy's timeout | `curl -w "%{time_total}" -o /dev/null -s http://<upstream>/` |
| `no certificate available for xxx` | ACME certificate not yet issued or failed to renew | `caddy certificate list` |
| `acme: error: 400 :: urn:ietf:params:acme:error:dns :: DNS problem` | DNS-01 ACME challenge failed; wrong DNS provider credentials or propagation delay | `dig TXT _acme-challenge.<domain>` |
| `too many simultaneous ACME challenges` | ACME rate limit hit; too many concurrent certificate requests | `grep -c 'acme' /var/log/caddy/caddy.log` |
| `permission denied: listening on :443` | Caddy lacks `CAP_NET_BIND_SERVICE` or is not running as root | `getcap $(which caddy)` |
| `Error during TLS handshake: EOF` | Client disconnected mid-handshake; TLS version or cipher mismatch | `openssl s_client -connect <host>:443 -tls1_2` |
| `no upstreams available` | All backends in a load-balance group are unhealthy | `caddy adapt --config /etc/caddy/Caddyfile && journalctl -u caddy -n 50` |
| `failed to get certificate: ACME server responded with error code 429` | Let's Encrypt rate limit exceeded (5 certs per domain per week) | `curl https://crt.sh/?q=<domain>&output=json \| jq '.[].not_before' \| head` |

# Capabilities

1. **Automatic HTTPS** — Certificate provisioning, renewal, on-demand TLS
2. **Reverse proxy** — Upstream management, load balancing, health checks
3. **Configuration** — Caddyfile validation, JSON API config updates
4. **Admin API** — Runtime config inspection and modification
5. **File serving** — Static file configuration, compression

# Critical Metrics to Check First

| Priority | Metric | CRITICAL | WARNING |
|----------|--------|----------|---------|
| 1 | Admin API `/reverse_proxy/upstreams` healthy | All `false` | Any `false` |
| 2 | `caddy_http_request_errors_total` rate | > 1/s | > 0.01/s |
| 3 | `probe_ssl_earliest_cert_expiry` | < 3 days | < 7 days |
| 4 | `caddy_http_request_duration_seconds` p99 | > 2 s | > 0.5 s |
| 5 | `process_open_fds` / `process_max_fds` | > 90% | > 80% |

# Output

Standard diagnosis/mitigation format. Always include: admin API output,
Caddy logs, Prometheus metric values, and recommended Caddyfile changes.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| ACME certificate renewal failing silently | Port 80 blocked by a security group or host firewall rule added during a recent infra change — HTTP-01 challenge cannot be completed | `curl -v http://<domain>/.well-known/acme-challenge/test` from an external network; check `iptables -L INPUT -n \| grep 80` |
| All upstreams marked unhealthy by Caddy health checks | Upstream application crashed or OOM-killed — not a Caddy misconfiguration | `kubectl get pods -l app=<upstream-app> --field-selector=status.phase!=Running` or `systemctl status <upstream-service>` |
| `context deadline exceeded` errors spiking after a deploy | Downstream database connection pool saturated — upstream app is slow, not Caddy | `kubectl exec -it <app-pod> -- curl -s localhost:8080/metrics \| grep -E 'db_pool|connection'` |
| TLS handshake failures from clients | DNS TTL expired and old A record pointing to decommissioned IP — clients cannot complete TCP handshake with Caddy | `dig <domain> +short` and verify output matches the active Caddy host IP |
| `caddy_http_request_errors_total` rising only for one upstream route | Misconfigured reverse proxy header stripping — upstream app rejects requests missing `X-Forwarded-Proto` or `Host` | `curl -v -H "Host: <domain>" http://<upstream-ip>:<port>/health` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N upstream backends slow / unhealthy | Caddy active health checks mark it down but load balancer still sends retries; p99 latency elevated for ~1/N of requests | Fraction of users hit slow responses or 502s; hard to reproduce without sticky routing | `curl -s http://localhost:2019/reverse_proxy/upstreams \| jq '.[] \| {upstream: .dial, healthy: .healthy, fails: .fails}'` |
| 1 domain's certificate expired while others renewed | `probe_ssl_earliest_cert_expiry` low for one hostname; other vhosts healthy; Caddy logs show renewal errors only for that domain | HTTPS broken for that specific domain; all other domains unaffected | `caddy certificate list 2>/dev/null \| grep -E 'domain|expiry|managed'` and `openssl s_client -connect <failing-domain>:443 -servername <failing-domain> </dev/null 2>&1 \| grep -E 'notAfter|subject'` |
| 1 Caddyfile route matching incorrectly (shadowed by earlier rule) | A subset of URL paths returns wrong content or wrong upstream; other paths route correctly; no errors in logs | Specific API paths or static assets served from wrong backend without any log-level warning | `curl -s http://localhost:2019/config/ \| jq '.apps.http.servers[].routes[].match'` to inspect route order |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| TLS certificate expiry | < 30 days | < 7 days | `curl -v https://<host> 2>&1 | grep "expire date"` — or: `openssl s_client -connect <host>:443 -servername <host> </dev/null 2>&1 | grep notAfter` |
| HTTP request latency (p99) | > 500ms | > 2,000ms | `curl -s http://localhost:2019/metrics | grep 'caddy_http_request_duration_seconds{.*quantile="0.99"'` |
| HTTP 5xx error rate (% of total requests) | > 0.5% | > 2% | `curl -s http://localhost:2019/metrics | grep caddy_http_response_size_bytes_count` and compare to `grep 'caddy_http_request_errors_total'` |
| Active connections | > 5,000 | > 10,000 | `curl -s http://localhost:2019/metrics | grep caddy_http_active_requests` |
| Upstream health check failures (unhealthy backends) | > 1 backend down | > 50% of backends down | `curl -s http://localhost:2019/reverse_proxy/upstreams | jq '[.[] | select(.healthy == false)] | length'` |
| ACME certificate renewal errors (last 24 h) | > 1 renewal failure | > 3 renewal failures | `journalctl -u caddy --since "24 hours ago" | grep -ci 'certificate\|acme\|renew.*error\|failed'` |
| Goroutine count (memory/concurrency pressure) | > 5,000 | > 20,000 | `curl -s http://localhost:2019/metrics | grep go_goroutines` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Goroutine count (via `/debug/pprof/goroutine`) | Growing unboundedly under sustained load; approaching 50,000 goroutines per instance | Profile for goroutine leaks: `curl -s http://localhost:2019/debug/pprof/goroutine?debug=1 | head -100`; scale horizontally if legitimately connection-bound | 1–3 days |
| Active connections per upstream | Consistently at `max_conns` setting per upstream; upstream queuing observed in logs | Increase `max_conns` per upstream or add upstream instances; tune `keepalive` idle connection pool size in Caddy reverse_proxy config | 2–5 days |
| TLS certificate expiry | Any certificate within 30 days of expiry on non-ACME-managed certs | Switch to ACME auto-renewal; or renew manually and reload: `caddy reload --config /etc/caddy/Caddyfile` | 14–30 days |
| Disk usage for ACME certificate storage (`~/.local/share/caddy`) | Growing with large numbers of managed domains; approaching 90% of disk | Purge stale certificates for decommissioned domains; move certificate storage to a larger volume | 14–30 days |
| Request rate (RPS) per site | p95 RPS trending upward >20% month-over-month; approaching known single-instance throughput limit | Add Caddy instances behind a layer-4 load balancer; enable HTTP/3 to reduce connection overhead | 14–30 days |
| Upstream response time (p99) | Rising week-over-week even when Caddy CPU is low; upstream processing time increasing | Investigate upstream service capacity; add upstream instances; enable Caddy passive health checks with `fail_duration` to avoid routing to slow upstreams | 3–7 days |
| File descriptor usage (`/proc/$(pidof caddy)/fd`) | Approaching system `nofile` ulimit (default 1024 on some systems; should be 65535+) | Set `LimitNOFILE=65535` in the caddy systemd unit; verify: `cat /proc/$(pidof caddy)/limits | grep 'open files'` | 1–3 days |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check Caddy service status and last 20 log lines
systemctl status caddy --no-pager -l && journalctl -u caddy -n 20 --no-pager

# Validate current Caddyfile syntax before reloading
caddy validate --config /etc/caddy/Caddyfile

# Inspect the live running config via the admin API
curl -s http://localhost:2019/config/ | jq '.'

# Check TLS certificate status for a domain (expiry, issuer, SANs)
echo | openssl s_client -connect <domain>:443 -servername <domain> 2>/dev/null | openssl x509 -noout -dates -subject -issuer

# Show HTTP request error rate (5xx) from Prometheus metrics endpoint
curl -s http://localhost:2019/metrics | grep -E 'caddy_http_requests_total|caddy_http_request_errors_total' | grep -v '^#'

# Check ACME certificate storage for upcoming expirations (within 30 days)
find ~/.local/share/caddy/certificates -name '*.crt' -exec openssl x509 -noout -enddate -subject -in {} \; 2>/dev/null | paste - - | awk -F= '{print $2, $3}' | sort

# Check reverse proxy upstream health from Caddy metrics
curl -s http://localhost:2019/metrics | grep -E 'caddy_reverse_proxy_upstreams_healthy|caddy_reverse_proxy_upstream' | grep -v '^#'

# Tail Caddy access log for 4xx/5xx errors in the last minute
journalctl -u caddy --since "1 minute ago" --no-pager | grep -E '"status":(4|5)[0-9]{2}' | tail -30

# Check if admin API is only bound to localhost (security verification)
ss -tlnp | grep 2019

# Reload Caddy config without dropping connections and confirm success
caddy reload --config /etc/caddy/Caddyfile && curl -s -o /dev/null -w "%{http_code}" http://localhost:2019/config/
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| HTTP Request Success Rate (non-5xx) | 99.9% | `1 - (rate(caddy_http_request_errors_total[5m]) / rate(caddy_http_requests_total[5m]))`; Caddy Prometheus metrics | 43.8 min/month | Burn rate > 14.4× (>1% 5xx rate for 5 min) → page |
| TLS Certificate Validity (no expired certs serving traffic) | 99.95% | Synthetic probe: `echo \| openssl s_client -connect <domain>:443` returns valid cert with expiry > 0 days; or `caddy_tls_managed_certificate` gauge from Caddy metrics | 21.9 min/month | Any cert expiry ≤ 7 days without renewal in progress → page |
| Reverse Proxy Upstream Availability | 99.5% | `caddy_reverse_proxy_upstreams_healthy / caddy_reverse_proxy_upstreams_total`; Caddy built-in health check metrics | 3.6 hr/month | Burn rate > 6× (any upstream unhealthy for > 5 min) → alert |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| TLS — HTTPS enforced | `grep -E "redir|tls|http://" /etc/caddy/Caddyfile` | All HTTP listeners redirect to HTTPS; no plaintext-only virtual hosts in production |
| TLS — minimum version | `curl -s http://localhost:2019/config/ \| jq '.. \| objects \| select(.protocol?) \| .protocol'` | TLS 1.2 minimum; TLS 1.0 and 1.1 absent from cipher suite configuration |
| Automatic HTTPS / certificate management | `caddy environ && curl -s http://localhost:2019/pki/ca/local \| jq '.root_certificate \| length'` | ACME email configured; certificates managed automatically; no expired certs in `~/.local/share/caddy/certificates/` |
| Admin API network exposure | `ss -tlnp \| grep 2019` | Admin API bound to `127.0.0.1:2019` only; not reachable from external interfaces |
| Authentication on sensitive routes | `grep -E "basicauth|forward_auth|jwt" /etc/caddy/Caddyfile` | Authentication directive present on admin/internal routes; no unauthenticated exposure of management endpoints |
| Access controls — IP restrictions | `grep -E "remote_ip\|not remote_ip\|trusted_proxies" /etc/caddy/Caddyfile` | `trusted_proxies` set to known upstream proxy CIDRs only; not set to `private_ranges` unless behind a controlled load balancer |
| Resource limits — rate limiting | `grep -E "rate_limit\|limit_except" /etc/caddy/Caddyfile` | Rate limiting directives present for public endpoints; no unbounded request fan-out |
| Logging configuration | `grep -A10 "log" /etc/caddy/Caddyfile` | Access logs enabled with JSON format; log output directed to a persistent file or journald; log rotation configured |
| Backup — Caddyfile and certificates | `ls -lh /etc/caddy/ && ls -lh ~/.local/share/caddy/certificates/ 2>/dev/null \| head -10` | Caddyfile is version-controlled or backed up; certificate storage directory is included in backup schedule |
| Network exposure — open ports | `ss -tlnp \| grep caddy` | Caddy listens only on expected ports (80, 443, 2019); no unexpected bound addresses |
| HTTP P99 Response Latency ≤ 500 ms | 99% | `histogram_quantile(0.99, rate(caddy_http_request_duration_seconds_bucket[5m])) < 0.5`; Caddy Prometheus `caddy_http_request_duration_seconds` histogram | 7.3 hr/month | Burn rate > 3× (>1% requests exceed 500 ms in 1h) → alert |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `{"level":"error","msg":"obtaining certificate","error":"acme: error 403: urn:ietf:params:acme:error:unauthorized"}` | Critical | ACME challenge failed; DNS not yet propagated or port 80 blocked by firewall | Verify DNS A record points to server; open port 80; check CAA records |
| `{"level":"warn","msg":"certificate expires soon","expiry":"...","remaining":"72h0m0s"}` | High | TLS certificate near expiry; ACME renewal failing | Check `acme_challenges` endpoint reachability; review renewal error logs; force renew with `caddy reload` |
| `{"level":"error","msg":"no upstreams available","upstream":"backend:8080"}` | Critical | All reverse proxy backends are down or health-check failing | Verify backend service is running; check `lb_policy` and health check config in Caddyfile |
| `{"level":"error","msg":"dial tcp ... connection refused"}` | High | Caddy cannot reach the upstream backend socket | Confirm backend is listening on the expected port; check firewall rules between Caddy and backend |
| `{"level":"error","msg":"loading config","error":"unknown directive: X"}` | Critical | Unsupported directive in Caddyfile for installed Caddy version | Check Caddy version; install missing plugin or remove unsupported directive |
| `{"level":"warn","msg":"client requested TLS 1.0 which is not supported"}` | Medium | Legacy client connecting with TLS 1.0 | Add `tls { protocols tls1.2 tls1.3 }` block; notify client to upgrade |
| `{"level":"error","msg":"http: proxy error","error":"context deadline exceeded"}` | High | Backend response time exceeding Caddy's proxy timeout | Increase `dial_timeout` or `response_header_timeout` in `reverse_proxy` block; investigate backend latency |
| `{"level":"info","msg":"config reloaded successfully"}` | Info | `caddy reload` or admin API config PUT applied cleanly | No action; verify routes are serving as expected after reload |
| `{"level":"error","msg":"writing response","error":"write: broken pipe"}` | Low | Client disconnected before response was fully sent | Usually benign (client timeout or navigation away); monitor rate for abnormal spikes |
| `{"level":"error","msg":"bind: address already in use","addr":":443"}` | Critical | Another process is occupying port 443; Caddy failed to start | Identify process with `ss -tlnp | grep 443`; terminate conflicting process; restart Caddy |
| `{"level":"warn","msg":"exceeded rate limit","remote_ip":"X.X.X.X"}` | High | Client IP hitting rate limit directive threshold | Review rate limit config; allowlist known good IPs; investigate for abuse |
| `{"level":"error","msg":"ACME server not reachable","error":"dial tcp: i/o timeout"}` | High | Outbound HTTPS to Let's Encrypt blocked by firewall or proxy | Open outbound port 443 to `acme-v02.api.letsencrypt.org`; check egress firewall rules |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| HTTP 502 Bad Gateway | Caddy reached the upstream but received an invalid response or connection was refused | Clients see 502; upstream backend is down or crashing | Restart backend service; check backend health; review `reverse_proxy` upstream list |
| HTTP 503 Service Unavailable | All upstreams are unhealthy per Caddy's health checks | All proxied requests fail | Investigate backend health check path; restore at least one healthy upstream |
| HTTP 504 Gateway Timeout | Backend accepted the connection but did not respond within the timeout | Slow requests fail; clients see timeout | Increase `response_header_timeout`; profile backend for slow queries |
| HTTP 421 Misdirected Request | SNI hostname does not match any configured site block | HTTPS request for unknown hostname fails | Add a site block in Caddyfile for the hostname; check `*.` wildcard cert coverage |
| `ACME challenge failed` (DNS-01) | DNS challenge TXT record not placed or TTL too long | Certificate issuance fails; site serves expired cert | Verify DNS provider API credentials in Caddy DNS plugin config; reduce DNS TTL |
| `certificate not yet valid` | Issued cert has a `notBefore` in the future (clock skew) | HTTPS connections rejected by strict clients | Sync system clock with NTP (`chronyc makestep`); regenerate certificate |
| `tls: no certificates configured` | No matching certificate for the SNI hostname in the request | TLS handshake fails; connection dropped | Add site block for the hostname; confirm automatic HTTPS is enabled for the domain |
| `config load error: json: cannot unmarshal` | Caddy JSON config (admin API) is malformed | Config reload fails; old config continues running | Validate JSON with `caddy fmt --overwrite`; check for syntax errors in JSON config |
| `dial: no such host` (upstream) | Upstream hostname in `reverse_proxy` does not resolve | All requests to that site block fail | Fix hostname in Caddyfile; verify internal DNS or `/etc/hosts` entry for upstream |
| `rate: limit exceeded` | Caddy rate_limit module threshold hit for a client | Client receives 429; legitimate traffic may be impacted | Tune rate limit burst/rate parameters; add IP allowlist for known good sources |
| `on-demand TLS: hostname not allowed` | Hostname not in `ask` endpoint allowlist for on-demand TLS | Certificate not issued; HTTPS fails for that hostname | Update the ask endpoint to return 200 for the hostname; add to allowed list |
| Admin API `401 Unauthorized` | Request to `localhost:2019` missing required auth token | Config management operations fail | Configure admin authentication in `admin` block; provide correct token |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| ACME Certificate Renewal Failure | `caddy_tls_certificates_renewed_total` not incrementing; cert age > 60 days | `acme: error 403 unauthorized` or `i/o timeout` to ACME server | Certificate expiry alarm; synthetic HTTPS check failing | Port 80 blocked, DNS misconfigured, or CAA record excluding Let's Encrypt | Fix network path; correct CAA record; force renewal via config reload |
| Backend Cascade Failure | `caddy_reverse_proxy_upstreams_healthy` = 0; `caddy_http_request_errors_total` spiking | `no upstreams available` for multiple site blocks simultaneously | 502 error rate alarm; on-call page | Backend service crash or network segment failure | Restore backend service; check inter-service network; verify health check endpoint |
| Config Reload Loop | `caddy reload` calls succeeding but errors reappearing; log shows rapid `config reloaded` then `error` cycle | `loading config` errors alternating with `config reloaded successfully` | Deployment pipeline reporting intermittent failure | Partial IaC apply with conflicting config; race condition in rolling deploy | Serialize config changes; use `caddy validate` before every reload; pin to single deployer |
| Port 443 Bind Race on Restart | Caddy start fails with `bind: address already in use` after OS restart | `bind: address already in use :443` in systemd journal | Service-down alert | Previous Caddy process not fully cleaned up; `SO_REUSEPORT` conflict | `ss -tlnp | grep 443`; kill stale process; set `RestartSec=2` in systemd unit; use `CAP_NET_BIND_SERVICE` |
| Upstream Latency Propagating to Clients | Caddy P99 `request_duration` rising; upstream response time rising in parallel | `context deadline exceeded` proxy errors for slow requests | P99 latency SLO burn rate alert; backend latency alarm | Slow backend query or GC pause propagating through proxy | Tune `response_header_timeout`; add circuit breaker; profile backend; enable streaming |
| Rate Limit Blocking Legitimate Traffic | 429 error rate rising; specific IP ranges affected; user complaints | `exceeded rate limit` for known good IP blocks (CDN egress, office NAT) | Customer-facing error rate alarm; support tickets | Rate limit configured per-IP but traffic coming through shared CDN or NAT IP | Add CDN/load-balancer IP ranges to rate limit allowlist; rate limit by forwarded header instead |
| On-Demand TLS Exhausting ACME Rate Limits | New domains failing certificate issuance; existing domains unaffected | `acme: urn:ietf:params:acme:error:rateLimited` in logs | New-domain HTTPS failure alert | Too many certificate requests in short window (Let's Encrypt: 50 certs/domain/week) | Throttle on-demand TLS issuance via `ask` endpoint; use wildcard cert; contact LE for limit increase |
| Caddy OOM Kill Under Traffic Spike | Caddy process absent after traffic event; high memory before crash | `Out of memory: Kill process caddy` in `dmesg`; process absent from `ps` | Service-down alert; all sites returning connection refused | Large number of concurrent TLS handshakes or buffered request bodies exhausting RAM | Increase server RAM; add `request_body_max` to limit body size; enable swap as safety net |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `502 Bad Gateway` | fetch, axios, curl | Upstream backend unreachable or actively refusing connections | Caddy logs: `dial tcp <upstream>: connection refused`; test upstream directly | Verify upstream is running; check Caddy reverse_proxy `to` address; add health checks |
| `504 Gateway Timeout` | fetch, axios, curl | Upstream too slow; `response_header_timeout` or `dial_timeout` exceeded | Caddy logs: `context deadline exceeded`; upstream response latency metrics | Increase `response_header_timeout`; optimize upstream; add streaming |
| `SSL_ERROR_RX_RECORD_TOO_LONG` (browser) | All browsers | Client connecting on HTTP port 443 or TLS misconfiguration | `curl -I http://<host>:443`; Caddy config: port binding vs. TLS config | Ensure Caddy binds TLS to 443; redirect HTTP port 80 to HTTPS properly |
| `ERR_CERT_AUTHORITY_INVALID` (browser) | All browsers | Caddy using self-signed cert (local CA or ACME staging); certificate not trusted | `openssl s_client -connect <host>:443`; check issuer | Import Caddy's local CA root; switch to production ACME endpoint; use trusted CA |
| `ERR_TOO_MANY_REDIRECTS` (browser) | All browsers | HTTP→HTTPS redirect loop; upstream also redirecting; double redirect | `curl -v http://<host>/` following redirects; Caddy config `redir` + upstream config | Ensure upstream does not redirect; use `X-Forwarded-Proto` to suppress upstream redirect |
| `413 Request Entity Too Large` | fetch, axios, HttpClient | `request_body_max` limit in Caddy config exceeded | Caddy logs: `request body too large`; check config `request_body_max` directive | Increase `request_body_max`; or stream uploads directly to upstream bypassing body buffering |
| `421 Misdirected Request` | All browsers/HTTP clients | TLS SNI hostname does not match any configured Caddy site | Caddy logs: `no matching site for`; check client's `Host` header | Add site block for the hostname; verify DNS resolves to this Caddy instance |
| `connection reset by peer` mid-response | curl, fetch | Upstream closed connection before response complete; Caddy propagates reset | Caddy logs: `read: connection reset by peer` from upstream; upstream crash/restart | Fix upstream stability; enable `lb_try_duration` for automatic retry on other upstreams |
| `ACME challenge failed: 403 Forbidden` | Caddy ACME client | `.well-known/acme-challenge` path blocked by upstream rewrite or firewall | `curl http://<domain>/.well-known/acme-challenge/test`; check firewall and rewrites | Exclude ACME challenge path from rewrites; open port 80 inbound; use DNS challenge instead |
| `429 Too Many Requests` from Caddy | fetch, axios | Caddy `rate_limit` module or plugin threshold hit | Caddy logs: `rate limit exceeded`; access log `status=429` | Tune `rate_limit` rate and burst; whitelist trusted IPs; move rate limiting to upstream |
| `context canceled` in streaming responses | fetch with streaming, SSE clients | Client disconnected; Caddy propagates cancellation to upstream | Caddy logs: `context canceled`; upstream premature termination | Normal for client-initiated disconnects; add `flush_interval -1` for SSE/streaming routes |
| `no upstreams available` (502) | fetch, axios | All upstreams failed health checks simultaneously | Caddy logs: `no upstreams available`; upstream health check endpoint test | Fix at least one upstream; reduce health check aggressiveness; add passive health check |

---

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| TLS Certificate Renewal Approaching Failure | Cert valid but renewal silently failing; expiry < 30 days approaching | `caddy environ | grep CADDY`; `caddy version`; check Caddy logs for ACME renewal errors | Days | Check ACME logs: `journalctl -u caddy | grep acme`; verify port 80 open; switch to DNS challenge |
| Upstream Pool Degradation (Passive Health) | Increasing 5xx from one upstream; others taking all traffic; load imbalance | Caddy access logs: count `upstream` field per backend; `status>=500` rate per upstream | Hours | Remove degraded backend; fix it; re-add; tune `fail_duration` and `max_fails` |
| File Descriptor Leak | Caddy FD count growing over days; eventually `too many open files` error | `ls /proc/$(pgrep caddy)/fd | wc -l` vs `ulimit -n`; systemd `LimitNOFILE` | Days | Identify leaking plugin or handler; restart Caddy (graceful reload); increase `LimitNOFILE` |
| Access Log Volume Growing Unbounded | Disk filling from high-QPS site access logs; Caddy write I/O increasing | `du -sh /var/log/caddy/`; `df -h`; log rotation config | Days | Configure logrotate for Caddy logs; add `roll_size` and `roll_keep` in Caddy log config |
| Memory Growth from Large Response Buffering | Caddy RSS growing under load; responses fully buffered for gzip/transforms | `ps aux | grep caddy` RSS trend; enable `flush_interval -1` on large endpoints | Hours to days | Use `flush_interval -1` for streaming routes; limit `request_body_max`; profile handlers |
| On-Demand TLS Domain Accumulation | Certificate storage growing; SQLite/file cert store approaching size limits | `du -sh /var/lib/caddy/.local/share/caddy/certificates/`; count cert dirs | Weeks | Clean up stale domains; use wildcard certificate; set `on_demand` with `ask` endpoint validation |
| Upstream Latency P99 Creep | Caddy P99 `request_duration` rising in sync with upstream; no Caddy-side issue | Caddy access log: `duration` field P99 trend; `upstream_latency` if custom metric | Days | Profile upstream; add caching layer (Caddy `cache` plugin or external); scale upstream |
| Config Reload Accumulating Goroutine Leaks | Caddy memory growing after repeated `caddy reload`; goroutine count up | `curl localhost:2019/debug/pprof/goroutine?debug=1` (if admin API enabled) | Weeks | Update to latest Caddy version; reduce hot-reload frequency; use rolling restart |

---

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Collects: Caddy process status, version, config, certificate status, recent errors
CADDY_ADMIN="${CADDY_ADMIN:-localhost:2019}"

echo "=== Caddy Process Status ==="
systemctl status caddy --no-pager 2>/dev/null || pgrep -a caddy

echo "=== Caddy Version ==="
caddy version 2>/dev/null

echo "=== Caddy Admin API Health ==="
curl -sf "http://$CADDY_ADMIN/config/" -o /dev/null && echo "Admin API: OK" || echo "Admin API: UNREACHABLE"

echo "=== Active Configuration (truncated) ==="
curl -sf "http://$CADDY_ADMIN/config/" 2>/dev/null | python3 -m json.tool 2>/dev/null | head -50 \
  || caddy adapt --config /etc/caddy/Caddyfile 2>/dev/null | python3 -m json.tool | head -50

echo "=== Certificate Status ==="
CERT_DIR="${CERT_DIR:-/var/lib/caddy/.local/share/caddy/certificates}"
if [ -d "$CERT_DIR" ]; then
  find "$CERT_DIR" -name "*.crt" | while read CERT; do
    EXPIRY=$(openssl x509 -in "$CERT" -noout -enddate 2>/dev/null | cut -d= -f2)
    DOMAIN=$(basename "$(dirname "$CERT")")
    echo "  $DOMAIN → expires: $EXPIRY"
  done
else
  echo "Certificate directory not found at $CERT_DIR"
fi

echo "=== Recent Caddy Errors (last 1h) ==="
journalctl -u caddy --since "1 hour ago" --no-pager -p err 2>/dev/null | tail -30

echo "=== Open File Descriptors ==="
PID=$(pgrep -x caddy | head -1)
[ -n "$PID" ] && ls /proc/"$PID"/fd | wc -l | xargs echo "  FDs open:" || echo "Caddy not running"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Diagnoses request latency, upstream health, and error rates from Caddy access logs
ACCESS_LOG="${1:-/var/log/caddy/access.log}"
CADDY_ADMIN="${CADDY_ADMIN:-localhost:2019}"

echo "=== Last 100 Requests: Status Code Distribution ==="
tail -1000 "$ACCESS_LOG" 2>/dev/null | python3 -c "
import sys, json, collections
codes = collections.Counter()
for line in sys.stdin:
    try:
        entry = json.loads(line)
        codes[entry.get('status', entry.get('resp_headers', {}).get('status', 'unknown'))] += 1
    except: pass
for code, count in sorted(codes.items()): print(f'  HTTP {code}: {count}')
" || tail -100 "$ACCESS_LOG" | grep -oP '"status":\K[0-9]+' | sort | uniq -c | sort -rn

echo "=== Slowest 10 Requests (last 1000) ==="
tail -1000 "$ACCESS_LOG" 2>/dev/null | python3 -c "
import sys, json
entries = []
for line in sys.stdin:
    try:
        e = json.loads(line)
        entries.append((e.get('duration', 0), e.get('request', {}).get('uri', ''), e.get('status', '')))
    except: pass
for dur, uri, status in sorted(entries, reverse=True)[:10]:
    print(f'  {dur:.3f}s  [{status}]  {uri}')
" 2>/dev/null || echo "Enable JSON access logging for detailed analysis"

echo "=== Upstream Error Rate (last 100 entries) ==="
tail -100 "$ACCESS_LOG" 2>/dev/null | grep -E '"status":(5[0-9]{2})' | wc -l | xargs echo "  5xx errors:"

echo "=== Admin API: Active Upstreams ==="
curl -sf "http://$CADDY_ADMIN/reverse_proxy/upstreams" 2>/dev/null | python3 -m json.tool 2>/dev/null \
  || echo "No reverse_proxy upstreams endpoint (requires Caddy 2.6+)"

echo "=== Memory & CPU ==="
ps aux | grep "[c]addy" | awk '{printf "  PID: %s  CPU: %s%%  RSS: %s MB\n", $2, $3, $6/1024}'
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Audits TLS config, certificate expiry, port bindings, and upstream connectivity
CADDY_ADMIN="${CADDY_ADMIN:-localhost:2019}"
CERT_DIR="${CERT_DIR:-/var/lib/caddy/.local/share/caddy/certificates}"

echo "=== Port Bindings ==="
ss -tlnp | grep -E ':80\b|:443\b|:2019\b'
ss -ulnp | grep -E ':80\b|:443\b'

echo "=== TLS Certificate Expiry (all managed certs) ==="
find "$CERT_DIR" -name "*.crt" 2>/dev/null | while read CERT; do
  EXPIRY_EPOCH=$(openssl x509 -in "$CERT" -noout -enddate 2>/dev/null | cut -d= -f2 | xargs -I{} date -d {} +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$(openssl x509 -in "$CERT" -noout -enddate | cut -d= -f2)" +%s 2>/dev/null)
  NOW_EPOCH=$(date +%s)
  DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
  DOMAIN=$(basename "$(dirname "$CERT")")
  STATUS="OK"
  [ "$DAYS_LEFT" -lt 14 ] && STATUS="WARNING"
  [ "$DAYS_LEFT" -lt 3 ] && STATUS="CRITICAL"
  echo "  [$STATUS] $DOMAIN: $DAYS_LEFT days remaining"
done

echo "=== Upstream Connectivity Test ==="
UPSTREAMS=$(curl -sf "http://$CADDY_ADMIN/config/" 2>/dev/null | \
  python3 -c "import sys,json; cfg=json.load(sys.stdin); \
  [print(u['dial']) for s in cfg.get('apps',{}).get('http',{}).get('servers',{}).values() \
  for r in s.get('routes',[]) for h in r.get('handle',[]) \
  if h.get('handler')=='reverse_proxy' for u in h.get('upstreams',[])]" 2>/dev/null)
for UPSTREAM in $UPSTREAMS; do
  HOST=$(echo "$UPSTREAM" | cut -d: -f1)
  PORT=$(echo "$UPSTREAM" | cut -d: -f2)
  nc -z -w3 "$HOST" "$PORT" 2>/dev/null && echo "  $UPSTREAM: REACHABLE" || echo "  $UPSTREAM: UNREACHABLE"
done

echo "=== Caddyfile / Config Validation ==="
if [ -f /etc/caddy/Caddyfile ]; then
  caddy validate --config /etc/caddy/Caddyfile 2>&1 && echo "Config: VALID" || echo "Config: INVALID"
fi

echo "=== Systemd LimitNOFILE ==="
systemctl show caddy --property=LimitNOFILE 2>/dev/null
```

---

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Upstream Throughput Monopolization by Large Downloads | Large file downloads saturating upstream link; small API requests timing out | Caddy access log: high `duration` + large `resp_size` requests; `nethogs` on upstream interface | Add `rate_limit` by path for large downloads; use `header` to cap response size | Route large file downloads to CDN or S3 direct; separate Caddy instances for API vs. static |
| TLS Handshake CPU Spike Under Connection Flood | CPU near 100%; new connection latency spiking; existing connections still served | `ss -s` TCP `SYN_RECV` count; Caddy access log connection rate; `top` showing caddy CPU | Enable TLS session resumption (default in Caddy); rate-limit new connections at firewall | Use hardware TLS offload (AWS ALB, Nginx with OpenSSL hardware provider) in front of Caddy |
| On-Demand TLS Domain Exhaustion (Let's Encrypt Rate Limit) | New domains failing cert issuance; existing domains unaffected; ACME 429 in logs | Caddy logs: `acme: urn:ietf:params:acme:error:rateLimited`; count cert requests per hour | Switch to DNS challenge; use wildcard cert; contact Let's Encrypt for increase; add `ask` endpoint gating | Implement `ask` URL validation to prevent arbitrary domain cert issuance; pre-provision certs |
| Shared Caddy Instance Serving Mixed-SLA Sites | High-traffic low-priority site consuming all worker goroutines; high-SLA site degraded | Caddy access log: request queue depth per `Host`; goroutine count via pprof | Add per-site `rate_limit`; separate high-SLA sites to dedicated Caddy instance | Separate Caddy instances per SLA tier; use upstream load balancer for traffic shaping |
| Reverse Proxy Buffer Exhaustion Under Slow Clients | Memory growing during traffic from slow clients; upstream keeps connection open waiting for buffer flush | `ps aux` RSS growing; Caddy logs `write: broken pipe` after long duration | Enable `flush_interval -1` for streaming; reduce `response_header_timeout` to drop slow clients faster | Set aggressive `response_header_timeout` and `read_timeout`; tune buffer limits |
| Log Write I/O Competing with TLS Handshakes | Latency spikes correlated with disk I/O wait; access log writes blocking Caddy | `iostat -x 1 5` on log disk; `iotop` showing caddy write I/O | Move access logs to separate disk or tmpfs; use async log buffer; log to syslog over UDP | Log to remote syslog or Loki; use ramdisk for high-frequency access logging |
| Admin API Polling by Monitoring Tools | Caddy admin API goroutine count rising; config endpoint hammered by monitoring | `caddy admin` access log (if enabled); `netstat` for connections to port 2019 | Reduce monitoring poll frequency; use metrics endpoint instead of config API | Use Caddy's Prometheus metrics endpoint (`/metrics`); rate-limit admin API access |
| Upstream Connection Pool Exhaustion Under Burst | 502 errors during traffic spike; `no upstreams available` in Caddy logs | Caddy logs: upstream selection failures; upstream keepalive pool size vs. burst QPS | Increase `keepalive` connections in `reverse_proxy` block; add more upstream instances | Set `keepalive 100` in reverse_proxy; size upstream pool for burst; use active health checks |
| ACME Certificate Issuance Blocking Request Serving | First-request latency for new domains very high (5–30s); other requests unaffected | Caddy logs: `obtaining certificate` during request handling; `on_demand` tls config | Pre-provision certificates; use `ask` endpoint to pre-validate before on-demand issuance | Use DNS challenge for wildcard; avoid on-demand TLS in high-traffic environments |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Caddy process crashes | All inbound HTTP/HTTPS traffic drops → upstream services unreachable → health checks fail → load balancers remove backends → application 503 storm | All services behind this Caddy instance; external traffic completely stopped | `systemctl is-active caddy` returns `inactive`; `curl -sf https://<domain>/health` fails; Prometheus `caddy_up == 0` | `systemctl restart caddy`; if systemd fails, `caddy run --config /etc/caddy/Caddyfile &`; immediately route traffic to backup instance |
| Let's Encrypt ACME outage | Certificate renewal fails for all domains expiring in next 30 days → after expiry, TLS handshakes fail → browsers show ERR_CERT_DATE_INVALID → users blocked | All domains using ACME (HTTP-01 or DNS-01 challenges); only affects certs expiring during outage | Caddy logs: `acme: Error -> urn:ietf:params:acme:error:serverInternal`; cert expiry monitoring alerts; `openssl s_client -connect <domain>:443 </dev/null | grep "notAfter"` | Switch to ZeroSSL or Buypass ACME endpoint in Caddyfile; or manually provision cert: `openssl req -x509` as temporary measure |
| All upstream backends fail simultaneously | Caddy returns 502 Bad Gateway to all clients → application health dashboards red → SLO breach | All services proxied through Caddy; Caddy itself stays up but useless | Caddy access log: `upstream: dial tcp <ip>:<port>: connect: connection refused`; Prometheus `caddy_reverse_proxy_upstreams_healthy == 0` | Enable Caddy's static error page with maintenance message; route to fallback upstream; check upstream health with `nc -z <host> <port>` |
| TLS certificate expiry (ACME disabled or blocked) | HTTPS connections fail with `ERR_CERT_DATE_INVALID` → clients cannot connect → API integrations break → webhook deliveries fail | All clients connecting to this domain; automated integrations that don't ignore TLS errors | `echo | openssl s_client -connect <domain>:443 2>&1 | grep "Verify return code"` shows error 10; browser shows certificate expired | Temporarily redirect HTTP → HTTPS off; provision cert manually and place in `/etc/caddy/certs/`; update Caddyfile to use `tls /path/cert /path/key` |
| Caddy admin API unreachable (port 2019 blocked) | Dynamic config updates via API fail → automated deployments cannot update routes → new services cannot be registered | Only automated config-push workflows; traffic serving continues unaffected | `curl -sf http://localhost:2019/config/` returns connection refused; deployment pipelines fail on config push step | Use `caddy reload --config /etc/caddy/Caddyfile` instead of API; check firewall: `ss -tlnp \| grep 2019`; restart Caddy with admin enabled |
| Upstream DNS resolution failure for reverse proxy targets | Caddy cannot resolve upstream hostnames → all proxied requests fail with 502 → same cascade as backend failure | All services configured with hostname (not IP) as upstream; DNS-dependent upstreams | Caddy logs: `dial tcp: lookup <hostname>: no such host`; `dig <hostname>` from Caddy host fails | Change upstream to use IP address temporarily; fix DNS; add `/etc/hosts` entry as immediate workaround |
| File descriptor limit exhaustion under connection storm | New connections rejected; existing connections slow; `too many open files` in Caddy logs | All new clients; existing connections may be unaffected briefly before also degrading | `cat /proc/$(pgrep caddy)/limits \| grep "open files"`; `ls /proc/$(pgrep caddy)/fd \| wc -l` near limit | `systemctl set-property caddy LimitNOFILE=65536`; `kill -USR1 $(pgrep caddy)` to reload limits without dropping connections |
| Caddy config reload failure leaves stale routes | `caddy reload` fails silently; new routes not active; old routes serving decommissioned backends | Deployments that changed routing; new services not reachable via Caddy | `caddy validate --config /etc/caddy/Caddyfile 2>&1`; compare expected vs actual config via `curl -sf http://localhost:2019/config/` | Fix syntax error in Caddyfile; `caddy reload --config /etc/caddy/Caddyfile --force`; check exit code of reload command |
| Memory leak in long-running Caddy instance | Memory grows over days/weeks → OOM killer terminates Caddy → brief downtime | All traffic; typically manifests as scheduled maintenance window surprise | `ps aux \| grep caddy` showing RSS growing; `cat /proc/$(pgrep caddy)/status \| grep VmRSS`; OOM in `dmesg` | Schedule periodic Caddy restarts during low-traffic windows; upgrade Caddy version; enable memory limit in systemd unit |
| ACME HTTP-01 challenge blocked by upstream WAF or proxy | Certificate renewal fails for specific domains; Caddy logs `acme: challenge authorization failed`; certs expire | Domains behind WAF that blocks ACME `/.well-known/acme-challenge/` paths | Caddy logs: `http-01 challenge: 403 Forbidden`; test manually: `curl http://<domain>/.well-known/acme-challenge/test` | Switch to DNS-01 challenge: add `tls { dns <provider> }` in Caddyfile; configure DNS API credentials | 

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Caddy version upgrade (v2.6 → v2.7) | Named matchers syntax changed; Caddyfile validation fails; Caddy won't start after upgrade | Immediate on `systemctl restart caddy` | `journalctl -u caddy -b \| head -50`; compare Caddyfile syntax with version changelog | `apt-get install caddy=<previous-version>` or `dnf downgrade caddy`; restore Caddyfile from version control |
| Caddyfile reverse_proxy upstream address change | Traffic routes to wrong backend; old backend receives no traffic and idles; 502 if new address wrong | Immediate on `caddy reload` | Caddy access log showing `upstream: <new-ip>` for all requests; `curl http://localhost:2019/config/ \| jq .apps.http` | Revert Caddyfile to previous version; `caddy reload --config /etc/caddy/Caddyfile` |
| Adding `encode gzip` to large file download routes | Upload/download of already-compressed files (zip, jpg, video) artificially CPU-intensive; no benefit | Immediate; visible under load | `top` showing caddy CPU elevated; access log showing `Content-Encoding: gzip` on binary files | Remove `encode` directive for binary file paths or exclude MIME types: `encode { match header Content-Type image/* }` |
| TLS version restriction change (removing TLS 1.2) | Old clients (iOS < 12, Android < 5, curl without TLS 1.3) start failing with `SSL_ERROR_PROTOCOL_VERSION_ALERT` | Immediate; affects only old TLS clients | Access log showing `TLS handshake error` for specific clients; test: `openssl s_client -tls1_2 -connect <domain>:443` | Re-add TLS 1.2 support: `tls { protocols tls1.2 tls1.3 }` in Caddyfile; `caddy reload` |
| Header manipulation directive blocking authentication | Auth headers stripped by `header -Authorization`; backend services return 401; SSO flows break | Immediate on reload | 401 errors spike in access log; application logs show missing auth token; `curl -v -H "Authorization: Bearer x" https://<domain>/api` shows 401 | Remove or correct `header` directive; `caddy reload`; test with explicit auth header |
| `rate_limit` module added incorrectly | Legitimate users throttled; 429 errors for normal usage; specific endpoints unreachable | Immediate | Access log shows `status 429` for normal user IPs; check module configuration in Caddyfile | Remove `rate_limit` block; `caddy reload`; re-add with correct `rate` and `burst` values after testing |
| HTTPS redirect loop from double-redirect config | Browser shows `ERR_TOO_MANY_REDIRECTS`; curl shows infinite 301 loop | Immediate on config change | `curl -L -v http://<domain>/` shows same redirect repeating; Caddy access log showing rapid successive requests | Check for both `redir` and `auto_https` both redirecting; add `:80 { ... }` block without redirect to handle HTTP directly |
| Wildcard certificate `tls *.example.com` replacing per-domain certs | Subdomains not listed in Caddy configuration no longer get certificate coverage; `curl` shows wrong cert | At next cert renewal or restart | `echo \| openssl s_client -connect <subdomain>:443 2>&1 \| grep "subject="` shows wrong cert | Add explicit `tls` block per subdomain or use `on_demand` TLS; `caddy reload` |
| Increasing `timeouts` values for slow upstreams | Idle connections held open longer; connection pool exhausted faster under load; goroutine leak | Hours to days; manifests as slow memory growth then sudden degradation | `curl http://localhost:2019/debug/pprof/goroutine?debug=1 \| head -20` shows goroutine growth | Reduce `idle_timeout` and `response_header_timeout`; `systemctl restart caddy` to clear leaked goroutines |
| systemd `User=caddy` change to root | Caddy now runs as root; security regression; port binding works but file permissions change | Immediate on service restart | `ps aux \| grep caddy` shows root; `journalctl -u caddy` shows no permission error (was previously failing) | Revert systemd unit file: `systemctl edit caddy --full`; restore `User=caddy`; fix file ownership: `chown -R caddy:caddy /etc/caddy /var/lib/caddy` |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Caddy config API vs Caddyfile drift | `diff <(caddy fmt /etc/caddy/Caddyfile) <(curl -sf http://localhost:2019/config/ \| caddy adapt --adapter json)` | Config pushed via API not reflected in Caddyfile on disk; next `caddy reload` from file reverts API changes | Silent config rollback on service restart; routes added dynamically disappear | Always use file-based config as source of truth; commit Caddyfile to git; use `caddy reload` not API for persistent changes |
| Multiple Caddy instances with divergent configs behind load balancer | `curl -sf http://instance1:2019/config/ > /tmp/c1.json && curl -sf http://instance2:2019/config/ > /tmp/c2.json && diff /tmp/c1.json /tmp/c2.json` | Intermittent routing differences; some users hit new routes, others hit old routes | A/B routing inconsistency; authentication failures if session affinity not set | Use config management (Ansible) to deploy identical Caddyfile to all instances; verify with config diff |
| TLS cert mismatch between Caddy and CDN termination | `curl -vk https://<domain>` vs `curl -v https://<domain>` (via CDN) show different cert SAN | CDN caches old cert; Caddy serving new cert; some clients see expired cert via CDN edge | Subset of users (CDN-cached) see expired cert; direct-to-origin users unaffected | Purge CDN cert cache; ensure CDN certificate rotation is synchronized with Caddy; use cert pinning monitoring |
| Caddy access log format drift after config reload | Log parsing pipeline (Loki, Splunk) fails to parse new format; metrics derived from logs go silent | `journalctl -u caddy --since "1 hour ago" \| head -5` shows different JSON structure | Monitoring based on log parsing becomes blind; SLO calculations break | Pin log format explicitly in Caddyfile: `log { format json { ...fields} }`; always test log format before production reload |
| ACME cert stored in `/var/lib/caddy` not visible after `chown` change | Caddy issues new cert but old cert still served; `openssl s_client` shows old cert | `ls -la /var/lib/caddy/.local/share/caddy/certificates/` shows wrong ownership | Cert renewal succeeds but Caddy cannot read new cert; old cert continues serving until hard expiry | `chown -R caddy:caddy /var/lib/caddy`; `systemctl restart caddy`; verify with `openssl s_client` |
| Stale upstream health check cache marking good backend as down | Some upstreams bypassed even after recovery; healthy backends not receiving traffic | `curl -sf http://localhost:2019/config/ \| jq '.apps.http.servers[].routes'` shows passive health state | Reduced upstream capacity; higher load on remaining backends | Force Caddy reload to reset health state: `caddy reload --config /etc/caddy/Caddyfile`; or restart Caddy |
| /etc/hosts override diverging from Caddy upstream DNS | `getent hosts <upstream>` returns different IP than DNS; Caddy connects to stale IP from /etc/hosts | Caddy proxies to wrong backend IP while `dig <upstream>` shows correct IP | Traffic misrouted after backend IP change | Remove stale /etc/hosts entry; verify Caddy upstream resolution: `strace -e trace=network -p $(pgrep caddy)` |
| Session affinity broken after Caddy config reload | Users logged in before reload lose session; application shows login required | `curl -b "session=X" https://<domain>/api/profile` returns 401 after reload | User experience degradation; requires re-login | Ensure upstream sticky sessions are handled at application layer (JWT, DB-backed); Caddy is stateless — no session state to preserve |
| HTTP/2 vs HTTP/1.1 protocol negotiation mismatch after cert change | Some clients fail to upgrade to HTTP/2; ALPN negotiation fails; slower performance or connection errors | `curl -v --http2 https://<domain>/ 2>&1 \| grep "< HTTP"` shows HTTP/1.1 unexpectedly | Performance degradation; potential application breakage if HTTP/2 specific features relied on | Verify ALPN in cert: `openssl s_client -connect <domain>:443 -alpn h2`; ensure Caddy TLS block includes `alpn h2 http/1.1` |
| Caddy automatic HTTPS overriding explicit `tls off` directive | After Caddy upgrade, site switches from HTTP to HTTPS unexpectedly; redirects break | `curl -v http://<domain>/` returns 301 to HTTPS unexpectedly | Services that must serve HTTP (internal health checks, ACME HTTP-01) break | Explicitly disable auto HTTPS: `{ auto_https off }` in global options; or use `:80 { ... }` block explicitly |

## Runbook Decision Trees

### Decision Tree 1: HTTP 502/503 Errors from Reverse Proxy
```
Is Caddy process running? (systemctl is-active caddy)
├── NO  → Is Caddyfile valid? (caddy validate --config /etc/caddy/Caddyfile)
│         ├── NO  → Syntax error: journalctl -u caddy -b | grep "error"; restore from git; reload
│         └── YES → Resource exhaustion? (check dmesg | grep "Out of memory"; ulimit -n)
│                   ├── YES → OOM: increase memory limits; check for connection leak; restart caddy
│                   └── NO  → Unknown crash: journalctl -u caddy -b --no-pager | tail -100; escalate
└── YES → Is upstream backend responding? (curl -sf http://<upstream-host>:<port>/health)
          ├── NO  → Backend down: check backend service status; restart backend; remove from lb if clustered
          └── YES → Is Caddy reaching backend? (check caddy logs: journalctl -u caddy | grep "dial\|connect\|upstream")
                    ├── connect refused → Backend port wrong in Caddyfile; verify with ss -tlnp on backend
                    ├── timeout → Network issue: ping <backend-host>; check firewall rules; verify MTU
                    └── OK     → Check header/TLS mismatch: journalctl -u caddy | grep "tls\|certificate\|handshake"
                                 ├── TLS error → Backend TLS misconfigured; add transport { tls_insecure_skip_verify } temporarily
                                 └── NO     → Escalate: enable debug logging (log { level DEBUG }) in Caddyfile; caddy reload
```

### Decision Tree 2: TLS Certificate Renewal Failure
```
Is the domain certificate expired or expiring within 7 days?
(echo | openssl s_client -connect <domain>:443 2>&1 | openssl x509 -noout -enddate)
├── NOT EXPIRED → Check ACME renewal logs: journalctl -u caddy | grep -i "acme\|renew\|certificate"
│                 ├── Rate limited → Let's Encrypt rate limit hit: check https://crt.sh?q=<domain>; wait or use staging ACME
│                 └── No errors   → Certificate is healthy; false alert; verify monitoring probe
└── EXPIRED/EXPIRING → Is port 80 reachable from internet? (curl -I http://<domain>/.well-known/acme-challenge/test)
                        ├── NO  → HTTP-01 challenge blocked: check firewall port 80; Caddyfile redirect blocking /.well-known/
                        │         Fix: ensure port 80 open; add redir exemption; caddy reload
                        └── YES → Is ACME CA reachable from server? (curl -sf https://acme-v02.api.letsencrypt.org/directory)
                                  ├── NO  → Network/DNS issue from server: check /etc/resolv.conf; test outbound HTTPS
                                  └── YES → Force renewal: touch /var/lib/caddy/.local/share/caddy/locks/<domain>; restart caddy
                                            ├── Succeeds → Monitor: journalctl -fu caddy | grep "certificate"
                                            └── Fails    → Fall back to manual: certbot certonly --standalone -d <domain>; point Caddyfile to cert path
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| ACME Let's Encrypt rate limit exhaustion | Too many cert issuance attempts for same domain; cert renewals blocked cluster-wide | `journalctl -u caddy | grep -i "rate limit\|too many certificates"`; check https://crt.sh?q=<domain> for cert history | All new/renewed TLS certs fail; services run on expired certs | Switch to ACME staging environment; use wildcard cert; wait for rate limit window (1 week) | Use wildcard certs for subdomains; consolidate issuance; monitor cert count at https://crt.sh |
| Runaway connection pool exhausting file descriptors | `ulimit -n` hit; Caddy logs "too many open files"; new connections refused | `cat /proc/$(pgrep caddy)/limits | grep "open files"`; `lsof -p $(pgrep caddy) | wc -l` | All new inbound connections fail | `systemctl edit caddy` to add `LimitNOFILE=65536`; restart caddy | Set `LimitNOFILE=65536` in caddy.service override; configure `keepalive_idle_conns` limit in reverse_proxy |
| Unlimited request body size enabling DoS | Large file uploads consuming all disk/RAM; other requests starved | `df -h /tmp`; `journalctl -u caddy | grep "request body"`; check active upload connections with `ss -tnp | grep caddy` | Disk exhaustion or OOM kill of Caddy | Add `request_body { max_size 10MB }` to Caddyfile; caddy reload | Always set `request_body { max_size }` directive; add `limits` global block |
| Access log filling disk | `/var/log/caddy/` partition full; Caddy unable to write logs; silent request drops | `df -h /var/log`; `du -sh /var/log/caddy/`; `ls -lhrt /var/log/caddy/` | Disk full may cause Caddy process issues; audit trail lost | Rotate logs immediately: `logrotate -f /etc/logrotate.d/caddy`; delete oldest logs | Configure `log { output file /var/log/caddy/access.log { roll_size 100MB } }` in Caddyfile |
| Admin API exposed to public internet | Caddy admin endpoint (port 2019) accessible externally; config/cert data exfiltrated | `curl http://<public-ip>:2019/config/`; `nmap -p 2019 <public-ip>` | Full Caddy configuration accessible; potential remote code execution via config API | `systemctl reload caddy` after adding `admin localhost:2019` to global block | Always bind admin API to localhost; add firewall rule blocking external access to 2019 |
| Reverse proxy buffering large responses in memory | Memory usage climbing; high latency for downstream clients; OOM risk | `curl -s http://localhost:2019/metrics | grep caddy_http_request_size`; `ps aux | grep caddy` for RSS | OOM kill of Caddy; service outage | Add `flush_interval -1` to reverse_proxy block for streaming; reduce buffer sizes | Use `flush_interval -1` for streaming endpoints; set `response_header_timeout` to prevent slow backends |
| TLS mutual auth misconfiguration causing cert spam | Client certs being regenerated in a loop; CA filling with short-lived certs | `journalctl -u caddy | grep "tls.obtain\|client auth"`; check cert count in `/var/lib/caddy/.local/` | CA quota exhaustion; disk fill from cert storage | Disable auto-HTTPS for internal mTLS endpoints: `tls internal`; clean up orphaned certs | Use `tls { client_auth { mode require_and_verify } }` correctly; don't mix auto-HTTPS with mTLS |
| Debug logging left on in production | Disk filling with verbose logs; Caddy CPU elevated for log formatting | `journalctl -u caddy | grep "DEBUG"`; `du -sh /var/log/caddy/` | Disk exhaustion; performance degradation; sensitive request data in logs | Remove `log { level DEBUG }` from Caddyfile; `caddy reload` | Always use `level INFO` or `level WARN` in production; enforce via config review in CI |
| Slow upstream backend causing goroutine leak | Caddy goroutine count growing; memory climbing; request queue backing up | `curl -s http://localhost:2019/metrics | grep go_goroutines`; check upstream response time | Memory exhaustion; cascading failure to all proxied services | Set `transport http { response_header_timeout 10s dial_timeout 5s }` in reverse_proxy; circuit-break backend | Always configure timeouts in reverse_proxy transport block; add health checks with `health_uri /health` |
| Unthrottled file server serving large directory | Disk I/O saturation from directory listing or large file transfers; other requests starved | `iostat -x 1`; `journalctl -u caddy | grep "file_server"`; check active connections to port 80/443 | I/O starvation affecting all virtual hosts on same server | Add `browse` restriction; use `limits` block; temporarily disable file_server directive | Always limit `file_server` to specific paths; add `rate_limit` if using caddy-ratelimit plugin |

## Latency & Performance Degradation Patterns
| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot virtual host / single-site overload | One upstream receiving all load; other vhosts unaffected; Caddy goroutines climbing | `curl -s http://localhost:2019/metrics \| grep caddy_http_requests_in_flight`; `curl -s http://localhost:2019/metrics \| grep 'caddy_http_request_duration.*{host="<hot-host>"}'` | Viral traffic spike or misconfigured DNS routing all traffic to single vhost | Add `rate_limit` directive via caddy-ratelimit plugin; enable upstream health-check load balancing: `lb_policy round_robin`; use `header_down` to add cache headers |
| Connection pool exhaustion to upstream | 502 errors to specific backend; `caddy_reverse_proxy_upstreams_healthy 0`; new requests failing | `curl -s http://localhost:2019/metrics \| grep caddy_reverse_proxy_upstreams`; `ss -tnp \| grep caddy` | Backend connection pool full; upstream not releasing idle connections fast enough | Set `transport http { max_conns_per_host 200 keepalive 30s keepalive_idle_conns 50 };` in reverse_proxy block; reduce `keepalive_idle_conns` |
| GC / memory pressure from large response buffering | Caddy RSS climbing; intermittent response latency; eventual OOM | `ps aux \| grep caddy`; `curl -s http://localhost:2019/metrics \| grep go_memstats_alloc_bytes`; `curl -s http://localhost:2019/metrics \| grep go_gc_duration` | Large buffered responses held in memory; streaming endpoints not using `flush_interval -1` | Add `flush_interval -1` to reverse_proxy for streaming responses; add `request_body { max_size 50MB }` to cap body buffering |
| Goroutine leak from slow upstream | `go_goroutines` metric growing indefinitely; memory climbing; request queue building | `curl -s http://localhost:2019/metrics \| grep go_goroutines`; `curl -s http://localhost:2019/debug/pprof/goroutine?debug=1 \| head -50` | No `response_header_timeout` or `dial_timeout` set; goroutines waiting on slow backends forever | Set `transport http { dial_timeout 5s response_header_timeout 15s read_timeout 30s };` in reverse_proxy; restart Caddy after leak accumulates |
| Slow ACME HTTP-01 challenge impacting request latency | All requests to `/.well-known/acme-challenge/` slow; blocks HTTPS traffic until complete | `time curl -sf http://<domain>/.well-known/acme-challenge/test`; `journalctl -u caddy \| grep -i "acme\|challenge"` | ACME CA responding slowly; Caddy blocking on cert provisioning during initial startup | Pre-provision certs before routing traffic; use `on_demand` TLS only for small-scale dynamic cert use cases; add ACME timeout: `cert_issuer acme { timeout 60s }` |
| CPU spike from TLS handshake storm | Caddy CPU 100% on new connection surge; TLS negotiation latency high; established connections unaffected | `top -p $(pgrep caddy)`; `curl -s http://localhost:2019/metrics \| grep caddy_tls_handshake_duration`; `openssl s_client -connect <host>:443 \| grep "Session-ID"` | TLS session cache not warm; many clients not reusing sessions; CPU-intensive RSA key exchange | Enable TLS session tickets (Caddy does this by default); prefer ECDSA certs over RSA (faster handshake); use HTTP/2 connection reuse |
| Lock contention on Caddy config reload | High latency spike during `caddy reload`; all in-flight requests stall briefly | `time caddy reload --config /etc/caddy/Caddyfile`; `journalctl -u caddy \| grep "reloading\|reload complete"` | Config reload acquires write lock on routing table; large Caddyfile with many matchers increases lock time | Minimize Caddyfile complexity; pre-validate with `caddy validate`; schedule reloads during low-traffic periods; use `caddy fmt` to optimize ordering |
| Serialization overhead in JSON logging | Caddy CPU elevated when structured logging enabled; request throughput reduced | `journalctl -u caddy \| grep DEBUG`; `curl -s http://localhost:2019/metrics \| grep caddy_http_request_duration`; check log level in Caddyfile | `log { level DEBUG }` in production; per-request JSON marshalling is expensive at high RPS | Set `log { level WARN }` in production Caddyfile; use `log { sampling { interval 1s first 100 thereafter 100 } }` for high-volume sites |
| Batch request misconfiguration (large concurrent upload) | Upload endpoints saturating CPU/disk; all other vhosts experience latency | `ss -tnp \| grep caddy`; `curl -s http://localhost:2019/metrics \| grep caddy_http_request_size`; `iostat -x 1` | No request size or concurrency limits on upload handler; disk I/O saturation | Add `request_body { max_size 100MB }`; use `limits` global block; add `header Connection close` to force connection teardown after large uploads |
| Downstream dependency latency (upstream health check interval too long) | Caddy routing to a failed upstream for up to `health_interval` seconds; errors spiking | `curl -s http://localhost:2019/metrics \| grep caddy_reverse_proxy_upstreams_healthy`; `curl -s http://localhost:2019/config/ \| python3 -m json.tool \| grep health` | Default `health_interval 30s` is too long; Caddy continues routing to dead upstream | Set `health_uri /health health_interval 5s health_timeout 2s` in reverse_proxy; enable `fail_duration 10s` for passive health checking |

## Network & TLS Failure Patterns
| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| ACME TLS certificate expiry | Browser `NET::ERR_CERT_DATE_INVALID`; Caddy logs `certificate expired`; renewal failed silently | `echo \| openssl s_client -connect <domain>:443 2>&1 \| openssl x509 -noout -enddate`; `journalctl -u caddy \| grep -i "renew\|ACME\|expired"` | HTTPS completely broken for affected domains; users see security errors | Force renewal: `caddy reload` (triggers check); if blocked, `systemctl stop caddy && certbot certonly --standalone -d <domain>` then point Caddyfile to manual cert path |
| mTLS client cert rotation failure | Clients rejected with `tls: certificate required` or `tls: bad certificate`; upstream mTLS handshake fails | `curl --cert old.crt --key old.key https://<domain>/health`; `journalctl -u caddy \| grep -i "client auth\|certificate required\|bad certificate"` | Services using mTLS authentication fail; API calls returning 495/496 | Update `tls { client_auth { trusted_ca_certs_pem_files <new-ca.pem> } }` in Caddyfile; `caddy reload`; coordinate with client to present new cert |
| DNS resolution failure for upstream proxy target | Caddy reverse_proxy returning 502; upstream hostname not resolving; `dig <upstream-host>` fails | `dig <upstream-hostname>`; `journalctl -u caddy \| grep -i "no such host\|dial\|502"`; `curl -s http://localhost:2019/metrics \| grep caddy_reverse_proxy_upstreams_healthy` | All requests to affected upstream return 502; complete downstream service outage | Switch upstream to IP temporarily: update `reverse_proxy 10.x.x.x:8080`; fix DNS then revert; or add `/etc/hosts` entry as workaround |
| TCP connection exhaustion (FD limit) | New connections refused; Caddy logs `accept tcp: too many open files`; existing connections unaffected | `lsof -p $(pgrep caddy) \| wc -l`; `cat /proc/$(pgrep caddy)/limits \| grep "open files"`; `journalctl -u caddy \| grep "too many open"` | No new connections accepted; service appears down to new clients | `systemctl edit caddy` to add `LimitNOFILE=131072`; `systemctl daemon-reload && systemctl restart caddy` |
| Load balancer (upstream) misconfiguration — session affinity mismatch | Stateful app users getting logged out; requests routed to wrong backend; `caddy_reverse_proxy_upstreams_healthy` fluctuating | `curl -s http://localhost:2019/config/ \| python3 -m json.tool \| grep -A5 "load_balancing"`; test: `for i in {1..10}; do curl -si https://<host>/whoami; done` | Session state lost for users; application errors from inconsistent backend routing | Enable sticky sessions: `lb_policy cookie { name caddy_sticky secure }` in reverse_proxy; or move to stateless app design |
| Packet loss causing upstream timeout cascade | Upstream timeout errors climbing; Caddy returning 504; intermittent with no pattern | `ping -c 100 <upstream-ip> \| tail -3`; `mtr --report <upstream-ip>`; `curl -s http://localhost:2019/metrics \| grep caddy_http_response_duration` | Intermittent 504 errors; users retrying; backend receives duplicate requests | Add `retries 2` in reverse_proxy (idempotent only); increase `read_timeout` to handle network jitter; investigate switch/route between Caddy and upstream |
| MTU mismatch causing large HTTPS responses to hang | Large file downloads or API responses truncating; connection hangs after ~1400 bytes | `curl -v --max-time 10 https://<domain>/large-endpoint 2>&1 \| tail -5`; `ping -M do -s 1452 <upstream-ip>` | Path MTU discovery blocked; PMTUD blackhole between Caddy and client/upstream | Enable TCP MSS clamping on the router; add `header_up Connection close` to disable keepalive for affected routes; set MTU 1400 on Caddy host NIC |
| Firewall rule change blocking port 443 outbound (ACME) | ACME certificate renewal fails; Caddy logs `connection refused` to `acme-v02.api.letsencrypt.org` | `curl -sf https://acme-v02.api.letsencrypt.org/directory`; `journalctl -u caddy \| grep -i "acme\|letsencrypt\|connect"`; `traceroute acme-v02.api.letsencrypt.org` | Certificate renewals blocked; certs expire without renewal; HTTPS broken | Open egress HTTPS (443) from Caddy host to `acme-v02.api.letsencrypt.org`; use DNS-01 challenge via `caddy-dns` plugin to avoid HTTP dependency |
| SSL handshake timeout from TLS 1.0/1.1 client | Old clients failing TLS negotiation; Caddy logs `tls: no supported versions satisfy MinVersion`; 400 errors | `journalctl -u caddy \| grep -i "tls\|handshake\|version"`; `nmap --script ssl-enum-ciphers -p 443 <host>` | Legacy clients (old browsers, Java apps) unable to connect | Add `tls { protocols tls1.2 tls1.3 }` explicitly (this is Caddy's default); for legacy clients, allow TLS 1.0 only in dedicated vhost |
| Connection reset by upstream (RST mid-response) | Clients receiving partial responses; `curl` shows `curl: (56) Recv failure: Connection reset by peer`; 502 errors | `journalctl -u caddy \| grep -i "reset\|RST\|broken pipe"`; `tcpdump -i any tcp port <upstream-port> and tcp[tcpflags] & tcp-rst != 0` | Upstream crashing or restarting mid-connection; Caddy forwards RST to client | Enable upstream health checks; add `fail_duration 10s` passive failure detection; add retry: `retries 1` for idempotent routes |

## Resource Exhaustion Patterns
| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of Caddy process | Caddy process dies; systemd shows OOMKilled; all HTTP traffic down | `journalctl -u caddy \| grep -E "OOM\|killed"`; `dmesg \| grep -i "caddy\|oom"`; `systemctl status caddy` | `systemctl start caddy`; add `MemoryMax=1G` to systemd override; reduce `keepalive_idle_conns` and `max_conns_per_host` | Set `MemoryMax` in systemd unit; monitor `go_memstats_alloc_bytes_total` Prometheus metric; add `flush_interval -1` for streaming |
| Disk full on TLS certificate storage | Caddy cannot write renewed certificates; ACME renewal fails; existing certs serve until expiry | `df -h /var/lib/caddy`; `du -sh /var/lib/caddy/.local/share/caddy/`; `ls -la /var/lib/caddy/.local/share/caddy/certificates/` | Free space: delete stale/orphaned cert directories; `find /var/lib/caddy -name "*.pem" -mtime +365 -delete`; restart Caddy | Monitor `/var/lib/caddy` disk; Caddy stores one cert + key + metadata per domain; alert at 80% |
| Disk full on access log partition | Log writes failing; Caddy may crash or drop logs; audit trail lost | `df -h /var/log`; `du -sh /var/log/caddy/`; `ls -lhrt /var/log/caddy/` | `logrotate -f /etc/logrotate.d/caddy`; delete oldest logs; set `log { output file /var/log/caddy/access.log { roll_keep 5 } }` | Configure `roll_size 100MB roll_keep 7` in Caddyfile log block; set up logrotate |
| File descriptor exhaustion | New connections refused; `accept tcp: too many open files`; existing connections continue | `lsof -p $(pgrep caddy) \| wc -l`; `cat /proc/$(pgrep caddy)/limits \| grep "open files"`; `journalctl -u caddy \| grep "too many open"` | `systemctl edit caddy` add `LimitNOFILE=131072`; `systemctl daemon-reload && systemctl restart caddy` | Pre-set `LimitNOFILE=131072` in caddy.service override; estimate: 2 FDs per active connection + 2 per upstream connection |
| Inode exhaustion from ACME temp files | Caddy ACME renewal fails; disk shows free blocks but `df -i` shows 100% inodes | `df -i /var/lib/caddy`; `find /var/lib/caddy -type f \| wc -l`; `find /tmp -name ".caddy*" \| wc -l` | Delete orphaned ACME temp/lock files: `find /var/lib/caddy -name "*.tmp" -delete`; `find /tmp -name ".caddy*" -delete` | Monitor inode usage; Caddy creates temp files during cert issuance; clean up regularly |
| CPU throttle from systemd cgroup | Caddy latency intermittently high; CPU steal apparent in profiling; `systemd-cgtop` shows caddy throttled | `systemd-cgtop -n 3 \| grep caddy`; `cat /sys/fs/cgroup/system.slice/caddy.service/cpu.stat \| grep throttled_usec` | `systemctl set-property caddy CPUQuota=400%`; or remove CPU limit: `systemctl set-property caddy CPUQuota=` | Caddy is latency-critical; avoid hard CPU caps; grant `CPUWeight=200` priority instead |
| Swap exhaustion from goroutine memory growth | System swap at 100%; Caddy response latency > 1s; goroutine stack pages being swapped | `free -h`; `vmstat 1 5`; `cat /proc/$(pgrep caddy)/status \| grep VmSwap`; `curl -s http://localhost:2019/metrics \| grep go_goroutines` | Fix goroutine leak (set timeouts); `systemctl restart caddy`; `swapoff -a && swapon -a` if RAM available | Set upstream timeouts; monitor `go_goroutines` metric; alert if goroutine count > 10000 |
| Kernel PID/thread limit | Caddy unable to create new goroutine OS threads; requests stalling; Go runtime errors in logs | `cat /proc/sys/kernel/threads-max`; `ps -eLf \| grep caddy \| wc -l`; `journalctl -u caddy \| grep "clone\|fork\|thread"` | `sysctl -w kernel.threads-max=65536`; persist in `/etc/sysctl.d/99-caddy.conf`; `systemctl restart caddy` | Pre-set thread limits in provisioning; Go runtime uses OS threads for blocking syscalls |
| Network socket buffer exhaustion | TCP send buffer full for slow clients; Caddy goroutines blocked on `write()`; latency spikes | `sysctl net.core.wmem_max`; `ss -tnp \| grep caddy \| grep "rcv\|send"` column values | `sysctl -w net.core.wmem_default=26214400 net.core.wmem_max=26214400`; persist in sysctl.d | Tune socket buffers on high-bandwidth servers; set `net.ipv4.tcp_wmem = 4096 87380 16777216` |
| Ephemeral port exhaustion (reverse proxy outbound) | Caddy cannot open new connections to upstream; 502 errors; `ss -tnp \| grep caddy \| grep TIME_WAIT` flooding | `ss -tnp state time-wait \| grep caddy \| wc -l`; `sysctl net.ipv4.ip_local_port_range` | `sysctl -w net.ipv4.ip_local_port_range="1024 65535"`; enable `net.ipv4.tcp_tw_reuse=1`; enable keepalives in `transport http { keepalive 1m }` | Enable HTTP keepalives to upstreams (reuses connections); set `keepalive_idle_conns 100` to maintain pool |

## Distributed Transaction & Event Ordering Failures
| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation — duplicate request to upstream from retry | Client receives response but network drop causes Caddy retry; upstream processes request twice | `journalctl -u caddy \| grep "retrying"`; `curl -s http://localhost:2019/metrics \| grep caddy_reverse_proxy_requests_total`; upstream logs showing duplicate transaction IDs | Duplicate orders, double-charges, or duplicate DB writes in upstream app | Set `retries 0` for non-idempotent routes (POST/PATCH); only enable `retries 1` for GET and idempotent endpoints; document retry policy |
| Partial ACME order — DNS TXT created but cert not issued | DNS-01 challenge TXT record left in DNS after failed order; next renewal attempt sees stale challenge | `kubectl get challenges -A` (if using cert-manager) or `journalctl -u caddy \| grep "challenge\|order"`; `dig _acme-challenge.<domain> TXT` | Stale challenge records confuse future ACME orders; subsequent renewals fail | Delete stale TXT records from DNS; delete Caddy's ACME order state in `/var/lib/caddy/.local/share/caddy/`; force re-issue by restarting Caddy |
| Out-of-order config reload during rolling restart | Two `caddy reload` commands issued in quick succession; second reload applies older config | `journalctl -u caddy \| grep -E "reloading\|config loaded\|config version"`; `curl -s http://localhost:2019/config/ \| python3 -m json.tool \| grep version` | Active config may not match intended state; routes may be missing or duplicated | Run `curl -s http://localhost:2019/config/` to verify current config; reapply correct Caddyfile with `caddy reload` and verify |
| Cross-service deadlock — circular reverse proxy | Service A proxies to B, B proxies back to A; requests loop; Caddy goroutines and FDs climbing | `curl -s http://localhost:2019/metrics \| grep go_goroutines`; `curl -v https://<host>/problematic-route 2>&1 \| grep "< HTTP"`; `ss -tnp \| grep caddy \| wc -l` | Goroutine exhaustion; OOM kill; all traffic on affected vhosts fails | Add `max_requests` or circuit breaker; fix routing topology; `systemctl restart caddy` to clear goroutine pile |
| Distributed lock expiry mid-certificate-issuance | Caddy cluster node A starts ACME order; lock expires before cert saved; node B starts duplicate order | `journalctl -u caddy \| grep "lock\|obtain\|ACME"`; `ls -la /var/lib/caddy/.local/share/caddy/locks/`; check lock file timestamps | Duplicate ACME orders for same domain; rate limit consumption; potential cert mismatch between cluster nodes | Ensure cluster uses shared cert storage (e.g., S3 or NFS); use Caddy's `storage` directive pointing to shared backend; avoid split cert storage |
| At-least-once event delivery — webhook forwarded multiple times | Upstream webhook endpoint receives same event multiple times; Caddy retried after upstream timeout | `journalctl -u caddy \| grep -i "retry\|504\|timeout"`; `curl -s http://localhost:2019/metrics \| grep caddy_reverse_proxy_requests_total{code="504"}` | Duplicate webhook processing; side effects triggered multiple times | Disable retries for webhook routes: `reverse_proxy { retries 0 }`; ensure upstream implements idempotency key check |
| Compensating transaction failure — TLS cert rollback to expired cert | Caddy reload with new cert fails validation; Caddy rolls back to previous cert which is expired | `journalctl -u caddy \| grep -E "error\|invalid\|rollback\|certificate"`; `echo \| openssl s_client -connect <host>:443 2>&1 \| openssl x509 -noout -enddate` | HTTPS broken; users see certificate error; service effectively down | Fix new cert issue (validate with `caddy validate`); manually place valid cert and key at configured paths; `systemctl restart caddy` |
| Out-of-order TLS certificate update in shared storage | Caddy cluster: node A writes new cert; node B reads partial write; serves malformed cert | `journalctl -u caddy \| grep -i "tls\|certificate\|storage\|corrupt"`; `echo \| openssl s_client -connect <node-b-ip>:443 2>&1 \| grep "verify error"` | Intermittent TLS handshake failures depending on which cluster node serves the request | Use atomic writes for cert storage backend; prefer Caddy's built-in storage locking; restart affected node to reload cert from storage |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — single vhost TLS handshake storm | `curl -s http://localhost:2019/metrics | grep go_goroutines`; one vhost consuming disproportionate CPU during connection surge | Other vhosts experience elevated TLS handshake latency; `caddy_http_request_duration_seconds` rises for all hosts | Rate-limit connections to noisy vhost at firewall: `iptables -A INPUT -p tcp --dport 443 -s <noisy-source-cidr> -m connlimit --connlimit-above 50 -j DROP` | Enable per-vhost rate limiting via caddy-ratelimit plugin; use `@<vhost>` matcher: `route @<vhost> { rate_limit ... }` |
| Memory pressure from large response buffering by one upstream | `curl -s http://localhost:2019/metrics | grep go_memstats_alloc_bytes`; one upstream returning large payloads buffering in memory | Other vhost responses delayed by GC pressure; tail latency rising cluster-wide | Add `flush_interval -1` to streaming upstream: `reverse_proxy <upstream> { flush_interval -1 }` | Set `request_body { max_size 50MB }` per vhost; use `flush_interval -1` to avoid buffering large responses |
| Disk I/O saturation from access log writes (one high-traffic vhost) | `iostat -x 1 | grep <log-disk>`; `du -sh /var/log/caddy/`; one access log growing at GB/hr rate | All vhosts' access log writes delayed; structured log delivery lagging | Set `log { output file /var/log/caddy/<vhost>.log { roll_size 100MB roll_keep 3 } }` per vhost | Route high-traffic vhost logs to separate disk or reduce log verbosity: `log { level WARN }`; use log sampling |
| Network bandwidth monopoly from large file download via `file_server` | `ss -tnp | grep caddy`; watching socket send buffer per connection; `iftop -n` on Caddy host | Other vhost responses throttled; TCP kernel buffers saturated | Use `header_down Content-Disposition attachment` and `limits` block to cap concurrent downloads | Implement bandwidth throttling via upstream rate limiter; add `X-Accel-Buffering: no` for streaming; use CDN offload for large file serving |
| Connection pool starvation — one upstream monopolizing keepalive pool | `curl -s http://localhost:2019/metrics | grep caddy_reverse_proxy_upstreams_healthy`; `ss -tnp | grep caddy | grep <upstream-ip> | wc -l` | Other upstreams cannot get keepalive connections; cold-start latency for all new upstream connections | Set `max_conns_per_host 50` per upstream in `transport http { }` block to cap pool size | Tune `keepalive_idle_conns` per vhost; use separate `reverse_proxy` blocks with explicit transport limits per upstream |
| Quota enforcement gap — no per-vhost request size limit | One vhost receiving large upload POSTs consuming all Caddy's file descriptor and memory budget | All vhosts affected when Caddy nears OOM due to large upload buffering | Add `request_body { max_size 10MB }` to the problematic vhost route immediately | Enforce `request_body { max_size }` on all vhosts; set global `limits` in Caddy global block |
| Cross-tenant data leak risk — shared TLS session tickets | Caddy cluster nodes sharing TLS session tickets; session resumption across tenants on multi-tenant Caddy | TLS session state from one tenant resumable by another tenant if they share session ticket keys | Caddy rotates session ticket keys automatically every 24h; for multi-tenant isolation, use separate Caddy instances per tenant | Deploy separate Caddy instances per security domain; do not use shared TLS session ticket keys across trust boundaries |
| Rate limit bypass via X-Forwarded-For header spoofing | `curl -H "X-Forwarded-For: 1.2.3.4" https://<domain>/limited-endpoint`; if rate limit not triggered, bypass confirmed | Rate limit applied to spoofed IP instead of real client; abuse bypasses per-IP limits | `rate_limit { zone_key {http.request.remote_host} }` — use `remote_host` not `X-Forwarded-For` for origin-side rate limiting | Configure `trusted_proxies` in Caddyfile global block; use `{http.request.remote_host}` as rate limit key behind trusted proxy |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure (Caddy Prometheus endpoint down) | Caddy dashboards blank; no `caddy_http_*` metrics in Prometheus; `up{job="caddy"}` == 0 | Caddy metrics endpoint not configured (`metrics` global block missing) or Prometheus scrape job misconfigured | `curl -s http://localhost:2019/metrics | head -10`; if empty, metrics not enabled | Add `metrics` to Caddy global block; add `handle /metrics { metrics }` route; configure Prometheus scrape job |
| Trace sampling gap — reverse proxy upstream latency not traced | Slow upstream responses not captured in distributed trace; Caddy latency invisible in Jaeger/Zipkin | Caddy does not natively propagate W3C Trace-Context unless configured; upstream traces disconnected from client trace | `curl -s http://localhost:2019/metrics | grep caddy_reverse_proxy_upstreams_latency`; correlate with upstream logs | Configure `header_up X-Request-Id {http.request.uuid}` to inject correlation ID; use `tracing` global block if available |
| Log pipeline silent drop (structured log JSON parse failure) | Caddy access logs present but log aggregator (Fluentd/Vector) silently discarding malformed JSON lines | Caddy log contains non-JSON lines (startup messages, panics) mixed with JSON access logs; parser fails silently | `journalctl -u caddy | head -20` to see if startup logs are mixed; `journalctl -u caddy | python3 -c "import sys,json;[json.loads(l) for l in sys.stdin]"` | Configure separate log output: `log { output file /var/log/caddy/access.log { format json } }`; filter non-JSON lines in log pipeline |
| Alert rule misconfiguration — 5xx errors not alerted because using HTTP/2 | 5xx error spike not triggering alert; Prometheus query using wrong status label value | Caddy emits `status="502"` but alert query filters `status=~"5.."` which may not match string labels | `curl -s http://localhost:2019/metrics | grep 'caddy_http_response_status_count_total{.*5'` | Validate Prometheus query against actual metric labels: `caddy_http_response_status_count_total`; use `status=~"5[0-9][0-9]"` |
| Cardinality explosion from per-path metrics | Prometheus TSDB OOM; Caddy metrics consuming excessive memory; dashboards timing out | `handle_path` or `uri` matcher creating per-URL-path label causing cardinality explosion with dynamic paths | `curl -s http://localhost:2019/metrics | wc -l` to estimate metric volume; `grep "caddy_http" http://localhost:2019/metrics | cut -d'{' -f1 | sort -u` | Normalize path labels; avoid including dynamic URL segments in metrics; use `uri replace` to group paths |
| Missing health endpoint for Caddy itself | Load balancer routing to unhealthy Caddy instance; Caddy process up but not serving | Caddy has no built-in `/health` endpoint by default; LB TCP check passes even if Caddy is degraded | `curl -sf http://localhost:2019/config/ > /dev/null && echo healthy` as LB health script; or `curl -sf https://<domain>/__caddy_health` | Add health endpoint to Caddyfile: `handle /__health { respond "OK" 200 }`; configure LB to probe this path |
| Instrumentation gap — ACME renewal failures not monitored | TLS certificates expiring silently; no alert until cert is expired and clients see errors | ACME renewal failures logged but no Prometheus metric emitted by default; `cert_manager_*` metrics absent for Caddy-managed certs | `echo | openssl s_client -connect <domain>:443 2>&1 | openssl x509 -noout -enddate`; `journalctl -u caddy | grep -i "renew\|acme\|failed"` | Deploy cert expiry exporter alongside Caddy; alert on `ssl_certificate_expiry_seconds < 604800` (7 days) |
| Alertmanager outage during Caddy incident | On-call not paged during Caddy TLS failure; Prometheus firing alerts but no PagerDuty notification | Alertmanager pod down; webhook endpoint unreachable; no dead-man switch configured | `curl -s http://alertmanager:9093/api/v1/status`; `curl -s http://prometheus:9090/api/v1/alerts | python3 -m json.tool | grep caddy` | Configure Prometheus `DeadMansSwitch` alert: `absent(up{job="caddy"})`; use Alertmanager's watchdog/heartbeat integration |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor Caddy version upgrade rollback (e.g., v2.6 → v2.7) | Config directive syntax changed; `caddy run` fails at startup; new version rejects old Caddyfile syntax | `caddy validate --config /etc/caddy/Caddyfile 2>&1`; `journalctl -u caddy | grep "error\|unrecognized"` | `apt install caddy=2.6.*` or replace binary with previous version; `systemctl restart caddy` | Validate Caddyfile against new version before upgrade: `caddy validate`; test in staging environment first |
| Major Caddy upgrade (v1 → v2 — complete config format change) | Caddy v1 JSON/config format rejected by v2; all routes fail to load; service down | `caddy run --config /etc/caddy/Caddyfile 2>&1 | head -20`; check for `v1` config format indicators | Keep Caddy v1 binary as fallback; restore v1 config; point systemd unit to v1 binary temporarily | Convert Caddyfile to v2 format using `caddy adapt`; test in staging; do not upgrade v1→v2 in-place in production |
| Schema migration partial completion — Caddy admin API config partially applied | Some routes updated via `PATCH /config/apps/http/servers/srv0/routes/0` but others not; inconsistent routing state | `curl -s http://localhost:2019/config/apps/http/servers/ | python3 -m json.tool`; compare with intended state | Reapply full Caddyfile: `caddy reload --config /etc/caddy/Caddyfile --force` to atomically replace config | Always use `caddy reload` for full config replacement rather than partial admin API patches in production |
| Rolling upgrade version skew (Caddy behind LB with mixed versions) | Some Caddy nodes on new version, some on old; inconsistent behavior for TLS session resumption and header handling | `curl -si https://<domain>/ | grep Server`; check Caddy version in response or `journalctl -u caddy | grep "Caddy"` version line | Pin all LB backends to same Caddy version; drain new-version nodes from LB: update LB backend weights | Upgrade all Caddy instances atomically during maintenance window; use blue-green deployment for version upgrades |
| Zero-downtime migration gone wrong (Caddyfile hot reload causing connection drops) | `caddy reload` drops in-flight requests; clients see connection reset; reload takes longer than expected | `journalctl -u caddy | grep -E "reloading\|reload complete\|connection reset"`; `curl -s http://localhost:2019/metrics | grep caddy_http_requests_in_flight` | Revert config: `git -C /etc/caddy checkout -- Caddyfile && caddy reload` | Use `caddy reload` (graceful) not `systemctl restart caddy` (drops connections); validate config first: `caddy validate` |
| Config format change — deprecated `tls` directive behavior | After upgrade, TLS config silently falls back to defaults; unexpected TLS protocol versions or cipher suites | `nmap --script ssl-enum-ciphers -p 443 <host>`; `openssl s_client -connect <host>:443`; `journalctl -u caddy | grep "tls\|cipher"` | Revert Caddyfile to explicit TLS config; `caddy reload` | Always specify TLS config explicitly in Caddyfile: `tls { protocols tls1.2 tls1.3 }`; review TLS defaults in Caddy release notes |
| Data format incompatibility — ACME account key format change | After upgrade or account key migration, Caddy cannot read existing ACME account; creates duplicate LE account; hits rate limit | `ls -la /var/lib/caddy/.local/share/caddy/`; `journalctl -u caddy | grep "acme\|account\|malformed"` | Restore ACME account key from backup; or delete account directory to force new registration (may hit rate limit) | Back up `/var/lib/caddy/.local/share/caddy/` before upgrading; ACME accounts are not portable across incompatible formats |
| Dependency version conflict — Go runtime upgrade in new Caddy affecting TLS | New Caddy binary compiled with newer Go TLS stack; cipher suite priority changed; legacy clients failing | `nmap --script ssl-enum-ciphers -p 443 <host>`; `journalctl -u caddy | grep "handshake\|cipher\|tls"`; test with old Java client | Downgrade Caddy to previous Go-compiled version; or explicitly configure `tls { ciphers ... }` to restore cipher order | Test TLS compatibility with representative clients before upgrading; use `caddy run` in staging with TLS probe |

## Kernel/OS & Host-Level Failure Patterns
**Minimum cross-cutting cases to evaluate here:** OOM killer false kill, inode exhaustion, CPU steal, NTP skew affecting locks, leases, or coordination, file descriptor exhaustion, and TCP conntrack table saturation.

| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|-------------------|--------------|-------------|----------------------|
| OOM killer terminates Caddy process | `dmesg | grep -i "oom\|killed" | grep caddy`; `journalctl -u caddy | grep -i "killed"`; `systemctl is-active caddy` | Caddy response buffering in memory; large file uploads or reverse proxy response bodies cached; Go GC not keeping up | All reverse proxy and HTTPS serving stops; upstream services unreachable through Caddy; ACME renewal goroutines lost | `systemctl restart caddy`; set `response_max_size` in Caddyfile; add `MemoryMax=512M` to caddy systemd override; `ulimit -v` check |
| Inode exhaustion on Caddy log/data partition | `df -i /var/log/caddy`; `find /data/caddy -type f | wc -l`; `ls /data/caddy/certificates/ | wc -l` | ACME cert storage creating many small files (one dir per domain); large number of virtual hosts; log rotation not purging old files | Caddy cannot write access logs; ACME cannot store new certificates; TLS cert renewal fails silently | `find /data/caddy/certificates -name "*.json" -mtime +90 -delete`; `logrotate -f /etc/logrotate.d/caddy`; mount certificate store on separate partition |
| CPU steal spike causing TLS handshake timeouts | `top` showing `%st > 15`; `curl -w "%{time_connect}:%{time_appconnect}" https://<site>`; Caddy TLS handshake histogram spiking | Hypervisor overcommit; cloud burstable instance `t3` CPU credit exhaustion; CPU-intensive TLS 1.3 handshakes affected most | HTTPS connection establishment slow; clients seeing TLS timeout; Go TLS goroutines piling up | `iostat -c 1 5` to confirm steal; migrate to non-burstable instance; check CPU credits: `aws cloudwatch get-metric-statistics --metric-name CPUCreditBalance`; enable TLS session resumption to reduce handshake CPU |
| NTP clock skew causing ACME certificate validation failure | `chronyc tracking | grep "System time"`; `timedatectl status`; `journalctl -u caddy | grep -E "ACME\|certificate\|time"` | NTP daemon stopped; clock skew > 30 seconds causes ACME challenge `badNonce` or `rateLimited` errors; ACME CA rejects requests with stale timestamps | ACME certificate renewal fails; existing certs eventually expire; HTTPS serving breaks with `CERTIFICATE_EXPIRED` | `systemctl restart chronyd && chronyc makestep`; force cert renewal: `caddy reload --config /etc/caddy/Caddyfile`; check renewal: `journalctl -u caddy | grep "certificate"` |
| File descriptor exhaustion under high connection load | `cat /proc/$(pgrep caddy)/limits | grep "open files"`; `lsof -p $(pgrep caddy) | wc -l`; `journalctl -u caddy | grep "too many open files"` | Default FD limit 1024 too low; each upstream connection = 1 FD; each TLS session = 1 FD; busy reverse proxy hitting limit | Caddy stops accepting new connections; upstream requests fail with connection error; HTTP 502 returned to clients | `systemctl edit caddy` → `LimitNOFILE=65536`; `systemctl daemon-reload && systemctl restart caddy`; verify: `cat /proc/$(pgrep caddy)/limits | grep "open files"` |
| TCP conntrack table full dropping incoming HTTPS connections | `sysctl net.netfilter.nf_conntrack_count`; compare to `nf_conntrack_max`; `dmesg | grep "nf_conntrack: table full"`; `ss -s | grep TIME-WAIT` | High connection rate from many clients; TIME_WAIT sockets consuming conntrack entries; missing `net.ipv4.tcp_tw_reuse` | New HTTPS connections silently dropped by kernel before reaching Caddy; clients see connection refused | `sysctl -w net.netfilter.nf_conntrack_max=524288 net.ipv4.tcp_tw_reuse=1`; persist in `/etc/sysctl.d/99-caddy.conf`; verify: `sysctl net.netfilter.nf_conntrack_count` |
| Kernel panic / node crash losing Caddy TLS state | `dmesg | grep -i "panic\|oops"`; `last reboot`; `ls -la /data/caddy/certificates/`; check if ACME storage files are intact post-reboot | Hardware fault; kernel OOM panic; Caddy data dir on tmpfs not persisted | Caddy restarts but may re-request certs; ACME rate limit hit if too many domains; TLS cert store corruption | `caddy validate --config /etc/caddy/Caddyfile`; check cert files: `ls /data/caddy/certificates/**/*.crt | head`; if corrupt: `rm -rf /data/caddy/certificates && systemctl restart caddy` |
| NUMA memory imbalance causing Go GC pauses in Caddy | `numastat -p caddy`; `GODEBUG=gctrace=1 caddy run 2>&1 | grep "gc"` shows long STW pauses; high P99 latency on specific CPUs | Go runtime allocating across NUMA nodes; GC marking phase crossing NUMA boundary; memory bandwidth saturation | Intermittent high-latency responses (P99 spike); correlated with GC pause duration; affects all upstream proxy requests | `numactl --cpunodebind=0 --membind=0 caddy run --config /etc/caddy/Caddyfile`; or add `GOGC=200` env to reduce GC frequency; set `GOMAXPROCS` to CPUs on single NUMA node |

## Deployment Pipeline & GitOps Failure Patterns
**Minimum cross-cutting cases to evaluate here:** image pull failure (rate limit or auth), Helm drift, ArgoCD sync stuck, PodDisruptionBudget-blocked rollout, blue-green cutover failure, and ConfigMap or Secret drift.

| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|----------------|-------------------|---------------|------------|
| Image pull rate limit hitting Caddy container | Caddy container stuck in `ImagePullBackOff`; events show `toomanyrequests` from Docker Hub | `kubectl describe pod -l app=caddy | grep -A5 "Events:"`; `kubectl get events | grep "caddy.*pull"` | `kubectl create secret docker-registry dockerhub-creds --docker-server=docker.io`; patch deployment to use secret | Mirror `caddy:2` to private ECR/GCR; set `imagePullPolicy: IfNotPresent`; use authenticated pull via imagePullSecrets |
| Image pull auth failure for private Caddy image | Caddy pod in `ErrImagePull`; registry credential expired; `unauthorized: authentication required` | `kubectl describe pod -l app=caddy | grep "unauthorized"`; `kubectl get secret regcred -n web` | Rotate registry credentials: `kubectl delete secret regcred && kubectl create secret docker-registry regcred ...`; rolling restart | Use IRSA (EKS) or Workload Identity (GKE) for registry auth; rotate secrets via External Secrets Operator |
| Helm chart drift — Caddyfile ConfigMap diverged from chart values | Caddy serving old routes; new upstream endpoints not registered; `caddy reload` not triggered | `helm diff upgrade caddy ./charts/caddy -n web`; `kubectl get cm caddy-config -o yaml | diff - charts/caddy/templates/caddyfile-cm.yaml` | `helm rollback caddy <revision> -n web`; `kubectl exec -it $(kubectl get pod -l app=caddy -o name) -- caddy reload --config /etc/caddy/Caddyfile` | Run `caddy validate` as Helm pre-upgrade hook; enable ArgoCD auto-sync with self-heal |
| ArgoCD sync stuck on Caddy namespace | ArgoCD app `caddy` shows `OutOfSync` indefinitely; Caddyfile changes not applied | `argocd app get caddy --show-operation`; `argocd app logs caddy`; `kubectl get events -n web --sort-by=.lastTimestamp | tail -20` | `argocd app terminate-op caddy && argocd app sync caddy --force` | Add `syncPolicy.automated.selfHeal: true`; define Caddy Deployment as ArgoCD managed resource; exclude manually managed TLS secrets from sync |
| PodDisruptionBudget blocking Caddy rolling update | `kubectl rollout status deployment/caddy` stuck; PDB blocking pod eviction; live traffic on single pod | `kubectl get pdb -n web`; `kubectl describe pdb caddy-pdb | grep "Allowed disruptions"` | Temporarily set `minAvailable: 0`: `kubectl patch pdb caddy-pdb -p '{"spec":{"minAvailable":0}}'`; complete rollout; restore PDB | Set `minAvailable: 1` with replicas ≥ 2; use `maxUnavailable: 1` in Deployment rolling update strategy |
| Blue-green traffic switch failure — new Caddy pod missing routes | New Caddy pod deployed but Caddyfile ConfigMap not updated; returning 404 for new routes | `kubectl exec -it caddy-green-pod -- curl -s localhost:2019/config/ | python3 -m json.tool | grep routes`; `curl -sf https://<site>/api/health` | Revert service selector to blue: `kubectl patch svc caddy -p '{"spec":{"selector":{"version":"blue"}}}'` | Add readiness probe: `httpGet: path: /health port: 2019`; validate Caddy config API before switching VIP |
| ConfigMap/Secret drift — TLS cert Secret updated in git but not in cluster | HTTPS serving old expired certificate; `curl -vI https://<site> 2>&1 | grep "expire date"` shows old cert | `kubectl get secret caddy-tls -n web -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -dates`; compare to expected cert | `kubectl create secret tls caddy-tls --cert=new.crt --key=new.key -n web --dry-run=client -o yaml | kubectl apply -f -`; `kubectl rollout restart deployment/caddy -n web` | Use cert-manager for auto-rotation; External Secrets Operator to sync from Vault; avoid manual cert Secret management |
| Feature flag stuck — `on_demand_tls` enabled but ACME rate limited | On-demand TLS attempting to provision certs for unexpected hostnames; ACME rate limit `429 urn:ietf:params:acme:error:rateLimited` | `journalctl -u caddy | grep "rateLimited\|on_demand"`; `curl -s https://acme-v02.api.letsencrypt.org/directory | python3 -m json.tool | grep "newNonce"` | Disable `on_demand` temporarily: update Caddyfile to explicit host list; `caddy reload --config /etc/caddy/Caddyfile` | Use `on_demand_tls` only with `ask` endpoint for hostname validation; pre-provision certs for known domains |

## Service Mesh & API Gateway Edge Cases
**Minimum cross-cutting cases to evaluate here:** circuit breaker false positives, rate limiting on legitimate traffic, stale service discovery endpoints, mTLS rotation interruption, retry storm amplification, gRPC keepalive or max-message failures, and trace context loss.

| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive tripping on Caddy pod | Istio marking Caddy pod as unhealthy due to transient 502 on upstream restart; healthy Caddy ejected | `istioctl proxy-config cluster <client-pod>.default | grep caddy`; `kubectl exec <pod> -c istio-proxy -- curl -s localhost:15000/stats | grep "outlier_detection.ejections_active"` | All upstream traffic routed away from healthy Caddy pod; single pod overloaded | `kubectl annotate pod <caddy-pod> traffic.sidecar.istio.io/excludeInboundPorts="80,443"`; or set DestinationRule `consecutiveGatewayErrors: 10` to raise threshold |
| Rate limit hitting legitimate high-traffic routes | Envoy rate limit policy returning 429 to valid API clients via Caddy; `caddy access logs show 429 from envoy` | `kubectl get envoyfilter -n web | grep ratelimit`; `kubectl exec -c istio-proxy <pod> -- curl -s localhost:15000/stats | grep ratelimit`; check if Caddy is behind Envoy rate limiter | High-traffic endpoints throttled; SLA breach for legitimate clients | Increase rate limit for specific routes: update EnvoyFilter `rate_limit` config; or move rate limiting into Caddyfile with `rate_limit` plugin to avoid double-limiting |
| Stale service discovery — Caddy upstream address pointing to terminated pod | Caddy reverse proxy returning `502 Bad Gateway` for specific backend; backend pod was replaced | `curl -s localhost:2019/config/ | python3 -m json.tool | grep upstreams`; `kubectl get endpoints <upstream-svc> -o yaml | grep ip`; `nslookup <upstream-svc>` | Fraction of requests returning 502; correlated with pod restarts | Update Caddy to use Kubernetes Service DNS name instead of pod IP: replace upstream IP with `<svc-name>.<namespace>.svc.cluster.local`; `caddy reload` |
| mTLS rotation breaking Caddy upstream TLS connections | Caddy proxy connections to mTLS-protected upstream failing during cert rotation; `caddy logs show "x509: certificate has expired"` | `istioctl proxy-config secret <caddy-pod> | grep -E "VALID|EXPIRED"`; `kubectl exec <caddy-pod> -- curl -svk https://<upstream>:8443 2>&1 | grep "issuer"` | Upstream API calls failing through Caddy; HTTP 502 returned to clients | Add Caddy `transport http { tls_insecure_skip_verify }` temporarily during rotation; or force Istio cert refresh: `kubectl delete secret istio.<caddy-pod-sa> -n web` |
| Retry storm — Caddy retrying failed upstream, amplifying load | Caddy `reverse_proxy` retrying 3x on 502; upstream receiving 3x traffic volume; cascade failure | `kubectl logs -l app=caddy -n web | grep "retrying"` or `curl -s localhost:2019/metrics | grep "retry"`; check upstream pod CPU spike | Upstream service CPU saturation; recovery time extended; retry amplification loop | Set `reverse_proxy` `max_fails 1 fail_duration 10s` to reduce retry aggressiveness; add `lb_try_duration 5s`; handle retries at application level with exponential backoff |
| gRPC max message size causing proxy failure | Caddy proxying gRPC responses truncated; client receives `code = ResourceExhausted desc = grpc: received message larger than max` | `kubectl logs -l app=caddy | grep "grpc\|message size"`; test: `grpc_cli call <caddy-host>:443 <method> ''`; `curl -H "Content-Type: application/grpc" https://<site>/service` | Large gRPC responses (>4MB default) failing through Caddy; only affects specific gRPC endpoints | Add Caddyfile `@grpc protocol grpc` matcher with `reverse_proxy` and `header_up grpc-max-message-size 67108864`; or use `grpc_pass` directive |
| Trace context propagation gap — Caddy not forwarding W3C trace headers | Distributed traces show broken spans at Caddy proxy boundary; downstream services missing trace context | `curl -H "traceparent: 00-abc123-abc123-01" https://<site>/api/endpoint -v 2>&1 | grep traceparent`; check if Caddy strips `traceparent`/`tracestate` headers | Service map incomplete in Jaeger/Zipkin; latency root cause invisible; SLO breach uninvestigated | Add Caddyfile `header_up traceparent {http.request.header.traceparent}` in reverse_proxy block; enable Caddy OpenTelemetry plugin: `tracing { span caddy }` |
| Load balancer health check misconfiguration excluding healthy Caddy pods | ALB/NLB marking Caddy targets unhealthy; pods serving traffic but LB not routing to them | `kubectl describe pod -l app=caddy | grep -E "Readiness|Liveness"`; `aws elbv2 describe-target-health --target-group-arn <arn>`; `curl -f http://<pod-ip>:2019/health` | Traffic concentrated on few pods or no pods; overload or 503 | Fix LB health check to use Caddy admin API: `GET /health HTTP/1.1 Host: localhost:2019`; or add Caddyfile `respond /health 200` block; set `healthyThresholdCount: 2` |
