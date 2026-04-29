---
name: netlify-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-netlify-agent
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
# Netlify SRE Agent

## Role
On-call SRE responsible for the Netlify platform. Owns build pipeline health, deploy preview integrity, serverless function availability, custom domain routing, form submission handling, Identity service, split testing, large media, and build plugin execution. Responds to build failures, deploy timeouts, function invocation errors, DNS issues, and identity service outages.

## Architecture Overview

```
Git Push / CLI / API
        │
        ▼
  Netlify Build System
  ┌──────────────────────────────────────────────────────────────┐
  │  Install (npm/yarn/pnpm)                                     │
  │  ↓                                                           │
  │  Build Plugins (pre-build / post-build hooks)                │
  │  ↓                                                           │
  │  Build Command (gatsby build / hugo / next build etc.)       │
  │  ↓                                                           │
  │  Post-Processing (asset optimization, form injection)        │
  │  ↓                                                           │
  │  Atomic Deploy (deploy-to-CDN, flip traffic on success)      │
  └──────────────────────────────────────────────────────────────┘
        │
        ▼
  Netlify ADN (Application Delivery Network)
  ┌──────────────────────────────────────────────────────────────┐
  │  Static Assets (CDN)  ──▶  Edge Nodes (global PoPs)         │
  │  Netlify Functions    ──▶  AWS Lambda (us-east-1 + edge)    │
  │  Edge Functions       ──▶  Deno runtime at edge             │
  │  Custom Domains       ──▶  SSL (Let's Encrypt)              │
  │  Forms                ──▶  Netlify Form Processing           │
  │  Identity             ──▶  GoTrue auth service              │
  │  Large Media          ──▶  Git LFS proxy                    │
  │  Split Testing        ──▶  Traffic splitting at ADN          │
  └──────────────────────────────────────────────────────────────┘
```

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| Build success rate | < 95% | < 85% | Track per build command type |
| Build duration p95 | > 8 min | > 25 min (near 30-min default limit) | Build timeout configurable; default 30 min |
| Function invocation error rate | > 1% | > 5% | 5xx from Netlify Functions |
| Function duration p95 | > 8s | > 25s (near 26s limit) | Hard timeout at 26s (background: 15 min) |
| Deploy preview availability | < 99% | < 95% | Preview DNS resolution and routing |
| Form submission delivery rate | < 99% | < 95% | Missed submissions are not recoverable |
| Identity service error rate | > 1% | > 5% | GoTrue errors affect sign-up/login flows |
| Custom domain cert expiry | < 30 days | < 7 days | Auto-renew via Let's Encrypt |
| Large media transformation errors | > 5% | > 15% | LFS-backed images failing to transform |
| Bandwidth used / plan limit | > 80% | > 95% | Overage billing or throttle |

## Alert Runbooks

### ALERT: Build Failure Spike

**Symptoms:** Multiple site deployments fail; builds show non-zero exit codes; deploy log shows build error.

**Triage steps:**
1. List recent failed deploys via CLI or API:
   ```bash
   netlify deploys --site-id $SITE_ID | head -20
   # or
   curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
     "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=10" \
     | jq '[.[] | {id: .id, state: .state, error_message: .error_message, created_at: .created_at}]'
   ```
2. Fetch build log for the failing deploy:
   ```bash
   curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
     "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID/log" | jq '.[].message'
   ```
3. Classify the failure from log output:
   - `npm ERR!` or `yarn error` → dependency install failure
   - `Build script returned non-zero exit code: 1` → build command failed
   - `Build exceeded maximum allowed runtime` → build timeout hit
   - `JavaScript heap out of memory` → OOM in build
   - `Error: Cannot find module` → missing dependency
4. If a recent build plugin caused the failure, disable it:
   ```bash
   # In netlify.toml, comment out the failing plugin
   # [[plugins]]
   #   package = "@netlify/plugin-failing"
   ```

---

### ALERT: Deploy Preview Broken

**Symptoms:** Preview URLs return 404 or fail to load; `deploy-preview-*.netlify.app` not resolving.

**Triage steps:**
1. Check the deploy preview status:
   ```bash
   curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
     "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?context=deploy-preview&per_page=10" \
     | jq '[.[] | {id: .id, state: .state, deploy_url: .deploy_url, error: .error_message}]'
   ```
2. Verify the preview deployment completed successfully (state = `ready`):
   ```bash
   curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
     "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID" | jq '.state'
   ```
3. If state is `error`, check the build log for the specific deploy.
4. If state is `ready` but URL returns 404, the site's redirect rules may be misconfigured:
   ```bash
   cat netlify.toml | grep -A 5 "\[\[redirects\]\]"
   ```
5. Check if branch deploy settings allow preview for the branch:
   ```bash
   curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
     "https://api.netlify.com/api/v1/sites/$SITE_ID" \
     | jq '.build_settings.allowed_branches'
   ```

---

### ALERT: Netlify Functions Error Rate Elevated

**Symptoms:** API endpoints returning 502 or 500; `Function invocation failed` in logs; timeouts on function-backed routes.

**Triage steps:**
1. Check function logs (last 15 minutes):
   ```bash
   netlify functions:invoke $FUNCTION_NAME --site-id $SITE_ID 2>&1
   # For live tail:
   netlify dev --live  # local testing first
   ```
2. Check function invocation errors via API:
   ```bash
   curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
     "https://api.netlify.com/api/v1/sites/$SITE_ID/functions/$FUNCTION_NAME/log?from=$(date -u -v-15M +%s)000" \
     | jq '.[] | select(.level == "error") | .message'
   ```
3. Look for timeout errors (function ran longer than 26 seconds):
   - If timing out: check external API calls, database queries, large data processing.
   - If memory error: check for memory leaks; upgrade function memory.
4. Verify the function is deployed in the current production deploy:
   ```bash
   curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
     "https://api.netlify.com/api/v1/sites/$SITE_ID/functions" | jq '.[].name'
   ```

---

### ALERT: Custom Domain DNS / Certificate Issue

**Symptoms:** Production domain shows cert error; HTTPS failing; HTTP redirects not working.

**Triage steps:**
1. Check DNS configuration:
   ```bash
   netlify domains:inspect $DOMAIN --site-id $SITE_ID
   dig CNAME $DOMAIN
   # Expected: $SITE_NAME.netlify.app
   # Apex: dig A $DOMAIN → should show Netlify load balancer IPs
   ```
2. Check cert provisioning status:
   ```bash
   curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
     "https://api.netlify.com/api/v1/sites/$SITE_ID/ssl" | jq .
   ```
3. If cert is expired or shows `pending`, re-provision:
   ```bash
   curl -X POST -H "Authorization: Bearer $NETLIFY_TOKEN" \
     "https://api.netlify.com/api/v1/sites/$SITE_ID/ssl"
   ```
4. Verify Netlify nameservers are used if DNS is managed by Netlify:
   ```bash
   dig NS $ROOT_DOMAIN
   # Should return *.netlify.com nameservers
   ```

## Common Issues & Troubleshooting

### 1. Build Timeout

**Diagnosis:**
```bash
# Check build log for timeout message
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID/log" \
  | jq '[.[] | .message]' | grep -i "timeout\|exceeded"
# Message: "Build exceeded maximum allowed runtime of 30 minutes"
```

### 2. Form Submissions Failing (Not Reaching Dashboard)

**Diagnosis:**
```bash
# Check form submissions count
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/forms" | jq '[.[] | {name: .name, submission_count: .submission_count}]'

# Check recent submissions
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/submissions?per_page=10" | jq .

# Verify form has data-netlify="true" attribute
# The HTML must be present in the static build output, not injected client-side only
curl -s https://$PRODUCTION_DOMAIN/contact | grep -i "netlify\|data-netlify"
```

### 3. Identity (GoTrue) Service Failures

**Diagnosis:**
```bash
# Check identity service status
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID" | jq '.identity_instance'

# Test the Identity endpoint
curl "https://$SITE_NAME.netlify.app/.netlify/identity/user" \
  -H "Authorization: Bearer $USER_JWT"

# Check identity configuration
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/identity" | jq .
```

### 4. Large Media Transformation Errors

**Diagnosis:**
```bash
# Check if LFS credentials are valid
git lfs env
git lfs ls-files | head -10

# Test a Large Media URL
curl -I "https://$SITE_NAME.netlify.app/.netlify/large-media/$IMAGE_PATH?nf_resize=fit&w=800"
# Look for: X-NFO-* headers confirming Large Media processing
```

### 5. Split Testing Causing Routing Conflicts

**Diagnosis:**
```bash
# Check active split tests
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/split_tests" | jq .

# Check if a redirect rule conflicts with split test routing
cat netlify.toml | grep -A 5 "\[\[redirects\]\]"
```

### 6. Build Plugin Failure Blocking Deploy

**Diagnosis:**
```bash
# Check build log for plugin error
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID/log" \
  | jq '[.[] | .message]' | grep -i "plugin\|Plugin"
```
Typical message: `@netlify/plugin-name errored: Error: ...`

## Key Dependencies

- **Git Provider (GitHub / GitLab / Bitbucket):** Webhooks trigger builds; outage breaks automatic deployments; check webhook delivery in git provider settings
- **npm / yarn Registry:** Build-time install; private registry auth failures break dependency install
- **AWS Lambda (us-east-1):** Netlify Functions run on AWS Lambda; AWS Lambda regional outage causes function errors
- **Let's Encrypt:** TLS cert provisioning; rate limits (5 per domain per week) delay re-provisioning
- **GoTrue:** Open-source Identity service used by Netlify Identity; GoTrue service availability affects auth flows
- **Git LFS:** Large Media depends on Git LFS; pointer resolution failure causes missing media
- **External SMTP Provider:** Email notifications, form notifications, and Identity email flows depend on Netlify's or custom SMTP

## Cross-Service Failure Chains

- **npm registry outage** → Dependency install fails → All builds fail → No deployments possible until registry recovers
- **AWS Lambda us-east-1 degradation** → Netlify Functions 502/504 → All dynamic API routes fail → Static pages still served
- **GoTrue service down** → Identity sign-in and sign-up broken → Gated content inaccessible → Form submissions using Identity token fail
- **Let's Encrypt rate limit hit** → Cert provisioning fails for new/re-added domains → HTTPS broken; HTTP-only fallback required
- **Build plugin API dependency down** → Plugin fails during post-build hook → Build marked as failed even if site compiled correctly → Disable plugin to unblock
- **CDN edge node degradation** → Static assets slow or partially unavailable in one region → Visitors see old cached content or timeout

## Partial Failure Patterns

- **Functions work, static assets broken:** CDN propagation delay post-deploy; purge CDN cache or wait for TTL.
- **Forms working on production, not on preview:** Data-netlify attribute present only in production build; check build-time HTML output for preview branch.
- **Identity working, form notifications not delivered:** External SMTP misconfigured; Identity and forms use different notification paths.
- **Split test active but all traffic goes to branch A:** Traffic cookie set for all users; clear cookies or set cookie TTL correctly.
- **Edge functions work, serverless functions error:** Edge uses Deno runtime; serverless uses Node.js; incompatible APIs used (`Deno.*` vs. `process.*`).

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|---------|----------|
| Build time (medium static site) | < 3 min | 3–10 min | > 25 min (near timeout) |
| Build time (Next.js SSG) | < 5 min | 5–15 min | > 25 min |
| Function cold start p95 | < 800ms | 800ms–2s | > 3s |
| Function execution p95 | < 3s | 3–20s | > 25s (near 26s limit) |
| Static asset TTFB p95 | < 50ms | 50–200ms | > 500ms |
| Deploy to CDN propagation | < 30s | 30s–2min | > 5 min |
| Form submission processing | < 2s | 2–10s | > 30s |
| Identity token validation p95 | < 100ms | 100–300ms | > 500ms |

