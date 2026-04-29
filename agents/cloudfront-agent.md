---
name: cloudfront-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - provider-cloudfront
  - component-cloudfront-agent
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
# CloudFront SRE Agent

## Role
This agent owns the full operational lifecycle of AWS CloudFront CDN deployments, from distribution configuration and cache behavior management to incident response for origin errors, SSL certificate failures, WAF blocks, Lambda@Edge/CloudFront Functions execution issues, and geographic restriction problems. It distinguishes between errors originating at the edge (4xx/5xx from CloudFront itself) versus errors proxied from the origin (origin 5xx forwarded to clients), monitors cache hit ratios to detect cache poisoning or misconfiguration, and provides data-driven guidance on TTL tuning, origin shield optimization, and invalidation cost management. It responds to performance regressions, security incidents (WAF bypass, DDoS), and distribution deployment failures.

## Architecture Overview
A CloudFront distribution sits between end users and one or more origins (ALB, API Gateway, S3, custom HTTP). At the edge, CloudFront PoPs (Points of Presence) cache responses and optionally execute Lambda@Edge functions (at four event hooks) or CloudFront Functions (viewer request/response only). An Origin Shield can be enabled as an additional caching layer between the edge PoPs and the origin, reducing origin traffic. AWS WAF can be attached to the distribution to filter requests before they reach the origin or even the cache layer. TLS is terminated at the CloudFront edge using ACM-managed certificates.

```
User
  └── CloudFront Edge PoP
        ├── CloudFront Functions (Viewer Request / Viewer Response)
        ├── Lambda@Edge (Viewer Request / Origin Request / Origin Response / Viewer Response)
        ├── AWS WAF (request evaluation)
        │
        ├── Cache (HIT → return cached response, no origin contact)
        │
        └── Cache MISS → Origin Shield (optional) → Origin
                                                      ├── ALB → EC2/ECS
                                                      ├── API Gateway
                                                      └── S3 Bucket
```

## Key Metrics to Monitor

| Metric | Warning Threshold | Critical Threshold | Notes |
|--------|------------------|--------------------|-------|
| `5xxErrorRate` (CloudFront metric) | > 0.5% | > 1% | Both edge-generated and origin-proxied 5xx |
| `4xxErrorRate` | > 2% | > 5% | High 4xx can indicate WAF blocks or bad cache keys |
| `OriginLatency` (P99) | > 500 ms | > 2 s | Time from CloudFront to origin response; excludes edge processing |
| Cache hit rate (`CacheHitRate`) | < 80% | < 50% | Low hit rate = high origin load and high latency |
| `TotalErrorRate` | > 1% | > 3% | Combined 4xx + 5xx rate |
| Lambda@Edge error rate | > 0.1% | > 1% | Errors in Lambda@Edge functions cause 502 responses |
| Origin `5xxErrorRate` (per-origin) | > 1% | > 5% | Distinguish origin problems from edge problems |
| WAF `BlockedRequests` rate | > 5% of requests | > 20% | High block rate may indicate false positives or attack |
| SSL certificate days until expiry (ACM) | < 30 days | < 7 days | Auto-renew normally occurs 60 days before expiry |
| Distribution propagation time | > 10 min | > 30 min | CloudFront config change propagation to all edge PoPs |

## Alert Runbooks

### Alert: High5xxErrorRate
**Condition:** `5xxErrorRate > 1%` sustained for > 5 min
**Triage:**
1. Determine if errors are edge-generated or origin-proxied:
   `aws cloudfront get-distribution-metrics --distribution-id <dist-id>` — compare `5xxErrorRate` vs `OriginLatency` spikes.
2. Check CloudFront access logs (S3 or CloudWatch) for the specific error sub-codes:
   `aws logs filter-log-events --log-group-name /aws/cloudfront/<dist-id> --filter-pattern '"502" OR "503" OR "504"' --start-time <epoch_ms>`
3. 502 = CloudFront cannot connect to origin or Lambda@Edge error. 503 = origin returned 503. 504 = origin timeout.
4. If 502: check Lambda@Edge function health — `aws logs filter-log-events --log-group-name /aws/lambda/us-east-1.<function-name> --filter-pattern ERROR`
5. If 503/504: check origin health directly: `curl -v -H "Host: <your-domain>" https://<origin-alb-dns>/health`
### Alert: SSLCertificateExpiringSoon
**Condition:** ACM certificate associated with CloudFront distribution expires in < 30 days and no renewal is in progress
**Triage:**
1. Check certificate status: `aws acm describe-certificate --certificate-arn <cert-arn> --region us-east-1 --query 'Certificate.{Status:Status,NotAfter:NotAfter,RenewalStatus:RenewalSummary.RenewalStatus}'`
2. CloudFront requires certificates in `us-east-1`; verify the certificate is in that region.
3. If renewal is `PENDING_VALIDATION`: check DNS CNAME records for the ACM validation record — `aws acm describe-certificate --certificate-arn <arn> --query 'Certificate.DomainValidationOptions'`
4. Verify DNS CNAME exists in Route 53: `aws route53 list-resource-record-sets --hosted-zone-id <zone-id> --query 'ResourceRecordSets[?Type==\`CNAME\`]'`
### Alert: CacheHitRateLow
**Condition:** `CacheHitRate < 50%` for > 30 min
**Triage:**
1. Check `ResultType` distribution in logs to see `Hit`, `Miss`, `RefreshHit`, `Error`, `LimitExceeded`:
   `aws logs filter-log-events --log-group-name /aws/cloudfront/<dist-id> --filter-pattern '"Miss"' --start-time <epoch_ms> | head -50`
2. Check if `Vary` headers from origin are busting the cache (each unique `User-Agent` or `Accept` becomes a separate cache entry):
   `grep -i 'vary' <cloudfront-access-log>` or check origin response headers.
3. Verify cache key configuration — are query strings, cookies, or headers being forwarded to the origin unnecessarily?
   `aws cloudfront get-cache-policy --id <cache-policy-id>`
4. Check if TTLs are set correctly: `aws cloudfront get-distribution-config --id <dist-id> --query 'DistributionConfig.DefaultCacheBehavior.{DefaultTTL:DefaultTTL,MaxTTL:MaxTTL,MinTTL:MinTTL}'`
### Alert: WAFBlockRateSurge
**Condition:** WAF `BlockedRequests` > 20% of total requests for > 5 min
**Triage:**
1. Check WAF metrics by rule: `aws wafv2 get-sampled-requests --web-acl-arn <waf-acl-arn> --rule-metric-name <rule-name> --scope CLOUDFRONT --time-window StartTime=<epoch>,EndTime=<epoch> --max-items 100`
2. Identify the top-blocking rule and sample blocked request characteristics (IP, URI, User-Agent, body).
3. Determine if blocks are legitimate (DDoS/scanning) or false positives (legitimate traffic matching WAF rules).
4. Check WAF logs in S3 or CloudWatch Logs for the `action: BLOCK` records.
## Common Issues & Troubleshooting

### Issue: CloudFront Returning Stale Content After Deployment
**Symptoms:** Users see old version of the site after a deployment; cache-busted URLs work but root path does not.
**Diagnosis:** `curl -I https://<domain>/ | grep -i 'x-cache\|age\|cache-control'` — check `X-Cache: Hit from cloudfront` and `Age: <seconds>`.
### Issue: CORS Errors from CloudFront
**Symptoms:** Browser console shows `CORS policy: No 'Access-Control-Allow-Origin'`; direct origin requests work.
**Diagnosis:** `curl -H "Origin: https://app.example.com" -I https://<cloudfront-domain>/api/resource` — check if `Access-Control-Allow-Origin` is present in the response.
### Issue: Lambda@Edge Function 502 Errors
**Symptoms:** Specific paths return HTTP 502; CloudFront logs show `x-edge-result-type: Error`; Lambda@Edge associated with the cache behavior.
**Diagnosis:** Check Lambda@Edge logs in CloudWatch — they appear in the region where the request was processed: `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/us-east-1.<function-name>`. Then: `aws logs filter-log-events --log-group-name /aws/lambda/us-east-1.<function-name> --filter-pattern 'ERROR' --start-time <epoch_ms>`
### Issue: Custom Domain Returns Certificate Warning
**Symptoms:** Browser shows SSL certificate warning; `ERR_CERT_COMMON_NAME_INVALID` for HTTPS requests.
**Diagnosis:** `openssl s_client -connect <cloudfront-domain>:443 -servername <custom-domain> 2>/dev/null | openssl x509 -noout -text | grep -E 'Subject:|DNS:'` — verify the certificate covers the custom domain.
### Issue: CloudFront Returns 403 for S3 Origin
**Symptoms:** HTTP 403 responses from CloudFront for objects that exist in S3.
**Diagnosis:** `aws s3 ls s3://<bucket>/<path>` — verify object exists. `aws cloudfront get-distribution-config --id <dist-id> --query 'DistributionConfig.Origins'` — check OAC/OAI configuration.
## Key Dependencies

- **ACM Certificate (us-east-1)**: CloudFront requires TLS certificates in us-east-1. Certificate expiry or renewal failure causes TLS handshake failures for all HTTPS traffic on the distribution.
- **Origin (ALB / API Gateway / S3)**: CloudFront is a pure cache/proxy. If the origin is down, all cache misses return errors. Only cached responses are served during origin outages.
- **Route 53 / DNS**: The custom domain must point to the CloudFront distribution domain (`<hash>.cloudfront.net`). DNS misconfiguration routes traffic to wrong endpoints.
- **Lambda@Edge functions**: Executed synchronously on every request at the configured hook. Lambda@Edge errors cause HTTP 502 responses and are difficult to debug due to multi-region log distribution.
- **AWS WAF Web ACL**: Attached to the distribution. WAF rule misconfiguration can block legitimate traffic globally. WAF changes take effect immediately (no propagation delay).
- **S3 Access Logs / CloudWatch Logs**: Required for forensics and compliance. If log delivery is misconfigured, incident investigation is severely hampered.
- **AWS Shield**: DDoS protection. Standard is automatic; Advanced requires activation. Without Shield Advanced, large DDoS attacks can incur significant unexpected bandwidth costs.

## Cross-Service Failure Chains

**Chain 1: ACM Certificate Renewal Failure → TLS Handshake Error → Complete Distribution Outage**
An ACM certificate auto-renew attempt fails because the DNS validation CNAME was accidentally deleted from Route 53 during a DNS cleanup operation. The certificate expires. CloudFront cannot present a valid certificate for the custom domain. All HTTPS connections to the distribution fail with `SSL_ERROR_RX_RECORD_TOO_LONG` or `ERR_CERT_DATE_INVALID`. HTTP traffic (if enabled) continues but browsers enforce HSTS, blocking access. Recovery requires re-issuing the certificate (`aws acm request-certificate`), adding the DNS validation record, waiting for validation, then updating the distribution — a process that takes 15–45 minutes during which the domain is fully inaccessible.

**Chain 2: Lambda@Edge Deployment Bug → 502 Storm → Origin Overload on Retry**
A developer deploys a new version of a Lambda@Edge function that has an unhandled exception in the origin request handler. All cache misses now return HTTP 502 instead of forwarding to the origin. The CDN cache continues serving cached responses (HITs are fine), but all misses — including first requests for new content, API endpoints, and dynamic pages — fail. Client applications retry automatically, generating more requests. The retry storm, combined with cache TTL expiry accelerating misses, increases the rate of 502 errors over time. Rolling back the Lambda@Edge function version and waiting for CloudFront propagation (5–15 minutes) resolves the issue.

**Chain 3: WAF Managed Rule Update → False Positive Blocks → Regional Service Degradation**
AWS pushes an automatic update to an AWS Managed Rules rule group that the CloudFront distribution subscribes to. The new rule version incorrectly classifies legitimate API request payloads (e.g., a JSON field with a common SQL keyword) as `SQLi_BODY` injection attempts. A large percentage of POST requests from production clients are blocked with HTTP 403. The application shows authentication failures or empty data sets where blocked API calls were used. Detection is delayed because HTTP 403 looks like an authorization issue rather than a WAF block. Resolution: set the specific managed rule to `COUNT` mode temporarily: `aws wafv2 update-web-acl` with the rule's `OverrideAction: Count`, then open a support case with AWS to fix the rule.

## Partial Failure Patterns

- **Static assets fine, API calls failing**: Lambda@Edge error on origin request hook for API paths only. Static assets are cached and served from edge; API paths (cache miss + Lambda@Edge) return 502.
- **Most regions fast, one region slow**: Regional CloudFront PoP issue or regional origin latency problem. Users in the affected geography experience high latency; others are unaffected.
- **HTTPS working, custom error pages broken**: Custom error page S3 origin has incorrect permissions. Main content is served fine; error pages (404, 500 custom HTML) show CloudFront's default error page instead.
- **Cache hit rate normal, some paths always miss**: Over-aggressive query string forwarding for specific paths. Most paths cache normally; paths with varying query parameters never hit the cache.
- **WAF blocking some users, not others**: IP-based or geo-based WAF rule partially matching. Users from certain ASNs, countries, or with certain User-Agent strings are blocked while others succeed.
- **Distribution deployed, changes not live everywhere**: CloudFront propagation incomplete. Some edge PoPs have the new config; others are still serving old behavior. Typically resolves within 5–15 min but can take up to 30 min.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|---------|
| Cache HIT response time (P99, small object) | < 10 ms | 10–50 ms | > 50 ms |
| Cache MISS total latency (origin + edge, P99) | < 200 ms | 200 ms–1 s | > 1 s |
| Lambda@Edge execution time (viewer request) | < 5 ms | 5–50 ms | > 50 ms (approaching 5 s limit) |
| CloudFront Functions execution time | < 1 ms | 1–5 ms | > 5 ms (nearing 1 ms hard limit) |
| Distribution config propagation time | < 5 min | 5–15 min | > 30 min |
| Cache invalidation propagation time | < 30 s | 30 s–3 min | > 3 min |
| SSL/TLS handshake time (P99) | < 50 ms | 50–200 ms | > 200 ms |
| Origin Shield → Origin latency (P99) | < 100 ms | 100–500 ms | > 500 ms |

