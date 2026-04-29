---
name: helm-agent
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-helm-agent
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
# Helm SRE Agent

## Role
Owns reliability and incident response for Helm-managed Kubernetes workloads. Responsible for release lifecycle health (install, upgrade, rollback, uninstall), hook failure triage, chart version governance, OCI/ChartMuseum registry operations, values configuration correctness, and CRD upgrade safety across all Helm-managed namespaces.

## Architecture Overview

```
Developer / CI Pipeline
        │
        ├─── helm install / upgrade / rollback / uninstall
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│  Helm Client (v3)                                             │
│  ├── Chart sources: OCI registry, ChartMuseum, local, Git     │
│  ├── Values: values.yaml, -f overrides, --set flags           │
│  └── Helmfile / Helmsman for multi-chart orchestration        │
└──────────────────────────┬────────────────────────────────────┘
                           │ Kubernetes API (REST)
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Kubernetes Cluster                                          │
│                                                              │
│  Release Secrets (helm.sh/release.v1) in target namespace   │
│  └── helm.sh/release.v1  (base64-gzip of release object)    │
│                                                              │
│  Rendered Kubernetes Resources                               │
│  ├── Deployments, StatefulSets, Services, Ingresses          │
│  ├── CRDs (crds/ directory — special lifecycle)              │
│  └── Hooks (pre-install, post-install, pre-upgrade, etc.)    │
└──────────────────────────────────────────────────────────────┘
```

Helm stores release state as Kubernetes Secrets (v3) in the release namespace. Release status transitions: `pending-install` → `deployed` → `pending-upgrade` → `deployed` | `failed`. A release stuck in `pending-*` blocks all further Helm operations.

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| Releases in `failed` state | > 1 | > 3 | `helm list -A --filter '.*' -o json \| jq '.[] \| select(.status=="failed")'` |
| Releases in `pending-upgrade`/`pending-install` | > 0 for > 5 min | > 0 for > 15 min | Helm lock; blocks further upgrades |
| Hook job completion time | > 5 min | > 15 min or `BackoffLimitExceeded` | Pre/post hooks blocking release |
| Chart pull latency (OCI/ChartMuseum) | > 5s | > 30s | Registry performance or network issues |
| Helm revision count per release | > 50 | > 256 | Old revisions pile up; `--history-max` not set |
| CRD version drift (installed vs. chart) | 1 major version behind | 2+ major versions | Breaking API changes accumulate |
| Values schema validation failures | Any | Any + blocked deploy | `values.schema.json` introduced in chart |
| Helmfile sync failures | > 0 | > 3 consecutive | Environment config drift |
| Release rollback frequency | > 2/week per release | > 1/day | Unstable chart or values config |
| Registry auth failures | Any | > 3 consecutive | Token expiry or registry outage |

## Alert Runbooks

### Alert: `HelmReleasePendingUpgrade`
**Trigger:** Release status is `pending-upgrade` for more than 10 minutes.

**Triage steps:**
1. Identify stuck releases:
   ```bash
   helm list -A -o json | jq '.[] | select(.status | startswith("pending"))'
   ```
2. Find the release secret to inspect state:
   ```bash
   kubectl get secret -n <NAMESPACE> -l owner=helm,status=pending-upgrade
   ```
3. Check if a hook job is blocking:
   ```bash
   kubectl get jobs -n <NAMESPACE> -l "helm.sh/chart"
   kubectl describe job -n <NAMESPACE> <HOOK_JOB_NAME>
   ```
4. If hook is `BackoffLimitExceeded` or stuck, delete it to unblock and roll back:
   ```bash
   kubectl delete job -n <NAMESPACE> <HOOK_JOB_NAME>
   helm rollback <RELEASE_NAME> -n <NAMESPACE>
   ```
5. If no hook, the release state is corrupted. Patch the release secret:
   ```bash
   # Get the latest revision number
   helm history <RELEASE_NAME> -n <NAMESPACE>
   # Roll back to last successful revision
   helm rollback <RELEASE_NAME> <LAST_GOOD_REVISION> -n <NAMESPACE>
   ```

---

### Alert: `HelmReleaseInFailedState`
**Trigger:** Release status transitions to `failed`.

**Triage steps:**
1. Get release failure details:
   ```bash
   helm status <RELEASE_NAME> -n <NAMESPACE>
   helm history <RELEASE_NAME> -n <NAMESPACE> --max 5
   ```
2. Check rendered resources for errors:
   ```bash
   kubectl get events -n <NAMESPACE> --sort-by='.lastTimestamp' | tail -20
   kubectl get pods -n <NAMESPACE> | grep -v Running
   ```
3. Review the failed release manifest:
   ```bash
   helm get manifest <RELEASE_NAME> -n <NAMESPACE> | kubectl apply --dry-run=server -f -
   ```
---

### Alert: `HelmHookJobFailed`
**Trigger:** A pre/post-install or pre/post-upgrade hook job completes with failure.

**Triage steps:**
1. Identify the failing hook:
   ```bash
   kubectl get jobs -n <NAMESPACE> -l "helm.sh/hook" -o wide
   kubectl logs -n <NAMESPACE> job/<HOOK_JOB_NAME> --previous
   ```
2. Check hook annotations on the job:
   ```bash
   kubectl get job -n <NAMESPACE> <HOOK_JOB_NAME> -o jsonpath='{.metadata.annotations}'
   ```
3. If the hook is a database migration, check if migration is idempotent before rerunning.
---

### Alert: `HelmChartRegistryAuthFailure`
**Trigger:** `helm pull` or `helm upgrade` fails with authentication error.

**Triage steps:**
1. Test registry authentication:
   ```bash
   # OCI registry
   helm registry login <REGISTRY_HOST> --username <USER> --password <TOKEN>
   
   # ChartMuseum
   curl -u user:pass https://<CHARTMUSEUM_HOST>/api/charts/<CHART_NAME>
   ```
2. Check if the registry secret exists in the namespace:
   ```bash
   kubectl get secret -n <NAMESPACE> -l "helm.sh/chart" -o name
   ```
3. Re-authenticate and retry:
   ```bash
   helm registry logout <REGISTRY_HOST>
   helm registry login <REGISTRY_HOST> --username $REGISTRY_USER --password $REGISTRY_TOKEN
   helm pull oci://<REGISTRY_HOST>/charts/<CHART_NAME> --version <VERSION>
   ```

## Common Issues & Troubleshooting

### Issue 1: Release Stuck in `pending-upgrade` — Cannot Run Any Helm Command
**Symptom:** `Error: UPGRADE FAILED: another operation (install/upgrade/rollback/uninstall) is in progress`

**Diagnosis:**
```bash
# Confirm stuck status
helm list -n <NAMESPACE> -o json | jq '.[] | select(.name=="<RELEASE_NAME>") | .status'

# Find the Helm release secret
kubectl get secrets -n <NAMESPACE> -l "name=<RELEASE_NAME>,owner=helm" \
  --sort-by='.metadata.creationTimestamp' -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.labels.status}{"\n"}{end}'
```

### Issue 2: Pre-Upgrade Hook Job Blocking Upgrade
**Symptom:** Upgrade hangs; hook job in `Failed` or `Running` state indefinitely.

**Diagnosis:**
```bash
kubectl get jobs -n <NAMESPACE> -l "helm.sh/hook=pre-upgrade"
kubectl describe job -n <NAMESPACE> <HOOK_JOB>
kubectl logs -n <NAMESPACE> -l "job-name=<HOOK_JOB>" --tail=50
```

### Issue 3: CRD Upgrade Conflict
**Symptom:** `Error: rendered manifests contain a resource that already exists` or CRD validation errors after upgrade.

**Diagnosis:**
```bash
# Check installed CRD versions
kubectl get crds | grep <CRD_GROUP>

# Compare chart CRD with installed
helm show crds <CHART> --version <NEW_VERSION> | kubectl diff -f -

# Check if existing CRs are compatible with new schema
kubectl get <CUSTOM_RESOURCE> -A -o yaml | kubectl apply --dry-run=server -f -
```

### Issue 4: Values Schema Validation Failure
**Symptom:** `Error: values don't meet the specifications of the schema(s) in the following chart(s)`.

**Diagnosis:**
```bash
# Identify which values are failing
helm upgrade <RELEASE_NAME> <CHART> -n <NAMESPACE> --dry-run -f values.yaml 2>&1

# Review chart schema
helm show schema <CHART>

# Check current effective values
helm get values <RELEASE_NAME> -n <NAMESPACE> --all
```

### Issue 5: OCI Registry Pull Failure
**Symptom:** `Error: failed to fetch oci://registry.example.com/charts/app: unexpected status code 401 Unauthorized`

**Diagnosis:**
```bash
# Check Helm registry credentials
cat ~/.config/helm/registry/config.json | python3 -m json.tool

# Test OCI registry access
crane auth get <REGISTRY_HOST>
curl -H "Authorization: Bearer <TOKEN>" https://<REGISTRY_HOST>/v2/<CHART>/tags/list
```

### Issue 6: Helmfile Sync Partial Failure
**Symptom:** `helmfile sync` exits with errors; some releases updated, others not.

**Diagnosis:**
```bash
# List current state of all releases
helmfile list

# Diff without applying to see what would change
helmfile diff

# Identify which releases failed in the helmfile sync output
helmfile sync 2>&1 | grep -E "FAILED|Error|error"
```

## Key Dependencies

- **Kubernetes API Server** — all Helm operations require API server availability; degraded API server causes Helm timeouts
- **etcd** — stores Helm release secrets (Secrets API backed by etcd); etcd latency impacts release state reads/writes
- **OCI Registry / ChartMuseum** — chart source; authentication tokens must be valid; registry unavailability blocks installs/upgrades
- **Container Registry** — chart values reference image tags; wrong tags cause pod `ImagePullBackOff`
- **CRDs** — charts with CRDs require CRD installation before CR resources can be applied; CRD deletion destroys all CRs
- **RBAC** — Helm service account must have permissions to create/update all resource types in the chart
- **Cert-Manager** — charts using TLS often depend on cert-manager; absent cert-manager causes Ingress or webhook failures
- **Namespaces** — target namespaces must exist before install; `--create-namespace` flag handles this but may be omitted

## Cross-Service Failure Chains

- **Helm upgrade of ingress-nginx fails mid-way** → IngressClass resource deleted → All ingress routes return 404 → Multiple services appear down
- **Pre-upgrade DB migration hook fails** → Helm release stuck in `pending-upgrade` → Application pods on old version → Developers cannot deploy fixes until manually unblocked
- **CRD deleted during `helm uninstall`** → All Custom Resources across all namespaces destroyed silently → Data loss for CRD-backed storage (e.g., Prometheus rules, cert-manager certificates)
- **Helmfile sync fails on secrets chart** → Application environment variables missing → Pods start with missing config → Application crashes at startup
- **Registry token expiry in CI** → All chart upgrades fail across all pipelines → Accumulated application version lag → Emergency manual deploys required

## Partial Failure Patterns

- **Deployment updated, Service not:** Helm partially applied; pods on new version but Service selector still points to old labels. Run `helm upgrade --atomic` to auto-rollback on failure.
- **Hook completes but release marked failed:** Hook ran successfully but timed out waiting for `helm.sh/hook-succeeded` annotation. Check `hook-succeeded` pod annotation; manually delete hook object and retry.
- **ConfigMap updated, Deployment not restarted:** Values change updates ConfigMap but Deployment does not detect change (missing `checksum/config` annotation). Manually trigger rolling restart: `kubectl rollout restart deployment -n <NS>`.
- **Helm diff shows no changes but pods are on wrong image:** Release deployed with `--reuse-values` and image tag was moved. Always pass explicit `-f values.yaml` to prevent stale values inheritance.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|----------|
| `helm upgrade` (< 10 resources) | < 30s | 30-90s | > 90s |
| `helm upgrade` (50+ resources) | < 2 min | 2-5 min | > 5 min |
| `helm rollback` | < 60s | 1-3 min | > 3 min |
| `helm history` | < 3s | 3-10s | > 10s |
| `helm list -A` | < 5s | 5-15s | > 15s |
| `helm pull` (from OCI/ChartMuseum) | < 10s | 10-30s | > 30s |
| Hook job completion (pre-upgrade) | < 5 min | 5-15 min | > 15 min |
| `helmfile sync` (10 releases) | < 5 min | 5-15 min | > 15 min |