## Capacity Planning Indicators

| Indicator | Current Baseline | Scale-Up Trigger | Notes |
|-----------|-----------------|-----------------|-------|
| Bandwidth used / plan limit | — | > 80% | Upgrade plan; offload heavy assets to S3/R2 |
| Build minutes / month | — | > 80% of plan limit | Optimize builds; use build cache |
| Function invocations / month | — | > 80% of plan limit | Review if functions can be replaced with edge functions |
| Concurrent builds | — | > 60% of plan concurrency | Upgrade plan for more concurrent build slots |
| Form submissions / month | — | > 80% of plan limit | Migrate to dedicated form service |
| Identity active users | — | > 80% of plan limit | Identity free tier = 1000 MAU |
| Site count per team | — | > 500 sites | Review dormant sites; consolidate |
| Large media stored | — | > 80% of LFS quota | Clean up unused LFS objects |

## Diagnostic Cheatsheet

```bash
# Get site info
netlify status --site-id $SITE_ID

# List recent deploys
netlify deploys --site-id $SITE_ID | head -20

# Get build log for a deploy
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID/log" | jq -r '.[].message'

# List all environment variables
netlify env:list --site-id $SITE_ID

# Check function list for deployed site
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/functions" | jq '.[].name'

# Invoke a function locally for debugging
netlify functions:invoke $FUNCTION_NAME --payload '{"test": true}'

# Check custom domain and cert status
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/ssl" | jq .

# List all form submissions for a site
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/submissions?per_page=20" | jq .

# Roll back to a previous deploy
curl -X POST -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys/$DEPLOY_ID/restore"

# Lock a deploy to prevent it from being overridden
curl -X POST -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID/lock"

# List active split tests
curl -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/split_tests" | jq .
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|-------------------|-------------|
| Production deploy success rate | 99% (user-caused errors excluded) | 7.2 hours | Successful deploys / total deploys triggered |
| Function invocation success rate | 99.5% | 3.6 hours | Non-5xx / total invocations per 5-min window |
| Static asset CDN availability | 99.99% | 4.3 minutes | Synthetic probe every 30s |
| Form submission delivery rate | 99.9% | 43.8 minutes | Submitted vs. received in dashboard |

## Configuration Audit Checklist

| Check | Expected State | How to Verify | Risk if Misconfigured |
|-------|---------------|--------------|----------------------|
| Build command correct | Matches framework expectation | `netlify.toml` `[build].command` | Wrong command → always failing builds |
| Publish directory correct | Points to static output dir | `netlify.toml` `[build].publish` | Empty site deployed |
| All sensitive env vars set | Present for all required contexts | `netlify env:list` | Runtime crashes on missing var |
| Form spam protection | reCAPTCHA or honeypot enabled | HTML form attributes | Spam submission floods |
| Identity JWT secret non-default | Custom secret set | Dashboard > Identity > Settings | Predictable JWT secret |
| Function timeout within limits | ≤ 26s for sync, configured for bg | `netlify.toml` or function metadata | Unexpected truncation |
| Redirect rules order correct | Most specific rules first | `netlify.toml` `[[redirects]]` order | Wrong page served for routes |
| Deploy notifications configured | Slack/email for build failures | Dashboard > Notifications | Silent build failures |
| Build plugins pinned to versions | Explicit `@version` in netlify.toml | `[[plugins]]` config | Breaking plugin update breaks build |
| Domain HTTPS forced | HTTP → HTTPS redirect enabled | Dashboard > Domain management > HTTPS | Users served over HTTP |

## Log Pattern Library

| Pattern | Source | Meaning | Action |
|---------|--------|---------|--------|
| `Build exceeded maximum allowed runtime` | Build log | Build timeout reached | Optimize build; enable caching; upgrade plan |
| `npm ERR! code ERESOLVE` | Build log | Dependency conflict | Add `overrides` or `--legacy-peer-deps` |
| `JavaScript heap out of memory` | Build log | Node.js OOM in build | Set `NODE_OPTIONS=--max-old-space-size=4096` |
| `Error: ENOMEM` | Build log | OS-level OOM | Reduce parallelism in build command |
| `Function invocation failed` | Function log | Lambda error | Check function code for exception |
| `Task timed out after 26.00 seconds` | Function log | Function timeout | Optimize or use Background Function |
| `error Command failed with exit code 1` | Build log | Build command non-zero exit | Read preceding lines for root cause |
| `Failed to transform image` | Large media log | LFS transform error | Check LFS credentials; verify transform params |
| `Could not find file` | Build log | Missing file in build output | Check `publish` directory configuration |
| `Redirect rules loop detected` | Deploy log | Circular redirect | Review netlify.toml redirects for cycles |
| `netlify-identity-widget: error` | Browser log | GoTrue API error | Check Identity service status |
| `Build cancelled` | Build log | Manual or auto-cancel | Check if superseded by newer commit |

## Error Code Quick Reference

| Error / Status | Context | Meaning | Resolution |
|---------------|---------|---------|-----------|
| `Build returned non-zero exit code: 1` | Build | Build command failed | Check build log for the actual error |
| `Build exceeded maximum allowed runtime` | Build | Timeout reached | Enable caching; optimize or increase limit |
| `Missing required environment variable` | Build/Runtime | Env var undefined | Add via `netlify env:set` |
| `Function invocation failed` | Functions | Lambda execution error | Check function logs; fix exception |
| `Task timed out after 26.00 seconds` | Functions | Function timeout | Optimize or use background function |
| `503 Service Unavailable` | Functions | Lambda cold start or overload | Reduce function size; warm up if critical |
| `CERT_PROVISIONING_FAILED` | Domain | Let's Encrypt cert failed | Fix DNS records; check Let's Encrypt rate limit |
| `DOMAIN_NOT_VERIFIED` | Domain | DNS not pointing to Netlify | Update CNAME/A records |
| `Form not found` | Forms | Form HTML missing from build | Add static HTML form with `data-netlify="true"` |
| `Identity: 422 Unprocessable Entity` | Identity | GoTrue validation error | Check request payload; review GoTrue logs |
| `LFS object not found` | Large Media | LFS pointer with no backing object | Re-push LFS objects: `git lfs push --all origin` |
| `Split test branch not deployed` | Split Tests | Branch for split test has no deploy | Deploy the branch before enabling split test |

## Known Failure Signatures

| Signature | Pattern | Root Cause | Resolution |
|-----------|---------|-----------|-----------|
| All builds fail after `package.json` update | `ERESOLVE` or `MODULE_NOT_FOUND` | Breaking dependency change | Roll back package.json; investigate conflict |
| Functions return 502 after deploy | New function code crashes on cold start | Initialization error (top-level await, require failure) | Check function boot code; add error handling |
| Preview deploys stuck at "Building" | No log output after clone step | Git submodule or LFS issue | Check `.gitmodules`; re-configure LFS |
| Form submissions stop appearing in dashboard | Zero new submissions despite traffic | `data-netlify` attribute missing from build output | Add static HTML form stub; redeploy |
| Identity sign-ups work, logins fail | `401` from `/.netlify/identity/token` | JWT secret rotated without session invalidation | Force all users to re-authenticate |
| Split test delivering 100% to one branch | Users all get same experience | Branch A has deploy; Branch B build failed | Fix Branch B build; re-activate split test |
| CDN serves stale content after deploy | Old HTML/JS still appearing post-deploy | Browser or ISP DNS/CDN cache | Add cache-busting asset fingerprints; set short TTL |
| Build works locally, fails in Netlify | Environment-specific build error | Different Node.js version or OS | Set `NODE_VERSION` env var; add `.nvmrc` |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `fetch` resolves with HTTP 500 | Browser `fetch` / `axios` | Netlify Function uncaught exception or Lambda crash | `curl "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys/$DEPLOY_ID/functions" \| jq .` | Add try/catch in function handler; return structured error response |
| `fetch` resolves with HTTP 502 | Browser `fetch` / `axios` | Lambda cold start timeout; function bundle too large | Check function bundle size in `.netlify/functions-serve/` | Reduce dependencies; use esbuild bundling via `netlify.toml` |
| `fetch` resolves with HTTP 504 | Browser `fetch` / `axios` | Function exceeded 26s synchronous timeout | `netlify functions:invoke $FUNCTION_NAME` locally and time it | Optimize slow external calls; switch to Background Function for long tasks |
| `TypeError: Failed to fetch` | Browser `fetch` | CORS headers absent from function response | Browser devtools — preflight `OPTIONS` returns no CORS headers | Add `Access-Control-Allow-Origin` header in function response |
| `401 Unauthorized` on API call | `netlify-identity-widget` | GoTrue JWT validation failure; mismatched site URL | `curl "https://$SITE_NAME.netlify.app/.netlify/identity/user" -H "Authorization: Bearer $JWT"` | Confirm `netlify-identity-widget` is initialized with the correct site URL |
| Form submission silently ignored | JavaScript `FormData` / `fetch` | `data-netlify="true"` attribute missing from static HTML at build time | `curl -s https://$PRODUCTION_DOMAIN/contact \| grep "data-netlify"` | Add hidden static form in `public/index.html` for client-side-only forms |
| `404 Not Found` on valid SPA route | SPA router (React Router, Vue Router) | Redirect rule for `/* /index.html 200` missing from `netlify.toml` | `curl -I https://$PRODUCTION_DOMAIN/some/path` — returns 404 | Add `[[redirects]] from = "/*" to = "/index.html" status = 200` |
| `process.env.VAR` is `undefined` at runtime | Node.js function | Env var scoped only to `builds`, not available to `functions` | `netlify env:list --scope functions --site-id $SITE_ID` | Re-set env var with `--scope all` or explicitly include `functions` scope |
| Split test A/B cookie loops; user stuck on variant | Browser cookie / SWR cache | Split test branch deploy failed; traffic cookie set to unavailable branch | `curl "https://api.netlify.com/api/v1/sites/$SITE_ID/split_tests" \| jq .` | Pause split test; clear `nf_ab` cookie; fix failing branch deploy |
| Edge function returns `500` on Deno-incompatible code | Browser `fetch` | Node.js-specific API (`fs`, `process.env`, `require`) used in Deno-based edge function | Deploy log: `ReferenceError: require is not defined` | Rewrite edge function using Web API equivalents; move to serverless for Node.js APIs |
| Identity sign-up emails never arrive | `netlify-identity-widget` | External SMTP provider misconfigured or GoTrue email template broken | `curl "https://api.netlify.com/api/v1/sites/$SITE_ID/identity" \| jq .email_template` | Verify SMTP settings in Identity dashboard; test with Netlify's built-in mailer first |
| Large media image returns 400 | `<img>` tag / `next/image` | Invalid transform parameters in URL query string | `curl -I "https://$SITE_NAME.netlify.app/.netlify/large-media/$PATH?nf_resize=invalid"` | Use only supported transforms: `nf_resize=fit\|smartcrop`, `w=`, `h=` |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Build time creep from growing static page count | Build time increasing ~30s per week for SSG sites | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=20" \| jq '[.[] \| .deploy_time] \| add/length'` | Weeks | Enable incremental builds (Gatsby DSG, Next.js ISR); use on-demand builders for rarely-visited pages |
| Function cold start latency growing | p95 cold start rising above 1s and trending up | `netlify functions:invoke $FUNCTION_NAME --payload '{}'` (measure wall time) | Days–weeks | Audit function bundle size; switch from CommonJS `require` to ESM; lazy-load heavy SDKs |
| CDN cache hit rate declining | TTFB rising on static assets despite no traffic increase | `curl -I https://$PRODUCTION_DOMAIN/static/main.js \| grep -i "x-cache\|age"` | Days | Check `Cache-Control` headers in `netlify.toml`; ensure static asset paths are fingerprinted |
| Bandwidth usage trending toward plan limit | Month-over-month bandwidth growth > 20% | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID" \| jq .published_deploy.url` then check dashboard usage | Weeks | Move large binaries and media to external storage (S3, Cloudflare R2); add aggressive cache headers |
| Build plugin execution time growing | Post-build plugin step taking longer each build | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID/log" \| jq '[.[] \| select(.message \| test("plugin")) \| .message]'` | Days | Pin plugin version; review plugin changelog for performance regressions |
| Identity MAU approaching free tier limit (1000) | MAU growing toward limit in dashboard | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/identity" \| jq .` | Weeks | Migrate to external auth provider (Auth0, Supabase Auth) before limit causes sign-up failures |
| Form spam submissions growing | Submission volume increasing without real user growth | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/submissions?per_page=20" \| jq '.[].data'` | Days | Enable reCAPTCHA or honeypot field; filter in form notification handler |
| Let's Encrypt cert expiry approaching | Cert expiry < 30 days without auto-renew confirmation | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/ssl" \| jq .expires_at` | 30 days | Confirm DNS is still pointing to Netlify; trigger manual cert renewal via API |
| Redirect rule count growing causing routing slowdowns | Page load times slowly increasing on redirect-heavy routes | `wc -l netlify.toml` and count `[[redirects]]` blocks | Weeks | Consolidate redirect rules; move complex routing logic to edge functions |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Netlify Full Health Snapshot
# Usage: NETLIFY_TOKEN=xxx SITE_ID=xxx PRODUCTION_DOMAIN=xxx bash snapshot.sh

