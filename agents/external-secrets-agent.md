---
name: external-secrets-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-external-secrets-agent
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
# External Secrets SRE Agent

## Role
Owns reliability and incident response for the External Secrets Operator (ESO) in Kubernetes. Responsible for SecretStore and ClusterSecretStore authentication health, ExternalSecret sync correctness and freshness, secret version drift, RBAC configuration for store access, webhook certificate lifecycle, controller pod stability, and cross-provider (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, Azure Key Vault) failure triage.

## Architecture Overview

```
External Secret Providers
┌─────────────────┐  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐
│ AWS Secrets Mgr │  │ GCP Secret Mgr   │  │  HashiCorp   │  │  Azure Key Vault │
│ AWS SSM Param   │  │                  │  │  Vault       │  │                  │
└────────┬────────┘  └────────┬─────────┘  └──────┬───────┘  └────────┬─────────┘
         │                   │                    │                   │
         └───────────────────┴────────────────────┴───────────────────┘
                                       │ API calls (auth'd)
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  External Secrets Operator (ESO) — Controller Pod(s)                            │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │ ExternalSecret Reconciler                                                 │  │
│  │ ├── Watch ExternalSecret resources                                        │  │
│  │ ├── Validate SecretStore / ClusterSecretStore                             │  │
│  │ ├── Fetch secret from provider                                            │  │
│  │ └── Create/Update Kubernetes Secret                                       │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │ Webhook Server (admission validation)                                     │  │
│  │ ├── Validates ExternalSecret, SecretStore, ClusterSecretStore specs       │  │
│  │ └── TLS served via cert-manager or self-signed (auto-rotated)             │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────┘
         │ Kubernetes API (Watch/Create/Update Secrets)
         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  Kubernetes Resources                                                            │
│  ├── ExternalSecret (namespaced) → Kubernetes Secret                            │
│  ├── SecretStore (namespaced) — provider config + auth                          │
│  ├── ClusterSecretStore (cluster-wide) — shared provider config + auth          │
│  └── Kubernetes Secret (synced, owned by ESO)                                   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| `externalsecret_sync_calls_error` | > 1/5min | > 5/5min | Sync failures; provider or auth error |
| `externalsecret_sync_calls_total` drop | > 20% drop vs baseline | > 50% drop | Reconciler or provider outage |
| ExternalSecret `Ready=False` count | > 1 | > 5 | `kubectl get externalsecrets -A \| grep -v True` |
| SecretStore `Ready=False` count | Any | Any | Auth failure; entire namespace blocked |
| ClusterSecretStore `Ready=False` count | Any | Any | Cluster-wide auth failure |
| Controller pod restart count | > 2/day | > 1/hour | OOM, panic, or webhook cert issue |
| Webhook certificate expiry | < 30 days | < 7 days | Blocks all ESO resource creation/update |
| Secret staleness (time since last sync) | > `refreshInterval` + 5min | > `refreshInterval` × 2 | Sync loop stalled |
| Provider API error rate | > 1% of calls | > 5% of calls | AWS/GCP/Vault throttling or outage |
| `externalsecret_provider_api_calls_count{status="error"}` | Any | > 3 consecutive | Provider authentication or network issue |

## Alert Runbooks

### Alert: `ExternalSecretSyncFailed`
**Trigger:** `externalsecret_sync_calls_error` rate > 0 sustained for 5 minutes.

**Triage steps:**
1. Find all failing ExternalSecrets:
   ```bash
   kubectl get externalsecrets -A -o json | \
     jq -r '.items[] | select(.status.conditions[] | select(.type=="Ready" and .status=="False")) |
     "\(.metadata.namespace)/\(.metadata.name): \(.status.conditions[] | select(.type=="Ready") | .message)"'
   ```
2. Check the referenced SecretStore:
   ```bash
   kubectl describe secretstore -n <NAMESPACE> <STORE_NAME>
   kubectl describe clustersecretstore <STORE_NAME>
   ```
3. Review controller logs for the specific sync error:
   ```bash
   kubectl logs -n external-secrets deployment/external-secrets --tail=100 \
     | grep -E "error|Error|sync|<EXTERNALSECRET_NAME>"
   ```
4. Verify provider credentials are not expired:
   - AWS: `aws sts get-caller-identity`
   - GCP: `gcloud auth list`
   - Vault: `vault token lookup`
---

### Alert: `SecretStoreAuthFailed`
**Trigger:** SecretStore or ClusterSecretStore condition `Ready=False`.

**Triage steps:**
1. Get details of the failed store:
   ```bash
   kubectl get secretstore -A -o json | \
     jq -r '.items[] | select(.status.conditions[] | select(.type=="Ready" and .status=="False")) |
     "\(.metadata.namespace)/\(.metadata.name): \(.status.conditions[].message)"'
   ```
2. Check the auth method configured:
   ```bash
   kubectl get secretstore -n <NAMESPACE> <STORE_NAME> -o yaml | grep -A20 "provider:"
   ```
3. For AWS: verify IRSA annotation on ESO ServiceAccount:
   ```bash
   kubectl get sa -n external-secrets external-secrets -o yaml | grep "annotations"
   ```
4. For Vault: test token validity:
   ```bash
   VAULT_TOKEN=$(kubectl get secret -n <NS> <VAULT_TOKEN_SECRET> -o jsonpath='{.data.token}' | base64 -d)
   vault token lookup -format=json $VAULT_TOKEN | jq '.data | {display_name, expire_time, policies}'
   ```
---

### Alert: `ExternalSecretsWebhookCertExpiring`
**Trigger:** ESO webhook TLS certificate expires within 7 days.

**Triage steps:**
1. Check webhook certificate status:
   ```bash
   kubectl get secret -n external-secrets external-secrets-webhook -o yaml \
     | grep "tls.crt" | awk '{print $2}' | base64 -d \
     | openssl x509 -noout -enddate
   ```
2. Check if cert-manager is managing the certificate:
   ```bash
   kubectl get certificate -n external-secrets
   kubectl describe certificate -n external-secrets external-secrets-webhook
   ```
4. If self-managed, restart the webhook pod to trigger cert regeneration:
   ```bash
   kubectl rollout restart deployment -n external-secrets external-secrets-webhook
   ```

---

### Alert: `ExternalSecretStaleness`
**Trigger:** ExternalSecret has not synced within 2× its `refreshInterval`.

**Triage steps:**
1. Find stale ExternalSecrets:
   ```bash
   kubectl get externalsecrets -A -o json | jq -r \
     '.items[] | select(.status.refreshTime != null) |
     "\(.metadata.namespace)/\(.metadata.name): last sync \(.status.refreshTime), interval \(.spec.refreshInterval)"'
   ```
2. Check reconciler queue depth in controller logs:
   ```bash
   kubectl logs -n external-secrets deployment/external-secrets --tail=200 \
     | grep -E "reconcile|queue|requeue"
   ```
3. Verify the ESO controller is not in a crash loop:
   ```bash
   kubectl get pods -n external-secrets
   ```
## Common Issues & Troubleshooting

### Issue 1: AWS Secrets Manager Auth Failure (IRSA)
**Symptom:** SecretStore shows `Ready=False`; error: `AccessDeniedException: User: arn:aws:sts::... is not authorized`.

**Diagnosis:**
```bash
# Check IRSA annotation on ESO service account
kubectl get sa -n external-secrets external-secrets \
  -o jsonpath='{.metadata.annotations}' | python3 -m json.tool

# Verify the IAM role trust policy allows the ESO service account
aws iam get-role --role-name <ESO_IAM_ROLE> \
  --query 'Role.AssumeRolePolicyDocument' | python3 -m json.tool

# Test assuming the role
aws sts assume-role-with-web-identity \
  --role-arn arn:aws:iam::<ACCOUNT>:role/<ESO_IAM_ROLE> \
  --role-session-name test \
  --web-identity-token $(cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token) 2>&1

# Check if the IAM policy grants secrets:GetSecretValue
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::<ACCOUNT>:role/<ESO_IAM_ROLE> \
  --action-names secretsmanager:GetSecretValue \
  --resource-arns arn:aws:secretsmanager:<REGION>:<ACCOUNT>:secret:<SECRET_NAME>
```

### Issue 2: Vault Auth Failure (Token Expired)
**Symptom:** ExternalSecret condition: `SecretSyncedError: could not get provider client: token is expired`.

**Diagnosis:**
```bash
# Check the Vault token secret
kubectl get secret -n <NAMESPACE> <VAULT_TOKEN_SECRET> -o yaml | grep "token:"

# Decode and inspect token
VAULT_ADDR=https://vault.example.com
VAULT_TOKEN=$(kubectl get secret -n <NS> <VAULT_TOKEN_SECRET> \
  -o jsonpath='{.data.token}' | base64 -d)
curl -s -H "X-Vault-Token: $VAULT_TOKEN" $VAULT_ADDR/v1/auth/token/lookup-self \
  | python3 -m json.tool | grep -E "expire_time|renewable|policies"

# Check SecretStore provider config for auth type
kubectl get secretstore -n <NS> <STORE_NAME> -o yaml | grep -A20 "vault:"
```

### Issue 3: ExternalSecret Syncs but Kubernetes Secret Has Wrong Data
**Symptom:** Secret exists but application fails with wrong credentials; `kubectl get secret` shows base64 data.

**Diagnosis:**
```bash
# Decode and inspect secret
kubectl get secret -n <NS> <SECRET_NAME> -o jsonpath='{.data}' | \
  python3 -c "import sys,json,base64; [print(k+': '+base64.b64decode(v).decode()) for k,v in json.load(sys.stdin).items()]"

# Compare with what's in the provider
aws secretsmanager get-secret-value \
  --secret-id <SECRET_NAME> --query SecretString --output text

# Check ExternalSecret spec for correct key mapping
kubectl get externalsecret -n <NS> <ES_NAME> -o yaml | grep -A20 "data:"

# Check if the secret version is pinned to an old version
kubectl get externalsecret -n <NS> <ES_NAME> -o yaml \
  | grep -E "version|remoteRef"
```

### Issue 4: RBAC Permission Denied on SecretStore
**Symptom:** Controller logs: `RBAC: access to resource "secrets" is not allowed`.

**Diagnosis:**
```bash
# Check ESO ClusterRole
kubectl get clusterrole external-secrets-controller -o yaml \
  | grep -A5 "rules"

# Check what permissions the ESO service account actually has
kubectl auth can-i create secrets --as=system:serviceaccount:external-secrets:external-secrets -n <TARGET_NS>
kubectl auth can-i get secrets --as=system:serviceaccount:external-secrets:external-secrets -n <TARGET_NS>

# Check if a namespace-specific RBAC restriction is in place
kubectl get rolebinding -n <TARGET_NS> | grep external-secrets
```

### Issue 5: Webhook Certificate Expired — All ESO Resources Blocked
**Symptom:** `kubectl apply` of ExternalSecret fails: `x509: certificate has expired or is not yet valid`.

**Diagnosis:**
```bash
# Check ValidatingWebhookConfigurations for ESO (two are installed: secretstore-validate, externalsecret-validate)
kubectl get validatingwebhookconfiguration secretstore-validate externalsecret-validate -o yaml \
  | grep "caBundle" | head -c200

# Inspect the webhook serving certificate
kubectl get secret -n external-secrets external-secrets-webhook \
  -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -enddate -subject

# Check if cert-manager Certificate is failing
kubectl get certificate -n external-secrets external-secrets-webhook
kubectl describe certificate -n external-secrets external-secrets-webhook | tail -20
```

### Issue 6: GCP Secret Manager Auth Failure (Workload Identity)
**Symptom:** SecretStore `Ready=False`; error: `PERMISSION_DENIED: Permission 'secretmanager.versions.access' denied`.

**Diagnosis:**
```bash
# Check Workload Identity binding on ESO KSA
kubectl get sa -n external-secrets external-secrets \
  -o jsonpath='{.metadata.annotations.iam\.gke\.io/gcp-service-account}'

# Verify GCP IAM binding exists
GCP_SA=external-secrets@<PROJECT_ID>.iam.gserviceaccount.com
gcloud iam service-accounts get-iam-policy $GCP_SA \
  --format=json | jq '.bindings[] | select(.role | contains("workloadIdentityUser"))'

# Test from inside pod
kubectl run wi-test --image=google/cloud-sdk:alpine --restart=Never -n external-secrets \
  --overrides='{"spec":{"serviceAccountName":"external-secrets"}}' \
  -- gcloud auth print-identity-token
```

## Key Dependencies

- **Kubernetes API Server** — all ExternalSecret and SecretStore watches + Kubernetes Secret writes; API server degradation causes sync backlog
- **AWS Secrets Manager / SSM Parameter Store** — secrets source for AWS-based stores; IAM role or service account must have `GetSecretValue`/`GetParameter` permissions
- **GCP Secret Manager** — secrets source for GCP stores; Workload Identity or service account key required
- **HashiCorp Vault** — secrets source for Vault stores; token TTL, KV engine version (v1/v2), and RBAC policy must be correctly configured
- **Azure Key Vault** — secrets source; Azure Managed Identity or Service Principal with `get` permission on secrets
- **cert-manager** — manages webhook TLS certificates; cert-manager failure causes webhook cert expiry and blocks all ESO resource mutations
- **ESO webhook pod** — validates ExternalSecret/SecretStore specs on admission; if webhook is down, new resources cannot be created
- **OIDC Provider (EKS/GKE)** — required for IRSA (AWS) and Workload Identity (GCP); OIDC issuer must be reachable

## Cross-Service Failure Chains

- **Vault token expires** → SecretStore transitions to `Ready=False` → All ExternalSecrets using that store fail to sync → Application Secrets become stale → Applications fail on restart with missing credentials
- **AWS IAM role trust policy accidentally modified** → IRSA token exchange fails → All namespaces using ClusterSecretStore blocked → Mass sync failure across cluster
- **cert-manager controller crashes** → ESO webhook cert not renewed → Webhook cert expires → New ExternalSecret/SecretStore resources cannot be created or modified → Drift accumulates silently
- **ESO controller pod OOMKilled** → All sync reconciliation stops → Secrets not refreshed for duration of downtime → Applications with short-lived tokens (Vault dynamic secrets) fail
- **Kubernetes RBAC change removes ESO permissions** → Controller cannot write Kubernetes Secrets → ExternalSecret objects show `Synced=False` but provider fetch succeeds → Applications use stale secrets until restarts

## Partial Failure Patterns

- **Some ExternalSecrets syncing, others not:** Usually a per-namespace SecretStore auth issue affecting only that namespace; ClusterSecretStore still works for other namespaces.
- **Secret created but empty:** `remoteRef.property` references a JSON key that doesn't exist in the provider secret value. Secret is created but has no keys. Application crashes with empty env vars.
- **Old secret version served after rotation:** `remoteRef.version` is pinned; new secret version in provider not picked up. Remove the version pin or update it.
- **Webhook rejects valid resources:** Stale webhook CA bundle in `ValidatingWebhookConfiguration`; webhook serving cert rotated but `caBundle` field not updated. Force-reinstall webhook config or sync cert via cert-manager `Certificate` annotation.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|----------|
| ExternalSecret initial sync time | < 10s | 10-60s | > 60s |
| ExternalSecret refresh sync time | < 5s | 5-30s | > 30s |
| SecretStore validation time | < 5s | 5-15s | > 15s |
| Provider API call latency (AWS/GCP/Vault) | < 500ms | 500ms-2s | > 2s |
| Webhook admission response time | < 200ms | 200ms-1s | > 1s (admission timeout) |
| Controller reconcile queue length | < 10 | 10-100 | > 100 (sync backlog) |
| Controller pod memory | < 256Mi | 256-512Mi | > 512Mi (OOM risk) |
| Secrets managed per controller instance | < 500 | 500-2000 | > 2000 (scale out needed) |

## Capacity Planning Indicators

| Indicator | Healthy | Watch | Action Required |
|-----------|---------|-------|-----------------|
| ExternalSecret count per controller | < 500 | 500-1000 | > 1000 — scale to multiple controller replicas |
| Provider API calls per minute | < 100 | 100-500 | > 500 — risk of API throttling; increase `refreshInterval` |
| Average sync duration (p95) | < 5s | 5-30s | > 30s — provider latency; check network or API quota |
| Controller memory usage | < 256Mi | 256-512Mi | > 512Mi — increase limit; consider sharding by namespace |
| Unique SecretStore count | < 50 | 50-200 | > 200 — review ClusterSecretStore consolidation |
| Secret rotation frequency (per day) | < 50 | 50-200 | > 200 — high churn; verify not causing Kubernetes Secret storms |
| Webhook admission latency p99 | < 100ms | 100-500ms | > 500ms — webhook pod under load; scale replicas |
| Stale secrets count (past refresh window) | 0 | 1-5 | > 5 — ESO reconciler backlogged or provider unreachable |

## Diagnostic Cheatsheet

```bash
# List all ExternalSecrets and their sync status
kubectl get externalsecrets -A -o custom-columns=\
'NS:.metadata.namespace,NAME:.metadata.name,STORE:.spec.secretStoreRef.name,READY:.status.conditions[0].status,MSG:.status.conditions[0].message'

