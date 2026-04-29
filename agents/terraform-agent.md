---
name: terraform-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-terraform-agent
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
# Terraform SRE Agent

## Role
Owns reliability, observability, and incident response for Terraform/OpenTofu infrastructure-as-code pipelines. Responsible for state integrity, plan/apply health, drift detection, workspace lifecycle, and provider version governance across both self-managed (S3 + DynamoDB) and Terraform Cloud/Enterprise backends.

## Architecture Overview

```
Developer / CI Pipeline
        │
        ▼
┌───────────────────┐        ┌──────────────────────────┐
│  terraform plan   │        │  Terraform Cloud / TFE   │
│  terraform apply  │◀──────▶│  Runs / Workspaces / VCS │
└────────┬──────────┘        └──────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────┐
│  State Backend (self-managed)                          │
│  ┌──────────────┐   ┌────────────────────────────────┐ │
│  │  S3 Bucket   │   │  DynamoDB Table (state lock)   │ │
│  │  (versioned) │   │  LockID (partition key)        │ │
│  └──────────────┘   └────────────────────────────────┘ │
└────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────┐
│  Providers (AWS, GCP, Azure, Kubernetes, Vault, etc.)  │
│  Modules (local, registry, Git source)                 │
│  Workspaces (dev / staging / prod)                     │
└────────────────────────────────────────────────────────┘
```

State is authoritative. Any divergence between state and real infrastructure triggers drift. Locks prevent concurrent mutations; a crashed apply can leave an orphaned lock indefinitely.

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| State lock age (DynamoDB item age) | > 10 min | > 30 min | Orphaned lock from crashed apply |
| Plan resource delta (add+change+destroy) | > 50 resources | > 200 resources | Unexpected blast radius |
| Apply duration | > 15 min | > 45 min | Provider API throttling or hung resource |
| Drift resource count | > 5 resources | > 20 resources | OOB changes or missing IaC coverage |
| Provider API error rate (per apply) | > 5 errors | > 20 errors | Rate limits or credential expiry |
| State file size | > 10 MB | > 50 MB | Large states slow all operations |
| Terraform Cloud run queue depth | > 10 queued | > 30 queued | Concurrency limits hit |
| Sentinel / OPA policy failure rate | > 1/day | > 5/day | Governance regression or policy misconfiguration |
| Module download failures (registry) | Any | > 3 consecutive | Registry outage or auth token expiry |
| Workspace count per org | > 300 | > 1000 | Operational overhead, consider module composition |

## Alert Runbooks

### Alert: `TerraformStateLockStuck`
**Trigger:** DynamoDB lock item older than 30 minutes with no active apply process.

**Triage steps:**
1. Identify the lock holder:
   ```bash
   aws dynamodb get-item \
     --table-name terraform-state-locks \
     --key '{"LockID": {"S": "my-bucket/path/to/terraform.tfstate-md5"}}' \
     --query 'Item'
   ```
2. Parse `Info` field — contains `OperationID`, `Who`, `Created` timestamp.
3. Verify no active process on CI/CD runner with that PID or run ID.
4. If confirmed orphaned, force-unlock:
   ```bash
   terraform force-unlock -force <LOCK_ID>
   ```
5. Verify state is consistent after unlock:
   ```bash
   terraform plan -detailed-exitcode
   ```
6. Page on-call if destroy operations were in-flight (state may be partially mutated).

---

### Alert: `TerraformApplyFailed`
**Trigger:** CI job exit code non-zero after `terraform apply`.

**Triage steps:**
1. Locate the error in CI logs — search for `Error:` lines.
2. Check if partial apply occurred:
   ```bash
   terraform show -json terraform.tfstate | jq '.values.root_module.resources | length'
   ```
3. For provider errors (e.g., `RequestError: send request failed`), retry after verifying credentials:
   ```bash
   terraform providers
   aws sts get-caller-identity
   ```
4. For resource-specific errors, target the affected resource:
   ```bash
   terraform apply -target=aws_instance.web -auto-approve
   ```
5. If apply is unrecoverable, roll back by reverting the Git commit and re-applying the previous plan.

---

### Alert: `TerraformDriftDetected`
**Trigger:** Scheduled `terraform plan` detects changes not initiated via IaC.

**Triage steps:**
1. Generate a detailed drift report:
   ```bash
   terraform plan -detailed-exitcode -out=drift.plan 2>&1 | tee drift.log
   terraform show -json drift.plan | jq '.resource_changes[] | select(.change.actions != ["no-op"])'
   ```
2. Identify whether the change is intentional (operator console action) or malicious.
3. For intentional drift, import or update IaC:
   ```bash
   terraform import aws_security_group.imported sg-0abc123
   ```
4. For unintentional drift, revert via apply:
   ```bash
   terraform apply -target=<RESOURCE_ADDRESS> -auto-approve
   ```
---

### Alert: `TerraformCloudRunFailed`
**Trigger:** Terraform Cloud workspace run transitions to `errored` state.

**Triage steps:**
1. Fetch run details via API:
   ```bash
   curl -s -H "Authorization: Bearer $TFC_TOKEN" \
     "https://app.terraform.io/api/v2/runs/$RUN_ID?include=plan,apply" | jq '.data.attributes'
   ```
2. Review Sentinel/OPA policy check failures:
   ```bash
   curl -s -H "Authorization: Bearer $TFC_TOKEN" \
     "https://app.terraform.io/api/v2/runs/$RUN_ID/policy-checks" | jq '.data[].attributes'
   ```
3. Check workspace variable set for missing or expired credentials.
4. Trigger a new run via API or UI after remediation.

## Common Issues & Troubleshooting

### Issue 1: State Lock Stuck — Cannot Acquire Lock
**Symptom:** `Error: Error acquiring the state lock`

**Diagnosis:**
```bash
# Check who holds the lock
aws dynamodb scan \
  --table-name terraform-state-locks \
  --filter-expression "attribute_exists(LockID)" \
  --query 'Items[*].{LockID:LockID.S,Who:Info.S,Created:Created.S}'

# Verify no active terraform process
ps aux | grep terraform
```

### Issue 2: Provider Version Conflict
**Symptom:** `Error: Inconsistent dependency lock file` or `required_providers` constraint not satisfied.

**Diagnosis:**
```bash
cat .terraform.lock.hcl | grep -A5 'provider "registry.terraform.io'
terraform version
terraform providers
```

### Issue 3: State Drift After Manual Console Changes
**Symptom:** `terraform plan` shows unexpected resource modifications.

**Diagnosis:**
```bash
terraform plan -out=plan.out -detailed-exitcode
terraform show -json plan.out | \
  jq '.resource_changes[] | select(.change.actions | contains(["update"])) | {addr: .address, before: .change.before, after: .change.after}'
```

### Issue 4: State File Corruption
**Symptom:** `Error: Failed to load state: state snapshot was created by Terraform vX.Y.Z`.

**Diagnosis:**
```bash
# Inspect current state
aws s3 cp s3://my-tfstate-bucket/path/terraform.tfstate /tmp/current.tfstate
python3 -m json.tool /tmp/current.tfstate | head -50

# List S3 versions for rollback
aws s3api list-object-versions \
  --bucket my-tfstate-bucket \
  --prefix path/terraform.tfstate \
  --query 'Versions[*].{VersionId:VersionId,LastModified:LastModified}' \
  --output table
```

### Issue 5: Module Source Not Found
**Symptom:** `Error: Failed to download module` or `could not download module from <URL>`.

**Diagnosis:**
```bash
# Test module source accessibility
curl -v "https://registry.terraform.io/v1/modules/hashicorp/consul/aws"

# Check Git SSH key for private modules
ssh -T git@github.com

# Check Terraform Cloud token
terraform login --check 2>/dev/null || echo "Not logged in"
```

### Issue 6: Workspace State Isolation Broken
**Symptom:** Resources from wrong workspace appear in plan, or `terraform workspace list` shows unexpected state.

**Diagnosis:**
```bash
terraform workspace show
terraform workspace list
terraform state list | head -20

# Check S3 backend key contains workspace name
aws s3 ls s3://my-tfstate-bucket/ --recursive | grep terraform.tfstate
```

## Key Dependencies

- **AWS S3** — state storage; bucket must have versioning enabled and MFA-delete for production
- **AWS DynamoDB** — distributed locking; `LockID` partition key (String), PAY_PER_REQUEST billing recommended
- **Terraform Cloud / TFE** — alternative state backend + run orchestration + Sentinel policy enforcement
- **Provider APIs** — AWS, GCP, Azure, Kubernetes API server; credential rotation impacts all workspaces
- **VCS (GitHub/GitLab)** — VCS-driven runs in TFC; webhook connectivity required
- **Private Module Registry** — authentication tokens with read access to module sources
- **Sentinel / OPA** — policy-as-code framework; policy failures block applies in TFC/TFE

## Cross-Service Failure Chains

- **DynamoDB table deleted or throttled** → All concurrent state operations hang → Multiple teams blocked from applying → Manual force-unlock cascade required
- **S3 bucket policy change (public block enabled incorrectly)** → State reads fail → All Terraform operations fail with `AccessDenied`
- **IAM credential rotation without updating CI secrets** → Provider authentication fails on next apply → Resource drift accumulates until credentials are updated
- **Terraform Cloud API outage** → VCS-triggered runs queue indefinitely → Emergency fallback to local apply with local state copy
- **Provider version pin removed from lock file** → `terraform init -upgrade` pulls breaking provider version → Apply creates destructive plan

## Partial Failure Patterns

- **Apply interrupted mid-execution:** State contains some new resources but not all. Orphaned resources exist in cloud without IaC tracking. Re-apply is usually safe but verify no destructive changes are in the remaining plan.
- **Sentinel policy soft-fail override:** A run proceeds past a `soft-mandatory` policy. Resources are created outside governance guardrails. Alert fires but apply completes. Requires post-hoc compliance review.
- **Module download partial:** Some modules initialize, others fail. `.terraform/modules` contains partial cache. Symptoms: plan works for some resources, fails for others. Fix: `rm -rf .terraform/modules && terraform init`.
- **State push race condition:** Two CI jobs trigger simultaneously. One job's `terraform apply` wins the lock; the other's plan is now stale. After first apply, second job must re-plan before applying.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|----------|
| `terraform init` | < 30s | 30-120s | > 120s |
| `terraform plan` (< 100 resources) | < 60s | 1-3 min | > 3 min |
| `terraform plan` (500+ resources) | < 3 min | 3-8 min | > 8 min |
| `terraform apply` (< 20 changes) | < 2 min | 2-10 min | > 10 min |
| `terraform apply` (100+ changes) | < 15 min | 15-30 min | > 30 min |
| State lock acquisition | < 5s | 5-30s | > 30s |
| `terraform state list` | < 10s | 10-30s | > 30s |
| Module download (registry) | < 15s | 15-60s | > 60s |

## Capacity Planning Indicators

| Indicator | Healthy | Watch | Action Required |
|-----------|---------|-------|-----------------|
| State file size | < 5 MB | 5-20 MB | > 20 MB — split into child workspaces |
| Resources per workspace | < 500 | 500-1000 | > 1000 — refactor into modules/stacks |
| Active workspaces per team | < 50 | 50-200 | > 200 — workspace lifecycle automation needed |
| DynamoDB lock table RCU/WCU | < 50% capacity | 50-80% | > 80% — switch to PAY_PER_REQUEST |
| S3 state bucket API req/min | < 100 | 100-500 | > 500 — review concurrent pipeline design |
| Terraform Cloud run concurrency | < 70% of limit | 70-90% | > 90% — upgrade plan or add agents |
| Provider API error rate | < 0.1% | 0.1-1% | > 1% — investigate throttling/quotas |
| Drift resource count (weekly) | 0 | 1-10 | > 10 — enforce IaC-only change policy |

## Diagnostic Cheatsheet