echo "=== Netlify Platform Status ==="
curl -s https://www.netlifystatus.com/api/v2/summary.json \
  | jq '.components[] | select(.status != "operational") | {name, status}'

echo ""
echo "=== Recent Deploys (last 10) ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=10" \
  | jq '[.[] | {state: .state, id: .id, created_at: .created_at, error_message: .error_message, deploy_time: .deploy_time}]'

echo ""
echo "=== Currently Published Deploy ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID" \
  | jq '{published_deploy: .published_deploy.id, url: .url, ssl_url: .ssl_url}'

echo ""
echo "=== DNS & Certificate Status ==="
dig CNAME "www.$PRODUCTION_DOMAIN" +short
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/ssl" \
  | jq '{state: .state, expires_at: .expires_at}'

echo ""
echo "=== Deployed Functions ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/functions" \
  | jq '[.[] | {name: .name, runtime: .runtime}]'

echo ""
echo "=== Active Split Tests ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/split_tests" \
  | jq '[.[] | {id: .id, active: .active, branches: [.branches[].branch]}]'

echo ""
echo "=== Environment Variables Count by Scope ==="
netlify env:list --site-id "$SITE_ID" 2>/dev/null | grep -c "." || \
  curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
    "https://api.netlify.com/api/v1/accounts/$ACCOUNT_ID/env?site_id=$SITE_ID" \
    | jq 'length'
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Netlify Performance Triage
# Usage: NETLIFY_TOKEN=xxx SITE_ID=xxx DEPLOY_ID=xxx FUNCTION_NAME=xxx bash perf-triage.sh

echo "=== Last 5 Build Durations ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=5" \
  | jq '[.[] | {id: .id, deploy_time: .deploy_time, state: .state, created_at: .created_at}]'

echo ""
echo "=== Build Log Error Lines ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID/log" \
  | jq -r '.[].message' | grep -iE "error|failed|killed|timeout|OOM" | head -30

echo ""
echo "=== Function Bundle Size ==="
if [ -d ".netlify/functions-serve/$FUNCTION_NAME" ]; then
  du -sh ".netlify/functions-serve/$FUNCTION_NAME/"
else
  echo "Run 'netlify dev' first to build function bundles locally"
fi

echo ""
echo "=== Function Invocation Test (local timing) ==="
if command -v netlify &>/dev/null; then
  time netlify functions:invoke "$FUNCTION_NAME" --payload '{"test": true}' 2>&1 | tail -5
fi

echo ""
echo "=== Static Asset Cache Status ==="
curl -sI "https://$PRODUCTION_DOMAIN/" | grep -iE "x-cache|age|cache-control|x-nf-request-id"
curl -sI "https://$PRODUCTION_DOMAIN/" | grep "x-cache" | grep -c "HIT" && echo "Cache HIT" || echo "Cache MISS"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Netlify Connection & Resource Audit
# Usage: NETLIFY_TOKEN=xxx SITE_ID=xxx bash resource-audit.sh

echo "=== Site Configuration ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID" \
  | jq '{
      name: .name,
      url: .url,
      build_command: .build_settings.cmd,
      publish_dir: .build_settings.dir,
      node_version: .build_settings.env.NODE_VERSION,
      allowed_branches: .build_settings.allowed_branches
    }'

echo ""
echo "=== Build Plugin Configuration ==="
if [ -f netlify.toml ]; then
  grep -A 5 "\[\[plugins\]\]" netlify.toml || echo "No plugins configured"
else
  echo "No local netlify.toml found"
fi

echo ""
echo "=== Identity Service Status ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/identity" \
  | jq '{enabled: .enabled, external_providers: .external_providers}'

echo ""
echo "=== Form Submissions (last 24h count) ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/forms" \
  | jq '[.[] | {name: .name, submission_count: .submission_count}]'

echo ""
echo "=== Locked Deploys ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=10" \
  | jq '[.[] | select(.locked == true) | {id: .id, created_at: .created_at}]'

