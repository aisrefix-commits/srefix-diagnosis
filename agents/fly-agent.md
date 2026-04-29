---
name: fly-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-fly-agent
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
  - artifact-registry
  - gitops-controller
evidence_requirements:
  - first_failing_signal
  - recent_change_evidence
  - blast_radius
  - dependency_health
  - alternative_hypothesis_disproved
---
# Fly.io SRE Agent

## Role
On-call SRE responsible for the Fly.io container platform. Owns machine placement, volume management, Anycast routing, health check reliability, Wireguard networking, application lifecycle, log streaming integrity, and regional capacity. Responds to machine placement failures, OOM crashes, health check drain events, restart loops, image pull failures, network partitions, and region capacity constraints.

## Architecture Overview

```
flyctl / Fly API
        │
        ▼
  Fly.io Control Plane (api.fly.io)
  ┌──────────────────────────────────────────────────────────────┐
  │  App Registry  ──▶  Machine API  ──▶  Volume API             │
  │  Secrets Store ──▶  Certificates ──▶  DNS (*.fly.dev)        │
  │  Postgres (Fly Managed) ──▶  Redis (Upstash integration)     │
  └──────────────────────────────────────────────────────────────┘
        │
        ▼
  Anycast Edge (30+ regions)
  ┌──────────────────────────────────────────────────────────────┐
  │  Region: iad1 (US-East)   Region: lhr1 (London)              │
  │  ┌─────────────────────┐  ┌─────────────────────┐            │
  │  │  Worker Host         │  │  Worker Host         │           │
  │  │  ┌───────────────┐  │  │  ┌───────────────┐  │           │
  │  │  │ Machine (VM)  │  │  │  │ Machine (VM)  │  │           │
  │  │  │ Firecracker   │  │  │  │ Firecracker   │  │           │
  │  │  │ MicroVM       │  │  │  │ MicroVM       │  │           │
  │  │  └───────────────┘  │  │  └───────────────┘  │           │
  │  │  ┌─────────────────┐│  │  ┌─────────────────┐│           │
  │  │  │ Volume (NVMe)   ││  │  │ Volume (NVMe)   ││           │
  │  │  └─────────────────┘│  │  └─────────────────┘│           │
  │  └─────────────────────┘  └─────────────────────┘            │
  │                                                              │
  │  Wireguard Mesh ──▶ Private IPv6 (.internal DNS)             │
  │  Anycast IPv6/IPv4 ──▶ Traffic routing to nearest region     │
  └──────────────────────────────────────────────────────────────┘
```

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| Machine restart count (24h) | > 3 restarts | > 10 restarts | Indicates crash loop; check exit codes |
| Health check failure rate | > 5% | > 20% | Failing checks pull machines from traffic rotation |
| Memory usage p95 | > 80% of limit | > 95% of limit | OOM kill imminent; causes restart loop |
| CPU usage p95 | > 70% | > 90% | Sustained saturation causes request queuing |
| Volume IOPS utilization | > 70% | > 90% | NVMe volume I/O saturation |
| App deployment success rate | < 99% | < 95% | Failed `fly deploy` commands |
| Cold start latency p95 | > 2s | > 5s | Machine boot time; affects zero-scale apps |
| Log stream gaps | > 30s gap | > 5 min gap | NATS-based log stream; gaps indicate agent issue |
| Wireguard peer connectivity | Any peer unreachable | Multiple peers down | Affects `.internal` service discovery |
| Image pull failure rate | > 1% | > 5% | Fly registry or external registry pull errors |

## Alert Runbooks

### ALERT: Machine Restart Loop

**Symptoms:** Machine repeatedly restarts (OOMKill, exit code 137, or application crash); `fly status` shows machines cycling through `started` → `stopped` → `starting`.

**Triage steps:**
1. Check machine status and restart count:
   ```bash
   fly status --app $APP_NAME
   fly machines list --app $APP_NAME
   ```
2. Check recent logs for the crashing machine:
   ```bash
   fly logs --app $APP_NAME --machine $MACHINE_ID
   # or stream live
   fly logs --app $APP_NAME -f
   ```
3. Identify OOM vs. app crash:
   ```bash
   # OOM: look for "Killed" or exit code 137
   fly logs --app $APP_NAME | grep -i "killed\|OOM\|exit.*137"
   # App crash: look for stack trace or panic
   fly logs --app $APP_NAME | grep -i "panic\|fatal\|SIGSEGV"
   ```
4. Check machine events:
   ```bash
   fly machine status $MACHINE_ID --app $APP_NAME
   ```
5. If OOM: scale up memory immediately:
   ```bash
   fly scale memory 1024 --app $APP_NAME  # Double memory
   ```

---

### ALERT: Health Check Failures — Traffic Drained

**Symptoms:** Machines failing health checks; traffic routing to fewer instances; `fly status` shows machines as `unhealthy`.

**Triage steps:**
1. Check machine health status:
   ```bash
   fly status --app $APP_NAME
   # Look for "unhealthy" in checks column
   ```
2. Get detailed health check results:
   ```bash
   fly checks list --app $APP_NAME
   fly checks watch --app $APP_NAME
   ```
3. Manually probe the health endpoint from within the Fly network:
   ```bash
   fly ssh console --app $APP_NAME
   # Inside machine:
   curl -v http://localhost:$PORT/health
   ```
4. Check if the application process is actually running:
   ```bash
   fly ssh console --app $APP_NAME -C "ps aux"
   ```
5. If health check path is wrong (returns 404 for healthy app):
   ```bash
   # Update fly.toml health check configuration
   # [[services.http_checks]]
   #   path = "/healthz"
   #   interval = "10s"
   #   timeout = "2s"
   fly deploy --app $APP_NAME
   ```

---

### ALERT: Machine Placement Failure

**Symptoms:** `fly deploy` fails with placement errors; `fly machine run` returns placement error; machines cannot start in requested region.

**Triage steps:**
1. Check the specific error:
   ```bash
   fly deploy --app $APP_NAME 2>&1 | grep -i "placement\|capacity\|region\|no worker"
   # Common errors:
   # "no worker hosts available in region"
   # "failed to place machine: insufficient capacity"
   ```
2. Check if the issue is region-specific:
   ```bash
   # Try a different region
   fly machine run $IMAGE --region lhr --app $APP_NAME
   ```
3. Check current machine distribution:
   ```bash
   fly machines list --app $APP_NAME | awk '{print $3}' | sort | uniq -c
   ```
4. If region is at capacity, check alternative regions:
   ```bash
   fly platform regions
   # Pick a nearby region with capacity
   ```
---

### ALERT: Volume Attachment Failure

**Symptoms:** Machine fails to start because volume cannot attach; `fly deploy` fails with volume errors; machine shows `volume_failed` state.

**Triage steps:**
1. Check volume status:
   ```bash
   fly volumes list --app $APP_NAME
   # Look for: state = "created" (should be "attached") or "error"
   ```
2. Check if volume is attached to a running machine:
   ```bash
   fly volumes show $VOLUME_ID --app $APP_NAME
   ```
3. If volume is unattached (machine was deleted), create a new machine in the same region:
   ```bash
   fly machine run $IMAGE \
     --volume $VOLUME_ID:/data \
     --region $REGION \
     --app $APP_NAME
   ```
4. If volume is corrupted, check filesystem:
   ```bash
   fly ssh console --app $APP_NAME
   # Inside machine:
   df -h /data
   fsck /dev/vdb  # Emergency: only if unmounted
   ```

## Common Issues & Troubleshooting

### 1. Application OOM Kill

**Diagnosis:**
```bash
# Check for OOM kill events
fly logs --app $APP_NAME | grep -i "killed\|OOM\|exit code 137\|out of memory"

# Check current memory configuration
fly scale show --app $APP_NAME

# Monitor memory usage in real time
fly metrics --app $APP_NAME
# or
fly ssh console --app $APP_NAME -C "cat /proc/meminfo"
```

### 2. Image Pull Failure from Fly Registry

**Diagnosis:**
```bash
# Check deploy output for image pull errors
fly deploy --app $APP_NAME 2>&1 | grep -i "pull\|image\|registry\|unauthorized"

# Common messages:
# "Error: failed to fetch image: unauthorized"
# "Error: image not found: registry.fly.io/myapp:deployment-abc123"
# "net/http: TLS handshake timeout" (transient)

# Check if image exists in Fly registry
fly releases --app $APP_NAME
```

### 3. Wireguard / Private Network Connectivity Issues

**Diagnosis:**
```bash
# Check Wireguard tunnel status
fly wireguard status --org $ORG_NAME

# Test .internal DNS resolution
fly ssh console --app $APP_NAME
# Inside machine:
nslookup $APP_NAME.internal
ping6 $MACHINE_ID.$APP_NAME.internal

# Check peer connectivity
wg show  # Inside machine

# List all peers in the private network
fly ips private --app $APP_NAME
```

### 4. Log Streaming Gaps

**Diagnosis:**
```bash
# Check if logs are being received at all
fly logs --app $APP_NAME -f

# Look for log agent issues
fly ssh console --app $APP_NAME
# Inside:
systemctl status fly-logshipper 2>/dev/null || ps aux | grep log
```

### 5. Anycast Routing — Traffic Not Reaching a Region

**Diagnosis:**
```bash
# Check where traffic is landing
fly status --app $APP_NAME
# Look for request counts per machine

# Test from a specific region using Fly's edge
curl -I "https://$APP_NAME.fly.dev" \
  --resolve "$APP_NAME.fly.dev:443:$(dig +short $APP_NAME.fly.dev | head -1)"

# Check Anycast IP assignment
fly ips list --app $APP_NAME

# Check if machines are started in the intended regions
fly machines list --app $APP_NAME | grep -E "iad|lhr|sin|nrt"
```

### 6. Zero-Scale App Not Waking Up

**Diagnosis:**
```bash
# Check if the app has machines in stopped state
fly machines list --app $APP_NAME | grep stopped

# Check the autostop configuration
cat fly.toml | grep -A 5 "auto_stop\|min_machines\|concurrency"

# Test if wake-up is working
time curl https://$APP_NAME.fly.dev/health

# Check wake logs
fly logs --app $APP_NAME -f  # And send a request in parallel
```

## Key Dependencies

- **Fly Registry (registry.fly.io):** Container image storage; pull failures block deployments
- **Firecracker Hypervisor:** All machines run in Firecracker MicroVMs; hypervisor bugs can cause machine failures
- **NVMe Volume Storage:** Persistent data; volumes are region-specific and cannot cross regions
- **NATS (Log Streaming):** Fly uses NATS internally for log transport; NATS outage causes log gaps
- **Anycast Network:** BGP-based Anycast for IPv4/IPv6 routing; routing changes affect latency and availability
- **Wireguard:** Private network mesh between all org machines; Wireguard issues break `.internal` service discovery
- **Fly DNS (*.internal):** Internal DNS used for service-to-service communication within an org
- **Consul (Cluster State):** Used internally for distributed coordination; affects machine scheduling

