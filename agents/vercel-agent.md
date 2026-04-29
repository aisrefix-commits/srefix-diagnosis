---
name: vercel-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-vercel-agent
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
# Vercel SRE Agent

## Role
On-call SRE responsible for the Vercel deployment platform. Owns build pipeline health, deployment lifecycle, edge function availability, domain/certificate provisioning, environment variable integrity, and platform-level protection rules. Responds to build failures, stuck deployments, edge runtime errors, DNS misconfigurations, cold-start spikes, and bandwidth limit events.

## Architecture Overview

```
Git Push / API Trigger
        │
        ▼
  Vercel Build Pipeline
  ┌──────────────────────────────────────────────────────────────┐
  │  Install Dependencies (npm/yarn/pnpm/bun)                    │
  │  ↓                                                           │
  │  Build Command (next build / vite build / etc.)              │
  │  ↓                                                           │
  │  Output Processing (.vercel/output, static files)            │
  │  ↓                                                           │
  │  Deployment Creation (immutable URL)                         │
  └──────────────────────────────────────────────────────────────┘
        │
        ▼
  Vercel Edge Network (100+ PoPs globally)
  ┌──────────────────────────────────────────────────────────────┐
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
  │  │ Static Assets│  │  Edge Fns    │  │  Serverless  │       │
  │  │ (CDN cached) │  │ (V8 isolates │  │  Functions   │       │
  │  │              │  │  Edge Runtime│  │  (Node.js    │       │
  │  │              │  │  25ms CPU)   │  │  Lambda)     │       │
  │  └──────────────┘  └──────────────┘  └──────────────┘       │
  │                                                              │
  │  Custom Domains ──▶ SSL/TLS (Let's Encrypt / custom cert)    │
  │  Preview URLs  ──▶  *.vercel.app                             │
  └──────────────────────────────────────────────────────────────┘
        │
        ▼
  Vercel KV / Postgres / Blob  (Storage integrations)
  Team / Project Settings  ──▶  Environment Variables
```

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| Build success rate | < 95% | < 85% | Per project; track error type (install vs. build) |
| Build duration p95 | > 5 min | > 15 min | Build timeout is 45 min by default |
| Serverless function error rate | > 1% | > 5% | 5xx responses from function invocations |
| Edge function CPU time p95 | > 15ms | > 22ms (near 25ms limit) | Hard limit 25ms; violating = FUNCTION_INVOCATION_TIMEOUT |
| Cold start p95 (serverless) | > 1000ms | > 3000ms | Indicates large bundle or memory-heavy init |
| Domain cert provisioning status | `pending` > 10 min | `pending` > 1 hr | Cert provisioning requires DNS to resolve first |
| Bandwidth used / limit | > 80% of plan | > 95% of plan | Overage billing or throttling depending on plan |
| Deployment queue time | > 2 min | > 10 min | Indicates build concurrency exhaustion |
| Function memory usage p95 | > 80% of limit | > 95% of limit | Default 1024 MB; configurable up to 3008 MB |
| 4xx rate on preview deployments | > 10% | > 30% | Often env var missing in preview environment |

## Alert Runbooks

### ALERT: Build Failure Spike

**Symptoms:** Multiple deployments fail; builds exit with non-zero code; deploy status shows `ERROR`.

**Triage steps:**
1. List recent failed deployments:
   ```bash
   vercel list --token $VERCEL_TOKEN | head -20
   # or via API
   curl -H "Authorization: Bearer $VERCEL_TOKEN" \
     "https://api.vercel.com/v6/deployments?projectId=$PROJECT_ID&state=ERROR&limit=10" \
     | jq '[.deployments[] | {id: .uid, url: .url, createdAt: .createdAt, meta: .meta.githubCommitMessage}]'
   ```
2. Fetch build logs for a failing deployment:
   ```bash
   vercel logs $DEPLOYMENT_URL --token $VERCEL_TOKEN
   # or
   curl -H "Authorization: Bearer $VERCEL_TOKEN" \
     "https://api.vercel.com/v2/deployments/$DEPLOYMENT_ID/events?limit=100" \
     | jq '[.[] | select(.type == "stderr" or .type == "error") | .text]'
   ```
3. Classify failure type from logs:
   - `npm ERR! code ERESOLVE` → dependency resolution conflict
   - `Error: ENOMEM` or `Killed` → OOM during build (need more memory)
   - `tsc --noEmit` TypeScript errors → type errors introduced in PR
   - `next build` exit 1 with linting errors → ESLint blocking build
   - `Cannot find module` → missing package or path alias misconfigured
4. If it's an environment variable missing in build phase, add it:
   ```bash
   vercel env add BUILD_VAR --token $VERCEL_TOKEN
   # Select: Production, Preview, Development
   ```

---

### ALERT: Deployment Stuck / Queued

**Symptoms:** Deployment stays in `QUEUED` or `BUILDING` state for > 10 minutes; no build output progress.

**Triage steps:**
1. Check current deployment status:
   ```bash
   curl -H "Authorization: Bearer $VERCEL_TOKEN" \
     "https://api.vercel.com/v13/deployments/$DEPLOYMENT_ID" \
     | jq '{status: .status, buildingAt: .buildingAt, createdAt: .createdAt}'
   ```
2. Check if build concurrency is exhausted (all builder slots occupied):
   ```bash
   curl -H "Authorization: Bearer $VERCEL_TOKEN" \
     "https://api.vercel.com/v6/deployments?teamId=$TEAM_ID&state=BUILDING&limit=20" \
     | jq 'length'
   ```
3. If a hung build is consuming a slot, cancel it:
   ```bash
   curl -X DELETE -H "Authorization: Bearer $VERCEL_TOKEN" \
     "https://api.vercel.com/v13/deployments/$STUCK_DEPLOYMENT_ID"
   ```
4. Check Vercel platform status for builder outages:
   ```bash
   curl -s https://www.vercel-status.com/api/v2/summary.json \
     | jq '.components[] | select(.status != "operational")'
   ```

---

### ALERT: Edge Function Runtime Errors

**Symptoms:** `FUNCTION_INVOCATION_FAILED` or `EDGE_FUNCTION_INVOCATION_TIMEOUT` errors; 500/503 responses from edge routes.

**Triage steps:**
1. Check function invocation errors in logs:
   ```bash
   vercel logs $DEPLOYMENT_URL --token $VERCEL_TOKEN | grep -i "error\|FUNCTION\|EDGE"
   ```
2. Check the edge function's CPU time usage:
   ```bash
   curl -H "Authorization: Bearer $VERCEL_TOKEN" \
     "https://api.vercel.com/v2/deployments/$DEPLOYMENT_ID/events?limit=200" \
     | jq '[.[] | select(.type == "error") | {text: .text, payload: .payload}]'
   ```
3. If hitting the 25ms CPU limit, identify the hot path (regex, crypto, heavy parsing) and defer to a serverless function.
4. If it's a memory error in edge runtime (128 MB limit), move to serverless.

---

### ALERT: Domain Certificate Provisioning Failure

**Symptoms:** HTTPS not working on custom domain; cert status shows `pending` or `invalid`; SSL handshake errors.

**Triage steps:**
1. Check domain configuration:
   ```bash
   vercel domains ls --token $VERCEL_TOKEN
   curl -H "Authorization: Bearer $VERCEL_TOKEN" \
     "https://api.vercel.com/v9/projects/$PROJECT_ID/domains" \
     | jq '[.domains[] | {name: .name, verified: .verified, error: .error}]'
   ```
2. Verify DNS propagation:
   ```bash
   dig CNAME www.example.com
   # Should show: cname.vercel-dns.com
   dig A example.com
   # Should show Vercel's IP: 76.76.21.21
   ```
3. If DNS is correct but cert is still pending, re-trigger verification:
   ```bash
   curl -X POST -H "Authorization: Bearer $VERCEL_TOKEN" \
     "https://api.vercel.com/v9/projects/$PROJECT_ID/domains/$DOMAIN/verify"
   ```
4. If using a CDN in front of Vercel, ensure SSL passthrough is configured correctly.

## Common Issues & Troubleshooting

### 1. Build OOM (Out of Memory) During `next build`

**Diagnosis:**
```bash
# Look for OOM signal in build logs
vercel logs $DEPLOYMENT_URL --token $VERCEL_TOKEN | grep -i "Killed\|ENOMEM\|heap\|memory"
```
Typical output: `FATAL ERROR: Ineffective mark-compacts near heap limit Allocation failed` or process `Killed`.

### 2. Environment Variable Missing in Preview Deployments

**Diagnosis:**
```bash
# Check which env vars are configured per environment
vercel env ls --token $VERCEL_TOKEN

# Check if the variable exists for preview
curl -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v9/projects/$PROJECT_ID/env" \
  | jq '[.envs[] | {key: .key, target: .target, type: .type}]'
```
Symptoms: 500 errors on preview URLs, `process.env.VAR_NAME` is `undefined` at runtime.

### 3. Serverless Function Cold Start Spikes

**Diagnosis:**
```bash
# Check function duration metrics
curl -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/deployments/$DEPLOYMENT_ID/events?limit=100" \
  | jq '[.[] | select(.type == "response") | {duration: .payload.duration, status: .payload.status}] | sort_by(-.duration) | .[0:10]'
```
High cold start = large Lambda bundle, heavy initialization (DB connections, large SDK imports).

### 4. TypeScript Build Errors Blocking CI

**Diagnosis:**
```bash
# Fetch TypeScript errors from build logs
vercel logs $DEPLOYMENT_URL --token $VERCEL_TOKEN | grep "^.*\.tsx\?([0-9]*,[0-9]*)"
```

### 5. Preview Deployment Protection Blocking Legitimate Users

**Diagnosis:**
```bash
# Check project's protection settings
curl -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v9/projects/$PROJECT_ID" \
  | jq '{passwordProtection: .passwordProtection, ssoProtection: .ssoProtection, deploymentProtection: .deploymentProtection}'
```
Symptoms: Preview URLs return `401 Unauthorized` or Vercel login prompt for external testers.

### 6. Bandwidth Limit Approaching

**Diagnosis:**
```bash
# Check team usage
curl -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/teams/$TEAM_ID/usage" \
  | jq '{bandwidthUsage: .bandwidth, bandwidthLimit: .bandwidthLimit}'
```

## Key Dependencies

- **Git Provider (GitHub / GitLab / Bitbucket):** Deployment triggers depend on webhooks from the git provider; outage breaks automatic deployments
- **npm / yarn Registry:** Build-time dependency installation; registry outages or private package auth failures break installs
- **Custom DNS Provider:** CNAME records must point to Vercel for domain routing and cert provisioning
- **Let's Encrypt:** TLS cert issuance; rate limiting (5 certs per domain per week) or ACME challenge failures delay cert provisioning
- **Vercel KV / Postgres / Blob:** Optional storage integrations; connection issues surface as runtime errors in serverless functions
- **External APIs called from serverless functions:** Outages or timeouts cause function errors
- **Vercel Edge Config:** Real-time configuration reads; Edge Config outage can cause edge function failures

## Cross-Service Failure Chains

- **npm registry outage** → `npm install` fails in all builds → Entire CI/CD pipeline blocked → All pending deployments fail
- **DNS misconfiguration after provider migration** → Cert provisioning fails → HTTPS broken on production domain → Site unreachable for HTTPS users
- **TypeScript strict mode change merged to main** → All production builds fail → Deployment blocked → Hotfix requires `ignoreBuildErrors` temporary bypass
- **Edge function CPU limit breach under traffic spike** → Edge runtime kills function → 500 errors served from CDN edge → No fallback → All edge-routed traffic fails
- **Environment variable deleted accidentally** → All function invocations fail with `undefined` errors → Production outage → Env var restoration requires redeployment
- **Build concurrency exhausted by feature branch spam** → Production hotfix deployment queued behind feature builds → Delayed critical fix delivery

