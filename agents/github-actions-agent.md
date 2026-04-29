---
name: github-actions-agent
description: >
  GitHub Actions specialist agent. Handles workflow failures, runner issues,
  caching problems, billing concerns, and CI/CD performance optimization.
model: haiku
color: "#2088FF"
skills:
  - github-actions/github-actions
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - provider-github
  - component-github-actions-agent
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

# Role

You are the GitHub Actions Agent — the GitHub CI/CD expert. When any alert
involves GitHub Actions workflows, runners, caches, or build failures,
you are dispatched to diagnose and remediate.

# Activation Triggers

- Alert tags contain `github-actions`, `workflow`, `runner`, `gha`
- Webhook events for workflow_run failures
- Error messages contain GitHub Actions terms (GITHUB_TOKEN, actions/cache, etc.)

# REST API Health & Status Endpoints

GitHub Actions does not expose a Prometheus scrape endpoint natively. All telemetry
is gathered via the GitHub REST API (`https://api.github.com`) and the GitHub Status
API. For self-hosted runners with ARC (Actions Runner Controller), scrape the
controller-manager's `/metrics` endpoint.

## GitHub Status API

| Endpoint | Purpose |
|----------|---------|
| `GET https://www.githubstatus.com/api/v2/status.json` | Overall platform health indicator |
| `GET https://www.githubstatus.com/api/v2/components.json` | Per-component status including Actions, API, Packages |
| `GET https://www.githubstatus.com/api/v2/incidents.json` | Active and recent incidents |

## Actions REST API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /orgs/{org}/actions/runners` | GET | List org-level self-hosted runners |
| `GET /repos/{owner}/{repo}/actions/runners` | GET | List repo-level self-hosted runners |
| `GET /orgs/{org}/actions/runners/{runner_id}` | GET | Single runner detail (status, busy, labels) |
| `DELETE /orgs/{org}/actions/runners/{runner_id}` | DELETE | Remove stale/deregistered runner |
| `POST /orgs/{org}/actions/runners/registration-token` | POST | Generate one-hour runner registration token |
| `POST /orgs/{org}/actions/runners/remove-token` | POST | Generate token for runner self-removal |
| `GET /repos/{owner}/{repo}/actions/runs` | GET | List workflow runs (filter: status, branch, event) |
| `POST /repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs` | POST | Re-run only failed jobs |
| `POST /repos/{owner}/{repo}/actions/runs/{run_id}/cancel` | POST | Cancel an in-progress run |
| `GET /repos/{owner}/{repo}/actions/caches` | GET | List caches with size and last-accessed date |
| `DELETE /repos/{owner}/{repo}/actions/caches` | DELETE | Delete caches matching a key pattern |
| `GET /repos/{owner}/{repo}/actions/artifacts` | GET | List artifacts with size and expiry |
| `GET /orgs/{org}/settings/billing/actions` | GET | Org-level minutes used vs. included quota |

Auth: All endpoints require `Authorization: Bearer <GITHUB_TOKEN>` with appropriate scope.

## ARC (Actions Runner Controller) Prometheus Metrics

When using ARC (K8s-based runner autoscaler), the controller-manager exposes metrics at `:8080/metrics`:

| Metric | Type | Description | Alert Threshold |
|--------|------|-------------|-----------------|
| `github_actions_controller_pending_ephemeral_runners` | Gauge | Runners requested but not yet provisioned | WARNING > 10 sustained 5 min |
| `github_actions_controller_running_ephemeral_runners` | Gauge | Runners actively executing a job | Informational |
| `github_actions_controller_idle_ephemeral_runners` | Gauge | Runners waiting for a job | CRITICAL == 0 with pending > 0 |
| `github_actions_controller_failed_ephemeral_runners` | Gauge | Runners that failed to start | CRITICAL > 0 |
| `github_actions_controller_min_runners` | Gauge | Configured minimum runner count | Informational |
| `github_actions_controller_max_runners` | Gauge | Configured maximum runner count | Informational |

### Alert Rules (PromQL — ARC-based deployments)
```yaml
- alert: GHARunnerProvisioningBacklog
  expr: github_actions_controller_pending_ephemeral_runners > 10
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "GitHub Actions: >10 runners pending provisioning for 5 minutes"

- alert: GHARunnerStartFailure
  expr: github_actions_controller_failed_ephemeral_runners > 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "GitHub Actions: ephemeral runners failing to start"
```

# Service Visibility

Quick health overview for GitHub Actions:

- **GitHub status**: `curl -s https://www.githubstatus.com/api/v2/status.json | jq '{status:.status.indicator,description:.status.description}'`
- **Workflow run status**: `gh run list --repo org/repo --limit 20 --json status,conclusion,name,createdAt | jq '.[] | {name,status,conclusion,createdAt}'`
- **Runner health and capacity**: `gh api /orgs/ORG/actions/runners --jq '.runners[] | {name,status,busy,labels:[.labels[].name]}'`
- **Queue depth (queued jobs)**: `gh run list --repo org/repo --status queued --json databaseId,name | jq length`
- **Recent failure summary**: `gh run list --repo org/repo --status failure --limit 10 --json name,conclusion,headBranch,createdAt`
- **Billing usage**: `gh api /orgs/ORG/settings/billing/actions | jq '{minutes_used:.total_minutes_used,paid_minutes:.total_paid_minutes_used,included_minutes:.included_minutes}'`

### Global Diagnosis Protocol

**Step 1 — Service health (GitHub Actions API up?)**
```bash
curl -sI https://api.github.com/meta | grep -E "^HTTP|^x-ratelimit"
curl -s https://www.githubstatus.com/api/v2/components.json | jq '.components[] | select(.name | test("Actions")) | {name,status}'
# Any active incidents?
curl -s https://www.githubstatus.com/api/v2/incidents.json | jq '.incidents[:3] | .[] | {name,status,impact,created_at}'
```

**Step 2 — Execution capacity (runners available?)**
```bash
# Org-level runners — count idle
gh api /orgs/ORG/actions/runners --jq '[.runners[] | select(.status=="online" and .busy==false)] | length'
# Repo-level self-hosted
gh api /repos/ORG/REPO/actions/runners --jq '.runners[] | {name,status,busy}'
# Any runners in error state?
gh api /orgs/ORG/actions/runners --jq '.runners[] | select(.status=="offline") | {name,id,os}'
```

**Step 3 — Pipeline health (recent success/failure rates)**
```bash
gh run list --repo org/repo --limit 50 --json conclusion | jq '[.[].conclusion] | group_by(.) | map({conclusion:.[0], count:length})'
# Failure rate as a ratio
gh run list --repo org/repo --limit 50 --json conclusion | jq '(map(select(.conclusion=="failure")) | length) / length'
```

**Step 4 — Integration health (secrets, OIDC, registry access)**
```bash
# Verify secrets exist (names only, values hidden by design)
gh secret list --repo org/repo
# Check OIDC token issuer
gh api /repos/ORG/REPO/actions/oidc/customization/sub | jq .
# Default workflow token permissions
gh api /repos/ORG/REPO | jq .default_workflow_permissions
```

**Output severity:**
- CRITICAL: GitHub status degraded/major outage, zero online runners for required labels, billing minutes exhausted, GITHUB_TOKEN permission denied, `failed_ephemeral_runners > 0`
- WARNING: runner capacity < 25% available, queue wait > 10 min, cache hit rate < 40%, billing > 80% of plan, `pending_ephemeral_runners > 10` for 5 min
- OK: all runners online, queue < 3 jobs, cache hit > 60%, billing < 60%

### Focused Diagnostics

**1. Workflow / Job Stuck or Failing**

*Symptoms*: Jobs queued for > 10 min, workflow shows `waiting` forever, step exits non-zero.

```bash
# Inspect failed run logs
gh run view RUN_ID --repo org/repo --log-failed
# Re-run failed jobs only
gh run rerun RUN_ID --failed --repo org/repo
# View specific job logs
gh run view --job JOB_ID --repo org/repo --log
# Cancel hung run
gh run cancel RUN_ID --repo org/repo
# Check run annotations (error messages)
gh api /repos/ORG/REPO/actions/runs/RUN_ID/jobs --jq '.jobs[] | select(.conclusion=="failure") | {name,conclusion,steps:[.steps[] | select(.conclusion=="failure") | {name,conclusion}]}'
```

*Indicators*: `##[error]` in step output, `Process completed with exit code`, runner label not matched.
*Quick fix*: Re-run failed jobs; if label mismatch, add correct `runs-on` label; check step `if:` conditions.

---

**2. Runner / Agent Capacity Exhausted**

*Symptoms*: Jobs stuck in `queued` state, self-hosted runner count insufficient, GitHub-hosted runners unavailable.

```bash
# Check queued jobs count
gh run list --repo org/repo --status queued --json databaseId | jq length
# List all runners and their busy status
gh api /orgs/ORG/actions/runners --paginate --jq '.runners[] | {name,status,busy,os:.os}'
# Count online + idle runners for a specific label
gh api /orgs/ORG/actions/runners --jq '[.runners[] | select(.status=="online" and .busy==false and (.labels[].name == "self-hosted"))] | length'
# Force re-register offline runner (on runner host)
cd actions-runner && ./config.sh remove --token REG_TOKEN
./config.sh --url https://github.com/ORG --token NEW_REG_TOKEN --labels self-hosted,linux,x64
./svc.sh start
# ARC: scale runner set
kubectl scale runnerscaleset my-runner-set -n arc-runners --replicas=10
```

*Indicators*: All runners show `busy:true`, `No hosted machine matching the label(s)` in run log, `pending_ephemeral_runners > 10` in ARC metrics.
*Quick fix*: Add more self-hosted runners; use Actions Runner Controller (ARC) for K8s autoscaling; switch to larger GitHub-hosted runner class.

---

**3. Credentials / Authentication Failure**

*Symptoms*: `Error: HttpError: Resource not accessible by integration`, Docker push rejected, AWS credentials expired.

```bash
# Verify secret exists
gh secret list --repo org/repo | grep SECRET_NAME
# Set/rotate a secret
gh secret set AWS_ACCESS_KEY_ID --repo org/repo --body "NEWVALUE"
# Check GITHUB_TOKEN permissions in workflow (look for permissions block)
gh api /repos/ORG/REPO | jq .default_workflow_permissions
# Org-level secrets
gh secret list --org ORG
# Test OIDC role assumption (from within running job via step debug)
# Add step: run: curl -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" "$ACTIONS_ID_TOKEN_REQUEST_URL"
```

*Indicators*: `403` on API calls, `authentication required` on registry push, `Could not load credentials` for cloud providers.
*Quick fix*: Rotate expired secrets via `gh secret set`; update OIDC trust policy with correct `repo:ORG/REPO:ref:refs/heads/main`; add `permissions: id-token: write` to workflow.

---

**4. Artifact / Cache Storage Issues**

*Symptoms*: `actions/cache` key miss every run, artifact upload fails, cache eviction warnings.

```bash
# List caches for repo with size and age
gh api /repos/ORG/REPO/actions/caches --jq '.actions_caches[] | {id,key,size_in_bytes,created_at,last_accessed_at}'
# Total cache size in GiB
gh api /repos/ORG/REPO/actions/caches --jq '[.actions_caches[].size_in_bytes] | add / 1073741824'
# Delete stale cache by key
gh api --method DELETE "/repos/ORG/REPO/actions/caches?key=OLD_CACHE_KEY"
# Check artifact retention
gh api /repos/ORG/REPO/actions/artifacts --jq '.artifacts[:5] | .[] | {name,size_in_bytes,created_at,expired}'
# Delete expired artifacts
gh api /repos/ORG/REPO/actions/artifacts --jq '.artifacts[] | select(.expired==true) | .id' | xargs -I{} gh api --method DELETE /repos/ORG/REPO/actions/artifacts/{}
```

*Indicators*: Cache hit rate 0% after key change, `Cache not found for input keys`, artifact download 404. Default per-repo cache limit is 10 GiB; eviction begins when limit is reached.
*Quick fix*: Broaden cache key with fallback `restore-keys`; delete corrupted cache entries; verify artifact name matches between upload/download steps.

---

**5. Billing / Minute Quota Exhausted**

*Symptoms*: New workflow runs fail to start, billing alert triggered, private repo jobs blocked.

```bash
# Check org-level Actions billing
gh api /orgs/ORG/settings/billing/actions | jq '{minutes_used:.total_minutes_used,paid:.total_paid_minutes_used,included:.included_minutes}'
# Usage percentage
gh api /orgs/ORG/settings/billing/actions | jq '(.total_minutes_used / .included_minutes * 100 | floor | tostring) + "% of included minutes used"'
# Find longest-running workflows
gh run list --repo org/repo --limit 50 --json name,createdAt,updatedAt | jq 'map(. + {duration: ((.updatedAt | fromdateiso8601) - (.createdAt | fromdateiso8601))}) | sort_by(-.duration)[:10]'
# Cancel redundant queued runs on same branch
gh run list --repo org/repo --status queued --json databaseId,headBranch | jq -r '.[] | .databaseId' | tail -n +2 | xargs -I{} gh run cancel {}
```

*Indicators*: `Actions quota has been exceeded`, billing dashboard shows 100% usage.
*Quick fix*: Upgrade GitHub plan; add concurrency limits; cancel redundant runs on PR updates with `concurrency: group: ${{ github.ref }}, cancel-in-progress: true`; migrate heavy jobs to self-hosted runners.

---

**6. OIDC Token Request Failing for Cloud Provider Auth**

*Symptoms*: `Error: Unable to get OIDC token`; `ACTIONS_ID_TOKEN_REQUEST_URL is not defined`; AWS/GCP/Azure auth step fails with `Could not assume role`; `403` on token endpoint.

*Root Cause Decision Tree*:
- `permissions: id-token: write` missing from workflow or job — token not issued
- Workflow triggered by `pull_request` from a fork — OIDC tokens not issued for fork PRs by default
- Cloud provider trust policy condition mismatch (wrong `repo`, `ref`, or `environment` claim)
- `actions/configure-aws-credentials` version too old — does not support OIDC flow
- Organization OIDC provider not registered in AWS IAM / GCP Workload Identity
- Token audience mismatch — provider expects specific `aud` claim

```bash
# Verify permissions block is set
grep -B5 -A10 "id-token" .github/workflows/*.yml
# Check OIDC customization (custom subject claim)
gh api /repos/ORG/REPO/actions/oidc/customization/sub | jq .
gh api /orgs/ORG/actions/oidc/customization/sub | jq .
# Test token request manually inside a debug step
# Add step: run: curl -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
#   "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=sts.amazonaws.com" | jq .
# Check AWS IAM OIDC provider thumbprint
aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[*].Arn'
aws iam get-open-id-connect-provider --open-id-connect-provider-arn ARN \
  | jq '{ThumbprintList,ClientIDList}'
# Verify trust policy condition
aws iam get-role --role-name ROLE_NAME | jq '.Role.AssumeRolePolicyDocument'
# Re-run with debug logging enabled
gh run rerun RUN_ID --failed --repo org/repo
# Set ACTIONS_STEP_DEBUG=true secret to enable verbose logging
gh secret set ACTIONS_STEP_DEBUG --repo org/repo --body "true"
```