## Capacity Planning Indicators

| Indicator | Healthy | Watch | Action Required |
|-----------|---------|-------|-----------------|
| Helm release history count per release | < 20 revisions | 20-50 | Set `--history-max=10` in all helm upgrades |
| Total Helm release secrets (cluster-wide) | < 500 | 500-2000 | Prune old revisions; `helm history --max` |
| Hook job retention (completed jobs) | < 10 per release | 10-50 | Set `hook-delete-policy: hook-succeeded` |
| OCI registry storage per chart | < 5 GB | 5-20 GB | Prune old chart versions; retention policy |
| Helmfile release count per environment | < 50 | 50-100 | Consider chunking with concurrency limits |
| Average upgrade duration trend (week-over-week) | Stable | +20% | Investigate API server load or chart size |
| Failed helm operations per day | 0 | 1-3 | Investigate values config or chart stability |
| Releases without `--atomic` flag | 0 | Any | Risk of partial deploys with no auto-rollback |

## Diagnostic Cheatsheet

```bash
# List all releases across all namespaces with status
helm list -A -o table

# List only failed or pending releases
helm list -A -o json | jq '.[] | select(.status | test("failed|pending"))'

# Show full release status including last deploy notes
helm status <RELEASE_NAME> -n <NAMESPACE>

# View complete release history
helm history <RELEASE_NAME> -n <NAMESPACE>

# Get all values (including defaults) for a deployed release
helm get values <RELEASE_NAME> -n <NAMESPACE> --all

# Get rendered manifests for a deployed release
helm get manifest <RELEASE_NAME> -n <NAMESPACE>

# Dry-run an upgrade to see what would change
helm upgrade <RELEASE_NAME> <CHART> -n <NAMESPACE> -f values.yaml --dry-run

# Diff current deployed vs proposed upgrade (requires helm-diff plugin)
helm diff upgrade <RELEASE_NAME> <CHART> -n <NAMESPACE> -f values.yaml

# Find all Helm-managed resources in a namespace
kubectl get all -n <NAMESPACE> -l "app.kubernetes.io/managed-by=Helm"

# Get the release secret contents (base64 decode + decompress)
kubectl get secret -n <NAMESPACE> sh.helm.release.v1.<RELEASE_NAME>.v<REV> \
  -o jsonpath='{.data.release}' | base64 -d | gunzip | python3 -m json.tool | head -50

# List hooks for a release
helm get hooks <RELEASE_NAME> -n <NAMESPACE>
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|--------------------|-------------|
| Helm upgrade success rate | 99% | 7.2 hours of upgrade failures | (successful upgrades / total upgrade attempts) × 100 |
| Release stuck in pending state | < 1 incident/week | 4 incidents/month | Manual count; alert when pending > 10 min |
| Rollback execution time (p95) | < 2 minutes | > 2 min for 5% of rollbacks | Measured from `helm rollback` start to `deployed` status |
| Hook job completion (p95) | < 10 minutes | > 10 min for 5% of hook jobs | Measured from job start to job completion |

## Configuration Audit Checklist

| Check | Command | Expected |
|-------|---------|----------|
| All releases in deployed state | `helm list -A -o json \| jq '[.[] \| select(.status != "deployed")] \| length'` | `0` |
| History-max set to prevent secret accumulation | `helm history <REL> -n <NS> \| wc -l` | < 15 revisions |
| `--atomic` used in CI pipelines | Grep CI config for `helm upgrade` commands | All upgrades include `--atomic` or `--wait` |
| Hook delete policy set | `helm get hooks <REL> -n <NS>` | All hooks have `helm.sh/hook-delete-policy` annotation |
| Values schema present in charts | `helm show schema <CHART>` | Schema exists for user-facing charts |
| CRDs match installed chart version | `helm show crds <CHART> \| kubectl diff -f -` | No diff |
| Registry credentials not expired | `helm registry login <REGISTRY> --dry-run` | No auth errors |
| `--create-namespace` not used unnecessarily | Review CI helm commands | Only used for first-time installs |
| Release RBAC follows least privilege | `kubectl get clusterrolebindings -l "app.kubernetes.io/managed-by=Helm"` | No `cluster-admin` unless explicitly required |
| Helmfile environments use separate values files | `cat helmfile.yaml \| grep environments` | Per-environment values files present |

## Log Pattern Library

| Pattern | Meaning | Action |
|---------|---------|--------|
| `UPGRADE FAILED: another operation (install/upgrade/rollback/uninstall) is in progress` | Helm lock active on release | Find and remove pending-status secret or roll back |
| `Error: UPGRADE FAILED: post-upgrade hooks failed: timed out waiting for the condition` | Post-upgrade hook exceeded timeout | Delete hook job; check hook logs; retry upgrade |
| `Error: rendered manifests contain a resource that already exists` | Resource created outside Helm | `helm upgrade --force` or adopt resource with `kubectl label` |
| `Error: INSTALLATION FAILED: cannot re-use a name that is still in use` | Release name already exists in another state | `helm list -A` to find; delete or rename conflicting release |
| `Error: failed to download "oci://..."` | OCI chart pull failure | Re-authenticate to registry; check network connectivity |
| `Error: values don't meet the specifications of the schema(s)` | values.yaml violates chart JSON schema | Fix offending values per schema; `helm show schema <CHART>` |
| `coalesce.go: cannot overwrite table with non table` | Incorrect values type (array vs map) | Check values hierarchy; likely string where map expected |
| `Error: no repositories found` | `helm repo add` not run | Add required chart repositories |
| `Warning: Kubernetes server has an older API version` | Cluster API version behind chart expectations | Upgrade cluster or use compatible chart version |
| `must validate one and only one schema` | Helm values conflict with `oneOf` schema | Review chart schema; only one of the conflicting options should be set |
| `Error: release: not found` | Release does not exist in namespace | Verify namespace; check with `helm list -A` |
| `Job.batch is invalid: spec.backoffLimit: must be greater than or equal to 0` | Hook job spec error | Fix job spec in chart template |

## Error Code Quick Reference

| Error / Message Fragment | Root Cause | Quick Fix |
|--------------------------|-----------|-----------|
| `another operation is in progress` | Pending-status release secret | Delete pending secret; `helm rollback` |
| `post-upgrade hooks failed` | Hook job failed or timed out | Delete hook job; check pod logs; retry |
| `cannot re-use a name that is still in use` | Unfinished prior install | `helm uninstall --no-hooks` then reinstall |
| `resource already exists` | Out-of-band resource conflicts with Helm | `kubectl annotate` and `label` resource to adopt it |
| `manifest validation error` | Resource spec violates Kubernetes API schema | Fix template or values; validate with `--dry-run` |
| `timeout waiting for the condition` | Hook or resource readiness timeout | Increase `--timeout`; investigate why resource not ready |
| `UPGRADE FAILED: release has no deployed releases` | No prior successful deploy to roll back to | Must install fresh; `helm install` |
| `Error: YAML parse error` | Malformed values.yaml or template | `helm template <CHART> -f values.yaml` to identify syntax error |
| `no repositories found. You might need to run 'helm repo add'` | Repo not configured in environment | `helm repo add <NAME> <URL> && helm repo update` |
| `Error: chart requires kubeVersion: >=X.Y.Z` | Cluster Kubernetes version too old | Upgrade cluster or find compatible chart version |
| `401 Unauthorized` (OCI/ChartMuseum) | Registry auth failure | Re-login with valid credentials |
| `Error: missing required value` | Required value in `values.schema.json` not provided | Add missing key to values override |

## Known Failure Signatures

| Signature | Likely Cause | Diagnostic Step |
|-----------|-------------|-----------------|
| Upgrade hangs indefinitely at "RUNNING" | Pre/post hook stuck; timeout not set | `kubectl get jobs -n <NS> -l helm.sh/hook`; check job logs |
| All Helm commands fail for a release | Pending-status secret present | `kubectl get secrets -n <NS> -l name=<REL>,status=pending-upgrade` |
| `helm list` slow or times out | Too many release secrets accumulated; etcd load | `kubectl get secrets -A -l owner=helm \| wc -l`; prune old revisions |
| New pods use old image after upgrade | `--reuse-values` used; image.tag value not updated | `helm get values <REL> -n <NS> \| grep tag`; always pass full values |
| Release shows `deployed` but pods crashing | Helm succeeded but app has runtime errors | `kubectl describe pod` + logs; Helm does not validate runtime health unless `--wait` used |
| `helm diff` shows empty diff but app behaving differently | ConfigMap data updated but pod not restarted | `kubectl rollout restart deployment -n <NS>`; add checksum annotation to deployment |
| Repeated rollbacks needed for same release | Underlying infra problem not fixed | Check dependencies (DB, external services); don't keep rolling back without root cause |
| CRD resources disappear cluster-wide after chart uninstall | CRDs in chart `crds/` directory auto-deleted on uninstall | Use `--keep-crds` flag with `helm uninstall`; never include production CRDs in `crds/` without explicit lifecycle policy |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `Error: UPGRADE FAILED: another operation (install/upgrade/rollback/uninstall) is in progress` | Helm CLI | Release secret stuck in `pending-upgrade` or `pending-install` state | `helm list -n <NS> -o json \| jq '.[] \| select(.status \| startswith("pending"))'` | Delete the pending-status secret; then rollback or retry upgrade |
| `ImagePullBackOff` on newly deployed pods | kubectl / k8s events | Wrong image tag in chart values; container registry unreachable or auth expired | `kubectl describe pod -n <NS> <POD>` for `Failed to pull image` error | Fix image tag in values; re-authenticate container registry secret |
| `CrashLoopBackOff` after Helm upgrade | kubectl / k8s events | Application misconfiguration in new values; broken ConfigMap or Secret reference | `kubectl logs -n <NS> <POD> --previous`; `helm get values <REL> -n <NS>` | Roll back with `helm rollback <REL> -n <NS>` |
| `Error: rendered manifests contain a resource that already exists` | Helm CLI | Resource was created out-of-band; Helm does not own it | `kubectl get <RESOURCE> -n <NS> -o yaml \| grep 'managed-by'` | Adopt resource: add Helm annotations/labels; or use `helm upgrade --force` |
| `Error: values don't meet the specifications of the schema(s)` | Helm CLI | values.yaml violates the chart's `values.schema.json` constraints | `helm show schema <CHART>`; `helm upgrade --dry-run -f values.yaml` | Fix the offending value key/type per schema definition |
| `Error: INSTALLATION FAILED: cannot re-use a name that is still in use` | Helm CLI | A prior install failed partway; release exists in non-deployed state | `helm list -A -o json \| jq '.[] \| select(.name=="<NAME>") \| .status'` | `helm uninstall --no-hooks <REL>` then reinstall |
| `Error: post-upgrade hooks failed: timed out waiting for the condition` | Helm CLI | Post-upgrade hook Job did not complete within `--timeout` | `kubectl get jobs -n <NS> -l helm.sh/hook=post-upgrade` and check logs | Delete hook Job; investigate why it didn't complete; retry upgrade |
| `Error: failed to download oci://...` | Helm CLI | OCI registry auth failure or network issue | `helm registry login <REGISTRY>` to test credentials | Re-authenticate with valid token; verify registry hostname and chart path |
| `Error: chart requires kubeVersion: >=X.Y.Z` | Helm CLI | Kubernetes cluster version is older than chart minimum | `kubectl version --short` | Upgrade cluster or pin to an older compatible chart version |
| `coalesce.go: cannot overwrite table with non table` | Helm CLI | values type mismatch — string provided where map expected | `helm get values <REL> -n <NS> --all` and compare to chart defaults | Correct the offending key's type in your values override file |
| Service returns 404 or 503 after upgrade | HTTP client / application | Helm partially applied; Service selector or Ingress not updated | `helm get manifest <REL> -n <NS>`; `kubectl get svc,ingress -n <NS>` | Roll back with `helm rollback`; use `--atomic` in future to auto-rollback |
| Pods on old image version after successful upgrade | Application health checks | `--reuse-values` caused stale image.tag to persist | `helm get values <REL> -n <NS> \| grep tag` | Always pass full explicit values; never rely on `--reuse-values` in CI |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Release secret accumulation in etcd | `helm history` showing > 50 revisions; etcd memory growing | `kubectl get secrets -A -l owner=helm \| wc -l` | Weeks to months | Set `--history-max=10` in all CI helm upgrade commands; prune old secrets |
| Hook Job pile-up (completed jobs not cleaned) | Completed hook Jobs accumulating in namespace | `kubectl get jobs -n <NS> -l helm.sh/hook --field-selector=status.successful=1 \| wc -l` | Weeks | Set `helm.sh/hook-delete-policy: hook-succeeded` on all hook Jobs |
| OCI registry storage growing unboundedly | Registry disk usage climbing; old chart versions retained indefinitely | Registry storage metrics dashboard; or `crane ls <REGISTRY>/<CHART>` to list tags | Months | Implement chart version retention policy; auto-delete versions older than 90 days |
| Helm upgrade duration trend increasing | p95 upgrade time growing week-over-week in CI metrics | Track CI job duration for helm upgrade steps | Weeks | Profile API server load; check if chart renders too many resources; review etcd latency |
| Helmfile sync partially failing in silence | Individual release sync errors buried in verbose output | `helmfile sync 2>&1 \| grep -c "FAILED"` | Days | Add per-release error monitoring; use `helmfile --log-level error sync` in CI with alerts |
| Values schema validation bypassed by CI override | `--set` overrides in CI silently ignoring schema checks | `helm upgrade --dry-run -f values.yaml` in CI to catch before apply | Weeks | Enforce dry-run + schema validation as a required CI gate before upgrade |
| Chart version lag accumulating across environments | Staging on chart v2.x; prod on v1.x; security patches missed | `helmfile list` across environments to compare chart versions | Weeks | Track chart version per environment in a registry or GitOps repo; alert on version delta > 1 minor |
| RBAC permission creep in Helm-deployed ServiceAccounts | ClusterRoleBindings accumulating broader permissions with each chart upgrade | `kubectl get clusterrolebindings -l app.kubernetes.io/managed-by=Helm -o json \| jq '.items[].roleRef.name'` | Months | Audit and tighten ServiceAccount RBAC on each major chart upgrade |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# helm-health-snapshot.sh
# Prints health summary of all Helm releases in the cluster