## Partial Failure Patterns

- **Preview deployments fail, production succeeds:** Different environment variable configuration; check `preview` vs. `production` env targets.
- **One region's edge functions timeout:** Regional edge PoP issue; other regions serve normally. Route traffic away from the affected region.
- **Static assets cached stale post-deployment:** CDN cache not purged; add cache-busting query strings or wait for TTL expiry.
- **API routes fail, static pages serve fine:** Serverless function runtime error; static files unaffected since served from CDN.
- **Image optimization returns 500:** `next/image` hitting external image origin that is rate-limiting Vercel's optimizer IPs.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|---------|----------|
| Build time (Next.js, medium project) | < 3 min | 3–10 min | > 15 min |
| Serverless function cold start p95 | < 500ms | 500–1500ms | > 3000ms |
| Serverless function duration p95 | < 1000ms | 1–5s | > 10s (hard limit varies) |
| Edge function CPU time p95 | < 10ms | 10–20ms | > 22ms (near 25ms limit) |
| Static asset TTFB p95 | < 50ms | 50–150ms | > 300ms |
| Domain cert provisioning time | < 5 min | 5–30 min | > 60 min |
| Deployment creation to live | < 45s | 45s–2min | > 5 min |
| Edge Config read p95 | < 5ms | 5–20ms | > 50ms |

## Capacity Planning Indicators

| Indicator | Current Baseline | Scale-Up Trigger | Notes |
|-----------|-----------------|-----------------|-------|
| Bandwidth used / plan limit | — | > 80% | Upgrade plan or offload to external CDN |
| Concurrent builds in flight | — | > 60% of plan limit | Upgrade to higher concurrency plan |
| Serverless execution units | — | > 80% of monthly limit | Optimize function duration; upgrade plan |
| Edge requests / month | — | > 80% of plan limit | Review edge function usage patterns |
| Build minutes / month | — | > 80% of plan limit | Parallelize builds; use Turbopack |
| Team members / seat limit | — | Near limit | Add seats or review inactive members |
| Projects per team | — | > 200 | Review for dormant projects; archive |
| Cron job slots | — | Near plan limit | Consolidate cron jobs into fewer schedules |

## Diagnostic Cheatsheet

```bash
# List recent deployments for a project
vercel list --token $VERCEL_TOKEN

# Inspect a specific deployment
vercel inspect $DEPLOYMENT_URL --token $VERCEL_TOKEN

# Tail live logs for a deployment
vercel logs $DEPLOYMENT_URL --follow --token $VERCEL_TOKEN

# List all environment variables for a project
vercel env ls --token $VERCEL_TOKEN

# Pull env vars locally for debugging
vercel env pull .env.local --token $VERCEL_TOKEN

# Check domain DNS and cert status
vercel domains inspect $DOMAIN --token $VERCEL_TOKEN

# Cancel a running/queued build
curl -X DELETE -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v13/deployments/$DEPLOYMENT_ID"

# Get all project domains with verification status
curl -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v9/projects/$PROJECT_ID/domains" | jq .

# Check edge function config for a project
cat .vercel/output/config.json | jq '.routes[] | select(.middlewarePath != null)'

# Verify current production deployment alias
curl -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/aliases/$PRODUCTION_DOMAIN" | jq '{deployment: .deployment, alias: .alias}'

# Get team usage stats
curl -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/teams/$TEAM_ID/usage" | jq .
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|-------------------|-------------|
| Production deployment success rate | 99% (excluding user errors) | 7.2 hours | Successful deploys / total deploys triggered |
| Serverless function error rate | < 0.5% | — | 5xx / total invocations per 5-min window |
| Static asset availability | 99.99% | 4.3 minutes | Synthetic probe to production CDN every 30s |
| Build P95 duration | < 5 minutes | Burn rate alert at 3x | Rolling 24h window on build durations |

## Configuration Audit Checklist

| Check | Expected State | How to Verify | Risk if Misconfigured |
|-------|---------------|--------------|----------------------|
| All sensitive env vars encrypted | `type: "encrypted"` (not `"plain"`) | `GET /api/v9/projects/$PROJECT_ID/env` | Secret exposure in Vercel dashboard |
| Production protection enabled | Password or SSO protection on production | Project > Settings > Deployment Protection | Unauthorized access to production |
| Preview protection enabled | Vercel Authentication or team access only | Project > Settings > Deployment Protection | Sensitive data in preview deployments exposed |
| Serverless function timeout configured | Matches expected operation duration + buffer | `vercel.json` functions config | Functions killed prematurely or running too long |
| Edge functions have explicit runtime | `export const runtime = 'edge'` only where appropriate | Review route files | Unexpected CPU limit hits |
| Domain auto-renew cert | Let's Encrypt auto-renews via Vercel | Dashboard > Domains | Cert expiry → production outage |
| Build output caching enabled | Framework cache enabled (Next.js, etc.) | Project > Settings > Build Cache | Unnecessarily slow builds |
| Git branch protection covers main | Deploy hook only triggers on approved merges | Git provider settings | Broken code auto-deployed to production |
| Cron job schedule verified | Correct cron expression for intended frequency | `vercel.json` crons config | Jobs running too frequently or not at all |
| Allowed IPs for restricted projects | Accurate IP allowlist | Project > Settings > Allowed IPs | Blocking legitimate users or allowing unauthorized |

## Log Pattern Library

| Pattern | Source | Meaning | Action |
|---------|--------|---------|--------|
| `npm ERR! code ERESOLVE` | Build stderr | Dependency resolution conflict | Add `overrides` or `--legacy-peer-deps` |
| `FATAL ERROR: CALL_AND_RETRY_LAST Allocation failed` | Build stderr | Node.js heap OOM | Set `NODE_OPTIONS=--max-old-space-size=4096` |
| `FUNCTION_INVOCATION_FAILED` | Runtime log | Serverless function crashed | Check function code for uncaught exception |
| `EDGE_FUNCTION_INVOCATION_TIMEOUT` | Runtime log | Edge function exceeded 25ms CPU | Profile hot path; move to serverless |
| `Error: Missing required env var` | Runtime stderr | Environment variable undefined | Add env var in Vercel dashboard for target |
| `504 GATEWAY_TIMEOUT` | Edge log | Function timed out (10s default) | Optimize function; increase timeout in config |
| `Build failed with 1 error` | Build output | Build command non-zero exit | Read full stderr for root cause |
| `Error: Cannot find module` | Build / runtime | Missing npm package | Add to `dependencies` (not `devDependencies`) |
| `Warning: Unhandled Promise Rejection` | Runtime | Async error not caught | Add `.catch()` or `try/catch` in async function |
| `x-vercel-cache: HIT` | Response header | CDN cache hit | Normal; confirms caching is working |
| `x-vercel-cache: MISS` | Response header | CDN cache miss | Check `Cache-Control` headers |
| `x-vercel-error: DEPLOYMENT_NOT_FOUND` | Response header | Deployment alias misconfigured | Re-alias domain to correct deployment |

## Error Code Quick Reference

| Error / Status | Context | Meaning | Resolution |
|---------------|---------|---------|-----------|
| `DEPLOYMENT_NOT_FOUND` | Domain routing | Deployment was deleted or alias broken | Re-alias domain to current deployment |
| `FUNCTION_INVOCATION_FAILED` | Runtime | Serverless function crashed (uncaught error) | Check logs; fix exception in function code |
| `EDGE_FUNCTION_INVOCATION_TIMEOUT` | Edge Runtime | CPU time exceeded 25ms limit | Reduce CPU work; move heavy logic to serverless |
| `FUNCTION_INVOCATION_TIMEOUT` | Runtime | Function execution exceeded duration limit | Increase timeout in `vercel.json`; optimize function |
| `DEPLOYMENT_BLOCKED` | Build | Deployment blocked by protection rules | Check protection configuration in project settings |
| `BUILD_FAILED` | Build | Build command returned non-zero exit code | Read build logs for root cause |
| `MISSING_ENV_VAR` | Runtime | Required env var not defined for environment | Add env var in dashboard for the correct target |
| `CERT_MISSING` | Domain | TLS certificate not provisioned | Verify DNS; re-trigger cert provisioning |
| `DOMAIN_NOT_VERIFIED` | Domain | DNS records not pointing to Vercel | Update CNAME / A record at DNS provider |
| `RATE_LIMITED` | API | Too many API requests | Add backoff; reduce request frequency |
| `CONCURRENT_BUILDS_EXCEEDED` | Build | Team build concurrency cap reached | Upgrade plan; cancel queued non-critical builds |
| 413 `PAYLOAD_TOO_LARGE` | Runtime | Response body > 4.5 MB (serverless limit) | Paginate response; stream large data |

## Known Failure Signatures

| Signature | Pattern | Root Cause | Resolution |
|-----------|---------|-----------|-----------|
| All builds fail after dependency update | `ERESOLVE` in npm output | Breaking peer dependency change | Pin the dependency version; add `overrides` |
| Production returns 500 after deploy | Functions crash on first request | Missing env var in production | Add env var; redeploy |
| Preview URL works, production 404 | Static page missing | Route not included in build output | Check `next.config.js` routes; check `trailingSlash` setting |
| Edge function works locally, fails in prod | `EDGE_FUNCTION_INVOCATION_TIMEOUT` | Code using Node.js APIs not available in edge runtime | Remove `fs`, `path`, `crypto` — not available in edge |
| Deployment stuck at "Building" for 20+ min | No log output after install step | Infinite loop or waiting for stdin in build script | Add `CI=true` env var; check for interactive prompts |
| HTTPS broken after DNS migration | `ERR_CERT_COMMON_NAME_INVALID` | Old cert still cached or new cert not provisioned | Force cert re-provisioning; clear DNS cache |
| Cold starts regressed after adding dependency | p99 cold start > 3s | Heavy SDK imported at module level | Lazy-load or dynamic import the SDK |
| Image optimization returning 400 | `/_next/image` 400 errors | External image domain not in `next.config.js` | Add domain to `images.domains` or `images.remotePatterns` |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `fetch` resolves with HTTP 500 | Browser `fetch` / `axios` | Serverless function uncaught exception | `vercel logs $DEPLOYMENT_URL` — look for `FUNCTION_INVOCATION_FAILED` | Add global error boundary; return structured error responses |
| `fetch` resolves with HTTP 502 | Browser `fetch` / `axios` | Lambda cold start timeout or OOM during init | Check function duration p95 > 3s in logs | Reduce bundle size; increase function memory |
| `fetch` resolves with HTTP 504 | Browser `fetch` / `axios` | Serverless function exceeded execution timeout | `vercel logs` — look for `GATEWAY_TIMEOUT` or `Function execution timed out` | Increase `maxDuration` in `vercel.json`; optimize slow operations |
| Network request hangs then times out | Browser `fetch` / `axios` | Edge function CPU limit (25ms) exceeded at busy PoP | `vercel logs` — `EDGE_FUNCTION_INVOCATION_TIMEOUT` | Move heavy logic to serverless function; cache computed results |
| `TypeError: Failed to fetch` | Browser `fetch` | CORS headers missing from function response | Browser devtools — Network tab, preflight `OPTIONS` returns no CORS headers | Add CORS headers in function handler or `vercel.json` headers config |
| `401 Unauthorized` on preview URL | Browser / test runner | Vercel Deployment Protection blocking unauthenticated access | `curl -I $PREVIEW_URL` — response contains `x-vercel-protection-bypass` hint | Disable protection or provide bypass token for CI |
| `404 Not Found` on valid route | SPA router / Next.js `Link` | Deployment alias pointing to stale/deleted deployment | `curl -H "Authorization: Bearer $VERCEL_TOKEN" "https://api.vercel.com/v2/aliases/$DOMAIN"` | Re-alias domain to active deployment |
| `process.env.VAR` is `undefined` at runtime | Next.js / Node.js app | Environment variable not set for target environment | `vercel env ls` — check if var exists for `production` target | Add env var in dashboard; trigger redeployment |
| Image `src` returns 400 | `next/image` component | External image domain not whitelisted in `next.config.js` | Browser devtools — `/_next/image` request returns 400 with domain error | Add domain to `images.remotePatterns` in `next.config.js` |
| `SyntaxError: Unexpected token '<'` in client JS | Browser JS runtime | HTML error page returned instead of JSON API response | Open Network tab — API route returns HTML 500 page | Fix server-side exception; add content-type check in client |
| KV / Postgres connection error in function | `@vercel/kv` / `@vercel/postgres` | Storage integration connection limit or region mismatch | `vercel logs` — `connection refused` or `too many connections` | Use connection pooling; ensure function region matches storage region |
| Stale data served after deploy | SWR / React Query | CDN cache not invalidated; old response TTL still valid | `curl -I $URL` — `x-vercel-cache: HIT` with old `age` value | Set `Cache-Control: no-store` for dynamic routes; use `revalidatePath` |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Bundle size creep increasing cold start | p95 cold start rising 50–100ms per week | `vercel logs $DEPLOYMENT_URL \| jq '[.[] \| select(.type=="response") \| .payload.duration] \| add/length'` | Weeks | Audit bundle with `ANALYZE=true next build`; code-split heavy dependencies |
| Build cache invalidation becoming more frequent | Build time slowly growing despite no major changes | `curl -H "Authorization: Bearer $VERCEL_TOKEN" "https://api.vercel.com/v6/deployments?projectId=$PROJECT_ID&limit=20" \| jq '[.deployments[].buildingAt]'` | Days–weeks | Pin dependency versions; use lockfile; avoid wildcard imports that bust cache |
| Serverless function memory drift | p95 memory usage creeping toward 1024 MB limit | `vercel logs $DEPLOYMENT_URL \| grep memorySize` | Days | Profile function memory; close DB connections; avoid module-level caches that grow unbounded |
| Edge function CPU time growth | p95 CPU time rising toward 25ms limit | `vercel logs $DEPLOYMENT_URL \| jq '[.[] \| select(.payload.cpuTime != null) \| .payload.cpuTime] \| add/length'` | Days | Profile regex and JSON parsing; cache expensive computations |
| Bandwidth usage approaching plan limit | Bandwidth metric trending toward 80% of plan | `curl -H "Authorization: Bearer $VERCEL_TOKEN" "https://api.vercel.com/v2/teams/$TEAM_ID/usage" \| jq .bandwidth` | Weeks | Enable immutable cache headers; move media to Vercel Blob or external CDN |
| Build concurrency saturation during peak hours | Queue time for deployments increasing at peak | `curl -H "Authorization: Bearer $VERCEL_TOKEN" "https://api.vercel.com/v6/deployments?teamId=$TEAM_ID&state=BUILDING&limit=20" \| jq length` | Hours | Cancel non-critical feature-branch builds; upgrade plan concurrency |
| Let's Encrypt cert approaching expiry | Cert expiry date approaching 30-day warning | `openssl s_client -connect $DOMAIN:443 -servername $DOMAIN </dev/null 2>/dev/null \| openssl x509 -noout -dates` | 30 days | Verify Vercel auto-renew is enabled; confirm DNS still points to Vercel |
| Function error rate slowly rising post-deploy | 5xx rate ticking from 0.1% to 0.5% over hours | `vercel logs https://$PRODUCTION_DOMAIN --token $VERCEL_TOKEN \| grep -c "FUNCTION_INVOCATION_FAILED"` | Hours | Check if new deploy introduced memory leak or error in rarely-hit code path |
| Cron job execution drift | Cron jobs starting later than scheduled over time | `curl -H "Authorization: Bearer $VERCEL_TOKEN" "https://api.vercel.com/v9/projects/$PROJECT_ID/crons" \| jq .` | Days | Review cron expressions; check for overlapping executions blocking scheduler |
| Image optimization cache miss rate rising | `x-vercel-cache: MISS` rate increasing on image routes | `curl -I "https://$PRODUCTION_DOMAIN/_next/image?url=..." \| grep x-vercel-cache` | Days | Pre-warm image optimizer; verify external image origin is not returning varying headers |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Vercel Full Health Snapshot
# Usage: VERCEL_TOKEN=xxx PROJECT_ID=xxx TEAM_ID=xxx PRODUCTION_DOMAIN=xxx bash snapshot.sh

