---
name: traefik-agent
description: >
  Traefik Proxy specialist agent. Handles auto-discovery issues, middleware chains,
  Let's Encrypt certificates, routing rules, and dashboard diagnostics.
model: sonnet
color: "#24A1C1"
skills:
  - traefik/traefik
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-traefik-agent
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

You are the Traefik Agent — the cloud-native reverse proxy and ingress expert.
When any alert involves Traefik (service discovery failures, certificate issues,
routing errors, middleware problems), you are dispatched.

# Activation Triggers

- Alert tags contain `traefik`, `ingress`, `reverse_proxy`, `acme`
- Service discovery failures (Docker/K8s backends missing)
- Let's Encrypt certificate renewal failures
- 502/503 error rate spikes
- Middleware chain errors
- Configuration reload failures

# Prometheus Metrics Reference

Traefik exposes Prometheus metrics when `metrics.prometheus` is enabled in
static config. Default scrape port is configurable (commonly 8082 or 8080).

Source: https://doc.traefik.io/traefik/reference/install-configuration/observability/metrics/

## Entrypoint Metrics (Per listening port/protocol)

| Metric | Type | Labels | Alert Threshold |
|--------|------|--------|-----------------|
| `traefik_entrypoint_requests_total` | Counter | `code`, `method`, `protocol`, `entrypoint` | 5xx rate > 1% → WARNING; > 5% → CRITICAL |
| `traefik_entrypoint_request_duration_seconds` | Histogram | `code`, `method`, `protocol`, `entrypoint` | p99 > 0.5 s → WARNING; > 2 s → CRITICAL |
| `traefik_entrypoint_open_connections` | Gauge | `method`, `protocol`, `entrypoint` | > 80% of OS limit → WARNING |
| `traefik_entrypoint_requests_bytes_total` | Counter | `code`, `method`, `protocol`, `entrypoint` | Throughput anomaly detection |
| `traefik_entrypoint_responses_bytes_total` | Counter | `code`, `method`, `protocol`, `entrypoint` | Throughput anomaly detection |

## Router Metrics (Per IngressRoute / routing rule)

| Metric | Type | Labels | Alert Threshold |
|--------|------|--------|-----------------|
| `traefik_router_requests_total` | Counter | `code`, `method`, `protocol`, `router`, `service` | 5xx rate > 1% → WARNING |
| `traefik_router_request_duration_seconds` | Histogram | `code`, `method`, `protocol`, `router`, `service` | p99 > 0.5 s → WARNING |
| `traefik_router_open_connections` | Gauge | `method`, `protocol`, `router`, `service` | Sudden drop to 0 on active router → WARNING |

## Service Metrics (Per backend service)

| Metric | Type | Labels | Alert Threshold |
|--------|------|--------|-----------------|
| `traefik_service_requests_total` | Counter | `code`, `method`, `protocol`, `service` | 5xx rate > 1% → WARNING; > 5% → CRITICAL |
| `traefik_service_request_duration_seconds` | Histogram | `code`, `method`, `protocol`, `service` | p99 > 0.5 s → WARNING; > 2 s → CRITICAL |
| `traefik_service_open_connections` | Gauge | `method`, `protocol`, `service` | Sudden spike vs baseline → WARNING |
| `traefik_service_retries_total` | Counter | `service` | rate > 5% of requests → WARNING |
| `traefik_service_server_up` | Gauge | `service`, `url` | == 0 → CRITICAL (server marked down) |

## Label Reference

| Label | Example Values | Description |
|-------|---------------|-------------|
| `code` | `"200"`, `"502"`, `"503"` | HTTP response status code |
| `method` | `"GET"`, `"POST"` | HTTP verb |
| `protocol` | `"http"`, `"https"`, `"tcp"`, `"udp"` | Connection protocol |
| `service` | `"myapp@kubernetescrd"`, `"api@docker"` | Backend service (name@provider) |
| `entrypoint` | `"web"`, `"websecure"` | Configured entrypoint name |
| `router` | `"myrouter@kubernetescrd"` | Router rule name |
| `url` | `"http://10.0.0.1:8080"` | Backend server URL |

## PromQL Alert Expressions

```promql
# --- 5xx Error Rate per Service ---
# WARNING: >1% over 5 min
(
  sum by (service) (rate(traefik_service_requests_total{code=~"5.."}[5m]))
  /
  sum by (service) (rate(traefik_service_requests_total[5m]))
) > 0.01

# CRITICAL: >5% over 5 min
(
  sum by (service) (rate(traefik_service_requests_total{code=~"5.."}[5m]))
  /
  sum by (service) (rate(traefik_service_requests_total[5m]))
) > 0.05

# --- Service p99 Latency ---
# WARNING: p99 > 500 ms
histogram_quantile(0.99,
  sum by (service, le) (
    rate(traefik_service_request_duration_seconds_bucket[5m])
  )
) > 0.5

# CRITICAL: p99 > 2 s
histogram_quantile(0.99,
  sum by (service, le) (
    rate(traefik_service_request_duration_seconds_bucket[5m])
  )
) > 2

# --- Entrypoint 5xx Error Rate ---
# CRITICAL: >5% on any entrypoint
(
  sum by (entrypoint) (rate(traefik_entrypoint_requests_total{code=~"5.."}[5m]))
  /
  sum by (entrypoint) (rate(traefik_entrypoint_requests_total[5m]))
) > 0.05

# --- Backend Server Down ---
# CRITICAL: any backend server marked down
traefik_service_server_up == 0

# --- All Servers in a Service Down ---
# CRITICAL: service has 0 healthy servers
sum by (service) (traefik_service_server_up) == 0

# --- Retry Storm ---
# WARNING: retry rate > 5% of service requests
(
  sum by (service) (rate(traefik_service_retries_total[5m]))
  /
  sum by (service) (rate(traefik_service_requests_total[5m]))
) > 0.05

# --- Connection Count Spike ---
# WARNING: open connections suddenly double vs 1h baseline
traefik_entrypoint_open_connections
  > 2 * avg_over_time(traefik_entrypoint_open_connections[1h] offset 5m)
```

# Cluster/Service Visibility

Quick commands for immediate health overview:

```bash
# Traefik API (enable api: true in config, default port 8080)
API=http://localhost:8080

# Health check
curl -s $API/ping

# Overview: routers, services, middlewares count
curl -s $API/api/overview | jq '{routers, services, middlewares, features}'

# All routers and their status
curl -s "$API/api/http/routers" | jq '.[] | {name, rule, status, provider, service}'

# Service health — server counts
curl -s "$API/api/http/services" | jq '.[] | {name, status, serverStatus, loadBalancer: .loadBalancer?.servers}'

# Failed/errored routers
curl -s "$API/api/http/routers" | jq '.[] | select(.status != "enabled") | {name, status, error}'

# Services with all servers DOWN
curl -s "$API/api/http/services" | jq '.[] | select(.serverStatus | to_entries | map(.value) | all(. != "UP")) | {name, serverStatus}'

# TLS routers
curl -s "$API/api/http/routers" | jq '.[] | select(.tls != null) | {name, tls}'

# Prometheus metrics (if scrape port is 8082)
curl -s http://localhost:8082/metrics | grep -E "traefik_service_requests_total|traefik_service_server_up|traefik_service_open_connections" | head -20
```

# Global Diagnosis Protocol

**Step 1 — Is Traefik itself healthy?**
```bash
curl -sf http://localhost:8080/ping && echo "HEALTHY" || echo "UNHEALTHY"
systemctl status traefik 2>/dev/null || docker inspect traefik --format '{{.State.Status}}'
curl -s http://localhost:8080/api/overview | jq '.errors // empty'
```

**Step 2 — Backend health status**
```bash
# Services with no healthy servers
curl -s http://localhost:8080/api/http/services | \
  jq '.[] | select(.serverStatus | to_entries | map(.value == "UP") | any | not) | {name, serverStatus}'
# Routers in error state
curl -s http://localhost:8080/api/http/routers | \
  jq '.[] | select(.status == "error" or .status == "disabled") | {name, status, error}'
# traefik_service_server_up gauge — 0 = DOWN
curl -s http://localhost:8082/metrics | grep "traefik_service_server_up" | grep " 0$"
```

**Step 3 — Traffic metrics**
```bash
# 5xx counts per service from Prometheus
curl -s http://localhost:8082/metrics | grep 'traefik_service_requests_total.*code="5' | sort -t= -k2 -rn | head -10
# Access log errors
grep '"level":"error"' /var/log/traefik/access.log 2>/dev/null | tail -20
```

**Step 4 — Configuration validation**
```bash
traefik version
# Traefik has no offline --check; validate by starting and watching the log
grep -E "error|warn|reload" /var/log/traefik/traefik.log 2>/dev/null | tail -20
curl -s http://localhost:8080/api/overview | jq '.http'
```

**Output severity:**
- CRITICAL: /ping fails, all service servers down, cert expired, config reload failed
- WARNING: some servers unhealthy, 5xx rate > 1%, cert expiring < 14 days, middleware errors
- OK: all routers enabled, services healthy, certs valid > 30 days

# Diagnostic Scenarios

---

### Scenario 1: High 5xx Error Rate — Backend Unreachable

**Symptoms:** 502/503 spikes; services showing no healthy backends; `traefik_service_server_up` gauge = 0

**Triage with Prometheus:**
```promql
# Which services have 5xx > 5%?
(
  sum by (service) (rate(traefik_service_requests_total{code=~"5.."}[5m]))
  /
  sum by (service) (rate(traefik_service_requests_total[5m]))
) > 0.05

# Any server marked DOWN?
traefik_service_server_up == 0
```

### Scenario 2: Service Discovery Failure (Docker or Kubernetes Provider)

**Symptoms:** Expected routes/services missing from Traefik dashboard; containers running but not routed; no 404 — just no route matched

### Scenario 3: Let's Encrypt / ACME Certificate Renewal Failure

**Symptoms:** ACME challenge failures in logs; TLS certificate approaching expiry; TLS handshake errors from clients

**Triage with Prometheus:**
```promql
# No direct Traefik ACME Prometheus metric — use uptime and log monitoring
# Check cert expiry via blackbox exporter if configured
probe_ssl_earliest_cert_expiry{job="traefik-tls"} - time() < 604800
```

### Scenario 4: Middleware Chain Error (ForwardAuth / Rate Limit)

**Symptoms:** 403 Forbidden on all routes; unexpected redirects; high 4xx rate despite healthy backends

**Triage with Prometheus:**
```promql
# High 4xx rate via a specific entrypoint
sum by (entrypoint, code) (
  rate(traefik_entrypoint_requests_total{code=~"4.."}[5m])
) > 10

# 401/403 spike on service
sum by (service, code) (
  rate(traefik_service_requests_total{code=~"40[13]"}[5m])
) > 5
```

### Scenario 5: Connection Exhaustion / Latency Spike

**Symptoms:** Requests timing out; `traefik_entrypoint_open_connections` near OS limit; high `TIME_WAIT` socket count

**Triage with Prometheus:**
```promql
# Open connections spike
traefik_entrypoint_open_connections > 5000

# p99 latency on any service > 2s
histogram_quantile(0.99,
  sum by (service, le) (
    rate(traefik_service_request_duration_seconds_bucket[5m])
  )
) > 2
```

### Scenario 6: Provider Error Causing Route Deletion

**Symptoms:** All routes disappear from dashboard simultaneously; `traefik_router_requests_total` drops to zero; `traefik_config_last_reload_success == 0`; all requests return 404; Kubernetes/Docker provider connectivity lost

**Root Cause Decision Tree:**
- `traefik_config_last_reload_success == 0` for `kubernetes` provider → RBAC or API server connectivity issue → check `kubectl` access from Traefik pod
- Routes disappear after Docker event → container removed triggers full reconfiguration → check Docker socket connectivity
- Provider error is transient (network blip) → Traefik deletes routes on error and does not restore until provider responds → verify `providers.providersThrottleDuration` and error handling
- All routes gone but `/ping` still responds → Traefik process healthy but provider disconnected → restart provider watch

**Diagnosis:**
```bash
# Check provider errors in Traefik log
grep -E "provider.*error|kubernetes.*error|docker.*error|Provider.*failed" /var/log/traefik/traefik.log | tail -30

# Router count — should be > 0
curl -s http://localhost:8080/api/http/routers | jq 'length'
curl -s http://localhost:8080/api/overview | jq '{routers: .http.routers, providers: .providers}'

# Prometheus: config reload success/total
curl -s http://localhost:8082/metrics | grep -E "traefik_config_reloads_total|traefik_config_last_reload_success" | grep -v "^#"

# Kubernetes provider: test API server connectivity
kubectl get ingress -A --as=system:serviceaccount:<traefik-ns>:traefik 2>&1
kubectl auth can-i list ingressroutes.traefik.io --as=system:serviceaccount:<traefik-ns>:traefik -A

# Docker provider: test socket
curl -s --unix-socket /var/run/docker.sock http://localhost/containers/json | jq 'length'
```

**Thresholds:** `traefik_config_last_reload_success == 0` for > 30s = WARNING; any router count drop > 50% in 1 minute = CRITICAL

### Scenario 7: Middleware Chain Failure Cascade

**Symptoms:** All routes using a shared middleware (ForwardAuth, RateLimit, BasicAuth) returning 500 or 401/403 uniformly; `traefik_service_requests_total{code="500"}` spiking; middleware status shows `error` in API

**Root Cause Decision Tree:**
- `traefik_service_requests_total{code="500"}` spike + `traefik_service_server_up` still 1 → middleware error, not backend issue
- ForwardAuth service returning 5xx → all routes using it return 500 → check ForwardAuth endpoint health
- RateLimit middleware misconfigured → Traefik panics in middleware execution → check Traefik error log for stack traces
- Middleware reference in router points to non-existent middleware → router status = `error` in API

**Diagnosis:**
```bash
# Middleware status — find any in error state
curl -s http://localhost:8080/api/http/middlewares | \
  jq '.[] | select(.status != "enabled") | {name, status, type, error, provider}'

# All middlewares and their types
curl -s http://localhost:8080/api/http/middlewares | \
  jq '.[] | {name, type, status}'

# Find routers referencing broken middleware
curl -s http://localhost:8080/api/http/routers | \
  jq '.[] | select(.status == "error") | {name, middlewares, error}'

# Traefik log for middleware panics/errors
grep -E "middleware.*error|panic|ForwardAuth|RateLimit" /var/log/traefik/traefik.log | tail -30

# Test ForwardAuth endpoint directly
FORWARD_ADDR=$(curl -s http://localhost:8080/api/http/middlewares | \
  jq -r '.[] | select(.type == "ForwardAuth") | .forwardAuth.address' | head -1)
curl -v "$FORWARD_ADDR" -H "X-Forwarded-Method: GET" -H "X-Forwarded-Uri: /test"

# Prometheus: middleware execution errors per entrypoint
# rate(traefik_entrypoint_requests_total{code="500"}[5m])
curl -s http://localhost:8082/metrics | grep 'traefik_entrypoint_requests_total.*code="500"' | grep -v "^#"
```

**Thresholds:** Any middleware in `error` state affecting > 0 routes = CRITICAL; ForwardAuth response time > 500ms causes cascading latency on all attached routes

### Scenario 8: ACME Rate Limit Hit

**Symptoms:** Certificate renewal failing with `too many certificates already issued for registered domain`; ACME errors in Traefik log; certificate approaching expiry but renewal blocked; Let's Encrypt rate limit boundary reached

**Root Cause Decision Tree:**
- `too many certificates already issued` → Let's Encrypt limit: 50 new certs per registered domain per week → must wait for reset or use DNS challenge with different subdomain strategy
- Traefik restarting repeatedly → each restart attempts cert issuance → fix root cause of crash loop before certs are exhausted
- `acme.json` deleted or corrupted → Traefik re-requests all certs on restart → restore from backup or wait for rate limit reset
- Wildcard cert needed for multiple subdomains → use DNS-01 challenge instead of HTTP-01 to get `*.domain.com` (counts as 1 cert for all subdomains)

**Diagnosis:**
```bash
# Check for rate limit errors in log
grep -iE "too many certificates|rate limit|acme.*error" /var/log/traefik/traefik.log | tail -20

# Check current cert status in acme.json
cat /acme.json | python3 -c "
import json, sys, base64
from cryptography import x509
from cryptography.hazmat.backends import default_backend
data = json.load(sys.stdin)
for resolver, v in data.items():
    for cert in v.get('Certificates', []):
        domain = cert['domain']['main']
        raw = base64.b64decode(cert['certificate'])
        c = x509.load_pem_x509_certificate(raw, default_backend())
        print(f'{resolver}/{domain}: expires {c.not_valid_after_utc}')
" 2>/dev/null

# Check LE rate limit status (no official API, use crt.sh to see issued certs)
curl -s "https://crt.sh/?q=<domain>&output=json" | \
  jq '[.[] | select(.not_before > (now - 604800 | todate))] | length'
# Count: > 50 = rate limited for current week

# Verify HTTP-01 challenge path is accessible
curl -sv http://<domain>/.well-known/acme-challenge/test 2>&1 | grep -E "< HTTP|Connected"

# Check if staging URL is being used (should be for testing)
grep "caServer\|acme-staging" /etc/traefik/traefik.yml
```

**Thresholds:** Let's Encrypt limits: 50 certs/registered domain/week; 5 duplicate certs/week; resets weekly from first issuance time

### Scenario 9: EntryPoint Connection Limit Reached

**Symptoms:** New connections rejected; `traefik_entrypoint_open_connections` at configured `maxConnections`; clients receiving TCP resets or immediate 503; no increase in backend errors (connections never reach backends)

**Root Cause Decision Tree:**
- `traefik_entrypoint_open_connections` at `maxConnections` → limit too low for traffic volume → increase `maxConnections` in entrypoint config
- Connections accumulating due to slow backends → backend latency causing connections to hold → fix upstream, also increase limit temporarily
- CLOSE_WAIT accumulation → Traefik not closing connections after backend closes → check for connection draining issues
- Limit not set but OS fd limit hit → `ulimit -n` for Traefik process too low → increase via systemd LimitNOFILE