echo ""
echo "=== Custom Domain & HTTPS Enforcement ==="
curl -s -H "Authorization: Bearer $NETLIFY_TOKEN" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID" \
  | jq '{custom_domain: .custom_domain, force_ssl: .force_ssl, ssl: .ssl}'
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Shared build concurrency exhaustion | Production deploys queue behind many simultaneous preview builds | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=20" \| jq '[.[] \| select(.state=="building")]'` | Cancel non-critical preview builds via API | Limit branch deploy triggers in site settings; deploy only on PR open, not every push |
| AWS Lambda us-east-1 cold start congestion | All Netlify Functions experience elevated cold starts simultaneously | Check Netlify status page for Lambda region degradation | Switch long-lived or latency-sensitive functions to Background Functions; cache responses at CDN | Architect functions to be stateless and fast-booting; keep bundles < 5 MB |
| Build minutes quota shared across team sites | Build minutes exhausted mid-month; all sites' builds blocked | Dashboard > Team > Usage — build minutes consumed | Prioritize production deploys; disable branch deploys on low-priority sites | Optimize build times; use build cache aggressively; upgrade plan |
| CDN edge node saturation during viral traffic | TTFB spikes on static assets across all sites on the same CDN node | `curl -I https://$PRODUCTION_DOMAIN/ \| grep "x-nf-request-id"` to identify serving node | Enable aggressive `Cache-Control: public, max-age=31536000` headers to maximize CDN offload | Move large media to dedicated CDN (Cloudflare R2 + CDN) separate from Netlify's CDN |
| Let's Encrypt rate limit shared across team | New custom domains fail cert provisioning; `CERT_PROVISIONING_FAILED` for multiple sites | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/ssl" \| jq .` | Wait 7 days for rate reset; use custom wildcard cert via Netlify dashboard | Use a wildcard cert covering all subdomains; avoid frequent domain removal and re-addition |
| Form processing service overload during spam surge | Form submissions delayed or dropped; submission counts stop increasing | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/submissions?per_page=5" \| jq '.[0].created_at'` | Enable reCAPTCHA immediately; temporarily disable form endpoint | Add honeypot fields and reCAPTCHA to all forms proactively |
| Build plugin dependency on shared external API | Plugin fails for all sites if its external service (e.g., CMS API) is slow | Build log: plugin step timing significantly longer than usual | Disable the plugin temporarily; set plugin timeout if supported | Pin plugin versions; add retry/fallback logic for network-dependent plugins |
| Identity GoTrue service shared instance overload | Login and sign-up latency increases across all Identity-using sites | `curl "https://$SITE_NAME.netlify.app/.netlify/identity/user" -H "Authorization: Bearer $JWT"` — measure response time | Use Netlify Identity only for low-traffic auth; migrate high-traffic auth to Auth0 or Supabase | Keep Identity MAU under 500 for reliable performance; plan migration before hitting 1000 MAU limit |
| Redirect rule evaluation overhead | Page routing latency increases as `netlify.toml` redirect count grows | Count rules: `grep -c "\[\[redirects\]\]" netlify.toml` | Move complex routing to edge functions which execute at the edge without rule list scanning | Keep redirect rule count < 200; consolidate similar patterns with regex; use edge functions for complex routing |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Netlify CDN edge outage (specific PoP) | Traffic fails at edge → DNS still resolves to dead PoP → users in that region get 502/504 → no automatic failover for cached assets | All users geographically routed to affected edge PoP | `curl -I https://$SITE_DOMAIN/ | grep -E "x-nf-request-id|HTTP/"` returns 502; Netlify status page shows edge degradation | Configure DNS failover with a secondary CDN (Cloudflare) as fallback; update TTL to 60s proactively |
| Build failure on main branch blocks all subsequent deploys | Failed deploy locks production deployment queue → hotfixes cannot ship → outage duration extends | Production site stuck at last deploy; no updates ship until build fixed | Netlify deploy list: `state: "error"` blocking queue; build log shows failure; GitHub Actions report build failure | Manually roll back to last known-good deploy via Netlify UI or API: `PATCH /api/v1/sites/$SITE_ID/deploys/$DEPLOY_ID/restore` |
| Netlify Function cold start storm | Spike in requests triggers simultaneous Lambda cold starts → all Function invocations return 502 for 2-5s → upstream retries amplify → DDoS-like load on origin | All users hitting any Netlify Function endpoint during traffic spike | `x-nf-request-id` headers present on 502s; Function duration metrics spike in Netlify analytics | Pre-warm critical functions via scheduled pings; use Netlify Background Functions for non-latency-critical tasks |
| DNS misconfiguration after domain transfer | New DNS provider drops Netlify ALIAS/CNAME → site returns NXDOMAIN → CDN cache not warmed on new path → 100% error rate | 100% of all visitors; affects all browsers and API consumers | `dig $CUSTOM_DOMAIN +short` returns empty or wrong IP; Netlify dashboard shows DNS verification failed | Roll back DNS to previous provider; or immediately add correct CNAME/ALIAS pointing to `$SITE_NAME.netlify.app` |
| Let's Encrypt certificate expiry not auto-renewed | HTTPS connections fail with `ERR_CERT_DATE_INVALID` → browsers hard-block → traffic drops to near zero | All HTTPS visitors (effectively 100% of modern users) | `echo | openssl s_client -connect $DOMAIN:443 2>/dev/null | openssl x509 -noout -dates` shows expired; Netlify dashboard shows cert error | Manually trigger renewal in Netlify UI (Domain Management → HTTPS → Renew certificate); temporarily enable HTTP if possible |
| netlify.toml syntax error after deploy | Netlify fails to parse config → redirects, headers, and functions routes broken → all deep links return 404 → SPAs fail to route | Entire site routing; all redirects, headers, and Function invocations via custom paths | Deploy log: `Error parsing netlify.toml`; all redirects return 404; `curl -I https://$SITE/$REDIRECT_PATH` returns 404 | Roll back to previous deploy immediately; validate toml with `npx netlify-cli sites:list && netlify build --dry` locally before pushing |
| Netlify Identity GoTrue outage | All login/signup calls to `/.netlify/identity` fail → authenticated features unavailable → dependent workflows (gated content, admin) down | All users requiring authentication; public content unaffected | `curl -s https://$SITE/.netlify/identity/settings | jq .` returns 5xx; browser network tab shows 503 on identity calls | Show maintenance message for auth-gated features; route to static fallback page; contact Netlify support |
| Build environment variable deletion | Build succeeds but app behaves incorrectly at runtime (missing API keys → blank pages, broken integrations) | All users of features dependent on deleted env var | Browser console shows API call failures; app logs (if any) show `undefined` for config values; correlate with deploy where env var was removed | Re-add env var in Netlify UI → Environment Variables; trigger new deploy; check `_redirects` and build output for hardcoded values |
| Form spam surge triggering Netlify rate limit | Form submission endpoint throttled → legitimate submissions return 429 → contact/lead forms silently drop data | All users submitting forms during throttle window | HTTP 429 on `POST /.netlify/forms/...`; form submission count stops growing in Netlify dashboard | Enable reCAPTCHA immediately in form settings; add honeypot field; temporarily redirect form to third-party (Formspree) |
| Dependency install failure during build (npm registry outage) | `npm install` fails → build errors → deploy blocked → site stuck at old version | Production site cannot be updated; preview deploys also blocked | Build log: `npm ERR! network request failed` or `ECONNRESET`; correlate with npm status page incident | Use npm mirror (`NPM_CONFIG_REGISTRY=https://registry.npmmirror.com`) in build env vars; or use lockfile install with vendored deps |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Node.js version bump in `netlify.toml` | Build succeeds but native modules incompatible; or newer Node breaks outdated dependencies | Immediate on next deploy | Build log shows `Error: The module was compiled against a different Node.js version`; correlate with `NODE_VERSION` change in `netlify.toml` or env var | Revert `NODE_VERSION` in Netlify environment variables; pin to previous version |
| Adding a `[[redirects]]` rule with `force = true` | Legitimate deep-link paths now redirected; previously working routes break silently | Immediate | `curl -Iv https://$SITE/path-that-should-not-redirect 2>&1 | grep "< HTTP"` shows redirect; correlate with `netlify.toml` commit adding `force = true` | Remove or narrow the force redirect; rebuild and deploy; test all routes |
| Netlify Function runtime upgrade (Node 16 → Node 20) | `require()` of ESM-only packages breaks; deprecated APIs removed | Immediate on next Function invocation | Function invocation returns 500; Netlify Function logs show `ERR_REQUIRE_ESM`; correlate with runtime change in `netlify.toml [functions]` | Revert function runtime in `netlify.toml`; or migrate to ESM (`"type": "module"` in package.json) |
| Changing `publish` directory in `netlify.toml` | Wrong directory deployed; site shows stale or incorrect build output | Immediate | `curl https://$SITE/` shows old content or 404; deploy log shows files from wrong directory; correlate with `publish` setting change | Revert `publish` directory; redeploy; ensure build script outputs to correct path |
| Removing a custom domain from Netlify site | All traffic to that domain returns 404 (Netlify serves default 404 page) | Immediate on domain removal | DNS still resolves to Netlify but domain is unlinked; Netlify API `GET /api/v1/sites/$SITE_ID` shows domain removed from `custom_domain` | Re-add domain: `PATCH /api/v1/sites/$SITE_ID` with `{"custom_domain": "$DOMAIN"}`; re-provision cert |
| Build plugin version upgrade | Plugin introduces breaking change; build fails or produces malformed output | Immediate on next deploy | Build log shows error in plugin step (e.g., `@netlify/plugin-nextjs: Error: ...`); correlate with plugin version bump in `netlify.toml` | Pin to previous plugin version: `[plugins] package = "@netlify/plugin-nextjs" pinned_version = "5.0.0"` |
| Content Security Policy header change in `netlify.toml` | New CSP blocks inline scripts or third-party assets; browser console shows `Content Security Policy violation` | Immediate for all visitors after deploy | Browser console `Refused to execute inline script` or `refused to load resource from ...`; correlate with `[[headers]]` change in `netlify.toml` | Revert CSP header change; audit required sources; add missing directives incrementally |
| `netlify.toml` context-specific env var override | Staging env var bleeds into production deploy (wrong context match); or production deploys missing env vars | Immediate after deploy | App API calls fail with auth errors; correlate with `[context.production.environment]` change in `netlify.toml`; check `netlify env:list --context production` | Fix context block in `netlify.toml`; override in Netlify UI under Environment Variables scoped to production context |
| Switching from `yarn` to `npm` as package manager | `yarn.lock` ignored; version drift in dependencies; different sub-dependencies resolved | Immediate on next install | Build succeeds but runtime behavior differs; `npm ls <package>` shows different version than `yarn list`; correlate with `NPM_FLAGS` or package manager config change | Revert to yarn: set `NPM_FLAGS` env var or restore `.yarnrc` in repo root |
| Enabling branch deploy previews for all branches | Build minute consumption explodes; build queue congestion; accidental deploys from feature branches hitting production-like infrastructure | Within hours of enabling | Netlify usage dashboard shows build minutes spiking; multiple concurrent builds queuing; correlate with "All branches" deploy setting change | Restrict to named branches or PR-only: set `branches.deploy_previews = "preview-only"` in `netlify.toml` |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Stale CDN cache serving old deploy assets after rollback | `curl -I https://$SITE/main.js | grep -E "x-nf-deploy-id|cache"` | After rollback, some edge nodes still serve assets from reverted deploy; JS/CSS mismatch causes app to break for some users | Subset of users see broken UI; dependent on which CDN PoP they hit | Trigger cache purge via Netlify API: `POST /api/v1/sites/$SITE_ID/deploys/$DEPLOY_ID/restore`; add deploy ID to asset filenames for cache-busting |
| Split-deploy: HTML from new deploy, JS from old CDN cache | `curl -sI https://$SITE/_next/static/chunks/main.js | grep x-nf-deploy-id` vs HTML deploy ID | Browser fetches new HTML referencing new JS chunk names, but CDN serves cached old chunks → 404 for new assets | `ChunkLoadError` in browser console; broken SPA navigation; 404 on JS/CSS assets for some users | Force asset filenames to include content hash (default in Next.js/Vite); set short `Cache-Control` for HTML: `Cache-Control: no-cache` |
| Netlify Forms: duplicate submission processing | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/submissions?per_page=20" | jq '[.[] | {id, created_at, data}]'` | Retry on network error submits form twice; duplicate entries in Netlify Forms dashboard | Duplicate leads/contacts in CRM; notification emails sent twice | Add idempotency key as hidden field; deduplicate in webhook handler; use client-side `submitOnce` flag |
| Config drift: `netlify.toml` in repo vs site settings in UI | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID" | jq '{build_command, publish_dir}'` vs `netlify.toml` | Build command or publish dir in UI overrides `netlify.toml`; repo change has no effect | Repo changes to build settings ignored; confusing inconsistency between what's in code and what runs | Enforce `netlify.toml` as source of truth; remove conflicting UI overrides; document that UI settings take precedence over `netlify.toml` |
| Redirect loop: two `[[redirects]]` rules pointing at each other | `curl -Iv --max-redirs 5 https://$SITE/path 2>&1 | grep "< HTTP"` | Browser shows `ERR_TOO_MANY_REDIRECTS`; all traffic to affected path fails | 100% failure rate for affected URL paths | Audit `netlify.toml` for circular redirects; add `status = 200` on final rule; test with `netlify dev` locally |
| Branch deploy serving wrong environment variables | `curl https://$BRANCH_DEPLOY_URL/api/health` returns production data | Branch deploy incorrectly uses production API keys; test data writes to production database | Data corruption in production; PII exposure in logs | Set context-specific env vars: `[context.branch-deploy.environment]` in `netlify.toml`; use separate API credentials per environment |
| Function deploy version mismatch: new function code, old HTML | `curl -H "Authorization: Bearer $TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/functions"` | Function API contract changed but old HTML still calls old endpoints → `404` or `400` from new function | API consumers (HTML/SPA) get errors; backwards-incompatible Function changes break live users | Version Function endpoints (e.g., `/.netlify/functions/v2/handler`); deploy atomically by ensuring HTML and Function deploy together |
| DNS propagation lag after custom domain change | `dig $DOMAIN @8.8.8.8 +short` vs `dig $DOMAIN @1.1.1.1 +short` | Different resolvers return different IPs during propagation; some users see old site, some see new | Inconsistent user experience; transactions started on old site may fail on new | Set TTL to 60s 24h before any DNS change; wait for full TTL expiry before cutting over |
| Netlify Identity JWT token issued from wrong site | `curl -s https://$SITE/.netlify/identity/user -H "Authorization: Bearer $JWT" | jq .` | JWT from site A accepted (or rejected) on site B; cross-site auth confusion | Authentication bypasses or false rejections; particularly dangerous if multiple sites share Identity instance | Validate `aud` claim in JWT matches current site URL; do not reuse Identity tokens across sites |
| robots.txt redirect causing search engine inconsistency | `curl -I https://$SITE/robots.txt` | `robots.txt` redirected to `/*.html` version; Googlebot follows redirect to disallowed path; site delisted | SEO impact; deindexing of site over days/weeks | Ensure `robots.txt` returns 200 with `Content-Type: text/plain`; check `[[redirects]]` for unintentional wildcard match |

## Runbook Decision Trees

### Decision Tree 1: Netlify Deploy Failure
```
Is the latest deploy in error state?
curl -H "Authorization: Bearer $NETLIFY_TOKEN"
  "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=1" | jq '.[0].state'
├── YES → Is it a build error or a processing error?
│         (check: jq '.[0].error_message' from deploy JSON)
│         ├── BUILD ERROR → Is it a dependency install failure?
│         │   (build log: curl "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID/log")
│         │   ├── YES → Root cause: package lock mismatch or registry outage
│         │   │         Fix: pin dependency versions; clear build cache in Netlify UI;
│         │   │              retry deploy
│         │   └── NO  → Is it a framework build error (e.g. React/Vite type error)?
│         │             ├── YES → Root cause: code change broke build
│         │             │         Fix: revert commit; deploy last good SHA via
│         │             │              curl -X POST https://api.netlify.com/api/v1/deploys/$GOOD_DEPLOY_ID/restore
│         │             └── NO  → Check for Netlify build image issue:
│         │                       Try switching build image in Site Settings → Build & Deploy
│         └── PROCESSING ERROR → Is it a deploy timeout (> 30 min)?
│             ├── YES → Root cause: large asset processing / Functions bundling too slow
│             │         Fix: reduce Functions bundle size; exclude unused node_modules
│             └── NO  → File upload failure → Check Netlify status page for CDN issues;
│                       retry deploy; if persistent open Netlify support ticket
└── NO  → Deploy succeeded but site returning errors?
          ├── Check Function errors: Netlify Dashboard → Functions → Recent invocations
          │   ├── 502/503 → Function crashed → check logs; redeploy with fix
          │   └── Timeout → Function exceeding 10s limit → optimize or use Background Function
          └── Static assets 404 → Check publish directory in netlify.toml
                                   build.publish should match framework output dir
```

