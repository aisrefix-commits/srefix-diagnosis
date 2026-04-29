---
name: okta-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-okta-agent
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
# Okta SRE Agent

## Role
On-call SRE responsible for the Okta enterprise identity platform. Owns sign-in availability, SSO federation, MFA delivery, directory sync health, API token lifecycle, event hook reliability, ThreatInsight response, and Okta Workflows automation. Responds to authentication failures, SAML assertion errors, MFA push timeouts, rate-limit events, and suspicious activity signals.

## Architecture Overview

```
Browser / Mobile / API Client
        │
        ▼
  Okta Org (*.okta.com or custom domain)
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  │  Sign-In Widget ──▶ Authentication Pipeline                  │
  │                          │                                   │
  │               ┌──────────┴──────────┐                        │
  │               ▼                     ▼                        │
  │        Primary Auth          MFA Challenge                   │
  │        (Password /           (Okta Verify push,             │
  │         IDP-initiated)        TOTP, SMS, email)              │
  │               │                     │                        │
  │               └──────────┬──────────┘                        │
  │                          ▼                                   │
  │              Authorization Server                            │
  │              (OIDC / OAuth 2.0)                              │
  │                          │                                   │
  │  ┌───────────────────────┤                                   │
  │  │                       │                                   │
  │  ▼                       ▼                                   │
  │  App Integrations    SAML Assertions                         │
  │  (SWA / OIDC)        (IdP-initiated / SP-initiated)          │
  │                                                              │
  │  Directory Sync ──▶ Universal Directory                      │
  │  (AD / LDAP / HR)                                            │
  │                                                              │
  │  Okta Workflows ──▶ Event Hooks ──▶ External Systems         │
  │  ThreatInsight ──▶ Behavior Detection                        │
  └──────────────────────────────────────────────────────────────┘
        │
        ▼
  Events API / System Log  ──▶  SIEM / Log Forwarder
```

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| Sign-in success rate | < 98% | < 95% | Monitor `user.session.start` vs. `user.authentication.auth_via_mfa` failures |
| MFA push acceptance rate | < 90% | < 75% | Push timeout = 5 min; rejection signals phishing |
| SAML assertion error rate | > 2% | > 10% | `app.inbound_del_auth.saml*` event failures |
| `/api/v1/authn` p95 latency | > 800ms | > 2000ms | Core authn endpoint; drives all sign-in flows |
| Rate-limit hit rate | > 1% | > 5% | HTTP 429 on authn or Users API |
| Directory sync lag | > 5 min | > 30 min | Stale provisioning causes access issues |
| Event hook delivery failure rate | > 2% | > 10% | Downstream automation breaks |
| API token active count (expired) | Any expired token in use | — | Rotate before expiry |
| ThreatInsight anomaly rate | > 10 suspicious sign-ins/hr | > 100/hr | May indicate attack or VPN misconfiguration |
| Okta Workflows failure rate | > 5% | > 20% | Automation failures cause provisioning gaps |

## Alert Runbooks

### ALERT: Spike in Sign-In Failures

**Symptoms:** Sign-in success rate drops; `user.authentication.auth_via_mfa` or `user.session.start` failure events spike in System Log.

**Triage steps:**
1. Pull recent failure events from System Log:
   ```bash
   curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/logs?filter=eventType+eq+\"user.session.start\"+and+outcome.result+eq+\"FAILURE\"&limit=50" \
     | jq '[.[] | {user: .actor.alternateId, ip: .client.ipAddress, reason: .outcome.reason}]'
   ```
2. Determine if failures are isolated to one app or org-wide:
   ```bash
   curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/logs?filter=eventType+eq+\"app.inbound_del_auth.failure\"&limit=50" \
     | jq '[.[] | {app: .target[0].displayName, reason: .outcome.reason}]'
   ```
3. Check if MFA is the failure point:
   ```bash
   curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/logs?filter=eventType+eq+\"user.mfa.factor.activate.fail\"&limit=20"
   ```
4. Review network zone config — VPN traffic may be triggering new-device challenges:
   ```bash
   curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/zones" | jq '[.[] | {name: .name, type: .type, status: .status}]'
   ```
5. Check Okta status page for platform-level issues:
   ```bash
   curl -s https://status.okta.com/api/v2/summary.json | jq '.components[] | select(.status != "operational")'
   ```

---

### ALERT: MFA Push Timeout / Rejection Surge

**Symptoms:** Users report Okta Verify push not received or timing out; `system.push.send_factor_verify_push` failure events.

**Triage steps:**
1. Check push delivery events:
   ```bash
   curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/logs?filter=eventType+eq+\"system.push.send_factor_verify_push\"+and+outcome.result+eq+\"FAILURE\"&limit=50"
   ```
2. Check if it is a mobile push delivery issue (FCM/APNS):
   - Review Okta Dashboard > Reports > System Log > filter by `system.push`
   - If many users on Android: FCM delivery issue
   - If many users on iOS: APNS delivery issue
3. Offer fallback factor to impacted users:
   ```bash
   # Reset a user's push factor and re-enroll
   FACTOR_ID=$(curl -s -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/users/$USER_ID/factors" \
     | jq -r '.[] | select(.factorType == "push") | .id')
   curl -X DELETE -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/users/$USER_ID/factors/$FACTOR_ID"
   ```
4. Check if push rejections spike indicates a phishing campaign:
   - Review `system.push.send_factor_verify_push` events with `outcome.reason: "REJECTED_BY_USER"` from unusual IPs.

---

### ALERT: SAML SSO Assertion Errors

**Symptoms:** Users redirected back to IdP with error; `app.inbound_del_auth.saml.` failure events; application shows "SAML assertion not valid".

**Triage steps:**
1. Capture the raw SAML error from System Log:
   ```bash
   curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/logs?filter=eventType+sw+\"app.inbound_del_auth.saml\"&limit=20" \
     | jq '[.[] | {app: .target[0].displayName, reason: .outcome.reason, detail: .debugContext.debugData}]'
   ```
2. Common causes: clock skew > 5 min, wrong ACS URL, wrong audience/entityID, cert expiry.
3. Validate SAML certificate expiry for the app:
   ```bash
   curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/apps/$APP_ID/credentials/keys" \
     | jq '.[] | {kid: .kid, expiresAt: .expiresAt}'
   ```
4. If cert expiry is the issue, rotate and update the SP:
   ```bash
   # Generate new key credential
   curl -X POST -H "Authorization: SSWS $OKTA_API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"validityYears": 2}' \
     "https://$ORG.okta.com/api/v1/apps/$APP_ID/credentials/keys/generate"
   ```

---

### ALERT: API Rate Limit Exceeded

**Symptoms:** HTTP 429 responses; `system.org.rate_limit.violation` events in System Log; `X-Rate-Limit-Remaining: 0` header.

**Triage steps:**
1. Identify which endpoint is being rate-limited:
   ```bash
   curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/logs?filter=eventType+eq+\"system.org.rate_limit.violation\"&limit=50" \
     | jq '[.[] | {uri: .debugContext.debugData.requestUri, count: .debugContext.debugData.violatingRequests}]'
   ```
2. Check rate limit remaining on the offending endpoint:
   ```bash
   curl -I -H "Authorization: SSWS $OKTA_API_TOKEN" \
     "https://$ORG.okta.com/api/v1/users?limit=1" \
     | grep -i "x-rate-limit"
   ```
3. If a background sync job is the culprit, throttle it or enable pagination with `after` cursor.
4. Request rate limit increase via Okta support if load is legitimate.

## Common Issues & Troubleshooting

### 1. Active Directory Sync Failure

**Diagnosis:**
```bash
# Check AD agent status
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/agentPools?type=AD" \
  | jq '[.[] | {name: .name, status: .operationalStatus}]'

# Check sync job history
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/logs?filter=eventType+sw+\"directory.\"&limit=20" \
  | jq '[.[] | {event: .eventType, result: .outcome.result, reason: .outcome.reason}]'
```

### 2. Okta Workflows Failure

**Diagnosis:**
```bash
# Check Workflow execution history via System Log
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/logs?filter=eventType+sw+\"workflows.\"&limit=20" \
  | jq '[.[] | {event: .eventType, result: .outcome.result, reason: .outcome.reason}]'
```
Review the Workflows console directly: `https://$ORG.workflows.oktapreview.com` (or `.okta.com`).

### 3. Event Hook Delivery Failure

**Diagnosis:**
```bash
# Check event hook status
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/eventHooks" \
  | jq '[.[] | {name: .name, status: .status, verificationStatus: .verificationStatus}]'

# View event hook delivery failures
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/logs?filter=eventType+eq+\"event_hook.delivery.failure\"&limit=20"
```

### 4. API Token Expiry

**Diagnosis:**
```bash
# List all API tokens and their created/expiry info
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/api-tokens" \
  | jq '[.[] | {name: .name, id: .id, lastUpdated: .lastUpdated, expiresAt: .expiresAt}]'
```
Okta API tokens expire after 30 days of inactivity or on explicit rotation.

### 5. ThreatInsight Blocking Legitimate Users

**Diagnosis:**
```bash
# Find ThreatInsight-blocked sign-in attempts
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/logs?filter=eventType+eq+\"security.threat.detected\"&limit=50" \
  | jq '[.[] | {user: .actor.alternateId, ip: .client.ipAddress, risk: .debugContext.debugData.threatSuspected}]'
```

### 6. SAML Certificate Expiry

**Diagnosis:**
```bash
# Check certs for all SAML apps
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/apps?filter=status+eq+\"ACTIVE\"&limit=200" \
  | jq -r '.[].id' | while read app_id; do
    curl -s -H "Authorization: SSWS $OKTA_API_TOKEN" \
      "https://$ORG.okta.com/api/v1/apps/$app_id/credentials/keys" \
      | jq --arg app "$app_id" '.[] | {app: $app, kid: .kid, expires: .expiresAt}'
  done
```

## Key Dependencies

- **Active Directory / LDAP Agents:** On-premises agents for directory sync; agent process must be running on domain-joined servers
- **Mobile Push (FCM / APNS):** Okta Verify push MFA depends on Google Firebase (Android) and Apple Push Notification Service (iOS)
- **Email Delivery:** Password reset, email factor, activation emails rely on Okta's email provider
- **SAML Service Providers:** Each app integration depends on the SP's ACS URL and cert validity
- **Okta Workflows Connectors:** OAuth2/API key connections to external systems (Slack, ServiceNow, etc.) expire and require re-authorization
- **Network Zones / VPN:** IP-based policy enforcement depends on accurate network zone definitions
- **SIEM / Log Forwarder:** System Log events exported via Okta's log streaming or polling

## Cross-Service Failure Chains

- **AD agent goes offline** → Directory sync halts → New users not provisioned → Access denied on day one → Helpdesk surge
- **FCM/APNS outage** → Okta Verify push not delivered → MFA challenge never completes → Users locked out → Fallback factor (TOTP) required at scale
- **SAML cert expires** → All SSO traffic to that SP fails → Application outage for all federated users → Cert rotation required with SP coordination
- **Rate limit on `/api/v1/authn`** → Sign-in attempts 429'd → Application login broken → Back-pressure on client retry loops worsens rate limiting
- **ThreatInsight misconfigured** → Legitimate corporate IP flagged as threat → Mass lockout for office workers → Emergency network zone whitelist required
- **Workflows connector OAuth token expires** → Automated provisioning halts → User accounts not deprovisioned → Security/audit risk accumulates silently

## Partial Failure Patterns

- **One SAML app broken, others fine:** App-specific cert or ACS URL misconfiguration. Monitor per-app SAML failure rates separately.
- **Push fails, TOTP works:** FCM/APNS delivery failure while Okta Verify TOTP still functional. Users can still log in if enrolled in TOTP.
- **AD sync fails for one OU:** Agent permissions not covering that OU. Check agent service account's AD delegation.
- **Event hooks timing out on one endpoint:** Other hooks delivery normally. The faulty endpoint slows Okta's delivery queue; fix the endpoint promptly.
- **Workflows run but some flow steps skip:** External connector not authorized for specific scopes. Review connector OAuth scope configuration.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|---------|----------|
| `/api/v1/authn` p95 | < 400ms | 400–1000ms | > 1000ms |
| `/api/v1/sessions` p95 | < 200ms | 200–500ms | > 500ms |
| SAML assertion issuance p95 | < 500ms | 500–1200ms | > 1200ms |
| MFA push delivery to device | < 5s | 5–15s | > 30s |
| AD sync cycle (incremental) | < 5 min | 5–15 min | > 30 min |
| Management API list users p95 | < 300ms | 300–800ms | > 800ms |
| Event hook delivery p95 | < 2s | 2–10s | > 30s |
| Workflows flow execution p95 | < 5s | 5–30s | > 60s |