set -euo pipefail

echo "=== Helm Version ==="
helm version --short

echo ""
echo "=== All Releases (with status) ==="
helm list -A -o table

echo ""
echo "=== Stuck / Failed Releases ==="
STUCK=$(helm list -A -o json | jq -r '.[] | select(.status | test("failed|pending")) | "\(.namespace)/\(.name): \(.status)"')
if [[ -z "$STUCK" ]]; then
  echo "  None"
else
  echo "$STUCK"
fi

echo ""
echo "=== Hook Jobs with Issues ==="
kubectl get jobs -A -l "helm.sh/hook" \
  --no-headers 2>/dev/null | awk '$3 != $2 {print}' || echo "  None found"

echo ""
echo "=== Release Secret Count per Namespace ==="
kubectl get secrets -A -l owner=helm --no-headers 2>/dev/null \
  | awk '{print $1}' | sort | uniq -c | sort -rn | head -10

echo ""
echo "=== Recent Kubernetes Events for Helm Resources ==="
kubectl get events -A --sort-by='.lastTimestamp' 2>/dev/null \
  | grep -i "helm\|hook\|chart" | tail -15 || echo "  None"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# helm-perf-triage.sh
# Times helm operations and checks for degraded performance

RELEASE="${1:?Usage: $0 <release-name> <namespace>}"
NAMESPACE="${2:?Usage: $0 <release-name> <namespace>}"

echo "=== Helm Performance Triage: $RELEASE / $NAMESPACE ==="

echo ""
echo "--- helm history (etcd read speed) ---"
time helm history "$RELEASE" -n "$NAMESPACE" > /dev/null

echo ""
echo "--- helm get manifest (secret decode + render speed) ---"
time helm get manifest "$RELEASE" -n "$NAMESPACE" > /dev/null

echo ""
echo "--- helm status ---"
helm status "$RELEASE" -n "$NAMESPACE"

echo ""
echo "--- Revision count (history depth) ---"
REV_COUNT=$(helm history "$RELEASE" -n "$NAMESPACE" --max 9999 2>/dev/null | wc -l)
echo "  Revisions: $REV_COUNT"
if (( REV_COUNT > 50 )); then
  echo "  [WARNING] High revision count — etcd storage pressure; set --history-max=10"
fi

echo ""
echo "--- Hook Job status ---"
kubectl get jobs -n "$NAMESPACE" -l "helm.sh/chart" \
  -o custom-columns="NAME:.metadata.name,COMPLETE:.status.succeeded,FAILED:.status.failed" \
  --no-headers 2>/dev/null || echo "  No hook jobs found"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# helm-registry-audit.sh
# Audits OCI registry connectivity and validates release resource health

RELEASE="${1:?Usage: $0 <release-name> <namespace> [registry-host]}"
NAMESPACE="${2:?Usage: $0 <release-name> <namespace> [registry-host]}"
REGISTRY="${3:-}"

echo "=== Helm Registry & Resource Audit ==="

if [[ -n "$REGISTRY" ]]; then
  echo ""
  echo "--- OCI Registry Auth Check: $REGISTRY ---"
  if helm registry login "$REGISTRY" --help > /dev/null 2>&1; then
    cat ~/.config/helm/registry/config.json 2>/dev/null \
      | python3 -m json.tool 2>/dev/null \
      | grep -E '"serveraddress"|"username"' || echo "  No registry credentials cached"
  fi
fi

echo ""
echo "--- Release Resource Health: $RELEASE ---"
kubectl get all -n "$NAMESPACE" \
  -l "app.kubernetes.io/instance=$RELEASE" \
  -o custom-columns="KIND:.kind,NAME:.metadata.name,READY:.status.readyReplicas,DESIRED:.status.replicas" \
  --no-headers 2>/dev/null

echo ""
echo "--- Pods Not Running ---"
kubectl get pods -n "$NAMESPACE" \
  -l "app.kubernetes.io/instance=$RELEASE" \
  --field-selector='status.phase!=Running' \
  -o wide 2>/dev/null || echo "  All pods running"

echo ""
echo "--- CRDs vs Chart CRDs Drift ---"
CHART=$(helm list -n "$NAMESPACE" -o json | jq -r --arg r "$RELEASE" '.[] | select(.name==$r) | .chart')
echo "  Installed chart: $CHART"
helm show crds "$CHART" 2>/dev/null \
  | kubectl diff -f - 2>/dev/null | head -30 || echo "  No CRD drift detected"