## Cross-Service Failure Chains

- **NVMe volume full** → Application writes fail → App crashes with I/O error → Machine restarts → Restart loop as crash persists with full disk → Full application outage
- **Wireguard peer failure** → `.internal` DNS resolution fails → Service-to-service calls break → App returns 500s → Health checks fail → Machines pulled from traffic
- **Image pull rate limit (Docker Hub)** → `fly deploy` fails → New deployments blocked → Cannot roll out hotfixes → Platform is stuck on old image
- **All machines OOM in one region** → Machines crash and restart → During restart window, Anycast routes to other regions → Cross-region latency spike → Some requests timeout
- **Health check misconfiguration post-deploy** → All machines marked unhealthy → All traffic drained → Zero machines serving requests → Site down

## Partial Failure Patterns

- **One region degraded, others healthy:** Anycast redirects traffic to healthy regions; users in affected region see higher latency. Monitor per-region machine counts.
- **Volumes attached in one region, machines scheduled in another:** Volumes are region-specific; machines and their volumes must be in the same region. Fly scheduler should prevent this but can fail during region incidents.
- **Log gaps without service impact:** NATS or log shipper issue; app is serving requests but logs aren't appearing. Confirm app health independently of logs.
- **Health check HTTP path returning 200 but app is broken:** Health check passes but actual endpoints return errors. Separate health check from deep readiness check.
- **Zero-scale app wakes slowly during traffic spike:** First few requests timeout; subsequent requests normal. Keep min 1 machine running during expected spikes.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|---------|----------|
| Machine boot time (warm image) | < 2s | 2–5s | > 10s |
| Machine boot time (cold image pull) | < 30s | 30–60s | > 120s |
| Anycast routing latency (intra-region) | < 5ms | 5–20ms | > 50ms |
| Volume IOPS (NVMe, random 4K read) | < 3000 IOPS | 3000–8000 IOPS | > 8000 IOPS (saturated) |
| `.internal` DNS resolution | < 5ms | 5–20ms | > 50ms |
| Health check interval response time | < 500ms | 500ms–1s | > timeout value |
| `fly deploy` end-to-end (rolling) | < 2 min | 2–5 min | > 10 min |
| Log stream latency | < 2s | 2–10s | > 30s |

## Capacity Planning Indicators

| Indicator | Current Baseline | Scale-Up Trigger | Notes |
|-----------|-----------------|-----------------|-------|
| Machine count per region | — | Utilization > 70% on any machine | Add machines via `fly scale count` |
| Memory utilization p95 | — | > 80% | Scale memory before OOM kills |
| CPU utilization p95 | — | > 70% sustained | Scale to performance CPU or add machines |
| Volume storage utilization | — | > 75% | Volumes cannot be resized; plan ahead |
| Bandwidth (egress) | — | > 80% of plan | Reduce payload sizes; add CDN in front |
| Machine count for zero-scale apps | 0 (auto) | 1+ during business hours | Prevent cold starts during peak |
| Org machine limit | — | > 90% of quota | Contact Fly support to increase quota |
| IP address count | — | Near allocation limit | Allocate IPs before scaling |

## Diagnostic Cheatsheet

```bash
# Get app status overview (machines, health, IPs)
fly status --app $APP_NAME

# List all machines with their state and region
fly machines list --app $APP_NAME

# Tail live logs
fly logs --app $APP_NAME -f

# SSH into a specific machine
fly ssh console --app $APP_NAME --machine $MACHINE_ID

# Check machine resource usage (from inside machine)
fly ssh console --app $APP_NAME -C "top -b -n 1 | head -20"

# Check disk usage on a volume
fly ssh console --app $APP_NAME -C "df -h"

# List all volumes for an app
fly volumes list --app $APP_NAME

# Get machine event history
fly machine status $MACHINE_ID --app $APP_NAME

# Restart a specific machine
fly machine restart $MACHINE_ID --app $APP_NAME

# Check all secrets (names only — values are write-only)
fly secrets list --app $APP_NAME

# Run a one-off command in a new machine (ephemeral)
fly machine run $IMAGE --app $APP_NAME -C "your-command" --rm

# Check Wireguard peer status
fly wireguard status --org $ORG_NAME

# View release history
fly releases --app $APP_NAME
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|-------------------|-------------|
| Application availability (requests served) | 99.9% | 43.8 minutes | HTTP success rate from health check probe per region |
| Deploy success rate | 99% | 7.2 hours | Successful `fly deploy` / total deploys |
| Machine start time p95 | < 5s | Burn rate alert at 2x | Time from `machine start` command to `started` state |
| Volume I/O error rate | < 0.1% | 43.8 minutes | I/O errors per 1000 operations on persistent volumes |

## Configuration Audit Checklist

| Check | Expected State | How to Verify | Risk if Misconfigured |
|-------|---------------|--------------|----------------------|
| Health check configured | HTTP check on `/health` or `/healthz` | `fly.toml` `[[services.http_checks]]` | Unhealthy machine serves traffic; failing machine not drained |
| Memory limit appropriate for workload | > 1.5x average working set | `fly scale show` | OOM kills causing restart loops |
| Auto-start and auto-stop configured | Both `true` for cost optimization | `fly.toml` `[http_service]` auto_start/stop settings | Machines run idle (cost); or never wake (availability) |
| Secrets not hardcoded in Dockerfile | All secrets via `fly secrets` | Review Dockerfile for ENV with secrets | Secret exposure in image layers |
| Volumes have correct region | Same region as the machines using them | `fly volumes list` region column | Volume attachment failure on machine placement |
| Min machines > 0 for critical apps | `min_machines_running = 1` | `fly.toml` or `fly scale show` | Cold start under traffic spike; 502 on first request |
| Multi-region deployment | Machines in ≥ 2 regions | `fly machines list` region distribution | Single region failure = full outage |
| Concurrency limits set | `soft_limit` and `hard_limit` configured | `fly.toml` `[[services]]` concurrency | Machine overloaded; no backpressure signaling |
| Deploy strategy is rolling | `strategy = "rolling"` in fly.toml | `fly.toml` `[deploy]` section | Canary fails → full outage during deploy |
| SSH disabled in production | `no_ssh = true` if not needed | `fly.toml` or machine config | Lateral movement risk if machine compromised |

## Log Pattern Library

| Pattern | Source | Meaning | Action |
|---------|--------|---------|--------|
| `Killed` (exit code 137) | Machine log | OOM kill by kernel | Scale memory immediately |
| `fatal error: runtime: out of memory` | App log (Go) | Go runtime OOM | Scale memory; check for goroutine leaks |
| `FATAL ERROR: CALL_AND_RETRY_LAST Allocation failed` | App log (Node.js) | Node.js heap exhausted | Set `NODE_OPTIONS=--max-old-space-size` |
| `panic: runtime error` | App log (Go) | Go runtime panic | Fix nil pointer / out-of-bounds; check code |
| `Error response from daemon: No such image` | Deploy log | Image not found in registry | Rebuild and push image |
| `failed to pull image` | Deploy log | Registry pull failure | Check registry credentials; retry |
| `health check failed` | Machine event | Service not responding on health path | Check app startup; check health check path config |
| `host worker is at capacity` | Deploy log | Region placement failure | Try different region; contact Fly support |
| `volume not found` | Machine event | Volume attachment failed | Check volume exists in correct region |
| `connect: connection refused` | App log | Service not listening on expected port | Check PORT env var and server binding |
| `no space left on device` | App log / kernel | Volume disk full | Delete data; resize or replace volume |
| `wireguard: Handshake for peer` | System log | Wireguard tunnel establishment | Normal unless repeated failures |

## Error Code Quick Reference

| Error / Message | Context | Meaning | Resolution |
|----------------|---------|---------|-----------|
| `exit code 137` | Machine | OOM kill | Scale memory |
| `exit code 1` | Machine | Application error exit | Check app logs for crash reason |
| `no worker hosts available` | Placement | Region capacity exhausted | Try alternate region |
| `failed to place machine` | Placement | Scheduler cannot satisfy constraints | Relax region constraints |
| `volume not found` | Volume | Volume deleted or wrong region | Verify volume ID and region |
| `volume is attached to another machine` | Volume | Exclusive attach conflict | Stop the other machine first |
| `unauthorized` | Registry | Image pull auth failed | Re-authenticate; rotate registry token |
| `health check failed: 404` | Health | Health endpoint returns 404 | Fix health check path in `fly.toml` |
| `health check failed: timeout` | Health | App not responding within timeout | Increase timeout; check app startup time |
| `DNS lookup failed: .internal` | Wireguard | Internal DNS resolution failure | Check Wireguard status; restart app |
| `rate limit exceeded` | API | Too many API calls | Implement backoff in automation |
| `app not found` | API | Wrong app name or org | Verify `--app` flag and org membership |

## Known Failure Signatures

| Signature | Pattern | Root Cause | Resolution |
|-----------|---------|-----------|-----------|
| Machine restarts every ~30s | `exit code 137` in logs | OOM kill — memory limit too low | Scale memory; fix memory leak |
| Deploy hangs at "waiting for machines to be healthy" | Health checks never pass after deploy | App not binding to correct port | Check PORT env var; fix server listen address |
| Cold requests timeout, warm requests fast | p99 >> p50 latency | Zero-scale wake-up cold start | Set `min_machines_running = 1` |
| Intermittent 502 in one region only | Some requests fail, others succeed | Single machine unhealthy; Anycast routing to it | Check machine health; restart unhealthy machine |
| App starts, immediately OOMKills | Machine starts → killed → starts loop | Application loads entire dataset into memory on start | Lazy-load data; increase machine memory |
| `fly deploy` fails with image pull error | Deploy log shows unauthorized | Fly registry token expired or image deleted | Re-push image; authenticate to registry |
| `.internal` DNS works sometimes, not others | Intermittent service-to-service failures | Wireguard tunnel flapping | Reset Wireguard; check peer configuration |
| Volumes show `pending_destroy` | Cannot attach volume | Machine that was using volume destroyed incorrectly | Contact Fly support to recover volume state |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `fetch` resolves with HTTP 502 | Browser `fetch` / `axios` | Machine OOM-killed or crashed; Anycast routed to dead machine before health check drained it | `fly logs --app $APP_NAME \| grep "exit code 137\|killed"` | Scale machine memory; set `min_machines_running = 1`; configure health checks to drain quickly |
| `fetch` resolves with HTTP 503 | Browser `fetch` / `axios` | Zero-scale machine not yet awake; wake latency exceeded client timeout | `fly machines list --app $APP_NAME \| grep stopped` | Set `min_machines_running = 1`; increase client timeout; configure health check `grace_period = "30s"` |
| `fetch` resolves with HTTP 504 | Browser `fetch` / `axios` | App slow to respond; Fly proxy timed out waiting for machine | `fly logs -f --app $APP_NAME` — look for slow handler logs | Optimize slow handlers; increase `kill_timeout` in `fly.toml` |
| Connection reset (`ECONNRESET`) | Node.js `http` / `axios` | Machine restarted mid-request during rolling deploy or crash | `fly status --app $APP_NAME` — check restart counts | Use rolling deploy with proper health check; ensure graceful shutdown handles in-flight requests |
| `getaddrinfo ENOTFOUND app.internal` | Internal service client (Node.js `fetch`, Go `net/http`) | Wireguard tunnel down; `.internal` DNS not resolving | `fly ssh console --app $APP_NAME -C "nslookup $SERVICE.internal"` | Reset Wireguard: `fly wireguard reset --org $ORG_NAME`; add retry with exponential backoff |
| `ECONNREFUSED` to service port | Internal service client | Target machine started but app not yet listening on port | `fly ssh console --app $APP_NAME -C "ss -tlnp"` | Increase health check `grace_period`; ensure app binds on `0.0.0.0` not `127.0.0.1` |
| Database write fails with `no space left on device` | ORM / DB driver (Prisma, SQLAlchemy) | NVMe volume disk full | `fly ssh console --app $APP_NAME -C "df -h /data"` | Delete stale logs/temp files; plan larger volume before reaching 75% |
| Intermittent `ETIMEDOUT` on cross-region calls | Internal service mesh client | Cross-region Wireguard latency spike or packet loss | `fly ssh console --app $APP_NAME -C "ping6 -c 5 $MACHINE_ID.$APP_NAME.internal"` | Co-locate services in the same region; add circuit breaker |
| TLS handshake error on `*.fly.dev` | Browser / curl | Fly-managed cert provisioning failed or not yet issued | `curl -I https://$APP_NAME.fly.dev 2>&1 \| head -5` | Wait for cert propagation (< 2 min normally); check `fly certs list --app $APP_NAME` |
| Image pull fails silently; old version still running | Deployment pipeline | Fly registry push succeeded but pull failed on worker host | `fly releases --app $APP_NAME \| head -5` — confirm new version is live | Re-run `fly deploy`; verify image digest with `fly releases --app $APP_NAME --json` |
| Memory allocation error in app startup | App runtime (Go, Node.js, JVM) | Machine memory limit too low for application initialization | `fly logs --app $APP_NAME \| grep -i "allocation failed\|out of memory"` | Increase machine memory: `fly scale memory 1024 --app $APP_NAME` |
| Request latency spikes every ~30s | Browser performance observer | Health check endpoint doing expensive work (DB queries) | `fly checks watch --app $APP_NAME` — observe check intervals vs latency | Move health check to a lightweight dedicated handler; avoid DB calls in `/health` |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Volume disk usage creeping toward full | Disk usage > 60% and growing | `fly ssh console --app $APP_NAME -C "df -h /data"` | Days–weeks | Set up disk usage alerting; rotate logs; plan larger volume before 75% |
| Memory leak causing OOM frequency to increase | Weekly OOM kills becoming daily, then hourly | `fly logs --app $APP_NAME \| grep -c "exit code 137"` (compare across days) | Days | Take heap/memory profile before next OOM; identify unbounded cache or goroutine leak |
| CPU saturation growing under steady traffic | p95 CPU rising from 40% to 70% over weeks | `fly ssh console --app $APP_NAME -C "top -b -n 3 \| grep Cpu"` | Weeks | Profile hot code paths; switch to `performance` CPU kind; scale machine count |
| Image size growth slowing deploys | `fly deploy` time growing by seconds each week | `fly releases --app $APP_NAME --json \| jq '.[0:5] \| map(.createdAt)'` (compare timestamps) | Weeks | Audit `Dockerfile`; use multi-stage builds; remove dev dependencies; pin base image digest |
| Wireguard peer flapping increasing | Intermittent `.internal` resolution errors in logs | `fly logs --app $APP_NAME \| grep -c "wireguard"` | Hours–days | Reset Wireguard config; check for IP address conflicts in private network |
| Machine restart count slowly creeping up | Non-zero restart count growing over weeks | `fly machines list --app $APP_NAME \| awk '{print $1}' \| xargs -I{} fly machine status {} --app $APP_NAME` | Weeks | Investigate exit codes in logs; add readiness probe to distinguish crash from slow start |
| Health check latency rising | Check response times approaching timeout value | `fly checks watch --app $APP_NAME` | Days | Optimize health endpoint; ensure DB is not queried in health check path |
| Log stream gaps becoming more frequent | Log output disappearing for 10–30s windows | `fly logs --app $APP_NAME -f` (observe manually) | Days | Restart log-shipper by restarting machine; check NATS service on Fly status page |
| Anycast routing imbalance (one region getting more traffic) | Request distribution uneven across regions | `fly status --app $APP_NAME` — compare request counts per machine | Days | Rebalance machine counts per region; verify Anycast IP is assigned to all regions |
| Build time increasing as Dockerfile layers grow | `fly deploy` wall time growing each week | `time fly deploy --app $APP_NAME` (compare over time) | Weeks | Reorganize `Dockerfile` to cache stable layers first; use `.dockerignore`; enable BuildKit cache mounts |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Fly.io Full Health Snapshot
# Usage: APP_NAME=xxx ORG_NAME=xxx bash snapshot.sh