# List all SecretStore and ClusterSecretStore health
kubectl get secretstore,clustersecretstore -A

# Get detailed status of a failing ExternalSecret
kubectl describe externalsecret -n <NS> <NAME> | tail -30

# Force a sync on an ExternalSecret
kubectl annotate externalsecret -n <NS> <NAME> force-sync=$(date +%s) --overwrite

# Tail ESO controller logs in real time
kubectl logs -n external-secrets -l app.kubernetes.io/name=external-secrets -f --tail=100

# Check webhook pod health
kubectl get pods -n external-secrets -l app.kubernetes.io/name=external-secrets-webhook

# Verify webhook certificate validity
kubectl get secret -n external-secrets external-secrets-webhook \
  -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -enddate -subject

# List all Kubernetes Secrets managed by ESO
kubectl get secrets -A -o json | jq -r \
  '.items[] | select(.metadata.annotations."reconcile.external-secrets.io/data-hash" != null) |
   "\(.metadata.namespace)/\(.metadata.name)"'

# Check reconcile annotation to see last sync
kubectl get externalsecret -n <NS> <NAME> \
  -o jsonpath='{.metadata.annotations}' | python3 -m json.tool

# Test AWS Secrets Manager access from ESO pod
kubectl exec -n external-secrets deployment/external-secrets -- \
  aws secretsmanager get-secret-value --secret-id <SECRET_ARN> --query SecretString
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|--------------------|-------------|
| ExternalSecret sync success rate | 99.9% | 43.2 minutes of sync failures | `(successful syncs / total sync attempts) × 100` via `externalsecret_sync_calls_total` and `externalsecret_sync_calls_error` |
| Secret freshness (synced within refreshInterval) | 99.5% | 3.6 hours of stale secrets | Percentage of ExternalSecrets where `now - lastSyncTime < refreshInterval + 5min` |
| SecretStore availability (Ready=True) | 99.9% | 43.2 minutes of store invalidation | Percentage of time all stores are `Ready=True` |
| Webhook admission latency p99 | < 500ms | < 500ms for 99% of webhook calls | Measured via webhook access logs or controller metrics |

## Configuration Audit Checklist

| Check | Command | Expected |
|-------|---------|----------|
| All ExternalSecrets synced | `kubectl get externalsecrets -A \| grep -v True \| wc -l` | `0` |
| All SecretStores valid | `kubectl get secretstore,clustersecretstore -A \| grep -v True \| wc -l` | `0` |
| Webhook certificate not expiring soon | `kubectl get secret -n external-secrets external-secrets-webhook -o jsonpath='{.data.tls\.crt}' \| base64 -d \| openssl x509 -noout -enddate` | > 30 days remaining |
| Controller has resource limits | `kubectl get deployment -n external-secrets external-secrets -o yaml \| grep -A4 resources` | `limits` and `requests` defined |
| Controller RBAC follows least privilege | `kubectl get clusterrole external-secrets-controller -o yaml \| grep "secrets"` | Only `get`, `create`, `update`, `delete` on secrets |
| SecretStore does not use inline credentials | `kubectl get secretstore,clustersecretstore -A -o yaml \| grep -i "apiKey\|accessKey\|password"` | No inline credentials (use secretRef) |
| RefreshInterval set to appropriate value | `kubectl get externalsecrets -A -o json \| jq '[.items[].spec.refreshInterval] \| unique'` | Not `0` (disabled) unless intentional |
| ExternalSecrets have `creationPolicy: Owner` | `kubectl get externalsecrets -A -o json \| jq '[.items[].spec.target.creationPolicy] \| unique'` | `Owner` (ESO owns and manages the K8s Secret) |
| Namespace selector on ClusterSecretStore | `kubectl get clustersecretstore -o yaml \| grep namespaceSelector` | Present if store should be namespace-restricted |
| Prometheus metrics enabled | `kubectl get svc -n external-secrets \| grep metrics` | Metrics service present on port 8080 |

## Log Pattern Library

| Pattern | Meaning | Action |
|---------|---------|--------|
| `unable to get provider client: AccessDeniedException` | AWS IAM permission denied | Check IAM role policy; verify IRSA annotation |
| `token is expired` | Vault or other token-based auth expired | Rotate token; update Kubernetes Secret; consider renewable tokens |
| `rpc error: code = PermissionDenied` | GCP IAM permission denied | Check Workload Identity binding; grant `secretmanager.secretAccessor` |
| `SecretStore validation failed: could not connect to provider` | Provider endpoint unreachable | Check network connectivity; verify provider URL; check firewall/NSG |
| `unable to retrieve secret from provider: secret does not exist` | Secret key path wrong in ExternalSecret `remoteRef.key` | Verify secret path in provider console; update ExternalSecret spec |
| `error 4001: secret version not found` | Pinned version no longer exists in provider | Remove or update `remoteRef.version` in ExternalSecret |
| `secret sync failed: context deadline exceeded` | Provider API timeout | Check provider latency; increase timeout in SecretStore; check rate limits |
| `Reconciler error: failed to patch secret` | ESO cannot write Kubernetes Secret (RBAC) | Check ESO ClusterRole; verify can-i create/update secrets in namespace |
| `x509: certificate has expired or is not yet valid` | Webhook TLS cert expired | Rotate webhook cert; `cmctl renew` or delete and recreate |
| `validating webhook: the server could not find the requested resource` | Webhook not responding | Check webhook pod; verify `caBundle` in ValidatingWebhookConfiguration |
| `no condition of type Ready is present` | ExternalSecret never synced (just created) | Wait for first reconcile loop; check controller logs for this resource |
| `SecretStore validation succeeded` | Store successfully authenticated | Informational; confirm after auth fix |

## Error Code Quick Reference

| Error / Message Fragment | Root Cause | Quick Fix |
|--------------------------|-----------|-----------|
| `AccessDeniedException: User is not authorized` | AWS IAM policy missing | Add `secretsmanager:GetSecretValue` to ESO IAM role policy |
| `token is expired` | Vault/other token TTL exceeded | Rotate token; use `renewable=true` or Kubernetes auth |
| `PERMISSION_DENIED` (GCP) | GCP IAM missing `secretmanager.secretAccessor` | Grant role to Workload Identity service account |
| `does not exist` (secret not found) | Wrong `remoteRef.key` path | Verify exact path in provider; case-sensitive |
| `context deadline exceeded` | Provider API timeout | Check latency; increase `timeout` in SecretStore spec |
| `RBAC: access to secrets is denied` | ESO missing RBAC on target namespace | Create RoleBinding for ESO service account in namespace |
| `certificate has expired` (webhook) | Webhook TLS cert not renewed | `cmctl renew` or delete cert secret; restart webhook pod |
| `failed to patch secret: Operation cannot be fulfilled` | Secret modified outside ESO (resource version conflict) | Force sync; ESO will overwrite with correct data |
| `store not found` | ExternalSecret references nonexistent SecretStore | Create SecretStore or fix `secretStoreRef.name` in ExternalSecret |
| `kind ClusterSecretStore not found` | ExternalSecret uses ClusterSecretStore kind but store doesn't exist | Create ClusterSecretStore or change `secretStoreRef.kind` to `SecretStore` |
| `Azure: unauthorized_client` | Azure Service Principal credentials invalid or wrong tenant | Rotate Azure SP credentials; check `tenantId` in ClusterSecretStore |
| `invalid character in secret name` | AWS/GCP secret name has unsupported characters | Rename secret or add `remoteRef.decodingStrategy` |

## Known Failure Signatures

| Signature | Likely Cause | Diagnostic Step |
|-----------|-------------|-----------------|
| All ExternalSecrets simultaneously fail | IAM/auth credential expiry or ClusterSecretStore `Ready=False` | `kubectl get clustersecretstore`; check provider auth |
| Specific namespace all fail, others fine | Namespace-scoped SecretStore auth failure | `kubectl get secretstore -n <NS>`; check store status |
| Secret exists but has no keys | `remoteRef.property` references nonexistent JSON field | Decode secret value from provider; compare with ExternalSecret `data[].remoteRef.property` |
| ExternalSecret status shows `Synced` but data is stale | `refreshInterval` too long; or `force-sync` not working | Check `status.refreshTime`; verify controller is running |
| New ExternalSecrets cannot be created | Webhook cert expired or webhook pod down | `kubectl get pods -n external-secrets`; check webhook cert expiry |
| ESO controller pod restarting every few minutes | OOM from large number of ExternalSecrets | `kubectl top pod -n external-secrets`; increase memory limit |
| Vault secrets stop syncing after Vault seal | Vault sealed event not reflected as SecretStore error immediately | Vault: `vault status`; manually trigger SecretStore validation |
| Race condition: secret recreated with old value | ESO reconcile and external rotation racing | Ensure `refreshInterval` is > secret rotation frequency; use exact version in `remoteRef.version` |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| Pod stuck in `CreateContainerConfigError` | kubectl / k8s events | The Kubernetes Secret referenced by the Pod does not exist because ESO failed to sync | `kubectl describe pod -n <NS> <POD>` — `secret "..." not found`; `kubectl get externalsecret -n <NS>` for `Ready=False` | Fix the ExternalSecret sync error; manually create a placeholder secret as emergency stop-gap |
| Pod enters `CrashLoopBackOff` with missing env var error | Application runtime | ESO synced the Secret but the key name does not match what the Pod expects | `kubectl get secret -n <NS> <SECRET> -o json \| jq '.data \| keys'` vs. application env var names | Fix `spec.data[].secretKey` in ExternalSecret to match expected key names |
| Application returns 500 with "secret value empty" | Application runtime | ESO synced the Secret but the value in the provider was empty or null | `kubectl get secret -n <NS> <SECRET> -o json \| jq '.data'` for empty values; check provider for missing secret version | Populate the secret in the provider; trigger manual re-sync with `force-sync` annotation |
| Deployment rollout blocked; new pods never become Ready | kubectl / Helm | New pod references a Secret not yet created by ESO (ESO lag or sync failure) | `kubectl get externalsecret -n <NS>` for `SecretSyncedError` condition; check ESO controller logs | Wait for ESO to sync; fix underlying SecretStore auth; deploy ESO fix before application fix |
| Admission webhook error: `failed calling webhook "validate.externalsecret.external-secrets.io"` | kubectl / Helm / Terraform | ESO webhook pod is down or the webhook certificate has expired | `kubectl get pods -n external-secrets`; `kubectl get certificate -n external-secrets` | Restart ESO pods; renew webhook TLS certificate via cert-manager |
| All secrets across cluster suddenly stale / outdated | Application runtime | ESO controller pod is down (CrashLoopBackOff or OOMKilled); reconciliation loop stopped | `kubectl get pods -n external-secrets`; `kubectl logs -n external-secrets <POD>` | Restart ESO controller; check memory limits; escalate to cluster admin if OOM |
| AWS SDK `AccessDeniedException` in application | AWS SDK | ESO fetched a secret but the IAM role used has insufficient permissions for that specific path | CloudTrail: filter `secretsmanager` `AccessDenied` for ESO's IAM principal | Update IAM policy to grant `secretsmanager:GetSecretValue` on the specific secret ARN |
| `InvalidSignatureException` from AWS SDK in ESO logs | ESO controller | System clock skew between ESO pod and AWS endpoint | `kubectl exec -n external-secrets <POD> -- date` vs. `date -u` | Ensure node NTP sync; set `hostNetwork: false`; restart ESO pod |
| Vault `403 Forbidden` in ESO logs; secret not synced | ESO controller / Vault SDK | Vault token expired or Vault policy does not grant `read` on the secret path | `vault token lookup`; `vault policy read <POLICY>` | Renew Vault token or rotate AppRole credentials in SecretStore; update Vault policy |
| GCP `PermissionDenied` on Secret Manager access | ESO controller / GCP SDK | ESO's Workload Identity or service account key lacks `secretmanager.versions.access` | `gcloud projects get-iam-policy <PROJECT> --flatten="bindings[].members" --filter="bindings.members:<SA>"` | Grant `roles/secretmanager.secretAccessor` to the ESO service account |
| Secret value out of date by hours despite rotation | Application runtime | `refreshInterval` is too long; ESO has not re-synced since the secret was rotated | `kubectl get externalsecret -n <NS> <NAME> -o yaml \| grep refreshInterval`; check `status.refreshTime` | Reduce `refreshInterval`; trigger immediate re-sync via `force-sync: "true"` annotation |
| Azure `AuthorizationFailed` in ESO logs | ESO controller / Azure SDK | Managed Identity or Service Principal missing `Key Vault Secrets User` role on Key Vault | `az keyvault show --name <KV> --query properties.accessPolicies` | Assign `Key Vault Secrets User` role to the ESO managed identity |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Secret value staleness growing | Secrets successfully synced but values are hours behind provider updates | `kubectl get externalsecret -A -o json \| jq '.items[] \| {name:.metadata.name, refreshTime:.status.refreshTime, interval:.spec.refreshInterval}'` | Hours to days | Reduce `refreshInterval` for sensitive secrets; verify reconciler loop is healthy |
| ESO controller memory growing unboundedly | Memory usage climbing week-over-week; OOMKilled events eventually appear | `kubectl top pod -n external-secrets`; Prometheus `container_memory_usage_bytes` for ESO pod | Weeks | Increase ESO pod memory limit; check for ExternalSecret count explosion; upgrade ESO version |
| SecretStore authentication credential expiry approaching | No immediate failure; will cause hard sync failure when credentials expire | For AWS: `aws sts get-caller-identity`; for Vault: `vault token lookup \| grep expire_time`; for Azure: SP certificate expiry | Days to weeks | Automate credential rotation; use Workload Identity (IRSA / GKE WI) to avoid static credentials |
| ExternalSecret count growing without cleanup | Controller reconcile queue growing; sync latency increasing | `kubectl get externalsecret -A --no-headers \| wc -l`; ESO metric `controller_runtime_reconcile_queue_length` | Months | Implement ExternalSecret lifecycle automation; delete on service decommission |
| Webhook certificate expiry approaching | Admission webhooks still working; first sign is `x509: certificate has expired` at expiry | `kubectl get certificate -n external-secrets -o json \| jq '.items[].status.conditions[] \| select(.type=="Ready")'` | Days | Ensure cert-manager is managing the webhook certificate; verify auto-renewal is configured |
| Provider API rate limit consumption rising | Occasional throttling errors in ESO logs during peak hours | `kubectl logs -n external-secrets <POD> \| grep -c "RateLimit\|ThrottlingException"` per hour | Days | Increase `refreshInterval` to reduce API call frequency; stagger ExternalSecret sync times |
| Drift between Kubernetes Secret and provider value | Application gets wrong config; secret in cluster does not match what is in Secrets Manager | `kubectl get secret -n <NS> <NAME> -o json \| jq '.data \| map_values(@base64d)'` vs. provider value | Hours | Trigger force-sync; investigate if ESO is reconciling or if controller is stuck |
| ClusterSecretStore credential shared rotation failure | All ExternalSecrets using a ClusterSecretStore fail simultaneously after credential rotation | `kubectl get externalsecret -A -o json \| jq '[.items[] \| select(.spec.secretStoreRef.kind=="ClusterSecretStore") \| .status.conditions[]] \| group_by(.type)'` | Minutes | Update the credential in the ClusterSecretStore immediately; ensure rotation is scripted end-to-end |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# eso-health-snapshot.sh
# Prints full External Secrets Operator health summary