**Diagnosis:**
```bash
# Current open connections per entrypoint
curl -s http://localhost:8082/metrics | grep traefik_entrypoint_open_connections | grep -v "^#"

# Check configured maxConnections
grep -E "maxConnections|entryPoints" /etc/traefik/traefik.yml -A5

# OS socket state
ss -s
ss -tn | awk '{print $1}' | sort | uniq -c | sort -rn

# Traefik process fd limit
cat /proc/$(pgrep traefik | head -1)/limits | grep "open files"

# PromQL: connection count spike
# traefik_entrypoint_open_connections > 5000
curl -s http://localhost:8082/metrics | grep "traefik_entrypoint_open_connections" | \
  awk '{print $1, $2}' | sort -k2 -rn
```

**Thresholds:** Default no `maxConnections` set (OS limits apply); for explicit limits, WARNING at 80%, CRITICAL at 95%

### Scenario 10: Router Priority Conflict

**Symptoms:** Traffic for specific path/host going to wrong service; lower-priority router never receives requests; duplicate route matches causing unpredictable routing; `traefik_router_requests_total` shows unexpected distribution

**Root Cause Decision Tree:**
- Two routers match same request → Traefik default priority is rule length (longer rule wins) → if explicit `priority` values are set, they override the length-based default and a shorter rule may catch requests intended for the longer one → check rule specificity and explicit priorities
- Explicit priority values conflict → two routers with same `priority` → Traefik picks non-deterministically → assign distinct priorities
- `PathPrefix` and `Path` both defined for same path → `Path` should have higher priority → verify rule type precedence
- Kubernetes IngressRoute vs Ingress both matching → default priority is computed from rule length (longer rule wins), not 0 → if rule lengths are equal, the winner is non-deterministic; assign explicit `priority` values

**Diagnosis:**
```bash
# List all routers with their rules and priorities
curl -s http://localhost:8080/api/http/routers | \
  jq '[.[] | {name, rule, priority, service, status}] | sort_by(.priority) | reverse'

# Find routers that could match the same request (manual inspection)
curl -s http://localhost:8080/api/http/routers | \
  jq '.[] | select(.rule | contains("example.com") or contains("/api")) | {name, rule, priority, service}'

# Check for disabled/error routers being overshadowed
curl -s http://localhost:8080/api/http/routers | \
  jq '.[] | select(.status == "error" or .status == "disabled") | {name, status, error, rule}'

# Traefik log for routing decisions (enable accessLog with fields)
grep -E "RouterName|router|rule" /var/log/traefik/access.log 2>/dev/null | tail -20

# Prometheus: request distribution per router
curl -s http://localhost:8082/metrics | grep "traefik_router_requests_total" | grep -v "^#" | \
  awk '{print $1, $2}' | sort -k2 -rn | head -20
```

**Thresholds:** Any router with > 0 `status=error` that should be active = CRITICAL; zero requests on a router expected to receive traffic = WARNING

### Scenario 11: Provider Configuration Error Deleting All Routes

**Symptoms:** All routes disappear from Traefik dashboard simultaneously; `traefik_config_last_reload_success == 0`; all requests return 404; `traefik_config_reloads_total` incrementing without success flips; Kubernetes API server unreachable during Traefik restart causes empty config to load

**Root Cause Decision Tree:**
- `traefik_config_last_reload_success == 0` for Kubernetes provider → RBAC or API server connectivity issue → check `kubectl` access from Traefik pod
- Routes disappear after Traefik restart + API server temporarily unreachable → empty config applied → add `--providers.kubernetescrd.throttleduration=10s` to prevent rapid reload storms
- Docker provider: container removal event triggers full reconfiguration and empty config briefly applied → check Docker socket connectivity
- Provider error is transient → Traefik deletes routes on error and does not restore until provider responds → verify `providersThrottleDuration`

**Diagnosis:**
```bash
# Router count — should be > 0
curl -s http://localhost:8080/api/http/routers | jq 'length'
curl -s http://localhost:8080/api/overview | jq '{routers: .http.routers, providers: .providers}'

# Provider errors in Traefik log
grep -E "provider.*error|kubernetes.*error|Provider.*failed" /var/log/traefik/traefik.log | tail -30

# Prometheus: config reload counter and last-reload success flag
curl -s http://localhost:8082/metrics | grep -E "traefik_config_reloads_total|traefik_config_last_reload_success" | grep -v "^#"

# Kubernetes provider: test API server connectivity
kubectl get ingress -A --as=system:serviceaccount:<traefik-ns>:traefik 2>&1
kubectl auth can-i list ingressroutes.traefik.io --as=system:serviceaccount:<traefik-ns>:traefik -A
```

**Thresholds:** `traefik_config_last_reload_success == 0` for > 30s = WARNING; router count drop > 50% in under 1 minute = CRITICAL

### Scenario 12: Middleware Chain Failure Cascade

**Symptoms:** All routes using a shared middleware (ForwardAuth, RateLimit) returning 500 uniformly; `traefik_service_requests_total{code="500"}` spike correlated with middleware enable time; middleware shows `error` status in API; backends still healthy (`traefik_service_server_up == 1`)

**Root Cause Decision Tree:**
- `traefik_service_requests_total{code="500"}` spike + `traefik_service_server_up` still 1 → middleware error, not backend issue
- ForwardAuth service returning 5xx → all routes using it return 500 → check ForwardAuth endpoint health separately
- RateLimit or custom plugin middleware misconfigured → Traefik panics in middleware chain → check error log for stack traces
- Middleware reference in router points to non-existent middleware → router status = `error` in API

**Diagnosis:**
```bash
# Find middlewares in error state
curl -s http://localhost:8080/api/http/middlewares | \
  jq '.[] | select(.status != "enabled") | {name, status, type, error, provider}'

# Find routers referencing broken middleware
curl -s http://localhost:8080/api/http/routers | \
  jq '.[] | select(.status == "error") | {name, middlewares, error}'

# Traefik log for middleware panics
grep -E "middleware.*error|panic|ForwardAuth|RateLimit" /var/log/traefik/traefik.log | tail -30

# Enable DEBUG logging to see middleware chain execution
# --log.level=DEBUG

# Test ForwardAuth endpoint directly
FORWARD_ADDR=$(curl -s http://localhost:8080/api/http/middlewares | \
  jq -r '.[] | select(.type == "ForwardAuth") | .forwardAuth.address' | head -1)
curl -v "$FORWARD_ADDR" -H "X-Forwarded-Method: GET" -H "X-Forwarded-Uri: /test"
```

**Thresholds:** Any middleware in `error` state affecting > 0 routes = CRITICAL; ForwardAuth response time > 500ms causes cascading latency on all attached routes

### Scenario 13: ACME Let's Encrypt Rate Limit Hit

**Symptoms:** Certificate renewal failing with `429 urn:ietf:params:acme:error:rateLimited` or `too many certificates already issued`; ACME errors in Traefik log; certificate approaching expiry but renewal blocked; Traefik crash-looping and consuming LE quota on each restart

**Root Cause Decision Tree:**
- `too many certificates already issued` → LE limit: 50 new certs per registered domain per week → must wait for reset or use DNS-01 challenge with wildcard
- Traefik restarting repeatedly → each restart attempts cert issuance → fix root cause of crash loop before certs exhausted
- `acme.json` deleted or corrupted → Traefik re-requests all certs on restart → restore from backup or wait for rate limit reset
- Multiple subdomains needed → use DNS-01 challenge for `*.domain.com` wildcard (counts as 1 cert)

**Diagnosis:**
```bash
# Check for rate limit errors
grep -iE "too many certificates|rate limit|rateLimited|acme.*error" /var/log/traefik/traefik.log | tail -20

# Inspect acme.json cert status and expiry
cat /acme.json | python3 -c "
import json, sys, base64
from cryptography import x509
from cryptography.hazmat.backends import default_backend
data = json.load(sys.stdin)
for resolver, v in data.items():
    for cert in v.get('Certificates', []):
        domain = cert['domain']['main']
        raw = base64.b64decode(cert['certificate'])
        c = x509.load_pem_x509_certificate(raw, default_backend())
        print(f'{resolver}/{domain}: expires {c.not_valid_after_utc}')
" 2>/dev/null

# Count certs issued this week via crt.sh
curl -s "https://crt.sh/?q=<domain>&output=json" | \
  jq '[.[] | select(.not_before > (now - 604800 | todate))] | length'
# > 50 = rate limited for current week

# Check if staging caServer is configured (use for testing)
grep "caServer\|acme-staging" /etc/traefik/traefik.yml
```

**Thresholds:** LE limits: 50 new certs per registered domain per week; 5 duplicate certs per week; resets weekly from first issuance time

### Scenario 14: EntryPoint Connection Limit Reached

**Symptoms:** New connections rejected; `traefik_entrypoint_open_connections` at configured `maxConnections`; clients receiving TCP resets or immediate 503; connections never reach backends (no corresponding backend error increase); OS fd limit for Traefik process exhausted

**Root Cause Decision Tree:**
- `traefik_entrypoint_open_connections` at `maxConnections` → limit too low for current traffic → increase `maxConnections` or remove it
- Connections accumulating due to slow backends → backend latency holding connections open → fix upstream and increase limit temporarily
- CLOSE_WAIT accumulation → Traefik not closing connections after backend closes → check connection draining config
- No explicit `maxConnections` but OS fd limit hit → `ulimit -n` for Traefik process too low → raise via systemd LimitNOFILE

**Diagnosis:**
```bash
# Current open connections per entrypoint
curl -s http://localhost:8082/metrics | grep traefik_entrypoint_open_connections | grep -v "^#"

# Configured maxConnections
grep -E "maxConnections|entryPoints" /etc/traefik/traefik.yml -A5

# OS socket state
ss -s
ss -tn | awk '{print $1}' | sort | uniq -c | sort -rn

# Traefik process fd limit
cat /proc/$(pgrep traefik | head -1)/limits | grep "open files"

# PromQL: connection count spike
# traefik_entrypoint_open_connections{entrypoint="websecure"} > 10000
curl -s http://localhost:8082/metrics | grep "traefik_entrypoint_open_connections" | \
  awk '{print $1, $2}' | sort -k2 -rn
```

**Thresholds:** Default: no `maxConnections` set (OS limits apply); for explicit limits, WARNING at 80%, CRITICAL at 95%

### Scenario 15: Docker Provider Causing Traefik Restart on Every Container Change

**Symptoms:** Traefik process restarts or reloads route configuration every time any Docker container starts, stops, or changes state (even unrelated containers); `traefik_config_reloads_total` increments for every container lifecycle event; momentary connection drops on every deploy; CPU spikes during high-churn container environments (CI runners, ephemeral jobs); Traefik logs show `Configuration received from provider docker` at very high frequency

**Root Cause Decision Tree:**
- Docker provider `watch: true` (or default behavior) triggers a full route reload for every Docker event → any container state change (even unlabeled containers) causes a reconfiguration cycle
  - Without `exposedByDefault: false`, ALL containers get a default route, so every new container adds/removes routes
  - High-churn environments (CI pipelines, batch jobs, auto-scaling) generate hundreds of Docker events per minute
- `swarmMode: true` not set when running Docker Swarm → Traefik watches container events instead of service events → much higher event rate
- `throttleDuration` not configured → Traefik processes every event immediately rather than batching → multiple reloads per second possible

**Diagnosis:**
```bash
# Check reload frequency
curl -s http://localhost:8082/metrics | grep traefik_config_reloads_total
watch -n5 'curl -s http://localhost:8082/metrics | grep traefik_config_reloads_total'

# Count Docker events per minute in real time
docker events --format '{{.Time}} {{.Type}} {{.Action}} {{.Actor.Attributes.name}}' &
# Watch for high event rate from unrelated containers

# Check Traefik Docker provider configuration
grep -A20 "providers:" /etc/traefik/traefik.yml | grep -A15 "docker:"

# Check if exposedByDefault is true (causes all containers to be tracked)
grep "exposedByDefault\|watch\|throttleDuration\|swarmMode" /etc/traefik/traefik.yml

# Check current route count (high = many unlabeled containers generating routes)
curl -s http://localhost:8080/api/http/routers | jq 'length'
curl -s http://localhost:8080/api/http/services | jq 'length'

# Traefik logs: frequency of provider updates
journalctl -u traefik --since "10 min ago" | grep "provider" | wc -l
```

**Thresholds:** WARNING when `traefik_config_reloads_total` rate > 1/minute; CRITICAL when > 10/minute; each reload introduces brief latency spikes as route tables are swapped

### Scenario 16: Kubernetes IngressClass Annotation Missing — Wrong Controller Handling Ingresses

**Symptoms:** After migrating from Traefik v1 to v2/v3, or after adding a second ingress controller (e.g., nginx-ingress), some Ingress resources are handled by the wrong controller; Traefik picks up Ingresses intended for nginx-ingress, or Traefik ignores its own Ingresses; `traefik_service_server_up` shows 0 for services that should have routes; `kubectl describe ingress` shows the wrong controller name in the `IngressClass` field; routing is non-deterministic

**Root Cause Decision Tree:**
- Traefik v2+ requires explicit `IngressClass` assignment; Traefik v1 used `kubernetes.io/ingress.class: traefik` annotation → migration left old annotation style that Traefik v2 ignores by default
  - In Traefik v2.3+, `IngressClass` resource replaces the annotation; if `ingressClass` name not set in Traefik config, Traefik may handle all unclassed Ingresses
- Multiple ingress controllers in cluster with `ingressClass.isDefaultClass: true` → both claim unclassed Ingresses → race condition
- `publishedService` not set → Traefik doesn't update Ingress `status.loadBalancer.ingress` → external-dns / cert-manager cannot find the IP

**Diagnosis:**
```bash
# List all IngressClass resources
kubectl get ingressclass -o wide

# Check which Ingresses have no class (both controllers may claim these)
kubectl get ingress -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}: class={.spec.ingressClassName} annotation={.metadata.annotations.kubernetes\.io/ingress\.class}{"\n"}{end}'

# Check Traefik's configured ingress class
kubectl get deployment traefik -n <traefik-ns> -o jsonpath='{.spec.template.spec.containers[0].args}' | tr ',' '\n' | grep ingress

# Or check Traefik static config
kubectl get configmap traefik -n <traefik-ns> -o yaml | grep -A10 "kubernetes:"

# Check Traefik API for discovered routes vs expected
curl -s http://localhost:8080/api/http/routers | jq '.[].rule' | sort

# Check if nginx-ingress is also handling Traefik Ingresses
kubectl describe ingress <ingress-name> -n <ns> | grep -E "Class\|Controller\|Events"
```

**Thresholds:** Any Ingress being handled by more than one controller = critical misconfiguration; `traefik_service_server_up == 0` for a service that has healthy pods = routing not configured for that service

### Scenario 17: Let's Encrypt OCSP Stapling Failure Causing Slow TLS Handshake

**Symptoms:** TLS handshake time increases from < 50ms to 500ms-2s intermittently; clients report slow initial page loads but subsequent requests are fast; `openssl s_client -status -connect <host>:443` shows `OCSP Response Status: unauthorized` or no OCSP response; Traefik logs show OCSP-related errors; problem occurs more frequently for certificates issued by certain CAs; mobile clients on high-latency connections affected most

**Root Cause Decision Tree:**
- OCSP stapling configured but Traefik cannot reach the CA's OCSP responder → stapled response absent → client must perform its own OCSP check → adds 100-500ms to handshake
  - Traefik must fetch OCSP response from CA's OCSP URL (embedded in certificate) at certificate load and periodically refresh
  - Outbound HTTP access to CA OCSP URLs (e.g., `http://ocsp.int-x3.letsencrypt.org`) may be blocked by firewall or proxy
- OCSP response caching expired and refresh failed → Traefik serves stale/absent staple → browser performs live OCSP check
- Let's Encrypt OCSP responder rate limiting → too many certificate renewals requesting OCSP simultaneously → responder returns errors
- Traefik running in Kubernetes without internet egress → OCSP responder unreachable → all TLS handshakes degraded

**Diagnosis:**
```bash
# Check if OCSP staple is being served
echo | openssl s_client -connect <host>:443 -servername <host> -status 2>/dev/null | \
  grep -A10 "OCSP Response"

# Check OCSP responder URL in certificate
echo | openssl s_client -connect <host>:443 -servername <host> 2>/dev/null | \
  openssl x509 -noout -text | grep "OCSP"

# Test OCSP responder reachability from Traefik host
curl -v http://ocsp.int-x3.letsencrypt.org 2>&1 | grep -E "Connected|refused|timeout"

# Check Traefik ACME/TLS logs
journalctl -u traefik | grep -iE "ocsp|stapl" | tail -20

# Check Traefik metrics for TLS handshake duration
curl -s http://localhost:8082/metrics | grep "tls\|handshake" | grep -v "^#"

# Measure handshake time with and without OCSP
time echo | openssl s_client -connect <host>:443 -servername <host> -status 2>/dev/null | head -5
time echo | openssl s_client -connect <host>:443 -servername <host> 2>/dev/null | head -5
```

**Thresholds:** TLS handshake p99 > 500ms = WARNING (OCSP check adds latency); OCSP response absent on > 10% of handshakes = CRITICAL; OCSP responder unreachable for > 5 minutes = sustained handshake degradation

### Scenario 18: Middleware Order Issue — Rate Limit Not Consuming Auth Failures

**Symptoms:** Brute-force attacks on authentication endpoints not being rate-limited; failed auth attempts consume rate limit quota of legitimate users (the auth middleware returns early before rate limit middleware increments counter); or conversely, authentication failures are not rate-limited at all, allowing unlimited failed login attempts; `traefik_service_requests_total{code="401"}` rate very high; rate limit middleware counters not incrementing as expected