echo "=== Vercel Platform Status ==="
curl -s https://www.vercel-status.com/api/v2/summary.json \
  | jq '.components[] | select(.status != "operational") | {name, status}'

echo ""
echo "=== Recent Deployments (last 10) ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v6/deployments?projectId=$PROJECT_ID&limit=10" \
  | jq '[.deployments[] | {state: .state, url: .url, created: .createdAt}]'

echo ""
echo "=== Active Builds (concurrency check) ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v6/deployments?teamId=$TEAM_ID&state=BUILDING&limit=20" \
  | jq 'length'

echo ""
echo "=== Production Domain Alias ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/aliases/$PRODUCTION_DOMAIN" \
  | jq '{alias: .alias, deploymentId: .deployment.id}'

echo ""
echo "=== Domain DNS & Cert ==="
dig CNAME "www.$PRODUCTION_DOMAIN" +short
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v9/projects/$PROJECT_ID/domains" \
  | jq '[.domains[] | {name: .name, verified: .verified, certExpiry: .certs[0].expiredAt}]'

echo ""
echo "=== Team Bandwidth Usage ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/teams/$TEAM_ID/usage" \
  | jq '{bandwidth: .bandwidth, bandwidthLimit: .bandwidthLimit}'

echo ""
echo "=== Recent Live Logs (last 30 lines) ==="
vercel logs "https://$PRODUCTION_DOMAIN" --token "$VERCEL_TOKEN" 2>/dev/null | tail -30
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Vercel Performance Triage
# Usage: VERCEL_TOKEN=xxx DEPLOYMENT_ID=xxx bash perf-triage.sh

echo "=== Function Duration Distribution ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/deployments/$DEPLOYMENT_ID/events?limit=500" \
  | jq '[.[] | select(.payload.duration != null) | .payload.duration] | {
      count: length,
      avg: (if length > 0 then (add / length | floor) else 0 end),
      p50: (sort | .[length/2 | floor]),
      p95: (sort | .[((length * 0.95) | floor)])
    }'

echo ""
echo "=== Top 10 Slowest Requests ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/deployments/$DEPLOYMENT_ID/events?limit=500" \
  | jq '[.[] | select(.payload.duration != null and .payload.path != null) |
      {path: .payload.path, duration: .payload.duration, status: .payload.status}] |
      sort_by(-.duration) | .[0:10]'

echo ""
echo "=== Error Rate by Status Code ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/deployments/$DEPLOYMENT_ID/events?limit=500" \
  | jq '[.[] | select(.payload.status != null) | .payload.status] |
      group_by(.) | map({status: .[0], count: length}) | sort_by(-.count)'

echo ""
echo "=== Edge Function CPU Time Distribution ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/deployments/$DEPLOYMENT_ID/events?limit=500" \
  | jq '[.[] | select(.payload.cpuTime != null) | .payload.cpuTime] | {
      count: length,
      max: max,
      p95: (sort | .[((length * 0.95) | floor)])
    }'
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Vercel Connection & Resource Audit
# Usage: VERCEL_TOKEN=xxx PROJECT_ID=xxx TEAM_ID=xxx bash resource-audit.sh

echo "=== Environment Variables Audit ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v9/projects/$PROJECT_ID/env" \
  | jq '[.envs[] | {key: .key, target: .target, type: .type, updatedAt: .updatedAt}] |
      sort_by(.key)'

echo ""
echo "=== Sensitive Variables (non-encrypted) ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v9/projects/$PROJECT_ID/env" \
  | jq '[.envs[] | select(.type != "encrypted") | {key: .key, type: .type, target: .target}]'

echo ""
echo "=== Function Configuration (vercel.json) ==="
if [ -f vercel.json ]; then
  jq '.functions // {} | to_entries[] | {path: .key, memory: .value.memory, maxDuration: .value.maxDuration, regions: .value.regions}' vercel.json
else
  echo "No local vercel.json found — checking API"
  curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
    "https://api.vercel.com/v9/projects/$PROJECT_ID" \
    | jq '{framework: .framework, buildCommand: .buildCommand, outputDirectory: .outputDirectory}'
fi

echo ""
echo "=== Project Domains with Cert Expiry ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v9/projects/$PROJECT_ID/domains" \
  | jq '[.domains[] | {name: .name, verified: .verified, certExpiry: (.certs[0].expiredAt // "N/A")}]'

echo ""
echo "=== Team Usage Summary ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v2/teams/$TEAM_ID/usage" | jq .

