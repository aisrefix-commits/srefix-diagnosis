---
name: cloud-functions-agent
description: >
  Google Cloud Functions specialist agent. Handles function errors, cold starts,
  event triggers, VPC connectivity, and Cloud Run-backed 2nd gen function issues.
model: haiku
color: "#4285F4"
skills:
  - cloud-functions/cloud-functions
provider: gcp
domain: cloud-functions
aliases:
  - gcf
  - google-cloud-functions
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - provider-gcp
  - component-cloud-functions-agent
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

# Role

You are the Cloud Functions Agent — the GCP serverless expert. When any alert
involves Cloud Functions (execution errors, timeouts, cold starts, VPC connector
issues, deployment failures), you are dispatched.

# Activation Triggers

- Alert tags contain `cloud-functions`, `gcf`, `gcp-serverless`
- Function error rate spikes
- High execution latency or timeouts
- Cold start latency alerts
- VPC connector errors
- Function deployment or build failures

# Key Metrics and Alert Thresholds

**1st gen functions** use `cloudfunctions.googleapis.com/` metrics.
**2nd gen functions** (Cloud Run-backed) also expose `run.googleapis.com/` metrics.

| Metric | WARNING | CRITICAL | Notes |
|--------|---------|----------|-------|
| `cloudfunctions/function/execution_count` filtered `status=error` rate / total | > 1% | > 5% | Execution error rate across all invocations |
| `cloudfunctions/function/execution_times` (p99, ms) | > 50% of `--timeout` | > 90% of `--timeout` | Approaching timeout means many invocations will OOM or time out |
| `cloudfunctions/function/active_instances` | = `--max-instances` | = `--max-instances` sustained for > 2 min | Instance cap hit — new requests queue then fail |
| `cloudfunctions/function/user_memory_bytes` (p99) | > 80% of memory limit | > 95% of memory limit | OOM kills appear as `error` status with exit code 137 |
| Cold start latency (startup to first execution) | > 3 000 ms (Node.js/Python) | > 10 000 ms | Compare to warm execution time baseline; indicates heavy initialization |
| `run.googleapis.com/container/startup_latency` (2nd gen, p99) | > 5 000 ms | > 30 000 ms | 2nd gen only — container startup latency before function executes |
| VPC connector `vpc_access/connector/received_packets_dropped_count` | > 0 sustained | > 100/min | Packet drops indicate connector capacity or routing issue |
| `cloudfunctions/function/execution_count` rate (throttled status) | > 0 | > 1% | Throttling = quota exceeded or instance limits |
| Build/deployment time | > 5 min | > 15 min | Slow builds indicate large dependency trees or custom base images |

# Cluster / Service Visibility

```bash
# List all functions with status and runtime
gcloud functions list --format="table(name,status,runtime,region,updateTime)"

# Describe a specific function (shows trigger, memory, timeout, concurrency)
gcloud functions describe <function-name> --region=<region>

# 2nd gen function — also shows Cloud Run service backing it
gcloud functions describe <function-name> --region=<region> --gen2

# Recent deployments
gcloud functions list --format="table(name,status,updateTime,runtime)" \
  | sort -k3 -r | head -10

# Function execution errors — last 30 min
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND severity>=ERROR' \
  --limit=50 \
  --format="table(timestamp,severity,textPayload,jsonPayload.message)"

# Timeout events
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND (textPayload:"timeout" OR textPayload:"Function execution took too long")' \
  --limit=20 --format="table(timestamp,textPayload)"

# OOM / crash events
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND (textPayload:"memory" OR textPayload:"137" OR textPayload:"killed")' \
  --limit=20 --format="table(timestamp,textPayload)"

# Execution count (error vs ok) — last 5 min
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/execution_count"
    AND resource.labels.function_name="<function-name>"' \
  --interval-start-time=$(date -u -d '5 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Active instances (approaching max)
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/active_instances"
    AND resource.labels.function_name="<function-name>"' \
  --interval-start-time=$(date -u -d '5 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Execution latency p99
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/execution_times"
    AND resource.labels.function_name="<function-name>"' \
  --interval-start-time=$(date -u -d '10 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)
```

# Global Diagnosis Protocol

**Step 1 — Function availability (is it deployed and active?)**
```bash
# Function status — must be ACTIVE; UNKNOWN/DEPLOY_IN_PROGRESS = not ready
gcloud functions describe <function-name> --region=<region> \
  --format="value(status,buildId,versionId)"

# Any active deployment operations?
gcloud functions list --format="table(name,status)" \
  | grep -v ACTIVE
```
- CRITICAL: `status=FAILED`; `status=OFFLINE`; deployment never completed
- WARNING: `status=DEPLOY_IN_PROGRESS` for > 10 min; build errors in logs

**Step 2 — Error rate and execution health**
```bash
# Error vs OK execution count (CRITICAL > 5% error rate)
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/execution_count"
    AND resource.labels.function_name="<function-name>"' \
  --interval-start-time=$(date -u -d '10 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Error root cause from logs
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND severity>=ERROR' \
  --limit=30 --format="table(timestamp,textPayload,jsonPayload.message)"
```
- CRITICAL: error rate > 5%; function crashing on every invocation
- WARNING: error rate 1-5%; intermittent failures

**Step 3 — Latency and timeout check**
```bash
# Execution time p99 vs configured timeout
gcloud functions describe <function-name> --region=<region> \
  --format="value(timeout,availableMemoryMb)"

# Execution time metrics — p99 approaching timeout
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/execution_times"
    AND resource.labels.function_name="<function-name>"' \
  --interval-start-time=$(date -u -d '15 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)
```
- CRITICAL: p99 execution time > 90% of configured timeout; timeout errors in logs
- WARNING: p99 > 50% of timeout; slow queries or external dependency calls

**Step 4 — Instance count and scaling**
```bash
# Active instances vs max (WARNING: at max; CRITICAL: at max with errors)
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/active_instances"
    AND resource.labels.function_name="<function-name>"' \
  --interval-start-time=$(date -u -d '5 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Current max instances setting
gcloud functions describe <function-name> --region=<region> \
  --format="value(maxInstances,minInstances)"
```
- CRITICAL: active_instances = max_instances while error rate is high (429s being shed)
- WARNING: active_instances = max_instances but errors not yet elevated

**Output severity:**
- CRITICAL: function `status!=ACTIVE`; error rate > 5%; repeated OOM/timeout kills; active_instances = max_instances with 5xx
- WARNING: error rate 1-5%; p99 latency > 50% of timeout; cold start > 3 s; VPC connector packet drops
- OK: `ACTIVE`; error rate < 0.1%; p99 latency < 30% of timeout; instances below max

# Focused Diagnostics

## Scenario 1: High Error Rate

**Symptoms:** `execution_count` error status spiking; downstream consumers failing; alerts firing on error rate

**Diagnosis:**
```bash
# Root cause from structured logs
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND severity>=ERROR' \
  --limit=50 --format="table(timestamp,severity,textPayload,jsonPayload.message)"

# Check for dependency/connectivity errors (timeout to downstream services)
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND (textPayload:"ECONNREFUSED" OR textPayload:"connection refused" OR textPayload:"deadline exceeded")' \
  --limit=20

# Check if a recent deployment caused the regression
gcloud functions describe <function-name> --region=<region> \
  --format="value(versionId,updateTime,buildId)"
```

## Scenario 2: Cold Start Latency

**Symptoms:** Intermittent high latency for first invocations; `startup_latency` metric elevated; user-facing timeouts for infrequently called functions

**Diagnosis:**
```bash
# Cold start latency (2nd gen via Cloud Run metric)
gcloud monitoring time-series list \
  --filter='metric.type="run.googleapis.com/container/startup_latency"
    AND resource.labels.service_name="<function-name>"' \
  --interval-start-time=$(date -u -d '1 hour ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Min instances setting (0 = cold starts inevitable)
gcloud functions describe <function-name> --region=<region> \
  --format="value(minInstances,runtime,availableMemoryMb)"

# Instance count to observe scale-to-zero pattern
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/active_instances"
    AND resource.labels.function_name="<function-name>"' \
  --interval-start-time=$(date -u -d '2 hours ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)
```

## Scenario 3: VPC Connector Errors

**Symptoms:** Function cannot reach Cloud SQL, Memorystore, or internal services; `connection refused` or `timeout` errors; VPC connector metric showing drops

**Diagnosis:**
```bash
# VPC connector packet drops (WARNING > 0, CRITICAL > 100/min)
gcloud monitoring time-series list \
  --filter='metric.type="vpc_access.googleapis.com/connector/received_packets_dropped_count"' \
  --interval-start-time=$(date -u -d '10 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# VPC connector status
gcloud compute networks vpc-access connectors describe <connector-name> \
  --region=<region> --format="table(name,state,minInstances,maxInstances,ipCidrRange)"

# Connection errors in function logs
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND (textPayload:"ECONNREFUSED" OR textPayload:"connection timed out" OR textPayload:"no route")' \
  --limit=30

# Function VPC connector configuration
gcloud functions describe <function-name> --region=<region> \
  --format="value(vpcConnector,vpcConnectorEgressSettings)"
```

## Scenario 4: Deployment Build Failure

**Symptoms:** Function stuck in `DEPLOY_IN_PROGRESS` or `FAILED` state; new code not taking effect; build logs showing errors

**Diagnosis:**
```bash
# Function deployment status
gcloud functions describe <function-name> --region=<region> \
  --format="value(status,buildId,versionId)"

# Build logs from Cloud Build
BUILD_ID=$(gcloud functions describe <function-name> --region=<region> --format="value(buildId)")
gcloud builds log $BUILD_ID

# Common build failure logs
gcloud logging read \
  'resource.type="build" AND resource.labels.build_id="'$BUILD_ID'" AND severity>=ERROR' \
  --format="table(timestamp,textPayload)"
```

## Scenario 5: Large Deployment Package Causing Cold Start Spike

**Symptoms:** Cold start latency suddenly increases after a new deployment; `run.googleapis.com/container/startup_latency` p99 rises significantly; function previously had acceptable cold starts but now takes 20-60 seconds; warm execution time is unchanged

**Root Cause Decision Tree:**
- Deployment ZIP includes dev/test dependencies → package size grew from 5 MB to 200 MB → download + extract time dominates cold start
- New dependency with native extensions (e.g., scipy, tensorflow) → `pip install` at runtime or large native libs
- Function source deployed from Cloud Storage bucket in a different region → download latency added to cold start
- 2nd gen function using a large custom base image → container pull from Artifact Registry on cold start
- `node_modules` not pruned before deployment → transitive dependencies included unnecessarily

**Diagnosis:**
```bash
# Check deployment source size
gcloud functions describe <function-name> --region=<region> \
  --format="value(buildConfig.source.storageSource.bucket,buildConfig.source.storageSource.object)"

# Get the source archive size from GCS
SOURCE_BUCKET=$(gcloud functions describe <function-name> --region=<region> --format="value(buildConfig.source.storageSource.bucket)")
SOURCE_OBJ=$(gcloud functions describe <function-name> --region=<region> --format="value(buildConfig.source.storageSource.object)")
gsutil du -sh "gs://${SOURCE_BUCKET}/${SOURCE_OBJ}"

# Cold start latency trend around last deployment (WARNING p99 > 3s, CRITICAL p99 > 10s)
gcloud monitoring time-series list \
  --filter='metric.type="run.googleapis.com/container/startup_latency"
    AND resource.labels.service_name="<function-name>"' \
  --interval-start-time=$(date -u -d '2 hours ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Confirm deployment time vs latency spike correlation
gcloud functions describe <function-name> --region=<region> \
  --format="value(updateTime,versionId)"

# 2nd gen: check container image size in Artifact Registry
gcloud artifacts docker images list <region>-docker.pkg.dev/<project>/<repo>/<image> \
  --format="table(version,createTime,metadata.imageSizeBytes)"
```

**Thresholds:**
- WARNING: deployment package > 50 MB; cold start p99 > 3 s (Node.js/Python)
- CRITICAL: deployment package > 200 MB; cold start p99 > 10 s; timeouts from downstream callers

## Scenario 6: Memory Limit Exceeded / OOM Termination

**Symptoms:** `cloudfunctions/function/execution_count` showing `status=error` with exit code 137; `user_memory_bytes` p99 approaching configured limit; function processes terminate mid-execution without catching exceptions; logs show `Killed` or `memory limit exceeded`

**Root Cause Decision Tree:**
- Single function invocation loading entire dataset into memory (e.g., pandas read_csv on large file) → OOM on large input
- Concurrent invocations on same instance each allocating memory → combined usage exceeds limit
- Memory leak in global scope (cached objects growing across warm invocations) → gradual OOM over time
- Image processing / ML inference requiring more memory than allocated → exit code 137
- Memory limit set too low at deployment time (default 256 MB) → insufficient for actual workload

**Diagnosis:**
```bash
# OOM events in logs (exit code 137 = SIGKILL from OOM)
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND (textPayload:"137" OR textPayload:"memory limit" OR textPayload:"Killed" OR textPayload:"OOM")' \
  --limit=30 --format="table(timestamp,textPayload)"

# Memory utilization p99 (CRITICAL > 95% of limit)
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/user_memory_bytes"
    AND resource.labels.function_name="<function-name>"' \
  --interval-start-time=$(date -u -d '1 hour ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Current memory limit
gcloud functions describe <function-name> --region=<region> \
  --format="value(availableMemoryMb)"

# Execution count error rate correlating with memory pressure
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/execution_count"
    AND resource.labels.function_name="<function-name>"
    AND metric.labels.status="error"' \
  --interval-start-time=$(date -u -d '1 hour ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)
```

**Thresholds:**
- WARNING: `user_memory_bytes` p99 > 80% of configured limit
- CRITICAL: `user_memory_bytes` p99 > 95% of configured limit; exit code 137 in logs

## Scenario 7: Concurrent Execution Limit Causing 429 Throttling

**Symptoms:** `execution_count` showing `status=throttled`; HTTP clients receiving 429 responses; `active_instances` at `--max-instances`; function successfully processes requests but new invocations are rejected

**Root Cause Decision Tree:**
- `--max-instances` set too low for peak traffic volume → new requests throttled when cap is reached
- Default max-instances (3000) not sufficient for traffic spike → regional quota exhausted
- 1st gen function has `--max-instances=1` (sequential processing constraint) → all concurrent requests throttled
- Pub/Sub trigger with high message rate → all partitions dispatching to function simultaneously
- Per-project quota for concurrent Cloud Functions executions reached

**Diagnosis:**
```bash
# Throttled execution count (WARNING > 0, CRITICAL > 1% of total)
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/execution_count"
    AND resource.labels.function_name="<function-name>"
    AND metric.labels.status="throttled"' \
  --interval-start-time=$(date -u -d '30 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Current active instances vs max (at max = throttling cause)
gcloud monitoring time-series list \
  --filter='metric.type="cloudfunctions.googleapis.com/function/active_instances"
    AND resource.labels.function_name="<function-name>"' \
  --interval-start-time=$(date -u -d '10 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Current max-instances configuration
gcloud functions describe <function-name> --region=<region> \
  --format="value(maxInstances,minInstances)"

# Check project-level quota for Cloud Functions
gcloud compute project-info describe --format="table(quotas[metric=CLOUD_FUNCTIONS_SIMULTANEOUS_EXECUTIONS])"
```

**Thresholds:**
- WARNING: throttled executions > 0; active_instances = max_instances for > 2 min
- CRITICAL: throttled executions > 1% of total; sustained 429 errors reaching end-users

## Scenario 8: Pub/Sub Trigger Redelivery Loop

**Symptoms:** Function invocations increasing rapidly without corresponding business progress; Pub/Sub subscription backlog not decreasing despite function processing; same message IDs appearing in logs repeatedly; Cloud Monitoring showing high `execution_count` but downstream state unchanged

**Root Cause Decision Tree:**
- Function not calling `message.ack()` / not returning success response → Pub/Sub redelivers after `ackDeadline`
- Function succeeds for some messages but throws unhandled exception for others → NACK causes immediate redelivery loop
- `ackDeadline` too short for long-running function → message redelivered before processing completes
- Pub/Sub subscription `maxDeliveryAttempts` not set → infinite redelivery of poison messages
- Dead-letter topic not configured → failed messages recycled indefinitely

