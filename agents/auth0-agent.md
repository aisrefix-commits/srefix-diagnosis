---
name: auth0-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-auth0-agent
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
# Auth0 SRE Agent

## Role
On-call SRE responsible for the Auth0 managed authentication platform. Owns availability of login flows, token issuance, MFA, social connections, custom domains, and tenant health. Responds to authentication failures, anomaly detection blocks, rate-limit events, and Actions/Rules execution errors across all Auth0 tenants.

## Architecture Overview

```
Browser / Mobile App
       │
       ▼
  Custom Domain (*.auth.example.com)  ──▶  Auth0 Edge (CDN-backed)
       │                                         │
       ▼                                         ▼
  /authorize  ─────────────────────────▶  Universal Login Page
  /oauth/token ────────────────────────▶  Token Endpoint
       │
       ▼
  Auth0 Pipeline
  ┌──────────────────────────────────────────────────────┐
  │  Actions/Rules ──▶ Connections ──▶ User Store        │
  │       │                │                │            │
  │   (Node.js)     (Social/DB/LDAP)   (Auth0 DB /       │
  │                                     Custom DB)       │
  └──────────────────────────────────────────────────────┘
       │
       ▼
  Management API  ──▶  JWKS Endpoint (/.well-known/jwks.json)
       │
       ▼
  Anomaly Detection / Attack Protection
```

Tenants are regionally isolated (Auth0 hosts in regions including US, EU, AU, and JP). Log streams publish to external SIEMs. Management API is separate from the authentication pipeline.

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| Login success rate | < 98% | < 95% | Per connection; track `s` vs `f` log events |
| `/oauth/token` p95 latency | > 800ms | > 2000ms | Elevated by DB connections or Actions |
| Actions execution time | > 800ms | > 1000ms (timeout) | Post-login Actions pipeline has a hard timeout (consult current Auth0 docs for the exact value) |
| Failed login rate (tenant-wide) | > 5% | > 15% | Distinguish `fp`, `fu`, `f` event codes |
| Rate-limit hit rate | > 1% of requests | > 5% | HTTP 429 on `/oauth/token` or `/authorize` |
| Management API error rate | > 2% | > 10% | Affects provisioning and user updates |
| JWKS endpoint availability | < 99.9% | < 99% | Downstream JWT validation breaks |
| MFA challenge failure rate | > 10% | > 25% | OTP drift, push delivery, SMS delivery |
| Anomaly detection blocks | > 50/hr | > 500/hr | May indicate attack; may also be false positives |
| Custom domain cert expiry | < 30 days | < 7 days | Renewal must be triggered manually |

## Alert Runbooks

### ALERT: High Login Failure Rate

**Symptoms:** `s` (success) events drop; `f`, `fp`, `fu` events spike in tenant logs.

**Triage steps:**
1. Pull recent failure events:
   ```bash
   auth0 logs tail --filter "type:f OR type:fp OR type:fu" --tenant my-tenant
   ```
2. Check if failures are from a specific connection:
   ```bash
   curl -H "Authorization: Bearer $MGMT_TOKEN" \
     "https://$DOMAIN/api/v2/logs?q=type%3Af&include_fields=connection,description,user_name&per_page=50"
   ```
3. Check if the connection itself is broken (social provider down, custom DB script error):
   ```bash
   auth0 api get /api/v2/connections --query "strategy=google-oauth2"
   ```
4. Review Actions/Rules logs for script errors — go to Auth0 Dashboard > Actions > Logs or:
   ```bash
   curl -H "Authorization: Bearer $MGMT_TOKEN" \
     "https://$DOMAIN/api/v2/logs?q=type%3Aeacft+OR+type%3Aeacfe&per_page=20"
   ```
5. If a specific rule is erroring, disable it temporarily and monitor recovery.

---

### ALERT: Rate Limit Exceeded on `/oauth/token`

**Symptoms:** HTTP 429 responses; `limit_wc` or `api_limit` events in logs; clients receiving `{"error":"too_many_requests"}`.

**Triage steps:**
1. Identify the client causing the burst:
   ```bash
   curl -H "Authorization: Bearer $MGMT_TOKEN" \
     "https://$DOMAIN/api/v2/logs?q=type%3Alimit_wc&per_page=50&include_fields=client_id,ip,description"
   ```
2. Check if it's a legitimate traffic spike or a misconfigured service doing token refresh in a tight loop.
3. Review the tenant's rate-limit tiers in Auth0 Dashboard > Settings > Advanced.
4. If a machine-to-machine app is the culprit, cache its tokens (M2M tokens last by `expires_in`):
   ```bash
   # Check token lifetime for the offending client
   curl -H "Authorization: Bearer $MGMT_TOKEN" \
     "https://$DOMAIN/api/v2/clients/$CLIENT_ID?fields=jwt_configuration"
   ```
5. For sustained legitimate load, contact Auth0 support to request rate-limit increase; interim workaround: add exponential backoff + jitter in the application.

---

### ALERT: Anomaly Detection Blocking Legitimate Users

**Symptoms:** Users report `{"error":"too_many_attempts"}` or `{"error":"blocked_user"}`; `limit_mu` or `limit_ui` events spike.

**Triage steps:**
1. Check if a specific IP or user is blocked:
   ```bash
   curl -H "Authorization: Bearer $MGMT_TOKEN" \
     "https://$DOMAIN/api/v2/anomaly/blocks/ips/$SUSPECT_IP"
   ```
4. If false-positive rate is high, adjust brute-force thresholds in Auth0 Dashboard > Security > Attack Protection.
5. Correlate with upstream load balancer logs to confirm whether traffic is genuinely anomalous.

---

### ALERT: Custom Domain Certificate Expiry / Provisioning Failure

**Symptoms:** HTTPS errors on `login.example.com`; cert provisioning status shows `pending` or `cert_expired`.

**Triage steps:**
1. Check custom domain status:
   ```bash
   curl -H "Authorization: Bearer $MGMT_TOKEN" \
     "https://$DOMAIN/api/v2/custom-domains"
   ```
2. If status is `pending_verification`, re-trigger DNS verification:
   ```bash
   curl -X POST -H "Authorization: Bearer $MGMT_TOKEN" \
     "https://$DOMAIN/api/v2/custom-domains/$CUSTOM_DOMAIN_ID/verify"
   ```
3. Confirm CNAME record resolves correctly:
   ```bash
   dig CNAME login.example.com
   # Should point to $TENANT.edge.tenants.auth0.com
   ```
4. If cert is expired and re-provisioning fails, escalate to Auth0 support with tenant name and custom domain ID.
5. Interim: redirect login traffic back to the default `$TENANT.auth0.com` domain via application config.

## Common Issues & Troubleshooting

### 1. Social Connection Outage (e.g., Google OAuth Down)

**Diagnosis:**
```bash
# Check failure events scoped to the social connection
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=connection%3Agoogle-oauth2+AND+type%3Af&per_page=20"

# Verify connection is enabled
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/connections?strategy=google-oauth2&fields=id,name,enabled_clients"
```

### 2. Actions/Rules Execution Timeout

**Diagnosis:**
```bash
# Look for Action execution failure events (eacfe = Action failed)
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=type%3Aeacfe&per_page=20&include_fields=description,details"
```
Check Action logs in Dashboard > Actions > [Action Name] > Real-time Logs.

### 3. JWKS Endpoint Unreachable / Stale Keys

**Diagnosis:**
```bash
# Verify JWKS endpoint is reachable
curl -sv "https://$DOMAIN/.well-known/jwks.json" | jq '.keys | length'

# Check if downstream services are caching a rotated key
# Confirm the kid in a token matches a current JWKS kid
TOKEN_KID=$(echo $JWT | cut -d. -f1 | base64 -d 2>/dev/null | jq -r '.kid')
curl -s "https://$DOMAIN/.well-known/jwks.json" | jq ".keys[] | select(.kid == \"$TOKEN_KID\")"
```

### 4. MFA Push Notification Not Delivered

**Diagnosis:**
```bash
# Look for MFA-related failure events
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=type%3Emfa_push_failed+OR+type%3Emfa_otp_failed&per_page=20"

# Check user's MFA enrollments
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/users/$USER_ID/authenticators"
```

### 5. Management API Token Expired

**Diagnosis:**
```bash
# M2M token for Management API — check expiry
echo $MGMT_TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq '.exp' | xargs -I{} date -r {}

# If expired, request a new one
curl -X POST "https://$DOMAIN/oauth/token" \
  -H "Content-Type: application/json" \
  -d "{\"client_id\":\"$M2M_CLIENT_ID\",\"client_secret\":\"$M2M_CLIENT_SECRET\",\"audience\":\"https://$DOMAIN/api/v2/\",\"grant_type\":\"client_credentials\"}"
```

### 6. Tenant Log Stream Delivery Failure

**Diagnosis:**
```bash
# List log streams and check status
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/log-streams"

# Look for log stream health events
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=type%3Alog_stream_failure&per_page=20"
```

## Key Dependencies

- **Social Providers:** Google, GitHub, Facebook OAuth endpoints — outage causes login failures for social connections
- **SMS Provider (Twilio/Vonage):** Required for SMS OTP MFA — failure breaks phone-based MFA
- **Custom Database Scripts:** Node.js scripts calling external DBs — latency or errors surface as login failures
- **Downstream JWKS Consumers:** APIs validating Auth0-issued JWTs — key rotation requires cache invalidation
- **Email Provider:** SendGrid/Mailgun for password reset, email verification — email delivery failures block self-service flows
- **DNS / CDN:** Custom domain CNAME must resolve; CDN in front of Auth0 edge can cause cert mismatch

## Cross-Service Failure Chains

- **Google OAuth outage** → Social connection fails → Users cannot log in with Google → Traffic shifts to username/password → password reset emails surge → email provider rate-limited
- **Actions calling downstream API down** → Action timeout exceeded → Login latency spike → User-facing timeout → Session state lost → Support ticket surge
- **JWKS endpoint returns 503** → All downstream API services reject tokens → Full application outage → Cannot renew sessions
- **Rate limit on `/oauth/token`** → M2M clients start queuing → Upstream service calls fail → Cascading timeouts across microservices
- **Anomaly detection false positive during marketing spike** → Legitimate users blocked → Support overwhelmed → Manual unblock operation required at scale

## Partial Failure Patterns

- **Single connection failing:** Login works for most users; only users of one IdP (e.g., enterprise SAML) fail. Monitor per-connection failure rates separately.
- **Actions failing silently:** Login succeeds but enrichment/authorization logic skipped. Downstream APIs may deny access due to missing claims. Always alert on `eacfe` events.
- **Log stream lagging:** Authentication is healthy but alerting is blind. Monitor log stream delivery health independently from login success rate.
- **Custom domain cert valid but DNS misconfigured:** Auth0 backend healthy; users see TLS errors at the edge. Monitor DNS resolution separately.
- **MFA partially broken:** OTP works, push broken. Users who enrolled only push are blocked; OTP users succeed. Monitor per-factor failure rates.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|---------|----------|
| `/authorize` redirect p95 | < 300ms | 300–800ms | > 800ms |
| `/oauth/token` p95 (password grant) | < 500ms | 500–1200ms | > 1200ms |
| `/oauth/token` p95 (client_credentials) | < 200ms | 200–600ms | > 600ms |
| Actions execution time | < 500ms | 500–800ms | > 1000ms (timeout) |
| Management API GET p95 | < 200ms | 200–500ms | > 500ms |
| Management API PATCH/POST p95 | < 300ms | 300–800ms | > 800ms |
| JWKS endpoint p95 | < 100ms | 100–300ms | > 300ms |
| Custom DB login script p95 | < 300ms | 300–700ms | > 1000ms |

## Capacity Planning Indicators

| Indicator | Current Baseline | Scale-Up Trigger | Notes |
|-----------|-----------------|-----------------|-------|
| Monthly active users (MAU) | — | > 80% of plan limit | Auth0 pricing is MAU-based |
| Peak `/oauth/token` req/sec | — | > 70% of rate limit | Request limit increase before hitting cap |
| M2M token requests/month | — | > 80% of plan limit | M2M counted separately from MAU |
| Actions execution time p99 | < 500ms | > 800ms | Indicates need to optimize or split Actions |
| Log retention volume | — | > 80% of tier limit | Enable external log streaming before data loss |
| Tenant connection count | — | > 50 connections | Complexity increases; consider consolidation |
| Custom domains | — | Near plan limit | Enterprise plan required for multiple domains |
| Management API req/min | — | > 70% of rate limit | Cache responses; paginate efficiently |

## Diagnostic Cheatsheet