echo ""
echo "--- RBAC Audit for Release ServiceAccounts ---"
kubectl get clusterrolebindings -l "app.kubernetes.io/instance=$RELEASE" \
  -o custom-columns="BINDING:.metadata.name,ROLE:.roleRef.name" --no-headers 2>/dev/null || echo "  No cluster-level bindings"
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| etcd storage pressure from accumulated release secrets | `helm list` and `helm history` become slow; etcd memory alert fires | `kubectl get secrets -A -l owner=helm \| wc -l`; etcd `db_size` metric | Prune old revisions: `helm history <REL> -n <NS> --max 9999 \| awk 'NR>1{print $1}' \| head -N \| xargs helm del --revision` | Set `--history-max=10` globally in CI; enforce via Helmfile config |
| Hook Job CPU/memory contention with application pods | Application pods throttled or evicted during heavy migration Job run | `kubectl top pods -n <NS>`; compare Job resource requests vs. node allocatable | Add `resources.limits` to hook Job spec; schedule migrations during low-traffic windows | Set explicit resource requests and limits on all hook Job templates in the chart |
| OCI registry rate limiting affecting multiple teams | `helm pull` failures across CI pipelines simultaneously; 429 responses from registry | Registry access logs; count `helm pull` requests per minute during failure window | Cache pulled charts in CI artifact store; use a pull-through proxy (e.g., Harbor) | Run a shared internal chart registry mirror; all teams pull from mirror, not upstream |
| Helm upgrades competing for Kubernetes API server quota | Concurrent helmfile syncs trigger API server throttle; 429 from `kubectl` during CI peak | `kubectl get --raw /metrics \| grep apiserver_request_total`; API server audit logs | Reduce `helmfile` concurrency with `--concurrency=3`; stagger CI pipeline times | Set concurrency limits in Helmfile; use GitOps pull-based approach (ArgoCD/Flux) to serialize applies |
| Namespace ResourceQuota exhausted by Helm-deployed workloads | New pods pending; `helm upgrade` succeeds but pods never start | `kubectl describe namespace <NS> \| grep -A 20 'Resource Quotas'` | Delete unused deployments in namespace; increase quota temporarily | Set conservative resource requests in chart values; monitor quota headroom per namespace |
| Shared container registry pull rate limit | `ImagePullBackOff` across multiple releases after upgrades | `kubectl describe pod -n <NS> <POD> \| grep "pull rate limit"` | Switch to pre-pulled image cache; use `imagePullPolicy: IfNotPresent` | Run a pull-through registry cache (e.g., Harbor); avoid DockerHub for production |
| Large chart with many resources overwhelming API server | Helm upgrade hangs at resource apply phase; API server CPU spikes | `kubectl get --raw /metrics \| grep apiserver_request_duration`; watch API server pod CPU | Reduce `--parallelism` equivalent: split chart into sub-charts | Design charts with bounded resource counts; avoid generating > 100 resources per release |
| CRD version conflict between two charts | Both charts own the same CRD; one upgrade breaks the other's CRs | `kubectl get crd <NAME> -o yaml \| grep helm.sh` to see which release owns it | Remove CRD from one chart's `crds/` directory; manage CRD lifecycle separately | Never install the same CRD from two different Helm charts; use a dedicated CRD chart |
| Webhook timeout cascading during mass upgrade | All resource mutations blocked; cluster appears frozen during concurrent helm upgrades | `kubectl get validatingwebhookconfigurations`; check webhook endpoint pod health | Increase webhook `timeoutSeconds`; temporarily disable non-critical webhooks | Set `failurePolicy: Ignore` for non-critical webhooks; keep webhook pods on dedicated nodes |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Helm release upgrade fails mid-apply (partial resource state) | Some resources updated, others not → application starts with mixed old/new config → runtime errors or protocol mismatch | Single release affected; downstream services may get errors from partially-upgraded dependency | `helm status <RELEASE> -n <NS>` shows `failed`; `kubectl get events -n <NS>` shows resource errors at upgrade time | `helm rollback <RELEASE> <PREV_REVISION> -n <NS>` immediately; verify all pods healthy post-rollback |
| Pre-upgrade hook Job fails → upgrade aborted, release stuck in `pending-upgrade` | Helm marks release `pending-upgrade`; subsequent `helm upgrade` commands fail with `another operation is in progress` | Release is locked; no further upgrades or rollbacks possible without manual intervention | `helm status <RELEASE> -n <NS>` returns `pending-upgrade`; `helm history <RELEASE>` shows no new revision | `kubectl delete secret sh.helm.release.v1.<REL>.v<N> -n <NS>` to unlock; or `helm rollback` with `--force` |
| Kubernetes API server throttling during mass Helm sync | Helmfile sync applies dozens of releases simultaneously → API 429 errors → some releases partially applied | Multiple releases in degraded/partial state; cluster state diverges from desired state | `kubectl get --raw /metrics | grep apiserver_request_total`; Helm output showing `too many requests` errors | Reduce Helmfile concurrency: `helmfile sync --concurrency 3`; retry failed releases individually |
| CRD deletion during `helm uninstall` breaks dependent CRs | Helm deletes CRD → all custom resources of that type immediately gone → controllers that depend on CRs crash | All CRs of that type lost cluster-wide (across all namespaces); controllers enter crash loop | `kubectl get <CRD_KIND> -A` returns nothing after uninstall; controller logs show `no kind ... registered` | Restore CRD from backup: `kubectl apply -f crd-backup.yaml`; restore CRs from ETCD backup or GitOps source |
| Validating webhook introduced by Helm chart blocks all pod creation | Webhook pod becomes unhealthy → webhook endpoint unreachable → all pod creation in cluster rejected | Cluster-wide pod creation halted; deployments, jobs, daemonsets all fail to schedule | `kubectl describe pod -n <NS> <PODNAME>` shows `failed calling webhook`; `kubectl get validatingwebhookconfigurations` | `kubectl delete validatingwebhookconfiguration <NAME>` or patch `failurePolicy: Ignore` immediately |
| Helm release secret accumulation causing etcd OOM | Thousands of release secrets stored in etcd → etcd memory grows → etcd pod OOM-killed → control plane unavailable | Kubernetes control plane down; all API operations fail | `kubectl get secrets -A -l owner=helm | wc -l` > 10,000; etcd `db_size` metric near quota | Emergency: `kubectl delete secrets -A -l owner=helm --field-selector=metadata.name!=sh.helm.release.v1.<REL>.v<LATEST>` |
| Wrong image tag in chart upgrade causing ImagePullBackOff | Deployment rolls forward; new pods fail to pull image → rollout stalls → old pods terminated → service degraded | Service availability degraded proportional to deployment progress | `kubectl get pods -n <NS>` shows `ImagePullBackOff`; `kubectl describe pod` shows `404 Not Found` for image | `helm rollback <RELEASE> -n <NS>`; verify image exists before next upgrade: `docker manifest inspect <IMAGE>:<TAG>` |
| Helm chart includes duplicate ClusterRole that overwrites existing RBAC | Uninstall of one chart deletes ClusterRole used by another → second chart's ServiceAccount loses permissions | All pods using the shared ClusterRole lose API server access; applications fail with `403 Forbidden` | `kubectl auth can-i list pods --as system:serviceaccount:<NS>:<SA>` returns `no` unexpectedly after chart uninstall | Re-create ClusterRole and ClusterRoleBinding from backup; audit RBAC: `kubectl get clusterrolebindings -o yaml | grep <SA>` |
| NetworkPolicy deployed by Helm chart blocks cross-namespace traffic | Strict `NetworkPolicy` applied without allowing existing traffic patterns → inter-service communication drops | Traffic between namespaces fails; dependent services return 503; alert storm from monitoring | `kubectl describe networkpolicy -n <NS>`; `kubectl exec -it <POD> -- curl http://<SVC>.<OTHER_NS>` returns connection refused | `kubectl delete networkpolicy <NAME> -n <NS>` as emergency; then audit and fix the policy spec |
| Helm-managed ConfigMap change causes rolling restart of all pods | ConfigMap checksum annotation change triggers rolling restart → all replicas cycle simultaneously if `maxUnavailable` misconfigured | Service outage if `maxUnavailable: 100%` or if pods have slow startup | `kubectl rollout status deployment/<NAME> -n <NS>` shows stalled; `kubectl get pods -n <NS>` shows all `Terminating` | `kubectl rollout pause deployment/<NAME> -n <NS>`; then `helm rollback <RELEASE> -n <NS>` |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Helm CLI version upgrade (e.g., 3.11 → 3.14) | Chart rendering differences due to changed template engine behavior; `helm diff` shows unexpected resource changes | On first `helm upgrade` run with new CLI | Compare `helm template` output before and after CLI version; check Helm changelog for breaking changes | Pin Helm CLI version in CI; use `helm version --short` in pipeline to assert exact version |
| Chart version bump with values schema change | `helm upgrade` fails: `values don't meet the specifications of the schema in Chart.yaml`; existing values.yaml rejected | Immediately at upgrade invocation | `helm upgrade --dry-run` exposes schema validation errors before apply; `helm lint` in CI | Fix values.yaml to satisfy new schema; or pin chart version and update values carefully |
| `helm upgrade --force` flag added to CI pipeline | Forces pod replacement even when no change needed; causes unnecessary restarts; brief service disruption | Within seconds of CI pipeline running | `kubectl get events -n <NS> | grep "Killing"` timestamps correlate with CI run; `--force` in CI pipeline config history | Remove `--force` from CI; use `--atomic` instead for safe rollbacks |
| Global values override with incorrect type (string vs. int in YAML) | Pods start with wrong environment variable types; application config parse errors; `CrashLoopBackOff` | Within minutes of deployment | `kubectl logs <POD> -n <NS>` shows config parse error; `helm get values <RELEASE> -n <NS>` shows wrong type | `helm rollback <RELEASE> -n <NS>`; fix values.yaml type; redeploy |
| PodDisruptionBudget added to chart blocking upgrade rollout | Rolling upgrade stalls indefinitely because PDB prevents pod termination | Within first rolling restart attempt | `kubectl describe pdb -n <NS>` shows `0` disruptions allowed; `kubectl rollout status` hangs | `kubectl delete pdb <NAME> -n <NS>` temporarily; fix PDB `minAvailable` value and redeploy |
| `helm repo update` pulling new default chart values that override pinned values | Previously working values silently overridden by new chart defaults; runtime behavior changes | On next `helm upgrade` after `helm repo update` | `helm diff upgrade` output shows unexpected value changes; compare `helm get values <REL>` before/after | Always use `helm upgrade --reuse-values` or pin all values explicitly; avoid relying on chart defaults |
| Service type changed from ClusterIP to LoadBalancer in chart upgrade | Cloud provider provisions new LB; DNS still points to old LB IP; traffic drops | 5–15 min until DNS TTL expires | `kubectl get svc -n <NS>` shows new `EXTERNAL-IP`; DNS still resolves to old IP | Update DNS immediately; `kubectl patch svc` to restore ClusterIP if unintentional; `helm rollback` |
| StorageClass changed in PVC template during Helm upgrade | Helm cannot modify immutable PVC fields; upgrade fails with `field is immutable`; pod remains on old PVC | Immediately at upgrade | `helm upgrade` error: `The PersistentVolumeClaim ... is invalid: spec: Forbidden: field is immutable` | Delete PVC manually (after data backup); re-run upgrade; or keep old StorageClass and migrate data separately |
| RBAC `rules` in ClusterRole tightened during chart upgrade | Applications lose permissions they had before; `403 Forbidden` errors in logs post-upgrade | Within seconds to minutes as permission checks fail | `kubectl auth can-i <VERB> <RESOURCE> --as system:serviceaccount:<NS>:<SA>` returns `no`; app logs show `403` | `helm rollback <RELEASE> -n <NS>`; audit new ClusterRole against previous with `helm diff` |
| Liveness probe parameters tightened in chart upgrade | Pods restarted by kubelet due to stricter probe; cascading restarts under load | During/after rolling upgrade under traffic load | `kubectl describe pod -n <NS> <POD>` shows `Liveness probe failed`; probe parameters in new chart diff | `helm rollback <RELEASE>`; tune probe `initialDelaySeconds` and `failureThreshold` before redeploying |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Helm release state in etcd diverges from actual deployed resources | `helm status <REL> -n <NS>` shows `deployed`; `kubectl get deploy -n <NS>` shows different spec than `helm get manifest` | Resources were modified directly with `kubectl` outside Helm; Helm thinks state is correct but it is not | `helm upgrade` may silently overwrite manual changes or fail on conflict | Re-import resources into Helm state: `helm upgrade --force --set ...`; or adopt resources with Helm annotations |
| Multiple Helm releases managing the same Kubernetes resource | `helm install release-a` and `helm install release-b` both manage `ClusterRole/viewer`; uninstalling one deletes shared resource | `helm uninstall release-a` removes ClusterRole; release-b pods lose RBAC permissions | Surprise permission loss; cascading failures in release-b's workloads | Remove resource from one chart; use `helm.sh/resource-policy: keep` annotation on shared resources |
| Helm secret revision overflow (integer wraps or is manually deleted) | `helm history <REL>` shows missing revisions; `helm rollback <REL> <N>` fails with `not found` | Manual deletion of Helm secret; or secret storage backend corruption | Cannot roll back to missing revision; forced to forward-fix | Re-create release from GitOps source: `helm upgrade --install --reset-values`; restore desired state from Git |
| Helmfile environment variable drift between CI and production | `helm get values <REL> -n prod` differs from expected values in Helmfile; production running with stale config | Production values were never updated because CI used wrong environment flag | Silent misconfiguration; feature flags wrong; wrong resource limits in production | `helmfile --environment prod diff`; `helmfile --environment prod sync` to converge; audit all env-specific values |
| Release `namespace` field mismatch: resource deployed to wrong namespace | `helm list -A` shows release in `default`; actual pods running in `kube-system` | `helm install` run without `-n <NS>` flag; resources created in default namespace | RBAC, NetworkPolicy, and quota scoping broken; resources may conflict with system components | `helm uninstall <REL>` from wrong namespace; `helm install <REL> -n <CORRECT_NS>` with correct namespace flag |
| CRD version skew: chart installs v1beta1 CRD; cluster has v1 | `kubectl apply` of CRD fails silently or creates v1beta1 schema; controllers reject CRs with v1 fields | `kubectl get crd <NAME> -o yaml | grep versions` shows only v1beta1 | New CRs using v1 fields fail validation; controller errors on startup | Manually upgrade CRD: `kubectl replace -f crd-v1.yaml`; or use `kubectl apply --server-side` for CRD upgrade |
| Helm `--set` override persisted in release secret, overriding values.yaml in subsequent upgrades | `helm upgrade` without `--set` no longer applies the override, but previous override persists in release secret | `helm get values <REL> -n <NS>` shows unexpected override that is not in values.yaml | Operator believes values.yaml is source of truth, but release secret contains hidden overrides | Use `--reset-values` with explicit `--values` on next upgrade; audit release secrets: `helm get values --all <REL>` |
| Two ArgoCD apps managing same Helm release (split ownership) | ArgoCD detects drift and re-syncs competing apps; resources oscillate between two desired states | `argocd app list` shows both apps targeting same namespace/release name | Resources continuously updated; potential config thrashing; possible data-impacting restarts | Delete one ArgoCD app; designate single owner; add `argocd.argoproj.io/managed-by` labels consistently |
| Helm chart templating renders different manifests on different machines (locale/timezone issues) | `helm template` output differs between developer machine and CI; CI deploys unexpected resources | `helm diff` in CI shows changes that developer did not see locally | Unexpected production changes; resource mutations not visible in local testing | Pin Helm version and Go version in CI; use `helm template --strict`; review diff output before every deploy |
| Post-upgrade hook deletes production data assuming old schema | Hook Job runs `DROP TABLE legacy_*`; data loss occurs silently | Immediately post-upgrade when hook Job completes | `kubectl logs job/<HOOK_JOB_NAME> -n <NS>` shows deletion; correlate with upgrade timestamp | Restore from backup; audit all hook Job scripts before chart upgrades; use `helm.sh/hook-delete-policy: before-hook-creation` |

## Runbook Decision Trees

### Decision Tree 1: Helm Release Stuck / Cannot Upgrade or Rollback

```
Is helm status showing a stuck/failed state?
├── YES → check: helm status <RELEASE> -n <NS>
│         Is the state "pending-upgrade" or "pending-install"?
│         ├── YES → Is the Kubernetes API server reachable?
│         │         ├── NO  → K8s control plane issue; escalate to cluster admin with kubectl cluster-info
│         │         └── YES → Delete stuck revision secret:
│         │                   kubectl get secrets -n <NS> -l owner=helm,name=<RELEASE> --sort-by=.creationTimestamp
│         │                   kubectl delete secret sh.helm.release.v1.<RELEASE>.v<N> -n <NS>
│         │                   → Then: helm rollback <RELEASE> <LAST_GOOD_REV> -n <NS> --wait
│         └── Is the state "failed"?
│                   ├── YES → Check hook pod logs: kubectl get pods -n <NS> -l helm.sh/chart=<CHART>; kubectl logs <hook-pod> -n <NS>
│                   │         → Fix hook error; then: helm rollback <RELEASE> <PREV_REV> -n <NS>
│                   └── NO  → State is "uninstalling" → check finalizers: kubectl get all -n <NS> -l app.kubernetes.io/instance=<RELEASE>
│                             → Remove stuck finalizers: kubectl patch <resource> <name> -p '{"metadata":{"finalizers":[]}}' --type=merge
└── NO  → Is "helm upgrade" hanging (no response after 5+ minutes)?
          ├── YES → Check if previous upgrade is still running: kubectl get pods -n <NS> --watch
          │         → Is a hook pod looping or CrashLooping?
          │         ├── YES → Delete stuck hook pod; re-run upgrade with --no-hooks to bypass
          │         └── NO  → Check admission webhook latency: kubectl get validatingwebhookconfigurations; kubectl get mutatingwebhookconfigurations
          └── NO  → Escalate: provide helm history output, kubectl events, and APIServer audit logs
```

