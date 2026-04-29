---
name: ingress-nginx-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-ingress-nginx-agent
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
# Ingress-NGINX SRE Agent

## Role
Owns reliability and incident response for the ingress-nginx Kubernetes Ingress Controller (kubernetes/ingress-nginx). Responsible for HTTP/HTTPS traffic routing correctness, SSL termination health, upstream pod availability, ConfigMap-driven global configuration, IngressClass conflicts, rate limiting enforcement, controller pod stability, and multi-instance coordination.

## Architecture Overview

```
External Traffic (HTTPS/HTTP)
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│  Kubernetes Service: ingress-nginx-controller                │
│  (LoadBalancer or NodePort)                                  │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  ingress-nginx Controller Pod(s)                             │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ nginx process (main)                                 │    │
│  │ ├── worker_processes auto                            │    │
│  │ ├── upstream{} blocks (per Service)                  │    │
│  │ ├── server{} blocks (per host/path)                  │    │
│  │ └── SSL: terminates TLS, offloads to upstream HTTP   │    │
│  └──────────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ ingress-nginx controller (Go)                        │    │
│  │ ├── Watches Ingress, Service, Endpoint resources     │    │
│  │ └── Generates nginx.conf → reloads nginx on change   │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────┬───────────────────────────────────────┘
                       │ Kubernetes API (Watch)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  Ingress Resources (per namespace)                           │
│  ConfigMap: ingress-nginx/ingress-nginx-controller           │
│  IngressClass: nginx                                         │
│  Service / Endpoints (backends)                              │
│  TLS Secrets (kubernetes.io/tls type)                        │
└──────────────────────────────────────────────────────────────┘
```

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| `nginx_ingress_controller_requests` 5xx rate | > 1% of total requests | > 5% | Upstream errors or nginx config issues |
| `nginx_ingress_controller_requests` 502 count | > 10/min | > 100/min | Upstream pods down or crashing |
| `nginx_ingress_controller_requests` 504 count | > 5/min | > 50/min | Upstream response timeout |
| `nginx_ingress_controller_nginx_process_cpu_seconds_total` rate | > 70% of CPU limit | > 90% | Potential throttling; increase resources |
| Controller pod memory usage | > 75% of limit | > 90% of limit | OOM kill risk; review ConfigMap buffer sizes |
| `nginx_ingress_controller_ssl_expire_time_seconds` | < 30 days to expiry | < 7 days | TLS cert expiry; cert-manager renewal issue |
| `nginx_ingress_controller_config_hash` change rate | N/A | > 10 reloads/min | Config thrash; endpoint churn or annotation storm |
| `nginx_ingress_controller_config_last_reload_successful` == 0 | Any | > 3 consecutive | nginx config validation failure; broken Ingress resource |
| Upstream response time p99 | > 2s | > 10s | Application performance or timeout misconfiguration |
| Controller pod restart count | > 2/day | > 1/hour | OOM, crash, or readiness probe failure |

## Alert Runbooks

### Alert: `IngressNginx502RateHigh`
**Trigger:** 502 error rate > 5% over 5-minute window.

**Triage steps:**
1. Identify affected ingresses:
   ```bash
   kubectl logs -n ingress-nginx deployment/ingress-nginx-controller --tail=200 \
     | grep ' 502 ' | awk '{print $1}' | sort | uniq -c | sort -rn | head -10
   ```
2. Check backend pods for the affected service:
   ```bash
   kubectl get pods -n <APP_NAMESPACE> -l app=<APP_LABEL> -o wide
   kubectl describe endpoints -n <APP_NAMESPACE> <SERVICE_NAME>
   ```
3. Verify endpoints are populated:
   ```bash
   kubectl get endpoints -n <APP_NAMESPACE> <SERVICE_NAME> -o jsonpath='{.subsets}'
   ```
4. If no endpoints, check readiness probes and pod health.
5. Temporary mitigation — scale up pods:
   ```bash
   kubectl scale deployment -n <APP_NAMESPACE> <DEPLOYMENT_NAME> --replicas=<N>
   ```

---

### Alert: `IngressNginxControllerOOM`
**Trigger:** Controller pod OOMKilled or memory > 90% of limit.

**Triage steps:**
1. Confirm OOM event:
   ```bash
   kubectl describe pod -n ingress-nginx <CONTROLLER_POD> | grep -A5 "OOMKilled\|Last State"
   kubectl get events -n ingress-nginx --sort-by='.lastTimestamp' | tail -20
   ```
2. Check current memory usage:
   ```bash
   kubectl top pod -n ingress-nginx
   ```
3. Review large buffer-related ConfigMap settings:
   ```bash
   kubectl get configmap -n ingress-nginx ingress-nginx-controller -o yaml \
     | grep -E "proxy-buffer|large-client-header|worker"
   ```
4. Temporarily increase memory limit:
   ```bash
   kubectl patch deployment -n ingress-nginx ingress-nginx-controller \
     --type='json' \
     -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"1Gi"}]'
   ```
5. Review ingress count and annotation complexity as root cause.

---

### Alert: `IngressNginxConfigReloadFailing`
**Trigger:** `nginx_ingress_controller_config_last_reload_successful == 0`.

**Triage steps:**
1. Check controller logs for nginx config test errors:
   ```bash
   kubectl logs -n ingress-nginx deployment/ingress-nginx-controller --tail=100 \
     | grep -E "error|NGINX config test failed|reload"
   ```
2. Validate nginx config inside the pod:
   ```bash
   kubectl exec -n ingress-nginx deployment/ingress-nginx-controller -- nginx -t 2>&1
   ```
3. Identify the offending Ingress resource:
   ```bash
   kubectl get ingresses -A -o json | jq '.items[] | select(.metadata.annotations | to_entries[] | .value | test("[<>{}]")) | .metadata.name'
   ```
---

### Alert: `IngressNginxSSLCertExpiring`
**Trigger:** TLS certificate expiry < 7 days.

**Triage steps:**
1. Identify expiring certs:
   ```bash
   kubectl get secrets -A -o json | jq -r \
     '.items[] | select(.type=="kubernetes.io/tls") |
      "\(.metadata.namespace)/\(.metadata.name): \(.data["tls.crt"])"' \
     | while IFS=: read loc cert; do
       exp=$(echo "$cert" | base64 -d | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
       echo "$loc expires: $exp"
     done
   ```
2. Check cert-manager Certificate resource:
   ```bash
   kubectl get certificates -A
   kubectl describe certificate -n <NAMESPACE> <CERT_NAME>
   ```
## Common Issues & Troubleshooting

### Issue 1: Ingress Returns 404 for All Paths
**Symptom:** All requests to the host return `404 Not Found` with nginx 404 page.

**Diagnosis:**
```bash
# Check if ingress has correct ingressClassName
kubectl get ingress -n <NAMESPACE> <INGRESS_NAME> -o yaml | grep -E "ingressClassName|annotations"

# Verify controller is watching the correct IngressClass
kubectl get ingressclass nginx -o yaml

# Check if annotation is being used (older style)
kubectl get ingress -n <NAMESPACE> <INGRESS_NAME> \
  -o jsonpath='{.metadata.annotations.kubernetes\.io/ingress\.class}'

# Check controller logs for ingress admission
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller --tail=50 \
  | grep <INGRESS_NAME>
```

### Issue 2: 502 Bad Gateway for Specific Upstream
**Symptom:** Specific service returns 502; other services on the same controller are fine.

**Diagnosis:**
```bash
# Check if service has ready endpoints
kubectl get endpoints -n <APP_NS> <SERVICE_NAME>

# Check pod readiness
kubectl get pods -n <APP_NS> -l app=<APP> --field-selector=status.phase=Running

# Curl from inside nginx pod to backend directly
kubectl exec -n ingress-nginx <CONTROLLER_POD> -- \
  curl -v http://<SERVICE_NAME>.<APP_NS>.svc.cluster.local:<PORT>/health

# Check if service port matches containerPort
kubectl get service -n <APP_NS> <SERVICE_NAME> -o yaml | grep -A5 "ports:"
kubectl get deployment -n <APP_NS> <DEPLOYMENT> -o yaml | grep -A5 "containerPort"
```

### Issue 3: Large Headers Causing 400 Bad Request
**Symptom:** Requests with large cookies or Authorization headers return 400.

**Diagnosis:**
```bash
# Check current header buffer settings in ConfigMap
kubectl get configmap -n ingress-nginx ingress-nginx-controller -o yaml \
  | grep -E "proxy-buffer|large-client-header"

# Check nginx.conf inside pod
kubectl exec -n ingress-nginx <CONTROLLER_POD> -- \
  grep -E "large_client_header|proxy_buffer" /etc/nginx/nginx.conf
```

### Issue 4: Multiple IngressClass Conflict — Wrong Controller Handling Ingresses
**Symptom:** Some ingresses handled by wrong controller; traffic routed incorrectly.

**Diagnosis:**
```bash
# List all IngressClass resources
kubectl get ingressclass -o wide

# List all ingress resources and their classes
kubectl get ingress -A -o custom-columns=\
'NS:.metadata.namespace,NAME:.metadata.name,CLASS:.spec.ingressClassName,ANNOT:.metadata.annotations.kubernetes\.io/ingress\.class'

# Check which controllers exist
kubectl get pods -A | grep -E "ingress|nginx|traefik|haproxy"
```

### Issue 5: Rate Limiting Not Working or Too Aggressive
**Symptom:** Rate limit annotations set but requests not throttled, or users getting 429 unexpectedly.

**Diagnosis:**
```bash
# Check rate limit annotations on the ingress
kubectl get ingress -n <NAMESPACE> <INGRESS_NAME> -o yaml \
  | grep "nginx.ingress.kubernetes.io/limit"

# Check if limit-rps is applied in nginx.conf
kubectl exec -n ingress-nginx <CONTROLLER_POD> -- \
  grep -A5 "limit_req_zone" /etc/nginx/nginx.conf | head -20

# Check if rate limiting is bypassed by whitelist
kubectl get ingress -n <NAMESPACE> <INGRESS_NAME> \
  -o jsonpath='{.metadata.annotations.nginx\.ingress\.kubernetes\.io/limit-whitelist}'
```

### Issue 6: SSL Certificate Not Served / Redirect Loop
**Symptom:** HTTPS returns wrong certificate or HTTP to HTTPS redirect creates infinite loop.

**Diagnosis:**
```bash
# Check if TLS secret exists and is valid
kubectl get secret -n <NAMESPACE> <TLS_SECRET_NAME> -o yaml | grep "tls.crt"
kubectl get secret -n <NAMESPACE> <TLS_SECRET_NAME> \
  -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -text | grep -E "Subject:|DNS:"

# Check ingress TLS config
kubectl get ingress -n <NAMESPACE> <INGRESS_NAME> -o yaml | grep -A10 "tls:"

# Check for redirect loop annotation
kubectl get ingress -n <NAMESPACE> <INGRESS_NAME> \
  -o jsonpath='{.metadata.annotations.nginx\.ingress\.kubernetes\.io/ssl-redirect}'
```

## Key Dependencies

- **Kubernetes API Server** — controller watches Ingress, Service, Endpoint resources; API server unavailability causes config to go stale
- **CoreDNS** — upstream service resolution inside cluster; DNS failures cause controller to not resolve backend service names
- **cert-manager** — automates TLS certificate provisioning and renewal; cert-manager failures cause certificate expiry
- **Load Balancer (cloud provider)** — ingress-nginx controller Service of type LoadBalancer; cloud LB provisioning delays affect external traffic
- **Backend Pods (per service)** — readiness of upstream pods directly determines 502/504 rate
- **IngressClass resource** — must exist and match `--ingress-class` flag for controller to handle Ingress resources
- **ConfigMap (`ingress-nginx-controller`)** — global nginx configuration; bad values here affect all virtual hosts

## Cross-Service Failure Chains

- **Burst of pod restarts in app namespace** → Endpoints list empties and repopulates rapidly → nginx config reloads continuously → reload storm → temporary traffic disruption during each reload
- **cert-manager controller crashes** → TLS certificates not renewed → Certs expire → HTTPS traffic fails with SSL error for all affected hosts
- **ConfigMap annotation typo pushed via GitOps** → nginx config test fails → nginx refuses to reload → All new Ingress resource changes ignored → Routing config diverges from desired state
- **Node pressure evicts ingress-nginx pod** → NodePort traffic to evicted pod fails → LoadBalancer health checks fail node → Partial traffic loss during rescheduling
- **API server slow responses** → Controller Watch events delayed → Endpoint changes not reflected in nginx.conf → Stale upstreams return 502 for pods that have been replaced

## Partial Failure Patterns

- **Some hosts 404, others working:** IngressClass mismatch on subset of Ingresses; ingresses without `ingressClassName` not handled by this controller instance.
- **HTTPS works, HTTP doesn't (or vice versa):** SSL redirect annotation inconsistency or missing `80` port rule in Service. Check both protocol rules in nginx.conf.
- **Rate limiting works for some IPs but not others:** `limit-whitelist` includes a broad CIDR; or `use-forwarded-headers=true` is set but upstream doesn't send `X-Real-IP`.
- **Websocket connections dropping:** Missing `nginx.ingress.kubernetes.io/proxy-read-timeout` annotation; default 60s timeout killing long-lived WebSocket connections.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|----------|
| nginx config reload time | < 1s | 1-5s | > 5s (connections dropped during reload) |
| Upstream response time p99 | < 500ms | 500ms-2s | > 2s |
| Controller pod CPU usage | < 500m | 500m-800m | > 800m (throttling) |
| Controller pod memory | < 512Mi | 512Mi-750Mi | > 750Mi (OOM risk) |
| Concurrent active connections | < 5,000 | 5,000-20,000 | > 20,000 (worker saturation) |
| Config reload frequency | < 1/min | 1-10/min | > 10/min (endpoint churn) |
| SSL handshake time p95 | < 100ms | 100-500ms | > 500ms |
| DNS resolution time (upstream) | < 5ms | 5-50ms | > 50ms |