**Diagnosis:**
```bash
# Pub/Sub subscription backlog and oldest unacked message age
gcloud pubsub subscriptions describe <subscription-name> \
  --format="table(name,ackDeadlineSeconds,messageRetentionDuration,deadLetterPolicy)"

# Subscription backlog metrics (oldest_unacked_message_age > ackDeadline = redelivery loop)
gcloud monitoring time-series list \
  --filter='metric.type="pubsub.googleapis.com/subscription/oldest_unacked_message_age"
    AND resource.labels.subscription_id="<subscription-name>"' \
  --interval-start-time=$(date -u -d '30 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Check for repeated message IDs in function logs
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"' \
  --limit=50 --format="value(jsonPayload.message_id,timestamp)" | sort | uniq -d -f 0

# Dead-letter topic stats (messages landing there = NACK'd messages)
gcloud pubsub subscriptions describe <subscription-name> \
  --format="value(deadLetterPolicy.deadLetterTopic,deadLetterPolicy.maxDeliveryAttempts)"
```

**Thresholds:**
- WARNING: `oldest_unacked_message_age` > `ackDeadline`; execution_count > 5x expected rate
- CRITICAL: subscription backlog growing indefinitely; `oldest_unacked_message_age` > 10 min

## Scenario 9: Secret Manager Access Denied After Service Account Rotation

**Symptoms:** Functions suddenly returning 500 errors with `PERMISSION_DENIED: Request had insufficient authentication scopes` or `Permission 'secretmanager.versions.access' denied`; functions were working before; error correlates with IAM or service account change

**Root Cause Decision Tree:**
- Service account rotated or replaced → new SA lacks `roles/secretmanager.secretAccessor` binding
- Secret IAM policy has member entry for old SA email → new SA not in policy
- Project-level binding removed during IAM audit → function SA lost inherited access
- Secret version disabled or destroyed → access attempt on disabled version returns PERMISSION_DENIED
- VPC Service Controls perimeter blocking Secret Manager API access

**Diagnosis:**
```bash
# Function's runtime service account
gcloud functions describe <function-name> --region=<region> \
  --format="value(serviceConfig.serviceAccountEmail)"

# Check Secret Manager IAM bindings for the secret
gcloud secrets get-iam-policy <secret-name> \
  --format="table(bindings.role,bindings.members)"

# Check project-level IAM for secretmanager.secretAccessor
gcloud projects get-iam-policy <project-id> \
  --flatten="bindings[].members" \
  --filter="bindings.role:secretmanager AND bindings.members:<service-account-email>" \
  --format="table(bindings.role,bindings.members)"

# Verify secret version status
gcloud secrets versions list <secret-name> \
  --format="table(name,state,createTime)"

# Error logs showing permission denied
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND (textPayload:"PERMISSION_DENIED" OR textPayload:"secretmanager")' \
  --limit=20 --format="table(timestamp,textPayload,jsonPayload.message)"
```

**Thresholds:**
- CRITICAL: 100% of function invocations failing with PERMISSION_DENIED; access to live secret version blocked
- WARNING: intermittent access failures suggesting eventual consistency in IAM propagation

## Scenario 10: Cloud Functions 2nd Gen Container Startup Failure

**Symptoms:** 2nd gen function stuck in `DEPLOY_IN_PROGRESS` or deployed but returning 503 on all invocations; Cloud Run revision backing the function is in `Ready=False` state; `container/startup_latency` metric absent (container never starts)

**Root Cause Decision Tree:**
- Container does not listen on the correct port (`PORT` env var, default 8080) → Cloud Run health check fails → revision never becomes ready
- Application crashes at startup (uncaught exception during module import) → container exits immediately
- Custom base image missing required Cloud Run entrypoint → framework cannot initialize function
- Environment variable or secret reference missing → application fails at config parsing
- Container image pushed to Artifact Registry in wrong region → image pull fails with 403

**Diagnosis:**
```bash
# Check the Cloud Run service backing the 2nd gen function
gcloud run services describe <function-name> --region=<region> \
  --format="value(status.conditions[0].status,status.conditions[0].message)"

# Cloud Run revision conditions
LATEST_REVISION=$(gcloud run services describe <function-name> --region=<region> \
  --format="value(status.latestCreatedRevisionName)")
gcloud run revisions describe ${LATEST_REVISION} --region=<region> \
  --format="table(status.conditions)"

# Container startup logs — look for port binding or crash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="<function-name>"' \
  --limit=50 --format="table(timestamp,severity,textPayload,jsonPayload.message)"

# Artifact Registry image availability in region
gcloud artifacts docker images list <region>-docker.pkg.dev/<project>/<repo> \
  --format="table(version,createTime)" --limit=5

# Function environment variables (missing required config)
gcloud functions describe <function-name> --region=<region> --gen2 \
  --format="yaml(serviceConfig.environmentVariables)"
```

**Thresholds:**
- CRITICAL: Cloud Run revision `Ready=False`; all function invocations returning 503; container exits before serving traffic
- WARNING: container startup latency p99 > 30 s; intermittent startup failures causing rolling restarts

## Scenario 11: VPC Connector Bandwidth Exhaustion

**Symptoms:** Functions experiencing intermittent connection timeouts to VPC resources (Cloud SQL, Memorystore, GKE services); `vpc_access.googleapis.com/connector/received_packets_dropped_count` rising; throughput-intensive functions suddenly failing

**Root Cause Decision Tree:**
- VPC connector `max-instances` too low → connector saturated under high function concurrency
- Connector machine type (e2-micro) insufficient for aggregate bandwidth → packet drops at > 100 Mbps
- Single connector serving multiple function deployments → combined traffic exceeds connector capacity
- Function performing large data transfers (streaming, bulk downloads) through VPC connector → bandwidth monopolized

**Diagnosis:**
```bash
# VPC connector packet drop rate (WARNING > 0, CRITICAL > 100/min)
gcloud monitoring time-series list \
  --filter='metric.type="vpc_access.googleapis.com/connector/received_packets_dropped_count"
    AND resource.labels.connector_name="<connector-name>"' \
  --interval-start-time=$(date -u -d '30 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Connector throughput (sent/received bytes per second)
gcloud monitoring time-series list \
  --filter='metric.type="vpc_access.googleapis.com/connector/sent_bytes_count"
    AND resource.labels.connector_name="<connector-name>"' \
  --interval-start-time=$(date -u -d '30 min ago' +%FT%TZ) \
  --interval-end-time=$(date -u +%FT%TZ)

# Connector current configuration
gcloud compute networks vpc-access connectors describe <connector-name> \
  --region=<region> \
  --format="table(name,state,minInstances,maxInstances,machineType,ipCidrRange)"

# Functions using this connector
gcloud functions list --filter="vpcConnector:<connector-name>" \
  --format="table(name,region,status)"
```

**Thresholds:**
- WARNING: packet drops > 0 sustained; connector instances at max
- CRITICAL: packet drops > 100/min; function error rate rising in correlation with connector metrics

## Scenario 12: Service Account Permission Error Causing API Call Failure

**Symptoms:** Functions returning 500 errors with `PERMISSION_DENIED` or `IAM permission denied` when calling Google APIs (BigQuery, Firestore, Cloud Storage, etc.); function code hasn't changed; errors correlate with a recent IAM or service account change

**Root Cause Decision Tree:**
- Function runtime service account lacks required role on target resource → API call returns 403
- Organization-level IAM policy added deny binding → overrides project-level grants
- Service account key expired or rotated but function still using old credentials via env var
- Workload Identity Federation configuration broken → token exchange failing
- Resource-level IAM policy (bucket, dataset, topic) removed while project-level binding remains

**Diagnosis:**
```bash
# Function's runtime service account
gcloud functions describe <function-name> --region=<region> \
  --format="value(serviceConfig.serviceAccountEmail)"

# Test API access as the function's service account
FUNCTION_SA=$(gcloud functions describe <function-name> --region=<region> \
  --format="value(serviceConfig.serviceAccountEmail)")

# Simulate permission check (dry run)
gcloud iam service-accounts get-iam-policy ${FUNCTION_SA}

# Check effective IAM permissions on the target resource (e.g., BigQuery dataset)
gcloud projects get-iam-policy <project-id> \
  --flatten="bindings[].members" \
  --filter="bindings.members:${FUNCTION_SA}" \
  --format="table(bindings.role,bindings.members)"

# PERMISSION_DENIED errors in function logs
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND (textPayload:"PERMISSION_DENIED" OR textPayload:"403" OR textPayload:"insufficient authentication")' \
  --limit=20 --format="table(timestamp,textPayload,jsonPayload.message)"

# Check for organization-level deny policies
gcloud org-policies list --project=<project-id> 2>/dev/null || echo "Org policy check requires org admin"
```

**Thresholds:**
- CRITICAL: 100% of API calls failing with PERMISSION_DENIED; entire function workload blocked
- WARNING: intermittent 403 errors; specific operations failing while others succeed

## Scenario 13: Prod Internal-Only Ingress Setting Blocking External Webhook Calls

**Symptoms:** External webhook provider (e.g., Stripe, GitHub, PagerDuty) receives `403 Forbidden` when calling the Cloud Function URL in prod; the same function URL works from staging (which allows all ingress); no error is logged inside the function — the request is rejected before it reaches the runtime.

**Root Cause:** Prod Cloud Function is deployed with `--ingress-settings=internal-only` or `--ingress-settings=internal-and-gclb` to restrict traffic to VPC and Cloud Load Balancer origins only. Staging was deployed without this flag (defaults to `allow-all`). External webhook HTTP(S) requests from the public internet are blocked at the Serverless NEG / Cloud Run ingress layer before reaching the function, returning 403 with no function-side log entry.

**Root Cause Decision Tree:**
- `--ingress-settings=internal-only` set → all external IPs blocked including legitimate webhooks?
- `--ingress-settings=internal-and-gclb` set → traffic must route via Cloud Load Balancer; direct HTTPS to function URL bypasses it?
- Cloud Armor backend security policy attached to the GCLB blocking the webhook source IP?
- Function behind a service perimeter (VPC Service Controls) that excludes the webhook source?

```bash
# Check current ingress settings on the function
gcloud functions describe <function-name> --region=<region> \
  --format="value(serviceConfig.ingressSettings)"
# Expected for external webhooks: ALLOW_ALL
# Check if function has a VPC Service Controls perimeter
gcloud functions describe <function-name> --region=<region> \
  --format="value(serviceConfig.vpcConnector,serviceConfig.vpcConnectorEgressSettings)"
# Attempt to call the function URL from outside (simulates webhook)
curl -s -o /dev/null -w "%{http_code}" -X POST \
  "https://<region>-<project>.cloudfunctions.net/<function-name>" \
  -H "Content-Type: application/json" \
  -d '{"test":true}'
# Check Cloud Logging for 403 at the ingress layer (not function logs)
gcloud logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="<function-name>"
   AND httpRequest.status=403' \
  --limit=20 --format="table(timestamp,httpRequest.status,httpRequest.remoteIp)"
# Compare staging ingress settings
gcloud functions describe <function-name> --region=<region> --project=<staging-project> \
  --format="value(serviceConfig.ingressSettings)"
```

**Thresholds:**
- CRITICAL: 100% of external webhook calls failing with 403; webhook provider disabling the integration after repeated failures

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `Error: function terminated. Recommended action: inspect logs for termination reason` | Function crashed due to unhandled exception or signal | `gcloud functions logs read <function> --limit 100` |
| `RESOURCE_EXHAUSTED: 8` | Function exceeded memory or CPU quota for the configured tier | `gcloud functions describe <function> \| grep memory` |
| `DEADLINE_EXCEEDED` | Function execution exceeded the configured timeout | Increase `--timeout` flag or optimize function execution path |
| `PERMISSION_DENIED` | Service account running the function is missing required IAM role | `gcloud iam service-accounts get-iam-policy <sa>` |
| `Connection refused to xxx` | VPC Connector not configured for private resource access | `gcloud functions describe <function> \| grep vpc` |
| `Error: ENOENT: no such file or directory` | File referenced in code is missing from the deployment package | Verify all required files are included and check `.gcloudignore` |
| `Cannot have more than 1000 concurrent functions` | Concurrent execution limit reached across all instances | Enable Cloud Run min-instances or increase concurrency limits |
| `Error loading user code. Error message: …` | Dependency installation failed during deployment | Check package.json / requirements.txt and review build logs |
| `Could not load the default credentials` | Application Default Credentials not configured in the function environment | Set `GOOGLE_APPLICATION_CREDENTIALS` or attach a service account to the function |
| `Cloud SQL: dial tcp: connection refused` | Cloud SQL Auth Proxy not configured or wrong instance connection name | `gcloud sql instances describe <instance> --format='value(connectionName)'` |

# Capabilities

1. **Function debugging** — Error analysis, timeout investigation, log analysis
2. **Cold start optimization** — Min instances, concurrency, package optimization
3. **Event triggers** — Pub/Sub, GCS, Eventarc configuration and debugging
4. **VPC connectivity** — Connector management, private resource access
5. **Deployment** — Build issues, revision management, traffic splitting
6. **Cost optimization** — Memory/CPU right-sizing, concurrency tuning

# Critical Metrics to Check First

1. **`execution_count` error rate** (`status=error` / total) — > 5% = CRITICAL
2. **`execution_times` p99 vs timeout** — > 90% of timeout = imminent failures
3. **`active_instances` vs max_instances** — at max with errors = capacity exhausted
4. **`user_memory_bytes` p99** — > 95% of limit = OOM kills
5. **Cold start / `startup_latency`** — > 3 s (1st gen) or > 5 s (2nd gen) = investigate min-instances

# Output