echo ""
echo "=== Cron Jobs ==="
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v9/projects/$PROJECT_ID/crons" | jq .
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Build concurrency exhaustion by feature branches | Production hotfix queued behind many feature-branch builds | `curl "https://api.vercel.com/v6/deployments?teamId=$TEAM_ID&state=BUILDING" \| jq '[.[].meta.githubCommitRef]'` | Cancel non-production builds manually | Configure branch-level deploy rules to limit concurrent preview builds |
| Shared serverless execution unit pool | Function invocations throttled during traffic spike on another project | Check team-level execution unit consumption in dashboard usage page | Upgrade to Pro/Enterprise for dedicated concurrency; use edge functions for lightweight routes | Separate high-traffic projects into isolated Vercel teams |
| Bandwidth quota shared across team projects | High-traffic project consumes most of the monthly bandwidth, degrading others | `curl "https://api.vercel.com/v2/teams/$TEAM_ID/usage" \| jq .bandwidth` | Move large media assets to Vercel Blob or external CDN for the high-traffic project | Set per-project bandwidth targets; move static-heavy projects to separate CDN |
| Edge function CPU contention at a PoP | Requests to specific geographic PoP slow down during regional traffic spike | `vercel logs $DEPLOYMENT_URL \| jq '[.[] \| select(.payload.cpuTime > 15)]'` | Convert CPU-heavy routes from edge to serverless to free PoP capacity | Profile edge functions; keep CPU < 10ms; push expensive work to serverless |
| Let's Encrypt rate limit contention (5 certs/domain/week) | New domain cert provisioning fails with rate-limit error; affects all projects adding domains this week | `dig TXT _acme-challenge.$DOMAIN` to check for existing ACME challenge records | Wait 7 days for rate limit reset; use custom wildcard cert to bypass | Use a wildcard certificate covering all subdomains; avoid frequent domain re-provisioning |
| Shared Vercel KV / Postgres connection limits | Functions return connection errors during multi-project spikes | `vercel logs \| grep "too many connections\|connection pool"` | Add connection pooling (`@vercel/postgres` uses pooler by default); reduce function concurrency | Set `POSTGRES_URL_NON_POOLING` only for migrations; use pooler URL for runtime; pool size ≤ 10 per function |
| Image optimization worker overload | `/_next/image` requests return 503 or queue; image-heavy pages slow across all projects | `curl -I "https://$PRODUCTION_DOMAIN/_next/image?url=..."` — check response time and `x-vercel-cache` | Serve images via Vercel Blob with its own CDN instead of the optimizer | Use `next/image` with `unoptimized={true}` for non-critical images; pre-optimize images before upload |
| Cron job pile-up at minute boundary | Multiple cron jobs fire simultaneously; serverless execution unit spike | `curl "https://api.vercel.com/v9/projects/$PROJECT_ID/crons" \| jq '.[].schedule'` | Stagger cron schedules by a few minutes | Offset cron expressions (e.g., `*/5` → `1,6,11,16...`); use queue-based background jobs for heavy work |
| Deployment webhook storms (many PRs merged simultaneously) | Many concurrent builds triggered by simultaneous merges; build queue depth spikes | `curl "https://api.vercel.com/v6/deployments?teamId=$TEAM_ID&state=QUEUED" \| jq length` | Enable "Smart deployments" to deduplicate rapid pushes to same branch | Use merge queues in GitHub to serialize production deployments |
| Shared Edge Config reaching read throughput limit | Edge Config reads slow down or error across all projects sharing the Edge Config store | `vercel logs \| grep "Edge Config\|edge-config"` | Split Edge Config stores by project or by criticality | Limit Edge Config reads to middleware critical path only; cache values in memory for the request lifecycle |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Vercel platform-wide edge network degradation | All edge function invocations and static asset serving fail globally | All production deployments on Vercel; 100% of end-user traffic | `curl -I https://$PRODUCTION_DOMAIN` returns 5xx; Vercel status page at vercel-status.com shows incident | Activate origin failover DNS if configured (e.g., Cloudflare with origin fallback); monitor vercel-status.com |
| Upstream API (e.g., Supabase/PlanetScale/Upstash) outage during serverless execution | Serverless functions time out waiting for upstream; Lambda timeout 500s returned to users | All API routes dependent on the upstream service | Function logs: `Error: connect ETIMEDOUT`; `vercel logs $URL | jq '.[] | select(.payload.statusCode==500)'` | Return graceful degraded response; enable circuit breaker in function code; cache last-good response |
| Build failure on main branch blocking all subsequent deployments | Production hotfixes cannot be deployed; new builds queue behind failed build | Production deployment pipeline blocked until build fixed | Vercel dashboard shows `FAILED` state on latest main build; `vercel list` shows no new `READY` deployment | Use `vercel deploy --prebuilt` with a pre-built artifact from a known-good commit; or roll back via dashboard |
| DNS propagation delay after domain transfer or NS change | New deployment unreachable via custom domain; users hit stale CDN cache or DNS NXDOMAIN | Users depending on custom domain; preview URLs still work | `dig +trace $CUSTOM_DOMAIN` shows wrong NS or old A records; `curl https://api.vercel.com/v9/projects/$PROJECT_ID/domains` shows `verified: false` | Serve traffic temporarily via `<hash>.vercel.app` URL; expedite DNS by reducing TTL before transfer |
| Environment variable deleted in production | All production functions reading that variable fail with `undefined`/null pointer errors | All functions depending on that env var in all production deployments | Function logs: `TypeError: Cannot read properties of undefined`; `vercel env ls` shows variable missing | Re-add env variable: `vercel env add <NAME> production`; redeploy: `vercel redeploy <deployment-url>` |
| Edge Config store deleted or corrupted | Middleware reading Edge Config throws at every request; all requests that pass through middleware fail | All routes processed by that middleware; typically entire application | `vercel logs | grep "Edge Config\|@vercel/edge-config"` shows `Store not found`; middleware returns 500 | Remove Edge Config read from middleware temporarily; redeploy with fallback defaults hardcoded |
| Function memory limit exhausted causing OOM crash | Function returns 500; Lambda log shows `Process exited before completing request`; high error rate | Only routes served by that function; others unaffected | `vercel logs $URL | jq '.[] | select(.payload.message | contains("FUNCTION_INVOCATION_FAILED"))'` | Increase `memory` in `vercel.json` functions config; identify memory leak and fix before next deploy |
| Vercel KV / Postgres cold start connection pool exhaustion | First N concurrent cold-start invocations each open DB connections; connection limit hit | All users hitting cold-started functions simultaneously during traffic spike | DB logs: `FATAL: remaining connection slots are reserved`; function logs show DB connection errors | Reduce max pool size per function; enable `pg_bouncer`/Neon pooler; warm functions with synthetic pings |
| CDN cache poisoning by incorrect cache headers on a bad deploy | Stale/broken responses served to all users worldwide from CDN edge nodes | Entire user base receives bad cached response | `curl -I https://$PRODUCTION_DOMAIN` shows `x-vercel-cache: HIT` with stale content and error body | `curl -X PURGE "https://api.vercel.com/v1/edge-cache/purge"` or redeploy (which busts CDN cache) |
| ISR revalidation failure causing stale page serving | `next/cache` revalidation webhook fails; pages never update despite new data in CMS | All ISR pages show stale content; no error visible to end user | `vercel logs | grep "revalidate\|ISR"` shows failures; page content unchanged after CMS publish | Manually trigger revalidation: `fetch('/api/revalidate?secret=<token>&path=<page>')`; force full redeploy |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Next.js version upgrade breaking ISR/App Router behavior | Static pages return stale data or 500; hydration errors in browser console | Immediately on first production deployment | `vercel inspect $DEPLOYMENT_URL` — compare Next.js version vs previous; browser console shows `Hydration failed` | Rollback: `vercel rollback` or redeploy previous commit; pin `next` version in `package.json` |
| `vercel.json` routes/rewrites change causing wrong routing | Previously-working URLs return 404 or route to wrong function | Immediately on deployment | `vercel logs | grep 404`; compare `vercel inspect` routing config vs previous deployment | Revert `vercel.json`; `git revert`; redeploy |
| Environment variable changed from plaintext to secret | Functions referencing variable now receive empty string (secret env vars need re-access in new deployment) | On next function cold start after deployment | Function logs show `undefined` for env var value; `vercel env ls` shows type changed to `sensitive` | `vercel env rm <VAR> production`; re-add as plaintext or ensure function is redeployed after secret update |
| `maxDuration` reduced for a function | Long-running requests now timeout at new limit; error `FUNCTION_INVOCATION_TIMEOUT` | Immediately for requests exceeding new limit | `vercel logs | grep FUNCTION_INVOCATION_TIMEOUT`; correlate spike with deployment time | Restore `maxDuration` in `vercel.json`; redeploy |
| New middleware added blocking all requests | Entire site returns 500 or incorrect redirect; middleware throwing unhandled exception | Immediately on deployment | `vercel logs | grep "_middleware"` shows uncaught exception; all routes affected including static assets | Remove or disable middleware: comment out `middleware.ts`; `git revert`; redeploy |
| Node.js runtime version changed (`engines.node` in `package.json`) | Build succeeds but native modules or `fs` APIs behave differently; runtime errors in production | Immediately on first invocation post-deploy | `vercel inspect $URL | jq .functions[].runtime`; compare vs previous; function logs show module errors | Revert `engines.node` field; redeploy; test runtime change in preview environment first |
| Custom domain SSL certificate replaced with incompatible key type | Some clients (older browsers/mobile) fail TLS handshake | Immediately for affected clients | `openssl s_client -connect $DOMAIN:443 2>&1 | grep "Cipher\|Protocol"`; test with old TLS clients | Re-provision certificate with RSA key if EC key caused compatibility issues; via Vercel domain settings |
| Output directory changed in project settings | Build artifacts not found; deployment shows blank page or 404 for all routes | Immediately on deployment | `vercel inspect $DEPLOYMENT_URL | jq .outputDirectory`; `vercel logs` shows missing static files | Revert output directory in Vercel project settings → Build & Output Settings → Output Directory |
| Git integration branch changed (deploy branch reassigned) | Production deployment now tracking wrong branch; stale code in production | Immediately on next push to newly-assigned branch | `curl "https://api.vercel.com/v9/projects/$PROJECT_ID" | jq .productionBranch`; compare with intended | Change production branch back: Vercel dashboard → Settings → Git → Production Branch |
| `headers()` function in `next.config.js` adding `Cache-Control: no-store` broadly | CDN stops caching all responses; origin function invocations spike; costs increase | Immediately on deployment | `curl -I https://$DOMAIN/` shows `cache-control: no-store`; Vercel analytics shows invocation count increase | Scope `Cache-Control` header to specific paths only; revert `next.config.js`; redeploy |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| ISR stale data divergence across edge nodes | `curl -s -I https://$DOMAIN/page -H "Cache-Control: no-cache"` — compare `x-vercel-cache` and response body from different regions | Different users see different versions of the same page depending on PoP | Inconsistent UX; A/B-like split on stale vs fresh content | Trigger full revalidation: redeploy or call on-demand revalidation API for affected paths |
| Deployment rollback leaving Edge Config at newer schema version | Rolled-back function code reads Edge Config expecting old schema; new schema written by newer deployment | `vercel env ls` and `vercel edge-config list` — compare schema version in Edge Config vs expected by rolled-back code | Runtime errors in middleware or functions reading Edge Config with wrong field names | Downgrade Edge Config to matching schema before completing rollback; or keep forward-compatible config schema |
| Concurrent deployments racing (two pushes to main within seconds) | Two deployments both promoted; users see alternating responses during alias flip | `vercel list | head -5` — two recent `READY` deployments for main branch within same minute | Brief inconsistency window; users may see different code versions simultaneously | Use merge queue to serialize pushes; `vercel alias set` manually to pin correct deployment |
| Preview deployment shares env vars with production (wrong scope) | Preview branch using production database or API key; test writes corrupt production data | `vercel env ls | jq '.[] | select(.target[] | contains("preview")) | .key'` — check for production credentials scoped to preview | Corrupted production data from preview testing | Immediately change production API keys; audit preview deployments for writes; use separate DB for preview |
| Vercel KV data written by one region not yet visible in another | KV reads in different edge regions return stale values within consistency window | `vercel logs | grep "stale"` — no direct signal; compare KV read values across regions | Race conditions in global state management | Use `@vercel/kv` with `wait: true` for strong consistency on critical writes; design for eventual consistency |
| Function A and Function B writing conflicting data to shared Postgres | Two concurrent API calls update the same row with different values; last-write-wins | `SELECT pg_stat_activity.query FROM pg_stat_activity WHERE state = 'active'` | Data corruption in production database | Add `SELECT ... FOR UPDATE` or optimistic locking; use Postgres advisory locks for critical sections |
| Build cache serving stale modules after dependency update | `node_modules` from old lockfile cached by Vercel; new version of dependency not picked up | `vercel inspect $DEPLOYMENT_URL | jq .build.env` — check `VERCEL_FORCE_NO_CACHE` flag | Application running old dependency version despite `package.json` update | Force cache bust: set env var `VERCEL_FORCE_NO_CACHE=1` for one deployment; then unset |
| Multiple aliases pointing to different deployments (alias split) | `vercel alias ls $PROJECT_ID` shows same domain aliased to two deployments | Some edge nodes serving old deployment, others new; non-deterministic user experience | Inconsistent feature availability; impossible to debug | `vercel alias set $DEPLOYMENT_URL $CUSTOM_DOMAIN` to pin single deployment; remove conflicting alias |
| Cron job triggering on wrong timezone boundary (UTC vs local) | Job runs at unexpected time; processes data for wrong time window | `vercel logs $URL | grep cron` — note actual execution timestamps vs expected | Off-by-hours data processing; reports generated for wrong period | All cron schedules in `vercel.json` use UTC; adjust schedule string to correct UTC offset |
| Edge function returning stale response from in-process cache across requests | Edge function global variable holding cache not reset between invocations on same edge instance | `curl https://$DOMAIN/api/cached-data` from same region returns old data after update | Stale API responses served to users; cache invalidation not working | Do not use module-level globals for caching in edge functions; use Edge Config or KV with explicit TTL |