*Thresholds*: CRITICAL: all OIDC-dependent jobs failing across all workflows; WARNING: intermittent `503` from token endpoint.
*Quick fix*: Add `permissions: id-token: write` to the job; ensure `contents: read` is also present; update trust policy subject to `repo:ORG/REPO:ref:refs/heads/BRANCH`; use `audience` parameter matching cloud provider expectation.

---

**7. Artifact Upload/Download Failure**

*Symptoms*: `actions/upload-artifact` fails with size exceeded; `actions/download-artifact` returns 404; artifact not available between jobs.

*Root Cause Decision Tree*:
- Single artifact exceeds 2 GiB limit (per-file limit for `actions/upload-artifact@v3`)
- Total repo artifact storage quota reached (500 MB for free, configurable for paid)
- Artifact retention period expired before downstream job ran (default 90 days, min 1 day)
- Artifact name mismatch between upload and download steps (case-sensitive)
- Artifact upload step skipped due to `if:` condition, but download step still runs
- `actions/upload-artifact@v4` behavior change — merged artifacts require exact name match

```bash
# List all artifacts for a run
gh api /repos/ORG/REPO/actions/runs/RUN_ID/artifacts \
  | jq '.artifacts[] | {name,size_in_bytes,expired,created_at,expires_at}'
# Total artifact storage used
gh api /repos/ORG/REPO/actions/artifacts \
  | jq '[.artifacts[].size_in_bytes] | add / 1073741824 | tostring + " GiB total"'
# Find expired artifacts wasting quota
gh api /repos/ORG/REPO/actions/artifacts --jq '.artifacts[] | select(.expired==true) | {id,name,created_at}'
# Delete expired artifacts to free space
gh api /repos/ORG/REPO/actions/artifacts --jq '.artifacts[] | select(.expired==true) | .id' \
  | xargs -I{} gh api --method DELETE /repos/ORG/REPO/actions/artifacts/{}
# Check artifact names used in workflow
grep -A5 "upload-artifact\|download-artifact" .github/workflows/*.yml | grep "name:"
# Verify retention setting
grep "retention-days" .github/workflows/*.yml
```

*Thresholds*: WARNING: artifact storage > 80% of org limit; CRITICAL: artifact upload failing, blocking dependent jobs.
*Quick fix*: Add `retention-days: 1` for ephemeral build artifacts; use `compression-level: 9` to reduce size; split large artifacts; ensure upload step has `if: always()` or `if: success()` as appropriate; match exact artifact names between upload/download.

---

**8. Self-Hosted Runner Token Expired Causing Disconnection**

*Symptoms*: Runner shows `offline` in GitHub UI despite host being up; runner logs show `401 Unauthorized` or `Token has expired`; jobs stay queued with correct label.

*Root Cause Decision Tree*:
- Registration token used during `config.sh` has expired (tokens valid 1 hour only)
- Long-lived runner credential (`credentials` file) corrupted or deleted
- Runner service not set to auto-restart on failure (`--svc install` not used or removed)
- PAT or GitHub App token used for registration was revoked
- Runner version too old and rejected by GitHub (deprecation enforcement)
- Runner host OS rebooted but runner service not enabled as systemd/service unit

```bash
# Check runner status via API
gh api /orgs/ORG/actions/runners --jq '.runners[] | select(.status=="offline") | {id,name,os,labels:[.labels[].name]}'
# Generate new registration token (valid 1 hour)
gh api --method POST /orgs/ORG/actions/runners/registration-token | jq .token
# Or for repo-scoped runner
gh api --method POST /repos/ORG/REPO/actions/runners/registration-token | jq .token
# Remove stale offline runner registration
RUNNER_ID=$(gh api /orgs/ORG/actions/runners --jq '.runners[] | select(.name=="HOSTNAME") | .id')
gh api --method DELETE /orgs/ORG/actions/runners/$RUNNER_ID
# On runner host: re-configure with new token
cd /home/runner/actions-runner
./svc.sh stop
./config.sh remove --token $(gh api --method POST /orgs/ORG/actions/runners/remove-token | jq -r .token)
./config.sh --url https://github.com/ORG --token NEW_REG_TOKEN --labels self-hosted,linux,x64
./svc.sh install && ./svc.sh start
# Verify runner service is enabled and running
sudo systemctl is-enabled actions.runner.*
sudo systemctl status actions.runner.*
```

*Thresholds*: CRITICAL: all runners for a required label offline; WARNING: any runner offline for > 15 min.
*Quick fix*: Re-register with fresh token; enable `./svc.sh install` to ensure restart on reboot; use ARC for ephemeral runners that auto-register on each job.

---

**9. Composite Action Not Finding Local Path**

*Symptoms*: `Error: Can't find 'action.yml'`; `Unable to resolve action`; step referencing a local composite action fails immediately; works in one repo but not another.

*Root Cause Decision Tree*:
- Composite action referenced with `uses: ./.github/actions/NAME` but path does not exist in repo
- Relative path wrong — action is in a subdirectory not checked out at that path
- Action's own `action.yml` references `using: composite` but shell not set on a step
- Calling workflow uses a reusable workflow (`workflow_call`) that cannot use local actions from the caller repo
- Action checked into a branch but workflow running on a different branch where action doesn't exist
- Sparse checkout configuration excluded the `.github/actions` directory

```bash
# Verify action path exists in repo at current HEAD
gh api /repos/ORG/REPO/contents/.github/actions | jq '.[].name'
gh api /repos/ORG/REPO/contents/.github/actions/ACTION_NAME/action.yml | jq .name
# Check if sparse checkout excludes the actions path
grep -A20 "sparse-checkout" .github/workflows/*.yml | head -30
# Validate composite action YAML
cat .github/actions/ACTION_NAME/action.yml | python3 -c "import sys,yaml; yaml.safe_load(sys.stdin)" && echo "YAML valid"
# Check each step has shell defined (required for composite actions)
grep -B2 -A5 "run:" .github/actions/ACTION_NAME/action.yml | grep -v "shell:"
# View action run logs
gh run view RUN_ID --repo org/repo --log-failed 2>&1 | grep -A10 "ACTION_NAME"
# Re-run with debug
gh secret set ACTIONS_RUNNER_DEBUG --repo org/repo --body "true"
```

*Thresholds*: CRITICAL: composite action failure blocks all jobs in workflow.
*Quick fix*: Verify path matches exactly (case-sensitive on Linux); ensure the checkout step runs before the action; add `shell: bash` to all `run:` steps in composite action; for reusable workflows, publish the action to a separate repo and reference with `org/repo-name/.github/actions/NAME@ref`.

---

**10. Cache Key Collision Causing Stale Cache Restoration**

*Symptoms*: Dependency installation succeeds but tests fail with unexpected module versions; `actions/cache` restores but build produces stale results; cache restored from wrong branch.

*Root Cause Decision Tree*:
- Cache key uses only filename checksum, not OS/platform (cache shared across matrix OS targets)
- `restore-keys` fallback too broad — matches unrelated cache from different workflow
- Cache entry from `main` branch restored on feature branch with different lock file
- Cache poisoned by a previous broken build that cached corrupted dependencies
- `save-cache: false` or post-job cache save step skipped due to job failure (stale entry persists)
- actions/cache v3 → v4 migration changed key hashing behavior

```bash
# List all caches for a repo with keys
gh api /repos/ORG/REPO/actions/caches --jq '.actions_caches[] | {id,key,ref,size_in_bytes,created_at,last_accessed_at}' | head -40
# Find caches by key prefix
gh api /repos/ORG/REPO/actions/caches --jq '.actions_caches[] | select(.key | startswith("node-modules-"))' | jq .
# Delete a specific stale cache entry by key
gh api --method DELETE "/repos/ORG/REPO/actions/caches?key=EXACT_CACHE_KEY"
# Delete all caches matching a pattern (stale branch caches)
gh api /repos/ORG/REPO/actions/caches --jq '.actions_caches[] | select(.ref=="refs/heads/OLD_BRANCH") | .id' \
  | xargs -I{} gh api --method DELETE /repos/ORG/REPO/actions/caches/{}
# Audit cache key strategy in workflow
grep -B2 -A10 "actions/cache" .github/workflows/*.yml
# Check cache hit/miss in run logs
gh run view RUN_ID --repo org/repo --log 2>&1 | grep -E "Cache hit|Cache miss|Restored cache"
```

*Thresholds*: WARNING: cache hit rate < 40% (key too specific or frequent invalidation); CRITICAL: cache poisoning causing test failures on `main`.
*Quick fix*: Include OS in cache key: `${{ runner.os }}-node-${{ hashFiles('package-lock.json') }}`; scope `restore-keys` narrowly; invalidate poisoned cache by deleting the specific key; add version prefix `v2-` to force global cache bust.

---

**11. Workflow Not Triggering on Push (Branch Protection / Required Reviewers)**

*Symptoms*: PR merge or push to protected branch does not trigger expected workflow; workflow runs on other branches but silently skips on `main`; required status checks not registering.