### Decision Tree 2: Helm Diff Shows Unexpected Drift from GitOps Source of Truth

```
Does helm diff show changes not in GitOps?
├── YES → Was there a manual kubectl apply or patch recently?
│         ├── YES → Identify who made the change: kubectl get events -n <NS>; check audit logs
│         │         → Reconcile: helm upgrade --install <RELEASE> <CHART> -n <NS> -f values.yaml
│         └── NO  → Is an operator or controller mutating resources?
│                   ├── YES → Identify mutating webhook: kubectl get mutatingwebhookconfigurations -o yaml | grep <resource>
│                   │         → Exclude operator-managed fields from Helm chart or add lifecycle hook
│                   └── NO  → Is ArgoCD/Flux in error state?
│                             ├── YES → check: kubectl get applications -n argocd; kubectl describe application <APP>
│                             │         → Fix sync error; force reconcile: argocd app sync <APP> --force
│                             └── NO  → CRD version drift → compare: kubectl get crd <CRD> -o jsonpath='{.spec.versions[*].name}'
│                                       → Re-apply CRD from chart: helm upgrade <RELEASE> <CHART> --set crds.install=true
└── NO  → helm diff shows no changes
          → Confirm deployed revision matches expected: helm history <RELEASE> -n <NS> | tail -1
          → Verify image tags match desired: kubectl get pods -n <NS> -o jsonpath='{.items[*].spec.containers[*].image}'
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Helm hook spawning unbounded Job pods on every upgrade | Large number of completed/failed Job pods accumulating; namespace resource quota exhausted | `kubectl get pods -n <NS> --field-selector=status.phase=Failed` — count hook pods | Namespace quota for pods exhausted; future upgrades fail | `kubectl delete pods -n <NS> --field-selector=status.phase=Failed`; set `"helm.sh/hook-delete-policy": hook-succeeded` | Add `hook-delete-policy: before-hook-creation,hook-succeeded` to all hook Job templates |
| Helm history accumulation filling etcd | etcd storage growing; `helm list` response time slow | `kubectl get secrets -A -l owner=helm | wc -l`; etcd `--quota-backend-bytes` alert | etcd quota exceeded → cluster-wide write freeze | `helm history <RELEASE> -n <NS>`; prune old revisions: `helm plugin install https://github.com/helm/helm-mapkubeapis` | Set `--history-max 10` in Helm config or per-release; prune regularly via CronJob |
| Helmfile sync running concurrently on multiple CI runners | Race condition causing partial upgrades; conflicting state in etcd | `kubectl get secrets -A -l owner=helm --sort-by=.creationTimestamp | tail -20` | Releases stuck in pending state; production partially upgraded | Cancel redundant CI jobs; manually resolve stuck secrets | Enforce single Helmfile runner via CI job mutex; use Helm `--atomic` flag |
| CRD installed by chart being deleted on chart uninstall | CRD deletion cascades to all instances cluster-wide | `helm uninstall <RELEASE> -n <NS> --dry-run 2>&1 | grep crd` | All CRD instances deleted cluster-wide; data loss possible | Stop helm uninstall immediately if in progress; restore CRDs from backup | Use `"helm.sh/resource-policy": keep` annotation on all CRDs in Helm chart templates |
| values.yaml with unbounded resource requests deployed to all namespaces | All nodes CPU/memory overcommitted; pod evictions cluster-wide | `kubectl get pods -A --field-selector=status.phase=Failed -o json | jq '.items[].status.containerStatuses[].lastState.terminated.reason'` | Cluster-wide resource starvation | `helm upgrade <RELEASE> <CHART> --set resources.requests.cpu=100m --set resources.requests.memory=128Mi` | Enforce LimitRange and ResourceQuota per namespace; validate resource limits in CI before deploy |
| Chart with PodDisruptionBudget blocking node drain during upgrade | Node drain hangs indefinitely; rolling upgrade stalls | `kubectl get pdb -A`; `kubectl describe pdb -n <NS> <PDB>` — check `Disruptions Allowed: 0` | Cluster node drain blocked; upgrades fail; maintenance window overrun | `kubectl delete pdb -n <NS> <PDB>` temporarily; complete drain; restore PDB | Set `minAvailable: 1` (not 100%) on PDBs; always test PDB settings in staging with node drain simulation |
| Secret values accidentally committed to Helm values in Git | Secrets exposed in Git history; compliance violation | `git log --all --full-history -- '**/values.yaml' | head -20`; grep for `password\|secret\|key` in values files | Credential exposure; compliance violation | Rotate all exposed credentials immediately; use `git filter-repo` to purge history | Use helm-secrets or Vault agent injector for secret values; never store secrets in values.yaml |
| Helm upgrade with `--force` recreating StatefulSet pods causing data loss | StatefulSet pods recreated instead of rolling updated; PVC data detached | `kubectl get events -n <NS> --sort-by=.lastTimestamp | grep "Deleted\|Killed"` | Data loss if PVC not retained; service downtime | Stop upgrade; verify PVC ReclaimPolicy is `Retain`; restore from backup if data lost | Never use `--force` on StatefulSet workloads; use targeted pod deletion instead |
| Chart repo served over HTTP caching stale chart versions | Outdated chart deployed silently; security patches missed | `helm search repo <chart> --versions`; compare with upstream release page | Running vulnerable versions of chart; silent version drift | `helm repo update`; `helm upgrade <RELEASE> <CHART> --version <LATEST>` | Use OCI registry for charts instead of HTTP chart repos; pin chart versions in Helmfile |
| Resource limit absent on chart causing node CPU/memory monopoly | Single Helm release consuming all node resources; co-located services evicted | `kubectl top pods -n <NS> --sort-by=cpu`; check values.yaml for missing `resources.limits` | Node resource exhaustion; evictions across all co-located services | `helm upgrade <RELEASE> <CHART> --set resources.limits.cpu=500m --set resources.limits.memory=512Mi` | Require non-empty `resources.limits` as chart linting CI gate; use OPA/Gatekeeper policy |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| etcd hot key from Helm secret churn — frequent upgrades updating same release secret | `helm upgrade` taking >30s; etcd latency p99 elevated | `kubectl exec -n kube-system etcd-<NODE> -- etcdctl endpoint status --write-out=table`; watch `etcd_server_proposals_applied_total` rate | High revision churn compressing same etcd key repeatedly | Set `--history-max 3` to limit revision secrets; batch upgrades |
| Helm connection pool exhaustion to API server | `helm list` hangs; `Error: Kubernetes cluster unreachable` | `kubectl top pods -n kube-system | grep kube-apiserver`; `kubectl get --raw /metrics | grep apiserver_current_inflight_requests` | Too many concurrent Helm operations exhausting kube-apiserver connections | Serialize Helm operations via CI mutex; limit concurrent `helm upgrade` across namespaces |
| Controller-manager GC pressure from Helm-created resource storm | Pod scheduling delays after large Helm deploy; `kube-controller-manager` CPU spike | `kubectl top pods -n kube-system`; `kubectl get --raw /metrics | grep workqueue_depth` | Creating hundreds of resources simultaneously overloads controller work queues | Use `--wait` with staggered rollouts; split large charts into sub-charts |
| Helm hook pre-upgrade thread pool saturation | `pre-upgrade` hook Job pods pending; upgrade stalls indefinitely | `kubectl get pods -n <NS> -l helm.sh/hook=pre-upgrade`; `kubectl describe pod <hook-pod> -n <NS>` | RBAC or quota prevents hook Job from scheduling; no capacity for hook pod | Free namespace resources; verify `ServiceAccount` for hook has correct RBAC |
| Slow `helm template` / `helm lint` from large chart with many dependencies | CI pipeline template/lint step takes >2 minutes | `time helm dependency build <CHART_DIR>` — measure download time; check `charts/` dir size | Downloading large chart dependencies on each CI run from slow chart repo | Cache `charts/` directory in CI; use OCI registry for faster dependency pulls |
| CPU steal on CI runner during Helm operations causing kubectl timeout | `helm upgrade` fails with `context deadline exceeded` in CI only | Compare wall-clock vs CPU time on runner; `kubectl config view` — ensure correct cluster URL | Overloaded shared CI runner CPU-stolen by other jobs; kubectl timeout expires | Increase `--timeout` flag; use dedicated runner for production deployments |
| Lock contention — multiple GitOps reconcile loops competing for same Helm release | Releases stuck in `pending-upgrade` state; no progress | `helm list -A | grep pending`; `kubectl get secrets -n <NS> -l owner=helm | grep pending` | Two ArgoCD/Flux reconcilers targeting same release simultaneously | Delete stuck secret: `kubectl delete secret -n <NS> <stuck-helm-secret>`; fix GitOps to single reconciler per release |
| `helm upgrade --install` serialization overhead from large rendered manifest | `helm upgrade` takes minutes for chart with 500+ resources | `helm template <RELEASE> <CHART> | wc -l` — measure template size; `time helm upgrade ...` | Kubernetes API server serializing large manifest batch; admission webhooks slow | Split chart; reduce resource count per chart; use `--atomic` only for critical services |
| Helmfile sync batch size causing API server overload | API server error rate spikes during `helmfile sync`; pod scheduling latency | `kubectl get --raw /metrics | grep apiserver_request_total | grep 429` — look for throttling | Helmfile applying all releases concurrently without concurrency limit | Add `--concurrency 3` to `helmfile sync`; use `--selector` for staged deployments |
| Downstream dependency latency from slow Helm chart repo | `helm repo update` or `helm pull` takes >60s; CI bottleneck | `time helm repo update`; `curl -w "%{time_total}" -o /dev/null <CHART_REPO_URL>/index.yaml` | Remote chart repo slow or rate-limiting | Switch to OCI registry: `helm push <chart>.tgz oci://<REGISTRY>`; cache chart artifacts in CI |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| Helm chart repo TLS cert expiry | `helm repo update` fails: `x509: certificate has expired or is not yet valid` | `curl -vI <CHART_REPO_URL> 2>&1 | grep "expire date\|SSL certificate"` | All `helm pull` and `helm repo update` fail; CI pipelines blocked | Switch to OCI registry or mirror; update repo TLS cert; workaround: `helm repo add --insecure-skip-tls-verify` (dev only) |
| Kubernetes API server TLS cert rotation breaking `helm` kubectl context | `helm` commands fail: `x509: certificate signed by unknown authority` | `kubectl config view --minify | grep server`; `openssl s_client -connect <API_SERVER>:6443 -showcerts` | All Helm operations fail cluster-wide | Update kubeconfig: `aws eks update-kubeconfig` or `gcloud container clusters get-credentials`; rotate CA bundle |
| DNS resolution failure for chart repository hostname | `helm repo update` fails: `dial tcp: lookup <REPO_HOST>: no such host` | `dig <REPO_HOST>` — expect IP; `nslookup <REPO_HOST>` | Helm chart pulls fail; CI/CD pipelines blocked | Fix DNS or update `/etc/hosts` on runner; use IP-based URL temporarily; switch to local mirror |
| OCI registry TCP connection exhaustion during parallel chart pulls | CI jobs fail with `connection reset by peer` when pulling charts concurrently | `ss -s` on CI runner — watch TIME-WAIT count; `docker system df` | Parallel Helm chart pulls fail; CI pipeline flapping | Serialize chart pulls; increase ephemeral port range: `sysctl -w net.ipv4.ip_local_port_range="1024 65535"` |
| kube-apiserver load balancer dropping long-lived Helm `--wait` connections | `helm upgrade --wait` fails with `connection reset` after LB idle timeout (e.g. 60s) | `kubectl get events -n <NS> | grep "Liveness\|timeout"`; compare LB idle timeout with `--timeout` setting | Cloud LB idle connection timeout shorter than Helm `--wait` period | Add keepalive annotation to LB; use `--timeout 10m` with LB idle set to 15m |
| Packet loss between CI runner and kube-apiserver causing kubectl retries | `helm upgrade` takes 3x normal time; kubectl retries visible in `--debug` output | `helm upgrade --debug <RELEASE> <CHART> 2>&1 | grep "retry\|timeout"`; `ping -c 100 <API_SERVER_IP>` | Helm operations succeed but slowly; unstable CI timing | Move CI runner to same network as kube-apiserver; verify firewall rules for TCP 6443 |
| MTU mismatch between CI runner and Kubernetes cluster | Large manifests fail to transmit; truncated responses from API server | `ping -M do -s 1400 <API_SERVER_IP>` — if fails, MTU too large; `ip link show` on runner | `helm upgrade` of charts with large manifests fails silently | Align MTU: `ip link set eth0 mtu 1450` on CI runner; verify with `ip route show` |
| Firewall change blocking webhook admission during Helm deploy | `helm upgrade` fails: `Internal error occurred: failed calling webhook`; webhook timeout | `kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o yaml | grep -A2 clientConfig` | All Helm deployments that trigger admission webhook fail | Verify firewall allows TCP from kube-apiserver to webhook service; or temporarily patch webhook `failurePolicy: Ignore` |
| SSL handshake timeout to OCI registry during chart push/pull in air-gap | `helm push` fails: `tls: no reroute to localhost`; helm pull returns timeout | `openssl s_client -connect <OCI_REGISTRY>:443 -showcerts`; check CA bundle | Air-gap environment lacks CA chain for private OCI registry | Add private CA to system trust store: `update-ca-certificates`; configure `helm env HELM_REGISTRY_CONFIG` |
| Connection reset from chart repo during `helm install` mid-stream | Helm returns `Error: failed to install CRD`; partial CRD installation | `kubectl get crd | grep <chart>` — partial list; `helm status <RELEASE> -n <NS>` shows failed | CRDs partially installed; Helm release in failed state | `helm uninstall <RELEASE> -n <NS>`; manually delete partial CRDs; reinstall on stable network |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| etcd storage quota exhaustion from Helm revision secret accumulation | `helm upgrade` fails: `etcdserver: mvcc: database space exceeded`; cluster write freeze | `kubectl exec -n kube-system etcd-<NODE> -- etcdctl endpoint status --write-out=table | grep dbSize`; `kubectl get secrets -A -l owner=helm | wc -l` | Defragment etcd: `kubectl exec etcd-<NODE> -n kube-system -- etcdctl defrag`; delete old Helm secrets | Set `--history-max 5` globally; schedule monthly Helm history pruning CronJob |
| Kubernetes API server memory exhaustion from large Helm-managed ConfigMap | kube-apiserver OOMKilled; all kubectl commands fail | `kubectl top pods -n kube-system | grep apiserver`; `kubectl get configmap -A -o json | jq '.items | max_by(.data | to_entries | map(.value | length) | add)'` | Reduce large ConfigMap data; split into multiple smaller ConfigMaps; reduce values.yaml embedded data size | Enforce `maxDataSize` check in CI chart linting; store large config in an external secret store |
| Namespace pod quota exhaustion from Helm hook pods not cleaning up | `helm upgrade` fails: `exceeded quota: pods`; hook Job pods fill namespace quota | `kubectl get pods -n <NS> | grep Completed | wc -l`; `kubectl describe quota -n <NS>` | Delete completed hook pods: `kubectl delete pods -n <NS> --field-selector=status.phase=Succeeded` | Add `hook-delete-policy: hook-succeeded,hook-failed` to all hook annotations |
| CI runner disk exhaustion from cached Helm chart archives | CI runners return `no space left on device`; chart builds fail | `du -sh ~/.helm/cache/archive/*` — check Helm chart cache size on runner | Clear Helm cache: `rm -rf ~/.helm/cache/archive/*`; `helm repo remove` unused repos | Add Helm cache cleanup step to CI post-build; limit runner disk allocation |
| CPU throttle on Helm operator/reconciler pod exceeding CGroup limit | Helm operator (e.g., Helmfile controller) misses reconcile deadlines; releases drift | `kubectl top pods -n flux-system | grep helm-controller`; `cat /sys/fs/cgroup/cpu/kubepods/pod<POD_UID>/cpu.stat | grep throttled` | Helm controller CPU limit too low for cluster scale | Increase `resources.limits.cpu` on helm-controller pod; monitor with `container_cpu_cfs_throttled_seconds_total` |
| Webhook handler memory OOM from large rendered manifests | Validating webhook OOMKilled during large Helm chart deploy | `kubectl logs -n <WEBHOOK_NS> <WEBHOOK_POD> --previous | grep OOM`; `kubectl get events -n <NS> | grep FailedCreate` | Increase webhook pod memory limit; reduce chart manifest size | Right-size webhook pod memory; profile peak memory during largest chart install |
| kube-apiserver goroutine leak from unanswered Helm `--wait` watches | API server goroutine count climbing; response latency increasing over time | `kubectl get --raw /metrics | grep go_goroutines`; correlate with Helm `--wait` operations | Helm `--wait` leaves open watch connections that never close on timeout | Always pair `--wait` with `--timeout`; ensure Helm process terminates cleanly |
| Ephemeral storage exhaustion on node from Helm-generated large log-volume containers | Node evicts all pods due to ephemeral storage limit; nodes report `DiskPressure` | `kubectl describe nodes | grep "DiskPressure\|ephemeral-storage"` | Helm chart missing `ephemeralStorage` limits; containers writing unbounded logs to emPtyDir | Add `resources.limits.ephemeral-storage` to all chart templates; configure log rotation |
| Network socket buffer exhaustion on node serving Helm webhook traffic | Webhook calls queued; admission denied due to timeout | `ss -lnt | grep :9443`; `cat /proc/net/sockstat | grep sockets` | High concurrent Helm deploys each triggering admission webhook simultaneously | Increase `net.core.rmem_max` and `net.core.wmem_max`; rate-limit concurrent Helm operations |
| ServiceAccount token secret exhaustion (legacy tokens filling etcd) | etcd size growing from auto-created SA token secrets; `kubectl get secrets -A | wc -l` large | `kubectl get secrets -A --field-selector type=kubernetes.io/service-account-token | wc -l` | Delete unused legacy SA secrets; disable auto-mounting: `automountServiceAccountToken: false` in chart templates | Upgrade to bound SA tokens (Kubernetes 1.24+); set `automountServiceAccountToken: false` by default in charts |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Helm upgrade idempotency violation — concurrent upgrades overwriting each other | Release stuck in `pending-upgrade`; deployed resources reflect mix of old and new values | `helm list -A | grep pending`; `kubectl get secrets -n <NS> -l owner=helm --sort-by=.creationTimestamp` | Release in indeterminate state; running pods mix old/new image versions | Delete pending secret: `kubectl delete secret -n <NS> sh.helm.release.v1.<RELEASE>.v<N>`; rollback: `helm rollback <RELEASE> -n <NS>` |
| Saga partial failure — Helm upgrade completing some namespaces but failing others in Helmfile | Some namespaces updated; others running old chart version; inconsistent cluster state | `helm list -A -o json | jq '.[] | select(.chart=="<CHART>") | {name, namespace, app_version}'` | Services across namespaces running incompatible API versions | Identify failed namespaces; run targeted `helmfile sync --selector namespace=<NS>` to converge |
| Helm hook replay causing duplicate Job execution on upgrade retry | `pre-upgrade` hook Job runs twice on retry; creates duplicate database migrations or seed data | `kubectl get jobs -n <NS> -l helm.sh/hook`; check Job completion count vs expected | Double migration; duplicate data in database; potential data corruption | Delete completed hook Job before retry: `kubectl delete job -n <NS> <hook-job>`; add idempotency check in Job scripts |
| Cross-service deadlock — Helm post-upgrade hook waiting on resource created by another in-progress Helm release | Hook pod stuck in `Init:0/1`; init container waiting for service that is itself being upgraded | `kubectl describe pod -n <NS> <hook-pod>`; check `initContainers[].state`; `helm list -A | grep pending` | Deadlock between two Helm releases; both `--wait` indefinitely | Cancel one upgrade: `helm rollback <RELEASE_B> -n <NS_B>`; restart remaining upgrade after dependency resolves |
| Out-of-order ArgoCD sync applying old Helm chart revision after new one deployed | ArgoCD sync wave delivers old chart template after new; pods flip-flop between image versions | `kubectl rollout history deployment/<DEPLOY> -n <NS>`; `argocd app history <APP>` | Continuous pod restarts; service instability; confusing deployment state | Disable ArgoCD auto-sync temporarily; apply correct chart version; re-enable sync pointing to correct revision |
| At-least-once Helm install duplicate — GitOps controller retrying already-succeeded install | Release shows multiple `v1` secrets; two copies of each resource created | `kubectl get secrets -n <NS> -l owner=helm | grep "v1"` (multiple with same release); `helm list -A | grep failed` | Duplicate Kubernetes resources (Services, Deployments) causing selector conflicts | Delete duplicate release secrets; `helm uninstall` one; clean orphaned resources |
| Compensating rollback failure — `helm rollback` failing due to deleted CRD | `helm rollback` errors: `no kind "CustomResource" is registered`; cannot restore previous state | `helm rollback --dry-run <RELEASE> <REV> -n <NS> 2>&1 | grep "no kind\|unrecognized"` | Previous release state unrestorable via Helm; manual recovery needed | Reinstall CRD from previous chart version; then retry `helm rollback`; if not possible, `helm upgrade` forward |
| Distributed lock expiry mid-Helm upgrade — Flux helm-controller lease lost | Helm controller reconcile interrupted; release left in `pending-upgrade` | `kubectl get lease -n flux-system`; `kubectl logs -n flux-system -l app=helm-controller | grep "lock\|lease\|context"` | Release stuck; subsequent reconciles blocked until stuck state cleared | Delete stuck Helm release secret; force Flux reconcile: `flux reconcile helmrelease <NAME> -n <NS>` |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — Helm-deployed batch job consuming all namespace CPU quota | `kubectl top pods -n <NS>`; `rate(container_cpu_usage_seconds_total{namespace="<NS>"}[5m])` dominated by one release | Other deployments in namespace CPU-throttled; latency spikes for user-facing services | `kubectl delete deployment <BATCH_RELEASE> -n <NS>` or `kubectl scale deployment <NAME> -n <NS> --replicas=0` | Add `resources.limits.cpu` to chart values; enforce namespace LimitRange in chart prerequisites |
| Memory pressure from adjacent Helm release — large JVM heap consuming node memory | `kubectl top nodes` — specific node memory pressure; `kubectl describe node <NODE> | grep "Non-terminated Pods"` | Pods on same node evicted; PVCs may lose mounted volumes | `kubectl cordon <NODE>`; `kubectl drain <NODE> --ignore-daemonsets`; evict the large pod | Enforce `resources.limits.memory` in chart; use `pod.spec.topologySpreadConstraints` to spread across nodes |
| Disk I/O saturation from Helm-managed PVC — one release writing heavily to shared storage class | `kubectl get pvc -A`; storage class IOPS metrics in cloud console; `iostat -x 1 5` on node | Other releases sharing same storage class experience slow PVC I/O | `kubectl scale deployment <HEAVY_RELEASE> -n <NS> --replicas=0` | Use separate StorageClass per tenant with IOPS limits; add `storageIOPS` annotation to chart PVC templates |
| Network bandwidth monopoly — Helm chart deploying high-throughput data transfer pod on shared node | `iftop -i eth0` on node; `kubectl exec -n <NS> <POD> -- ss -ti | grep throughput`; cloud network metrics | All pods on node experience network degradation; external request latency | Add `pod.spec.bandwidth.egress` annotation (if CNI supports it); move pod to dedicated node | Add NetworkPolicy bandwidth annotations; use `node.kubernetes.io/bandwidth` label to isolate network-intensive workloads |
| Connection pool starvation — Helm release holding all database connections from shared connection pool | `kubectl exec -n <DB_NS> <DB_POD> -- psql -c "SELECT count(*), application_name FROM pg_stat_activity GROUP BY application_name"` | Other services receive `too many connections`; all DB-dependent services fail | `helm upgrade <OFFENDING_RELEASE> -n <NS> --reuse-values --set db.pool.max=5` to reduce pool size | Enforce `maxConnections` per release in chart values; deploy PgBouncer as connection proxy with per-tenant limits |
| Quota enforcement gap — Helm release deploying in namespace without ResourceQuota | `kubectl describe namespace <NS>` — no ResourceQuota present; `kubectl top pods -n <NS>` shows uncapped resource use | Releases in quota-enforced namespaces cannot deploy; uncapped namespace consumes disproportionate cluster resources | `kubectl create quota <NS>-quota -n <NS> --hard=cpu=4,memory=8Gi,pods=20` | Add ResourceQuota and LimitRange as mandatory Helm chart pre-requisite or include in chart `templates/` |
| Cross-tenant data leak risk — Helm chart creating shared ConfigMap readable across namespaces | `kubectl get configmap <NAME> -n default -o yaml | grep -i "password\|secret\|token"` — check for secrets in ConfigMaps accessible across namespaces | Tenant B can read Tenant A's configuration including embedded credentials from shared namespace ConfigMap | `kubectl delete configmap <NAME> -n default`; recreate in tenant-specific namespace | Enforce chart linting rule: no secrets in ConfigMaps; use Kubernetes Secrets with RBAC; run `conftest` policy checks in CI |
| Rate limit bypass — Helm chart deploying ingress without rate limiting annotations causing tenant to consume all ingress capacity | `kubectl exec -n ingress-nginx <POD> -- curl -s http://localhost:10254/metrics | grep nginx_ingress_controller_requests` dominated by one namespace | Other tenants' ingress routes experience increased latency; shared ingress controller saturated | Add rate limit annotation: `kubectl annotate ingress <NAME> -n <NS> nginx.ingress.kubernetes.io/limit-rps=100` | Include rate-limit annotations as defaults in ingress chart template; enforce via OPA/Gatekeeper policy |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Helm release metric scrape failure — Prometheus ServiceMonitor references deleted Service | `helm_release_*` metrics absent in Prometheus; `up{job="helm-exporter"}==0` | Helm chart upgrade changed Service name; ServiceMonitor label selector no longer matches | `kubectl get servicemonitor -n <MONITORING_NS> -o yaml | grep selector`; compare with `kubectl get svc -n <NS> --show-labels` | Update ServiceMonitor label selector to match new Service labels; add Prometheus alert on `up==0` for all Helm-exported jobs |
| Trace sampling gap — Helm hook Jobs not instrumented causing blind spot during upgrade operations | Failed upgrade hooks not traced; upgrade failures have no trace; only hook pod logs available | Hook Jobs are short-lived; no distributed tracing instrumented in hook containers | `kubectl logs -n <NS> <HOOK_POD> --previous` manually; check `helm history <RELEASE>` status | Add OpenTelemetry sidecar to hook Job templates; emit structured log events with correlation ID |
| Log pipeline silent drop — Helm-managed Fluentd DaemonSet misconfigured after chart upgrade | Application logs missing from ELK/Splunk for specific nodes; no error surfaced | Fluentd config change in chart upgrade broke `<source>` path match for new log format | `kubectl logs -n logging <FLUENTD_POD>` — check for parse errors; compare log count in ELK vs `kubectl logs <APP_POD>` | Add Fluentd `@ERROR` label handler with alerting; add integration test in CI that verifies log flow after chart upgrade |
| Alert rule misconfiguration — `kube_deployment_spec_replicas != kube_deployment_status_replicas` alert silenced after Helm upgrade renamed deployment | Deployment renamed by chart; old alert references old deployment name; mismatch goes undetected | Helm chart refactored deployment name in new version; Prometheus alert not updated | `kubectl get deployment -A | grep <EXPECTED_NAME>`; manually check `kube_deployment_status_replicas` in Prometheus | Version alert rules alongside chart versions in same Git repo; add CI step that validates alert expressions against current resource names |
| Cardinality explosion — Helm chart upgrade adding unique `release_revision` label to all metrics | Prometheus tsdb head grows after every `helm upgrade`; dashboards time out; scrape duration grows | Chart version added `helm.sh/chart: <name>-<version>` as Prometheus label causing new series per upgrade | `kubectl exec -n monitoring prometheus-<POD> -- promtool tsdb analyze /prometheus`; identify exploding label series | Remove high-cardinality labels from Prometheus metric definitions in chart; use recording rules to drop revision labels |
| Missing Helm release health endpoint — Helm `--wait` completing but service not functionally healthy | Helm upgrade returns success; monitoring shows no issue; users experience errors | Helm `--wait` only checks pod `Ready` state, not application-level health; no functional probe | `kubectl exec -n <NS> <POD> -- curl -s http://localhost/health` — test application health endpoint manually | Add custom Helm test: `helm test <RELEASE> -n <NS>`; implement `helm.sh/hook: test` Job that validates functional endpoint |
| Instrumentation gap — Helm chart not exposing metrics for init containers running critical schema migrations | Schema migration failures invisible to Prometheus; alert only fires when app container fails | Init containers complete before Prometheus scrape; metrics emitted during init not captured | `kubectl logs -n <NS> <POD> -c <INIT_CONTAINER>` for migration output; add structured log output | Use Kubernetes Job instead of init container for migrations; emit metrics via Prometheus pushgateway from Job |
| Alertmanager outage during Helm rollout — webhook delivery failures causing silent alert loss | Helm upgrade of alertmanager chart itself causes notification gap; PagerDuty incidents not created | Alertmanager pod in `pending-upgrade` or restart loop during chart upgrade | Check Prometheus alerts directly: `http://prometheus:9090/alerts`; check `helm status alertmanager -n monitoring` | Implement dead-man's-switch Watchdog alert routed to independent notifier; blue-green deploy alertmanager upgrades |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor Helm chart version upgrade rollback — new chart values schema breaking existing overrides | `helm upgrade` fails: `Error: values don't meet the specifications of the schema`; no pods restarted | `helm upgrade --dry-run <RELEASE> <CHART>:<NEW_VERSION> -f values.yaml 2>&1 | grep "Error"` | `helm rollback <RELEASE> <PREVIOUS_REVISION> -n <NS>`; verify: `helm status <RELEASE> -n <NS>` | Run `helm upgrade --dry-run` in CI before merging chart version bump; validate schema compatibility |
| Schema migration partial completion — Helm hook Job completes migration on primary DB but fails on replica | Primary DB schema updated; replica still on old schema; read replicas return errors for new schema queries | `kubectl logs -n <NS> <MIGRATION_JOB_POD>`; connect to replica: check schema version table | Re-run migration hook Job targeting replica specifically; do not rollback primary until replica is in sync | Design migrations as idempotent; use `helm test` Job to verify all DB replicas are at expected schema version post-upgrade |
| Rolling upgrade version skew — multiple Helm releases sharing a CRD where new version introduces breaking change | New pods using updated CRD; old pods failing to parse CRD responses; mix of behaviors in cluster | `kubectl get crd <CRD_NAME> -o yaml | grep "storedVersions"`; `helm list -A | grep <CHART>` across all namespaces | Halt upgrade; `helm rollback` newest namespace; ensure all namespaces on same chart version before CRD upgrade | Upgrade CRD via separate Helm chart (`crds/` directory); gate application chart upgrade on CRD version compatibility check |
| Zero-downtime upgrade gone wrong — `helm upgrade --atomic` rollback killing healthy pods mid-traffic | Users experience errors during automatic rollback triggered by single failing pod; rollback causes more disruption than issue | `helm status <RELEASE> -n <NS>` shows `failed`; `kubectl get events -n <NS> | grep "Rolling back"` | Let `--atomic` complete rollback; verify `helm status` shows previous revision; check pod health | Use `--timeout 10m` with `--atomic` only for non-critical upgrades; prefer manual rollback control via `helm rollback` for production |
| Config format change — Helm chart upgrades nginx.conf template breaking existing `nginx.ingress.kubernetes.io/` annotations | After chart upgrade, Ingress annotations no longer rendered; routes silently ignored | `kubectl exec -n ingress-nginx <POD> -- cat /etc/nginx/nginx.conf | grep <EXPECTED_CONFIG>`; diff against pre-upgrade config | `helm rollback ingress-nginx <PREV_REVISION> -n ingress-nginx`; `kubectl rollout status deployment/ingress-nginx-controller -n ingress-nginx` | Capture `kubectl exec <POD> -- nginx -T > pre-upgrade.conf` before upgrade; diff with `post-upgrade.conf` in upgrade runbook |
| Data format incompatibility — Helm chart upgrade changes ConfigMap data structure read by application | App pods crash with `KeyError` or `NullPointerException` after ConfigMap schema changed by chart upgrade | `kubectl get configmap <NAME> -n <NS> -o yaml`; compare `data:` keys with application expectations; `kubectl logs <CRASHING_POD> -n <NS>` | `helm rollback <RELEASE> <PREV_REV> -n <NS>` to restore old ConfigMap schema | Pin ConfigMap key names in chart; use `helm diff upgrade` to preview ConfigMap changes before applying |
| Feature flag rollout — Helm chart enabling new service mesh integration causing existing services to lose connectivity | After chart upgrade enabling mTLS sidecar injection, services in non-annotated namespaces fail to communicate | `kubectl get pods -n <NS> -o json | jq '.items[].spec.containers[].name'` — check for unexpected `istio-proxy` injection | Disable injection: `kubectl label namespace <NS> istio-injection-`; rollback chart: `helm rollback <RELEASE>` | Use feature flags via values: `serviceMesh.enabled: false` default; test mesh integration in isolated namespace first |
| Dependency version conflict — Helm chart upgrading Kubernetes API version in `Chart.yaml` `kubeVersion` breaking air-gap installs | `helm install` fails: `chart requires kubeVersion: >=1.25.0` but cluster is 1.24; upgrade blocked | `helm show chart <REPO>/<CHART>:<NEW_VERSION> | grep kubeVersion`; `kubectl version --short` | Pin chart to previous version: `helm upgrade <RELEASE> <CHART>:<PREV_VERSION> -n <NS>`; upgrade cluster first | Document Kubernetes version prerequisites in chart CHANGELOG; validate `kubeVersion` constraint in CI before merging |


## Kernel/OS & Host-Level Failure Patterns
**Minimum cross-cutting cases to evaluate here:** OOM killer false kill, inode exhaustion, CPU steal, NTP skew affecting locks, leases, or coordination, file descriptor exhaustion, and TCP conntrack table saturation.


| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|------------------|--------------|-------------|----------------------|
| OOM killer terminates helm-controller or flux-helm-controller pod | `dmesg | grep -i "oom\|killed process" | grep helm` on the node hosting the controller | helm-controller JVM/Go runtime consuming more memory than pod limit during large chart reconcile | Helm releases not reconciled; GitOps drift accumulates silently | Increase helm-controller memory limit: `kubectl patch deployment helm-controller -n flux-system -p '{"spec":{"template":{"spec":{"containers":[{"name":"manager","resources":{"limits":{"memory":"512Mi"}}}]}}}}'` |
| Inode exhaustion on node from Helm chart cache | `df -i /var/lib/docker` or `df -i /run/containerd` on node running helm-controller | Helm chart extract operations creating many small files in temp directories; container layer caches | New pods cannot be scheduled on node; `no space left on device` for file operations | `crictl rmi --prune` on affected node; `kubectl drain <NODE> --ignore-daemonsets`; clean `/tmp/helm-*` directories |
| CPU steal spike degrading helm-controller reconcile performance | `sar -u 1 10 | awk 'NR>3{print $9}'` on node hosting helm-controller; `%steal` >10% | Hypervisor overcommit on shared VM host running Kubernetes control plane workloads | Helm reconcile loops slow; `HelmRelease` objects remain `Reconciling` longer than timeout | Migrate helm-controller to dedicated node with `nodeSelector: node-role.kubernetes.io/control-plane: ""`; or pin to dedicated VM tier |
| NTP clock skew causing Helm release timestamp comparison failures | `kubectl exec -n flux-system <helm-controller-pod> -- date` vs `date` on node; check `chronyd tracking` on node | NTP sync failure on node; Helm release `lastApplied` timestamp diverges from cluster time | Helm controller may re-apply already-applied releases; or skip releases based on stale time comparison | `chronyc makestep` on affected node; `systemctl restart chronyd`; verify: `chronyc tracking | grep "System time"` |
| File descriptor exhaustion on Kubernetes API server from Helm watch connections | `ls /proc/$(pgrep kube-apiserver)/fd | wc -l` approaching `ulimit -n`; API server logs `too many open files` | Each `helm upgrade --wait` opens a Kubernetes watch; many concurrent Helm operations exhaust API server FDs | Kubernetes API server becomes unresponsive; all kubectl/helm commands fail cluster-wide | Increase OS FD limit for kube-apiserver: `echo "* soft nofile 1048576" >> /etc/security/limits.conf`; restart API server node |
| TCP conntrack table full from Helm webhook admission traffic | `dmesg | grep "nf_conntrack: table full"` on node running admission webhook | High-frequency Helm deployments generating many short-lived TCP connections to validating/mutating webhooks | New Helm releases rejected at network level; `context deadline exceeded` in Helm output | `sysctl -w net.netfilter.nf_conntrack_max=524288`; persist in `/etc/sysctl.d/99-helm.conf`; reduce with shorter `nf_conntrack_tcp_timeout_time_wait` |
| Kernel panic / node crash killing nodes running Helm-managed StatefulSet leaders | `kubectl get nodes` shows node `NotReady`; `kubectl describe node <NODE> | grep "KernelDeadlock\|NotReady"` | Hardware fault or kernel OOM panic on node hosting StatefulSet pods | StatefulSet pods on crashed node stuck in `Terminating`; no new pod scheduled until `--pod-eviction-timeout` expires | Force delete stuck pods: `kubectl delete pod <POD> -n <NS> --force --grace-period=0`; drain and replace node |
| NUMA memory imbalance on node causing Go GC pressure in helm-controller | `numastat -p $(pgrep helm-controller)` shows imbalanced `numa_miss`; helm-controller GC pause metrics elevated | Go runtime allocating across NUMA boundaries on multi-socket nodes | helm-controller reconcile latency increases; `HelmRelease` reconcile intervals exceed configured timeout | Add `topologySpreadConstraints` to helm-controller pod to schedule on single-socket node; set `GODEBUG=madvdontneed=1` env var |

## Deployment Pipeline & GitOps Failure Patterns
**Minimum cross-cutting cases to evaluate here:** image pull failure (rate limit or auth), Helm drift, ArgoCD sync stuck, PodDisruptionBudget-blocked rollout, blue-green cutover failure, and ConfigMap or Secret drift.


| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Image pull rate limit for Helm chart OCI image from DockerHub | `helm pull oci://registry-1.docker.io/<CHART> --version <V>` returns `429 Too Many Requests` | `helm pull oci://registry-1.docker.io/<CHART> --version <V> 2>&1 | grep "429\|rate"` | Switch to authenticated pull: `helm registry login registry-1.docker.io -u <USER> -p <TOKEN>`; then retry | Mirror all OCI charts to private registry (ECR/GCR); configure `helm-controller` to use mirrored URL |
| Image pull auth failure for private Helm chart OCI registry | `HelmChart` object status: `pull failed: unauthorized`; `kubectl describe helmchart -n flux-system <NAME>` | `kubectl describe helmchart -n flux-system <NAME> | grep "authentication\|unauthorized"` | Recreate OCI registry secret: `kubectl create secret docker-registry oci-creds -n flux-system --docker-server=<REG> --docker-username=<U> --docker-password=<T>`; patch HelmRepository | Rotate registry credentials before expiry; use IRSA/Workload Identity for OCI registry auth |
| Helm chart drift — `helm diff` shows values diverged from Git source | `flux get helmreleases -A` shows `False` in READY column; `kubectl describe helmrelease <NAME> -n <NS>` shows drift message | `helm diff upgrade <RELEASE> <REPO>/<CHART> -f values.yaml -n <NS>` | Force Flux reconcile: `flux reconcile helmrelease <NAME> -n <NS> --with-source` | Enable Flux `remediation.retries` and `remediation.remediateLastFailure: true` in HelmRelease spec |
| ArgoCD sync stuck — Helm chart ApplicationSet with wave sync ordering deadlocked | ArgoCD application in `OutOfSync` state indefinitely; `argocd app sync <APP>` returns `ComparisonError` | `argocd app get <APP> -o json | jq '.status.conditions'`; `kubectl get application -n argocd <APP> -o yaml | grep "syncError"` | Hard refresh: `argocd app get <APP> --hard-refresh`; if stuck: `argocd app sync <APP> --force` | Add health checks to all Helm chart resources; configure ArgoCD `syncOptions: - CreateNamespace=true` to avoid ordering issues |
| PodDisruptionBudget blocking Helm rolling upgrade | `kubectl rollout status deployment/<DEPLOY> -n <NS>` stalls; `kubectl describe pdb <PDB> -n <NS>` shows `0 disruptions allowed` | `kubectl get pdb -n <NS> -o json | jq '.items[] | {name:.metadata.name, allowed:.status.disruptionsAllowed}'` | Temporarily patch PDB: `kubectl patch pdb <PDB> -n <NS> -p '{"spec":{"minAvailable":1}}'`; complete rollout; restore | Set PDB `minAvailable` to `replicaCount - 1` in chart values rather than hardcoded `maxUnavailable: 0` |
| Blue-green traffic switch failure — Helm chart deploying new Deployment but Ingress selector not updated | New pod version running but traffic still routed to old pods; no Helm error surface | `kubectl get ingress <NAME> -n <NS> -o yaml | grep selector`; `kubectl get endpoints <SVC> -n <NS>` | Manually patch Service selector: `kubectl patch service <SVC> -n <NS> -p '{"spec":{"selector":{"version":"v2"}}}'`; update chart values | Implement blue-green as explicit chart values toggle: `blueGreen.activeSlot: blue`; validate in CI |
| ConfigMap/Secret drift — `helm upgrade --reuse-values` missing new required key | Application crashes with missing environment variable after upgrade; ConfigMap missing new key | `helm get values <RELEASE> -n <NS>`; `helm diff upgrade <RELEASE> <CHART> -f new-values.yaml -n <NS>` | `helm upgrade <RELEASE> <CHART> -f complete-values.yaml -n <NS>` with full values file | Never use `--reuse-values` in CI; always pass explicit `-f values.yaml`; add chart `required` function for mandatory values |
| Feature flag stuck — `helm upgrade --set featureFlags.newUI=true` not reflected in running ConfigMap | Feature flag change applied in Helm values but pod not restarted; old ConfigMap value still in effect | `kubectl get configmap <NAME> -n <NS> -o jsonpath='{.data.feature_flags}'`; compare with `helm get values <RELEASE>` | Force pod restart: `kubectl rollout restart deployment/<DEPLOY> -n <NS>` | Add ConfigMap checksum annotation to Deployment pod template so ConfigMap changes trigger pod restart |