## Runbook Decision Trees

### Decision Tree 1: Production Deployment Down / 5xx Errors

```
Is the Vercel Edge Network reachable? (`curl -sI https://<domain>`)
├── NO  → Check Vercel status: `curl -s https://www.vercel-status.com/api/v2/status.json`
│         ├── Vercel incident active → Follow Vercel status page; configure DNS failover if available
│         └── Vercel healthy → Check DNS: `dig <domain>`; verify NS records point to Vercel
└── YES → Are requests returning 5xx?
          ├── 500 (Function error) → `vercel logs <deployment-url> --follow` — find error stack trace
          │                          ├── Runtime exception → Identify function; check recent code change
          │                          │   ├── Recent deploy → `vercel rollback <prev-deployment-url>`
          │                          │   └── No recent deploy → Check env vars: `vercel env ls`
          │                          └── Timeout (504) → Check function duration; optimize or increase maxDuration
          ├── 502 (Bad Gateway) → Is the deployment healthy?
          │                       `vercel inspect <deployment-url>` — check function status
          │                       ├── Functions in error state → Redeploy: `vercel --prod`
          │                       └── Functions healthy → Check origin/upstream if using rewrites
          └── 503 (Unavailable) → Check account limits: function invocation quota
                                  ├── Quota exceeded → Upgrade plan or optimize function call rate
                                  └── Not quota → Check build output: `vercel build --debug`