## Capacity Planning Indicators

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Monthly data transfer (TB) | Growing > 20% month-over-month | Review cache hit ratio; plan for bandwidth cost optimization | 1 month |
| Lambda@Edge invocations/month | Approaching function concurrency limits (1,000/region) | Request Lambda concurrency limit increase; optimize to reduce invocations | 2 weeks |
| Origin request rate (cache misses) | Growing > 30% week-over-week | Increase TTLs; improve cache key efficiency; add Origin Shield | 1 week |
| WAF request units/month | > 80% of provisioned capacity | Upgrade WAF tier; optimize rule count and complexity | 2 weeks |
| CloudFront Functions invocations | > 2M/day (approaching free tier) | Review if Functions logic is necessary on every request | 1 week |
| Cache invalidation requests | > 500/day (cost concern at $0.005/path) | Switch to versioned URLs (cache busting) instead of invalidations | 1 week |
| Distribution count | > 200 (service limit) | Consolidate distributions; use path-based routing within single distribution | 1 month |
| ACM certificate count | > 1,000 per region | Audit and delete unused certificates; use wildcard certificates | 1 month |

## Diagnostic Cheatsheet

```bash
# Get distribution summary including status and domain
aws cloudfront get-distribution --id <dist-id> --query 'Distribution.{Status:Status,Domain:DomainName,State:DistributionConfig.Enabled}'

# Check 5xx error rate for the last hour
aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name 5xxErrorRate --dimensions Name=DistributionId,Value=<dist-id> --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 300 --statistics Average

# Get cache hit rate for the last hour
aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name CacheHitRate --dimensions Name=DistributionId,Value=<dist-id> --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 300 --statistics Average

# Invalidate all cache (emergency)
aws cloudfront create-invalidation --distribution-id <dist-id> --paths "/*"

# Check ACM certificate expiry dates in us-east-1
aws acm list-certificates --region us-east-1 --query 'CertificateSummaryList[*].{ARN:CertificateArn,Domain:DomainName}' && aws acm describe-certificate --certificate-arn <arn> --region us-east-1 --query 'Certificate.{NotAfter:NotAfter,Status:Status,RenewalStatus:RenewalSummary.RenewalStatus}'

# Sample recent WAF blocked requests
aws wafv2 get-sampled-requests --web-acl-arn <arn> --rule-metric-name <rule-name> --scope CLOUDFRONT --time-window StartTime=$(date -u -d '30 minutes ago' +%s),EndTime=$(date -u +%s) --max-items 50 --region us-east-1

# Check distribution propagation status (is deploy complete?)
aws cloudfront get-distribution --id <dist-id> --query 'Distribution.{Status:Status,LastModified:DistributionConfig.HttpVersion}'

# Test specific path response headers (cache debug)
curl -sI https://<domain>/<path> | grep -E 'x-cache|age|cache-control|content-type|via'

# List all Lambda@Edge associations for a distribution
aws cloudfront get-distribution-config --id <dist-id> --query 'DistributionConfig.DefaultCacheBehavior.LambdaFunctionAssociations'

# Check CloudFront IP ranges (useful for origin security group rules)
curl -s https://ip-ranges.amazonaws.com/ip-ranges.json | python3 -c "import sys,json; data=json.load(sys.stdin); [print(p['ip_prefix']) for p in data['prefixes'] if p['service']=='CLOUDFRONT']" | head -20
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Request Success Rate (non-4xx client errors) | 99.95% | 1 - ((5xxErrorRate + WAF 403 rate) / total requests) | 21.6 min/month | Burn rate > 14.4× |
| Cache Hit Rate | ≥ 80% | `CacheHitRate` metric, 5-min average | N/A (performance SLO) | Alert if 1h average drops below 60% |
| Edge Latency P99 (cache HIT) | ≤ 50 ms | CloudFront access log `time-taken` for `Hit` result types | N/A (latency budget) | Alert if P99 > 100 ms for 15 min |
| SSL Certificate Validity | 100% unexpired | ACM certificate `NotAfter` monitored daily | 0 (any expiry is SLO breach) | Alert at 30-day expiry; page at 7-day expiry |

## Configuration Audit Checklist

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| HTTPS enforced (HTTP redirected) | `aws cloudfront get-distribution-config --id <id> --query 'DistributionConfig.DefaultCacheBehavior.ViewerProtocolPolicy'` | `redirect-to-https` or `https-only` |
| ACM certificate in us-east-1 and valid | `aws acm describe-certificate --certificate-arn <arn> --region us-east-1 --query 'Certificate.Status'` | `ISSUED` and `NotAfter` > 30 days |
| WAF attached to distribution | `aws cloudfront get-distribution-config --id <id> --query 'DistributionConfig.WebACLId'` | Non-empty WAF ACL ARN |
| S3 origins use OAC (not public) | `aws cloudfront get-distribution-config --id <id> --query 'DistributionConfig.Origins.Items[*].S3OriginConfig'` | `OriginAccessControlId` set; no public S3 bucket policy needed |
| Origin Shield enabled for high-traffic distributions | `aws cloudfront get-distribution-config --id <id> --query 'DistributionConfig.Origins.Items[*].OriginShield'` | `Enabled: true` with appropriate region for high-traffic origins |
| Access logging enabled | `aws cloudfront get-distribution-config --id <id> --query 'DistributionConfig.Logging'` | `Enabled: true`; bucket and prefix set |
| Geo-restrictions match policy | `aws cloudfront get-distribution-config --id <id> --query 'DistributionConfig.Restrictions'` | Matches geographic compliance requirements |
| Default root object set | `aws cloudfront get-distribution-config --id <id> --query 'DistributionConfig.DefaultRootObject'` | `index.html` (or appropriate) — prevents directory listing |
| Custom error pages configured | `aws cloudfront get-distribution-config --id <id> --query 'DistributionConfig.CustomErrorResponses'` | 404, 403, 500 mapped to custom error pages |
| TLS minimum protocol version | `aws cloudfront get-distribution-config --id <id> --query 'DistributionConfig.ViewerCertificate.MinimumProtocolVersion'` | `TLSv1.2_2021` or newer |

## Log Pattern Library

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `x-edge-result-type: Error` with `sc-status: 502` | Critical | Lambda@Edge function error or origin connection failure | Check Lambda@Edge logs; roll back function if recently deployed |
| `x-edge-result-type: Error` with `sc-status: 503` | Critical | Origin returned 503 or is unreachable | Check origin health; escalate to origin team |
| `x-edge-result-type: Error` with `sc-status: 504` | High | Origin response timeout (default 30 s) | Increase origin response timeout in distribution config; investigate origin slowness |
| `x-edge-result-type: Miss` at unexpectedly high rate | Medium | Low cache hit rate; TTLs too short or cache keys too broad | Review `Cache-Control` headers; optimize cache key policy |
| `x-edge-result-type: RefreshHit` | Info | Conditional request to origin for stale content | Normal; indicates TTL-based revalidation is working |
| `cs-method: OPTIONS` with `sc-status: 403` | Medium | CORS preflight blocked by WAF or missing CORS config | Add WAF exception for OPTIONS method; verify CORS headers in CloudFront |
| `sc-status: 403` with `x-edge-result-type: Error` | High | WAF block, geo restriction, or signed URL/cookie requirement | Check WAF logs; verify request meets distribution access requirements |
| `sc-bytes: 0` with `sc-status: 200` | Medium | Empty response from origin | Check origin for silent failures; add response validation in Lambda@Edge |
| `time-taken: > 5` (seconds) in access log | High | Lambda@Edge approaching 5 s execution limit or origin very slow | Profile Lambda@Edge; add `console.time` instrumentation |
| `ssl_protocol: TLSv1` or `TLSv1.1` | High | Client using deprecated TLS version | Update `MinimumProtocolVersion` to `TLSv1.2_2021`; advise clients to update |
| `x-forwarded-for` showing unusual IP concentration | Medium | Potential DDoS or scraping from single IP/range | Add rate-based WAF rule; consider AWS Shield Advanced |
| CloudTrail: `UpdateDistribution` by unexpected IAM principal | High | Unauthorized CloudFront config change | Review change diff; revert if unauthorized; audit IAM policy |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| HTTP 502 (from CloudFront) | CloudFront received invalid response from origin or Lambda@Edge error | Affected path returns error | Roll back Lambda@Edge; check origin validity |
| HTTP 503 (from CloudFront) | Origin unavailable or returned 503 | Cache misses fail; cached content still served | Fix origin; enable custom error caching |
| HTTP 504 (from CloudFront) | Origin response timeout | Cache misses timeout | Increase origin timeout setting; fix origin slowness |
| HTTP 403 (x-edge-result-type: Error) | WAF block, geo restriction, or signed URL required | Request blocked | Check WAF logs; verify access requirements |
| HTTP 403 (from S3 origin) | S3 bucket policy rejects CloudFront OAC | S3-backed content unavailable | Fix S3 bucket policy to allow CloudFront OAC principal |
| HTTP 301/302 loop | CloudFront and origin both redirecting HTTP↔HTTPS | Infinite redirect | Set `ViewerProtocolPolicy: redirect-to-https`; disable origin redirect |
| `Distribution: InProgress` | Config change propagating to edge PoPs | New config not yet active globally | Wait 5–15 minutes; check `Status: Deployed` |
| `InvalidationError` | Cache invalidation failed | Stale content may persist | Retry invalidation; check path format (must start with `/`) |
| `LambdaFunctionAssociation error` | Lambda@Edge function not deployable | Cannot update distribution | Ensure Lambda is in `us-east-1`; function must have `AmazonDynamoDB` compatible execution role |
| `CertificateNotFound` | ACM cert ARN not found in us-east-1 | Distribution cannot be updated | Re-issue certificate in us-east-1; update distribution |
| `SSL_ERROR_RX_RECORD_TOO_LONG` (client) | HTTP traffic hitting HTTPS port | TLS negotiation failure | Verify port 443 is in use; check `ViewerProtocolPolicy` |
| WAF `BLOCK` action (terminatingRule) | Request matched a WAF block rule | Request returns 403 | Add rule exception; switch to COUNT for investigation |

## Known Failure Signatures

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Lambda@Edge 502 Storm | 5xxErrorRate spikes; OriginLatency drops to 0 | `x-edge-result-type: Error`, `sc-status: 502` | High5xxErrorRate | Lambda@Edge function exception | Roll back Lambda function version immediately |
| WAF False Positive Flood | BlockedRequests spikes; AllowedRequests drops; 403 rate > 20% | WAF logs show single rule `terminatingRuleId` for all blocks | WAFBlockRateSurge | AWS managed rule update or overly broad custom rule | Set rule to COUNT mode; add rule exception |
| Cert Expiry Cliff | SSL handshake errors from all regions; HTTPS completely down | Client-side SSL errors; no CloudFront access logs (connection pre-log) | SSLCertificateExpiringSoon | ACM certificate expired; auto-renew failed | Re-issue cert; update distribution; propagate |
| Cache Cascade Miss | CacheHitRate drops from 90% to 10%; OriginLatency P99 spikes | All `x-edge-result-type: Miss`; `Vary: *` in response headers | CacheHitRateLow | Origin returning `Vary: *` or `Cache-Control: no-store` | Remove/suppress `Vary: *`; check origin config |
| Stale Content Incident | User complaints about old content; `Age: 86400` in responses | `x-edge-result-type: Hit`; high `Age` values | None (silent) | TTL too long; deployment without invalidation | Create invalidation for affected paths |
| Origin Overload (Cold) | OriginLatency P99 spikes; origin 503 rate rises | `sc-status: 503` from origin; CloudFront origin timeout logs | High5xxErrorRate | Cache hit rate dropped; origin flooded with misses | Warm cache; reduce origin concurrency; add Origin Shield |
| Distribution Deploy Stuck | Distribution status `InProgress` > 30 min | CloudTrail shows `UpdateDistribution` not completing | DistributionPropagationTimeout | CloudFront propagation issue | Contact AWS Support; do not make additional config changes |
| Geo-Block Bypass | Unexpected traffic from blocked countries in access logs | `cs(Country)` field shows blocked country values | Geo Restriction Alert | Geographic restriction not applied to all paths/behaviors | Add geo-block to all cache behaviors including default |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `HTTP 502 Bad Gateway` | Fetch API, Axios, curl | CloudFront cannot connect to origin, or Lambda@Edge threw an unhandled exception | Check `x-cache: Error from cloudfront` header; check Lambda@Edge logs in us-east-1 | Fix Lambda@Edge error; verify origin security group allows CloudFront IPs |
| `HTTP 503 Service Unavailable` | Fetch API, Axios | Origin returned 503 to CloudFront; origin overloaded | `curl -I https://<domain>` — check `x-cache` and `x-amz-cf-pop` headers; test origin directly | Scale origin (add capacity); enable Origin Shield to reduce origin request rate |
| `HTTP 504 Gateway Timeout` | Fetch API, curl | Origin did not respond within CloudFront's origin response timeout (default 30s) | Measure origin directly: `curl -w "%{time_total}" https://<origin-alb>/path` | Reduce origin response time; increase CloudFront `OriginResponseTimeout` (max 60s) |
| `HTTP 403 Forbidden — Access Denied (CloudFront)` | Browser, Fetch API | WAF rule blocked the request, or geo restriction matched, or signed URL/cookie expired | Check `x-amz-cf-id` in response; query CloudFront access logs for `sc-status=403` | Review WAF rules for false positives; check geo restriction config; refresh signed URL |
| `SSL_ERROR_RX_RECORD_TOO_LONG` / TLS handshake failure | Browser | ACM certificate expired or wrong certificate associated with distribution | `openssl s_client -connect <domain>:443 -servername <domain> 2>&1 \| grep "Verify return code"` | Renew ACM certificate; confirm certificate is in us-east-1 for CloudFront |
| `NET::ERR_CERT_AUTHORITY_INVALID` | Browser | Certificate not issued by trusted CA; custom certificate not properly chained | `openssl s_client -connect <domain>:443 -showcerts` | Replace certificate with ACM-issued cert; verify certificate chain |
| `HTTP 404 from CloudFront (not origin)` | Fetch API | Custom error page configured; or path not matched by any cache behavior | Check `x-cache` and `x-amz-cf-pop`; confirm origin returns 404 or check behavior path patterns | Review cache behavior path patterns; check origin actually has the resource |
| Stale content returned despite recent deployment | Browser cache, Fetch API | TTL not expired; CloudFront serving cached old version | Check `Age` response header; `x-cache: Hit from cloudfront` | Create invalidation: `aws cloudfront create-invalidation --distribution-id <id> --paths "/*"` |
| `HTTP 429` from WAF | Fetch API, mobile SDK | AWS WAF rate-based rule triggered | CloudWatch → `AWS/WAFV2` → `BlockedRequests` metric | Raise rate limit threshold; add IP-based exception for trusted clients |
| Slow first byte, fast subsequent requests | Browser performance | Cache MISS on first request; subsequent requests are cache HITs | Check `x-cache` header; `Hit from cloudfront` vs `Miss from cloudfront` | Tune TTL; warm cache with pre-warming requests after deployment |
| `HTTP 413 Request Entity Too Large` | Fetch API (file upload) | CloudFront default body size limit (1 GB for non-streaming) exceeded | Check request body size; CloudFront logs for `413` status | Use S3 pre-signed URL for large uploads, bypassing CloudFront |
| Lambda@Edge function returns `502` with no body | Fetch API, browser | Lambda@Edge response object malformed (missing `statusCode`, headers not lowercase) | `aws logs filter-log-events --log-group-name /aws/lambda/us-east-1.<fn-name> --filter-pattern ERROR` | Fix response object structure; ensure all header names are lowercase |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Cache hit rate declining | `CacheHitRate` drifting from 90% → 70% over weeks as query string or header variability increases | `aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name CacheHitRate --dimensions Name=DistributionId,Value=<id>` | 2–3 weeks | Audit cache key policy; remove unnecessary query strings/headers from cache key |
| Origin latency P99 creep | `OriginLatency` P99 rising 10–20% per week as backend grows | CloudFront metrics → `OriginLatency` P99 trend in CloudWatch | 2–4 weeks | Profile origin performance; scale origin; enable Origin Shield |
| Lambda@Edge memory footprint growth | Lambda@Edge invocation duration rising; cold starts more frequent | `aws logs filter-log-events --log-group-name /aws/lambda/us-east-1.<fn> --filter-pattern "Init Duration"` | 2–4 weeks | Audit Lambda@Edge code for memory leaks; refactor to CloudFront Functions for lightweight logic |
| ACM certificate expiry drift | Certificate `days_until_expiry` falling; auto-renew may fail if DNS validation records removed | `aws acm describe-certificate --certificate-arn <arn> --query 'Certificate.NotAfter'` | 30–60 days | Verify CNAME validation records still present in Route 53; trigger manual renewal |
| WAF rule false-positive rate rising | `BlockedRequests` rate creeping up; user complaints about sporadic 403s | `aws wafv2 get-sampled-requests --web-acl-arn <arn> --rule-metric-name <rule> --scope CLOUDFRONT --time-window StartTime=<t>,EndTime=<t> --max-items 100` | 1–2 weeks | Review WAF rule matches; tighten rule scope; add IP allowlist for known clients |
| Distribution config change propagation time increasing | `UpdateDistribution` taking longer to reach `Deployed` status over successive deploys | CloudTrail → filter for `UpdateDistribution` events; note time from API call to `Deployed` status | N/A (ad-hoc) | Minimize frequency of full distribution updates; use origin groups and cache behavior ordering |
| S3 origin 403 rate rising from missing objects | `4xxErrorRate` slowly rising as application deletes objects without invalidation | `aws logs filter-log-events --log-group-name /aws/cloudfront/<id> --filter-pattern '"403"'` | 1–2 weeks | Audit S3 object lifecycle; implement invalidation on object deletion |
| Viewer-side TLS deprecation warnings | Browser console TLS 1.0/1.1 deprecation warnings; some clients failing | `aws cloudfront get-distribution --id <id> --query 'Distribution.DistributionConfig.ViewerCertificate.MinimumProtocolVersion'` | 2–4 weeks | Set `MinimumProtocolVersion` to `TLSv1.2_2021` in distribution |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# CloudFront Full Health Snapshot
# Usage: export CF_DIST_ID="E1EXAMPLE"; export DOMAIN="example.com"; ./cf-health-snapshot.sh