**Root Cause Decision Tree:**
- Middleware chain order: if `RateLimit` middleware is placed AFTER `ForwardAuth` in the chain, and ForwardAuth returns 401 early, the RateLimit middleware never executes → failed auth attempts are NOT rate-limited
  - Traefik middleware order is defined by the order in the `middlewares` list on the router (first to last = outermost to innermost)
  - For brute-force protection: RateLimit must come BEFORE ForwardAuth so it executes on every request regardless of auth outcome
- Opposite problem: RateLimit is BEFORE ForwardAuth but uses a per-IP key → legitimate users behind a NAT share an IP with attackers → rate limited unfairly
- `period` on RateLimit too short → limit resets before next attack wave → ineffective

**Diagnosis:**
```bash
# Check middleware order on routers
curl -s http://localhost:8080/api/http/routers | \
  jq '.[] | {name: .name, middlewares: .middlewares, rule: .rule}'

# Check middleware definitions
curl -s http://localhost:8080/api/http/middlewares | \
  jq '.[] | {name: .name, type: .type, provider: .provider}'

# Check rate limiter state
curl -s http://localhost:8080/api/http/middlewares/<ratelimit-name>@<provider> | jq .

# Monitor 401 rate vs rate limit triggers
watch -n5 'curl -s http://localhost:8082/metrics | grep -E "traefik_service_requests_total.*(401|429)"'

# Check actual middleware execution order from Traefik config
grep -A20 "middlewares:" /etc/traefik/dynamic/*.yml | grep -v "^#"

# Test with a script: send 20 rapid auth failures and count rate-limited responses
for i in $(seq 1 20); do
  curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer invalid" https://<host>/protected
done | sort | uniq -c
```

**Thresholds:** Any 401 rate > 10 req/s from single IP with no 429s appearing = rate limit not applied to auth failures; RateLimit middleware positioned after ForwardAuth in chain = misconfigured for brute-force protection

### Scenario 19: Traefik Memory Leak from Large Number of Routers with Regex Rules

**Symptoms:** Traefik process RSS memory grows continuously over hours/days without recovering; `go_memstats_heap_inuse_bytes` metric grows monotonically; Traefik OOMKilled in Kubernetes; the memory growth rate correlates with number of routers using regex `PathRegexp()` or `HostRegexp()` rules; adding new Ingress resources accelerates the leak; `runtime.ReadMemStats` via pprof shows increasing `HeapAlloc` correlated with regex compilation

**Root Cause Decision Tree:**
- Traefik compiles regex rules on every configuration reload and may not release old compiled regex objects → GC pressure increases with rule count
  - Complex regex patterns (e.g., `PathRegexp(^/api/v[0-9]+/users/[a-z0-9-]{36}/`)`) are expensive to compile and hold significant memory
  - Dynamic providers (Docker, Kubernetes) with many short-lived services cause frequent recompilation
- `accessLog.bufferingSize` set to very large value → log buffer consumes unbounded memory under high traffic
- Router count in the thousands (common in multi-tenant setups) → Traefik trie/routing table memory proportional to rule complexity
- Kubernetes: every Ingress annotation change triggers full route recompilation → memory spikes with each deploy

**Diagnosis:**
```bash
# Monitor Traefik memory over time
watch -n10 'curl -s http://localhost:8082/metrics | grep -E "go_memstats_heap|go_goroutines|process_resident"'

# Count total routers and services (proxy for memory pressure)
curl -s http://localhost:8080/api/http/routers | jq 'length'
curl -s http://localhost:8080/api/http/services | jq 'length'

# Count regex rules specifically
curl -s http://localhost:8080/api/http/routers | jq '[.[] | select(.rule | test("Regexp"))] | length'

# Access pprof heap profile (if pprof endpoint enabled)
curl -s http://localhost:8082/debug/pprof/heap > /tmp/traefik-heap.prof
go tool pprof -top /tmp/traefik-heap.prof | head -20

# Check accessLog buffering config
grep -E "accessLog|bufferingSize" /etc/traefik/traefik.yml

# Check if memory growth correlates with provider reload events
curl -s http://localhost:8082/metrics | grep traefik_config_reloads_total
```

**Thresholds:** WARNING when heap grows > 20% over 1 hour without traffic increase; CRITICAL when RSS > 80% of container memory limit; router count > 1000 with regex rules = elevated memory risk

### Scenario 20: WebSocket Upgrade Failing Through Traefik

**Symptoms:** WebSocket connections fail to establish through Traefik; clients receive `426 Upgrade Required` or `400 Bad Request`; browser DevTools shows the WebSocket connection (101 Switching Protocols) never completes; HTTP requests to the same backend work fine; `traefik_service_requests_total{code="426"}` or `{code="400"}` appears; problem may be intermittent if sticky sessions not configured and multiple backend instances exist (only some support WebSocket on a given connection)

**Root Cause Decision Tree:**
- Traefik not forwarding `Upgrade` and `Connection: Upgrade` headers to backend → backend never receives WebSocket upgrade request
  - Traefik v2+ with `entryPoints.websecure.http.middlewares` stripping headers → headers may be removed by middleware
  - Default Traefik behavior passes Upgrade headers for HTTP/1.1, but custom middleware may interfere
- Backend has multiple instances and WebSocket protocol requires same connection for full session → Traefik round-robins subsequent requests to a different backend → session breaks
  - WebSocket uses a persistent connection; once upgraded, all further frames go over the same TCP connection; stickiness only needed if the upgrade request itself is load-balanced
- HTTP/2 entrypoint used for WebSocket → HTTP/2 does not support WebSocket Upgrade the same way → must use HTTP/1.1 for WebSocket backends
- `stripPrefix` or `replacePathRegex` middleware modifying the path after the Upgrade request → backend receives wrong path → upgrade rejected

**Diagnosis:**
```bash
# Check if Upgrade header is being forwarded
curl -v -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  -H "Sec-WebSocket-Version: 13" \
  https://<host>/ws 2>&1 | grep -E "HTTP|Upgrade|101|400|426"

# Check Traefik router for the WebSocket path
curl -s http://localhost:8080/api/http/routers | \
  jq '.[] | select(.rule | contains("/ws")) | {name, rule, middlewares, service}'

# Check if sticky sessions are configured for the WebSocket service
curl -s http://localhost:8080/api/http/services | \
  jq '.[] | select(.loadBalancer.sticky != null)'

# Check entrypoint protocol (HTTP/2 may break WebSocket)
grep -A10 "entryPoints:" /etc/traefik/traefik.yml | grep -E "http2|h2c|protocol"

# Test direct WebSocket connection to backend (bypassing Traefik)
websocat ws://<backend-ip>:<port>/ws
# If direct works but Traefik path fails, it's a header/routing issue

# Check for middleware that might strip Upgrade headers
curl -s http://localhost:8080/api/http/middlewares | \
  jq '.[] | select(.headers != null) | {name, headers: .headers}'
```

**Thresholds:** Any WebSocket upgrade failure (non-101 response) where HTTP works = CRITICAL configuration issue; intermittent drops only on WebSocket connections = sticky session or keep-alive misconfiguration

## Cross-Service Failure Chains

| Traefik Symptom | Actual Root Cause | First Check |
|-----------------|------------------|-------------|
| 504 Gateway Timeout | Backend service not gracefully handling keep-alive → Traefik holding connections to closed backends | Check `traefik_entrypoint_requests_bytes_total` vs backend `traefik_service_requests_total` |
| ACME cert renewal failure | DNS propagation delay for DNS-01 challenge | Check DNS TTL for domain and wait for propagation |
| Middleware not applying | Traefik version mismatch between CRD version and deployed Traefik version | `kubectl get traefikservice -A` and compare apiVersion |
| High memory usage | Unlimited Ingress resources flooding Traefik config regeneration | Count `kubectl get ingress -A \| wc -l` |
| Backend 0 healthy | Kubernetes Endpoints not populated (service selector mismatch) | `kubectl get endpoints <svc>` |
| SSL error on specific domain | Multiple Ingress resources claim same host, conflicting TLS secrets | `kubectl get ingress -A -o yaml \| grep host:` |

---

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `level=error msg="... 502 Bad Gateway"` | Upstream service unreachable or returning error | `curl -v http://<backend_url>/health` |
| `level=error msg="... dial tcp ...: connect: connection refused"` | Service not listening on the configured port | `ss -tlnp \| grep <port>` on the backend host |
| `level=error msg="... i/o timeout"` | Upstream response timeout exceeded (`respondingTimeout` / `readTimeout`) | `curl -w '%{time_total}' http://<backend_url>/health` |
| `level=error msg="... x509: certificate signed by unknown authority"` | Traefik does not trust upstream's self-signed cert (`insecureSkipVerify` not set) | Add `serversTransport.insecureSkipVerify: true` or add CA to trust store |
| `level=error msg="... acme: error: 429 ..."` | Let's Encrypt rate limit hit (5 certs per domain per week) | `curl https://acme-v02.api.letsencrypt.org/directory` — check `Retry-After` |
| `level=error msg="... error getting challenge for token"` | ACME HTTP-01 challenge failed — port 80 is blocked or not reaching Traefik | `curl http://<domain>/.well-known/acme-challenge/test` |
| `level=error msg="... middleware ... not found"` | Middleware referenced in router annotation/config but not defined anywhere | `curl http://localhost:8080/api/http/middlewares` — check list for missing name |

---

### Scenario 21: ACME Certificate Removed After Security Hardening — Port 80 Closed

**Symptoms:** After a security hardening pass that closed port 80 on the firewall, Let's Encrypt certificate renewal fails silently; certs expire 30-90 days later causing `certificate has expired` errors for all HTTPS traffic; `traefik_tls_certs_not_after - time()` shows approaching expiry; Traefik logs show `error getting challenge for token` on renewal attempts

**Root Cause Decision Tree:**
- `error getting challenge for token` in logs → ACME HTTP-01 challenge request not reaching Traefik → port 80 blocked by firewall or not forwarded in cloud security group
- Challenge request reaches server but returns 404 → Traefik's `web` entrypoint (port 80) not defined or disabled → verify entrypoint config
- Port 80 open but challenge still failing → HTTP→HTTPS redirect middleware intercepts the challenge URL → exclude `/.well-known/acme-challenge/` from redirect
- Rate limit error (429) → previous renewal attempts already consumed the weekly quota → wait for rate limit reset (up to 7 days) or use staging ACME

**Diagnosis:**
```bash
# Check current cert expiry dates
curl -s http://localhost:8080/api/http/routers | \
  jq '.[] | select(.tls != null) | {name, rule, tls}' | grep -A3 "tls"

# Check ACME challenge reachability from the internet
DOMAIN=<your-domain>
curl -v http://$DOMAIN/.well-known/acme-challenge/test 2>&1 | grep -E "< HTTP|refused|timed out"

# Check Traefik logs for ACME errors
journalctl -u traefik --since "24 hours ago" | grep -E "acme|challenge|certificate|renew" | tail -30

# Verify port 80 entrypoint is configured
grep -E "web:|entryPoints" /etc/traefik/traefik.yml | head -10
curl -s http://localhost:8080/api/overview | jq '.entrypoints'

# Check ACME storage file for cert expiry
cat /etc/traefik/acme.json | jq '.["letsencrypt"].Certificates[] | {domain: .domain.main, expiry: .Certificate}' 2>/dev/null | head -30

# Check rate limit status
journalctl -u traefik | grep "429\|rate limit\|too many" | tail -10
```

**Thresholds:** Let's Encrypt certificates expire in 90 days; Traefik renews at 30 days before expiry; if renewal has been failing for 60+ days and expiry is within 30 days, this is CRITICAL

### Scenario 22: Headers Middleware Added Globally — CORS Preflight Returns Double Headers

**Symptoms:** After adding a global `headers` middleware for security headers (HSTS, X-Frame-Options, etc.), CORS-enabled endpoints start returning duplicate headers causing browser CORS errors; `Access-Control-Allow-Origin` appears twice in response; some browsers reject duplicates; only affects services that also set CORS headers in their own application response

**Root Cause Decision Tree:**
- Duplicate `Access-Control-Allow-Origin` after adding global headers middleware → both application and Traefik middleware set the same header → `customResponseHeaders` in middleware conflicts with app-set headers
- Security headers present on OPTIONS preflight → middleware applies to all methods including OPTIONS → application's CORS logic should return 200/204 on OPTIONS but Traefik middleware overrides it
- Only specific routes affected → those services set their own CORS headers; global middleware double-sets them → exclude those services from the global middleware or set `accessControlAllowOriginList` only in middleware, not in app

**Diagnosis:**
```bash
# Check which middlewares are attached to affected routers
curl -s http://localhost:8080/api/http/routers | \
  jq '.[] | {name, middlewares, rule}' | grep -B2 -A5 "middleware"

# Inspect the headers middleware configuration
MIDDLEWARE=<middleware-name>
curl -s http://localhost:8080/api/http/middlewares/$MIDDLEWARE | jq '.headers'

# Test for duplicate headers
curl -v https://<host>/api/test 2>&1 | grep -i "access-control\|x-frame\|hsts"

# Test OPTIONS preflight
curl -v -X OPTIONS https://<host>/api/test \
  -H 'Origin: https://trusted.com' \
  -H 'Access-Control-Request-Method: POST' 2>&1 | grep "^< "

# Check application response headers without Traefik middleware
curl -v http://<backend_direct_url>/api/test 2>&1 | grep -i "access-control"
```

**Thresholds:** Any duplicate response header for `Access-Control-Allow-Origin` causes all browsers to reject the CORS preflight; this is a hard failure for all browser clients

### Scenario 23: TLS Minimum Version Raised — mTLS Clients Get Handshake Failure

**Symptoms:** After raising `minVersion: VersionTLS12` to `VersionTLS13` in the Traefik TLS options, service-to-service mTLS clients start failing; internal services using older Go TLS stacks (< 1.18), Java 11 default config, or Python < 3.7 report handshake failures; `traefik_service_requests_total{code="502"}` spikes; Traefik logs show `tls: no supported versions satisfy MinVersion and MaxVersion`

**Root Cause Decision Tree:**
- `tls: no supported versions satisfy MinVersion` → client does not support TLS 1.3 → lower `minVersion` back to TLS 1.2
- Client supports TLS 1.3 but handshake still fails → cipher suite incompatibility or missing `preferServerCipherSuites` → check client cipher list
- Only some backends fail → those backends have a different TLS option applied via `serversTransport` → check per-service TLS options
- External clients unaffected, only internal mTLS clients break → internal services built with older runtime; external browsers have TLS 1.3 support

**Diagnosis:**
```bash
# Check current TLS options configuration
grep -A10 "minVersion\|tls:" /etc/traefik/traefik.yml | head -30
curl -s http://localhost:8080/api/http/routers | jq '.[] | select(.tls != null) | {name, tls}' | head -20

# Test which TLS versions the failing client supports
# From the client machine or simulate:
openssl s_client -connect <traefik_host>:443 -tls1_2 2>&1 | grep -E "Cipher|alert|error|Verify"
openssl s_client -connect <traefik_host>:443 -tls1_3 2>&1 | grep -E "Cipher|alert|error|Verify"

# Check Traefik logs for TLS errors from specific client IPs
journalctl -u traefik --since "15 minutes ago" | grep -E "tls:|handshake|version" | tail -30

# Verify the TLS option is correctly applied to the router
TLS_OPT=<tls-option-name>
curl -s http://localhost:8080/api/http/routers | jq --arg opt "$TLS_OPT" '.[] | select(.tls.options == $opt) | .name'
```

**Thresholds:** Any internal service affected by TLS version changes is a P1 if it handles live traffic; identify all mTLS consumers before changing minimum TLS version

# Capabilities

1. **Service discovery** — Docker labels, K8s IngressRoute, file provider debugging
2. **TLS management** — Let's Encrypt ACME, certificate troubleshooting
3. **Middleware** — Auth, rate limiting, headers, compress configuration
4. **Routing** — Host/path rules, priority, weighted round-robin
5. **Dashboard** — API queries, router/service inspection
6. **Provider management** — Docker, Kubernetes, Consul, file provider issues

# Critical Metrics to Check First

| Priority | Metric | CRITICAL | WARNING |
|----------|--------|----------|---------|
| 1 | `traefik_service_server_up` | == 0 (any server) | — |
| 2 | `traefik_service_requests_total` 5xx rate | > 5% | > 1% |
| 3 | TLS cert expiry (`probe_ssl_earliest_cert_expiry`) | < 3 days | < 14 days |
| 4 | `traefik_service_request_duration_seconds` p99 | > 2 s | > 0.5 s |
| 5 | `traefik_entrypoint_open_connections` | Near OS fd limit | > 80% fd limit |

# Output