echo "=== Fly Platform Status ==="
curl -s https://status.flyio.net/api/v2/summary.json \
  | jq '.components[] | select(.status != "operational") | {name, status}'

echo ""
echo "=== App Overview ==="
fly status --app "$APP_NAME"

echo ""
echo "=== All Machines (state + region) ==="
fly machines list --app "$APP_NAME"

echo ""
echo "=== Health Checks ==="
fly checks list --app "$APP_NAME"

echo ""
echo "=== Volume Status ==="
fly volumes list --app "$APP_NAME"

echo ""
echo "=== Recent Releases ==="
fly releases --app "$APP_NAME" | head -10

echo ""
echo "=== Disk Usage (per machine) ==="
fly machines list --app "$APP_NAME" --json \
  | jq -r '.[].id' \
  | while read mid; do
      echo "--- Machine: $mid ---"
      fly ssh console --app "$APP_NAME" --machine "$mid" -C "df -h /data 2>/dev/null || df -h /" 2>/dev/null || echo "SSH unavailable"
    done

echo ""
echo "=== Wireguard Peer Status ==="
fly wireguard status --org "$ORG_NAME" 2>/dev/null || echo "No Wireguard peers or org not set"

echo ""
echo "=== Recent Log Tail (30 lines) ==="
fly logs --app "$APP_NAME" 2>/dev/null | tail -30
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Fly.io Performance Triage
# Usage: APP_NAME=xxx bash perf-triage.sh

echo "=== Machine CPU & Memory (live sample) ==="
fly machines list --app "$APP_NAME" --json \
  | jq -r '.[].id' \
  | while read mid; do
      echo "--- Machine: $mid ---"
      fly ssh console --app "$APP_NAME" --machine "$mid" \
        -C "top -b -n 1 \| head -12" 2>/dev/null || echo "SSH unavailable"
    done

echo ""
echo "=== Memory Usage vs Limit ==="
fly scale show --app "$APP_NAME"

echo ""
echo "=== OOM Events (last 100 log lines) ==="
fly logs --app "$APP_NAME" 2>/dev/null \
  | grep -iE "killed|OOM|exit.*137|out of memory|allocation failed" \
  | tail -20

echo ""
echo "=== Machine Restart Counts ==="
fly machines list --app "$APP_NAME" --json \
  | jq '[.[] | {id: .id, region: .region, restarts: .restart_count, state: .state}]'

echo ""
echo "=== Health Check Response Times ==="
fly checks watch --app "$APP_NAME" &
WATCH_PID=$!
sleep 15
kill $WATCH_PID 2>/dev/null

echo ""
echo "=== Cold Start Timing (request to /health) ==="
time curl -s -o /dev/null "https://$APP_NAME.fly.dev/health"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Fly.io Connection & Resource Audit
# Usage: APP_NAME=xxx ORG_NAME=xxx bash resource-audit.sh

echo "=== Secrets (names only) ==="
fly secrets list --app "$APP_NAME"

echo ""
echo "=== IP Addresses (Anycast + Private) ==="
fly ips list --app "$APP_NAME"

echo ""
echo "=== Private Network Peers ==="
fly ips private --app "$APP_NAME" 2>/dev/null || echo "Private IPs not available"

echo ""
echo "=== Port Mappings (from fly.toml) ==="
grep -A 5 "\[http_service\]\|\[\[services\]\]" fly.toml 2>/dev/null || echo "No local fly.toml"

echo ""
echo "=== Volume Details ==="
fly volumes list --app "$APP_NAME" --json \
  | jq '[.[] | {id: .id, name: .name, size_gb: .size_gb, region: .region, state: .state, attached_machine_id: .attached_machine_id}]'

echo ""
echo "=== Network Connectivity Test (internal DNS) ==="
fly ssh console --app "$APP_NAME" \
  -C "nslookup $APP_NAME.internal && echo 'Internal DNS OK'" 2>/dev/null || echo "SSH unavailable"

echo ""
echo "=== Open Ports Inside Machine ==="
fly ssh console --app "$APP_NAME" -C "ss -tlnp" 2>/dev/null || echo "SSH unavailable"