## Service Mesh & API Gateway Edge Cases
**Minimum cross-cutting cases to evaluate here:** circuit breaker false positives, rate limiting on legitimate traffic, stale service discovery endpoints, mTLS rotation interruption, retry storm amplification, gRPC keepalive or max-message failures, and trace context loss.


| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive on Helm admission webhook | `kubectl describe webhookconfiguration <HELM_WEBHOOK>` shows `FailurePolicy: Fail`; Envoy circuit breaker trips on webhook service | Helm validating webhook service experiencing GC pause; Envoy circuit breaker trips on momentary latency spike | All Helm releases rejected by admission control; deployment pipeline halted | `kubectl patch validatingwebhookconfiguration <NAME> -p '{"webhooks":[{"name":"<WEBHOOK>","failurePolicy":"Ignore"}]}'` temporarily; scale webhook deployment; tune circuit breaker threshold |
| Rate limit hitting legitimate Helm CI/CD traffic | Helm upgrade pipeline returns `429`; `kubectl logs -n istio-system <INGRESSGATEWAY> | grep "429\|rate_limited"` | Istio rate limit EnvoyFilter misconfigured with low global limit; burst of CI deploys triggers it | CI/CD pipeline blocked; Helm upgrades queued; GitOps drift accumulates | Increase rate limit for CI/CD service account IP range: `kubectl edit envoyfilter helm-ratelimit -n istio-system`; add IP whitelist for CI runners |
| Stale service discovery — Helm-upgraded Service endpoints not updated in Envoy EDS | Old pod IPs still in Envoy endpoint cache after `helm upgrade`; traffic routed to terminated pods | Envoy EDS cache lag after pod replacement during Helm rolling update; `terminationGracePeriodSeconds` too short | Request errors to pods that have already terminated; appears as intermittent 502/503 | `istioctl proxy-config endpoints <CLIENT_POD> -n <NS> | grep <SVC>`; force EDS refresh: `istioctl proxy-config cluster --fqdn <SVC> -n <NS>` |
| mTLS rotation breaking Helm hook Job connections | Helm pre-upgrade Job fails with `x509: certificate signed by unknown authority` | Istio certificate rotation happening simultaneously with Helm hook execution; hook pod gets new cert, target service still using old | Helm upgrade fails; `helm rollback` required; underlying issue persists until cert rotation completes | Set `hook-delete-policy: before-hook-creation` and retry: `helm upgrade --force`; or add `initContainer` delay to wait for cert propagation |
| Retry storm amplifying failed Helm webhook | `kubectl get events -n <NS> | grep "failed calling webhook"` at high rate; Helm operations all failing | Istio retry policy retrying failed admission webhook calls; each retry triggers another webhook invocation | Exponential webhook load; webhook pod overloaded; admission backlog grows | `kubectl edit virtualservice <WEBHOOK_SVC> -n <WEBHOOK_NS>` — set `retries.attempts: 1`; scale webhook deployment horizontally |
| gRPC keepalive failure in Flux helm-controller to kube-apiserver communication | `flux get helmreleases -A` hangs; `kubectl logs -n flux-system helm-controller-<POD> | grep "transport is closing\|keepalive"` | Long-running gRPC watch streams from helm-controller to API server dropped by load balancer idle timeout | helm-controller loses watch on HelmRelease objects; reconciliation stops until reconnect | Tune gRPC keepalive: add `HELM_CONTROLLER_GRPC_KEEPALIVE=30s` env var; or set `--kube-api-qps` and `--kube-api-burst` on controller |
| Trace context propagation gap — Helm hook Jobs missing parent trace context | Helm upgrade traces show gap between pre-upgrade hook and main deployment; hook latency not attributed | Hook Job pods created without trace context propagation; Helm does not inject trace headers into hook pod env | Cannot correlate slow hook execution with overall deployment time in Jaeger/Zipkin | Add `OTEL_TRACE_PARENT` env var to hook Job template from parent pipeline; use structured logs with `deploy_id` correlation ID |
| Load balancer health check misconfiguration — AWS NLB marking Helm webhook service unhealthy | `kubectl describe service <WEBHOOK_SVC> -n <NS> | grep "LoadBalancer\|health"`; NLB target group shows unhealthy | NLB TCP health check on webhook port 443; TLS termination at pod level causes NLB to see TLS handshake as unhealthy | Helm operations involving webhooks fail with `connection refused`; admission control breaks | `aws elbv2 modify-target-group --target-group-arn <ARN> --health-check-protocol HTTPS --health-check-path /healthz`; or switch to ALB with proper HTTPS health check |