Standard diagnosis/mitigation format. Always include: dashboard API output,
Traefik logs, PromQL query results, and recommended configuration changes.

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| HTTP 5xx error rate (per service) | > 1% of requests | > 5% of requests | `curl -s http://traefik:8080/metrics | grep traefik_service_requests_total | grep -v '#'` |
| Request p99 latency (per service) | > 500ms | > 2s | PromQL: `histogram_quantile(0.99, sum by (service, le) (rate(traefik_service_request_duration_seconds_bucket[5m])))` |
| Open connections per entrypoint | > 80% of OS fd limit | At OS fd limit (ulimit -n) | `curl -s http://traefik:8080/metrics | grep traefik_entrypoint_open_connections` |
| TLS certificate expiry (days remaining) | < 14 days | < 3 days | `curl -s http://traefik:8080/metrics | grep probe_ssl_earliest_cert_expiry` |
| Backend server health (servers marked down) | Any server down on non-critical service | Any server down on critical service | `curl -s http://traefik:8080/api/http/services | jq '.[].serverStatus'` |
| Middleware retry rate (retries/s) | > 10 retries/s per service | > 50 retries/s per service | `curl -s http://traefik:8080/metrics | grep traefik_service_retries_total` |
| Configuration reload errors (last 5 min) | > 1 error | > 5 errors | `curl -s http://traefik:8080/metrics | grep traefik_config_last_reload_success` (== 0 means last reload failed) |
| Router rule evaluation time p99 | > 10ms | > 100ms | PromQL: `histogram_quantile(0.99, sum by (router, le) (rate(traefik_router_request_duration_seconds_bucket[5m])))` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| `traefik_entrypoint_requests_total` rate | Growing >80% of tested max throughput (rps) | Scale Traefik replicas horizontally; increase `maxIdleConnsPerHost` in transport config | 1–2 weeks |
| `traefik_entrypoint_open_connections` | Trending toward OS `net.core.somaxconn` limit | Tune `ulimit` / `fs.file-max` on nodes; increase Traefik connection pool limits | 1 week |
| `traefik_service_request_duration_seconds` p99 | Rising above SLA without upstream latency increase | Add Traefik replicas; check TLS handshake overhead; enable HTTP/2 multiplexing | Days |
| Certificate expiry countdown (via `traefik_tls_certs_not_after`) | Any cert expiring within 30 days | Trigger ACME renewal or rotate manually: `kubectl delete secret <tls-secret> -n traefik`; verify cert-manager CertificateRequest | 30 days |
| Memory usage per Traefik pod | >60% of memory limit | Review number of routers/middlewares (each has memory cost); upgrade to larger pod or add replicas | 1 week |
| `traefik_config_last_reload_success` | Drops to 0 (last reload failed) | Investigate and fix config errors immediately; each failed reload means new routes are not applied | Immediate |
| IngressRoute / Ingress object count | Approaching thousands of routes | Enable per-provider polling interval tuning; consider splitting into multiple Traefik instances per domain group | 2–4 weeks |
| Backend service health check failure rate | >5% of health checks failing across services | Investigate downstream service degradation; adjust health check intervals to avoid false positives while backends scale | Days |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check Traefik pod health and restart counts
kubectl get pods -n traefik -l app=traefik -o wide

# List all routers and their current status from the dashboard API
curl -s http://traefik:8080/api/http/routers | jq '.[] | {name: .name, status: .status, rule: .rule, service: .service}'

# Check all backend service health — identify servers marked DOWN
curl -s http://traefik:8080/api/http/services | jq '.[] | select(.serverStatus | to_entries[] | .value != "UP") | {name: .name, serverStatus: .serverStatus}'

# Inspect current 4xx/5xx error rates by service
curl -s http://traefik:8080/metrics | grep 'traefik_service_requests_total' | grep -E '"5[0-9]{2}"|"4[0-9]{2}"'

# Check TLS certificate expiry times
curl -s http://traefik:8080/metrics | grep traefik_tls_certs_not_after

# Count open connections per entrypoint
curl -s http://traefik:8080/metrics | grep traefik_entrypoint_open_connections

# Check config reload success/failure totals
curl -s http://traefik:8080/metrics | grep -E "traefik_config_reloads_total|traefik_config_last_reload_success"

# Tail Traefik logs for routing errors and panics
kubectl logs -n traefik -l app=traefik --since=5m | grep -iE "error|panic|fail|invalid|reload"

# Verify ACME certificate renewal status
kubectl logs -n traefik -l app=traefik --since=1h | grep -iE "acme|certificate|renew|letsencrypt"

# Check p99 request latency per entrypoint
curl -s http://traefik:8080/metrics | grep 'traefik_entrypoint_request_duration_seconds_bucket' | awk -F'"' '{print $2, $4}' | sort -u | tail -20
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| HTTP success rate (non-5xx) | 99.9% | `1 - sum(rate(traefik_service_requests_total{code=~"5.."}[5m])) / sum(rate(traefik_service_requests_total[5m]))` | 43.8 min | >14.4x burn rate (5xx rate >1.44% for 1 h) |
| Request p99 latency ≤ 500 ms | 99% | `histogram_quantile(0.99, sum(rate(traefik_service_request_duration_seconds_bucket[5m])) by (le)) < 0.5` | 7.3 hr | p99 > 500 ms sustained for 1 h burns budget |
| TLS certificate validity (no expired certs) | 99.95% | `(traefik_tls_certs_not_after - time()) > 0` — all certificates have future expiry | 21.9 min | Any cert with `not_after - time() < 86400` (24 h) fires P2; < 3600 fires P1 |
| Config reload availability | 99.5% | `traefik_config_last_reload_success == 1` — last reload succeeded | 3.6 hr | Any failed config reload (gauge flips to 0) triggers alert |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Dashboard access restricted | `curl -s -o /dev/null -w "%{http_code}" http://traefik:8080/dashboard/` from an external IP | Returns `401` or `403` — dashboard must not be unauthenticated and publicly reachable |
| HTTPS redirect configured on HTTP entrypoint | `kubectl get deployment -n traefik -o jsonpath='{.items[0].spec.template.spec.containers[0].args}' | grep -i "redirectScheme\|entrypoints.web.http.redirections"` | `redirections.entryPoint.to=websecure` and `scheme=https` configured so HTTP traffic is upgraded |
| ACME/Let's Encrypt resolver configured | `kubectl get configmap traefik-config -n traefik -o yaml | grep -A5 'certificatesResolvers'` | Resolver defined with valid `email`, `storage` path, and `httpChallenge` or `tlsChallenge` |
| Forwarded headers trusted only from known proxies | `kubectl get deployment -n traefik -o jsonpath='{.items[0].spec.template.spec.containers[0].args}' | grep -E "forwardedHeaders\|trustedIPs"` | `--entrypoints.*.forwardedHeaders.trustedIPs` set to upstream load balancer CIDR — `insecure=true` must not be set in production |
| Access logs enabled | `kubectl get deployment -n traefik -o jsonpath='{.items[0].spec.template.spec.containers[0].args}' | grep accesslog` | `--accesslog=true` present; format is `json` for structured parsing |
| Metrics endpoint enabled | `curl -s -o /dev/null -w "%{http_code}" http://traefik:8080/metrics` | Returns `200` — Prometheus scrape target must be reachable |
| IngressRoute CRDs installed and up to date | `kubectl get crd | grep traefik.io` | `ingressroutes.traefik.io`, `middlewares.traefik.io`, and `tlsoptions.traefik.io` present with current version |
| Default TLS minimum version set to 1.2 | `kubectl get tlsoption -A -o yaml | grep minVersion` | `minVersion: VersionTLS12` — `VersionTLS10` and `VersionTLS11` must not be used |
| Rate limiting middleware applied to public routes | `kubectl get middleware -A | grep ratelimit` | At least one RateLimit middleware exists and is referenced in IngressRoutes for public-facing endpoints |
| Resource requests and limits set | `kubectl get deployment -n traefik -o jsonpath='{.items[0].spec.template.spec.containers[0].resources}'` | Non-empty `requests` and `limits` for CPU and memory to prevent noisy-neighbor resource contention |

---

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `level=error msg="Unable to obtain ACME certificate for domain" error="acme: error: 429 :: too many requests"` | Error | Let's Encrypt rate limit hit; too many cert issuance attempts for same domain | Wait 1 hour; use staging CA for testing; check for certificate renewal loops |
| `level=error msg="Backend not found" routerName=<name>` | Error | IngressRoute references a service that does not exist or has no ready endpoints | Verify target Service exists; check `kubectl get endpoints <svc>`; fix IngressRoute spec |
| `level=warn msg="Skipping certificate" domain=<d> err="no such host"` | Warning | DNS record for domain does not resolve; ACME HTTP/TLS challenge cannot be validated | Add DNS A/CNAME record; verify domain ownership; check challenge resolver config |
| `level=error msg="Service not found" serviceName=<svc>` | Error | Traefik dynamic config references a nonexistent service name | Audit IngressRoute and Middleware CRDs for stale service references; run `kubectl get svc` |
| `level=error msg="404 page not found" method=<m> path=<p> host=<h>` | Warning | No router matched the incoming request; routing rule not matching | Review IngressRoute `match` expressions; check `Host()` and `PathPrefix()` rules; test with curl -H "Host:" |
| `level=warn msg="Provider connection is lost, reconnecting..." provider=kubernetescrd` | Warning | Traefik lost connection to Kubernetes API; dynamic config updates paused | Check Kubernetes API server health; verify Traefik ServiceAccount RBAC permissions |
| `level=error msg="dial tcp: connect: connection refused" upstreamName=<svc>` | Error | Upstream pod not listening on configured port; pod crashed or port mismatch | Check pod logs; verify `containerPort` matches Service `targetPort`; run `kubectl describe svc` |
| `level=warn msg="Request is blocked by middleware" middleware=ratelimit routerName=<n>` | Warning | Client hitting rate limit enforced by RateLimit middleware | Expected if rate limit is correct; if false positive, increase `average`/`burst` in Middleware spec |
| `level=error msg="TLS handshake error" error="tls: unknown certificate authority"` | Error | Client does not trust Traefik's certificate; wrong cert or self-signed CA | Check cert in `tls.certificates` or ACME store; verify CA chain is complete |
| `level=error msg="error calling middlewares chain" error="context deadline exceeded"` | Error | Upstream response taking longer than configured timeout; middleware chain timed out | Increase `forwardingTimeouts.responseHeaderTimeout` or `readTimeout`; investigate slow upstream |
| `level=warn msg="Health check failed" serviceName=<svc> url=<url>` | Warning | Active health check to upstream endpoint returning non-2xx or timing out | Check upstream pod health; adjust `healthCheck.interval` and `healthCheck.path` in service config |
| `level=error msg="Unable to start server" error="bind: address already in use"` | Critical | Another process using the same entrypoint port; Traefik cannot bind | Identify conflicting process; ensure only one Traefik pod per node for host network mode; check DaemonSet vs Deployment |

---

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `502 Bad Gateway` | Traefik reached upstream but got an invalid or no response | End-user sees error page; request not served | Check upstream pod logs; verify pod is running and port is correct; inspect backend health |
| `503 Service Unavailable` | No healthy backends available for the matched router | All requests to that route fail | Check `kubectl get endpoints`; verify pod readiness; confirm service selector matches pod labels |
| `404 Not Found` (from Traefik) | No router rule matched the request | Request silently dropped with error response | Audit IngressRoute match rules; check Host and Path expressions; verify Traefik processed the CRD |
| `429 Too Many Requests` | RateLimit middleware threshold exceeded for the client IP or group | Client throttled; requests rejected until rate drops | Expected behavior if rate limit is intentional; tune `average`/`burst` if limits are too aggressive |
| `acme: error 429` | Let's Encrypt rate limit; too many certificates issued for the same registered domain | HTTPS unavailable until rate limit window resets (1 hour / 1 week) | Switch to staging ACME endpoint; wait for rate limit reset; consolidate domains into SANs |
| `CERTIFICATE_EXPIRED` | TLS certificate past `NotAfter` date; ACME renewal failed silently | Browser TLS errors for all HTTPS routes | Check ACME storage volume; verify DNS/port 80 reachable for HTTP challenge; renew manually via `certbot` |
| `MIDDLEWARE_NOT_FOUND` | IngressRoute references a Middleware that does not exist in the same namespace | Router applies no middleware; security/rate-limit policies not enforced | Create missing Middleware resource; check namespace (CRDs are namespace-scoped) |
| `PROVIDER_ERROR` (`kubernetescrd`) | Kubernetes provider failed to sync CRD config | Dynamic config changes not applied until provider reconnects | Check Kubernetes API health; verify Traefik ClusterRole grants `ingressroutes`, `middlewares` read access |
| `TLS_HANDSHAKE_TIMEOUT` | Client did not complete TLS handshake within timeout | Connection dropped; may indicate slow client or TLS fingerprinting scan | Check `respondingTimeouts.readTimeout`; review access logs for suspicious IPs; consider IP allowlist |
| `ENTRYPOINT_PORT_CONFLICT` | Traefik cannot bind to a configured entrypoint port | That entrypoint is down; HTTP or HTTPS traffic not accepted | Check for port conflicts; inspect DaemonSet host port allocations; verify no duplicate Traefik instances |
| `REDIRECT_LOOP` | HTTP→HTTPS redirect rule combined with HTTPS IngressRoute also redirecting | Browser reports `ERR_TOO_MANY_REDIRECTS` | Ensure HTTP entrypoint redirect is at the entrypoint level; HTTPS IngressRoute should not also have redirect middleware |
| `PASSTHROUGH_CERT_MISSING` | TLS passthrough IngressRoute cannot find SNI match | Backend receives raw TLS but may reject due to wrong SNI | Verify `tls.passthrough=true` on IngressRoute; ensure backend handles its own TLS and SNI matches |

---

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| **ACME Rate Limit Loop** | `traefik_tls_certs_not_after` for domain trending toward 0; ACME request counter spiking | `acme: error: 429 :: too many requests` | `TraefikCertExpiringSoon` | Renewal loop retrying too aggressively; Let's Encrypt rate limit hit | Backoff; use staging CA; consolidate SANs; fix root cert issue first |
| **No Healthy Backends** | `traefik_service_requests_total{code="503"}` rising for specific service; `traefik_service_server_up` = 0 | `Service not found` or health check failures | `TraefikBackend503Rate` | All pods for a service unhealthy or terminated; endpoint slice empty | Fix deployment; check readiness probe; verify service selector matches pod labels |
| **Provider Sync Lost** | `traefik_config_last_reload_success == 0`; new IngressRoutes not applied | `Provider connection is lost, reconnecting... kubernetescrd` | `TraefikProviderError` | Kubernetes API unreachable or RBAC permission revoked | Check API server health; reapply ClusterRole; restart Traefik |
| **TLS Cert Mismatch / Expired** | `traefik_tls_certs_not_after` < 0; TLS handshake error count rising | `tls: unknown certificate authority` / `certificate has expired` | `TraefikCertExpired` | ACME renewal failed; manual cert not rotated; ACME storage corrupted | Clear acme.json; fix DNS/challenge; rotate cert manually |
| **Redirect Loop** | `traefik_router_requests_total{code="301"}` very high; no `200` for same host | `Exceeded 10 redirects` in browser; no upstream hits visible | `TraefikRedirectLoop` | HTTP→HTTPS redirect on entrypoint AND middleware-level redirect on HTTPS IngressRoute | Remove duplicate redirect middleware; keep redirect only at entrypoint level |
| **Upstream Timeout Cascade** | `traefik_service_request_duration_seconds` p99 > `responseHeaderTimeout`; `502` rate rising | `context deadline exceeded` on middleware chain | `TraefikUpstreamHighLatency` | Slow upstream pods; upstream DB bottleneck; network latency spike | Increase timeout; scale upstream; check upstream pod metrics; circuit breaker middleware |
| **Rate Limit False Positives** | `429` rate high; no corresponding traffic spike; affected IPs are legitimate users | `Request is blocked by middleware ratelimit` for internal IPs | `TraefikHighRejectionRate` | RateLimit `average`/`burst` too low; shared IP from NAT gateway miscounted | Raise limits; use `sourceCriterion.ipStrategy.depth` to trust forwarded headers from known proxy |
| **Port Bind Conflict** | Traefik pod in `CrashLoopBackOff`; exits immediately after start | `bind: address already in use` | `TraefikPodCrashLoop` | Another process or DaemonSet using same host port; duplicate Traefik instance | Identify conflicting pod; change entrypoint port or DaemonSet node affinity |
| **IngressRoute Not Matching** | `404` for specific path from Traefik (not upstream); upstream pods healthy; `200` on direct pod access | `404 page not found` entries for expected paths in access log | `TraefikRouteNotFound` | IngressRoute `match` expression missing PathPrefix or Host; namespace mismatch on Middleware | Review `match` rule; test with `traefik` route dump; fix Host/Path expressions |
| **Dashboard Exposed Publicly** | `traefik_router_requests_total` for dashboard router from external IPs; no auth headers | No auth errors in logs — requests succeed | `TraefikDashboardPubliclyAccessible` | Dashboard middleware missing or BasicAuth not configured | Add `BasicAuth` middleware to dashboard IngressRoute; restrict by IP allowlist |

---

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `502 Bad Gateway` | Browser / HTTP client | Upstream backend pod unhealthy or terminated; health check failing | `traefik_service_server_up{service="<svc>"}` = 0; `kubectl get endpoints <svc>` | Fix upstream deployment; check readiness probe; verify service selector |
| `503 Service Unavailable` | Browser / HTTP client | All backend servers removed from load balancer; no healthy endpoints | `traefik_service_requests_total{code="503"}` rising; endpoint slice empty | Scale up upstream pods; fix readiness probes; check deployment rollout status |
| `504 Gateway Timeout` | Browser / HTTP client | Upstream response time exceeded `responseHeaderTimeout` | `traefik_service_request_duration_seconds` p99 at timeout threshold | Increase `responseHeaderTimeout`; optimize upstream; scale upstream pods |
| `429 Too Many Requests` | Browser / HTTP client / API client | Rate limit middleware triggered for client IP or user | `traefik_router_requests_total{code="429"}` rising; ratelimit middleware logs | Tune `average` and `burst` in RateLimit middleware; use correct `sourceCriterion.ipStrategy` |
| `404 Not Found` (from Traefik, not upstream) | Browser / API client | IngressRoute match expression does not match request path/host | Access log shows `404` without upstream hit; `traefik_router_requests_total{code="404"}` | Review `match` expression in IngressRoute; test with `curl -H "Host: <host>" <url>` |
| `TLS handshake error: unknown certificate authority` | Browser / curl / any TLS client | TLS certificate expired, wrong cert, or ACME renewal failed | `traefik_tls_certs_not_after` near zero; ACME logs show error | Renew certificate manually; fix ACME challenge; update `acme.json` |
| `ERR_TOO_MANY_REDIRECTS` in browser | Browser | HTTP → HTTPS redirect loop; middleware applied at both entrypoint and IngressRoute | Browser devtools shows infinite 301 chain | Remove redundant redirect middleware from IngressRoute when entrypoint-level redirect is active |
| `connection refused` to Traefik entrypoint port | Browser / load balancer health check | Traefik pod in `CrashLoopBackOff`; port bind conflict | `kubectl logs -n traefik <pod> | grep "bind"` | Identify conflicting port; fix DaemonSet/Deployment; check host port availability |
| `401 Unauthorized` unexpectedly | Browser / API client | BasicAuth or ForwardAuth middleware rejecting valid credentials | Traefik access log shows `401` with no upstream hit; middleware name in log | Verify credentials in Secret; check ForwardAuth backend availability; test auth endpoint directly |
| `websocket: close 1006 abnormal closure` | WebSocket client | Traefik closing idle WebSocket connection before `sticky` or timeout configured | Traefik logs `EOF` on WebSocket upgrade; client disconnects periodically | Configure `readTimeout` and `writeTimeout` for WebSocket; enable sticky sessions for stateful WS |
| CORS preflight `OPTIONS` returns 404 | Browser (SPA / fetch) | No IngressRoute matching `OPTIONS` method; CORS middleware missing | Browser devtools shows `OPTIONS` returning 404 from Traefik | Add CORS middleware to IngressRoute; or add explicit `OPTIONS` route matcher |
| mTLS `certificate required` rejected | API client with mTLS | Client certificate not trusted by Traefik's TLS store; wrong CA configured | Traefik logs `tls: certificate required` or `bad certificate` | Add client CA cert to `TLSStore`; verify `ClientAuth: RequireAndVerifyClientCert` settings |