```

### Decision Tree 2: Deployment Build Failing

```
Did `vercel --prod` or CI deployment fail?
├── YES → What stage failed? (`vercel inspect <deployment-url>` → check `Build` section)
│         ├── Install (npm/yarn) → Dependency resolution error?
│         │                        ├── Missing package → Check `package.json`; lock file mismatch
│         │                        │   Fix: `npm ci` locally; commit updated lock file
│         │                        └── Private package → Check `NPM_TOKEN` env var in Vercel project
│         ├── Build (framework compile) → Framework build error?
│         │                               ├── Next.js → `vercel logs <deployment>` for build output
│         │                               │            Check for missing env vars at build time
│         │                               └── TypeScript error → Fix type error; check tsconfig paths
│         └── Function bundle too large → `vercel inspect <deployment>` — check function sizes
│                                          ├── > 50 MB → Move large dependencies to Edge Config or external CDN
│                                          └── Tree-shake → Review dynamic imports; exclude dev dependencies
└── NO  → Deployment succeeded but behavior wrong?
          ├── Old code serving → Check deployment aliases: `vercel alias ls`
          │                      Verify production alias points to new deployment
          └── Env var missing → `vercel env ls --environment=production`
                                 Add missing var: `vercel env add <KEY> production`
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Serverless Function invocation storm (recursive call or bot traffic) | Invocation count spikes to millions; Vercel usage dashboard shows cost spike | Vercel dashboard → Usage → Function Invocations; `vercel logs --follow` for caller IP/user-agent | Function invocation quota exhausted; unexpected bill; other functions throttled | Add rate limiting middleware (`next-rate-limit`); block abusive IP via Vercel Firewall rules | Implement Vercel Firewall rules for bot/DDoS; add invocation rate alerts in Vercel dashboard |
| Edge Function cold start cascade from large bundle | Build output > 1 MB per Edge Function; cold start latency > 1 s for every request | `vercel inspect <deployment>` — Edge Function sizes; `vercel build --debug` for bundle analysis | User-facing latency SLO breach; poor Core Web Vitals scores | Move non-edge logic to Serverless Functions; reduce Edge Function bundle size | Set CI bundle size budget check; use dynamic imports; keep Edge Functions < 100 KB |
| Preview deployment accumulation consuming storage quota | Hundreds of stale preview deployments; Vercel storage and bandwidth quota consumed | `vercel ls --scope <team> | wc -l`; `vercel ls --scope <team>` — filter by age | Storage quota reached; new deployments may fail | Bulk-remove old previews: `vercel ls | grep Preview | awk '{print $1}' | xargs vercel remove --yes` | Configure auto-deletion of preview deployments after merge/close in `vercel.json`; set retention policy |
| Bandwidth overuse from uncached large static assets | CDN bandwidth quota consumed; overage charges | Vercel Analytics → Bandwidth; `curl -sI https://<domain>/<asset>` — check `x-vercel-cache: MISS` | Bandwidth overage bill; potential throttling of all traffic | Add `Cache-Control: public, max-age=31536000, immutable` header; purge and re-serve via CDN | Set proper cache headers in `vercel.json` headers config; audit assets > 1 MB for CDN caching |
| Environment variable secret exposed in client bundle | Sensitive `NEXT_PUBLIC_` prefixed secret visible in browser JS bundle | `curl -s https://<domain>/_next/static/chunks/*.js | grep -i "secret\|key\|password"` | Secret exposure; potential credential compromise | Immediately rotate the exposed secret; redeploy without the `NEXT_PUBLIC_` prefix for server-only vars | Never prefix secrets with `NEXT_PUBLIC_`; use server-side env vars for all sensitive values; secret scanning in CI |
| ISR (Incremental Static Regeneration) revalidation storm | Too many pages revalidating simultaneously; function invocation spike; origin database overloaded | Vercel Functions invocation chart during revalidation window; DB query rate spike | Database connection pool exhausted; slow page loads during revalidation | Increase `revalidate` interval; stagger revalidation with `unstable_revalidate` timing | Use on-demand ISR (`res.revalidate()`) instead of time-based; implement staggered revalidation |
| Vercel KV / Blob / Postgres usage quota consumed by unthrottled writes | KV writes or Blob uploads near plan limits; new writes failing | Vercel dashboard → Storage → KV/Blob/Postgres usage tab | Storage operations fail for all users; data writes rejected | Implement write-through cache with TTL; batch write operations; purge stale KV keys | Set storage usage alerts in Vercel dashboard; implement TTL on all KV keys; archive old Blob objects |
| Function timeout cascade: long-running DB query blocking all connections | Functions timing out at `maxDuration`; all requests to that route failing | `vercel logs <deployment> --follow` — filter for `Task timed out`; check DB slow query log | All requests to affected route fail; DB connection pool exhausted | Add DB query timeout < function `maxDuration`; scale up DB; add connection pooler (PgBouncer) | Set query timeout in DB client; use connection pooling; add p99 DB latency alert |
| Middleware running on every request including static assets | Unnecessary function invocations; cost and latency increase | `vercel logs --follow` — filter for middleware invocations on `/_next/static/` paths | Wasted invocations; latency added to static asset delivery | Add `matcher` config to middleware to exclude static paths: `matcher: ['/((?!_next/static|_next/image|favicon.ico).*)']` | Always configure `matcher` in `middleware.ts`; test middleware scope before deploying |
| CI/CD pipeline creating duplicate deployments on every push | Vercel deployment count quota consumed; build minutes exhausted | `vercel ls` — check for duplicate deployments with same git SHA | Build minute quota exhaustion; team blocked from deploying | Disable duplicate pipelines; configure `vercel.json` `github.autoJobCancelation = true` | Ensure only one CI pipeline triggers Vercel deployments; use `vercel.json` to control deployment triggers |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Cold start latency on infrequently called Serverless Functions | First request after idle > 2 s; Vercel function logs show `INIT` duration | `vercel logs <deployment> --follow \| grep "INIT"`; Vercel Analytics → Functions → Cold Start p99 | Function not warmed; large bundle or slow module initialisation | Move heavy initialisations outside handler; reduce bundle with tree-shaking; use Edge Functions for ultra-low latency |
| Edge Function connection pool exhaustion to upstream API | Edge Function requests hang; upstream fetch timeout errors in logs | `vercel logs <deployment> --follow \| grep "fetch\|timeout"`; check `x-vercel-id` header for region | Edge Function making unbounded concurrent upstream fetches without connection pooling | Implement request concurrency limiter; use `waitUntil` for background work; add upstream timeout: `fetch(url, {signal: AbortSignal.timeout(3000)})` |
| ISR revalidation memory spike | Serverless Function OOM during revalidation; Next.js `FUNCTION_INVOCATION_TIMEOUT` errors | `vercel logs <deployment> \| grep "out of memory\|FUNCTION_INVOCATION"`; Vercel Usage → Functions memory chart | Large page prop data fetched on revalidation; no streaming | Switch to on-demand ISR (`revalidatePath`/`revalidateTag`); reduce `getStaticProps` payload size; use streaming SSR |
| Middleware thread pool saturation on high-traffic route | Middleware execution time > 50 ms on hot route; Core Web Vitals TTFB degrades | `curl -w "%{time_starttransfer}" https://<domain>/hot-path`; Vercel Analytics → Web Vitals TTFB | Middleware doing expensive computation (e.g., JWT verify, geo-lookup) synchronously on every request | Move JWT verification to Edge Config lookup; use `geolocation()` caching; narrow middleware `matcher` |
| Slow database query blocking Serverless Function | API route p99 > 5 s; DB slow query log shows full table scans | `vercel logs <deployment> --follow \| grep "slow\|timeout"`; `EXPLAIN ANALYZE <query>` on DB | Missing index on query used in API route; connection pooler not configured (new connection per invocation) | Add DB index; configure Vercel Postgres with `pgbouncer=true` connection string; use Prisma Accelerate |
| CPU steal on shared Edge network node | Intermittent latency spikes on Edge Functions correlated to specific PoP; no code change | `curl -w "%{time_connect} %{time_starttransfer}" -H "x-vercel-debug-proxy-path: 1" https://<domain>/` for PoP timing | Shared compute resource contention at specific Vercel Edge PoP | Force routing away from problematic PoP via Edge Config region override; report to Vercel support with `x-vercel-id` |
| Lock contention in Vercel KV (hot key writes) | `vercel logs` shows KV write errors with high latency; same key written by many concurrent invocations | `vercel logs <deployment> \| grep "kv\|redis\|WRONGTYPE"`; measure KV write duration in function instrumentation | Multiple Serverless Functions writing to same KV key concurrently (race condition) | Use KV atomic operations (`kv.set` with `nx` or `incr`); shard hot keys by invocation region; use Vercel KV pipeline |
| Large response serialization overhead | API routes with large JSON responses have high p99; Time to First Byte elevated | `curl -w "%{time_starttransfer}" https://<domain>/api/large-endpoint`; `vercel logs \| grep "size"` | Returning full DB result set without pagination; no streaming | Implement pagination; use `Response.json()` streaming; compress with `Content-Encoding: gzip` in headers |
| Batch size misconfiguration in Vercel Cron job | Cron invokes function that processes entire dataset in one call; function times out at `maxDuration` | `vercel logs <deployment> \| grep "Task timed out"`; check cron schedule in `vercel.json` | Cron handler processes unbounded dataset without batching; exceeds 300 s `maxDuration` | Implement cursor-based batching in cron handler; chain invocations with queue (Vercel KV as job queue) |
| Downstream external API latency cascading to SSR pages | SSR pages slow because `getServerSideProps` awaits slow third-party API | `curl -w "%{time_total}" https://<domain>/ssr-page`; check Vercel function logs for fetch duration | No timeout on external fetch; no fallback/stale data | Set `AbortSignal.timeout(2000)` on external fetches; implement stale-while-revalidate fallback; add circuit breaker |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on custom domain | Browser shows `NET::ERR_CERT_DATE_INVALID`; `curl -I https://<domain>` returns TLS error | `echo \| openssl s_client -connect <domain>:443 2>/dev/null \| openssl x509 -noout -dates` | Vercel-managed cert renewal failed (DNS validation issue) or custom cert expired | Re-verify domain in Vercel dashboard → Domains; for custom cert, rotate via `vercel certs add`; check DNS CAA record |
| mTLS failure between Edge Middleware and internal service | Middleware receiving 401/403 from internal upstream after cert rotation | `vercel logs <deployment> \| grep "certificate\|mTLS\|401"`; `openssl s_client -connect <internal-host>:443` | Internal service mTLS cert rotated without updating Vercel Edge Config secret | Update mTLS client cert in Vercel environment variables; redeploy; `vercel env add CLIENT_CERT production` |
| DNS propagation failure after domain migration | Some users see old deployment; others see new; split-brain DNS | `dig <domain> @8.8.8.8`; `dig <domain> @1.1.1.1`; compare TTL and A records | DNS TTL too high during migration; registrar and Vercel nameservers disagree | Wait for TTL expiry; force DNS flush: `sudo dscacheutil -flushcache`; use `vercel dns ls` to verify records |
| TCP connection exhaustion from Vercel Functions to database | DB connections refused; function errors `ECONNREFUSED` or `too many connections` | `vercel logs <deployment> \| grep "ECONNREFUSED\|too many"`; check DB `SHOW processlist` or `SELECT count(*) FROM pg_stat_activity` | Serverless Functions open new DB connection per invocation without connection pooler | Enable PgBouncer via Vercel Postgres connection string `?pgbouncer=true`; or use Prisma Accelerate connection pooling |
| Vercel Edge Network misconfiguration routing to wrong origin | Cache hits serve stale/wrong content; `x-vercel-cache: HIT` on pages that should miss | `curl -sI https://<domain>/page \| grep x-vercel-cache`; `vercel inspect <deployment>` for routing config | Incorrect `rewrites` or `headers` in `vercel.json` causing incorrect cache key | Fix `vercel.json` routing rules; add `Cache-Control: no-store` on dynamic routes; purge edge cache: `vercel --force` |
| Packet loss from user to Vercel Edge PoP | Intermittent page load failures from specific geographic region; error 524 | `mtr --report vercel.com`; check Vercel status page for regional PoP issues; `traceroute <domain>` | ISP routing issue or Vercel PoP capacity event | Verify on Vercel status page; switch to alternative PoP by adjusting Edge middleware region config; report to Vercel support |
| MTU mismatch on corporate VPN affecting preview deployments | Preview deployment assets truncated; large JS chunks fail to load on VPN users | `ping -M do -s 1400 <vercel-ip>`; check browser console for network errors on large assets | VPN tunnel MTU lower than assets; no chunked transfer fallback | Enable compression on assets; split large chunks; inform network team to set VPN MTU to 1400; use `script[type=module]` chunking |
| Firewall blocking Vercel webhook callbacks to CI | GitHub deployment status webhooks from Vercel timing out; CI pipeline stuck | `curl -v -X POST https://<ci-webhook-url>`; check CI platform firewall / allowlist for Vercel IP ranges | Enterprise firewall blocking inbound webhooks from Vercel IP range | Add Vercel IP ranges to CI platform allowlist; use Vercel's GitHub integration native deployment status instead of webhooks |
| SSL handshake timeout on Vercel → Upstash Redis connection | Edge Function logs show `SSL SYSCALL error: EOF` or `ECONNRESET` on Redis calls | `vercel logs <deployment> \| grep "upstash\|redis\|SSL"`; `openssl s_time -connect <upstash-host>:6379 -new` | Upstash TLS cert changed or region endpoint DNS changed; Edge Function using stale connection | Update `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` in Vercel env vars; redeploy |
| Connection reset on large file upload to Vercel Blob | File upload to `/api/upload` fails mid-stream with `connection reset`; large files only | `curl -v -F "file=@largefile.bin" https://<domain>/api/upload`; check Vercel function logs for `ECONNRESET` | Vercel Serverless Function has 4.5 MB request body limit; large uploads exceed limit | Use Vercel Blob `put` with direct upload URL pattern; bypass function body limit with client-side SDK upload |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| Serverless Function OOM | Function crashes with `JavaScript heap out of memory`; error 500 returned | `vercel logs <deployment> \| grep "heap out of memory\|SIGTERM"`; Vercel Usage → Functions → Memory | Redeploy with reduced memory footprint; add `export const config = { maxDuration: 30 }` and memory limit | Reduce bundle size; avoid loading large datasets in memory; stream large responses; paginate DB queries |
| Vercel Blob storage quota exhausted | Blob `put` calls return quota error; uploads fail for all users | Vercel dashboard → Storage → Blob → usage meter; `vercel blob ls \| wc -l` | Archive or delete unused blobs: use Vercel Blob API `del` on old objects; upgrade plan if needed | Set TTL on ephemeral blobs; implement lifecycle policy; alert at 80% quota |
| Vercel KV storage quota exhausted | KV `set` calls fail; application data writes rejected | Vercel dashboard → Storage → KV → usage; `vercel kv keys '*' \| wc -l` | Flush expired keys: `vercel kv flushdb` (dangerous — only if all keys have TTL); delete stale keys | Set `EX` TTL on all KV writes; monitor KV usage; alert at 80% |
| Build minute quota exhausted | New deployments queued indefinitely; `vercel deploy` returns quota error | Vercel dashboard → Settings → Usage → Build Minutes; `vercel ls --scope <team> \| head -20` | Cancel non-essential preview deployments; disable auto-deploy for draft PRs | Configure `vercel.json` `github.enabled = false` for non-production branches; use `ignoreCommand` to skip unnecessary builds |
| Vercel Postgres connection limit reached | DB queries fail with `remaining connection slots are reserved`; API routes 503 | `SELECT count(*) FROM pg_stat_activity;`; `vercel logs \| grep "connection slots\|too many connections"` | Kill idle connections: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND query_start < now()-interval '5 min'` | Enable `pgbouncer=true` connection string parameter; set `pool_size` appropriately; use Prisma Accelerate |
| CPU time quota throttle per function invocation | Functions running longer than expected; Vercel billing shows CPU time spike | `vercel logs <deployment> \| grep "duration"`; Vercel Usage → Functions → CPU Time chart | Optimise function: eliminate blocking loops; use async/streaming; reduce synchronous processing | Profile function locally with `--inspect`; set aggressive `maxDuration`; use Edge Functions for CPU-light work |
| Inode/file limit in build container | Build fails with `ENOSPC: no space left on device` during `npm install` or `next build` | `vercel build --debug 2>&1 \| grep "ENOSPC"`; check `node_modules` size: `du -sh node_modules` | Clear build cache: `vercel deploy --force` (bypasses cache); reduce dependencies | Audit and prune unused dependencies; use `.vercelignore` to exclude large non-build files |
| Edge Function bundle size limit (1 MB) | Deployment fails with `Edge Function exceeds maximum size`; build error | `vercel build --debug \| grep "bundle size"`; `ls -lh .vercel/output/functions/` | Remove large dependencies from Edge Functions; use dynamic imports for server-only code | Set CI bundle size check; use `next/dynamic` for heavy libraries; keep Edge Functions dependency-free |
| Network egress bandwidth quota | Vercel dashboard shows bandwidth overage; potential request throttling | Vercel dashboard → Usage → Bandwidth; `curl -sI https://<domain>/large-asset \| grep content-length` | Add aggressive `Cache-Control` headers to reduce origin hits; enable Vercel CDN for static assets | Configure `vercel.json` headers for long cache TTLs; compress assets; use CDN for large static files |
| Ephemeral port exhaustion in Edge Function runtime | Edge Function cannot open outbound fetch connections; `EADDRNOTAVAIL` errors | `vercel logs <deployment> \| grep "EADDRNOTAVAIL\|cannot assign"`; check concurrent fetch count in code | Reduce concurrent fetch calls; implement request queue; use `Promise.all` with bounded concurrency | Limit concurrent upstream fetches with semaphore pattern; reuse connections via `keepalive: true` in fetch options |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation in API route — duplicate payment/order on retry | User sees duplicate charges; webhook retry from Stripe/PayPal triggers handler twice | `vercel logs <deployment> \| grep "webhook\|duplicate"`; check Stripe dashboard for duplicate event IDs | Financial data integrity loss; duplicate orders | Implement idempotency key check in handler using Vercel KV: `kv.set(idempotencyKey, 'processed', {nx: true, ex: 3600})` |
| ISR partial revalidation failure — stale pages after data update | Some pages show new data, others show stale data after `revalidateTag` call | `curl -sI https://<domain>/page \| grep x-nextjs-cache`; check Next.js revalidation logs in `vercel logs` | Inconsistent user experience; stale data served from Edge cache | Force full revalidation: `vercel --prod --force`; or call `revalidatePath('/', 'layout')` to bust all cached pages |
| Webhook event ordering failure — out-of-order Stripe events | `payment_intent.succeeded` processed before `payment_intent.created`; state machine inconsistency | `vercel logs <deployment> \| grep "payment_intent"`; compare `created` timestamps in Stripe Dashboard → Events | Incomplete order records; downstream fulfilment pipeline errors | Store events in Vercel KV keyed by `paymentIntentId`; process in correct order using `created` field; implement event sourcing |
| Cross-service deadlock — API route A waits for B, B calls back to A | Mutual HTTP calls between two API routes cause request timeout for both | `vercel logs <deployment> --follow \| grep "timeout\|ETIMEDOUT"`; trace `x-vercel-id` chain across log entries | Both routes time out; user sees 504 errors | Break circular dependency; move shared logic to a third service or Vercel Edge Config; use async queue pattern |
| At-least-once Vercel Cron duplicate execution | Cron job fires twice in same period (Vercel platform retry on timeout); operation applied twice | `vercel logs <deployment> \| grep "cron\|scheduled"`; compare function invocation timestamps in Vercel Usage | Duplicate data mutations; double emails sent; duplicate charges | Add idempotency guard in cron handler: check KV for last-run timestamp; skip if run within cron interval |
| Compensating transaction failure in multi-step API route | Step 1 (DB write) succeeds, Step 2 (email send) fails; no rollback implemented | `vercel logs <deployment> \| grep "Error\|failed"`; check DB for committed-but-incomplete records | Orphaned DB records without corresponding notifications; user support burden | Implement saga pattern with Vercel KV as state store; record each step; add cleanup cron for stuck sagas |
| Out-of-order Edge Middleware execution after config change | Middleware chain re-ordered by `vercel.json` update; auth check bypassed for cached requests | `curl -sI https://<domain>/protected \| grep "x-middleware-rewrite\|location"`; check `vercel.json` middleware order | Security bypass; unauthenticated access to protected routes | Redeploy with correct middleware order; add `Cache-Control: no-store` on all auth-gated routes; audit `vercel.json` |
| Distributed lock expiry during long Serverless Function (KV-backed lock) | Two concurrent function invocations both acquire "lock" after first TTL expires mid-operation | `vercel logs <deployment> \| grep "lock\|concurrent"`; check KV for lock key: `vercel kv get distributed_lock` | Duplicate processing; data corruption in shared resource | Use Vercel KV `SET NX PX <ttl>` atomic lock; extend TTL if operation expected to exceed initial TTL; implement watchdog renewal |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — one Serverless Function exhausting concurrency | Vercel Usage shows one function at 100% concurrent invocations; other functions queueing | Other tenant's functions cold-start or queue; latency spikes for all routes | Set `maxDuration` on noisy function: `export const config = { maxDuration: 10 }`; separate into dedicated project | Move high-concurrency function to separate Vercel project with dedicated concurrency limits; optimize function to reduce duration |
| Memory pressure from large SSR payload in shared deployment | `vercel logs \| grep "out of memory"` on one SSR route; other routes also affected due to shared function instance limits | Adjacent SSR routes starved for memory; OOM kills affect entire deployment | Reduce memory: paginate data; stream response; set `maxDuration` to force function recycle | Stream large SSR responses with `Response.body` streaming; paginate database queries; separate memory-intensive routes to dedicated functions |
| Disk I/O saturation in build container — monorepo build thrashing | Build takes > 30 min; build minutes quota consumed rapidly; other team projects queued | Other projects cannot deploy; build queue backed up across team | Cancel non-critical builds: Vercel dashboard → Deployments → Cancel; use `vercel.json` `ignoreCommand` to skip unaffected builds | Configure `ignoreCommand` to detect changed packages: `npx turbo-ignore`; use Vercel's build cache aggressively |
| Network bandwidth monopoly from large asset deployment | Vercel bandwidth usage spikes when one project deploys large media assets; other projects' CDN bandwidth throttled | Other projects experience slower asset delivery; potential CDN throttling | Move large assets to dedicated S3/CDN; use `.vercelignore` to exclude from deployment: `echo "public/large-assets" >> .vercelignore` | Use Vercel Blob for user-uploaded content; keep deployment assets < 50 MB; use external CDN for video/large files |
| Connection pool starvation — one project exhausting Vercel Postgres connections | `SELECT count(*) FROM pg_stat_activity` near limit; other projects' DB queries failing | Other applications sharing Vercel Postgres cannot connect; queries return `too many connections` | Kill idle connections from noisy project: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = '<project>'` | Enable connection pooling per project: add `?pgbouncer=true` to connection string; set `pool_size` appropriately per project |
| Quota enforcement gap — one project consuming all team build minutes | Vercel Usage shows one project using 90%+ of team build minutes; auto-deploy triggered on every push | Other projects cannot deploy; deployment queue blocked | Disable auto-deploy for noisy project: Vercel dashboard → Project → Settings → Git → Disable Auto Deployments | Set `ignoreCommand` per project; restrict which branches trigger production builds; disable preview deployments for non-critical branches |
| Cross-tenant data leak risk via shared Vercel KV namespace | Two projects sharing same Vercel KV database; project A reads keys set by project B | Project B data exposed to Project A; potential user data leak between tenants | Create separate KV database per project in Vercel dashboard; update `UPSTASH_REDIS_REST_URL` env var | Never share Vercel KV databases between projects with different tenant contexts; use key prefixes at minimum |
| Rate limit bypass — one project exhausting shared Vercel Edge Network | Vercel Edge cache evictions spike; one project's traffic pattern busts CDN cache aggressively | Other projects see reduced CDN hit rate; origin function invocations increase; latency rises | Add aggressive `Cache-Control` headers to noisy project: `vercel.json` → `"headers": [{"source": "/(.*)", "headers": [{"key": "Cache-Control", "value": "s-maxage=86400"}]}]` | Configure proper cache headers per route; use `stale-while-revalidate`; isolate cache-busting routes behind feature flag |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure — no external metrics from Vercel Functions | Prometheus/Datadog shows no Vercel function metrics; only Vercel dashboard has data | Vercel Functions have no Prometheus metrics endpoint; serverless runtime blocks long-lived processes | Poll Vercel API for usage metrics: `curl -H "Authorization: Bearer $VERCEL_TOKEN" https://api.vercel.com/v2/deployments/<id>/usage`; check Vercel Analytics API | Use Vercel Log Drains to stream function logs to external observability platform: `vercel log-drain add --url https://logs.example.com` |
| Trace sampling gap — Edge Function executions not traced | Distributed traces missing Edge Function leg; no Jaeger/OTEL trace from Vercel edge | Vercel Edge Runtime does not support full Node.js OTEL SDK; `@opentelemetry/sdk-node` not compatible | Use `x-vercel-id` header as trace correlation ID; log trace context in Edge Function; correlate in Datadog using header value | Instrument with lightweight OTEL HTTP exporter compatible with Edge Runtime: `fetch` to OTLP HTTP endpoint within function |
| Log pipeline silent drop — Vercel log drain losing events under load | SIEM missing Vercel function logs; gaps correlate with traffic spikes | Vercel Log Drain HTTP endpoint overwhelmed; drain buffer drops events silently during burst | Check log drain health in Vercel dashboard → Log Drains → Status; compare Vercel invocation count vs log events received | Use Vercel Log Drain with buffered intermediate (e.g., Kinesis or Kafka) not direct-to-SIEM; scale drain endpoint |
| Alert rule misconfiguration — Vercel deployment failure not alerting | Broken deployment goes to production; no PagerDuty alert | Alert based on Vercel webhook `deployment.error` event; webhook URL changed after rotation | Check Vercel deployment status: `vercel ls --scope <team> \| head -10`; check deployment state manually | Re-register webhook in Vercel dashboard; validate `x-vercel-signature`; test with `curl -X POST` simulated event |
| Cardinality explosion blinding dashboards | Datadog/Grafana shows millions of unique Vercel function name series; queries OOM | Vercel log drain forwarding per-deployment function name as metric tag creates new time series per deployment | Use Datadog log-to-metric pipeline to aggregate by function name without deployment hash; strip deployment ID from metric tags | Configure log drain processor to normalize function names; drop deployment ID from metric dimensions |
| Missing health endpoint — Vercel deployment readiness not validated | Post-deployment, broken routes serve 500 errors; no automated health check | Vercel does not run health checks after promotion to production; deploy-to-prod is instant | Add post-deployment smoke test in CI: `vercel deploy --prod && curl -f https://<domain>/api/health` | Implement `POST /api/health` endpoint returning 200; add GitHub Actions step running `curl -f` after `vercel deploy --prod` |
| Instrumentation gap — ISR revalidation failures not tracked | Stale pages served after data update; `revalidateTag` silently failing | Next.js ISR revalidation errors swallowed; no metric emitted on revalidation failure | Check Next.js revalidation logs: `vercel logs <deployment> \| grep "revalidat"`; test manually: `curl -X POST https://<domain>/api/revalidate?path=/` | Wrap `revalidateTag` in try/catch; emit metric to Datadog on failure; add health check validating freshness of cached page |
| Alertmanager/PagerDuty outage — Vercel functions sending alerts fail | Monitoring function that calls PagerDuty API returns 500; no incident created for real outage | Vercel function itself experiencing the outage it's supposed to alert on; circular dependency | Check Vercel status page: `curl -s https://www.vercel-status.com/api/v2/status.json \| jq .status`; verify PagerDuty manually | Use external uptime monitor (Pingdom/UptimeRobot) not hosted on Vercel to alert on Vercel outages; separate monitoring from monitored |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Next.js minor version upgrade rollback | Pages render incorrectly or return 500 after `next` version bump; hydration errors in browser console | `vercel logs <deployment> \| grep "Error\|TypeError"`; `npm list next`; check Vercel deployment error tab | Rollback deployment: `vercel rollback <previous-deployment-url>`; pin Next.js version in `package.json` | Test upgrade in preview deployment first; run `next lint` and `next build` in CI before merging; check Next.js changelog |
| Next.js major version upgrade rollback (e.g., 13 → 14 App Router migration) | App Router migration causes data-fetching regressions; components throw on server; routes 404 | `vercel logs <deployment> \| grep "Error\|NEXT_NOT_FOUND"`; `curl -f https://<domain>/migrated-route` | Rollback to Pages Router deployment: `vercel rollback <pre-migration-deployment-url>`; revert `package.json` and `app/` directory | Migrate one route at a time; use parallel Pages+App Router during transition; validate all routes before full cutover |
| Schema migration partial completion — Vercel Postgres column add | Some API routes use new column; database migration applied but some replicas not updated; inconsistent responses | `vercel logs \| grep "column.*does not exist"`; `SELECT column_name FROM information_schema.columns WHERE table_name='<table>'` | Rollback migration: run `ALTER TABLE <table> DROP COLUMN <new_col>` if safe; redeploy previous function version | Use expand-and-contract migration pattern; deploy backward-compatible code before schema change; verify migration fully applied |
| Rolling upgrade version skew — mixed deployment serving traffic | Vercel Edge Network serves requests from both old and new deployment during switchover; inconsistent behavior | `curl -sI https://<domain>/ \| grep "x-vercel-deployment-url"`; compare deployment IDs across requests | Force instant cutover: `vercel promote <new-deployment-url> --scope <team>` to atomically switch all traffic | Use atomic promotion not gradual rollout for breaking changes; blue-green deploy with manual promotion trigger |
| Zero-downtime migration gone wrong — Vercel KV data format change | New deployment writes new KV format; old deployment (still serving some traffic) reads incompatible format; cache inconsistency | `vercel logs \| grep "JSON.parse\|undefined\|TypeError"` on KV read errors; `vercel kv get <test-key>` to inspect format | Deploy backward-compatible KV reader before switching writer; rollback new deployment if reader errors spike | Write KV values with schema version field; reader handles both old and new format during migration window |
| Config format change breaking Vercel deployment (vercel.json) | Deployment fails with `Invalid vercel.json`; build aborts before function deployment | `vercel inspect <deployment-url>`; check build error; `npx vercel-schema validate vercel.json` locally | Restore previous `vercel.json` from git: `git checkout HEAD~1 -- vercel.json`; push to trigger new deployment | Validate `vercel.json` in CI: `npx vercel-schema validate vercel.json`; add JSON schema lint step to GitHub Actions |
| Data format incompatibility — Edge Config schema change | Edge Config consumer reads unknown keys after schema update; middleware throws; all requests return 500 | `vercel edge-config get <key>`; `vercel logs \| grep "edge-config\|TypeError"`; check middleware error stack | Rollback Edge Config to previous snapshot via Vercel dashboard → Edge Config → History → Restore | Version Edge Config schema; deploy reader that handles both old and new schema before updating config |
| Feature flag rollout causing regression — new Next.js experimental flag | Enabling experimental Next.js feature causes build failure or runtime regression in production | `vercel logs <deployment> \| grep "experimental\|Error"`; compare `next.config.js` diff | Disable experimental flag: revert `next.config.js`; push commit to trigger new deployment; `vercel rollback` as emergency | Test experimental flags in preview deployments only; promote to production only after 24 h soak; document each flag in config comment |