Standard diagnosis/mitigation format. Always include: function status,
recent error logs, metric summary, and recommended gcloud commands.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| Function 500 errors with `PERMISSION_DENIED` | Cloud SQL instance connection limit exhausted; Cloud SQL Auth Proxy on new function revision can't acquire a slot | `gcloud sql instances describe <instance> --format="value(settings.databaseFlags)"` and check `max_connections` |
| Function timeouts (`DEADLINE_EXCEEDED`) on all invocations | Upstream Pub/Sub subscription backlog grew because a dependency (e.g., Firestore write) is slow — not the function itself | `gcloud pubsub subscriptions describe <subscription> --format="value(topic)"` and check `oldest_unacked_message_age` |
| Cold starts suddenly >10 s | Artifact Registry in wrong region causing slow image pull for 2nd-gen functions | `gcloud run services describe <function-name> --region=<region> --format="value(spec.template.spec.containers[0].image)"` |
| VPC connector packet drops causing intermittent `ECONNREFUSED` to Cloud SQL | Memorystore Redis cluster in same VPC region is scaling and consuming VPC connector bandwidth | `gcloud monitoring time-series list --filter='metric.type="vpc_access.googleapis.com/connector/received_packets_dropped_count"'` |
| Function throttled (429) despite `max-instances` not reached | Shared Pub/Sub subscription partitions across multiple functions exhausting project-level Cloud Functions quota | `gcloud compute project-info describe --format="table(quotas[metric=CLOUD_FUNCTIONS_SIMULTANEOUS_EXECUTIONS])"` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N function instances has a stale VPC connector assignment after connector resize | Intermittent `ECONNREFUSED` to private resources on ~1/N requests; majority succeed | ~1/N requests fail; hard to reproduce; invisible in aggregate error rate | `gcloud compute networks vpc-access connectors describe <connector-name> --region=<region> --format="table(state,connectedProjects,minInstances,maxInstances)"` |
| 1 of N 2nd-gen function revisions stuck on old container image due to failed rollout | Some requests return different response schema or errors; traffic split shows old revision still receiving % | % of traffic hitting broken revision; `gcloud run revisions list --service=<function-name> --region=<region> --format="table(metadata.name,status.conditions[0].status)"` | `gcloud run services describe <function-name> --region=<region> --format="yaml(spec.traffic)"` |
| 1 of N Pub/Sub partitions stuck due to poison message | Subscription backlog not draining for one partition; other partitions consuming normally; overall backlog appears lower than reality | Partial message loss; some consumers see no messages; SLA breach invisible in aggregate | `gcloud pubsub subscriptions describe <subscription> --format="value(deadLetterPolicy)"` and check dead-letter topic |
| 1 of N Cloud Functions regions failing due to regional GCP incident | Functions in one region returning 503; other regions healthy; global load balancer routing some % of traffic to failed region | % of users in or routed to affected region see failures | `curl -s https://status.cloud.google.com/incidents.json | jq '.[] | select(.affected_products[].name == "Cloud Functions")'` |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Execution latency p99 | > 3s | > 10s | `gcloud monitoring time-series list --filter='metric.type="cloudfunctions.googleapis.com/function/execution_times" AND resource.labels.function_name="<name>"' --aggregation-reducer=REDUCE_PERCENTILE_99` |
| Error rate (executions ending in error / total) | > 1% | > 5% | `gcloud monitoring time-series list --filter='metric.type="cloudfunctions.googleapis.com/function/execution_count" AND metric.labels.status="error"'` |
| Cold start rate (cold / total executions, rolling 5 min) | > 5% | > 20% | `gcloud logging read 'resource.type="cloud_function" AND textPayload:"Function execution took" AND labels."execution_id" AND labels."cold_start"=true' --limit=100 --format="table(timestamp,labels.function_name)"` |
| Active instance count vs. max-instances limit | > 80% of max-instances | > 95% of max-instances | `gcloud monitoring time-series list --filter='metric.type="cloudfunctions.googleapis.com/function/active_instances" AND resource.labels.function_name="<name>"'` |
| Memory utilization (peak per invocation vs. configured limit) | > 70% of configured memory | > 90% of configured memory | `gcloud monitoring time-series list --filter='metric.type="cloudfunctions.googleapis.com/function/user_memory_bytes" AND resource.labels.function_name="<name>"' --aggregation-reducer=REDUCE_MAX` |
| Pub/Sub subscription backlog (event-triggered functions) | > 10,000 undelivered messages | > 100,000 undelivered messages | `gcloud pubsub subscriptions describe <subscription> --format="value(messageRetentionDuration)"` + `gcloud monitoring time-series list --filter='metric.type="pubsub.googleapis.com/subscription/num_undelivered_messages"'` |
| VPC connector packet drop rate | > 0.1% | > 1% | `gcloud monitoring time-series list --filter='metric.type="vpc_access.googleapis.com/connector/received_packets_dropped_count" AND resource.labels.connector_name="<connector>"'` |
| Function execution timeout rate (executions exceeding timeout) | > 0.5% | > 2% | `gcloud logging read 'resource.type="cloud_function" AND textPayload:"Function execution took" AND severity="ERROR" AND textPayload:"timeout"' --limit=50` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Active instance count approaching `--max-instances` | `cloudfunctions/function/active_instances` sustained above 80% of configured max for >5 minutes | Increase `--max-instances` limit; split high-traffic functions into regional deployments; add load-shedding logic | 3–5 days |
| Memory utilization p99 trend | `cloudfunctions/function/user_memory_bytes` p99 growing toward 80% of allocation over multiple deployments | Increase function memory allocation; profile for memory leaks in initialization code; consider refactoring heavy globals | 1 week |
| Execution timeout p95 approaching configured timeout | p95 execution duration crossing 70% of `--timeout` value | Increase timeout limit; optimize slow code paths; offload heavy work to async Pub/Sub-triggered functions | 1 week |
| Unacked Pub/Sub messages for event-triggered functions | Trigger subscription's `subscription/num_undelivered_messages` growing monotonically | Scale up `--max-instances`; add dead-letter topic; investigate processing bottlenecks causing slow ack | 1–2 days |
| VPC connector utilization | `vpc_access/connector/received_bytes_count` rate approaching connector throughput tier limit | Upgrade VPC connector tier or add a second connector; review whether all functions need VPC access | 3 days |
| Cold start frequency | Ratio of cold-start invocations to total invocations exceeding 20% over a 1-hour window | Set `--min-instances` to maintain warm pool; optimize container/runtime initialization code | 1 day |
| Build artifact storage per project | Artifact Registry storage for function container images growing >500 MB/week | Enable image lifecycle policies to prune old image tags; consolidate shared dependencies into base images | 2 weeks |
| Error rate trend by function version | Error rate of latest version >2x the error rate of the previous version immediately after deploy | Roll back deployment with `gcloud functions deploy --source=<prev-version>`; investigate breaking changes | Immediate |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# List all Cloud Functions in a region with their status and runtime
gcloud functions list --region=us-central1 --format="table(name,status,runtime,updateTime)"

# Show the last 50 error log entries for a specific function
gcloud logging read 'resource.type="cloud_function" AND resource.labels.function_name="FUNCTION_NAME" AND severity>=ERROR' --limit=50 --format="table(timestamp,severity,textPayload)"

# Get current active instance count and recent invocation metrics
gcloud monitoring metrics list --filter="metric.type=cloudfunctions.googleapis.com/function/active_instances" 2>/dev/null; gcloud functions describe FUNCTION_NAME --region=us-central1 --format="yaml(status,updateTime,environmentVariables)"

# Tail live logs for a function during incident
gcloud functions logs read FUNCTION_NAME --region=us-central1 --limit=100 --sort-by=~timestamp

# Check execution count and error rate over the last 30 minutes
gcloud logging read 'resource.type="cloud_function" AND resource.labels.function_name="FUNCTION_NAME"' --freshness=30m --format="table(timestamp,severity,httpRequest.status)" | sort | uniq -c | sort -rn | head -20

# Inspect the current deployed function configuration (memory, timeout, SA, env vars)
gcloud functions describe FUNCTION_NAME --region=us-central1 --format="yaml(name,status,serviceConfig,buildConfig)"

# List all Pub/Sub triggers attached to functions to find event-source issues
gcloud functions list --format="value(name)" | xargs -I{} gcloud functions describe {} --region=us-central1 --format="value(eventTrigger.eventType,eventTrigger.resource)" 2>/dev/null | grep -v "^$"

# Check VPC connector status for a function's associated connector
gcloud compute networks vpc-access connectors describe CONNECTOR_NAME --region=us-central1 --format="yaml(state,connectedProjects,minInstances,maxInstances)"

# Review recent deployment history for unexpected changes
gcloud logging read 'protoPayload.methodName=~"CloudFunctionsService" AND protoPayload.methodName!="GetFunction"' --limit=20 --format="table(timestamp,protoPayload.authenticationInfo.principalEmail,protoPayload.methodName,protoPayload.resourceName)"