---

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| ACME certificate approaching expiry | `traefik_tls_certs_not_after` decreasing below 30 days; renewal logs missing | `kubectl logs -n traefik <pod> | grep "acme\|certificate"` | 30 days | Verify ACME challenge accessibility; check `acme.json` permissions; restart Traefik to force renewal attempt |
| Backend pod count shrinking without alert | `traefik_service_server_up` count dropping; 502 rate slowly rising | `kubectl get endpoints <svc> -o wide` | Hours | Check deployment scaling; HPA status; pod crash loop; fix readiness probes |
| Configuration reload failure rate accumulating | `traefik_config_last_reload_success == 0`; new IngressRoutes not taking effect | `kubectl logs -n traefik <pod> | grep "error\|failed to reload"` | Hours | Fix malformed IngressRoute YAML; check RBAC; validate CRD schema with `kubectl apply --dry-run=server` |
| Memory leak in Traefik over days of uptime | Traefik pod memory trending up; no traffic increase; slow p99 latency creeping | `kubectl top pods -n traefik` daily; `container_memory_working_set_bytes` graph | Days to weeks | Schedule periodic rolling restarts; upgrade Traefik version; profile middleware for memory leak |
| Middleware chain increasing request latency | p99 latency slowly rising without upstream change; Traefik CPU growing | `traefik_router_request_duration_seconds` p99 trend; count middleware per router | Days | Profile middleware chain; remove unused middlewares; check ForwardAuth backend latency |
| Plugin or custom middleware error rate rising | `traefik_router_requests_total{code=~"5.."}` slowly increasing for routers attached to specific middleware | `kubectl logs -n traefik <pod> | grep "<middleware-name>"` | Hours | Disable or roll back problematic plugin; check plugin logs; pin plugin version |
| CertResolver queue growing from Let's Encrypt domain additions | ACME challenge failures for new domains; existing certs unaffected | `kubectl logs -n traefik <pod> | grep "acme" | grep "error"` | Hours | Check DNS propagation for new domains; verify challenge type compatibility; use DNS-01 for wildcard |
| Traefik DaemonSet node coverage degrading | Some nodes not running Traefik; traffic to pods on those nodes returns 503 | `kubectl get pods -n traefik -o wide` vs `kubectl get nodes` | During node events | Fix DaemonSet tolerations; check node taints; verify affinity rules for new node types |

---

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Traefik Full Health Snapshot
NS="${TRAEFIK_NAMESPACE:-traefik}"
echo "=== Traefik Pod Status ==="
kubectl get pods -n "$NS" -o wide