```bash
# Show current workspace
terraform workspace show

# List all resources in state
terraform state list

# Show detailed resource in state
terraform state show aws_instance.web

# Pull current remote state to local file
terraform state pull > /tmp/state-$(date +%s).json

# Check all providers and their versions
terraform providers

# Validate configuration syntax
terraform validate

# List all locks in DynamoDB table
aws dynamodb scan --table-name terraform-state-locks \
  --query 'Items[*].{ID:LockID.S,Info:Info.S}' --output json

# Show last 10 S3 state versions
aws s3api list-object-versions \
  --bucket my-tfstate-bucket --prefix prod/terraform.tfstate \
  --query 'sort_by(Versions, &LastModified)[-10:].[VersionId,LastModified]' \
  --output table

# Force-unlock a specific lock (get ID from DynamoDB scan)
terraform force-unlock -force <LOCK_ID>

# List TFC workspaces for an organization
curl -s -H "Authorization: Bearer $TFC_TOKEN" \
  "https://app.terraform.io/api/v2/organizations/$TFC_ORG/workspaces" \
  | jq '.data[].attributes.name'

# Get latest run status for a TFC workspace
curl -s -H "Authorization: Bearer $TFC_TOKEN" \
  "https://app.terraform.io/api/v2/workspaces/$WS_ID/runs?page%5Bsize%5D=1" \
  | jq '.data[0].attributes | {status, "created-at", message}'

# Refresh state without plan (reconcile state vs. reality)
terraform refresh
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|--------------------|-------------|
| Successful apply rate | 99.5% | 3.6 hours of failed applies | (successful applies / total apply attempts) × 100 |
| State lock availability | 99.9% | 43 minutes of lock unavailability | Time state is readable and lockable / total time |
| Drift detection latency | 100% of drift detected within 1hr | 0 undetected drift events > 1hr | Scheduled plan runs every 60min in CI |
| Apply completion time (p95) | < 20 min | > 20 min for 5% of applies | Measured from `terraform apply` start to exit |

## Configuration Audit Checklist

| Check | Command | Expected |
|-------|---------|----------|
| S3 versioning enabled | `aws s3api get-bucket-versioning --bucket <BUCKET>` | `Status: Enabled` |
| S3 MFA delete enabled (prod) | `aws s3api get-bucket-versioning --bucket <BUCKET>` | `MFADelete: Enabled` |
| DynamoDB table exists with correct key | `aws dynamodb describe-table --table-name terraform-state-locks` | `KeySchema: LockID (HASH)` |
| State encryption at rest | `aws s3api get-bucket-encryption --bucket <BUCKET>` | SSE-S3 or SSE-KMS present |
| Backend config uses correct workspace key | `cat backend.tf` | Key includes `${terraform.workspace}` |
| Provider version constraints present | `cat versions.tf` | `required_providers` with version constraints |
| Lock file committed to VCS | `git ls-files .terraform.lock.hcl` | File present |
| Sentinel policies enforced (TFC) | Check workspace settings in TFC | Policy sets attached to workspace |
| Remote state access restricted | Review S3 bucket policy | Only known CI role ARNs |
| `terraform validate` passes | `terraform validate` | `Success! The configuration is valid.` |

## Log Pattern Library

| Pattern | Meaning | Action |
|---------|---------|--------|
| `Error: Error acquiring the state lock` | Lock currently held, cannot acquire | Check DynamoDB for lock owner; wait or force-unlock |
| `Lock Info: ID: <uuid>` | Lock metadata in error output | Use ID for `terraform force-unlock` |
| `Error: Failed to load state: state snapshot was created by Terraform v1.X.Y` | State written by newer Terraform version | Upgrade local Terraform CLI to match |
| `Error: Inconsistent dependency lock file` | `.terraform.lock.hcl` out of sync | Run `terraform init -upgrade` |
| `Warning: Version constraints inside provider configuration blocks are deprecated` | Legacy provider config | Move version constraints to `required_providers` block |
| `Error: creating EC2 Instance: RequestError: send request failed` | Provider API unreachable | Check IAM credentials and AWS region endpoint |
| `Error: error configuring S3 Backend: no valid credential sources` | Backend auth failure | Verify `AWS_ACCESS_KEY_ID` / role in environment |
| `Plan: 0 to add, 0 to change, 1 to destroy` | Unexpected destroy detected | Audit `terraform plan` output before applying |
| `Sentinel Result: false` | Sentinel policy violation | Review policy output; remediate config or request override |
| `module.vpc.aws_subnet.private[0]: Still destroying...` | Resource deletion taking long | Check cloud console for resource state; manual deletion may be needed |
| `Error: timeout while waiting for plugin to start` | Provider binary crash or incompatibility | Re-run `terraform init`; check provider binary in `.terraform/providers` |
| `remote: Repository not found` | VCS module source inaccessible | Verify SSH key or OAuth token for Git module source |

## Error Code Quick Reference

| Error / Message Fragment | Root Cause | Quick Fix |
|--------------------------|-----------|-----------|
| `state lock timeout` | Concurrent apply or orphaned lock | `terraform force-unlock -force <ID>` |
| `AccessDenied` (S3) | IAM policy missing S3 permissions | Add `s3:GetObject`, `s3:PutObject` to role |
| `ResourceNotFoundException` (DynamoDB) | Lock table not created | Create DynamoDB table with `LockID` String partition key |
| `The given key does not identify an element` | `terraform state rm` bad address | `terraform state list` to get exact address |
| `Error: No valid credential sources found` | Missing AWS credentials | Set `AWS_PROFILE` or instance profile |
| `plugin crashed` | Provider binary incompatibility | Delete `.terraform` dir; `terraform init` |
| `Error: Module not installed` | `terraform init` not run | `terraform init` |
| `Error: Invalid count argument` | `count` depends on unknown value | Use `-target` or refactor to avoid dynamic count |
| `Error: Cycle` | Circular resource dependency | Refactor to break dependency cycle |
| `Error: Provider produced inconsistent result after apply` | Provider bug with plan vs. apply diff | Upgrade provider; file bug report |
| `409 Conflict` (TFC API) | Run already in progress on workspace | Wait for current run to complete or cancel it |
| `Error: error reading S3 Bucket (terraform-state): NoSuchBucket` | Backend S3 bucket deleted or wrong region | Verify bucket name and region in backend config |

## Known Failure Signatures

| Signature | Likely Cause | Diagnostic Step |
|-----------|-------------|-----------------|
| Plan shows 0 changes but resources are clearly wrong in cloud | Stale local state not pulled from remote | `terraform refresh` then `terraform plan` |
| `terraform init` hangs indefinitely | Registry unreachable or DNS failure | `curl -v https://registry.terraform.io/health` |
| DynamoDB lock acquired but apply never starts | CI runner killed; lock orphaned | Scan DynamoDB; compare Created timestamp to CI run time |
| All workspaces suddenly show auth errors | IAM role rotation or S3 bucket policy change | `aws sts get-caller-identity`; check CloudTrail for bucket policy events |
| `terraform apply` creates resource then immediately plans to destroy it | `ignore_changes` removed or lifecycle mismatch | Check `lifecycle` block; review `terraform plan` output carefully |
| Sentinel policies pass in staging, fail in prod | Different policy set versions attached to workspaces | Compare policy sets attached to each workspace in TFC |
| Provider initialization slow or failing | Provider binary cache miss, slow registry | Check `.terraform/providers`; pre-cache in CI via artifact |
| `terraform destroy` deletes wrong resources | Wrong workspace selected | Always run `terraform workspace show` before destructive operations |
| State serial jumps by > 1 between versions | Concurrent applies both succeeded (race condition) | Audit S3 versions for same serial number; reconcile manually |
| Module outputs used before apply completes | Cross-workspace `terraform_remote_state` depends on in-progress apply | Serialize workspace applies; avoid circular remote state deps |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `Error: Error acquiring the state lock` | Terraform CLI | Orphaned DynamoDB lock from a crashed apply | `aws dynamodb scan --table-name terraform-state-locks` | `terraform force-unlock -force <LOCK_ID>` after verifying no active process |
| `Error: configuring S3 Backend: no valid credential sources` | Terraform CLI / AWS SDK | IAM credentials missing or expired in the environment | `aws sts get-caller-identity` | Rotate credentials; update CI secrets; verify instance profile |
| `Error: Failed to load state: state snapshot was created by Terraform vX.Y.Z` | Terraform CLI | State file written by a newer Terraform version than the one running | Check `terraform version` vs. `"terraform_version"` in state | Upgrade Terraform CLI to match or higher |
| `Error: Inconsistent dependency lock file` | Terraform CLI | `.terraform.lock.hcl` out of sync with `required_providers` constraints | `terraform providers` | `terraform init -upgrade` to regenerate lock file |
| `AccessDenied` on S3 state read/write | AWS SDK | IAM policy missing `s3:GetObject` / `s3:PutObject` on the state bucket | CloudTrail `GetObject` denial event | Attach correct S3 policy to CI role; verify bucket policy |
| `ResourceNotFoundException` on DynamoDB lock | AWS SDK | Lock table deleted or wrong table name in backend config | `aws dynamodb describe-table --table-name terraform-state-locks` | Re-create DynamoDB table with `LockID` String partition key |
| `409 Conflict` from Terraform Cloud API | TFC API client | Another run is already in progress on the workspace | TFC UI / `GET /api/v2/runs` for the workspace | Wait for or cancel the in-progress run |
| `Sentinel Result: false` | TFC Sentinel | Policy-as-code violation blocks the apply | Review `policy-checks` response from TFC API | Remediate config to satisfy policy or request soft-fail override |
| `Error: Provider produced inconsistent result after apply` | Terraform CLI | Provider bug: plan differs from actual post-apply state | Upgrade provider version; check provider GitHub issues | Pin to known-good provider version in `.terraform.lock.hcl` |
| `Error: Failed to download module` | Terraform CLI | Registry unreachable, SSH key missing, or OAuth token expired | `curl https://registry.terraform.io/health`; `ssh -T git@github.com` | Re-authenticate; clear `.terraform/modules`; `terraform init` |
| `Error: Cycle` | Terraform CLI | Circular resource dependency in configuration | `terraform graph \| dot -Tsvg > graph.svg` | Refactor to break the cycle; use `depends_on` carefully |
| `Error: Invalid count argument` | Terraform CLI | `count` or `for_each` depends on a value unknown until apply | Inspect the offending resource's `count` expression | Use `-target` for first apply; refactor to avoid dynamic count on unknown values |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| State file growing unboundedly | State file size crossing 5 MB; `terraform plan` taking longer each week | `aws s3api head-object --bucket <BUCKET> --key <KEY> \| jq '.ContentLength'` | Weeks to months | Split monolithic workspace into child workspaces; reduce resource count per state |
| DynamoDB lock table RCU creeping up | Occasional slow lock acquisition (> 5s) | `aws cloudwatch get-metric-statistics --namespace AWS/DynamoDB --metric-name ConsumedReadCapacityUnits` | Days to weeks | Switch billing mode to `PAY_PER_REQUEST`; review concurrent pipeline frequency |
| Provider API error rate slowly rising | Sporadic `RequestError` in apply logs | Grep apply logs for `Error:` count per day over rolling week | Days | Investigate IAM credential age; check provider quota consumption; rotate credentials proactively |
| Workspace count explosion | New workspaces being created without decommission | `curl -s -H "Authorization: Bearer $TFC_TOKEN" "https://app.terraform.io/api/v2/organizations/$TFC_ORG/workspaces" \| jq '.meta.pagination."total-count"'` | Weeks | Enforce workspace lifecycle policy; automate decommission on PR branch deletion |
| Drift count rising week over week | Scheduled plan shows 1-2 unmanaged changes per run | `terraform plan -detailed-exitcode 2>&1 \| grep -E "to add\|to change\|to destroy"` | Weeks | Enforce IaC-only change policy; add SCPs/guardrails blocking console mutations |
| Module download times increasing | `terraform init` taking 60-90s where it was 15s | Time `terraform init` in CI and track in metrics | Days | Pre-cache provider/module binaries as CI artifacts; use private mirror |
| Provider version skew accumulating | Multiple workspaces on different provider minor versions | `grep -r 'version = ' .terraform.lock.hcl \| sort \| uniq -c` across workspaces | Weeks | Standardize provider version pinning across all workspaces via a shared versions module |
| Terraform Cloud run queue depth rising | Queue depth above 5 during business hours | `curl -s -H "Authorization: Bearer $TFC_TOKEN" "https://app.terraform.io/api/v2/organizations/$TFC_ORG/runs?filter[status]=pending" \| jq '.meta.pagination."total-count"'` | Hours | Upgrade TFC plan for higher concurrency; split large applies into smaller targeted runs |
| Apply duration trend increasing | p95 apply time growing 10% week-over-week | Track apply duration in CI metrics or TFC run history | Weeks | Profile which resource types are slowest; check for provider API throttling |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# terraform-health-snapshot.sh
# Prints a full health summary for a Terraform workspace

set -euo pipefail
WORKSPACE_DIR="${1:-.}"
cd "$WORKSPACE_DIR"

echo "=== Terraform Version ==="
terraform version