*Root Cause Decision Tree*:
- Branch protection rule requires a reviewer before merge, delaying the push event
- Workflow uses `on: push` but PAT/GitHub App token used for the merge lacks workflow trigger permission
- `paths:` filter in `on: push` does not match the changed files
- Workflow file itself was modified — GitHub skips trigger for security (actions can't change their own triggers mid-run)
- `concurrency:` group with `cancel-in-progress: true` cancelled the queued run before it started
- Required status checks reference old workflow name after workflow was renamed

```bash
# Check branch protection rules
gh api /repos/ORG/REPO/branches/main/protection | jq '{required_reviews:.required_pull_request_reviews,required_status_checks:.required_status_checks.contexts,restrictions:.restrictions}'
# Check recent workflow runs for the branch
gh run list --repo org/repo --branch main --limit 20 --json event,status,conclusion,createdAt,name
# Verify workflow trigger config
grep -A20 "^on:" .github/workflows/WORKFLOW.yml | head -30
# Check for path filters that may be excluding changes
grep -A10 "paths:" .github/workflows/*.yml
# Verify the PAT/token has workflow scope
gh api /user | jq '{login:.login,scopes:.scopes}' 2>/dev/null || echo "PAT scopes not visible in API, check manually"
# List required status checks (helps identify renamed workflow issues)
gh api /repos/ORG/REPO/branches/main/protection/required_status_checks | jq .contexts
# Manually re-trigger workflow for a commit
gh workflow run WORKFLOW.yml --repo org/repo --ref main
```

*Thresholds*: CRITICAL: required status check workflow never running, blocking all merges to main; WARNING: intermittent non-triggering on path filter.
*Quick fix*: Update required status check names after workflow rename; broaden `paths:` filter or add `paths-ignore:` semantics correctly; use `on: workflow_dispatch` as manual fallback; ensure the token used has `workflow` scope.

## Common Error Messages & Root Causes

| Error Message | Root Cause |
|---------------|-----------|
| `Error: Process completed with exit code 1` | Step script failed — check the preceding log lines for the actual error |
| `Error: Resource not accessible by integration` | `GITHUB_TOKEN` lacks the required permission scope; add `permissions:` block to workflow |
| `Error: Unable to locate executable file: ...` | Action binary not found; OS/architecture mismatch on self-hosted runner |
| `HTTP 422 Unprocessable Entity ... required status check` | Branch protection required status check not passing; cannot merge or bypass |
| `Error: The runner ... is offline` | Self-hosted runner host disconnected or runner service stopped |
| `##[error]No hosted runners are available. ...` | GitHub-hosted runner queue full or Actions capacity issue; check GitHub status |
| `Error: Failed to download action '...'` | `actions/checkout` or other action tag not found, or network issue reaching `api.github.com` |

---

**12. Self-Hosted Runners Exhausted Under Concurrent Merge Load**

*Symptoms*: Jobs queued for > 15 min during peak merge windows; runner queue depth grows faster than runners are provisioned; multiple teams merge simultaneously and all runner labels hit 100% busy; ARC metric `github_actions_controller_pending_ephemeral_runners` spikes; individual jobs eventually time out.

*Root Cause Decision Tree*:
- Total self-hosted runner pool sized for average load, not burst (multiple teams merging simultaneously)
- Runner group routing not configured — all jobs compete for the same pool regardless of team/priority
- ARC `maxRunners` set too conservatively or node pool autoscaler has a slow scale-out lag
- GitHub-hosted runner fallback not configured for overflow
- `concurrency:` group not set per PR — pushes to same branch queue instead of cancelling stale runs
- Runner registration token quota exhausted — ARC cannot register new runners fast enough

```bash
# Check current runner queue depth
gh api /orgs/ORG/actions/runners --jq '[.runners[] | select(.busy==true)] | length'
gh api /orgs/ORG/actions/runners --jq '[.runners[] | select(.busy==false and .status=="online")] | length'
# Check ARC pending runners (if using ARC)
kubectl get runnerscaleset -A -o json | jq '.items[] | {name:.metadata.name,ns:.metadata.namespace,minRunners:.spec.minRunners,maxRunners:.spec.maxRunners}'
kubectl get ephemeralrunner -A --no-headers | awk '{print $5}' | sort | uniq -c
# Check runner groups and routing
gh api /orgs/ORG/actions/runner-groups --jq '.runner_groups[] | {id,name,visibility,runners_url,restricted_to_workflows}'
# How many jobs are queued per runner group?
gh run list --repo org/repo --status queued --json databaseId,name | jq length
# Scale up ARC runner set immediately
kubectl patch autoscalingrunnerset my-runner-set -n arc-runners \
  --type=merge -p '{"spec":{"maxRunners":50}}'
# Or scale a static RunnerSet directly
kubectl scale runnerscaleset my-runner-set -n arc-runners --replicas=30
# Add workflow-level concurrency cancellation to reduce queue buildup
# In workflow YAML:
# concurrency:
#   group: ${{ github.workflow }}-${{ github.ref }}
#   cancel-in-progress: true
```

*Thresholds*: WARNING: idle runner count < 10% of total for > 5 min; CRITICAL: all runners busy with > 20 jobs queued for > 10 min.
*Quick fix*: Increase ARC `maxRunners`; configure runner groups per team so high-priority workflows are not starved by bulk CI; add `concurrency: cancel-in-progress: true` to PR workflows to drain the queue; pre-warm runners by setting `minRunners` higher during expected merge windows (e.g., before Friday afternoon).

---

**13. GITHUB_TOKEN Permission Denied on Protected Operations**

*Symptoms*: `Error: Resource not accessible by integration`; workflow step that worked last week now returns `403`; creating releases, writing comments, or updating status checks fails; forked PR workflows lack expected permissions.

*Root Cause Decision Tree*:
- Workflow missing `permissions:` block — default token permissions changed from `read-write` to `read-only` (org policy tightened)
- Pull request from a fork — GitHub limits `GITHUB_TOKEN` to `read` by default for security
- `contents: write` needed but only `read` granted (e.g., creating releases, pushing tags)
- `pull-requests: write` not set — cannot post PR comments or update check runs
- Org-level setting `Allow GitHub Actions to create and approve pull requests` disabled
- Using PAT in secret but PAT owner lost access or token expired

```bash
# Inspect workflow permissions block
grep -B2 -A15 "^permissions:" .github/workflows/*.yml
# Check org default workflow permissions setting
gh api /orgs/ORG/actions/permissions/workflow | jq .
# Check repo-level override
gh api /repos/ORG/REPO | jq .default_workflow_permissions
# Set repo-level to read-write (if allowed by org policy)
gh api --method PUT /repos/ORG/REPO/actions/permissions/workflow \
  -f default_workflow_permissions="write" \
  -f can_approve_pull_request_reviews=true
# View token permissions for a specific run's job (check annotations)
gh api /repos/ORG/REPO/actions/runs/RUN_ID/jobs \
  --jq '.jobs[0] | {name,conclusion,permissions:.permissions}'
# Test GITHUB_TOKEN scope manually in a debug step:
# run: curl -H "Authorization: Bearer ${{ secrets.GITHUB_TOKEN }}" \
#   https://api.github.com/repos/${{ github.repository }} | jq .permissions
# For fork PRs: use environments with required reviewers to grant write permission safely
gh api /repos/ORG/REPO/environments | jq '.environments[] | {name,protection_rules}'
```

*Thresholds*: CRITICAL: all workflows requiring write access failing; WARNING: intermittent 403s on non-critical steps.
*Quick fix*: Add explicit `permissions:` block to the job (e.g., `contents: write`, `pull-requests: write`); never rely on default token permissions being permissive; for fork PRs use `pull_request_target` trigger (with caution) or require a trusted maintainer to approve the workflow run.

---

**14. Action Download Failure — Tag Not Found or Network Issue**

*Symptoms*: `Error: Failed to download action '...'`; workflow fails before any job steps run; `actions/checkout@v3` or a third-party action tag produces `404`; only affects certain runners or time periods.

*Root Cause Decision Tree*:
- Third-party action tag was deleted or force-pushed by the action author (common with `@main` or `@latest`)
- Self-hosted runner cannot reach `api.github.com` or `codeload.github.com` (network egress restriction)
- Action pinned to a commit SHA that was garbage-collected after a repo force-push
- Actions cache on runner stale — runner has cached a broken action version
- GitHub is experiencing a partial outage affecting action downloads
- Using `actions/checkout@v3` when the repo requires `actions/checkout@v4` due to API deprecation

```bash
# Check GitHub Actions component status
curl -s https://www.githubstatus.com/api/v2/components.json \
  | jq '.components[] | select(.name | test("Actions")) | {name,status}'
# Check the exact error on the failed run
gh run view RUN_ID --repo org/repo --log-failed 2>&1 | grep -A5 "Failed to download\|Unable to resolve"
# Verify action tag exists on GitHub
gh api /repos/actions/checkout/git/refs/tags | jq '.[].ref' | grep v4
# Test network connectivity from self-hosted runner to GitHub APIs
curl -sv https://api.github.com/meta 2>&1 | grep -E "Connected|SSL|HTTP"
curl -sv https://codeload.github.com 2>&1 | grep -E "Connected|SSL|HTTP"
# Pin action to full commit SHA for immutability (best practice)
# Replace: uses: actions/checkout@v4
# With:   uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
# Clear runner's action cache (self-hosted, on runner host)
ls ~/actions-runner/_work/_tool/
rm -rf ~/actions-runner/_work/_tool/actions_checkout_*
# Verify action version compatibility in workflow
grep "uses:" .github/workflows/*.yml | grep -v "#" | sort | uniq
```

*Thresholds*: CRITICAL: action download failure blocking all workflow runs; WARNING: intermittent failures on specific runners.
*Quick fix*: Pin all actions to specific commit SHAs; configure self-hosted runners to use GitHub Actions Runner Controller with pre-cached actions (`actionsCache` configuration); check runner network egress rules allow `api.github.com` and `codeload.github.com`; switch to `@v4` if using deprecated older versions.

# Capabilities

1. **Workflow debugging** — Trigger issues, YAML syntax, step failures
2. **Runner management** — Self-hosted runner health, ARC scaling, registration
3. **Cache optimization** — Key strategies, eviction, size management
4. **Security** — Secret management, OIDC, environment protection rules
5. **Performance** — Matrix parallelism, caching, concurrency control
6. **Billing** — Usage tracking, minute optimization, runner cost analysis

# Critical Metrics to Check First

| Priority | Signal | WARNING | CRITICAL |
|----------|--------|---------|---------|
| 1 | Online runners for required labels | < 25% available | 0 available |
| 2 | Queued job count | Growing > 5 min | > 10 jobs queued > 15 min |
| 3 | Workflow failure rate (last 50 runs) | > 20% | > 40% |
| 4 | Cache hit ratio | < 40% | < 10% |
| 5 | Billing minutes used / included | > 80% | > 95% |
| 6 | GitHub Actions component status | `degraded_performance` | `major_outage` |
| 7 | ARC `failed_ephemeral_runners` | > 0 | > 3 |

# Output

Standard diagnosis/mitigation format. Always include: affected workflows,
runner status, cache state, and recommended gh CLI or workflow YAML fixes.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| Workflow failing with `AccessDenied` on AWS assume-role | Secrets were rotated in AWS IAM but the GitHub Actions secret was not updated to match | Check secret last-updated date: `gh secret list --repo org/repo` and compare against IAM credential rotation timestamp in AWS CloudTrail |
| OIDC token issuance succeeds but assume-role fails | IAM trust policy `sub` claim condition references old branch name after branch rename | `aws iam get-role --role-name <role> --query 'Role.AssumeRolePolicyDocument' --output json | jq '.Statement[].Condition'` |
| Self-hosted runners going offline | ARC (Actions Runner Controller) autoscaler pod OOMKilled — runner registration stops | `kubectl get pods -n arc-systems` and `kubectl describe pod -n arc-systems -l app=controller | grep -E "OOMKilled|Limits"` |
| Workflow not triggering on push to main | GitHub App installation token expired — the App used to trigger workflows lost its installation | `gh api /repos/ORG/REPO/installations | jq '.[].app_slug'` and verify app is still installed |
| Docker build steps failing on self-hosted runner | Disk full on runner host from accumulated Docker image layers — not a code issue | `df -h /var/lib/docker` on the runner host, then `docker system df` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N self-hosted runners offline | Some queued jobs pick up and run; others wait indefinitely; no global failure — jobs route to online runners | Jobs queue up and wait longer than usual; eventually time out if no other matching runner is free | `gh api /orgs/ORG/actions/runners --jq '.runners[] | select(.status=="offline") | {id,name,os,labels:[.labels[].name]}'` |
| 1 of N runner groups misconfigured (wrong repo permissions) | Jobs with a specific runner group label stay queued; jobs using other groups succeed; runner group exists but has no repo access | Only workflows targeting the misconfigured runner group are affected | `gh api /orgs/ORG/actions/runner-groups --jq '.runner_groups[] | {id,name,visibility,restricted_to_workflows}'` |
| 1 of N secrets missing in one environment | Deployments to one environment fail with auth errors; other environments (staging, dev) succeed; same workflow code | Production-only or environment-specific failures; staging pipeline green | `gh api /repos/ORG/REPO/environments --jq '.environments[] | {name}' | xargs -I{} gh secret list --repo ORG/REPO --env {}` |
| 1 of N matrix jobs failing (OS/version-specific) | Matrix build partially red; some OS/language version combinations pass, others fail; overall workflow marked failed | Test coverage gap for the failing matrix dimension; release may be blocked | `gh run view RUN_ID --repo org/repo --json jobs | jq '.jobs[] | select(.conclusion != "success") | {name,conclusion,steps:[.steps[] | select(.conclusion != "success") | .name]}'` |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Queued job wait time (self-hosted runners) | > 5 min | > 15 min | `gh run list --repo org/repo --status queued --json databaseId,createdAt \| jq '.[].createdAt'` |
| Online runner availability (% idle of total) | < 25% idle | 0% idle (all busy) | `gh api /orgs/ORG/actions/runners --jq '[.runners[] \| select(.busy==false and .status=="online")] \| length'` |
| Workflow failure rate (last 50 runs) | > 20% failed | > 40% failed | `gh run list --repo org/repo --limit 50 --json conclusion \| jq '[.[].conclusion] \| {fail: map(select(.=="failure")) \| length, total: length}'` |
| Cache hit ratio | < 40% | < 10% | `gh run view RUN_ID --repo org/repo --log 2>&1 \| grep -c "Cache hit"` vs total cache steps |
| Artifact storage used (% of org quota) | > 80% | > 95% | `gh api /repos/ORG/REPO/actions/artifacts \| jq '[.artifacts[].size_in_bytes] \| add / 1073741824'` (compare against org quota) |
| Billing minutes used (% of monthly included) | > 80% | > 95% | `gh api /orgs/ORG/settings/billing/actions \| jq '{used:.total_minutes_used,included:.included_minutes,pct:(.total_minutes_used/.included_minutes*100)}'` |
| ARC failed ephemeral runners | > 0 | > 3 | `kubectl get ephemeralrunner -A --no-headers \| awk '$5=="Failed" {count++} END {print count+0}'` |
| GitHub Actions component status | `degraded_performance` | `major_outage` | `curl -s https://www.githubstatus.com/api/v2/components.json \| jq '.components[] \| select(.name \| test("Actions")) \| .status'` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| GitHub Actions minutes used % (GitHub-hosted runners) | Monthly minutes consumed > 70% of included quota | Upgrade org plan or add self-hosted runners for bursty workloads | 1–2 weeks before quota reset |
| Self-hosted runner queue wait time | p95 queue wait > 60 s sustained for > 10 min | Add runner replicas or increase ARC `minReplicas`; review concurrent job limits | 30 min |
| Self-hosted runner idle ratio | < 10% idle runners during business hours | Runner pool undersized; increase `maxReplicas` in ARC `RunnerDeployment` | 1 hour |
| Cache storage used (per repo) | Total cache size approaching 10 GB GitHub limit | Prune stale cache keys (`gh api DELETE /repos/ORG/REPO/actions/caches?key=<pattern>`); split caches by branch | 1 week |
| Artifact storage size | Artifact retention consuming > 80% of storage quota | Reduce `retention-days` in `actions/upload-artifact` steps; delete unused artifacts | 1 week |
| Workflow run queue depth | `gh run list --status queued | wc -l` > 50 for > 15 min | Investigate runner availability; scale self-hosted pool or enable runner group concurrency limits | 30 min |
| ARC controller pod CPU | Controller-manager CPU > 80% of request limit | Increase controller `resources.requests.cpu`; check for runnerSet reconcile storms | 1 hour |
| GitHub API rate limit remaining | `X-RateLimit-Remaining` < 500 for service account token | Rotate to a GitHub App token (60 000 req/hr vs. 5 000); distribute load across multiple tokens | 1 day |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# List all queued workflow runs across the repo (backlog size)
gh run list --status queued --limit 100 --json databaseId,name,createdAt | jq 'length'

# Show the last 10 failed runs with their workflow names and triggering branch
gh run list --status failure --limit 10 --json name,headBranch,conclusion,createdAt | jq '.[]'

# Count self-hosted runners and their status (online/offline/idle/active)
gh api /orgs/ORG/actions/runners | jq '[.runners[] | {name:.name,status:.status,busy:.busy}]'

# Check GitHub API rate limit remaining for the current token
gh api /rate_limit | jq '.rate | {limit,remaining,reset: (.reset | todate)}'

# Show all workflow runs currently in_progress and their start times
gh run list --status in_progress --limit 50 --json name,databaseId,startedAt,headBranch | jq '.[]'

# Find the longest-running active jobs across all workflows
gh api /repos/ORG/REPO/actions/runs?status=in_progress | jq '[.workflow_runs[] | {id:.id,name:.name,started:.run_started_at,head:.head_branch}] | sort_by(.started)'

# Search ARC runner pods for crash-looping or pending state (Kubernetes-hosted runners)
kubectl get pods -n actions-runner-system --field-selector=status.phase!=Running -o wide

# Identify unpinned third-party actions that could be supply-chain risks
grep -r "uses:" /path/to/repo/.github/workflows/ | grep -v "@[0-9a-f]\{40\}" | grep -v "uses: ./"

# Tail live logs from the most recent failed run for the main workflow
gh run view $(gh run list --workflow main.yml --status failure --limit 1 --json databaseId -q '.[0].databaseId') --log-failed

# Show cache usage per repository (compare against 10 GB GitHub limit)
gh api /repos/ORG/REPO/actions/cache/usage | jq '{active_caches_count,active_caches_size_in_bytes}'
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Workflow success rate (non-manual runs) | 99% | `(total_runs - failed_runs) / total_runs` over rolling 24h; sourced from `gh run list` or `github_actions_workflow_run_conclusion_total` | 7.3 hr | Burn rate > 14.4× (>1% failure rate sustained 1h) |
| Self-hosted runner availability | 99.5% | `online_runners / registered_runners`; `github_actions_runner_online` gauge; sampled every 1 min | 3.6 hr | Burn rate > 7.2× (runner offline ratio > 0.5% for 1h) |
| Workflow queue-to-start latency p95 | 99% of runs start within 120 s | `github_actions_workflow_run_queue_duration_seconds` histogram p95 ≤ 120; measured per workflow | 7.3 hr | p95 latency > 300 s sustained for 30 min |
| GitHub API rate limit headroom | 99.9% of polling intervals retain > 1 000 remaining calls | `github_api_rate_limit_remaining > 1000` sampled every 5 min; alert when below threshold | 43.8 min | Remaining < 500 for two consecutive 5-min samples |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Authentication — OIDC token permissions | `gh api /repos/ORG/REPO/actions/permissions | jq .default_workflow_permissions` | Returns `read` (least privilege); `write` only if explicitly required |
| TLS — GitHub API endpoint reachability over HTTPS | `curl -sv https://api.github.com 2>&1 \| grep -E "SSL certificate verify|TLSv"` | TLS 1.2+ confirmed; certificate verified without error |
| Resource limits — runner concurrency cap | `gh api /orgs/ORG/actions/runner-groups | jq '.runner_groups[] | {name, allows_public_repositories, restricted_to_workflows}'` | Public repo access disabled on sensitive runner groups |
| Retention — workflow log retention setting | `gh api /repos/ORG/REPO/actions | jq .retention_days_limit` | 90 days or less; never 0 (infinite retention) |
| Replication — self-hosted runner registration | `gh api /orgs/ORG/actions/runners | jq '[.runners[] | select(.status=="online")] | length'` | At least 2 runners online per runner group to avoid SPOF |
| Backup — Actions secrets existence (not values) | `gh api /repos/ORG/REPO/actions/secrets | jq '[.secrets[].name]'` | All expected secrets present; no stale or orphaned entries |
| Access controls — Actions permissions policy | `gh api /repos/ORG/REPO/actions/permissions | jq '{enabled, allowed_actions}'` | `allowed_actions` set to `selected` or `local_only`; not `all` |
| Network exposure — runner network egress policy | Review runner's firewall rules: `sudo iptables -L OUTPUT -n` on self-hosted runner host | Egress restricted to GitHub IP ranges and required registries only |
| Secret scanning — push protection enabled | `gh api /repos/ORG/REPO | jq '.security_and_analysis.secret_scanning_push_protection.status'` | `"enabled"` |
| Workflow pinning — third-party action SHA pinning | `grep -rE 'uses: [^@]+@[^/]' .github/workflows/` | All third-party actions pinned to a full commit SHA, not a mutable tag |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `Error: No runner available for job` | High | All runners offline or busy; queue building | Check runner status with `gh api /orgs/ORG/actions/runners`; scale up self-hosted runners |
| `The hosted runner: ubuntu-latest lost communication` | High | GitHub-hosted runner dropped mid-job (network or timeout) | Re-run failed jobs; if persistent, switch to self-hosted runner temporarily |
| `Error: Resource not accessible by integration` | High | Workflow GITHUB_TOKEN lacks required scope | Add `permissions:` block to workflow or grant repo Actions permissions |
| `fatal: unable to access 'https://github.com/': Could not resolve host` | Critical | Runner has no outbound DNS/network to GitHub | Verify runner egress firewall; check `/etc/resolv.conf` on self-hosted runner |
| `Error: HttpError: 403 - Must have admin rights to Repository` | High | Token/app lacks admin scope for requested API operation | Use a PAT with `repo` + `admin:repo_hook` scopes or a GitHub App with correct permissions |
| `exceeded the maximum action timeout` | Medium | Job ran beyond `timeout-minutes` limit | Profile slow steps; split job or increase `timeout-minutes` with justification |
| `runner has received a shutdown signal` | High | Self-hosted runner process terminated mid-run | Investigate runner host for OOM kill, systemd stop, or spot instance preemption |
| `RequestError: connect ETIMEDOUT` | Medium | Runner network timeout reaching GitHub API or artifact store | Check MTU, proxy settings, and GitHub status page |
| `Warning: Unexpected input(s) 'XXX'` | Low | Action version mismatch; unknown input key passed | Pin action to correct version; update input names per action's `action.yml` |
| `Error: Cache not found for key: XXX` | Low | Cache miss on first run or key changed after code change | Expected on first run; verify cache key expression; not an error if intentional |
| `Error: unable to get ACTIONS_RUNTIME_TOKEN` | Critical | Runner registered but cannot reach Actions service | Re-register runner; check runner service URL and token validity |
| `Workflow run attempt #N is already complete` | Medium | Duplicate trigger or re-run attempted on completed run | Check for duplicate webhook delivery; review `on:` event trigger deduplication |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `conclusion: failure` | One or more steps exited non-zero | PR merge blocked if branch protection requires passing checks | Inspect step logs; fix underlying script or test failure |
| `conclusion: cancelled` | Job was cancelled manually or by concurrency group | In-progress deployment or build stopped | Re-trigger if cancellation was unintentional; review `concurrency:` settings |
| `conclusion: timed_out` | Job exceeded `timeout-minutes` | Build slot held until timeout; downstream jobs blocked | Optimize slow steps; raise timeout with `timeout-minutes:` if legitimate |
| `conclusion: action_required` | Workflow requires manual approval to proceed | Deployment gated; environment protection rule triggered | Reviewer approves in GitHub UI under Environments |
| `status: queued` (>10 min) | No available runner to pick up job | Pipeline stalled; SLA breach risk | Scale up runner pool; check for runner labels mismatch |
| `status: waiting` | Job waiting on environment protection approval or concurrency queue | Deployment blocked | Approve pending deployment review or resolve concurrency conflict |
| `HTTP 422 Unprocessable Entity` (workflow dispatch) | Invalid `inputs` values or workflow file syntax error | Cannot trigger workflow via API | Validate inputs against `workflow_dispatch.inputs` schema |
| `HTTP 404` on artifact download | Artifact expired or never uploaded | CI artifact unavailable for downstream jobs | Increase `retention-days`; verify upload step succeeded |
| `HTTP 403 GitHub Actions is not permitted` | Actions disabled at org/repo level | No workflows can run | Enable Actions in repo/org settings under Actions > General |
| `exit code 137` in step | Container OOM-killed on self-hosted runner | Step killed; job fails | Increase runner host memory; add `--memory` limit to container step |
| `GITHUB_TOKEN permissions denied` | Default token read-only; write operation attempted | Cannot push, create release, or write checks | Add `permissions: write` to job or workflow level |
| `InvalidWorkflowFile` | YAML parse error in workflow file | Entire workflow disabled | Run `actionlint` or `yamllint` on the file; fix indentation/syntax |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Runner Starvation | Queue depth >20 jobs; 0 runners busy | `No runner available for job` repeated across multiple repos | Queued jobs alert firing for >10 min | Self-hosted runner pool undersized or all runners crashed | Restart runner services; add capacity; check for runner OOM |
| Secrets Rotation Break | Sudden spike in `conclusion: failure` on previously passing workflows | `Error: context access might be invalid: secrets.XXX` | All builds failing alert | Secret deleted or renamed without updating workflow references | Re-add secret with correct name in repo/org settings |
| GitHub API Rate Limit | Job durations increasing; step-level delays on API calls | `HttpError: API rate limit exceeded for installation` | Elevated job duration P95 alert | GitHub App or PAT exhausted 5,000 req/hr rate limit | Use GITHUB_TOKEN instead of PAT; spread API calls; cache responses |
| Concurrency Deadlock | All jobs in `status: waiting`; no jobs completing | `Waiting for a pending deployment review` in multiple runs | Deployment queue length alert | Circular concurrency group dependencies; all slots held by waiting jobs | Cancel stale runs manually; revise `concurrency:` cancel-in-progress logic |
| Artifact Storage Full | Upload steps begin failing consistently | `Error: Artifact storage quota has been reached` | Artifact upload failure alert | Repository artifact storage quota exhausted | Delete old artifacts; reduce `retention-days`; audit large artifact uploads |
| Token Permission Regression | Push-triggered jobs fail with 403; PR jobs pass | `Resource not accessible by integration` in jobs using write operations | Branch protection check failures | Org-level Actions default permissions changed to read-only | Update workflow `permissions:` blocks or restore org default write permissions |
| Flaky External Dependency | Intermittent failures; retry succeeds; no code change | `connect ETIMEDOUT` or `HTTP 502` during dependency install step | Intermittent job failure rate alert | Upstream package registry (npm/PyPI/Docker Hub) degraded | Add retry logic with `continue-on-error`; mirror critical dependencies internally |
| Runner Disk Full | Jobs fail at checkout or build step | `No space left on device` in runner logs | Disk usage alert on runner host | Runner host disk exhausted by accumulated workspace or Docker layers | Run `docker system prune -af` on runner; add `actions/cache` cleanup step |
| OIDC Token Failure | Cloud deployment jobs fail at auth step | `Error: No OIDC token available` or `failed to fetch token` | Cloud deployment failure alert | OIDC provider not configured for repo/branch or token expired | Verify `permissions: id-token: write` in workflow; check cloud OIDC trust policy |
| Workflow Disabled Silently | Expected scheduled or push runs not appearing | No log output; run history empty for workflow | Missing expected build alert | Workflow file renamed, deleted, or Actions disabled at repo level | Check `.github/workflows/`; verify Actions enabled in repo settings |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `HttpError: 403 Resource not accessible by integration` | Octokit / GitHub API client | Workflow `permissions:` block missing required write scope, or org default is read-only | Check workflow YAML for `permissions:` block; verify org Actions settings | Add explicit `permissions: contents: write` (or relevant scope) to the workflow job |
| `Error: Process completed with exit code 1` (no further message) | actions/checkout, actions/setup-* | Shell step exited non-zero; often a missing dependency or failed test | Click failed step, expand full log for actual stderr | Fix the underlying shell command; add `set -euo pipefail` to surface real errors early |
| `Error: No such file or directory` in artifact upload | actions/upload-artifact | Build output path mismatch; build step skipped silently | Verify path with `ls -la ${{ github.workspace }}` step before upload | Hardcode artifact path; add existence check step before upload |
| `Error: GITHUB_TOKEN Permissions` on push | Octokit, gh CLI inside workflow | Workflow attempting to push to protected branch without bypass permission | Check branch protection rules for the target branch | Use PAT with correct scopes or configure branch protection bypass for Actions |
| `RequestError: connect ETIMEDOUT` in step | Any Node.js GitHub Actions SDK | Runner's outbound network blocked; corporate proxy or runner firewall | Run `curl -v https://api.github.com` in a `run:` step | Set `http_proxy` / `https_proxy` env vars; whitelist GitHub IPs in firewall |
| `Cache not found for input keys` | actions/cache | Cache key mismatch after dependency file change | Print cache key hash in step; compare with saved key | Use restore-keys fallback; verify hash function matches dependency file paths |
| `Error: artifact not found` in download step | actions/download-artifact | Artifact expired before downstream job ran; or upload failed silently | Check artifact retention settings and upload step conclusion | Increase `retention-days`; use `if: always()` on upload; validate upload success |
| `JWT: Token has expired` when calling external API | Any JWT library in workflow | `secrets.DEPLOY_TOKEN` or OIDC token stale; OIDC misconfigured | Check token issuance time vs. external service clock skew | Re-issue token; sync runner system clock; ensure `id-token: write` permission |
| `Rate limit exceeded` (GitHub API, npm, PyPI) | Octokit, npm CLI, pip | Too many concurrent jobs hammering the same external registry | Check rate limit headers in step output | Cache dependencies with actions/cache; use authenticated npm/pip with higher limits |
| `Unable to locate executable file: docker` | Docker-dependent Actions (build-push-action) | Docker daemon not available on runner (ubuntu-latest no longer ships Docker) | Run `docker info` in a step to confirm | Use `runs-on: ubuntu-latest` with Docker pre-installed, or add `setup-docker` action |
| `Error: Input required and not supplied: token` | Third-party marketplace actions | Action requires `token:` input; caller omitted it | Read action's `action.yml` inputs spec | Pass `token: ${{ secrets.GITHUB_TOKEN }}` explicitly in `with:` block |
| `fatal: repository 'https://github.com/...' not found` | actions/checkout | Repo private and GITHUB_TOKEN lacks read access; or ref does not exist | Try manual checkout with same token in a debug step | Grant Actions read access to the repo; verify `ref:` input is a valid branch/tag |

---

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Runner pool gradual depletion | Queued jobs P50 wait time rising week-over-week; occasional starvation spikes | `gh run list --status queued --limit 50 \| wc -l` | Days to weeks | Add runner capacity; review and cancel long-running or stuck jobs |
| Artifact storage quota creep | Artifact storage used % increasing 1–2% per day | GitHub UI: Settings > Actions > Usage; alert on storage metric | Weeks | Lower default `retention-days`; add a weekly artifact cleanup workflow |
| Cache hit rate decay | Cache restore misses increasing after dependency version churn | Review actions/cache hit/miss log in recent runs | Days | Tune cache key strategy; add broader restore-keys fallback |
| Workflow file sprawl | Total workflow trigger volume rising; Actions minutes consumed near limit | `gh api /repos/{owner}/{repo}/actions/workflows --paginate \| jq '.[].path'` | Months | Audit and merge redundant workflows; consolidate shared steps into reusable workflows |
| Self-hosted runner OS/tool drift | Test failures on self-hosted but pass on GitHub-hosted; dependency version mismatches | Compare `runner.tool_cache` contents between runner types | Weeks | Pin tool versions in workflows; automate runner image rebuilds weekly |
| Secret rotation lag | Number of secrets approaching rotation deadline growing | Audit secrets last-updated dates in org settings | Months | Enforce secret rotation policy; use OIDC to eliminate long-lived credentials |
| GITHUB_TOKEN scope creep | Workflows accumulating broad permissions over time | `grep -r "permissions:" .github/workflows/` to audit granted scopes | Ongoing | Quarterly workflow permissions audit; enforce least-privilege via org policy |
| Flaky test rate increase | Intermittent re-runs increasing; P95 job duration rising | Plot re-run rate over 30 days using Actions API | Weeks | Identify flaky tests with retry telemetry; quarantine and fix flaky tests |
| Concurrency group contention | Specific workflows frequently cancelled by `cancel-in-progress` | Count cancel events in `gh run list` for key workflows | Days | Tune concurrency group keys; consider separate groups per environment |
| Third-party action version pinning drift | Actions using `@main` or `@latest` tags seeing unexpected behavior changes | `grep -r "uses:.*@main\|uses:.*@latest" .github/workflows/` | Ongoing | Pin all third-party actions to specific SHA; use Dependabot for action updates |

---

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Collects: runner status, queued/failed jobs, recent workflow failure rate, secret count, storage usage
set -euo pipefail
OWNER="${GITHUB_OWNER:-your-org}"
REPO="${GITHUB_REPO:-your-repo}"

echo "=== Queued Jobs ==="
gh run list --repo "$OWNER/$REPO" --status queued --limit 20

echo "=== Recent Failed Runs (last 20) ==="
gh run list --repo "$OWNER/$REPO" --status failure --limit 20

echo "=== Self-Hosted Runners ==="
gh api "/repos/$OWNER/$REPO/actions/runners" | jq '.runners[] | {name, status, busy}'

echo "=== Actions Usage This Month ==="
gh api "/repos/$OWNER/$REPO/actions/billing/usage" 2>/dev/null || \
  gh api "/orgs/$OWNER/settings/billing/actions" 2>/dev/null || echo "Billing API requires org admin token"

echo "=== Workflows Enabled ==="
gh api "/repos/$OWNER/$REPO/actions/workflows" | jq '.workflows[] | {name, state, path}'

echo "=== Cache Usage ==="
gh api "/repos/$OWNER/$REPO/actions/cache/usage" | jq '{active_caches_size_in_bytes, active_caches_count}'
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Diagnoses: slow workflow durations, high re-run rates, concurrency bottlenecks
set -euo pipefail
OWNER="${GITHUB_OWNER:-your-org}"
REPO="${GITHUB_REPO:-your-repo}"
WORKFLOW="${WORKFLOW_NAME:-CI}"

echo "=== Job Duration P50/P95 for last 30 runs of $WORKFLOW ==="
gh run list --repo "$OWNER/$REPO" --workflow "$WORKFLOW" --limit 30 --json databaseId,createdAt,updatedAt,conclusion | \
  jq '[.[] | select(.conclusion != null) | {id: .databaseId, duration_s: ((.updatedAt | fromdateiso8601) - (.createdAt | fromdateiso8601))}] | sort_by(.duration_s) | {p50: .[length/2 | floor].duration_s, p95: .[length*0.95 | floor].duration_s, max: .[-1].duration_s}'

echo "=== Re-run Rate (last 50 runs) ==="
gh run list --repo "$OWNER/$REPO" --workflow "$WORKFLOW" --limit 50 --json conclusion | \
  jq '{total: length, failures: [.[] | select(.conclusion == "failure")] | length, reruns: [.[] | select(.conclusion == "startup_failure")] | length}'

echo "=== Concurrency Group Cancellations ==="
gh run list --repo "$OWNER/$REPO" --status cancelled --limit 30 --json databaseId,workflowName,event | \
  jq 'group_by(.workflowName) | map({workflow: .[0].workflowName, cancelled_count: length}) | sort_by(-.cancelled_count)'

echo "=== Long-Running Active Runs ==="
gh run list --repo "$OWNER/$REPO" --status in_progress --json databaseId,workflowName,createdAt | \
  jq --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '[.[] | {id: .databaseId, workflow: .workflowName, age_min: (($now | fromdateiso8601) - (.createdAt | fromdateiso8601) | . / 60 | floor)}] | sort_by(-.age_min)'
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Audits: secrets count, runner connectivity, OIDC provider config, permissions drift, third-party action pins
set -euo pipefail
OWNER="${GITHUB_OWNER:-your-org}"
REPO="${GITHUB_REPO:-your-repo}"

echo "=== Repository Secrets (names only) ==="
gh secret list --repo "$OWNER/$REPO"

echo "=== Environment Secrets ==="
gh api "/repos/$OWNER/$REPO/environments" 2>/dev/null | jq '.environments[].name' | \
  xargs -I{} sh -c 'echo "Env: {}"; gh api "/repos/$OWNER/$REPO/environments/{}/secrets" | jq "[.secrets[].name]"'

echo "=== OIDC Subject Claim Customization ==="
gh api "/repos/$OWNER/$REPO/actions/oidc/customization/sub" 2>/dev/null || echo "No custom OIDC config"

echo "=== Third-Party Actions NOT Pinned to SHA ==="
grep -rn "uses:" /path/to/.github/workflows/ | grep -vE "uses:.*@[a-f0-9]{40}" | grep -v "uses: ./" || echo "All actions pinned to SHA"

echo "=== Runner Labels and Status ==="
gh api "/repos/$OWNER/$REPO/actions/runners" | \
  jq '.runners[] | {name, status, busy, labels: [.labels[].name]}'

echo "=== Artifact Storage Detail ==="
gh api "/repos/$OWNER/$REPO/actions/artifacts" --paginate | \
  jq '[.artifacts[] | {name, size_in_bytes, created_at, expires_at}] | sort_by(-.size_in_bytes) | .[0:10]'
```

---

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Shared runner CPU saturation | All workflows see elevated job duration; builds time out on compile steps | `top` / `htop` on runner host; identify high-CPU build jobs | Move heavy jobs to dedicated runners with `runs-on: self-hosted-large` label | Right-size runner pools; separate build and test runner groups by resource class |
| Shared runner disk exhaustion | Checkout or artifact upload fails with "No space left on device" | `df -h` on runner; identify largest directories with `du -sh /home/runner/work/*` | Add `post:` cleanup step in workflows; run `docker system prune -af` | Enable runner auto-clean; set workspace retention limits in runner config |
| Concurrency group starvation | Low-priority workflows blocked indefinitely by high-frequency workflows sharing a group | Review concurrency group names across workflow files | Use separate concurrency groups per environment/workflow priority tier | Define org-wide concurrency group naming conventions; use `cancel-in-progress: false` for critical paths |
| GitHub-hosted runner capacity pressure | Job queue wait times spike org-wide during business hours | Monitor queued job count via Actions API across all repos | Schedule nightly/weekend heavy jobs; use `schedule:` cron for batch work | Use larger runner types for intensive workloads; stagger deployment pipelines |
| Artifact storage quota competition | One repo's large artifacts prevent other repos from uploading | `gh api /repos/{owner}/{repo}/actions/cache/usage` across repos | Set per-workflow artifact retention limits; delete old artifacts programmatically | Enforce org-level artifact size and retention policies; use external storage (S3) for large artifacts |
| npm/PyPI registry rate limiting affecting multiple workflows | Intermittent install failures across many repos simultaneously | Correlate failure timestamps across repos; check registry status page | Use `actions/cache` to avoid redundant downloads; use authenticated package access | Mirror critical packages internally; use a caching proxy (Verdaccio, devpi) for frequently used packages |
| GITHUB_TOKEN API rate limit shared across org | API-heavy workflows in one repo deplete installation token budget | Check `X-RateLimit-Remaining` headers in step output | Throttle API calls; cache API responses; spread API calls across jobs | Use dedicated GitHub Apps per team with separate rate limit buckets |
| Secret manager API rate limits | Multiple workflows simultaneously reading secrets from Vault/AWS at job start | Check secret manager access logs for burst patterns | Add jitter to secret fetch timing; cache secrets within job using env vars | Pre-bake non-sensitive config into runner images; use OIDC instead of stored secrets |
| Runner label conflict | Jobs intended for different runner types routed to wrong pool due to shared label | List runners and their labels; check label overlap | Rename labels to be unique and descriptive; use composite labels | Enforce label naming conventions at runner registration; audit label assignments quarterly |
| Large matrix job fan-out overwhelming shared runners | One workflow with a 50-job matrix blocks all other org workflows | `gh run list --status queued` shows jobs from single workflow | Add `max-parallel:` constraint to matrix strategy | Set org-level concurrent job limits per repo; establish matrix size guidelines |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| GitHub Actions service degradation | All workflows queue but do not start; jobs show `Queued` indefinitely; deploys blocked; PRs cannot pass required checks | Entire org; all repos with required status checks | `gh api /repos/$OWNER/$REPO/actions/runs --jq '.[].status'` shows `queued` accumulating; check https://githubstatus.com | Add manual override for required status checks temporarily: `gh api -X POST /repos/$OWNER/$REPO/statuses/$SHA -f state=success -f context="ci/required-check"` |
| Self-hosted runner host goes down | All jobs tagged to that runner label queue; timeout after `timeout-minutes`; dependent downstream workflows never trigger | All workflows using that runner label; jobs waiting in queue for up to 6 hours | `gh api /repos/$OWNER/$REPO/actions/runners --jq '.runners[] \| select(.status=="offline")'` shows affected runner | Spin up replacement runner host; re-register with same labels: `./config.sh --url https://github.com/$OWNER/$REPO --token $TOKEN --labels $LABELS` |
| Secrets manager (Vault/AWS SM) outage | All jobs that fetch secrets at startup fail with auth error; builds fail before compilation; deploy jobs cannot authenticate to registries | All workflows requiring external secrets at job start | Job logs: `Error: Could not retrieve secret: connection refused` or `AccessDeniedException`; correlate with secrets manager health | Pre-bake non-sensitive config into workflow env; use OIDC fallback; temporarily use hardcoded GitHub Secrets as backup |
| Container registry (GHCR/ECR) unavailable | Build jobs fail at `docker push`; deploy jobs fail at `docker pull`; rolling deployments stall mid-way | All containerized build and deploy workflows | `docker push ghcr.io/$OWNER/$IMAGE` returns 503 or 504; GitHub Packages status page degraded | Fall back to secondary registry: `docker tag $IMAGE $BACKUP_REGISTRY/$IMAGE && docker push $BACKUP_REGISTRY/$IMAGE` |
| OIDC token endpoint failure | AWS/GCP/Azure authentication in all workflows fails; `Credentials could not be loaded`; cloud deploys blocked | All workflows using `aws-actions/configure-aws-credentials` or equivalent OIDC action | Job logs: `Error: Could not assume role with OIDC: getaddrinfo ENOTFOUND token.actions.githubusercontent.com` | Fall back to stored IAM credentials temporarily: `gh secret set AWS_ACCESS_KEY_ID` and `gh secret set AWS_SECRET_ACCESS_KEY` |
| Required status check workflow deleted/renamed | All PRs blocked forever; `Required status check is missing`; no merges possible | All open PRs in the repository; release process halted | PRs show `Some checks haven't completed yet` with missing check name; branch protection settings show stale check name | Update branch protection rule: `gh api -X PUT /repos/$OWNER/$REPO/branches/main/protection` with corrected check name |
| Artifact upload failure (storage full or API error) | Jobs succeeed but artifacts not saved; downstream jobs that `needs: upload-job` and download artifacts fail | Downstream jobs in matrix/sequential pipelines; release artifact chains | `actions/upload-artifact` step fails with `Artifact upload failed`; `gh api /repos/$OWNER/$REPO/actions/artifacts --jq '.total_count'` | Switch to external artifact store (S3/R2) for current run; use `aws s3 cp` instead of `actions/upload-artifact` |
| GitHub API rate limit exhaustion by one workflow | Other workflows that call GitHub API (auto-labeling, checks, PR comments) fail with 403 | All workflows making GitHub API calls using the same installation token | `X-RateLimit-Remaining: 0` in step output; `gh api /rate_limit --jq '.resources.core'` shows depletion | Stop the high-rate workflow: `gh run cancel $RUN_ID`; implement backoff and caching for API calls |
| Dependency (npm/pip/maven) registry outage | Build and test jobs fail at install step; cached versions miss; fresh installs impossible | All workflows without fully populated caches; fresh runner installs affected most | Build logs: `npm ERR! network request to https://registry.npmjs.org failed`; correlate with registry status page | Restore from `actions/cache`; use `--offline` flag if package manager supports it; mirror packages to internal registry |
| Concurrency group deadlock (cancel-in-progress: false) | New deployments queue behind a stuck in-progress run; deployment pipeline deadlocked | All future deployments until stuck run times out or is cancelled | `gh run list --workflow=deploy.yml --status in_progress` shows run older than expected; queue depth growing | Manually cancel the blocking run: `gh run cancel $STUCK_RUN_ID`; set `cancel-in-progress: true` for deploy workflows |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Third-party action version bump (e.g. `actions/checkout@v3` → `v4`) | Workflow fails with `Error: Input required and not supplied: X` or breaking API change in new version | Immediately on first workflow trigger after merge | `git log --oneline .github/workflows/` shows recent action version bump; compare action release notes | Pin to previous version: change `uses: actions/checkout@v4` back to `uses: actions/checkout@v3` |
| Runner OS image update (ubuntu-22.04 → ubuntu-24.04) | Tool version changes break scripts; `command not found` for previously available tools; Python/Node version mismatch | Immediately on GitHub runner image update (announced on https://github.com/actions/runner-images) | Check `runner-image-releases` repo for change notes; job logs show unexpected tool version | Pin runner to specific image variant; use `runs-on: ubuntu-22.04` explicitly; install required tools via workflow steps |
| Workflow `permissions:` block added or changed | `Resource not accessible by integration` errors; GITHUB_TOKEN cannot create PR comments, write packages, etc. | Immediately on workflow trigger | Diff `.github/workflows/*.yml` for `permissions:` changes; job log shows `403` with `insufficient scopes` | Add required permission: `permissions: pull-requests: write` in workflow; verify minimum required scopes |
| Branch protection rule change (new required check added) | All open PRs immediately blocked on new check name; merges fail until check passes | Immediate (applies retroactively to open PRs) | GitHub UI shows new required check pending on all open PRs; correlate with admin settings change | Remove new check from required list temporarily while ensuring all open PRs can run it |
| Self-hosted runner OS/agent upgrade | Runner jobs fail with `##[error]Version X.Y.Z of the runner is too old`; or new runner binary has breaking config | After runner update applied | Runner log: `Listening for Jobs`; compare runner version before/after: `./bin/Runner.Listener --version` | Roll back runner: stop service, download previous runner binary, re-register; pin runner version in autoscaler config |
| Secret rotation (name unchanged, value changed) | Dependent service authentication fails silently; jobs complete but operations fail downstream | Immediately on first job run after secret rotation | Correlate secret rotation timestamp with first job failure timestamp; test secret validity: `aws sts get-caller-identity` | Re-rotate to known-good value: `gh secret set AWS_ACCESS_KEY_ID -b "$VALID_KEY"`; verify with manual test job |
| `workflow_call` interface change (input/output rename) | Caller workflows fail with `Unexpected input(s) X`; called workflow outputs not found by callers | Immediately on first call after interface change | Diff `workflow_dispatch: inputs:` in reusable workflow; correlate with caller error messages | Maintain backward-compatible interface; add new input alongside old; deprecate with warning comment |
| Docker base image update in workflow Dockerfile | Build succeeds but tests fail; runtime behavior changes; security tool scan triggers new violations | Immediately on next build after image update | Compare `FROM` tag in Dockerfile; check base image changelog; correlate with first failing test names | Pin `FROM` to specific digest: `FROM ubuntu@sha256:$DIGEST`; update base image changes in a dedicated PR with full test run |
| Environment protection rule change (required reviewer added) | Deploy workflows block awaiting approval that previously deployed automatically; CD pipeline stalls | Immediately on next deploy workflow trigger | GitHub UI shows `Waiting for review`; correlate with environment settings change timestamp | Remove reviewer requirement temporarily to unblock; or approve pending deployments via UI |
| `GITHUB_TOKEN` permissions scope reduction (org-level policy) | Jobs that previously wrote to packages, created releases, or commented on PRs now get 403 | Immediately after org policy change | `gh api /repos/$OWNER/$REPO --jq '.permissions'`; job logs show `Resource not accessible by integration` | Use dedicated GitHub App token instead of GITHUB_TOKEN: `actions/create-github-app-token` |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Artifact version mismatch between upload and download jobs | `gh api /repos/$OWNER/$REPO/actions/artifacts --jq '.artifacts[] \| select(.name=="build-output") \| {id, created_at}'` | Downstream job downloads artifact from different run; uses wrong binary/package version | Silent correctness bug; wrong code deployed to production | Always use `run_id` scoped artifact names: `artifact-name-${{ github.run_id }}`; never use generic names |
| Cache poisoning (stale dependency cache) | `gh api /repos/$OWNER/$REPO/actions/caches --jq '.actions_caches[] \| select(.key \| startswith("deps-"))'` | Tests pass locally but fail in CI; new vulnerability in cached dependency not detected | Security issue; inconsistent behavior between CI and local | Manually invalidate cache: `gh api -X DELETE /repos/$OWNER/$REPO/actions/caches?key=deps-` ; change cache key to force refresh |
| Concurrent deploy workflows writing to same environment | Two PRs merged simultaneously; both trigger deploy; last deploy overwrites first; one deploy's changes lost | `gh run list --workflow=deploy.yml --status in_progress` shows 2+ simultaneous runs | Silent config drift in environment; inconsistent state | Use `concurrency: group: deploy-${{ github.ref }} cancel-in-progress: false` to serialize deploys; never allow concurrent deploys to same environment |
| Environment variable drift between environments (staging vs prod) | Config differences cause bugs that only appear in prod; tests pass in staging | `gh api /repos/$OWNER/$REPO/environments/staging/variables` vs `/environments/production/variables` shows divergence | Prod-only bugs; security configuration mismatches | Use infrastructure-as-code for env vars (Terraform); audit `gh api /repos/$OWNER/$REPO/environments` diff regularly |
| Workflow output inconsistency (job output vs artifact) | `${{ needs.build.outputs.image_tag }}` shows different value than what's in the uploaded artifact | Job output set during cancellation/partial run; consumers use wrong value | Wrong image tag deployed; version mismatch | Always derive image tag from git SHA: `IMAGE_TAG=${{ github.sha }}`; never pass mutable values as job outputs |
| Branch protection bypass via status check deception | `gh api /repos/$OWNER/$REPO/commits/$SHA/check-runs` shows check passing from unexpected app | Forged status check bypasses merge protection; malicious or untested code merged | Security breach; untested code in main branch | Audit check run creators; enforce `required_status_checks.strict: true`; use GitHub App with verified identity for status reporting |
| Reusable workflow SHA pinning drift | Called workflow updated but SHA in caller not bumped; behavior diverges from caller's expectation | `uses: $OWNER/.github/.github/workflows/deploy.yml@main` — `main` is a moving target | Unexpected behavior changes in reusable workflow consumers | Pin reusable workflows to SHA: `uses: $OWNER/.github/.github/workflows/deploy.yml@$SHA`; use Dependabot for controlled updates |
| Secret scope mismatch (repo secret vs env secret) | Job reads wrong secret value; env-specific secret not applied in environment context | `gh secret list --repo $OWNER/$REPO` and `gh api /repos/$OWNER/$REPO/environments/prod/secrets` differ | Wrong credentials used; auth fails in production but not staging | Audit secret hierarchy; ensure environment secrets override repo secrets for env-specific deployments |
| Runner registration token reuse | Two runner instances registered with same name; GitHub routes jobs to either indeterminately | `gh api /repos/$OWNER/$REPO/actions/runners --jq '.runners[] \| select(.name=="$RUNNER_NAME")'` shows duplicates | Non-deterministic job execution; different tool versions on different runner instances | Remove duplicate runner registrations; re-register with unique names; add instance ID to runner name |
| Workflow file syntax valid but semantics changed by YAML merge | `on: push: branches: [main]` accidentally merged with `on: push:` removing branch filter; triggers on all branches | `gh api /repos/$OWNER/$REPO/actions/workflows --jq '.workflows[] \| {name, path}'` then review file | Workflows trigger on unintended branches; test/deploy pipelines run on feature branches | Restore correct `on:` triggers; use `workflow_dispatch` with explicit inputs for sensitive operations |

## Runbook Decision Trees

### Decision Tree 1: All Workflow Runs Queued / Not Starting

```
Are self-hosted runners online?
`gh api /repos/$OWNER/$REPO/actions/runners --jq '.runners[] | select(.status=="online") | .name'`
├── YES (runners online) → Are runners showing as busy?
│   `gh api /repos/$OWNER/$REPO/actions/runners --jq '.runners[] | {name, busy}'`
│   ├── ALL busy → Scale out runners (all capacity consumed):
│   │   → Launch additional runner instances via Terraform/Ansible
│   │   → Or switch queued jobs to GitHub-hosted runners temporarily
│   └── NOT busy → Runners online but not picking up jobs?
│       → Check runner label mismatch: compare job `runs-on` with runner labels
│       `gh api /repos/$OWNER/$REPO/actions/runners --jq '.runners[] | {name, labels: [.labels[].name]}'`
│       → Fix label mismatch in workflow YAML or add missing label to runner
└── NO (no online runners) → Are runner hosts reachable?
    `ping $RUNNER_HOST` / `ssh $RUNNER_HOST`
    ├── YES (host up, runner offline) → Runner service crashed:
    │   `ssh $RUNNER_HOST 'sudo systemctl status actions.runner.*'`
    │   → Restart: `sudo systemctl restart actions.runner.*`
    │   → If repeatedly crashing: `journalctl -u actions.runner.* -n 100`
    └── NO (host unreachable) → Infrastructure failure:
        → Provision replacement runner hosts
        → Register new runners: `./config.sh --url https://github.com/$OWNER/$REPO --token $TOKEN --unattended`
        → Remove offline registrations from GitHub
        → Escalate to infra team if host provisioning fails
```

### Decision Tree 2: Workflow Failing with Secret / Permission Error

```
Is the error message "Context access might be invalid" or "secret not found"?
`gh run view $RUN_ID --log | grep -E "secret|permission|access denied|OIDC"`
├── YES (secret error) → Does the secret exist in the repo/org?
│   `gh secret list --repo $OWNER/$REPO`
│   ├── Secret missing → Re-create secret from vault:
│   │   `gh secret set $SECRET_NAME -b "$VALUE" --repo $OWNER/$REPO`
│   └── Secret exists → Is it scoped to the correct environment?
│       `gh api /repos/$OWNER/$REPO/environments --jq '.environments[].name'`
│       → Verify workflow `environment:` key matches configured environment name
│       → Check environment protection rules: `gh api /repos/$OWNER/$REPO/environments/$ENV_NAME`
└── NO → Is it an OIDC token / cloud auth failure?
    `gh run view $RUN_ID --log | grep -E "oidc\|jwt\|assume role\|403"`
    ├── OIDC error → Check OIDC trust policy in cloud provider:
    │   AWS: `aws iam get-role --role-name $ROLE_NAME --query 'Role.AssumeRolePolicyDocument'`
    │   → Verify `token.actions.githubusercontent.com` audience and subject claim
    │   → Ensure workflow `permissions: id-token: write` is set
    └── Other permission error → Check repository Actions permissions:
        `gh api /repos/$OWNER/$REPO/actions/permissions`
        → Verify `GITHUB_TOKEN` permissions in workflow match required scopes
        → Escalate to GitHub org admin if org-level policy blocks permissions
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Minutes quota exhaustion | Long-running jobs or matrix explosion on GitHub-hosted runners | `gh api /orgs/$ORG/settings/billing/actions --jq '{included_minutes, minutes_used_breakdown}'` | All GitHub-hosted runner jobs blocked until month reset or upgrade | Cancel in-progress runs: `gh run cancel $RUN_ID`; disable expensive workflows temporarily | Set `timeout-minutes` on all jobs; use self-hosted runners for heavy workloads |
| Storage quota exhaustion | Artifacts not expiring; large build artifacts uploaded | `gh api /repos/$OWNER/$REPO/actions/artifacts --jq '.total_count, .artifacts[].size_in_bytes' \| head -20` | New artifact uploads fail; old artifacts inaccessible | Delete large/old artifacts: `gh api -X DELETE /repos/$OWNER/$REPO/actions/artifacts/$ID` | Set `retention-days` on all `upload-artifact` steps; default is 90 days |
| Matrix strategy explosion | Unbound matrix variables generating hundreds of jobs | `gh run view $RUN_ID --json jobs --jq '.jobs \| length'` | Hundreds of concurrent jobs consuming all runner capacity and minutes quota | Cancel run: `gh run cancel $RUN_ID`; add `max-parallel` to matrix strategy | Always set `max-parallel` in matrix; validate matrix size in PR checks |
| Cron job accumulation | Multiple scheduled workflows added without audit | `gh api /repos/$OWNER/$REPO/actions/workflows --jq '.workflows[] \| select(.path \| test("schedule")) \| {name, path}'` | Minutes quota drain on recurring basis | Disable unused scheduled workflows: `gh workflow disable $WORKFLOW_ID` | Audit scheduled workflows quarterly; require justification for cron additions |
| Self-hosted runner runaway cost | Cloud-provisioned runners not auto-terminating after job | Cloud provider billing dashboard (EC2/GKE node costs) | Idle runner instances accruing compute costs 24/7 | Terminate idle runner instances; remove registration from GitHub | Implement ephemeral runners (JIT runners); use `--ephemeral` flag with auto-scaling |
| Artifact upload size abuse | Docker images or build caches uploaded as artifacts | `gh api /repos/$OWNER/$REPO/actions/artifacts --jq '.artifacts[] \| select(.size_in_bytes > 500000000) \| {name, size_in_bytes}'` | Storage quota consumed rapidly | Delete oversized artifacts; redirect to GitHub Packages or registry | Enforce artifact size limits via policy; use container registry for images |
| Repeated failed reruns | Automated retry logic rerequeueing jobs indefinitely | `gh run list --status failure --limit 100 --json databaseId --jq 'length'` | Minutes exhausted; runner capacity consumed by failing workloads | Disable auto-rerun automation; cancel queued reruns | Add circuit breaker to rerun automation (max 3 retries); alert on repeated failures |
| Third-party action billing surprises | Paid GitHub Marketplace action charges accruing | GitHub billing dashboard → Actions usage | Unexpected billing charges | Remove or replace paid action; use open-source alternative | Audit Marketplace action licenses before adopting; prefer verified free actions |
| Cache storage overrun | Large caches accumulating across branches | `gh api /repos/$OWNER/$REPO/actions/caches --jq '.total_count, (.actions_caches \| map(.size_in_bytes) \| add)'` | Cache storage limit hit; new cache saves silently fail | `gh cache delete --all --repo $OWNER/$REPO` for stale caches | Use specific cache keys with branch scope; set cache `restore-keys` appropriately |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot workflow triggering bottleneck | Single workflow triggered hundreds of times per minute; all other jobs queued | `gh run list --limit 100 --json workflowName,status --jq 'group_by(.workflowName) \| map({name: .[0].workflowName, count: length}) \| sort_by(-.count)'` | Overly broad trigger (`on: push` to main without path filters); CI event fan-out | Add `paths:` and `branches:` filters to workflow trigger; use `workflow_dispatch` for manual-only flows |
| Connection pool exhaustion on self-hosted runner | Runner jobs timeout waiting for GitHub API responses; `actions/github-script` steps hang | `ss -s` on runner host (check ESTABLISHED count to api.github.com); runner `_diag/*.log` shows HTTP 429 | Runner host making too many concurrent GitHub API calls; no retry backoff | Reduce concurrency: set `max-parallel` in matrix; add `GITHUB_API_DELAY` env var; implement exponential backoff in scripts |
| Runner GC/memory pressure | Java/Node build steps OOM; runner process killed mid-job | `free -h` on runner host; `dmesg \| grep -i oom` on runner host; check job logs for "Killed" | Build tool (Maven/Gradle/Webpack) consuming all available RAM | Increase runner VM memory; set JVM heap: `MAVEN_OPTS=-Xmx4g`; add `actions/setup-java` `overwrite-settings: true` with memory settings |
| Thread pool saturation in parallel matrix jobs | Many matrix jobs queued despite available runners; scheduling latency high | `gh run view $RUN_ID --json jobs --jq '.jobs[] \| select(.status=="queued") \| {name, startedAt}'` | GitHub-hosted runner pool depleted; too many concurrent jobs without `max-parallel` | Add `strategy: max-parallel: 5` to matrix; use larger self-hosted runner fleet with autoscaling |
| Slow `actions/checkout` on large monorepo | Checkout step takes > 5 minutes; shallow clone not configured | Job step timing in GitHub Actions UI; `gh run view $RUN_ID --json steps` | Full git history checked out; large LFS objects pulled unnecessarily | Add `fetch-depth: 1` to `actions/checkout`; use `lfs: false` if LFS not needed in job |
| CPU throttle on small GitHub-hosted runner | Build step CPU-bound; 2-core GitHub runner pegged at 100% | Job step timing shows build steps 2–3× slower than expected; GitHub runner specs: ubuntu-latest = 2 vCPU | Compute-intensive build on underpowered runner | Switch to `runs-on: ubuntu-latest-4-cores` or self-hosted runner with more cores; split build into parallel jobs |
| Lock contention in concurrent release jobs | Two release workflows triggered simultaneously; both try to publish same version | `gh run list --workflow=release.yml --json status,createdAt \| jq 'group_by(.status) \| .[] \| select(.[0].status=="in_progress")'` | No concurrency group on release workflow; race condition on version tag | Add `concurrency: group: release-${{ github.ref }} cancel-in-progress: false` to release workflow |
| Serialization overhead in large artifact handling | `actions/upload-artifact` and `actions/download-artifact` steps slow (> 5 min) | Job step timing in GitHub UI; artifact size: `gh api /repos/$OWNER/$REPO/actions/artifacts --jq '.artifacts[0].size_in_bytes'` | Artifacts too large; no compression; transferring entire build output | Use `compression-level: 9` in upload-artifact; filter to only necessary files; use GitHub Packages for Docker images instead |
| Batch size misconfiguration in test splitting | Some runners finish tests in 2 min, others take 20 min; uneven distribution | `gh run view $RUN_ID --json jobs --jq '.jobs[] \| {name, duration: (.completedAt - .startedAt)}'` | Test suite not split evenly across matrix shards; no timing-based splitting | Use `pytest-split` or `jest --shard` with timing data; balance by test file history not file count |
| Downstream dependency latency (slow npm/pip registry) | `npm install` or `pip install` steps take 10+ minutes | Job step timing showing package install dominates; check step logs for slow registry responses | No dependency caching configured; downloading from slow package registry | Add `actions/cache` for `node_modules` or pip cache: `cache: 'npm'` in `actions/setup-node`; pin to fast registry mirror |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on self-hosted runner's GitHub Enterprise connection | Runner jobs fail with `x509: certificate has expired`; runner shows offline in GitHub | `echo \| openssl s_client -connect $GHES_HOST:443 2>&1 \| openssl x509 -noout -dates` | GitHub Enterprise Server TLS cert expired; runner cannot verify HTTPS | Renew GHES TLS cert; restart GHES service; re-register runners: `./config.sh --url $GHES_URL --token $TOKEN` |
| mTLS rotation failure for self-hosted runner credentials | Runner disconnects with `401 Unauthorized`; runner token expired | `gh api /repos/$OWNER/$REPO/actions/runners --jq '.runners[] \| select(.status=="offline") \| {name, id}'` | Runner registration token expired (max 1 hour) or PAT revoked | Re-register runner: `./config.sh remove --token $REMOVE_TOKEN && ./config.sh --url $REPO_URL --token $NEW_TOKEN` |
| DNS resolution failure in self-hosted runner network | Job steps fail with `Could not resolve host: api.github.com` | Runner `_diag/*.log` shows DNS errors; `nslookup api.github.com` from runner host fails | Corporate DNS blocking or misconfigured DNS forwarder on runner host | Fix DNS: `echo "nameserver 8.8.8.8" >> /etc/resolv.conf`; verify proxy: set `https_proxy` env var in runner systemd unit |
| TCP connection exhaustion from runner to GitHub API | Workflow scripts fail with `connect: connection refused` to GitHub API endpoints | `ss -s` on runner host (high TIME_WAIT count); `netstat -an \| grep api.github.com \| wc -l` | Rapid sequential API calls exhausting ephemeral ports; no connection reuse | Set `net.ipv4.tcp_tw_reuse=1` on runner host; reduce API call rate in workflow scripts; use `gh` CLI which pools connections |
| Load balancer misconfiguration blocking runner webhook | Self-hosted runner not receiving job assignments; jobs queue indefinitely | Runner `_diag/*.log` shows `long polling` but no jobs assigned; `gh api /repos/$OWNER/$REPO/actions/runners` shows runner `idle` | Corporate load balancer or proxy dropping long-poll HTTP connections (connection timeout) | Increase load balancer idle connection timeout to > 90 seconds; use HTTPS polling not WebSocket if LB blocks WS |
| Packet loss between runner and artifact storage | `actions/upload-artifact` fails with checksum mismatch or timeout | Job logs show upload retry attempts; `ping -c 100 pipelines.actions.githubusercontent.com` from runner host | Network packet loss on runner host uplink; MTU mismatch | Check runner NIC and switch for errors: `ethtool -S $IFACE \| grep error`; set MTU: `ip link set $IFACE mtu 1500` |
| MTU mismatch on runner Docker network | Jobs using Docker container actions fail with TCP stalls for large payloads | `docker network inspect bridge \| jq '.[0].Options'`; `ping -s 8972 -M do $GATEWAY` from inside container | Docker bridge MTU differs from host MTU; large packets fragmented | Set Docker MTU: `echo '{"mtu": 1450}' > /etc/docker/daemon.json && systemctl restart docker` |
| Firewall rule change blocking GitHub webhook | `push` events no longer trigger workflows; GitHub shows delivery failure in repo webhook settings | `gh api /repos/$OWNER/$REPO/hooks/$HOOK_ID/deliveries \| jq '.[0] \| {status_code, response_body}'` | Firewall blocking inbound connections from GitHub webhook IP ranges | Update firewall to allow GitHub's webhook IPs: `curl https://api.github.com/meta \| jq '.hooks'` |
| SSL handshake timeout to third-party action host | Workflow step using private action repo fails with TLS timeout | Job log shows `fatal: unable to access '$ACTION_URL': SSL_connect timeout`; step using `uses: org/action@v1` | Action hosted on slow or overloaded private registry; TLS negotiation too slow | Pin action to SHA: `uses: org/action@$SHA`; mirror to public registry; set `GIT_SSL_NO_VERIFY=false` and check cert chain |
| Connection reset during Docker layer push | Docker push to GHCR resets mid-upload; large layer fails | Job log shows `unexpected EOF` or `connection reset by peer` during `docker push`; affected layer size > 500MB | GitHub Packages (GHCR) connection timeout on large layer upload | Split Docker build into smaller layers; use multi-stage builds; retry push with `--retry` flag; reduce layer size with `.dockerignore` |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill on self-hosted runner | Build step killed with exit code 137; `dmesg \| grep oom` on runner host | `dmesg \| grep -i "oom.*runner\|java.*killed\|node.*killed"` on runner host | Restart failed job; increase runner VM memory or reduce parallelism; set `MAVEN_OPTS=-Xmx3g` | Size runner VMs for peak job memory; monitor with Prometheus node_exporter; set job-level memory resource requests |
| Disk full on runner data partition | Job fails with "no space left on device" during build or artifact upload | `df -h` on runner host; `du -sh /home/runner/work/*` | `find /home/runner/work -mtime +1 -exec rm -rf {} +`; restart runner service | Set runner work dir cleanup policy; add pre-job cleanup step: `- run: df -h && docker system prune -f` |
| Disk full on runner log partition | Runner service stops writing diagnostic logs; hard to diagnose failures | `df -h /home/runner/actions-runner/_diag` on runner host | `find /home/runner/actions-runner/_diag -mtime +7 -delete` | Configure log rotation for runner `_diag` directory via logrotate |
| File descriptor exhaustion on runner | Build tools fail to open files; `git` operations fail with "too many open files" | `cat /proc/$(pgrep Runner.Listener)/limits \| grep "open files"`; `ls /proc/$(pgrep Runner.Listener)/fd \| wc -l` | `systemctl restart actions.runner.*`; increase limit: `ulimit -n 65536` in runner service unit | Set `LimitNOFILE=65536` in runner systemd unit file (`/etc/systemd/system/actions.runner.*.service`) |
| Inode exhaustion from build temp files | Build fails with "no space left on device" despite disk having free space | `df -i /home/runner/work` | `find /home/runner/work -name "*.tmp" -o -name "*.class" \| xargs rm -f` | Add build cleanup steps; use `.gitignore`-style cleanup in Makefile/build scripts; monitor inodes via node_exporter |
| CPU throttle on GitHub-hosted runner | Compute-intensive steps (compilation, test) run slowly; 2-core limit hit | Job step timing in GitHub UI (steps taking 3–5× longer than local); check GitHub runner specs | Switch `runs-on` to self-hosted larger runner or `ubuntu-latest-8-cores` (if enabled) | Use larger GitHub-hosted runners via billing plan; split build into parallel jobs to use multiple 2-core runners |
| Swap exhaustion on self-hosted runner | Runner VM thrashing swap; jobs extremely slow | `free -h` on runner host; `vmstat 1 5` (check `si/so`) | Restart lowest-priority running job; add swap: `fallocate -l 8G /swapfile && mkswap /swapfile && swapon /swapfile` | Size runner RAM to avoid swap; use ephemeral runner VMs with sufficient memory for peak job requirements |
| GitHub Actions minutes quota exhaustion | All GitHub-hosted runner jobs stuck in queue; `gh run watch $RUN_ID` shows `queued` indefinitely | `gh api /orgs/$ORG/settings/billing/actions --jq '{minutes_used: .total_minutes_used, included: .included_minutes}'` | Cancel non-critical runs: `gh run list --status queued --json databaseId --jq '.[].databaseId' \| xargs -I{} gh run cancel {}` | Set `timeout-minutes` on all jobs; migrate heavy workloads to self-hosted runners |
| Runner registration token pool exhaustion | New runner registration fails; runner autoscaler cannot provision runners | `gh api /repos/$OWNER/$REPO/actions/runners --jq '.total_count'` vs `runner_group` limit | Check org runner group limit in GitHub Settings → Actions → Runner groups; increase limit or request GitHub support | Set appropriate runner group limits; use JIT (just-in-time) runner tokens to reduce registration overhead |
| Ephemeral port exhaustion from parallel `gh` CLI calls | Workflow steps calling `gh` in parallel fail with connection errors | `ss -s` on runner host (check TIME_WAIT count) | `sysctl -w net.ipv4.ip_local_port_range="1024 65535" net.ipv4.tcp_tw_reuse=1` | Use `GITHUB_TOKEN` with `gh api` batching; avoid tight loops of sequential API calls in scripts |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation causing duplicate deploys | Two runs of `workflow_dispatch` with same inputs both succeed; app deployed twice | `gh run list --workflow=deploy.yml --json status,conclusion,createdAt \| jq '[.[] \| select(.conclusion=="success")] \| length'` | Duplicate deployment causes service restart or version conflict | Add `concurrency: group: deploy-${{ github.ref }} cancel-in-progress: false` to deployment workflow; track deploys in external state store |
| Saga/workflow partial failure in multi-job workflow | Deploy job succeeds but post-deploy smoke test job skipped due to earlier job failure; system partially deployed | `gh run view $RUN_ID --json jobs --jq '.jobs[] \| {name, status, conclusion}'` | App deployed without validation; broken state in production | Use `needs:` correctly and `if: always()` for cleanup jobs; implement rollback job triggered on downstream failure |
| Message replay causing duplicate release tags | Release workflow re-triggered on `workflow_run` completion; same tag pushed twice | `gh api /repos/$OWNER/$REPO/releases --jq '.[0:5] \| .[].tag_name'`; `gh api /repos/$OWNER/$REPO/git/refs/tags` | Duplicate release artifacts; tag conflict error; release notes overwritten | Check if tag exists before creating: add `- run: git tag $TAG 2>/dev/null || exit 0` gate step; store release state in step output |
| Cross-workflow deadlock via environment protection | Workflow A waiting for environment approval; Workflow B also waiting; approver needed for both but can only approve one at a time | `gh run list --status waiting --json workflowName,url --jq '.[] \| {workflowName, url}'` | Both deployments blocked; manual intervention required | Approve sequentially via GitHub UI; redesign to use single deployment pipeline instead of parallel environment-protected workflows |
| Out-of-order event processing from concurrent pushes | Two pushes to main in quick succession; second CI run finishes before first; older build deployed last | `gh run list --workflow=ci.yml --branch=main --json headSha,status,createdAt \| jq 'sort_by(.createdAt)'` | Older code deployed over newer code; regression | Use `concurrency: group: deploy-main cancel-in-progress: true` to cancel superseded runs; deploy only from latest SHA |
| At-least-once delivery duplicate from re-run on partial failure | Job re-run after transient failure re-executes already-successful steps (e.g., publishing artifact twice) | `gh run view $RUN_ID --json jobs --jq '.jobs[] \| {name, runAttempt, conclusion}'` (multiple attempts) | Artifact or package published multiple times; registry shows duplicate versions | Add idempotency check in publish step: query registry before publishing; use `--skip-existing` flag where available (e.g., `twine upload --skip-existing`) |
| Compensating transaction failure in rollback workflow | Rollback workflow triggered after failed deploy but rollback itself fails; system left in unknown state | `gh run list --workflow=rollback.yml --json status,conclusion \| jq '.[0]'` | Production in broken state; neither new nor old version fully running | Trigger manual rollback via `gh workflow run rollback.yml --field target_sha=$STABLE_SHA`; escalate to on-call; consider direct k8s/platform rollback bypassing Actions |
| Distributed lock expiry during long deployment | Deployment acquires external lock (e.g., Terraform state lock, k8s deployment lock); GitHub Actions job timeout kills process; lock not released | `gh run view $RUN_ID --json conclusion --jq '.conclusion'` shows `cancelled` or `timed_out`; check Terraform state lock: `terraform force-unlock $LOCK_ID` | Infrastructure lock held; subsequent deployments fail with "state locked" | Force-unlock: `terraform force-unlock -force $LOCK_ID`; set `timeout-minutes` on job shorter than lock TTL; add lock release in `post:` step |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: large build monopolizing self-hosted runner | Runner CPU at 100%; other teams' jobs queued; `top` on runner host shows single build job dominating | Other team jobs wait 30+ minutes in queue | Cancel monopolizing job: `gh run cancel $RUN_ID`; use `timeout-minutes` to auto-kill | Assign separate runner groups per team; use `runs-on: [self-hosted, team-a]` label-based routing |
| Memory pressure from parallel matrix jobs | Self-hosted runner OOM kills; `dmesg \| grep oom` shows build jobs killed | Some matrix jobs fail; partial test results; flaky CI for all users on runner | `gh run cancel $RUN_ID` to free memory | Set `max-parallel: 4` on matrix jobs; size runner VMs for peak concurrent job memory usage |
| Disk I/O saturation from large artifact uploads | Runner disk I/O at 100%; other jobs' checkout and build steps slow | Slow git operations; compilation timeouts for unrelated jobs | Pause artifact upload job: `gh run cancel $UPLOAD_RUN_ID` | Use separate runner with fast NVMe for artifact-heavy jobs; add `runs-on: artifact-runner` label |
| Network bandwidth monopoly from Docker layer pushes | Runner network saturated; `iftop` on runner host shows GHCR push consuming all bandwidth | Other jobs fail to pull base images; `docker pull` timeouts | Cancel large push job: `gh run cancel $RUN_ID` | Set Docker build/push jobs on dedicated runner with high-bandwidth NIC; schedule large pushes off-peak |
| Connection pool starvation: GitHub API rate limit | Workflow scripts hitting GitHub API rate limit; `gh api /rate_limit` shows `remaining: 0` | Other teams' workflows that use `gh api` fail with 403 | Identify high-API-call workflow: `gh run list --workflow=$CULPRIT.yml` | Use `GITHUB_TOKEN` (per-repo rate limit) instead of PAT (per-user limit); cache API responses; add `gh api --cache 60` |
| Quota enforcement gap: org Actions minutes exhaustion | All GitHub-hosted jobs queue indefinitely; `gh api /orgs/$ORG/settings/billing/actions` shows minutes depleted | All teams' CI blocked; releases delayed | Cancel all non-critical queued runs: `gh run list --status queued --json databaseId --jq '.[].databaseId' \| xargs -I{} gh run cancel {}` | Set `timeout-minutes` on all jobs; assign per-team Actions spending limits in Org billing settings |
| Cross-tenant secret access via shared runner | Team B's self-hosted runner picks up Team A's job due to missing label selector | Team A's secrets exposed to Team B's runner environment | Restrict runner to correct repo: `gh api -X PATCH /orgs/$ORG/actions/runners/$ID -f labels='["team-a"]'` | Use runner groups scoped to specific repos; never share self-hosted runners across trust boundaries |
| Rate limit bypass via multiple GitHub Apps | Team creates multiple GitHub Apps to multiply API rate limits; floods shared runner queue | Shared runner queue saturated; other teams' jobs delayed | Identify abusing app: `gh api /orgs/$ORG/audit-log?phrase=workflow_run --jq '.[0:20]'` | Enforce per-team runner group limits; require approval for new GitHub App installations in org |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure | GitHub Actions usage dashboard blank; no data for job success rates | GitHub Actions does not expose Prometheus metrics natively; custom exporter down | `gh api /repos/$OWNER/$REPO/actions/runs --jq '.workflow_runs \| map(select(.conclusion=="failure")) \| length'` | Deploy `github-actions-exporter`; alert on exporter `up == 0`; use GitHub API polling as backup |
| Trace sampling gap: intermittent flaky tests missed | Flaky test only fails 1% of runs; no alert fires; accumulates tech debt | Test summary only reports final pass/fail; flaky failures averaged out over many runs | `gh run list --status failure --workflow=test.yml --json databaseId --jq '.[].databaseId' \| head -10 \| xargs -I{} gh run view {}` | Add test flakiness tracking: parse JUnit XML from artifacts; alert when same test fails > 2% of runs |
| Log pipeline silent drop: expired run logs | Post-incident investigation finds run logs deleted | GitHub Actions log retention default 90 days; custom retention can be as low as 1 day | `gh api /repos/$OWNER/$REPO/actions/runs/$RUN_ID \| jq '.logs_url'` (check if URL returns 404) | Set org-level log retention to maximum (400 days for Enterprise); export critical run logs to external storage immediately after run |
| Alert rule misconfiguration: no alert on deploy failure | Production deployment fails silently; team notified hours later by user reports | Workflow `conclusion: failure` not sending notification; `on-failure` Slack action misconfigured | `gh run list --workflow=deploy.yml --json conclusion --jq '.[] \| select(.conclusion=="failure") \| .conclusion'` | Add explicit failure notification step: `if: failure()` with Slack/PagerDuty alert; test by deliberately failing a deploy |
| Cardinality explosion: too many workflow files | GitHub Actions usage dashboard slow; API calls for run listing time out | Org has 500+ workflow files; listing all runs across all workflows hits GitHub API rate limits | Filter to critical workflows: `gh run list --workflow=deploy.yml --limit 20` instead of listing all | Create aggregated status badges per team; use GitHub Actions usage report CSV export for billing/audit |
| Missing health endpoint: self-hosted runner liveness | Runner shows `online` in GitHub but is not picking up jobs | GitHub runner heartbeat endpoint responds but runner listener process hung | `pgrep Runner.Listener` on runner host; `gh api /repos/$OWNER/$REPO/actions/runners --jq '.runners[] \| select(.status=="online") \| {name, busy}'` | Add external runner health check: cron job that triggers test workflow and alerts if it doesn't complete |
| Instrumentation gap: no visibility into queued job wait time | Jobs taking 20+ minutes before starting but no alert; runners available according to GitHub | GitHub does not expose queue wait time as a metric natively | `gh run view $RUN_ID --json jobs --jq '.jobs[] \| {name, startedAt, createdAt}'` (diff times) | Implement queue time monitoring: GitHub App that tracks `queued → in_progress` transition time; alert if > 10 minutes |
| Alertmanager/PagerDuty outage: no pages for broken main | Main branch CI red for hours; no one paged | GitHub Actions notification sent to Slack webhook that was rotated; webhook returning 410 | `gh api /repos/$OWNER/$REPO/actions/runs --jq '.workflow_runs[0] \| {conclusion, name, created_at}'` manually | Test all notification webhooks monthly; add redundant PagerDuty alert via GitHub branch protection required status checks |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| actions/checkout minor version upgrade breaking clone | After bumping `actions/checkout@v3` to `@v4`, git operations in subsequent steps fail due to changed working directory behavior | `gh run view $RUN_ID --log \| grep -A5 "actions/checkout"` | Pin to previous version SHA: `uses: actions/checkout@$V3_SHA` | Always pin actions to SHA; test version bumps in a non-critical branch workflow first |
| Runner OS image upgrade breaking build tools | GitHub-hosted runner `ubuntu-latest` pointed to new Ubuntu version; installed tool versions changed | `gh run view $RUN_ID --log \| grep -E "not found\|command not found\|version"` | Pin to specific Ubuntu version: `runs-on: ubuntu-22.04` instead of `ubuntu-latest` | Pin `runs-on` to specific OS version for production workflows; test `ubuntu-latest` in separate test workflow |
| Schema migration partial completion in deploy workflow | Database migration step succeeded but application deploy step failed; DB and app at different schema versions | `gh run view $RUN_ID --json jobs --jq '.jobs[] \| {name, conclusion}'` | Trigger rollback workflow: `gh workflow run rollback.yml --field target_sha=$PREV_SHA` | Wrap migration + deploy in single atomic job; add schema version check as first step of deploy |
| Rolling upgrade version skew from parallel workflow runs | Two deploy workflows running simultaneously on different commits; version skew in production | `gh run list --workflow=deploy.yml --status in_progress --json databaseId,headSha --jq '.'` | Cancel older run: `gh run cancel $OLDER_RUN_ID` | Add `concurrency: group: deploy cancel-in-progress: true` to deploy workflow |
| Zero-downtime migration gone wrong via reusable workflow change | Reusable workflow (`workflow_call`) interface changed; callers broken but reusable workflow already merged | `gh run list --status failure --json workflowName --jq '.[] \| .workflowName' \| sort \| uniq -c` | Revert reusable workflow change: `git revert $COMMIT && git push`; or pin callers to old ref | Version reusable workflows; use `workflow_call` inputs with backward-compatible defaults |
| Config format change in composite action | `action.yml` composite action updated with new required input; existing callers break | `gh run view $RUN_ID --log \| grep -E "required input\|missing\|undefined"` | Revert `action.yml` or add `default:` for new input to preserve backward compatibility | Treat `action.yml` inputs as public API; use semantic versioning for composite actions; test with all callers before merge |
| Data format incompatibility: artifact schema change | New workflow version uploads artifacts in different format; downstream workflow that consumes them fails | `gh run view $DOWNSTREAM_RUN_ID --log \| grep -E "parse\|decode\|format\|jq"` | Revert artifact format change; or update consumer workflow simultaneously | Version artifact filenames (e.g., `results-v2.json`); change consumer and producer in same PR |
| Dependency version conflict in self-hosted runner Docker image | Self-hosted runner Docker image updated; newer base image has incompatible tool version (e.g., Node 22 vs 18) | Runner `_diag/*.log` shows version mismatch; `gh run view $RUN_ID --log \| grep "version"` | Pin runner Docker image to previous tag in runner registration | Tag runner images with semantic version; use image digest pinning in runner compose file; test runner image updates in staging fleet |

## Kernel/OS & Host-Level Failure Patterns

| Failure Mode | Impact on GitHub Actions | Detection Command | Remediation |
|-------------|------------------------|-------------------|-------------|
| OOM killer terminates runner process | Self-hosted runner job abruptly killed; workflow shows `The runner has received a shutdown signal` with no logs | `dmesg -T \| grep -i "oom-kill" \| grep -E "Runner\|actions"` on runner host; `gh run view $RUN_ID --json conclusion --jq '.conclusion'` shows `failure` with no step failure | Increase runner host memory; add `--memory` limit to Docker-based runners; set `ACTIONS_RUNNER_MEMORY_LIMIT` env var; move memory-intensive jobs to larger runner labels |
| Inode exhaustion on runner workspace | Workflow fails with `No space left on device` despite disk showing free space; `actions/checkout` fails | `df -i /home/runner/work` on self-hosted runner; `gh run view $RUN_ID --log \| grep "No space left"` | Add pre-job cleanup step: `find /home/runner/work -maxdepth 3 -type d -mtime +7 -exec rm -rf {} +`; configure `clean: true` on `actions/checkout`; schedule cron to prune old workspaces |
| CPU steal on shared runner host | Workflow steps take 3-5x longer than baseline; no explicit errors but duration anomalies | `sar -u 1 5 \| grep -E "steal"` on self-hosted runner host; compare `gh run view $RUN_ID --json jobs --jq '.jobs[].steps[] \| {name, duration: (.completedAt \| sub(.startedAt))}' ` timing to baseline | Migrate self-hosted runners to dedicated hosts or instances with guaranteed CPU; use `runs-on:` labels to route critical jobs to dedicated runner pools |
| NTP skew causing token/cache expiry | OIDC token validation fails; `actions/cache` restore returns `Cache service responded with 403`; workflow credential exchange fails | `timedatectl status` on runner host; `gh run view $RUN_ID --log \| grep -E "token.*expir\|403\|clock"` | Sync runner host NTP: `sudo systemctl restart chronyd`; verify: `chronyc tracking \| grep "System time"`; for GitHub-hosted runners, report to GitHub Support |
| File descriptor exhaustion | Runner process cannot open new connections; API calls to GitHub fail with `Too many open files`; artifact upload fails | `cat /proc/$(pgrep Runner.Listener)/limits \| grep "open files"` on runner host; `gh run view $RUN_ID --log \| grep "Too many open files"` | Increase runner fd limits: add `LimitNOFILE=65536` to runner systemd unit; add `* soft nofile 65536` to `/etc/security/limits.conf`; reduce parallel matrix job count |
| Conntrack table saturation | Runner cannot establish new HTTPS connections to GitHub API or container registry; jobs hang at checkout or docker pull | `sysctl net.netfilter.nf_conntrack_count` vs `net.netfilter.nf_conntrack_max` on runner host; `dmesg \| grep "nf_conntrack: table full"` | Increase conntrack max: `sysctl -w net.netfilter.nf_conntrack_max=262144`; reduce conntrack timeout: `sysctl -w net.netfilter.nf_conntrack_tcp_timeout_established=600`; distribute jobs across more runner hosts |
| Kernel panic on runner host | All in-flight jobs on host lost simultaneously; multiple workflows fail at the same timestamp | `journalctl -k --since "1 hour ago" \| grep -i "panic\|BUG\|oops"` on runner host (after reboot); `gh run list --status failure --json databaseId,createdAt \| jq '[.[] \| select(.createdAt > "TIMESTAMP")]'` — cluster of failures at same time | Enable kdump on runner hosts; configure runner auto-start on boot: `systemctl enable actions.runner.*.service`; use runner groups across multiple hosts so kernel panic on one host doesn't lose all capacity |
| NUMA imbalance causing inconsistent job performance | Same workflow completes in 2 min on some runs, 8 min on others; no code change; runner host has multi-socket CPU | `numactl --hardware` on runner host; `numastat -p $(pgrep Runner.Worker)` to check memory allocation across NUMA nodes | Pin runner processes to NUMA node: `numactl --cpunodebind=0 --membind=0 ./run.sh`; or configure systemd unit with `CPUAffinity=` and `NUMAPolicy=bind` |

## Deployment Pipeline & GitOps Failure Patterns

| Failure Mode | Impact on GitHub Actions | Detection Command | Remediation |
|-------------|------------------------|-------------------|-------------|
| Image pull failure in workflow | Docker build or container action fails with `toomanyrequests` or `unauthorized` from registry; deploy pipeline blocked | `gh run view $RUN_ID --log \| grep -E "toomanyrequests\|unauthorized\|ImagePullBackOff"` | Authenticate to registry in workflow: add `docker/login-action@v3` step; use GitHub Container Registry (ghcr.io) to avoid Docker Hub rate limits; cache base images with `actions/cache` |
| Registry auth token expired mid-workflow | First Docker pull succeeds but subsequent pulls in later steps fail with `401 Unauthorized`; token TTL shorter than workflow duration | `gh run view $RUN_ID --log \| grep -B5 "401\|unauthorized" \| grep -i "step"` — identify which step fails | Re-authenticate before each pull step; use `docker/login-action@v3` with `logout: false` at the start of each job; reduce workflow duration by parallelizing |
| Helm drift between Git and live cluster | Deploy workflow succeeds but cluster state does not match Git manifests; `helm diff` shows unexpected changes | Add `helm diff upgrade` step to workflow: `helm diff upgrade $RELEASE $CHART --values values.yaml` in CI; `gh run view $RUN_ID --log \| grep "has been changed"` | Add mandatory `helm diff` check before `helm upgrade` in deploy workflow; fail pipeline if drift detected; add scheduled workflow to run `helm diff` nightly |
| ArgoCD sync stuck after GitHub Actions deploy | Workflow pushes new image tag to Git; ArgoCD Application shows `OutOfSync` but never progresses to `Synced` | `argocd app get $APP --output json \| jq '.status.sync.status'` in post-deploy workflow step; `gh run view $RUN_ID --log \| grep "OutOfSync"` | Add ArgoCD sync wait step: `argocd app wait $APP --timeout 300 --sync` in workflow; add health check step after sync; configure ArgoCD webhook on GitHub push events |
| PDB blocking rollout triggered by workflow | Deploy workflow triggers rollout; PodDisruptionBudget prevents old pods from terminating; rollout stuck at partial progress | Add rollout status check: `kubectl rollout status deployment/$DEPLOY --timeout=600s` in workflow; `gh run view $RUN_ID --log \| grep "waiting for\|PodDisruptionBudget"` | Add pre-deploy PDB check step: `kubectl get pdb -n $NS -o json \| jq '.items[] \| select(.status.disruptionsAllowed==0)'`; temporarily adjust PDB during deploy window; add workflow timeout |
| Blue-green cutover failure | Workflow switches traffic to green environment; health check fails; rollback step not triggered; users hit broken green deployment | `gh run view $RUN_ID --json jobs --jq '.jobs[] \| select(.name \| contains("cutover")) \| {name, conclusion}'`; check health check step output | Add explicit health check with rollback: `if ! curl -sf $GREEN_URL/health; then gh workflow run rollback.yml; exit 1; fi`; implement blue-green cutover as reusable workflow with built-in rollback |
| ConfigMap drift from manual kubectl edit | Workflow deploys app expecting ConfigMap values from Git but someone manually edited ConfigMap in cluster; app behaves unexpectedly | Add drift detection step: `kubectl get configmap $CM -n $NS -o yaml \| diff - k8s/configmap.yaml` in pre-deploy workflow | Enforce GitOps: add `kubectl apply --server-side --field-manager=github-actions` to detect and overwrite manual changes; add scheduled workflow to audit ConfigMap drift |
| Feature flag misconfiguration in deploy workflow | Workflow sets feature flag via API call; flag value incorrect due to environment variable typo; feature enabled/disabled incorrectly in production | `gh run view $RUN_ID --log \| grep -E "feature.flag\|toggle\|variant"` — check actual flag value set vs expected | Add feature flag validation step: query flag service API after setting and assert expected value; use environment-specific workflow inputs with `required: true` for flag values; add approval gate for production flag changes |

## Service Mesh & API Gateway Edge Cases

| Failure Mode | Impact on GitHub Actions | Detection Command | Remediation |
|-------------|------------------------|-------------------|-------------|
| Circuit breaker false positive on deploy target | Workflow deploy succeeds but service mesh circuit breaker trips on new version due to startup latency; traffic not reaching new pods | `gh run view $RUN_ID --log \| grep -E "circuit\|breaker\|outlier"` in post-deploy health check step; `istioctl proxy-config cluster $POD \| grep "circuit_breakers"` | Add warm-up step in deploy workflow: wait for readiness before marking deploy complete; configure circuit breaker with higher `consecutive5xx` threshold during deploy window; add `istioctl` validation step |
| Rate limiting blocking deploy API calls | Workflow makes many API calls to deploy target (Kubernetes API, cloud APIs); rate limited; deploy steps fail intermittently | `gh run view $RUN_ID --log \| grep -E "429\|rate.limit\|throttl"` | Add retry with backoff to deploy steps: `for i in 1 2 3; do kubectl apply -f manifests/ && break \|\| sleep $((i*10)); done`; batch API calls; use server-side apply to reduce API call count |
| Stale service discovery after deploy | Workflow deploys new version; old endpoints still served by service mesh; health check passes but users see old version | Add version verification step: `curl -s $SERVICE_URL/version` in post-deploy workflow; compare against expected version from `$GITHUB_SHA` | Add service discovery refresh step: `kubectl rollout status` then verify endpoints: `kubectl get endpoints $SVC -o json \| jq '.subsets[].addresses[].targetRef.name'` match new pods; add propagation delay wait |
| mTLS certificate rotation during deploy | Deploy workflow runs during scheduled mTLS cert rotation; new pods cannot establish mTLS connections; deploy health check fails | `gh run view $RUN_ID --log \| grep -E "tls\|certificate\|handshake\|x509"` in deploy step output; `istioctl proxy-status \| grep -v SYNCED` | Add pre-deploy mesh health check: `istioctl proxy-status \| grep -c "NOT SYNCED"` — if > 0, wait; schedule deploys outside cert rotation windows; add retry logic for TLS errors in health check |
| Retry storm from workflow-triggered load test | Workflow runs integration tests against deployed service; test failures trigger retries; retries amplify through service mesh; downstream services overwhelmed | `gh run view $RUN_ID --log \| grep -E "retry\|timeout\|503"` — count escalating error pattern; `istioctl proxy-config route $POD \| grep retries` | Cap retries in workflow test step: set `--max-retries 2` on test runner; configure mesh retry budget: `retries: { attempts: 2, retryOn: "5xx" }` in VirtualService; add circuit breaker test step |
| gRPC deadline exceeded in deploy verification | Workflow post-deploy gRPC health check fails with `DEADLINE_EXCEEDED`; service is actually healthy but mesh adds latency | `gh run view $RUN_ID --log \| grep -E "DEADLINE_EXCEEDED\|grpc.*timeout"` | Increase gRPC deadline in health check step: `grpcurl -max-time 30 $HOST:$PORT grpc.health.v1.Health/Check`; configure mesh timeout to match: `timeout: 30s` in VirtualService |
| Trace context lost across workflow-triggered services | Deploy workflow triggers smoke tests; distributed traces show broken spans; cannot correlate test requests through service mesh | `gh run view $RUN_ID --log \| grep -E "trace.id\|correlation.id\|x-request-id"` — check if trace headers are propagated in test requests | Inject trace headers in workflow test step: `curl -H "x-request-id: gh-$GITHUB_RUN_ID" -H "traceparent: 00-$(openssl rand -hex 16)-$(openssl rand -hex 8)-01" $SERVICE_URL`; verify end-to-end trace in post-deploy step |
| Load balancer health check mismatch post-deploy | Workflow deploys new version with changed health endpoint; external LB still checking old path; LB marks all backends unhealthy; outage | `gh run view $RUN_ID --log \| grep -E "health.*check\|unhealthy\|backend"` in post-deploy monitoring step; `curl -s -o /dev/null -w "%{http_code}" $LB_HEALTH_URL` | Add LB health check validation in deploy workflow: verify health endpoint responds before and after deploy; update LB health check path in same workflow as app deploy; add rollback trigger if LB reports unhealthy backends |