echo "=== CloudFront Health Snapshot: $(date -u) ==="
echo "Distribution: $CF_DIST_ID"

echo ""
echo "--- Distribution Status ---"
aws cloudfront get-distribution --id $CF_DIST_ID \
  --query 'Distribution.{Status:Status,DomainName:DomainName,Enabled:DistributionConfig.Enabled}' \
  --output table

echo ""
echo "--- CloudFront Metrics (last 5 min) ---"
for METRIC in 5xxErrorRate 4xxErrorRate CacheHitRate; do
  VAL=$(aws cloudwatch get-metric-statistics \
    --namespace AWS/CloudFront \
    --metric-name $METRIC \
    --dimensions Name=DistributionId,Value=$CF_DIST_ID \
    --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
    --period 300 --statistics Average \
    --query 'Datapoints[0].Average' --output text 2>/dev/null)
  echo "$METRIC: ${VAL:-N/A}%"
done

echo ""
echo "--- SSL Certificate Expiry ---"
aws acm list-certificates --certificate-statuses ISSUED \
  --query 'CertificateSummaryList[*].{Arn:CertificateArn,Domain:DomainName}' \
  --output table

echo ""
echo "--- Recent Distribution Changes (CloudTrail) ---"
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=UpdateDistribution \
  --max-results 5 \
  --query 'Events[*].{Time:EventTime,User:Username,Event:EventName}' \
  --output table 2>/dev/null

echo ""
echo "--- Live HTTP Check ---"
curl -sI "https://$DOMAIN/" | grep -E "HTTP/|x-cache|age:|x-amz-cf-pop"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# CloudFront Performance Triage — run when latency or error rates are elevated
# Usage: export CF_DIST_ID="E1EXAMPLE"; export LOG_GROUP="/aws/cloudfront/$CF_DIST_ID"; ./cf-perf-triage.sh

echo "=== CloudFront Performance Triage: $(date -u) ==="