echo ""
echo "=== Org Machine Quota Check ==="
fly platform status --org "$ORG_NAME" 2>/dev/null || \
  echo "Run 'fly orgs show $ORG_NAME' to check org quota"
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Worker host CPU contention (shared Firecracker host) | p99 latency spikes without corresponding app CPU increase; jitter across all machines on the same host | `fly ssh console --app $APP_NAME -C "cat /proc/schedstat"` (compare idle vs. running time) | Request migration to a different host by stopping and restarting the machine | Use `performance` CPU kind for latency-sensitive workloads; Fly places `performance` VMs on less-dense hosts |
| NVMe volume I/O saturation | Disk reads/writes slow; database query times spike; app logs show I/O wait | `fly ssh console --app $APP_NAME -C "iostat -x 1 5"` inside machine | Move read-heavy workloads to memory caching (Redis); batch writes to reduce IOPS | Monitor volume IOPS; stay below 70% of provisioned IOPS; use write-ahead batching for high-write apps |
| Anycast routing concentration post-region failure | One region absorbs all traffic after another region's machines go down; surviving machines overloaded | `fly status --app $APP_NAME` — request counts skewed to one region | Immediately scale up machines in the overloaded region | Maintain equal machine counts across ≥ 3 regions; set `concurrency.hard_limit` to trigger backpressure |
| Wireguard connection table saturation | Internal `.internal` calls fail intermittently; new Wireguard peers fail to connect | `fly wireguard list --org $ORG_NAME \| wc -l` | Prune stale peers: `fly wireguard remove $STALE_PEER --org $ORG_NAME` | Regularly audit and remove unused Wireguard peers; avoid creating ephemeral peers that are not cleaned up |
| Docker Hub pull rate limit shared across org | `fly deploy` fails with `429 Too Many Requests` from Docker Hub | Deploy log: `toomanyrequests: You have reached your pull rate limit` | Authenticate to Docker Hub: set `FLY_DOCKER_HUB_TOKEN` secret | Mirror base images to Fly's own registry (`registry.fly.io`) to avoid Docker Hub rate limits |
| Machine count approaching org quota limit | New machines fail to create; `fly scale count` errors out | `fly platform status --org $ORG_NAME` | Delete unused or stopped machines; destroy old canary machines | Monitor org machine count; contact Fly support proactively to raise quota before hitting the limit |
| Volume region capacity exhaustion | New volumes fail to create in a specific region | `fly volumes create ... --region $REGION` returns capacity error | Use a neighboring region for the new volume and co-locate the machine | Provision volumes slightly ahead of demand; monitor region-specific capacity on Fly status page |
| DNS resolution overload on `.internal` | Service-to-service calls intermittently slow; `nslookup` takes > 20ms | `fly ssh console -C "time nslookup $SERVICE.internal"` | Cache resolved addresses in-process for short TTLs (30–60s) | Use direct IPv6 addresses for critical hot paths instead of DNS when latency is critical |
| Log aggregation NATS backpressure | `fly logs -f` shows gaps or stops; machines appear healthy but logs missing | Fly status page — check log streaming component status | Machine restart resets log-shipper agent; use external log drain (Papertrail, Datadog) | Configure a log drain via `fly logs` destination for external persistence independent of NATS |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Fly region becomes unreachable | Anycast routing re-directs all traffic to surviving regions; surviving machines overloaded; health checks fail across entire app | All users in affected region; potential total outage if only 1 region | `fly status --app $APP_NAME` shows machines in one region all `stopped`; global latency spike in logs | `fly scale count 4 --region iad,ord` to add machines in healthy regions immediately |
| Wireguard BGP route withdrawal | Internal `.internal` DNS resolution fails; service-to-service calls return `connection refused`; apps that depend on private networking break | All apps using `$APP.internal` DNS for inter-service calls | `fly ssh console --app $APP_NAME -C "nslookup backend.internal"` times out; app logs show `dial tcp: lookup backend.internal: no such host` | Switch to hardcoded IPv6 private addresses; restart Wireguard peer: `fly wireguard reset --org $ORG` |
| NVMe volume detach / corruption | App machine crashes; persistent state lost; stateful app (PostgreSQL, Redis) refuses to start | Single machine / single app with volumes; data loss risk | `fly volumes list --app $APP_NAME` shows volume in `detaching` state; app logs: `failed to open database: no such file or directory` | Restore from snapshot: `fly volumes create --snapshot-id $SNAP_ID --region $REGION` |
| Upstream Docker registry unavailable during deploy | `fly deploy` fails mid-rollout; old machines stopped but new machines fail to start; partial outage | App capacity reduced mid-deploy; traffic served by fewer machines | Deploy log: `failed to fetch image: context deadline exceeded`; `fly status` shows machines in `failed` state | `fly deploy --image registry.fly.io/$APP:$LAST_GOOD_TAG` to redeploy from Fly's internal registry cache |
| Fly API (api.fly.io) outage | All management plane operations fail; `fly deploy`, `fly scale`, `fly secrets set` all hang | Operator ability to remediate any issue; apps continue running but cannot be changed | `curl -sf https://api.fly.io/healthz` fails; `fly status` returns `connection refused` | Pre-cache machine restart via `fly machine restart $MACHINE_ID` queued before outage; wait for API restoration |
| App process OOM-killed in loop | Machine restarts repeatedly; `/healthcheck` never becomes healthy; Fly marks machine as failed | Single machine first; if all machines OOM simultaneously, full app outage | `fly logs --app $APP_NAME` shows `Out of memory: Kill process`; `fly status` shows machine restart count incrementing | `fly scale memory 1024 --app $APP_NAME` to increase RAM; `fly machine update --memory 2048 $MACHINE_ID` |
| TLS certificate renewal failure | HTTPS requests fail with `SSL_ERROR_RX_RECORD_TOO_LONG`; cert expired | All external HTTPS traffic to the app | `fly certs check --app $APP_NAME` shows `Awaiting configuration`; browser shows certificate expired error | `fly certs add $DOMAIN --app $APP_NAME` to force re-issue; verify DNS CNAME pointing to `$APP.fly.dev` |
| Private network peer certificate expiry | Wireguard tunnels drop silently; inter-app communication fails with timeouts | All apps using private networking across the org | `fly wireguard list --org $ORG` shows expired peer; app logs show connection timeouts on `.internal` addresses | `fly wireguard remove $EXPIRED_PEER --org $ORG && fly wireguard create --org $ORG $REGION $PEER_NAME` |
| Machine concurrent limit (org quota) exceeded | New machine spawning fails during auto-scaling or deploy; `fly scale count` returns quota error | Deploy and scaling operations across all apps in org | `fly deploy` log: `error creating machine: quota exceeded`; `fly platform status --org $ORG_NAME` shows usage at limit | Delete stopped/unused machines: `fly machines list --app $APP --state stopped | xargs fly machine destroy` |
| BGP black-hole after Fly anycast prefix withdrawal | Traffic destined for Fly IPs drops at ISP level; app unreachable globally | Total public traffic to the app; `.internal` traffic unaffected | External monitoring (Pingdom/BetterUptime) triggers; `curl -v https://$APP.fly.dev` times out at TCP level | Failover DNS to backup origin (Cloudflare proxy / CDN fallback); wait for Fly network restoration |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| `fly deploy` with broken health check config | New machines never become healthy; deploy hangs; old machines keep serving (if `strategy: rolling`) | 2–5 min (health check wait timeout) | `fly logs` shows `health check failed`; `fly status` shows machines in `starting` → `failed` loop | `fly deploy --image registry.fly.io/$APP:$PREV_TAG` to revert to last known-good image |
| `fly.toml` port / service section change | External traffic stops reaching app; machines run but 502 from GCLB | Immediate on next deploy | `fly services list --app $APP_NAME` shows unexpected port mapping; compare `fly.toml` git diff | Revert `fly.toml` service section; redeploy |
| Volume mount path change in `fly.toml` | App starts but data directory empty; app treats fresh state as new install | Immediate on deploy | App logs: `database not found, initializing fresh`; `fly ssh console -C "ls /data"` shows empty mount | Revert mount path in `fly.toml`; existing volume still has data at original path |
| Secret variable rename / removal | App starts but fails authentication to downstream service; silent runtime failures | Immediate on next machine restart | App logs show auth errors; correlate with `fly secrets list` showing removed variable | `fly secrets set OLD_VAR_NAME=value --app $APP_NAME` to restore |
| Fly machine size downgrade (RAM reduction) | App OOM-killed shortly after deploy; worse under load | Minutes to hours depending on traffic | `fly logs` shows `Killed` or OOM messages; correlate with `fly config show` size change | `fly scale memory 2048` or `fly machine update --vm-size performance-2x $MACHINE_ID` |
| `[http_service].force_https` toggle off | HTTP traffic no longer redirected; mixed content warnings; cookies without `Secure` flag sent over HTTP | Immediate | Browser dev tools show HTTP requests; compare `fly.toml` git diff | Re-enable `force_https = true` in `fly.toml`; redeploy |
| Region removal from `fly.toml` `[deploy].regions` | Machines in removed region not replaced; capacity reduced silently; latency increases for regional users | Next deploy | `fly status` shows fewer machines post-deploy; correlate with `fly.toml` regions diff | Add region back to `fly.toml` and redeploy; `fly scale count 2 --region $REGION` |
| Dockerfile `CMD` / `ENTRYPOINT` change | App crashes immediately on start with `exec format error` or wrong entrypoint | Immediate on deploy | `fly logs` shows process exit code 1 or `exec format error`; new machines fail all health checks | `fly deploy --image registry.fly.io/$APP:$PREV_TAG` to roll back image |
| `concurrency.hard_limit` reduction | Requests rejected with 503 under previously-acceptable load; `Too many connections` in logs | Under load (minutes after deploy) | Fly metrics show `queue_depth` > 0; correlate with `fly.toml` concurrency config change | Increase `hard_limit` back; redeploy; or `fly scale count` to add machines |
| Wireguard peer update / rotation | Inter-app communication breaks silently for peers using old credentials | Immediate for new connections | App logs show timeouts to `.internal` addresses; `fly wireguard list` shows peer with updated config | Remove and recreate peer: `fly wireguard remove $PEER && fly wireguard create $ORG $REGION $PEER` |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Volume detached while app writing | `fly volumes list --app $APP_NAME --json \| jq '.[].state'` | App crash; filesystem errors in logs: `EIO: i/o error`; data at attach point missing | Data written after detach lost; database corruption if mid-transaction | Stop machine; reattach volume: `fly machine update $MACHINE_ID --volume $VOL_ID:/data`; run `fsck` if ext4 |
| Two machines sharing same volume (misconfiguration) | `fly volumes list --app $APP_NAME --json \| jq '.[].attached_machine_id'` | Filesystem corruption; SQLite WAL conflicts; PostgreSQL `could not lock file "postmaster.pid"` | Data corruption; both machines may crash | Immediately stop one machine; run `fsck`; restore from snapshot if corruption detected |
| Config drift between machines after partial deploy | `fly machine exec $MACHINE_ID -- env \| sort > /tmp/a; fly machine exec $MACHINE2_ID -- env \| sort > /tmp/b; diff /tmp/a /tmp/b` | One machine behaves differently; intermittent errors for subset of requests | Non-deterministic responses; A/B inconsistency in stateful behavior | Full redeploy: `fly deploy --strategy=immediate` to force all machines to new config simultaneously |
| Stale DNS resolution for `.internal` after machine replacement | `fly ssh console --app $APP_NAME -C "nslookup backend.internal"` returns old IPv6 | Service calls succeed but reach terminated/replaced machine; intermittent timeouts | Intermittent failures for requests routed to dead IP | `fly machine restart` on affected consumers; Fly internal DNS TTL is 60s — wait for propagation |
| Secret version skew (rolling deploy with secret change) | Check timestamps: `fly secrets list --app $APP_NAME` vs machine creation times | Half of machines use old secret value; half use new; auth fails for subset of requests | 50% error rate on authentication-dependent endpoints | Complete rolling deploy before changing secrets; use atomic redeploy: `fly deploy --strategy=immediate` |
| Fly Postgres replica lag | `fly postgres connect --app $PG_APP -C "SELECT now() - pg_last_xact_replay_timestamp() AS lag;"` | Read replicas returning stale data; app reads diverge from writes | Stale reads; potential double-spend or visibility bugs in critical workflows | Route all writes and critical reads to primary; use `target_session_attrs=read-write` in connection string |
| Fly Postgres split-brain after failover | `fly postgres status --app $PG_APP` shows multiple primaries | Write conflicts; duplicate primary nodes; app writing to both | Data inconsistency; potential data loss | `fly postgres failover --app $PG_APP` to force leader election; manually demote old primary |
| LiteFS cluster quorum loss | LiteFS logs: `unable to elect primary: no quorum`; `fly logs --app $APP_NAME` | All LiteFS nodes refuse writes; app read-only or offline | Complete write unavailability for LiteFS-backed app | Restart LiteFS consul: `fly machine restart $CONSUL_MACHINE_ID`; manually promote replica if consul unavailable |
| Multi-region sticky session routing failure | Compare region header: `curl -I https://$APP.fly.dev \| grep fly-region` vs expected region | User session lost after region re-route; login loops; cart data missing | User experience degradation; session-dependent workflows broken | Force region pinning: set `fly-prefer-region` header in app; or use centralized session store (Redis/Upstash) |
| Environment variable injection race at startup | App reads env var before Fly secrets injected: `fly logs` shows `env var not set` immediately at startup | App crashes before health check; missing config values at process start | Machine stuck in restart loop; unable to serve traffic | Add startup delay or env-check loop in `CMD`; use `fly secrets deploy` to ensure secrets applied before deploy |