```bash
# Tail live login events for a tenant
auth0 logs tail --tenant my-tenant

# Get last 50 failed login events with user and IP
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=type%3Af&per_page=50&include_fields=user_name,ip,connection,description" | jq .

# Get all blocked users
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/users?q=blocked%3Atrue&per_page=100" | jq '.[].user_id'

# Check rate limit headers on the Management API
curl -i -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/users?per_page=1" 2>&1 | grep -i "x-ratelimit"

# Inspect a specific user's MFA enrollments
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/users/$USER_ID/authenticators" | jq .

# Decode a JWT without a library (for quick kid/exp inspection)
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq '{kid: .kid, sub: .sub, exp: .exp, iat: .iat}'

# Check all Actions and their current deployment status
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/actions/actions?per_page=50" | jq '[.actions[] | {name: .name, status: .status, deployed: .deployed_version}]'

# List all tenant connections and their strategy
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/connections?per_page=100&fields=name,strategy,enabled_clients" | jq .

# Check custom domain configuration and cert status
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/custom-domains" | jq '.[].status'

# Search logs for a specific user's events in the last hour
curl -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=user_name%3Auser%40example.com&per_page=50" | jq '[.[] | {type: .type, date: .date, description: .description}]'
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|-------------------|-------------|
| Login success rate | 99.5% | 3.6 hours | `(s events) / (s + f events)` per 5-min window |
| `/oauth/token` p95 latency | < 500ms | Burn rate alert at 2x | Measured from Auth0 edge; excludes network RTT |
| JWKS endpoint availability | 99.95% | 21.9 minutes | Synthetic probe every 30s |
| Management API availability | 99.9% | 43.8 minutes | Synthetic probe + log stream health check |

## Configuration Audit Checklist

| Check | Expected State | How to Verify | Risk if Misconfigured |
|-------|---------------|--------------|----------------------|
| Brute-force protection enabled | Enabled, threshold ≤ 10 | Dashboard > Security > Attack Protection | Account takeover |
| Breached password detection | Enabled | Dashboard > Security > Attack Protection | Credential stuffing success |
| MFA enforced for all users | Required (not optional) | Dashboard > Security > Multi-factor Auth | Account compromise |
| All Actions use try/catch | No unguarded await | Review Action code | Login outage on downstream failure |
| Custom domain cert valid | > 30 days remaining | `curl https://$DOMAIN/api/v2/custom-domains` | Login page TLS errors |
| M2M token lifetime | ≤ 86400s (24h) | `GET /api/v2/clients/$CLIENT_ID \| jq .jwt_configuration` | Long-lived credential exposure |
| Log streams active | `active` status | `GET /api/v2/log-streams` | Loss of audit trail |
| Allowed callback URLs restricted | No wildcards (`*`) | Dashboard > Applications > [App] > Settings | Open redirect |
| Token rotation enabled (refresh tokens) | Enabled | Dashboard > Applications > [App] > Advanced | Token replay attack |
| Tenant admins use MFA | All admins enrolled | Dashboard > Settings > Tenant Members | Admin account takeover |

## Log Pattern Library

| Pattern | Event Type | Meaning | Action |
|---------|-----------|---------|--------|
| `type: "s"` | Success | Successful login | Baseline metric |
| `type: "f"` | Failure | Generic login failure | Investigate `description` field |
| `type: "fp"` | Failure (password) | Wrong password | Monitor for brute force |
| `type: "fu"` | Failure (username) | User not found | May indicate enumeration |
| `type: "limit_wc"` | Rate limit | `/oauth/token` rate limit hit | Add backoff; request limit increase |
| `type: "limit_mu"` | Brute force block | User blocked after too many attempts | Review if legitimate; unblock if needed |
| `type: "slo"` | Logout | Successful logout | Baseline metric |
| `type: "eacfe"` | Action failure | Action execution error | Check Action code; roll back if needed |
| `type: "depnote"` | Deprecation | Deprecated feature used | Plan migration before removal date |
| `type: "mgmt_api_write"` | Mgmt API write | User/role/connection modified | Audit for unauthorized changes |
| `type: "api_limit"` | API rate limit | Management API limit hit | Implement caching; reduce polling |
| `type: "sfu"` | Security failure | Suspicious activity detected | Review Anomaly Detection; may indicate attack |

## Error Code Quick Reference

| Error Code / Message | HTTP Status | Meaning | Resolution |
|---------------------|------------|---------|-----------|
| `invalid_grant` | 400 | Expired/invalid authorization code or refresh token | Re-authenticate; check token lifetime config |
| `access_denied` | 403 | User denied consent or rule blocked login | Check Rules/Actions for deny logic |
| `too_many_requests` | 429 | Rate limit exceeded | Back off; check token caching |
| `unauthorized_client` | 401 | Client not authorized for grant type | Check Application grant type settings |
| `invalid_client` | 401 | Bad client_id or client_secret | Verify credentials; check secret rotation |
| `user_does_not_exist` | 400 | User not found in connection | Check connection; verify user exists |
| `blocked_user` | 401 | User account is blocked | Unblock via Management API |
| `mfa_required` | 403 | MFA required but not completed | Expected behavior; ensure MFA flow is correct |
| `mfa_enrollment_required` | 403 | User must enroll in MFA | Direct user to MFA enrollment page |
| `connection_error` | 500 | Error connecting to identity provider | Check social/enterprise connection config |
| `script_execution_timeout` | 500 | Custom DB script timed out | Optimize script; check external DB latency |
| `tenant_not_found` | 404 | Tenant domain doesn't exist | Verify domain in request URL |

## Known Failure Signatures

| Signature | Pattern | Root Cause | Resolution |
|-----------|---------|-----------|-----------|
| Spike in `type:f` from single IP range | Hundreds of failed logins from ASN | Credential stuffing | Enable Breached Password Detection; block ASN |
| All logins fail with `connection_error` | `type:f` + `description: "error in rule"` | Action/Rule uncaught exception | Check Action logs; roll back last deployment |
| Intermittent 429 on `/oauth/token` | Periodic bursts, then normal | M2M service not caching tokens | Add token cache with TTL = `expires_in - 60s` |
| Login works, API returns 401 | Auth success but downstream rejects | JWKS key rotated; consumer cache stale | Restart downstream services to flush JWKS cache |
| MFA push never arrives | `mfa_push_failed` events | FCM/APNS delivery issue or Guardian outage | Fall back to OTP; check Auth0 Guardian status |
| Custom domain returns `ERR_CERT_COMMON_NAME_INVALID` | TLS cert mismatch | CNAME not pointing to Auth0 edge | Fix DNS CNAME; re-trigger domain verification |
| Actions deploy but login still uses old code | Deployment shows success | Propagation delay (up to 60s) | Wait; verify with a test login after 60s |
| Management API returns 429 on list operations | Automated script hitting `/api/v2/users` | No pagination; fetching all users in loop | Add `per_page=100`; implement cursor-based pagination |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `invalid_grant` (HTTP 400) on token exchange | `auth0-js`, `auth0-spa-js`, any OIDC library | Authorization code expired (> 60s), reused, or PKCE verifier mismatch | Check `type:f` log with `description: "invalid_grant"` and compare timestamps | Ensure single-page apps exchange codes within 60s; do not retry on code exchange errors |
| `access_denied` (HTTP 403) during login | `auth0-spa-js`, server-side OIDC library | Action/Rule explicitly called `api.access.deny()`; or user consent denied | `curl .../api/v2/logs?q=type:eacfe` for Action error with `access_denied` description | Review Action logic; check if deny is intentional (authorization policy) |
| `too_many_requests` (HTTP 429) on `/oauth/token` | Any OAuth client library | Client-credentials M2M app not caching tokens; per-client rate limit exceeded | `curl .../api/v2/logs?q=type:limit_wc` for the offending `client_id` | Cache M2M token with TTL = `expires_in - 60s`; add exponential backoff |
| `unauthorized_client` (HTTP 401) | OAuth client library | Application not authorized for the requested grant type (`client_credentials`, `password`, etc.) | Auth0 Dashboard > Applications > [App] > Advanced > Grant Types | Enable the required grant type in Application settings |
| `invalid_client` (HTTP 401) | OAuth client library | Wrong `client_id` or `client_secret`; secret rotated without updating application | `curl .../api/v2/clients/$CLIENT_ID --header "Authorization: Bearer $MGMT_TOKEN" \| jq .client_id` | Verify and update credentials; rotate secret and propagate to all services |
| `mfa_required` (HTTP 403) after password auth | OAuth client library | MFA policy requires second factor; application does not handle MFA challenge flow | Expected behavior if MFA enrolled; check if application implements MFA challenge response | Implement MFA challenge handling in the application's auth flow |
| JWT validation fails: `invalid signature` | `jsonwebtoken`, `jose`, `python-jose` | Downstream service cached stale JWKS after Auth0 key rotation | `curl https://$DOMAIN/.well-known/jwks.json \| jq '.keys[].kid'` vs. token `kid` header | Flush JWKS cache on downstream services; restart services or increase cache TTL |
| JWT validation fails: `jwt expired` | `jsonwebtoken`, `jose` | Token lifetime is too short for long-running operations; clock skew between issuer and validator | Decode token: `echo $TOKEN \| cut -d. -f2 \| base64 -d \| jq .exp` | Increase token lifetime in Application settings; implement silent token refresh |
| `blocked_user` (HTTP 401) | Any auth client | Brute-force protection blocked the user after too many failed attempts | `curl .../api/v2/user-blocks/$USER_ID -H "Authorization: Bearer $MGMT_TOKEN"` | Unblock user via Management API; review if block was legitimate |
| Login page fails to load / CORS error | Browser fetch API | Custom domain certificate expired or CNAME misconfigured | `dig CNAME login.example.com`; `curl -sv https://login.example.com` for cert errors | Re-trigger domain verification; renew certificate; fall back to `$TENANT.auth0.com` |
| `connection_error` (HTTP 500) on social login | `auth0-spa-js` | Social provider (Google, GitHub, etc.) OAuth endpoint is down | `curl .../api/v2/logs?q=type:f+AND+connection:google-oauth2` | Disable the failing social connection temporarily; display user-facing message |
| Silent token renewal fails with `login_required` | `auth0-spa-js` SPA SDK | Auth0 session expired; `prompt=none` in silent renew fails because no active session | Browser Dev Tools > Network: `/authorize` response with `error=login_required` | Re-authenticate user; increase session lifetime in Tenant Settings |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Actions execution time creeping up | p95 Actions latency growing week-over-week; approaching 800ms warning threshold | `curl .../api/v2/logs?q=type:eacft&per_page=100` and parse `details.elapsed` field | Weeks | Profile which external call inside the Action is slowing; add caching or timeout guards |
| MAU approaching plan limit | Monthly active user count reaching 80% of plan cap | Auth0 Dashboard > Settings > Subscription — MAU gauge; or Management API tenant stats | Weeks | Upgrade plan before limit; implement user deduplication; contact Auth0 sales |
| M2M token request count approaching plan limit | M2M quota at 80%+; not yet throttled | Auth0 Dashboard > Applications > (M2M app) > Token Usage | Days to weeks | Cache M2M tokens aggressively; audit all M2M clients for duplicate token fetches |
| Log retention window filling | Oldest tenant logs approaching retention limit; SIEM missing events | `curl .../api/v2/logs?per_page=1` — check `date` of oldest available log | Days | Enable external log streaming to Datadog/Splunk before log data is lost |
| Custom domain cert expiry approaching | Cert still valid; no user impact yet | `curl -sv https://login.example.com 2>&1 \| grep "expire date"` | 30 days | Trigger Auth0 cert renewal; verify CNAME is still correct; set calendar reminder |
| Anomaly detection sensitivity drifting (too many false positives) | Legitimate users being blocked at increasing rate; support tickets rising | `curl .../api/v2/logs?q=type:limit_mu&per_page=100` — count per day over rolling week | Weeks | Tune brute-force threshold in Dashboard > Security > Attack Protection |
| Deprecated feature usage accumulating | `depnote` log events appearing; Auth0 sends deprecation notice email | `curl .../api/v2/logs?q=type:depnote&per_page=50` — check `description` for deprecation target | Months | Migrate away from deprecated API or feature before removal date |
| Management API polling rate approaching limit | Occasional 429 on Management API; caching not implemented | `curl -i .../api/v2/users?per_page=1 -H "Authorization: Bearer $MGMT_TOKEN" 2>&1 \| grep x-ratelimit` | Days | Implement response caching; reduce polling frequency; use log streaming instead of polling logs |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# auth0-health-snapshot.sh
# Prints full Auth0 tenant health summary

set -euo pipefail
: "${DOMAIN:?Set DOMAIN to your Auth0 tenant domain}"
: "${MGMT_TOKEN:?Set MGMT_TOKEN to a valid Management API token}"