### Decision Tree 2: Netlify Function Latency or Errors Spike
```
Are Netlify Functions returning elevated 5xx or high latency?
├── YES → Is the issue on all functions or a specific one?
│         ├── ALL functions → Check AWS Lambda us-east-1 health (Netlify Functions run there)
│         │   ├── AWS degraded (check status.aws.amazon.com) → Nothing to do; wait + communicate
│         │   └── AWS healthy → Check Function bundle size: functions/ directory
│         │       if > 50 MB unzipped → Root cause: oversized bundle
│         │       Fix: add .netlifyignore; use dynamic imports; split functions
│         └── ONE function → Is it a cold start latency spike (first request > 2s)?
│             ├── YES → Root cause: Lambda cold start
│             │         Fix: keep bundle < 5 MB; avoid synchronous require() of large modules;
│             │              use Background Functions for long-running work
│             └── NO  → Is it a runtime error? (check function invocation logs)
│                       ├── YES → Root cause: unhandled exception
│                       │         Fix: add error handling; redeploy; rollback if critical
│                       └── NO  → Check external dependency (DB/API) latency from function
│                                 Add timeout + circuit breaker in function code
└── NO  → Are users seeing stale content despite successful deploy?
          ├── YES → CDN cache not purged → manually purge:
          │         curl -X POST -H "Authorization: Bearer $NETLIFY_TOKEN"
          │           https://api.netlify.com/api/v1/sites/$SITE_ID/deploys (trigger new deploy)
          └── NO  → Investigate Netlify Identity or Forms service independently
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Build minutes quota exhausted | Builds triggered on every push including draft PRs; 500 build minutes/month consumed | Netlify Dashboard → Team → Usage → Build minutes; or `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/accounts/$ACCOUNT_SLUG/bandwidth"` | All site builds blocked until month reset or upgrade | Disable branch deploys for non-critical branches; cancel queued builds via API | Restrict build triggers to `main` and explicit PR labels; use `[skip ci]` in commit messages |
| Bandwidth overrun from unoptimized assets | Large uncompressed images or JS bundles served directly; CDN bandwidth charges | Netlify Dashboard → Analytics → Bandwidth tab | Overage charges at $0.20/GB above plan limit | Enable Netlify Large Media or migrate assets to Cloudflare R2 | Compress images at build time with `@netlify/plugin-image-optim`; enable Brotli compression |
| Function invocation explosion from bot traffic | Scraper or misconfigured cron hitting Functions endpoint thousands of times/hour | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/analytics?from=$(date -d '1 hour ago' +%s)000" \| jq '.functions'` | Function invocation limit hit; legitimate calls rejected | Add rate limiting in function; block offending IP/User-Agent in `netlify.toml` redirect rule with `status = 429` | Deploy WAF via Netlify edge function; add bot detection (`cf-ipcountry` header checks) |
| Form submission spam filling storage | Spam bots submitting forms; submission storage quota consumed | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/submissions?per_page=5" \| jq '.[].created_at'` — same timestamp burst | Form storage quota hit; legitimate submissions dropped | Enable reCAPTCHA on form: add `data-netlify-recaptcha="true"` attribute | Add honeypot field `<p class="hidden"><input name="bot-field"></p>`; enable spam filtering in form settings |
| Excessive split testing variants consuming bandwidth | A/B test with many variants increases effective bandwidth per pageview | Netlify Dashboard → Split Testing tab — count active variants | Bandwidth proportional to variant count × traffic | Reduce active split test variants to 2; disable inactive tests | Set split test end dates; disable tests after statistical significance achieved |
| Identity service MAU overage | Monthly Active Users exceeding free tier (1000 MAU); billed at $0.99/100 MAU | Netlify Dashboard → Identity tab → Usage | Billing spike; no functional impact | Migrate auth to dedicated provider (Auth0 free tier: 7500 MAU) | Monitor Identity MAU monthly; plan migration before reaching 800 MAU |
| Concurrent build slots all consumed | Team plan has 3 concurrent builds; CI pipeline triggers many simultaneous builds | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=20" \| jq '[.[] \| select(.state=="building")] \| length'` | Deploys queue; production hotfixes delayed | Cancel non-critical builds via API: `curl -X DELETE -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/deploys/$BUILD_ID/cancel"` | Serialize build pipelines in CI; add `concurrency` groups in GitHub Actions |
| Large function bundle causing slow cold starts and storage | `node_modules` bundled into function zip; zip > 50 MB | `ls -lh .netlify/functions-internal/*.zip 2>/dev/null` | Every cold start 3-8s; poor user experience | Add `external_node_modules` in `netlify.toml` functions config; use esbuild bundler | Configure `[functions] node_bundler = "esbuild"` in `netlify.toml`; audit bundle with `npm ls --prod` |
| Analytics API polling creating excessive API calls | Monitoring script polling Netlify Analytics API every minute | Count API calls in Netlify Dashboard → Integrations → API usage | API rate limit hit (500 req/min); monitoring gaps | Increase polling interval to 15 min; cache analytics responses | Use Netlify webhooks for deploy events instead of polling; subscribe to build notifications |
| Deploy preview never cleaned up for stale PRs | Deploy previews accumulate for hundreds of merged/closed PRs | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=100" \| jq '[.[] \| select(.context=="deploy-preview")] \| length'` | Storage and DNS record accumulation; minor cost impact | Enable automatic deploy preview deletion in site settings; manually delete old ones | Configure `[context.deploy-preview]` with `delete_on_close = true`; clean up via API after PR merge |

## Latency & Performance Degradation Patterns
| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Cold start latency on Netlify Functions | First request after idle period takes 3–8s; subsequent requests fast | `curl -w "\ntime_total: %{time_total}\n" https://$SITE_DOMAIN/.netlify/functions/$FUNC_NAME` — compare first vs second call | Function container recycled after inactivity; Node.js module initialization on cold path | Reduce bundle size with esbuild; lazy-load heavy modules; add scheduled synthetic ping every 5 min to keep warm |
| CDN cache miss storm on deploy | After new deploy, all CDN edge nodes have empty cache; all requests hit origin Functions simultaneously | Netlify Analytics: request spike at deploy time; Function invocation count spikes | Atomic deploy invalidates all CDN cache globally; no cache warming | Pre-warm critical paths via post-deploy script: `curl https://$SITE_DOMAIN/` for top pages; use `Cache-Control: s-maxage=3600` for static assets |
| Large unoptimized JS bundle causing high TTFB | Time-to-first-byte for main JS chunk > 2s; Netlify Edge CDN serves fast but client parse time high | `curl -w "%{time_starttransfer}" -o /dev/null https://$SITE_DOMAIN/main.chunk.js` | No code splitting; all JS in single bundle; `netlify.toml` missing build optimization | Enable code splitting in Vite/Webpack; add `netlify-plugin-minify-html`; set `_headers` for `Cache-Control: public, max-age=31536000, immutable` on hashed assets |
| Function thread pool saturation from synchronous DB calls | Functions take 5–10s; Netlify function timeout (26s default) reached under load | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/functions" | jq '.[] | {name, timeout, invocations}'` | Synchronous blocking I/O in function; Netlify single-threaded Node.js per invocation | Use `async/await` with connection pooling; switch to async DB clients; use Netlify Background Functions for long-running tasks |
| Slow redirect chain increasing page load time | Page redirects 3–5 times before final destination; each hop adds 100–200ms RTT | `curl -L -w "%{redirect_url}\n%{time_redirect}\n" -o /dev/null https://$SITE_DOMAIN/$PATH` | Multiple redirect rules in `_redirects` or `netlify.toml` chain without consolidation | Consolidate redirect chains; use direct source→destination rules; check for trailing slash redirect + language redirect + auth redirect stacking |
| Edge function CPU timeout on complex processing | Netlify Edge Function times out (50ms CPU limit per invocation); 500 returned | Netlify Dashboard → Edge Functions → Invocations tab — filter by status 500; check timeout logs | CPU-intensive logic (JSON parsing, crypto) in edge function exceeds Deno isolate 50ms CPU budget | Offload CPU-heavy work to serverless Functions (not Edge); limit Edge Functions to header manipulation and simple routing |
| Build caching disabled after dependency change | Every build runs full `npm install`; 8-min build becomes 12 min | Netlify Dashboard → Deploy → Build log: `No cached dependencies found` on every build | `package-lock.json` changes on every build (e.g., non-deterministic lock file); cache invalidated | Pin dependency versions; commit `package-lock.json`; use `netlify.toml` `[build] command` with cache path configuration |
| Serialization overhead for large JSON API responses | Netlify Function returning large paginated datasets takes > 5s | `time curl https://$SITE_DOMAIN/.netlify/functions/api?page=1\&size=1000` | No pagination or streaming; full dataset loaded into memory and serialized as one response | Implement cursor-based pagination; stream responses using `Response` with `ReadableStream`; limit default page size to 100 |
| Batch processing misconfiguration in Background Functions | Background Function processes events one-at-a-time instead of batched; quota of 15 min timeout wasted | Netlify Dashboard → Background Functions → check invocation duration approaching 15-min limit | Missing batch loop; each invocation processes single item from queue | Restructure Background Function to process up to 100 items per invocation; use SQS or Upstash Queue as input |
| Downstream API latency (third-party in serverless function) | Function p99 latency tracks third-party API latency; no timeout configured | `curl -w "%{time_connect} %{time_starttransfer} %{time_total}\n" https://third-party-api.com/endpoint` | No `AbortController` timeout on `fetch()`; function waits up to 26s for slow upstream | Add `AbortController` with 5s timeout: `const ctrl = new AbortController(); setTimeout(() => ctrl.abort(), 5000); fetch(url, {signal: ctrl.signal})` |

## Network & TLS Failure Patterns
| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on custom domain | Browser shows `NET::ERR_CERT_DATE_INVALID`; `curl -v https://$CUSTOM_DOMAIN` shows expired cert | `openssl s_client -connect $CUSTOM_DOMAIN:443 2>&1 | grep "notAfter"` — past date | Netlify auto-renewal via Let's Encrypt failed; DNS misconfiguration blocked renewal ACME challenge | Trigger manual renewal: Netlify Dashboard → Domain Settings → HTTPS → Renew certificate; or `curl -X POST -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/ssl"` |
| Let's Encrypt rate limit hit during cert provisioning | New site or custom domain HTTPS provisioning fails with `too many certificates` error | Netlify Dashboard → Domain Settings → HTTPS — error message; check https://crt.sh/?q=$DOMAIN for recent issuances | More than 5 duplicate certs issued in 7 days for same domain (Let's Encrypt rate limit) | Wait 7 days for rate limit to reset; or use Netlify's wildcard cert by adding domain as subdomain of `netlify.app`; consider Netlify Pro for managed cert |
| DNS propagation delay after custom domain change | Site unreachable from some regions; `nslookup $CUSTOM_DOMAIN` returns different IPs from different DNS servers | `dig $CUSTOM_DOMAIN @8.8.8.8` vs `dig $CUSTOM_DOMAIN @1.1.1.1` — inconsistent results | DNS TTL not expired on all resolvers; old IP still cached | Wait for TTL expiry (check old TTL: `dig +nocmd $DOMAIN any +noall +answer | grep TTL`); use low TTL (300s) before migration |
| TCP connection exhaustion from client-side connection storm | Mass traffic event; CDN edge node connection queue full; some requests dropped | Netlify Status page at https://www.netlifystatus.com/; `curl -I https://$SITE_DOMAIN` — check `x-nf-request-id` header present | Traffic spike exceeds CDN edge node connection limit for origin fetch | Enable Netlify Bot Protection; add rate limiting in Edge Function; contact Netlify support for traffic surge pre-provisioning |
| Load balancer misconfiguration causing split-brain deploys | Some requests return old deploy, others return new deploy during atomic swap | `curl -H "Cache-Control: no-cache" https://$SITE_DOMAIN/ -v | grep "x-nf-request-id"` — compare deploy IDs across multiple requests | Netlify CDN in process of propagating new deploy globally; edge nodes at different deploy versions | Wait 60–120s for global deploy propagation; use `x-nf-deploy-id` response header to confirm deploy version; do not roll back unless functionally broken |
| Packet loss causing WebSocket disconnections for real-time features | WebSocket connections to Netlify Functions drop intermittently; client reconnects frequently | Client-side: `ws.onerror` fires; server-side Function logs `WebSocket connection closed unexpectedly`; `mtr --report $SITE_DOMAIN` shows packet loss | Note: Netlify Functions do not support WebSockets natively; packet loss on edge-to-client path | Migrate real-time features to Netlify's partner integrations (Ably, Pusher) or use SSE (Server-Sent Events) which is HTTP-based |
| MTU mismatch causing truncated responses for large pages | Large HTML pages (> 1500 bytes) occasionally return partial content; GZIP decompression fails client-side | `curl -H "Accept-Encoding: identity" https://$SITE_DOMAIN/large-page | wc -c` vs `Content-Length` header | Network path MTU smaller than response segment size; rare on Netlify CDN but can occur on enterprise proxy chains | Add `netlify.toml` `[[headers]]` with `path = "/*"` and explicit `Content-Encoding: gzip`; verify with `curl --compressed` |
| Firewall/WAF rule blocking legitimate traffic | 403 responses for legitimate API requests; `x-nf-request-id` present but body is Netlify error | `curl -v -H "User-Agent: Mozilla/5.0" https://$SITE_DOMAIN/.netlify/functions/api` — check response body | Netlify WAF rule (if enabled) or `_headers` file rule misconfigured; bot protection false positive | Review Netlify Bot Protection settings in Dashboard; add `X-Robots-Tag: noindex` exemptions; whitelist legitimate user agent patterns |
| SSL/TLS handshake timeout from old cipher suites | Old clients (IE11, Android 4.x) cannot connect; `SSL_ERROR_NO_CYPHER_OVERLAP` | `curl --tls-max 1.1 https://$SITE_DOMAIN` — should fail as Netlify enforces TLS 1.2+ | Netlify enforces TLS 1.2 minimum; clients requiring TLS 1.0/1.1 cannot connect | Netlify does not support TLS 1.0/1.1 — inform stakeholders; provide fallback non-TLS path if regulatory required |
| Connection reset from Netlify origin on oversized request body | Function POST request with body > 6MB returns `413 Request Entity Too Large` or connection reset | `curl -X POST -H "Content-Length: 7000000" --data-binary @large-file.bin https://$SITE_DOMAIN/.netlify/functions/upload` | Netlify Functions have 6MB max request body limit | Use Netlify Large Media or pre-signed S3 upload URLs for files > 1MB; stream uploads directly to S3 bypassing Functions |

## Resource Exhaustion Patterns
| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| Build minutes quota exhausted | Builds fail with `Build minutes limit reached`; no new deploys possible | Netlify Dashboard → Team → Usage → Build minutes; or `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/accounts/$ACCOUNT_ID/bandwidth"` | Upgrade plan or wait for monthly reset; cancel queued builds: `curl -X DELETE -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/deploys/$ID/cancel"` | Restrict branch deploys; use `[skip ci]` for docs commits; split large monorepo into separate sites |
| Function execution timeout exhaustion | Functions returning 502 after 26s (default); background functions returning 502 after 15 min | `curl -w "\ntime_total: %{time_total}\n" https://$SITE_DOMAIN/.netlify/functions/$FUNC_NAME` — value approaching 26 | Long-running operation exceeds Function timeout | Refactor to use Netlify Background Functions (15 min); break into chained function calls; offload to external async queue |
| Bandwidth overage | Monthly bandwidth exceeded; Netlify bills overage at $0.20/GB | Netlify Dashboard → Analytics → Bandwidth tab; `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID" | jq .published_deploy.summary` | Add `Cache-Control: public, max-age=31536000` on static assets to increase CDN cache hit rate; compress images | Enable Netlify image CDN; use Brotli compression in `netlify.toml`; implement CDN cache headers |
| Function memory exhaustion (1024MB limit) | Function OOM crash; Netlify returns 502; logs show `JavaScript heap out of memory` | Netlify Dashboard → Functions → select function → Invocation logs — filter for error 502 | Reduce in-memory data processing; stream large datasets instead of loading all into memory; implement pagination | Limit result set sizes; use streaming Node.js patterns; test memory usage locally with `--max-old-space-size` flag |
| Concurrent build slot exhaustion | Builds queue; production hotfix blocked for minutes | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=10" | jq '[.[] | select(.state=="building")] | length'` | Cancel non-production builds: `curl -X DELETE -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/deploys/$ID/cancel"` | Limit concurrent builds with GitHub Actions `concurrency` groups; use `build.ignore` in `netlify.toml` for unchanged packages |
| Identity MAU limit exhausted | New user signups fail; Netlify Identity returns 403 or 429 | Netlify Dashboard → Identity → Usage — MAU counter at limit | Upgrade to Netlify Pro Identity; or migrate to Auth0 (7500 MAU free tier) | Monitor Identity MAU monthly; alert at 80% of plan limit; plan migration before hard limit hit |
| Form submission storage quota hit | New form submissions silently dropped or return error | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/submissions?per_page=1"` — check total count | Export and delete old submissions via Netlify API; upgrade plan for more storage | Enable spam filtering; export submissions weekly via API and delete from Netlify; implement honeypot |
| Disk space for build cache exhausted | Builds take full time without caching; `No space left on device` in build log | Netlify Dashboard → Deploys → Build log — search for disk space errors or cache miss messages | Clear build cache: Netlify Dashboard → Deploys → Clear cache and retry deploy | Clear `node_modules` cache periodically; use `netlify.toml` `[build] ignore` to skip unnecessary dependency installs |
| DNS zone record limit | Cannot add new DNS records; Netlify returns error on DNS zone update | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/dns_zones/$ZONE_ID/dns_records" | jq length` | Delete obsolete records; contact Netlify support for limit increase; migrate DNS to Route 53 if needed | Review and prune old DNS records quarterly; use wildcard records where applicable |
| Ephemeral port exhaustion during Function invocation storm | Functions cannot make outbound HTTP requests; `EADDRNOTAVAIL` in Function logs | Not directly accessible in Netlify managed environment; infer from failed outbound requests | Implement request queuing in Function with `p-limit` to cap concurrent outbound fetches; reduce Function concurrency via rate limiting | Limit concurrency in Functions; use connection pooling for DB connections; batch API calls |

## Distributed Transaction & Event Ordering Failures
| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation in Form submission webhook | Netlify sends form submission webhook; target server times out; Netlify retries; endpoint processes duplicate | Check submission timestamp: `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/submissions?per_page=50" | jq '.[] | {id, created_at}'` — look for rapid duplicates | Duplicate form submissions processed; CRM or database records duplicated | Add idempotency check in webhook handler using Netlify `submission_id`; deduplicate on `x-netlify-submission-id` header |
| Partial deploy failure leaving inconsistent static assets | Deploy completes partially; some JS chunks reference new hashes not yet on CDN; 404 for chunks | `curl -I https://$SITE_DOMAIN/static/js/main.abc123.chunk.js` — 404 during deploy propagation | JavaScript runtime errors for users during deploy propagation window (60–120s) | Enable deploy locking: Netlify atomic deploys should prevent this; if recurring, investigate split CDN propagation; use `netlify deploy --prod` not environment promotion |
| Out-of-order webhook delivery causing state machine corruption | Netlify deploy webhook fires `deploy_created` → `deploy_building` → `deploy_ready` out of order due to network conditions | Compare webhook payload `created_at` timestamps vs receipt order in your webhook handler logs | Downstream state machine in wrong state; monitoring thinks deploy succeeded when it failed | Add sequence validation in webhook handler: only transition state if new event timestamp > last seen timestamp; use Netlify API to verify actual deploy state: `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID"` |
| Function invocation duplicate from client retry without idempotency key | Client retries POST to Netlify Function on timeout; Function processes twice (payment, order creation) | Check application logs for duplicate order IDs with similar timestamps; no direct Netlify-side detection | Duplicate payments or orders processed | Require idempotency key header in all mutating Function calls; store processed keys in KV (Netlify Blobs or Redis): `if (await kv.get(idempotencyKey)) return cached_response` |
| Compensating action failure after Background Function crash | Background Function starts long job (e.g., resize images, send emails); crashes mid-way; no compensation | Netlify Dashboard → Background Functions → filter status `error`; check which operations completed before crash | Partial job completion; e.g., some emails sent, some not; inconsistent state | Implement checkpoint pattern: write progress to Netlify Blobs after each batch; on retry, read checkpoint and resume from last completed step |
| At-least-once delivery duplicate from Netlify webhook retry | Webhook delivery fails with 5xx; Netlify retries up to 10 times; all retries succeed; endpoint processes 10× | Downstream database shows multiple records with same Netlify `deploy_id`; webhook handler logs show same payload twice | Duplicate notifications, duplicate CI triggers, duplicate analytics events | Make webhook handlers idempotent: store `deploy_id` in DB with unique constraint; skip processing if already handled: `INSERT ... ON CONFLICT DO NOTHING` |
| Distributed lock contention during multi-site deploy coordination | Multiple Netlify sites deployed simultaneously; shared resource (database migration) run concurrently by each deploy hook | Check deploy webhook timestamps in Netlify Dashboard → Deploys for concurrent events | Database migration runs multiple times; schema conflicts | Implement external distributed lock (Redis `SET NX PX`); deploy hook checks lock before running migration; only first acquirer runs migration |
| Stale deploy preview serving outdated API contract | Deploy preview uses preview URL but calls production API; API schema changed in same PR; preview tests wrong contract | `curl https://deploy-preview-$PR--$SITE_NAME.netlify.app/api/health | jq .version` vs production version | Test results in deploy preview don't reflect actual PR changes; false confidence in review | Configure Netlify Function environment per context in `netlify.toml` `[context.deploy-preview.environment]`; deploy preview should use preview API URL or mock |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor on shared Function runtime | Function p99 latency spikes without code change; Netlify platform-wide slowdown reported | Tenant Functions cold start time increases; timeout rates rise | No direct isolation command (managed platform); implement retry with exponential backoff in client | Use Netlify Edge Functions (runs on Cloudflare Workers — better isolation) for latency-sensitive paths; contact Netlify support if systematic |
| Memory pressure from co-located large Function | Other Functions get OOM-killed due to shared container memory | Functions return 502 with no code change; Netlify Dashboard Functions logs show `memory limit exceeded` for neighbor | Redeploy with smaller Function bundle: `netlify deploy --prod`; split large Functions into smaller ones | Keep individual Functions under 50MB bundle size; use dynamic imports; avoid loading entire SDK at cold start |
| Build slot saturation from one team's CI pipeline | Production deploy queues behind auto-generated preview builds | Emergency hotfix cannot deploy for 10+ minutes | Cancel non-critical builds: `curl -X DELETE -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/deploys/$DEPLOY_ID/cancel"` | Use `netlify.toml` `[build] ignore` script to skip builds for docs-only changes; configure GitHub Actions `concurrency` to limit parallel deploy triggers |
| Bandwidth monopoly from one site's viral traffic | CDN hit rate drops; origin requests surge; other sites on account slow | Other sites under same Netlify account may share CDN capacity; latency increases | Enable Netlify High-Performance CDN (upgrade plan); use `Cache-Control: s-maxage=31536000` on static assets to maximize CDN caching | Add `Cache-Control` headers in `netlify.toml`; enable Netlify image CDN; use asset fingerprinting for long cache TTL |
| Form submission spam overwhelming storage quota | Legitimate form submissions rejected; quota consumed by bot spam | Site forms stop working; submissions return 500 or silent drop | Enable spam filtering: Netlify Dashboard → Forms → Settings → Enable spam filtering (Akismet) | Add honeypot field: `<input name="bot-field" style="display:none">`; implement reCAPTCHA in form; add `netlify-plugin-form-spam-filter` |
| Identity MAU quota exhaustion from shared account | New user signups fail with 403; existing users unaffected | Applications using Netlify Identity cannot onboard new users | Monitor: Netlify Dashboard → Identity → Usage; no runtime isolation available | Export users and migrate to Auth0 or Supabase Auth before hitting limit; plan MAU capacity monthly; upgrade to higher Identity tier |
| Concurrent Function invocation surge from one integration | Other team's webhook integration triggers 10K Function invocations in 1 minute; quota hit | Other sites sharing account Functions quota get 429 responses | Rate limit the integration webhook source; add `netlify-plugin-rate-limiting` or edge function rate limiter | Separate high-invocation Functions into dedicated Netlify account; implement token bucket rate limiting in Edge Function |
| Rate limit bypass via multiple site subdomains | Attacker bypasses per-site rate limit by using multiple Netlify preview deploy URLs for same Function | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=50" \| jq '[.[] \| select(.state=="ready")] \| length'` — count active deploys | Lock old deploys: delete stale preview deploys via API; implement IP-based rate limiting in Edge Function | Implement rate limiting keyed on IP (not deploy URL) in Netlify Edge Function; lock production deploys; limit preview deploy retention |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure (no native Prometheus endpoint) | No Netlify metrics in Grafana; dashboards empty | Netlify does not expose Prometheus metrics natively; custom scraper not configured | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID" \| jq '{bandwidth_usage,ssl_status,capabilities}'` — manual check | Build custom Netlify metrics exporter using Netlify API; push metrics to Prometheus pushgateway or Datadog via scheduled Lambda |
| Trace sampling gap missing Function cold starts | APM shows normal latency; users report slow page loads; cold start overhead invisible | Netlify Functions do not natively emit distributed traces; cold start duration not in logs | Add manual timing: `const start = Date.now(); ... console.log('duration_ms', Date.now() - start)` in Function; check Netlify Dashboard → Functions → Invocation details | Instrument Functions with `@netlify/functions` + OpenTelemetry: use `@opentelemetry/sdk-node` with custom Netlify exporter; export to Honeycomb or Jaeger |
| Log pipeline silent drop (Function log retention 1 hour) | Incident postmortem finds no Function logs; 2-hour-old error invisible | Netlify Function logs retained for only 1 hour in Dashboard; no persistent log storage by default | Forward logs in real-time: `console.log(JSON.stringify({level:'error',msg,...}))` + Netlify Log Drains to Datadog | Configure Netlify Log Drains: Dashboard → Site Settings → Log Drains → Add drain to Datadog/Papertrail; retain logs for 30+ days |
| Alert rule misconfiguration (no build failure alerting) | Build fails silently; broken production deploy goes unnoticed for hours | Default Netlify email alerts go to wrong inbox; Slack notification not configured | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=5" \| jq '.[] \| {state,error_message,created_at}'` | Configure deploy notifications: Dashboard → Site Settings → Build & Deploy → Deploy notifications → Add Slack webhook for `deploy_failed` events |
| Cardinality explosion in custom Function metrics | Datadog dashboard OOM or costs spike; custom metrics with per-request tags | Function emitting `netlify.function.duration` metric with `request_id` tag for every invocation | `datadog-metrics \| grep netlify.function \| wc -l` — count distinct metric series | Remove high-cardinality tags (`request_id`, `session_id`) from custom metrics; aggregate to per-function or per-endpoint granularity only |
| Missing health endpoint for Netlify Function availability | Load testing shows 100% success but real users get errors; health check passes on stale cache | Health check hits CDN-cached response, not live Function; Function runtime errors invisible | `curl -H "Cache-Control: no-cache" https://$SITE_DOMAIN/.netlify/functions/health` — bypass CDN cache | Add `Cache-Control: no-store, no-cache` header in health Function response; configure Netlify Edge Function to bypass CDN for `/.netlify/functions/health` path |
| Instrumentation gap in Netlify Edge Function middleware | Authentication bypass occurs in edge middleware; no log evidence | Edge Function logs not forwarded to Log Drain by default in some configurations; middleware errors silent | `netlify dev` locally with `--debug` flag to reproduce; check Netlify Dashboard → Edge Functions → Invocation logs | Enable Edge Function logs in Log Drain: `netlify.toml` → `[edge_functions]` → `deno_version = "1.x"`; add explicit `console.error` in catch blocks |
| Alertmanager equivalent outage (Netlify email alerts disabled) | Site goes down; no one receives alert | Netlify alert emails going to spam; email delivery provider outage; alert email address changed | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID" \| jq '.notification_email'` — verify alert email is correct | Configure redundant alerting: Slack webhook + PagerDuty via Netlify Deploy notifications; use external uptime monitor (Better Uptime, Pingdom) as backup |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Node.js runtime version upgrade (18 → 20 in netlify.toml) | Functions fail with `TypeError: X is not a function`; crypto or stream API changed | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys/$DEPLOY_ID/log" \| grep "TypeError\|ReferenceError"` | Revert `netlify.toml`: change `NODE_VERSION = "20"` back to `"18"`; trigger redeploy: `git commit --allow-empty -m "revert node version" && git push` | Test with `nvm use 20 && npm test` locally before changing `NODE_VERSION` in `netlify.toml`; run `netlify dev` with target Node version |
| Build plugin version upgrade breaking build | After auto-upgrading Netlify Build Plugin, build fails: `Plugin returned unexpected output` | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys/$DEPLOY_ID/log" \| grep "plugin\|Plugin"` | Pin plugin version in `netlify.toml`: `[[plugins]] package = "@netlify/plugin-lighthouse" [plugins.inputs] budget_path = "budget.json"`; add version pin in package.json | Pin all build plugin versions; review plugin changelog before upgrading; test upgrades on branch deploy first |
| Framework adapter migration (Gatsby → Next.js on Netlify) | Deploy succeeds but ISR/SSR pages return 404; Netlify Next.js runtime not detected | `curl -I https://$SITE_DOMAIN/api/hello` — check if server-side route returns 200 or 404; check deploy log for `@netlify/plugin-nextjs` | Rollback: `git revert HEAD && git push`; restore previous deploy: Netlify Dashboard → Deploys → select previous → Publish deploy | Install `@netlify/plugin-nextjs` before migrating; verify `next.config.js` compatible; test with `netlify dev` before production deploy |
| DNS migration from Netlify DNS to external DNS | SSL cert stops renewing after DNS moved; `NETLIFY_DNS` ownership check fails | `netlify env:get NETLIFY_SITE_ID && netlify ssl --force` — check SSL status; `dig $DOMAIN @8.8.8.8 TXT` — verify DNS propagation | Re-add Netlify DNS records at new registrar; or re-delegate to Netlify DNS: update nameservers back to `dns1.p01.nsone.net` | Plan DNS migration with 48h TTL reduction first; verify SSL cert auto-renewal works at new DNS before cutting over; test with branch deploy on subdomain |
| Environment variable name change breaking Function | After renaming `DATABASE_URL` to `DB_CONNECTION_STRING`, Functions return 500 | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys/$DEPLOY_ID/log" \| grep "undefined\|Cannot read prop"` | Add back old env var: Netlify Dashboard → Site Settings → Environment Variables → Add `DATABASE_URL`; or redeploy with corrected code | Use `netlify env:list` to audit before renaming; deploy code change and env var change simultaneously in same deploy; never remove old name until new name verified |
| Netlify Identity to external Auth migration partial completion | Some users using old Identity JWTs; some using new Auth0 tokens; middleware rejects one or other | `curl https://$SITE_DOMAIN/.netlify/identity/settings \| jq .external` — check which providers active; test with old and new tokens | Re-enable Netlify Identity: Dashboard → Identity → Enable; both auth methods active until migration complete | Run parallel auth middleware accepting both token types during migration; migrate users in batches; validate 100% cutover before disabling Identity |
| Form feature flag migration (Netlify Forms → custom backend) | After removing `data-netlify="true"`, form submissions silently dropped; no serverless handler active | `curl -X POST https://$SITE_DOMAIN/api/contact -d 'name=test' -v` — check if endpoint exists and returns 200 | Re-add `data-netlify="true"` to form and redeploy; form submissions resume | Deploy backend form handler before removing Netlify Forms attribute; validate new handler in staging; monitor form submission count before and after cutover |
| Dependency version conflict after `npm update` before deploy | Post-update build fails: `peer dep missing: react@^17, got react@18` | `npm ls react` — check installed versions; `cat package-lock.json \| jq '.packages[""].peerDependencies'` | `git checkout package-lock.json && netlify deploy --prod` to redeploy with previous lock file | Use `npm ci` instead of `npm install` in `netlify.toml`; commit `package-lock.json`; use Renovate Bot with test CI before auto-merging dependency PRs |

## Kernel/OS & Host-Level Failure Patterns

| Failure | Symptom | Service-Specific Detection | Root Cause | Remediation |
|---------|---------|---------------------------|------------|-------------|
| OOM killer targets Netlify build process | Build fails with `Killed` signal; build log truncated; no framework-specific error message | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=1" \| jq '.[0] \| {state,error_message}'`; build log shows `signal: killed` | Next.js/Gatsby SSG build generating thousands of pages exceeds Netlify build container memory limit (8GB) | Reduce concurrent page generation: `{ "build": { "environment": { "NODE_OPTIONS": "--max-old-space-size=4096" } } }` in `netlify.toml`; use ISR instead of full SSG; split large builds |
| Inode exhaustion during build dependency install | Build fails with `ENOSPC: no space left on device` during `npm install` despite free disk space | Build log: `npm ERR! ENOSPC`; `netlify deploy --build 2>&1 \| grep "ENOSPC\|no space"` | `node_modules` with deeply nested dependencies creates millions of files; build container inode limit reached | Use `npm ci` instead of `npm install`; add `.npmrc` with `package-lock=true`; clean cache in build: `rm -rf node_modules/.cache`; reduce dependency count |
| CPU throttling degrades build performance | Build takes 3x longer than baseline; webpack/esbuild compilation hangs; build timeout at 30 min | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys/$DEPLOY_ID" \| jq '.deploy_time'` — compare to baseline | Shared build infrastructure CPU throttled during peak hours; concurrent builds on same runner compete for CPU | Schedule builds during off-peak; optimize build with `esbuild` over `webpack`; enable Netlify build cache: `[[plugins]] package = "netlify-plugin-cache"`; reduce build scope with incremental builds |
| NTP skew causes deploy timestamp ordering issues | Deploy list shows deploys in wrong order; auto-publish selects wrong deploy; rollback picks incorrect version | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=5" \| jq '.[].created_at'` — check for non-monotonic timestamps | Build container clock skew causes deploy timestamps to be non-monotonic; Netlify API sorts by created_at | Report to Netlify support as platform issue; use deploy IDs (not timestamps) for rollback: `netlify deploy --prod --deploy-id <id>`; pin production deploy explicitly |
| File descriptor exhaustion in Netlify Function | Lambda function returns `EMFILE: too many open files`; concurrent invocations each opening DB connections | Function logs: `Error: EMFILE, too many open files`; `curl https://$SITE_DOMAIN/.netlify/functions/<fn> -v` returns 500 | Each Function invocation opens new file handles (DB connections, file reads); Lambda instance FD limit ~1024 | Reuse connections across invocations: initialize DB pool outside handler; use connection pooling (PgBouncer, RDS Proxy); close file handles explicitly in `finally` block |
| TCP conntrack not applicable — managed platform | N/A for Netlify managed infrastructure; edge network handles connection management | N/A | N/A — Netlify CDN and Function runtime are fully managed; no user-accessible conntrack tuning | Monitor from application side: track Function cold starts via `context.callbackWaitsForEmptyEventLoop`; report platform connection issues to Netlify support |
| Edge Function cold start latency spike | First request after idle period takes 2-5s; Deno runtime initialization visible in response time | `curl -w "@curl-format.txt" -o /dev/null -s https://$SITE_DOMAIN/edge-path` — measure TTFB; compare cold vs warm | Edge Function Deno isolate recycled after inactivity; cold start includes V8 isolate creation + module import | Minimize Edge Function import size; use `Deno.core` APIs instead of heavy npm imports; add synthetic keep-alive: cron pings Edge Function endpoint every 5 min |
| Build container disk full during large asset processing | Build fails processing large media files; `ENOSPC` during image optimization or font subsetting | Build log: `Error: ENOSPC: no space left on device, write`; check build log for asset processing step | Large Media files or image optimization generates temporary files exceeding build container disk (20GB) | Use Netlify Large Media (Git LFS) to avoid processing large files during build; optimize images before commit; split asset pipeline from main build |

## Deployment Pipeline & GitOps Failure Patterns

| Failure | Symptom | Service-Specific Detection | Root Cause | Remediation |
|---------|---------|---------------------------|------------|-------------|
| Git webhook delivery failure — deploys not triggering | Push to GitHub/GitLab but no Netlify build starts; webhook delivery shows failure in GitHub settings | GitHub repo → Settings → Webhooks → check delivery status; `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=1" \| jq '.[0].created_at'` — stale | GitHub webhook secret rotated or Netlify site link broken; webhook endpoint returning 401/500 | Re-link repository: Netlify Dashboard → Site Settings → Build & deploy → Link repository; verify webhook in GitHub Settings → Webhooks; trigger manual deploy: `netlify deploy --build --prod` |
| Branch deploy drift — production and preview diverge | Preview deploy shows correct content but production deploy is outdated; `netlify deploy --prod` not triggered | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys?per_page=5" \| jq '.[] \| {branch,state,created_at}'` | Production branch deploy auto-publish disabled; or merge to `main` not triggering production build | Check auto-publishing: Dashboard → Site Settings → Build & deploy → Deploy contexts; verify production branch matches: `netlify status --json \| jq '.siteData.build_settings.repo_branch'` |
| Build plugin version pinning drift | Build passes locally but fails on Netlify; plugin version auto-updated between builds | Build log: `Plugin "@netlify/plugin-nextjs" version X.Y.Z`; compare to `package.json` pinned version | Netlify auto-updates unpinned build plugins between deploys; new version introduces breaking change | Pin plugin version in `package.json`: `"@netlify/plugin-nextjs": "4.41.3"`; commit `package-lock.json`; use `npm ci` in build command |
| Environment variable not available during build | Build fails with `undefined` for expected env var; Function runtime has the var but build step does not | Build log: `process.env.API_URL is undefined`; `netlify env:list --scope build` — check if var has correct scope | Environment variable scoped to `runtime` only, not `build`; or recently added but deploy cache stale | Set env var scope to include `builds`: `netlify env:set API_URL <value> --scope builds,functions,runtime`; clear build cache: `netlify deploy --build --prod --clear` |
| Deploy lock prevents automated deploys | CI/CD pipeline deploy fails; Netlify returns `Deploy lock is active`; manual lock forgotten | `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID" \| jq '.deploy_lock'` — returns true | Operator enabled deploy lock during incident and forgot to unlock; all automated deploys blocked | Unlock: `netlify deploy:unlock`; or API: `curl -X POST -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys/unlock"` |
| Monorepo base directory misconfiguration | Build runs but publishes wrong directory; site shows stale or wrong content; no build error | `netlify status --json \| jq '.siteData.build_settings.base'`; compare published files with expected output | Base directory in Netlify site settings does not match monorepo package path; build runs from repo root instead of package dir | Fix base directory: Dashboard → Site Settings → Build & deploy → Base directory; or `netlify.toml`: `[build] base = "packages/web"` |
| Build cache corruption causes intermistent failures | Build fails with cryptic webpack/Vite errors; clearing cache fixes it; recurs after a few deploys | Build log: `Module not found` or `Cannot find module` for existing dependency; `netlify deploy --build --clear` succeeds | Stale `node_modules` or `.next/cache` persisted across builds; dependency version change not reflected in cached modules | Add cache clearing to CI: `netlify deploy --build --prod --clear`; or exclude problematic caches: `[[plugins]] package = "netlify-plugin-cache" [plugins.inputs] paths = []` |
| Netlify Functions deploy partial failure | Some Functions deploy but others silently missing; invocations return 404 for missing functions | `curl https://$SITE_DOMAIN/.netlify/functions/<fn-name>` — returns 404; `netlify functions:list` — function missing from output | Function file naming convention wrong (e.g., `function.ts` instead of `function/index.ts`); or function exceeds 50MB bundle limit | Check function bundling: `netlify functions:build`; verify directory structure matches Netlify convention; check bundle size: `du -sh .netlify/functions-serve/<fn-name>` |

## Service Mesh & API Gateway Edge Cases

| Failure | Symptom | Service-Specific Detection | Root Cause | Remediation |
|---------|---------|---------------------------|------------|-------------|
| Netlify CDN cache serves stale content after deploy | New deploy published but users see old content; cache-control headers not triggering purge | `curl -I https://$SITE_DOMAIN/page \| grep -E "x-nf-request-id\|age\|cache-control"`; `netlify deploy --prod` completed but `age` header shows high value | CDN edge nodes caching old assets; `Cache-Control: public, max-age=31536000` on HTML pages prevents purge | Set `Cache-Control: public, max-age=0, must-revalidate` for HTML in `netlify.toml` headers; use content-hashed filenames for static assets; force cache purge: `curl -X POST -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys/$DEPLOY_ID/restore"` |
| Netlify redirect rules conflict with Function routes | Function returns 404; redirect rule in `_redirects` catches the path before it reaches Functions | `curl -v https://$SITE_DOMAIN/.netlify/functions/api 2>&1 \| grep "< HTTP\|location"`; check `_redirects` or `netlify.toml` [[redirects]] for conflicting rule | Wildcard redirect `/* /index.html 200` catches `/.netlify/functions/*` before Function handler | Add Function route exception before wildcard: `/.netlify/functions/* 200!` in `_redirects`; or use `netlify.toml`: `[[redirects]] from = "/.netlify/functions/*" status = 200 force = true` |
| Split testing sends traffic to broken branch deploy | A/B test routes 50% to branch with broken build; users see error page | `curl -H "Cookie: nf_ab=branch-b" https://$SITE_DOMAIN/ -v`; Dashboard → Split testing → check branch health | Split test branch has failing Function or missing assets; no automated health check on split test branches | Disable split test: Dashboard → Split testing → Deactivate; verify branch deploy health before activating split test; add pre-activation smoke test |
| mTLS/custom certificate renewal fails | Site shows SSL error after custom certificate expires; Netlify auto-renewal failed | `echo \| openssl s_client -connect $SITE_DOMAIN:443 -servername $SITE_DOMAIN 2>/dev/null \| openssl x509 -dates -noout`; `netlify status --json \| jq '.siteData.ssl.state'` | DNS validation for Let's Encrypt auto-renewal failed; DNS records changed to external provider without updating validation | Re-provision SSL: `curl -X POST -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/ssl"`; verify DNS: `dig _acme-challenge.$SITE_DOMAIN TXT`; or upload manual cert |
| Retry storm from external API gateway to Netlify Functions | Function timeout causes gateway to retry 3x; each retry is a new cold start; Function execution triples | Function logs show duplicate invocations; `curl https://$SITE_DOMAIN/.netlify/functions/api` — response time >10s triggering gateway retry | External API gateway (Kong/NGINX) retries on 504 timeout; each retry starts new Function invocation | Increase Function timeout in `netlify.toml`: `[functions] external_node_modules = ["*"]`; set `AWS_LAMBDA_EXEC_WRAPPER` timeout; configure gateway with `proxy_next_upstream off` for Function paths |
| Edge Function geolocation routing returns wrong region | Edge Function `context.geo.country` returns incorrect country; users see wrong locale content | `curl -H "X-NF-Debug-Logging: true" https://$SITE_DOMAIN/localized-page -v 2>&1 \| grep "x-nf-geo"`; test from VPN in target country | Netlify edge node geo-IP database stale; or CDN routing sends user to wrong POP | Add fallback geolocation: check `Accept-Language` header as backup; report geo-IP accuracy issue to Netlify support; use client-side geolocation API as secondary signal |
| Proxy redirect to external API returns CORS errors | Frontend calls `/api/*` proxied to external backend; browser blocks with CORS error | Browser console: `Access-Control-Allow-Origin missing`; `curl -H "Origin: https://$SITE_DOMAIN" https://$SITE_DOMAIN/api/data -v 2>&1 \| grep "access-control"` | Netlify proxy `[[redirects]] from = "/api/*" to = "https://backend.example.com/:splat" status = 200` does not forward CORS headers from backend | Add CORS headers in `netlify.toml`: `[[headers]] for = "/api/*" [headers.values] Access-Control-Allow-Origin = "*"`; or handle CORS in backend; use Edge Function middleware for dynamic CORS |
| Form submission spam flooding Netlify Forms quota | Netlify Forms quota exceeded; legitimate form submissions rejected with 403; form notification spam | Dashboard → Forms → check submission count spike; `curl -H "Authorization: Bearer $NETLIFY_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID/forms" \| jq '.[].submission_count'` | Bot submitting spam to HTML forms with `data-netlify="true"`; no CAPTCHA or honeypot field | Add honeypot: `<input name="bot-field" style="display:none">`; add `data-netlify-recaptcha="true"` to form; purge spam: Dashboard → Forms → Delete spam submissions; implement server-side form handling instead |