## Kernel/OS & Host-Level Failure Patterns
| Failure Mode | Vercel-Specific Symptom | Detection Command | Root Cause Confirmation | Remediation |
|-------------|------------------------|-------------------|------------------------|-------------|
| Serverless function memory limit exceeded | Vercel function returns `FUNCTION_INVOCATION_FAILED`; logs show `Runtime exited with signal: killed` | `vercel logs <deployment> --since 1h \| grep "FUNCTION_INVOCATION_FAILED\|killed\|memory"`; check Vercel dashboard function metrics | Function memory usage exceeds configured limit (default 1024MB); large payload processing or memory leak | Increase function memory in `vercel.json`: `"functions": {"api/*.ts": {"memory": 3009}}`; optimize memory usage; stream large payloads instead of buffering |
| Vercel build OOM during Next.js static generation | Build fails with `JavaScript heap out of memory` during `next build`; deployment never completes | `vercel logs <deployment> --output raw \| grep "heap\|ENOMEM\|JavaScript heap"`; check build log in Vercel dashboard | `next build` ISR/SSG generates thousands of pages; Node.js heap exceeds build container memory limit | Add `NODE_OPTIONS=--max-old-space-size=4096` to Vercel environment variables; reduce ISG page count; use `dynamicParams` to defer page generation |
| Serverless function cold start timeout | Function returns `504 GATEWAY_TIMEOUT` on first invocation after idle period; subsequent calls succeed | `vercel logs <deployment> \| grep "504\|GATEWAY_TIMEOUT\|cold start"; curl -w "time_total: %{time_total}" https://<domain>/api/<endpoint>` | Cold start initialization (DB connection, SDK init) exceeds function `maxDuration`; common with large dependency bundles | Reduce bundle size with `@vercel/nft` tree-shaking; move initialization outside handler; increase `maxDuration` in `vercel.json`; use Edge Runtime for latency-sensitive endpoints |
| Edge function CPU time limit exceeded | Edge function returns `EDGE_FUNCTION_INVOCATION_TIMEOUT`; compute-heavy logic fails at edge | `vercel logs <deployment> \| grep "EDGE_FUNCTION_INVOCATION\|timeout"; vercel inspect <deployment>` | Edge Runtime has strict CPU time limit (typically 30s wall clock); compute-intensive operations (crypto, image processing) exceed limit | Move compute-heavy logic to Serverless Function (not Edge); optimize algorithms; use Web Crypto API instead of Node.js crypto; split work into multiple edge invocations |
| File system read-only in serverless function | Function fails with `EROFS: read-only file system` when trying to write temp files | `vercel logs <deployment> \| grep "EROFS\|read-only\|EACCES"` | Vercel serverless functions have read-only filesystem except `/tmp`; application writing to `/var` or project directory | Write temporary files to `/tmp` only; use Vercel Blob Storage or S3 for persistent files; configure library temp paths: `TMPDIR=/tmp` |
| Serverless function execution duration limit | Long-running API endpoint returns `504` after max duration; background processing cut off | `vercel logs <deployment> \| grep "504\|FUNCTION_INVOCATION_TIMEOUT"; curl -w "\ntime_total: %{time_total}\n" https://<domain>/api/<endpoint>` | Function exceeds `maxDuration` (default 10s on Hobby, 300s on Pro); long-running computation or external API call hangs | Increase `maxDuration` in `vercel.json`: `"functions": {"api/long.ts": {"maxDuration": 300}}`; implement background jobs with Vercel Cron or external queue; stream responses for real-time feedback |
| Concurrent function execution limit reached | Functions return `429 Too Many Requests`; burst traffic exceeds account concurrency limit | `vercel logs <deployment> \| grep "429\|concurrent\|throttle"; vercel inspect <deployment> \| grep concurrency` | Account-level concurrent function execution limit reached; traffic spike exceeds provisioned capacity | Implement request queuing in application; use Edge Functions for lightweight requests; contact Vercel to increase concurrency limit; implement client-side retry with backoff |
| ISR revalidation fails silently on large pages | Stale pages served after data update; ISR `revalidate` callback completes but page not updated in cache | `curl -sI https://<domain>/<path> \| grep "x-vercel-cache\|age"; vercel logs <deployment> \| grep "revalidat"` | ISR revalidation function OOM or timeout during page regeneration; page HTML too large for regeneration within function limits | Reduce page size; split large pages into client-fetched components; use on-demand revalidation: `res.revalidate('/path')` with smaller pages; monitor revalidation success rate |