echo "=== Auth0 Tenant Health Snapshot: $DOMAIN ==="
echo ""

echo "--- Auth0 Platform Status ---"
curl -s https://status.auth0.com/api/v2/summary.json \
  | jq '.components[] | select(.status != "operational") | {name, status}' \
  || echo "  All components operational"

echo ""
echo "--- Last 10 Failed Login Events ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=type%3Af&per_page=10&include_fields=user_name,connection,description,ip" \
  | jq -r '.[] | "\(.user_name) [\(.connection)]: \(.description) from \(.ip)"'

echo ""
echo "--- Failure Count by Connection (last 50 failures) ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=type%3Af&per_page=50" \
  | jq 'group_by(.connection) | map({connection: .[0].connection, count: length}) | sort_by(-.count)'

echo ""
echo "--- Action Errors (last 20) ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=type%3Aeacfe&per_page=20&include_fields=description,details" \
  | jq -r '.[] | "\(.description)"'

echo ""
echo "--- Blocked Users Count ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/users?q=blocked%3Atrue&per_page=1&include_totals=true" \
  | jq '.total // 0 | "  Blocked users: \(.)"'

echo ""
echo "--- Custom Domain Status ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/custom-domains" \
  | jq -r '.[] | "\(.custom_domain): \(.status)"'

echo ""
echo "--- Rate Limit Headroom ---"
curl -sI -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/users?per_page=1" \
  | grep -i "x-ratelimit"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# auth0-perf-triage.sh
# Analyzes login latency, Action execution times, and rate-limit proximity

set -euo pipefail
: "${DOMAIN:?Set DOMAIN}"
: "${MGMT_TOKEN:?Set MGMT_TOKEN}"

echo "=== Auth0 Performance Triage ==="
echo ""