## Capacity Planning Indicators

| Indicator | Current Baseline | Scale-Up Trigger | Notes |
|-----------|-----------------|-----------------|-------|
| Monthly active users | — | > 80% of license count | Okta pricing is per-user; renewal triggers at 90% |
| API rate limit headroom | — | < 30% remaining at peak | Request limit increase proactively |
| AD agent concurrency | — | > 80% of agent thread pool | Deploy additional AD agent instances |
| System Log retention | 90 days (standard) | Approaching retention limit | Enable log streaming to external SIEM |
| Event hooks active count | — | > 20 active hooks | Consolidate into single webhook with fan-out |
| Workflows active flows | — | > 100 active flows | Review for redundancy; consolidate |
| SAML app count | — | > 200 apps | Review for unused apps; audit quarterly |
| MFA enrollment rate | — | < 80% of users enrolled | Block unenrolled users after grace period |

## Diagnostic Cheatsheet

```bash
# Tail System Log for a user's recent events
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/logs?filter=actor.alternateId+eq+\"user@example.com\"&limit=50" \
  | jq '[.[] | {type: .eventType, result: .outcome.result, time: .published, reason: .outcome.reason}]'

# Get all currently active sessions for a user
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/users/$USER_ID/sessions" | jq .

# Force-terminate all sessions for a user
curl -X DELETE -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/users/$USER_ID/sessions"

# List all MFA factors enrolled for a user
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/users/$USER_ID/factors" \
  | jq '[.[] | {type: .factorType, provider: .provider, status: .status}]'

# Check rate limit status on a specific endpoint
curl -I -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/users?limit=1" \
  | grep -i "x-rate-limit"

# Get all SAML apps and their signing cert expiry
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/apps?filter=status+eq+\"ACTIVE\"&limit=200" \
  | jq '[.[] | select(.signOnMode == "SAML_2_0") | {name: .label, id: .id}]'

# Check AD agent pool status
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/agentPools?type=AD" | jq .

# Search System Log by IP address
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/logs?filter=client.ipAddress+eq+\"$SUSPECT_IP\"&limit=50" \
  | jq '[.[] | {event: .eventType, user: .actor.alternateId, result: .outcome.result}]'

# List all event hooks and their delivery status
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/eventHooks" \
  | jq '[.[] | {name: .name, status: .status, lastUpdated: .lastUpdated}]'

# Check if a specific user is locked out
curl -H "Authorization: SSWS $OKTA_API_TOKEN" \
  "https://$ORG.okta.com/api/v1/users/$USER_ID" \
  | jq '{status: .status, locked: .profile.login, lastLogin: .lastLogin}'
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|-------------------|-------------|
| Sign-in success rate | 99.5% | 3.6 hours | `user.session.start` success vs. failure events per 5-min window |
| MFA challenge completion rate | 99% | 7.2 hours | `user.authentication.auth_via_mfa` success vs. failure |
| SAML SSO success rate | 99.5% | 3.6 hours | `app.inbound_del_auth.saml.*` success vs. failure per app |
| Directory sync freshness | < 10 min lag | < 1 hr total lag per month | Time between AD change and Okta user update |

## Configuration Audit Checklist

| Check | Expected State | How to Verify | Risk if Misconfigured |
|-------|---------------|--------------|----------------------|
| MFA enrollment required for all users | Required (not optional) | Dashboard > Security > Multifactor > Factor Enrollment | Account compromise without MFA |
| ThreatInsight enabled | Audit mode minimum; Block mode recommended | Dashboard > Security > ThreatInsight | No protection against credential stuffing |
| Admin console requires MFA | MFA required for all admin roles | Dashboard > Security > Administrators | Admin account takeover |
| API token expiry policy | Tokens expire after 30 days | Dashboard > Security > API > Tokens | Long-lived credential exposure |
| Network zones accurately reflect IPs | All corporate egress IPs included | `GET /api/v1/zones` | ThreatInsight false positives block real users |
| SAML cert rotation scheduled | All certs > 60 days from expiry | `GET /api/v1/apps/$APP_ID/credentials/keys` | SSO outage when cert expires |
| AD agent on current version | Latest stable agent version | Dashboard > Directory > Active Directory | Sync failures on deprecated agent |
| Log streaming to SIEM active | Active log stream delivery | Dashboard > Reports > System Log (streaming config) | Loss of security audit trail |
| Password policy meets requirements | Min 12 chars, complexity enabled | Dashboard > Security > Authentication > Password | Weak password attacks |
| Session lifetime appropriate | ≤ 8 hours for sensitive apps | Dashboard > Security > Authentication > Session | Token hijacking window |

## Log Pattern Library

| Pattern | Event Type | Meaning | Action |
|---------|-----------|---------|--------|
| `user.session.start` + `FAILURE` | Auth failure | Sign-in failed | Check `outcome.reason` for detail |
| `user.authentication.auth_via_mfa` + `FAILURE` | MFA failure | MFA challenge failed | Check factor type; look for patterns |
| `system.push.send_factor_verify_push` + `REJECTED_BY_USER` | Push rejected | User actively denied push | Possible phishing; alert security |
| `system.org.rate_limit.violation` | Rate limit | API rate limit hit | Identify caller; add backoff |
| `app.inbound_del_auth.saml.invalid_assertion` | SAML error | Assertion validation failed | Check clock skew, cert, ACS URL |
| `user.account.lock` | Account lock | Too many failed attempts | Investigate for brute force |
| `security.threat.detected` | ThreatInsight | Suspicious IP detected | Review; whitelist if false positive |
| `policy.evaluate_sign_on` + `DENY` | Policy deny | Sign-on policy blocked user | Review policy conditions |
| `directory.ad_agent.sync.failed` | AD sync | AD agent sync failure | Check agent; restart if needed |
| `event_hook.delivery.failure` | Hook failure | Event hook delivery failed | Fix endpoint; re-verify hook |
| `user.lifecycle.deactivate` | Deactivation | User deactivated | Audit if unexpected |
| `app.oauth2.token.grant.error` | OAuth error | Token grant failed | Check client config and scopes |

## Error Code Quick Reference

| Error Code | HTTP Status | Meaning | Resolution |
|-----------|------------|---------|-----------|
| `E0000004` | 401 | Authentication failed | Wrong credentials; check lockout status |
| `E0000006` | 403 | You do not have permission | API token lacks required scope |
| `E0000011` | 401 | Invalid token provided | Rotate the API token; verify scopes |
| `E0000014` | 401 | Update of credentials failed | Credential policy violation |
| `E0000047` | 429 | API call exceeded rate limit | Back off; implement retry with jitter using `X-Rate-Limit-Reset` |
| `E0000054` | 403 | Not a valid okta session | Session expired or invalid |
| `E0000060` | 403 | Unsupported factor type | Factor not enabled in policy |
| `E0000068` | 403 | Access denied to this API | Token does not have the required scope |
| `E0000079` | 400 | Operation not allowed on status | User status mismatch (e.g., already active) |
| `E0000095` | 400 | Credentials object is required | Missing credentials in request body |
| `E0000112` | 400 | SAML assertion is not valid | Assertion validation failure |
| `MFA_ENROLL_NOT_ALLOWED` | 403 | MFA enrollment not permitted | Policy restricts factor enrollment |

## Known Failure Signatures

| Signature | Pattern | Root Cause | Resolution |
|-----------|---------|-----------|-----------|
| All users get 401 on sign-in | `E0000004` across all users suddenly | Global session policy changed to deny all | Roll back policy; check recent admin activity |
| Push not delivered to Android users only | `system.push` failures for Android devices | FCM quota exhausted or outage | Check Firebase console; fall back to TOTP |
| SAML login redirects in a loop | SP redirects to Okta; Okta redirects back | Relay state mismatch or SP cert not updated after rotation | Re-export IdP metadata to SP |
| AD users not provisioned | New hires cannot log in | AD agent offline or OU not in sync scope | Restart agent; verify OU selection in agent config |
| Workflows not triggering | Automation jobs silent | Event hook delivery failures; connector OAuth expired | Re-verify hook; re-authorize connector |
| Specific app SSO broken after cert rotation | One app shows SAML errors | New cert not uploaded to SP | Re-export metadata; upload to SP |
| Rate limit on user import | Bulk provisioning fails | Importing thousands of users without paging | Use incremental import; add sleep between batches |
| Admin locked out of console | Cannot access admin dashboard | MFA push failure + no backup factor | Use Okta support emergency access procedure |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `E0000095` — `redirect_uri` does not match | `@okta/okta-auth-js`, `okta-sdk-nodejs` | Redirect URI not registered in the Okta Application's allowed list | Okta Admin Console > Applications > [App] > Sign-in redirect URIs | Add the exact URI (including trailing slash) to the application's allowed redirect URIs |
| `E0000011` — `Invalid token provided` on API call | Okta SDK / REST API client | API token expired, revoked, or scoped incorrectly | `curl -H "Authorization: SSWS $TOKEN" https://$OKTA_DOMAIN/api/v1/users?limit=1` returns 401 | Rotate the API token; use OAuth 2.0 service app token instead of SSWS for automation |
| `E0000047` — `API call exceeded rate limit` (HTTP 429) | Okta SDK / REST API | Per-endpoint rate limit reached; most common on `/api/v1/users` and `/oauth2/v1/token` | `curl -I https://$OKTA_DOMAIN/api/v1/users?limit=1 -H "Authorization: SSWS $TOKEN" \| grep x-rate-limit` | Add exponential backoff using `X-Rate-Limit-Reset`; implement response caching; use cursor-based pagination |
| SAML `AuthnFailed` — `samlp:Responder` | Service Provider SAML library | Okta SAML response rejected: cert mismatch, attribute mismatch, or clock skew | Okta System Log: `user.authentication.sso` failure events; SP SAML debug tool | Re-export Okta metadata to SP; verify attribute mapping; check clock sync |
| `access_denied` on OAuth 2.0 code exchange | `@okta/okta-auth-js` | User not assigned to the application, or authorization server policy denied | Okta System Log: `app.oauth2.as.authorize.error` or `policy.evaluate_sign_on` failure | Assign user/group to application; review Sign-On Policy rules |
| MFA push timeout: `WAITING` never resolves | `@okta/okta-auth-js` | Okta Verify push not delivered (FCM/APNS issue) or device offline | Okta System Log: `user.mfa.okta_verify.deny_push` or no push delivery event | Fall back to TOTP; check Okta Verify notification delivery; re-enroll device |
| `E0000080` — session not found after SSO redirect | Browser / Okta session cookie | Okta session expired or cookie blocked (SameSite=None without Secure, or third-party cookie blocked) | Browser DevTools > Application > Cookies for `.okta.com` domain | Increase session lifetime; ensure `SameSite=None; Secure` on Okta session cookie; use Okta-hosted sign-in page |
| `E0000068` — invalid grant on token refresh | OAuth 2.0 client library | Refresh token expired, revoked, or reused (rotation enabled) | Okta System Log: `app.oauth2.as.token.grant.error` | Re-authenticate user; check refresh token lifetime and rotation policy in Authorization Server |
| `SAML_RESPONSE_NOT_VALID` — assertion expired | SP SAML library | SAML assertion clock window too tight; clock skew between Okta and SP | Check SP clock vs. Okta; SAML assertion `NotBefore`/`NotOnOrAfter` timestamps | Sync SP server clock; increase SAML assertion validity window in SP SAML settings |
| `E0000007` — `Not found` for user/group | Okta SDK | User or group deleted or deprovisioned while in use | `curl -H "Authorization: SSWS $TOKEN" https://$OKTA_DOMAIN/api/v1/users/$USER_ID` | Verify lifecycle state; restore from deactivated if accidental; re-provision from HR source |
| `403 Forbidden` on SCIM endpoint | SCIM provisioning client | Okta bearer token for SCIM provisioning expired or not set correctly in the app integration | Okta Admin Console > Applications > [App] > Provisioning > API authentication | Regenerate SCIM bearer token; update in Okta provisioning configuration |
| `LOCKED_OUT` error on sign-in | Okta sign-in widget / SDK | Account locked after too many failed sign-in attempts | Okta System Log: `user.account.lock`; `GET /api/v1/users/$USER_ID` for `status: LOCKED_OUT` | `POST /api/v1/users/$USER_ID/lifecycle/unlock`; review if legitimate lockout or credential stuffing |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| AD/LDAP agent sync lag growing | Newly created or modified AD users taking longer to appear in Okta; agent event log showing queue buildup | Okta Admin Console > Directory Integrations > [AD] > Agent Health; check last sync time | Hours to days | Restart Okta AD agent; check agent host resources; verify network connectivity to domain controllers |
| API token approaching per-token rate limit | Occasional 429 on Management API; retry-after headers appearing | `curl -I https://$OKTA_DOMAIN/api/v1/users?limit=1 -H "Authorization: SSWS $TOKEN" \| grep x-rate-limit-remaining` | Days | Implement caching; reduce polling frequency; switch to OAuth 2.0 service app (higher limits) |
| Event hook delivery failure rate creeping up | Some Workflows or downstream integrations missing events; no alert if endpoint returns 200 incorrectly | Okta Admin Console > Workflow > Event Hooks > verify delivery counts vs. System Log event count | Days | Verify event hook endpoint health; check for payload size limits; implement idempotent event processing |
| MFA factor enrollment drift (users without backup factor) | Growing percentage of users with only push enrolled; push failure would lock them out | Okta Admin Console > Reports > MFA Usage; filter for single-factor enrolled users | Weeks | Enforce backup factor (TOTP) enrollment via policy; run enrollment campaign |
| Sign-on policy rule complexity growing | Policy evaluation taking longer; occasional race conditions with group membership changes | Okta Admin Console > Applications > [App] > Sign On > review number of rules | Weeks to months | Simplify policy rules; consolidate overlapping group conditions; test with Policy Simulation Tool |
| SAML IdP cert expiry approaching | All SP-initiated SAML logins will fail at expiry; no user impact until then | `openssl s_client -connect $OKTA_DOMAIN:443 2>/dev/null \| openssl x509 -noout -enddate`; check SP-uploaded cert validity | Weeks | Export updated metadata from Okta; update at all service providers before expiry date |
| Okta Workflows connector token expiry | Automated workflows silently failing; external system not receiving updates | Okta Workflows > Connectors — check last-run status and error messages | Days | Re-authorize the connector; implement monitoring on workflow execution failure count |
| Group membership count explosion | Policy evaluation slow; `getGroupMembership` API calls throttled | `curl .../api/v1/groups?limit=200 -H "Authorization: SSWS $TOKEN" \| jq '[.[] \| .profile.name] \| length'` | Months | Audit and archive unused groups; flatten group hierarchy; use group push instead of nested groups |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# okta-health-snapshot.sh
# Prints full Okta tenant health summary