## Runbook Decision Trees

### Decision Tree 1: Machine Crash-Looping / App Unavailable

```
Is `fly status --app $APP_NAME` showing all machines as "started"?
├── YES → Is HTTP health check passing? (curl -sf https://$APP_NAME.fly.dev/health)
│         ├── YES → Check Fly Anycast routing lag: dig +short $APP_NAME.fly.dev
│         │         (if IPs stale, wait 60s for DNS TTL or redeploy)
│         └── NO  → App process is up but unhealthy → inspect app-level logs:
│                   `fly logs --app $APP_NAME | tail -100`
│                   → Fix: address application error, then `fly deploy --strategy=rolling`
└── NO  → Are machines in "failed" or "stopped" state?
          `fly status --app $APP_NAME --json | jq '.[] | select(.state!="started")'`
          ├── YES → Is it a single region? (check .region field in JSON)
          │         ├── YES (region outage) → Scale to healthy region:
          │         │   `fly scale count 4 --region iad --app $APP_NAME`
          │         └── NO (global failure) → Check image pull:
          │             `fly logs --app $APP_NAME | grep "failed to pull"`
          │             → Roll back: `fly deploy --image registry.fly.io/$APP_NAME:$PREV_TAG`
          └── NO  → Machines stuck in "replacing" or "starting"?
                    `fly machine restart $MACHINE_ID --app $APP_NAME`
                    ├── If OOM kill: increase memory: `fly scale memory 512 --app $APP_NAME`
                    └── If repeated failure: escalate to Fly.io support with `fly doctor --app $APP_NAME` output
```

### Decision Tree 2: Fly Postgres Replication Lag / Failover