echo ""
echo "--- OriginLatency P99 (last 15 min) ---"
aws cloudwatch get-metric-statistics \
  --namespace AWS/CloudFront \
  --metric-name OriginLatency \
  --dimensions Name=DistributionId,Value=$CF_DIST_ID \
  --start-time $(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-15M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 900 --statistics p99 \
  --output table 2>/dev/null || echo "Check CloudFront metrics in console"

echo ""
echo "--- Recent 5xx Errors in Access Logs ---"
aws logs filter-log-events \
  --log-group-name "$LOG_GROUP" \
  --filter-pattern '"502" OR "503" OR "504"' \
  --start-time $(($(date +%s) - 900))000 \
  --limit 20 \
  --query 'events[*].message' \
  --output text 2>/dev/null | head -20

echo ""
echo "--- Lambda@Edge Errors (us-east-1) ---"
for FN_NAME in $(aws lambda list-functions --region us-east-1 --query 'Functions[?contains(FunctionName, `edge`)].FunctionName' --output text 2>/dev/null); do
  echo "Function: $FN_NAME"
  aws logs filter-log-events \
    --log-group-name "/aws/lambda/us-east-1.$FN_NAME" \
    --filter-pattern ERROR \
    --start-time $(($(date +%s) - 900))000 \
    --limit 5 \
    --query 'events[*].message' --output text 2>/dev/null | head -5
done

echo ""
echo "--- Cache Hit Rate Trend (last 1 hour, 5-min intervals) ---"
aws cloudwatch get-metric-statistics \
  --namespace AWS/CloudFront \
  --metric-name CacheHitRate \
  --dimensions Name=DistributionId,Value=$CF_DIST_ID \
  --start-time $(date -u -d '60 minutes ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-60M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 --statistics Average \
  --query 'sort_by(Datapoints, &Timestamp)[*].{Time:Timestamp,HitRate:Average}' \
  --output table 2>/dev/null
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# CloudFront Distribution Configuration and Resource Audit
# Usage: export CF_DIST_ID="E1EXAMPLE"; ./cf-resource-audit.sh

echo "=== CloudFront Resource Audit: $(date -u) ==="

echo ""
echo "--- Distribution Configuration Summary ---"
aws cloudfront get-distribution-config --id $CF_DIST_ID \
  --query 'DistributionConfig.{Origins:Origins.Quantity,Behaviors:CacheBehaviors.Quantity,PriceClass:PriceClass,HTTPVersion:HttpVersion,WAFWebACLId:WebACLId}' \
  --output table

echo ""
echo "--- Origins and Their Protocols ---"
aws cloudfront get-distribution-config --id $CF_DIST_ID \
  --query 'DistributionConfig.Origins.Items[*].{Id:Id,Domain:DomainName,Protocol:CustomOriginConfig.OriginProtocolPolicy}' \
  --output table 2>/dev/null

echo ""
echo "--- Cache Behaviors (Path Patterns) ---"
aws cloudfront get-distribution-config --id $CF_DIST_ID \
  --query 'DistributionConfig.CacheBehaviors.Items[*].{Path:PathPattern,TTL:DefaultTTL,Compress:Compress,CachePolicyId:CachePolicyId}' \
  --output table 2>/dev/null

echo ""
echo "--- WAF Web ACL Association ---"
WAF_ACL=$(aws cloudfront get-distribution-config --id $CF_DIST_ID \
  --query 'DistributionConfig.WebACLId' --output text)
echo "WAF ACL: ${WAF_ACL:-None}"

echo ""
echo "--- ACM Certificates (us-east-1) ---"
aws acm list-certificates --region us-east-1 --certificate-statuses ISSUED \
  --query 'CertificateSummaryList[*].{Domain:DomainName,Arn:CertificateArn}' \
  --output table

echo ""
echo "--- Pending Invalidations ---"
aws cloudfront list-invalidations --distribution-id $CF_DIST_ID \
  --query 'InvalidationList.Items[?Status==`InProgress`].{Id:Id,Status:Status,Created:CreateTime}' \
  --output table
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Cache invalidation storm flooding origin | `CacheHitRate` drops to near 0%; origin 503 rate spikes immediately after invalidation | Check CloudWatch `Requests` vs `CacheHitRate` correlation; check for `/*` invalidation in CloudTrail | Throttle origin with Origin Shield; return `stale-while-revalidate` headers | Invalidate specific paths (not `/*`); use versioned asset filenames instead of invalidations |
| Lambda@Edge memory contention at edge PoP | Lambda@Edge P99 duration rising; occasional 502s from function timeout | `aws logs filter-log-events --log-group-name /aws/lambda/us-east-1.<fn> --filter-pattern "Max Memory Used"` | Reduce Lambda@Edge memory usage; move logic to CloudFront Functions | Keep Lambda@Edge < 128 MB; prefer CloudFront Functions for simple header manipulation |
| WAF rule over-blocking legitimate traffic | `BlockedRequests` > 5% of total; user-reported 403s from valid requests | `aws wafv2 get-sampled-requests --web-acl-arn <arn> --rule-metric-name <rule> --scope CLOUDFRONT` | Switch rule to Count mode to stop blocking while investigating | Test WAF rules in Count mode before enabling Block mode in production |
| Origin Shield absorbing but not caching (cache-busting headers) | Origin Shield requests = edge requests; Shield not reducing origin load | CloudFront access logs: `x-edge-detailed-result-type` showing `Miss` despite Origin Shield | Remove or normalize `Cache-Control: no-store` from origin responses for cacheable content | Ensure origin sets appropriate `Cache-Control` headers; configure shield behavior override |
| High-concurrency path bypassing cache (POST/PUT) | Origin latency P99 spikes during POST-heavy workloads; cache metrics unaffected | CloudFront logs: filter for `cs-method=POST`; count vs total requests | Route POSTs to origin directly without CloudFront; or use separate distribution for API | Use separate distributions for cacheable assets vs API; set API behaviors to `no-cache` |
| Geo restriction blocking legitimate users behind corporate proxy | Users in allowed countries see 403; proxy IP is in blocked country | CloudFront access logs: `cs(Country)` field for blocked 403s vs actual user country | Switch from country-based geo restriction to WAF-based IP allowlist | Use WAF instead of geo restriction for fine-grained control; test from proxy IPs |
| Multiple distributions competing for same ACM certificate quota | Certificate association failing; `TooManyDistributionsAssociatedToKeyGroup` error | `aws cloudfront list-distributions --query 'DistributionList.Items[*].{Id:Id,Aliases:Aliases.Items}'` | Consolidate distributions; use wildcard certificate | Use wildcard ACM certificate (`*.example.com`) to cover all distributions |
| S3 origin throttling from too many cache misses | `x-amz-request-id` 503 responses from S3 origin; S3 prefix-level throttling | S3 server access logs: filter for `503` responses; check request rate per key prefix | Enable Origin Shield to coalesce requests; spread objects across multiple S3 key prefixes | Enable Origin Shield for S3 origins; use CloudFront cache policies with generous TTLs |
| CloudFront Functions execution quota exhaustion | Viewer-facing 500 errors during traffic spikes; Functions billing metric at limit | CloudWatch: `FunctionExecutionErrors` and `FunctionThrottles` for the distribution | Switch compute-intensive logic to Lambda@Edge; optimize Function code | Keep CloudFront Functions under 1 ms execution time; profile with test events |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Origin ELB/ALB becomes unhealthy | CloudFront receives 5xx from origin → CloudFront returns 502/504 to all users → cache miss traffic gets errors (cached content continues serving) | All uncached routes; dynamic endpoints; API requests | CloudWatch `5xxErrorRate` rising; `OriginLatency` P99 spiking; `aws cloudfront get-distribution --id $DIST_ID --query 'Distribution.DomainName'` and `curl -I https://dist.cloudfront.net/api/health` returning 502 | Enable CloudFront custom error pages to serve graceful fallback; update origin to a healthy ALB if available; temporarily cache error responses |
| ACM certificate expiry in us-east-1 | HTTPS for all aliases on the distribution fails; browsers show `NET::ERR_CERT_DATE_INVALID` | All HTTPS traffic to all CNAMEs on the distribution | `aws acm describe-certificate --certificate-arn $CERT_ARN --region us-east-1 --query 'Certificate.{Status:Status,NotAfter:NotAfter}'`; CloudWatch ACM `DaysToExpiry` alert | Request new certificate in ACM and associate with distribution: `aws cloudfront update-distribution`; ACM auto-renewal should prevent this if DNS validation is configured |
| WAF Web ACL rule misconfiguration after update | Legitimate requests blocked → API clients receive 403 → dependent services cascade-fail | All traffic matching the mis-configured rule; potentially entire distribution | `aws wafv2 get-sampled-requests --web-acl-arn $WAF_ARN --rule-metric-name $RULE --scope CLOUDFRONT --time-window ...` shows legitimate traffic blocked; application error rate spikes | Switch the rule to `COUNT` action immediately: `aws wafv2 update-web-acl` with rule action changed to `Count`; investigate sampled requests before re-enabling Block |
| Lambda@Edge function unhandled exception on viewer-request | Every request returns 502; CloudFront cannot process viewer-request stage | 100% of traffic on routes with the Lambda@Edge trigger | `aws logs filter-log-events --log-group-name /aws/lambda/us-east-1.<fn-name> --filter-pattern "ERROR"` shows unhandled exceptions; CloudFront returns `Error 1001` | Disassociate Lambda@Edge from the distribution behavior: update the cache behavior to remove the Lambda@Edge ARN; redeploy with fixed code |
| S3 origin bucket policy blocks CloudFront OAC | CloudFront returns 403 for all S3-origin requests; cached content still served until TTL expires | All S3-origin cache misses; new content requests | CloudFront access logs: `x-edge-result-type=Error`; `sc-status=403`; `x-edge-detailed-result-type=AccessDenied` | Re-apply OAC bucket policy: `aws s3api put-bucket-policy --bucket $BUCKET --policy file://cloudfront-oac-policy.json` |
| CloudFront distribution disabled accidentally | All traffic returns `distributions are disabled` error; DNS resolves to CloudFront but no content served | All traffic to the distribution; entire website/API | `aws cloudfront get-distribution --id $DIST_ID --query 'Distribution.DistributionConfig.Enabled'` returns `false`; all requests return CloudFront error page | Re-enable distribution: `aws cloudfront update-distribution` with `Enabled: true` in the config; propagation takes ~10 min |
| High invalidation rate collapsing cache hit ratio | Frequent `/*` invalidations keep cache empty → origin overloaded → latency spikes → error rate rises | Origin; all dynamic and static content; origin auto-scaling may lag | CloudWatch `CacheHitRate` near 0%; origin request count spikes; `aws cloudfront list-invalidations --distribution-id $DIST_ID --query 'InvalidationList.Items[?Status==\`InProgress\`]'` shows queue depth | Pause invalidations; re-enable origin-based cache headers; use versioned filenames instead of path invalidations |
| Origin Shield regional outage | Origin Shield PoP unreachable → CloudFront edges cannot reach shield → origin receives full uncollapsed request volume | All origin Shield–enabled behaviors; origin must absorb direct cache-miss traffic | CloudWatch `OriginLatency` spikes for shield-enabled origins; origin access logs show request rate surge | Disable Origin Shield temporarily: update cache behavior to remove `OriginShieldEnabled`; origin will absorb direct traffic — ensure auto-scaling is active |
| Geo restriction rule change blocking large user segment | Users in newly restricted regions see 403; CDN-delivered app unusable for them | All users in affected regions; potentially large percentage of global traffic | CloudFront access logs: `cs(Country)` spike for 403 responses matching newly restricted countries; user reports from affected regions | Revert geo restriction configuration: `aws cloudfront update-distribution` to remove or restore the country list; propagation ~5–10 min |
| Response header policy stripping CORS headers | Frontend JavaScript receives CORS errors; API calls from browser fail; SPA breaks | All browser-based API calls to CloudFront-fronted endpoints | Browser console: `Access-Control-Allow-Origin` header missing; `curl -H "Origin: https://app.example.com" -I https://dist.cloudfront.net/api/` confirms missing CORS headers; correlate with response header policy change | Update or remove the response header policy from the distribution behavior: `aws cloudfront update-distribution`; add correct CORS headers to origin or CloudFront response headers policy |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| CloudFront distribution configuration update | Traffic interruption during propagation; edge PoPs serving stale config; ~10 min deployment window where behavior is inconsistent | 5–15 min after `update-distribution` API call | `aws cloudfront get-distribution --id $DIST_ID --query 'Distribution.Status'` shows `InProgress`; correlate error spike with deployment timing | Wait for `Deployed` status; if broken: immediately call `update-distribution` with previous config using `ETag` from pre-change `get-distribution-config` |
| Cache policy TTL increase | Previously short-lived content now cached for longer; stale data served after origin updates | Immediate for new cache entries; existing cache entries use old TTL | CloudFront access logs: `CacheHitRate` rising after policy change; application showing stale data correlating with TTL change timestamp | Perform targeted invalidation: `aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/api/v1/*"`; revert cache policy TTL |
| Origin domain name change | CloudFront cannot resolve new origin; all requests return `Error 523: Origin is unreachable` | Immediate | `curl -I https://dist.cloudfront.net/` returns 523; CloudTrail shows `UpdateDistribution` with origin domain change; `dig <new-origin-domain>` confirms DNS resolution issue | Revert origin domain name: `aws cloudfront update-distribution` with previous origin config; verify new origin DNS before changing |
| Lambda@Edge new version deployment | New function version has a bug; viewer-request or origin-request errors on all matching routes | Immediate on new version association | `aws logs filter-log-events --log-group-name /aws/lambda/us-east-1.<fn> --filter-pattern "Task timed out"` or `"Error"`; correlate with new function ARN in distribution config | Roll back to previous Lambda@Edge version ARN: `aws cloudfront update-distribution` with the previous function ARN; verify ARN version number |
| Adding new cache behavior with overlapping path pattern | Requests that previously hit the default behavior now match the new behavior with different settings | Immediate | CloudFront access logs: requests on expected path returning unexpected behavior (different cache headers, missing auth); correlate with behavior addition in CloudTrail | Reorder behaviors (more specific paths first) or delete the conflicting behavior; CloudFront evaluates behaviors in order, first match wins |
| Enabling HTTP/3 (QUIC) on existing distribution | Some clients that don't support HTTP/3 negotiation correctly experience connection failures | Immediate | Client error reports of connection failures from specific OS/browser versions; `curl --http3 https://yourdomain.com` to confirm; CloudFront logs show `cs-protocol=HTTP/3` with errors | Disable HTTP/3: `aws cloudfront update-distribution` setting `HttpVersion` back to `http2`; allow time for propagation |
| Changing Origin Protocol Policy from HTTPS-only to Match-Viewer | Origin receives HTTP requests from CloudFront for HTTP viewer connections; origin may not be listening on port 80 | Immediate | Origin access logs show HTTP connections; origin returns 400 or connection refused on port 80; CloudFront returns 502 | Revert Origin Protocol Policy to `https-only`; if origin must support HTTP, ensure it listens on port 80 |
| ACM certificate swap to new ARN | Certificate not yet propagated to all CloudFront edge PoPs; some users see old cert, others see new cert; if new cert is misconfigured, some see TLS errors | 15–30 min propagation | `aws cloudfront get-distribution --id $DIST_ID --query 'Distribution.Status'` shows `InProgress`; TLS verification errors from certain geographic regions during propagation | Do not swap certificates under active load; schedule during low-traffic window; verify new cert includes all required CNAMEs before association |
| Price class change (e.g., All → US/EU only) | Users in excluded regions now served from nearest available PoP, which may be far away; latency increases significantly | Immediate after propagation | CloudFront access logs: edge PoP codes (`x-edge-location`) changing for affected regions; latency increase in CloudWatch `OriginLatency` for affected geographies | Revert price class to `PriceClass_All`: `aws cloudfront update-distribution` with `PriceClass: PriceClass_All` |
| Removing Trusted Key Group from signed URLs config | Signed URL validation stops working; all signed URL requests return 403 | Immediate after distribution propagation | CloudFront access logs: `403` responses on previously working signed URL paths; `x-edge-detailed-result-type=InvalidSignature` or `MissingKey` in logs | Re-add the Key Group to the cache behavior: `aws cloudfront update-distribution` with `TrustedKeyGroups` restored |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Edge PoP serving stale cached content after invalidation failure | `aws cloudfront list-invalidations --distribution-id $DIST_ID --query 'InvalidationList.Items[].{Id:Id,Status:Status}'` | Some users see updated content; others see old content depending on which PoP they hit; inconsistent behavior by region | Customer confusion; incorrect data displayed; A/B consistency violations | Check if invalidation completed; create new invalidation for affected paths; verify with `curl -H "Pragma: akamai-x-check-cacheable" https://dist.cloudfront.net/path` and inspect `Age` header |
| CloudFront Functions deployed with stale logic after code push | `aws cloudfront describe-function --name $FN_NAME --stage LIVE --query 'FunctionSummary.FunctionMetadata.Stage'` | Some edge PoPs running new function; others running old function during propagation window (~1 min) | Inconsistent request transformation or auth enforcement during deployment | Ensure `aws cloudfront publish-function --name $FN_NAME --if-match $ETAG` completes before routing traffic; monitor `FunctionExecutionErrors` during propagation |
| Origin returning inconsistent cache-control headers across requests | `curl -I https://dist.cloudfront.net/page --header "Cache-Control:"` returns different TTLs on repeated calls | CloudFront caches some responses for long periods; others not at all; users get inconsistent fresh/stale mixes | Unpredictable caching behavior; some users permanently stuck on stale responses | Standardize `Cache-Control` headers at origin; override with CloudFront cache policy `DefaultTTL`; invalidate affected paths |
| Multiple CloudFront distributions with conflicting CNAMEs | `aws cloudfront list-distributions --query 'DistributionList.Items[*].{Id:Id,Aliases:Aliases.Items[]}'` | Route53 or DNS alias resolves to one distribution; traffic intended for another distribution goes to the wrong one | Wrong origin served; WAF/caching rules of wrong distribution applied | Remove duplicate CNAMEs from the incorrect distribution; ensure each CNAME exists on exactly one distribution; verify with `dig CNAME yourdomain.com` |
| WAF rules out of sync between development and production | `aws wafv2 list-rules --web-acl-id $PROD_ACL_ARN --scope CLOUDFRONT` vs dev ACL | Security rules enforced in prod but not dev; or prod missing a rule that exists in dev | Security gap or inconsistent blocking behavior between environments | Export WAF ACL config: `aws wafv2 get-web-acl --name $ACL_NAME --scope CLOUDFRONT --id $ACL_ID`; diff and sync environments; manage with Terraform |
| Lambda@Edge function version drift between edge PoPs | `aws lambda list-aliases --function-name $FN_NAME` shows multiple live aliases | Different edge PoPs running different Lambda@Edge versions during blue/green deployment; inconsistent behavior for users | Inconsistent auth, header manipulation, or routing behavior during rollout | Ensure only one Lambda@Edge ARN version is associated with the distribution behavior; complete rollout before releasing to production |
| S3 origin replication lag in cross-region setup | `aws s3api head-object --bucket $BUCKET --key path/to/object --region $SECONDARY_REGION` returns older ETag than primary | CloudFront serving stale content from replicated bucket before replication completes | Users in regions served by replicated S3 origin see old file versions | Invalidate CloudFront cache for the affected path; wait for S3 replication to complete; monitor with S3 replication metrics |
| Cache behavior path pattern order causing wrong behavior match | `aws cloudfront get-distribution-config --id $DIST_ID --query 'DistributionConfig.CacheBehaviors.Items[*].PathPattern'` | Requests matching `/api/v2/*` also matching `/api/*` if pattern order is wrong; wrong cache TTL, origin, or auth applied | Incorrect caching of API responses; auth bypass if less-restrictive behavior matched first | Reorder cache behaviors so more specific patterns come first; CloudFront evaluates in order and uses first match |
| Origin failover active/passive inconsistency | `aws cloudfront get-distribution-config --id $DIST_ID --query 'DistributionConfig.OriginGroups'` | Primary origin recovered but CloudFront still routing to secondary due to health check caching | Traffic hitting secondary origin longer than necessary; secondary may have different content or configs | Trigger origin group re-evaluation by generating a request to the primary origin directly; CloudFront re-evaluates origin health per its health check interval |
| Signed cookie inconsistency between CloudFront key pairs | Some users with valid signed cookies get 403 (key pair used to sign is not in trusted key group) | `x-edge-detailed-result-type=InvalidSignature` in CloudFront access logs; `aws cloudfront get-distribution-config --id $DIST_ID --query 'DistributionConfig.CacheBehaviors.Items[].TrustedKeyGroups'` | Users with valid sessions locked out | Verify signing key pair ID matches a key in the trusted key group; update Key Group to include all active signing keys |

## Runbook Decision Trees

### Decision Tree 1: 5xx Error Rate Spike from CloudFront Distribution

```
Is CloudWatch 5xxErrorRate > SLO threshold for distribution $CF_DIST_ID?
├── YES → Are errors CloudFront-generated (520-527 range) or origin-generated (500-503)?
│         Check: aws cloudfront get-distribution-config --id $CF_DIST_ID; examine access logs for sc-status + x-edge-result-type
│         ├── CLOUDFRONT-GENERATED (52x) → Is it 502/523 (origin unreachable)?
│         │                                 ├── YES → Root cause: origin down → Fix: aws cloudfront update-distribution to switch to secondary origin; check origin ELB/EC2 health
│         │                                 └── NO  → Is it 504 (origin timeout)? → Root cause: origin too slow → Fix: increase CloudFront origin timeout; scale origin capacity
│         └── ORIGIN-GENERATED (500-503)  → Check origin health directly: curl -I --resolve yourdomain.com:443:<ORIGIN_IP> https://yourdomain.com
│                                           ├── Origin unhealthy → Escalate to origin team with origin logs
│                                           └── Origin healthy → Root cause: CloudFront forwarding wrong Host header → Fix: verify Origin Custom Headers config in distribution
└── NO  → Check 4xxErrorRate: if elevated, check WAF BlockedRequests or geo-restriction denying legitimate traffic
```

### Decision Tree 2: Cache Hit Rate (CHR) Degradation

```
Is CacheHitRate below expected baseline (< 80% for static-heavy distributions)?
├── YES → Was there a recent invalidation? (check: aws cloudfront list-invalidations --distribution-id $CF_DIST_ID --query 'InvalidationList.Items[?Status==`Completed`]')
│         ├── YES (/* invalidation) → Root cause: global invalidation warming → Fix: pre-warm critical paths using edge PoP warmup script; CHR recovers as traffic re-fills cache
│         └── NO  → Are origin Cache-Control headers preventing caching? (check: curl -I https://yourdomain.com/<asset> | grep -i 'cache-control\|x-cache')
│                   ├── Cache-Control: no-cache/private → Root cause: origin headers bypassing cache → Fix: create CloudFront Cache Policy with TTL override; set DefaultTTL/MaxTTL in behavior
│                   └── Cache-Control allows caching → Is query string variance fragmenting the cache key?
│                       ├── YES → Root cause: high-cardinality query strings in cache key → Fix: create Cache Policy excluding tracking parameters; update behavior to use new policy
│                       └── NO  → Is there a Bypass Cache behavior rule matching too broadly? (check: distribution behaviors for Viewer Protocol Policy and cache behavior order) → Fix: reorder behaviors so specific paths match before wildcard catch-all
└── NO  → CHR healthy; check OriginLatency if P99 latency is the SLO concern
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Accidental `/*` cache invalidation loop | Script invalidating `/*` on every deployment; each invalidation costs $0.005/path after first 1000/month | `aws cloudfront list-invalidations --distribution-id $CF_DIST_ID --query 'InvalidationList.Items[*].{Status:Status,Paths:InvalidationBatch.Paths.Items}'` | $0.005 × request paths × deployments/month = unbounded cost | Stop the invalidation script; use versioned filenames for assets instead | Replace cache-busting invalidations with asset fingerprinting (hash in filename); invalidate specific paths only when necessary |
| Lambda@Edge in all 4 event triggers on high-traffic path | Lambda@Edge attached to viewer-request + viewer-response + origin-request + origin-response; billed per invocation | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.CacheBehaviors.Items[].LambdaFunctionAssociations'` | 4× Lambda invocations per request; significant compute cost on high-traffic paths | Remove unnecessary event trigger associations; migrate simple logic to CloudFront Functions ($0.10/M vs $0.60/M) | Use CloudFront Functions for header manipulation/redirects; reserve Lambda@Edge for complex logic only |
| Origin Shield enabled globally including low-traffic origins | Origin Shield enabled on all origins regardless of geographic distribution | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.Origins.Items[].OriginShield'` | Additional $0.0080/10K requests for Origin Shield pass-through; unnecessary cost for single-region origins | Disable Origin Shield for origins co-located with the primary edge PoP | Enable Origin Shield only when origin is distant from majority of edge PoPs; review cost vs. cache-miss reduction |
| Wildcard distribution serving unexpected high-traffic subdomain | *.example.com distribution inadvertently serving a high-traffic new subdomain | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.Aliases'`; CloudWatch `Requests` metric spike | Bandwidth charges billed to CloudFront budget; unexpected bill | Add explicit CNAME binding on new subdomain to correct distribution | Audit CloudFront aliases monthly; do not use wildcard CNAMEs unless intentional |
| Uncompressed large binary serving at high volume | Origin serving uncompressed responses (e.g., JSON without gzip) through CloudFront | CloudWatch `BytesDownloaded` per request = avg response size; check `x-cache` header for `Compress` status | Bandwidth billed at CloudFront data transfer rates; higher latency | Enable CloudFront Compress Objects Automatically in distribution behavior config | Set `Compress = true` in all CloudFront behaviors; ensure origin sends `Accept-Encoding: gzip` forwarded |
| Real-time log delivery to Kinesis causing overrun | Real-time log delivery at 100% sampling rate on multi-billion-request distribution | Check Kinesis Data Stream shard count and CloudFront real-time log config: `aws cloudfront list-realtime-log-configs` | Kinesis shard costs + high PUT payload charges | Reduce sampling rate in real-time log config: `aws cloudfront update-realtime-log-config --sampling-rate 5` | Set sampling rate to 1-5% for real-time logs; use standard logs for full fidelity analysis at lower cost |
| Multiple redundant distributions for same domain | Teams creating new distributions rather than adding behaviors to existing ones | `aws cloudfront list-distributions --query 'DistributionList.Items[*].{Id:Id,Aliases:Aliases.Items,Status:Status}'` | Per-distribution fixed costs; ACM certificate quota consumption; management overhead | Consolidate distributions by adding behaviors/origins to primary distribution | Enforce distribution naming and ownership in IaC; require review before creating new distributions |
| WAF on CloudFront with very broad inspect scope | WAF rule inspecting body on all requests; CloudFront WAF charges per rule inspection | `aws wafv2 list-web-acls --scope CLOUDFRONT --region us-east-1`; check `RulesCount` and statement types | WAF charges $1/M requests × number of rules; body inspection doubles processing cost | Switch body inspection rules from ALL requests to specific URIs matching upload endpoints | Scope WAF rules narrowly; use rate-based rules only on attack-prone paths; review WAF cost breakdown monthly |
| Geo-restriction exception list not maintained | Allow-list of IPs for geo-restricted zones growing unbounded from contractor additions | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.Restrictions.GeoRestriction'` | Not a direct cost issue, but security risk; may inadvertently lift restrictions | Audit allow-list; remove stale entries; enforce TTL on IP exceptions | Track IP exception additions in git; set 90-day review reminder for all exception list entries |
| S3 origin without Origin Access Control (direct exposure) | S3 bucket publicly accessible; traffic bypassing CloudFront and hitting S3 directly (no cost savings from caching) | `aws s3api get-bucket-policy --bucket $BUCKET \| jq '.'` — check for `Principal: "*"` | S3 GET request charges; data transfer costs; security exposure | Enforce OAC: update S3 bucket policy to allow only CloudFront OAC service principal | Always configure Origin Access Control for S3 origins; block public S3 access via bucket policy and account-level S3 Block Public Access |
| CloudFront Function error causing fallthrough to origin | Buggy CloudFront Function returning error causes all viewer-request events to hit origin (losing cache benefit) | `aws cloudwatch get-metric-statistics --metric-name FunctionExecutionErrors --dimensions Name=FunctionName,Value=$CF_FUNCTION_NAME` | Cache bypassed; origin overloaded; latency and cost increase | Disable the CloudFront Function association temporarily; roll back to last known-good version | Test CloudFront Functions with `aws cloudfront test-function` before associating with distribution |

## Latency & Performance Degradation Patterns
| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot shard / single CloudFront PoP overloaded | Requests from a specific region experiencing high latency; `x-amz-cf-pop` header shows same PoP on all slow requests | `aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name 5xxErrorRate --dimensions Name=DistributionId,Value=$CF_DIST_ID Name=Region,Value=Global --period 60 --statistics Average` | Anycast routing concentrating traffic to a single PoP at capacity | Enable CloudFront Origin Shield for the affected region; contact AWS support with PoP identifier from `x-amz-cf-pop` header |
| Connection pool exhaustion to ALB/EC2 origin | CloudFront returning `Error 502` or `Error 504`; origin ALB healthy but max connections exceeded | `aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB --metric-name RejectedConnectionCount --dimensions Name=LoadBalancer,Value=$ALB_ARN_SUFFIX --period 60 --statistics Sum` | CloudFront sending more concurrent connections than origin can handle; ALB max connection limit reached | Increase ALB target group max connections; add EC2 instances to origin; enable CloudFront Origin Shield to reduce origin request rate |
| Lambda@Edge GC / memory pressure | Lambda@Edge responses slow; CloudWatch `Duration` metric at P99 near 5000ms; occasional OOM | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Duration --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics p99` | Lambda@Edge function accumulating large memory objects between warm invocations | Reduce Lambda@Edge memory footprint; clear caches in the function handler exit path; increase Lambda memory allocation |
| Thread pool saturation in Lambda@Edge | Lambda@Edge cold starts increasing under burst traffic; concurrency limit reached | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name ConcurrentExecutions --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics Maximum` | Lambda@Edge concurrency limit reached; new requests queued or throttled | Request Lambda concurrency limit increase via AWS Service Quotas; implement CloudFront caching to reduce Lambda@Edge invocations |
| Slow origin TTFB due to uncached dynamic content | CloudFront `OriginLatency` metric high; `cf-cache-status: MISS` or `x-cache: Miss from cloudfront` on all requests | `aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name OriginLatency --dimensions Name=DistributionId,Value=$CF_DIST_ID Name=Region,Value=Global --period 60 --statistics p99` | Dynamic content with `no-cache` headers bypassing CloudFront; all requests proxied to slow origin | Add caching headers for cacheable dynamic content; implement cache behaviors per path pattern; use CloudFront Functions for header manipulation |
| CPU steal / noisy neighbor on Lambda@Edge | Lambda@Edge duration higher than expected with no code change; inconsistent latency across PoPs | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Duration --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics Average` | Lambda execution environment experiencing resource contention in multi-tenant Lambda infrastructure | Increase Lambda memory (more memory = more CPU allocation in Lambda); simplify Lambda@Edge logic; migrate simple logic to CloudFront Functions |
| Lock contention in CloudFront Function / Lambda@Edge global state | Race condition in Lambda@Edge accessing shared mutable global variable; intermittent incorrect response | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics Sum` | Lambda@Edge warm instances sharing global state with concurrent requests; race condition | Make Lambda@Edge functions stateless; use only immutable global constants; move mutable state to request-scoped variables |
| Cache invalidation serialization overhead | CloudFront invalidation API calls serializing; invalidation queue backing up | `aws cloudfront list-invalidations --distribution-id $CF_DIST_ID --query 'InvalidationList.Items[?Status==\`InProgress\`]'` | Too many concurrent invalidation requests; CloudFront serializing processing at 3,000 paths/distribution/second | Batch invalidations into fewer requests with wildcard paths (e.g. `/api/*`); use versioned filenames instead of cache busting via invalidation |
| Batch size misconfiguration in CloudFront Logs delivery | Standard log files delivered to S3 in very small batches causing excessive S3 PUT operations | `aws s3api list-objects-v2 --bucket $LOG_BUCKET --prefix cloudfront/ --query 'length(Contents)'` — check file count per hour | CloudFront delivering log files every few seconds instead of every 5 minutes due to high request volume | Switch from standard logs to real-time logs with Kinesis; use `sampling_rate` on real-time log config to reduce volume |
| Downstream dependency latency from Lambda@Edge fetch | Lambda@Edge performing external HTTP fetch on every viewer-request; slow API adds latency | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Duration --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics Maximum` | Lambda@Edge fetching external data (auth service, config API) without caching or circuit breaker | Cache external API responses in Lambda@Edge global scope with TTL; implement timeout + fail-open for non-critical external calls |

## Network & TLS Failure Patterns
| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on custom domain | Browsers report `ERR_CERT_DATE_INVALID`; CloudFront returns `400 Bad Request` for HTTPS | `aws acm describe-certificate --certificate-arn $CERT_ARN --region us-east-1 --query 'Certificate.NotAfter'` | ACM certificate not renewed (manual certificate) or ACM auto-renewal failed due to DNS validation record deletion | For ACM: re-validate certificate via DNS; for manual cert: upload renewed certificate to ACM then update CloudFront distribution |
| mTLS failure after origin certificate rotation | CloudFront returns `502 Bad Gateway`; origin logs show TLS handshake error after cert rotation | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.Origins.Items[] \| {Domain: .DomainName, SSLProtocols: .CustomOriginConfig.OriginSSLProtocols}'` | Origin's new TLS certificate uses a CA not trusted by CloudFront or has a mismatched hostname | Ensure origin certificate is signed by a publicly trusted CA; enable `MatchViewer` or `HTTPSOnly` on origin protocol policy |
| DNS resolution failure for origin | CloudFront returning `502 Bad Gateway`; CloudFront cannot resolve origin hostname | `dig $ORIGIN_HOSTNAME`; `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.Origins.Items[].DomainName'` | Origin hostname in CloudFront config deleted or mis-typed; DNS TTL cached stale NXDOMAIN | Update origin domain name in distribution config: `aws cloudfront update-distribution --id $CF_DIST_ID`; verify DNS entry exists |
| TCP connection exhaustion from CloudFront to origin | CloudFront returning `504 Gateway Timeout`; origin seeing max TCP connections from CloudFront IPs | `aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB --metric-name ActiveConnectionCount --period 60 --statistics Maximum` | CloudFront fan-out from hundreds of PoPs opening persistent connections to origin; origin TCP stack exhausted | Enable CloudFront Origin Shield to reduce number of CloudFront nodes connecting to origin; increase origin server `MaxClients` |
| Load balancer misconfiguration after CloudFront origin pool update | `503 Service Unavailable` after changing CloudFront origin to new ALB | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.Origins.Items[].CustomOriginConfig'` | New ALB not yet passing health checks; security group not allowing CloudFront prefix list IDs | Add CloudFront managed prefix list (`com.amazonaws.global.cloudfront.origin-facing`) to ALB security group ingress |
| Packet loss causing Lambda@Edge stream truncation | Large Lambda@Edge responses (streaming) being truncated; `Content-Length` mismatch | `curl -w "%{size_download}\n" https://yourdomain.com/large-response` — compare to expected size | MTU mismatch or packet loss between CloudFront edge and Lambda@Edge execution environment | Reduce Lambda@Edge response size; implement chunked transfer encoding; check Lambda@Edge region routing |
| MTU mismatch between CloudFront and VPN-connected origin | Large responses from origin silently truncated; only affects payloads > 1400 bytes | `ping -M do -s 1400 $ORIGIN_IP` — if fails, MTU mismatch | VPN or Direct Connect between CloudFront and origin using smaller MTU than standard Ethernet | Set `OriginCustomHeaders` to cap maximum response size; negotiate MSS clamping on VPN/DX connection; set origin server MTU to 1400 |
| Firewall rule change blocking CloudFront IP ranges | CloudFront requests to origin returning `Connection refused`; started after firewall change | `aws ec2 describe-security-groups --group-ids $SG_ID \| jq '.SecurityGroups[].IpPermissions'` | Security group rule for CloudFront managed prefix list removed or not updated after AWS IP range change | Re-add CloudFront prefix list: `aws ec2 authorize-security-group-ingress --group-id $SG_ID --ip-permissions '[{"IpProtocol":"tcp","FromPort":443,"ToPort":443,"PrefixListIds":[{"PrefixListId":"pl-3b927c52"}]}]'` |
| SSL handshake timeout on origin with Full encryption | CloudFront returning `525 SSL Handshake Failed`; origin using TLS 1.1 only | `openssl s_client -connect $ORIGIN:443 -tls1_2` — if fails, origin does not support TLS 1.2+ | Origin web server configured with TLS 1.0/1.1 only; CloudFront requiring TLS 1.2+ | Update origin TLS configuration to support TLS 1.2+; in CloudFront origin settings set minimum TLS version to `TLSv1.2_2021` |
| Connection reset from ALB after CloudFront idle timeout | Random `504` errors on long-running requests; ALB closing connection before CloudFront times out | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.Origins.Items[].ConnectionTimeout'` | ALB idle timeout (default 60s) shorter than CloudFront origin read timeout; connection closed mid-response | Increase ALB idle timeout: `aws elbv2 modify-load-balancer-attributes --load-balancer-arn $ALB_ARN --attributes Key=idle_timeout.timeout_seconds,Value=120` |

## Resource Exhaustion Patterns
| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| Lambda@Edge OOM kill | `Error: Runtime exited with error: signal: killed` in Lambda@Edge CloudWatch Logs; `OOMKilled` in metrics | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics Sum` | Lambda@Edge function loading large data structures into memory (JSON config, certificate bundles) | Increase Lambda@Edge memory allocation; stream large data instead of loading into memory; cache parsed data in global scope |
| Lambda@Edge concurrent execution limit | Lambda@Edge throttling at regional concurrency limit; `429 Too Many Requests` in viewer-response | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Throttles --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics Sum` | Lambda@Edge burst concurrency limit reached during traffic spike | Request concurrency limit increase via AWS Service Quotas; add CloudFront caching to reduce Lambda@Edge invocations per request |
| S3 origin disk full (N/A — managed S3) | Not applicable for S3 origins; monitor S3 bucket size for cost | `aws s3api list-objects-v2 --bucket $BUCKET --query 'sum(Contents[].Size)'` | S3 storage is unlimited but uncontrolled writes can cause cost overrun | Implement S3 lifecycle policies for automatic deletion of old objects; set S3 storage size budget alert |
| CloudFront access log storage full in S3 | S3 log bucket lifecycle policy not set; bucket growing unbounded | `aws s3api head-bucket --bucket $LOG_BUCKET`; `aws s3api get-bucket-lifecycle-configuration --bucket $LOG_BUCKET` | No S3 lifecycle policy on CloudFront log bucket; logs accumulating indefinitely | Set S3 lifecycle rule: `aws s3api put-bucket-lifecycle-configuration --bucket $LOG_BUCKET --lifecycle-configuration file://lifecycle.json` (expire after 30 days) | Always attach lifecycle policy to CloudFront log buckets at creation |
| Lambda@Edge file descriptor exhaustion | Lambda@Edge function failing with `EMFILE: too many open files` for functions making many HTTP requests | `aws cloudwatch filter-log-events --log-group-name /aws/lambda/us-east-1.$FUNCTION_NAME --filter-pattern "EMFILE"` | Lambda@Edge function opening HTTP connections without closing them in `finally` blocks | Ensure all `fetch()` and HTTP connections use try/finally with explicit close; reduce maximum concurrent outbound connections | Implement connection pooling in Lambda@Edge; use `AbortController` to timeout and clean up connections |
| WAF rule evaluation CPU throttle | WAF latency adding > 100ms to every request; CloudFront `5xxErrorRate` elevated during WAF processing | `aws wafv2 get-web-acl --id $WAF_ACL_ID --name $WAF_ACL_NAME --scope CLOUDFRONT --region us-east-1` — count rules and rule group sizes | Too many WAF rules with complex regex or body inspection on high-traffic distribution | Reduce WAF rule count; replace complex regex rules with AWS Managed Rules; move rate-based rules earlier in rule priority | Scope WAF body inspection rules to specific URI prefixes; benchmark WAF rule evaluation latency in staging |
| Kinesis Data Firehose shard exhaustion from real-time log delivery | Real-time log delivery to Kinesis falling behind; `GetRecords.IteratorAgeMilliseconds` growing | `aws cloudwatch get-metric-statistics --namespace AWS/Kinesis --metric-name WriteProvisionedThroughputExceeded --dimensions Name=StreamName,Value=$STREAM_NAME --period 60 --statistics Sum` | CloudFront real-time log throughput exceeds Kinesis stream shard capacity (1MB/s per shard) | Add Kinesis shards: `aws kinesis update-shard-count --stream-name $STREAM_NAME --target-shard-count $NEW_COUNT --scaling-type UNIFORM_SCALING` | Enable Kinesis on-demand mode for variable log volume; set sampling rate on real-time log config to reduce throughput |
| Network socket buffer exhaustion on Lambda@Edge host | Lambda@Edge internal networking error; `ENOBUFS` in CloudWatch logs | `aws cloudwatch filter-log-events --log-group-name /aws/lambda/us-east-1.$FUNCTION_NAME --filter-pattern "ENOBUFS"` | Lambda@Edge receiving large data from origin faster than function can consume it | Implement streaming response processing in Lambda@Edge rather than buffering full response body | Use Lambda response streaming (`awslambda.streamifyResponse`) for large response bodies |
| Ephemeral port exhaustion in Lambda@Edge for multi-fetch | Lambda@Edge making parallel outbound HTTP requests from a single warm execution environment | `aws cloudwatch filter-log-events --log-group-name /aws/lambda/us-east-1.$FUNCTION_NAME --filter-pattern "cannot assign requested address"` | Many concurrent outbound `fetch()` calls in a single Lambda execution consuming all ephemeral ports | Serialize or limit parallel outbound calls; use `Promise.all()` with concurrency limit; reuse HTTP connections | Limit parallel outbound connections per Lambda invocation; use connection pooling libraries |

## Distributed Transaction & Event Ordering Failures
| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation in Lambda@Edge cache write | Lambda@Edge writing to external cache (ElastiCache/DynamoDB) on viewer-request; duplicate trigger causes double-write | `aws cloudwatch filter-log-events --log-group-name /aws/lambda/us-east-1.$FUNCTION_NAME --filter-pattern "duplicate_key"` | Stale or conflicting cache entries causing users to receive incorrect cached responses | Add idempotency check in Lambda@Edge using DynamoDB conditional writes: `ConditionExpression: "attribute_not_exists(requestId)"` |
| Saga partial failure in Lambda@Edge + origin workflow | Lambda@Edge modifying auth state but origin API call fails; state inconsistent between edge and origin | `aws cloudwatch filter-log-events --log-group-name /aws/lambda/us-east-1.$FUNCTION_NAME --filter-pattern "ERROR" --start-time $EPOCH_MS` | User session or auth state inconsistent between CloudFront edge cache and origin | Implement compensating step in Lambda@Edge `catch` block; use SQS FIFO queue for reliable edge-to-origin state sync |
| Message replay causing stale cache poisoning | S3 event notification replayed; Lambda@Edge cache invalidation triggered for already-updated objects | `aws cloudfront list-invalidations --distribution-id $CF_DIST_ID --query 'InvalidationList.Items[?Status==\`Completed\`]'` — check for duplicate path invalidations | Cache invalidated unnecessarily; brief cache miss storm on origin | Implement idempotency in invalidation Lambda using DynamoDB to track invalidation request IDs | Use versioned S3 keys and corresponding versioned CloudFront paths instead of invalidations for cache busting |
| Cross-service deadlock between Lambda@Edge and DynamoDB | Lambda@Edge viewer-request acquiring DynamoDB lock; origin-request Lambda also acquiring same lock; timeout | `aws cloudwatch get-metric-statistics --namespace AWS/DynamoDB --metric-name SystemErrors --dimensions Name=TableName,Value=$TABLE --period 60 --statistics Sum` | Both Lambda@Edge invocations timeout; `502 Bad Gateway` for the request; DynamoDB lock released on timeout | Standardize lock acquisition order across Lambda@Edge event types; use DynamoDB optimistic locking (`version` attribute) instead of pessimistic locks |
| Out-of-order S3 event processing for CloudFront cache invalidation | S3 `ObjectCreated` and `ObjectDeleted` events processed in wrong order; CloudFront cache invalidated for a key that was just re-uploaded | `aws s3api head-object --bucket $BUCKET --key $KEY` — check `ETag` vs cached version | CloudFront serving 404 for an object that exists in S3; incorrect cache invalidation order | Implement sequence number in S3 object metadata; Lambda discards invalidation events with `sequence <= lastInvalidated` | Use S3 versioning and invalidate CloudFront using version-specific URLs to avoid ordering dependency |
| At-least-once SQS delivery duplicate triggering double invalidation | SQS message for CloudFront invalidation delivered twice; same paths invalidated twice causing double cache miss | `aws sqs get-queue-attributes --queue-url $QUEUE_URL --attribute-names ApproximateNumberOfMessages` — check for duplicate processing | Unnecessary cache miss storms; doubled origin load during invalidation window | Implement SQS FIFO queue with `MessageGroupId` per distribution ID for deduplication | Use SQS FIFO with `MessageDeduplicationId` set to `invalidation_path + timestamp_bucket` to prevent duplicate processing within 5-minute window |
| Compensating transaction failure in Lambda@Edge rollback | Lambda@Edge rollback step (remove from cache, restore redirect rule) fails after partial update | `aws cloudwatch filter-log-events --log-group-name /aws/lambda/us-east-1.$FUNCTION_NAME --filter-pattern "rollback_failed"` | CloudFront distribution in inconsistent state; some cache behaviors updated, others not | Manually re-apply full distribution config from last known-good snapshot; use `aws cloudfront update-distribution` with complete config | Store distribution config snapshots in S3 before each update; implement idempotent rollback in deployment Lambda |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor in Lambda@Edge shared execution env | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Duration --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics p99` — P99 duration high with no code change | All CloudFront requests processed by affected Lambda@Edge PoP experience added latency | No direct Lambda isolation in multi-tenant edge; mitigate by reducing Lambda@Edge compute complexity | Increase Lambda memory (more memory = dedicated vCPU); simplify Lambda@Edge logic; migrate header manipulation to CloudFront Functions (runs in dedicated process) |
| Memory pressure from adjacent Lambda@Edge warm instance | Lambda@Edge OOM errors appearing after previously stable; `aws cloudwatch filter-log-events --log-group-name /aws/lambda/us-east-1.$FUNCTION_NAME --filter-pattern "Runtime exited"` | Lambda@Edge invocations failing with OOM; CloudFront returning 502 for edge-processed requests | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics Sum` | Increase Lambda@Edge memory allocation; reduce global state in Lambda; split expensive per-PoP caching into CloudFront KVS |
| Disk I/O saturation at Kinesis log delivery | `aws cloudwatch get-metric-statistics --namespace AWS/Kinesis --metric-name WriteProvisionedThroughputExceeded --dimensions Name=StreamName,Value=$STREAM_NAME --period 60 --statistics Sum` — real-time log delivery failing | Other CloudFront distributions sharing the same Kinesis stream lose real-time log delivery | `aws kinesis update-shard-count --stream-name $STREAM_NAME --target-shard-count $NEW_COUNT --scaling-type UNIFORM_SCALING` | Use separate Kinesis streams per distribution for high-volume distributions; enable Kinesis on-demand mode |
| Network bandwidth monopoly from large cached object | `aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name BytesDownloaded --dimensions Name=DistributionId,Value=$CF_DIST_ID Name=Region,Value=Global --period 60 --statistics Sum` — one distribution consuming bulk of bandwidth | Other distributions on same account subject to account-level transfer quota pressure | No per-distribution bandwidth cap in CloudFront | Add CloudFront cache behavior for large objects to use a separate distribution; enable CloudFront access controls to prevent hotlinking |
| Connection pool starvation at shared ALB origin | `aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB --metric-name RejectedConnectionCount --dimensions Name=LoadBalancer,Value=$ALB_ARN_SUFFIX --period 60 --statistics Sum` | All CloudFront distributions routing to shared ALB experiencing 502/504 | Add CloudFront Origin Shield to reduce origin fan-out; scale ALB target group | Separate high-traffic distributions to dedicated origins; enable CloudFront caching to reduce origin connection rate |
| Quota enforcement gap for CloudFront invalidations | `aws cloudfront list-invalidations --distribution-id $CF_DIST_ID \| jq '.InvalidationList.Items \| length'` — approaching 3,000 paths/distribution/second | All invalidation batches from multiple tenants serialized; cache stale longer than expected | No per-tenant invalidation quota; enforce at application layer | Use wildcard invalidation paths (`/api/*`) to batch multiple paths; implement per-tenant invalidation rate limiting in deployment pipeline |
| Cross-tenant data leak via shared S3 origin | Multiple tenants' content in same S3 bucket without prefix isolation; CloudFront cache behavior misconfiguration serves wrong tenant's content | Wrong tenant's private content cached and served to other tenants | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.CacheBehaviors.Items[] \| {PathPattern, OriginId, CachePolicyId}'` | Immediately invalidate the affected paths; add `tenant_id` path prefix to all S3 objects; enforce separate cache behaviors per tenant |
| Rate limit bypass for S3 origin via CloudFront | Application rate limiter applied at ALB/origin bypassed because requests served from CloudFront cache | Cached responses served to users who should be rate-limited; business rule circumvented | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.CacheBehaviors.Items[] \| select(.PathPattern \| test("api")) \| .CachePolicyId'` | Ensure API paths with rate-limiting use `CachingDisabled` cache policy; apply rate limiting at CloudFront WAF layer for all paths |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure for CloudFront metrics | CloudWatch dashboard for `AWS/CloudFront` namespace shows no data | CloudFront metrics only available in `us-east-1`; dashboard configured in wrong region | `aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name Requests --dimensions Name=DistributionId,Value=$CF_DIST_ID Name=Region,Value=Global --period 300 --statistics Sum --region us-east-1` | Always query CloudFront metrics in `us-east-1`; update Grafana data source to use `us-east-1` for `AWS/CloudFront` namespace |
| Trace sampling gap in Lambda@Edge cross-region | Lambda@Edge X-Ray traces missing for PoPs outside `us-east-1` | X-Ray sampling rule not applied globally; Lambda@Edge executes in multiple regions but X-Ray segments may not propagate | `aws lambda get-function-configuration --function-name us-east-1.$FUNCTION_NAME --region us-east-1 \| jq '.TracingConfig'` | Enable active X-Ray tracing: `aws lambda update-function-configuration --function-name $FUNCTION_NAME --tracing-config Mode=Active --region us-east-1`; set global sampling rule |
| Log pipeline silent drop for CloudFront access logs | S3 log bucket showing no new log files; access log delivery silently stopped | S3 bucket ACL for `awslogsdelivery` principal removed; or bucket in different region from log delivery expectation | `aws s3api get-bucket-acl --bucket $LOG_BUCKET \| jq '.Grants[] \| select(.Grantee.URI \| test("awslogsdelivery"))'` | Re-grant log delivery: `aws s3api put-bucket-acl --bucket $LOG_BUCKET --grant-write URI=http://acs.amazonaws.com/groups/s3/LogDelivery --grant-read-acp URI=http://acs.amazonaws.com/groups/s3/LogDelivery` |
| Alert rule misconfiguration on CloudFront 5xx rate | `5xxErrorRate` alarm never fires during an origin outage | Alarm dimensioned on `Region=Global` but actual metric data published without that dimension | `aws cloudwatch describe-alarms --alarm-names $ALARM_NAME \| jq '.MetricAlarms[0].Dimensions'` — verify dimension names | Update alarm dimensions to match how CloudFront publishes: `Name=DistributionId,Value=$CF_DIST_ID` + `Name=Region,Value=Global`; test with `aws cloudwatch set-alarm-state` |
| Cardinality explosion blinding real-time logs | Kinesis stream backed up; real-time log consumer OOM | CloudFront real-time logs with full URL path including query strings creating millions of unique log records | `aws cloudwatch get-metric-statistics --namespace AWS/Kinesis --metric-name GetRecords.IteratorAgeMilliseconds --dimensions Name=StreamName,Value=$STREAM_NAME --period 60 --statistics Average` | Add `sampling_rate` to real-time log config: `aws cloudfront update-realtime-log-config --sampling-rate 1` (1% sampling); remove high-cardinality fields from log config |
| Missing health endpoint for Lambda@Edge function | Lambda@Edge function is unhealthy (throwing exceptions) but CloudFront continues routing to it | CloudFront does not health-check Lambda@Edge functions; exceptions return 502 without alerting | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics Sum --region us-east-1` | Create CloudWatch alarm on Lambda@Edge `Errors > 0`; add `wcu-cloudfront-errors` metric filter; implement synthetic canary using CloudWatch Synthetics |
| Instrumentation gap in origin failover path | CloudFront failing over to secondary origin silently; no metric or alert on origin failover activation | CloudFront origin failover does not emit a dedicated CloudWatch metric; only 5xx rate reflects it indirectly | `aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name OriginLatency --dimensions Name=DistributionId,Value=$CF_DIST_ID Name=Region,Value=Global --period 60 --statistics p99` | Log `x-amz-cf-id` and `x-cache` response headers from origin; add CloudWatch alarm on secondary origin's `HealthyHostCount` dropping to zero |
| PagerDuty outage silencing CloudFront error alert | CloudFront 5xx rate critical but on-call not paged | PagerDuty integration key rotated; CloudWatch SNS subscription to PagerDuty endpoint returning 403 | `aws cloudwatch describe-alarm-history --alarm-name $ALARM_NAME --history-item-type Action \| jq '.AlarmHistoryItems[] \| {Timestamp, HistorySummary}'` — check if action was attempted | Update PagerDuty integration key in SNS HTTPS subscription; add backup email notification channel to alarm; test with `aws cloudwatch set-alarm-state --alarm-name $ALARM_NAME --state-value ALARM --state-reason test` |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Lambda@Edge Node.js runtime version upgrade | Lambda@Edge function errors after runtime upgrade; breaking API changes in new Node.js version | `aws lambda get-function-configuration --function-name us-east-1.$FUNCTION_NAME --region us-east-1 \| jq '.Runtime'`; `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=us-east-1.$FUNCTION_NAME --period 60 --statistics Sum --region us-east-1` | Update Lambda runtime back to previous version: `aws lambda update-function-configuration --function-name $FUNCTION_NAME --runtime nodejs18.x --region us-east-1`; redeploy with `aws cloudfront update-distribution` | Test Lambda@Edge against new Node.js runtime in `wrangler dev`-equivalent; pin runtime version in IaC; test in staging distribution first |
| CloudFront cache policy migration partial completion | Some cache behaviors using new cache policy; others still using legacy `ForwardedValues`; inconsistent caching behavior | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.CacheBehaviors.Items[] \| {PathPattern, CachePolicyId, ForwardedValues}'` | Revert to previous distribution config: `aws cloudfront update-distribution --id $CF_DIST_ID --distribution-config file://previous-config.json --if-match $ETAG` | Migrate all cache behaviors atomically in a single `UpdateDistribution` call; validate with `--dry-run` equivalent by checking config before submit |
| Rolling upgrade version skew for CloudFront Functions | New CloudFront Function version deployed but old version still running at some PoPs during propagation | `aws cloudfront describe-function --name $FUNCTION_NAME --stage LIVE \| jq '.FunctionSummary.FunctionMetadata.Stage'`; check deployment status | `aws cloudfront update-function --name $FUNCTION_NAME --function-code fileb://previous-function.js --function-config '{"Comment":"rollback","Runtime":"cloudfront-js-1.0"}'`; then `aws cloudfront publish-function --name $FUNCTION_NAME --if-match $ETAG` | CloudFront Functions propagation takes up to 2 minutes; canary-test in staging distribution before promoting to live |
| Zero-downtime origin migration gone wrong | Traffic switched to new origin but new origin not fully warmed; cache miss storm overwhelming new origin | `aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name 5xxErrorRate --dimensions Name=DistributionId,Value=$CF_DIST_ID Name=Region,Value=Global --period 60 --statistics Average` | Switch origin back: update CloudFront origin DomainName to previous origin; invalidate cache; `aws cloudfront create-invalidation --distribution-id $CF_DIST_ID --paths '/*'` | Warm new origin before switching; use CloudFront origin groups for gradual traffic migration; pre-populate origin cache before DNS cutover |
| Viewer protocol policy change breaking old HTTPS clients | Changing cache behavior `ViewerProtocolPolicy` from `allow-all` to `https-only`; old HTTP clients receiving 301 loop | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.CacheBehaviors.Items[] \| {PathPattern, ViewerProtocolPolicy}'` | Revert viewer protocol policy: `aws cloudfront update-distribution` with `ViewerProtocolPolicy: allow-all` for affected behavior | Test protocol policy change in staging; check for clients not following redirects; add CloudWatch alarm on 3xx rate spike |
| ACM certificate migration causing TLS version mismatch | Migrating from legacy ACM cert to new cert; new cert using TLS policy incompatible with old clients | `aws cloudfront get-distribution-config --id $CF_DIST_ID \| jq '.DistributionConfig.ViewerCertificate'` | Revert to previous certificate ARN: `aws cloudfront update-distribution` with old `ACMCertificateArn`; wait for propagation | Check minimum TLS version requirements before cert migration; test with `openssl s_client -connect $DOMAIN:443 -tls1` to verify old clients |
| Feature flag (A/B test) rollout causing cache key regression | New cache behavior with experiment header in cache key; cache hit rate drops to near 0% after rollout | `aws cloudwatch get-metric-statistics --namespace AWS/CloudFront --metric-name CacheHitRate --dimensions Name=DistributionId,Value=$CF_DIST_ID Name=Region,Value=Global --period 60 --statistics Average` | Remove experiment header from cache key policy: `aws cloudfront update-distribution` reverting `CachePolicyId` to previous; invalidate cache | Test cache policy changes in staging with realistic traffic; monitor `CacheHitRate` for 5 minutes after any cache behavior change |
| CloudFront managed prefix list version conflict | AWS updates CloudFront managed prefix list version; origin security group not updated; `521` errors | `aws ec2 describe-managed-prefix-lists --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \| jq '.PrefixLists[] \| {Version, MaxEntries}'` | Manually add new CloudFront IP ranges to origin security group; or re-associate managed prefix list with latest version | Use AWS-managed prefix list reference in security groups (`pl-3b927c52`) — it auto-updates; never manually list CloudFront CIDRs |
| Distributed lock expiry mid-distribution-update | Deployment automation holding distributed lock while updating CloudFront distribution; lock expires before update completes; second deployer starts | `aws cloudfront get-distribution --id $CF_DIST_ID --query 'Distribution.Status'` — check for `InProgress` with unexpected deployer | Two concurrent CloudFront distribution updates creating conflicting config states; one update overwriting the other | Check CloudFront `ETag` before each update: `aws cloudfront get-distribution-config --id $CF_DIST_ID --query 'ETag'`; use ETag-based optimistic locking | Always use CloudFront ETag-based conditional updates (`--if-match $ETAG`) in deployment automation to prevent concurrent update conflicts |

## Kernel/OS & Host-Level Failure Patterns
**Minimum cross-cutting cases to evaluate here:** OOM killer false kill, inode exhaustion, CPU steal, NTP skew affecting locks, leases, or coordination, file descriptor exhaustion, and TCP conntrack table saturation.

| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|------------------|--------------|-------------|----------------------|
| OOM killer terminates Lambda@Edge function container | CloudWatch Logs for Lambda@Edge showing `Process exited before completing request`; `aws logs filter-log-events --log-group-name /aws/lambda/us-east-1.$FUNCTION_NAME --filter-pattern "Runtime exited" --region us-east-1` | Lambda@Edge function exceeding 128MB memory limit while processing large CloudFront responses | Lambda@Edge invocation fails; CloudFront returns `502` for affected requests | Increase Lambda@Edge memory: `aws lambda update-function-configuration --function-name $FUNCTION_NAME --memory-size 512 --region us-east-1`; republish and update CloudFront association |
| Inode exhaustion on EC2 origin behind CloudFront | EC2 origin returning `500 Internal Server Error`; `df -i /` shows `IUse%` at 100% | Excessive temp files from application framework (e.g. Rails tmp cache, PHP session files) | Origin cannot create new files; web server returning 500; CloudFront caching the error | `find /tmp -type f -mtime +1 -delete`; restart origin application; add CloudFront error page caching with short TTL to avoid caching 500s |
| CPU steal spike on EC2 origin degrading CloudFront cache miss performance | CloudFront `CacheMissCount` elevated; origin P99 latency spikes; `top` shows `%st > 15` on origin | Overcommitted EC2 host; noisy neighbor instance on same physical host | Cache misses taking 5–10× longer to fill; P99 end-user latency SLO breach | `aws ec2 stop-instances --instance-ids $ID && aws ec2 start-instances --instance-ids $ID` to migrate to new host; switch to dedicated or memory-optimized instance type |
| NTP clock skew causing CloudFront signed URL/cookie validation failures | CloudFront returning `403 Access Denied` for signed URLs with valid signatures; `aws cloudfront list-invalidations --distribution-id $CF_DIST_ID` shows no active invalidations | EC2 origin or signing service clock drifted; `Date` in signed URL outside CloudFront's allowed skew | Signed URL-protected content inaccessible to all users; CDN delivery broken | `timedatectl status` on signing service host; `chronyc makestep`; `aws ssm send-command --document-name AWS-RunShellScript --parameters '{"commands":["chronyc makestep"]}' --targets "Key=instanceids,Values=$INSTANCE_ID"` |
| File descriptor exhaustion on origin web server | CloudFront returning `503 Service Unavailable`; origin Nginx logs show `too many open files`; `ss -s` shows thousands of CLOSE_WAIT | Default `ulimit -n 1024` on EC2; CloudFront opening many persistent connections to origin | New connections from CloudFront to origin rejected; CloudFront retries and fails over | `ulimit -n 65536` on origin; add `LimitNOFILE=65536` to Nginx/Apache systemd unit; `aws ssm send-command --document-name AWS-RunShellScript --parameters '{"commands":["ulimit -n 65536; systemctl restart nginx"]}' --targets "Key=instanceids,Values=$INSTANCE_ID"` |
| TCP conntrack table full on NAT Gateway behind CloudFront origin | CloudFront `502` errors on specific requests; `dmesg | grep "nf_conntrack: table full"` on NAT gateway instance | High-throughput origin behind NAT instance (not AWS Managed NAT Gateway) exhausting conntrack table | New connections from CloudFront silently dropped by NAT; intermittent 502s | `sysctl -w net.netfilter.nf_conntrack_max=524288` on NAT instance; migrate from legacy NAT instance to AWS Managed NAT Gateway |
| Kernel panic on EC2 origin node behind CloudFront | EC2 instance status check failing in `aws ec2 describe-instance-status --instance-ids $ID`; CloudFront returning `502` | Hardware fault or kernel panic on EC2 host | Complete origin failure; CloudFront serving stale cache if enabled; all cache misses 502 | `aws ec2 stop-instances --instance-ids $ID && aws ec2 start-instances --instance-ids $ID`; enable CloudFront origin failover to secondary origin; verify with `aws cloudwatch get-metric-statistics --metric-name 5xxErrorRate --namespace AWS/CloudFront` |
| NUMA memory imbalance on large EC2 origin | `numastat` on `c5.18xlarge` origin shows imbalanced allocation; large Lambda@Edge-warmed pages showing cache miss spikes | Origin application not NUMA-aware; memory allocations crossing NUMA nodes under high concurrency | High origin latency for cache-miss requests; increased CloudFront `OriginLatency` metric | `numactl --interleave=all` for origin web server process; pin Nginx worker processes to NUMA nodes with `worker_cpu_affinity`; monitor with `aws cloudwatch get-metric-statistics --metric-name OriginLatency --namespace AWS/CloudFront` |

## Deployment Pipeline & GitOps Failure Patterns
**Minimum cross-cutting cases to evaluate here:** image pull failure (rate limit or auth), Helm drift, ArgoCD sync stuck, PodDisruptionBudget-blocked rollout, blue-green cutover failure, and ConfigMap or Secret drift.

| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Lambda@Edge image/deployment rate limit | Lambda@Edge function update stuck; `aws lambda update-function-code` returns `TooManyRequestsException` | `aws lambda get-function --function-name $FUNCTION_NAME --region us-east-1 \| jq '.Configuration.LastUpdateStatus'` | Wait and retry with exponential backoff; use `aws lambda wait function-updated` before CloudFront association update | Batch Lambda@Edge updates; use CodeDeploy for controlled rollout; avoid parallel deployments to the same function |
| Lambda@Edge ECR image pull auth failure | CloudFront returning `502`; Lambda@Edge logs show `AccessDeniedException pulling ECR image` | `aws logs filter-log-events --log-group-name /aws/lambda/us-east-1.$FUNCTION_NAME --filter-pattern "AccessDenied" --region us-east-1` | `aws lambda update-function-configuration --function-name $FUNCTION_NAME --region us-east-1`; fix ECR repo policy to allow `lambda.amazonaws.com` principal | Grant ECR `ecr:GetDownloadUrlForLayer` to Lambda execution role; use inline ZIP deployment for Lambda@Edge to avoid ECR dependency |
| CloudFront distribution config Helm/Terraform drift | `terraform plan` shows unexpected CloudFront distribution changes; cache behaviors differ from desired state | `terraform plan -target=aws_cloudfront_distribution.$NAME 2>&1 \| grep -E "change\|add\|destroy"` | `terraform apply -target=aws_cloudfront_distribution.$NAME` to reconcile; or `git revert` the offending commit and re-apply | Import existing CloudFront distribution into Terraform state with `terraform import`; enable Sentinel/OPA policy to require review for CloudFront changes |
| ArgoCD sync stuck on CloudFront Kubernetes Ingress annotation update | ArgoCD showing `Degraded`; Kubernetes Ingress with CloudFront annotation not applying | `argocd app get $APP -o json \| jq '.status.conditions'`; `kubectl describe ingress $INGRESS_NAME` | `argocd app rollback $APP $PREV_REVISION`; manually apply: `kubectl apply -f ingress.yaml` | Use AWS Load Balancer Controller properly; ensure ArgoCD sync waves order Ingress after ACM cert issuance |
| PodDisruptionBudget blocking origin rolling update behind CloudFront | CloudFront `5xxErrorRate` elevated during origin deployment; PDB preventing pod termination | `kubectl get pdb -n $NS`; `kubectl describe pdb $PDB_NAME` — check `AllowedDisruptions` | `kubectl patch pdb $PDB_NAME -p '{"spec":{"minAvailable":1}}'`; ensure CloudFront has stale cache fallback enabled during rollout | Enable CloudFront origin failover with secondary origin; set PDB to allow at least 1 pod disruption at a time |
| Blue-green CloudFront origin switch failure | CloudFront distribution updated to new ALB but new ALB health checks failing; all traffic 503 | `aws cloudfront get-distribution --id $CF_DIST_ID \| jq '.Distribution.DistributionConfig.Origins.Items[].DomainName'`; `aws elbv2 describe-target-health --target-group-arn $TG_ARN` | Revert origin: `aws cloudfront update-distribution --id $CF_DIST_ID --if-match $ETAG --distribution-config "$OLD_CONFIG"` | Test new origin health before switching CloudFront; use weighted routing in Route 53 for gradual traffic shift before CloudFront update |
| CloudFront cache behavior / S3 origin policy drift | S3 bucket policy updated removing CloudFront OAC access; CloudFront returning `403 Access Denied` | `aws s3api get-bucket-policy --bucket $BUCKET \| jq '.Policy' \| python3 -m json.tool \| grep "cloudfront"` | Add OAC policy statement back: `aws s3api put-bucket-policy --bucket $BUCKET --policy "$POLICY_JSON"` | Manage S3 bucket policy via Terraform with CloudFront OAC ARN as parameter; alert on bucket policy changes via CloudTrail |
| Feature flag stuck enabling new CloudFront function behavior | CloudFront Function updated with new feature flag logic; cannot roll back because function is `LIVE` | `aws cloudfront describe-function --name $FUNCTION_NAME \| jq '.FunctionSummary.FunctionMetadata.Stage'`; `aws cloudfront list-functions --stage LIVE` | Publish previous version: `aws cloudfront publish-function --name $FUNCTION_NAME --if-match $PREV_ETAG` | Store all CloudFront Function versions in git; use `DEVELOPMENT` stage for testing before publishing; maintain rollback runbook |

## Service Mesh & API Gateway Edge Cases
**Minimum cross-cutting cases to evaluate here:** circuit breaker false positives, rate limiting on legitimate traffic, stale service discovery endpoints, mTLS rotation interruption, retry storm amplification, gRPC keepalive or max-message failures, and trace context loss.

| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive: CloudFront origin failover triggering prematurely | CloudFront switching to secondary origin on transient 500; secondary origin overwhelmed by full traffic | `aws cloudwatch get-metric-statistics --metric-name 5xxErrorRate --namespace AWS/CloudFront --period 60 --statistics Average` — check if spike was brief | Secondary origin not sized for 100% traffic; both origins degraded | Adjust CloudFront origin failover criteria: increase `HTTPStatusCodes` threshold; add `FailoverCriteria.StatusCodes` only for 5xx not transient errors; scale up secondary origin |
| Rate limit hitting legitimate CloudFront API calls in automation | AWS SDK returning `Throttling: Rate exceeded` when calling `GetDistribution` or `CreateInvalidation` in CI/CD | `aws cloudwatch get-metric-statistics --metric-name ThrottledRequests --namespace AWS/CloudFront --period 3600 --statistics Sum` | CloudFront invalidation pipeline blocked; stale content served | Cache invalidation results; use `aws cloudfront create-invalidation` with wildcard `/*` once per deploy instead of per-file; implement exponential backoff in automation |
| Stale service discovery: CloudFront pointing to decommissioned ALB DNS | CloudFront returning `502` after ALB replaced; old ALB DNS name still in CloudFront origin config | `aws cloudfront get-distribution --id $CF_DIST_ID \| jq '.Distribution.DistributionConfig.Origins.Items[].DomainName'`; `nslookup $OLD_ALB_DNS` — if NXDOMAIN, decommissioned | All CloudFront requests 502; complete cache-miss traffic failure | `aws cloudfront update-distribution --id $CF_DIST_ID --if-match $ETAG --distribution-config "$NEW_CONFIG_WITH_NEW_ALB"` immediately |
| mTLS rotation breaking CloudFront custom origin HTTPS | CloudFront returning `502` after origin ACM certificate rotated; `ssl handshake failure` in access logs | `aws cloudfront get-distribution --id $CF_DIST_ID \| jq '.Distribution.DistributionConfig.Origins.Items[].CustomOriginConfig.OriginSSLProtocols'`; `openssl s_client -connect $ORIGIN:443` | All HTTPS cache-miss requests failing; cached content still served until TTL expiry | Update origin ACM certificate; if using self-signed origin cert, add it to CloudFront trusted store or switch to full public CA cert; verify with `curl -v https://$ORIGIN` |
| Retry storm amplifying CloudFront origin errors | Brief origin 503 causing CloudFront to retry per cache behavior; origin flooded with retried requests | `aws cloudwatch get-metric-statistics --metric-name OriginRequestCount --namespace AWS/CloudFront --period 60 --statistics Sum`; compare to `RequestCount` | Origin overwhelmed by CloudFront retry fan-out; outage extended | Disable CloudFront origin retry for POST/non-idempotent requests; set `OriginRequestPolicy` `QueryStrings=none` to increase cache hit rate; add AWS WAF rate limiting at origin ALB |
| gRPC streaming failure through CloudFront | gRPC streaming responses truncated; `grpc_cli call` returns `RESOURCE_EXHAUSTED` or incomplete stream | `aws cloudfront get-distribution --id $CF_DIST_ID \| jq '.Distribution.DistributionConfig.DefaultCacheBehavior.AllowedMethods'`; check `HTTP2` enabled in distribution | gRPC stream terminated by CloudFront response size or timeout limit | Enable HTTP/2 in CloudFront distribution; set `DefaultTTL=0` for gRPC cache behavior; increase `OriginResponseTimeout` to 60s for streaming endpoints |
| Trace context propagation gap losing CloudFront Ray ID equivalent | X-Ray traces missing CloudFront edge spans; `X-Amzn-Trace-Id` not forwarded to origin | `aws cloudfront get-distribution --id $CF_DIST_ID \| jq '.Distribution.DistributionConfig.Origins.Items[].CustomOriginConfig'`; check if `X-Amzn-Trace-Id` in origin request policy | Cannot correlate CloudFront access logs to X-Ray origin traces; slow requests unattributable | Add `X-Amzn-Trace-Id` to CloudFront origin request policy headers whitelist; enable X-Ray tracing on Lambda@Edge: `aws lambda update-function-configuration --tracing-config Mode=Active` |
| Load balancer health check misconfiguration: CloudFront to ALB health check port mismatch | ALB target group unhealthy; CloudFront returning `502` after origin reconfiguration | `aws elbv2 describe-target-health --target-group-arn $TG_ARN`; `aws elbv2 describe-target-groups --target-group-arns $TG_ARN \| jq '.TargetGroups[].HealthCheckPort'` | All CloudFront requests to this origin failing; secondary origin (if configured) receiving all traffic | `aws elbv2 modify-target-group --target-group-arn $TG_ARN --health-check-port $CORRECT_PORT`; verify target health within 30s |