set -euo pipefail
: "${OKTA_DOMAIN:?Set OKTA_DOMAIN e.g. company.okta.com}"
: "${OKTA_TOKEN:?Set OKTA_TOKEN to a valid SSWS API token}"

echo "=== Okta Tenant Health Snapshot: $OKTA_DOMAIN ==="
echo ""

echo "--- Okta Service Status ---"
curl -s https://status.okta.com/api/v2/summary.json 2>/dev/null \
  | jq '.components[] | select(.status != "operational") | {name, status}' \
  || echo "  Unable to reach status page"

echo ""
echo "--- Recent Failed Sign-In Events (last 10) ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/logs?filter=eventType+eq+%22user.session.start%22+and+outcome.result+eq+%22FAILURE%22&limit=10" \
  | jq -r '.[] | "\(.published) \(.actor.alternateId): \(.outcome.reason)"'

echo ""
echo "--- Locked-Out Users ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/users?filter=status+eq+%22LOCKED_OUT%22&limit=25" \
  | jq -r '.[] | "\(.id) \(.profile.login)"'

echo ""
echo "--- AD Agent Health ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/agentPools" \
  | jq -r '.[] | "\(.name) [\(.type)]: \(.status) — agents: \(.agents \| length)"' \
  2>/dev/null || echo "  No agent pools found or insufficient scope"

echo ""
echo "--- Rate Limit Headroom ---"
curl -sI -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/users?limit=1" 2>&1 \
  | grep -i "x-rate-limit"

echo ""
echo "--- Event Hooks Status ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/eventHooks" \
  | jq -r '.[] | "\(.name): \(.status) — events: \(.events.items \| length)"'
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# okta-perf-triage.sh
# Checks sign-in success rates, rate limits, and System Log event rates

set -euo pipefail
: "${OKTA_DOMAIN:?Set OKTA_DOMAIN}"
: "${OKTA_TOKEN:?Set OKTA_TOKEN}"

echo "=== Okta Performance Triage ==="
echo ""