```
Is Fly Postgres primary accessible?
`fly postgres connect --app $PG_APP -c "SELECT pg_is_in_recovery();"` returns 'f'?
├── YES (primary up) → Is replication lag acceptable?
│   `fly postgres connect --app $PG_APP -c "SELECT * FROM pg_stat_replication;"`
│   ├── Lag < 1MB → Normal; monitor replication slot bloat:
│   │   `fly postgres connect --app $PG_APP -c "SELECT slot_name, pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn) FROM pg_replication_slots;"`
│   └── Lag > 10MB → Replica overloaded or network partition
│       → Check replica machine: `fly status --app $PG_APP`
│       → Restart replica machine: `fly machine restart $REPLICA_ID --app $PG_APP`
└── NO (primary down or in recovery) → Automatic failover triggered?
    `fly postgres status --app $PG_APP`
    ├── YES (new primary elected) → Update app connection string if needed:
    │   `fly secrets set DATABASE_URL=$(fly postgres show --app $PG_APP --json | jq -r '.ConnectionString')`
    └── NO (no leader) → Manual failover:
        `fly postgres failover --app $PG_APP`
        → Verify: `fly postgres connect --app $PG_APP -c "SELECT pg_is_in_recovery();"`
        → Escalate to Fly.io support if failover fails after 5 min
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Machine count explosion | Autoscale misconfiguration or runaway deploy loop | `fly status --app $APP_NAME --json \| jq 'length'` | Billing overrun; resource exhaustion | `fly scale count 2 --app $APP_NAME` to cap machines | Set `max_machines_running` in `fly.toml`; audit autoscale policies |
| Volume storage overrun | Log/data write without rotation | `fly volumes list --app $APP_NAME --json \| jq '.[].size_gb'` | Storage billing spike | `fly ssh console --app $APP_NAME -C "df -h"` then clear stale data | Configure log rotation; set application-level storage quotas |
| Bandwidth egress spike | CDN bypass; DDoS; bulk data export | `fly metrics --app $APP_NAME` (check `fly_net_egress_bytes`) | Bandwidth cost overrun | Enable Fly Shield WAF; throttle endpoint in app; block IPs via `fly ips list` | Use Fly's built-in edge caching; add rate limiting middleware |
| GPU machine left running | Dev/test machine not stopped after use | `fly machines list --app $APP_NAME --json \| jq '.[] \| select(.config.guest.gpu_kind!=null)'` | Very high per-minute GPU billing | `fly machine stop $MACHINE_ID --app $APP_NAME` immediately | Use `auto_stop_machines = true` in `fly.toml`; set billing alerts |
| Fly Postgres WAL accumulation | Long-running transactions or idle replication slots | `fly postgres connect --app $PG_APP -c "SELECT pg_size_pretty(pg_wal_directory_size());"` | Disk full → Postgres crash | Drop idle slots: `SELECT pg_drop_replication_slot('slot_name');` | Monitor WAL size; set `max_slot_wal_keep_size` in `postgresql.conf` |
| Redundant release artifact buildup | CI deploys many releases without pruning | `fly releases --app $APP_NAME \| wc -l` | Registry storage cost | `fly releases prune --app $APP_NAME --keep 5` | Automate release pruning in CI; configure Fly registry retention policy |
| Multi-region over-provisioning | Machines deployed to unused regions | `fly status --app $APP_NAME --json \| jq '.[].region' \| sort \| uniq -c` | Sustained idle machine cost | Destroy machines in unused regions: `fly machine destroy $ID --app $APP_NAME` | Define explicit region list in `fly.toml`; review region strategy quarterly |
| Secret value size abuse | Large secrets stored unnecessarily | `fly secrets list --app $APP_NAME` (count and review names) | Minor; secrets stored in encrypted store but affects env size | Move large blobs to object storage (R2/S3); reference by URL | Document secret size guidelines; enforce in PR review |
| Metrics cardinality explosion | App emitting unbounded custom metric labels | `fly metrics --app $APP_NAME` (check metric count via Prometheus endpoint) | Metrics storage cost; dashboard slowness | Remove high-cardinality labels from app instrumentation | Review metric label design; use bounded label values only |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot region routing | All traffic routed to single region despite multi-region deploy | `fly status --app $APP_NAME --json \| jq '.[].region' \| sort \| uniq -c` | `[regions]` misconfigured or anycast DNS not propagated | Add capacity in target region: `fly scale count 2 --region $REGION --app $APP_NAME`; verify `fly.toml` `[regions]` block |
| Connection pool exhaustion to Fly Postgres | App logs show "too many clients"; DB connections at max | `fly postgres connect --app $PG_APP -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"` | App not using pgBouncer; connection pool sized too small | Enable pgBouncer: `fly postgres create --initial-cluster-size 1 --vm-size shared-cpu-1x --volume-size 10`; set `pool_mode=transaction` |
| GC/memory pressure causing machine swap | Machine memory near limit; p99 latency spikes | `fly metrics --app $APP_NAME` (check `fly_instance_memory_usage_pct`); `fly ssh console --app $APP_NAME -C "cat /proc/meminfo"` | Memory limit too low for workload; GC pause under pressure | Scale memory: `fly scale memory 512 --app $APP_NAME`; add GOGC or JVM heap tuning env vars |
| Fly Machines cold-start latency | First request after idle period takes 2–10 seconds | `fly logs --app $APP_NAME \| grep "machine started"` | `auto_stop_machines = true` with `min_machines_running = 0`; machine boots from stopped state | Set `min_machines_running = 1` in `fly.toml`; use `services.http_checks` to keep machines warm |
| Slow Fly Volume I/O | High disk write latency; app logs show slow DB or file operations | `fly ssh console --app $APP_NAME -C "iostat -x 1 5"` | NVMe volume contention; single-threaded workload saturating volume I/O | Migrate to higher IOPS machine (`performance-1x`); split writes across volumes |
| CPU steal on shared-cpu machines | Intermittent latency spikes on `shared-cpu` VMs | `fly ssh console --app $APP_NAME -C "top -b -n 3 \| grep '%Cpu'"` (check `st` steal) | Noisy neighbor on shared host; workload exceeding burstable CPU credit | Upgrade to `performance-1x` or `performance-2x` machine type: `fly scale vm performance-1x --app $APP_NAME` |
| Thread pool saturation | HTTP 503 under load; request queuing visible in logs | `fly logs --app $APP_NAME \| grep -E "queue full\|worker timeout\|503"` | Puma/Gunicorn/Nginx worker count too low; blocking I/O in handlers | Increase workers: set `WEB_CONCURRENCY` env var; `fly secrets set WEB_CONCURRENCY=4 --app $APP_NAME` |
| Serialization overhead on large payloads | High CPU on `fly_instance_cpu_utilization`; response time proportional to payload size | `fly metrics --app $APP_NAME`; `fly logs --app $APP_NAME \| grep "slow"` | JSON serialization of large objects without streaming | Add response streaming or pagination; use `msgpack` or `protobuf` for internal APIs |
| Batch size misconfiguration in background jobs | Background workers process tiny batches; high overhead per job cycle | `fly logs --app $APP_NAME \| grep -E "processed\|batch"` (check items per cycle) | Default batch size too small for workload; per-job overhead dominates | Increase batch size via env var: `fly secrets set BATCH_SIZE=500 --app $APP_NAME`; restart workers |
| Downstream dependency latency (Fly Postgres replica lag) | App reads return stale data; replica query latency high | `fly postgres connect --app $PG_APP -c "SELECT client_addr, state, sent_lsn, replay_lsn, (sent_lsn - replay_lsn) AS lag FROM pg_stat_replication;"` | Replica falling behind under write load; replica machine undersized | Upgrade replica VM: `fly machine update $REPLICA_ID --vm-size performance-1x --app $PG_APP`; route read queries to primary under heavy load |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry | Browser shows certificate expired; `fly certs list --app $APP_NAME` shows expired cert | Fly-managed cert not auto-renewed due to DNS misconfiguration | All HTTPS traffic fails; site unreachable | `fly certs check $DOMAIN --app $APP_NAME`; delete and re-add cert: `fly certs remove $DOMAIN --app $APP_NAME && fly certs add $DOMAIN --app $APP_NAME` |
| mTLS rotation failure between Fly services | Internal service-to-service calls return 401/TLS error after cert rotation | `fly logs --app $APP_NAME \| grep -E "certificate\|TLS\|handshake"` | Internal API calls fail; service mesh broken | Re-provision WireGuard peer: `fly wireguard reset --org $ORG`; verify `fly.toml` `[services.tls_options]` |
| DNS resolution failure for custom domain | App returns 404; `dig $CUSTOM_DOMAIN` shows wrong or missing CNAME | Custom domain CNAME not pointing to `$APP_NAME.fly.dev` | Custom domain traffic fails; affects all users on that domain | Verify DNS: `dig CNAME $CUSTOM_DOMAIN`; update CNAME to `$APP_NAME.fly.dev` at registrar; `fly certs check $DOMAIN` |
| TCP connection exhaustion to upstream | App logs show "connection refused" or "too many open files" to upstream services | `fly ssh console --app $APP_NAME -C "ss -s"` (check estab count); `ulimit -n` | Upstream connection failures; 502/503 errors | Increase file descriptor limit in Dockerfile: `RUN ulimit -n 65536`; add connection pooling; `fly scale vm` to reduce per-machine load |
| Fly proxy load balancer misconfiguration | Requests not reaching correct machines; sticky sessions failing | `fly logs --app $APP_NAME \| grep -E "no healthy upstream\|balancer"` | Some requests return 502; session affinity broken | Verify `[services]` `internal_port` in `fly.toml` matches app listen port; `fly deploy --strategy rolling --app $APP_NAME` |
| Packet loss between Fly regions | Elevated latency and retransmits on inter-region calls | `fly ssh console --app $APP_NAME -C "ping -c 20 $PEER_MACHINE_IP"` (check loss%); `fly wireguard ping --org $ORG` | Degraded replication and cross-region RPC performance | Check Fly status page; reset WireGuard: `fly wireguard reset --org $ORG`; re-establish peering |
| MTU mismatch on WireGuard tunnel | Large requests fail or hang; small requests succeed | `fly ssh console --app $APP_NAME -C "ping -s 1400 -M do $PEER_IP"` (check ICMP fragmentation needed) | Intermittent failures for large payloads over WireGuard tunnels | Lower MTU: set `MTU = 1280` in WireGuard config; `fly wireguard reset --org $ORG` |
| Firewall rule change blocking egress | App cannot reach external services; new outbound connections timeout | `fly ssh console --app $APP_NAME -C "curl -v --max-time 5 https://external-service.com"` | External API integrations break | Check Fly.io status page; verify egress via `fly doctor --app $APP_NAME`; use Fly's outbound proxy if needed |
| SSL handshake timeout from Fly edge | Clients report TLS negotiation timeout; `fly logs` shows no incoming connection | `curl -v --connect-timeout 10 https://$DOMAIN 2>&1 \| grep -E "SSL\|TLS\|handshake"` | All HTTPS traffic blocked at Fly edge | `fly certs check $DOMAIN --app $APP_NAME`; verify cert is valid: `fly certs list --app $APP_NAME`; contact Fly.io support if cert healthy |
| Connection reset on long-lived WebSocket | WebSocket clients disconnect after ~60s idle | `fly logs --app $APP_NAME \| grep -E "reset\|websocket\|close"` | WebSocket connections dropped; real-time features break | Set `services.http_options.idle_timeout = 3600` in `fly.toml`; add application-level WebSocket ping/pong heartbeat |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill | Machine restarts; `fly logs` shows "out of memory" or "killed" | `fly logs --app $APP_NAME \| grep -iE "oom\|killed\|out of memory"` | `fly scale memory 512 --app $APP_NAME`; identify leak with heap profiler | Set memory limit with headroom; add OOM alerting via `fly metrics` |
| Fly Volume disk full (data partition) | App writes fail; database errors; volume at 100% | `fly ssh console --app $APP_NAME -C "df -h /data"` | `fly volumes extend $VOLUME_ID --size-gb 20 --app $APP_NAME`; clear stale data | Monitor volume usage; alert at 80%; use `auto_extend_size_gb` in `fly.toml` |
| Fly Volume disk full (log partition) | App log writes failing; `/var/log` full | `fly ssh console --app $APP_NAME -C "df -h /var/log"` | `fly ssh console --app $APP_NAME -C "journalctl --vacuum-size=100M"` | Configure log rotation in app; pipe logs to external log shipper |
| File descriptor exhaustion | "Too many open files" errors; new connections refused | `fly ssh console --app $APP_NAME -C "cat /proc/$(pgrep -n $PROC)/fdinfo \| wc -l"` | `fly machine restart $MACHINE_ID --app $APP_NAME` as immediate relief; fix leak in app | Set `nofile` ulimit in Dockerfile; implement FD leak detection |
| Inode exhaustion on Fly Volume | Disk shows free space but writes fail with "no space left" | `fly ssh console --app $APP_NAME -C "df -i /data"` | Delete small files accumulating in temp directories: `fly ssh console --app $APP_NAME -C "find /data/tmp -mtime +1 -delete"` | Audit for unbounded small file creation; implement file lifecycle management |
| CPU throttle on shared machine | p95 latency degrades; CPU steal visible in `top` | `fly metrics --app $APP_NAME` (check `fly_instance_cpu_utilization`); `fly ssh console --app $APP_NAME -C "mpstat 1 5"` | `fly scale vm performance-1x --app $APP_NAME` to move off shared CPU | Profile CPU usage; use `performance-*` machine types for latency-sensitive apps |
| Swap exhaustion | Machine severely degraded; high disk I/O from swap | `fly ssh console --app $APP_NAME -C "free -h; cat /proc/meminfo \| grep Swap"` | `fly machine restart $MACHINE_ID --app $APP_NAME`; immediately scale memory | Machines with adequate RAM should not swap; upgrade VM memory to eliminate swap use |
| Kernel PID/thread limit | App cannot fork workers; "resource temporarily unavailable" on thread creation | `fly ssh console --app $APP_NAME -C "cat /proc/sys/kernel/pid_max; ps aux \| wc -l"` | Restart machine; reduce thread count in app config | Tune `pids_limit` in machine config; monitor thread count via metrics |
| Network socket buffer exhaustion | TCP connections drop; kernel logs show socket buffer errors | `fly ssh console --app $APP_NAME -C "ss -s \| grep -E 'closed\|timewait'"` (high TIME_WAIT count) | `fly machine restart $MACHINE_ID --app $APP_NAME`; enable `SO_REUSEADDR` in app | Tune `net.core.somaxconn` and `net.ipv4.tcp_tw_reuse` via Dockerfile `sysctl` |
| Ephemeral port exhaustion | Outbound connections to Fly Postgres or external APIs fail; "cannot assign address" | `fly ssh console --app $APP_NAME -C "ss -s \| grep TIME-WAIT"` (excessive TIME_WAIT) | Restart app machine; implement connection pooling to reduce new connections | Use persistent connection pooling (pgBouncer, HTTP keep-alive); set `net.ipv4.ip_local_port_range` wider |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation causing duplicate writes | App processes same webhook or job twice after Fly machine restart; duplicate records in DB | `fly postgres connect --app $PG_APP -c "SELECT id, count(*) FROM events GROUP BY id HAVING count(*) > 1;"` | Duplicate orders, charges, or records in application database | Add unique constraint or idempotency key column; replay events through deduplication layer |
| Saga/workflow partial failure after Fly machine restart | Multi-step workflow interrupted mid-flight when machine restarts; steps 1-2 done, step 3 missing | `fly logs --app $APP_NAME \| grep -E "workflow\|saga\|step.*failed"` | Inconsistent application state; orphaned resources (e.g., charge without fulfillment) | Implement saga log in Fly Postgres; query incomplete sagas: `fly postgres connect --app $PG_APP -c "SELECT * FROM saga_log WHERE completed = false AND created_at < NOW() - INTERVAL '5 minutes';"` |
| Message replay causing data corruption | Background worker consumes Redis/queue message multiple times after crash; state mutated repeatedly | `fly ssh console --app $APP_NAME -C "redis-cli -u $REDIS_URL LLEN dead_letter_queue"` | Corrupted aggregate state; incorrect counters or balances | Implement at-least-once delivery with idempotency keys; check `fly logs` for repeated job IDs; roll back corrupted records |
| Cross-service deadlock across Fly apps | Two Fly apps each waiting on the other's Postgres lock; both time out | `fly postgres connect --app $PG_APP -c "SELECT pid, wait_event, query FROM pg_stat_activity WHERE wait_event_type = 'Lock';"` | Both dependent services hang; cascading timeout failures | Terminate blocking queries: `fly postgres connect --app $PG_APP -c "SELECT pg_terminate_backend($PID);"` ; enforce lock ordering in application code |
| Out-of-order event processing after Fly autoscale | Multiple autoscaled machines consume events from shared queue in different order; later events processed before earlier ones | `fly logs --app $APP_NAME \| grep -E "event_id\|sequence\|out.of.order"` | Stale writes overwrite newer data; incorrect final state | Add sequence numbers to events; use `SELECT ... FOR UPDATE SKIP LOCKED` in Postgres for ordered processing |
| At-least-once delivery duplicate from Fly machine preemption | Fly preempts machine mid-job; job re-queued and processed again by new machine | `fly logs --app $APP_NAME \| grep "machine preempted\|job requeued"` | Duplicate side effects (emails, charges, external API calls) | Store job completion status in Fly Postgres before acknowledging queue; check completion before re-executing |
| Compensating transaction failure after Fly Postgres failover | Postgres failover mid-saga leaves compensating transactions unexecuted; rollback incomplete | `fly postgres connect --app $PG_APP -c "SELECT * FROM saga_log WHERE step = 'compensate' AND status = 'pending';"` | Partial refunds, dangling allocations, or unreleased locks | Manually execute pending compensation steps; add saga recovery job that runs on startup: `fly logs --app $APP_NAME \| grep "saga recovery"` |
| Distributed lock expiry mid-operation | Fly machine slow due to CPU steal; Redis lock TTL expires before operation completes; second machine acquires same lock | `fly ssh console --app $APP_NAME -C "redis-cli -u $REDIS_URL TTL $LOCK_KEY"` (check remaining TTL vs operation duration) | Two machines execute same critical section concurrently; data corruption | Extend lock TTL to 3× max operation duration; implement fencing tokens; detect lock loss mid-operation and abort |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor on shared-cpu machine | `fly ssh console --app $APP_NAME -C "top -b -n 3"` shows high `st` steal; latency spikes for specific tenant | Tenant A's workload latency 2–5× degraded | `fly scale vm performance-1x --app $APP_NAME` to move off shared CPU | Upgrade all latency-sensitive tenant apps to `performance-*` machine types |
| Memory pressure from adjacent Fly machine | App logs show OOM kills; `fly metrics` shows `fly_instance_memory_usage_pct` near 100% | Tenant app crashing repeatedly; data loss risk | `fly machine restart $MACHINE_ID --app $APP_NAME` to reschedule on different host | `fly scale memory 1024 --app $APP_NAME`; set conservative memory limits per tenant app |
| Disk I/O saturation on shared Fly Volume host | `fly ssh console --app $APP_NAME -C "iostat -x 1 5"` shows `%util` near 100%; read/write latency high | Tenant DB operations slow; timeouts | Move to dedicated volume: `fly volumes create data --size 20 --region $REGION --app $APP_NAME` | Migrate tenant to a freshly created volume on a different host; schedule heavy I/O during off-peak |
| Network bandwidth monopoly | `fly metrics --app $APP_NAME` shows excessive `fly_edge_http_response_bytes_total` from one tenant's app | Other apps on same host experience network latency | Rate-limit at app level: `fly secrets set RATE_LIMIT_MBPS=100 --app $NOISY_APP_NAME` | Implement per-tenant egress rate limiting in app; consider dedicated Fly org for high-bandwidth tenants |
| Connection pool starvation to shared Fly Postgres | `fly postgres connect --app $PG_APP -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"` shows one app holding all connections | Other tenant apps get `too many clients` errors | `fly postgres connect --app $PG_APP -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = '$NOISY_APP';"` | Deploy pgBouncer: `fly postgres create --initial-cluster-size 1`; assign separate Postgres instance per tenant |
| Quota enforcement gap: tenant exceeding machine count | `fly status --app $APP_NAME --json \| jq 'length'` shows machines above provisioned quota | Other tenants cannot scale up in same region due to capacity | `fly scale count $QUOTA --app $NOISY_APP_NAME` to enforce ceiling | Implement org-level machine quotas; use `fly autoscale set max=$TENANT_MAX` per tenant app |
| Cross-tenant data leak risk via shared Fly Postgres | Two tenant apps using same Postgres instance with insufficient row-level security | Tenant A queries can access Tenant B's rows if RLS not enforced | `fly postgres connect --app $PG_APP -c "SELECT * FROM pg_policies WHERE tablename = '$TABLE';"` to audit | Enable PostgreSQL RLS: `ALTER TABLE $TABLE ENABLE ROW LEVEL SECURITY`; create per-tenant policies; migrate to separate DBs |
| Rate limit bypass via multiple small apps | Tenant splits traffic across many small Fly apps to bypass per-app rate limiting | Fair-use tenants experience degraded service from org-level capacity exhaustion | `fly apps list --org $ORG --json \| jq 'length'` to detect app count anomaly | Enforce org-level app count limits; implement rate limiting at org level via Fly for Teams |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure | Prometheus shows no data for `fly_instance_*` metrics; dashboards go blank | Fly Prometheus endpoint (`/prometheus/$ORG/metrics`) requires valid Bearer token; token expired | `curl -H "Authorization: Bearer $(fly auth token)" https://api.fly.io/prometheus/$ORG/metrics \| head` | Rotate Fly API token; update Prometheus `bearer_token` in scrape config; alert on scrape target `up == 0` |
| Trace sampling gap missing incidents | Distributed trace shows no spans during incident window; errors invisible | Sampling rate set to 1% drops 99% of requests; incidents affecting < 1% of traffic missed | Check error rate directly: `fly logs --app $APP_NAME \| grep -cE "error\|ERROR\|panic"` | Set trace sampling to 100% for error paths; use head-based sampling or tail-based sampling that keeps error traces |
| Log pipeline silent drop | Application logs absent from external log shipper (Logtail/Datadog) during incident | Log drain disconnected silently; Fly log drain has no backpressure — drops if shipper is slow | `fly logs --app $APP_NAME --no-tail \| tail -100` directly (bypasses drain) | Re-add log drain: `fly logs drain list --app $APP_NAME`; test drain health; add alert on log ingestion rate drop |
| Alert rule misconfiguration | On-call not paged during high error rate | Prometheus alert using `fly_edge_http_response_status` with wrong label filter; alerts never fire | Manually test alert: `curl -s $PROMETHEUS/api/v1/query?query=fly_edge_http_response_status` and inspect labels | Audit all alert rule label matchers; add `absent()` alert to catch scrape failures; test alerts with `amtool check-config` |
| Cardinality explosion blinding dashboards | Grafana dashboards load slowly or crash; Prometheus high-cardinality metrics causing OOM | Fly metrics include per-machine labels; autoscaled fleet creates thousands of unique label combinations | Filter by app instead of machine: `fly_instance_cpu_utilization{app="$APP_NAME"}` without machine-level labels | Reduce cardinality: use recording rules to pre-aggregate per-app; drop high-cardinality labels in Prometheus `metric_relabel_configs` |
| Missing health endpoint | Fly HTTP health checks fail silently; machine restarts without alerting on-call | Fly health check returns 200 but application is functionally broken (serves stale data) | `fly ssh console --app $APP_NAME -C "curl -s localhost:$PORT/health \| jq"` to check internal health response | Implement deep health endpoint that checks DB connectivity and critical dependencies; add content validation to health check |
| Instrumentation gap in critical path | Slow Fly Postgres queries not visible in metrics; users report latency but no alert fires | `pg_stat_statements` not enabled on Fly Postgres; no slow query logging | `fly postgres connect --app $PG_APP -c "SELECT * FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;"` after enabling | Enable pg_stat_statements: `fly postgres connect --app $PG_APP -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"` |
| Alertmanager/PagerDuty outage | Fly app incident occurs; no pages sent; team unaware | Alertmanager itself down or PagerDuty integration token expired | Check alert delivery: `amtool alert query`; verify PagerDuty events at `https://events.pagerduty.com` | Add dead-man's-switch alert that pages if Alertmanager stops firing; store Fly `fly doctor` output in external monitoring |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Fly app minor version upgrade rollback | New deploy causes increased 5xx errors; health check failures after deploy | `fly logs --app $APP_NAME \| grep -cE "panic\|fatal\|error"` compared to pre-deploy baseline | `fly releases --app $APP_NAME --json \| jq '.[1].version'` then `fly deploy --image $PREVIOUS_IMAGE --app $APP_NAME` | Use `--strategy=canary` deploy; verify health before full rollout; keep previous image SHA in runbook |
| Fly Postgres major version upgrade rollback | Application gets `unsupported frontend protocol` or `password authentication failed` after upgrade | `fly postgres connect --app $PG_APP -c "SELECT version();"` | Restore from snapshot: `fly volumes snapshots --app $PG_APP`; provision new Postgres from snapshot pre-upgrade | Test upgrade on staging Postgres first; take manual snapshot before upgrade: `fly postgres backup create --app $PG_APP` |
| Schema migration partial completion | Database migration failed mid-run; some tables altered, others not; application errors on affected tables | `fly postgres connect --app $PG_APP -c "SELECT * FROM schema_migrations ORDER BY version DESC LIMIT 5;"` | Re-run migration in transactional wrapper; or restore from pre-migration snapshot | Wrap all migrations in transactions; use `migrate` tool that supports rollback; take DB snapshot before any migration |
| Rolling upgrade version skew | Old and new app versions running simultaneously; incompatible API calls between versions | `fly status --app $APP_NAME --json \| jq '.[].image_ref'` (check for mixed images) | Force all machines to new version: `fly deploy --strategy=immediate --app $APP_NAME` | Maintain backward-compatible APIs during rolling deploys; use feature flags to gate new behavior |
| Zero-downtime migration gone wrong | Blue-green deploy: traffic switched to new app but new app fails; old app already stopped | `fly status --app $APP_NAME --json \| jq '.[] \| {id, state}'` shows all machines unhealthy | Restart previous release: `fly releases list --app $APP_NAME`; `fly deploy --image $PREV_SHA --app $APP_NAME --strategy=immediate` | Keep minimum 1 old machine until new machines pass health checks; test blue-green with canary 5% before full cutover |
| Config format change breaking old machines | `fly.toml` schema change in new deploy breaks running machines with old config | `fly logs --app $APP_NAME \| grep -E "config\|invalid\|parse error"` | `git revert` the `fly.toml` change and `fly deploy`; or `fly config save --app $APP_NAME` to refresh | Validate `fly.toml` with `fly config validate --app $APP_NAME` before deploy; review Fly.io changelog for schema breaking changes |
| Data format incompatibility after app upgrade | New app version writes data in new format; old machines still running cannot read it | `fly logs --app $APP_NAME \| grep -E "unmarshal\|decode\|format"` from machines running old image | Roll back all machines: `fly deploy --image $OLD_IMAGE --app $APP_NAME --strategy=immediate` | Use versioned data formats; read old + new format before writing new format only; feature flag the new format |
| Dependency version conflict in new deploy | New `Dockerfile` pulls updated npm/pip/gem package breaking runtime; build succeeds but app crashes | `fly logs --app $APP_NAME \| grep -E "require\|module\|import.*error"` after new deploy | `fly deploy --image $PREV_IMAGE --app $APP_NAME` immediately; pin breaking dependency version | Pin all dependency versions in `package-lock.json`/`Pipfile.lock`; run integration tests in CI before `fly deploy` |