echo ""
echo "=== Traefik Version and Entrypoints ==="
TRAEFIK_POD=$(kubectl get pod -n "$NS" -l app=traefik -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
[ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- traefik version 2>/dev/null || echo "No Traefik pod found"

echo ""
echo "=== Config Reload Status ==="
[ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- wget -qO- http://localhost:8082/metrics 2>/dev/null \
  | grep -E "traefik_config_reloads_total|traefik_config_last_reload_success"

echo ""
echo "=== TLS Certificate Expiry ==="
[ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- wget -qO- http://localhost:8082/metrics 2>/dev/null \
  | grep "traefik_tls_certs_not_after"

echo ""
echo "=== Backend Server Health ==="
[ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- wget -qO- http://localhost:8082/metrics 2>/dev/null \
  | grep "traefik_service_server_up" | grep -v "^#"

echo ""
echo "=== Recent Errors ==="
kubectl logs -n "$NS" -l app=traefik --tail=50 2>/dev/null | grep -i "error\|warn\|fatal\|acme"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Traefik Performance Triage
NS="${TRAEFIK_NAMESPACE:-traefik}"
TRAEFIK_POD=$(kubectl get pod -n "$NS" -l app=traefik -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

echo "=== Request Rate by Service and Code ==="
[ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- wget -qO- http://localhost:8082/metrics 2>/dev/null \
  | grep "traefik_service_requests_total" | grep -v "^#" | sort -t= -k3 -rn | head -20

echo ""
echo "=== Request Duration Histogram Buckets by Router (compute p99 in Prometheus) ==="
[ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- wget -qO- http://localhost:8082/metrics 2>/dev/null \
  | grep 'traefik_router_request_duration_seconds_bucket' | head -30

echo ""
echo "=== Error Rate (4xx/5xx) by Service ==="
[ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- wget -qO- http://localhost:8082/metrics 2>/dev/null \
  | grep "traefik_service_requests_total" | grep -E '"(4|5)[0-9][0-9]"' | sort -t= -k2 -rn | head -15

echo ""
echo "=== Open Connections per Entrypoint ==="
[ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- wget -qO- http://localhost:8082/metrics 2>/dev/null \
  | grep "traefik_entrypoint_open_connections"

echo ""
echo "=== Traefik Pod Resource Usage ==="
kubectl top pods -n "$NS" --sort-by=cpu 2>/dev/null
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Traefik Connection and Resource Audit
NS="${TRAEFIK_NAMESPACE:-traefik}"
TRAEFIK_POD=$(kubectl get pod -n "$NS" -l app=traefik -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

echo "=== Active Routes (IngressRoutes) ==="
kubectl get ingressroute -A 2>/dev/null | head -30

echo ""
echo "=== Middleware Inventory ==="
kubectl get middleware -A 2>/dev/null

echo ""
echo "=== TLS Stores and Options ==="
kubectl get tlsstore,tlsoption -A 2>/dev/null

echo ""
echo "=== Traefik Dashboard API — Routers ==="
[ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- \
  wget -qO- "http://localhost:8080/api/http/routers" 2>/dev/null \
  | python3 -c "import json,sys; [print(f\"  {r['name']}: {r.get('status','?')} rule={r.get('rule','?')[:60]}\") for r in json.load(sys.stdin)]" 2>/dev/null | head -20

echo ""
echo "=== acme.json Certificate Domains ==="
kubectl get secret -n "$NS" -l app=traefik-cert 2>/dev/null \
  || ([ -n "$TRAEFIK_POD" ] && kubectl exec -n "$NS" "$TRAEFIK_POD" -- \
    cat /data/acme.json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); [print(k, [c['domain'] for c in v.get('Certificates',[])]) for k,v in d.items() if isinstance(v,dict) and v.get('Certificates')]" 2>/dev/null)

echo ""
echo "=== Pod Restart Count ==="
kubectl get pods -n "$NS" -o custom-columns="NAME:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount,STATUS:.status.phase"
```

---

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| **High-traffic service flooding Traefik connection pool** | `traefik_entrypoint_open_connections` at capacity; other services experience increased latency or connection drops | `traefik_service_requests_total` sorted by count; top services consuming most connections | Apply RateLimit middleware to high-traffic service; scale Traefik replicas; increase `maxIdleConnsPerHost` | Set per-service RateLimit from day one; implement circuit breaker middleware for high-volume services |
| **Misconfigured ForwardAuth backend causing global latency** | All routes using ForwardAuth middleware show increased p99; ForwardAuth backend under load | `traefik_router_request_duration_seconds_bucket` p99 for routers using ForwardAuth | Add timeout to ForwardAuth middleware (`authResponseHeaders`); scale ForwardAuth backend | Set `authResponseHeadersRegex` to avoid large header copies; configure explicit `AuthResponseTimeout` |
| **CPU spike from wildcard certificate renewal** | Traefik CPU jumps during ACME renewal; in-flight requests experience brief latency increase | `kubectl logs -n traefik <pod> | grep "acme"` during CPU spike window | Stagger renewal by pre-generating certificates; switch to DNS-01 challenge for background renewal | Enable ACME `caServer` staging for testing; use cert-manager as alternative to avoid Traefik ACME CPU load |
| **Noisy IngressRoute with aggressive health check polling** | Backend pods log high health check frequency; Traefik CPU elevated proportional to service count | `kubectl exec -n traefik <pod> -- wget -qO- http://localhost:8080/api/http/services | python3 -m json.tool | grep interval` | Increase health check `interval` and `timeout` in Service definition; reduce check frequency | Standardize health check intervals across services; default to 30 s interval for non-critical services |
| **Large request bodies overwhelming Traefik memory** | Traefik pod memory climbing; OOMKilled events; other services affected by pod restart | `kubectl logs -n traefik <pod> | grep "request body"` and identify large-payload services | Set `maxRequestBodyBytes` in middleware; add `BufferingMiddleware` with size limit for affected routes | Enforce payload size limits per service via middleware; direct large file uploads to direct service bypass |
| **Multiple services sharing single Traefik pod in DaemonSet with port conflict** | One namespace's cert renewal or config reload causes brief outage for all services on node | `traefik_config_last_reload_success` flipping to 0; node-level events correlate | Use separate Traefik deployments per namespace/team; isolate critical services to dedicated Traefik instance | Multi-instance Traefik architecture with `ingressClass` per team; avoid shared DaemonSet for mixed-criticality services |
| **Plugin running on every request consuming CPU** | CPU usage growing linearly with request rate; plugin-specific middleware shows high latency | `traefik_router_request_duration_seconds_bucket` p99 for routers using the plugin | Disable or optimize plugin; cache plugin decisions where possible; upgrade to latest plugin version | Benchmark plugins under load before production rollout; limit plugin scope to affected routes only |
| **Log volume spike from chatty upstream causing disk pressure** | Traefik access log disk write rate high; node disk I/O saturation; other pods I/O starved | `kubectl exec -n traefik <pod> -- du -sh /var/log/traefik/` | Enable access log filtering (`filters.statusCodes`); reduce log verbosity; use async log buffer | Configure log rotation and compression; use `bufferingSize` for access logs; ship logs via sidecar to external system |
| **Sticky session affinity overloading single backend** | One pod receives disproportionate traffic; others idle; overall latency rising | `traefik_service_server_up` + backend request distribution from metrics | Adjust sticky session cookie `path`; add `maxConn.amount` per server; implement passive health check | Evaluate whether sticky sessions are truly needed; prefer stateless backends; use Redis for session state instead |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Traefik pod OOMKilled | Pod restarts → in-flight requests dropped → backend health checks pause → backends marked unhealthy briefly → 503s to all routes | All traffic through that Traefik pod/node fails; retries hit surviving pods | `kubectl get events -n traefik --field-selector reason=OOMKilling`; `traefik_service_server_up` drops; Prometheus: `kube_pod_container_status_restarts_total` | Increase memory limit; set `--accesslog.bufferingsize=100`; scale to multiple replicas so restarts are rolling |
| ACME Let's Encrypt rate limit hit | Certificate renewal fails → cert expires → all HTTPS routes return cert error → clients get TLS handshake failure | All domains using that ACME resolver fail HTTPS; HTTP fallback depends on redirect config | Traefik log: `acme: error: 429 urn:ietf:params:acme:error:rateLimited`; `curl -Iv https://<domain>` shows expired cert | Switch to pre-provisioned cert via cert-manager; manually import cert as Kubernetes Secret; disable ACME redirector temporarily |
| Kubernetes API server unreachable | Traefik cannot watch IngressRoute CRDs → config updates stop → new routes not picked up; existing routes keep working until restart | New deployments/routes invisible to Traefik; stale routes for deleted services remain active | Traefik log: `provider: error connecting to k8s API`; `kubectl get events -n traefik` shows API server errors | Traefik continues serving cached config; fix API server; Traefik auto-recovers on reconnect |
| Upstream service all pods crash | Traefik backend pool empty → all requests to that service get 503 → retry storms from clients → Traefik connection exhaustion if circuit breaker absent | All traffic to affected service returns 503; if service is auth provider, all routes with ForwardAuth fail | `traefik_service_server_up{service="<name>"}` = 0; `traefik_service_requests_total{code="503"}` spikes | Enable CircuitBreaker middleware; configure health check with `passHostHeader = false`; alert on `traefik_service_server_up == 0` |
| DNS resolution failure for backend service | Traefik cannot resolve service hostname → backend marked down → 502 for all routes pointing to that backend | Single service outage; if shared backend (e.g., auth) all dependents fail | Traefik log: `dial tcp: lookup <service>: no such host`; `nslookup <service>.svc.cluster.local` from Traefik pod fails | Switch backend URL to pod IP or ClusterIP directly; check CoreDNS pods; restart Traefik to clear DNS cache |
| etcd/API server throttling Traefik watch | API server sends 429 to Traefik → watch reconnects flood → CPU spike → brief config reload storm | Config drift: routes added/removed in Kubernetes not reflected; service discovery delayed | Traefik log: `Received error from watcher, will retry: too many requests`; `apiserver_request_total{code="429"}` | Apply `--providers.kubernetescrd.throttleduration=10s` flag; reduce Traefik API watch poll frequency; upgrade Traefik version with backoff |
| cert-manager certificate Secret deleted | Traefik IngressRoute TLS references missing Secret → TLS handshake fails for affected routes | All routes on that TLS domain return cert error | Traefik log: `secret default/<secret-name> not found`; `kubectl get secret -n <ns> <tls-secret>` returns NotFound | Recreate cert-manager Certificate resource; or manually create Secret from backup cert; Traefik picks up new Secret within seconds |
| LoadBalancer Service IP lost (cloud provider issue) | External IP de-assigned → DNS points to dead IP → all external traffic drops → users see connection timeout | Complete external traffic outage; internal cluster traffic unaffected | `kubectl get svc -n traefik traefik` shows `<pending>` external IP; DNS `dig +short <domain>` returns IP with no route | Re-provision LoadBalancer Service; update DNS if IP changes; use anycast/static IP reservation in cloud provider |
| Middleware chain misconfiguration cascading across routes | Shared middleware (e.g., rate limiter) returns error → all routes using it fail with 500/502 | All routes referencing that middleware name return errors | `traefik_router_requests_total{code="500"}` spikes for routers using the broken middleware; Traefik log: `middleware: error` | Remove middleware reference from IngressRoute; apply emergency IngressRoute patch with `kubectl edit`; Traefik reloads within 1-2s |
| Node affinity/taint causes Traefik DaemonSet not scheduled | Traefik pod evicted from node → that node's ingress traffic unrouted → LoadBalancer backend pool shrinks | Traffic previously handled by evicted node dropped; asymmetric load on remaining nodes | `kubectl get pods -n traefik -o wide` shows missing pod on node; `kubectl describe node <node>` shows taint | Remove taint or update Traefik DaemonSet tolerations: `kubectl patch ds traefik -n traefik --type=json -p '[{"op":"add","path":"/spec/template/spec/tolerations/-","value":{"operator":"Exists"}}]'` |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Traefik version upgrade | IngressRoute CRD schema version mismatch; existing routes return 404; `traefik_config_last_reload_success` flips to 0 | Immediate post-upgrade | Traefik log: `CRD version mismatch`; compare `kubectl get crd ingressroutes.traefik.io -o yaml` schema version | `helm rollback traefik <prev_revision> -n traefik`; or `kubectl rollout undo deployment/traefik -n traefik` |
| Middleware `rateLimit` burst/average reduction | Legitimate traffic rejected with 429; application error rate increases | Immediate on config reload | Traefik log: `middleware: rateLimiter: too many requests`; `traefik_router_requests_total{code="429"}` rises after config change | Revert RateLimit middleware config: `kubectl edit middleware -n <ns> <name>`; increase `burst` and `average` values |
| TLS options change (minimum TLS version bump) | Clients using TLS 1.1/1.0 get `SSL handshake failed`; older browsers/integrations fail | Immediate on reload | Traefik log: `tls: no supported versions satisfying MinVersion`; client errors correlate with `tlsOption` change | Revert `minVersion` in TLSOption: `kubectl edit tlsoption -n traefik default`; Traefik reloads automatically |
| IngressRoute priority reordering | Wrong backend receives requests; regex routes shadow exact routes | Immediate on reload | Compare route priorities via `wget -qO- http://localhost:8080/api/http/routers | python3 -m json.tool`; check `priority` field | Explicitly set `priority` in IngressRoute spec to restore order; higher number = higher priority |
| Entrypoint port change in Traefik static config | LoadBalancer Service no longer forwards to correct port; traffic drops | Immediate, requires pod restart for static config | `kubectl get svc -n traefik` shows old `targetPort`; update Service `targetPort` to match new entrypoint | Revert static config change; update both Traefik deployment config and LoadBalancer Service `targetPort` simultaneously |
| `passHostHeader` change on service | Backend receives wrong `Host` header; virtual hosting on backend breaks; backend returns 404 | Immediate on reload | Backend access logs show unexpected Host header; correlate with Traefik config change timestamp | Revert `passHostHeader` in IngressRoute ServersTransport; or fix backend to accept multiple Host values |
| `serversTransport` TLS verification disabled/enabled | Backend HTTPS connections fail if cert changes from self-signed to CA-signed or vice versa | Immediate on reload | Traefik log: `x509: certificate signed by unknown authority`; correlate with serversTransport config change | Set `insecureSkipVerify: true` temporarily; or add CA cert to `rootCAs` in ServersTransport |
| Docker provider label removal on running container | Route for that service disappears from Traefik; 404 for that hostname | Immediate on next Docker provider poll (default 2s) | `wget -qO- http://localhost:8080/api/http/routers` no longer lists service; `docker inspect <container>` shows missing labels | Re-add labels to container: `docker stop`/start with correct labels; or use file provider as fallback |
| `accessLog.filters` change to suppress 2xx logs | 5xx errors no longer logged; debugging production issues requires unfiltered logging | Immediate, but only noticed when investigating incident | Traefik access log shows no entries during known traffic; compare access log volume before/after config change | Remove log filter: set `accessLog.filters.statusCodes = []`; or add `"500-599"` back to status code filter |
| Kubernetes RBAC change removing Traefik ServiceAccount permissions | Traefik loses ability to read Secrets (TLS certs) or watch IngressRoutes → certs expire, new routes not loaded | Immediate for new routes; certs fail at renewal | Traefik log: `secrets is forbidden: User "system:serviceaccount:traefik:traefik" cannot get resource "secrets"`; `kubectl auth can-i get secrets --as=system:serviceaccount:traefik:traefik` returns no | Restore RBAC: `kubectl apply -f traefik-clusterrole.yaml`; restart Traefik pod to re-initialize watchers |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Traefik multi-replica config split (different IngressRoute versions per pod) | `kubectl exec -n traefik <pod1> -- wget -qO- http://localhost:8080/api/http/routers | jq .[].name` vs pod2 | Intermittent 404s depending on which Traefik pod handles request; inconsistent routing | Users see flapping routes; non-deterministic behavior | Force config resync: `kubectl rollout restart deployment/traefik -n traefik`; ensure all pods watch same Kubernetes API |
| acme.json inconsistency between Traefik pods | Each Traefik pod has its own `acme.json`; separate ACME accounts | Let's Encrypt rate limit hit faster (N pods × renewal attempts); certs may differ between pods | Rate limit exhaustion; cert SNI mismatch on different replicas | Use cert-manager instead of Traefik ACME for multi-replica deployments; share certs via Kubernetes Secret |
| Stale backend in Traefik service registry after pod deletion | `wget -qO- http://localhost:8080/api/http/services | jq` shows dead backend IP | Traefik sends requests to terminated pod IP; gets connection refused; marks unhealthy only after health check cycle | Brief 502s until health check detects dead backend | Reduce health check `interval` to 5s; enable `terminationGracePeriodSeconds` on pods so they drain before termination |
| IngressRoute applied to wrong namespace (cross-namespace leak) | `kubectl get ingressroute -A` shows unexpected routes; `wget -qO- http://localhost:8080/api/http/routers` shows duplicate rule | Two services respond to same hostname; requests split between intended and unintended backends | Security exposure; traffic leak between tenants | Delete rogue IngressRoute: `kubectl delete ingressroute -n <wrong_ns> <name>`; enable `allowCrossNamespace: false` in Traefik config |
| TLS certificate applied to wrong entrypoint | `curl -Iv https://<domain>:443` returns wrong cert (different CN/SAN) | Browser cert mismatch warning; HSTS-enabled browsers block silently | User-visible TLS errors; potential MitM confusion | Check `tls.certResolver` or `tls.secretName` in IngressRoute; correct to right cert Secret; Traefik reloads within seconds |
| ForwardAuth response header cache inconsistency | ForwardAuth backend returns cached stale auth decision | User logged out but still authorized; or recently authorized user blocked | Security inconsistency; incorrect access control decisions | Add `Cache-Control: no-store` to ForwardAuth backend responses; set short TTL; restart ForwardAuth backend |
| Weighted round-robin weights drift (canary misconfiguration) | Traffic split does not match configured percentages; canary receives too much/little traffic | Unexpected canary rollout; production impact from canary version higher than intended | Deployment risk; unexpected version served to users | `wget -qO- http://localhost:8080/api/http/services | jq '.[] | select(.weighted) | .weighted.services'`; correct weights in TraefikService spec |
| Middleware applied to wrong IngressRoute (shared name conflict) | Different namespace's middleware inadvertently applied | Unexpected rate limiting, auth, or header manipulation on routes | Security gap or unintended blocking | Traefik resolves middleware by `namespace@kubernetescrd` syntax; use fully qualified names: `kubectl edit ingressroute` and specify `<name>@<namespace>` |
| LoadBalancer sticky session cookie mismatch between replicas | User hitting different Traefik replica gets different sticky cookie name | Session affinity broken; users bounce between backends | Stateful sessions broken; user experience degraded | Ensure consistent `cookieName` in sticky session config across all Traefik instances; use same config source |
| Cert-manager and Traefik ACME both managing same domain | Competing renewals; cert overwritten; timing race condition | Intermittent cert invalidity; unexpected cert changes | TLS errors during cert overwrite window | Choose one cert management system; remove ACME config from Traefik if using cert-manager; delete Traefik ACME challengeType for affected domain |

## Runbook Decision Trees

### Decision Tree 1: Backend Service Returning 502/503 Errors
```
Is traefik_service_requests_total{code="502"} or {code="503"} elevated?
├── YES → Check Traefik service health: wget -qO- http://localhost:8080/api/http/services | jq '.[] | select(.status!="enabled")'
│         ├── Service shows serverStatus "DOWN" → Check backend pod readiness: kubectl get endpoints <service> -n <ns>
│         │   ├── Endpoints empty → Pod not ready: kubectl describe pod <pod>; check readinessProbe
│         │   └── Endpoints populated but still DOWN → Check Traefik health check config; verify healthcheck.path returns 200
│         └── All services "enabled" → Check for timeout errors in logs: kubectl logs -n traefik <pod> | grep "dial tcp\|connection refused\|timeout"
│                   ├── "connection refused" → Backend pod crashed: kubectl logs <backend-pod> --previous
│                   └── "timeout" → Backend overloaded: check backend CPU/memory; scale up replicas
└── NO  → Is traefik_service_requests_total{code="504"} elevated?
          ├── YES → Root cause: upstream timeout → Fix: increase `responseHeaderTimeout` in ServersTransport; or reduce backend processing time
          └── NO  → Check for certificate errors: kubectl logs -n traefik <pod> | grep -i "certificate\|tls\|acme"
                    ├── ACME rate limit → Switch to staging ACME; wait 1 hour for rate limit reset
                    └── Cert expired → Force renewal: delete ACME cert secret; restart Traefik to trigger renewal
```

### Decision Tree 2: Traefik Not Picking Up New IngressRoute / Route Missing
```
Is the IngressRoute resource present and has no errors?
├── NO  → Apply missing resource: kubectl apply -f <ingressroute.yaml>; check: kubectl describe ingressroute <name>
└── YES → Check Traefik router list: wget -qO- http://localhost:8080/api/http/routers | jq '.[] | select(.name | contains("<expected>"))'
          ├── Router present but "status":"disabled" → Check rule syntax: kubectl get ingressroute <name> -o yaml; look for typos in Host() rule
          ├── Router absent → Check Traefik namespace watch config: kubectl exec -n traefik <pod> -- env | grep TRAEFIK
          │   ├── Namespace not watched → Edit Traefik static config to add namespace or use cluster-scoped provider
          │   └── Namespace watched → Check for CRD version mismatch: kubectl get crd ingressroutes.traefik.io -o jsonpath='{.spec.versions[*].name}'
          └── Router present but wrong backend → Middleware conflict or wrong Service reference
                    ├── Verify: kubectl get ingressroute <name> -o jsonpath='{.spec.routes[0].services[0]}'
                    └── Fix service name/port; apply: kubectl apply -f corrected-ingressroute.yaml
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| ACME rate limit exhaustion | Too many cert renewals (>50 certs/domain/week); staging/production API confusion | `kubectl logs -n traefik <pod> \| grep "acme\|rate limit\|too many certificates"` | All HTTPS domains stop renewing; TLS errors after expiry | Switch resolver to LetsEncrypt staging; use cert-manager as fallback; wait 7-day rate limit reset | Use wildcard certs; consolidate SANs; cap certificates per domain |
| Router rule explosion causing CPU spike | Hundreds of IngressRoutes with complex regex rules applied at once | `wget -qO- http://localhost:8080/api/http/routers \| jq 'length'`; check Traefik CPU: `kubectl top pod -n traefik` | Traefik CPU 100%; config reload latency >30s; new routes not applied | Simplify rules; use PathPrefix over regex; batch IngressRoute deployment | Limit IngressRoute count per namespace; review regex complexity in CI |
| Memory leak from middleware chain | Rate-limit or circuit-breaker middleware buffering large request bodies | `kubectl top pod -n traefik`; compare `traefik_entrypoint_open_connections` trend | Traefik OOM killed; brief service interruption during pod restart | Reduce `maxRequestBodyBytes` in buffering middleware; restart pod to clear leak | Set memory limits on Traefik pod; enable readBufferSize and writeBufferSize caps |
| Open connection exhaustion | Keep-alive connections from high-traffic clients not being closed | `traefik_entrypoint_open_connections{entrypoint="websecure"}` near pod thread limit | New connections rejected with 503; latency spike | `kubectl rollout restart deployment/traefik -n traefik`; reduce `idleConnTimeout` | Set `forwardingTimeouts.idleConnTimeout`; configure upstream keep-alive timeout |
| Log disk fill (access log verbosity) | Access logging enabled on high-RPS entry point without rotation | `kubectl exec -n traefik <pod> -- df -h /var/log/traefik/` | Pod disk full; log writes block request handling; pod restart loop | Disable access log temporarily: set `accessLog: null` in static config and reload | Use structured JSON logging with external log shipper; set `bufferingSize` |
| Plugin panic causing config reload loop | Third-party Traefik plugin (e.g., GeoBlock, JWT) crashes on malformed config | `kubectl logs -n traefik <pod> \| grep -c "plugin\|panic\|reload failure"` | Config reloads fail; no new routes applied; existing routes stale | Remove plugin from static config; restart Traefik without plugin | Pin plugin versions; test plugins in staging before production rollout |
| Wildcard route shadowing specific routes | IngressRoute with `PathPrefix("/")` matches before specific routes due to priority | `wget -qO- http://localhost:8080/api/http/routers \| jq '.[] \| {name,rule,priority}'` | Specific service routes return wrong backend responses | Add explicit `priority` field to IngressRoutes; higher number = higher priority | Enforce route priority conventions; lint IngressRoutes in CI for shadowing |
| Middleware applied globally breaking health checks | Auth middleware applied to all routes including `/healthz` | `curl -v http://<service>/healthz` returns 401; `traefik_service_requests_total{code="401"}` spike | Load balancer marks Traefik unhealthy; pod evicted from rotation | Add health check path to middleware exclusion list or use separate IngressRoute | Use named middleware with explicit route attachment; never apply auth globally |
| Entrypoint port conflict on node restart | Traefik DaemonSet port 80/443 conflicts with other process on node | `kubectl describe pod -n traefik <pod> \| grep "address already in use"` | Traefik pod stuck in CrashLoopBackOff on affected node | Cordon node: `kubectl cordon <node>`; identify conflicting process: `ss -tlnp \| grep ':80'` | Use hostPort only with DaemonSet; add node selector to prevent co-location |

## Latency & Performance Degradation Patterns
| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot entry point (single entrypoint absorbing all traffic) | p99 latency spike on `websecure`; other entrypoints unaffected | `traefik_entrypoint_request_duration_seconds_bucket{entrypoint="websecure"}` histogram in Prometheus; `wget -qO- http://localhost:8080/api/http/routers \| jq 'length'` | All ingress routes bound to single entrypoint; no traffic spread | Split high-traffic routes to dedicated entrypoints; add Traefik HPA based on `traefik_entrypoint_open_connections` |
| Connection pool exhaustion (backend keep-alive) | Backend pods receive no new connections; Traefik `502` rate rises | `traefik_service_requests_total{code="502"}` spiking; `wget -qO- http://localhost:8080/api/http/services \| jq '.[].serverStatus'` | Backend connection pool saturated; `maxIdleConnsPerHost` default too low | Set `forwardingTimeouts.dialTimeout=5s`; tune `transport.maxIdleConnsPerHost=200` in Traefik provider config |
| GC / memory pressure in Traefik Go runtime | Traefik latency jitter every ~30s; Go GC pauses visible | Prometheus `go_gc_duration_seconds` p99 >50ms for Traefik pod; `kubectl top pod -n traefik` memory growing | Large number of active connections; Go GC pressure from connection object allocation | Increase Traefik pod memory limit; set `GOGC=400` env var to reduce GC frequency; enable Go 1.21+ memory balloon |
| Thread pool saturation (HTTP/2 streams) | HTTP/2 clients see `GOAWAY` frames; stream reset errors in client logs | `traefik_entrypoint_open_connections{protocol="http"}` near Traefik goroutine limit; `kubectl logs -n traefik <pod> \| grep GOAWAY` | Too many concurrent HTTP/2 streams; Traefik goroutine pool exhausted | Set `http2.maxConcurrentStreams=250` in Traefik static config; increase pod CPU/memory limits |
| Slow backend response causing Traefik timeout cascade | Client receives `504 Gateway Timeout`; Traefik access log shows `duration > responseHeaderTimeout` | `kubectl logs -n traefik <pod> \| grep '"downstream_status":504'`; `traefik_service_request_duration_seconds` p99 >5s | Backend pods CPU-bound or waiting on DB; `responseHeaderTimeout` too short | Increase `forwardingTimeouts.responseHeaderTimeout=120s` for slow backends; add circuit breaker middleware |
| CPU steal (cloud) | Traefik request latency jitter; `top` shows `st` CPU time >5% | `kubectl exec -n traefik <pod> -- cat /proc/stat \| awk 'NR==1{print "steal:", $9}'` | Noisy co-tenant on cloud VM | Move Traefik pods to dedicated node group with reserved instances; use node affinity |
| Lock contention in rate-limit middleware | Rate-limit middleware causing serialization; p99 latency climbs for all routes using it | `traefik_router_request_duration_seconds_bucket` p99 for routers using the rate-limit middleware vs request rate; Traefik Go pprof: `curl http://localhost:8082/debug/pprof/mutex` | In-memory rate limiter using global mutex; high concurrency contention | Switch to Redis-backed rate limit with `redis` middleware; or shard rate limiters per backend service |
| Serialization overhead (large request body buffering) | Traefik memory spikes; latency increases for file upload routes | `kubectl top pod -n traefik` memory spike on upload paths; `traefik_entrypoint_requests_bytes_total` rising | Buffering middleware copying entire request body into memory | Disable buffering middleware on upload routes; use streaming proxy with `passTLSClientCert` only for auth; set `maxRequestBodyBytes` limit |
| Batch size misconfiguration (access log buffer) | Disk I/O spikes periodically; access log flush causes latency jitter | `kubectl exec -n traefik <pod> -- iostat -x 1 5` I/O spike correlating with log flush interval | Access log `bufferingSize` too small causing frequent disk flushes | Set `accessLog.bufferingSize=10000` in Traefik static config to buffer log writes |
| Downstream dependency latency (cert-manager ACME) | TLS handshake latency spike when ACME cert renewal in progress | `kubectl logs -n traefik <pod> \| grep -i "acme\|renew\|challenge"` during latency spike window | ACME HTTP-01 challenge response blocking request handling on same pod | Use DNS-01 challenge to decouple cert renewal from request path; or use cert-manager to pre-provision certs as Kubernetes secrets |

## Network & TLS Failure Patterns
| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry (ACME Let's Encrypt) | Browser `NET::ERR_CERT_DATE_INVALID`; `kubectl logs -n traefik <pod> \| grep "certificate has expired"` | ACME renewal failed (rate limit, DNS propagation, or storage issue); `acme.json` not updated | HTTPS broken for all affected domains; users see browser cert error | Check ACME storage secret; delete and re-create to force renewal: `kubectl delete secret -n traefik acme-certs`; verify ACME resolver config |
| mTLS rotation failure (IngressRoute with client cert) | Clients with new cert get `400 No required SSL certificate was sent`; old clients succeed | `kubectl get ingressroute <name> -o jsonpath='{.spec.tls.options}'`; `kubectl get tlsoption <name> -o yaml` — check `clientAuth.secretNames` | New client cert rejected; service-to-service auth broken | Update `tlsOption` to include new CA secret: add to `clientAuth.secretNames` list; apply and wait for Traefik config reload |
| DNS resolution failure (Kubernetes service backend) | Traefik `503 Service Unavailable`; log: `dial tcp: lookup <svc>.<ns>.svc.cluster.local: no such host` | `kubectl exec -n traefik <pod> -- nslookup <service>.<namespace>.svc.cluster.local` fails | Traefik cannot resolve backend service; all routes to that service return 503 | Fix CoreDNS pod health: `kubectl rollout restart deployment/coredns -n kube-system`; use ClusterIP directly in IngressRoute as temporary fallback |
| TCP connection exhaustion (NAT source ports) | Intermittent `502` from Traefik; backend sees connection refused; `ss -s` shows TIME-WAIT near port range | `ss -s` on Traefik node: `TIME-WAIT` count; `sysctl net.ipv4.ip_local_port_range` | Many short-lived connections exhausting ephemeral port range | `sysctl -w net.ipv4.ip_local_port_range="1024 65535"`; `net.ipv4.tcp_tw_reuse=1`; enable HTTP keep-alive to backend |
| Load balancer misconfiguration (health check) | Traefik pods not receiving traffic from cloud LB; LB shows backends as unhealthy | Cloud LB health check target: verify `HTTP:8080/ping` endpoint is reachable; `curl http://traefik-lb:80/ping` | Traefik pods bypassed by cloud LB; no traffic flows through | Configure cloud LB health check: `GET /ping` on port 8080 (Traefik API/metrics port); ensure Traefik `ping` entrypoint is configured |
| Packet loss / retransmit (Traefik → backend) | Sporadic `502` errors; Traefik log shows `read tcp: connection reset by peer` | `kubectl exec -n traefik <pod> -- ss -s` (retransmit count); `netstat -s \| grep retransmit` | Intermittent backend connection failures; elevated error rate | Check CNI for packet drops between Traefik and backend namespace; check backend pod health; review NetworkPolicy |
| MTU mismatch (VXLAN pod network) | Large requests fail with `502`; small requests succeed; Wireshark shows fragmentation | `kubectl exec -n traefik <pod> -- ping -M do -s 1472 <backend-pod-ip>` — if fails, MTU mismatch confirmed | Large HTTP request bodies fragmented; TCP retransmits; intermittent 502 | Set CNI MTU to 1450 for VXLAN; patch Calico/Flannel/Cilium MTU config |
| Firewall rule change blocking port 443 | All HTTPS traffic stopped; Traefik still running; cloud SG or iptables rule missing | `nc -zv <traefik-lb-ip> 443` from external; check cloud security group for port 443 | Complete HTTPS outage for all services behind Traefik | Restore cloud security group rule or iptables rule for port 443; check if network policy change caused it |
| SSL handshake timeout (Traefik → mTLS backend) | Traefik log: `tls: handshake failure` connecting to backend; `traefik_service_requests_total{code="502"}` spike | `openssl s_client -connect <backend>:443 -cert client.crt -key client.key` from Traefik pod | Backend requires client cert; Traefik not presenting cert in `serversTransport` | Configure `serversTransport` with `certificates` block in Traefik dynamic config; reference correct secret |
| Connection reset (WebSocket upgrade failure) | WebSocket clients disconnected; Traefik log: `unable to upgrade connection: ...` | `kubectl logs -n traefik <pod> \| grep -i "websocket\|upgrade"` | Missing `Connection: Upgrade` header preservation; proxy stripping WebSocket headers | Add `headers` middleware with `customResponseHeaders: {"Connection": "keep-alive"}`; verify IngressRoute has no stripping middleware on WebSocket path |

## Resource Exhaustion Patterns
| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill (Traefik pod) | Pod `OOMKilled`; all in-flight requests dropped; pod restarts | `kubectl get pod -n traefik -o jsonpath='{.items[*].status.containerStatuses[0].lastState.terminated.reason}'` | Increase memory limit in Helm values: `resources.limits.memory=512Mi`; reduce `accessLog.bufferingSize` | Set memory limit based on connection count × overhead; monitor `go_memstats_alloc_bytes` |
| Disk full (access log) | Traefik pod write errors; log: `no space left on device`; access log drops | `kubectl exec -n traefik <pod> -- df -h /var/log/traefik/` | Disable access log temporarily: set `accessLog: {}` with no file path (stdout only); restart pod | Use stdout logging with external log shipper; do not write access logs to pod-local disk |
| Disk full (ACME JSON storage) | `acme.json` cannot be written; ACME cert renewal fails silently | `kubectl exec -n traefik <pod> -- df -h /data/` (if using local ACME storage) | Expand PVC or switch ACME storage to Kubernetes secret (`certificatesResolvers.acme.tlsChallenge` with `storage: ""`) | Use Kubernetes Secret as ACME storage (default in Traefik v2+); avoids local disk dependency |
| File descriptor exhaustion | Traefik error: `too many open files`; new connections refused | `kubectl exec -n traefik <pod> -- cat /proc/$(pgrep traefik)/limits \| grep "open files"` | Raise per-process FD limit on the node (`LimitNOFILE` in the systemd unit or container runtime config); set `nofile` ulimit on Traefik pod spec; restart pod | Configure host `LimitNOFILE=1048576`; monitor `process_open_fds` |
| Inode exhaustion | ACME or temp files consuming all inodes; pod cannot write new files | `kubectl exec -n traefik <pod> -- df -i /` | Delete temp/partial cert files; Traefik has few inodes usually — check for log file proliferation | Use emptyDir for temp storage; do not write many small files to pod filesystem |
| CPU throttle (CFS) | Traefik request latency spikes under load; `container_cpu_cfs_throttled_seconds_total` high | Prometheus: `rate(container_cpu_cfs_throttled_seconds_total{container="traefik"}[5m])` | Remove CPU limit from Traefik Helm values or increase significantly; Traefik is latency-sensitive | Use Burstable or Guaranteed QoS; set `resources.requests.cpu=500m` without `limits.cpu` for Traefik |
| Swap exhaustion (node) | Traefik Go GC takes >1s; latency spikes; memory paging visible | `kubectl exec -n traefik <pod> -- cat /proc/meminfo \| grep Swap` | Disable swap on Traefik nodes; add memory; `kubectl drain <node>` and reschedule | Run Traefik on nodes with swap disabled; use node taints to ensure co-location with other latency-sensitive services |
| Kernel PID / thread limit | Traefik cannot spawn new goroutine OS threads; connections queue up | Node: `cat /proc/sys/kernel/threads-max` + `ps -eLf \| wc -l` | `sysctl -w kernel.threads-max=4194304`; `sysctl -w kernel.pid_max=4194304` on host | Set kernel limits in node DaemonSet init container; Traefik uses ~1000 threads at peak |
| Network socket buffer exhaustion | High-throughput routes show throughput plateau; Traefik `sendmsg` errors in kernel log | `sysctl net.core.rmem_max net.core.wmem_max` on Traefik node | `sysctl -w net.core.rmem_max=134217728 net.core.wmem_max=134217728` | Tune socket buffers in node init container; configure `net.ipv4.tcp_rmem` and `tcp_wmem` |
| Ephemeral port exhaustion (Traefik → backends) | `502` errors with `connect: cannot assign requested address` in Traefik logs | `ss -s` on Traefik node: TIME-WAIT count approaching port range limit | `sysctl -w net.ipv4.ip_local_port_range="1024 65535"`; enable HTTP keep-alive to all backends | Configure `forwardingTimeouts.idleConnTimeout=90s`; enable `transport.keepAlive=true` in serversTransport |

## Distributed Transaction & Event Ordering Failures
| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation (duplicate request forwarding) | Traefik retries request to backend on timeout; backend receives duplicate POST; not idempotent | `kubectl logs -n traefik <pod> \| grep '"method":"POST"' \| grep '"downstream_status":5'` — count POST 5xx then retry attempts | Duplicate order/payment submission; data inconsistency in backend | Disable automatic retries on non-idempotent methods: set `retry: attempts: 0` for POST/PUT/PATCH routes; require `Idempotency-Key` header in backend |
| Saga / workflow failure (canary weight split) | Weighted service split mid-saga: step 1 hits canary backend, step 2 hits stable backend; state inconsistent | `wget -qO- http://localhost:8080/api/http/services \| jq '.[] \| select(.weighted) \| {name,servers:.weighted.services}'` — verify weights; check if saga steps land on same backend | Saga steps use different code versions; compensating transactions may not exist in old version | Use session-affinity middleware (sticky sessions) for saga workflows; or complete saga before shifting canary weight |
| Message replay causing duplicate webhook delivery | Traefik retries timed-out webhook request to backend; upstream delivery service also retries; triple delivery | `kubectl logs -n traefik <pod> \| grep '"upstream_addr":"<webhook-backend>"' \| grep 'retry'` — count duplicates | Downstream webhook handler processes event multiple times; side effects executed repeatedly | Add `X-Request-ID` header via Traefik headers middleware; backend deduplicates by request ID; set `retry: attempts: 1` max for webhook routes |
| Cross-service deadlock (circuit breaker + rate limiter interaction) | Circuit breaker opens; rate limiter still counting failed requests toward quota; legitimate retries blocked | `wget -qO- http://localhost:8080/api/http/middlewares \| jq '.[] \| select(.circuitBreaker or .rateLimit) \| {name,type}'` — identify overlapping middleware | Services fail open or closed simultaneously; cascading 429/503 | Separate circuit breaker and rate limiter into distinct middleware chains; ensure circuit breaker trip does not consume rate limit budget |
| Out-of-order request delivery (HTTP/1.1 pipelining) | Backend receives requests in wrong order; stateful protocol breaks | `kubectl logs -n traefik <pod> \| grep '"proto":"HTTP/1.1"'` for pipelined requests to stateful backend | State machine in backend confused; session corruption for chat/stateful APIs | Disable HTTP/1.1 pipelining on routes to stateful backends; enforce HTTP/2 multiplexing (preserves ordering per stream) |
| At-least-once middleware execution (retry on error) | Traefik retry middleware replays request; non-idempotent backend creates duplicate resource | `kubectl get middleware -n <ns> <retry-middleware> -o yaml` — check `attempts` and `initialInterval`; `kubectl logs -n traefik <pod> \| grep 'retrying request'` | Duplicate resource creation in backend databases | Restrict retry middleware to idempotent routes only (GET, HEAD); remove from POST/PATCH/DELETE routes |
| Compensating transaction failure (canary rollback) | Canary deployment causes errors; rollback changes Traefik weighted service back to stable; in-flight canary requests fail mid-stream | `kubectl get ingressroute -n <ns> <name> -o yaml` — watch weight change during rollback; `traefik_service_requests_total` error spike during weight shift | In-flight requests to canary backend abruptly closed; clients see connection reset | Implement graceful canary drain: shift weight to 0% then wait for in-flight requests to complete (poll `traefik_entrypoint_open_connections`) before removing canary backend |
| Distributed lock expiry during TLS cert handoff | cert-manager renews cert and updates Kubernetes secret; Traefik watching secret detects change and reloads TLS mid-connection; active TLS sessions disrupted | `kubectl logs -n traefik <pod> \| grep -i "certificate\|reload\|tls"` during cert renewal window | Active HTTPS connections reset during TLS config reload; clients see `connection reset` | Enable Traefik's graceful shutdown with `forwardingTimeout`; cert-manager: use `renewBefore` well ahead of expiry to avoid rush renewal during peak traffic |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor (one IngressRoute with heavy middleware chain) | Prometheus `traefik_service_request_duration_seconds` p99 spike for all services; `kubectl top pod -n traefik` CPU at limit | All tenant routes share Traefik pod CPU; heavy regex middleware on one route starves others | `kubectl annotate ingressroute <noisy-route> -n <ns> traefik.io/priority="-1"` to lower route priority | Move CPU-intensive middleware (JWT validation, complex regex) to separate Traefik instance; use dedicated entrypoint per tenant |
| Memory pressure from adjacent tenant (large response buffering) | Traefik pod `go_memstats_alloc_bytes` growing; OOMKill risk; one tenant route serving large file downloads | Other tenants' routes experience latency during GC pressure | Disable buffering middleware on large-response routes: `kubectl edit middleware -n <ns> buffering-<tenant>` | Disable `buffering` middleware for file-download routes; stream responses directly without buffering; set `maxResponseBodyBytes` limit |
| Disk I/O saturation (access log write) | Traefik access log writes filling disk; pod I/O rate high; other pod operations slowed | Access log-induced I/O on shared node disk starves other pods | Disable access log file output temporarily: `kubectl set env deployment/traefik -n traefik TRAEFIK_ACCESSLOG=false` | Route access logs to stdout only; use external log shipper (Fluent Bit) to avoid local disk I/O |
| Network bandwidth monopoly (large asset CDN route) | `traefik_entrypoint_responses_bytes_total` metric dominated by one service; node bandwidth saturated | Other tenants' services see increased latency; WebSocket connections dropped | Add bandwidth-limit annotation to offending IngressRoute via `plugin` middleware; or use separate Traefik instance for CDN routes | Configure dedicated Traefik instance for large-asset routes with separate node group; use cloud CDN offloading for static assets |
| Connection pool starvation (forwardAuth service bottleneck) | All tenant routes hang waiting for ForwardAuth response; `traefik_service_open_connections{service="forwardauth"}` at max | All authenticated routes return 401 or timeout; user sessions broken | Scale ForwardAuth backend: `kubectl scale deployment forwardauth --replicas=5`; add ForwardAuth result cache: `forwardAuth.authResponseHeaders=Cache-Control` | Add connection pooling configuration to ForwardAuth middleware; cache auth decisions in Redis; separate auth service per tenant tier |
| Quota enforcement gap (no rate limit on specific route) | One tenant's IngressRoute missing `rateLimit` middleware; sends 10x normal traffic | Other tenants share entrypoint and backend connection limits; slower responses | Apply rateLimit middleware: `kubectl patch ingressroute <name> -n <ns> --type=json -p '[{"op":"add","path":"/spec/routes/0/middlewares/-","value":{"name":"rate-limit-default"}}]'` | Enforce rateLimit middleware via Kyverno policy on all IngressRoutes; add default middleware in static config |
| Cross-tenant data leak risk (shared router rule collision) | Two tenants' IngressRoutes have overlapping `Host` + `PathPrefix` rules; one tenant's traffic routed to other's backend | Tenant A receives Tenant B's data; both tenants see unexpected responses | Check router priority: `wget -qO- http://localhost:8080/api/http/routers \| jq '.[] \| {name,rule,priority,service}'` — identify conflicting rules | Assign explicit priorities to all IngressRoutes via `spec.routes[].priority`; add namespace prefix to route rules |
| Rate limit bypass (custom `X-Forwarded-For` header injection) | Client sets `X-Forwarded-For: 127.0.0.1` to spoof trusted IP; rate limiter uses client-supplied header | Attacker bypasses IP-based rate limiting by spoofing trusted IP in header | Check `traefik_service_requests_total` for rate-limited service — if no 429s despite high traffic, bypass confirmed | Configure Traefik to use `depth=1` in `ipStrategy` for rate limit middleware to use real client IP; add `X-Real-Ip` trust configuration |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure (Traefik /metrics endpoint) | Prometheus shows stale `traefik_*` metrics; dashboard flatlines | Traefik `--metrics.prometheus.entrypoint` not configured; or scrape target `down` in Prometheus | `curl http://traefik:8080/metrics \| grep traefik_entrypoint_requests_total` — verify endpoint live | Configure dedicated `metrics` entrypoint in Traefik static config: `entrypoints.metrics.address=:8082`; update Prometheus scrape target |
| Trace sampling gap missing slow requests | APM shows no traces for Traefik → backend latency spikes; distributed trace broken | Traefik tracing not enabled or sampling rate too low (default 0 in some versions) | Check access log for slow requests: `kubectl logs -n traefik <pod> \| jq 'select(.Duration > 5000000000)'` | Enable Jaeger/Tempo tracing: `--tracing.jaeger.samplingServerURL=http://jaeger:5778/sampling`; set `--tracing.jaeger.samplingParam=0.1` |
| Log pipeline silent drop | Traefik access log entries missing from Loki; gaps in request history | Fluent Bit on Traefik node restarted; buffer overflow; Traefik logging to file not stdout | `kubectl logs -n traefik <pod> --tail=50` direct fallback; check Fluent Bit: `kubectl get pod -n logging -l app=fluent-bit` | Switch Traefik to stdout JSON logging: `accessLog.filePath=` (empty = stdout); verify Fluent Bit pipeline health |
| Alert rule misconfiguration (5xx rate) | Backend returns 503 errors for 10 min; no alert fires | Prometheus alert uses `traefik_service_requests_total` but service name label changed after IngressRoute rename | `curl http://prometheus:9090/api/v1/rules \| jq '.data.groups[] \| select(.name \| contains("traefik"))'` — verify rule evaluations | Audit alert rule labels match current Traefik service naming conventions; test with `ALERTS{alertname=~"Traefik.*"}` |
| Cardinality explosion blinding dashboards | Grafana Traefik dashboard fails to render; Prometheus memory spikes | `traefik_service_requests_total` with `url` label containing full request URLs; unbounded cardinality | `curl http://prometheus:9090/api/v1/label/__name__/values \| jq '.data \| map(select(startswith("traefik"))) \| length'` — count series | Add `metric_relabel_configs` to drop high-cardinality labels (e.g., URL paths); use prefix aggregation only |
| Missing health endpoint coverage | Traefik pod healthy according to kubelet but ACME cert renewal silently failing; HTTPS broken hours later | Kubernetes probe checks `/ping` on port 8080 which succeeds even if ACME is broken | Add external blackbox probe: `blackbox_exporter` HTTPS check on `https://<domain>/` from outside cluster | Configure Prometheus Blackbox Exporter with HTTPS check for all ACME-managed domains; alert on `probe_ssl_earliest_cert_expiry < 7 days` |
| Instrumentation gap in critical path (middleware execution time) | Requests slow but Traefik service latency metric looks normal; middleware is the bottleneck | Traefik `traefik_service_request_duration_seconds` measures total request time but does not break down per-middleware | Use Traefik access log `RequestCount` + custom log fields to estimate middleware overhead; check `OverheadDuration` field | Enable Traefik tracing with middleware spans: `--tracing.jaeger` — each middleware appears as a span in trace |
| Alertmanager / PagerDuty outage | Traefik complete outage (pod crash); on-call not paged; users complain first | Alertmanager pod OOMKilled; PagerDuty webhook integration key expired | `curl http://alertmanager:9093/-/healthy`; `curl -X POST http://alertmanager:9093/api/v2/alerts -d '[{"labels":{"alertname":"TestAlert"}}]'` | Add Alertmanager HA (3 replicas); configure deadman's snitch; test PagerDuty key monthly with synthetic alert |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor Traefik version upgrade rollback | New Traefik version breaks WebSocket routing; clients see `101 Switching Protocols` then immediate disconnect | `kubectl logs -n traefik <pod> \| grep -i "websocket\|upgrade\|101"` — count disconnect events post-upgrade | Roll back Helm release: `helm rollback traefik <previous_revision> -n traefik`; verify with `helm history traefik -n traefik` | Pin Traefik image tag in Helm values; test WebSocket routes in staging before upgrading production |
| Major version upgrade (v1 → v2/v3) | IngressRoute CRDs from v1 (`Ingress` annotations) not recognized by v2 Traefik; all routes broken | `kubectl get ingressroute -A \| wc -l` — if 0 and using old `Ingress` objects: migration incomplete; `kubectl get ingress -A` for old-style configs | Redeploy v1 Traefik image; update Helm values to previous version; CRDs are separate and can coexist | Use Traefik migration guide; install new CRDs before upgrading; run old and new Traefik in parallel during transition |
| Schema migration (CRD API version change) | After upgrading Traefik CRDs, existing IngressRoute objects fail validation; `kubectl apply` returns schema error | `kubectl get ingressroute -A -o json \| jq '.items[0].apiVersion'` — check if old API version still valid | Apply previous CRD manifest: `kubectl apply -f https://raw.githubusercontent.com/traefik/traefik/<prev_tag>/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml` | Always apply new CRDs before upgrading Traefik Helm chart; test `kubectl apply --dry-run=server` against new CRD version |
| Rolling upgrade version skew | Two Traefik pods running different versions; ACME cert managed by old pod not readable by new pod | `kubectl get pod -n traefik -o jsonpath='{.items[*].status.containerStatuses[0].image}'` — check for mixed versions | Pause rollout: `kubectl rollout pause deployment/traefik -n traefik`; undo: `kubectl rollout undo deployment/traefik -n traefik` | Use `maxUnavailable=0, maxSurge=1` for Traefik deployment updates; ensure ACME storage is Kubernetes Secret (not local file) |
| Zero-downtime migration gone wrong (ACME storage switch) | Migrating ACME storage from `acme.json` file to Kubernetes Secret; existing certs lost; all domains need re-challenge | `kubectl get secret -n traefik acme-certs -o jsonpath='{.data.acme\.json}' \| base64 -d \| jq 'keys'` — check if domains populated | Import old `acme.json` content into Kubernetes Secret: `kubectl create secret generic acme-certs --from-file=acme.json=/data/acme.json -n traefik` | Export `acme.json` before switching storage backends; test storage migration in staging with real ACME staging environment |
| Config format change (static config YAML/TOML breaking syntax) | Traefik pod fails to start after config change; log: `Error while loading config`; all traffic stopped | `kubectl logs -n traefik <pod> --previous \| grep -i "error\|invalid\|parse"` | Roll back ConfigMap: `kubectl rollout undo deployment/traefik -n traefik` which restores previous pod spec with previous ConfigMap revision | Validate Traefik config in a sandbox container before apply (`docker run --rm -v $(pwd)/traefik.yaml:/etc/traefik/traefik.yaml traefik:<tag>`); use `kubectl diff` to preview changes |
| Data format incompatibility (ACME certificate format change) | After upgrading to Traefik v3, existing `acme.json` certificates not recognized; all HTTPS broken | `kubectl exec -n traefik <pod> -- cat /data/acme.json \| jq 'keys'` — check certificate structure vs v3 expected format | Trigger ACME re-challenge: delete `acme.json` content for affected domains; Traefik will re-issue via Let's Encrypt (may hit rate limits) | Backup `acme.json` before major upgrade; check Traefik v3 migration guide for ACME storage format changes |
| Feature flag rollout causing regression (Kubernetes provider namespace filter) | After enabling `providers.kubernetesIngress.namespaces` filter, routes from excluded namespaces disappear | `wget -qO- http://localhost:8080/api/http/routers \| jq 'length'` — route count drops post-config change | Remove namespace filter: `kubectl edit configmap -n traefik traefik-config` and remove `namespaces` field; `kubectl rollout restart deployment/traefik -n traefik` | Test provider config changes in staging; spin up a throwaway Traefik pod with the new `--configFile` to validate before rolling out; monitor route count metric after config changes |

## Kernel/OS & Host-Level Failure Patterns
| Failure Mode | Traefik-Specific Symptom | Detection Command | Root Cause Confirmation | Remediation |
|-------------|-------------------------|-------------------|------------------------|-------------|
| OOM killer targets Traefik process | Traefik pod restarted; all ingress routes briefly unavailable; `OOMKilled` in pod status | `dmesg -T \| grep -i "oom.*traefik"; kubectl describe pod -n traefik <pod> \| grep OOMKilled` | `kubectl get pod -n traefik <pod> -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}'` returns `OOMKilled` | Increase Traefik memory limits in Helm values; reduce `maxIdleConnsPerHost`; check for memory leak in middleware chain via `wget -qO- http://localhost:8080/debug/pprof/heap > /tmp/heap.pprof` |
| Inode exhaustion on ACME storage volume | Let's Encrypt certificate renewals fail; new routes cannot obtain certificates; `no space left on device` in logs | `df -i /data; kubectl exec -n traefik <pod> -- df -i /data` | Inode usage at 100% while disk space available; `acme.json` file fragmentation or excessive tmp files | Clean stale ACME challenge files; mount dedicated volume for `/data` with sufficient inode count; use `ext4` with `-N` inode parameter |
| CPU steal on shared cloud instance | Traefik request latency spikes unpredictably; `traefik_entrypoint_request_duration_seconds` p99 > 2s | `cat /proc/stat \| grep cpu; kubectl top pod -n traefik; mpstat -P ALL 1 5 \| grep steal` | CPU steal% > 10% correlates with latency spikes; other pods on same node also affected | Migrate Traefik to dedicated node pool with guaranteed CPU; use `nodeSelector` or `nodeAffinity` for Traefik pods; request burstable-to-dedicated instance types |
| NTP clock skew breaks ACME challenge validation | Let's Encrypt HTTP-01 or TLS-ALPN-01 challenge fails with timestamp validation error; certificates not renewed | `kubectl exec -n traefik <pod> -- date; ntpq -p; chronyc tracking \| grep "System time"` | Clock skew > 30s causes ACME server to reject challenge responses; `traefik_tls_certs_not_after` shows expired certs | Sync NTP on all nodes: `chronyc makestep`; deploy chrony DaemonSet; add NTP monitoring alert for skew > 5s |
| File descriptor exhaustion under high connection count | Traefik returns `502 Bad Gateway` for new connections; log: `socket: too many open files` | `kubectl exec -n traefik <pod> -- cat /proc/1/limits \| grep "open files"; ls -1 /proc/1/fd \| wc -l` | FD count at ulimit; high keep-alive connection count from many backends | Increase ulimit in pod securityContext: `ulimit -n 1048576`; set Traefik `transport.respondingTimeouts.idleTimeout=90s` to reclaim idle connections; tune `maxIdleConnsPerHost` |
| TCP conntrack table full drops new connections | New TCP connections to Traefik entrypoints silently dropped; existing connections work; no Traefik error logs | `conntrack -C; sysctl net.netfilter.nf_conntrack_count; dmesg \| grep conntrack` | `nf_conntrack_count` equals `nf_conntrack_max`; `dmesg` shows `nf_conntrack: table full, dropping packet` | Increase conntrack max: `sysctl -w net.netfilter.nf_conntrack_max=524288`; reduce `nf_conntrack_tcp_timeout_established` to 3600; offload TCP termination to cloud LB if possible |
| Kernel regression breaks SO_REUSEPORT after node upgrade | Traefik fails to bind entrypoint port after node kernel upgrade; `address already in use` | `kubectl logs -n traefik <pod> \| grep "address already in use"; uname -r; ss -tlnp \| grep :443` | New kernel version changed `SO_REUSEPORT` behavior; stale socket from previous Traefik process | Restart kubelet to release stale sockets; pin kernel version in node image; test kernel upgrades on canary node before fleet rollout |
| cgroup memory pressure causes Traefik throttling | Traefik responds slowly; no OOM but throughput degraded; `memory.pressure` shows `some` stalls | `cat /sys/fs/cgroup/memory/kubepods/.../memory.pressure; kubectl describe pod -n traefik <pod> \| grep -A5 Limits` | Memory pressure `some` or `full` counters increasing; Traefik allocations near cgroup limit triggering reclaim | Increase memory request to equal limit (Guaranteed QoS); reduce middleware memory footprint; disable unnecessary access log buffering |

## Deployment Pipeline & GitOps Failure Patterns
| Failure Mode | Traefik-Specific Symptom | Detection Command | Root Cause Confirmation | Remediation |
|-------------|-------------------------|-------------------|------------------------|-------------|
| Traefik image pull failure from DockerHub rate limit | Traefik pod stuck in `ImagePullBackOff`; ingress traffic served by stale old pod or not at all | `kubectl describe pod -n traefik <pod> \| grep -A3 "Failed to pull"; kubectl get events -n traefik --field-selector reason=Failed` | Event message contains `toomanyrequests` or `429`; DockerHub anonymous pull limit exceeded | Mirror `traefik` image to private registry (ECR/GCR/ACR); configure `imagePullSecrets` with DockerHub paid credentials; use `image.registry` override in Helm values |
| Helm drift between Git and live Traefik config | IngressRoute CRDs in cluster differ from Git; routes silently misconfigured; some paths return 404 | `helm get values traefik -n traefik -o yaml > /tmp/live.yaml; diff /tmp/live.yaml values/traefik-values.yaml` | Helm values diverged from Git source of truth; manual `kubectl edit` overrides not committed | Re-sync from Git: `helm upgrade traefik traefik/traefik -n traefik -f values/traefik-values.yaml`; enable ArgoCD self-heal for Traefik Application |
| ArgoCD sync stuck on Traefik CRD update | ArgoCD shows `OutOfSync` for Traefik; new IngressRoutes not applied; existing routes work but new ones missing | `argocd app get traefik --grpc-web \| grep -i "sync\|health"; kubectl get application -n argocd traefik -o jsonpath='{.status.sync.status}'` | CRD update requires `Replace` sync policy but ArgoCD defaults to `Apply`; CRD too large for server-side apply | Add `argocd.argoproj.io/sync-options: Replace=true` annotation to Traefik CRDs; or apply CRDs separately before ArgoCD sync |
| PDB blocks Traefik rolling update | Traefik deployment update stuck; new pod pending; old pod cannot be evicted; ingress running stale config | `kubectl get pdb -n traefik; kubectl describe pdb traefik-pdb -n traefik; kubectl rollout status deployment/traefik -n traefik` | PDB `minAvailable=1` with `maxUnavailable=0` on single-replica deployment blocks eviction | Set PDB `maxUnavailable=1` instead of `minAvailable`; scale Traefik to 2+ replicas before upgrades; use `maxSurge=1` in deployment strategy |
| Blue-green cutover leaves stale Traefik routing rules | After switching from blue to green Traefik deployment, old IngressRoutes still active; traffic split between old and new config | `kubectl get ingressroute -A -o jsonpath='{.items[*].metadata.labels}' \| grep -E "blue\|green"` | Old IngressRoute objects not deleted during cutover; both blue and green IngressRoutes match same `Host()` rule | Delete old-env IngressRoutes before switching `Service` backend: `kubectl delete ingressroute -l env=blue -n traefik`; use Traefik weighted round-robin for gradual cutover |
| ConfigMap drift causes Traefik static config mismatch | Traefik running with stale static config; new entrypoints or middleware not available; `kubectl diff` shows changes | `kubectl get configmap -n traefik traefik-config -o yaml \| diff - traefik-config.yaml` | ConfigMap updated but Traefik pod not restarted; static config requires restart, not just reload | Use `helm.sh/hook` annotations or `reloader.stakater.com/auto: "true"` to auto-restart on ConfigMap change; add configmap hash annotation to pod template |
| Secret rotation breaks Traefik dashboard basic auth | Traefik dashboard returns `401 Unauthorized` after secret rotation; no one can access dashboard | `kubectl get secret -n traefik traefik-dashboard-auth -o jsonpath='{.data.users}' \| base64 -d` — verify htpasswd format | New secret value not in valid htpasswd format; or secret name changed but middleware still references old name | Validate htpasswd format before applying: `htpasswd -nbB admin <password>`; update Middleware `basicAuth.secret` reference; restart not needed for dynamic config |
| Terraform and Helm fight over Traefik LoadBalancer Service | Traefik `LoadBalancer` Service annotation changes reverted every 5 min; cloud LB health checks misconfigured | `kubectl get svc -n traefik traefik -o yaml \| grep -A20 annotations; terraform plan \| grep traefik` | Both Terraform (managing cloud LB) and Helm (managing K8s Service) set conflicting annotations; Terraform overrides Helm | Use `lifecycle { ignore_changes }` in Terraform for K8s-managed annotations; or manage Service entirely in Terraform with `kubernetes_service` resource and remove from Helm chart |

## Service Mesh & API Gateway Edge Cases
| Failure Mode | Traefik-Specific Symptom | Detection Command | Root Cause Confirmation | Remediation |
|-------------|-------------------------|-------------------|------------------------|-------------|
| Circuit breaker false positive on healthy backend | Traefik returns `503` for a backend that is actually healthy; `circuitBreaker` middleware triggers erroneously | `wget -qO- http://localhost:8080/api/http/services \| jq '.[] \| select(.status != "enabled") \| .name'` | CircuitBreaker expression `ResponseCodeRatio(500, 600, 0, 600) > 0.30` triggers on a single slow response burst; backend health check passes | Tune circuit breaker expression: increase sample window `LatencyAtQuantileMS(50.0) > 200`; add `checkPeriod` and `fallbackDuration` to smooth out transients |
| Rate limiting blocks legitimate high-volume client | Specific API consumer gets `429 Too Many Requests` from Traefik `rateLimit` middleware; other clients fine | `kubectl logs -n traefik <pod> \| grep "Rate limit exceeded"; wget -qO- http://localhost:8080/api/http/middlewares \| jq '.[] \| select(.rateLimit)'` | `rateLimit.average` and `rateLimit.burst` too low for legitimate high-throughput client; `sourceCriterion` groups all requests from same IP | Add per-client rate limit with `sourceCriterion.requestHeaderName: X-API-Key`; increase `average` and `burst` for identified legitimate clients; use `ipStrategy.excludedIPs` for trusted proxies |
| Stale service discovery endpoints after backend scale-down | Traefik routes traffic to terminated backend pods; clients see `502 Bad Gateway` intermittently | `wget -qO- http://localhost:8080/api/http/services/<service>@kubernetescrd \| jq '.loadBalancer.servers'` — check for IPs of non-existent pods | Kubernetes Endpoints not yet updated; Traefik provider poll interval too long; `readinessProbe` not configured on backend | Reduce Traefik provider poll interval: `providers.kubernetesIngress.throttleDuration=2s`; ensure backend pods have `readinessProbe`; configure `serversTransport.forwardingTimeouts.dialTimeout=5s` |
| mTLS certificate rotation interrupts backend connections | After rotating mTLS certificates for backend services, Traefik returns `502`; log: `tls: bad certificate` | `kubectl logs -n traefik <pod> \| grep "tls\|certificate\|x509"; openssl s_client -connect <backend>:443 -cert /certs/client.pem` | Traefik ServersTransport still references old client certificate; new cert not loaded; Traefik requires dynamic config reload | Update `ServersTransport` TLS config with new cert reference; Traefik reloads dynamic config automatically; verify with `wget -qO- http://localhost:8080/api/http/serversTransports` |
| Retry storm amplification through Traefik retry middleware | Backend service briefly slow; Traefik `retry` middleware retries 3x; backend receives 4x normal load; cascading failure | `wget -qO- http://localhost:8080/api/http/middlewares \| jq '.[] \| select(.retry)'; kubectl logs -n traefik <pod> \| grep "retry\|attempt"` | Retry `attempts=3` with no `initialInterval` causes immediate retries; backend overwhelmed by amplified traffic | Set `retry.initialInterval=100ms` with exponential backoff; reduce `attempts` to 2; add circuit breaker before retry middleware in chain; implement `Retry-After` header support |
| gRPC max message size exceeded through Traefik | gRPC clients receive `ResourceExhausted: grpc: received message larger than max`; works when bypassing Traefik | `wget -qO- http://localhost:8080/api/http/routers \| jq '.[] \| select(.rule \| contains("grpc"))'` | Traefik `h2c` backend with default `maxRequestBodyBytes` smaller than gRPC message; buffering middleware interfering | Remove `buffering` middleware from gRPC routes; set Traefik entrypoint `transport.respondingTimeouts.readTimeout=0` for streaming; configure backend `serversTransport` with `maxIdleConnsPerHost=0` |
| Trace context propagation lost through Traefik | Distributed traces show gap at Traefik; downstream spans not correlated with upstream; Jaeger shows broken traces | `wget -qO- http://localhost:8080/api/overview \| jq .tracing`; `curl -H "traceparent: 00-..." http://traefik:80/ -v 2>&1 \| grep traceparent` | Traefik tracing not enabled or using different propagation format (Jaeger vs W3C); `--tracing.jaeger` not configured | Enable Traefik tracing: `--tracing.jaeger.samplingServerURL=http://jaeger:5778/sampling`; or use OpenTelemetry: `--tracing.otlp.http.endpoint=http://otel-collector:4318/v1/traces`; match propagation format with upstream |
| WebSocket upgrade blocked by middleware chain | WebSocket connections fail with `400 Bad Request` at Traefik; HTTP upgrade header stripped by middleware | `curl -v -H "Connection: Upgrade" -H "Upgrade: websocket" http://traefik:80/ws/; kubectl logs -n traefik <pod> \| grep -i "upgrade\|websocket"` | `headers` middleware strips `Connection` and `Upgrade` headers; or `compress` middleware interferes with WebSocket upgrade | Exclude WebSocket routes from `compress` and `headers` middleware; ensure IngressRoute for WS path does not include body-modifying middleware; verify `websocket` passthrough with `websocat ws://traefik:80/ws/` |