set -euo pipefail
ESO_NS="${1:-external-secrets}"

echo "=== External Secrets Operator Health Snapshot ==="
echo ""

echo "--- ESO Controller Pods ---"
kubectl get pods -n "$ESO_NS" -o wide

echo ""
echo "--- ExternalSecrets Sync Status (all namespaces) ---"
kubectl get externalsecrets -A \
  -o custom-columns="NS:.metadata.namespace,NAME:.metadata.name,STORE:.spec.secretStoreRef.name,READY:.status.conditions[0].type,REASON:.status.conditions[0].reason" \
  --no-headers 2>/dev/null | head -40

echo ""
echo "--- Failed / NotSynced ExternalSecrets ---"
kubectl get externalsecrets -A -o json 2>/dev/null \
  | jq -r '.items[] | select(.status.conditions[]? | .type == "Ready" and .status != "True") | "\(.metadata.namespace)/\(.metadata.name): \(.status.conditions[0].message)"' \
  | head -20

echo ""
echo "--- SecretStore Health ---"
kubectl get secretstores -A \
  -o custom-columns="NS:.metadata.namespace,NAME:.metadata.name,READY:.status.conditions[0].type,REASON:.status.conditions[0].reason" \
  --no-headers 2>/dev/null

echo ""
echo "--- ClusterSecretStore Health ---"
kubectl get clustersecretstores \
  -o custom-columns="NAME:.metadata.name,READY:.status.conditions[0].type,REASON:.status.conditions[0].reason" \
  --no-headers 2>/dev/null

echo ""
echo "--- Webhook Certificate Status ---"
kubectl get certificate -n "$ESO_NS" \
  -o custom-columns="NAME:.metadata.name,READY:.status.conditions[0].type,EXPIRY:.status.notAfter" \
  --no-headers 2>/dev/null || echo "  No certificates found in ESO namespace"

echo ""
echo "--- Recent ESO Controller Errors ---"
kubectl logs -n "$ESO_NS" -l "app.kubernetes.io/name=external-secrets" --tail=50 2>/dev/null \
  | grep -E '"level":"error"\|ERROR\|Failed\|error' | tail -15
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# eso-perf-triage.sh
# Checks sync latency, reconcile queue, and provider API health

set -euo pipefail
ESO_NS="${1:-external-secrets}"

echo "=== ESO Performance Triage ==="
echo ""

echo "--- Controller Resource Usage ---"
kubectl top pod -n "$ESO_NS" 2>/dev/null || echo "  metrics-server not available"

echo ""
echo "--- ExternalSecret Sync Age (stale if > refreshInterval) ---"
kubectl get externalsecrets -A -o json 2>/dev/null | jq -r '
  .items[] |
  .metadata.namespace as $ns |
  .metadata.name as $name |
  .spec.refreshInterval as $interval |
  .status.refreshTime as $refreshed |
  "\($ns)/\($name)  interval=\($interval)  lastSync=\($refreshed)"
' | head -20