## Kernel/OS & Host-Level Failure Patterns
**Minimum cross-cutting cases to evaluate here:** OOM killer false kill, inode exhaustion, CPU steal, NTP skew affecting locks, leases, or coordination, file descriptor exhaustion, and TCP conntrack table saturation.


| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|------------------|--------------|-------------|----------------------|
| OOM killer activation on Fly machine | `fly logs --app $APP_NAME \| grep -iE "oom\|killed process\|out of memory killer"` | App memory leak or insufficient machine memory allocation | Machine restarts; in-flight requests dropped; Fly health check fails | `fly scale memory 1024 --app $APP_NAME`; add heap profiler; set `[env] GOMEMLIMIT` or JVM `-Xmx`; alert on `fly_instance_memory_usage_pct > 85` |
| Inode exhaustion on Fly rootfs or volume | `fly ssh console --app $APP_NAME -C "df -i / /data"` shows 100% inode usage | Unbounded small file creation (tmp files, log fragments, socket files) | Writes fail with "no space left on device" even when disk has free blocks | `fly ssh console --app $APP_NAME -C "find /tmp /data/tmp -type f -mtime +1 -delete"` then `fly volumes extend $VOLUME_ID --size-gb 20 --app $APP_NAME` |
| CPU steal spike on shared-cpu machine | `fly ssh console --app $APP_NAME -C "mpstat 1 10 \| grep -E 'steal\|%st'"` shows `%st > 5` | Noisy neighbor on same Fly host consuming hypervisor CPU | p95 latency degrades 2–5×; timeouts in downstream services | `fly scale vm performance-1x --app $APP_NAME` to move to dedicated vCPU; verify with `mpstat` after reschedule |
| NTP clock skew causing JWT/token failures | `fly ssh console --app $APP_NAME -C "chronyc tracking \| grep 'System time'"` shows offset > 1s | Fly VM NTP drift; chrony not syncing against Fly hypervisor time source | JWT `iat`/`exp` validation failures; TLS certificate time errors; distributed lock TTL corruption | `fly ssh console --app $APP_NAME -C "chronyc makestep"` to force sync; redeploy machine if drift persists: `fly machine restart $MACHINE_ID --app $APP_NAME` |
| File descriptor exhaustion in Fly app process | `fly ssh console --app $APP_NAME -C "cat /proc/\$(pgrep -n node)/fdinfo \| wc -l"` approaches `ulimit -n` | Connection pool leak; unclosed HTTP/DB connections; log file handles not released | New connections refused with "too many open files"; app degrades silently | `fly machine restart $MACHINE_ID --app $APP_NAME` for immediate relief; set `nofile` in Dockerfile `RUN ulimit -n 65536`; fix FD leak in app |
| TCP conntrack table full | `fly ssh console --app $APP_NAME -C "cat /proc/sys/net/netfilter/nf_conntrack_count"` equals `nf_conntrack_max` | High connection rate app (WebSocket hub, proxy) exhausting kernel conntrack slots | New TCP connections refused by kernel; app appears unreachable | `fly ssh console --app $APP_NAME -C "sysctl -w net.netfilter.nf_conntrack_max=262144"` (temporary); set in Dockerfile `CMD` via wrapper script; `fly machine restart` to apply |
| Kernel panic / unresponsive Fly machine | `fly status --app $APP_NAME --json \| jq '.[] \| select(.state=="stopped")'` shows unexpected stopped machines; `fly logs` stream goes silent | Kernel bug, OOM kill of critical process (PID 1), or hardware fault on Fly host | Machine completely unresponsive; health checks fail; traffic not served | `fly machine start $MACHINE_ID --app $APP_NAME`; if fails, `fly machine destroy $MACHINE_ID --app $APP_NAME` then `fly scale count +1 --app $APP_NAME` to replace |
| NUMA memory imbalance on large Fly machines | `fly ssh console --app $APP_NAME -C "numactl --hardware"` shows uneven memory allocation; `fly ssh console --app $APP_NAME -C "numastat"` shows high numa_miss | Multi-NUMA host with app allocating memory across nodes; common on `performance-8x` and larger | High memory latency for cross-NUMA accesses; CPU cache misses; unpredictable p99 latency | `fly ssh console --app $APP_NAME -C "numactl --interleave=all $APP_BIN"` to interleave; or `fly scale vm performance-4x` and run two machines to avoid NUMA split |