echo "--- Login Success Rate (last 100 events) ---"
LOGS=$(curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?per_page=100&include_fields=type")
SUCCESS=$(echo "$LOGS" | jq '[.[] | select(.type=="s")] | length')
FAILURE=$(echo "$LOGS" | jq '[.[] | select(.type | test("^f"))] | length')
TOTAL=$(( SUCCESS + FAILURE ))
echo "  Success: $SUCCESS | Failure: $FAILURE | Total: $TOTAL"
if [[ $TOTAL -gt 0 ]]; then
  RATE=$(echo "scale=1; $SUCCESS * 100 / $TOTAL" | bc)
  echo "  Success rate: ${RATE}%"
fi

echo ""
echo "--- Rate Limit Events (last 50 logs) ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=type%3Alimit_wc+OR+type%3Aapi_limit&per_page=50&include_fields=client_id,description,date" \
  | jq -r '.[] | "\(.date) client=\(.client_id): \(.description)"' | head -10

echo ""
echo "--- Action Execution Errors (last 24h signal) ---"
ACTION_ERRORS=$(curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/logs?q=type%3Aeacfe&per_page=50" | jq length)
echo "  Action errors in recent log window: $ACTION_ERRORS"

echo ""
echo "--- Actions Deployment Status ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/actions/actions?per_page=20" \
  | jq -r '.actions[] | "\(.name): \(.status)"'
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# auth0-connection-audit.sh
# Audits connections, M2M clients, and anomaly blocks

set -euo pipefail
: "${DOMAIN:?Set DOMAIN}"
: "${MGMT_TOKEN:?Set MGMT_TOKEN}"

echo "=== Auth0 Connection & Resource Audit ==="
echo ""

echo "--- All Connections and Enabled Client Count ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/connections?per_page=50&fields=name,strategy,enabled_clients" \
  | jq -r '.[] | "\(.name) [\(.strategy)]: \(.enabled_clients | length) clients"'

echo ""
echo "--- M2M Clients with Short Token Lifetimes (< 3600s) ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/clients?per_page=50&fields=name,app_type,jwt_configuration" \
  | jq -r '.[] | select(.app_type=="non_interactive") | select((.jwt_configuration.lifetime_in_seconds // 86400) < 3600) | "\(.name): \(.jwt_configuration.lifetime_in_seconds)s"'

echo ""
echo "--- Blocked IPs ---"
BLOCKED_IPS=$(curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/anomaly/blocks/ips")
echo "$BLOCKED_IPS" | jq length | xargs echo "  Blocked IPs:"
echo "$BLOCKED_IPS" | jq -r '.[] // empty' | head -10

echo ""
echo "--- Log Streams Status ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/log-streams" \
  | jq -r '.[] | "\(.name) [\(.type)]: \(.status)"'

echo ""
echo "--- Tenant Admin MFA Status ---"
echo "  (Check manually: Auth0 Dashboard > Settings > Tenant Members)"
echo "  Verify all admins have MFA enrolled."

echo ""
echo "--- Trigger Bindings (Action pipeline order) ---"
curl -s -H "Authorization: Bearer $MGMT_TOKEN" \
  "https://$DOMAIN/api/v2/actions/triggers/post-login/bindings" \
  | jq -r '.bindings[] | "\(.display_name): \(.action.name)"'
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| One M2M application exhausting the `/oauth/token` rate limit | All other M2M and user login clients receive 429; `limit_wc` events spike for one `client_id` | `curl .../api/v2/logs?q=type:limit_wc&per_page=100 \| jq 'group_by(.client_id) \| map({client:.[0].client_id,count:length})'` | Identify the offending client; force it to cache tokens (`expires_in - 60s`); add backoff | Enforce per-client token caching in code review; monitor M2M token request rate per client |
| One high-traffic application consuming MAU quota | MAU counter approaching plan limit faster than expected; other apps potentially unable to register new users | Auth0 Dashboard > Applications > filter by MAU contribution | Audit the high-MAU app for duplicate user creation or test users not cleaned up | Separate production and test tenants; clean up test users; monitor MAU per application |
| Action with external HTTP call slowing all logins | `/oauth/token` and `/authorize` p95 latency rises for all clients; Action execution time metric elevated | `curl .../api/v2/logs?q=type:eacft&per_page=50` — parse `details.elapsed`; identify which Action is slow | Add timeout guard in the Action; use `try/catch` with fail-open fallback; cache external API responses | Set 2-3s timeout on all external HTTP calls in Actions; use Auth0 Actions `require` caching pattern |
| Anomaly detection triggered by legitimate bulk operation (import, test suite) | Hundreds of users blocked; support queue surges; `limit_mu` events spike | `curl .../api/v2/logs?q=type:limit_mu&per_page=100 \| jq '[.[].ip] \| group_by(.) \| map({ip:.[0],count:length})'` | Unblock IP range; temporarily raise brute-force threshold during operation | Allowlist known operation IPs in Auth0 before running bulk imports or load tests |
| Log stream delivery backpressure during high authentication volume | Auth0 logs delivered to SIEM delayed by hours; alerting blind during peak traffic | `curl .../api/v2/log-streams` for `status`; Auth0 Dashboard > Monitoring > Logs | Ensure SIEM endpoint can accept Auth0 log throughput; upgrade SIEM ingestion capacity | Right-size the log stream destination for expected peak event rate; use multiple log streams for redundancy |
| Management API rate limit shared between automation and incident response | On-call engineer's manual Management API calls return 429 during automated provisioning runs | `curl -I .../api/v2/users?per_page=1 \| grep x-ratelimit-remaining` | Pause automated provisioning script during incident; use a separate M2M client for automation vs. on-call | Use dedicated M2M clients for automation vs. incident response; implement rate-aware pagination |
| One tenant's custom database script exhausting the external DB connection pool | Other connections using the same DB also slow; login latency spikes with DB query time | `curl .../api/v2/logs?q=type:f&per_page=50` — filter `connection` for custom DB; correlate with DB monitoring | Add connection pool limits to the custom DB script; add query timeout | Set `maxRows` and `timeout` in custom DB scripts; use connection pooling middleware (PgBouncer) |
| JWKS endpoint caching causing stale key contention across multiple API services | After key rotation, multiple services fail token validation simultaneously; `invalid signature` errors spike | Each service's error logs for `invalid signature`; correlate timestamp with JWKS rotation event | Restart all services simultaneously to flush caches; or deploy a JWKS cache-bust mechanism | Use a short JWKS cache TTL (10 min); implement key rotation notification via webhook to downstream services |
| High volume of silent token renewals overwhelming `/authorize` endpoint | SPA users experience session expiry at scale during peak hours; `/authorize` latency rises | Monitor Auth0 `authorize` request count; compare silent renewal frequency in SPA code | Increase SPA session lifetime; reduce silent renewal frequency; stagger renewal across clients | Use `cacheLocation: 'localstorage'` in `auth0-spa-js` to persist tokens; increase `sessionCheckExpiryDays` |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Auth0 tenant rate limit hit on `/oauth/token` | All M2M and user authentication attempts return `HTTP 429`; API calls fail; users cannot log in; automated services de-authenticate | All applications and users in the tenant | Auth0 logs `type:limit_wc`; application logs `HTTP 429 Too Many Requests`; Datadog/CloudWatch spike | Cache tokens until `expires_in - 60s`; implement client-side rate limiter; contact Auth0 support to raise limit |
| Auth0 Action with broken external dependency (e.g., Stripe API down) | Login pipeline stalls at Action execution; `/authorize` requests time out; users locked out | All users attempting login via that connection/application | Auth0 logs `type:eacft` with elevated `details.elapsed`; application error `Login timeout` | Add `try/catch` with fail-open fallback in Action; disable the failing Action temporarily via Management API |
| Auth0 custom domain DNS failure | Browser OIDC flows fail with `ERR_NAME_NOT_RESOLVED`; API clients using custom domain cannot reach token endpoints | All users and services configured to use the custom Auth0 domain | `dig <custom-domain>` fails or returns wrong IP; Auth0 real domain (`<tenant>.auth0.com`) still works | Temporarily switch application `domain` config to `<tenant>.auth0.com`; debug DNS/CDN configuration |
| JWKS key rotation without downstream service cache invalidation | API services fail to validate tokens with `invalid signature`; all authenticated API calls return `401` | All resource servers (APIs) validating Auth0-issued JWTs | Service logs `JsonWebTokenError: invalid signature`; correlate with Auth0 key rotation event in Management API | Restart all API services to flush JWKS cache; implement short JWKS cache TTL (600s); use `kid` header for targeted cache invalidation |
| Auth0 outage (upstream Auth0 service degradation) | All login flows fail; M2M token refresh blocked; existing sessions may still work until expiry | All users authenticating; M2M clients needing fresh tokens | `https://status.auth0.com` shows incident; application logs consistent `502`/`503` from Auth0 endpoints | Use Auth0 session cookies for existing users; implement offline fallback for M2M services; monitor `status.auth0.com` alerts |
| Brute-force protection blocking legitimate traffic (false positive) | Users in a specific IP range blocked; corporate VPN egress IP flagged; bulk test runs triggering anomaly detection | All users behind the flagged IP range | Auth0 logs `type:limit_wc` or `type:limit_mu` for the IP; `GET /api/v2/anomaly/blocks/ips` lists the IP | Unblock IP: `DELETE /api/v2/anomaly/blocks/ips/{ip_address}`; whitelist corporate IP ranges in Auth0 anomaly settings |
| Auth0 log stream destination (SIEM) unavailable | Auth0 log delivery drops; log stream enters degraded mode; security events not forwarded | Security monitoring blind; compliance audit trail gap | `GET /api/v2/log-streams` shows `status: "paused"` or `"disabled"`; SIEM shows gap in Auth0 log ingestion | Repair SIEM endpoint; resume log stream: `PATCH /api/v2/log-streams/<id>` with `status: "active"`; retroactively pull logs via Management API |
| Expired M2M client secret used by all backend services | All backend-to-backend API calls fail with `401 Unauthorized` and `invalid_client`; downstream service workflows break | All internal microservices using that M2M client | Application logs `invalid_client`; Auth0 log `type:fc` with `description: invalid client credentials`; correlate with secret rotation date | Rotate M2M client secret in Auth0 dashboard; deploy new secret to all consuming services via secrets manager |
| Auth0 Management API client token expiry during incident response | On-call engineer's API calls return `401`; automated provisioning scripts fail; no ability to manage users during outage | Incident response blocked; automated Auth0 management pipeline down | `curl -I https://<tenant>.auth0.com/api/v2/users?per_page=1 -H "Authorization: Bearer $TOKEN"` returns `401`; token expiry time passed | Re-authenticate: `curl -X POST https://<tenant>.auth0.com/oauth/token -d '{"client_id":"...","client_secret":"...","audience":"https://<tenant>.auth0.com/api/v2/","grant_type":"client_credentials"}'` |
| Auth0 post-login Action causing token enrichment failure for all users | Access tokens missing required claims; downstream API authorization fails with `403`; all authenticated user actions blocked | All users logging in after the broken Action deployment | Application `403` errors for all users; Auth0 logs `type:eacft` failures; Action debug logs in tenant | Disable the failing Action: `PATCH /api/v2/actions/actions/<id>` with `deployed: false`; re-enable after fix |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Deploying a new Auth0 Action with unhandled exception | All users in the trigger's pipeline get login error `Something went wrong`; existing sessions unaffected until expiry | Immediate on Action deployment | Auth0 logs `type:eacft` spike; Action execution logs show uncaught exception; correlate with deployment timestamp | Disable Action: `PATCH /api/v2/actions/actions/<id>` with `deployed: false`; restore previous version from Action version history |
| Changing `token_endpoint_auth_method` on M2M client from `client_secret_post` to `client_secret_basic` | M2M services sending credentials in request body now get `401 invalid_client` | Immediate on client save | Auth0 log `type:fc` with `client_id` matching the changed client; `description: invalid client authentication method` | Revert `token_endpoint_auth_method` to `client_secret_post` in client settings; or update service to send `Authorization: Basic` header |
| Rotating Auth0 signing keys (RS256) | In-flight tokens still use old `kid`; if JWKS cache TTL too long, all tokens appear invalid after rotation | Immediately for new tokens; existing tokens invalid after cache refresh | Service logs `JsonWebTokenError: invalid signature`; `kid` in JWT header not found in current JWKS endpoint | Set JWKS cache TTL ≤ 600s; implement JWKS fallback to re-fetch on `invalid signature` error; wait for old tokens to expire |
| Enabling MFA for all users on a production tenant without migration path | Existing users cannot log in until they enroll MFA; if `mfa_policy: all_applications`, enrollment forced immediately | Immediate for all non-MFA-enrolled users | Auth0 logs `type:gd_enrollment_start` mass spike; support tickets from users unable to log in | Set MFA policy to `opt-in` temporarily; send MFA enrollment emails before enforcing; use Auth0 `Guardian` enrollment flows |
| Adding a new required field to the Auth0 DB user schema without migration | Existing users missing the field fail validation on login if Actions access it; `null` dereference in Action code | Immediate for users without the field who trigger the Action | Auth0 Action execution errors `Cannot read property 'X' of undefined`; correlate with schema change and Action deployment | Guard the field access in Action code: `const value = event.user.user_metadata?.new_field ?? 'default'`; backfill existing users |
| Changing Auth0 application `Allowed Callback URLs` removing a URL | Users redirected to that URL after login get `Callback URL mismatch`; only affects clients using the removed URL | Immediate on change save | Auth0 log `type:fp` with `description: redirect_uri does not match`; browser error page `Callback URL mismatch` | Re-add the missing URL to `Allowed Callback URLs` in the Auth0 application settings |
| Enabling Universal Login with a custom template that has a JavaScript error | All users see blank login page or JavaScript error in browser; social login buttons missing | Immediate on template save | Browser console `Uncaught SyntaxError` or `ReferenceError` on login page; Auth0 tenant logs `type:f` for all login attempts | Revert to default Universal Login template: Auth0 Dashboard > Branding > Universal Login > Reset to default |
| Updating SAML connection metadata URL with expired certificate | Enterprise SSO users get `SAML assertion signature verification failed` | Immediate for SAML SSO users after metadata update | Auth0 log `type:fs` with SAML signature error; correlate with metadata URL change in Auth0 connection settings | Re-upload correct SAML certificate from IdP; or revert to old metadata URL |
| Adding IP allowlist to Auth0 Management API without including Terraform/CI runner IP | All infrastructure-as-code Auth0 provisioning fails with `403 Forbidden`; Terraform plans break | Immediate on allowlist activation | Terraform logs `Error 403: IP not allowed`; correlate with Management API IP restriction change | Add CI runner IP range to Management API IP restrictions; or temporarily disable restriction to unblock pipeline |
| Increasing token lifetime beyond IdP session lifetime | Users with expired IdP sessions still have valid Auth0 access tokens; IdP-level logout does not revoke Auth0 tokens | After token refresh cycle when IdP session expires | Users can make API calls after IdP logout; `POST /oauth/revoke` needed explicitly | Add Auth0 back-channel logout; implement refresh token rotation; set Auth0 token lifetime ≤ IdP session lifetime |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| User created in Auth0 but not synced to application database (partial user creation) | `curl -H "Authorization: Bearer $TOKEN" "https://<tenant>.auth0.com/api/v2/users?q=email:<email>"` returns user; app DB shows no matching record | User can log in to Auth0 but gets `User not found` in application | User locked out of application despite valid credentials; support escalation | Create the missing user record in application DB from Auth0 user data; trigger a post-login Action to auto-provision missing users |
| Auth0 user metadata updated via Action and via Management API simultaneously | `user_metadata` shows inconsistent values depending on which path last wrote; last write wins with no conflict resolution | `GET /api/v2/users/<id>` metadata value differs from what application Action wrote at login time | User profile data inconsistent; personalization features broken | Enforce single-writer pattern for `user_metadata`; use Actions for write-at-login, Management API only for admin operations |
| JWKS cached with old keys after rotation causing split validation behavior | Some API service pods validate tokens successfully (old key cached), others reject tokens (new key fetched) | Some API requests succeed and others fail 401 for the same token depending on which pod handles it | Non-deterministic authentication failures; hard to reproduce; load-balanced flapping | Rolling restart all API service pods to flush JWKS caches; enforce short JWKS TTL (600s) via environment variable |
| Auth0 log gaps due to log stream pause — security events not delivered to SIEM | `GET /api/v2/log-streams` shows `status: "paused"`; SIEM log sequence numbers have gaps | Security team blind to authentication events during gap; audit compliance issue | Compliance audit failure; missed brute-force or credential stuffing attack during gap window | Re-activate log stream; backfill missing logs by polling `GET /api/v2/logs` with `from` parameter for the gap window |
| Auth0 tenant in EU region but application enforcing user data in US region | `sub` claim resolves to a user stored in EU Auth0 infrastructure; GDPR data residency conflict | Compliance audit identifies cross-region user data flow | Review Auth0 tenant region: Auth0 Dashboard > Settings > Tenant region; compare with application data residency policy | Migrate to a new Auth0 tenant in the correct region; export/import users with `auth0-deploy-cli`; update all application client configs |
| Duplicate Auth0 user entries from social + database connection with same email | `GET /api/v2/users?q=email:<email>` returns 2+ users; login shows both identities; user_id differs | User logs in via Google and gets different profile than when using email/password; two separate user records | Data fragmentation; purchase history, preferences split across two identities | Link accounts: `POST /api/v2/users/<primary-id>/identities` with secondary `user_id`; enable Auth0 account linking via Action |
| `app_metadata` written by Terraform (management API) overwriting Action-written values | After Terraform apply, `app_metadata.roles` reset to IaC-defined values; runtime role assignments lost | `GET /api/v2/users/<id>` shows stale `app_metadata` after Terraform run | Users lose runtime-granted roles; authorization failures | Use merge strategy in Terraform: `auth0_user_metadata` resource with `merge = true`; separate Terraform-managed and runtime-managed metadata keys |
| Auth0 Rules (legacy) and Actions (new) both running for same trigger — double execution | Both a Rule and an Action run on login; `user_metadata` written twice; conflicting claims added to token | Auth0 logs show both `rule:X` and `action:Y` in same login event | Token contains duplicate or conflicting claims; downstream services reject malformed JWT | Disable the legacy Rule once the Action is confirmed working: Auth0 Dashboard > Auth Pipeline > Rules; never run both for the same trigger |
| Stale refresh token after user password reset | User resets password; existing refresh tokens remain valid (if not revoked); old sessions continue | `POST /oauth/token` with old `refresh_token` succeeds despite password change | Security: compromised sessions remain active after password reset | Enable Auth0 refresh token revocation on password change: `POST /api/v2/users/<id>/sessions` to revoke all sessions; configure tenant to auto-revoke |
| Auth0 rate limit on user search API causing pagination failures | `GET /api/v2/users?page=N` returns `429` during bulk user export | User export script fails mid-way; exported user list incomplete | Partial user migration or audit report with missing users | Use checkpoint-based export with `from` (log ID) pagination instead of page-based; implement retry with `Retry-After` header respect |

## Runbook Decision Trees

### Decision Tree 1: Users Cannot Log In

```
Users reporting login failures?
├── Check Auth0 status: https://status.auth0.com
│   ├── OUTAGE listed → Auth0 upstream issue
│   │   ├── Implement degraded mode (show banner, extend session TTL)
│   │   ├── Enable M2M token stale-use grace period
│   │   └── Monitor status page; page Auth0 support via https://support.auth0.com
│   └── No outage → investigate tenant
│       ├── Check Auth0 logs for recent failures:
│       │   curl -H "Authorization: Bearer $MGMT_TOKEN" \
│       │     "https://$DOMAIN/api/v2/logs?q=type:f&sort=date:-1&per_page=20"
│       │   ├── type:fp (redirect_uri mismatch) → Add missing URL to Allowed Callback URLs in app settings
│       │   ├── type:eacft (Action error) → Disable failing Action; check Action code
│       │   ├── type:limit_wc (rate limit) → Cache tokens; contact Auth0 to raise limit
│       │   ├── type:fs (SAML error) → Check IdP certificate expiry; re-upload SAML metadata
│       │   └── type:f (generic failure) → Check Universal Login template for JS errors
│       └── Check custom domain:
│           dig <custom-domain>
│           ├── DNS resolves → Check TLS cert expiry: openssl s_client -connect <custom-domain>:443
│           └── DNS fails → Revert application to use <tenant>.auth0.com domain temporarily
```

### Decision Tree 2: API Returns 401 Unauthorized

```
API service returning 401 after successful Auth0 login?
├── Check JWT validation in API service logs
│   ├── "invalid signature" error →
│   │   ├── Check JWKS endpoint: curl https://$DOMAIN/.well-known/jwks.json
│   │   ├── Compare `kid` in JWT header with keys in JWKS
│   │   │   ├── `kid` not in JWKS → JWKS cache stale after key rotation
│   │   │   │   → Rolling restart API pods to flush JWKS cache
│   │   │   │   → Set JWKS cache TTL to 600s going forward
│   │   │   └── `kid` present → audience or issuer mismatch
│   │   │       → Check `aud` claim in token vs API identifier in Auth0
│   │   └── "jwt expired" error → Token lifetime issue; check clock skew between servers
│   ├── "invalid_client" from token endpoint →
│   │   ├── M2M client secret rotated → Deploy new secret from secrets manager
│   │   └── Client ID wrong → Verify application client ID in Auth0 Dashboard
│   └── 401 on all requests including health checks →
│       ├── Is token being sent? Verify Authorization header: curl -v ... -H "Authorization: Bearer $TOKEN"
│       └── Token from correct tenant/audience? Decode: echo "<token>" | cut -d. -f2 | base64 -d | jq
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| M2M client requesting new token on every API call instead of caching | `client_credentials` grant called per request; token quota exhausted rapidly | `GET /api/v2/logs?q=type:slo&sort=date:-1` — count `grant_type:client_credentials` events per minute | Auth0 M2M token quota (per subscription tier) exhausted; all M2M auth blocked | Patch application to cache token until `expires_in - 60s`; restart the service | Enforce token caching in all M2M services; monitor `client_credentials` grant count via log stream to SIEM |
| Auth0 log export retention tier upgraded unnecessarily | Log retention set higher than required for the tenant; billing tier forced up | Auth0 Dashboard > Tenant Settings > Log Retention — compare to plan entitlement | Monthly subscription charge increases to next tier; no operational benefit on dev tenants | Reduce log retention to the plan default (consult current Auth0 plan documentation for exact day counts per tier): Auth0 Dashboard > Tenant Settings | Use log streaming to external SIEM for long-term retention; keep Auth0 log retention at minimum plan default |
| Auth0 Actions calling external HTTP API on every login (no caching) | Action performs uncached HTTP call per login; external API rate limited or slow → login latency and cost spike | Auth0 Action execution logs show `details.elapsed > 500ms`; external API access logs show burst | Login latency spike; Auth0 Action timeout exceeded causing login failures; external API costs | Add in-memory cache in Action using `api.cache.set(key, value, ttl)`; fail-open with `try/catch` | Use Auth0 Action built-in cache for external API results; set `ttl` ≥ 60 s for stable data |
| Auth0 Management API called on every user request (no app-side caching) | Application fetches user roles/metadata via Management API per request; rate limit hit | `GET /api/v2/logs?q=type:mgmt_api_read&per_page=100` — high frequency from one client_id | Management API rate limit (limits vary by plan and endpoint; consult current Auth0 docs) exhausted; user-facing API returns `429` | Add Redis/in-memory cache for Management API responses (TTL 60-300 s); return cached data on 429 | Cache Management API responses in application layer; use Auth0 tokens with custom claims to avoid runtime Management API calls |
| Excessive Auth0 tenant users from test account proliferation | Automated tests create new user accounts without cleanup; tenant user count grows beyond plan limit | `GET /api/v2/stats/daily` — cumulative user count; `GET /api/v2/users?q=email:*test*&per_page=1` count | Plan user limit hit; new user signups blocked with `OperationNotAllowed` | Bulk delete test users: paginate `GET /api/v2/users?q=email:*test*` and call `DELETE /api/v2/users/<id>` for each | Use dedicated Auth0 tenant for automated testing; clean up test users in test teardown; set quota alert |
| Auth0 Enterprise SAML connection metadata refresh causing unnecessary API calls | Metadata refresh interval too low; redundant fetches from IdP metadata URL | Check tenant logs for repeated `type:fsa` (SAML assertion) events from the same connection | IdP rate limits metadata fetches; connection degraded | Increase metadata refresh interval in SAML connection settings; cache metadata in Auth0 connection config | Set SAML metadata refresh to 24 h; use static certificate upload instead of URL-based metadata where possible |
| Auth0 custom email provider sending emails to test/dev users | Dev tenant using production SendGrid API key; all registration/password-reset emails delivered and billed | Check SendGrid activity feed for domains matching dev/test users | Unexpected SendGrid billing; potential spam complaints from dev addresses | Switch dev tenant to Auth0 default email provider (free) or use a test inbox service like Mailtrap | Configure custom email provider only on production tenant; use Auth0 built-in email for dev/staging |
| Log streaming to multiple destinations duplicating SIEM ingestion | Auth0 log stream configured with 3 destinations (Splunk + Datadog + custom webhook); all receive same events | `GET /api/v2/log-streams` — count active streams and destinations | SIEM ingestion costs multiplied by destination count; storage costs tripled | Disable redundant log streams: `PATCH /api/v2/log-streams/<id>` with `status: "disabled"` for non-primary streams | Maintain single authoritative log stream; fan out within SIEM if needed; review log stream count quarterly |
| Auth0 Attack Protection SMS OTP sending to auto-generated phone numbers during abuse | Credential stuffing bot registers phone numbers for OTP; SMS costs spike | Auth0 Dashboard > Attack Protection > Bot Detection — enable; check MFA enrollment log `type:gd_start_enroll` spike | Unexpected SMS provider billing; Auth0 Guardian SMS quota exhausted | Enable email-only MFA for new enrollments; enable Auth0 Bot Detection; block disposable phone number patterns | Enable Bot Detection and Suspicious IP Throttling in Attack Protection; require email verification before SMS MFA enrollment |
| Unused Auth0 applications accumulating with `none` client authentication | Legacy apps never deleted; Auth0 plan's application count limit approached; management overhead | `GET /api/v2/clients?per_page=100` — list all clients; filter by last login date in logs | Plan application limit hit; inability to create new applications for legitimate use | Archive unused apps: disable client with `PATCH /api/v2/clients/<id>` body `{"app_type": "non_interactive"}` then delete | Quarterly audit of Auth0 applications; delete apps with no login events in 90 days; enforce IaC-only app creation |

## Latency & Performance Degradation Patterns
| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot tenant — single high-traffic application triggering Auth0 rate limits | Application logs `429 Too Many Requests` from Auth0 token endpoint; login latency spikes | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:limit_*&per_page=20&sort=date:-1"` | Auth0 per-tenant rate limit on `/oauth/token` endpoint hit; burst login traffic exceeds limit | Implement client-side token caching with refresh-before-expiry; use token introspection cache; spread load across multiple Auth0 applications if multi-tenant | Set `cache-control: max-age` for tokens; alert on `type:limit_*` log events |
| Auth0 Action execution latency — slow external HTTP call in Action | Login flow P99 latency spikes > 2 s; Auth0 Action logs show `execution time exceeded` | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=20&sort=date:-1" | jq '[.[] | {date, description}]'` | Action makes synchronous HTTP call to slow internal API; no timeout set; whole login pipeline blocked | Add `fetch` timeout in Action code: `const resp = await fetch(url, {signal: AbortSignal.timeout(2000)})`; cache Action results in `api.cache`; make non-critical calls async | Set Action timeout to 5 s in Dashboard; offload non-critical enrichment to post-login webhook |
| GC / memory pressure in Auth0 Action Node.js runtime | Action invocations fail with `memory limit exceeded`; error type `eacft` with out-of-memory message | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=50&sort=date:-1" | jq '[.[] | select(.description | test("memory"))]'` | Action accumulating large objects in closure scope; or importing heavy npm packages on every invocation | Refactor Action to avoid top-level heavy imports; use `api.cache` for large lookup tables; reduce object allocations | Monitor Action execution time histogram in Auth0 Dashboard → Actions → Monitor |
| Thread pool saturation — Auth0 Management API rate limits on bulk user operations | Bulk user imports/updates receive `429`; management operations queue up | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:sapi&per_page=50" | jq '[.[] | select(.description | test("429"))]'` | Concurrent Management API calls exceeding per-endpoint rate limit (bulk endpoints have stricter limits; consult current Auth0 rate-limit docs) | Implement request queue with rate limiter: `p-throttle` npm library; use `GET /api/v2/jobs/users-imports/{id}` for async bulk import | Batch user operations; use Auth0 bulk jobs API `/api/v2/jobs/users-imports` instead of individual user creates |
| Slow user search query — large tenant with millions of users | `GET /api/v2/users?q=...` takes > 5 s; Management API returns `503` for user search | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/users?q=email%3A%22user%40example.com%22&search_engine=v3"` | Large tenant user store; Lucene query not using indexed fields; `search_engine=v2` (deprecated) | Always use `search_engine=v3`; search on indexed fields only (`email`, `user_id`); use `GET /api/v2/users-by-email` for email lookup | Migrate all queries to `search_engine=v3`; avoid wildcard `q=*` queries on large tenants |
| CPU steal — Auth0 tenant on shared infrastructure during peak | Login latency increases during peak hours (9 AM, market open); no change in application traffic | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:f*&per_page=100&sort=date:-1" | jq '[.[] | .date] | group_by(.[0:13]) | map({hour: .[0][0:13], count: length})'` | Auth0 shared infrastructure under load from other tenants; no mitigation for SaaS users | Upgrade to Auth0 Private Cloud or dedicated deployment if available; spread login traffic via pre-login queuing | Monitor Auth0 status page (status.auth0.com); open support ticket with latency evidence during peak |
| Lock contention — Auth0 Rules/Actions updating same user metadata concurrently | Login pipeline serializes on user `app_metadata` update; concurrent logins for same user slow | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=user_id:<id>&per_page=50&sort=date:-1" | jq '[.[] | {type, date}]'` | Multiple Actions each calling `auth0.updateAppMetadata()`; each call is a Management API write; rate-limited and serialized | Batch all `app_metadata` updates into a single Action at end of pipeline; use read-only earlier in pipeline; avoid metadata writes in hot path | Design pipeline to write metadata at most once per login; cache metadata in token claims |
| Serialization overhead — oversized ID token payload | Application JWT decode takes > 10 ms; token size > 10 KB; cookie truncation on some browsers | `curl -s "https://$DOMAIN/oauth/token" -d "..." | jq -r '.id_token' | cut -d. -f2 | base64 -d 2>/dev/null | jq '. | length'` | Excessive claims added by Actions (full user profile, large permission arrays); token serialized on every request | Remove non-essential claims from ID token; move large data to userinfo endpoint; use `accessToken` for API claims only | Limit Action-added claims; token size should be < 4 KB; use Auth0's `userinfo` endpoint for large profile data |
| Batch size misconfiguration — Auth0 User Import job with too many users per file | User import job fails with `Request Entity Too Large`; large import batches cause job timeouts | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/jobs/<job_id>"` — check `status` and `errors_count` | Single import file exceeds Auth0's 500 KB / 10,000 users per job limit | Split import files: max 10,000 users per job; run multiple concurrent jobs; use `upsert: true` for idempotent re-imports | Enforce file size limit in pre-import script; monitor job `status` polling via `GET /api/v2/jobs/{id}` |
| Downstream social connection provider latency | Google/GitHub OAuth login takes > 5 s; `type:fsa` (failed social auth) events in logs | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:fsa&per_page=20&sort=date:-1" | jq '[.[] | {date, connection: .details.body.connection}]'` | Social identity provider (Google, GitHub) experiencing elevated latency; Auth0 proxies OAuth redirect | Show user-facing loading indicator during social login; implement login fallback to username/password; alert on `type:fsa` spike | Monitor `type:fsa` log event rate; set up Auth0 alert for failed social authentication spike |

## Network & TLS Failure Patterns
| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on custom domain | Browser shows `ERR_CERT_DATE_INVALID` on `login.example.com`; `openssl s_client` shows expired cert | `echo | openssl s_client -connect login.$DOMAIN:443 2>/dev/null | openssl x509 -noout -enddate` | All users redirected to login page see certificate error; authentication unavailable | Rotate custom domain cert in Auth0 Dashboard → Custom Domains → Renew; or rotate ACM cert if using AWS CloudFront fronting Auth0 |
| mTLS rotation failure — API client cert rejected by Auth0 Management API | Management API returns `401 Unauthorized` after client cert rotation; `type:sapi` log shows `certificate_error` | `curl --cert client.crt --key client.key -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/clients"` | All automated Management API operations (user provisioning, RBAC) fail | Re-upload client certificate to Auth0 application in Dashboard → Applications → <app> → Credentials; verify `client_assertion_type` |
| DNS resolution failure for Auth0 custom domain | Application OAuth redirect fails; `nslookup login.example.com` returns NXDOMAIN | `nslookup login.$DOMAIN` from application host | All login flows fail for users; authentication entirely unavailable | Check Route 53 / DNS provider for CNAME pointing to Auth0 custom domain endpoint; restore DNS record; verify `dig login.$DOMAIN CNAME` |
| TCP connection exhaustion — high-volume token endpoint calls from single host | Application host accumulates `TIME_WAIT` on port 443 to Auth0; `connect() failed: Cannot assign requested address` | `ss -s | grep TIME-WAIT` on application host | Application cannot reach Auth0 for token validation; all API requests fail authentication | Enable TCP reuse: `sysctl -w net.ipv4.tcp_tw_reuse=1`; implement token caching to reduce Auth0 calls; use connection pool with keep-alive | Cache JWTs; validate tokens locally using JWKS instead of Auth0 introspection endpoint per request |
| Load balancer (CDN/WAF) misconfiguration blocking Auth0 callback URL | Login returns `403 Forbidden` after OAuth redirect; WAF log shows blocked request to `/callback` | `curl -I "https://$APP_DOMAIN/callback?code=test&state=test"` — check for 403 | OAuth authorization code flow fails; users stuck in login loop | Add `/callback` path exception to WAF rules; whitelist Auth0 IP ranges (available from Auth0 docs); verify CDN passthrough of OAuth params |
| Packet loss between Auth0 and application's token introspection caller | Intermittent `503` from Auth0; application logs `connection reset` or `timeout` calling Auth0 APIs | `curl -o /dev/null -s -w "%{http_code} %{time_total}" "https://$DOMAIN/oauth/token"` — check for intermittent non-200 | Intermittent authentication failures; user experience degraded; retry storms possible | Implement retry with exponential backoff on Auth0 API calls; use circuit breaker (resilience4j / axios-retry); cache JWKS locally to avoid per-request Auth0 calls | Cache JWKS with 1-hour TTL; use local JWT verification instead of introspection |
| MTU mismatch causing Auth0 large response truncation | Auth0 JWKS or large userinfo response truncated; JWT signature validation fails with `invalid signature` | `ping -M do -s 1400 $DOMAIN` from application host | JWT validation fails; all API requests rejected as unauthenticated | Fix MTU on network path; add TCP MSS clamping; verify with `curl -v "https://$DOMAIN/.well-known/jwks.json" | wc -c` matches expected size |
| Firewall rule change blocking outbound HTTPS to Auth0 from application | Application logs `connection refused` to `$DOMAIN:443`; OAuth flows fail | `curl -v "https://$DOMAIN/.well-known/openid-configuration"` from application host | Complete authentication outage; no user can log in | Restore outbound HTTPS rule: allow egress to `*.auth0.com:443` from application security group; verify: `nc -zv $DOMAIN 443` |
| SSL handshake timeout — Auth0 Action calling internal mTLS-protected service | Action fails with `SSL handshake timeout`; login blocked for affected connection | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=20" | jq '[.[] | select(.description | test("timeout|SSL"))]'` | Internal service TLS handshake slow or certificate mismatch; Action has no timeout guard | Add timeout in Action: `AbortSignal.timeout(3000)`; disable mTLS requirement on internal endpoint for Auth0 egress IPs; investigate internal service cert |
| Connection reset from Auth0 webhook/log stream to SIEM | Log stream shows delivery failures; SIEM not receiving Auth0 events | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/log-streams" | jq '[.[] | {name, status}]'` | SIEM endpoint IP or port changed; Auth0 log stream cannot reach destination | Update log stream endpoint: Dashboard → Monitoring → Streams → edit destination URL; verify SIEM accepts connections from Auth0 IP ranges |

## Resource Exhaustion Patterns
| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| Auth0 tenant log storage quota exhausted | New log events not written; `GET /api/v2/logs` returns stale events; audit trail incomplete | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?per_page=1&sort=date:-1" | jq '.[0].date'` — compare to current time | Auth0 log retention is managed by Auth0 (2-30 days); older logs purged automatically | Stream logs to SIEM immediately: configure Auth0 log stream to Splunk/Datadog/S3; Auth0 log storage is not configurable | Always configure log streaming to external SIEM; never rely on Auth0 native log retention as sole audit trail |
| Auth0 tenant application limit reached | `POST /api/v2/clients` returns `403 Limit Reached`; cannot create new applications | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/clients?per_page=100" | jq 'length'` | Auth0 plan's max application count reached; unused legacy applications accumulating | Delete unused applications: `curl -H "Authorization: Bearer $MGMT_TOKEN" -X DELETE "https://$DOMAIN/api/v2/clients/<id>"`; upgrade Auth0 plan if limit is legitimate | Quarterly audit of applications; enforce IaC-only app creation via `auth0-deploy-cli`; delete apps after project sunset |
| Auth0 Management API token quota exhausted | All Management API calls return `429`; automated provisioning, RBAC sync, and monitoring fail | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:limit_wc&per_page=20" | jq '[.[] | {date, description}]'` | Management API rate limit hit (varies by plan); all automation blocked | Reduce Management API call frequency; implement request queue with token bucket; cache `GET` responses with TTL | Cache Management API responses; use Auth0 webhooks instead of polling; rate-limit all automation to < 2 req/s |
| Auth0 Action npm dependency disk quota | Action deployment fails with `npm install failed: ENOSPC`; cannot update Actions | Check via Auth0 Dashboard → Actions → Monitor for deployment errors | Action dependency tree too large; Auth0 limits Action bundle size | Reduce dependencies: use built-in Node.js APIs instead of npm packages; audit `require()` statements; remove unused packages | Limit Action npm dependencies to < 10 packages; prefer Auth0 SDK built-in helpers; avoid large packages (e.g., `lodash`, `moment`) |
| CPU throttle — Auth0 Action execution time limit hit | Action returns `timeout exceeded`; login blocked with error `eacft` type | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=50" | jq '[.[] | select(.description | test("timeout"))]'` | Action execution time exceeds Auth0's documented per-trigger timeout; synchronous external calls block | Make non-critical calls async; use `api.cache` for lookup data; move heavy processing out of Action into background API | Profile Action execution time in Auth0 Dashboard → Actions → Monitor; set internal soft timeout < 5 s |
| Auth0 user import job queue exhaustion | `POST /api/v2/jobs/users-imports` returns `429`; bulk user migration stalled | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/jobs?per_page=50" | jq '[.[] | select(.type=="users_import") | {id, status, created_at}]'` | Auth0 concurrent job limit per tenant reached; too many parallel import jobs submitted | Wait for existing jobs to complete: poll `GET /api/v2/jobs/<id>` until `status=completed`; submit next batch sequentially | Implement sequential job submission with polling; never submit more than 5 concurrent import jobs |
| Swap / memory exhaustion in Auth0 Action (oversized cache) | Actions fail with out-of-memory errors; large datasets cached in `api.cache` | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=50" | jq '[.[] | select(.description | test("memory|heap"))]'` | Caching large lookup tables (user roles, permission maps) in Action `api.cache` exceeds memory limit | Reduce cached data size; paginate large lookups; use external cache (Redis/Elasticache) accessible from Action via HTTPS | Limit cached objects to < 1 MB; use external cache for datasets > 10 KB |
| Auth0 connection limit — too many social/enterprise connections | `POST /api/v2/connections` returns `403 Limit Reached`; cannot add new SSO connection | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/connections?per_page=100" | jq 'length'` | Auth0 plan's connection limit reached; legacy or unused connections not deactivated | Delete unused connections: `curl -H "Authorization: Bearer $MGMT_TOKEN" -X DELETE "https://$DOMAIN/api/v2/connections/<id>"`; upgrade plan | Quarterly connection audit; enforce IaC-only connection creation; decommission connections on SSO offboarding |
| Auth0 log stream delivery backlog | Log stream paused; `GET /api/v2/log-streams` shows `status: paused`; SIEM gap in Auth0 events | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/log-streams" | jq '[.[] | {name, status, isPaused}]'` | SIEM endpoint unavailable for > 60 min; Auth0 pauses delivery; events lost up to retention limit | Restore SIEM endpoint; resume log stream: Auth0 Dashboard → Monitoring → Streams → Resume; accept gap in coverage | Use multiple log stream destinations; alert on log stream `status != active`; SIEM endpoint must have 99.9% uptime |
| Ephemeral port exhaustion on application calling Auth0 per-request | Application host `Cannot assign requested address` on port 443 to Auth0; one connection per API request | `ss -s | grep TIME-WAIT` on application host | Application calling Auth0 token introspection or Management API per request without connection reuse | Implement JWKS-based local token validation (no Auth0 call per request); use HTTP keep-alive; cache Management API tokens with `expires_in` TTL | Never call Auth0 per-request; validate JWT locally; refresh Management API token only on expiry |

## Distributed Transaction & Event Ordering Failures
| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation — duplicate user created via concurrent registration | Two concurrent calls to `POST /api/v2/users` with same `email`; second call creates duplicate if unique constraint not enforced by calling service | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/users-by-email?email=<email>"` — check for multiple results | Duplicate Auth0 users; application can't determine canonical user; downstream profile data split | Deduplicate: link accounts via `POST /api/v2/users/<primary>/identities`; delete duplicate: `DELETE /api/v2/users/<duplicate_id>`; add application-level idempotency key before calling `POST /api/v2/users` |
| Saga partial failure — user created in Auth0 but application database write failed | Auth0 user exists; application `users` table has no record; user can authenticate but has no profile | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/users/<user_id>"` — user exists; `SELECT * FROM users WHERE auth0_id = '<user_id>'` — no row | User gets `403` or blank profile on first login; cannot access application | Implement post-registration webhook or Auth0 Action to create application profile on first login; use idempotent upsert in application on every login event |
| Out-of-order event processing — `app_metadata` update from old JWT token overwriting newer write | Application updates `app_metadata` using stale Management API token; older timestamp overwrites newer subscription status | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/users/<id>" | jq '.app_metadata'` — compare with expected current state | User's Auth0 metadata reverted; subscription tier, roles, or feature flags reset to old value | Re-apply correct `app_metadata`: `PATCH /api/v2/users/<id>` with correct values; implement versioned `app_metadata` with `updated_at` field and check before write |
| At-least-once delivery duplicate — Auth0 post-login Action triggers external API twice | Auth0 Action calls external API on every login; user logs in twice in quick succession; API called twice | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=user_id:<id>+type:s&per_page=20&sort=date:-1" | jq '[.[] | .date]'` | External API side-effects triggered twice (email sent, credit debited, audit log duplicated) | Make external API calls idempotent: pass `idempotency-key: <user_id>-<login_event_id>` header; use `type:s` (login) log `log_id` as idempotency key | Always pass idempotency keys to downstream APIs from Actions; never rely on Auth0 Actions being called exactly once |
| Compensating transaction failure — user role grant fails after provisioning Action | Auth0 Action grants role via Management API; Management API rate-limited; role not assigned; Action returns success | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/users/<id>/roles" | jq '.'` — check if role is present | User authenticates but missing expected roles; authorization checks fail in application | Re-grant role: `curl -H "Authorization: Bearer $MGMT_TOKEN" -X POST "https://$DOMAIN/api/v2/users/<id>/roles" -d '{"roles":["<role_id>"]}'`; add retry logic in Action for Management API calls | Implement retry with backoff in Action for Management API calls; verify role assignment in Action before returning; alert on `type:eacft` log events |
| Distributed lock expiry mid-operation — JWKS key rotation mid-token-validation | Auth0 rotates signing key; application cached old JWKS; tokens signed with new key rejected | `curl "https://$DOMAIN/.well-known/jwks.json" | jq '[.keys[] | .kid]'` vs JWT header `kid` | All token validations fail for tokens signed with new key; users receive `401 Unauthorized` | Force JWKS cache refresh in application; update JWKS cache TTL to 1 hour with `kid`-miss re-fetch; restart application pods to clear in-memory JWKS cache | Implement JWKS cache with `kid`-based invalidation: if `kid` not found in cache, re-fetch before rejecting token |
| Cross-service deadlock — Auth0 Action and application both updating user metadata simultaneously | Application updates `app_metadata` via Management API while Action also updates during login; last-write-wins causes data loss | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:sapi+user_id:<id>&per_page=20" | jq '[.[] | {date, description}]'` | User's `app_metadata` in inconsistent state; authorization decisions based on stale data | Establish ownership: only Auth0 Actions write `app_metadata`; application reads metadata from token claims only; never write `app_metadata` from both Action and application layer simultaneously |
| Message replay causing stale Auth0 session token reuse | Attacker or misconfigured client replays old authorization code; Auth0 accepts it if PKCE not enforced | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:fece&per_page=20" | jq '[.[] | {date, description}]'` | Authorization code reuse; potential account compromise if replay succeeds | Enforce PKCE for all public clients: set `require_pkce: true` in application settings; enable `log type:fece` alert for code exchange failures; rotate all application client secrets |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — one Auth0 application triggering heavy Action execution for all users | Action execution time rising; `type:eacft` log events showing increasing `execution time`; all applications on same tenant affected | All login flows slower; login latency P99 > 3 s for all applications using Actions | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=100" | jq '[.[] | {date, client_id}] | group_by(.client_id) | map({client: .[0].client_id, count: length}) | sort_by(.count) | reverse | .[0:3]'` | Bind slow Action to specific application only; optimize Action code; remove blocking HTTP calls; use `api.cache` |
| Memory pressure — Auth0 Action with large in-memory lookup table causing OOM during peak login | Action `type:eacft` errors with `memory exceeded` during business hours; other applications' logins also affected | All applications' Actions share same Node.js runtime resource pool; mass login failures | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=50" | jq '[.[] | select(.description | test("memory"))] | length'` | Reduce Action in-memory cache size; use `api.cache` with bounded size; move large lookups to external API endpoint |
| Disk I/O saturation — high-volume application generating millions of Auth0 log events | Auth0 log stream delivery falling behind; SIEM showing log gaps; other applications' logs delayed in stream | All applications' audit trails delayed; compliance monitoring blind spots | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=client_id:<high_volume_app>&per_page=1" | jq '.[0].date'` — compare to current time | Reduce log verbosity for high-volume app: adjust Auth0 log level; consider dedicated Auth0 tenant for high-volume application |
| Network bandwidth monopoly — one application requesting large `userinfo` payload on every token validation | Auth0 `/userinfo` endpoint latency increasing; all applications sharing tenant affected | Token validation slower for all; API response times increase across all applications | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:ssa&per_page=20" | jq '[.[] | {date, client_id, scope}]'` — check for large scope requests | Reduce `/userinfo` scope: remove unnecessary claims from application scope request; move large profile data to dedicated API |
| Connection pool starvation — one application's M2M token refresh loop monopolizing Management API rate limit | All Management API calls returning `429`; monitoring, provisioning, and RBAC sync all blocked | All Management API automation blocked across all applications; alerting and provisioning fail | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:limit_wc&per_page=20" | jq '[.[] | {date, client_id}]'` | Identify and fix token refresh loop: implement token caching with `expires_in` TTL; rate-limit Management API calls |
| Quota enforcement gap — no per-application login rate limit; one app's credential stuffing exhausts tenant rate limit | Auth0 tenant rate limit hit by botnet targeting one application; all other applications also rate limited | All applications return `429` to legitimate users; complete authentication outage | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:f&per_page=100" | jq '[.[] | .client_id] | group_by(.) | map({client: .[0], count: length}) | sort_by(.count) | reverse'` | Enable per-application Brute Force Protection; add CAPTCHA for affected application; implement WAF rate limiting per application |
| Cross-tenant data leak risk — Auth0 Action reading `app_metadata` from wrong user due to race condition | Action updating `user.app_metadata` writing to wrong user during concurrent login burst | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/users?q=app_metadata.last_ip_change:*&per_page=10&search_engine=v3" | jq '.'` — inspect for unexpected metadata values | Disable affected Action immediately; audit `app_metadata` for affected users; restore correct metadata | Make Action user ID handling explicit: always use `event.user.user_id` not cached variable; never share state between Action invocations |
| Rate limit bypass — application using multiple M2M clients to circumvent per-client rate limits | Management API calls succeeding despite apparent rate limit; total API calls 10× expected | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/clients?q=name:automation*&per_page=100" | jq 'length'` — check for many automation clients | Consolidate M2M clients: one client per service; revoke excess clients; implement request queue | Enforce maximum M2M client count per application team; audit via `GET /api/v2/clients` quarterly |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure — Auth0 log stream to Datadog interrupted | Auth0 login failure spike not visible in Datadog; SRE alerted by user complaints instead of metrics | Log stream delivery paused due to Datadog API key rotation; Auth0 does not retry after extended pause | Check Auth0 log stream status: `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/log-streams" | jq '[.[] | {name, status}]'`; query Auth0 directly: `curl ... "https://$DOMAIN/api/v2/logs?q=type:f&per_page=100"` | Update Datadog API key in log stream; resume stream; configure backup log stream to S3; alert on log stream `status != active` |
| Trace sampling gap — Auth0 Actions not emitting traces for fast-failing executions | Fast-failing Actions (< 100 ms) not visible in APM; intermittent login failures untraced | Auth0 Actions do not natively emit OpenTelemetry traces; only Auth0 log events available | Use `type:eacft` log events as proxy for Action failure traces: `curl ... "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=100&sort=date:-1"` | Add manual instrumentation in Action: `console.log(JSON.stringify({event: 'action_start', userId: event.user.user_id, ts: Date.now()}))`; ship console logs to SIEM |
| Log pipeline silent drop — Auth0 log stream overwhelmed during login spike | Login event logs missing from SIEM during peak traffic; audit trail incomplete during incident | Auth0 log stream HTTPS delivery timeout during high-volume spike; events lost without retry | Query Auth0 native logs for the gap window: `curl ... "https://$DOMAIN/api/v2/logs?q=date:[<start> TO <end>]&per_page=100"` | Implement second log stream to S3 as backup; set SIEM endpoint auto-scaling; use Auth0 log stream `resumeFrom` to replay missed events |
| Alert rule misconfiguration — `type:f` login failure alert not firing because log type changed | Auth0 changed `type:fu` (failed login with wrong username) not covered by `type:f` alert query | Alert query uses only `type:f`; Auth0 has 50+ event types; `fu`, `fp`, `fcp` also indicate failures | List all failure event types: `curl ... "https://$DOMAIN/api/v2/logs?q=type:f*&per_page=100" | jq '[.[] | .type] | unique'` | Update alert to cover all failure types: `type:(f OR fu OR fp OR fcp OR fsa OR fcoa)`; review Auth0 log event type list after each Auth0 update |
| Cardinality explosion — per-user Auth0 log metrics creating millions of time series in SIEM | SIEM slow query on Auth0 log dashboard; indexes bloated; cardinality > 1M unique user_ids | SIEM indexes `user_id` field from Auth0 logs; millions of users × 50 event types = millions of series | Aggregate by event type only: `SELECT type, COUNT(*) FROM auth0_logs GROUP BY type ORDER BY 2 DESC` without `user_id` in dashboard | Set SIEM field exclusion for `user_id` in Auth0 log index; use anonymized `user_hash` for cardinality-sensitive dashboards |
| Missing health endpoint — Auth0 tenant login available check not monitored | Auth0 tenant degraded (login latency > 10 s) but no synthetic monitor detecting it | Auth0 does not expose a `/_healthz` endpoint; only status.auth0.com is monitored | Run Synthetic Monitor via CloudWatch or Datadog: `POST https://$DOMAIN/oauth/token` with test user credentials every 60 s; alert on `status != 200` or response time > 3 s | Configure Datadog Synthetic Test or AWS CloudWatch Synthetics to simulate full login flow every minute; alert on failure |
| Instrumentation gap — no metric for Auth0 custom domain certificate expiry | Custom domain `login.example.com` certificate expires; users cannot log in; no advance warning | ACM or custom certificate expiry not monitored via Auth0 Management API; only detected when expired | Check certificate expiry: `echo | openssl s_client -connect login.$DOMAIN:443 2>/dev/null | openssl x509 -noout -enddate`; also: Auth0 Dashboard → Custom Domains → certificate details | Add certificate expiry check to synthetic monitor; alert 30 days before expiry; automate renewal via `cert-manager` or ACM auto-renewal |
| Alertmanager/PagerDuty outage during Auth0 incident | Auth0 login failures spiking; PagerDuty not firing; SRE unaware until customer complaints | PagerDuty integration key in Alertmanager expired; or Alertmanager pod down | Check Auth0 status directly: `curl -s https://status.auth0.com/api/v2/status.json | jq '.status.description'`; subscribe to Auth0 status page webhooks | Configure Auth0 log stream webhook directly to PagerDuty as backup; test PagerDuty integration monthly; use Auth0 Monitor alerts natively |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor version upgrade rollback — Auth0 Actions Node.js runtime upgrade breaks `require()` compatibility | Auth0 Action fails with `Cannot find module 'crypto'` after runtime upgrade from Node 16 → 18 | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=20" | jq '[.[] | {date, description}]'` | Revert Action runtime: Auth0 Dashboard → Actions → <action> → Edit → Runtime → select `node16`; redeploy Action | Test Actions against new runtime in Auth0 Actions sandbox before accepting runtime upgrade; verify all `require()` calls |
| Major version upgrade — Auth0 tenant migrated to new pipeline; Actions replace Rules/Hooks | Existing Rules continue to run alongside new Actions; duplicate processing causes double role assignments | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/rules" | jq '[.[] | select(.enabled==true) | .name]'` | Disable duplicate Rules that overlap with new Actions: `curl -H "Authorization: Bearer $MGMT_TOKEN" -X PATCH "https://$DOMAIN/api/v2/rules/<id>" -d '{"enabled":false}'` | Migrate Rules to Actions incrementally; disable Rule before enabling equivalent Action; never run both simultaneously |
| Schema migration partial completion — Auth0 user metadata migration via bulk import partially completed | Half of users have new `app_metadata` schema; other half still on old schema; application crashes on schema mismatch | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/jobs/<job_id>" | jq '{status, summary}'` — check import job status | Resume failed import job or retry with remaining users; use `upsert: true` to safely re-import completed users | Use idempotent `upsert: true` in all bulk imports; monitor job status via `GET /api/v2/jobs/<id>` polling; implement two-phase migration (optional field first) |
| Rolling upgrade version skew — auth0-deploy-cli version mismatch between CI pipelines | Some CI pipelines deploy Auth0 config in old format; others in new format; config overwrites between pipelines | `a0deploy export --config_file config.json --output_folder /tmp/current` then `diff /tmp/current /tmp/expected` | Pin `auth0-deploy-cli` version in all CI pipelines; `npm install auth0-deploy-cli@2.8.0`; validate config format before deploy | Enforce `auth0-deploy-cli` version via `package.json` `exact` version pinning; use `--dry-run` before applying |
| Zero-downtime migration gone wrong — Auth0 custom domain DNS cutover breaks existing sessions | After DNS CNAME update, existing sessions using old domain cookie become invalid; all users logged out | `curl -I "https://login.$DOMAIN/co/authenticate"` — check for expected auth response | Revert DNS CNAME to old domain; re-configure Auth0 custom domain to old value; add old domain as allowed origin | Test custom domain migration in staging with production traffic simulation; plan for session invalidation and user re-login during migration window |
| Config format change — auth0-deploy-cli v2 → v3 YAML format change for connections | CI pipeline fails on `auth0-deploy-cli` v3 with `Invalid connection type: oauth2`; Auth0 connections not updated | `a0deploy export --config_file config.json 2>&1 | grep ERROR` | Revert to `auth0-deploy-cli` v2: `npm install auth0-deploy-cli@2.x`; convert YAML to v2 format | Read `auth0-deploy-cli` changelog before upgrading; run `--dry-run` in staging; validate all connection configs post-upgrade |
| Data format incompatibility — Auth0 `user_metadata` schema change breaking application deserialization | Application throws `KeyError: 'legacy_field'` after Auth0 Management API response schema change | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/users/<sample_user_id>" | jq '.user_metadata'` — compare to expected schema | Add default handling for missing field in application code; backfill missing `user_metadata` field via bulk update job | Use schema validation on Auth0 `user_metadata` in Action pre-write; add application-layer migration on first access |
| Feature flag rollout — Auth0 Organizations feature enabled causing existing multi-tenant login to break | Users of multi-tenant application see `organization_id is required` error after enabling Organizations | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:f&per_page=20" | jq '[.[] | select(.description | test("organization"))]'` | Disable Organizations for affected application: Auth0 Dashboard → Applications → <app> → Organizations → Disable | Enable Organizations in shadow mode first; update all application login flows to include `organization` parameter before enabling |
| Dependency version conflict — Auth0 SDK version incompatible with new token endpoint behavior | Application using `auth0-spa-js` 1.x gets `invalid_grant` on token refresh after Auth0 update | `curl "https://$DOMAIN/.well-known/openid-configuration" | jq '.grant_types_supported'` — compare to SDK expectations | Upgrade `auth0-spa-js` to `^2.0.0`; clear application's cached tokens; force re-login | Pin `auth0-spa-js` to tested version; upgrade in staging first; monitor `type:f` with `error: invalid_grant` log events post-upgrade |

## Kernel/OS & Host-Level Failure Patterns
| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|-------------------|--------------|-------------|----------------------|
| Auth0 Actions managed Node.js runtime OOM-killed mid-execution; Action returns `internal_error` | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:eacft&per_page=50" | jq '[.[] | select(.description | test("memory|killed|internal"))]'` | Action code loading large user lists or external HTTP responses into memory exceeding the runtime memory limit | Action execution fails; login flow interrupted; users see generic error page | Reduce Action memory footprint: paginate API calls; stream JSON parsing; if self-hosted auth proxy, check host: `dmesg -T | grep -i 'oom\|killed'` and increase container memory limit |
| Auth0 log export inode exhaustion on log-streaming receiver host | `df -i /var/log/auth0-export/` on log receiver host; inode usage > 95% | Auth0 Log Streaming webhook creates one file per event batch; millions of small files exhaust inodes | Log export stops writing; compliance gap; SIEM missing Auth0 events | `find /var/log/auth0-export/ -type f -mtime +7 -delete`; switch to single-file append or S3 streaming: reconfigure Auth0 Log Stream to S3 Event Bridge |
| CPU steal on auth proxy EC2 instance causing Auth0 callback latency > 5 s | `sar -u 1 5 | awk '$NF ~ /steal/ || NR==3{print}'` on auth proxy host; `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/logs?q=type:seacft&per_page=10" | jq '.[].details.elapsedTime'` | Noisy neighbor on shared EC2 instance; auth proxy callback handler starved of CPU | Auth0 callback times out; authorization code exchange fails; users stuck on login page | Migrate auth proxy to dedicated instance or burstable `t3.medium` with unlimited credits: `aws ec2 modify-instance-attribute --instance-id $ID --instance-type '{"Value":"t3.medium"}'` |
| NTP skew on auth proxy causing JWT `iat` / `exp` validation failures | `chronyc tracking | grep 'System time'` on auth proxy; `curl -s "https://$DOMAIN/.well-known/openid-configuration" | jq '.id_token_signing_alg_values_supported'`; test: `date -u +%s` vs `curl -s http://worldtimeapi.org/api/timezone/Etc/UTC | jq '.unixtime'` | NTP daemon stopped or firewall blocking UDP 123; host clock drifted > 30 s | JWT validation rejects valid tokens (`exp` appears in past); users logged out repeatedly | `systemctl restart chronyd && chronyc makestep 1 3`; verify: `chronyc tracking | grep 'System time'`; add NTP monitoring: check `chronyc tracking` offset < 1 s |
| File descriptor exhaustion on auth proxy handling Auth0 OIDC callback connections | `ls /proc/$(pgrep -f auth-proxy)/fd | wc -l`; `cat /proc/sys/fs/file-nr`; `ss -s | grep estab` | Auth proxy not closing HTTP connections after Auth0 token exchange; fd leak accumulates over days | New login requests get `Too many open files`; auth proxy returns 502; all login flows fail | `sysctl -w fs.file-max=1048576`; restart auth proxy process; fix connection pool: set `http.Client{Timeout: 10 * time.Second}` or equivalent keep-alive close |
| Conntrack table full on auth proxy NAT gateway blocking Auth0 API calls | `conntrack -C` or `cat /proc/sys/net/netfilter/nf_conntrack_count` vs `cat /proc/sys/net/netfilter/nf_conntrack_max`; `dmesg | grep conntrack` | High-volume token refresh requests from many tenants exhausting conntrack table on NAT gateway | New connections to Auth0 tenant endpoints dropped; token refresh fails; users see `network_error` | `sysctl -w net.netfilter.nf_conntrack_max=524288`; reduce conntrack timeout: `sysctl -w net.netfilter.nf_conntrack_tcp_timeout_time_wait=30`; consider direct routing without NAT |
| Kernel panic on auth proxy host after security patch | `journalctl -k -b -1 | grep -i panic` on auth proxy host; check uptime: `uptime`; verify last reboot was unplanned | Kernel security patch (e.g., Spectre/Meltdown mitigation) causing panic on boot or under load | Auth proxy completely unavailable; all Auth0 login flows for applications routing through this host fail | Boot previous kernel: select older kernel in GRUB; `grub2-set-default 1 && reboot`; file kernel bug with vendor; add redundant auth proxy in different AZ |
| NUMA imbalance on multi-socket auth proxy causing inconsistent Auth0 callback latency | `numactl --hardware`; `numastat -p $(pgrep -f auth-proxy)`; check P99 vs P50 latency spread > 10x | Auth proxy process memory allocated on remote NUMA node; cross-socket memory access adds 50-100 ns per operation | Some Auth0 callback requests complete in 50 ms, others take 500 ms; inconsistent user experience | `numactl --cpunodebind=0 --membind=0 /usr/bin/auth-proxy`; or set systemd service: `CPUAffinity=0-15` and `NUMAPolicy=bind` |

## Deployment Pipeline & GitOps Failure Patterns
| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Image pull rate limit — auth proxy container image pull fails from Docker Hub | `kubectl describe pod auth-proxy | grep -A5 'Events'` shows `ImagePullBackOff: toomanyrequests` | `kubectl get events -n auth --field-selector reason=Failed | grep -i 'pull\|rate\|limit'` | `kubectl set image deployment/auth-proxy auth-proxy=$PRIVATE_REGISTRY/auth-proxy:$PREVIOUS_TAG`; pull from private ECR mirror | Mirror images to private ECR: `aws ecr get-login-password | docker login --username AWS --password-stdin $ECR`; update Helm chart `image.repository` to ECR |
| Auth failure — auth-deploy-cli CI pipeline cannot authenticate to Auth0 Management API | CI pipeline fails: `401 Unauthorized` on Auth0 Management API; tenant config not deployed | `curl -s -o /dev/null -w '%{http_code}' -X POST "https://$DOMAIN/oauth/token" -d '{"client_id":"$M2M_CLIENT_ID","client_secret":"$M2M_SECRET","audience":"https://$DOMAIN/api/v2/","grant_type":"client_credentials"}'` | Rotate M2M client secret in Auth0 Dashboard; update CI secret: `gh secret set AUTH0_CLIENT_SECRET --body "$NEW_SECRET"` | Rotate M2M secrets quarterly via automation; alert on 401 responses in CI logs; use short-lived tokens with `token_endpoint_auth_method: private_key_jwt` |
| Helm drift — Auth0 proxy Helm release values differ from Git-committed values.yaml | `helm get values auth-proxy -n auth -o yaml | diff - helm/auth-proxy/values.yaml` shows drift | `helm diff upgrade auth-proxy helm/auth-proxy/ -f helm/auth-proxy/values.yaml -n auth` | `helm rollback auth-proxy 0 -n auth`; commit current live values to Git: `helm get values auth-proxy -n auth -o yaml > helm/auth-proxy/values.yaml && git add && git commit` | Enable ArgoCD or Flux for auth-proxy Helm release; block manual `helm upgrade` via OPA Gatekeeper |
| ArgoCD sync stuck — Auth0 tenant config ArgoCD Application stuck in `OutOfSync` | ArgoCD UI shows `OutOfSync` with `SyncFailed`; auth0-deploy-cli diff shows changes not applied | `argocd app get auth0-tenant-config --output json | jq '{syncStatus:.status.sync.status, health:.status.health.status, message:.status.conditions[0].message}'` | `argocd app sync auth0-tenant-config --force --prune`; if webhook issue: `argocd app sync auth0-tenant-config --retry-limit 3` | Add ArgoCD sync webhook for Auth0 deploy pipeline; set `syncPolicy.automated.selfHeal: true`; add sync failure alert to Slack |
| PDB blocking — auth proxy PodDisruptionBudget preventing rolling update | `kubectl rollout status deployment/auth-proxy -n auth` hangs; pods stuck in `Pending` | `kubectl get pdb -n auth -o yaml | jq '.items[] | {name:.metadata.name, allowed:.status.disruptionsAllowed, current:.status.currentHealthy}'` | Temporarily relax PDB: `kubectl patch pdb auth-proxy-pdb -n auth -p '{"spec":{"minAvailable":1}}'`; complete rollout; restore PDB | Set PDB `maxUnavailable: 1` instead of `minAvailable` to allow rolling updates; ensure replica count > PDB minimum |
| Blue-green switch fail — Auth0 custom domain DNS switch to new auth proxy fails | After DNS CNAME update from blue to green, Auth0 custom domain validation fails; login page shows certificate error | `dig +short login.$DOMAIN CNAME`; `curl -vI "https://login.$DOMAIN" 2>&1 | grep 'SSL certificate\|subject'`; Auth0 Dashboard: Custom Domains status | Revert DNS CNAME to blue: `aws route53 change-resource-record-sets --hosted-zone-id $ZONE --change-batch '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"login.'$DOMAIN'","Type":"CNAME","TTL":60,"ResourceRecords":[{"Value":"blue-proxy.'$DOMAIN'"}]}}]}'` | Pre-validate SSL certificate on green proxy matches Auth0 custom domain; reduce DNS TTL to 60 s before cutover; test with `curl --resolve` before DNS switch |
| ConfigMap drift — Auth0 connection config in Kubernetes ConfigMap out of sync with Auth0 tenant | Auth proxy using stale OIDC discovery URL from ConfigMap; Auth0 tenant updated discovery endpoint | `kubectl get configmap auth0-config -n auth -o yaml | grep issuer`; compare with: `curl -s "https://$DOMAIN/.well-known/openid-configuration" | jq '.issuer'` | Update ConfigMap: `kubectl create configmap auth0-config -n auth --from-literal=issuer="https://$DOMAIN/" --dry-run=client -o yaml | kubectl apply -f -`; restart auth proxy | Sync ConfigMap from Auth0 tenant via CronJob: `curl Auth0 discovery endpoint > configmap.yaml && kubectl apply`; use External Secrets Operator |
| Feature flag stuck — Auth0 Actions deploy flag left in `draft` state; new login logic not active | Auth0 Action deployed but not activated; production still running old login flow; team believes new flow is live | `curl -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/actions/actions" | jq '[.actions[] | {name:.name, status:.status, deployed:.deployed}]'` | Deploy Action: `curl -X POST -H "Authorization: Bearer $MGMT_TOKEN" "https://$DOMAIN/api/v2/actions/actions/$ACTION_ID/deploy"`; verify: `curl ... | jq '.status'` shows `built` | Add post-deploy verification step in CI: assert Action status is `built` and bound to trigger; alert if any Action is in `draft` for > 1 hour after pipeline completes |

## Service Mesh & API Gateway Edge Cases
| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive — Envoy trips circuit breaker on Auth0 token endpoint during normal latency spike | Envoy returns `503 UO` (upstream overflow) to auth proxy; Auth0 token endpoint healthy but slow (P99 = 2 s) | Envoy outlier detection `consecutive5xx=5` too aggressive; Auth0 occasional 500 during maintenance counted | All token exchange requests fail; users cannot complete login; auth proxy logs `upstream_reset_before_response_started` | Increase outlier detection threshold: `kubectl edit destinationrule auth0-upstream` set `consecutiveErrors: 10` and `interval: 30s`; add `baseEjectionTime: 60s` |
| Rate limit false positive — API gateway rate limiter blocking legitimate Auth0 callback burst | API gateway returns `429 Too Many Requests` on `/callback` endpoint during peak login hour | Rate limit set to 100 req/s on `/callback`; shift change causes 200+ simultaneous logins | Legitimate users cannot complete Auth0 login flow; callback requests dropped; users see error page | Increase rate limit for `/callback`: update Envoy `local_rate_limit` to 500 req/s; or exempt `/callback` path from rate limiting; add `x-envoy-retry-on: 429` |
| Stale discovery — service mesh routing auth proxy traffic to terminated pod | Auth proxy requests to downstream services fail with `connection refused`; Envoy endpoint list stale | Kubernetes endpoint controller slow to remove terminated pod; Envoy EDS cache TTL too long | Intermittent 503 errors on post-login API calls; some users see errors, others work fine | `istioctl proxy-config endpoint $(kubectl get pod -n auth -l app=auth-proxy -o name) | grep downstream-svc`; force EDS refresh: `istioctl proxy-config endpoint --reset`; reduce EDS refresh interval |
| mTLS rotation — Istio cert rotation breaks Auth0 webhook callback | Auth0 Log Streaming webhook to internal service fails after Istio root CA rotation; Auth0 cannot verify mTLS | Istio rotated intermediate CA; Auth0 Log Stream webhook endpoint now presents new certificate chain; Auth0 webhook verification fails | Auth0 log events not delivered to SIEM; compliance gap; security team blind to login anomalies | Update Auth0 Log Stream webhook URL to use external-facing endpoint (not behind mesh); or add new Istio root CA to Auth0 webhook trusted CAs; `istioctl pc secret <pod> -o json | jq '.dynamicActiveSecrets[0].secret.tlsCertificate'` |
| Retry storm — auth proxy retrying failed Auth0 token requests amplifying load | Auth0 rate limit hit (429); auth proxy retries aggressively; each retry triggers more 429s; exponential load growth | Auth proxy HTTP client configured with immediate retry, no backoff; 3 retries per request = 3x amplification | Auth0 tenant rate-limited for all applications; login failures cascade across all apps sharing tenant | Add exponential backoff: configure auth proxy retry with `initialBackoff: 1s, maxBackoff: 30s, backoffMultiplier: 2`; add jitter; respect `Retry-After` header from Auth0 429 responses |
| gRPC metadata loss — Auth0 JWT claims lost in gRPC service-to-service propagation | Downstream gRPC service receives empty `authorization` metadata; Auth0 JWT present in initial HTTP request but lost at gRPC boundary | Envoy HTTP-to-gRPC transcoding strips custom headers; `authorization` not in `request_headers_to_add` | Downstream services cannot authorize requests; return `UNAUTHENTICATED`; feature degradation | Add header propagation in Envoy filter: `typed_per_filter_config` with `request_headers_to_add: [{header: {key: "authorization", value: "%REQ(authorization)%"}}]`; verify: `grpcurl -H "authorization: Bearer $TOKEN" $SVC:443 list` |
| Trace context gap — Auth0 Actions callback losing OpenTelemetry trace context | Distributed traces break at Auth0 boundary; Auth0 Action → callback webhook does not propagate `traceparent` header | Auth0 Actions runtime does not forward incoming `traceparent`; trace context lost across Auth0 pipeline | Cannot trace end-to-end login flow; debugging latency issues requires manual correlation across Auth0 logs and application traces | Inject `traceparent` in Auth0 Action `api.redirect.sendUserTo()` URL as query parameter; extract and re-inject in callback handler; correlate via Auth0 `log_id` ↔ application `trace_id` mapping |
| LB health check mismatch — ALB health check on auth proxy passes but Auth0 connectivity broken | ALB marks auth proxy healthy; auth proxy responds 200 on `/healthz`; but Auth0 token endpoint unreachable from auth proxy | Health check only tests auth proxy process liveness; does not verify Auth0 connectivity | Users reach auth proxy but login fails; auth proxy returns 502 on `/callback` when exchanging code for token | Add deep health check: auth proxy `/health/ready` must test Auth0 token endpoint: `GET https://$DOMAIN/.well-known/openid-configuration`; update ALB health check path: `aws elbv2 modify-target-group --health-check-path /health/ready` |