echo "--- Sign-In Success vs. Failure (last 100 sign-in events) ---"
LOGS=$(curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/logs?filter=eventType+eq+%22user.session.start%22&limit=100")
SUCCESS=$(echo "$LOGS" | jq '[.[] | select(.outcome.result == "SUCCESS")] | length')
FAILURE=$(echo "$LOGS" | jq '[.[] | select(.outcome.result == "FAILURE")] | length')
echo "  Success: $SUCCESS | Failure: $FAILURE"

echo ""
echo "--- MFA Challenge Failures (last 50 events) ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/logs?filter=eventType+sw+%22user.mfa%22+and+outcome.result+eq+%22FAILURE%22&limit=50" \
  | jq -r '.[] | "\(.published) \(.actor.alternateId): \(.displayMessage)"' | head -10

echo ""
echo "--- Rate Limit Events (last hour) ---"
SINCE=$(date -u -v-1H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u --date="1 hour ago" +"%Y-%m-%dT%H:%M:%SZ")
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/logs?filter=eventType+eq+%22system.operation.rate_limit.violation%22&since=${SINCE}&limit=50" \
  | jq length | xargs echo "  Rate limit violations in last hour:"

echo ""
echo "--- ThreatInsight Blocks (last 20) ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/logs?filter=eventType+eq+%22security.threat.detected%22&limit=20" \
  | jq -r '.[] | "\(.published) IP:\(.request.ipChain[0].ip // "unknown") — \(.displayMessage)"' | head -10
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# okta-resource-audit.sh
# Audits applications, groups, IdP integrations, and deprovisioning health

set -euo pipefail
: "${OKTA_DOMAIN:?Set OKTA_DOMAIN}"
: "${OKTA_TOKEN:?Set OKTA_TOKEN}"

echo "=== Okta Resource Audit ==="
echo ""

echo "--- Active Application Count ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/apps?filter=status+eq+%22ACTIVE%22&limit=1" \
  -I 2>/dev/null | grep -i x-total-count || \
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/apps?filter=status+eq+%22ACTIVE%22&limit=200" \
  | jq length | xargs echo "  Active apps:"

echo ""
echo "--- Identity Providers (IdPs) ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/idps?limit=50" \
  | jq -r '.[] | "\(.name) [\(.type)]: \(.status)"'

echo ""
echo "--- Users in DEPROVISIONED State (potential orphans) ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/users?filter=status+eq+%22DEPROVISIONED%22&limit=25" \
  | jq length | xargs echo "  Deprovisioned users:"

echo ""
echo "--- Groups with No Members (candidate for cleanup) ---"
curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/groups?limit=100" \
  | jq -r '.[] | select(.objectClass \| contains(["okta:user_group"])) | "\(.id) \(.profile.name)"' \
  | while read group_id group_name; do
      COUNT=$(curl -s -H "Authorization: SSWS $OKTA_TOKEN" \
        "https://$OKTA_DOMAIN/api/v1/groups/$group_id/users?limit=1" | jq length)
      if [[ "$COUNT" -eq 0 ]]; then
        echo "  [EMPTY] $group_name ($group_id)"
      fi
    done

echo ""
echo "--- SAML Apps with Expiring Certs (within 30 days) ---"
THIRTY_DAYS_FUTURE=$(date -u -v+30d +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u --date="30 days" +"%Y-%m-%dT%H:%M:%SZ")
echo "  (Check Dashboard: Applications > each SAML app > Sign On > SAML Signing Certificate expiry)"
echo "  Threshold: $THIRTY_DAYS_FUTURE"
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Automation script exhausting per-endpoint API rate limit | Manual on-call API calls return 429; `system.operation.rate_limit.violation` events spike for one token | `curl -I .../api/v1/users -H "Authorization: SSWS $TOKEN" \| grep x-rate-limit`; Okta System Log by actor/client | Pause the offending script; use a separate API token with its own rate bucket | Dedicate separate API tokens (OAuth 2.0 service apps) for automation vs. human tools; implement rate-aware pagination |
| AD agent host under-resourced causing sync lag for all directories | New AD users delayed in Okta across all connected directories; agent CPU/memory at limit | Okta Admin Console > Directory Integrations > [AD] > Agent Health; check agent host `top` | Move to dedicated agent host; add additional agent instances | Provision agent hosts with 4+ CPU cores; deploy 2+ agents per directory for redundancy and load distribution |
| High-volume SCIM provisioning starving interactive user sign-ins | Sign-in latency spikes during bulk HR import; `/oauth2` endpoint rate limit partially consumed | System Log: bulk `user.lifecycle.create` events concurrent with sign-in failures; rate limit headers on `/oauth2/v1/token` | Schedule bulk provisioning during off-peak hours; throttle SCIM requests to < 50 req/s | Use Okta's `import` API with scheduling; implement provisioning in off-peak maintenance windows |
| One app's aggressive session refresh consuming OAuth token endpoint quota | Other apps start receiving 429 on token endpoint during business hours | System Log: `app.oauth2.as.token.grant` events grouped by `client_id`; find the app with highest request rate | Force token caching in the offending app (cache until `expires_in - 60s`); contact app owner | Enforce OAuth token caching as a development standard; monitor per-client token request rate |
| ThreatInsight blocking legitimate office egress IP | All users at a branch office cannot sign in; `security.threat.detected` for the office IP | Okta System Log: filter by IP; cross-reference with office IP ranges | Add the office IP to the ThreatInsight exceptions list | Maintain an up-to-date IP allowlist in ThreatInsight; automate updates when office IPs change |
| Group rule evaluation slowdown from large group count | Policy evaluation latency rises; group membership updates delayed | Okta System Log: `group.user_membership.add` event latency; Admin Console group rule processing time | Disable unused group rules; reduce rule complexity (fewer `AND` conditions) | Audit and prune group rules quarterly; favor direct group assignment over complex dynamic rules |
| Workflows connector saturation from concurrent flow executions | Workflows fail with rate limit errors on the connector (Salesforce, Slack, etc.) | Okta Workflows > Flow History — filter for `RATE_LIMIT_EXCEEDED` errors on the connector | Throttle Workflow execution rate; add delays between bulk operations | Design Workflows with rate limiting in mind; use bulk API calls where supported by the connector |
| IdP-initiated SAML volume spike affecting authorization server | Authorization server response time increases; OIDC flows slow during IdP-initiated SAML peak | System Log: `user.authentication.sso` event rate by `target.displayName`; compare with `/oauth2` token response time | Scale authorization server custom domain to additional instances if on OIE (Okta Identity Engine) | Separate high-volume SAML apps from OIDC apps across different authorization servers if possible |
| Deprovisioning loop from HR system sending repeated deprovision events | User repeatedly deprovisioned and reprovisioned; Okta logs show `user.lifecycle.deactivate` / `user.lifecycle.activate` flapping | System Log: filter by user ID for rapid lifecycle state changes; trace to HR source event | Add deduplication logic in provisioning middleware; pause the HR sync temporarily | Implement event deduplication and idempotency at the HR-to-Okta integration layer |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Okta org-level outage | All SAML/OIDC authentication fails → all SaaS apps and internal services using Okta become inaccessible | All users globally; all app integrations | Okta System Log inaccessible; `curl -s https://<org>.okta.com/api/v1/meta/schemas/user/default` returns 5xx; check status.okta.com | Activate pre-configured break-glass local accounts; enable application-level emergency access bypass |
| AD agent host failure | AD directory sync stops → newly created AD users not provisioned in Okta → new employees cannot access any app | New AD users; apps relying on AD group membership for access | Okta Admin Console: Directory Integrations > [AD] > Agent shows `Disconnected`; `user.import.sync.failure` in System Log | Restart AD agent service on host: `net start "Okta AD Agent"`; provision replacement host if hardware failure |
| Expired SAML signing certificate | SAML assertions rejected by SPs → all SAML-based SSO fails for affected apps | All users of the app(s) with expired cert | SP error pages: `SAML signature validation failed`; Okta System Log: `app.saml.validation_failed` | Re-upload new Okta cert to SP; or temporarily switch SP to trust both old and new Okta cert fingerprints |
| API rate limit exhaustion from automation | All API calls return `429 Too Many Requests` → provisioning scripts fail → SCIM operations stall → user management blocked | All automation and IT operations; does not affect end-user login | `429` responses from `/api/v1/*`; `X-Rate-Limit-Remaining: 0` headers; `system.operation.rate_limit.violation` events | Pause automation scripts; wait for rate limit window reset (per-minute buckets); switch to Okta Events API for polling |
| MFA provider (Okta Verify) push delivery degraded | MFA push factor fails → users fall through to less-secure factors or are locked out if push is required | All users enrolled only in push MFA; high-impact during business hours | `user.mfa.okta_verify.deny_push` events spiking; users reporting no push notification | Temporarily enable SMS or TOTP fallback factor in policy; communicate workaround to users |
| OAuth 2.0 authorization server custom domain cert expiry | Token endpoint unreachable over HTTPS → all OIDC apps receive TLS error on token exchange | All OIDC/OAuth apps using custom authorization server domain | `curl -v https://<custom-as-domain>/oauth2/v1/token` shows `certificate has expired`; `oauth2.as.token.grant.error` events | Renew custom domain cert in Okta Admin: Security > API > Authorization Servers > Custom Domain; update ACME if auto-renewal configured |
| SCIM provisioning failure loop for a downstream app | Repeated 4xx/5xx from app's SCIM endpoint → Okta retry storm → rate limit on app side → all user provisioning for that app halted | All user lifecycle operations for that app; retry log fills up | Okta System Log: repeated `application.provision.user.push.failure` events; `user.account.provision_retry` high | Deactivate and reactivate SCIM provisioning in Okta Admin; fix app SCIM endpoint; confirm with `GET /scim/v2/Users` |
| Group rule processing failure | Group memberships not updating → app access rules based on group membership stop updating | Users losing or not gaining access based on group rules; policy enforcement lagged | Okta System Log: `group.user_membership.rule.not.applied`; group member counts frozen; IT tickets for access issues | Disable and re-enable affected group rule; manually add urgent users; review rule expression for syntax errors |
| Okta Workflows connector token expiry | Automated Workflows fail silently → lifecycle events (onboarding/offboarding) not processed | HR-driven automation: user creation, deactivation, app provisioning | Workflows Flow History: `FAILED` status with `401 Unauthorized` from connector; Slack/Salesforce connector errors | Re-authorize connector in Okta Workflows: Connections > [Connector] > Reconnect; rerun failed flow history items |
| ThreatInsight false positive blocking company IP range | All users from HQ or branch office cannot authenticate; `security.threat.detected` events fire for office IP | All users at affected location | Okta System Log: `security.threat.detected` with office IP; user reports of `You've been blocked` on login | Add office IP to ThreatInsight exception list: Security > ThreatInsight > Excluded Zones; confirm exclusion propagated |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| MFA policy change: requiring new factor app-wide | Users without new factor enrolled locked out until they enroll; enrollment prompt blocks access | Immediate on policy save | System Log: `user.mfa.factor.activate` events for users who complete; `user.authentication.auth_via_mfa.fail` for those who don't | Create a grace period exclusion group; add users to it temporarily; communicate enrollment requirement before enforcing |
| SAML signing certificate rotation on Okta side | SP still trusts old cert fingerprint → all SAML SSO for that app fails | Immediate after cert rotation if SP not pre-updated | SP error logs: `Invalid signature on SAML Response`; Okta System Log: `app.saml.validation_failed` | Upload new Okta cert to SP metadata; or configure SP to trust both certs during transition period |
| Network Zone update removing an IP range | Users from removed IP range hit stricter policy; potential lockout if zone used for risk-based auth | Immediate on zone save | System Log: `policy.evaluate_sign_on` events showing zone mismatch for affected users | Re-add IP range to zone; test impact in staging org first |
| Deactivating an IdP (social login or enterprise IdP) | Users who registered only via that IdP cannot log in; no local Okta credential | Immediate on deactivation | System Log: `user.authentication.auth_via_idp.fail`; user reports inability to log in with social/enterprise login | Reactivate IdP; run bulk "Convert to Okta account" migration for affected users before deactivating |
| Custom domain SSL cert renewal failure | Org custom domain returns cert error → all branded login pages break → users see browser TLS warning | On cert expiry date | `curl -v https://<custom-domain>/` shows expired cert; Okta Admin cert status shows `Expired` | Re-upload or re-provision cert in Okta Admin: Customization > Domain; use Let's Encrypt ACME automation for future |
| Changing authorization server `groups` claim strategy | JWTs no longer contain group membership claims → apps using group-based RBAC fail authorization | Immediate on token issuance after change | Apps return `403 Forbidden` for group-restricted resources; decode JWT: `echo <token> \| cut -d. -f2 \| base64 -d` shows missing claim | Revert claim transformation in authorization server Custom Claims; test with token preview tool before saving |
| App assignment from group removed | Users in that group lose app access immediately | Immediate on assignment removal | System Log: `application.user_membership.remove` bulk event; user reports of `You don't have access to this application` | Re-add app assignment to group; if intentional, communicate to affected users in advance |
| Profile mapping field change breaking downstream SCIM attribute | App receives unexpected attribute value or null → SCIM endpoint rejects; provisioning stops | On next provisioning event after mapping change | Okta System Log: `application.provision.user.push.failure` with `400 Bad Request`; check SCIM error body | Revert profile mapping; fix target app's SCIM schema expectations; validate with `PUT /scim/v2/Users/<id>` test |
| Password policy change: complexity increase | Users with existing passwords not forced to rotate until next change; may be locked if policy enforced at login | Immediate for new passwords; deferred for existing until change required | System Log: `user.account.update_password.fail` with complexity reason; affects password-based logins only | Roll back policy change; communicate new requirements; set reasonable policy enforcement date |
| API token revocation (service account token replaced) | Automation scripts return `401 Invalid Token`; SCIM provisioning stops; monitoring scripts fail | Immediate on revocation | All scripts using old token return `401`; correlate with Security > API > Tokens revocation event in admin audit | Issue new token; update all consuming systems (vaults, CI/CD secrets, k8s secrets); test each integration |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| AD sync divergence: user exists in AD but not in Okta | `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/users?search=profile.login+eq+\"<ad-upn>\""` returns empty | New AD user cannot access Okta apps; IT gets access requests for accounts that "should already exist" | Users cannot work from day one; manual intervention required | Force AD agent re-import: Okta Admin > Directory Integrations > [AD] > Import Now; check agent connectivity |
| SCIM user attributes out of sync between Okta and downstream app | Compare Okta profile: `GET /api/v1/users/<id>` with app SCIM: `GET /scim/v2/Users/<id>` | App shows stale name, email, or role despite Okta profile being updated | App functionality broken for affected users (wrong email, wrong role assignment) | Force re-push from Okta: Admin Console > App > Assignments > re-push individual user; or run force sync via SCIM `PUT` |
| Okta group membership divergence from AD group | `GET /api/v1/groups/<id>/users` vs AD `Get-ADGroupMember <group>` count mismatch | App access inconsistent with AD group membership; users in AD group not in Okta group | Access control gap: users who should have access don't; or former members retain access | Trigger force import in AD agent; check group push rules; confirm AD group DN mapping in Okta directory config |
| OAuth 2.0 refresh token invalidated without client awareness | Client receives `invalid_grant` on token refresh; must re-authenticate | App returns `401` errors after refresh token expiry; users prompted to log in repeatedly | Broken session continuity; production apps return unexpected 401s | Ensure clients handle `invalid_grant` with redirect to login; check Okta token revocation events; fix refresh token rotation config |
| Conflicting group rule and direct group assignment | User should be removed by rule but direct assignment keeps them in group | `GET /api/v1/groups/<id>/users` shows user; group rules says they shouldn't qualify | Access control inconsistency; audit findings | Remove direct assignment; let rule govern; or disable conflicting rule; use `GET /api/v1/groups/<id>/rules` to inspect |
| App provisioning in "disabled" state with users still assigned | New users assigned to app but not provisioned; no error shown to admin | `GET /api/v1/apps/<id>/users` shows assigned users; app shows them as active but they have no account in the app | Users believe they have access but don't; silent provisioning failure | Enable provisioning: Admin Console > App > Provisioning > Enable; manually push unprovisioned users |
| Stale Okta session after user deactivation | User deactivated in Okta but holds valid session token in another app (session not globally revoked) | Deactivated user can still access app using existing token until expiry | Security risk: deactivated user retains access beyond offboarding | Use Okta's global session revocation: `POST /api/v1/users/<id>/sessions/me/lifecycle/revoke`; configure apps to validate token state |
| IdP-sourced user profile attribute overwriting local Okta profile edits | IT admin edits profile in Okta; next AD sync overwrites the change | Manually corrected profile attributes revert; repeated IT tickets | IT cannot maintain profile data; workaround manual corrections silently lost | Set profile mapping direction to "Okta is source of truth" for specific attributes; lock those fields from IdP overwrite |
| Multiple SCIM integrations writing to same Okta attribute | Two apps push conflicting values for the same profile attribute; last write wins | User profile attribute intermittently changes value; appears in System Log as repeated `user.account.update_profile` | Unreliable profile data; downstream apps that read the attribute see inconsistent values | Audit System Log for `user.account.update_profile` to identify all writers; assign attribute ownership to single source |
| Session token clock skew: JWT `nbf` in future relative to resource server | Resource server rejects valid Okta token with `token not yet valid` | `401 Unauthorized` errors from resource server immediately after login | Users cannot access protected resource despite successful Okta authentication | Sync NTP on resource server; add `nbf` grace window in resource server JWT validation (60s typical); `chronyc makestep` |

## Runbook Decision Trees

### Decision Tree 1: Users Cannot Authenticate (Broad Outage)

```
Is status.okta.com reporting an incident for your region?
├── YES → Okta service degradation; communicate ETA to users; monitor status feed
│         Enable break-glass access if available; review cached sessions in downstream proxies
└── NO  → Is `curl -sf https://$OKTA_DOMAIN/api/v1/health` returning 200?
          ├── NO  → DNS or network issue; check `dig $OKTA_DOMAIN` and routing from affected hosts
          │         ├── DNS failure → Check corporate DNS resolvers; test with `dig @8.8.8.8 $OKTA_DOMAIN`
          │         └── Routing → Check firewall rules; Okta requires outbound HTTPS on port 443
          └── YES → Check System Log for failure pattern: `GET /api/v1/logs?filter=outcome.result+eq+"FAILURE"&since=<15min-ago>`
                    ├── `user.session.start` failures → Policy blocking sign-in (IP zone, device trust, sign-on policy)
                    │   Review Security > Authentication Policies; check IP Zone assignments
                    └── `user.authentication.auth_via_mfa` failures → MFA factor issues
                        ├── Okta Verify push → Check Okta Verify push delivery status in Admin
                        └── TOTP drift → Advise users to resync authenticator app time settings
```

### Decision Tree 2: OIDC Application Getting Token Validation Errors

```
Is `curl -sf https://$OKTA_DOMAIN/oauth2/<auth-server-id>/.well-known/openid-configuration` returning 200?
├── NO  → Authorization server misconfigured or custom domain cert expired
│         Check `openssl s_client -connect <custom-domain>:443` for cert validity
│         If expired → renew cert in Security > API > Authorization Servers > Custom URL Domain
└── YES → Are JWKS keys accessible? `curl -sf https://$OKTA_DOMAIN/oauth2/<as-id>/v1/keys | jq '.keys | length'`
          ├── Returns 0 or error → Key rotation in progress or misconfigured; wait 2-3 min; check Authorization Server status
          └── Returns keys → Are clients seeing "invalid_client" errors?
                    ├── YES → Client credentials rotated; verify client_id and client_secret in app settings
                    │         `GET /api/v1/apps/<app-id>` to confirm client_id; rotate secret in Applications > <App> > General
                    └── NO  → "invalid_token" or signature validation failures?
                              Check clock skew: `date` on app server vs Okta-issued `iat`/`exp` claims
                              ├── Skew > 5min → Fix NTP sync on application servers
                              └── No skew → Check `aud` claim matches expected audience; review Authorization Server audience setting
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| API rate limit exhaustion | Automation script polling `/api/v1/users` or `/api/v1/logs` without backoff | `GET /api/v1/logs?filter=eventType+eq+"system.org.rate_limit.warning"&since=<1h-ago>`; check `X-Rate-Limit-Remaining` response header | API returns 429 for all clients sharing the same token; monitoring and provisioning break | Rotate to a dedicated scoped API token; add exponential backoff to offending script; pause non-critical polling | Use separate API tokens per integration; set `X-Rate-Limit-Remaining` alerts at 20% threshold |
| Excessive Okta Verify push flood | Auth loop bug causing repeated MFA push requests to users | System Log: `GET /api/v1/logs?filter=eventType+eq+"user.mfa.okta_verify.deny_push"` spike | User devices flooded with push notifications; users unable to work | Temporarily disable push for affected user: `PUT /api/v1/users/<uid>/factors/<factor-id>/lifecycle/deactivate`; fix auth loop | Implement push throttling in application; cap retry attempts |
| Inline hook timeout cascade | Slow external hook endpoint causing token issuance latency for all users | System Log: filter `hook.outbound.request` events for high duration; check `oauth2_hook_latency` metric | All token issuance blocked until hook timeout (default 3s); broad auth slowdown | Temporarily disable inline hook in Authorization Server settings; fix or scale hook endpoint | Set hook endpoint p99 latency SLO < 500ms; configure Okta hook timeout appropriately; have killswitch runbook |
| Group rule storm | Misconfigured group rule matching all users and causing recursive evaluation | Check Admin Console: Directory > Groups > Rules for rules with broad conditions; System Log for `group.user.membership.add` volume | Group membership computation spike; Okta backend slowdown | Deactivate offending group rule: PUT `/api/v1/groups/rules/<rule-id>/lifecycle/deactivate` | Validate group rule scope before activation; test in staging Okta org; use `&limit=` in group membership queries |
| Okta AD Agent import loop | Full import triggered repeatedly (manually or by automation) causing duplicate provisioning events | Check System Log for `user.import.start` frequency; AD Agent logs on host | User attribute overwrites; provisioning workflows triggered repeatedly | Stop repeated imports; deactivate auto-import schedule temporarily; investigate triggering automation | Set AD agent scheduled import to incremental; require approval for manual full imports in production |
| OAuth token leakage via public clients | Public OAuth app configured without PKCE; tokens captured from redirect URIs | Audit Applications: check all apps with `token_endpoint_auth_method=none` lacking `require_pkce` | Tokens can be replayed; unauthorized resource access | Enable PKCE requirement on all public clients: Applications > <App> > General > Require PKCE | Enforce PKCE on all new public client registrations via policy; periodic OAuth app security audit |
| Excessive session creation from automation | Service account configured to use interactive SSO instead of M2M token | System Log: filter `user.session.start` events for service account users | Session quota consumed; user sessions may be impacted | Switch service account to OAuth 2.0 client credentials flow; revoke active sessions: `DELETE /api/v1/users/<uid>/sessions` | Use dedicated OAuth M2M clients for all automation; never use user credentials for service accounts |
| API token sprawl | Many long-lived API tokens with super-admin scope created over time | `GET /api/v1/api-tokens` list for tokens with no expiry and admin scope | Any leaked token has full org admin access | Rotate all old tokens; delete unused: `DELETE /api/v1/api-tokens/<id>`; scope new tokens to minimum privileges | Set token expiration policy; use scoped OAuth 2.0 service apps instead of SSWS tokens; audit quarterly |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot org rate limit (API calls) | All API integrations returning 429; `X-Rate-Limit-Remaining: 0` in response headers | `curl -sI -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/users?limit=1" | grep -i x-rate-limit` | Single org rate limit shared across all API tokens; automation flooding `/api/v1/users` or `/api/v1/logs` | Distribute integrations across multiple API tokens; add exponential backoff; use `X-Rate-Limit-Reset` header for retry timing |
| OIDC token introspection endpoint latency | Application token validation latency >1s; `POST /oauth2/<as-id>/v1/introspect` slow | `time curl -s -X POST "https://$OKTA_DOMAIN/oauth2/default/v1/introspect" -d "token=<tok>&token_type_hint=access_token" -u "<client>:<secret>"` | Okta authorization server under load; introspection requires server-side lookup | Switch to local JWT validation using JWKS: `curl "https://$OKTA_DOMAIN/oauth2/default/v1/keys"` and verify `alg`/`iss`/`exp` locally |
| Inline hook execution adding token issuance latency | Login flow latency = hook round-trip; p99 auth >3s; users report slow logins | Okta System Log: `GET /api/v1/logs?filter=eventType+eq+"system.hook.outbound.request"` — check `debugContext.debugData.hookDuration` | Hook endpoint slow to respond; synchronous execution blocks token issuance | Optimize hook endpoint response time <500ms; or temporarily disable hook: Okta Admin > Workflow > Inline Hooks > Deactivate |
| Group membership evaluation pressure | Token issuance slow for users with many groups; `groups` claim slow to populate | System Log: check `user.authentication.sso` event `debugData.requestProcessingTime`; compare users with many vs few groups | Large number of Okta groups requiring rule evaluation per token issuance | Reduce group count using group hierarchies; cache group membership evaluation; limit groups in token via claim filter |
| AD Agent full import blocking incremental syncs | User provisioning delayed; AD Agent logs show `ImportRunning` for >30 min | AD Agent host: `Get-EventLog -LogName Application -Source "Okta AD Agent" -Newest 50` on Windows | Full AD import triggered manually or on schedule; blocks delta sync from processing changes | Cancel full import from Okta Admin > Directory > Directory Integrations; restart AD Agent service | Disable scheduled full imports; use incremental import only; require manual approval for full imports |
| OIDC discovery endpoint cache miss | First token validation after deployment takes >2s; Okta `.well-known/openid-configuration` fetched per instance | `time curl -s "https://$OKTA_DOMAIN/.well-known/openid-configuration" | jq .jwks_uri` | Each application instance fetches discovery on cold start; no shared cache | Implement application-level OIDC config caching with 24h TTL; pre-warm on deployment |
| Inline hook invocation storm | Multiple apps configured with same inline hook endpoint; traffic spikes under load | Count active inline hooks: `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/inlineHooks" | jq length` | All token issuances across all applications trigger same hook synchronously | Rate-limit hook endpoint; implement async hook pattern where possible; deduplicate hook calls for same user |
| Session token GC lag | Okta session API queries slow under heavy login load; `GET /api/v1/sessions` timeouts | `time curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/sessions/<session-id>"` | Large number of active sessions in org; GC of expired sessions lagging | Not directly tunable in Okta SaaS; reduce session lifetime via sign-on policy; contact Okta support if systemic |
| Webhook event delivery backlog | App event receiver reporting events delayed >5 min; audit log shows events published but not delivered | Okta Admin > Reports > System Log: filter `system.event_hook.attempt.failed`; check `hook.attempt.failed` count | Slow event hook consumer; Okta retries with exponential backoff; backlog grows | Scale event hook consumer; process events asynchronously; `PATCH /api/v1/eventHooks/<id>` to temporarily deactivate and drain |
| MFA factor challenge latency (Okta Verify) | Push notification delivery delayed >10s; users complaining; `user.mfa.okta_verify.*.push` events show high `outcome.reason` latency | System Log: `GET /api/v1/logs?filter=eventType+eq+"user.mfa.okta_verify.push_verify"&since=<time>` — check timestamp delta | APNS/FCM push delivery delay; device offline; cellular network congestion | Offer TOTP as fallback (`GET /api/v1/users/<id>/factors` — use `token:software:totp` factor type); monitor Okta Status page |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on custom Okta domain | Browser shows cert error on branded login page; `openssl s_client -connect <custom-domain>:443 2>&1 | grep "Verify return code"` | Custom domain TLS cert in Okta not auto-renewed; Let's Encrypt or customer-managed cert expired | All logins via branded domain fail; users cannot authenticate | Renew cert via Okta Admin > Customization > Domain > Certificate; for Let's Encrypt: re-run ACME challenge; update cert in Okta settings |
| mTLS client cert rotation failure (OAuth private_key_jwt) | Service-to-service auth failing with `invalid_client`; JWT signing key rotated but Okta JWKS not updated | `curl -s "https://$OKTA_DOMAIN/oauth2/v1/clients/<client-id>/jwks"` shows old key; `openssl x509 -noout -dates -in <new-cert.pem>` | Application rotated JWT signing key but did not update Okta app JWKS endpoint or upload new public key | Update Okta app public JWKS: `PUT /api/v1/apps/<id>/credentials/jwks`; coordinate key rotation with deployment |
| DNS failure for Okta org hostname | All auth fails; `nslookup $OKTA_DOMAIN` from application servers fails | `dig $OKTA_DOMAIN +short`; `curl -v "https://$OKTA_DOMAIN/api/v1/org"` | Okta org hostname DNS resolution failure (rare); local DNS resolver failure more common | Check application server DNS resolver; `cat /etc/resolv.conf`; test with public resolver: `dig @8.8.8.8 $OKTA_DOMAIN` |
| TCP connection timeout to Okta APIs | API calls timing out; `curl --connect-timeout 5 "https://$OKTA_DOMAIN/api/v1/users?limit=1"` fails | `traceroute $OKTA_DOMAIN`; check egress firewall rules from application server | Egress firewall blocking outbound HTTPS to Okta IP ranges; ISP routing issue | Verify egress rules allow all Okta IP ranges (published at `https://help.okta.com/en/prod/Content/Topics/Security/Network/...`); check cloud security group outbound rules |
| Okta IP allowlist misconfiguration blocking API access | API returns 403 with `Your client does not have access to perform this action`; only from certain source IPs | `curl -sI -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/org" | grep HTTP` from multiple source IPs | Okta Network Zone or ThreatInsight blocking API calls from new egress IP (e.g. after cloud migration) | Add new IP to Okta Network Zone: Admin > Security > Network > Edit zone; or temporarily disable IP allowlist enforcement |
| Okta AD Agent TLS failure (certificate pinning) | AD Agent showing disconnected in Okta Admin; Agent log: `SSL certificate verify failed` | AD Agent host: `Test-NetConnection -ComputerName <okta-domain> -Port 443`; check AD Agent log at `C:\ProgramData\Okta\Okta AD Agent\logs\` | Okta certificate chain changed; AD Agent using custom cert store without updated CA | Update Windows certificate store with new Okta CA; restart Okta AD Agent service; re-register agent if needed |
| Packet loss causing Okta Verify push retry storms | Users receiving multiple MFA push notifications; Okta retrying delivery | System Log: count `user.mfa.okta_verify.push_verify` events per user per minute; check for duplicates | APNS/FCM delivery unreliable; Okta retries result in multiple notifications | No direct remediation; instruct users to wait for single push; offer TOTP fallback; monitor Okta Status page for push delivery issues |
| MTU mismatch causing SSO SAML POST truncation | SAML POST to SP fails; SP reports invalid assertion; assertion appears truncated | Base64-decode SAMLResponse from browser: `echo '<saml>' | base64 -d | xmllint --format -` — check if XML complete | Large SAML assertion (many groups/attributes) exceeds MTU; TCP fragmentation drops final segment | Reduce attributes in SAML assertion (limit groups claim); fix MTU on intermediate network path; use HTTP-Redirect binding for smaller payloads |
| SSL handshake timeout to external inline hook | Inline hook endpoint SSL negotiation times out; Okta logs `hook.outbound.request` events with `error: SSL handshake failed` | System Log: `GET /api/v1/logs?filter=eventType+eq+"system.hook.outbound.request"` — check `debugContext.debugData.error` | Hook endpoint TLS configuration incompatible with Okta TLS version/cipher requirements | Ensure hook endpoint supports TLS 1.2+; verify no cipher mismatch; test: `curl -v --tlsv1.2 https://<hook-endpoint>/okta` |
| Connection reset on event hook delivery | Event hooks frequently failing; `system.event_hook.attempt.failed` with `connection reset by peer` | `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/eventHooks" | jq '.[].status'` | Event hook consumer timing out or dropping connections; Okta connection reset after short idle | Implement long-polling endpoint on hook consumer; ensure HTTP server keepalive configured; check load balancer idle timeout |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| Org API rate limit exhaustion | All API consumers return 429; admin console actions slow | `curl -sI -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/users?limit=1" | grep -i "x-rate-limit-remaining"` | Single org has shared API rate limits; automation scripts not throttling | Implement client-side rate limiting with `X-Rate-Limit-Reset` header; use separate tokens per integration; contact Okta to increase limits |
| System Log storage (API query window exceeded) | `GET /api/v1/logs` with old `since` parameter returns empty or error | `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/logs?since=<90+days-ago>&limit=1" | jq .errorCode` | Okta System Log retention is 90 days; queries beyond window return empty | Export logs before 90-day window via scheduled automation; use Okta Log Streaming (to S3/Splunk) for long-term retention |
| MFA factor registration quota per user | User cannot add new authenticator; Okta returns error on factor enrollment | `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/users/<uid>/factors" | jq length` | Per-user factor limit reached (Okta supports limited factors per type per user) | Delete unused/old factors: `DELETE /api/v1/users/<uid>/factors/<factor-id>`; audit enrolled factors |
| AD Agent host disk full (import cache) | AD Agent cannot write import cache; sync stops; Okta shows agent as `degraded` | On AD Agent host: `Get-PSDrive C | Select-Object Used,Free`; check `C:\ProgramData\Okta\Okta AD Agent\` size | AD Agent caches AD objects locally; large AD with many users/groups fills disk | Clear AD Agent cache: stop service, delete `C:\ProgramData\Okta\Okta AD Agent\cache\`, restart service | Monitor AD Agent host disk usage; size host with at least 10GB free for cache |
| OAuth app grant table growth | `GET /api/v1/apps/<id>/grants` returning thousands of entries; token issuance slow | `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/apps/<id>/grants?limit=200" | jq length` | Automation creating new grants without revoking old ones; one-time OAuth flows accumulating grants | Revoke stale grants: `DELETE /api/v1/apps/<id>/grants/<grant-id>`; implement grant cleanup in offboarding workflow | Use `offline_access` scope only when needed; revoke grants on user offboarding |
| Session quota per user | Users cannot create new sessions; old sessions not expiring; `session.create` failing | `GET /api/v1/users/<uid>/sessions` — count active sessions | Too many concurrent sessions from multiple devices/browsers without auto-expiry | Force expire old sessions: `DELETE /api/v1/users/<uid>/sessions`; configure sign-on policy session lifetime | Set session idle timeout in sign-on policy; enable `usePersistentCookie: false` for web apps |
| Inline hook retry queue exhaustion | Hook delivery failures accumulate; Okta stops retrying; events silently dropped | System Log: `filter=eventType+eq+"system.hook.outbound.request"` with `outcome.result eq "FAILURE"` count | Hook endpoint down for extended period; Okta retry queue has finite depth | Restore hook endpoint availability; check if events were dropped; replay missed events from System Log | Monitor hook endpoint availability; implement circuit breaker at hook endpoint; set up alerting on `hook.outbound.request` failures |
| API token count approaching limit | New token creation fails; automation cannot get new tokens | `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/api-tokens" | jq length` | Okta limits total API tokens per org; old tokens not cleaned up | Delete expired/unused tokens: `DELETE /api/v1/api-tokens/<id>`; identify by `lastUsed` field in token list | Set expiration on all programmatically created tokens; automate cleanup of tokens unused for >90 days |
| Webhook event backlog (event hook retry exhaustion) | Events from 24+ hours ago still undelivered; hook consumer processing too slow | System Log: `filter=eventType+eq+"system.event_hook.attempt"` — check event `published` vs delivery timestamp delta | Event hook consumer throughput lower than Okta event publication rate | Scale event hook consumer horizontally; process events in parallel by `eventType`; increase consumer instance count | Design event hook consumers for horizontal scaling; use message queue between Okta webhook and processors |
| Ephemeral session token exhaustion for passwordless flows | Magic link / email factor tokens expire before users click; users blocked from logging in | System Log: `filter=eventType+eq+"user.authentication.auth_via_mfa"` with `outcome.result eq "FAILURE"` and `outcome.reason contains "expired"` | Short magic link TTL combined with delayed email delivery | Increase magic link TTL in authenticator settings; investigate email delivery delay separately | Monitor email delivery latency; set magic link TTL to 15+ minutes to accommodate delayed email; provide fallback auth method |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation on user provisioning | SCIM provisioner creates user twice due to retry; two Okta user objects with same login/email | `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/users?search=profile.email+eq+\"<email>\"" | jq length` returns >1 | Duplicate accounts; user may authenticate to wrong account; licensing double-counted | Deactivate and delink duplicate: `POST /api/v1/users/<dup-id>/lifecycle/deactivate`; merge attributes if needed; delete duplicate | Use SCIM provisioner with idempotent create (check-then-create); set `externalId` on all SCIM-provisioned users |
| Saga/workflow partial failure (JIT provisioning) | User authenticated via SAML/OIDC but JIT provisioning group assignment failed mid-transaction | System Log: `filter=eventType+eq+"user.account.provision"` — check for `FAILURE` after successful `user.session.start` | User exists in Okta but missing group memberships; downstream apps deny access due to missing role claims | Manually assign missing groups: `POST /api/v1/groups/<id>/users`; re-authenticate user to get new token with correct groups | Implement provisioning health check: after JIT, verify group memberships before completing SSO redirect |
| Out-of-order AD sync events | AD user deprovisioned; Okta processes deactivation event after re-activation event due to AD Agent queue reordering | System Log: filter `target.id eq "<user-id>"` — check `eventType` sequence for `user.lifecycle.deactivate` vs `user.lifecycle.activate` order | User incorrectly deactivated in Okta despite being active in AD; blocked from logging in | Re-activate user: `POST /api/v1/users/<id>/lifecycle/activate`; reconcile AD Agent import to resync | Configure AD Agent incremental import with consistent ordering; investigate AD Agent event queue for reordering bugs |
| At-least-once webhook delivery causing duplicate app provisioning | Okta event hook delivers `user.account.provision` twice (network glitch causes Okta retry); downstream app creates user twice | App database: check for duplicate user records with same Okta `userId`; System Log: check two `system.event_hook.attempt.success` for same event | Duplicate user accounts in downstream application; potential access control duplication | Deduplicate in application using Okta `userId` as idempotency key; delete duplicate app user record | Implement idempotent webhook handler: use Okta event `uuid` as deduplication key stored in processing table |
| Cross-service deadlock (SCIM + Group Rule) | SCIM provisioner assigns user to group; group rule triggers evaluation that modifies user profile; profile change triggers SCIM back to source system | System Log: alternating `group.user_membership.add` and `user.account.update_profile` events for same user in rapid loop | Infinite loop; API rate limits exhausted; user profile/group membership oscillating | Break loop: deactivate the group rule causing the loop: `PUT /api/v1/groups/rules/<id>/lifecycle/deactivate`; investigate rule conditions | Ensure group rules do not trigger profile attribute changes that feed back into group rule conditions |
| Compensating transaction failure on user deprovisioning | HR system triggers Okta deactivate; Okta deactivates user and revokes all sessions; but downstream SCIM-provisioned apps not notified due to hook failure | System Log: `user.lifecycle.deactivate` succeeded; `system.event_hook.attempt.failed` for deprovisioning hook | Deprovisioned user retains access in downstream apps until next sync | Manually trigger deprovisioning: check each downstream app and call `DELETE /api/v1/apps/<id>/users/<uid>`; replay event via Okta redelivery | Set up monitoring on `event_hook.attempt.failed` for user lifecycle events; implement manual deprovisioning checklist as fallback |
| Distributed lock expiry during token refresh (authorization server) | OAuth refresh token exchange fails mid-operation; client gets `invalid_grant`; retry gets new token but old token revoked mid-use | System Log: `filter=eventType+eq+"app.oauth2.token.refresh_token_revoked"` simultaneous with `app.oauth2.authorize.failed` | Client briefly loses API access; if client does not retry, service disruption | Implement OAuth client retry logic: on `invalid_grant`, fall back to user re-authentication or client credentials re-auth | Use short-lived access tokens (15 min) with refresh token rotation; implement client-side token refresh with mutex to prevent concurrent refresh |
| Out-of-order group membership affecting SAML assertion | User removed from group; SAML assertion still includes group for duration of Okta session (cached assertion) | `GET /api/v1/users/<id>/groups` shows group removed; but SP still sees group in SAML attributes for existing session | User retains access in SP despite Okta group removal until session expires or SP re-validates | Force session termination: `DELETE /api/v1/users/<uid>/sessions`; user must re-authenticate to get updated assertion | Set SP session lifetime short (≤1h); use OIDC with short-lived tokens instead of SAML for privilege-sensitive apps |
| Inline hook compensating failure (token issuance rollback) | Inline hook returns error response; Okta denies token issuance; user blocked; hook endpoint cannot be reverted | System Log: `filter=eventType+eq+"app.oauth2.authorize.failed"` with `outcome.reason contains "hook"`; count affected users | Users blocked from authenticating until hook endpoint fixed or hook deactivated | Deactivate hook immediately: Okta Admin > Workflow > Inline Hooks > Deactivate; users can then authenticate without hook; fix endpoint before re-enabling | Implement hook endpoint with circuit breaker; design hook to return `allow` on timeout/error rather than blocking auth |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor (rate-limit contention across integrations) | `GET /api/v1/logs?filter=eventType+eq+"system.org.rate_limit.warning"` — multiple integrations hitting shared org rate limit | Other integrations receive 429; authentication flows disrupted for applications sharing token quota | Identify consuming integration: `GET /api/v1/logs` grouped by `actor.id` (API token accessor) | Distribute API integrations across multiple API tokens; each token gets independent sub-quota; implement client-side throttling with `X-Rate-Limit-Reset` header parsing |
| Memory pressure from large group rule evaluation | Org-level group rule evaluation triggered by one app's massive import; Okta org CPU bound | Other apps' JIT provisioning delayed; login latency increased for all users | Pause group rule: `POST /api/v1/groups/rules/<id>/lifecycle/deactivate` | Limit group rule complexity; schedule large AD imports during off-peak hours; break large rules into targeted, indexed sub-rules |
| Disk I/O saturation (System Log volume) | Verbose application emitting thousands of events per minute; System Log query performance degraded | Audit queries for other applications return slowly; log export jobs time out | Filter verbose app from System Log queries: add `&filter=client.id+ne+"<verbose-app-client-id>"` to narrow query scope | Contact Okta support if single application is monopolizing System Log storage; reduce event verbosity in application (fewer token introspection calls) |
| Network bandwidth monopoly (event hook delivery) | One application's event hook consumer slow; Okta queuing all events; consuming Okta delivery worker capacity | Other applications' event hooks delayed; webhook SLA breached | Temporarily deactivate slow hook: `PUT /api/v1/eventHooks/<id>` with `{"status":"INACTIVE"}` | Scale event hook consumer; process events asynchronously; use Okta Log Streaming (Splunk/S3) instead of per-app event hooks |
| Connection pool starvation (shared OIDC client) | Multiple applications sharing same OAuth `client_id`; rate limit on `/oauth2/v1/token` exhausted | Applications cannot exchange auth codes; login fails with 429 | Check client rate limit: `GET /api/v1/logs?filter=client.id+eq+"<shared-client-id>"&since=<time>` — count token endpoint calls | Register separate OAuth application per service in Okta; never share `client_id` across applications; each gets independent rate limit |
| Quota enforcement gap (app assignment without license check) | Application assigned to unlimited users; Okta license limit for licensed app exceeded | Users beyond license quota cannot access app; no warning until hard limit hit | `GET /api/v1/apps/<id>/users | jq length` — count assigned users vs license limit | Implement assignment policy with group-based access; audit app user count: `GET /api/v1/apps/<id>/users?limit=200` paginated; remove unneeded assignments |
| Cross-tenant data leak risk (shared Okta org multi-tenancy) | Applications in same Okta org can enumerate other apps' users via `/api/v1/users` with admin token | One application's API token can expose user data for other apps in same org | `GET /api/v1/users?search=profile.email+sw+"@otherdomain.com"` from compromised token | Use separate Okta orgs for strict tenant isolation; if shared org required, scope API tokens via custom admin roles with app-specific permissions; never grant `Super Administrator` to application tokens |
| Rate limit bypass via multiple API tokens | Service registering 10 API tokens and rotating through them to multiply effective rate limit | Other services starved of API capacity; org rate limit multiplied beyond SLA | `GET /api/v1/api-tokens | jq length` — count total tokens; `GET /api/v1/api-tokens | jq '.[].name'` — identify tokens per service | Audit and consolidate API tokens per service to one; enforce via Okta admin policy documenting token naming convention; regularly audit token count |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure (no Okta metrics endpoint) | Authentication failure rate rising; no Prometheus alert fires | Okta is SaaS — no Prometheus endpoint; metrics must be derived from System Log API polling | Poll System Log for failed auth events: `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/logs?filter=outcome.result+eq+\"FAILURE\"&since=$(date -v-5M +%Y-%m-%dT%H:%M:%SZ)" | jq length` | Implement custom exporter polling Okta System Log API; push auth failure counts to Prometheus Pushgateway; or use Okta Log Streaming to Splunk/DataDog for real-time metrics |
| Trace sampling gap (MFA challenge flow not traced) | Multi-step MFA flows failing intermittently; traces only show first hop; MFA challenge step invisible | Application traces the OIDC redirect but not Okta-internal MFA challenge; gap between redirect and callback | Check System Log for MFA events: `GET /api/v1/logs?filter=eventType+sw+"user.mfa"&since=<time>` — correlate session IDs | Correlate Okta `sessionId` from System Log with application trace `correlationId`; add Okta System Log export to distributed tracing backend as custom span source |
| Log pipeline silent drop (Okta Log Streaming to S3 lag) | Security team queries S3 log bucket; events from last 30 min missing; no error visible | Okta Log Streaming has delivery delay; S3 delivery failures silently retried without alerting the user | Check Okta Log Streaming status: Admin > Reports > Log Streaming; use System Log API directly for recent events: `GET /api/v1/logs?since=<30-min-ago>` | Add monitoring on S3 bucket `LastModified` timestamp of log objects; alert if no new log object delivered within 10 min; set up Okta event hook as redundant delivery path |
| Alert rule misconfiguration (wrong System Log filter syntax) | Alerting on user lockouts not firing despite visible lockout events in System Log | OData filter syntax error in alert query: `eventType eq "user.account.lock"` (missing `+`) passes silently returning all events | Test filter directly: `curl -s -H "Authorization: SSWS $OKTA_TOKEN" "https://$OKTA_DOMAIN/api/v1/logs?filter=eventType+eq+\"user.account.lock\"&limit=5" | jq length` | Validate all System Log filters against live API before adding to monitoring; use Okta System Log UI filter builder to generate correct syntax; test alert rule with known events |
| Cardinality explosion in custom Okta log exporter | Custom log exporter using `target.id` (user ID) as Prometheus label; millions of series; Prometheus OOM | Each unique user ID creates new time series; 50K users = 50K series per metric | Count series: `curl http://prometheus:9090/api/v1/label/okta_user_id/values | jq '.data | length'` | Remove `okta_user_id` from Prometheus labels; use it only in logs/traces; aggregate by `eventType`, `outcome.result`, `actor.type` for metrics |
| Missing health endpoint for Okta AD Agent | AD Agent silently disconnected; user sync stopped; no alert fired for hours | Okta AD Agent health only visible in Okta Admin console; no external health check API for the agent host service | On AD Agent host: `Get-Service "Okta AD Agent" | Select-Object Status`; check Okta Admin: `GET /api/v1/agentPools?expand=agents | jq '.[].agents[] | select(.operationalStatus != "OPERATIONAL")'` | Add Windows service monitor for `Okta AD Agent` service health; or set up Okta admin API polling for agent status; alert on `operationalStatus != "OPERATIONAL"` |
| Instrumentation gap in JIT provisioning critical path | JIT-provisioned users missing group memberships silently; app access denied but no auth error logged | JIT provisioning failure logged at Okta level but not surfaced to application; app sees authenticated user with no roles | Check System Log after JIT login: `GET /api/v1/logs?filter=eventType+sw+"user.account.provision"+and+target.id+eq+"<user-id>"&since=<time>` | Add post-login group membership check in application; log warning if user authenticated but `groups` claim empty; alert on `groups` claim mismatch vs expected |
| Alertmanager/PagerDuty outage (Okta used for Alertmanager SSO) | Alertmanager SSO broken; SREs cannot authenticate to Alertmanager to manage incidents | Okta itself experiencing issues; all SSO-protected tools inaccessible; circular dependency | Direct Alertmanager access: configure emergency local admin account in Alertmanager as break-glass bypass; `curl -u admin:password http://alertmanager:9093/-/healthy` | Add local `basic_auth` fallback to Alertmanager for break-glass access; test monthly; document break-glass procedure in runbook; never rely solely on SSO for operations tools |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Okta AD Agent minor version upgrade rollback | After upgrading AD Agent, sync stops; Agent shows `DEGRADED` in Okta Admin | Okta Admin: `GET /api/v1/agentPools?expand=agents | jq '.[].agents[] | {id, version, operationalStatus}'`; Windows Event Log: `Get-EventLog -LogName Application -Source "Okta AD Agent" -Newest 20` | Uninstall new version via Windows Add/Remove Programs; download and reinstall previous version from Okta Admin > Downloads; re-register agent | Test AD Agent upgrade on non-production AD environment first; check Okta AD Agent release notes for known issues; upgrade one agent at a time in multi-agent deployments |
| Okta org major migration (Classic → OIE) | After migrating from Classic to OIE engine, custom sign-on policies not behaving as expected; users hitting unexpected MFA prompts | Check authenticator policy: `GET /api/v1/policies?type=ACCESS_POLICY` (OIE) vs `GET /api/v1/policies?type=OKTA_SIGN_ON` (Classic); compare rule counts and conditions | Classic → OIE migration is not reversible; contact Okta Support; document OIE policy differences; adjust OIE policies to match intended Classic behavior | Test OIE migration in sandbox org first; audit all sign-on policies and rules before migration; plan for extended parallel testing period |
| SAML certificate rotation partial completion | Some SPs updated with new cert, some still using old; SSO broken for apps not yet updated | `GET /api/v1/apps?filter=name+eq+"<saml-app>"&limit=200 | jq '.[].credentials.signing.kid'` — compare signing key IDs across apps; test SSO: `GET /api/v1/apps/<id>/sso/saml/metadata` | Re-upload old signing certificate to Okta: Admin > Applications > App > Sign On > SAML Signing Certificates; restore old cert as active signing cert | Coordinate SAML cert rotation with all SP admins before rotating; use certificate preview/advance sharing feature; never rotate active SAML signing cert without SP confirmation |
| Rolling upgrade of inline hook endpoint | Inline hook endpoint upgraded; brief period where requests fail; Okta retries cause delayed token issuance | `GET /api/v1/logs?filter=eventType+eq+"system.hook.outbound.request"&since=<time>` — check failure rate during deployment window | Deactivate hook during deployment: Okta Admin > Workflow > Inline Hooks > Deactivate; upgrade endpoint; reactivate | Use blue/green deployment for hook endpoint; route Okta to stable endpoint during deployment; test hook with: `POST /api/v1/inlineHooks/<id>/execute` |
| Zero-downtime policy migration (group rule refactor) | After updating group rule conditions, users lose group memberships mid-session; app access revoked unexpectedly | `GET /api/v1/groups/rules/<id>` — check new vs old conditions; `GET /api/v1/logs?filter=eventType+eq+"group.user_membership.remove"&since=<time>` — count unexpected removals | Revert group rule: `PUT /api/v1/groups/rules/<id>` with original JSON body; re-run group rule evaluation: `POST /api/v1/groups/rules/<id>/lifecycle/activate` | Test group rule changes on test users before production rollout; check `invalidUsers` count: `GET /api/v1/groups/rules/<id>` — non-zero means rule has evaluation errors |
| Auth policy config format change (OIE policy JSON schema) | After Okta platform update, existing policy API calls return `422` with schema validation error | `GET /api/v1/policies/<id>?type=ACCESS_POLICY` — inspect current schema; compare with Terraform/Pulumi IaC definitions; check for new required fields | Reapply policy via Okta Admin UI (not API) if IaC schema is outdated; update Terraform provider version: `terraform init -upgrade` | Pin Okta Terraform provider version; subscribe to Okta API changelog; validate IaC plan against live org before applying: `terraform plan` |
| Feature flag rollout regression (ThreatInsight blocking legitimate users) | After enabling Okta ThreatInsight in enforcement mode, legitimate users from corporate VPN blocked | `GET /api/v1/logs?filter=eventType+eq+"security.threat.detected"&since=<time>` — check blocked IPs; compare with corporate IP ranges | Switch ThreatInsight from `BLOCK` to `AUDIT` mode: Admin > Security > General > Okta ThreatInsight > Audit Mode; or exclude corporate IPs: Admin > Security > Network > Add IP to trusted zone | Test ThreatInsight in Audit mode for 1 week before enabling enforcement; add corporate IP ranges to `TRUSTED_PROXY_ZONE` before enabling |
| Dependency version conflict (Okta OIDC library + new PKCE requirement) | After Okta org requires PKCE for public clients, existing OAuth apps using implicit flow stop working | `GET /api/v1/apps/<id> | jq '.settings.oauthClient.grant_types'` — check if `implicit` grant present; `GET /api/v1/logs?filter=eventType+eq+"app.oauth2.authorize.failed"&since=<time>` — look for PKCE errors | Re-enable implicit flow temporarily: Okta Admin > Applications > App > General > Grant type > Implicit; coordinate app updates | Audit all applications for PKCE support before enabling org-level PKCE enforcement; use `require_pkce: true` per-app before setting org default; test with PKCE-enabled test app |

## Kernel/OS & Host-Level Failure Patterns
**Minimum cross-cutting cases to evaluate here:** OOM killer false kill, inode exhaustion, CPU steal, NTP skew affecting locks, leases, or coordination, file descriptor exhaustion, and TCP conntrack table saturation.

| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|------------------|--------------|-------------|----------------------|
| OOM killer terminates Okta AD Agent on Windows domain controller | Windows Event Log: `Get-WinEvent -FilterHashtable @{LogName='System'; Id=1076,6008} -MaxEvents 10`; `Get-Process "OktaAgentSvc" -ErrorAction SilentlyContinue` returns nothing | Okta AD Agent memory leak during large directory sync; 50K+ users with full attribute sync consuming > 2GB | AD Agent stops syncing users to Okta; new user provisioning fails; password sync stops | Restart agent: `Restart-Service "Okta AD Agent"`; limit sync scope: reduce attribute mapping in Okta Admin > Directory > Directory Integrations; set memory limit via Windows Job Objects |
| Inode exhaustion on Okta RADIUS Agent Linux host | `df -i /var/log/okta`; `find /var/log/okta -type f | wc -l` | Okta RADIUS Agent debug logging enabled in production; per-request log files accumulating without rotation | RADIUS Agent cannot write log files; authentication requests may still work but audit trail lost | `find /var/log/okta -name '*.log.*' -mtime +7 -delete`; disable debug logging: set `loglevel=INFO` in agent config; configure logrotate for `/var/log/okta/*.log` |
| CPU steal spike on Okta AD Agent VM causing sync timeout | `Get-Counter '\Processor(_Total)\% Processor Time'`; `Get-Counter '\Hyper-V Hypervisor Virtual Processor(_Total)\% Guest Run Time'` on Hyper-V | Noisy neighbor on shared hypervisor running domain controller with Okta AD Agent | Directory sync operations timeout; `GET /api/v1/logs?filter=eventType+eq+"system.agent.ad.import.error"` shows timeout errors | Migrate AD Agent to dedicated VM; increase sync timeout in agent config; schedule syncs during off-peak hours |
| NTP clock skew causing Okta SAML assertion validation failure | `w32tm /query /status` on Windows; `timedatectl status` on Linux; `GET /api/v1/logs?filter=eventType+eq+"app.auth.sso.assertion.failed"&since=<time>` | Windows Time Service stopped or misconfigured; clock drift > SAML assertion `NotBefore`/`NotOnOrAfter` tolerance (typically 5 min) | All SAML SSO assertions rejected by SPs; users cannot log into SAML apps; `SAMLResponse` time validation fails | `w32tm /resync /force` on Windows; `chronyc makestep` on Linux; verify: `w32tm /query /status | findstr Offset`; check SP clock synchronization too: both IdP and SP must be time-synced |
| File descriptor exhaustion on Okta RADIUS Agent handling MFA push | `lsof -p $(pgrep okta-radius) | wc -l`; `cat /proc/$(pgrep okta-radius)/limits | grep 'open files'` | Each MFA push verification opens persistent HTTPS connection to Okta API; concurrent RADIUS requests with MFA exhaust FDs | New RADIUS authentication requests rejected; VPN users cannot authenticate; `Too many open files` in agent log | `prlimit --pid $(pgrep okta-radius) --nofile=65536:65536`; add `LimitNOFILE=65536` to systemd unit; reduce MFA push timeout: agents settings in Okta Admin |
| TCP conntrack table full on host running Okta AD Agent with many DCs | `dmesg | grep 'nf_conntrack: table full'`; `cat /proc/sys/net/netfilter/nf_conntrack_count` (Linux); `netstat -an | find /c "ESTABLISHED"` (Windows) | AD Agent maintaining LDAP connections to multiple domain controllers; conntrack table sized for default | LDAP sync connections dropped; AD Agent shows `DEGRADED` in Okta Admin; user/group sync fails | Linux: `sysctl -w net.netfilter.nf_conntrack_max=524288`; Windows: increase ephemeral port range: `netsh int ipv4 set dynamicport tcp start=10000 num=55535` |
| Kernel panic on domain controller running Okta AD Agent | AD Agent offline after DC crash; `GET /api/v1/agentPools?expand=agents` shows agent `INACTIVE` | Kernel bug, hardware fault, or BSOD on domain controller | User provisioning and password sync halted; if single-agent deployment, all Okta-AD sync stops | Verify DC recovery: `dcdiag /test:connectivity`; restart agent: `Start-Service "Okta AD Agent"`; check Okta Admin for agent status; if agent not recovering, re-register: download and reinstall from Okta Admin |
| NUMA memory imbalance on high-traffic Okta RADIUS Agent | `numactl --hardware`; `numastat -p $(pgrep okta-radius) | grep -E 'numa_miss|numa_foreign'` | RADIUS Agent with TLS termination hitting remote NUMA memory for crypto operations; latency on MFA verification | RADIUS authentication P99 latency elevated; MFA push verification slow; VPN login times increase | Pin process to NUMA node: `numactl --cpunodebind=0 --membind=0 /opt/okta/radius/bin/okta-radius-agent`; or update systemd ExecStart with `numactl --localalloc` |

## Deployment Pipeline & GitOps Failure Patterns
**Minimum cross-cutting cases to evaluate here:** image pull failure (rate limit or auth), Helm drift, ArgoCD sync stuck, PodDisruptionBudget-blocked rollout, blue-green cutover failure, and ConfigMap or Secret drift.

| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Okta AD Agent installer download failure | Agent installer download from Okta Admin fails; `Invoke-WebRequest` timeout or TLS error | `Test-NetConnection -ComputerName "your-org.okta.com" -Port 443`; `[Net.ServicePointManager]::SecurityProtocol` — check TLS version | Download installer from alternate machine and transfer via USB/network share; use cached installer from previous version | Cache agent installers in internal artifact repository; test network connectivity to Okta before upgrade window |
| Okta AD Agent installer auth failure in proxy environment | Agent registration fails during install; `The remote server returned an error: (407) Proxy Authentication Required` | `Get-ItemProperty 'HKLM:\SOFTWARE\Okta\Okta AD Agent' | Select-Object ProxyAddress`; `netsh winhttp show proxy` | Configure proxy: `netsh winhttp set proxy proxy-server="http://proxy:8080" bypass-list="*.internal.com"`; re-run installer | Pre-configure proxy settings before agent installation; document proxy requirements in agent deployment runbook |
| Terraform drift — Okta application config out of sync with Git | `terraform plan -target=okta_app_saml.myapp` shows unexpected drift; SAML cert or redirect URI changed outside Terraform | `terraform state show okta_app_saml.myapp`; compare with Okta Admin UI: `GET /api/v1/apps/<id>` | `terraform apply -target=okta_app_saml.myapp` to restore desired state; or `terraform import` if resource was recreated | Use `okta_app_saml` data source for read-only references; enforce Okta changes only via Terraform in CI; add `prevent_destroy = true` lifecycle |
| ArgoCD sync stuck on Okta OIDC client Secret rotation | ArgoCD shows app `OutOfSync`; Secret containing Okta `client_secret` for OIDC not updating | `argocd app get <app> --refresh`; `kubectl get secret okta-oidc-secret -o jsonpath='{.metadata.resourceVersion}'` | Manually update Secret: `kubectl create secret generic okta-oidc-secret --from-literal=client-secret=$NEW_SECRET --dry-run=client -o yaml | kubectl apply -f -`; restart dependent pods | Use External Secrets Operator syncing from Vault; configure ArgoCD to manage Secrets; store client_secret in Vault not Git |
| PodDisruptionBudget blocking rollout of Okta-authenticated app | `kubectl rollout status deployment/<okta-app>` hangs; PDB prevents pod eviction during maintenance | `kubectl get pdb -n <ns>`; `kubectl describe pdb <pdb> | grep -E 'Allowed\|Disruption'` | Temporarily patch PDB: `kubectl patch pdb <pdb> -p '{"spec":{"maxUnavailable":1}}'`; complete rollout; restore PDB | Set PDB `maxUnavailable: 1` with sufficient replicas; ensure Okta session persistence survives pod cycling |
| Blue-green cutover failure — Okta OIDC redirect URI mismatch | After switching to green environment, Okta rejects auth callback; `redirect_uri_mismatch` error | `GET /api/v1/apps/<id> | jq '.settings.oauthClient.redirect_uris'` — check if green URL listed; browser shows Okta error page | Add green redirect URI to Okta app: `PUT /api/v1/apps/<id>` with updated `redirect_uris` array; or revert DNS to blue | Pre-register both blue and green redirect URIs in Okta app before cutover; verify with: `curl -s https://$OKTA_DOMAIN/oauth2/default/.well-known/oauth-authorization-server` |
| ConfigMap/Secret drift breaking Okta OIDC integration | Application fails to authenticate; `invalid_client` error from Okta; client_id in Secret doesn't match Okta app | `kubectl get secret okta-oidc -o jsonpath='{.data.client-id}' | base64 -d`; compare with `GET /api/v1/apps/<id> | jq '.credentials.oauthClient.client_id'` | Restore Secret from Vault or Git: `kubectl apply -f okta-secret.yaml`; rolling restart dependent pods | Store Okta secrets in Vault; validate client_id against Okta API in CI; use External Secrets Operator |
| Feature flag stuck — Okta ThreatInsight blocking legitimate traffic after enable | `GET /api/v1/logs?filter=eventType+eq+"security.threat.detected"&since=<time>` shows corporate IPs blocked; VPN users locked out | ThreatInsight enforcement mode enabled without IP exclusions; corporate egress IPs classified as threat | Legitimate users blocked at Okta login; support tickets spike | Switch to Audit mode: Okta Admin > Security > General > ThreatInsight > Audit; add corporate IPs to trusted zone: Admin > Security > Networks > Add Zone |

## Service Mesh & API Gateway Edge Cases
**Minimum cross-cutting cases to evaluate here:** circuit breaker false positives, rate limiting on legitimate traffic, stale service discovery endpoints, mTLS rotation interruption, retry storm amplification, gRPC keepalive or max-message failures, and trace context loss.

| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive on Okta-authenticated service during Okta latency | Envoy circuit breaker opens on application service; auth callback taking > 5s due to Okta slowness | Okta API responding slowly (e.g., during maintenance); application's auth middleware blocks while validating token with Okta; Envoy timeout exceeded | Service marked unhealthy; users cannot access application even when Okta recovers | Increase timeout for auth-dependent services: `DestinationRule` with `connectionPool.tcp.connectTimeout: 30s`; cache Okta JWKS locally with TTL; avoid inline Okta API calls in request path |
| Okta API rate limiting blocking application token validation | `GET /api/v1/logs?filter=eventType+eq+"system.org.rate_limit.violation"&since=<time>` shows rate limit hits; applications getting 429 from Okta | Multiple applications introspecting tokens via `/oauth2/v1/introspect` on every request; exceeding Okta org rate limit | Token validation fails; users see 403 or 500 from applications; SaaS integrations fail | Switch from introspection to local JWT validation: verify signature with cached JWKS from `https://$OKTA_DOMAIN/oauth2/default/v1/keys`; reduce Okta API calls; request rate limit increase from Okta |
| Stale SAML metadata cached in service provider | SP using cached Okta SAML metadata with old signing certificate; SSO fails with `invalid signature` | Okta rotated SAML signing cert; SP metadata cache not refreshed; `metadata_url` polling interval too long | SSO broken for this SP; users cannot log in; `SAMLResponse` signature validation fails | Force SP metadata refresh: re-import from `https://$OKTA_DOMAIN/app/<app-id>/sso/saml/metadata`; or manually upload new cert to SP; verify: `openssl x509 -in okta-saml-cert.pem -dates -noout` |
| mTLS rotation breaking Okta event hook delivery | Okta event hooks fail to deliver after mTLS cert rotation on webhook endpoint; `GET /api/v1/eventHooks/<id>` shows `INACTIVE` | Webhook endpoint requires client mTLS; Okta does not support client certificates for event hooks; cert rotation removed server-side TLS trust | Event hook delivery silently stops; security events not forwarded to SIEM; compliance gap | Remove mTLS requirement for Okta webhook endpoint; use `eventHookSecret` header-based verification instead; or place Okta webhook behind API gateway that terminates mTLS; verify: `POST /api/v1/eventHooks/<id>/lifecycle/verify` |
| Retry storm on Okta token endpoint during mass session refresh | `GET /api/v1/logs?filter=eventType+eq+"system.org.rate_limit.warning"&since=<time>` shows burst; applications retrying on 429 | Coordinated session expiry across application fleet; all instances hit `/oauth2/v1/token` simultaneously | Okta rate limit exceeded; token refresh fails; users logged out; retry amplifies load | Stagger token expiry: add jitter to `expires_in` handling; implement exponential backoff on 429; cache refresh tokens with staggered TTL; request DPoP token binding to reduce token rotation frequency |
| gRPC service behind Okta-authenticated gateway losing auth context | gRPC metadata (Bearer token) stripped by API gateway after Okta token exchange; downstream gRPC service gets unauthenticated request | API gateway performs Okta token introspection but does not forward resulting access token in gRPC metadata; `authorization` metadata key missing | Downstream gRPC services reject requests; users authenticated at gateway but unauthorized at service | Configure gateway to pass Okta access token in gRPC metadata: set `grpc-metadata-authorization: Bearer $TOKEN`; or use Okta-issued JWT directly in gRPC metadata without gateway introspection |
| Trace context propagation loss through Okta SSO redirect flow | Distributed trace breaks during Okta OIDC redirect; original request trace ID not preserved after `/oauth2/default/v1/authorize` callback | Okta redirect flow creates new HTTP request; browser redirect does not carry `traceparent` header; trace context lost at IdP boundary | Cannot trace end-to-end login latency; Okta authentication time invisible in application traces | Store trace context in OIDC `state` parameter before redirect; extract and restore after callback; or accept trace break at auth boundary and correlate via user session ID across trace segments |
| Load balancer health check failing on Okta RADIUS Agent port | NLB removes RADIUS Agent from target group; health check on UDP port 1812 times out | UDP health checks unreliable; NLB TCP health check configured on wrong port; RADIUS Agent only listens on UDP | VPN authentication fails for users routed to removed target; intermittent auth failures | Configure NLB to use HTTP health check on RADIUS Agent management port (if available); or use TCP health check on agent status port; verify: `radtest testuser testpass $RADIUS_HOST 0 $SHARED_SECRET` |