echo ""
echo "--- ExternalSecrets with Long Refresh Intervals (> 1h) ---"
kubectl get externalsecrets -A -o json 2>/dev/null | jq -r '
  .items[] |
  select(.spec.refreshInterval | (. // "1h") | test("[0-9]+h") and (ltrimstr("0") | tonumber? // 0) > 1) |
  "\(.metadata.namespace)/\(.metadata.name): \(.spec.refreshInterval)"
' 2>/dev/null | head -20

echo ""
echo "--- Total ExternalSecret Count ---"
kubectl get externalsecrets -A --no-headers 2>/dev/null | wc -l | xargs echo "  Total ExternalSecrets:"

echo ""
echo "--- Provider Error Rate (last 100 log lines) ---"
kubectl logs -n "$ESO_NS" -l "app.kubernetes.io/name=external-secrets" --tail=100 2>/dev/null \
  | grep -ciE "error|failed|denied|throttl" || echo "  0 errors in last 100 lines"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# eso-connection-audit.sh
# Audits SecretStore credentials, ExternalSecret key coverage, and Kubernetes Secret freshness

set -euo pipefail
NAMESPACE="${1:-default}"

echo "=== ESO Connection & Resource Audit: namespace=$NAMESPACE ==="
echo ""

echo "--- SecretStores in Namespace ---"
kubectl get secretstores -n "$NAMESPACE" -o yaml 2>/dev/null \
  | grep -E 'name:|provider:|region:|vault:|kind:' | head -30

echo ""
echo "--- ExternalSecrets vs Kubernetes Secrets Coverage ---"
echo "  ExternalSecrets:"
kubectl get externalsecrets -n "$NAMESPACE" \
  -o custom-columns="NAME:.metadata.name,TARGET:.spec.target.name,STORE:.spec.secretStoreRef.name" \
  --no-headers 2>/dev/null

echo ""
echo "  Kubernetes Secrets (ESO-owned):"
kubectl get secrets -n "$NAMESPACE" \
  -l "reconcile.external-secrets.io/created-by" \
  -o custom-columns="NAME:.metadata.name,KEYS:.data" \
  --no-headers 2>/dev/null | head -20

echo ""
echo "--- Secrets Missing ESO Ownership (potential orphans) ---"
# Secrets that look like app secrets but aren't managed by ESO
kubectl get secrets -n "$NAMESPACE" --no-headers 2>/dev/null \
  | grep -v "helm.sh\|kubernetes.io/service-account\|default-token\|reconcile.external-secrets" \
  | awk '{print $1}' | head -20

echo ""
echo "--- Force-Sync All ExternalSecrets in Namespace ---"
echo "  (To trigger: uncomment the line below)"
# kubectl annotate externalsecrets -n "$NAMESPACE" --all force-sync="$(date +%s)" --overwrite

echo ""
echo "--- IAM / Credential Check (AWS example) ---"
if command -v aws &>/dev/null; then
  echo "  AWS caller identity:"
  aws sts get-caller-identity 2>/dev/null | jq '{Account, Arn}' || echo "  AWS credentials not configured"
fi
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Provider API rate limit shared across all ExternalSecrets | Random sync failures; AWS `ThrottlingException` or GCP `RESOURCE_EXHAUSTED` in ESO logs during peak hours | `kubectl logs -n external-secrets <POD> \| grep -c ThrottlingException` per hour; CloudTrail/GCP audit log by ESO principal | Increase `refreshInterval` on non-critical secrets; stagger sync times across ExternalSecrets | Use Workload Identity with per-namespace IAM scoping; request provider API quota increase |
| ESO controller OOMKilled from too many ExternalSecrets | All syncs stop; controller pod restarts; all applications get stale secrets | `kubectl describe pod -n external-secrets <POD> \| grep OOMKilled`; `kubectl get externalsecrets -A --no-headers \| wc -l` | Increase ESO pod memory limit immediately; reduce ExternalSecret count if possible | Set memory limits with headroom for ExternalSecret count; alert on memory > 80% before OOM |
| ClusterSecretStore credential throttled by one heavy namespace | Namespaces with frequent refreshes starve namespaces needing urgent syncs | `kubectl get externalsecrets -A -o json \| jq '.items \| group_by(.spec.secretStoreRef.name) \| map({store:.[0].spec.secretStoreRef.name,count:length})'` | Move heavy-refresh ExternalSecrets to a dedicated SecretStore with its own credentials | Use namespace-scoped SecretStores for teams with different refresh needs |
| Kubernetes API server overloaded by ESO reconcile loop | API server latency increases; other controllers slow down; etcd write rate spikes | `kubectl get --raw /metrics \| grep apiserver_request_total`; check ESO reconcile rate metric | Reduce ESO concurrency via `--concurrent` flag (default 1); reduce number of ExternalSecrets | Tune ESO `--concurrent` and increase `refreshInterval` to reduce reconcile pressure |
| Webhook pod down causing cluster-wide admission block | All new ExternalSecret, SecretStore, and ClusterSecretStore resource creates/updates rejected | `kubectl get pods -n external-secrets -l "app.kubernetes.io/component=webhook"` | Restart webhook pod; temporarily set webhook `failurePolicy: Ignore` for recovery | Run webhook with `minAvailable: 1` PodDisruptionBudget; use multi-replica webhook deployment |
| Vault token lease exhaustion from many ExternalSecrets | New ESO sync attempts fail with `403 Forbidden`; Vault audit log shows token limit reached | `vault list sys/leases/lookup/auth/<METHOD>/login`; count ESO-issued tokens | Rotate AppRole secret; switch to Kubernetes auth with short-lived tokens | Use Vault Kubernetes auth (short-lived tokens auto-renewed); avoid long-lived static tokens |
| etcd Secret object count approaching limit | ESO-managed Secrets accumulating; `etcdserver: mvcc: database space exceeded` | `kubectl get secrets -A -l "reconcile.external-secrets.io/created-by" --no-headers \| wc -l` | Delete orphaned ESO-managed Secrets; prune ExternalSecrets for decommissioned services | Enforce ExternalSecret lifecycle ownership; delete ExternalSecret when service is removed |
| Azure Key Vault throttling from multiple SecretStores | ESO sync latency spikes; Azure `429 Too Many Requests` in logs | Azure Monitor: Key Vault requests metric grouped by caller | Consolidate to fewer ClusterSecretStores per Key Vault; cache secrets in memory with longer interval | Request Key Vault throughput quota increase; use Key Vault Premium for higher RPS |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Secret provider (AWS Secrets Manager) regional outage | ESO cannot fetch secrets → sync fails → refreshed secrets not updated → applications using expired rotated credentials start failing with auth errors | All applications whose secrets have a refresh interval shorter than the outage duration | ESO logs `failed to fetch secret: RequestError: send request failed`; `kubectl get externalsecret -A -o json | jq '.items[].status.conditions'` shows `Ready: False`; AWS Health Dashboard shows regional event | Increase `refreshInterval` on all ExternalSecrets to buy time; disable auto-rotation for duration of outage |
| ESO controller crash-loop (OOMKill) | All ExternalSecret sync stops → rotated credentials not propagated → pods using rotated secrets start receiving 401/403 errors | All applications dependent on ESO-managed secrets with short refresh intervals | `kubectl get pods -n external-secrets` shows `OOMKilled` restarts; `kubectl get externalsecrets -A | grep -v Ready`; application logs `Invalid API Key` or `Access Denied` | Increase ESO memory limit; restart ESO pod; manually sync critical secrets: `kubectl annotate es <name> force-sync=$(date +%s) --overwrite` |
| Vault seal / HA failover during active ESO syncs | ESO gets `503 Service Unavailable` from Vault → queues retry storms → Vault unsealed nodes overloaded with re-auth requests on recovery | All applications using Vault-backed SecretStores; Vault recovery delayed by retry storm | ESO logs `vault: response.code=503`; Vault logs `core: vault is sealed`; `kubectl get secretstores -A -o json | jq '.items[].status'` shows `NotReady` | Pause ESO reconciliation by scaling to 0 during Vault unseal: `kubectl scale deployment/external-secrets -n external-secrets --replicas=0`; restart after Vault healthy |
| IRSA / Workload Identity token expiry | ESO pod loses IAM permissions → all ClusterSecretStore syncs fail → application secrets not refreshed → downstream auth failures cascade | All ExternalSecrets using the affected ClusterSecretStore | ESO logs `failed to assume role: ExpiredTokenException`; `kubectl describe secretstore <name>` → `condition: SecretSyncedError`; CloudTrail shows `AssumeRoleWithWebIdentity` failures | Restart ESO pod to force OIDC token refresh; verify OIDC provider configuration in IAM; check `ServiceAccount` annotation `eks.amazonaws.com/role-arn` |
| Kubernetes API server degraded → ESO cannot update Secrets | ESO fetches from provider successfully but cannot write Kubernetes Secret → applications get stale secret content → rotated secrets never applied | Applications relying on recently rotated secrets | ESO logs `failed to update secret: context deadline exceeded`; `kubectl get events -n external-secrets | grep FailedUpdate`; kube-apiserver latency metrics elevated | ESO retries on its own; prioritize kube-apiserver recovery; no manual action needed unless apiserver outage is prolonged |
| Secret rotation propagation lag exceeds application retry window | Provider rotates secret → ESO sync delayed by `refreshInterval` → application tries old credential → provider already invalidated old credential → 401 errors | Applications using invalidated credentials during lag window | Application logs `401 Unauthorized` with previously valid token; `kubectl get externalsecret <name> -o json | jq '.status.refreshTime'` is stale | Force immediate sync: `kubectl annotate es <name> force-sync=$(date +%s) --overwrite`; temporarily keep both old and new credential versions active in provider |
| Namespace deletion without ExternalSecret cleanup | ESO controller sees orphaned sync state → log spam with `namespace not found` errors → controller queue depth grows → other syncs delayed | ESO controller performance; namespaces with high-volume ExternalSecrets may have delayed syncs | ESO logs `failed to reconcile externalsecret: namespace <name> not found`; `kubectl get externalsecrets -A | grep <deleted-ns>`; ESO reconcile queue depth metric rising | Delete orphaned ExternalSecrets: `kubectl delete externalsecret -n <ns> --all`; add ESO garbage collection finalizer policy |
| Provider credential with Read-only scope suddenly needs Write (secret rotation) | ESO rotation feature fails with `403 Forbidden: insufficient permissions` → rotation halts → secret version not incremented → downstream applications get `invalid version` error | Only applications depending on ESO-managed rotation (not just fetch) | ESO logs `failed to push secret: AccessDeniedException`; `kubectl get pushsecret -A -o json | jq '.items[].status'` shows errors | Grant write permissions to ESO IAM role; use separate IAM role for read vs write operations |
| CertificateAuthority rotation in Vault PKI | All mTLS certs signed by old CA become untrusted after rotation → service mesh TLS handshakes fail → all inter-service communication drops | All services using Vault-issued TLS certificates via ExternalSecrets | Application logs `x509: certificate signed by unknown authority`; ESO syncs succeed but new CA cert not yet distributed to all consumers | Force sync of CA bundle ExternalSecrets immediately; rolling restart all pods to pick up new CA; ensure CA bundle is distributed before revoking old CA |
| GCP Secret Manager secret version disabled mid-rotation | ESO fetches `latest` version → gets disabled version → 404 / invalid response → sync fails → application gets no secret update | Applications using the specific GCP secret | ESO logs `rpc error: code = NotFound desc = Secret ... is disabled`; `kubectl get externalsecret <name> | grep -v Ready`; GCP audit logs show `AccessSecretVersion` failures | Re-enable the secret version in GCP Secret Manager: `gcloud secrets versions enable <version> --secret=<name>`; or pin ESO to a valid version number |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| ESO version upgrade breaking CRD schema | Existing ExternalSecrets fail validation with `unknown field` or `required field missing`; reconciliation stops for all resources | Immediately on upgrade | `kubectl get events -n external-secrets | grep FailedCreate`; `kubectl logs deployment/external-secrets | grep "validation error"`; diff old vs new CRD schema | `kubectl apply -f` previous ESO CRD version; rollback helm: `helm rollback external-secrets <previous-revision> -n external-secrets` |
| `refreshInterval` shortened cluster-wide | Sudden spike in provider API calls → throttling → sync failures for all ExternalSecrets | Within minutes of applying new interval | Provider throttle errors in ESO logs; CloudTrail/GCP audit logs show request rate spike; correlate with ExternalSecret update timestamp | Revert `refreshInterval` to longer value; patch all ExternalSecrets: `kubectl patch es -A --type=merge -p '{"spec":{"refreshInterval":"1h"}}'` |
| ClusterSecretStore IAM role policy change (permissions removed) | All ExternalSecrets using that store fail with `AccessDeniedException`; no secrets synced | Immediately on next sync cycle after IAM change | ESO logs `failed to get secret value: AccessDeniedException: User ... is not authorized to perform: secretsmanager:GetSecretValue`; `kubectl get secretstores -A | grep NotReady` | Restore IAM policy; re-apply original permissions; trigger resync |
| Vault auth method path renamed | ESO cannot authenticate: `vault: 404 path not found` | Immediately on next sync | ESO logs `error authenticating with Vault: error calling Vault: error writing <old-path>/login`; `kubectl describe secretstore <name>` shows auth error | Update SecretStore spec with new Vault auth path; `kubectl edit secretstore <name>` and correct `path:` field |
| Secret name/key changed in provider | ESO sync succeeds with wrong data or 404 → application reads empty/wrong secret value | Immediately on next sync after rename | Application logs show empty env vars or malformed config; ESO logs `ResourceNotFoundException: Secrets Manager can't find the specified secret`; `kubectl get externalsecret <name> -o json | jq '.status'` | Update ExternalSecret `spec.data[].remoteRef.key` to new secret name in provider |
| Target Kubernetes Secret name changed in ExternalSecret | Old Secret remains (orphaned); new Secret created with new name; pods still reference old name → crash-loop | Immediately for new pods using the new Secret name | `kubectl get secrets -n <ns>` shows both old and new secret names; pods using old name fail with `secret not found`; check deployment env refs | Update pod/deployment to reference new Secret name; delete old orphaned Secret |
| `deletionPolicy: Delete` enabled for the first time | Deleting an ExternalSecret now deletes the underlying Kubernetes Secret → pods crash immediately | Immediately on next ExternalSecret deletion | `kubectl get events | grep "Deleted Secret"`; pods enter `CreateContainerConfigError`; `kubectl describe pod` shows `secret not found`; correlate with ExternalSecret delete event | Restore Secret from provider immediately via re-applying ExternalSecret; change `deletionPolicy: Retain` for critical secrets |
| Webhook TLS cert renewal changes CA bundle | Admission webhook rejects ExternalSecret CRD operations with `x509: certificate signed by unknown authority` | After cert rotation, on next webhook call | `kubectl get validatingwebhookconfigurations secretstore-validate externalsecret-validate -o json | jq '.webhooks[].clientConfig.caBundle'` shows new CA; `kubectl apply -f externalsecret.yaml` returns TLS error | Update webhook `caBundle` with new CA: `kubectl patch validatingwebhookconfiguration secretstore-validate --patch ...` (and `externalsecret-validate`); cert-manager handles this automatically if configured |
| Network policy added blocking ESO egress to provider | ESO sync attempts time out silently; secrets become stale | After next sync attempt (within `refreshInterval`) | ESO logs `context deadline exceeded` on provider calls (no auth error, just timeout); `kubectl exec -n external-secrets <pod> -- curl -s https://secretsmanager.<region>.amazonaws.com/` fails | Add egress NetworkPolicy rule for ESO pod to provider endpoint; `kubectl apply -f network-policy-eso-egress.yaml` |
| Kubernetes RBAC change removing ESO service account permissions | ESO cannot read/write Secrets or ExternalSecret status; all syncs fail with `forbidden` | Immediately on RBAC change | ESO logs `failed to update secret: secrets is forbidden: User ... cannot create resource "secrets" in API group "" in the namespace ...`; `kubectl auth can-i create secrets --as=system:serviceaccount:external-secrets:external-secrets` returns `no` | Restore RBAC ClusterRole/RoleBinding; `kubectl apply -f` original RBAC manifests |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| ExternalSecret and Kubernetes Secret out of sync (provider updated, Secret not yet refreshed) | `kubectl get externalsecret <name> -o json \| jq '.status.refreshTime'` vs current time; compare secret value hash | Applications using the Kubernetes Secret have old credential; provider has already rotated | Auth failures for applications until next sync; window = `refreshInterval` | Force immediate resync: `kubectl annotate externalsecret <name> -n <ns> force-sync=$(date +%s) --overwrite` |
| Multiple ExternalSecrets targeting the same Kubernetes Secret name | `kubectl get externalsecrets -n <ns> -o json \| jq '[.items[] \| {name: .metadata.name, target: .spec.target.name}] \| group_by(.target) \| map(select(length>1))'` | One ExternalSecret's sync overwrites another's data; losing keys from the other source | Applications missing expected secret keys intermittently | Use merged ExternalSecret with multiple `spec.data` entries rather than two separate ExternalSecrets targeting the same Secret |
| ClusterSecretStore vs SecretStore precedence confusion | `kubectl get secretstores,clustersecretstores -A` — same store name exists at both namespace and cluster scope | ExternalSecrets in some namespaces resolve to different store; inconsistent provider credentials used | Some namespaces get wrong provider credentials; security boundary violations | Audit store resolution: `kubectl get externalsecret <name> -o json \| jq '.spec.secretStoreRef'`; rename stores to avoid ambiguity |
| ESO annotation `force-sync` applied to wrong resource | `kubectl get externalsecrets -A -o json \| jq '.items[] \| select(.metadata.annotations["force-sync"] != null) \| .metadata.name'` | Unexpected mass-resync of all ExternalSecrets; provider API throttled | API rate limit consumed; non-critical syncs delay critical ones | Remove `force-sync` annotation after sync completes; use targeted sync, not wildcard |
| Secret version pinned in ExternalSecret but version deprecated/deleted in provider | `kubectl get externalsecret <name> -o json \| jq '.spec.data[].remoteRef.version'` shows pinned version; provider shows version deleted | ESO sync fails with `ResourceNotFoundException`; application receives no secret update | Secret becomes `SyncError`; application uses stale value | Update ExternalSecret to use `latest` or new version; `kubectl patch externalsecret <name> --type=json -p '[{"op":"replace","path":"/spec/data/0/remoteRef/version","value":"AWSCURRENT"}]'` |
| Orphaned Kubernetes Secrets after ExternalSecret deleted without `deletionPolicy: Delete` | `kubectl get secrets -n <ns> -l "reconcile.external-secrets.io/created-by"` shows Secrets with no corresponding ExternalSecret | Stale secrets with possibly old credentials remain in cluster | Old credentials in cluster etcd; potential security audit finding | Identify orphans and delete: `kubectl delete secret <orphan-name> -n <ns>`; implement periodic audit job |
| Vault path naming collision: two ExternalSecrets reading same Vault path with different expected keys | Both get same data but silently miss expected keys that are in a different path | Applications receive wrong secret contents; no error logged by ESO (sync "succeeds") | Silent misconfiguration; application uses wrong credential | Audit: compare `kubectl get externalsecret <name> -o json \| jq '.spec.data[].remoteRef'` against actual Vault paths; correct path references |
| ExternalSecret target Secret missing `immutable: false` while Vault secret rotated | If Secret was previously created as immutable, ESO cannot update it → sync fails with `Invalid: spec is immutable` | ESO logs `failed to update secret: Secret ... is immutable`; application continues using old credential after rotation | Credentials not rotated despite provider rotation | Delete and recreate the Kubernetes Secret (brief interruption); never create ESO-managed Secrets as immutable |
| Provider caching returning stale value during rotation transition | `aws secretsmanager get-secret-value --secret-id <name> --version-stage AWSCURRENT` returns old value from provider cache | ESO fetches and syncs old credential even after rotation | Application continues using old (potentially revoked) credential | Disable provider-side caching; call `aws secretsmanager rotate-secret --rotate-immediately`; force ESO resync |
| Two ESO replicas (HA mode) racing to update the same Secret | `kubectl get events -n <ns> \| grep "Conflict"` shows `Operation cannot be fulfilled: the object has been modified` | Intermittent sync conflicts logged; actual Secret content correct (one write wins); no data loss but noisy logs | No impact to data correctness; only log noise | ESO uses leader election in HA mode — verify `--enable-leader-election=true`; only leader performs writes |

## Runbook Decision Trees

### Decision Tree 1: ExternalSecret Stuck in `SyncError` State

```
ExternalSecret shows SyncError
├── Check ESO logs for error type
│   kubectl logs deployment/external-secrets -n external-secrets | grep -A3 "failed to sync"
│
├── ERROR: "AccessDeniedException" / "403 Forbidden"
│   ├── Is the SecretStore IAM role / SA correct?
│   │   kubectl describe clustersecretstore <name> | grep -A10 "Provider"
│   │   → YES: Check if IAM policy was recently modified (CloudTrail)
│   │       → Restore policy → trigger resync
│   │   → NO: Fix annotation eks.amazonaws.com/role-arn on ESO service account
│   │       kubectl annotate sa external-secrets -n external-secrets eks.amazonaws.com/role-arn=<arn> --overwrite
│   │       kubectl rollout restart deployment/external-secrets -n external-secrets
│
├── ERROR: "ResourceNotFoundException" / "404"
│   ├── Does the secret exist in the provider?
│   │   aws secretsmanager describe-secret --secret-id <name>
│   │   → NO: Create the secret in the provider, then resync
│   │   → YES: Check ExternalSecret remoteRef key spelling
│   │       kubectl get externalsecret <name> -o json | jq '.spec.data[].remoteRef'
│   │       → Fix key path → kubectl apply → resync
│
├── ERROR: "context deadline exceeded" / timeout
│   ├── Can ESO pod reach the provider endpoint?
│   │   kubectl exec -n external-secrets deployment/external-secrets -- curl -s -o /dev/null -w "%{http_code}" https://secretsmanager.us-east-1.amazonaws.com/
│   │   → HTTP 200: Provider reachable → check refreshInterval vs provider rate limits
│   │   → Connection refused / timeout: NetworkPolicy blocking egress
│   │       kubectl get networkpolicy -n external-secrets
│   │       → Add egress rule for provider CIDR/endpoint
│
└── ERROR: "invalid token" / "ExpiredTokenException"
    └── Restart ESO pod to refresh OIDC/IRSA token
        kubectl rollout restart deployment/external-secrets -n external-secrets
        → If persists: verify OIDC provider in IAM console matches cluster OIDC issuer
            aws iam list-open-id-connect-providers
```

### Decision Tree 2: Application Receiving Wrong or Stale Secret Values

```
Application reports unexpected secret value / auth failure after rotation
│
├── Confirm the Kubernetes Secret value
│   kubectl get secret <name> -n <ns> -o json | jq '.data | map_values(@base64d)'
│
├── Does the Secret value match what the provider currently has?
│   aws secretsmanager get-secret-value --secret-id <name> --query SecretString
│   │
│   ├── NO — Secret is stale, not yet synced
│   │   ├── Check ExternalSecret refreshTime
│   │   │   kubectl get externalsecret <name> -n <ns> -o json | jq '.status.refreshTime'
│   │   │   → Force immediate resync:
│   │   │       kubectl annotate externalsecret <name> -n <ns> force-sync=$(date +%s) --overwrite
│   │   │   → After sync, restart application pods:
│   │   │       kubectl rollout restart deployment/<app> -n <ns>
│   │
│   ├── YES — Secret is current but app still fails
│   │   ├── Did the pod restart / pick up the new secret?
│   │   │   kubectl describe pod <pod> | grep -A3 "Started"
│   │   │   → NO: App may cache env vars — rolling restart needed
│   │   │       kubectl rollout restart deployment/<app> -n <ns>
│   │   │   → YES: Check if app reads secret from volume (auto-updated) vs env (requires restart)
│   │
│   └── Both stale AND wrong key mapping
│       kubectl get externalsecret <name> -o json | jq '.spec.data[] | {k8sKey: .secretKey, remoteKey: .remoteRef.key}'
│       → Correct the remoteRef mapping → kubectl apply → resync
```

## Cost & Quota Runaway Patterns
| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|----------------------|------------|
| refreshInterval set to 1s cluster-wide | Misconfigured ExternalSecret template with `refreshInterval: 1s` applied to many resources | `kubectl get externalsecrets -A -o json | jq '[.items[] | select(.spec.refreshInterval == "1s")] | length'`; AWS CloudTrail shows `GetSecretValue` call rate explosion | AWS Secrets Manager throttling (100 req/s limit per region); all secrets fail to sync | Patch all affected ExternalSecrets: `kubectl get es -A -o name | xargs -I{} kubectl patch {} --type=merge -p '{"spec":{"refreshInterval":"1h"}}'` | Set minimum refreshInterval via OPA/Kyverno policy; default to 1h |
| Provider API quota exhausted (AWS Secrets Manager 100 TPS) | Too many ExternalSecrets with short refresh intervals; spike during mass resync | `aws cloudwatch get-metric-statistics --namespace AWS/SecretsManager --metric-name ResourceCount`; ESO logs `ThrottlingException: Rate exceeded` | All ExternalSecret syncs fail until quota recovers (typically 1-5 min backoff) | Stagger resync: avoid triggering `force-sync` on all resources simultaneously; request quota increase via AWS Support | Implement `refreshInterval` tiering (critical: 5m, standard: 1h, non-sensitive: 24h); use jitter in refresh |
| ExternalSecret count explosion from namespace proliferation | CI/CD creates a new namespace per PR with 20+ ExternalSecrets; thousands of ExternalSecrets accumulate | `kubectl get externalsecrets -A | wc -l`; compare with expected baseline | ESO controller CPU/memory grows linearly; reconcile queue depth increases; sync latency increases for all | Implement namespace lifecycle management; delete preview namespaces after PR merge | Enforce ExternalSecret count per namespace limit via admission webhook; cleanup pipeline for ephemeral namespaces |
| Vault dynamic secrets with short TTL causing constant renewal | Vault dynamic secret TTL set to 60s; ESO renewing every 30s; Vault audit log filling rapidly | `vault audit list`; check Vault audit log write rate; ESO metrics `externalsecret_sync_calls_total` very high | Vault performance degradation; audit log storage cost; Vault token issuance rate limited | Increase Vault secret TTL to minimum 1h: `vault write <path>/config default_ttl=1h`; adjust ESO refreshInterval accordingly | Set Vault TTL policy minimums; align `refreshInterval` with TTL — never refresh faster than TTL/2 |
| GCP Secret Manager billable access calls from excessive syncs | Each `AccessSecretVersion` call billed at $0.03 per 10,000; short refreshInterval across many secrets = unexpected monthly bill | GCP Billing console → filter by `Secret Manager API`; `gcloud billing budgets list` for budget alerts | Unexpected GCP bill increase | Extend refreshInterval; use Secret Manager global replication only where needed | Set GCP budget alert at 20% above baseline; review refreshInterval in cost context |
| Vault lease accumulation from ESO tokens not being revoked | ESO tokens not revoked on sync failure; Vault lease storage fills | `vault list sys/leases/lookup/auth/kubernetes/login`; Vault operator metrics `vault.runtime.num_goroutines` growing | Vault storage exhausted; Vault performance degradation | Run `vault lease revoke -prefix auth/kubernetes/login/`; reconfigure ESO to use Vault token TTL-based expiry | Configure Vault auth role `token_ttl` and `token_max_ttl`; enable Vault lease count quota |
| ESO PodDisruptionBudget preventing node drain causing cloud cost waste | ESO PDB blocks node pool scale-down; nodes remain in cluster indefinitely | `kubectl get pdb -n external-secrets`; `kubectl describe pdb external-secrets` — check `minAvailable` | Cloud node cost accumulates; cluster autoscaler blocked | Adjust PDB to allow scale-down: `kubectl patch pdb external-secrets --type=merge -p '{"spec":{"minAvailable":1}}'`; ensure at least 2 ESO replicas | Set `minAvailable: 1` with `replicas: 2`; don't set `minAvailable` equal to replica count |
| Large secrets (e.g., TLS cert chains) synced with high frequency | ESO syncing 50KB+ secrets every 5 minutes; Kubernetes API server write amplification | `kubectl get secrets -A -o json | jq '[.items[] | {name: .metadata.name, size: (.data | to_entries | map(.value | length) | add)}] | sort_by(-.size) | .[0:10]'` | etcd storage pressure; kube-apiserver CPU from large object serialization | Increase refreshInterval for large secrets to 24h; split large bundles into smaller secrets | Audit secret size at creation; set size limit policy for ESO-managed secrets; prefer referencing certs by thumbprint over full chain |
| Prometheus scraping ESO metrics at 15s interval with high cardinality labels | High-cardinality labels (secret name, namespace) in `externalsecret_sync_calls_total` causing Prometheus TSDB growth | `curl -s localhost:8080/metrics | grep externalsecret | wc -l`; Prometheus TSDB size growing | Prometheus memory/disk pressure; slow queries | Reduce metric cardinality: drop high-cardinality labels at scrape time via relabel_configs | Configure ESO `--metrics-addr` and `--enable-leader-election`; use recording rules to aggregate high-cardinality metrics |

## Latency & Performance Degradation Patterns
| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot secret causing provider API rate limit | Hundreds of ExternalSecrets all with `refreshInterval: 1m` pointing to same secret; AWS Secrets Manager `ThrottlingException` | `kubectl get externalsecrets -A -o json \| jq '[.items[] \| select(.spec.data[].remoteRef.key)] \| group_by(.spec.data[].remoteRef.key) \| map({key: .[0].spec.data[].remoteRef.key, count: length}) \| sort_by(-.count) \| .[0:10]'`; `aws cloudwatch get-metric-statistics --namespace AWS/SecretsManager --metric-name ResourceCount` | Many ESO-managed secrets referencing same upstream secret with short refresh intervals | Increase `refreshInterval` to 1h for non-critical secrets; use `ClusterSecretStore` caching layer; request Secrets Manager quota increase |
| ESO controller connection pool exhaustion to kube-apiserver | ESO reconcile queue backs up; secrets not syncing; ESO logs `Timeout: request did not complete within requested timeout` | `kubectl get pods -n external-secrets -o json \| jq '.items[].status.containerStatuses[].restartCount'`; `kubectl top pod -n external-secrets`; ESO logs: `kubectl logs deployment/external-secrets -n external-secrets \| grep timeout` | ESO controller using too many kube-apiserver connections; operator pattern creating excessive watches | Ensure ESO uses shared informers; update ESO to latest version; reduce `--concurrent` flag if set too high |
| JVM-like GC pressure in ESO Go runtime from large secret values | ESO pod memory growing; high GC pause in Go runtime; sync latency increasing | `kubectl top pod -n external-secrets`; Prometheus: `go_gc_duration_seconds{quantile="0.99"}` for ESO pod; `kubectl exec -n external-secrets <pod> -- cat /proc/meminfo \| grep -E 'MemAvailable\|MemTotal'` | Large secret values (TLS cert chains, kubeconfig files) causing Go allocator pressure | Set memory limits on ESO pod; increase `GOMEMLIMIT` env var: `kubectl set env deployment/external-secrets GOMEMLIMIT=512MiB`; split large secrets into smaller ones |
| Reconcile thread pool saturation from namespace explosion | ESO reconcile queue depth growing; `externalsecret_sync_calls_total` metric plateaus; new ExternalSecrets not syncing | `kubectl logs deployment/external-secrets -n external-secrets \| grep 'queue depth'`; Prometheus: `workqueue_depth{name="externalsecret"}` | Too many ExternalSecrets for single controller worker count | Increase ESO worker concurrency: `helm upgrade external-secrets --set concurrent=20`; scale ESO horizontally (HPA on CPU) |
| Slow AWS Secrets Manager response due to cross-region calls | ExternalSecret sync taking > 10s; Kubernetes Secret creation delayed; ESO logs `context deadline exceeded` | `kubectl logs deployment/external-secrets -n external-secrets \| grep 'deadline exceeded'`; test: `time aws secretsmanager get-secret-value --secret-id <name> --region <region>` | ESO SecretStore configured with wrong region; secrets stored in remote region | Update ClusterSecretStore to use region co-located with cluster: `region: us-east-1`; verify with `aws configure get region` |
| CPU steal on ESO pod node | ESO reconcile latency high; CPU throttle counters rising; pod CPU usage low | `kubectl top pod -n external-secrets`; Prometheus: `container_cpu_cfs_throttled_seconds_total{container="external-secrets"}`; `cat /sys/fs/cgroup/cpu/cpu.stat \| grep nr_throttled` | ESO pod CPU limit too low; or node overcommitted | Increase ESO CPU limit; set `priorityClassName: system-cluster-critical`; schedule on dedicated node pool |
| Lock contention from concurrent SecretStore validation webhooks | Many namespace creations simultaneously triggering ESO webhook; webhook calls serializing; API server slow | `kubectl get events -n external-secrets \| grep Webhook`; `kubectl top pod -n external-secrets -l app=external-secrets-webhook` | ESO admission webhook called for every ExternalSecret create/update; high concurrency during namespace bootstrap | Scale ESO webhook deployment: `kubectl scale deployment/external-secrets-webhook --replicas=3 -n external-secrets`; set webhook `failurePolicy: Ignore` for non-critical |
| Serialization overhead from base64-decoded large binary secrets | ESO CPU high during sync; large Kubernetes Secrets slow to create; kube-apiserver write latency elevated | `kubectl get secrets -n <ns> -o json \| jq '[.items[] \| {name: .metadata.name, size: (.data \| to_entries \| map(.value \| length) \| add // 0)}] \| sort_by(-.size) \| .[0:5]'` | Binary secrets (keystores, certs) stored as large base64 blobs; serialization CPU cost | Switch to referencing certs by ARN/path and loading at runtime; avoid storing multi-MB binaries in Kubernetes Secrets; use CSI Secret Store driver for large binary secrets |
| Batch refresh misconfiguration creating resync storms | All ExternalSecrets refreshing at same clock minute (e.g., all created at startup with `refreshInterval: 1h`); burst of provider API calls | `kubectl get externalsecrets -A -o json \| jq '[.items[] \| .status.refreshTime] \| group_by(.[0:16]) \| map({time: .[0][0:16], count: length}) \| sort_by(-.count) \| .[0:5]'` | All ESO resources created simultaneously; all refresh times synchronized | Stagger refresh intervals: use jitter by setting different `refreshInterval` values (1h, 65m, 70m); patch: `kubectl patch es <name> --type=merge -p '{"spec":{"refreshInterval":"65m"}}'` |
| Downstream Vault dependency latency from token renewal storms | ESO Vault syncs slow; Vault audit log shows token issuance spike every N minutes | `kubectl logs deployment/external-secrets -n external-secrets \| grep 'vault'`; `vault audit list`; `vault token lookup <eso-token>` — check TTL | Vault token TTL too short; ESO renewing token too frequently; Vault performance degradation | Set Vault role token TTL to 1h: `vault write auth/kubernetes/role/eso-role token_ttl=1h token_max_ttl=24h`; use Vault agent sidecar for token management |

## Network & TLS Failure Patterns
| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on SecretStore provider connection | ESO logs `x509: certificate has expired or is not yet valid`; all ExternalSecrets in `SecretSyncedError` state | `kubectl logs deployment/external-secrets -n external-secrets \| grep 'x509'`; `openssl s_client -connect secretsmanager.<region>.amazonaws.com:443 2>&1 \| grep notAfter`; `kubectl get secretstores -A -o json \| jq '.items[].status'` | All secret syncs fail; applications cannot get updated secrets; stale secrets may remain working | Rotate SecretStore TLS cert; for AWS: verify AWS root CA bundle in cluster; update trust bundle in SecretStore `caBundle` field; trigger sync: `kubectl annotate es <name> force-sync=$(date +%s)` |
| mTLS client cert rotation failure for Vault SecretStore | ESO cannot authenticate to Vault; logs `client certificate authentication failed`; ExternalSecrets fail | `kubectl get secret <vault-client-cert-secret> -n external-secrets -o json \| jq '.metadata.creationTimestamp'`; `openssl x509 -noout -enddate -in <(kubectl get secret <name> -n external-secrets -o jsonpath='{.data.tls\.crt}' \| base64 -d)` | All Vault-backed ExternalSecrets fail to sync; applications may use stale secret values | Rotate client cert: delete and recreate cert-manager Certificate resource; ESO picks up new cert automatically if mounted as volume with live reload |
| DNS resolution failure for Vault or AWS Secrets Manager endpoint | ESO logs `dial tcp: lookup vault.internal: no such host`; secrets stuck in `NotReady` state | `kubectl exec -n external-secrets <eso-pod> -- nslookup vault.internal`; `kubectl exec -n external-secrets <eso-pod> -- curl -sv https://vault.internal:8200/v1/sys/health` | All secrets from affected provider fail to sync | Fix DNS: verify service entry exists: `kubectl get svc vault -n vault-system`; check CoreDNS: `kubectl get pods -n kube-system -l k8s-app=kube-dns`; update SecretStore `server` with IP as fallback |
| TCP connection exhaustion from ESO to AWS Secrets Manager | AWS Secrets Manager `ConnectionError: dial tcp: connect: connection refused`; ESO retry storm | `kubectl exec -n external-secrets <eso-pod> -- ss -tn \| grep 443 \| wc -l`; `aws cloudwatch get-metric-statistics --namespace AWS/SecretsManager --metric-name SuccessfulRequestCount` | All AWS-backed ExternalSecrets fail; secrets not updated | Reduce ESO concurrent reconciles: `helm upgrade external-secrets --set concurrent=5`; implement exponential backoff; verify VPC endpoint for Secrets Manager exists: `aws ec2 describe-vpc-endpoints \| grep secretsmanager` |
| AWS PrivateLink / VPC endpoint misconfiguration | ESO cannot reach Secrets Manager over private endpoint; traffic leaving VPC via internet; SCP blocking internet egress | `kubectl exec -n external-secrets <eso-pod> -- curl -v https://secretsmanager.us-east-1.amazonaws.com`; `aws ec2 describe-vpc-endpoints --filters Name=service-name,Values=com.amazonaws.us-east-1.secretsmanager` | Secret syncs fail if SCP blocks internet; or data travels over internet (compliance violation) | Create VPC endpoint: `aws ec2 create-vpc-endpoint --service-name com.amazonaws.<region>.secretsmanager --vpc-id <vpc> --subnet-ids <subnets>`; update security group to allow HTTPS from ESO pods |
| Packet loss between ESO pod and provider (Vault/AWS) | Intermittent `context deadline exceeded` in ESO logs; retry metrics spiking | `kubectl exec -n external-secrets <eso-pod> -- ping -c 100 <provider-endpoint>`; `kubectl logs deployment/external-secrets -n external-secrets \| grep -c 'deadline'` | Intermittent sync failures; secrets may fall out of sync for minutes | Investigate CNI/network path; check Cilium/Calico flow logs; increase ESO `--timeout` flag; file cloud provider support case |
| MTU mismatch causing ESO provider request failures | Large secret values (e.g., 50KB JSON) fail to sync while small secrets succeed | `kubectl exec -n external-secrets <eso-pod> -- ping -s 1473 -M do secretsmanager.us-east-1.amazonaws.com` — ICMP fragmentation failure | Large secrets fail; small secrets succeed; inconsistent sync behavior | Fix CNI MTU settings; set Calico/Cilium MTU to 1450 for VxLAN; or reduce max secret value size via provider-side config |
| Firewall/NetworkPolicy blocking ESO egress to provider | All ExternalSecrets in error state after network policy change; ESO logs `connection refused` to provider | `kubectl get networkpolicy -n external-secrets`; `kubectl exec -n external-secrets <eso-pod> -- curl -I https://secretsmanager.us-east-1.amazonaws.com`; `kubectl describe networkpolicy <name> -n external-secrets` | Complete ESO outage; all secrets fail to sync | Add egress NetworkPolicy for ESO namespace: allow TCP 443 to provider CIDR; `kubectl apply -f - <<< '{"apiVersion":"networking.k8s.io/v1","kind":"NetworkPolicy","metadata":{"name":"eso-egress"},...}'` |
| SSL handshake timeout during ESO pod mass restart | All ESO pods restart simultaneously (rolling update); mass TLS handshakes to provider; rate limited | `kubectl get events -n external-secrets \| grep BackOff`; `kubectl logs deployment/external-secrets -n external-secrets \| grep 'TLS handshake timeout'` | Slow ESO startup; all ExternalSecrets enter `Error` state during restart | Stagger ESO rollouts: set `maxSurge: 1, maxUnavailable: 0` in deployment; add `minReadySeconds: 30`; implement connection pre-warming |
| Vault TLS connection reset from idle timeout | ESO Vault connections reset after idle period; next sync request fails and must reconnect | `kubectl logs deployment/external-secrets -n external-secrets \| grep 'connection reset by peer'`; check Vault `default_lease_ttl` and `max_lease_ttl` | Occasional sync delay on reconnect; not critical unless reconnect itself fails | Enable TCP keepalive on ESO-to-Vault connections; reduce Vault `idle_timeout`; ensure ESO reconnect logic is enabled (default in current versions) |

## Resource Exhaustion Patterns
| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of ESO controller pod | ESO pod OOMKilled; ExternalSecrets stop syncing; `kubectl describe pod` shows `OOMKilled`; Kubernetes Secrets become stale | `kubectl describe pod -n external-secrets -l app=external-secrets \| grep -A3 OOMKilled`; `kubectl top pod -n external-secrets`; Prometheus: `container_memory_working_set_bytes{container="external-secrets"}` | Increase memory limit: `helm upgrade external-secrets external-secrets/external-secrets --set resources.limits.memory=512Mi`; restart pod: `kubectl rollout restart deployment/external-secrets -n external-secrets` | Set memory limit 2x observed peak; configure VPA for ESO; monitor `container_memory_working_set_bytes` with alert > 80% |
| Disk full on ESO pod tmp filesystem | ESO fails to create temp files during secret processing; sync fails with `no space left on device` | `kubectl exec -n external-secrets <eso-pod> -- df -h /tmp`; `kubectl exec -n external-secrets <eso-pod> -- ls -lah /tmp` | Clear temp files: `kubectl exec -n external-secrets <eso-pod> -- find /tmp -type f -mmin +60 -delete`; or restart pod | Mount tmpfs for `/tmp` with size limit: `kubectl patch deployment/external-secrets -n external-secrets --type=json -p '[{"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"tmp","emptyDir":{"medium":"Memory","sizeLimit":"100Mi"}}}]'` |
| Kubernetes Secret count approaching etcd quota | etcd `NOSPACE` or `mvcc: database space exceeded`; ESO cannot create new Kubernetes Secrets | `kubectl get secrets -A \| wc -l`; `etcdctl endpoint status --write-out=json \| jq '.[].Status.dbSize'`; `kubectl api-resources --verbs=list --namespaced -o name \| xargs -I{} kubectl get {} -A --ignore-not-found 2>/dev/null \| wc -l` | Delete unused Secrets: `kubectl get secrets -A -o json \| jq '.items[] \| select(.metadata.ownerReferences == null)' \| kubectl delete -f -`; compact etcd | Implement Secret lifecycle management; delete ExternalSecret resources for decommissioned services; set ResourceQuota on Secrets per namespace |
| File descriptor exhaustion in ESO pod | ESO logs `too many open files`; watch connections to Kubernetes API fail | `kubectl exec -n external-secrets <eso-pod> -- cat /proc/1/limits \| grep 'open files'`; `kubectl exec -n external-secrets <eso-pod> -- ls /proc/1/fd \| wc -l` | Restart ESO pod; increase FD limit via SecurityContext: `kubectl patch deployment/external-secrets -n external-secrets --type=json -p '[{"op":"add","path":"/spec/template/spec/containers/0/securityContext","value":{"sysctls":[{"name":"fs.file-max","value":"65536"}]}}]'` | Set `ulimit -n` via container securityContext; monitor FD usage; ensure ESO closes connections after failed syncs |
| Inode exhaustion on ESO node from many small temp files | Node inode exhaustion; other pods on same node fail to create files; `df -i` shows 100% | `df -i <node-mount>`; `find /tmp -type f -user <eso-uid> \| wc -l` on node | Delete stale ESO temp files; restart ESO pod to clear its temp directory | Use `emptyDir` with `medium: Memory` for ESO temp storage; implement proper cleanup in ESO sync loop |
| CPU throttle from low CPU limit on ESO during reconcile burst | ESO reconcile queue drains slowly; sync latency high during peak hours; CPU throttle counter rising | `kubectl top pod -n external-secrets`; Prometheus: `rate(container_cpu_cfs_throttled_seconds_total{container="external-secrets"}[5m])`; `cat /sys/fs/cgroup/cpu/cpu.stat \| grep nr_throttled` | Remove CPU limit or increase significantly: `kubectl set resources deployment/external-secrets -n external-secrets --limits=cpu=2` | Use CPU requests (not limits) for ESO; set request to match average usage; set limit to 4x request for burst headroom |
| Swap exhaustion on ESO node during Go GC | Go GC triggers paging to swap; ESO latency spikes; sync timeouts increase | `free -m` on ESO node; `cat /proc/$(pgrep external-secrets)/status \| grep VmSwap`; `vmstat 1 5` — si/so columns | Disable swap on node; restart ESO pod with `GOMEMLIMIT` set to prevent over-allocation | Set `vm.swappiness=0` on all ESO nodes; set `GOMEMLIMIT` env var on ESO to 90% of memory limit |
| Kubernetes API server rate limit quota exhausted by ESO | ESO requests throttled by `client-go` rate limiter; `429 Too Many Requests` from kube-apiserver; syncs queued | `kubectl logs deployment/external-secrets -n external-secrets \| grep 'throttling request'`; Prometheus: `rest_client_requests_total{code="429",container="external-secrets"}` | Reduce ESO concurrent reconciles; upgrade ESO to version with better rate limiting; increase kube-apiserver `--max-requests-inflight` if feasible | Set `--client-qps` and `--client-burst` flags on ESO to stay within kube-apiserver limits; upgrade ESO for improved client-go rate limiter |
| Network socket buffer exhaustion from ESO watch connections | ESO watch connections to kube-apiserver dropping; ExternalSecrets not updating on Secret change | `kubectl exec -n external-secrets <eso-pod> -- netstat -s \| grep -E 'buffer errors\|receive errors'`; `ss -s` on ESO pod | Increase socket buffer: `sysctl -w net.core.rmem_max=16777216` on node; restart ESO pod | Set socket buffer sysctls on cluster nodes; ensure ESO uses compressed watch responses via `--enable-compression` kube-apiserver flag |
| Ephemeral port exhaustion from ESO provider reconnects | ESO `EADDRNOTAVAIL` connecting to AWS/Vault; `TIME_WAIT` filling port range after rapid reconnects | `ss -s` on ESO node; `netstat -an \| grep 443 \| grep TIME_WAIT \| wc -l`; ESO logs `bind: cannot assign requested address` | Enable port reuse: `sysctl -w net.ipv4.tcp_tw_reuse=1`; reduce reconnect rate; implement connection pooling in ESO provider client | Use persistent HTTP connections to providers (HTTP/1.1 keep-alive or HTTP/2); set `net.ipv4.ip_local_port_range=1024 65535` |

## Distributed Transaction & Event Ordering Failures
| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation from ESO dual-write on race condition | ESO creates Kubernetes Secret and provider secret simultaneously during bootstrap; two versions of secret exist briefly | `kubectl get secret <name> -n <ns> -o json \| jq '{resourceVersion: .metadata.resourceVersion, creationTimestamp: .metadata.creationTimestamp}'`; `kubectl get events -n <ns> \| grep <secret-name>` | Brief window where old and new pod may use different secret values | ESO uses Kubernetes `createOrUpdate` (upsert) semantic — verify correct behavior in ESO version; add `immutable: true` on non-rotating secrets |
| Saga partial failure during secret rotation | Application secret rotation workflow: ESO fetches new secret from Vault, updates K8s Secret, triggers pod rolling restart — restart fails; pods stuck with neither old nor new credentials | `kubectl rollout status deployment/<app>` — stuck; `kubectl logs <app-pod> \| grep auth`; `kubectl get secret <name> -o json \| jq '.metadata.annotations["reconcile.external-secrets.io/data-hash"]'` — check hash | Application pods cycling between old and new credentials; authentication failures | Pause ESO sync: `kubectl annotate externalsecret <name> external-secrets.io/force-sync-`; rollback application deployment to previous version; investigate credential validity |
| Stale secret replay after ESO resync deletes intermediate rotation state | Vault secret rotation generates new version; ESO syncs intermediate version before final; old version briefly cached in K8s Secret | `kubectl get secret <name> -o json \| jq '.metadata.annotations'`; compare ESO `data-hash` with expected hash from Vault: `vault kv get -format=json <path> \| jq '.data.data'` | Applications using intermediate secret version; authentication may fail if intermediate is invalid | Force resync with latest version: `kubectl annotate externalsecret <name> external-secrets.io/force-sync=$(date +%s) --overwrite`; verify hash matches Vault current version |
| Cross-namespace secret dependency causing ordering failure | Namespace A ClusterExternalSecret depends on namespace B secret being created first; ESO creates A before B is ready | `kubectl get clustersecretstores -o json \| jq '.items[].status'`; `kubectl get externalsecrets -A -o json \| jq '[.items[] \| select(.status.conditions[0].reason != "SecretSynced")]'` | Namespace A applications start with missing dependency; crash-loops until B secret exists | Add `initContainer` in dependent namespace apps to wait for secret: `kubectl wait --for=condition=Ready externalsecret/<name>`; restructure ESO to use `ClusterExternalSecret` with explicit ordering |
| Out-of-order secret version delivery from provider caching | AWS Secrets Manager `GetSecretValue` returns cached previous version during ESO sync; K8s Secret updated with stale data | `aws secretsmanager get-secret-value --secret-id <name> --version-stage AWSCURRENT \| jq '.VersionId'`; compare with `kubectl get secret <name> -o json \| jq '.metadata.annotations["reconcile.external-secrets.io/data-hash"]'` | Applications receive outdated credentials; authentication failures if secret was rotated for breach | Force ESO to use `AWSCURRENT` version explicitly: set `remoteRef.version: AWSCURRENT` in ExternalSecret spec; trigger force-sync |
| At-least-once sync causing Kubernetes Secret write amplification | ESO syncs same secret multiple times in rapid succession (e.g., after pod restart); each sync triggers kube-apiserver write event; controllers watching secret restart unnecessarily | `kubectl get events -n <ns> \| grep <secret-name> \| grep -c Updated`; Prometheus: `externalsecret_sync_calls_total{name="<es-name>"}` — abnormally high | Unnecessary pod restarts if deployment `envFrom` uses secret; kube-apiserver write pressure | ESO uses `data-hash` annotation to skip writes if content unchanged — verify annotation is present: `kubectl get secret <name> -o json \| jq '.metadata.annotations["reconcile.external-secrets.io/data-hash"]'`; upgrade ESO if annotation missing |
| Compensating secret deletion failing during namespace cleanup | Namespace deletion removes ExternalSecret but ESO fails to delete managed Kubernetes Secret (finalizer stuck) | `kubectl get externalsecrets -n <ns>`; `kubectl get secrets -n <ns> \| grep <managed-prefix>`; `kubectl get ns <ns> -o json \| jq '.metadata.finalizers'` | Namespace stuck in `Terminating`; secret data persists beyond expected lifecycle | Remove stuck finalizer: `kubectl patch externalsecret <name> -n <ns> --type=json -p '[{"op":"remove","path":"/metadata/finalizers"}]'`; manually delete orphaned secrets |
| Distributed lock expiry during long-running Vault secret renewal | ESO leader election lease expires during slow Vault call; two ESO replicas both attempt to write same Kubernetes Secret | `kubectl get lease -n external-secrets`; `kubectl logs -n external-secrets -l app=external-secrets \| grep 'leader'`; `kubectl get events -n external-secrets \| grep LeaderElection` | Two ESO instances racing to write same secret; potential inconsistency if Vault returns different values per call | Increase ESO leader election lease duration: `helm upgrade external-secrets --set leaderElect=true --set leaderElectionLeaseDuration=60s`; ensure Vault responses are deterministic for same secret version |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: one tenant's ESO sync loop monopolizing controller goroutines | `kubectl top pods -n external-secrets` — ESO pod at 100% CPU; `kubectl get externalsecrets -A -o json | jq '[.items[] | select(.spec.refreshInterval=="0")]'` — zero-interval syncs | Other tenants' ExternalSecrets not syncing on schedule; Kubernetes Secrets stale | `kubectl patch externalsecret <name> -n <tenant-ns> --type=merge -p '{"spec":{"refreshInterval":"1h"}}'` to stop zero-interval sync | Enforce minimum refreshInterval via OPA/Kyverno policy: reject ExternalSecrets with `refreshInterval < 1m`; set ESO controller `--concurrent` limit |
| Memory pressure: large secret values from one tenant inflating ESO pod memory | `kubectl top pods -n external-secrets` — memory growing; `kubectl get externalsecrets -A -o json | jq '.items[] | .status.binding.name'`; check for secrets with large values: `kubectl get secret <name> -o json | jq '.data | to_entries[] | .value | length'` | ESO pod OOM-killed; all tenants lose secret sync capability | Restart ESO pod: `kubectl delete pod -n external-secrets <eso-pod>`; identify and quarantine large-secret ExternalSecret: `kubectl annotate externalsecret <name> -n <ns> 'reconcile.external-secrets.io/paused=true'` | Enforce secret size limits via Vault/AWS Secrets Manager policies; add ESO memory limit with headroom for max secret size |
| Disk I/O saturation: ESO audit/debug logging at trace level filling node disk | Node disk usage spike; `kubectl exec -n external-secrets <eso-pod> -- df -h /` — high usage; ESO logs streaming at DEBUG | Other pods on same node evicted due to disk pressure; node disk pressure taint applied | Reduce ESO log verbosity: `kubectl set env deployment/external-secrets -n external-secrets LOGLEVEL=info`; rotate logs: `kubectl exec -n external-secrets <eso-pod> -- kill -USR1 1` | Set ESO log level via Helm: `--set extraEnv[0].name=LOGLEVEL --set extraEnv[0].value=warn`; configure log rotation; add node disk pressure monitoring |
| Network bandwidth monopoly: ESO syncing many large secrets simultaneously on startup | Node network interface saturated after ESO pod restart triggers full re-sync of all ExternalSecrets | `kubectl get events -n external-secrets | grep ExternalSecret`; `iftop` on ESO pod network namespace shows burst traffic | Other pods on same node experience network latency; CNI plugin buffer overflow | Stagger ESO re-sync: `kubectl annotate externalsecret <name> -n <ns> 'external-secrets.io/force-sync=$(date +%s)'` individually; increase `--concurrent` gradually | Configure ESO startup sync stagger; use `syncPolicy.initial` to delay initial sync; separate high-volume tenants to dedicated ESO deployment |
| Connection pool starvation: ESO SecretStore sharing single Vault connection for all tenants | `vault audit list`; `vault read sys/leases/count` — many leases from single ESO AppRole; Vault connection log shows single client serving all requests | All tenants share single Vault token throughput; Vault rate limit hit causes global sync failure | Per-tenant SecretStore isolation: `kubectl get secretstore -A`; create per-namespace Vault AppRole; `vault write auth/approle/role/<tenant> policies=<tenant-policy>` | Deploy per-tenant SecretStore with dedicated Vault credentials; avoids shared rate limit ceiling; enables per-tenant Vault policy enforcement |
| Quota enforcement gap: no limit on number of ExternalSecrets per namespace | `kubectl get externalsecrets -A | awk '{print $1}' | sort | uniq -c | sort -rn` — one namespace has thousands; ESO controller queue depth high | ESO reconcile loop overwhelmed; all tenants' sync latency increases | Identify and batch-delete unnecessary ExternalSecrets: `kubectl get externalsecrets -n <tenant-ns> -o name | xargs kubectl delete -n <tenant-ns>` | Apply ResourceQuota: `kubectl apply -f - <<EOF\napiVersion: v1\nkind: ResourceQuota\nmetadata:\n  name: eso-quota\nspec:\n  hard:\n    count/externalsecrets.external-secrets.io: "50"\nEOF` |
| Cross-tenant data leak via ClusterExternalSecret with broad namespaceSelector | `kubectl get clusterexternalsecrets -A -o yaml | grep -A5 'namespaceSelector'`; verify selector is not `{}` (matches all namespaces) | ClusterExternalSecret with empty namespaceSelector creates Kubernetes Secret in all namespaces | Tenant A's secrets visible/overwritten in Tenant B's namespace; potential credential confusion | Immediately patch ClusterExternalSecret to restrict namespaces: `kubectl patch clusterexternalsecret <name> --type=merge -p '{"spec":{"namespaceSelector":{"matchLabels":{"team":"<specific-team>"}}}}'` |
| Rate limit bypass: one tenant's automated secret rotation triggering ESO re-sync storm | `aws cloudwatch get-metric-statistics --metric-name NumberOfApiCallsToSecretsManager`; spike at rotation time; `kubectl get events -A | grep ExternalSecret | grep -c Synced` | Automated rotation tool rotates all secrets simultaneously; ESO detects change and re-syncs everything at once | AWS Secrets Manager API rate limit hit; ESO sync fails for all tenants | Stagger secret rotation across tenants; use AWS Secrets Manager rotation schedules with randomized start time; configure ESO `refreshInterval` to poll rather than react to change notifications |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure: ESO Prometheus metrics not exported | Prometheus shows no `externalsecret_*` metrics; sync failures invisible | ESO metrics port (8080) not configured in ServiceMonitor; NetworkPolicy blocks Prometheus scrape | `kubectl exec -n external-secrets <eso-pod> -- curl -s localhost:8080/metrics | grep externalsecret`; check ServiceMonitor: `kubectl get servicemonitor -n external-secrets` | Create ServiceMonitor: `kubectl apply -f - <<EOF\napiVersion: monitoring.coreos.com/v1\nkind: ServiceMonitor\nmetadata:\n  name: external-secrets\n  namespace: external-secrets\nspec:\n  selector:\n    matchLabels:\n      app: external-secrets\n  endpoints:\n  - port: metrics\nEOF` |
| Trace sampling gap: ESO Vault API calls not traced | Vault performance issues causing ESO sync delays not visible in APM; root cause attribution impossible | ESO does not emit OpenTelemetry traces for provider API calls; Vault audit log not correlated | Correlate: `vault audit list`; `kubectl logs -n external-secrets <eso-pod> | grep 'duration\|latency'`; check `externalsecret_provider_api_calls_count` metric | Enable Vault request logging: `vault audit enable file file_path=/vault/audit/log`; measure ESO-to-Vault latency via `externalsecret_reconcile_duration` |
| Log pipeline silent drop: ESO sync error logs going to /dev/null | ExternalSecret sync failures not appearing in centralized log system; operators unaware of credential sync issues | ESO deployed with `--zap-log-level=error` writing to stderr which is not picked up by log aggregator | `kubectl logs -n external-secrets -l app=external-secrets --since=1h | grep -i error`; verify Fluentd/Fluent Bit collecting from `external-secrets` namespace | Configure log aggregator to collect from `external-secrets` namespace; set ESO log format to JSON: `--zap-encoder=json`; add namespace to Fluentd `<source>` config |
| Alert rule misconfiguration: ExternalSecret sync failure alert using wrong metric | ExternalSecrets failing silently; `externalsecret_sync_calls_error` metric exists but alert uses wrong label selector | Alert configured as `externalsecret_sync_calls_error > 0` without `namespace` label; multi-tenant alerts noisy and ignored | `kubectl get externalsecrets -A -o json | jq '[.items[] | select(.status.conditions[0].status=="False")]'` — direct check for not-synced ESOs | Fix alert: `externalsecret_sync_calls_error{name!=""} > 0`; add per-namespace granularity and routing to owning team |
| Cardinality explosion: per-secret-key metrics labels from ESO custom instrumentation | Prometheus OOM; custom ESO metrics with secret key names as labels have thousands of series | Application-side instrumentation emitting `secret_accessed{key="db_password_tenant_a_prod"}` per access | `curl http://prometheus:9090/api/v1/query?query=count({__name__=~"secret.*"})` — check cardinality; identify label values: `label_values(secret_accessed, key)` | Remove secret key name from metric labels; use secret name/namespace aggregation only; never include secret values or key names in labels |
| Missing health endpoint: no alerting on ESO SecretStore authentication failures | SecretStore Vault/AWS auth token expires; all ExternalSecrets fail to sync; no alert fires | ESO updates SecretStore status but no Prometheus metric for `SecretStore.Ready == False` | `kubectl get secretstores -A -o json | jq '.items[] | select(.status.conditions[0].status=="False") | .metadata'` | Create alert from ESO `externalsecret_provider_api_calls_count{status="error"}` > 0 for 5 minutes; or: `kubectl get secretstore -o json | jq '.status.conditions[] | select(.type=="Ready" and .status=="False")'` |
| Instrumentation gap: no metrics for ESO secret version drift | ESO syncing secret but application using older cached version; inconsistency not detected | No metric comparing ESO-synced secret version with provider's latest version; ESO only tracks sync timestamp | `aws secretsmanager get-secret-value --secret-id <name> --version-stage AWSCURRENT | jq '.VersionId'`; compare with `kubectl get secret <name> -o json | jq '.metadata.annotations'` | Add custom monitor: Lambda/CronJob comparing ESO-synced secret hash with provider's current version hash; alert on mismatch persisting > 5 minutes |
| Alertmanager / PagerDuty outage masking ExternalSecret credential expiry | Production credentials expired; ESO sync showing errors; no page sent; applications crashing | Alertmanager pod OOM-killed; alert routing config expired; PagerDuty key rotated without updating secret | `kubectl get pods -n monitoring | grep alertmanager`; direct check: `kubectl get externalsecrets -A -o json | jq '[.items[] | select(.status.conditions[0].reason!="SecretSynced")] | length'` | Implement backup health check: CronJob every 5 minutes checking `kubectl get externalsecrets -A | grep -c False` and SNS alert if > 0; independent of Prometheus/Alertmanager |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| ESO minor version upgrade breaking ExternalSecret API behavior | After ESO upgrade, ExternalSecrets stop syncing; new version changed default `refreshInterval` or sync logic | `helm list -n external-secrets`; `kubectl get externalsecrets -A -o json | jq '[.items[] | select(.status.conditions[0].status=="False")]'`; check ESO release notes | Rollback Helm release: `helm rollback external-secrets -n external-secrets`; verify: `kubectl rollout status deployment/external-secrets -n external-secrets` | Pin ESO Helm chart version; review release notes for breaking changes; test upgrade in staging cluster first |
| ESO major version upgrade (v0.8 → v0.9): CRD schema changes requiring migration | Existing ExternalSecret CRDs incompatible with new schema; objects stuck in invalid state after upgrade | `kubectl get externalsecrets -A -o json | jq '.items[0].apiVersion'`; `kubectl explain externalsecret.spec` — check if fields exist; `kubectl get events -n external-secrets | grep 'invalid'` | Rollback ESO deployment: `helm rollback external-secrets -n external-secrets`; CRD rollback: restore from `kubectl get crd externalsecrets.external-secrets.io -o yaml` backup | Backup all ExternalSecret objects before upgrade: `kubectl get externalsecrets -A -o yaml > eso-backup.yaml`; test migration script on staging; apply CRD upgrades separately before controller upgrade |
| Schema migration: ExternalSecret spec field renamed between ESO versions | `spec.data[].remoteRef.version` renamed to `spec.data[].remoteRef.versionId`; existing objects use old field name | `kubectl get externalsecrets -A -o json | jq '.items[] | select(.spec.data[].remoteRef.version != null)'` — old field usage | Patch affected ExternalSecrets: `kubectl get externalsecrets -A -o json | jq '.items[]' | sed 's/"version"/"versionId"/g' | kubectl apply -f -` | Add field rename detection to CI: `kubectl apply --dry-run=server -f externalsecret.yaml` will catch schema validation errors |
| Rolling upgrade version skew: old and new ESO pods running simultaneously with conflicting reconcile logic | Two ESO pods with different versions both reconciling same ExternalSecret; conflicting writes to Kubernetes Secret | `kubectl get pods -n external-secrets -o custom-columns='NAME:.metadata.name,IMAGE:.spec.containers[0].image'`; `kubectl get events -n <ns> | grep <secret-name> | grep -c Updated` — high update count | Scale down old deployment: `kubectl scale deployment external-secrets -n external-secrets --replicas=0`; deploy new version only | Use `Recreate` deployment strategy for ESO upgrades to avoid version skew; or use leader election to ensure only one controller active |
| Zero-downtime migration: switching from Vault to AWS Secrets Manager with no downtime | During provider migration, some ExternalSecrets pointing to Vault, others to AWS SM; applications getting inconsistent values | `kubectl get secretstores -A -o yaml | grep -E 'vault\|aws'`; `kubectl get externalsecrets -A -o json | jq '.items[] | .spec.secretStoreRef.name'` | Revert all ExternalSecrets to Vault SecretStore: `kubectl get externalsecrets -A -o json | jq -r '.items[] | .metadata.namespace + " " + .metadata.name' | xargs -L1 bash -c 'kubectl patch externalsecret $2 -n $1 ...'` | Dual-provision secrets in both Vault and AWS SM before migration; test each ExternalSecret individually; cut over one namespace at a time |
| Config format change: ESO SecretStore auth config field restructured | After ESO upgrade, SecretStore shows `InvalidProviderConfig` status; auth fields moved under new nested key | `kubectl describe secretstore <name> -n <ns>`; `kubectl get secretstore <name> -o yaml | jq '.spec.provider'` — check field structure against new ESO docs | Apply updated SecretStore with new field structure: `kubectl apply -f updated-secretstore.yaml`; verify: `kubectl get secretstore <name> -o jsonpath='{.status.conditions[0].status}'` | Test SecretStore config validation: `kubectl apply --dry-run=server -f secretstore.yaml`; review ESO changelog for provider config format changes |
| Data format incompatibility: secret key naming convention changed in provider | Vault secret path renamed; ExternalSecret using old path returns 404; Kubernetes Secret cleared | `kubectl describe externalsecret <name> -n <ns>`; `vault kv get <old-path>` vs `vault kv get <new-path>` — verify path exists | Update ExternalSecret remoteRef: `kubectl patch externalsecret <name> -n <ns> --type=json -p '[{"op":"replace","path":"/spec/data/0/remoteRef/key","value":"<new-path>"}]'` | Maintain backward-compatible Vault paths during migration (keep old path pointing to same secret); use Vault `kv put` aliases; update ExternalSecret before deleting old Vault path |
| Feature flag rollout causing ESO sync regression: new caching behavior enabled | After enabling ESO provider response caching, secrets not updating after rotation in external system | `kubectl get configmap -n external-secrets external-secrets-config -o yaml`; `kubectl logs -n external-secrets -l app=external-secrets | grep cache` | Disable caching: `kubectl set env deployment/external-secrets -n external-secrets ESO_CACHE_TTL=0`; force re-sync: `kubectl annotate externalsecret <name> -n <ns> external-secrets.io/force-sync=$(date +%s)` | Test cache behavior with secret rotation in staging; set cache TTL shorter than secret rotation frequency; add `force-sync` annotation to post-rotation runbook |

## Kernel/OS & Host-Level Failure Patterns

| Pattern | Symptoms | Detection | ESO-Specific Impact | Remediation |
|---------|----------|-----------|-------------------|-------------|
| OOM Kill on ESO controller | Controller pod evicted, `OOMKilled` in `kubectl describe pod -n external-secrets`, ExternalSecret sync stops across all namespaces | `dmesg \| grep -i "oom.*external-secrets"` ; `kubectl get events -n external-secrets --field-selector reason=OOMKilling` | All secret syncs halt; workloads read stale secrets until controller restarts | Increase controller memory limit: `kubectl patch deploy -n external-secrets external-secrets -p '{"spec":{"template":{"spec":{"containers":[{"name":"external-secrets","resources":{"limits":{"memory":"512Mi"}}}]}}}}'` ; reduce `--concurrent` (default 1) to lower peak RSS |
| Inode exhaustion on controller node | `no space left on device` in controller logs despite disk space available; new ExternalSecret CRDs fail to write status | `df -i /var/lib/kubelet \| awk 'NR==2{print $5}'` ; `kubectl logs -n external-secrets deploy/external-secrets \| grep -c "no space left"` | Controller cannot update ExternalSecret status conditions; webhook cert renewal may fail if writing temp files | Clear orphaned container logs: `find /var/log/containers -name "*.log" -size +100M -exec truncate -s 0 {} \;` ; cordon node and reschedule ESO pods |
| CPU steal >15% on controller node | Secret sync latency spikes (`externalsecret_sync_calls_total` rate drops), reconcile loop slows, provider API call timeouts | `mpstat -P ALL 1 3 \| awk '$NF<85{print "steal:",$11}'` ; `kubectl top pod -n external-secrets` | Provider API calls (Vault/AWS/GCP/Azure) timeout due to slow TLS handshakes; secrets go stale | Migrate ESO controller to dedicated node pool: `kubectl patch deploy -n external-secrets external-secrets -p '{"spec":{"template":{"spec":{"nodeSelector":{"workload":"control-plane"}}}}}'` |
| NTP clock skew >5s | AWS STS `SignatureDoesNotMatch` errors; Vault token validation fails with `token not yet valid`; GCP OAuth token rejected | `chronyc tracking \| grep "System time"` ; `kubectl logs -n external-secrets deploy/external-secrets \| grep -i "signature\|clock\|skew"` | All provider authentications fail simultaneously; every ExternalSecret enters `SecretSyncedError` | Fix NTP: `systemctl restart chronyd` ; verify with `chronyc sources -v` ; restart ESO controller to clear cached auth tokens |
| File descriptor exhaustion | `too many open files` in ESO logs; gRPC connections to Vault fail; webhook serving errors | `kubectl exec -n external-secrets deploy/external-secrets -- cat /proc/1/limits \| grep "open files"` ; `ls /proc/$(pgrep external-secrets)/fd \| wc -l` | Cannot open new connections to secret providers; webhook rejects admission requests for Secret objects | Increase fd limit in deployment: add `securityContext.sysctls` or set ulimit via init container; reduce `--concurrent` to lower connection count |
| Conntrack table full on node | `nf_conntrack: table full, dropping packet` in dmesg; intermittent provider API failures from ESO; some syncs succeed while others timeout | `sysctl net.netfilter.nf_conntrack_count net.netfilter.nf_conntrack_max` ; `dmesg \| grep conntrack` | ESO controller connections to external providers (AWS endpoints, Vault, Azure) randomly drop; sync becomes non-deterministic | `sysctl -w net.netfilter.nf_conntrack_max=262144` ; `conntrack -D -s $(kubectl get pod -n external-secrets -o jsonpath='{.items[0].status.podIP}')` to flush stale entries |
| Kernel panic / node crash | ESO controller pod disappears; `NotReady` node event; all in-flight provider API calls aborted; leader election lock held until TTL | `kubectl get nodes -o wide \| grep NotReady` ; `kubectl get lease -n external-secrets` ; `journalctl -k --since=-10min \| grep -i panic` | Leader lock held by dead pod delays new controller start by lease duration (default 15s); secrets stale during gap | Verify new leader elected: `kubectl get lease -n external-secrets -o yaml` ; force-delete stuck pod: `kubectl delete pod -n external-secrets <pod> --grace-period=0 --force` |
| NUMA imbalance causing latency | ESO reconcile latency p99 spikes; provider API calls show bimodal latency distribution; CPU utilization appears low but concentrated on one NUMA node | `numastat -p $(pgrep external-secrets)` ; `kubectl logs -n external-secrets deploy/external-secrets \| grep "reconcile duration"` | Cross-NUMA memory access adds 50-100ms to each provider API call and secret decryption; aggregate sync time increases | Pin ESO pods to single NUMA node via topology manager: `kubelet --topology-manager-policy=single-numa-node` ; or use `topologySpreadConstraints` in ESO deployment |

## Deployment Pipeline & GitOps Failure Patterns

| Pattern | Symptoms | Detection | ESO-Specific Impact | Remediation |
|---------|----------|-----------|-------------------|-------------|
| Image pull failure on ESO upgrade | ESO controller pods stuck in `ImagePullBackOff`; existing secrets not refreshed; new ExternalSecrets not reconciled | `kubectl get pods -n external-secrets -o wide \| grep ImagePull` ; `kubectl describe pod -n external-secrets -l app.kubernetes.io/name=external-secrets \| grep "Failed to pull"` | Complete secret sync outage; rotation deadlines missed; workloads using `refreshInterval` get stale data | Verify image exists: `crane manifest ghcr.io/external-secrets/external-secrets:v0.9.x` ; check pull secret: `kubectl get secret -n external-secrets regcred -o yaml` ; rollback: `kubectl rollout undo deploy/external-secrets -n external-secrets` |
| Registry auth expired for ESO image | `401 Unauthorized` on image pull; ESO upgrade blocked; old pods continue running but cannot be rescheduled | `kubectl get events -n external-secrets --field-selector reason=Failed \| grep -i "unauthorized\|401"` ; `kubectl get secret -n external-secrets -o json \| jq '.items[].metadata.name'` | If old pod gets evicted (node drain, OOM), ESO cannot restart; secret sync stops permanently | Rotate registry credential: `kubectl create secret docker-registry regcred -n external-secrets --docker-server=ghcr.io --docker-username=<user> --docker-password=<pat> --dry-run=client -o yaml \| kubectl apply -f -` |
| Helm values drift from live state | ESO CRDs out of sync with controller version; `CustomResourceDefinition` schema validation rejects new ExternalSecret fields; `helm diff` shows unexpected changes | `helm diff upgrade external-secrets external-secrets/external-secrets -n external-secrets -f values.yaml` ; `kubectl get crd externalsecrets.external-secrets.io -o jsonpath='{.spec.versions[*].name}'` | CRD version mismatch causes webhook rejection of new ExternalSecrets; existing secrets sync but new ones cannot be created | `helm upgrade external-secrets external-secrets/external-secrets -n external-secrets -f values.yaml --force` ; if CRDs diverged: `kubectl apply -f https://raw.githubusercontent.com/external-secrets/external-secrets/main/deploy/crds/bundle.yaml` |
| GitOps sync stuck on ESO namespace | ArgoCD/Flux shows `OutOfSync` or `SyncFailed` for external-secrets namespace; controller not updated; webhook cert not rotated | `kubectl get application -n argocd external-secrets -o jsonpath='{.status.sync.status}'` ; `flux get kustomization external-secrets` | Controller runs old version with known bugs; webhook cert may expire if cert-manager integration stalled | Force sync: `argocd app sync external-secrets --force` or `flux reconcile kustomization external-secrets` ; check for finalizer deadlocks: `kubectl get externalsecret -A -o json \| jq '.items[] \| select(.metadata.deletionTimestamp)'` |
| PDB blocking ESO controller rollout | `Waiting for deployment "external-secrets" rollout` hangs; `kubectl rollout status` times out; PDB prevents old pod termination | `kubectl get pdb -n external-secrets` ; `kubectl get deploy external-secrets -n external-secrets -o jsonpath='{.status.conditions[*].message}'` | ESO runs mixed old/new versions; leader election may flip between versions causing inconsistent reconciliation behavior | Temporarily relax PDB: `kubectl patch pdb -n external-secrets external-secrets-pdb -p '{"spec":{"minAvailable":0}}'` ; complete rollout; restore PDB |
| Blue-green deploy leaves orphan SecretStores | Old SecretStore/ClusterSecretStore objects reference decommissioned provider endpoints; ExternalSecrets bound to old stores fail silently | `kubectl get secretstore -A -o json \| jq '.items[] \| select(.status.conditions[0].status=="False") \| .metadata.name'` ; `kubectl get clustersecretstore -o json \| jq '.items[].status'` | Secrets referencing orphaned stores stop syncing; no error until `refreshInterval` elapses and sync fails | Delete orphan stores: `kubectl delete secretstore -n <ns> <old-store>` ; update ExternalSecrets to reference new store: `kubectl patch externalsecret <name> -n <ns> --type merge -p '{"spec":{"secretStoreRef":{"name":"<new-store>"}}}'` |
| ConfigMap drift in ESO controller config | Controller args in live deployment differ from Git source; `--concurrent` or `--metrics-addr` changed manually | `kubectl get deploy -n external-secrets external-secrets -o yaml \| diff - <(helm template external-secrets external-secrets/external-secrets -f values.yaml)` | Unexpected reconcile concurrency causes provider rate limiting; metrics endpoint change breaks Prometheus scraping | Reapply from Git source: `helm upgrade external-secrets external-secrets/external-secrets -n external-secrets -f values.yaml` ; audit with `kubectl diff -f <manifest>` |
| Feature flag misconfiguration in ESO | `--enable-cluster-store-reconciler` or `--enable-cluster-external-secret-reconciler` flags set to false; ClusterExternalSecret silently ignored | `kubectl get deploy -n external-secrets external-secrets -o jsonpath='{.spec.template.spec.containers[0].args}'` ; `kubectl get clusterexternalsecret -o json \| jq '.items[].status'` | ClusterExternalSecrets not reconciled across namespaces; secrets missing in target namespaces; workloads fail with missing env vars | Correct controller args: `kubectl edit deploy -n external-secrets external-secrets` ; verify flags against docs: `kubectl exec -n external-secrets deploy/external-secrets -- /external-secrets --help` |

## Service Mesh & API Gateway Edge Cases

| Pattern | Symptoms | Detection | ESO-Specific Impact | Remediation |
|---------|----------|-----------|-------------------|-------------|
| Circuit breaker tripping on provider API | Istio/Envoy circuit breaker opens for Vault/AWS endpoints; ESO logs show `upstream connect error or disconnect/reset before headers` | `kubectl logs -n external-secrets deploy/external-secrets \| grep "upstream connect error"` ; `istioctl proxy-config cluster -n external-secrets deploy/external-secrets \| grep "vault\|secretsmanager"` | All ExternalSecrets targeting the tripped provider enter sync failure; circuit breaker prevents recovery attempts | Tune outlier detection: `kubectl apply -f - <<< '{"apiVersion":"networking.istio.io/v1","kind":"DestinationRule","metadata":{"name":"vault-dr","namespace":"external-secrets"},"spec":{"host":"vault.vault.svc.cluster.local","trafficPolicy":{"outlierDetection":{"consecutive5xxErrors":10,"interval":"30s","baseEjectionTime":"60s"}}}}'` |
| Rate limiting on provider API via mesh | Envoy rate limit filter blocks ESO requests to AWS Secrets Manager; `429 Too Many Requests` in ESO logs; partial secrets synced | `kubectl logs -n external-secrets deploy/external-secrets \| grep -c "429\|rate limit"` ; `istioctl proxy-config log deploy/external-secrets -n external-secrets --level=debug 2>&1 \| grep ratelimit` | Secrets sync partially -- some ExternalSecrets succeed while others fail depending on rate limit bucket timing | Exempt ESO from rate limiting via EnvoyFilter; or reduce ESO sync frequency: `kubectl patch externalsecret <name> -n <ns> --type merge -p '{"spec":{"refreshInterval":"10m"}}'` across affected resources |
| Stale service discovery for Vault endpoint | Mesh DNS cache returns old Vault pod IPs after Vault rolling restart; ESO connects to terminated pods; connection timeouts | `istioctl proxy-config endpoint deploy/external-secrets -n external-secrets \| grep vault` ; `kubectl logs -n external-secrets deploy/external-secrets \| grep "connection refused\|context deadline exceeded"` | ESO cannot reach Vault; all Vault-backed ExternalSecrets stop syncing; `SecretSyncedError` status propagates | Force endpoint refresh: `istioctl proxy-config endpoint deploy/external-secrets -n external-secrets --cluster "outbound\|8200\|\|vault.vault.svc.cluster.local" -o json` ; restart ESO sidecar: `kubectl rollout restart deploy/external-secrets -n external-secrets` |
| mTLS handshake failure between ESO and provider | `TLS handshake error` in ESO logs; Vault/provider rejects ESO client cert; SPIFFE identity mismatch | `kubectl logs -n external-secrets deploy/external-secrets \| grep -i "tls\|handshake\|certificate"` ; `istioctl authn tls-check deploy/external-secrets.external-secrets vault.vault.svc.cluster.local` | ESO cannot authenticate to in-cluster providers (Vault, custom webhook stores); only external HTTPS providers unaffected | Verify PeerAuthentication allows ESO namespace: `kubectl get peerauthentication -A -o yaml \| grep -A5 external-secrets` ; check SPIFFE trust domain: `istioctl proxy-config secret deploy/external-secrets -n external-secrets` |
| Retry storm from ESO through mesh | ESO retry + Envoy retry = exponential request multiplication to provider; provider returns 503; mesh amplifies load | `kubectl logs -n external-secrets deploy/external-secrets \| grep -c "retrying"` ; `istioctl proxy-config route deploy/external-secrets -n external-secrets -o json \| jq '.[].virtualHosts[].retryPolicy'` | Provider overwhelmed by 3x-9x amplified requests; rate limiting triggered; cascade affects other tenants using same provider | Disable Envoy retries for provider routes: apply VirtualService with `retries: {attempts: 0}` for provider hosts; ESO built-in retry is sufficient |
| gRPC metadata loss through mesh proxy | ESO webhook gRPC calls lose custom metadata headers when transiting Envoy sidecar; admission webhook rejects requests with missing auth context | `kubectl logs -n external-secrets deploy/external-secrets-webhook \| grep "missing.*header\|auth.*context"` ; `istioctl proxy-config listener deploy/external-secrets-webhook -n external-secrets -o json \| jq '.[].filterChains'` | Webhook cannot validate ExternalSecret/SecretStore CRD submissions; all create/update operations rejected | Add header preservation in EnvoyFilter for webhook port; or exclude webhook port from mesh: `kubectl patch deploy -n external-secrets external-secrets-webhook -p '{"spec":{"template":{"metadata":{"annotations":{"traffic.sidecar.istio.io/excludeInboundPorts":"10250"}}}}}'` |
| Trace context propagation breaks ESO audit | Distributed tracing context not propagated from ESO to provider calls; cannot correlate secret fetch latency with provider-side spans | `kubectl logs -n external-secrets deploy/external-secrets \| grep -i "traceparent\|trace-id"` ; `jaeger query --service=external-secrets \| grep "missing parent"` | Cannot trace slow secret syncs to provider-side issues; incident triage requires manual correlation of ESO logs with provider audit logs | Enable trace propagation in ESO via environment variables: `kubectl set env deploy/external-secrets -n external-secrets OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 OTEL_SERVICE_NAME=external-secrets` |
| Load balancer health check hitting ESO webhook | Cloud LB or ingress health checks probe ESO webhook endpoint; webhook returns 400 on non-admission requests; LB marks backend unhealthy | `kubectl logs -n external-secrets deploy/external-secrets-webhook \| grep "health\|probe\|400"` ; `kubectl get svc -n external-secrets external-secrets-webhook -o yaml \| grep -A3 healthCheck` | LB removes ESO webhook from rotation; all ExternalSecret admission requests fail; new secrets cannot be created or updated | Configure dedicated health endpoint: ensure `/healthz` path returns 200; update Service annotations for cloud LB health check path: `kubectl annotate svc -n external-secrets external-secrets-webhook service.beta.kubernetes.io/aws-load-balancer-healthcheck-path=/healthz` |