echo ""
echo "=== Current Workspace ==="
terraform workspace show

echo ""
echo "=== State Lock Status ==="
TABLE=$(grep -r 'dynamodb_table' backend.tf backend/*.tf 2>/dev/null | awk -F'"' '{print $2}' | head -1)
if [[ -n "$TABLE" ]]; then
  aws dynamodb scan --table-name "$TABLE" \
    --query 'Items[*].{LockID:LockID.S,Info:Info.S}' \
    --output json | jq -r '.[] | "  LOCK: \(.LockID) | \(.Info)"'
  echo "  (empty = no active locks)"
else
  echo "  DynamoDB table not found in backend config"
fi

echo ""
echo "=== State Resource Count ==="
terraform state list 2>/dev/null | wc -l | xargs echo "  Resources in state:"

echo ""
echo "=== Provider Versions ==="
terraform providers 2>/dev/null | grep -E '^\s+(provider|└)' | head -20

echo ""
echo "=== Drift Check (plan exit code) ==="
terraform plan -detailed-exitcode -refresh=true -no-color 2>&1 | tail -5
echo "  Exit code 0=no changes, 1=error, 2=changes detected"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# terraform-perf-triage.sh
# Times key Terraform operations and flags degraded thresholds

set -euo pipefail
WORKSPACE_DIR="${1:-.}"
cd "$WORKSPACE_DIR"

time_cmd() {
  local label="$1"; shift
  local start end elapsed
  start=$(date +%s%3N)
  "$@" > /dev/null 2>&1
  end=$(date +%s%3N)
  elapsed=$(( end - start ))
  echo "  $label: ${elapsed}ms"
}

echo "=== Terraform Performance Triage ==="
echo ""

echo "--- state list ---"
time_cmd "terraform state list" terraform state list

echo ""
echo "--- state pull ---"
time_cmd "terraform state pull" terraform state pull

echo ""
echo "--- providers ---"
time_cmd "terraform providers" terraform providers

echo ""
echo "--- plan (no refresh) ---"
PLAN_START=$(date +%s)
terraform plan -refresh=false -detailed-exitcode -no-color 2>&1 | tail -3
PLAN_END=$(date +%s)
PLAN_SECS=$(( PLAN_END - PLAN_START ))
echo "  Plan duration: ${PLAN_SECS}s"
if (( PLAN_SECS > 180 )); then
  echo "  [CRITICAL] Plan > 3 min — investigate resource count and provider API latency"
elif (( PLAN_SECS > 60 )); then
  echo "  [WARNING] Plan > 60s"
fi

echo ""
echo "--- state file size ---"
BUCKET=$(grep -r 'bucket' backend.tf 2>/dev/null | awk -F'"' '{print $2}' | head -1)
KEY=$(grep -r ' key ' backend.tf 2>/dev/null | awk -F'"' '{print $2}' | head -1)
if [[ -n "$BUCKET" && -n "$KEY" ]]; then
  SIZE=$(aws s3api head-object --bucket "$BUCKET" --key "$KEY" --query 'ContentLength' --output text 2>/dev/null || echo "N/A")
  echo "  State file size: $SIZE bytes"
fi
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# terraform-resource-audit.sh
# Audits provider credentials, lock table, S3 backend, and orphaned resources

set -euo pipefail
WORKSPACE_DIR="${1:-.}"
cd "$WORKSPACE_DIR"

echo "=== Provider Credential Audit ==="
echo "--- AWS ---"
aws sts get-caller-identity 2>/dev/null && echo "  AWS: OK" || echo "  AWS: FAILED"

echo "--- Vault (if configured) ---"
if command -v vault &>/dev/null; then
  vault token lookup 2>/dev/null | grep -E 'expire_time|policies' || echo "  Vault: token invalid or not configured"
fi

echo ""
echo "=== S3 Backend Audit ==="
BUCKET=$(grep -r 'bucket' backend.tf 2>/dev/null | awk -F'"' '{print $2}' | head -1)
if [[ -n "$BUCKET" ]]; then
  echo "  Versioning: $(aws s3api get-bucket-versioning --bucket "$BUCKET" --query 'Status' --output text 2>/dev/null)"
  echo "  Encryption: $(aws s3api get-bucket-encryption --bucket "$BUCKET" --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' --output text 2>/dev/null)"
  echo "  Last 3 state versions:"
  aws s3api list-object-versions --bucket "$BUCKET" \
    --query 'sort_by(Versions, &LastModified)[-3:].[VersionId,LastModified,Size]' \
    --output table 2>/dev/null
fi

echo ""
echo "=== DynamoDB Lock Table Audit ==="
TABLE=$(grep -r 'dynamodb_table' backend.tf 2>/dev/null | awk -F'"' '{print $2}' | head -1)
if [[ -n "$TABLE" ]]; then
  echo "  Table status: $(aws dynamodb describe-table --table-name "$TABLE" --query 'Table.TableStatus' --output text 2>/dev/null)"
  echo "  Billing mode: $(aws dynamodb describe-table --table-name "$TABLE" --query 'Table.BillingModeSummary.BillingMode' --output text 2>/dev/null)"
  echo "  Active locks:"
  aws dynamodb scan --table-name "$TABLE" \
    --query 'Items[*].{LockID:LockID.S,Info:Info.S}' --output json 2>/dev/null \
    | jq -r '.[] | "    \(.LockID)"'
fi

echo ""
echo "=== Orphaned Resources Check ==="
terraform state list 2>/dev/null | grep -c '^' | xargs echo "  Total state resources:"
echo "  Resources with 'tainted' status:"
terraform state list 2>/dev/null | xargs -I{} terraform state show {} 2>/dev/null \
  | grep -c 'tainted' || echo "    0"
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| DynamoDB lock table hot partition | State lock acquisition takes > 30s; `ProvisionedThroughputExceededException` errors | `aws cloudwatch get-metric-statistics --namespace AWS/DynamoDB --metric-name ThrottledRequests`; check table metrics | Switch to `PAY_PER_REQUEST` billing mode immediately | Use `PAY_PER_REQUEST` by default for all state lock tables |
| S3 state bucket request throttling | `SlowDown` or `503 Service Unavailable` from S3 during state reads | `aws s3api get-bucket-request-payment` not the issue; check CloudWatch `AllRequests` metric on bucket | Reduce concurrent CI pipelines; add jitter to pipeline start times | Limit max concurrent Terraform runs per project; use TFC concurrency limits |
| Terraform Cloud concurrent run limit exhausted | Runs queued indefinitely; no progress in TFC dashboard | TFC UI: check `Queue` tab; `GET /api/v2/organizations/$ORG/runs?filter[status]=pending` | Cancel low-priority runs; manually trigger blocked critical runs | Upgrade TFC plan; assign run priority; use workspace-level auto-cancel for speculative plans |
| AWS IAM rate limiting shared across workspaces | `Throttling: Rate exceeded` on IAM API calls during multi-workspace apply | CloudTrail: count `ThrottlingException` events for `iam.amazonaws.com` by `userAgent` | Serialize IAM-heavy applies; avoid concurrent workspace applies modifying IAM | Stagger CI pipeline triggers; use separate IAM roles per team |
| Provider API quota shared with application workloads | Terraform apply fails mid-run with provider rate-limit errors despite low Terraform activity | Check provider quota dashboard (e.g., AWS Service Quotas); compare timestamps with app traffic | Apply during off-peak hours; use `-parallelism=5` to reduce concurrent API calls | Request quota increases proactively; separate Terraform IAM role from application role |
| Large state file slowing all workspace operations | `terraform plan` and `state list` consistently slow across a workspace; other workspaces unaffected | `aws s3api head-object` to check state size; `terraform state list \| wc -l` for resource count | Use `-target` for urgent changes while refactoring is planned | Split workspaces by service boundary; enforce < 500 resources per workspace |
| CI runner resource contention | Multiple Terraform jobs on same runner; OOM kills or CPU throttling of apply process | Runner host `top`; check if multiple `terraform` processes are running simultaneously | Limit Terraform job concurrency at the CI level; dedicate runner nodes to Terraform | Use dedicated runner pools or self-hosted agents with resource reservations for Terraform jobs |
| Module registry CDN contention | `terraform init` slow during peak hours (business hours for module authors) | `curl -w "%{time_total}" https://registry.terraform.io/v1/modules/hashicorp/consul/aws` | Pre-cache modules as CI artifacts; use a private Terraform module mirror | Mirror all required modules in an internal registry; specify `source` from internal registry |
| Shared VPC / subnet CIDR exhaustion from Terraform-managed IPs | EC2/GKE resource creation fails with `InsufficientFreeAddressesInSubnet` | `aws ec2 describe-subnets --query 'Subnets[*].{ID:SubnetId,Available:AvailableIpAddressCount}'` | Temporarily target only non-network resources; expand CIDR or add subnets | Monitor IP utilization as a Terraform output metric; alert before exhaustion |
| KMS key request quota exceeded | AWS resource creation fails with `ThrottlingException` on KMS | CloudTrail: filter for `kms.amazonaws.com ThrottlingException`; check which workspaces use the same KMS key | Reduce `-parallelism`; stagger applies across workspaces sharing the key | Use per-workspace KMS keys; request KMS quota increase |

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| State lock stuck (dead process holding DynamoDB lock) | All subsequent `terraform plan/apply` fail with `Error acquiring the state lock`; CI pipelines queue indefinitely; deployments blocked | All teams sharing the workspace; all CI pipelines for that workspace | `aws dynamodb get-item --table-name terraform-locks --key '{"LockID":{"S":"<workspace>"}}'` returns non-empty item; `terraform force-unlock` needed | `terraform force-unlock <lock-id>` after confirming the original process is dead; unblock all queued pipelines |
| S3 state backend region outage | All Terraform operations fail with `RequestError: send request failed`; no plans or applies possible | All workspaces using S3 backend in the affected region | `aws s3 ls s3://<state-bucket> --region <region>` hangs or returns `connection timeout`; CI logs: `Error loading state: RequestError` | Wait for S3 restoration; for critical applies, use `terraform state pull > terraform.tfstate` from a cached state and work locally with `-state` flag |
| Terraform provider API credential expiration | All applies fail at first resource requiring auth; half-applied changes if credentials expire mid-apply | All workspaces using expired credentials; partial infrastructure state mismatches | `terraform plan` returns `Error: error configuring Terraform AWS Provider: no valid credential sources`; AWS: `ExpiredTokenException` | Rotate credentials: update CI secret/env var/vault; re-run failed apply; check for drift from partial apply |
| Accidental `terraform destroy` on shared VPC | All dependent resources (EC2, RDS, EKS clusters in that VPC) lose network connectivity; downstream services unreachable | All applications and databases in the destroyed VPC; potentially entire region deployment | AWS console shows VPC deleted; all EC2 instances in `running` state but unreachable; health checks failing across services | Immediately re-apply VPC module: `terraform apply -target=module.vpc`; restore subnet, IGW, route tables; DNS/security groups may need manual reconciliation |
| Module version bump breaking downstream workspaces | Module interface change (removed variable, renamed output) breaks all workspaces depending on it; applies fail with `Unsupported argument` | All workspaces using that module version; auto-upgrade CI pipelines hit the breaking change first | `terraform plan` returns `Error: Unsupported argument`; module changelog shows breaking change; multiple workspaces fail simultaneously | Pin module version in each workspace: `source = "git::...?ref=v1.2.3"`; fix workspaces incrementally |
| DynamoDB lock table deleted | Lock acquisition fails with `ResourceNotFoundException`; without locking, concurrent applies risk state corruption | All workspaces using that lock table; risk of concurrent state writes corrupting state | `terraform apply` returns `Error acquiring the state lock: ResourceNotFoundException: Requested resource not found`; DynamoDB console shows missing table | Recreate lock table: `aws dynamodb create-table --table-name terraform-locks --attribute-definitions AttributeName=LockID,AttributeType=S --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST` |
| `terraform state mv` executed incorrectly at scale | Resources orphaned in state or duplicated; next apply tries to create existing resources or destroy them | The specific workspace where state was corrupted; could affect all managed resources | `terraform plan` shows unexpected create/destroy for resources that exist; `terraform state list` shows missing entries; infrastructure still running | `terraform state pull > backup.tfstate`; use `terraform state mv` to correct; or manually edit state JSON (risky) and push with `terraform state push` |
| Provider version auto-upgrade breaks resource schemas | Resources with changed schema fail plan with `An argument named X is not expected here` | All workspaces that run `terraform init -upgrade` or don't pin provider versions | `terraform plan` errors; provider changelog shows schema changes; `.terraform.lock.hcl` shows new provider hash | Pin provider version in `required_providers` block; restore `.terraform.lock.hcl` to previous version; `terraform init -upgrade=false` |
| Cloud account IAM permission reduction | Applies fail mid-run when they reach resources requiring the removed permission; partial applies leave infrastructure inconsistent | All resources in that workspace requiring the removed IAM action | AWS CloudTrail: `AccessDenied` for Terraform IAM role; `terraform apply` output: `Error: AccessDeniedException`; partial state changes recorded | Restore IAM permissions immediately; re-run `terraform apply` to converge remaining resources; audit what was partially applied |
| Workspace workspace remote state reference broken | Downstream workspaces that reference `terraform_remote_state` from destroyed/renamed upstream workspace fail plan | All workspaces with `data "terraform_remote_state"` pointing to the affected workspace | `terraform plan` returns `Error: Failed to load state: no state file found for workspace <name>`; state bucket shows missing path | Restore upstream workspace state; or update downstream `data "terraform_remote_state"` to new workspace path |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Terraform version upgrade (e.g., 1.5 → 1.6) | HCL syntax or behavior change causes plan failures; `provider version constraints not satisfied`; state format incompatible | Immediate on first `terraform init` with new version | `terraform version` vs `.terraform-version` file; error message references new behavior; `git bisect` Terraform version | Pin version in `.terraform-version` or CI `TF_VERSION` env var; downgrade Terraform version until code updated |
| `terraform.tfvars` sensitive variable change | Resources that depend on the variable show replacement in plan (e.g., DB password rotation triggers RDS replacement) | Immediate on next `terraform plan` | `terraform plan` output shows `forces replacement` next to the changed variable; check which variable changed in CI diff | Add `lifecycle { ignore_changes = [password] }` to prevent replacement; rotate via in-band mechanism instead |
| Backend configuration change (`bucket` or `key` rename) | `terraform init` re-initializes with empty state; `terraform plan` shows all resources to be created | Immediate on `terraform init` with new backend config | `terraform state list` returns empty; no resources in plan `data source`; backend config diff in `backend.tf` | Copy state from old backend path to new: `aws s3 cp s3://bucket/old-key.tfstate s3://bucket/new-key.tfstate`; re-init |
| Resource `count` → `for_each` migration without state move | Existing resources addressed as `resource[0]` not found by Terraform; plan shows destroy old + create new | Immediate on `terraform plan` | Plan shows destroy of `resource[0]`, `resource[1]` and create of `resource["key-a"]`, `resource["key-b"]` for same infra | Use `terraform state mv 'resource[0]' 'resource["key-a"]'` for each instance before applying; never apply the destroy plan |
| Removing a `lifecycle { prevent_destroy = true }` block | Next `terraform apply` that includes a destroy action for that resource succeeds; critical resource deleted | Immediately on the apply that triggers destruction | Git diff shows `prevent_destroy` removed; `terraform plan` shows `destroy` for the resource | Add `prevent_destroy = true` back immediately; if resource was already destroyed, restore from backup |
| Adding a new required provider version constraint | `terraform init` fails for all team members until they re-run init; CI pipelines fail at init step | Immediate after merge of constraint change | `terraform init` error: `required_providers constraints are not met`; `.terraform.lock.hcl` shows old provider hash | Either loosen constraint or require all consumers to run `terraform init -upgrade`; update lock file and commit |
| Module `output` block removal | Downstream workspaces that reference that output via `terraform_remote_state` fail plan | Immediate on next `terraform plan` of downstream workspace | Error: `An output value with the name X has not been declared`; upstream module Git diff shows removed output | Restore output in module; or update all downstream `data "terraform_remote_state"` references |
| AWS provider default tags change | All resources in workspace get replacement triggered by tag changes; mass replacement of stable infrastructure | Immediate on next `terraform plan` after provider config change | Plan shows `~ tags` updates or replacements on every resource; compare `provider "aws" { default_tags }` before/after | Add `lifecycle { ignore_changes = [tags] }` on resources that shouldn't be replaced for tags; or accept tag-only updates (no replacement) |
| Moving resources between modules without `terraform state mv` | Resources in old module path destroyed; resources in new module path created; downtime for the moved resources | Immediate on `terraform apply` | Plan shows destroy of `module.old.resource` and create of `module.new.resource` for identical infra | Run `terraform state mv module.old.resource module.new.resource` before applying; verify plan shows 0 destroy after state move |
| `null_resource` or `local-exec` script path change | Apply fails mid-run if script doesn't exist at new path; or runs old script if path cached | Immediate on apply that triggers the `null_resource` | Apply error: `local-exec provisioner error: ... no such file or directory`; `null_resource` shows `trigger` unchanged | Fix script path; `terraform taint null_resource.<name>` if re-run is needed; check that script is committed and accessible |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Concurrent applies without locking (DynamoDB lock table deleted) | `aws dynamodb describe-table --table-name terraform-locks` returns `ResourceNotFoundException` | Two applies run simultaneously; both read same state, both write conflicting state versions; last writer wins | State corruption; resources tracked incorrectly; phantom creates/destroys on next plan | Immediately halt all applies; recreate lock table; `terraform state pull` from S3 to verify state integrity; reconcile manually if corrupted |
| State drift from out-of-band manual change | `terraform plan` shows changes on a resource that was not modified in Terraform | Resource modified via AWS console, CLI, or another tool; Terraform wants to revert it | Unintended reversion of manual fix; or manual fix overrides intended Terraform state | Import the manual change: `terraform import resource_type.name <id>`; or `terraform apply` to revert manual change intentionally |
| Multiple state versions in S3 (version conflict after concurrent writes) | `aws s3api list-object-versions --bucket <state-bucket> --prefix <workspace>.tfstate` shows multiple versions with close timestamps | Subsequent plans reference incorrect state version; phantom diffs appear | Resources may be double-counted or missing in plan; incorrect dependency resolution | Compare state versions: `aws s3api get-object --bucket <bucket> --key <key> --version-id <id> state.json`; select correct version and set as current |
| Remote state output stale in downstream workspace | `data "terraform_remote_state"` returns old output value; downstream resource configured with stale data | Upstream changed an output; downstream workspace cached the old value | Downstream infrastructure uses wrong configuration (old IP, old ARN); silent misconfiguration | Refresh downstream workspace state: `terraform refresh` or `terraform apply -refresh-only`; verify with `terraform output` |
| Tainted resource inconsistency after failed apply | `terraform state list` shows resource as tainted; actual resource exists in cloud and is healthy | Next plan shows forced replacement of healthy resource; downtime if resource is in use | Unnecessary recreation of healthy resource; potential data loss for stateful resources | Untaint the resource: `terraform untaint resource_type.name`; verify actual resource state matches expected; re-run plan |
| Workspace variable override not applied | `terraform workspace show` shows correct workspace name; but `terraform.workspace` interpolation returns `default` in module | Module behaves identically across all workspaces; no per-environment differentiation | Wrong resources sized or configured for environment; production config in dev or vice versa | Check that module uses `var.environment` not `terraform.workspace` in sensitive places; pass environment as explicit variable |
| Provider alias misconfiguration causing resource assignment to wrong region | `terraform state show aws_instance.web` shows `region = us-west-2` but instance actually created in `us-east-1` | Resources created in wrong region; Terraform state references wrong region | Multi-region infrastructure in wrong location; latency/compliance issues | Import resource in correct region using correct provider alias; remove incorrect state entry: `terraform state rm`; reimport |
| `.terraform.lock.hcl` not committed causing provider version drift | Different team members get different provider versions; plans produce different diffs on same code | Non-deterministic plan outputs; "works on my machine"; CI produces different plan than local | Unpredictable infrastructure changes; provider bugs or behavior changes hit inconsistently | Commit `.terraform.lock.hcl` to version control; enforce via CI check; run `terraform providers lock` for all platforms: `-platform=linux_amd64 -platform=darwin_arm64` |
| `terraform_remote_state` pointing to wrong workspace | Downstream workspace references `workspace = "staging"` but reads production state | Production values (IPs, ARNs, sizes) used in staging environment | Staging environment misconfigured; potential production impact if staging affects shared resources | Fix `data "terraform_remote_state"` workspace attribute; `terraform apply -refresh-only` to update downstream values |
| Partial apply with some resources created and others failed | `terraform apply` exited mid-run; state has partial updates; cloud has partial infrastructure | Resources created before the failure are live; resources after are missing; plan shows mixed create/update/destroy | Inconsistent infrastructure; services may be partially up; manual reconciliation needed | Re-run `terraform apply`; Terraform is idempotent and will apply only the remaining changes; check for resources that need manual cleanup |

## Runbook Decision Trees

### Tree 1: `terraform apply` Failing in CI

```
Does `terraform plan` succeed locally with the same code?
├── NO  → Is there an authentication error?
│         `grep -i "credential\|token\|unauthorized\|AccessDenied" <ci-log>`
│         ├── YES → Rotate and re-inject credentials:
│         │         AWS: update IAM access key or OIDC token in CI secrets.
│         │         Vault: `vault token renew`; re-trigger pipeline.
│         └── NO  → Is there a syntax/validation error?
│                   `terraform validate` locally with same TF version as CI.
│                   ├── YES → Fix HCL syntax; commit and re-trigger.
│                   └── NO  → Check provider version mismatch:
│                             `cat .terraform.lock.hcl` vs CI provider cache.
│                             Run `terraform init -upgrade` and commit updated lock file.
└── YES → Is there a state lock error in CI?
          `grep "state lock\|LockID" <ci-log>`
          ├── YES → Check for stuck lock: `aws dynamodb get-item --table-name terraform-locks --key '{"LockID":{"S":"<workspace>"}}'`
          │         If stale: `terraform force-unlock <lock-id>` (confirm original process is dead first).
          │         Re-trigger CI pipeline.
          └── NO  → Is the error a cloud API quota or throttle?
                    `grep "ThrottlingException\|RequestLimitExceeded\|quota" <ci-log>`
                    ├── YES → Reduce parallelism: add `-parallelism=5` to apply command.
                    │         Request quota increase for affected resource type.
                    └── NO  → Check for partial state from previous failed apply:
                              `terraform plan` — if it shows unexpected creates/destroys,
                              investigate state with `terraform state list` and `terraform state show`.
                              Import or remove stale state entries as needed.
```

### Tree 2: Unexpected Resource Replacement in Plan

```
Does `terraform plan` show `forces replacement` for an unexpected resource?
├── YES → What is causing the replacement?
│         Look for `# aws_<resource>.name must be replaced` in plan output.
│         ├── Immutable attribute change (e.g., `availability_zone`, `engine_version`) →
│         │   Check if the change is intentional (expected migration).
│         │   ├── YES (intentional) → Accept replacement; schedule maintenance window.
│         │   └── NO  → Revert the variable/value in code; commit; re-plan to confirm 0 replacement.
│         ├── Tag change triggering replacement →
│         │   Add `lifecycle { ignore_changes = [tags] }` to the resource.
│         │   Re-plan; confirm replacement removed.
│         └── Resource moved between modules/addresses →
│             Use `terraform state mv <old_address> <new_address>` before applying.
│             Re-plan; confirm replacement removed.
└── NO (change is expected) → Is the replaced resource stateful (RDS, EBS, S3 bucket)?
                              ├── YES → Stop. Do NOT apply.
                              │         Take a manual snapshot/backup first.
                              │         `aws rds create-db-snapshot --db-instance-identifier <id> --db-snapshot-identifier ir-backup`
                              │         Plan a maintenance window; notify stakeholders.
                              │         Apply with `-target=resource_type.name` to limit blast radius.
                              └── NO (stateless) → Apply safely; monitor post-apply health checks.
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| `count` variable set to a large value accidentally | `count = var.replica_count` with `replica_count` defaulting to 100 instead of 1 | `terraform plan` shows 99 new resources to create; `aws ec2 describe-instances --filters Name=tag:ManagedBy,Values=terraform \| jq '.Reservations \| length'` | Massive cost spike; service quota exhaustion; other teams' resource creation blocked | Apply with `-target=var.replica_count=1`; destroy excess instances; `terraform apply -var replica_count=1` | Validate `count` and `for_each` values in CI; set variable validation blocks with max value constraints |
| High-cost instance type in wrong variable | `instance_type = var.instance_type` with production value (`p4d.24xlarge`) used in dev environment | `aws ec2 describe-instances --filter Name=tag:Environment,Values=dev` shows oversized instances | Thousands of dollars/hour for dev workloads | Immediately terminate: `aws ec2 terminate-instances --instance-ids <ids>`; update Terraform variable to correct instance type | Enforce per-environment variable validation; use workspace-specific `tfvars` files |
| Runaway spot instance fleet | Auto-scaling Terraform resource creates unlimited spot instances | `aws ec2 describe-spot-instance-requests --filters Name=state,Values=active` | Thousands of instances launched; account-level spot quota hit; massive cost | Set max capacity limit: update `max_size` in ASG resource; `terraform apply` to converge | Always set explicit `max_size` on any auto-scaling resource; set cost budget alert in AWS Budgets |
| Forgotten `terraform apply` running with large `-parallelism` creating all resources at once | `parallelism=100` in CI creates 200 resources simultaneously; API quota exhausted for other users | `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=RunInstances` shows burst | API throttling for all Terraform workspaces in the account; cost spike | Kill the CI job; resources created so far persist — clean up with `terraform destroy -target=...` for unneeded ones | Default `-parallelism=10`; require review for large `count`-based applies |
| S3 state bucket public access enabled accidentally | Terraform state files (containing secrets, IPs, resource IDs) accessible publicly | `aws s3api get-bucket-policy-status --bucket <state-bucket>` shows `"IsPublic": true` | Security incident: state files may contain sensitive data (passwords, private keys) | `aws s3api put-public-access-block --bucket <state-bucket> --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true` | Enforce S3 Public Access Block via AWS Organizations SCP; Terraform test in CI |
| NAT Gateway created in every AZ for dev environment | Module creates 3 NAT Gateways ($0.045/hr each + data transfer) for a rarely used dev environment | `aws ec2 describe-nat-gateways --filter Name=state,Values=available` \| count per VPC | ~$100/month per dev environment; scales with number of dev environments | Destroy dev NAT Gateways during off-hours: `terraform apply -var=enable_nat_gateway=false` in dev workspaces | Use `single_nat_gateway = true` for non-production; set schedule-based destroy for dev environments |
| Provider `ignore_changes` removal causing recreation of many resources | `lifecycle { ignore_changes = [ami] }` removed from large autoscaling group launch template; all instances replaced | `terraform plan` shows `replace` for all instances in ASG; `aws ec2 describe-instances` shows mass replacement | Service disruption; cost spike from termination/launch cycle | Restore `ignore_changes` block; commit and re-plan; if apply already started, reduce disruption with rolling update strategy | Require `lifecycle` block changes to go through change advisory board; CI plan review for large-scale replacements |
| Workspace per-feature-branch creating persistent cloud resources | CI creates a new Terraform workspace per branch; branches not cleaned up; resources accumulate | `terraform workspace list` shows hundreds of workspaces; `aws resource-groups list-groups` shows many tagged resources | Unbounded cost growth; hitting account-level resource quotas | Identify orphaned workspaces: compare workspace list against active Git branches; destroy orphaned: `terraform workspace select <orphan> && terraform destroy` | Enforce workspace lifecycle in CI: automatically destroy workspace on branch delete; set cost budget per workspace tag |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot state lock contention | Multiple `terraform apply` runs queue behind one lock; CI pipelines timeout waiting for DynamoDB lock | `aws dynamodb scan --table-name terraform-locks --filter-expression "attribute_exists(LockID)" --output json`; check for stuck items | Long-running apply holding DynamoDB lock; or crashed apply left lock orphaned | Force-unlock: `terraform force-unlock <lock-id>`; fix underlying apply or add per-module state files |
| S3 state backend GET rate limiting | `terraform plan` fails with "SlowDown: Please reduce your request rate"; CI runs slow | `aws cloudwatch get-metric-statistics --namespace AWS/S3 --metric-name 5xxErrors --dimensions Name=BucketName,Value=<state-bucket>` | Too many Terraform processes reading same state file simultaneously (mono-state anti-pattern) | Split state into per-module/per-service remote state files; reduce CI parallelism with `--parallelism=5` |
| Provider plugin GC/memory pressure | `terraform plan` OOMs on large state files (> 10,000 resources); CI runner runs out of memory | `terraform state list \| wc -l`; `top` during `terraform plan`; `terraform -chdir=<module> state list \| wc -l` | Monolithic state with thousands of resources; provider process holds full state graph in memory | Refactor to smaller modules with isolated state; use `terraform -target` for focused operations |
| Slow provider API calls from rate limiting | `terraform apply` takes hours; AWS API calls throttled; `TooManyRequestsException` in debug log | `TF_LOG=DEBUG terraform plan 2>&1 \| grep "429\|throttl\|rate"` | Terraform default `--parallelism=10` making too many concurrent AWS API calls | Reduce parallelism: `terraform apply -parallelism=3`; use provider `retry_mode = "adaptive"` in AWS provider config |
| Slow `terraform init` from downloading many providers | CI `terraform init` takes 3–5 minutes on cold runner; blocks pipeline | `time terraform init`; `TF_LOG=DEBUG terraform init 2>&1 \| grep "downloading\|fetching"` | Many provider dependencies; no provider cache; always downloading from registry | Configure provider cache: `TF_PLUGIN_CACHE_DIR=~/.terraform.d/plugin-cache`; commit `.terraform.lock.hcl`; use local mirror |
| CPU steal on shared CI runner executing Terraform | `terraform plan` takes 3× normal duration; CI host shared with many other jobs | `top` on CI runner during plan — check `%st` steal; `vmstat 1 5` | CI runner on over-committed hypervisor; many concurrent jobs on same host | Use dedicated CI runner pool for Terraform jobs; or self-hosted runners on Reserved instances |
| Lock contention from concurrent module graph traversal | `terraform graph` or large plan hangs; provider CLI blocks on resource dependency resolution | `TF_LOG=TRACE terraform plan 2>&1 \| grep "walking\|lock\|blocked"`; `ps aux \| grep terraform` | Complex dependency graph with circular-looking dependencies; Terraform serialising dependency traversal | Simplify resource dependency graph; explicitly use `depends_on` only where necessary; break large modules |
| Serialization overhead for large tfstate JSON | State reads/writes slow; `terraform state pull` takes > 10s for large state | `time terraform state pull > /dev/null`; `terraform state list \| wc -l`; `ls -lh <state-file>` | State file grown to hundreds of MB; JSON parse overhead on every plan/apply | Run `terraform state rm` for orphaned resources; use state surgery to split state; enable S3 compression |
| Batch resource misconfiguration creating serial dependencies | Resources with `count` or `for_each` apply serially instead of parallel; apply takes O(n) time | `terraform apply -parallelism=10 -no-color 2>&1 \| grep "Still creating\|Still modifying" \| wc -l`; compare to resource count | Implicit dependency chain between `count`-indexed resources preventing parallel creation | Refactor to remove implicit dependencies; use `for_each` with a map; verify with `terraform graph \| dot -Tpng > graph.png` |
| Downstream cloud API latency inflating plan/apply time | `terraform apply` hangs on specific resource type; debug log shows long API response times | `TF_LOG=DEBUG terraform apply 2>&1 \| grep "Request\|Response\| POST \| GET " \| tail -50` — look for long durations | Cloud service degradation (check cloud status page); or endpoint in wrong region | Check cloud provider status page; switch to regional endpoint in provider config; use `alias` provider in different region |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on Terraform Cloud/Enterprise endpoint | `terraform login` or remote operations fail with TLS certificate error | `echo \| openssl s_client -connect app.terraform.io:443 2>/dev/null \| openssl x509 -noout -dates` (or your TFE host) | Terraform Cloud/Enterprise TLS cert expired; or custom CA not trusted by Terraform CLI | Update `ca_certificate` in Terraform Enterprise config; re-import CA: `terraform login` with updated cert bundle |
| mTLS rotation failure between Terraform Enterprise and VCS | VCS webhooks to TFE fail after cert rotation; workspaces not triggering on push | TFE admin console → VCS providers → check connection status; TFE logs for TLS errors | VCS integration cert rotated on one side without updating the other | Re-verify VCS OAuth connection in TFE admin: Settings → VCS Providers → re-authorize |
| DNS resolution failure for S3 state backend | `terraform init` fails with "no such host"; `TF_LOG=DEBUG` shows DNS timeout | `TF_LOG=DEBUG terraform init 2>&1 \| grep "dial\|DNS\|no such host"`; `nslookup s3.<region>.amazonaws.com` | S3 endpoint hostname not resolvable from CI runner network; custom DNS misconfigured | Use S3 VPC endpoint or fix DNS resolver; alternatively specify `endpoint` in S3 backend config |
| TCP connection exhaustion from concurrent Terraform apply runners | Cloud provider API calls fail with "connection refused" or timeout; many CI pipelines run simultaneously | `ss -s` on CI runner; `netstat -an \| grep TIME_WAIT \| wc -l`; `cat /proc/sys/net/ipv4/ip_local_port_range` | Dozens of parallel Terraform processes each opening many provider connections; port exhaustion | `sysctl -w net.ipv4.tcp_tw_reuse=1`; limit concurrent Terraform jobs in CI scheduler; use `-parallelism=3` |
| Load balancer timeout on long-running Terraform remote operations | Remote plan/apply in Terraform Cloud times out via corporate proxy; `Error: error waiting for operation` | `TF_LOG=DEBUG terraform apply 2>&1 \| grep "timeout\|context deadline"`; check proxy idle timeout config | Corporate proxy or API gateway cuts connections idle longer than LB timeout | Increase proxy idle timeout; use Terraform Cloud agent mode to eliminate proxy hop |
| Packet loss causing provider API call retries | `terraform apply` takes much longer than expected; provider logs show retry storms | `TF_LOG=DEBUG terraform apply 2>&1 \| grep "retrying\|retry attempt"`; `ping -c 100 <aws-region-endpoint>` from CI | Network instability between CI runner and cloud API endpoint | Investigate network path (MTU, routing); move CI runner to same VPC/region as cloud resources |
| MTU mismatch dropping large Terraform state uploads | `terraform state push` fails for large states; small states work; hangs at specific file size | `TF_LOG=DEBUG terraform state push /tmp/state.json 2>&1`; test with `curl --data-binary @<large-file> https://s3.<region>.amazonaws.com/` | CNI or VPN overlay MTU too small; large S3 PUT requests get fragmented and dropped | Set MTU to 1450 on CI runner's network interface; or configure S3 multipart upload threshold lower |
| Firewall blocking Terraform Registry access | `terraform init` fails; provider download times out; "Error: Failed to install provider" | `TF_LOG=DEBUG terraform init 2>&1 \| grep "registry.terraform.io\|timeout"`; `curl -v https://registry.terraform.io/v1/providers` | Corporate firewall blocking `registry.terraform.io` or `releases.hashicorp.com` | Use local provider mirror: set `filesystem_mirror` in `~/.terraformrc`; or configure `network_mirror` to internal proxy |
| SSL handshake timeout connecting to private Terraform Enterprise | `terraform init` hangs indefinitely when connecting to on-prem TFE over VPN | `TF_LOG=DEBUG terraform init 2>&1 \| grep "handshake\|TLS\|timeout"` | VPN MTU too small for TLS handshake; or TFE using non-standard TLS cipher suite incompatible with Go defaults | Set `ssl_cert_file` in `~/.terraformrc` for custom CA; reduce TLS negotiation overhead by pinning cipher suite on TFE |
| Connection reset from AWS API during long CloudFormation/Terraform wait | `terraform apply` on long-provisioning resources (RDS, EKS) gets connection reset after 30–60 minutes | `TF_LOG=DEBUG terraform apply 2>&1 \| grep "connection reset\|EOF\|context"` | AWS API connection times out during long waits for resource to become available | Implement retry in provider; set `timeout` blocks in resource config: `timeouts { create = "60m" }`; use `terraform refresh` to recover |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of Terraform process during large plan | `terraform plan` crashes with signal 9 or exit code 137; CI shows "killed" | `dmesg \| grep -i oom`; `journalctl -k \| grep -i oom`; CI job log shows OOM exit | Reduce state size: `terraform state rm` unused resources; split module; increase CI runner RAM | Use runner with ≥ 8GB RAM for large states; split monolith state into ≤ 500 resource modules |
| S3 state bucket storage exhaustion | Old state versions filling S3; versioned state bucket approaching limit | `aws s3api list-object-versions --bucket <state-bucket> --output json \| jq '[.Versions[].Size] \| add'` | Many stale state versions accumulating with versioning enabled and no lifecycle policy | Set S3 lifecycle to expire non-current state versions after 90 days: `aws s3api put-bucket-lifecycle-configuration` | Enable S3 versioning lifecycle from day 1; alert on bucket size > 80% quota |
| DynamoDB lock table partition exhaustion | Lock operations throttled; `terraform apply` hangs waiting to acquire lock | `aws dynamodb describe-table --table-name terraform-locks \| jq '.Table.ItemCount'`; CloudWatch `ConsumedWriteCapacityUnits` | Too many active locks or orphaned items; DynamoDB capacity insufficient | Force-unlock stale items: `aws dynamodb scan --table-name terraform-locks` then `aws dynamodb delete-item` for orphans; increase WCU | Use DynamoDB autoscaling; set orphan lock detection in CI pipeline (fail if lock > 2h old) |
| File descriptor exhaustion in Terraform provider plugin | Provider plugin crashes; `too many open files` in `TF_LOG=DEBUG` output | `TF_LOG=DEBUG terraform apply 2>&1 \| grep "too many open files"`; `ulimit -n` on CI runner | Provider opens one FD per resource for parallel API calls; default FD limit too low | `ulimit -n 65536` in CI job pre-step; or set `nofile` limit in CI runner config |
| Inode exhaustion on CI runner from `.terraform` plugin directories | `terraform init` fails: "cannot create file"; disk has space but inodes exhausted | `df -i /home/runner`; `du --inodes ~/.terraform.d/ \| sort -n \| tail -20` | Many Terraform workspaces each with unpruned `.terraform/providers` directories | `find /home/runner/work -name ".terraform" -type d -mtime +7 -exec rm -rf {} +`; use shared plugin cache | Configure CI to clean `.terraform` dirs after each job; use `TF_PLUGIN_CACHE_DIR` for shared cache |
| CPU exhaustion from concurrent providers during apply | `terraform apply` uses 100% CPU for minutes during planning; starves other CI jobs | `top` during apply; `TF_LOG=DEBUG terraform apply 2>&1 \| grep "goroutine"` | High provider goroutine count under full `--parallelism=10` with many resources | Reduce `--parallelism=3`; run Terraform on dedicated CI runner; split large applies |
| Swap exhaustion on CI runner from multiple concurrent Terraform jobs | CI runner swapping heavily; all jobs slow; potential OOM on large plans | `free -h`; `vmstat 1 5 \| awk '{print $7,$8}'` — si/so | Many concurrent Terraform jobs each consuming 1–2GB RAM on same CI runner | Limit concurrent Terraform jobs per CI runner node; use memory-optimized runner instances; disable swap |
| Ephemeral port exhaustion from provider HTTP connections | Cloud API calls fail with "cannot assign requested address"; apply stalls | `ss -s \| grep TIME-WAIT`; `cat /proc/sys/net/ipv4/ip_local_port_range` on CI runner | Terraform provider makes many short-lived HTTPS connections (one per resource API call) accumulating in TIME_WAIT | `sysctl -w net.ipv4.tcp_tw_reuse=1 net.ipv4.ip_local_port_range="1024 65535"`; reduce `-parallelism` |
| Disk exhaustion on CI runner from Terraform plan outputs | CI runner disk fills from large plan output files saved as artifacts | `df -h /home/runner`; `du -sh /home/runner/work/*/terraform-plan* \| sort -h \| tail -10` | Binary plan files for large states saved as CI artifacts but never cleaned up | Add CI job step to clean up plan files after apply; store plan as S3 artifact with 7-day lifecycle |
| Kernel PID limit from Terraform provider goroutines | Terraform provider process cannot fork; `fork: resource temporarily unavailable` in TF_LOG=DEBUG | `cat /proc/sys/kernel/pid_max`; `cat /proc/$(pgrep terraform)/status \| grep Threads` | Large provider with many goroutines hitting kernel thread limit on constrained CI runner | `sysctl -w kernel.pid_max=524288`; upgrade to newer provider version with reduced goroutine usage |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation: resource created twice from concurrent applies | Two CI pipelines apply same Terraform config simultaneously; duplicate resources created (e.g., two ELBs) | `terraform state list \| grep <resource>`; `aws elb describe-load-balancers \| grep <name>`; check for duplicate in `terraform show` | Orphaned resource consuming cloud cost; potential routing split between duplicate resources | Import correct resource: `terraform import <resource_type>.<name> <cloud-id>`; destroy duplicate manually; fix CI to enforce serial applies |
| Saga partial failure: `terraform apply` succeeds for first half of resources then fails | Apply stopped mid-way; infrastructure in partially-updated state; rollback not automatic | `terraform show`; `terraform plan` — review what's left to apply; `terraform state list \| grep <affected-prefix>` | Live infrastructure in inconsistent state (e.g., new SG rule applied but ASG not updated) | Run `terraform apply` again (Terraform is idempotent for completed resources); fix failing resource then re-apply |
| Out-of-order state write: two sequential applies write state in wrong order | Apply A starts, then Apply B starts and finishes first, writing newer state; Apply A then overwrites with stale state | `aws s3api list-object-versions --bucket <state-bucket> --key <workspace>.tfstate \| jq '.Versions[0:3]'`; compare ETag and LastModified | State regression: resources Apply A was unaware of appear deleted in next plan | Restore correct state version: `aws s3api get-object --bucket <bucket> --key <key> --version-id <correct-version-id> /tmp/state.json && terraform state push /tmp/state.json` |
| Cross-module deadlock: module A creates IAM role, module B waits for it | Module B plan references IAM role ARN output from Module A; both applied simultaneously; circular dependency | `terraform output -module=module_a iam_role_arn` — if blank, Module A not yet complete; check remote state data source | Module B apply fails; infrastructure partially deployed; manual sequencing required | Apply Module A first, wait for completion, then apply Module B; use `terraform_remote_state` with proper dependency ordering |
| Distributed lock expiry: lock TTL too short for large apply | DynamoDB lock expires mid-apply; second runner acquires lock and starts concurrent apply | `aws dynamodb get-item --table-name terraform-locks --key '{"LockID":{"S":"<state-path>"}}'` — check `ExpireTime` | Concurrent applies; potential state corruption; cloud resources may be double-modified | Immediately `terraform force-unlock <id>` on the orphaned lock; inspect state for inconsistency; `terraform plan` to verify | Terraform's DynamoDB lock has no TTL by default; protect with CI-level mutex using GitHub Actions `concurrency` or equivalent |
| Compensating transaction failure: `terraform destroy` partially destroys and fails | Destroy removes half the resources; remaining resources have dangling references; IaC left in inconsistent state | `terraform state list` — compare to expected empty state; `terraform plan` — shows remaining resources | Live infrastructure with broken dependencies; manual cleanup required for orphaned resources | Continue destroy: fix failing resource (e.g., manually delete blocker), then re-run `terraform destroy`; or use `-target` to remove remaining |
| At-least-once IaC pipeline: same commit triggers Terraform apply twice | CI webhook retried; same Terraform apply runs twice; second run is usually a no-op but may catch drift | `terraform plan -no-color 2>&1 \| grep "0 to add, 0 to change, 0 to destroy"` — second run should show no changes | Usually harmless (Terraform is idempotent) but can re-trigger in-place updates on timestamps/tags causing drift | Add idempotency check in CI: run `terraform plan` first; only `terraform apply` if changes detected |
| Out-of-order provider version upgrade causing resource re-creation | Provider upgraded in `.terraform.lock.hcl`; new provider version plans replacement of existing resources | `terraform plan -no-color \| grep "must be replaced"` — check for unexpected replacements; `git diff .terraform.lock.hcl` | Production resources scheduled for replacement; potential data loss for stateful resources | Pin previous provider version: revert `.terraform.lock.hcl` and `required_providers` version constraint; `terraform init -upgrade=false` |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: one team's large plan saturating shared CI runner | `top` on CI runner shows `terraform plan` consuming 4 cores; other jobs queued | Other teams' CI pipelines wait 30+ minutes for a runner; SLO breaches | `kill $(pgrep terraform)` on runner; manually cancel the offending CI job | Allocate dedicated CI runner pools per team; set concurrency limit per workspace in TFE; use self-hosted runners with team-tagged pools |
| Memory pressure: monolithic state plan OOMing shared runner | CI runner shows OOM killer log; all jobs on that runner fail | All team pipelines on same runner fail simultaneously with OOM exit | `renice -n 10 $(pgrep terraform)` to reduce priority; cancel job | Enforce per-team state isolation; set CI runner memory limit per job; alert when `terraform state list \| wc -l` > 500 per workspace |
| Disk I/O saturation: multiple `terraform init` operations downloading large provider binaries | `iostat -x 1 3` on CI runner shows 100% disk utilization; all jobs slow | All teams' `terraform init` steps time out | Cancel the concurrent init jobs; free disk space: `find /home/runner -name ".terraform" -type d -mtime +1 -exec rm -rf {} +` | Configure `TF_PLUGIN_CACHE_DIR=/opt/tf-plugins` shared across all jobs on runner; set runner disk alert at 80% |
| Network bandwidth monopoly: team downloading multiple large provider binaries simultaneously | `iftop -n -i eth0` on runner shows Terraform download consuming 90% of bandwidth | Other teams' provider downloads time out; `terraform init` fails with connection errors | Throttle download: `tc qdisc add dev eth0 root tbf rate 50mbit burst 32kbit latency 50ms` on runner | Use `network_mirror` or `filesystem_mirror` for provider distribution; pre-bake providers into CI runner AMI |
| Connection pool starvation: concurrent Terraform applies exhausting AWS API connections | `TF_LOG=DEBUG terraform apply 2>&1 \| grep "429\|throttle"` from multiple pipelines simultaneously; AWS API returning throttle errors | Multiple teams' `-parallelism=10` applies hitting same AWS API endpoint simultaneously | `kill` excess Terraform apply jobs in CI; serialize with GitHub Actions `concurrency` group | Set CI-level mutex per AWS account; reduce `-parallelism=3`; implement exponential backoff in provider via `retry_mode = "adaptive"` |
| Quota enforcement gap: no per-workspace state size limit | One team's state file grows to 500MB; S3 GET costs spike; plan/apply very slow for all | Other teams sharing same S3 bucket see GET rate throttling | Compress state: `terraform state rm` for unused resources; enable state file S3 server-side compression | Set S3 lifecycle policy; alert on state file size > 50MB via CloudWatch S3 object size metric; enforce module decomposition |
| Cross-tenant state leak: shared S3 bucket with overly broad IAM policy | `aws s3api get-bucket-policy --bucket <state-bucket>` — shows `Principal: "*"` or team B has access to team A's prefix | Team B can read Team A's state file containing secrets and resource details | `aws s3api put-bucket-policy --bucket <state-bucket> --policy file://restrictive-policy.json` to scope per-prefix | Separate S3 state buckets per team or per environment; use S3 bucket policies with prefix-level IAM conditions |
| Rate limit bypass: team running many short `terraform plan` operations to probe cloud API | `aws cloudtrail lookup-events --lookup-attributes AttributeKey=UserName,AttributeValue=<tf-role>` shows hundreds of API calls/hour from one workspace | AWS API throttling affects all workspaces sharing the IAM role | Revoke workspace token temporarily; add CloudWatch alarm on `ThrottlingException` rate per IAM role | Implement per-workspace IAM roles with separate API rate limit budgets; use `-parallelism=1` for discovery/drift-detection plans |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure: no Terraform plan/apply duration metrics | No data on how long applies take; SLO for infrastructure change speed unmonitorable | Terraform CLI has no built-in Prometheus exporter; CI metrics not forwarded | Parse CI pipeline duration from CI API: `gh api /repos/<org>/<repo>/actions/runs \| jq '.workflow_runs[] \| {name, duration: .updated_at}'` | Export Terraform Cloud run metrics via TFE API to Prometheus using custom exporter; or use CI platform metrics (GitHub Actions workflow duration) |
| Trace sampling gap: failed `terraform apply` resource errors not correlated | Operator knows apply failed but cannot trace which exact AWS API call failed and why | `TF_LOG` not set by default; DEBUG output too verbose to store permanently | Re-run with `TF_LOG=JSON terraform apply 2>&1 \| tee /tmp/apply-debug.log` after failure; grep for `error` | Set `TF_LOG=JSON` in CI and ship structured debug log to log aggregation on failure only (via `if: failure()` CI step) |
| Log pipeline silent drop: CI artifact log truncated at 4MB | Large `terraform apply` output exceeds CI log size limit; end of apply output (including errors) missing | GitHub Actions / GitLab CI truncate logs at 4MB; late-plan errors not captured | Re-run apply with `terraform apply -no-color 2>&1 \| tee /tmp/apply.log && gzip /tmp/apply.log` then upload as artifact | Upload full apply log as CI artifact regardless of truncation; use `jq` to extract only error lines before upload |
| Alert rule misconfiguration: drift detection never fires | Terraform drift (manual AWS console changes) accumulates undetected for weeks | Prometheus alert on `terraform plan` exit code requires a scheduled plan job that was never deployed | Run manual drift check: `terraform plan -detailed-exitcode -out=drift.tfplan 2>&1`; exit code 2 = drift detected | Deploy scheduled CI pipeline running `terraform plan -detailed-exitcode` hourly; alert on exit code 2 via CI notification |
| Cardinality explosion: per-resource Terraform metrics | Custom Prometheus exporter emits one time series per Terraform-managed resource; millions of time series; Prometheus OOM | Naive exporter labels each resource by `resource_address` which is unique per instance | Drop per-resource labels; aggregate to workspace/module level only | Redesign exporter to emit counts and durations aggregated by `workspace`, `module`, `resource_type` — not per-resource instance |
| Missing health endpoint: no monitoring for Terraform Cloud workspace status | TFE workspace stuck in "planning" for hours; no alert fires | TFE UI shows status but no webhook or metric export configured | Poll TFE API: `curl -H "Authorization: Bearer $TFE_TOKEN" https://app.terraform.io/api/v2/workspaces/<ws-id>/runs \| jq '.data[0].attributes.status'` | Set up TFE notification config: workspace → Settings → Notifications → Slack/webhook on run failure; add Prometheus exporter for TFE workspace health |
| Instrumentation gap: no metrics for Terraform state lock acquisition time | State lock contention goes unnoticed; CI pipelines queue silently for 30+ minutes on locked state | DynamoDB lock table has no built-in CloudWatch metric for lock hold duration | Query DynamoDB lock table: `aws dynamodb scan --table-name terraform-locks --output json \| jq '.Items[] \| {LockID, Created:.Info.S}'` | Add CloudWatch alarm: custom metric for lock age > 10 minutes; publish via Lambda polling DynamoDB lock table every minute |
| Alertmanager outage silencing Terraform security alerts | `terraform apply` by unauthorized user goes undetected; no PagerDuty page | Alertmanager crash while Prometheus fires `unauthorized_terraform_apply` alert from CloudTrail event | Query CloudTrail directly: `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventSource,AttributeValue=sts.amazonaws.com --start-time <t1>` | Configure AWS CloudWatch Alarm directly on CloudTrail metric filter (independent of Prometheus) as backup alert path |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Terraform CLI minor version upgrade (e.g., 1.6 → 1.7) rollback | `terraform plan` produces different output; resource recreation triggered unexpectedly by provider behavior change | `terraform plan -no-color 2>&1 \| grep "must be replaced"`; compare plan output with previous version: `git stash && terraform-v1.6 plan` | Pin CLI version in CI: `tfenv use 1.6.6`; update `.terraform-version` file in repo | Pin Terraform version via `.terraform-version` (tfenv) or `required_version` in `terraform.tf`; test upgrades in staging before rolling out |
| Major version upgrade (e.g., 0.15 → 1.0): state format change | `terraform state pull` on old state fails with "state format error"; plan shows all resources for recreation | `terraform show` — if resources show as new, state format migration required; `terraform version` shows current version | `terraform state replace-provider` and re-run state migration; or restore pre-upgrade state from S3 version | Run `terraform 0.13upgrade` / `terraform 1.0upgrade` in dry-run; test on copy of production state; never skip state format migration steps |
| Schema migration partial completion: provider resource schema change mid-apply | Apply partially completes; new provider version requires new required attribute; apply fails half-way through | `terraform plan -no-color 2>&1 \| grep "required attribute\|Error\|missing"`; `terraform state list` to see which resources were already modified | Run `terraform apply -target=<failed-resource>` after adding required attribute; or `terraform state rm` and re-import for affected resources | Lock provider version in `.terraform.lock.hcl`; test provider upgrade in staging; read provider changelog before upgrading |
| Rolling workspace upgrade version skew: TFE workspaces at different Terraform versions | Workspace A on 1.6, Workspace B on 1.7; shared module incompatible with both; one workspace fails plan | `cat .terraform-version` or `terraform version` in each workspace CI pipeline; check `required_version` in module | Pin module to version compatible with both; or upgrade all workspaces simultaneously | Manage Terraform version via workspace-level `.terraform-version`; automate version drift detection via CI matrix test |
| Zero-downtime state migration to new S3 backend gone wrong | Traffic migrated to new backend but old state not fully copied; resources appear unmanaged in new workspace | `terraform state pull > /tmp/new-state.json && terraform state list \| wc -l` — compare to `wc -l` from old backend | Stop using new backend; `terraform state push /tmp/old-state.json` to restore; fix migration script | Use `terraform init -migrate-state` (not manual copy); verify state integrity with `terraform state list` count comparison before and after |
| Config format change: HCL2 syntax incompatibility after upgrade | `terraform validate` fails with HCL parse errors after upgrading Terraform version | `terraform validate -json 2>&1 \| jq '.diagnostics[] \| {summary, detail}'` | Revert Terraform version in CI: update `.terraform-version`; restore previous HCL syntax | Run `terraform validate` in CI for every PR; use pre-commit hook with `terraform fmt -check`; test version upgrades on a branch |
| Data format incompatibility: `terraform import` generating state incompatible with existing config | Imported resource state differs from config schema; subsequent `terraform plan` shows perpetual diff | `terraform plan -no-color 2>&1 \| grep "will be updated in-place\|perpetual"` | `terraform state rm <resource>`; fix config to match actual resource attributes; re-import | After `terraform import`, immediately run `terraform plan` and verify zero diff before committing state; use `import` blocks (Terraform 1.5+) for reproducibility |
| Dependency version conflict: AWS provider 5.x breaking existing module using 4.x resources | `terraform init` fails: "Incompatible provider version"; or `terraform plan` shows resource schema validation errors | `cat .terraform.lock.hcl \| grep required_providers`; `TF_LOG=DEBUG terraform init 2>&1 \| grep "provider\|version\|constraint"` | Pin provider in `required_providers`: `version = "~> 4.67"`; run `terraform init -upgrade=false` | Lock provider version in `required_providers` with `~>` minor-version constraint; run `terraform init -upgrade` only intentionally in a PR |

## Kernel/OS & Host-Level Failure Patterns

| Pattern | Symptoms | Detection | Terraform-Specific Diagnosis | Mitigation |
|---------|----------|-----------|------------------------------|------------|
| OOM kill of Terraform process during large plan | `terraform plan` killed mid-execution, exit code 137, partial state refresh | `dmesg \| grep -i "oom.*terraform" && journalctl -u terraform-runner \| grep -i "killed\|signal 9\|oom" && cat /proc/meminfo \| grep MemAvailable` | `terraform providers \| wc -l && terraform state list \| wc -l && ls -lh .terraform/providers/ && terraform plan -json 2>&1 \| tail -5` | Increase runner memory; use `-target` to plan subsets; enable `TF_CLI_ARGS_plan="-refresh=false"` for initial pass; split large state into multiple workspaces with `terraform_remote_state` data sources |
| Disk pressure on Terraform plugin cache | Provider downloads fail with `No space left on device`, init fails mid-download | `df -h ~/.terraform.d/plugin-cache && du -sh ~/.terraform.d/ && df -h /tmp && ls -lh .terraform/providers/` | `terraform providers lock -platform=linux_amd64 2>&1 && du -sh .terraform/providers/*/*/*/ \| sort -rh \| head -10 && terraform version -json \| jq '.provider_selections'` | Configure shared plugin cache: `export TF_PLUGIN_CACHE_DIR=/opt/terraform-cache`; prune old provider versions: `find ~/.terraform.d/plugin-cache -mtime +30 -delete`; use provider mirror for air-gapped environments |
| CPU throttling causing provider API timeout | `terraform apply` fails with context deadline exceeded on cloud API calls | `top -bn1 \| grep terraform && cat /sys/fs/cgroup/cpu/cpu.stat 2>/dev/null \| grep throttled && terraform apply 2>&1 \| grep -i "timeout\|deadline"` | `TF_LOG=DEBUG terraform plan 2>&1 \| grep -i "timeout\|retry\|rate\|throttl" \| tail -20 && env \| grep TF_` | Increase CPU limits on CI runner; set provider-specific timeouts: `timeouts { create = "30m" }`; configure `TF_CLI_ARGS_apply="-parallelism=5"` to reduce concurrent API calls; use provider retries |
| Kernel DNS resolver failure breaking provider auth | Terraform provider authentication fails, OIDC/OAuth token endpoints unreachable | `cat /etc/resolv.conf && dig registry.terraform.io +short && nslookup login.microsoftonline.com && curl -s https://sts.amazonaws.com/ \| head -5` | `TF_LOG=DEBUG terraform init 2>&1 \| grep -i "dns\|resolve\|lookup\|dial" \| tail -20 && terraform providers \| grep -i "registry\|source"` | Configure explicit DNS servers in `/etc/resolv.conf`; use `TF_CLI_CONFIG_FILE` with `host` block for custom registry endpoints; set `HTTP_PROXY`/`HTTPS_PROXY` for corporate networks; cache provider binaries locally |
| Inode exhaustion from Terraform module downloads | `terraform init` fails to unpack modules, `.terraform/modules` directory full of extracted files | `df -i $(pwd) && find .terraform/modules -type f \| wc -l && du -sh .terraform/modules/` | `terraform get -json 2>&1 \| jq '.modules \| length' 2>/dev/null && cat .terraform/modules/modules.json \| jq '.Modules \| length' && ls -la .terraform/modules/ \| wc -l` | Clean modules before init: `rm -rf .terraform/modules && terraform get`; use `source` with Git refs instead of registry to reduce unpacked files; consolidate modules; increase filesystem inode count |
| NUMA imbalance on CI runner processing large state | Terraform state operations (list, mv, rm) take 10x longer on multi-socket runners | `numactl --hardware && numastat -p $(pgrep terraform) 2>/dev/null && time terraform state list \| wc -l` | `ls -lh terraform.tfstate 2>/dev/null && terraform state list \| wc -l && time terraform plan -refresh-only -json 2>&1 \| tail -1` | Pin Terraform process to single NUMA node: `numactl --cpunodebind=0 --membind=0 terraform plan`; split state into smaller workspaces; use `-refresh=false` and targeted plans for large states |
| Noisy neighbor on shared CI runner starving Terraform | Terraform plan/apply takes 5x normal duration, intermittent cloud API timeouts | `pidstat -p $(pgrep terraform) 1 5 2>/dev/null && cat /proc/$(pgrep terraform)/status 2>/dev/null \| grep -i "voluntary\|context_switch" && kubectl top pod 2>/dev/null` | `time terraform plan -no-color 2>&1 \| tail -10 && TF_LOG=TRACE terraform plan 2>&1 \| grep -i "http.*duration\|retry\|429\|503" \| tail -20` | Use dedicated CI runner pool for Terraform; set resource requests/limits in CI pod spec; reduce parallelism: `terraform apply -parallelism=2`; implement Terraform plan caching |
| Filesystem lock contention on state file | Multiple Terraform runs fail with state lock errors, stale lock files on NFS/EFS | `ls -la .terraform.tfstate.lock.info 2>/dev/null && cat .terraform.tfstate.lock.info 2>/dev/null && mount \| grep -i "nfs\|efs\|cifs"` | `terraform force-unlock -force <lock-id> 2>&1 && terraform state pull \| jq '.serial, .lineage' && aws dynamodb scan --table-name <lock-table> --filter-expression "attribute_exists(LockID)" 2>/dev/null \| jq '.Items'` | Use remote state backends with native locking (S3+DynamoDB, GCS, Azure Blob); never store state on NFS; set `lock_timeout = "5m"` in backend config; automate stale lock cleanup in CI pipeline |

## Deployment Pipeline & GitOps Failure Patterns

| Pattern | Symptoms | Detection | Terraform-Specific Diagnosis | Mitigation |
|---------|----------|-----------|------------------------------|------------|
| Terraform state drift from manual cloud console changes | `terraform plan` shows unexpected changes, resources to be updated/destroyed that were manually modified | `terraform plan -detailed-exitcode; echo "Exit: $?" && terraform plan -json \| jq '.resource_changes[] \| select(.change.actions != ["no-op"]) \| {address, actions: .change.actions}'` | `terraform refresh && terraform plan -json \| jq '[.resource_changes[] \| select(.change.actions \| contains(["update"]))] \| length' && terraform show -json \| jq '.values.root_module.resources \| length'` | Enable drift detection in CI: `terraform plan -detailed-exitcode` (exit 2 = drift); import manual changes: `terraform import <address> <id>`; use `lifecycle { ignore_changes }` for expected drift fields; enforce tag-based change tracking |
| Atlantis/TFC plan-apply race condition | Multiple PRs modifying same workspace, second apply fails with state version conflict | `terraform state pull \| jq '.serial' && git log --oneline -10 && terraform plan 2>&1 \| grep -i "state\|serial\|lock\|conflict"` | `terraform force-unlock -force <lock-id> 2>&1 && terraform state pull \| jq '{serial, lineage, terraform_version}' && curl -s -H "Authorization: Bearer $TFC_TOKEN" https://app.terraform.io/api/v2/workspaces/<ws-id>/runs \| jq '.data[:5] \| .[].attributes.status'` | Configure workspace-level concurrency limits in TFC/Atlantis; use PR auto-merge queues; implement `depends_on` between workspace runs; add state locking verification in CI pre-apply step |
| Provider version constraint conflict after renovate/dependabot update | `terraform init` fails with provider version resolution errors, incompatible constraints across modules | `terraform init 2>&1 \| grep -i "incompatible\|constraint\|version\|required" && cat .terraform.lock.hcl \| grep -A2 "provider" && terraform version -json \| jq '.provider_selections'` | `terraform providers \| grep -v "^$" && find . -name "*.tf" -exec grep -l "required_providers" {} \; && grep -rh "version\s*=" --include="*.tf" . \| grep -i "provider\|terraform"` | Pin provider versions with `~>` constraints in root module; use `.terraform.lock.hcl` committed to Git; run `terraform init -upgrade` in separate PR for provider updates; test with `terraform validate` in CI |
| GitOps apply fails due to Terraform backend migration | Backend config changed in PR, `terraform init` requires interactive migration prompt | `terraform init 2>&1 \| grep -i "migrate\|backend\|copy\|reconfigure" && cat backend.tf && terraform state pull \| jq '.backend'` | `terraform init -reconfigure 2>&1 && terraform state list \| wc -l && diff <(terraform output -json) <(cat expected-outputs.json) 2>/dev/null` | Use `terraform init -migrate-state` in CI pipeline; add backend migration step in PR workflow; split migration into two PRs (add new backend, remove old); use `terraform state push` for manual migration |
| Terraform Cloud/Enterprise workspace variable drift | Workspace variables in TFC differ from code-defined defaults, unexpected plan results | `curl -s -H "Authorization: Bearer $TFC_TOKEN" https://app.terraform.io/api/v2/workspaces/<ws-id>/vars \| jq '.data[] \| {key: .attributes.key, value: .attributes.value, sensitive: .attributes.sensitive}'` | `terraform plan -var-file=<env>.tfvars -json \| jq '.resource_changes[] \| select(.change.actions != ["no-op"]) \| .address' && diff <(terraform show -json \| jq '.variables') <(cat <env>.tfvars.json)` | Version-control workspace variables via `tfe_variable` resources; use variable sets for shared config; implement `terraform plan` output diff check in CI; pin sensitive variables with checksum validation |
| Module registry unreachable during CI | `terraform init` fails to download modules from private registry, builds blocked | `terraform init 2>&1 \| grep -i "registry\|module\|download\|timeout\|403\|404" && curl -s https://registry.terraform.io/.well-known/terraform.json && terraform providers mirror /tmp/test 2>&1` | `cat .terraformrc 2>/dev/null && env \| grep TF_CLI_CONFIG && terraform get -update 2>&1 \| tail -20 && dig app.terraform.io +short` | Cache modules in CI: `terraform providers mirror <dir>`; use Git source references instead of registry; configure `.terraformrc` with `credentials` block for private registries; implement module vendoring with `terraform get -update` |
| Sentinel/OPA policy failure blocking legitimate changes | `terraform apply` rejected by policy check, false positive on valid infrastructure change | `curl -s -H "Authorization: Bearer $TFC_TOKEN" https://app.terraform.io/api/v2/runs/<run-id>/policy-checks \| jq '.data[] \| {name: .attributes["result-item"].policies[].name, result: .attributes.result}'` | `terraform plan -json \| jq '.resource_changes[] \| {address, actions: .change.actions, type: .type}' && sentinel test -run <policy-name> 2>&1` | Review policy with `sentinel apply -trace <policy>.sentinel`; add policy exceptions via `tfe_policy_set_parameter`; update policy to whitelist change pattern; override with `tfe_admin_run` if urgent and authorized |
| State file corruption after interrupted apply | `terraform plan` fails with state parse errors, resources show in inconsistent state | `terraform state pull \| jq '.serial' 2>&1 && terraform validate 2>&1 && terraform plan 2>&1 \| head -30 \| grep -i "error\|corrupt\|invalid\|parse"` | `terraform state list 2>&1 && terraform show 2>&1 \| head -20 && terraform state pull > /tmp/state-backup.json && jq '.' /tmp/state-backup.json \| wc -l && terraform state pull \| jq '.resources \| length'` | Restore from state backup: `terraform state push <backup>.tfstate`; for S3 backend, restore from versioned bucket: `aws s3api list-object-versions --bucket <bucket> --prefix <key>`; use `terraform state rm` and `terraform import` for individual resources |

## Service Mesh & API Gateway Edge Cases

| Pattern | Symptoms | Detection | Terraform-Specific Diagnosis | Mitigation |
|---------|----------|-----------|------------------------------|------------|
| Terraform-managed Istio VirtualService conflicts with mesh | Service mesh routing breaks after Terraform apply, VirtualService resources show conflicting specs | `kubectl get virtualservice -A -o json \| jq '.items[] \| select(.metadata.annotations["app.terraform.io/workspace-name"] != null) \| {name: .metadata.name, ns: .metadata.namespace}' && terraform state list \| grep "kubernetes_manifest.*virtualservice"` | `terraform plan -target='kubernetes_manifest.virtualservice' -json \| jq '.resource_changes[].change' && kubectl get virtualservice <name> -o yaml \| diff - <(terraform state show 'kubernetes_manifest.virtualservice["<key>"]' -no-color)` | Use `kubectl_manifest` provider instead of `kubernetes_manifest` for CRDs; set `server_side_apply = true` with `force_conflicts = true`; add lifecycle `ignore_changes` for mesh-injected annotations; separate mesh config from app Terraform |
| API gateway Terraform resource create/update race | Terraform creates API Gateway routes before backend is ready, health checks fail, routes return 503 | `terraform state show 'aws_apigatewayv2_route.main' && aws apigatewayv2 get-routes --api-id <id> \| jq '.Items[] \| {RouteKey, Target}' && terraform plan -json \| jq '.resource_changes[] \| select(.type \| test("apigateway"))'` | `terraform graph \| grep -i "apigateway\|integration\|deployment" && aws apigatewayv2 get-integrations --api-id <id> \| jq '.Items[] \| {IntegrationId, IntegrationUri, ConnectionType}'` | Add explicit `depends_on` between route, integration, and deployment resources; use `aws_apigatewayv2_deployment` with `triggers` map for automatic redeployment; implement health check verification in `provisioner "local-exec"` |
| Terraform destroying mesh sidecar injection namespace label | `terraform apply` removes `istio-injection=enabled` label, new pods deployed without sidecars | `kubectl get ns -l istio-injection=enabled && terraform state show 'kubernetes_namespace.app' \| grep -A5 labels && terraform plan -json \| jq '.resource_changes[] \| select(.type=="kubernetes_namespace") \| .change.after.metadata[0].labels'` | `terraform plan -target='kubernetes_namespace.app' 2>&1 && kubectl get ns <ns> -o json \| jq '.metadata.labels' && diff <(terraform show -json \| jq '.values.root_module.resources[] \| select(.type=="kubernetes_namespace") \| .values.metadata[0].labels') <(kubectl get ns <ns> -o json \| jq '.metadata.labels')` | Add `istio-injection` label to namespace resource in Terraform; use `lifecycle { ignore_changes = [metadata[0].labels["istio-injection"]] }` if managed externally; use `kubernetes_labels` resource for additive label management |
| NetworkPolicy Terraform resource blocking mesh control plane | Terraform-managed NetworkPolicy too restrictive, blocks Istio/Linkerd control plane traffic | `kubectl get networkpolicy -n <ns> -o json \| jq '.items[] \| select(.metadata.annotations \| has("app.terraform.io/workspace-name"))' && terraform state list \| grep network_policy` | `terraform state show 'kubernetes_network_policy.main' \| grep -A20 "ingress\|egress" && kubectl describe networkpolicy -n <ns> && kubectl exec <pod> -c istio-proxy -- pilot-agent request GET stats \| grep "cx_connect_fail"` | Add mesh control plane CIDR/labels to NetworkPolicy ingress/egress rules; use `cidr_blocks` for istiod IPs; allow port 15017 (webhook), 15012 (xDS), 15014 (metrics); template NetworkPolicy with mesh-aware rules |
| Terraform ALB ingress controller annotation drift | ALB annotations managed by both Terraform and ingress controller, constant plan changes | `terraform plan -json \| jq '.resource_changes[] \| select(.type \| test("ingress\|alb")) \| {address, actions: .change.actions}' && kubectl get ingress -n <ns> -o json \| jq '.items[0].metadata.annotations \| with_entries(select(.key \| test("alb")))'` | `terraform state show 'kubernetes_ingress_v1.main' \| grep -i "annotation" && diff <(terraform show -json \| jq '.values.root_module.resources[] \| select(.type=="kubernetes_ingress_v1") \| .values.metadata[0].annotations') <(kubectl get ingress <name> -n <ns> -o json \| jq '.metadata.annotations')` | Use `lifecycle { ignore_changes = [metadata[0].annotations] }` for controller-managed annotations; separate Terraform-managed annotations with prefix convention; use `kubernetes_annotations` resource for additive management |
| Service mesh mTLS config Terraform race with cert-manager | Terraform applies PeerAuthentication before cert-manager issues certificates, mTLS handshake fails | `terraform state list \| grep "peer_authentication\|certificate" && kubectl get peerauthentication -n <ns> && kubectl get certificate -n <ns> -o json \| jq '.items[] \| {name: .metadata.name, ready: .status.conditions[0].status}'` | `terraform graph \| grep -i "peer_auth\|certificate\|issuer" && terraform plan -json \| jq '.resource_changes[] \| select(.type \| test("peer_auth\|certificate")) \| {address, actions: .change.actions}'` | Add `depends_on` from PeerAuthentication to Certificate resource; use `kubectl_manifest` with `wait_for` block for certificate readiness; implement `time_sleep` resource between cert issuance and mTLS enforcement |
| Terraform-managed Gateway API resources version conflict | Gateway API CRDs upgraded but Terraform provider still generates old API version, apply fails | `kubectl get crds \| grep gateway && terraform providers \| grep -i "kubernetes\|kubectl" && terraform plan 2>&1 \| grep -i "gateway\|apiVersion\|unsupported\|version"` | `terraform state show 'kubernetes_manifest.gateway' \| grep apiVersion && kubectl api-versions \| grep gateway && terraform version -json \| jq '.provider_selections'` | Upgrade Terraform Kubernetes provider to match Gateway API CRD version; use `kubernetes_manifest` with explicit `apiVersion` field; run `terraform state rm` and `terraform import` for version migration; update `.terraform.lock.hcl` |
| Terraform cloud provider rate limiting during mesh infra deploy | Terraform apply fails with 429 errors deploying mesh-related cloud resources (NLB, security groups, VPC) | `terraform apply 2>&1 \| grep -i "429\|rate\|throttl\|exceeded\|limit" && TF_LOG=DEBUG terraform apply 2>&1 \| grep "HTTP/.*429\|Retry-After" \| tail -20` | `terraform plan -json \| jq '[.resource_changes[] \| .type] \| group_by(.) \| map({type: .[0], count: length}) \| sort_by(-.count) \| .[0:10]' && env \| grep -i "AWS_\|AZURE_\|GOOGLE_"` | Reduce parallelism: `terraform apply -parallelism=2`; add provider retry config: `provider "aws" { max_retries = 10 }`; stagger resource creation with `depends_on` chains; use `time_sleep` between large resource batches; request rate limit increase from cloud provider |