## Deployment Pipeline & GitOps Failure Patterns
| Failure Mode | Vercel-Specific Symptom | Detection Command | Root Cause Confirmation | Remediation |
|-------------|------------------------|-------------------|------------------------|-------------|
| Vercel build fails due to environment variable missing | Build fails with `undefined` errors; `process.env.DATABASE_URL` not set in build context | `vercel env ls --environment production; vercel logs <deployment> --output raw \| grep "undefined\|env\|DATABASE"` | Environment variable defined for Preview but not Production; or variable added to `.env.local` but not Vercel dashboard | Add variable in Vercel dashboard for all environments; use `vercel env pull` to sync; add build-time validation: `if (!process.env.DATABASE_URL) throw new Error("Missing DATABASE_URL")` |
| GitHub integration deployment stuck | Vercel deployment shows `Queued` for > 10 min after push; GitHub check pending indefinitely | `gh run list --repo <owner/repo> \| head -5; vercel ls --scope <team> \| head -5` | Vercel GitHub App webhook delivery delayed or failed; GitHub Actions workflow required before Vercel build | Check Vercel deployment queue in dashboard; re-trigger: `vercel --prod`; verify GitHub App installation at `github.com/settings/installations` |
| Preview deployment URL not generated for PR | GitHub PR missing Vercel preview comment; no preview URL available for review | `gh pr view <pr-number> --json comments \| jq '.comments[] \| select(.body \| contains("vercel"))'`; `vercel ls --scope <team> \| grep <branch>` | Vercel GitHub integration disabled or branch protection rules blocking deployment; `vercel.json` `git.deploymentEnabled: false` | Re-enable GitHub integration in Vercel project settings; check `vercel.json` for `git` configuration; verify Vercel bot has access to repository |
| Monorepo build deploys wrong project | Vercel builds and deploys root package instead of specific workspace; wrong application served | `vercel inspect <deployment> \| grep "Root Directory"; cat vercel.json \| jq .buildCommand` | `rootDirectory` not set in Vercel project settings for monorepo; or `vercel.json` at wrong level | Set `Root Directory` in Vercel project settings to workspace path (e.g., `packages/web`); configure `buildCommand` and `outputDirectory` in `vercel.json` |
| Domain configuration DNS propagation failure | Custom domain shows Vercel 404 page; DNS configured but not resolving to deployment | `dig <domain> +short; dig CNAME <domain> +short; vercel domains inspect <domain>` | DNS CNAME not pointing to `cname.vercel-dns.com`; or DNS propagation not complete; or domain not added to Vercel project | Verify DNS: `dig <domain> CNAME +short` should return `cname.vercel-dns.com`; add domain in Vercel dashboard; wait for propagation (up to 48h for DNS changes) |
| Vercel project settings drift from code | `vercel.json` in repo conflicts with project settings in dashboard; deployment uses unexpected configuration | `vercel inspect <deployment>; cat vercel.json; vercel project ls` | Dashboard settings override `vercel.json` for some fields; team members changed settings without updating code | Use `vercel.json` as single source of truth; document which settings are code-managed vs dashboard-managed; review project settings in deploy checklist |
| Secret rotation breaks Vercel function database connection | API endpoints return `500`; logs show database connection refused or auth failure | `vercel logs <deployment> \| grep "ECONNREFUSED\|auth\|password\|connection"; vercel env ls --environment production` | Database password rotated but Vercel environment variable not updated; function caches connection string at cold start | Update env var: `vercel env rm DATABASE_URL production && vercel env add DATABASE_URL production`; redeploy: `vercel --prod`; use Vercel integration for managed DB credentials |
| Build cache poisoning causes deployment failures | Builds fail with cryptic module resolution errors; clearing cache fixes the issue | `vercel logs <deployment> --output raw \| grep "Module not found\|Cannot find"`; `vercel --prod --force` to bypass cache | Vercel build cache contains stale `node_modules` or `.next/cache` from incompatible version; dependency version conflict | Force fresh build: `vercel --prod --force`; add `VERCEL_FORCE_NO_BUILD_CACHE=1` environment variable; clear cache in Vercel dashboard project settings |

## Service Mesh & API Gateway Edge Cases
| Failure Mode | Vercel-Specific Symptom | Detection Command | Root Cause Confirmation | Remediation |
|-------------|------------------------|-------------------|------------------------|-------------|
| Vercel Edge Network cache serving stale content | Users see outdated page content despite redeployment; `x-vercel-cache: HIT` on changed pages | `curl -sI https://<domain>/<path> \| grep "x-vercel-cache\|age\|cache-control"; vercel inspect <deployment>` | CDN cache not invalidated after deployment; `Cache-Control` header set to long `max-age`; ISR `revalidate` period too long | Purge cache: `vercel redeploy --force`; set appropriate `Cache-Control` headers; reduce ISR `revalidate` period; use `res.revalidate()` for on-demand invalidation |
| Rate limiting on Vercel API routes | API endpoints return `429`; legitimate traffic blocked; application degraded | `vercel logs <deployment> \| grep "429\|RATE_LIMIT"; curl -v https://<domain>/api/<endpoint>` | Vercel's built-in DDoS protection or account-level rate limiting triggered by traffic spike; or custom `vercel.json` rate limit config | Implement application-level rate limiting with Vercel KV/Redis; use Edge Middleware for custom rate limit logic; contact Vercel support to increase limits; cache API responses |
| Vercel middleware infinite redirect loop | All pages return `ERR_TOO_MANY_REDIRECTS`; middleware.ts redirecting every request including its own target | `curl -v -L --max-redirs 5 https://<domain>/ 2>&1 \| grep "Location"` | `middleware.ts` matcher too broad; middleware redirects `/en` to `/en` repeatedly; missing `matcher` config excludes redirect target | Add `matcher` config to `middleware.ts`: `export const config = { matcher: ['/((?!api\|_next\|favicon).*)'] }`; check redirect target is excluded from middleware |
| Vercel Edge Middleware timeout on external API call | Edge Middleware returns `504`; auth check against external IdP times out; all pages inaccessible | `vercel logs <deployment> \| grep "middleware\|504\|timeout"; curl -w "time_total: %{time_total}" https://<domain>/` | External IdP (Auth0/Clerk) response time > Edge Middleware timeout; network latency to IdP from edge location | Cache auth tokens in Vercel KV/Edge Config; reduce external calls in middleware; implement token validation locally (JWT verify without network call); add fallback for IdP timeout |
| Retry storm from client-side SWR/React Query | Frontend retries failed API calls aggressively; Vercel function invocations spike 10x; costs increase | `vercel logs <deployment> --since 1h \| grep "<endpoint>" \| wc -l`; check Vercel usage dashboard for function invocation spike | SWR/React Query default `retryCount=3` with short delay; one failing endpoint generates 4x requests from every active client | Configure client retry: `retryCount: 1, retryDelay: attempt => Math.min(1000 * 2 ** attempt, 30000)`; add circuit breaker in client; return proper error status codes to prevent retries |
| Vercel Firewall/WAF blocks legitimate traffic | Legitimate API requests blocked with `403 Forbidden`; Vercel Firewall rule false positive | `vercel logs <deployment> \| grep "403\|firewall\|blocked"; curl -v https://<domain>/api/<endpoint>` | Vercel Firewall rule matching on request body pattern or User-Agent; legitimate API clients blocked | Review Vercel Firewall rules in dashboard; add IP/path allowlist exceptions; adjust WAF sensitivity; use custom `x-vercel-skip-waf` header for trusted clients |
| Trace context not propagated through Vercel Edge Network | Distributed traces show gap at Vercel; upstream and downstream spans not correlated | `curl -v -H "traceparent: 00-<trace-id>-<span-id>-01" https://<domain>/api/test 2>&1; vercel logs <deployment> \| grep "traceparent"` | Vercel Edge Network does not forward `traceparent` header to serverless functions by default; header stripped at edge | Forward trace headers in `middleware.ts`: `request.headers.set('x-trace-id', request.headers.get('traceparent'))`; use Vercel's `x-vercel-trace` header for Vercel-native tracing |
| WebSocket connections not supported on Vercel | Application WebSocket upgrade fails with `426`; real-time features broken after migration to Vercel | `curl -v -H "Connection: Upgrade" -H "Upgrade: websocket" https://<domain>/ws/ 2>&1` | Vercel does not support WebSocket connections on serverless functions; only Edge Functions with limited support | Use Vercel's recommended alternatives: Server-Sent Events (SSE), Vercel AI SDK streaming, or external WebSocket service (Pusher/Ably/Socket.io cloud); implement polling fallback |