## Capacity Planning Indicators

| Indicator | Healthy | Watch | Action Required |
|-----------|---------|-------|-----------------|
| Ingress resource count (cluster-wide) | < 200 | 200-500 | > 500 — nginx.conf becomes large; memory pressure |
| Active unique upstream services | < 100 | 100-300 | > 300 — nginx upstream memory overhead significant |
| Controller replica count | 2 (HA) | 1 (no HA) | Scale to 3+ for high-traffic environments |
| nginx worker processes count | = CPU cores | < CPU cores | Set `worker-processes: auto` in ConfigMap |
| Requests per second (total) | < 10,000 | 10,000-50,000 | > 50,000 — scale controller replicas or optimize upstream |
| SSL certificates managed | < 100 | 100-500 | > 500 — cert-manager renewal load; review wildcard cert strategy |
| Config reload frequency | < 1/5min | 1-10/min | > 10/min — Endpoint churn; implement PodDisruptionBudgets |
| p99 upstream response time | < 500ms | 500ms-2s | > 2s — Add upstream caching or increase `proxy-read-timeout` |

## Diagnostic Cheatsheet

```bash
# Check all ingress-nginx pods and their status
kubectl get pods -n ingress-nginx -o wide

# Tail controller logs in real time
kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx -f --tail=100

# Test nginx config validity inside controller
kubectl exec -n ingress-nginx deployment/ingress-nginx-controller -- nginx -t

# Print current nginx.conf (large file)
kubectl exec -n ingress-nginx deployment/ingress-nginx-controller -- cat /etc/nginx/nginx.conf

# Find which ingress handles a specific host
kubectl get ingress -A -o json | jq -r \
  '.items[] | select(.spec.rules[].host == "<HOSTNAME>") | .metadata.namespace + "/" + .metadata.name'

# Check current config hash (changes on each reload)
kubectl get configmap -n ingress-nginx ingress-nginx-controller \
  -o jsonpath='{.metadata.annotations.ingress\.kubernetes\.io/configuration-hash}'

# List all annotations on a specific ingress
kubectl get ingress -n <NS> <NAME> -o jsonpath='{.metadata.annotations}' | python3 -m json.tool

# Test upstream connectivity from within controller pod
kubectl exec -n ingress-nginx deployment/ingress-nginx-controller -- \
  curl -sv http://<SERVICE>.<NS>.svc.cluster.local:<PORT>/

# Check current SSL certs loaded by nginx
kubectl exec -n ingress-nginx <CONTROLLER_POD> -- \
  ls -la /etc/ingress-controller/ssl/

# Verify TLS secret contents
kubectl get secret -n <NS> <TLS_SECRET> -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text | grep -E "Subject|Not After|DNS"
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|--------------------|-------------|
| HTTP 5xx error rate | < 0.1% of all requests | 43.2 minutes of all-traffic 5xx | `sum(rate(nginx_ingress_controller_requests{status=~"5.."}[5m])) / sum(rate(nginx_ingress_controller_requests[5m]))` |
| Request success rate (non-5xx) | 99.9% | 43.2 minutes of errors | Prometheus: nginx_ingress_controller_requests |
| p99 upstream latency | < 2s | > 2s for 1% of requests | `histogram_quantile(0.99, nginx_ingress_controller_request_duration_seconds_bucket)` |
| TLS certificate availability | 100% valid (no expired certs) | 0 expired certs in production | Prometheus: nginx_ingress_controller_ssl_expire_time_seconds |

## Configuration Audit Checklist

| Check | Command | Expected |
|-------|---------|----------|
| Controller pods are running | `kubectl get pods -n ingress-nginx` | All pods `Running`, restarts < 2 |
| IngressClass exists and is correct | `kubectl get ingressclass nginx` | Exists with `controller: k8s.io/ingress-nginx` |
| ConfigMap exists | `kubectl get configmap -n ingress-nginx ingress-nginx-controller` | Exists |
| SSL redirect enabled globally | `kubectl get configmap -n ingress-nginx ingress-nginx-controller -o yaml \| grep ssl-redirect` | `ssl-redirect: "true"` |
| HSTS header enabled | `kubectl get configmap -n ingress-nginx ingress-nginx-controller -o yaml \| grep hsts` | `hsts: "true"` |
| Controller RBAC exists | `kubectl get clusterrole ingress-nginx -o yaml \| grep rules \| wc -l` | Rules present for ingresses, services, endpoints |
| Admission webhook configured | `kubectl get validatingwebhookconfiguration ingress-nginx-admission` | Exists and `sideEffects: None` |
| Resource limits set | `kubectl get deployment -n ingress-nginx ingress-nginx-controller -o yaml \| grep -A4 resources` | `limits` and `requests` defined |
| Metrics service exists | `kubectl get svc -n ingress-nginx \| grep metrics` | Metrics service on port 10254 |
| Liveness/readiness probes configured | `kubectl get deployment -n ingress-nginx ingress-nginx-controller -o yaml \| grep -A5 livenessProbe` | Probes present |

## Log Pattern Library

| Pattern | Meaning | Action |
|---------|---------|--------|
| `[error] 1234#1234: *1 connect() failed (111: Connection refused)` | Backend pod refused connection | Check pod is running and listening on correct port |
| `[error] 1234#1234: *1 upstream timed out (110: Connection timed out)` | Backend exceeded proxy_read_timeout | Increase `proxy-read-timeout` annotation or fix slow backend |
| `[warning] 1234#1234: *1 upstream sent invalid header` | Backend sent malformed HTTP headers | Fix application response headers; check for binary responses on text endpoint |
| `Ignoring ingress <ns/name> because its ingress.class annotation is not "nginx"` | Ingress has wrong or missing class annotation | Set correct `ingressClassName` or annotation |
| `Error getting SSL certificate` | TLS secret missing or invalid | Check secret exists and has valid `tls.crt` / `tls.key` data |
| `NGINX reload failed: nginx: [emerg]` | Config test failed; nginx not reloaded | `nginx -t` inside pod to see full error |
| `Dynamic reconfiguration succeeded` | Endpoints updated without nginx reload | Normal; indicates Lua-based dynamic upstream update |
| `unexpected error: config error: admission webhook not ready` | Admission webhook unreachable | Check webhook pod; `kubectl get pods -n ingress-nginx` |
| `ssl_stapling` ignored, host not found | OCSP stapling resolver unreachable | Disable `ssl-stapling` or ensure DNS resolver is set in ConfigMap |
| `no endpoints for Ingress <ns/name>` | Service has no ready pods | Scale up deployment or fix readiness probe |
| `I1009 proxy: Skipping ssl validation for certificate in secret` | Self-signed cert in use | Expected for dev; replace with CA-signed cert in production |
| `413 Request Entity Too Large` | `client-max-body-size` limit exceeded | Increase via `nginx.ingress.kubernetes.io/proxy-body-size` annotation |

## Error Code Quick Reference

| Status / Error | Root Cause | Quick Fix |
|----------------|-----------|-----------|
| `502 Bad Gateway` | Backend pod down/unready or port mismatch | Check endpoints: `kubectl get endpoints -n <NS> <SVC>` |
| `503 Service Temporarily Unavailable` | No healthy upstream endpoints | All backend pods failing readiness; scale or fix |
| `504 Gateway Timeout` | Backend too slow; timeout exceeded | Increase `proxy-read-timeout` annotation; fix backend |
| `404 Not Found` (nginx default) | No matching Ingress rule or wrong IngressClass | Check `ingressClassName` and path rules |
| `413 Request Entity Too Large` | Body exceeds `client_max_body_size` | Add `nginx.ingress.kubernetes.io/proxy-body-size: "50m"` annotation |
| `400 Bad Request` (headers) | Header buffers too small | Increase `large-client-header-buffers` in ConfigMap |
| `429 Too Many Requests` | Rate limit hit | Expected behavior; review limit thresholds if incorrect |
| `SSL_ERROR_BAD_CERT_DOMAIN` | Certificate CN doesn't match hostname | Check cert SANs; regenerate with correct hostname |
| `ERR_TOO_MANY_REDIRECTS` | HTTP→HTTPS redirect loop | Disable `ssl-redirect` or add `X-Forwarded-Proto` header |
| `nginx: [emerg] no "ssl_certificate" is defined` | TLS secret referenced in Ingress doesn't exist | Create TLS secret or remove `tls:` block from Ingress |
| `upstream sent invalid status line` | Non-HTTP response on HTTP upstream | Check if backend uses HTTPS; set `backend-protocol: HTTPS` annotation |
| `connect() failed (113: No route to host)` | NetworkPolicy blocking controller→pod traffic | Add NetworkPolicy allowing ingress from ingress-nginx namespace |

## Known Failure Signatures

| Signature | Likely Cause | Diagnostic Step |
|-----------|-------------|-----------------|
| All ingresses suddenly return 404 | Controller restarted and lost IngressClass registration | `kubectl get ingressclass`; `kubectl get pods -n ingress-nginx` |
| Specific host intermittently 502 | One backend pod failing readiness; load balanced across healthy and unhealthy | `kubectl get endpoints -n <NS> <SVC>` — check number of IPs |
| SSL cert shows old expiry date after renewal | nginx still serving old cert from cache | `kubectl rollout restart deployment -n ingress-nginx ingress-nginx-controller` |
| nginx reloading every few seconds | Endpoint churn from HPA scaling or pod restarts | `kubectl get events -n <NS> \| grep Endpoints`; check PodDisruptionBudget |
| Large requests fail with 413 | `client-max-body-size` not set or too low | `kubectl annotate ingress <NAME> -n <NS> nginx.ingress.kubernetes.io/proxy-body-size=100m` |
| WebSocket connections drop after 60 seconds | Default `proxy-read-timeout` of 60s hit | Add `proxy-read-timeout: "3600"` and `proxy-send-timeout: "3600"` annotations |
| Controller pod uses excessive memory | Too many Ingresses with complex annotations; large proxy buffer settings | Count ingresses; reduce `proxy-buffer-size` in ConfigMap |
| Annotation changes not taking effect | Admission webhook blocking update | `kubectl describe ingress -n <NS> <NAME>` for admission rejection reason |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| HTTP 502 Bad Gateway | Browser / HTTP client | Upstream pod is down, unhealthy, or no endpoints exist for the Service | `kubectl get endpoints -n <NS> <SERVICE>`; check if `ENDPOINTS` is `<none>` | Fix upstream pod crash; verify Service selector matches pod labels |
| HTTP 504 Gateway Timeout | Browser / HTTP client | Upstream pod is alive but not responding within `proxy-read-timeout` | `kubectl logs -n ingress-nginx <CONTROLLER_POD>` for `upstream timed out` lines | Increase `proxy-read-timeout` annotation; fix slow application query |
| HTTP 503 Service Unavailable | Browser / HTTP client | All upstream pods failing health checks or NGINX upstream has zero active peers | `kubectl get pods -n <NS>` for upstream pods; check `nginx_ingress_controller_requests{status="503"}` metric | Scale up upstream deployment; fix health check endpoint |
| HTTP 413 Request Entity Too Large | Browser / HTTP client / API client | `client-max-body-size` limit exceeded in NGINX config | Check Ingress annotation `nginx.ingress.kubernetes.io/proxy-body-size` | Set annotation to required size (e.g., `100m`); update globally in ConfigMap |
| HTTP 414 URI Too Long | HTTP client | NGINX `large_client_header_buffers` too small for the request URI | Review ConfigMap for `large-client-header-buffers` value | Increase `large-client-header-buffers` in ConfigMap |
| HTTP 400 Bad Request on HTTPS | Browser | TLS SNI mismatch or expired certificate for the host | `echo \| openssl s_client -connect <HOST>:443 -servername <HOST> 2>/dev/null \| openssl x509 -noout -dates` | Renew or replace the TLS secret; verify secret name in Ingress spec |
| SSL_ERROR_RX_RECORD_TOO_LONG in browser | Browser TLS stack | Ingress routing HTTP traffic to HTTPS-only backend, or vice versa | Check Ingress `tls` block and `ssl-redirect` annotation | Ensure `ssl-passthrough` or `ssl-redirect` annotation is set correctly |
| HTTP 429 Too Many Requests | HTTP client | NGINX rate limiting via `limit-rps` or `limit-connections` annotation triggered | `kubectl describe ingress -n <NS> <NAME>` for rate-limit annotations | Increase rate limit threshold; add IP allowlist for trusted clients |
| WebSocket connection closes after 60s | WebSocket client library | Default `proxy-read-timeout: 60s` cuts idle WebSocket connections | `kubectl logs -n ingress-nginx <POD>` for `upstream prematurely closed connection` | Add `proxy-read-timeout: "3600"` and `proxy-send-timeout: "3600"` Ingress annotations |
| `ERR_CERT_AUTHORITY_INVALID` in browser | Browser TLS | Self-signed or unknown CA certificate in TLS Secret | `kubectl get secret -n <NS> <TLS_SECRET> -o json \| jq '.data."tls.crt"' \| base64 -d \| openssl x509 -noout -issuer` | Replace with a cert-manager-issued or CA-signed certificate |
| Redirect loop (too many redirects) | Browser | `ssl-redirect: "true"` combined with upstream also redirecting HTTP → HTTPS | `curl -v http://<HOST>/path` to trace redirect chain | Set `nginx.ingress.kubernetes.io/ssl-redirect: "false"` if upstream handles HTTPS |
| 404 Not Found for valid routes | HTTP client | IngressClass annotation mismatch; two controllers both watching without correct class | `kubectl get ingress -n <NS> <NAME> -o yaml \| grep ingressClassName` | Set `ingressClassName: nginx` explicitly in Ingress spec; verify only one controller owns the class |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| NGINX reload frequency increasing | Reloads every few seconds instead of minutes; p99 latency spikes briefly during each reload | `kubectl logs -n ingress-nginx <CONTROLLER_POD> \| grep -c "Reloading nginx"` per hour | Hours to days | Identify what is causing constant endpoint churn (HPA, rolling deploys); tune `--sync-period` |
| Worker process connection saturation | `worker_connections` approaching limit; connection refused errors under load | `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep worker_connections`; check `nginx_ingress_controller_nginx_process_connections` metric | Hours | Increase `worker-processes` and `max-worker-connections` in ConfigMap |
| Active connection count creeping up | Idle keep-alive connections accumulating; memory growing on controller pod | `kubectl exec -n ingress-nginx <POD> -- curl -s localhost:18080/nginx_status` for `Active connections` | Days | Tune `keep-alive: "75"` and `keep-alive-requests: "1000"` in ConfigMap; set upstream `keepalive` |
| Upstream error rate slowly rising | 502/504 rate increasing 1-2% per day; upstream pods healthy | `kubectl top pods -n <UPSTREAM_NS>` for CPU/memory; `kubectl logs -n ingress-nginx <POD> \| grep "upstream timed out"` | Days | Investigate upstream pod resource limits; scale replicas; check DB connection pool saturation |
| TLS certificate expiry approaching | No immediate errors; will cause hard failure at expiry | `kubectl get secrets -A --field-selector type=kubernetes.io/tls -o json \| jq '.items[] \| {name:.metadata.name, ns:.metadata.namespace}' \| xargs -I{} kubectl get secret {} -o jsonpath='{.data.tls\.crt}'` + openssl check | Weeks | Ensure cert-manager auto-renewal is configured; set up expiry alert 30 days prior |
| ConfigMap annotation conflicts accumulating | Unexpected NGINX behavior on some hosts but not others; hard-to-reproduce bugs | `kubectl get ingress -A -o json \| jq '.items[] \| select(.metadata.annotations \| to_entries \| map(select(.key \| startswith("nginx.ingress"))) \| length > 10)'` | Weeks | Audit per-Ingress annotations; consolidate global settings to ConfigMap |
| Controller pod memory growing | Memory usage climbing week-over-week; OOMKilled events appear eventually | `kubectl top pod -n ingress-nginx` weekly trend; Prometheus `container_memory_usage_bytes` | Weeks | Review proxy-buffer-size settings; count Ingress resources; upgrade controller version |
| Ingress resource count growing without cleanup | NGINX config growing; reload time increasing; plan/reload errors for large configs | `kubectl get ingress -A --no-headers \| wc -l` | Months | Implement Ingress lifecycle automation; delete stale Ingresses on service decommission |
| SSL session cache filling | TLS handshake latency slowly increasing; cache-miss rate rising | `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep ssl_session_cache` | Weeks | Increase `ssl-session-cache-size` in ConfigMap; tune `ssl-session-timeout` |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# ingress-nginx-health-snapshot.sh
# Prints full health summary of the ingress-nginx controller