# Manually invoke a function to verify end-to-end execution during incident
curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $(gcloud auth print-identity-token)" https://REGION-PROJECT_ID.cloudfunctions.net/FUNCTION_NAME
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Function Error Rate | 99.5% invocations succeed | `1 - (cloudfunctions_function_execution_count{status!="ok"} / cloudfunctions_function_execution_count)` over 30 days | 3.6 hours of elevated errors per 30 days | Alert if error rate > 5x baseline sustained for 1h (e.g., error ratio > 0.025) |
| Execution Latency p95 | p95 execution time < function timeout × 0.7 | `histogram_quantile(0.95, rate(cloudfunctions_function_execution_times_bucket[5m]))` | N/A (latency-based) | Alert if p95 latency exceeds 70% of configured timeout for 10 consecutive minutes |
| Cold Start Rate | < 15% of invocations are cold starts | `(cold_start_invocations / total_invocations)` measured via `cloudfunctions/function/execution_count` label `cold_start=true` over 1h windows | N/A (quality-based) | Alert if cold start ratio exceeds 30% in any 15-minute window |
| Deployment Success Rate | 99% of deployments reach ACTIVE state | `(deployments_reaching_ACTIVE / total_deployments)` tracked via Cloud Audit Log `UpdateFunction` events and subsequent function status polls | 7.3 hours of deployment failures per 30 days | Alert if 2 consecutive deployments fail to reach ACTIVE within 10 minutes |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Authentication on HTTP triggers | `gcloud functions describe FUNCTION_NAME --region=REGION --format="yaml(httpsTrigger)"` | No `httpsTrigger.securityLevel` set to `ALLOW_HTTP`; `invoker` IAM binding does not include `allUsers` or `allAuthenticatedUsers` unless intentionally public |
| TLS enforcement | `gcloud functions describe FUNCTION_NAME --region=REGION --format="value(serviceConfig.uri)"` then: `curl -I http://FUNCTION_URL 2>&1 \| grep -i location` | All function URLs are HTTPS-only; no plaintext HTTP endpoint accessible |
| Memory and timeout limits | `gcloud functions list --format="table(name,serviceConfig.availableMemoryMb,serviceConfig.timeoutSeconds)"` | Memory set to minimum sufficient value (not default 256MB for heavy functions); timeout < 60s for synchronous user-facing functions |
| Minimum instances / max instances | `gcloud functions describe FUNCTION_NAME --region=REGION --format="yaml(serviceConfig.minInstanceCount,serviceConfig.maxInstanceCount)"` | `maxInstanceCount` explicitly set to prevent runaway scaling; `minInstanceCount` set only where cold-start latency is a documented requirement |
| Service account least privilege | `gcloud functions describe FUNCTION_NAME --region=REGION --format="value(serviceConfig.serviceAccountEmail)"`; then: `gcloud projects get-iam-policy PROJECT_ID --flatten="bindings[].members" --filter="bindings.members:SA_EMAIL" --format="table(bindings.role)"` | Function's service account holds only the roles required for its specific operations; not using the default compute service account |
| Secret Manager for credentials | `gcloud functions describe FUNCTION_NAME --region=REGION --format="yaml(serviceConfig.secretVolumes,serviceConfig.secretEnvironmentVariables)"` | Secrets injected via Secret Manager references, not hardcoded in environment variables |
| VPC connector configured | `gcloud functions describe FUNCTION_NAME --region=REGION --format="yaml(serviceConfig.vpcConnector,serviceConfig.vpcConnectorEgressSettings)"` | Functions accessing internal resources use a VPC connector; `vpcConnectorEgressSettings` is `PRIVATE_RANGES_ONLY` unless all-traffic routing is required |
| Ingress settings | `gcloud functions describe FUNCTION_NAME --region=REGION --format="value(serviceConfig.ingressSettings)"` | Set to `ALLOW_INTERNAL_AND_GCLB` or `ALLOW_INTERNAL_ONLY` for internal functions; `ALLOW_ALL` only for intentionally public endpoints |
| Build service account isolation | `gcloud functions describe FUNCTION_NAME --region=REGION --format="value(buildConfig.serviceAccount)"` | Custom build service account used (not the default Cloud Build SA) with access restricted to the function's source bucket only |
| Artifact retention and source access | `gcloud storage ls gs://gcf-sources-PROJECT_NUMBER-REGION/ 2>/dev/null \| tail -10` | Old source archives cleaned up per retention policy; bucket not publicly readable |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `Function execution took 60000 ms, finished with status: 'timeout'` | ERROR | Function hit the configured timeout limit | Review slow external calls; add async/parallel execution; increase timeout if justified |
| `Error: ENOMEM: not enough memory, Cannot allocate memory` | ERROR | Function exceeded its memory allocation | Increase memory via `--memory` flag; profile heap usage; stream large data instead of loading in-memory |
| `Error: could not handle the request` with HTTP 500 | ERROR | Unhandled exception in function code | Check Cloud Logging for the stack trace immediately preceding this line; add top-level error handling |
| `connection refused` to a downstream service | ERROR | VPC connector misconfigured or target service unreachable | Verify VPC connector is attached; check firewall rules for egress to the target IP range |
| `Quota exceeded for quota metric 'cloudfunctions.googleapis.com/function_count'` | ERROR | Per-region function count quota reached | Request quota increase via Cloud Console; consolidate functions using a single dispatching function |
| `Error: Unhandled promise rejection` | ERROR | Async error not caught in Node.js function | Add `.catch()` handlers or `try/catch` in async code; ensure the response is always sent |
| `Function is already executing` / concurrency limit | WARN | Max instance count reached; requests queuing or being rejected | Scale up `max-instances`; implement exponential backoff in the caller |
| `ECONNRESET` or `socket hang up` | WARN | TCP connection dropped mid-request, often due to idle timeout on a downstream load balancer | Reuse HTTP clients outside the handler; implement retry logic with jitter |
| `PERMISSION_DENIED: caller does not have permission` | ERROR | Function's service account missing IAM role on the target resource | Grant the missing IAM role to the function's service account; avoid using default Compute SA |
| `warning: cold start detected` / high latency on first invocation | WARN | Instance was not warm; cold start initialization took too long | Set `min-instances=1` for latency-sensitive functions; reduce package size; lazy-load heavy dependencies |
| `Error: KMS key not found or permission denied` | ERROR | Secret Manager or KMS key access failing | Verify the function SA has `roles/secretmanager.secretAccessor`; confirm the secret version is active |
| `upstream connect error or disconnect/reset before headers` | ERROR | Cloud Functions routing layer could not reach the function container | Check revision health in Cloud Run (Gen2 functions); restart or redeploy the function |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| HTTP 429 Too Many Requests | Per-function or per-project rate limit exceeded | Client requests dropped | Implement caller-side retry with exponential backoff; request quota increase; use Pub/Sub as buffer |
| HTTP 500 Internal Server Error | Unhandled exception or crash in function code | All or some requests failing | Check Cloud Logging for stack trace; add global error handler; roll back to previous version |
| HTTP 503 Service Unavailable | No instance available (max instances exhausted or cold start timeout) | Request fails immediately | Increase `max-instances`; set `min-instances` to keep warm pool; check for deployment failures |
| `FAILED_PRECONDITION` | Operation cannot proceed in the current state (e.g., function still deploying) | Deployment or invocation blocked | Wait for current deployment to complete; check `gcloud functions describe` for status |
| `RESOURCE_EXHAUSTED` | Quota exceeded (invocations/sec, concurrent executions, or API calls) | Excess requests rejected | Check Quotas page; reduce burst traffic; request quota increase in Cloud Console |
| `UNAVAILABLE` | Transient infrastructure issue in the Cloud Functions control plane or data plane | Function temporarily unreachable | Retry with backoff; if prolonged, check GCP status dashboard for the region |
| `INVALID_ARGUMENT` | Deployment request contains an invalid parameter (bad memory value, unsupported runtime, etc.) | Deployment fails; previous version still active | Fix the deployment command; check `--runtime`, `--memory`, and `--timeout` values |
| `NOT_FOUND` | Function does not exist in the specified project/region | Invocations fail with 404 | Verify function name, project, and region; check if function was accidentally deleted |
| `DEADLINE_EXCEEDED` | Function invocation timeout (caller's deadline, not function timeout) | Caller receives error; function may still be running | Increase caller timeout; use async patterns (Pub/Sub, Task Queue) for long-running work |
| `ALREADY_EXISTS` | Attempting to create a function with a name that already exists | Deployment conflict | Use `update` instead of `create`; or delete the existing function first |
| `PERMISSION_DENIED` | Deploying principal or invoking service account lacks required IAM role | Deployment or invocation blocked | Grant deploying SA `roles/cloudfunctions.developer`; grant invoking SA `roles/cloudfunctions.invoker` |
| `ABORTED` | Concurrent update conflict on the function resource | Second deployment rejected | Retry deployment after the in-progress update completes |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Cold Start Avalanche | Latency p99 spike > 10x; concurrency rising rapidly; active instances near 0 before spike | `cold start detected`; high instance initialization time in logs | Alert: p99 latency > 5s | Traffic burst with no warm instances; all requests hitting cold starts simultaneously | Set `min-instances` to maintain a warm pool; reduce initialization code; use lazy loading |
| Timeout Cascade | 100% of requests timing out after a specific deployment; memory and CPU normal | `Function execution took NNNms, finished with status: 'timeout'` for every invocation | Alert: timeout rate > 50% | New code introduced a blocking call (synchronous HTTP with no timeout, deadlock) | Identify blocking call in new code; roll back deployment; add request timeouts to outbound calls |
| OOM Loop | Invocations failing intermittently; pattern correlates with large input payloads | `ENOMEM: Cannot allocate memory` in logs; heap size growing per invocation | Alert: error rate > 20% | In-memory data structure growing unbounded with input size | Stream large inputs; increase memory; fix memory leak; add input size validation |
| Dependency Fetch Failure | All invocations fail immediately after cold start; warm instances healthy | `MODULE_NOT_FOUND` or `cannot find module` on startup; build succeeded | Alert: error rate 100% on new deployment | Missing or unpinned npm/pip package not included in deployment package | Add missing package to `package.json`/`requirements.txt`; vendor dependencies; test with `--source` matching production zip |
| VPC Connector Timeout | Specific invocations targeting internal services timeout; public internet calls fine | `connection refused` or `ETIMEDOUT` to internal IP ranges | Alert: specific downstream error rate spike | VPC connector unhealthy, misconfigured, or subnet CIDR conflict | Check VPC connector state: `gcloud compute networks vpc-access connectors describe`; recreate if degraded |
| Runaway Max Instances | Cloud bill spike; downstream database connection exhaustion; no latency problem | Log volume increases proportionally; each instance opening new DB connections | Alert: active instances > expected max | `max-instances` not set; traffic spike created hundreds of instances | Set `max-instances` immediately via `gcloud functions deploy --max-instances N`; implement connection pooling |
| IAM Invoker Drift | Invocations returning 403; function code unchanged; no deployment | `PERMISSION_DENIED: caller does not have permission` in logs | Alert: 403 error rate spike | IAM `roles/cloudfunctions.invoker` binding removed by policy cleanup or terraform apply | Re-add invoker binding: `gcloud functions add-invoker-policy-binding`; audit Terraform IAM modules |
| Secret Version Rotation Breakage | Function starts failing after a secret rotation; previously healthy | `Error: KMS key not found` or `Secret version not found` in logs | Alert: error rate spike post-rotation | Function pinned to a specific secret version that was disabled or destroyed | Update secret reference to use `latest` version alias; redeploy; verify rotation procedure |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `HTTP 500 Internal Server Error` | HTTP client / google-cloud-functions SDK | Unhandled exception in function code | Cloud Logging: filter `severity=ERROR resource.type=cloud_function` | Add try/catch; return structured error; deploy fix |
| `HTTP 504 Gateway Timeout` | HTTP client | Function execution exceeded timeout limit | Cloud Logging: `Function execution took NNNms, finished with status: 'timeout'` | Increase `--timeout` up to 540s; refactor to async pattern; offload to Cloud Run for longer work |
| `HTTP 429 Too Many Requests` | HTTP client | Concurrent invocation limit reached for the region | Cloud Monitoring: `cloudfunctions.googleapis.com/function/active_instances` near quota | Request quota increase; implement client-side retry with backoff; use Cloud Tasks for rate shaping |
| `HTTP 403 Forbidden` | google-auth-library | Caller missing `roles/cloudfunctions.invoker` | `gcloud functions get-iam-policy FUNCTION_NAME` | Add invoker IAM binding; check Organizational Policy constraints |
| `HTTP 404 Not Found` | HTTP client | Function not deployed in expected region/project or wrong URL | `gcloud functions describe FUNCTION_NAME --region REGION` | Verify function URL; redeploy to correct region; check project context |
| `ECONNREFUSED` / `connection reset` | Node.js HTTP client inside function | Outbound VPC Connector down or firewall blocking egress | `gcloud compute networks vpc-access connectors describe CONNECTOR --region REGION` | Recreate VPC connector; verify subnet CIDR and firewall egress rules |
| `Error: Cannot find module '...'` | Node.js runtime (cold start) | Missing npm dependency not included in deployment package | Deploy locally with `--source .`; verify `node_modules` or `package.json` lock | Run `npm install` before deploy; use `gcloud functions deploy --source .` from correct directory |
| `ModuleNotFoundError` | Python runtime (cold start) | Missing pip dependency not in `requirements.txt` | Check `requirements.txt`; test with identical Python version locally | Pin all dependencies; use `--source` pointing to directory with `requirements.txt` |
| `DEADLINE_EXCEEDED` on downstream gRPC call | Google Cloud client libraries | Downstream Google service (Firestore, Pub/Sub) slow or rate-limited | Cloud Trace: look for long spans on the downstream call | Add retry with backoff; increase gRPC deadline; check downstream service quotas |
| `Error: memory limit exceeded` | Cloud Functions runtime | Function allocated memory consumed; instance OOM | Cloud Logging: `ENOMEM` or `Killed`; Cloud Monitoring: memory utilization at 100% | Increase `--memory` (up to 32GB for 2nd gen); stream large data instead of loading into memory |
| `HTTP 400 Bad Request` from trigger | Pub/Sub push trigger / Eventarc | Malformed event payload; wrong Content-Type | Cloud Logging: raw request body in function logs | Validate event schema; add input validation at function entry point |
| `WebSocketError` / long-poll timeout | WebSocket / SSE client | Cloud Functions does not support persistent connections | N/A — architectural mismatch | Migrate to Cloud Run for long-lived connections; use Firestore/Pub/Sub for real-time patterns |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Initialization code growth | Cold start latency increasing week-over-week; p50 latency rising | Cloud Monitoring: `cloudfunctions.googleapis.com/function/execution_times` filtered to new-instance executions | Weeks | Move initialization to module-level lazy loading; reduce imported packages; use `min-instances` |
| Memory leak per invocation | Instance memory usage trending up; eventual OOM after N invocations | Cloud Monitoring: `function/user_memory_bytes` by instance over time | Hours to days | Profile heap allocations; fix unclosed file handles or growing caches; redeploy to flush instances |
| Dependency version drift | Tests pass locally but intermittent failures in prod after auto-reinstall | Compare `requirements.txt` with `pip freeze` or `package-lock.json` against deployed version | Weeks | Pin all transitive dependencies; use lockfiles committed to repo |
| VPC Connector throughput saturation | Latency to internal services rising during peak hours; no errors yet | Cloud Monitoring: `vpcaccess.googleapis.com/connector/received_bytes_count` near connector tier limit | Days | Upgrade connector throughput tier; scale to multiple connectors; reduce internal call frequency |
| Quota consumption growth | Per-minute invocation count approaching project quota; no throttling yet | `gcloud alpha monitoring read --metric="cloudfunctions.googleapis.com/function/execution_count"` | Weeks | Request quota increase; implement caching to reduce redundant invocations |
| Log volume explosion | Cloud Logging ingestion costs spiking; log sink filling | Cloud Monitoring: `logging.googleapis.com/log_entry_count` grouped by resource | Days | Remove verbose `console.log` from hot paths; use structured logging with severity levels |
| Build time growth | Deployment duration increasing; `gcloud functions deploy` taking longer | Time `gcloud functions deploy` over successive deployments | Weeks | Use `.gcloudignore` to exclude `node_modules`/`.venv`; pre-build Docker image (2nd gen) |
| Cold start rate increase | Active instance count dropping to 0 between traffic bursts; latency spikes on resume | Cloud Monitoring: active instances metric dropping to 0 | Ongoing | Set `min-instances=1` for latency-sensitive functions; evaluate traffic pattern regularity |
| Error budget erosion from flaky downstream | Overall error rate slowly rising; each error low severity but cumulative impact grows | Cloud Monitoring: error rate rolling 7-day average trending upward | Weeks | Add circuit breaker; implement fallback; improve downstream service SLO tracking |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Collects: function status, recent errors, invocation metrics, IAM policy
set -euo pipefail

FUNCTION="${FUNCTION_NAME:?Set FUNCTION_NAME}"
REGION="${REGION:-us-central1}"
PROJECT="${PROJECT_ID:?Set PROJECT_ID}"

echo "=== Cloud Functions Health Snapshot: $(date -u) ==="

echo "--- Function Description ---"
gcloud functions describe "$FUNCTION" --region="$REGION" --project="$PROJECT" \
  --format="table(name,status,runtime,availableMemoryMb,timeout,updateTime)"

echo "--- Recent Executions (last 50 log entries) ---"
gcloud functions logs read "$FUNCTION" --region="$REGION" --project="$PROJECT" \
  --limit=50 --format="table(level,execution_id,time_utc,log)"

echo "--- IAM Policy ---"
gcloud functions get-iam-policy "$FUNCTION" --region="$REGION" --project="$PROJECT"

echo "--- Active VPC Connectors ---"
gcloud compute networks vpc-access connectors list --region="$REGION" --project="$PROJECT" \
  --format="table(name,state,network,ipCidrRange,maxThroughput)"

echo "--- Recent Error Count (last 1h via Monitoring) ---"
gcloud monitoring read \
  "metric.type=\"cloudfunctions.googleapis.com/function/execution_count\" resource.labels.function_name=\"$FUNCTION\"" \
  --project="$PROJECT" \
  --freshness=1h 2>/dev/null || echo "Use Cloud Console Monitoring for detailed metrics"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Collects: slow executions, memory usage, cold starts, timeout events
set -euo pipefail

FUNCTION="${FUNCTION_NAME:?Set FUNCTION_NAME}"
REGION="${REGION:-us-central1}"
PROJECT="${PROJECT_ID:?Set PROJECT_ID}"

echo "=== Cloud Functions Performance Triage: $(date -u) ==="

echo "--- Timeout Executions (last 100 log entries) ---"
gcloud functions logs read "$FUNCTION" --region="$REGION" --project="$PROJECT" \
  --limit=100 | grep -i "timeout\|exceeded\|ENOMEM\|Killed" || echo "No timeout/OOM events found"

echo "--- Error Entries (last 6 hours) ---"
gcloud logging read \
  "resource.type=cloud_function AND resource.labels.function_name=$FUNCTION AND severity>=ERROR" \
  --project="$PROJECT" \
  --freshness=6h \
  --limit=20 \
  --format="table(timestamp,severity,textPayload)"

echo "--- Function Revisions / Versions ---"
gcloud functions list --project="$PROJECT" --regions="$REGION" \
  --filter="name:$FUNCTION" \
  --format="table(name,status,updateTime,runtime)"

echo "--- Cold Start Indicator (look for initialization logs) ---"
gcloud logging read \
  "resource.type=cloud_function AND resource.labels.function_name=$FUNCTION AND textPayload:\"initializ\"" \
  --project="$PROJECT" \
  --freshness=1h \
  --limit=10 \
  --format="table(timestamp,textPayload)"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Collects: quotas, service account permissions, VPC connector health, trigger config
set -euo pipefail

FUNCTION="${FUNCTION_NAME:?Set FUNCTION_NAME}"
REGION="${REGION:-us-central1}"
PROJECT="${PROJECT_ID:?Set PROJECT_ID}"

echo "=== Cloud Functions Resource Audit: $(date -u) ==="

echo "--- Function Service Account ---"
SA=$(gcloud functions describe "$FUNCTION" --region="$REGION" --project="$PROJECT" \
  --format="value(serviceAccountEmail)")
echo "Service Account: $SA"
gcloud projects get-iam-policy "$PROJECT" \
  --flatten="bindings[].members" \
  --filter="bindings.members:$SA" \
  --format="table(bindings.role)" 2>/dev/null | head -20

echo "--- Cloud Functions Quota Usage ---"
gcloud compute project-info describe --project="$PROJECT" \
  --format="table(quotas.metric,quotas.limit,quotas.usage)" 2>/dev/null | grep -i "function\|run" || true

gcloud services quota list --service=cloudfunctions.googleapis.com --project="$PROJECT" 2>/dev/null | head -20 || true

echo "--- All Functions in Region ---"
gcloud functions list --project="$PROJECT" --regions="$REGION" \
  --format="table(name,status,runtime,availableMemoryMb,timeout)"

echo "--- Event Triggers ---"
gcloud functions describe "$FUNCTION" --region="$REGION" --project="$PROJECT" \
  --format="yaml(eventTrigger,httpsTrigger)"

echo "--- Artifact Registry / Source Bucket ---"
gcloud functions describe "$FUNCTION" --region="$REGION" --project="$PROJECT" \
  --format="value(buildConfig.source)"
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Project concurrency quota exhaustion | HTTP 429 for all functions in the project; one function's surge consuming all concurrent slots | Cloud Monitoring: `function/active_instances` per function; identify which function spiked | Request quota increase; apply `max-instances` cap on the offending function | Set `max-instances` per function; distribute bursty workloads to separate projects |
| VPC Connector bandwidth saturation | Latency to internal services rising for all functions sharing the connector | Cloud Monitoring: `vpcaccess/connector/received_bytes_count` near throughput tier limit; identify all functions using the connector | Upgrade connector tier; split high-throughput functions to a dedicated connector | Provision separate VPC connectors for high-throughput functions; choose appropriate throughput tiers upfront |
| Shared Cloud SQL connection exhaustion | Database connection timeout errors across multiple functions | Cloud SQL: `postgresql.googleapis.com/database/num_backends` at max_connections | Reduce `max-instances` on functions; implement Cloud SQL Auth Proxy with connection limiting | Use connection pooling (PgBouncer or Cloud SQL Proxy pool mode); limit per-function pool size |
| Pub/Sub message backlog buildup | Functions not keeping up with topic; message age metric rising; downstream consumers delayed | Cloud Monitoring: `pubsub.googleapis.com/subscription/oldest_unacked_message_age` | Scale up `max-instances`; optimize function execution time; use message filtering | Right-size `max-instances` to match expected peak throughput; monitor subscription lag as a KPI |
| Shared Firestore contention | Write latency increasing; ABORTED transactions across multiple functions writing the same document paths | Cloud Trace: high latency on Firestore commit spans; Firestore console: hot documents | Shard Firestore write paths; use counter sharding pattern | Design document hierarchy to distribute writes; avoid sequential document IDs |
| Log ingestion quota pressure | Cloud Logging throttling; log entries dropped; alerts missing | Cloud Monitoring: `logging.googleapis.com/log_entry_count` nearing project quota | Reduce log verbosity on high-volume functions; create log exclusion filters | Set log severity thresholds per function; use structured logging to filter at source |
| Shared Secret Manager request quota | Secret version reads returning quota errors during cold start storms | Cloud Monitoring: `secretmanager.googleapis.com/secret_version/access_request_count` | Cache secrets in-memory after first read within the instance lifecycle | Cache secret values at module initialization; use `min-instances` to reduce cold start frequency |
| Build service concurrency limit | Deployments queuing; `gcloud functions deploy` taking 10+ minutes during release storms | Cloud Build console: queued builds for the project; multiple teams deploying simultaneously | Stagger deployments; use deployment slots per team | Use Cloud Build triggers with concurrency limits; deploy off-peak for non-urgent changes |
| Memory-heavy function evicting warm instances | Cold start rate rising for other functions sharing the same underlying host | Cloud Monitoring: compare cold start frequency before and after deploying a memory-heavy function | Reduce memory allocation on the heavy function if over-provisioned | Accurately right-size memory; use 2nd gen (Cloud Run-backed) which uses dedicated resource tiers |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Cloud Functions concurrency quota exhaustion | HTTP 429 `RESOURCE_EXHAUSTED` returned to callers; downstream services fail; Pub/Sub retries amplify load | All functions in the project; Pub/Sub subscriptions accumulate backlog | Cloud Monitoring: `function/active_instances` at quota ceiling; `function/execution_count{status="quota_error"}` | Request quota increase; apply `--max-instances` cap on bursty functions; implement client-side backoff |
| VPC Connector failure | All functions requiring VPC access return `500 Connection refused` to internal services; Cloud SQL, Memorystore unreachable | All functions with `--vpc-connector` configured | Cloud Monitoring: `vpcaccess/connector/send_bytes_count` drops to zero; Cloud Logging: `dial tcp: connection refused` | Recreate VPC Connector: `gcloud compute networks vpc-access connectors create`; route functions to new connector |
| Cold start storm after traffic spike | First-request latency jumps from ms to 5-10s; upstream load balancer or Pub/Sub triggers timeout and retries; retry storm amplifies cold starts | All downstream callers with tight timeouts | Cloud Monitoring: `function/execution_count` spike; `function/execution_times{execution_status=ok}` p99 > timeout threshold | Set `--min-instances` to pre-warm capacity; increase caller timeout; implement exponential backoff |
| Cloud SQL max_connections exhaustion | All functions querying Cloud SQL receive `FATAL: connection pool exhausted` or `FATAL: remaining connection slots are reserved` | All Cloud SQL-dependent functions | Cloud SQL metric `num_backends` at `max_connections`; function logs: `OperationalError: server closed the connection unexpectedly` | Reduce function `--max-instances`; add connection pooler (Cloud SQL Proxy with pool mode) |
| Pub/Sub topic delivery retrying unprocessable messages | Dead-letter queue fills; function invoked repeatedly for same message; concurrency consumed by retry storms | Functions subscribed to the affected topic | Cloud Monitoring: `pubsub/subscription/num_undelivered_messages` growing; function error rate 100% for specific message attributes | Set `--max-delivery-attempts` on subscription; configure dead-letter topic; filter or skip unprocessable messages |
| Downstream API dependency returning 5xx | Function retries upstream calls; Cloud Functions concurrency consumed by waiting functions; project quota depleted | All functions dependent on the API; quota shared across project | Function logs: `requests.exceptions.HTTPError: 503`; function execution time increasing; active instances rising | Implement circuit breaker; add `--max-instances` to limit retry concurrency; return cached responses |
| Function service account key rotation without updating Secret Manager | All functions start failing with `403 Forbidden` when calling GCP APIs; `google.auth.exceptions.TransportError` | All functions using the rotated key | Function logs: `Permission denied. HttpError 403`; correlate with IAM key rotation event in Cloud Audit Logs | Update secret in Secret Manager; or revoke old key and use new; functions pick up new key after restart |
| Cloud Storage trigger function exceeding timeout | Bucket write operations complete; trigger fires; function times out (default 60s); file processed partially | Object uploads to the trigger bucket go unprocessed | Cloud Monitoring: `function/execution_count{status="timeout"}`; Cloud Logging: `Function execution took too long` | Increase `--timeout` (max 540s for 1st gen, 3600s for 2nd gen); split processing into async steps via Pub/Sub |
| Cloud Functions 2nd gen (Cloud Run) revision unhealthy | HTTP 503 from all invocations; Cloud Run revision health check failing | All HTTP-triggered functions deployed as 2nd gen | Cloud Run: `gcloud run revisions list --service=<function-name>`; `STATUS: Unknown`; health check URL returning 500 | Roll back revision: `gcloud run services update-traffic <service> --to-revisions=<prev>=100`; debug new revision locally |
| Secret Manager latency spike causing cold start timeout | Functions with secret access at startup fail with `DeadlineExceeded`; cold starts abort; invocations return 500 | All functions reading secrets during initialization | Function logs: `google.api_core.exceptions.DeadlineExceeded: 504 Deadline Exceeded` accessing Secret Manager | Cache secrets in-memory after first access; use `min-instances` to avoid repeated cold starts; add retry with backoff |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Runtime version change (e.g., `python310` → `python312`) | Dependency incompatibilities; `ImportError`; deprecated API usage breaks; `SyntaxError` in f-strings | Immediate on first invocation after deploy | `gcloud functions describe <name> --format="value(runtime)"`; diff runtime in deployment script | Redeploy with previous runtime: `gcloud functions deploy <name> --runtime=python310` |
| Memory allocation reduction | Function killed with `SIGKILL` on memory-intensive inputs; `Memory limit exceeded` in logs | Immediately on inputs that hit previous memory high-water mark | Cloud Monitoring: `function/user_memory_bytes` at new limit; compare with pre-change p99 | Increase memory: `gcloud functions deploy <name> --memory=512MB` |
| Timeout reduction | Functions processing large payloads return `503 timeout`; tasks left half-complete | Immediately on long-running invocations | Cloud Monitoring: `function/execution_count{status="timeout"}` spike; correlate with deployment timestamp | Increase timeout: `gcloud functions deploy <name> --timeout=300s` |
| `--ingress-settings` change to `internal-only` | External callers receive `403 Forbidden`; previously working HTTP triggers break | Immediate on setting change | `gcloud functions describe <name> --format="value(httpsTrigger.securityLevel,ingressSettings)"`; compare before/after | Revert: `gcloud functions deploy <name> --ingress-settings=all` |
| Service account change | Function loses permissions to access GCP resources; `403 Permission Denied` on API calls | Immediate on deploy with new SA | Cloud Audit Logs: `functions.googleapis.com/functions.patch` event; new SA missing IAM bindings | Revert SA: `gcloud functions deploy <name> --service-account=<original-sa>`; or add missing IAM roles to new SA |
| Environment variable removal | Function fails with `KeyError` or `None` type error accessing deleted env var | Immediate on first invocation | Function error logs show `KeyError: 'EXPECTED_VAR'`; correlate with deployment env var diff | Re-add missing env var: `gcloud functions deploy <name> --set-env-vars KEY=VALUE` |
| Dependency version bump in `requirements.txt` | `ImportError` or `AttributeError` from changed API in new library version | Immediate on cold start after deploy | Compare `requirements.txt` diff in git; check library changelog for breaking changes | Pin previous version in `requirements.txt`; redeploy |
| Trigger type change (HTTP → Pub/Sub or vice versa) | Old trigger URL returns `404`; or Pub/Sub messages no longer invoke function | Immediate on deploy | `gcloud functions describe <name> --format="yaml(eventTrigger,httpsTrigger)"` diff | Redeploy with correct trigger; note: trigger type changes require delete and recreate for 1st gen |
| Region change | Function URL changes; DNS-based invocations fail; VPC Connector in old region cannot reach function | Immediate on first invocation using old URL | `gcloud functions list --format="table(name,region)"` shows new region; caller still using old URL | Update caller config with new URL; or redeploy in original region |
| 1st gen → 2nd gen migration | Cold start behavior differs; `FUNCTION_TARGET` env var required; networking model changes | During migration deployment | Compare `gcloud functions describe --gen2`; check for `FUNCTION_TARGET` in logs; validate timeout behavior | Keep 1st gen version running in parallel; validate 2nd gen before cutover traffic |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Duplicate Pub/Sub message delivery (at-least-once) | Check idempotency key in downstream storage: `gcloud pubsub subscriptions pull <sub> --auto-ack`; inspect message IDs | Same event processed twice; duplicate database rows; double-charged operations | Data integrity violation; financial or audit discrepancy | Implement idempotency check in function (e.g., Firestore transaction check on message ID before processing) |
| Partial write after function timeout | Downstream storage partially updated; function timed out mid-transaction | Cloud Monitoring: `function/execution_count{status="timeout"}`; downstream DB shows partial row | Stale or corrupt downstream state | Use transactional writes (Firestore transactions, Cloud Spanner); implement compensating transaction on retry |
| Config drift between function versions (traffic split) | Two revisions serving traffic with different env vars or logic; A/B behavior unintentional | During canary deployment with traffic split | `gcloud run services describe <function> --format="yaml(status.traffic)"`; compare active revision env vars | Consolidate traffic to single revision: `gcloud run services update-traffic <name> --to-latest`; investigate env var divergence |
| Secret rotation lag between Secret Manager and running instances | Warm instances hold old secret in memory; new instances get new secret; API calls fail from warm instances | Immediately after secret rotation until warm instances restart | Function logs: `401 Unauthorized` from some instances but not others; inconsistent error rate | Force instance recycling by redeploying: `gcloud functions deploy <name>` (no-op deploy); or lower `--max-instances` temporarily |
| Event ordering violation from Pub/Sub (unordered delivery) | Function processes message B before message A despite publish order; downstream state incorrect | Non-deterministic; depends on redelivery | Data processed in wrong order; incorrect final state | Enable Pub/Sub message ordering on the subscription; use ordering keys; design function for idempotent out-of-order processing |
| Cloud Storage trigger fires for same object twice (eventual consistency) | Function invoked twice for single object write; duplicate processing | During GCS eventual consistency window | Duplicate side effects in downstream | Use Firestore to track processed object names with `set({mergeFields})` before processing |
| Firestore document partially updated by concurrent function instances | Multiple function instances race to update same document; one update lost | Under high-concurrency invocations for same document | Lost updates; stale data | Use Firestore transactions: `db.runTransaction()`; or use field merge updates |
| Function reading stale data from Cloud Memorystore after eviction | Cache miss returns None; function falls through to empty state; incorrect response | After Redis eviction under memory pressure | Silent correctness failure | Always validate cache miss against Cloud SQL; never return None as valid cached state |
| Environment variable not yet propagated to all warm instances after update | Some instances use old value; others use new; inconsistent behavior | Immediately after `gcloud functions deploy` with new env var while instances still warm | Non-deterministic behavior across invocations | Redeploy to force all instances to restart; or use Secret Manager versioned secrets with explicit version pinning |
| Pub/Sub dead-letter topic accumulating without alerting | Messages in DLQ silently not processed; data pipeline has invisible gap | After consumer errors; DLQ configured but no alert | Silent data loss; downstream pipeline incomplete | Set alert on `pubsub/subscription/num_undelivered_messages` for DLQ topic; create a DLQ processor function |

## Runbook Decision Trees

```
Decision Tree 1: Cloud Functions 5xx Spike

Is the error rate spike across ALL functions in the project?
├── YES → Is there a GCP status incident? (https://status.cloud.google.com)
│         ├── YES → Open GCP support case; monitor status page; no action until GCP resolves
│         └── NO  → Check project quota: `gcloud functions list --regions=<region>` for quota errors
│                   ├── QUOTA HIT → `gcloud compute project-info describe --format="yaml(quotas)"`;
│                   │               request quota increase or reduce max-instances on bursty functions
│                   └── NO QUOTA  → Check VPC Connector if functions use internal VPC:
│                                   `gcloud compute networks vpc-access connectors list`
│                                   ├── CONNECTOR UNHEALTHY → Recreate connector; redeploy affected functions
│                                   └── CONNECTOR OK → Escalate to GCP support with trace IDs
└── NO  → Is the 5xx spike on a recently deployed function?
          ├── YES → Roll back immediately: `git checkout <prev>` + `gcloud functions deploy`
          │         Then check logs: `gcloud functions logs read <name> --limit=100`
          │         ├── ImportError / SyntaxError → Fix dependency; redeploy
          │         ├── 403 / Permission Denied → Check service account IAM bindings
          │         └── Secret Manager error → Verify secret version and SA access
          └── NO  → Is the error correlated with a specific event trigger?
                    ├── Pub/Sub → Check for poison-pill messages: inspect DLQ
                    │             `gcloud pubsub subscriptions pull <dlq-sub> --auto-ack --limit=5`
                    │             └── Poison messages found → filter or skip; add `--max-delivery-attempts`
                    └── HTTP   → Check if a downstream API is returning 5xx:
                                  `gcloud functions logs read <name> \| grep "HTTPError\|ConnectionError"`
                                  ├── Downstream failing → Implement circuit breaker; return cached response
                                  └── Internal error    → Add structured logging; reproduce locally
```

```
Decision Tree 2: Pub/Sub-Triggered Function Backlog Growing

Is oldest_unacked_message_age > 60 seconds?
├── YES → Are function executions still happening (execution_count rising)?
│         ├── YES → Are executions completing with errors?
│         │         ├── YES → What error type?
│         │         │         ├── Timeout         → Increase `--timeout`; or split work via Pub/Sub chaining
│         │         │         ├── 403             → Fix IAM; check service account; `gcloud functions describe`
│         │         │         ├── DB/API error    → Circuit-break; check downstream health; check Cloud SQL metrics
│         │         │         └── Poison message  → Enable DLQ; set `--max-delivery-attempts=5`
│         │         └── NO  → Executions completing OK but still slow?
│         │                   └── Scale bottleneck → Increase `--max-instances`; check subscription `ackDeadlineSeconds`
│         │                                          `gcloud pubsub subscriptions modify <sub> --ack-deadline=300`
│         └── NO  → Is the function deployed and ACTIVE?
│                   ├── NO  → Redeploy: `gcloud functions deploy <name> --source=<src> --region=<region>`
│                   └── YES → Is max-instances reached?
│                             ├── YES → Increase limit or address upstream message rate
│                             └── NO  → Check for IAM issue: `gcloud functions logs read <name> --limit=20`
└── NO  → Backlog under 60 s; likely transient spike. Monitor for 10 more minutes.
          └── Still growing after 10 min → Re-enter tree from top
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Retry storm from upstream caller without backoff | Pub/Sub or HTTP caller retries immediately on each 5xx; invocation count multiplied 10–100× | `gcloud monitoring metrics list` → `function/execution_count` rate vs expected; Cloud Logging: high frequency from single message ID | Project invocation quota exhausted; billing spike | Reduce `--max-instances` to throttle; configure Pub/Sub `--max-delivery-attempts`; drop excess | Enforce exponential backoff in all callers; set DLQ on subscriptions; alert on invocation rate > baseline × 5 |
| Infinite Pub/Sub re-trigger loop | Function writes to a GCS bucket or Pub/Sub topic that triggers itself; recursive invocation | Cloud Monitoring: `execution_count` growing exponentially; Cloud Logging: same message ID appearing repeatedly | Complete project quota exhaustion; billing runaway | Scale function to 0: `gcloud functions deploy <name> --max-instances=0`; or pause the Pub/Sub subscription | Design trigger topology to avoid cycles; audit trigger chains before deploying new functions |
| Oversized memory allocation across many instances | Function deployed with 4 GB memory but only needs 256 MB; many concurrent instances | Cloud Monitoring: `function/user_memory_bytes` well below `--memory` limit at peak | Unnecessary per-GB-second billing; cost 16× higher than needed | Redeploy with corrected memory: `gcloud functions deploy <name> --memory=256MB` | Right-size memory during load testing; review Cloud Monitoring `user_memory_bytes` p99 before production deploy |
| Long-running functions timing out and retrying (Pub/Sub) | Function takes 9 min; timeout set to 10 min; Pub/Sub ack deadline 600 s → redelivery before ack | Cloud Monitoring: `execution_count` doubles every timeout window; DLQ filling | Cost doubles; Pub/Sub backlog grows; DLQ accumulates | Increase ack deadline: `gcloud pubsub subscriptions modify <sub> --ack-deadline=600`; increase `--timeout` | Set Pub/Sub `ackDeadlineSeconds` > function `--timeout`; instrument function with periodic ack extension |
| Unoptimized function making N+1 API calls per message | Each invocation calls downstream API once per item in a batch; payload has 1 000 items → 1 000 API calls | Cloud Trace: span count per invocation; billing on downstream API rising | Downstream API quota exhausted; function execution time × N | Batch downstream calls; use APIs with batch endpoints | Code review for N+1 patterns; benchmark with realistic payload sizes before deploy |
| Functions triggered by Cloud Storage on bucket with high object churn | Every small write (logs, temp files) triggers a function; thousands of invocations per minute unintentionally | Cloud Monitoring: `execution_count` much higher than expected writes; Cloud Logging: trigger prefix matches temp objects | Quota exhaustion; cost spike | Add object name prefix/suffix filter on trigger: `gcloud functions deploy <name> --trigger-resource=<bucket> --trigger-event=google.storage.object.finalize --trigger-event-filters=resourceName=<prefix>/*` | Apply narrow prefix/suffix filters on GCS triggers; never trigger on root of high-churn buckets |
| Misconfigured `--concurrency` on 2nd gen causing serial processing | `--concurrency=1` set unintentionally; each instance handles one request; scale-out creates many idle instances | `gcloud run services describe <2nd-gen-function> --format="value(spec.template.spec.containerConcurrency)"` returns 1; instance count very high | Cost high due to many instances; latency also high | Reset: `gcloud functions deploy <name> --concurrency=80` | Explicitly document and review concurrency setting in IaC; alert if instance count > invocation count × 2 |
| All functions in a project sharing single SA with broad IAM roles | Not a cost issue — a security blast-radius issue; one function compromise exposes all GCP resources | `gcloud projects get-iam-policy <project> --flatten="bindings[].members" --filter="bindings.members:serviceAccount:<sa>"` shows `roles/editor` or `roles/owner` | Total project compromise if SA key leaked or function has SSRF | Immediately rotate SA key; revoke broad roles; apply per-function least-privilege SAs | Use a dedicated SA per function; grant only required roles (e.g., `roles/datastore.user`, not `roles/editor`) |
| Orphaned Cloud Functions never cleaned up after service deprecation | Old functions still running; consuming invocation quota; billed for idle instances with `--min-instances` | `gcloud functions list --regions=<region> --format="table(name,updateTime,status)"` shows functions last updated months ago | Quota waste; security risk from unpatched runtimes | Delete orphaned functions: `gcloud functions delete <name> --region=<region>` | Implement function ownership tags; quarterly audit of deployed functions vs active services in IaC |
| Cloud Scheduler triggering function at wrong interval (cron misconfiguration) | `*/1 * * * *` set instead of `0 * * * *`; function triggers every minute instead of hourly | `gcloud scheduler jobs describe <job> --format="value(schedule)"`; invocation count 60× expected | Cost 60× expected; potential downstream API quota exhaustion | Pause job: `gcloud scheduler jobs pause <job>`; fix schedule | Review cron expressions before deploy; alert if invocation rate deviates > 2× from expected baseline |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Cold start latency spike on low-traffic functions | p99 latency 3–15s periodically; p50 normal; users see intermittent slow responses | `gcloud logging read 'resource.type=cloud_function AND textPayload:"Function execution started"' --limit=200 --format=json \| jq '[.[] \| {time:.timestamp, text:.textPayload}]'` — look for gap between invocations before slow start | No `--min-instances`; function spun down after idle period; JVM or large runtime cold init | Set `--min-instances=1` for latency-sensitive functions: `gcloud functions deploy <name> --min-instances=1`; reduce runtime init (lazy loading, smaller dependencies) |
| Pub/Sub subscription backlog causing HOL blocking | Function processes messages slowly; backlog grows; oldest unacked message age > SLO | `gcloud pubsub subscriptions describe <sub> --format="value(name)"` then Cloud Monitoring: `pubsub.googleapis.com/subscription/oldest_unacked_message_age` > threshold | Single-threaded function unable to process at publisher rate; or function throwing errors causing retries | Increase `--concurrency` (2nd gen): `gcloud functions deploy <name> --concurrency=80`; or increase `--max-instances` to scale out |
| VPC Connector throughput saturation | Function calls to internal services time out or are slow; external calls unaffected | `gcloud monitoring metrics list --filter="metric.type=vpcaccess.googleapis.com/connector/sent_bytes_count"` — saturated at connector max; `gcloud compute networks vpc-access connectors describe <connector> --region=<region> \| grep throughput` | VPC Connector at max throughput (e300: 200Mbps, e700: 1Gbps); all egress traffic bottlenecked | Upgrade connector machine type: `gcloud compute networks vpc-access connectors create <new> --machine-type=e1-standard-4`; update function to use new connector |
| GC pressure in Node.js/Python runtime from large request payloads | Invocation latency high for large payloads; memory usage near limit; GC pauses visible in logs | `gcloud functions logs read <name> --limit=200 \| grep -i "memory\|gc"` — check for high `user_memory_bytes` in Cloud Monitoring; `cloudfunctions.googleapis.com/function/user_memory_bytes` p99 approaching limit | Large JSON request bodies parsed and held in memory; V8/CPython GC pausing | Increase `--memory=2GB`; stream large payloads instead of buffering; set explicit memory limit in code; lazy-load large libraries |
| Thread pool saturation in 2nd gen (Cloud Run-backed) function | Concurrent requests > `--concurrency` wait; response time grows linearly with queue depth | `gcloud run services describe <2nd-gen-fn-service> --region=<region> --format="value(status.observedGeneration)"`; Cloud Monitoring: `run.googleapis.com/request_count` vs `container/instance_count` — requests/instance growing | Concurrency limit reached; function doing synchronous blocking I/O (DB queries, HTTP calls) using all threads | Increase `--concurrency`; convert sync blocking calls to async (asyncio in Python, async/await in Node.js); increase `--max-instances` |
| CPU throttle on 128MB or 256MB memory function | Function CPU-bound steps slow; all functions below `--memory=512MB` share fractional vCPU | `gcloud functions describe <name> --format="value(availableMemoryMb)"` < 512; Cloud Monitoring: `cloudfunctions.googleapis.com/function/execution_times` p99 high for CPU-bound steps | GCF allocates CPU proportional to memory; 128MB = 200mCPU; insufficient for CPU-intensive work | Increase `--memory=1GB` to get 600mCPU, or `--memory=2GB` to get 1 full vCPU: `gcloud functions deploy <name> --memory=1GB` |
| Lock contention in Cloud Firestore transactions called from function | Function execution time grows; Firestore transaction retries visible in logs; p99 > 5s | `gcloud logging read 'resource.type=cloud_function AND textPayload:"transaction conflict"' --limit=50` | Multiple concurrent function invocations running Firestore transactions on same document | Use optimistic concurrency control; redesign to reduce document contention; shard counters with distributed counter pattern |
| Serialization overhead from large Pub/Sub message payload | Function invocation time includes > 500ms of JSON deserialization | `gcloud logging read 'resource.type=cloud_function AND resource.labels.function_name=<name>' --limit=100 --format=json \| jq '[.[] \| .textPayload]'` — check for slow steps at beginning of function | 1MB+ JSON Pub/Sub messages; Python `json.loads` or Node `JSON.parse` on every invocation | Use Protobuf or Avro for Pub/Sub message encoding; reduce payload size (include reference IDs, not full objects); cache deserialized objects via `functools.cache` |
| Batch size misconfiguration on Pub/Sub-triggered function | Function triggered 1000×/second for 1000-message batch instead of once per batch | `gcloud pubsub subscriptions describe <sub> --format="value(pushConfig)"` — `maxMessages` not set; Cloud Monitoring: `execution_count` >> message batch size | Push subscription delivering one message per function invocation; `maxMessages` defaults to 1 | For pull-based functions: configure `max_messages` in Pub/Sub pull call; for push: use batch via Eventarc with CloudEvents batching (2nd gen) |
| Downstream Cloud SQL connection latency inflating function duration | Function p99 latency high; subtracting compute time shows DB connection setup = 80% of latency | `gcloud logging read 'resource.type=cloud_function AND resource.labels.function_name=<name>' --limit=50 --format=json \| jq '[.[] \| select(.textPayload \| test("connecting to"))]'` | Cloud SQL connection via Unix socket has per-invocation setup cost when not pooled | Use `pg-pool` (Node) or `SQLAlchemy` connection pool; initialize DB connection in global scope (outside request handler) to reuse across warm invocations |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on custom domain Cloud Function endpoint | External clients get `NET::ERR_CERT_DATE_INVALID`; `curl https://<custom-domain>/<function>` returns certificate error | `echo \| openssl s_client -connect <custom-domain>:443 2>&1 \| grep -E "notAfter\|Verify"` | Google-managed cert auto-renewal failed; or custom cert uploaded to load balancer expired | For Google-managed cert: check cert status `gcloud compute ssl-certificates describe <cert>`; delete and recreate to force renewal; for custom cert: upload renewed cert |
| mTLS failure between Cloud Function and internal service after cert rotation | Function logs `ssl.SSLError: CERTIFICATE_VERIFY_FAILED`; other services unaffected | `gcloud functions logs read <name> --limit=100 \| grep -i "ssl\|certificate\|tls"` | Client trust bundle in function not updated with new server CA after rotation | Update Secret Manager secret containing CA cert; redeploy function to pick up new secret version; or set `--update-secrets` trigger |
| DNS resolution failure for Cloud SQL or Memorystore within VPC | Function logs `Name or service not known: <internal-host>` despite VPC Connector being active | `gcloud functions logs read <name> \| grep -i "resolve\|nxdomain\|unknown host"` | VPC Connector not routing DNS to Cloud DNS internal zone; function using public DNS resolver that doesn't know private hostnames | Set `--vpc-egress=all-traffic` on function: `gcloud functions deploy <name> --vpc-egress=all-traffic`; verify Cloud DNS internal zone has records for private hostnames |
| TCP connection exhaustion when Cloud SQL proxy not used | Functions making direct TCP connections to Cloud SQL exhaust connection pool | Cloud SQL metrics: `database/postgresql/num_backends` at `max_connections`; function logs `connection pool exhausted` | Each function instance opens new DB connection; max_connections (e.g., 100) hit with 100+ concurrent instances | Mandatory: use Cloud SQL Auth Proxy or Cloud SQL Connector library; set `max_connections` in connection pool ≤ `Cloud_SQL_max_connections / max_instances` |
| Load balancer health check misconfiguration on 2nd gen function behind GFE | 2nd gen function returns 200 but load balancer marks it unhealthy; 502 errors to clients | `gcloud compute backend-services get-health <backend-svc> --global`; check health check config: `gcloud compute health-checks describe <hc>` | Health check path wrong (e.g., `/` returns 404 for function with specific path) or timeout too short for cold start | Update health check: `gcloud compute health-checks update http <hc> --request-path=/<function-path> --check-interval=30`; increase timeout to accommodate cold starts |
| Packet loss on VPC Connector causing intermittent function-to-internal-service failures | Function calls to internal services fail ~1% of requests; external API calls unaffected | `gcloud logging read 'resource.type=cloud_function AND severity>=WARNING' --limit=100 \| grep -i "timeout\|reset\|connection"` correlating with connector metrics | VPC Connector VM network issue; or oversaturation causing drops | Check connector health: `gcloud compute networks vpc-access connectors describe <connector> --region=<region>`; recreate if unhealthy; implement retry with exponential backoff in function code |
| MTU mismatch between VPC Connector and target service | Large response payloads from internal service silently truncated or cause TCP resets | Test from function: `import subprocess; subprocess.run(["ping", "-M", "do", "-s", "1400", "<internal-ip>"])` — check for fragmentation in logs | Functions receive incomplete data from internal services only for large payloads | Verify VPC Connector uses MTU matching the VPC (typically 1460 for GCP); set explicit MTU in connector or use jumbo frames if both endpoints support it |
| Firewall rule blocking function egress to Cloud APIs after VPC-SC policy change | Function starts failing with `403 Request is prohibited by organization's policy`; previously worked | `gcloud logging read 'resource.type=cloud_function AND severity=ERROR' --limit=50 \| grep -i "403\|policy\|VPC Service Controls"` | VPC Service Controls perimeter added; function's service account not in access policy | Add function service account to VPC-SC access level: `gcloud access-context-manager perimeters update <perimeter> --add-access-levels=<level>`; or add service account to allowed list |
| SSL handshake timeout calling external HTTPS API from function | Function times out with `SSLError: _ssl.c:... The handshake operation timed out`; only from function, not locally | `gcloud functions logs read <name> --limit=100 \| grep -i "ssl.*timeout\|handshake"`; test with lower timeout: set `--timeout` lower than default to surface TLS issues | External API TLS endpoint slow to complete handshake; possibly filtering by cloud egress IPs | Use Cloud NAT for stable egress IP; add retry with timeout; consider using VPC Connector with Cloud NAT for predictable IP |
| Connection reset after function instance recycled by GCF runtime | Persistent connections (gRPC, WebSocket) from callers dropped when GCF recycles instance | `gcloud logging read 'resource.type=cloud_function AND textPayload:"connection reset"' --limit=50` | GCF instance recycled after `--timeout` or memory limit; persistent connections not handled gracefully | Implement client-side reconnect logic with backoff; use Pub/Sub or Cloud Tasks for long-running operations instead of direct function calls |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of Cloud Function instance | Function exits with `memory limit exceeded`; error logged as `Error: memory limit of XXX MB exceeded` | Cloud Monitoring: `cloudfunctions.googleapis.com/function/user_memory_bytes` p99 at limit; `gcloud functions logs read <name> --limit=100 \| grep -i "memory limit\|oom"` | Increase memory: `gcloud functions deploy <name> --memory=2GB`; profile memory usage with `memory_profiler` (Python) or `--inspect` (Node) | Set memory alert at 80% of configured limit; lazy-load large models/configs; stream large datasets instead of buffering |
| Disk full on ephemeral `/tmp` filesystem | Function fails with `No space left on device` when writing temp files | `gcloud functions logs read <name> --limit=100 \| grep -i "no space\|ENOSPC"`; add instrumentation: `import shutil; shutil.disk_usage('/tmp')` | Functions have 512MB `/tmp` (512MB default) or up to 10GB configurable (2nd gen): `gcloud functions deploy <name> --gen2 --add-volume=name=tmp,type=ephemeral,size=10Gi`; clean up temp files explicitly in `finally` block | Stream files to GCS instead of buffering in `/tmp`; explicitly delete temp files; use `--ephemeral-storage-size=10GB` (2nd gen) |
| Disk full on log partition (Cloud Logging sink to GCS) | GCS log sink bucket fills; new logs dropped silently; `_Required` log bucket quota exceeded | `gcloud logging sinks describe <sink> \| grep destination`; check GCS bucket: `gsutil du -sh gs://<log-bucket>/`; Cloud Logging quota: `gcloud logging quota-info` | Pause or delete old log sink files: `gsutil -m rm -r gs://<log-bucket>/<old-prefix>`; set GCS lifecycle policy: `gsutil lifecycle set lifecycle.json gs://<log-bucket>` | Set GCS lifecycle policy with `Age` condition (e.g., 90 days); alert when log bucket usage > 80% of quota; use Cloud Logging log exclusions to reduce volume |
| File descriptor exhaustion in long-lived warm instances | Function fails with `OSError: [Errno 24] Too many open files` after running for hours | Add instrumentation: `import resource; resource.getrlimit(resource.RLIMIT_NOFILE)` and `open('/proc/self/fd/')` count; or `gcloud functions logs read <name> \| grep "Too many open files"` | Redeploy function (forces instance recycle): `gcloud functions deploy <name>`; fix FD leak in code (ensure all file handles closed in `finally`/context managers) | Use context managers (`with open(...) as f`); profile FD usage in load tests; set alarm on memory growth over time (FD leaks often correlate) |
| Inode exhaustion in ephemeral storage from many small temp files | `/tmp` filesystem fails despite space available; function errors with `No space left on device` | Add instrumentation: `import os; os.statvfs('/tmp').f_files - os.statvfs('/tmp').f_ffree`; or `ls /tmp \| wc -l` logged at function start | Redeploy to recycle instance; fix code to clean up temp files; batch small file writes into single archive | Use `tempfile.NamedTemporaryFile(delete=True)` to ensure cleanup; avoid creating thousands of small files in `/tmp`; write to GCS directly |
| CPU throttle — function under-provisioned for CPU-intensive work | Function execution time high; response slow; CPU-bound operations slower than expected | Cloud Monitoring: `cloudfunctions.googleapis.com/function/execution_times` p99 high; `--memory` low (128MB = 200mCPU) | Increase `--memory` to scale CPU proportionally: `gcloud functions deploy <name> --memory=4GB` (for 2 vCPU); or use `--cpu=2` flag (2nd gen) | For CPU-intensive functions (ML inference, compression, cryptography): always provision ≥ 1GB memory or explicitly set `--cpu`; benchmark CPU-bound steps |
| Swap exhaustion on underlying GCF host (Cloud Run-backed 2nd gen) | Extremely slow function execution; container not OOM-killed but nearly so | Cloud Monitoring: `run.googleapis.com/container/memory/utilizations` approaching 1.0; execution time > 10× normal | Increase memory: `gcloud functions deploy <name> --memory=4GB`; reduce number of concurrent instances via `--max-instances` to prevent memory pressure on host | GCF (backed by Cloud Run) does not use swap; if memory is exhausted the container is OOM-killed; size memory correctly |
| Pub/Sub acknowledgment deadline exceeded causing repeated function invocations | Same message processed 10+ times; function processing time > `ackDeadlineSeconds` | `gcloud pubsub subscriptions describe <sub> --format="value(ackDeadlineSeconds)"`; Cloud Monitoring: `pubsub.googleapis.com/subscription/dead_letter_message_count` growing | Function takes longer than ACK deadline (default 10–600s); Pub/Sub redelivers | Increase ACK deadline: `gcloud pubsub subscriptions modify-ack-deadline <sub> --ack-id=<id> --ack-deadline=600`; or increase `--timeout` and subscription `ackDeadlineSeconds` | Set subscription `ackDeadlineSeconds` = function `--timeout` + 30s buffer; implement dead-letter topic for poison messages |
| Network socket buffer exhaustion during high-volume HTTP response streaming | Function times out or truncates responses for large data exports | Cloud Monitoring: `cloudfunctions.googleapis.com/function/execution_times` spike for specific endpoints; `gcloud functions logs read <name> \| grep -i "socket\|buffer\|write"` | GCF HTTP response buffering limits for streaming; underlying socket buffer full | Use GCS signed URLs for large file downloads instead of streaming through function; paginate responses; use `Transfer-Encoding: chunked` for streaming |
| Ephemeral port exhaustion from function calling many downstream services | Function fails with `OSError: [Errno 99] Cannot assign requested address` | Add log: `import subprocess; subprocess.run(['ss', '-s'])` — check TIME-WAIT count; `gcloud functions logs read <name> \| grep "errno 99\|EADDRNOTAVAIL"` | Function making hundreds of short-lived HTTP connections per invocation; TIME-WAIT pool exhausted | Use `requests.Session()` or `httpx.Client()` with connection pooling and keep-alive; reuse HTTP client across invocations (global scope initialization) |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation — Pub/Sub at-least-once redelivery causes duplicate processing | Message processed twice; downstream resource created twice (e.g., duplicate GCS object, duplicate DB row) | `gcloud pubsub subscriptions describe <sub> --format="value(messageRetentionDuration)"`; check for duplicate message IDs: `gcloud logging read 'resource.type=cloud_function AND resource.labels.function_name=<name>' --limit=200 --format=json \| jq '[.[] \| .jsonPayload.message_id] \| group_by(.) \| map(select(length>1))'` | Duplicate records; idempotency broken; financial or inventory miscounts | Implement idempotency key in Firestore/Cloud Spanner using message ID: check-and-set before processing; use Pub/Sub exactly-once delivery (enable `enableExactlyOnceDelivery`) |
| Saga failure — multi-step workflow partially complete after function timeout | Cloud Workflow or Pub/Sub chained functions: step 1 writes to GCS, step 2 (function) times out before writing to Firestore | `gcloud logging read 'resource.type=cloud_function AND resource.labels.function_name=<step2-name> AND severity=ERROR' --limit=50 \| grep timeout`; check GCS: `gsutil ls gs://<bucket>/<prefix>/` for orphan files | Partial data in GCS without corresponding Firestore record; downstream consumers see incomplete state | Implement compensating step: Cloud Workflow `try/except` with rollback step that deletes orphan GCS file; or design for idempotent re-runs that detect and complete partial state |
| Message replay causing GCS object overwrites | Kafka/Pub/Sub topic replayed; function re-processes old messages and overwrites GCS objects with stale data | `gsutil stat gs://<bucket>/<object>` — check `Updated` timestamp vs expected; `gcloud pubsub subscriptions seek <sub> --time=<timestamp>` — verify replay was triggered | GCS objects contain stale data; downstream pipelines reading wrong version | Restore GCS object from versioning: `gsutil cp gs://<bucket>/<object>#<generation> gs://<bucket>/<object>`; enable GCS object versioning to allow recovery |
| Out-of-order Pub/Sub message processing causing stale state write | Function processes `UPDATE` message before `CREATE` message (different partitions/ordering keys) | Cloud Monitoring: `pubsub.googleapis.com/subscription/oldest_unacked_message_age`; `gcloud logging read 'resource.type=cloud_function AND textPayload:"not found"' --limit=50` — NOT FOUND errors on UPDATE operations | Stale writes; resource updated before creation; downstream state machine inconsistency | Enable Pub/Sub message ordering by setting `orderingKey` on publisher; use Cloud Tasks with explicit sequencing; or implement version-check in function before writing |
| At-least-once Eventarc trigger causing duplicate Cloud Storage trigger | GCS object finalize event triggers function twice (Eventarc redelivery) | `gcloud logging read 'resource.type=cloud_function AND resource.labels.function_name=<name>' --limit=100 --format=json \| jq '[.[] \| select(.jsonPayload.eventId != null) \| .jsonPayload.eventId] \| group_by(.) \| map(select(length>1))'` | Duplicate processing of uploaded files; transformed output written twice | Check Cloud Event `id` field in function handler and record in Firestore/Memorystore; skip if already processed: `if firestore.get(event_id): return` |
| Compensating transaction failure — Cloud Spanner transaction rollback fails | Function initiating Spanner transaction fails mid-commit; rollback attempt also fails due to timeout | `gcloud logging read 'resource.type=cloud_function AND textPayload:"ABORTED\|rollback failed"' --limit=50`; Cloud Spanner console: check for uncommitted transactions | Data in partially committed state in Spanner; reads may see inconsistent data | Spanner auto-rolls back uncommitted transactions after timeout; verify with: `gcloud spanner databases execute-sql <db> --sql="SELECT * FROM INFORMATION_SCHEMA.TRANSACTIONS WHERE STATUS='ACTIVE'"` — should clear automatically |
| Distributed lock expiry in Cloud Memorystore Redis lock mid-operation | Function acquires Redis lock for long operation (e.g., batch DB update); lock TTL expires; second function instance acquires lock; both run concurrently | `gcloud redis instances describe <instance> --region=<region> \| grep -E "currentLocationId\|memorySizeGb"`; `redis-cli -h <host> TTL <lock-key>` — TTL near 0 during operation | Concurrent execution of exclusive operation; data corruption if operation is not idempotent | Implement lock renewal (lock heartbeat) in function using `redis-cli SET <lock-key> <value> XX KEEPTTL`; or use Redlock algorithm; increase lock TTL to > worst-case operation time |
| Cross-function deadlock via Firestore transaction chaining | Function A holds Firestore transaction on doc X waiting for doc Y; Function B holds transaction on doc Y waiting for doc X | `gcloud logging read 'resource.type=cloud_function AND textPayload:"ALREADY_EXISTS\|contention\|deadline"' --limit=50`; Firestore console: check for hot documents | Both functions timeout; Firestore returns `ABORTED`; both retry and deadlock again | Break cycle: redesign one transaction to use a different document ordering; use Firestore batched writes instead of transactions for idempotent operations; add jitter to retry delay |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — high-concurrency function saturating project's Cloud Function quota | Cloud Monitoring: `cloudfunctions.googleapis.com/function/active_instances` at project max; one function monopolizing slots | Other functions in project cannot scale; cold starts increase | `gcloud functions deploy <noisy-fn> --max-instances=10 --region=<region>` to cap | Set `--max-instances` on all functions; separate noisy functions to dedicated GCP project with own quota |
| Memory pressure — one function leaking memory across warm instances causing OOM kills on shared host | Cloud Monitoring: `user_memory_bytes` for specific function consistently at 100% across all instances; OOM restarts | Other functions on same underlying Cloud Run instances (2nd gen) unaffected but host memory pressure increases cold start rate | Redeploy to force instance recycling: `gcloud functions deploy <name> --region=<region>` (no-op deploy) | Fix memory leak; increase `--memory` as temporary measure; set memory alert at 85% to detect leaks early |
| Disk I/O saturation — function writing large files to `/tmp` on shared instance | Cloud Monitoring: execution times spike for `/tmp`-heavy functions; `ENOSPC` errors appear in logs | Other functions sharing the ephemeral storage on same underlying instance may see failures | Redeploy affected function to cycle instances: `gcloud functions deploy <name> --region=<region>` | Stream data to GCS instead of buffering in `/tmp`; set explicit cleanup in `finally` blocks; use 2nd gen with separate ephemeral volume |
| Network bandwidth monopoly — function downloading large ML models on every cold start | Cloud Monitoring: execution times bimodal (warm: 100ms, cold: 30s); cold start instances spike | Other function cold starts slower due to shared egress bandwidth | Pre-cache model in GCS and download asynchronously; use `--min-instances=1` to reduce cold starts | Store large assets in GCS; download to `/tmp` once per instance lifecycle in global scope (outside handler); use signed URL for large downloads |
| Connection pool starvation — function triggering 1000 concurrent Cloud SQL connections | Cloud SQL `database/postgresql/num_backends` at `max_connections`; function invocations failing with connection errors | All Cloud SQL clients (other functions, Cloud Run services) in project cannot connect | Reduce max concurrent function instances: `gcloud functions deploy <name> --max-instances=20 --region=<region>` | Mandatory Cloud SQL Proxy for all functions; set connection pool size = `floor(max_connections / max_instances)` in pool config |
| Quota enforcement gap — no per-function invocation rate limit | One misconfigured Pub/Sub push subscription delivers 10K messages/second; function autoscales without bound | Project-level `cloudfunctions.googleapis.com/function` quota exhausted; other functions throttled | `gcloud pubsub subscriptions modify-push-config <sub> --push-endpoint="" --region=<region>` to stop delivery | Set per-Pub/Sub-subscription `maxOutstandingMessages` limit; add `--max-instances` cap; set Cloud Monitoring budget alert for invocation costs |
| Cross-tenant data isolation gap — functions in same project sharing Secret Manager access | `gcloud secrets get-iam-policy <shared-secret>` — multiple function SAs have access | Function for Tenant A can read Tenant B's secret if both SAs are in same project with broad Secret Manager IAM | Audit: `gcloud secrets list --format=json \| jq '.[] \| .name'` then check each secret's IAM | Create per-function service accounts; grant `roles/secretmanager.secretAccessor` only on specific secrets; use resource-level IAM not project-level |
| Rate limit bypass — function using multiple Pub/Sub subscriptions to bypass single-subscription rate limit | Cloud Monitoring: total invocations 10× higher than expected from one subscription; `gcloud pubsub subscriptions list \| grep <topic>` shows multiple subs | Per-subscription rate limits bypassed; project quota consumed faster | `gcloud pubsub subscriptions delete <extra-sub>` for unauthorized subscriptions | Enforce subscription count per topic via OPA/Config Controller; audit subscriptions regularly; set project-level invocation rate alert |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure — Cloud Monitoring metrics lag | Dashboards show `execution_count` from 5 minutes ago; real-time alert fires too late | Cloud Monitoring has 60-90s ingestion lag for Cloud Functions metrics; alerting window too short | Use Cloud Logging log-based metric for real-time alerting: filter `severity=ERROR` in function logs → log metric → alert | Use log-based metrics for latency-sensitive alerts; set alert evaluation window to 5 minutes to account for monitoring lag |
| Trace sampling gap — intermittent failures not traced | Function fails 0.1% of invocations but Cloud Trace captures none | Default Cloud Trace sampling is 0.1% for high-volume functions; rare errors fall outside sample | Enable error-triggered sampling: `if error: tracer.current_span().add_event("error")` with force-sample flag; or `gcloud functions deploy <name> --set-env-vars=GOOGLE_CLOUD_TRACE_EXPORTER=cloud_trace` | Use tail-based sampling: always sample requests with errors or latency > 2×p50; configure OpenTelemetry sampler to force-sample on error |
| Log pipeline silent drop — Cloud Logging quota exceeded | Function error logs missing from Cloud Logging; `_Required` bucket full | High-volume function writing structured JSON logs exceeds project's 1GB/day free tier; ingestion paused | Check quota: `gcloud logging quota-info`; search for dropped entries: `gcloud logging read 'resource.type=cloud_function AND logName:_Required' --limit=10` | Create log exclusion to drop DEBUG/INFO: `gcloud logging exclusions create low-severity --log-filter='resource.type=cloud_function AND severity<WARNING'`; increase log quota or route to GCS sink |
| Alert rule misconfiguration — error rate alert on non-HTTP functions | Pub/Sub-triggered function failures not alerted; team unaware of failures for hours | Alert configured on HTTP 5xx responses; Pub/Sub-triggered functions don't generate HTTP responses; errors only in logs | Check Pub/Sub DLQ: `gcloud pubsub subscriptions pull <dlq-sub> --limit=10 --auto-ack=false`; manual log check: `gcloud functions logs read <name> \| grep ERROR` | Create log-based alert on `severity=ERROR` for function logs; or use `cloudfunctions.googleapis.com/function/execution_count{status=error}` metric for all trigger types |
| Cardinality explosion — per-request custom metric labels | Cloud Monitoring `TimeSeries` quota exceeded; custom dashboards stop showing data | Function writing custom metric with `request_id` as label dimension; millions of unique label values | Query without request_id: use `sum` aggregation in Cloud Monitoring; disable custom metric temporarily | Remove high-cardinality labels from custom metrics; use only `function_name`, `region`, `status` as label dimensions; aggregate per-request data in the function before writing |
| Missing health endpoint — no external availability monitoring | Function down for 2 hours before user reports; no alert fired | No external blackbox monitor for Cloud Function URL; only internal Cloud Monitoring which relies on invocations | Deploy Cloud Monitoring uptime check: `gcloud monitoring uptime-check-configs create` targeting `https://<region>-<project>.cloudfunctions.net/<name>` | Create uptime check: `gcloud beta monitoring uptime-check-configs create --display-name="<fn> availability" --http-check-path=/<name> --host=<region>-<project>.cloudfunctions.net`; alert on check failure |
| Instrumentation gap — cold start latency not separated from execution latency | Users report slow responses; p99 high; but real execution is fast; cold starts invisible in aggregates | `execution_times` metric includes cold start initialization in first-request latency; no separate cold start metric in older 1st gen | Check cold start count: `gcloud logging read 'resource.type=cloud_function AND textPayload:"Function execution started"' --limit=100 --format=json \| jq 'length'` vs invocation count | Use 2nd gen Cloud Functions: Cloud Run exposes `container/startup_latencies` separately; add startup timing log: `print(f"init_duration_ms={int((time.time()-_START)*1000)}")` at module level |
| Alertmanager / PagerDuty outage — Cloud Monitoring alert notification delivery failure | Function incidents go unnoticed; alert fired in Cloud Monitoring but no PagerDuty page | PagerDuty webhook endpoint changed; Cloud Monitoring notification channel not updated | Check notification channel status: `gcloud alpha monitoring notification-channels describe <channel-id> \| grep enabled`; test: `gcloud alpha monitoring notification-channels send-verification-code <channel-id>` | Verify notification channels monthly with `send-verification-code`; configure backup email + PagerDuty; use Pub/Sub as notification channel for programmatic handling |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Cloud Functions runtime version upgrade — deprecated API removed | Function fails with `AttributeError` or `TypeError` after runtime upgrade (e.g., Python 3.8 → 3.12) | `gcloud functions logs read <name> --limit=100 \| grep -i "attributeerror\|importerror\|deprecated"` | `gcloud functions deploy <name> --runtime=python38 --region=<region>` to downgrade runtime | Test on new runtime in staging before upgrading production; run `pylint --py-version=3.12` locally; use `--runtime` pinned value in IaC (Terraform/Pulumi) |
| Dependency upgrade breaking function — new package version incompatible | Function starts failing after `requirements.txt` changes; previously working | `gcloud functions logs read <name> --limit=50 \| grep -i "importerror\|modulenotfounderror\|incompatible"` | Revert `requirements.txt` to previous version: pin `package==<previous-version>`; redeploy: `gcloud functions deploy <name> --region=<region>` | Pin all dependencies to exact versions in `requirements.txt`; use `pip-compile` to lock transitive dependencies; test dependency upgrades in isolation |
| 1st gen to 2nd gen migration — behavior difference causing errors | Concurrency semantics differ; 2nd gen handles multiple requests per instance; code using global state breaks | `gcloud functions describe <name> --gen2 --region=<region> --format="value(serviceConfig.uri)"`; `gcloud functions logs read <name> --gen2 --limit=100 \| grep -i "concurrent\|race\|global"` | `gcloud functions deploy <name> --no-gen2 --region=<region>` to revert to 1st gen | Review code for non-thread-safe global state before 2nd gen migration; add thread safety (locks, per-request context); load test with concurrent requests in staging |
| Container image migration — custom container breaking environment | 2nd gen function using custom container fails after base image upgrade | `gcloud functions logs read <name> --gen2 --limit=50 \| grep -i "container\|image\|entrypoint"` | `gcloud functions deploy <name> --source=<previous-image-digest> --region=<region>` | Pin container image digest in Cloud Build; test custom containers locally with `functions-framework`; validate with `docker run -p 8080:8080 <image>` before deploying |
| VPC Connector migration — new connector causing routing change | After switching to new VPC Connector, function cannot reach internal services | `gcloud functions logs read <name> --limit=100 \| grep -i "connection refused\|name or service"` — internal hostnames failing; `gcloud compute networks vpc-access connectors describe <new-connector> --region=<region>` | `gcloud functions deploy <name> --vpc-connector=<old-connector> --region=<region>` | Test internal connectivity from new connector before switching traffic: create test function with `--vpc-connector=<new>` and verify internal service access |
| IAM policy migration — removing legacy invoker binding breaks scheduled jobs | Cloud Scheduler stops triggering function after IAM cleanup removes old invoker | `gcloud logging read 'protoPayload.serviceName="cloudfunctions.googleapis.com" AND protoPayload.status.code!=0' --freshness=24h --limit=10`; `gcloud functions get-iam-policy <name> --region=<region>` | Re-add scheduler SA: `gcloud functions add-iam-policy-binding <name> --member="serviceAccount:<scheduler-sa>" --role="roles/cloudfunctions.invoker" --region=<region>` | Audit all IAM bindings before removal; test scheduled invocations after any IAM change; document all SA bindings in IaC |
| Environment variable migration to Secret Manager — missing secret version | Function fails with `secret not found` after switching from env vars to Secret Manager | `gcloud functions logs read <name> --limit=50 \| grep -i "secret\|notfound\|permission"` — check if SA has `secretmanager.secretAccessor` | Re-add env var temporarily: `gcloud functions deploy <name> --set-env-vars=KEY=value --region=<region>`; fix secret: `gcloud secrets add-iam-policy-binding <secret> --member="serviceAccount:<sa>" --role="roles/secretmanager.secretAccessor"` | Validate secret access before deploying: `gcloud secrets versions access latest --secret=<name>`; add secret version check to deployment pipeline |
| Trigger migration — Pub/Sub to Eventarc causing event format change | Function receives Eventarc CloudEvents format instead of raw Pub/Sub envelopes; JSON parsing fails | `gcloud functions logs read <name> --limit=100 \| grep -i "keyerror\|message\|data"` — function expects `event['data']` but receives CloudEvent `data` field | Deploy old Pub/Sub trigger function alongside new: `gcloud functions deploy <name>-legacy --trigger-topic=<topic> --region=<region>` | Update event parsing code to handle CloudEvents `io.cloudevents.CloudEvent` format before migration; test with `functions-framework --target=<fn> --signature-type=cloudevent` locally |

## Kernel/OS & Host-Level Failure Patterns

| Failure | Cloud Functions-Specific Symptom | Why It Happens | Detection Command | Remediation |
|---------|----------------------------------|----------------|-------------------|-------------|
| OOM killer terminates function instance | Function invocation returns `Memory limit exceeded`; Cloud Logging shows `RESOURCE_EXHAUSTED`; cold starts spike as instances are killed and recreated | Function memory allocation exceeds configured limit (e.g., 256MB default); large payload processing or memory leak in warm instances | `gcloud functions logs read <name> --region=<region> --limit=50 \| grep -i "memory limit"`; `gcloud monitoring metrics list --filter='metric.type="cloudfunctions.googleapis.com/function/instance_count"'` | Increase memory: `gcloud functions deploy <name> --memory=1024MB --region=<region>`; profile memory with `tracemalloc` (Python) or `--max-old-space-size` (Node.js); reduce payload size; implement streaming for large data |
| Inode exhaustion in function `/tmp` directory | Function fails with `OSError: [Errno 28] No space left on device` when writing to `/tmp`; subsequent invocations on same instance fail | Cloud Functions provides limited `/tmp` (in-memory tmpfs); warm instances accumulate temp files across invocations without cleanup | `gcloud functions logs read <name> --region=<region> --limit=100 \| grep -i "no space\|ENOSPC"`; check function code for `/tmp` writes without cleanup | Clean `/tmp` at function start: `import shutil; shutil.rmtree('/tmp/workdir', ignore_errors=True)`; use Cloud Storage instead of `/tmp` for large files; reduce `/tmp` usage; increase memory (tmpfs size scales with memory allocation) |
| CPU steal causing function timeout on shared infrastructure | Function execution time spikes intermittently; `DEADLINE_EXCEEDED` errors; same function sometimes fast, sometimes slow | Cloud Functions runs on shared multi-tenant infrastructure; CPU steal from noisy neighbors causes unpredictable latency | `gcloud functions logs read <name> --region=<region> --limit=100 \| grep "DEADLINE_EXCEEDED"`; compare execution times: `gcloud logging read 'resource.type="cloud_function" AND resource.labels.function_name="<name>"' --format=json \| jq '[.[] \| .jsonPayload.execution_time_ms] \| {min, max, avg: (add/length)}'` | Increase function timeout: `gcloud functions deploy <name> --timeout=540 --region=<region>`; use 2nd gen (Cloud Run-based) for more consistent CPU: `gcloud functions deploy <name> --gen2 --cpu=1 --region=<region>`; implement retry logic in callers |
| NTP skew causing timestamp anomalies in function logs | Function-generated timestamps differ from Cloud Logging timestamps; event ordering incorrect; time-sensitive logic (token expiry, cache TTL) fails | Cloud Functions instances inherit platform NTP; rare NTP drift on underlying infrastructure; function code using local clock instead of server time | `gcloud logging read 'resource.type="cloud_function" AND resource.labels.function_name="<name>"' --format=json \| jq '.[] \| {cloud_time: .timestamp, fn_time: .jsonPayload.timestamp}'` — compare timestamps | Use server-provided timestamps instead of local `datetime.now()`; for time-sensitive operations use Cloud Scheduler's invocation time from headers; implement clock-drift tolerance in TTL calculations |
| File descriptor exhaustion in function instance | Function fails with `socket: too many open files`; HTTP calls and database connections fail; warm instance becomes unusable | Warm function instances accumulate open connections (HTTP keep-alive, database pools) across invocations without proper cleanup | `gcloud functions logs read <name> --region=<region> --limit=100 \| grep -i "too many open files\|EMFILE"`; check connection pool settings in function code | Close connections explicitly in function handler; use connection pooling with max limits; implement `atexit` handler for cleanup; for Python: `requests.Session()` with `max_connections=10`; reduce `--max-instances` to limit total fd usage |
| TCP conntrack saturation on VPC connector | Functions using VPC connector fail to reach internal services; `Connection timed out` errors; connector throughput drops | VPC Serverless Access connector has limited conntrack capacity; high-concurrency functions create many short-lived TCP connections to internal services | `gcloud functions logs read <name> --region=<region> --limit=100 \| grep "Connection timed out"`; `gcloud compute networks vpc-access connectors describe <connector> --region=<region>` — check `throughput` and `connected_instances` | Scale up VPC connector: `gcloud compute networks vpc-access connectors update <connector> --region=<region> --min-instances=3 --max-instances=10`; use connection pooling in function code; reduce connection churn with keep-alive |
| Kernel panic on underlying Cloud Functions infrastructure | Functions in a region stop executing; invocations return `INTERNAL` error; Cloud Status page shows incident | GCP infrastructure issue affecting compute nodes running Cloud Functions; rare platform-level failure | Check GCP status: `curl -s "https://status.cloud.google.com/incidents.json" \| jq '[.[] \| select(.service_name=="Google Cloud Functions")] \| first'`; `gcloud functions logs read <name> --region=<region> --limit=10 \| grep "INTERNAL"` | Deploy function to multiple regions with Cloud Load Balancing: `gcloud functions deploy <name> --region=us-central1` AND `gcloud functions deploy <name> --region=us-east1`; use Traffic Director for regional failover; monitor GCP status page |
| NUMA imbalance causing cold start latency variance | Cold start times vary 3-5x between invocations in same region; some instances start in 500ms, others in 2500ms; no code change | Cloud Functions instances scheduled on VMs with different NUMA characteristics; memory allocation across NUMA boundaries during instance init | Compare cold start times: `gcloud logging read 'resource.type="cloud_function" AND textPayload:"Function execution started"' --format=json \| jq '[.[] \| .jsonPayload.startup_latency_ms] \| {min, max, p50: sort[length/2], p99: sort[(length*99/100)]}'` | Use 2nd gen Cloud Functions with `--min-instances=1` to keep warm instances: `gcloud functions deploy <name> --gen2 --min-instances=1 --region=<region>`; use provisioned concurrency; optimize initialization code to reduce cold start impact |

## Deployment Pipeline & GitOps Failure Patterns

| Failure | Cloud Functions-Specific Symptom | Why It Happens | Detection Command | Remediation |
|---------|----------------------------------|----------------|-------------------|-------------|
| Image pull failure for 2nd gen Cloud Functions container | Function deployment fails with `Failed to pull image`; existing instances still serving but new deploys blocked | Artifact Registry outage or IAM permission revoked for Cloud Build service account; custom container image deleted from registry | `gcloud functions describe <name> --gen2 --region=<region> --format="value(serviceConfig.uri)"`; `gcloud builds list --limit=5 --filter="status=FAILURE"` | Fix IAM: `gcloud projects add-iam-policy-binding <project> --member="serviceAccount:<build-sa>" --role="roles/artifactregistry.reader"`; verify image exists: `gcloud artifacts docker images list <registry>/<repo>` |
| Auth failure for Cloud Functions deployment service account | `gcloud functions deploy` fails with `PERMISSION_DENIED`; Cloud Build cannot build function source | Cloud Build or Cloud Functions service account missing IAM roles; org policy restricting function deployment | `gcloud functions deploy <name> --region=<region> 2>&1 \| grep "PERMISSION_DENIED"`; `gcloud projects get-iam-policy <project> --flatten="bindings[].members" --filter="bindings.members:serviceAccount" \| grep -i "cloudfunctions\|cloudbuild"` | Grant required roles: `gcloud projects add-iam-policy-binding <project> --member="serviceAccount:<project-number>@cloudbuild.gserviceaccount.com" --role="roles/cloudfunctions.developer"`; check org policies: `gcloud org-policies list --project=<project>` |
| Helm/Terraform drift between Git and live Cloud Functions config | Terraform plan shows drift; function has different memory, timeout, or env vars than in IaC; manual `gcloud functions deploy` overrode Terraform state | Emergency config change via `gcloud functions deploy` or Console without updating Terraform; state mismatch | `terraform plan -target=google_cloudfunctions_function.<name>` — shows drift; `gcloud functions describe <name> --region=<region> --format=json \| diff - terraform-state.json` | Run `terraform apply` to reconcile; import manual changes: `terraform import google_cloudfunctions_function.<name> <project>/<region>/<name>`; enforce deploy-only-via-CI policy |
| ArgoCD/Cloud Build sync stuck on function deployment | Cloud Build trigger fires but deployment hangs; function in `DEPLOYING` state for > 10 min | Cloud Build step waiting for Cloud Functions API to complete; function startup health check failing; or API quota exceeded | `gcloud functions describe <name> --region=<region> --format="value(status)"`; `gcloud builds describe <build-id>`; `gcloud functions logs read <name> --region=<region> --limit=20` | Cancel stuck deployment: `gcloud functions delete <name> --region=<region>` and redeploy; check Cloud Functions API quota: `gcloud services list --enabled \| grep cloudfunctions`; increase deployment timeout in Cloud Build step |
| PDB equivalent — minimum instances preventing scale-down | Cloud Functions with `--min-instances=10` prevents cost optimization; cannot scale below 10 even during zero traffic | `min-instances` set too high during incident response; forgotten after incident; costs accumulate | `gcloud functions describe <name> --region=<region> --format="value(minInstances)"`; check billing: `gcloud billing projects describe <project>` | Reduce min instances: `gcloud functions deploy <name> --min-instances=1 --region=<region>`; use Cloud Scheduler to scale min instances up during peak and down during off-peak |
| Blue-green cutover failure during function version migration | New function version deployed but traffic still routing to old version; or new version has bug and no rollback path | Cloud Functions 1st gen has no traffic splitting; deploy replaces function atomically; if new version broken, rollback requires redeploy | `gcloud functions describe <name> --region=<region> --format="value(versionId,updateTime)"`; `gcloud functions logs read <name> --region=<region> --limit=20 \| grep ERROR` | Use 2nd gen with Cloud Run traffic splitting: `gcloud run services update-traffic <name> --to-revisions=<old>=100 --region=<region>`; implement canary: `--to-revisions=<new>=10,<old>=90`; keep previous source zip for quick rollback |
| ConfigMap/Secret drift — environment variables out of sync | Function env vars differ from what's in Git/Secret Manager; function reads stale API keys or config values | Env vars updated via Console or `gcloud` without updating Git; Secret Manager version not pinned in function config | `gcloud functions describe <name> --region=<region> --format=json \| jq '.environmentVariables'`; diff with Git: `diff <(gcloud functions describe <name> --format=json \| jq -S '.environmentVariables') env-vars.json` | Use Secret Manager with version pinning: `gcloud functions deploy <name> --set-secrets=API_KEY=api-key:latest --region=<region>`; manage env vars via Terraform only; add drift detection to CI pipeline |
| Feature flag rollout — enabling Cloud Functions concurrency causing race conditions | 2nd gen function with concurrency > 1; concurrent requests share global state; race condition in handler; intermittent wrong results | `--concurrency=80` (default for 2nd gen) means multiple requests per instance; non-thread-safe global variables corrupted | `gcloud functions describe <name> --gen2 --region=<region> --format="value(serviceConfig.maxInstanceRequestConcurrency)"`; `gcloud functions logs read <name> --gen2 --limit=100 \| grep -i "race\|corrupt\|inconsistent"` | Set concurrency to 1: `gcloud functions deploy <name> --gen2 --concurrency=1 --region=<region>`; fix code for thread safety; use per-request context instead of global state; add thread locks for shared resources |

## Service Mesh & API Gateway Edge Cases

| Failure | Cloud Functions-Specific Symptom | Why It Happens | Detection Command | Remediation |
|---------|----------------------------------|----------------|-------------------|-------------|
| Circuit breaker false positive on Cloud Functions endpoint | API Gateway or Cloud Endpoints circuit breaker opens for function; clients receive `503`; function is healthy but slow during cold starts | Cold start latency (2-5s) triggers circuit breaker timeout; breaker treats cold starts as failures | `gcloud logging read 'resource.type="api_gateway" AND httpRequest.status=503' --limit=20`; `gcloud functions logs read <name> --region=<region> --limit=20 \| grep "Function execution started"` — check cold start frequency | Increase API Gateway timeout to accommodate cold starts; use `--min-instances=1` to eliminate cold starts: `gcloud functions deploy <name> --min-instances=1 --region=<region>`; configure circuit breaker `failureThreshold` > cold start duration |
| Rate limiting hitting legitimate Cloud Functions traffic | Cloud Functions returns `429 RESOURCE_EXHAUSTED`; API Gateway rate limit exceeded; legitimate traffic blocked during peak | Default Cloud Functions API rate limits (e.g., 1000 invocations/100s per region); or API Gateway rate limiting configured too low | `gcloud functions logs read <name> --region=<region> --limit=50 \| grep "429\|RESOURCE_EXHAUSTED"`; `gcloud monitoring metrics describe cloudfunctions.googleapis.com/function/execution_count --filter='metric.labels.status="error"'` | Request quota increase: `gcloud alpha services quota update --consumer=<project> --service=cloudfunctions.googleapis.com --metric=cloudfunctions.googleapis.com%2Fapi%2Frate_limit --value=5000`; implement client-side backoff; use 2nd gen with higher default limits |
| Stale service discovery for Cloud Functions behind Cloud Endpoints | Cloud Endpoints routing to old function URL; function redeployed to new region but Endpoints config not updated; 404 errors | Cloud Endpoints `openapi.yaml` hardcodes function URL; function URL changed after redeployment or region migration | `gcloud endpoints services describe <service> --format=json \| jq '.serviceConfig.apis[].methods[].requestUrl'`; `curl -v https://<endpoint>/<path>` — check for 404 | Update OpenAPI spec with new function URL; redeploy Endpoints config: `gcloud endpoints services deploy openapi.yaml`; use DNS-based routing instead of hardcoded URLs |
| mTLS rotation interrupting VPC-connected function traffic | Function cannot reach internal services via VPC connector after mTLS certificate rotation; `UNAVAILABLE` errors | VPC connector to internal service mesh uses mTLS; certificate rotated on mesh side but function instances have cached old cert | `gcloud functions logs read <name> --region=<region> --limit=50 \| grep -i "TLS\|certificate\|handshake\|UNAVAILABLE"`; check VPC connector status: `gcloud compute networks vpc-access connectors describe <connector> --region=<region>` | Configure internal services to accept both old and new certificates during rotation window; implement TLS cert refresh in function code; use Google-managed certificates for internal services |
| Retry storm amplification on Cloud Functions via Pub/Sub | Function overwhelmed by Pub/Sub retries; `execution_count` metric shows 10x expected invocations; downstream services overloaded | Pub/Sub retries on function timeout/error; default ack deadline retry creates exponential invocation growth; no dead letter queue | `gcloud pubsub subscriptions describe <sub> --format="value(ackDeadlineSeconds,deadLetterPolicy)"`; `gcloud monitoring time-series list --filter='metric.type="cloudfunctions.googleapis.com/function/execution_count"' --interval-start-time=<1h-ago>` | Configure dead letter topic: `gcloud pubsub subscriptions update <sub> --dead-letter-topic=<dlq> --max-delivery-attempts=5`; increase ack deadline: `--ack-deadline=600`; add idempotency to function handler; implement exponential backoff |
| gRPC keepalive affecting Cloud Functions to backend connectivity | Function gRPC calls to internal backend fail with `UNAVAILABLE` after warm instance idle period; connection reset | gRPC connection in warm function instance goes stale; Cloud Functions may terminate idle TCP connections; no keepalive configured | `gcloud functions logs read <name> --region=<region> --limit=50 \| grep -i "UNAVAILABLE\|grpc\|connection reset"`; check gRPC client settings in function code | Configure gRPC keepalive in function: `grpc.keepalive_time_ms=30000`; create new channel per invocation for critical calls; implement gRPC health check before each call; use connection pooling with validation |
| Trace context propagation loss across Cloud Functions chain | Trace shows gap between caller and Cloud Function; function-to-function traces disconnected in Cloud Trace | Cloud Functions does not automatically propagate `X-Cloud-Trace-Context` header from trigger to outbound calls; trace context lost at function boundary | `gcloud logging read 'resource.type="cloud_function" AND trace!=""' --limit=10 --format=json \| jq '.[] \| .trace'` — check if trace IDs connected; view in Cloud Trace console | Manually propagate trace context: extract `X-Cloud-Trace-Context` from request headers and include in outbound HTTP calls; use `opentelemetry-instrumentation-google-cloud` package; for Pub/Sub, embed trace ID in message attributes |
| Load balancer health check failing for Cloud Functions behind GLB | Global Load Balancer health check fails for Cloud Functions NEG; traffic not routed; function accessible directly but not via LB | Cloud Functions serverless NEG health check uses internal probing; function cold start exceeds health check timeout; or function returns non-200 for health probe | `gcloud compute health-checks describe <hc> --global`; `gcloud compute backend-services get-health <backend> --global`; `curl -v https://<lb-ip>/<function-path>` | Configure health check with longer timeout: `gcloud compute health-checks update http <hc> --timeout=30s --check-interval=30s --global`; add health endpoint to function that responds quickly without cold start penalty; use `--min-instances=1` |