## Deployment Pipeline & GitOps Failure Patterns
**Minimum cross-cutting cases to evaluate here:** image pull failure (rate limit or auth), Helm drift, ArgoCD sync stuck, PodDisruptionBudget-blocked rollout, blue-green cutover failure, and ConfigMap or Secret drift.


| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Docker image pull rate limit from Docker Hub | `fly deploy` fails with `429 Too Many Requests` or `toomanyrequests: You have reached your pull rate limit` | `fly logs --app $APP_NAME \| grep -i "rate limit\|429\|toomanyrequests"` during deploy | Switch to authenticated pull: set `fly secrets set DOCKER_HUB_TOKEN=$TOKEN --app $APP_NAME`; re-run `fly deploy` | Migrate base images to Fly's own registry or GitHub Container Registry; authenticate Docker pulls in `fly.toml` |
| Image pull auth failure for private registry | `fly deploy` returns `unauthorized: authentication required` or `denied: access forbidden` | `fly logs --app $APP_NAME \| grep -iE "unauthorized\|forbidden\|auth"` | Re-set registry credentials: `fly secrets set REGISTRY_PASSWORD=$PASS --app $APP_NAME`; verify with `fly ssh console --app $APP_NAME -C "docker login $REGISTRY"` | Rotate registry tokens before expiry; store credentials in Fly secrets not `fly.toml`; test pull in CI before deploy |
| Helm/chart configuration drift | App behavior diverges from expected; `fly config show --app $APP_NAME` env vars differ from Helm values | `fly config show --app $APP_NAME --json \| diff - expected-config.json` | Re-apply canonical config: `fly config save --app $APP_NAME` from git-tracked `fly.toml`; `fly deploy` | Pin `fly.toml` in git; use `fly config validate --app $APP_NAME` in CI; block manual `fly secrets set` outside CI/CD pipeline |
| ArgoCD/Flux GitOps sync stuck on Fly app config | GitOps controller reports `OutOfSync` but sync never completes; Fly app not updated | `fly releases --app $APP_NAME --json \| jq '.[0].created_at'` vs expected deploy time | Manually trigger deploy: `fly deploy --config fly.toml --app $APP_NAME`; then investigate GitOps controller logs | Add `fly deploy` status check as ArgoCD/Flux post-sync hook; alert if latest Fly release SHA != git HEAD SHA |
| PodDisruptionBudget equivalent: Fly `min_machines_running` blocking rollout | Rolling deploy stalls; `fly deploy` hangs waiting for health checks that never pass | `fly status --app $APP_NAME --json \| jq '.[] \| {id, state, checks}'` shows failing checks on new machines | Lower `min_machines_running` in `fly.toml` temporarily: `fly config save --app $APP_NAME` after editing; or `fly deploy --strategy=immediate` | Set realistic health check grace periods in `fly.toml`; test new image health endpoint before deploy: `fly ssh console --app $APP_NAME -C "curl -s localhost:$PORT/health"` |
| Blue-green traffic switch failure | New (green) app fails health checks; traffic never shifts; `fly ips` shows all traffic still on blue | `fly status --app $APP_NAME --json \| jq '.[] \| select(.image_ref \| contains("green"))'` shows unhealthy machines | `fly deploy --image $BLUE_IMAGE --app $APP_NAME --strategy=immediate` to revert all machines | Use `--strategy=canary` for initial 10% traffic test before full cutover; monitor `fly metrics` error rate before completing switch |
| ConfigMap/Secret drift causing app misconfiguration | App using stale env vars after `fly secrets set`; machines not reloaded | `fly ssh console --app $APP_NAME -C "env \| grep $KEY"` on running machine vs `fly secrets list --app $APP_NAME` | Force machine restart to pick up new secrets: `fly machine restart $MACHINE_ID --app $APP_NAME` or `fly deploy` for all | Always `fly deploy` after changing secrets; do not rely on live secret injection without restart; use `fly console` to verify env before trusting behavior |
| Feature flag stuck in old state after deploy | Feature flag service (LaunchDarkly, Flagsmith) not updated; new code behind flag never activates | `fly ssh console --app $APP_NAME -C "curl -s $FLAG_SERVICE_ENDPOINT/flags/$FLAG_KEY"` to check flag value | Toggle flag manually in flag service dashboard; or `fly secrets set ENABLE_FEATURE=true --app $APP_NAME` for env-based flags | Automate flag state transitions in deploy pipeline; add flag state check as post-deploy smoke test |

## Service Mesh & API Gateway Edge Cases
**Minimum cross-cutting cases to evaluate here:** circuit breaker false positives, rate limiting on legitimate traffic, stale service discovery endpoints, mTLS rotation interruption, retry storm amplification, gRPC keepalive or max-message failures, and trace context loss.


| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|-----------|--------|------------|
| Circuit breaker false positive on Fly app | Upstream service healthy but circuit breaker open; `fly logs --app $APP_NAME \| grep "circuit.*open"` | Transient timeout spike triggered breaker threshold; breaker not resetting after recovery | All requests to downstream rejected; elevated error rate even after downstream recovers | Tune half-open probe frequency; `fly ssh console --app $APP_NAME -C "curl -s localhost:$PORT/admin/circuit-breakers"` to inspect state; manually reset via admin endpoint |
| Rate limiter hitting legitimate traffic | Legitimate users get 429s; `fly metrics --app $APP_NAME` shows `fly_edge_http_requests_total` with status 429 rising | Rate limit configured too aggressively; shared IP (NAT/VPN) customers sharing a limit | Customer-facing 429 errors; SLA breach if sustained | `fly secrets set RATE_LIMIT_RPS=500 --app $APP_NAME` to increase limit; whitelist known IPs at Fly edge; switch from IP-based to user-token-based rate limiting |
| Stale service discovery endpoints via Fly `.internal` DNS | Requests to `app-name.internal` resolving to dead machine IPs; connection timeouts | Fly DNS cache returning stale A/AAAA records after machine stop; TTL not honored by app | Intermittent connection failures to internal services; hard to diagnose without DNS tracing | `fly ssh console --app $APP_NAME -C "dig @fdaa::3 app-name.internal AAAA"` to verify live IPs; restart app DNS cache: `fly machine restart $MACHINE_ID --app $APP_NAME` |
| mTLS rotation breaking inter-service connections | `fly logs --app $APP_NAME \| grep -iE "tls\|certificate\|handshake"` shows handshake failures after cert rotation | New TLS certificate not yet trusted by peer services; rotation not atomic across all Fly apps | Inter-service calls failing; internal API errors; cascading failures | Deploy new cert to all peers before removing old: `fly secrets set TLS_CERT="$(cat new.crt)" --app $PEER_APP`; keep old cert in trust bundle during transition |
| Retry storm amplifying errors across Fly apps | Error rate spikes across multiple apps; `fly logs --app $APP_NAME \| grep -c "retry attempt"` growing rapidly | Downstream service slow; upstream apps retrying with no backoff, amplifying load on downstream | Downstream Fly app overwhelmed; entire call chain degraded; potential cascade | Implement exponential backoff with jitter; add `Retry-After` header in downstream responses; `fly scale count 3 --app $DOWNSTREAM_APP` to temporarily absorb load |
| gRPC keepalive / max message size failure | `fly logs --app $APP_NAME \| grep -iE "max.*message\|keepalive\|ping.*timeout"` | gRPC client/server keepalive mismatch or message larger than `MaxRecvMsgSize` | gRPC connections drop silently; large payloads rejected; mobile clients disconnected | `fly ssh console --app $APP_NAME -C "grpc_health_probe -addr=localhost:$GRPC_PORT"` to test; update server: `fly secrets set GRPC_MAX_MSG_SIZE=52428800 --app $APP_NAME` (50MB) |
| Trace context propagation gap between Fly apps | Distributed traces show broken spans; no parent-child links across Fly app boundaries; gaps in Jaeger/Tempo | Missing `traceparent` header forwarding in one Fly app's HTTP client | Impossible to trace full request path; MTTR increases during incidents | `fly logs --app $APP_NAME \| grep -i "traceparent\|x-trace-id"` to check propagation; add W3C trace context middleware; verify: `fly ssh console --app $APP_NAME -C "curl -H 'traceparent: 00-abc123-1' localhost:$PORT/api"` |
| Load balancer health check misconfiguration | Fly HTTP health checks pass but app is functionally broken; traffic routed to bad machines | Health endpoint returns 200 but does not verify DB connectivity or critical service state | Users hitting broken machines; partial degradation masked by healthy-looking status | Deep health check: `fly ssh console --app $APP_NAME -C "curl -s localhost:$PORT/health/deep \| jq"` verify it checks DB; update `fly.toml` `[http_service.checks]` path to `/health/deep` |