set -euo pipefail
NS="${1:-ingress-nginx}"

echo "=== ingress-nginx Controller Health ==="
echo ""

echo "--- Controller Pods ---"
kubectl get pods -n "$NS" -l "app.kubernetes.io/name=ingress-nginx" -o wide

echo ""
echo "--- Controller Service (LoadBalancer IP) ---"
kubectl get svc -n "$NS" -l "app.kubernetes.io/name=ingress-nginx" -o wide

echo ""
echo "--- NGINX Status ---"
CONTROLLER_POD=$(kubectl get pods -n "$NS" -l "app.kubernetes.io/component=controller" \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [[ -n "$CONTROLLER_POD" ]]; then
  kubectl exec -n "$NS" "$CONTROLLER_POD" -- curl -s localhost:18080/nginx_status 2>/dev/null || echo "  nginx_status not available"
fi

echo ""
echo "--- All Ingress Resources (cluster-wide) ---"
kubectl get ingress -A -o custom-columns="NS:.metadata.namespace,NAME:.metadata.name,CLASS:.spec.ingressClassName,HOSTS:.spec.rules[*].host" --no-headers | head -30

echo ""
echo "--- TLS Secrets Expiry Check ---"
kubectl get secrets -A --field-selector type=kubernetes.io/tls -o json 2>/dev/null \
  | jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name)"' \
  | while read secret_path; do
      NS_S=$(echo "$secret_path" | cut -d/ -f1)
      NAME_S=$(echo "$secret_path" | cut -d/ -f2)
      CERT=$(kubectl get secret -n "$NS_S" "$NAME_S" -o jsonpath='{.data.tls\.crt}' 2>/dev/null | base64 -d 2>/dev/null)
      if [[ -n "$CERT" ]]; then
        EXPIRY=$(echo "$CERT" | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
        echo "  $secret_path — expires: $EXPIRY"
      fi
    done

echo ""
echo "--- Recent Error Events ---"
kubectl get events -n "$NS" --sort-by='.lastTimestamp' 2>/dev/null | grep -iE "error|fail|warning" | tail -10
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# ingress-nginx-perf-triage.sh
# Checks request rates, error rates, and connection counts

set -euo pipefail
NS="${1:-ingress-nginx}"

CONTROLLER_POD=$(kubectl get pods -n "$NS" -l "app.kubernetes.io/component=controller" \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

echo "=== ingress-nginx Performance Triage ==="
echo "  Controller pod: $CONTROLLER_POD"
echo ""

echo "--- NGINX Active Connections ---"
kubectl exec -n "$NS" "$CONTROLLER_POD" -- \
  curl -s localhost:18080/nginx_status 2>/dev/null || echo "  nginx_status unavailable"

echo ""
echo "--- Controller Pod Resource Usage ---"
kubectl top pod -n "$NS" 2>/dev/null || echo "  metrics-server not available"

echo ""
echo "--- Reload Count (last 100 log lines) ---"
kubectl logs -n "$NS" "$CONTROLLER_POD" --tail=100 2>/dev/null \
  | grep -c "Reloading nginx" || echo "  0 reloads in last 100 lines"

echo ""
echo "--- Recent 5xx / timeout errors ---"
kubectl logs -n "$NS" "$CONTROLLER_POD" --tail=200 2>/dev/null \
  | grep -E '"status":5[0-9][0-9]|upstream timed out|connect\(\) failed' \
  | tail -20

echo ""
echo "--- NGINX Worker Config ---"
kubectl exec -n "$NS" "$CONTROLLER_POD" -- \
  nginx -T 2>/dev/null | grep -E "worker_processes|worker_connections|keepalive_timeout" || true
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# ingress-nginx-resource-audit.sh
# Audits Ingress resources, TLS secrets, and backend endpoint health

set -euo pipefail
NS="${1:-ingress-nginx}"

echo "=== ingress-nginx Resource Audit ==="
echo ""

echo "--- Total Ingress Count ---"
kubectl get ingress -A --no-headers | wc -l

echo ""
echo "--- Ingresses without IngressClass (may be orphaned) ---"
kubectl get ingress -A -o json 2>/dev/null \
  | jq -r '.items[] | select(.spec.ingressClassName == null and (.metadata.annotations["kubernetes.io/ingress.class"] // "") == "") | "\(.metadata.namespace)/\(.metadata.name)"'

echo ""
echo "--- Backend Services with No Endpoints ---"
kubectl get ingress -A -o json 2>/dev/null | jq -r '
  .items[] | .metadata.namespace as $ns |
  .spec.rules[]?.http.paths[]?.backend.service |
  "\($ns)/\(.name)"' | sort -u \
  | while read ns_svc; do
      NS_B=$(echo "$ns_svc" | cut -d/ -f1)
      SVC=$(echo "$ns_svc" | cut -d/ -f2)
      EP_COUNT=$(kubectl get endpoints "$SVC" -n "$NS_B" \
        -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null | wc -w)
      if [[ "$EP_COUNT" -eq 0 ]]; then
        echo "  [NO ENDPOINTS] $ns_svc"
      fi
    done

echo ""
echo "--- ConfigMap Global Config ---"
kubectl get configmap -n "$NS" ingress-nginx-controller -o yaml 2>/dev/null \
  | grep -A 100 '^data:' | head -40

echo ""
echo "--- IngressClass Resources ---"
kubectl get ingressclass -o wide 2>/dev/null
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| CPU saturation on controller pod from one high-traffic Ingress | p99 latency rises for all Ingresses; CPU throttling on controller pod | `kubectl top pod -n ingress-nginx`; check which upstream gets the most traffic via `nginx_ingress_controller_requests` Prometheus metric by `ingress` label | Increase controller pod CPU limit; scale to multiple controller replicas with `--ingress-class` sharding | Deploy separate controller instances per traffic tier (public vs. internal); set CPU requests/limits |
| Worker connection exhaustion from long-lived WebSocket connections | New short-lived HTTP requests get connection refused during WebSocket-heavy periods | `curl -s localhost:18080/nginx_status` — high `Active connections` near `worker_connections` limit | Increase `max-worker-connections` in ConfigMap; set `worker-processes: auto` | Tune `worker_connections` based on expected concurrent connections; monitor `nginx_process_connections` |
| NGINX reload storm from one namespace with frequent endpoint churn | All Ingresses experience brief latency spikes every few seconds | `kubectl logs -n ingress-nginx <POD> \| grep "Reloading" \| tail -50` — timestamps showing rapid reloads | Identify the namespace causing churn: `kubectl get endpoints -A --watch`; add PodDisruptionBudget | Use `--min-ready-seconds` on upstream Deployments; configure graceful termination |
| TLS handshake CPU spike from one certificate-intensive client | TLS negotiation latency increases for all hosts during spike | Check `nginx_ingress_controller_ssl_expire_time_seconds` metric; server CPU during spike | Enable `ssl-session-cache` and increase cache size in ConfigMap | Enable OCSP stapling; tune `ssl_session_timeout`; offload TLS to a dedicated edge tier if possible |
| Rate-limit annotation on one Ingress consuming shared Lua memory | Other Ingresses exhibit memory pressure on controller pod | `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep limit_req_zone` | Increase controller pod memory limit; audit all `limit-rps` annotations | Set global rate-limit defaults in ConfigMap; avoid very large `limit-req-zone` sizes per Ingress |
| Proxy buffer size mismatch causing memory bloat | Controller memory growing with large upstream responses; `upstream sent invalid header` errors | `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep proxy_buffer` | Reduce `proxy-buffer-size` and `proxy-buffers-number` in ConfigMap | Benchmark upstream response sizes; set proxy buffer values appropriate to actual payload sizes |
| Multiple namespaces sharing one controller with conflicting ConfigMap annotations | Annotation set for one namespace affects routing in another | `kubectl get ingress -A -o json \| jq '.items[] \| .metadata.annotations'` for conflicts | Isolate conflicting Ingresses to a dedicated controller instance | Use separate ingress controllers per team/namespace; configure `--watch-namespace` to limit scope |
| Upstream slow consumer causing NGINX upstream queue buildup | 502/504 errors on one service; NGINX access log shows increasing `request_time`; other services unaffected | `kubectl logs -n ingress-nginx <POD> \| grep <UPSTREAM_HOST>`; `kubectl top pods -n <UPSTREAM_NS>` | Set `proxy-next-upstream` and short `proxy-connect-timeout` to fail fast | Add readiness probes to upstream pods; configure `upstream-keepalive-connections` per service |
| Ingress annotation length exceeding etcd value size limit | Ingress updates silently fail or return validation error | `kubectl describe ingress -n <NS> <NAME>` for annotation size; `etcdctl get` to check stored size | Consolidate annotations into ConfigMap global settings where possible | Limit per-Ingress annotations to the minimum needed; use Ingress-class-level ConfigMap for global tuning |
| Shared TLS wildcard certificate expiry causing cluster-wide impact | All hosts under `*.example.com` fail simultaneously at cert expiry | `kubectl get secrets -A --field-selector type=kubernetes.io/tls -o json \| jq` + openssl dates | Renew wildcard cert immediately; distribute to all consuming namespaces | Use per-service cert-manager certificates instead of shared wildcard; set 30-day expiry alert |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| ingress-nginx controller pod crash (OOMKilled) | Controller exits → NGINX worker processes stop → all ingress traffic drops immediately → all services behind ingress unreachable | All services exposed via ingress-nginx; direct NodePort/LoadBalancer services unaffected | `kubectl get pods -n ingress-nginx` shows `OOMKilled`; LB health checks fail; 502/503 at LB level; `nginx_ingress_controller_nginx_process_connections` drops to 0 | Increase controller memory limit; `kubectl rollout restart deployment/ingress-nginx-controller -n ingress-nginx` |
| Upstream service all pods unhealthy (no ready endpoints) | NGINX upstream has no healthy backends → every request returns `502 Bad Gateway` → clients retry → amplifies load on adjacent services | All traffic to that specific service returns 502; other services behind same controller unaffected | `nginx_ingress_controller_requests` with `status=502` for specific `ingress` label; `kubectl get endpoints -n <NS> <SVC>` shows empty `ADDRESSES` | Scale up backend deployment; fix readiness probe; `kubectl rollout restart deployment/<SVC> -n <NS>` |
| TLS certificate expiry | HTTPS connections refused with `SSL_ERROR_RX_RECORD_TOO_LONG` or browser cert warning → clients cannot reach service → downstream APIs calling this endpoint fail | All traffic to HTTPS host on expired cert blocked; HTTP traffic unaffected | `kubectl get secret -n <NS> <TLS_SECRET> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -dates` shows expiry in past; `nginx_ingress_controller_ssl_expire_time_seconds` alert | Renew cert via cert-manager: `kubectl delete certificate -n <NS> <CERT>`; or manually: `kubectl create secret tls <SECRET> --cert=cert.crt --key=cert.key -n <NS>` |
| NGINX config reload failure (bad Ingress annotation) | New Ingress with invalid annotation triggers `nginx -t` failure → controller refuses to reload → NGINX continues serving old config → new service unreachable but existing services work | Only the newly deployed service fails to get routing; existing routes unaffected | Controller log: `Error reloading NGINX: nginx: configuration file /etc/nginx/nginx.conf test failed`; `nginx_ingress_controller_config_last_reload_successful` gauge = 0 | Delete or fix offending Ingress: `kubectl delete ingress -n <NS> <BAD_INGRESS>`; controller auto-reloads |
| etcd slow or unavailable (Kubernetes API server degraded) | Ingress controller cannot watch Ingress/Service/Endpoint resources → config updates stall → NGINX keeps serving last-known config → new services not routed | New Ingress deployments invisible to NGINX; existing routes continue to work (stale but functional) | Controller log: `Failed to list *v1.Ingress: etcd cluster is unavailable`; `kubectl get ingress -A` hangs | etcd issue is upstream; ingress continues serving cached routes; escalate to cluster team to restore etcd |
| cert-manager ACME challenge failing cluster-wide | cert-manager cannot renew certs → certs expire → ingress-nginx serves expired cert → HTTPS connections rejected → all secured services unreachable | All HTTPS services whose certs are managed by cert-manager via Let's Encrypt | `kubectl describe certificaterequest -n <NS>` shows `failed: ACME challenge`; `kubectl get challenges -A` shows `pending` | Fall back to manual cert: `kubectl create secret tls <SECRET> --cert=cert.pem --key=key.pem -n <NS>`; investigate ACME challenge solver |
| Kubernetes node running controller pod evicted | Controller pod rescheduled → brief downtime during pod startup → NGINX workers restart → active long-lived connections dropped | Brief complete ingress outage; duration = pod startup time (~30s) | `kubectl describe pod -n ingress-nginx <POD>` shows `Evicted`; `kubectl get events -n ingress-nginx` shows eviction reason | Prevent single-replica controller: set `replicaCount: 2` with `podAntiAffinity`; ensure LB health check grace period > pod startup time |
| Backend Service DNS resolution failure inside NGINX | NGINX cannot resolve upstream service DNS → `NXDOMAIN` for upstream → all requests to that service return 502 | Single service returns 502; other services unaffected | NGINX error log: `no resolver defined to resolve ... while connecting to upstream`; `kubectl exec -n ingress-nginx <POD> -- nslookup <SVC>.<NS>.svc.cluster.local` fails | Verify CoreDNS is running: `kubectl get pods -n kube-system -l k8s-app=kube-dns`; configure `use-upstream-cluster-ip` annotation |
| Rate limiting misconfiguration causing legitimate traffic rejection | overly aggressive `nginx.ingress.kubernetes.io/limit-rps` drops normal user traffic → application returns 429 to all clients → client retries amplify load | All users on affected Ingress hit rate limit; other Ingresses unaffected | `nginx_ingress_controller_requests` with `status=429` on specific ingress label spikes; user reports mass 429 errors | Remove or relax rate limit: `kubectl annotate ingress -n <NS> <ING> nginx.ingress.kubernetes.io/limit-rps-`; redeploy |
| NGINX worker process killed by kernel (SIGKILL) | Active connections abruptly terminated → clients receive TCP RST → in-flight requests fail → application clients may not retry correctly | Fraction of in-flight connections lost; NGINX master restarts worker | `kubectl logs -n ingress-nginx <POD>` shows `worker process <PID> exited on signal 9`; `nginx_ingress_controller_nginx_process_connections` brief dip | Increase worker resource limits; check for kernel-level crash: `dmesg | grep -i "nginx worker"`; enable worker crash alerting |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| ingress-nginx Helm chart upgrade (e.g., 4.8 → 4.10) | Default ConfigMap values changed in new chart version; `proxy-body-size` or `use-regex` semantics changed; traffic routing breaks | On first NGINX reload after upgrade | `helm diff upgrade` output shows ConfigMap changes; `kubectl diff -f` on rendered chart; correlate with traffic anomaly timestamp | `helm rollback ingress-nginx -n ingress-nginx <PREV_REVISION>`; verify NGINX config is restored |
| Adding `ssl-passthrough` annotation to an Ingress | Controller restarts NGINX in passthrough mode for that hostname; other non-passthrough Ingresses on same IP may be affected | Immediately after annotation applied and NGINX reload | Controller log: `reloading nginx configuration`; `ssl-passthrough` mode changes listener behavior; test other HTTPS routes after change | Remove annotation: `kubectl annotate ingress -n <NS> <ING> nginx.ingress.kubernetes.io/ssl-passthrough-` |
| Changing `proxy-read-timeout` ConfigMap value to too low | Long-running API calls (file uploads, streaming, GraphQL subscriptions) start timing out with `504 Gateway Timeout` | Within first slow request after reload | `nginx_ingress_controller_requests` with `status=504` rises; client reports timeouts for previously-working requests; correlate with ConfigMap change | Increase `proxy-read-timeout` in ConfigMap: `kubectl patch configmap ingress-nginx-controller -n ingress-nginx --patch '{"data":{"proxy-read-timeout":"600"}}'` |
| IngressClass `default` flag changed (new default IngressClass added) | Existing Ingresses without explicit `ingressClassName` are claimed by new default controller; duplicate routing | `kubectl get ingress -A -o jsonpath='{..ingressClassName}'` shows blank on old Ingresses; new controller starts serving them | Ingresses with no class now handled by wrong controller; traffic may double-route or fail | Set `ingressClassName` explicitly on all Ingresses; remove `default` flag from unintended IngressClass |
| NGINX version upgrade changing default TLS cipher suite | Clients using deprecated TLS ciphers (e.g., `TLS_RSA_WITH_AES_128_CBC_SHA`) fail handshake; `curl: (35) OpenSSL SSL_connect error` | After NGINX restart with upgraded version | NGINX access log shows `SSL handshake failed` for specific client user-agents; correlate with upgrade timestamp | Add explicit `ssl-ciphers` to ConfigMap to include legacy ciphers; or update client TLS configuration |
| `worker-processes` set to a fixed value higher than CPU cores | Worker processes compete for CPU; context switching overhead; latency regression | During moderate-to-high load after config change | `kubectl top pod -n ingress-nginx` shows high CPU; `nginx_ingress_controller_nginx_process_connections` per worker unhealthy | Set `worker-processes: auto` in ConfigMap; reload controller |
| Service `targetPort` changed in backend Service without updating Ingress | NGINX upstream points to old port; all connections to that service fail with connection refused | Immediately after Service update | `kubectl describe ingress -n <NS> <ING>` shows correct service name; `kubectl describe service -n <NS> <SVC>` shows new targetPort; controller logs show upstream connect failure | Update Ingress backend `servicePort` to match new `targetPort`; or revert Service `targetPort` |
| NetworkPolicy added to `ingress-nginx` namespace blocking egress to backend services | NGINX cannot connect to upstream service pods; `upstream timed out (110: Connection timed out)` in NGINX error log | Within seconds of NetworkPolicy apply | `kubectl exec -n ingress-nginx <POD> -- nc -zv <SVC_IP> <PORT>` returns connection refused; correlate with NetworkPolicy creation time | Add egress rule to NetworkPolicy allowing ingress-nginx → backend namespace; or delete blocking NetworkPolicy |
| Horizontal Pod Autoscaler scaling down controller during traffic spike | Active connections dropped when excess controller pods terminated → brief 502 surge → user-visible errors | During HPA scale-down event under load | `kubectl get events -n ingress-nginx | grep "ScalingReplicaSet"`; `nginx_ingress_controller_nginx_process_connections` drops at same time as HPA scale-down | Increase HPA `scaleDown.stabilizationWindowSeconds`; set `minReplicas: 2`; configure connection draining |
| Changing `use-forwarded-headers: "true"` in ConfigMap | Applications behind proxy start receiving `X-Forwarded-For` headers; rate limiting by IP may break if backend trusts wrong IP | Immediately on NGINX reload | Application logs show different client IP format; rate limiting behavior changes; correlate with ConfigMap change | Revert `use-forwarded-headers` to previous value; audit all applications' IP-based logic before re-enabling |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Multiple controller replicas have divergent NGINX configs | `kubectl exec -n ingress-nginx <POD1> -- cat /etc/nginx/nginx.conf | md5sum` vs `<POD2>` differ | Different replicas route requests differently; intermittent 502/404 depending on which pod LB chooses | Non-deterministic routing; some requests succeed, some fail; hard to diagnose | Force sync: `kubectl rollout restart deployment/ingress-nginx-controller -n ingress-nginx`; all replicas reload from same config |
| Ingress annotation applied to one controller but not another (dual-controller setup) | `kubectl get ingress -A -o json | jq '.items[] | select(.spec.ingressClassName != "<EXPECTED_CLASS>")'` shows mismatched class | Service routed through wrong controller; wrong TLS cert or wrong rate limit policy applied | Security or rate limit bypass; or service totally unreachable via expected controller | Add explicit `ingressClassName` to all Ingresses; use `kubectl label` to migrate misclassified Ingresses |
| NGINX upstream list stale after Service deletion | Controller received Service delete event but NGINX not yet reloaded; requests still routed to deleted service's ghost upstream | `kubectl get service -n <NS> <SVC>` returns `not found`; but requests still reaching old pods briefly | Brief window where traffic goes to terminating pods; TCP resets for those connections | Confirm `nginx_ingress_controller_config_last_reload_successful` = 1 after Service deletion; reload if stuck |
| Duplicate Ingress host definitions in different namespaces | Two Ingresses in different namespaces claim same hostname; NGINX uses last one applied; one service silently unreachable | `kubectl get ingress -A | grep <HOSTNAME>` returns two rows | One service's traffic hijacked by another's Ingress; intermittent routing depending on reload order | Assign unique hostnames or delete duplicate; communicate with team owning conflicting Ingress |
| ConfigMap patch applied to wrong namespace controller | `kubectl patch configmap ingress-nginx-controller -n wrong-ns` → correct-ns controller still uses old config | `kubectl get configmap ingress-nginx-controller -n ingress-nginx` shows expected config; but controller behavior unchanged | Config drift between desired state and running controller | Verify controller namespace: `kubectl logs -n ingress-nginx <POD> | grep "configmap"`; patch correct namespace; trigger reload |
| TLS secret updated but NGINX still serving old cert | cert-manager renewed cert and updated Secret; but NGINX uses in-memory cert until reload | `kubectl get secret -n <NS> <TLS_SECRET> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -enddate` shows new date; but `curl -v https://<HOST>` shows old cert | Old cert continues to be served; client sees expiry warning until reload happens | Trigger NGINX reload: `kubectl exec -n ingress-nginx <POD> -- nginx -s reload`; or restart controller pod |
| Wildcard cert covering multiple hosts but only applied to one Ingress | Hosts not referencing the wildcard cert Secret use default cert (may be self-signed or expired) | `kubectl get ingress -A -o jsonpath='{range .items[*]}{.spec.tls}{"\n"}{end}'` shows inconsistent TLS secrets | Some hosts show browser cert warnings; monitoring shows mixed valid/invalid cert states | Apply wildcard secret to all relevant Ingresses; or use cert-manager to auto-provision per-Ingress certs |
| NGINX `server_tokens` setting showing version in error pages | Not a routing issue but an info-disclosure: `curl -v http://<INGRESS_IP>/nonexistent` returns `nginx/1.25.x` in response header | `curl -I http://<INGRESS_IP>/404-path | grep Server` returns nginx version | Security audit finding; attackers can target version-specific CVEs | Set `server-tokens: "false"` in ConfigMap: `kubectl patch configmap ingress-nginx-controller -n ingress-nginx --patch '{"data":{"server-tokens":"false"}}'` |
| Ingress backend service ClusterIP changed after recreation | NGINX compiled the old ClusterIP into its upstream; after service recreation NGINX routes to dead IP | `kubectl get svc -n <NS> <SVC> -o jsonpath='{.spec.clusterIP}'` returns new IP; curl to old IP fails | All traffic to that service returns connection refused until NGINX reload | Delete and re-create Ingress to force reload; or trigger controller restart to pick up new ClusterIP |
| cert-manager RBAC change removes permission to update Ingress TLS secrets | cert-manager cannot update Secret; cert rotation silently fails; cert expires | `kubectl describe certificaterequest -n <NS>` shows `Forbidden: cannot update secrets`; cert expiry within days | Cert expiry imminent; HTTPS traffic will break when cert expires | Restore RBAC: `kubectl apply -f cert-manager-clusterrole.yaml`; manually trigger cert renewal: `kubectl delete secret <TLS_SECRET> -n <NS>` |

## Runbook Decision Trees

### Tree 1: Ingress returns 502 Bad Gateway

```
Is nginx_ingress_controller_config_last_reload_successful = 0?
├── YES → NGINX config reload failed
│   ├── Run: kubectl exec -n ingress-nginx <POD> -- nginx -t
│   ├── Error message identifies bad Ingress annotation or ConfigMap value
│   ├── Find offending Ingress: kubectl get ingress -A | grep <problem_host>
│   └── Delete/fix Ingress → controller auto-reloads → verify gauge = 1
└── NO → NGINX config is valid; upstream issue
    ├── Check endpoints: kubectl get endpoints -n <NS> <SVC>
    │   ├── EMPTY ADDRESSES → no ready backend pods
    │   │   ├── Check pod readiness: kubectl get pods -n <NS> -l app=<SVC>
    │   │   ├── Pod CrashLoopBackOff → fix application crash
    │   │   └── Pod Pending → check scheduling: kubectl describe pod -n <NS> <POD>
    │   └── ADDRESSES POPULATED → pods exist but NGINX can't reach them
    │       ├── NetworkPolicy blocking? kubectl describe networkpolicy -n <NS>
    │       ├── Wrong targetPort? kubectl describe svc -n <NS> <SVC>
    │       └── DNS resolution failure? kubectl exec -n ingress-nginx <POD> -- nslookup <SVC>.<NS>.svc.cluster.local
```

### Tree 2: TLS / HTTPS failures

```
Does curl return SSL handshake error or browser shows cert warning?
├── YES → TLS problem
│   ├── Check cert expiry:
│   │   kubectl get secret -n <NS> <TLS_SECRET> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -dates
│   │   ├── CERT EXPIRED → renew
│   │   │   ├── cert-manager managed: kubectl delete certificate -n <NS> <CERT_NAME> → auto-renew
│   │   │   └── Manual: kubectl create secret tls <SECRET> --cert=new.crt --key=new.key -n <NS> --dry-run=client -o yaml | kubectl apply -f -
│   │   └── CERT VALID → NGINX serving stale cert from memory
│   │       └── Force reload: kubectl exec -n ingress-nginx <POD> -- nginx -s reload
│   └── Multiple controller replicas serving different certs?
│       ├── md5sum check: kubectl exec <POD1> -- cat /etc/nginx/nginx.conf | md5sum vs <POD2>
│       └── Rolling restart to sync: kubectl rollout restart deployment/ingress-nginx-controller -n ingress-nginx
└── NO → HTTP works but HTTPS does not reach correct backend
    ├── Is ssl-passthrough enabled on Ingress?
    │   ├── YES → backend must terminate TLS; check backend pod TLS config
    │   └── NO → check Ingress spec.tls section: kubectl get ingress -n <NS> <ING> -o yaml
    └── Wrong IngressClass? kubectl get ingressclass → confirm <ING> ingressClassName matches controller
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| NGINX controller OOMKilled due to unbounded ConfigMap `proxy-buffer-size` | Large response headers buffered in memory per-connection; controller pod memory grows until OOMKill | `kubectl describe pod -n ingress-nginx <POD>` shows `OOMKilled`; `kubectl top pod -n ingress-nginx` shows memory near limit | All ingress traffic lost for duration of pod restart; LB health check fails | Reduce `proxy-buffer-size` to `8k` in ConfigMap; increase controller memory limit: `kubectl set resources deployment/ingress-nginx-controller -n ingress-nginx --limits=memory=512Mi` | Default `proxy-buffer-size 4k`; increase controller memory request/limit in Helm values |
| Cloud LB data processing charges from excessive 4xx/health-check traffic | External health checkers polling every 1s per LB node; millions of requests/day | Cloud billing console: filter LB data-processed by listener; `nginx_ingress_controller_requests{status=~"4.."}` rate | High unexpected cloud LB bill | Consolidate health check path; increase health check interval on cloud LB to 10s; filter health checks from access logging | Set LB health check interval ≥ 10s; use a dedicated `/healthz` path that returns minimal response |
| Ingress with `use-regex: true` on all routes causing NGINX PCRE recompilation overhead | CPU spikes on every request; P99 latency grows linearly with number of Ingress routes | `kubectl get ingress -A -o json | jq '.items[].metadata.annotations["nginx.ingress.kubernetes.io/use-regex"]'` — count `"true"` entries; `kubectl top pod -n ingress-nginx` CPU | Controller pod CPU throttled; request latency regression for all routes | Remove `use-regex` annotation on Ingresses that don't require regex; use prefix-match instead | Audit Ingress annotations in CI; restrict regex Ingresses to those that strictly require them |
| Wildcard Ingress (`host: *`) catching all traffic and forwarding to wrong backend | All requests including unrelated services hit one backend | `kubectl get ingress -A -o json | jq '.items[] | select(.spec.rules[].host=="*")'` | All services with no Ingress receive misrouted traffic; billing on targeted backend explodes | Delete or restrict wildcard Ingress; add explicit hostnames | Require explicit hostname on all Ingresses; deny wildcard hosts via OPA/Gatekeeper policy |
| `nginx.ingress.kubernetes.io/limit-connections` too high allowing connection flood | External DDoS or misconfigured client opens thousands of connections per IP | `nginx_ingress_controller_nginx_process_connections{state="active"}` > expected baseline; `kubectl top pod -n ingress-nginx` high CPU | Backend services receive flood of connections; CPU/memory spike; pod evictions | Enable rate limiting: `kubectl annotate ingress -n <NS> <ING> nginx.ingress.kubernetes.io/limit-rps="100"`; block abusive IPs in cloud WAF | Set default `limit-connections` in ConfigMap; integrate cloud WAF upstream |
| Excess controller replicas scaled up by HPA not scaling back down | Over-provisioned pods consuming node resources; cloud node costs increasing | `kubectl get hpa -n ingress-nginx`; `kubectl get pods -n ingress-nginx | wc -l` | Wasted compute spend; other workloads may have less capacity | Manually scale down: `kubectl scale deployment/ingress-nginx-controller -n ingress-nginx --replicas=2`; adjust HPA `maxReplicas` | Set HPA `scaleDown.stabilizationWindowSeconds: 300`; set realistic `maxReplicas` based on peak traffic |
| `enable-access-log-for-health-check: "true"` generating excessive log volume | Log pipeline storage and cost growing from health check noise | `kubectl logs -n ingress-nginx <POD> | grep "/healthz" | wc -l` per minute; compare against total log lines | Log storage costs increase; log search performance degrades from noise | Set `enable-access-log-for-health-check: "false"` in ConfigMap | Default: disable health check access logging; only enable temporarily for debugging |
| Controller running on on-demand nodes instead of spot/preemptible | Cluster cost higher than expected; controller on expensive instance type | `kubectl get pod -n ingress-nginx <POD> -o jsonpath='{.spec.nodeName}'`; check node labels for instance lifecycle | Excess cloud compute costs | Add `nodeSelector` or `tolerations` in controller Helm values to prefer spot nodes with `node.kubernetes.io/lifecycle: spot` | Schedule ingress-nginx on spot nodes with `podAntiAffinity` across AZs; set `priorityClassName: system-cluster-critical` |
| Infinite redirect loop causing each request to spawn multiple upstream calls | NGINX redirect (`rewrite-target` annotation) creates loop; each client request generates N backend calls | `nginx_ingress_controller_requests` per-ingress label count > 1x client request rate; NGINX access log shows redirect chain | Backend request rate 2-10x client rate; backend CPU/cost spikes | Remove or fix `rewrite-target` annotation: `kubectl annotate ingress -n <NS> <ING> nginx.ingress.kubernetes.io/rewrite-target-` | Test rewrites in staging; review `rewrite-target` PRs carefully; add Ingress annotation schema validation in CI |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot Ingress rule — single high-traffic path saturating controller pod | `nginx_ingress_controller_requests` rate dominated by one Ingress; p99 latency rising | `kubectl exec -n ingress-nginx <POD> -- curl -s http://localhost:10254/metrics | grep nginx_ingress_controller_requests | sort -t{ -k2 -rn | head -10` | All traffic to one Ingress concentrating on single controller pod; no sharding | Scale controller replicas; add `nginx.ingress.kubernetes.io/load-balance: least_conn` annotation |
| NGINX upstream connection pool exhaustion to backend | `502 Bad Gateway` errors; `nginx_ingress_controller_requests{status="502"}` spiking by upstream/ingress label | `kubectl exec -n ingress-nginx <POD> -- curl -s http://localhost:10254/metrics | grep nginx_ingress_controller_requests` | Backend `keepalive` pool size too small; connections not being reused | Increase `upstream-keepalive-connections: "100"` in ConfigMap; tune `upstream-keepalive-requests` |
| NGINX worker process memory pressure from large request body buffering | Controller pod memory growing; `nginx_ingress_controller_nginx_process_resident_memory_bytes` near limit | `kubectl top pods -n ingress-nginx`; `kubectl exec -n ingress-nginx <POD> -- nginx -T | grep client_max_body_size` | Large file uploads buffered in worker memory; too many concurrent upload requests | Set `proxy-body-size: "10m"` in ConfigMap; enable `proxy-request-buffering: "off"` for streaming uploads |
| NGINX worker thread pool saturation from SSL termination overhead | CPU 100% on controller pods; SSL handshake latency high; `nginx_ingress_controller_response_duration_seconds` p99 elevated | `kubectl top pods -n ingress-nginx`; `kubectl exec -n ingress-nginx <POD> -- curl -s http://localhost:10254/metrics | grep ssl` | Too many concurrent TLS handshakes; `worker_processes` count too low | Increase controller pod `worker-processes` in ConfigMap; scale HPA; enable SSL session cache |
| Slow upstream (backend pod) causing NGINX worker blocking | NGINX workers all waiting on slow upstream; new requests queued | `kubectl exec -n ingress-nginx <POD> -- curl -s http://localhost:10254/metrics | grep "nginx_ingress_controller_response_duration_seconds"` — p99 high | Upstream service slow (GC, DB query, downstream); NGINX workers blocked in proxy_pass | Set `proxy-read-timeout: "60"` and `proxy-send-timeout: "60"`; investigate upstream latency root cause |
| CPU steal on node running ingress-nginx from co-located workloads | NGINX latency spikes correlate with noisy-neighbor workload schedule | `kubectl describe node <NODE> | grep cpu`; `sar -u 1 30` on node | Shared node CPU oversubscribed; ingress-nginx gets insufficient CPU cycles | Use `nodeSelector` or `taints/tolerations` to pin ingress-nginx to dedicated nodes |
| NGINX upstream keepalive contention — all keepalive slots occupied by slow backend | New upstream connections failing; `connect() failed (111: Connection refused)` in NGINX log | `kubectl logs -n ingress-nginx <POD> | grep "connect() failed\|upstream"` | Backend keepalive pool at limit; slow backends not releasing connections | Set `upstream-keepalive-connections: "200"`; add `proxy-next-upstream: "error timeout"` to retry |
| Lua plugin serialization overhead from complex annotation logic | Config reload taking >30s; request latency briefly elevated after each Ingress change | `kubectl logs -n ingress-nginx <POD> | grep "Reloading NGINX"`; `time kubectl apply -f <ingress>.yaml` | Custom Lua snippets or complex annotation regex evaluated on every config reload | Simplify Lua snippets; use `allow-snippet-annotations: "false"` for untrusted namespaces |
| `proxy-buffer-size` misconfiguration causing large-header requests to fail | `502` or `400` errors for requests with large cookies or JWT tokens; `upstream sent invalid header` in NGINX logs | `kubectl logs -n ingress-nginx <POD> | grep "upstream sent invalid header\|proxy_buffer_size"`; check ConfigMap | Default `proxy-buffer-size: "4k"` too small for large response headers from upstream | Increase `proxy-buffer-size: "16k"` and `proxy-buffers: "4 16k"` in ingress-nginx ConfigMap |
| Downstream dependency latency — slow cert-manager ACME challenge causing Ingress TLS delays | Ingress reports TLS `Not Ready`; cert-manager `CertificateRequest` stuck in `Pending` | `kubectl get certificates,certificaterequests -A`; `kubectl describe certificaterequest <NAME> -n <NS>` | cert-manager ACME HTTP-01 challenge slow or blocked; DNS-01 propagation delay | Check cert-manager logs: `kubectl logs -n cert-manager -l app=cert-manager`; verify ACME solver accessible |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| Ingress TLS cert expiry (Let's Encrypt / cert-manager) | Browsers show cert expired; `nginx_ingress_controller_ssl_expire_time_seconds` metric past threshold | `kubectl get secret -n <NS> <tls-secret> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -enddate`; `kubectl get certificates -A` | HTTPS connections rejected by clients; site unavailable | Delete expired CertificateRequest to force renewal: `kubectl delete certificaterequest -n <NS> <NAME>`; check cert-manager ACME access |
| mTLS rotation failure between ingress and backend | `502` or `400` errors after cert rotation; NGINX log: `SSL_CTX_use_certificate_file() failed` | `kubectl logs -n ingress-nginx <POD> | grep "SSL\|certificate"`; `kubectl describe secret <backend-cert-secret> -n <NS>` | Backend mTLS handshake fails; traffic to affected Ingress blocked | Roll back to old cert secret; coordinate cert rotation with config reload: `kubectl rollout restart deployment/ingress-nginx-controller -n ingress-nginx` |
| DNS resolution failure for Ingress backend Service | NGINX log: `host not found in upstream "<SERVICE>.<NS>.svc.cluster.local"`; `502` for affected host | `kubectl exec -n ingress-nginx <POD> -- nslookup <SERVICE>.<NS>.svc.cluster.local`; `kubectl get endpoints -n <NS> <SERVICE>` | All traffic to that Ingress host returns 502 | Verify Service exists and has endpoints; check CoreDNS: `kubectl logs -n kube-system -l k8s-app=kube-dns` |
| TCP connection exhaustion — cloud LB to ingress-nginx `TIME_WAIT` buildup | New client connections refused; cloud LB health check fails; node `ss -s` shows thousands of `TIME_WAIT` | `ss -s` on ingress-nginx node — watch TIME-WAIT; `kubectl exec -n ingress-nginx <POD> -- ss -s` | New connections rejected at cloud LB or controller pod | Enable `net.ipv4.tcp_tw_reuse=1` on nodes; tune `net.ipv4.tcp_fin_timeout=15` via sysctl |
| Cloud load balancer health check misconfiguration | Cloud LB marks controller pods unhealthy; traffic stops routing to healthy pods | `kubectl get svc -n ingress-nginx -o yaml | grep healthCheckNodePort`; cloud console LB health check status | Traffic dropped even though ingress-nginx pods are healthy | Fix LB health check path to `/healthz` on port `10254`; ensure security groups allow health check source IP |
| Packet loss between cloud LB and ingress-nginx pods | Intermittent `502` at low rate; `nginx_ingress_controller_requests{status="502"}` slowly climbing | `kubectl exec -n ingress-nginx <POD> -- ping -c 500 <LB_VIP>` — measure packet loss; check cloud LB access logs | Intermittent user-facing errors; low-level network degradation | Report to cloud provider; check NIC driver and offload settings on nodes: `ethtool -k eth0 | grep offload` |
| MTU mismatch between cloud LB and Kubernetes overlay network | Large response bodies silently truncated; `502` for responses >MTU; gzip responses fail | `ping -M do -s 1400 <INGRESS_EXTERNAL_IP>` from client; `kubectl exec -n ingress-nginx <POD> -- ip link show eth0` | Large HTTP responses (downloads, large JSON) return 502; small responses unaffected | Reduce overlay MTU: set `net.ipv4.ip_default_ttl` and `--iptables-mtu`; or `nginx.ingress.kubernetes.io/proxy-buffer-size` workaround |
| Firewall rule change blocking ingress-nginx webhook port 8443 | `helm upgrade` of ingress-nginx chart fails: `failed calling webhook "validate.nginx.ingress.kubernetes.io"`; new Ingresses rejected | `kubectl get validatingwebhookconfiguration ingress-nginx-admission -o yaml | grep -A2 clientConfig`; `telnet <WEBHOOK_SVC_IP> 8443` | New Ingress resources rejected by admission webhook; deployment pipelines blocked | Restore firewall/security-group rule allowing port 8443 from kube-apiserver CIDR; or temporarily delete webhook config |
| SSL handshake timeout between upstream backend and ingress-nginx (backend HTTPS) | `504` Gateway Timeout or `SSL_do_handshake() failed` in NGINX log for backends configured with `nginx.ingress.kubernetes.io/backend-protocol: HTTPS` | `kubectl logs -n ingress-nginx <POD> | grep "SSL_do_handshake\|upstream"` | All traffic to HTTPS-backend Ingress fails | Verify backend TLS cert valid; set `nginx.ingress.kubernetes.io/proxy-ssl-verify: "off"` for internal backends |
| Connection reset at ingress-nginx during long-polling or WebSocket upgrade | WebSocket connections drop after LB idle timeout; clients see `connection reset`; NGINX log: `upstream prematurely closed connection` | `kubectl logs -n ingress-nginx <POD> | grep "upstream prematurely\|connection reset"`; check Ingress annotations for WS support | WebSocket and long-poll connections dropped; real-time features broken | Add `nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"` and `nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"` annotations |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| ingress-nginx controller pod OOM kill | Controller pod restarted by OOM; `kubectl describe pod -n ingress-nginx <POD>` shows `OOMKilled`; brief traffic drop | `kubectl get events -n ingress-nginx | grep OOM`; `kubectl top pods -n ingress-nginx` | Increase memory limits in `values.yaml`: `controller.resources.limits.memory=1Gi`; `helm upgrade ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --set controller.resources.limits.memory=1Gi` | Right-size based on `nginx_ingress_controller_nginx_process_resident_memory_bytes` peak; set requests=limits for Guaranteed QoS |
| NGINX temp directory disk full (buffered request/response bodies) | `502` from NGINX; controller pod logs: `no space left on device`; temp files accumulating | `kubectl exec -n ingress-nginx <POD> -- df -h /tmp`; `kubectl exec -n ingress-nginx <POD> -- du -sh /tmp/nginx*` | Reduce `proxy-body-size`; disable body buffering: `proxy-request-buffering: "off"` in ConfigMap; delete old temp files | Mount dedicated emptyDir for NGINX temp; set `proxy-body-size: "10m"` to limit buffered size |
| Log partition disk full on ingress-nginx node from NGINX access log | Node `DiskPressure`; pod evictions on ingress node; `kubectl describe node <NODE>` shows disk pressure | `kubectl exec -n ingress-nginx <POD> -- du -sh /var/log/nginx/`; `df -h` on node | Enable log rotation; redirect logs to stdout only (`access-log-path: "stdout"`); truncate on node | Configure log rotation via ConfigMap `disable-access-log: "false"`; always log to stdout not to file |
| File descriptor exhaustion — too many concurrent upstream connections | NGINX log: `accept() failed (24: Too many open files)`; upstream connection failures | `kubectl exec -n ingress-nginx <POD> -- cat /proc/1/limits | grep "open files"` | `worker_rlimit_nofile` not set; too many simultaneous connections | Add `worker-rlimit-nofile: "65536"` to ingress-nginx ConfigMap; trigger config reload |
| Inode exhaustion on ingress-nginx node from small temp files | `no space left on device` despite `df` showing free disk space; `df -i` shows 100% inodes | `kubectl exec -n ingress-nginx <POD> -- df -i /tmp`; `find /tmp -type f | wc -l` on node | Delete orphaned temp files; reduce concurrent large uploads | Use XFS for node disk (better inode scaling); set `proxy-body-size` to limit temp file creation |
| CPU throttle on ingress-nginx pod from CGroup CPU limit | Latency spikes under moderate traffic; `container_cpu_cfs_throttled_seconds_total` metric high | `kubectl top pods -n ingress-nginx`; Prometheus: `rate(container_cpu_cfs_throttled_seconds_total{pod=~"ingress-nginx.*"}[5m])` | Increase CPU limit: `helm upgrade ingress-nginx ... --set controller.resources.limits.cpu=2000m` | Set CPU limit based on load test peak; monitor throttle ratio; consider `cpu.cfs_quota_us` tuning |
| Swap exhaustion on ingress-nginx node | NGINX worker processes page-faulting; latency high; `vmstat` shows swap I/O | `free -h`; `vmstat 1 10 | awk '{print $7, $8}'`; `kubectl describe node <NODE> | grep MemoryPressure` | Disable swap: `swapoff -a` on node; drain and recreate node | Disable swap on all Kubernetes nodes via kubelet config; provision node RAM at 2x expected working set |
| Kernel thread limit exhaustion from NGINX worker process spawning | NGINX fails to spawn workers; `fork: Resource temporarily unavailable` in controller logs | `cat /proc/sys/kernel/pid_max`; `ps aux | wc -l` on ingress-nginx node | `sysctl -w kernel.pid_max=4194304`; reduce `worker-processes` count in ConfigMap | Set `kernel.pid_max=4194304` via node DaemonSet or machine config; monitor with `node_processes` exporter |
| Socket backlog overflow at ingress-nginx pod from traffic burst | LB health checks fail sporadically; `connect() failed (111: Connection refused)` under burst | `ss -lnt | grep :80` — watch `Recv-Q`; `netstat -s | grep "SYNs to LISTEN"` | `net.core.somaxconn` too small; NGINX `listen` backlog too small | Increase `net.core.somaxconn=65535` via pod `securityContext.sysctls`; add `listen-backlog: "65535"` to ConfigMap |
| Ephemeral port exhaustion on ingress-nginx pod making upstream connections | NGINX log: `connect() failed (99: Cannot assign requested address)`; upstream connection failures | `kubectl exec -n ingress-nginx <POD> -- ss -s | grep TIME-WAIT`; `cat /proc/sys/net/ipv4/ip_local_port_range` | Enable `net.ipv4.tcp_tw_reuse=1` via pod sysctls; widen port range | Set `net.ipv4.ip_local_port_range=1024 65535` via pod sysctls; use upstream keepalive to reduce connection churn |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Ingress config hot-reload race — new Ingress rule applied mid-request causing connection drop | Brief `502` spike after Ingress resource creation/update; `nginx_ingress_controller_config_last_reload_successful` metric flaps | `kubectl logs -n ingress-nginx <POD> | grep "Reloading NGINX\|reload"`; `kubectl get events -n ingress-nginx | grep reload` | In-flight requests dropped at reload boundary; user-visible errors | Ensure NGINX graceful reload; tune `shutdown-grace-period` in ConfigMap; use `--enable-ssl-passthrough` carefully |
| Admission webhook saga failure — partial Ingress creation leaves invalid routing state | Ingress created but admission webhook rejected backend service annotation update; routing inconsistent | `kubectl describe ingress <NAME> -n <NS> | grep Events`; `kubectl get validatingwebhookconfiguration ingress-nginx-admission -o yaml` | Some Ingress rules valid, others invalid; partial traffic routing | Delete and recreate the Ingress resource; ensure all referenced Services exist before creating Ingress |
| Stale endpoint propagation — Ingress routes traffic to terminated pod during rolling update | `502` errors during deployment rollout; NGINX upstream list includes IPs of terminated pods | `kubectl get endpoints -n <NS> <SERVICE> -o yaml`; `kubectl logs -n ingress-nginx <POD> | grep "connect() failed"` | User-visible `502` errors during rolling deployments | Set `proxy-next-upstream: "error timeout"` annotation; configure PodDisruptionBudget; use `preStop` hook with `sleep 5` |
| Out-of-order cert-manager certificate update and Ingress TLS secret reference | NGINX loads old cert while cert-manager is rotating; brief window of mismatched cert | `kubectl get secret -n <NS> <tls-secret> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -enddate`; check cert-manager events | Clients may see old cert briefly after rotation; TLS handshake succeeds with previous cert | Trigger config reload after cert update: `kubectl rollout restart deployment/ingress-nginx-controller -n ingress-nginx`; cert-manager handles automatically in steady state |
| At-least-once delivery duplicate — client retry on `502` causes duplicate POST to backend | Backend receives duplicate POST (e.g., payment, order); `nginx_ingress_controller_requests{status="502"}` followed by `200` | `kubectl logs -n ingress-nginx <POD> | grep "POST.*502" | awk '{print $7}'`; cross-reference backend application logs | Duplicate write operations in backend; idempotency violation | Ensure backend APIs are idempotent (unique request ID header); set `proxy-next-upstream: "off"` for non-idempotent routes |
| Cross-namespace Ingress collision — two Ingresses claiming same hostname | NGINX config has duplicate server block; one Ingress silently wins; the other drops traffic | `kubectl get ingress -A -o json | jq '.items[] | select(.spec.rules[].host=="<HOST>") | {ns: .metadata.namespace, name: .metadata.name}'` | One service receives all traffic; other service completely unreachable | Assign unique hostnames; use IngressClass to scope controllers; add `--watch-namespace` limit to controller |
| Compensating rollback failure — Helm rollback of ingress chart restoring invalid ConfigMap | After Helm rollback, NGINX config fails to validate; controller stuck in reload loop | `kubectl logs -n ingress-nginx <POD> | grep "NGINX configuration test failed"`; `helm history ingress-nginx -n ingress-nginx` | NGINX refuses to reload invalid config; traffic continues on last-valid config (no new Ingress rules apply) | Identify bad ConfigMap key: `kubectl exec -n ingress-nginx <POD> -- nginx -t 2>&1`; fix value; trigger reload |
| Distributed rate limiting inconsistency — multiple controller replicas applying different rate limit counters | Rate limiting inconsistent across replicas; some clients rate-limited, others not for same key | `kubectl get pods -n ingress-nginx | grep -c Running` — if >1 pod and using `nginx.ingress.kubernetes.io/limit-rps`; check if Redis-backed global rate limiting configured | Per-pod rate limiting ineffective; SLA violations for rate-limited APIs | Enable global rate limiting via Redis: `lua-shared-dicts` + Redis; or accept per-pod limits and set lower threshold |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — one Ingress with complex Lua snippet consuming all NGINX worker CPU | `kubectl top pods -n ingress-nginx`; `kubectl exec -n ingress-nginx <POD> -- top -b -n1 | grep nginx` — worker threads at 100% | All other tenants' ingress routes experience latency; NGINX accept queue fills | `kubectl annotate ingress <NAME> -n <NS> nginx.ingress.kubernetes.io/configuration-snippet-` to remove Lua | Remove complex Lua from hot-path Ingress; use `--allow-snippet-annotations=false` to prevent future injection |
| Memory pressure from adjacent tenant's large request body buffering filling NGINX temp dir | `kubectl exec -n ingress-nginx <POD> -- df -h /tmp` — near full; `kubectl top pods -n ingress-nginx` | Other tenants share controller pod; NGINX temp disk fills; all uploads fail | Scale out: `kubectl scale deployment ingress-nginx-controller -n ingress-nginx --replicas=3`; move heavy upload Ingress to dedicated controller | Set `proxy-body-size: "10m"` per-tenant Ingress annotation; or deploy dedicated ingress class for large-upload tenants |
| Disk I/O saturation from access log volume — one high-traffic Ingress generating millions of log lines | `kubectl exec -n ingress-nginx <POD> -- du -sh /var/log/nginx/`; node `iostat -x 1 5` high `%util` | Node disk filling; pod evictions; other tenants' pods evicted from same node | Disable access log for high-traffic Ingress: `kubectl annotate ingress <NAME> -n <NS> nginx.ingress.kubernetes.io/enable-access-log=false` | Per-tenant access log disable; redirect all logs to stdout; avoid logging to file in container |
| Network bandwidth monopoly — one Ingress route handling large media downloads saturating egress | `iftop -i eth0` on ingress node — single Ingress host consuming all bandwidth; others starved | Other tenants' ingress routes experience packet loss and latency | Add bandwidth annotation: `kubectl annotate ingress <NAME> -n <NS> nginx.ingress.kubernetes.io/limit-rate=1m` | Implement per-Ingress bandwidth limiting via `limit-rate` and `limit-rate-after` annotations |
| Connection pool starvation — one tenant's persistent WebSocket connections holding all upstream keepalive slots | `kubectl exec -n ingress-nginx <POD> -- curl -s http://localhost:10254/metrics | grep nginx_ingress_controller_requests` — one host dominating | Other tenants' requests cannot reuse keepalive connections; new connections failing | Reduce WebSocket keepalive timeout: `kubectl annotate ingress <NAME> -n <NS> nginx.ingress.kubernetes.io/proxy-read-timeout=300` | Set per-Ingress `upstream-keepalive-connections: "10"`; deploy dedicated ingress class for WebSocket routes |
| Quota enforcement gap — no IngressClass-based multi-tenancy; all Ingresses share one controller | `kubectl get ingress -A | wc -l` — large number of ingresses all using same controller; no per-namespace controllers | One misbehaving Ingress can reload shared NGINX config every few seconds; all tenants see brief latency on reload | Deploy per-tenant IngressClass: `helm install ingress-nginx-tenant ingress-nginx/ingress-nginx --set controller.ingressClassResource.name=nginx-tenant` | Use `IngressClass` resources to isolate tenants; `--watch-namespace` to scope controller to specific namespaces |
| Cross-tenant data leak risk — ingress-nginx `configuration-snippet` injecting `add_header` leaking internal routing info | `kubectl get ingress -A -o yaml | grep add_header` — check for sensitive headers being added | Tenant with Ingress create permission injecting response headers revealing internal service paths or auth tokens | Other tenants' clients receive internal routing headers from misconfigured Ingress | Remove snippet: `kubectl annotate ingress <NAME> -n <NS> nginx.ingress.kubernetes.io/configuration-snippet-`; enforce via OPA policy |
| Rate limit bypass — tenant omitting `limit-rps` annotation, consuming disproportionate ingress capacity | `kubectl exec -n ingress-nginx <POD> -- curl http://localhost:10254/metrics | grep nginx_ingress_controller_requests | sort -t{ -k2 -rn | head` — one namespace dominates | Other tenants' routes experience increased latency; shared ingress controller saturated | Apply emergency rate limit: `kubectl annotate ingress <NAME> -n <NS> nginx.ingress.kubernetes.io/limit-rps=50` | Enforce rate limit annotations via OPA/Gatekeeper policy requiring `limit-rps` annotation on all Ingress resources |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Prometheus scrape failure — ingress-nginx metrics endpoint unreachable | `nginx_ingress_controller_*` metrics absent; ingress dashboards blank; `up{job="ingress-nginx"}==0` | Controller pod restarted and ServiceMonitor label selector outdated; or pod IP changed and scrape target stale | `kubectl exec -n ingress-nginx <POD> -- curl -s http://localhost:10254/metrics | head`; check Prometheus targets UI | Fix ServiceMonitor label selector; add `up==0` alert for ingress-nginx scrape job |
| Trace sampling gap — ingress-nginx Jaeger tracing at 1% missing request errors | Error rate spike in metrics but no traces available in Jaeger for incident window | OpenTracing `jaeger-sampler-type: const` with `jaeger-sampler-param: "0.01"` too low | Enable debug tracing temporarily: `kubectl edit configmap ingress-nginx -n ingress-nginx` → set `jaeger-sampler-param: "1"` | Configure adaptive sampling: `jaeger-sampler-type: probabilistic` with `jaeger-sampler-param: "0.1"` minimum |
| Log pipeline silent drop — NGINX access logs not flowing to ELK during traffic burst | Security gap; access log entries missing from Splunk for specific time windows | Fluentd on node buffer overflow during high-RPS period; log shipper drops oldest entries | `kubectl logs -n ingress-nginx <POD> | wc -l` vs Splunk query count for same period | Increase Fluentd `buffer_queue_limit`; use persistent buffer for ingress-nginx log stream; add overflow alert |
| Alert rule misconfiguration — `nginx_ingress_controller_requests{status=~"5.."}` alert silenced after controller upgrade renamed metric | 5xx error rate spike goes undetected; users experiencing errors without on-call page | ingress-nginx 1.x changed metric label from `status_code` to `status`; alert expression uses old label | `kubectl exec -n ingress-nginx <POD> -- curl -s http://localhost:10254/metrics | grep requests` — check current labels | Update Prometheus alert expression; test with synthetic 5xx by temporarily pointing backend to non-existent pod |
| Cardinality explosion — `nginx_ingress_controller_requests` with per-path labels creating millions of series | Prometheus memory OOM; ingress dashboards timeout; scrape duration >30s | Ingress routes with dynamic path segments (e.g., `/api/users/<UUID>/orders`) creating unique label per path | `kubectl exec prometheus-<POD> -- promtool tsdb analyze /prometheus | grep nginx` — identify cardinality | Use `metric_relabel_configs` to strip path from `nginx_ingress_controller_requests` labels; normalize dynamic paths |
| Missing ingress-nginx health endpoint alerting — webhook failures silent | Admission webhook rejections for new Ingress resources go undetected; deployments silently fail | Webhook failure mode `failurePolicy: Fail` rejects resources but no metric exposed for webhook rejections | `kubectl get events -A | grep "failed calling webhook\|admission"`; `kubectl describe ingress <NAME>` | Add alert on `kube_apiserver_admission_webhook_rejection_count{webhook=~".*ingress-nginx.*"} > 0` |
| Instrumentation gap — no metric for NGINX config reload failures | Bad Ingress annotation causing NGINX config test failure goes unreported; config not applied | ingress-nginx emits `nginx_ingress_controller_config_last_reload_successful` but not alerting on value=0 | `kubectl logs -n ingress-nginx <POD> | grep "NGINX configuration test failed\|Reloading NGINX"` | Add Prometheus alert: `nginx_ingress_controller_config_last_reload_successful == 0` |
| Alertmanager outage masking ingress-nginx 502 alert | 502 spike goes undetected; users experience errors; on-call not paged | Alertmanager OOMKilled simultaneously with ingress 502 incident | Check Prometheus alert state directly: `http://prometheus:9090/alerts`; `kubectl get pods -n monitoring | grep alertmanager` | Implement dead-man's-switch Watchdog alert; deploy alertmanager with `PodDisruptionBudget` and anti-affinity |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| ingress-nginx minor version upgrade rollback — breaking change in NGINX config template | After upgrade, NGINX config test fails; controller pod in `CrashLoopBackOff`; all traffic affected | `kubectl logs -n ingress-nginx <POD> | grep "NGINX configuration test failed\|nginx: configuration file"` | `helm rollback ingress-nginx <PREV_REV> -n ingress-nginx`; verify: `kubectl rollout status deployment/ingress-nginx-controller -n ingress-nginx` | Run `helm diff upgrade` before upgrade; stage upgrade in non-production ingress class first |
| Schema migration partial completion — Ingress `networking.k8s.io/v1` migration from `extensions/v1beta1` | Some Ingresses migrated; others still using deprecated API; Kubernetes 1.22+ silently drops old resources | `kubectl get ingress -A -o json | jq '.items[] | select(.apiVersion=="extensions/v1beta1") | .metadata.name'` | Re-apply Ingresses using `networking.k8s.io/v1` API format; `kubectl replace -f <ingress-v1>.yaml` | Run migration script: `kubectl-convert -f <ingress.yaml> --output-version networking.k8s.io/v1`; validate with `kubectl apply --dry-run=server` |
| Rolling upgrade version skew — new ingress-nginx pods and old pods serving different config after hot-reload race | Traffic inconsistency during rollout; some requests routed correctly, others returning 404 | `kubectl get pods -n ingress-nginx --show-labels | grep ingress-nginx-controller` — check for pods in different ReplicaSets | `kubectl rollout undo deployment/ingress-nginx-controller -n ingress-nginx`; wait for rollout completion | Use `kubectl rollout pause` during upgrade; validate new pods handle traffic before unpausing |
| Zero-downtime upgrade gone wrong — NGINX graceful shutdown dropping long-lived connections | WebSocket and SSE connections dropped during rolling upgrade; `kubectl logs` shows `signal 15` during pod termination | `kubectl logs -n ingress-nginx <OLD_POD> | grep "graceful shutdown\|exiting\|SIGTERM"` | Speed recovery: `kubectl rollout undo deployment/ingress-nginx-controller -n ingress-nginx` | Set `controller.lifecycle.preStop` sleep: `kubectl edit deployment/ingress-nginx-controller -n ingress-nginx` → add `preStop: exec: command: [sleep, "10"]` |
| Config format change — ingress-nginx 1.9+ ConfigMap key renamed causing annotations ignored | After upgrade, `proxy-body-size` ConfigMap key no longer effective; large uploads silently fail | `kubectl exec -n ingress-nginx <POD> -- nginx -T | grep client_max_body_size` — if default (1m), annotation not applied | `helm upgrade ingress-nginx ... --set controller.config.proxy-body-size=100m`; verify with `nginx -T` | Diff ConfigMap keys in ingress-nginx release notes; use `helm diff upgrade` to preview ConfigMap changes |
| Data format incompatibility — TLS secret created in wrong format after cert-manager upgrade | NGINX log: `SSL_CTX_use_certificate_file() failed`; HTTPS routes return `502` | `kubectl get secret -n <NS> <TLS_SECRET> -o jsonpath='{.data}' | jq 'keys'` — verify `tls.crt` and `tls.key` present | Delete and recreate TLS secret from new cert-manager output; trigger ingress reload: `kubectl rollout restart deployment/ingress-nginx-controller -n ingress-nginx` | Validate TLS secret format post-cert-manager upgrade: `kubectl get secret <NAME> -n <NS> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -text` |
| Feature flag rollout — enabling `enable-real-ip` causing IP-based rate limits to match wrong addresses | Rate limits applied to load balancer IP instead of real client IP; all clients behind same LB get throttled together | `kubectl exec -n ingress-nginx <POD> -- curl http://localhost:10254/metrics | grep limit`; check `X-Forwarded-For` header from test client | Set `use-forwarded-headers: "true"` and `forwarded-for-header: "X-Forwarded-For"` in ConfigMap | Test IP forwarding with `curl -H "X-Forwarded-For: <TEST_IP>" http://<INGRESS>/` before enabling cluster-wide |
| Dependency version conflict — ingress-nginx upgrade requiring newer Kubernetes API causing CRD incompatibility | `helm upgrade` fails: `resource mapping not found for name: "ingressclasses.networking.k8s.io"`; old cluster missing CRD | `kubectl api-resources | grep ingressclass`; `kubectl version --short` | Upgrade Kubernetes cluster to minimum supported version before ingress-nginx upgrade | Check ingress-nginx compatibility matrix: https://github.com/kubernetes/ingress-nginx#supported-versions; validate with `helm install --dry-run` |

## Kernel/OS & Host-Level Failure Patterns

| Failure Mode | Symptom | Root Cause | Diagnostic Commands | Remediation |
|-------------|---------|------------|--------------------:|-------------|
| OOM killer terminates ingress-nginx controller pod | All ingress traffic stops; `502 Bad Gateway` from load balancer; `kubectl describe pod` shows `OOMKilled` | NGINX worker processes consume excessive memory during SSL session cache growth or large request body buffering for many concurrent connections | `kubectl describe pod -n ingress-nginx <POD> \| grep -A5 "Last State"`; `dmesg \| grep -i "oom.*nginx"` ; `kubectl top pod -n ingress-nginx` | Increase controller memory limit: `kubectl set resources deployment/ingress-nginx-controller -n ingress-nginx --limits=memory=4Gi`; tune NGINX: set `proxy-buffer-size: "8k"` and `ssl-session-cache-size: "10m"` in ConfigMap |
| Inode exhaustion on ingress-nginx controller node | NGINX fails to write temp files for request buffering: `No space left on device`; large file uploads fail; `df -h` shows free space | NGINX creates temp files in `/tmp` for request body buffering; thousands of concurrent uploads exhaust inodes | `kubectl exec -n ingress-nginx <POD> -- df -i /tmp`; `kubectl exec -n ingress-nginx <POD> -- find /tmp -type f \| wc -l` | Configure `proxy-body-size: "0"` to disable buffering for large uploads; mount `/tmp` as emptyDir with `medium: Memory`; add cleanup: `client-body-temp-path` with periodic pruning |
| CPU steal causing NGINX request latency spikes | p99 request latency increases from 5ms to 200ms; `nginx_ingress_controller_request_duration_seconds` histogram shifts right | Noisy neighbor on shared node stealing CPU; NGINX worker processes cannot process connections fast enough | `kubectl exec -n ingress-nginx <POD> -- cat /proc/stat \| awk '/^cpu / {print "steal%: "$9}'`; `mpstat -P ALL 1 5 \| grep steal` | Use `nodeSelector` to pin ingress-nginx to dedicated nodes: `kubectl patch deployment ingress-nginx-controller -n ingress-nginx -p '{"spec":{"template":{"spec":{"nodeSelector":{"node-role":"ingress"}}}}}'`; use compute-optimized instances |
| NTP skew causing NGINX OCSP stapling failures | NGINX logs `OCSP response has expired`; TLS handshake fails for clients requiring OCSP; `ssl_stapling` broken | Host clock drifted ahead of actual time; OCSP response appears expired because NGINX compares against skewed local clock | `kubectl exec -n ingress-nginx <POD> -- date -u`; `chronyc tracking \| grep "System time"`; `kubectl logs -n ingress-nginx <POD> \| grep "OCSP"` | Sync clock: `chronyc makestep`; add NTP monitoring: alert on `node_timex_offset_seconds > 2`; disable OCSP stapling temporarily: `enable-ocsp: "false"` in ConfigMap |
| File descriptor exhaustion on ingress-nginx controller | NGINX cannot accept new connections: `Too many open files` in error log; connection queue grows; upstream timeouts increase | Each proxied connection uses 2 FDs (client + upstream); 50,000 concurrent connections exhaust default 65536 FD limit | `kubectl exec -n ingress-nginx <POD> -- cat /proc/1/limits \| grep "Max open files"`; `kubectl exec -n ingress-nginx <POD> -- ls /proc/1/fd \| wc -l`; `kubectl logs -n ingress-nginx <POD> \| grep "Too many open files"` | Increase FD limit in controller Deployment: `securityContext.sysctls: [{name: "net.core.somaxconn", value: "65535"}]`; set NGINX `worker-rlimit-nofile: "131072"` in ConfigMap; scale horizontally |
| TCP conntrack table full on ingress node | New connections to ingress dropped: `nf_conntrack: table full, dropping packet`; intermittent `502` errors; load balancer health checks fail | High-traffic ingress node handling 100K+ concurrent connections; conntrack entries from TIME_WAIT state accumulate | `kubectl exec -n ingress-nginx <POD> -- cat /proc/sys/net/netfilter/nf_conntrack_count`; `dmesg \| grep conntrack`; `kubectl exec -n ingress-nginx <POD> -- cat /proc/sys/net/netfilter/nf_conntrack_max` | Increase conntrack on node: `sysctl -w net.netfilter.nf_conntrack_max=1048576`; add node tuning DaemonSet; enable NGINX keepalive to reduce connection churn: `upstream-keepalive-connections: "320"` |
| Kernel panic on ingress node during TLS handshake storm | Ingress node goes offline; all traffic to node stops; `kubectl get nodes` shows `NotReady` | Kernel bug in TLS offload or AES-NI instruction triggered by high rate of TLS 1.3 handshakes; known issue with certain kernel versions | `journalctl -k -p 0 --since "1 hour ago"` on recovered node; `kubectl get events --field-selector reason=NodeNotReady`; `uname -r` | Update kernel: `apt-get upgrade linux-image-*`; enable kdump; deploy ingress-nginx with anti-affinity across nodes: `podAntiAffinity.requiredDuringSchedulingIgnoredDuringExecution` |
| NUMA imbalance causing NGINX worker thread latency disparity | Some NGINX worker processes serve requests 3x faster than others on same pod; uneven latency distribution per worker PID | NGINX workers scheduled across NUMA nodes; workers on remote NUMA node have higher memory access latency for connection pools | `kubectl exec -n ingress-nginx <POD> -- numastat -p $(pgrep -f "nginx: worker")`; `numactl --hardware` on node | Pin NGINX workers to single NUMA node: set `worker-cpu-affinity: "auto"` in ConfigMap; configure node with `topology.kubernetes.io/zone` labels; use `topologySpreadConstraints` to control placement |

## Deployment Pipeline & GitOps Failure Patterns

| Failure Mode | Symptom | Root Cause | Diagnostic Commands | Remediation |
|-------------|---------|------------|--------------------:|-------------|
| Image pull failure for ingress-nginx controller | Controller pod stuck in `ImagePullBackOff`; all ingress traffic affected if no other replicas exist | Docker Hub rate limit or registry auth failure for `registry.k8s.io/ingress-nginx/controller` image | `kubectl describe pod -n ingress-nginx <POD> \| grep -A5 "Events:"`; `kubectl get events -n ingress-nginx --field-selector reason=Failed \| grep image` | Use private registry mirror: `helm upgrade ingress-nginx ingress-nginx/ingress-nginx --set controller.image.registry=<PRIVATE_REG>`; add `imagePullSecrets`; pre-pull images on nodes |
| Ingress-nginx registry auth failure after credential rotation | Controller cannot pull new image version during upgrade: `unauthorized`; stuck on old version; upgrade blocked | ECR/GCR token expired; imagePullSecret contains stale credentials; new controller pods cannot start | `kubectl get secret -n ingress-nginx <SECRET> -o jsonpath='{.data.\.dockerconfigjson}' \| base64 -d \| jq '.auths'` | Automate token refresh: use IRSA for ECR or Workload Identity for GCR; `kubectl create secret docker-registry ingress-nginx-pull --docker-server=<REG> --docker-username=<USER> --docker-password=$(aws ecr get-login-password) -n ingress-nginx --dry-run=client -o yaml \| kubectl apply -f -` |
| Helm drift between ingress-nginx chart and live cluster | `helm upgrade` fails: `resource already exists`; ConfigMap manually modified for emergency NGINX tuning | Operator ran `kubectl edit configmap ingress-nginx-controller -n ingress-nginx` to add `proxy-read-timeout: "300"` during incident | `helm get manifest ingress-nginx -n ingress-nginx \| kubectl diff -f -`; `helm status ingress-nginx -n ingress-nginx` | Adopt resource: `kubectl annotate configmap ingress-nginx-controller meta.helm.sh/release-name=ingress-nginx --overwrite -n ingress-nginx`; update Helm values with manual changes |
| ArgoCD sync stuck on ingress-nginx CRD update | ArgoCD Application shows `OutOfSync` indefinitely; IngressClass CRD update blocked; new Ingress resources rejected | ArgoCD cannot update CRDs (out-of-scope by default); IngressClass CRD version mismatch between chart and cluster | `argocd app get ingress-nginx --grpc-web`; `kubectl get crd ingressclasses.networking.k8s.io -o jsonpath='{.metadata.resourceVersion}'`; `kubectl api-resources \| grep ingressclass` | Apply CRDs manually: `kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/<VERSION>/deploy/static/provider/cloud/deploy.yaml`; enable ArgoCD CRD management: `argocd app set ingress-nginx --sync-option ServerSideApply=true` |
| PDB blocking ingress-nginx controller rolling update | Controller rollout hangs; old pods still serving traffic; new pods cannot schedule because PDB prevents eviction | PDB `minAvailable: 1` with 1 replica means 0 disruptions allowed; rolling update cannot proceed | `kubectl get pdb -n ingress-nginx`; `kubectl describe pdb ingress-nginx-controller-pdb \| grep "Allowed disruptions: 0"` | Scale to 2 replicas first: `kubectl scale deployment ingress-nginx-controller -n ingress-nginx --replicas=2`; then proceed with rolling update; adjust PDB: `maxUnavailable: 1` |
| Blue-green cutover failure for ingress-nginx upgrade | New (green) controller serves different NGINX config; some routes return `404`; traffic split between old and new controllers | Green controller loaded Ingress resources but ConfigMap changes not applied; NGINX config test passed but custom snippets missing | `kubectl exec -n ingress-nginx <GREEN_POD> -- nginx -T \| grep -c "server_name"`; compare with blue: `kubectl exec -n ingress-nginx <BLUE_POD> -- nginx -T \| grep -c "server_name"` | Verify NGINX config parity: `kubectl exec <GREEN_POD> -- nginx -T > /tmp/green.conf && kubectl exec <BLUE_POD> -- nginx -T > /tmp/blue.conf && diff /tmp/blue.conf /tmp/green.conf`; apply missing ConfigMap entries |
| ConfigMap drift causing inconsistent NGINX config across controller replicas | Some controller pods using old ConfigMap values; others using new; inconsistent proxy timeouts across requests | ConfigMap updated but not all controller pods reloaded; NGINX reload failed silently on some pods | `kubectl get configmap ingress-nginx-controller -n ingress-nginx -o yaml \| grep "proxy-read-timeout"`; `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep proxy_read_timeout` per pod | Check reload status: `kubectl logs -n ingress-nginx <POD> \| grep "NGINX configuration test\|Reloading NGINX"`; force reload: `kubectl rollout restart deployment ingress-nginx-controller -n ingress-nginx` |
| Feature flag enabling ModSecurity WAF causing latency regression | Request latency increases 5x after enabling ModSecurity; p99 response time from 10ms to 50ms; CPU usage doubles | ModSecurity rule evaluation adds overhead per request; default OWASP CRS ruleset includes expensive regex rules | `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep modsecurity`; `kubectl logs -n ingress-nginx <POD> \| grep "ModSecurity\|SecRule"`; `kubectl top pod -n ingress-nginx` | Tune ModSecurity rules: disable expensive rules: `SecRuleRemoveById 920170 920171`; enable `SecRuleEngine DetectionOnly` for testing; set `enable-modsecurity: "false"` to rollback |

## Service Mesh & API Gateway Edge Cases

| Failure Mode | Symptom | Root Cause | Diagnostic Commands | Remediation |
|-------------|---------|------------|--------------------:|-------------|
| Circuit breaker false positive on ingress-nginx upstream | NGINX returns `502 Bad Gateway` for healthy backend; backend pod is running and passing health checks; requests succeed on direct pod access | Envoy sidecar circuit breaker on NGINX pod trips during transient upstream latency spike; marks backend as unhealthy | `istioctl proxy-config cluster <NGINX_POD>.ingress-nginx \| grep <BACKEND_SVC>`; `kubectl exec <NGINX_POD> -c istio-proxy -- pilot-agent request GET /stats \| grep outlier_detection` | Increase outlier detection: `DestinationRule` with `outlierDetection.consecutive5xxErrors: 10` and `interval: 30s` for backend services; or exclude ingress-nginx from mesh: `sidecar.istio.io/inject: "false"` |
| Rate limiting in NGINX hitting legitimate traffic | Users receive `503 Service Temporarily Unavailable` with NGINX rate limit response; legitimate API consumers throttled | `limit-req-zone` annotation rate too low; shared zone across all Ingress resources causes one high-traffic route to exhaust limit for others | `kubectl logs -n ingress-nginx <POD> \| grep "limiting requests"`; `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep limit_req` | Per-Ingress rate limit: `nginx.ingress.kubernetes.io/limit-rps: "100"` with `nginx.ingress.kubernetes.io/limit-burst-multiplier: "5"`; separate rate limit zones per Ingress resource |
| Stale service discovery endpoints behind ingress-nginx | NGINX proxies requests to terminated backend pod; `502` errors for 10-30s after backend pod termination | NGINX upstream list not updated immediately after Kubernetes endpoint removal; NGINX reload triggered but old worker still serving | `kubectl get endpoints -n <NS> <BACKEND_SVC>`; `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep "upstream.*{" -A20 \| grep server` | Reduce endpoint update delay: set `worker-shutdown-timeout: "10s"` in ConfigMap; add backend pod `preStop` hook with sleep to allow endpoint propagation before pod shutdown |
| mTLS certificate rotation breaking backend TLS passthrough | NGINX SSL passthrough Ingress returns `502` during cert rotation; TLS connection to backend fails with `certificate verify failed` | NGINX validates backend certificate during passthrough; new cert not yet propagated to all backend pods; NGINX rejects handshake | `kubectl logs -n ingress-nginx <POD> \| grep "ssl\|certificate\|verify\|upstream"` ; `kubectl exec -n ingress-nginx <POD> -- openssl s_client -connect <BACKEND_IP>:443 2>&1 \| grep "verify"` | Set `proxy-ssl-verify: "off"` for internal backends during rotation; or configure `proxy-ssl-trusted-certificate` with both old and new CA; use cert-manager with overlap period |
| Retry storm from ingress-nginx retry annotation | Backend overloaded; NGINX retries on `502`/`504`; each retry generates more load; backend falls over completely | `nginx.ingress.kubernetes.io/proxy-next-upstream: "error timeout http_502 http_504"` combined with multiple backend pods creates retry amplification | `kubectl get ingress <NAME> -n <NS> -o yaml \| grep proxy-next-upstream`; `kubectl logs -n ingress-nginx <POD> \| grep "upstream timed out"` | Limit retries: `nginx.ingress.kubernetes.io/proxy-next-upstream-tries: "1"`; add `proxy-next-upstream-timeout: "5"` to cap retry duration; implement circuit breaker at application level |
| gRPC keepalive mismatch through ingress-nginx | gRPC client receives `UNAVAILABLE: Transport closed` after 60s idle; long-running gRPC streams disconnected | NGINX default `grpc_read_timeout` and `grpc_send_timeout` set to 60s; NGINX closes idle gRPC streams before client keepalive fires | `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep "grpc_.*timeout"`; `kubectl logs -n ingress-nginx <POD> \| grep "upstream.*reset"` | Set gRPC timeouts: `nginx.ingress.kubernetes.io/server-snippet: "grpc_read_timeout 3600s; grpc_send_timeout 3600s;"`; configure gRPC keepalive: `nginx.ingress.kubernetes.io/configuration-snippet: "grpc_socket_keepalive on;"` |
| Trace context propagation lost through ingress-nginx | Distributed traces show gap between external client and backend service; ingress-nginx not forwarding `traceparent` header | NGINX not configured to propagate OpenTelemetry headers; `traceparent` and `tracestate` headers stripped during proxying | `kubectl exec -n ingress-nginx <POD> -- nginx -T \| grep "proxy_set_header.*trace"`; `curl -H "traceparent: 00-test-01" http://<INGRESS>/test -v 2>&1 \| grep trace` | Enable OpenTelemetry in ingress-nginx: `enable-opentelemetry: "true"` in ConfigMap; set `opentelemetry-config: "/etc/nginx/opentelemetry.toml"`; or manually forward headers: `proxy-set-headers` ConfigMap with `traceparent` and `tracestate` |
| Load balancer health check marking ingress-nginx as unhealthy | ALB/NLB removes ingress-nginx pod from target group; external traffic stops; NGINX is healthy and serving internal requests | Controller health endpoint `/healthz` on port `10254` returns `200` but response time exceeds ALB timeout during high connection count or config reload | `kubectl exec -n ingress-nginx <POD> -- curl -s -o /dev/null -w "%{time_total}" http://localhost:10254/healthz`; `aws elbv2 describe-target-health --target-group-arn <ARN>` | Increase ALB timeout: `aws elbv2 modify-target-group --target-group-arn <ARN> --health-check-timeout-seconds 10 --healthy-threshold-count 2`; use TCP health check on port 443 instead of HTTP |
