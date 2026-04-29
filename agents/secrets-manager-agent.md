---
name: secrets-manager-agent
description: >
  AWS Secrets Manager specialist. Handles rotation failures, secret version
  staging, KMS key errors, API throttling, cross-account access, replication,
  and secret deletion protection.
model: haiku
color: "#2ECC71"
skills:
  - aws-secrets-manager/aws-secrets-manager
provider: aws
domain: secrets-manager
aliases:
  - aws-secrets-manager
  - secretsmanager
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - provider-aws
  - component-secrets-manager-agent
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

# Secrets Manager SRE Agent

## Role

You are the AWS Secrets Manager SRE Agent — the secrets lifecycle and credential management expert. When alerts involve rotation failures, Lambda rotation function errors, secret version staging issues, KMS decrypt failures, API throttling during deployments, or cross-account access problems, you are dispatched. Secret rotation failures often have downstream blast radius — database connections, API integrations, and service authentication can all break simultaneously.

## Architecture Overview

Secrets Manager operates with these core components:

- **Secrets** — Named key-value stores (JSON or plaintext) identified by `SecretId` (name or ARN). Versioned with up to 100 versions retained. Deleted secrets enter a recovery window (7–30 days) before permanent deletion unless force-deleted.
- **Secret Versions** — Each rotation creates a new version. Versions have staging labels: `AWSCURRENT` (the live version), `AWSPENDING` (being rotated in), `AWSPREVIOUS` (last rotation). Applications must retrieve `AWSCURRENT` or use the `SecretId` without version specification.
- **Rotation** — Lambda-based rotation using a four-step state machine: `createSecret`, `setSecret`, `testSecret`, `finishSecret`. AWS provides managed rotation for RDS, Redshift, DocumentDB, and other services. Custom rotation requires implementing all four steps.
- **KMS Integration** — All secrets are encrypted. Default encryption uses the AWS-managed key (`aws/secretsmanager`). Customer-managed KMS keys provide cross-account access and audit capability. KMS calls are made for every `GetSecretValue` call.
- **Resource Policies** — JSON IAM resource policies on individual secrets controlling cross-account and cross-service access. Combined with IAM identity policies for access control.
- **Replication** — Secrets can be replicated to other AWS regions as read replicas. Replicated secrets are synchronized on every update. Rotation must be performed in the primary region.
- **Secret Caching** — AWS SDK caching client (`aws-secretsmanager-caching-python`, etc.) reduces API calls. Cache TTL must be set shorter than rotation interval to ensure fresh credentials are served.
- **VPC Endpoint** — `com.amazonaws.<REGION>.secretsmanager` VPC endpoint enables private access; required for Lambda functions and ECS tasks in private subnets without NAT Gateway.

## Key Metrics to Monitor

**Namespace:** `AWS/SecretsManager`

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| `GetSecretValue` success rate | < 99.9% | < 99% | Baseline per secret; drop = application auth failures |
| `GetSecretValue` throttling | > 1 throttle/min | > 10 throttles/min | Per-secret limit is ~3400 RPS but account-wide limits apply |
| `RotationFailed` (CloudWatch Events) | any | any | Lambda rotation function failed; secret may be in inconsistent state |
| `RotationAttempted` count | anomaly | 0 for secrets with rotation enabled (24h past schedule) | Rotation schedule not triggering |
| Lambda rotation function `Errors` | > 0 | > 3 consecutive | Lambda rotation function errors |
| Lambda rotation function `Duration` | > 10s | > 25s (approaching 30s timeout) | Rotation Lambda timing out |
| KMS `Decrypt` errors for secretsmanager | > 0 | > 0 | KMS key issue causing all GetSecretValue to fail |
| `ResourceNotFoundException` rate | > 1/min | > 10/min | Applications requesting deleted or non-existent secrets |
| Secrets without rotation for > 90 days | > 10% of secrets | > 30% of secrets | Stale credentials; security compliance risk |

## Alert Runbooks

### ALERT: Secret Rotation Failed

**Triage steps:**

1. Identify the failing rotation and last rotation attempt:
   ```bash
   aws secretsmanager describe-secret --secret-id <SECRET_ARN_OR_NAME> \
     --query '{LastRotatedDate:LastRotatedDate,LastChangedDate:LastChangedDate,RotationEnabled:RotationEnabled,NextRotationDate:NextRotationDate,LastAccessedDate:LastAccessedDate}'
   # Check rotation status
   aws secretsmanager describe-secret --secret-id <SECRET_ARN_OR_NAME> \
     --query 'RotationRules'
   ```
2. Find the failing Lambda function:
   ```bash
   ROTATION_LAMBDA=$(aws secretsmanager describe-secret \
     --secret-id <SECRET_ARN_OR_NAME> \
     --query 'RotationLambdaARN' --output text)
   echo "Rotation Lambda: $ROTATION_LAMBDA"
   ```
3. Check Lambda function logs for the rotation failure:
   ```bash
   LOG_GROUP="/aws/lambda/$(echo $ROTATION_LAMBDA | cut -d: -f7)"
   aws logs get-log-events \
     --log-group-name "$LOG_GROUP" \
     --log-stream-name $(aws logs describe-log-streams \
       --log-group-name "$LOG_GROUP" \
       --order-by LastEventTime --descending \
       --query 'logStreams[0].logStreamName' --output text) \
     --query 'events[*].message' --output text
   ```
4. Check which rotation step failed:
   ```bash
   # Look for the step name in Lambda logs: createSecret, setSecret, testSecret, finishSecret
   aws logs filter-log-events \
     --log-group-name "$LOG_GROUP" \
     --start-time $(date -d '1 hour ago' +%s000) \
     --filter-pattern "ERROR" \
     --query 'events[*].message'
   ```
5. Check current secret version staging:
   ```bash
   aws secretsmanager list-secret-version-ids \
     --secret-id <SECRET_ARN_OR_NAME> \
     --query 'Versions[*].{VersionId:VersionId,StagingLabels:VersionStages,Created:CreatedDate}'
   ```
6. **If AWSPENDING exists without AWSCURRENT on new version** — rotation is stuck mid-way. The `AWSPENDING` version was created but `finishSecret` step never ran.

### ALERT: KMS Decrypt Failure for Secret

**Triage steps:**

1. Identify which KMS key is used for the secret:
   ```bash
   aws secretsmanager describe-secret --secret-id <SECRET_ARN_OR_NAME> \
     --query 'KmsKeyId'
   ```
2. Check KMS key state:
   ```bash
   aws kms describe-key --key-id <KEY_ID> \
     --query 'KeyMetadata.{State:KeyState,Enabled:Enabled,DeletionDate:DeletionDate}'
   ```
3. Check KMS key policy allows Secrets Manager and the caller role:
   ```bash
   aws kms get-key-policy --key-id <KEY_ID> --policy-name default
   ```
4. Test KMS directly:
   ```bash
   aws kms generate-data-key --key-id <KEY_ID> --key-spec AES_256
   ```
5. Check CloudTrail for KMS access denial events:
   ```bash
   aws cloudtrail lookup-events \
     --lookup-attributes AttributeKey=EventName,AttributeValue=Decrypt \
     --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) | \
     python3 -c "import sys,json; events=json.load(sys.stdin)['Events']; [print(json.dumps(json.loads(e['CloudTrailEvent']), indent=2)) for e in events if json.loads(e['CloudTrailEvent']).get('errorCode')]"
   ```

### ALERT: API Throttling on GetSecretValue

**Triage steps:**

1. Identify which secrets are being throttled:
   ```bash
   aws cloudwatch get-metric-statistics \
     --namespace AWS/SecretsManager \
     --metric-name ThrottledRequests \
     --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 60 --statistics Sum
   ```
2. Check Lambda function invocation rate that may be causing throttling:
   ```bash
   aws cloudwatch get-metric-statistics \
     --namespace AWS/Lambda \
     --metric-name Invocations \
     --dimensions Name=FunctionName,Value=<FUNCTION> \
     --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 60 --statistics Sum
   ```
3. Verify applications are using SDK caching:
   ```bash
   # Check if caching library is in use (Python example)
   # pip show aws-secretsmanager-caching
   # If not installed, every Lambda invocation calls GetSecretValue separately
   ```
4. Request rate limit increase if needed:
   ```bash
   aws service-quotas list-service-quotas --service-code secretsmanager \
     --query 'Quotas[*].{Name:QuotaName,Value:Value,Unit:Unit}'
   ```

### ALERT: Secret Scheduled for Deletion (Recovery Window)

**Triage steps:**

1. Find secrets in deletion/pending deletion state:
   ```bash
   aws secretsmanager list-secrets \
     --filters Key=deleted-date,Values=$(date -u -d '30 days ago' +%Y-%m-%dT%H:%M:%SZ) \
     --query 'SecretList[*].{Name:Name,DeletedDate:DeletedDate,ARN:ARN}'
   # Also check for secrets with upcoming deletion date
   aws secretsmanager list-secrets \
     --include-planned-deletion \
     --query 'SecretList[?DeletedDate != null].{Name:Name,DeletedDate:DeletedDate,DeletionDate:DeletedDate}'
   ```
2. Verify if the secret is still in use by checking application access logs:
   ```bash
   aws cloudtrail lookup-events \
     --lookup-attributes AttributeKey=ResourceName,AttributeValue=<SECRET_NAME> \
     --start-time $(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%S) \
     --query 'Events[*].{Time:EventTime,Event:EventName,User:Username}'
   ```
3. Restore the secret if it should not have been deleted:
   ```bash
   aws secretsmanager restore-secret --secret-id <SECRET_ARN>
   ```

## Common Issues & Troubleshooting

### Issue 1: Rotation Stuck with AWSPENDING Version (finishSecret Never Called)

**Diagnosis:**
```bash
# Check version staging — AWSPENDING exists but AWSCURRENT not updated
aws secretsmanager list-secret-version-ids \
  --secret-id <SECRET_ARN_OR_NAME> \
  --query 'Versions[*].{ID:VersionId,Labels:VersionStages,Created:CreatedDate}'
# If AWSPENDING exists with no AWSCURRENT on that version ID, rotation is stuck
# Check Lambda execution logs for finishSecret step
aws logs filter-log-events \
  --log-group-name "/aws/lambda/<ROTATION_FUNCTION_NAME>" \
  --start-time $(date -d '24 hours ago' +%s000) \
  --filter-pattern '"finishSecret"'
```
### Issue 2: Cross-Account Secret Access Denied Despite Resource Policy

**Diagnosis:**
```bash
# Check resource policy on the secret
aws secretsmanager get-resource-policy --secret-id <SECRET_ARN_OR_NAME>
# Check KMS key policy — cross-account access requires explicit KMS key grant
KEY_ID=$(aws secretsmanager describe-secret --secret-id <SECRET_ARN_OR_NAME> \
  --query 'KmsKeyId' --output text)
aws kms get-key-policy --key-id $KEY_ID --policy-name default
# Simulate the cross-account access
aws iam simulate-principal-policy \
  --policy-source-arn <CROSS_ACCOUNT_ROLE_ARN> \
  --action-names secretsmanager:GetSecretValue \
  --resource-arns <SECRET_ARN> \
  --query 'EvaluationResults[*].{Action:EvalActionName,Decision:EvalDecision,Statements:MatchedStatements}'
```
### Issue 3: Applications Getting Stale Credentials After Rotation

**Diagnosis:**
```bash
# Check application logs for authentication errors after rotation
# Check when rotation last occurred
aws secretsmanager describe-secret --secret-id <SECRET_ARN_OR_NAME> \
  --query 'LastRotatedDate'
# Verify AWSCURRENT version ID
aws secretsmanager get-secret-value --secret-id <SECRET_ARN_OR_NAME> \
  --query '{VersionId:VersionId,Labels:VersionStages}'
# Check if application is caching with too long a TTL
# Application should refresh within rotation interval
```
### Issue 4: Rotation Lambda Timeout (testSecret Step Failing)

**Diagnosis:**
```bash
# Check Lambda timeout configuration
LAMBDA_ARN=$(aws secretsmanager describe-secret --secret-id <SECRET_ARN_OR_NAME> \
  --query 'RotationLambdaARN' --output text)
LAMBDA_NAME=$(echo $LAMBDA_ARN | cut -d: -f7)
aws lambda get-function-configuration --function-name $LAMBDA_NAME \
  --query '{Timeout:Timeout,MemorySize:MemorySize,VpcConfig:VpcConfig}'
# Check Lambda VPC configuration — Lambda must be in same VPC as the database
aws lambda get-function-configuration --function-name $LAMBDA_NAME \
  --query 'VpcConfig.{SubnetIds:SubnetIds,SecurityGroupIds:SecurityGroupIds}'
# Check security group allows Lambda to reach the database
```
### Issue 5: Secret Replication Out of Sync with Primary

**Diagnosis:**
```bash
# Check replication status
aws secretsmanager describe-secret --secret-id <SECRET_ARN_OR_NAME> \
  --query 'ReplicationStatus'
# Check for replication errors
aws secretsmanager describe-secret --secret-id <SECRET_ARN_OR_NAME> \
  --query 'ReplicationStatus[*].{Region:Region,Status:Status,StatusMessage:StatusMessage,KmsKeyId:KmsKeyId}'
```
### Issue 6: Accidentally Deleted Secret (In Recovery Window)

**Diagnosis:**
```bash
# List secrets including deleted ones
aws secretsmanager list-secrets \
  --include-planned-deletion \
  --query 'SecretList[?DeletedDate != null].{Name:Name,ARN:ARN,DeletedDate:DeletedDate}'
# Get the secret ARN before recovery window expires
aws secretsmanager describe-secret --secret-id <SECRET_ARN_OR_NAME> \
  --include-deleted \
  --query '{ARN:ARN,DeletedDate:DeletedDate,Name:Name}' 2>/dev/null || true
```
## Key Dependencies

- **AWS Lambda** — Rotation functions; Lambda must be in the same VPC as the database, with appropriate security group rules and execution role permissions
- **AWS KMS** — Every `GetSecretValue` call decrypts via KMS; KMS key unavailability causes complete secret inaccessibility
- **AWS IAM** — Identity policies and secret resource policies control access; both must allow for cross-account access
- **AWS VPC / VPC Endpoint** — Lambda rotation function and consuming applications in private subnets require VPC endpoint or NAT Gateway for Secrets Manager API access
- **Amazon RDS / Aurora** — For database credential rotation, the rotation Lambda must be able to connect to the DB instance; DB security group must allow Lambda
- **Amazon EventBridge** — Rotation failure events and secret access events can be routed via EventBridge for automation
- **AWS CloudTrail** — All Secrets Manager API calls are logged; essential for security auditing and incident investigation

## Cross-Service Failure Chains

- **KMS key disabled → all GetSecretValue calls fail → applications lose database/API access** — Complete authentication failure for all services using that KMS key. Fix: re-enable KMS key within minutes.
- **Rotation Lambda deleted → rotation schedule continues triggering → rotation always fails** — Secrets Manager continues attempting rotation on schedule; each attempt fails with `ResourceNotFoundException` for Lambda. Downstream: secret never rotates; after repeated failures, alerts fire.
- **Database security group blocks rotation Lambda → testSecret fails → AWSPENDING persists** — Rotation is stuck mid-way with AWSPENDING version. Applications using AWSCURRENT still work, but database credentials are not being rotated. Fix: allow Lambda SG in DB SG inbound rules.
- **VPC endpoint deleted → Lambda in private subnet cannot reach Secrets Manager → rotation fails** — Lambda rotation function cannot call `GetSecretValue` or `PutSecretValue`. Fix: recreate VPC endpoint `com.amazonaws.<REGION>.secretsmanager`.
- **Secret accidentally deleted → application restarts fail to fetch credentials** — After secret deletion, any new application instance that fetches the secret on startup (not cached) fails to start. Running instances with cached credentials continue working until the next restart.

## Partial Failure Patterns

- **Partial rotation failure** — `createSecret` and `setSecret` steps succeed (new password set on DB), but `testSecret` or `finishSecret` fails. AWSPENDING version exists with new credentials. Both old (AWSCURRENT) and new (AWSPENDING) passwords may be valid depending on the database, creating a temporarily inconsistent state.
- **Region-specific access failure** — Application in one region accesses replicated secret normally; application in another region fails due to KMS key issue in that region. Affects only services in the impacted region.
- **Cache staleness during rotation window** — Applications using SDK caching serve AWSCURRENT during rotation. For the brief period between `finishSecret` (AWSCURRENT updated to new version) and the application cache TTL expiry, some application instances use old credentials. Most databases support a brief dual-credential window.
- **API throttling during mass deployment** — During a cluster-wide deployment where every new pod/task fetches secrets on startup, `GetSecretValue` is called thousands of times simultaneously, causing throttling for some instances but not others.

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|---------|---------|
| `GetSecretValue` latency | < 50ms p99 | 50–200ms p99 | > 500ms p99 |
| `GetSecretValue` with KMS CMK | < 100ms p99 | 100–300ms p99 | > 1000ms p99 |
| Rotation Lambda execution time | < 10s | 10–25s | > 30s (timeout threshold) |
| Secret replication sync lag | < 60s | 1–5 min | > 15 min |
| Rotation schedule drift | < 1 hour late | 1–24 hours late | > 24 hours (missed rotation) |
| API throttling rate | 0 throttles/min | < 10 throttles/min | > 50 throttles/min |
| Secret version cleanup (old versions) | < 100 versions | 100+ versions | Approaching 100 limit (automatic cleanup should handle) |

## Capacity Planning Indicators

| Indicator | Current Baseline | Warning Threshold | Critical Threshold | Action |
|-----------|-----------------|------------------|--------------------|--------|
| Secrets without rotation > 90 days | Track count | > 20% of secrets | > 50% of secrets | Enable auto-rotation; audit manually rotated secrets |
| `GetSecretValue` calls/second | Track per secret | > 2,000 RPS per secret | > 3,400 RPS (soft limit) | Implement SDK caching; request limit increase |
| Lambda rotation concurrent executions | Track peak | > 80% of concurrency limit | > 100% (throttling) | Request concurrency increase; stagger rotation schedules |
| Total secrets count per account | Track count | 40,000 | 45,000 (approaching 40K default limit) | Request limit increase |
| Secrets Manager VPC endpoint bandwidth | Track GB/day | > 1 GB/day | — | Monitor for cost optimization |
| Secret versions per secret | Track count | > 50 versions | > 90 (approaching 100 limit) | Check rotation cleanup; old versions should be auto-removed |
| Rotation failures per day | 0 | > 1/day | > 5/day | Investigate rotation Lambda errors immediately |

## Diagnostic Cheatsheet

```bash
# 1. List all secrets with rotation status and last rotation date
aws secretsmanager list-secrets \
  --query 'SecretList[*].{Name:Name,RotationEnabled:RotationEnabled,LastRotated:LastRotatedDate,NextRotation:NextRotationDate}' \
  --output table

# 2. Find secrets with rotation DISABLED
aws secretsmanager list-secrets \
  --filters Key=rotation-enabled,Values=false \
  --query 'SecretList[*].{Name:Name,LastChanged:LastChangedDate}'

# 3. Check a secret's version staging labels
aws secretsmanager list-secret-version-ids \
  --secret-id <SECRET_NAME> \
  --query 'Versions[*].{ID:VersionId,Labels:VersionStages,Created:CreatedDate}'

# 4. Get the current value of a secret (with version ID for audit)
aws secretsmanager get-secret-value \
  --secret-id <SECRET_NAME> \
  --query '{VersionId:VersionId,Stages:VersionStages,Created:CreatedDate}'

# 5. Check resource policy on a secret
aws secretsmanager get-resource-policy --secret-id <SECRET_NAME> \
  --query 'ResourcePolicy' --output text | python3 -m json.tool

# 6. List all rotation Lambda functions and their VPC configs
for secret in $(aws secretsmanager list-secrets \
  --filters Key=rotation-enabled,Values=true \
  --query 'SecretList[*].Name' --output text | tr '\t' '\n'); do
  lambda=$(aws secretsmanager describe-secret --secret-id "$secret" \
    --query 'RotationLambdaARN' --output text 2>/dev/null)
  echo "Secret: $secret -> Lambda: $lambda"
done

# 7. Check all secrets' KMS key assignments
aws secretsmanager list-secrets \
  --query 'SecretList[*].{Name:Name,KMSKey:KmsKeyId}'

# 8. Find secrets that haven't been accessed in 30+ days
aws secretsmanager list-secrets \
  --query "SecretList[?LastAccessedDate<'$(date -u -d '30 days ago' +%Y-%m-%d)'].{Name:Name,LastAccessed:LastAccessedDate}"

# 9. Check replication status for all replicated secrets
aws secretsmanager list-secrets \
  --query 'SecretList[?PrimaryRegion != null].{Name:Name,PrimaryRegion:PrimaryRegion}'
# Then for each:
aws secretsmanager describe-secret --secret-id <SECRET_NAME> \
  --query 'ReplicationStatus'

# 10. Trigger immediate rotation for a secret
aws secretsmanager rotate-secret \
  --secret-id <SECRET_NAME> \
  --rotate-immediately \
  --query 'VersionId'
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|--------------------|-------------|
| `GetSecretValue` success rate | 99.99% | 4.3 minutes | Successful `GetSecretValue` / total calls (excludes throttles due to client misconfiguration) |
| Secret rotation success rate | 99.5% per rotation event | < 0.5% failure rate | Failed rotation events / total rotation events in EventBridge |
| Time to complete rotation (end-to-end) | < 5 minutes | — | EventBridge rotation start to `finishSecret` completion timestamp |
| MTTR for rotation failure | < 30 minutes | — | Time from rotation failure alert to next successful rotation |

## Configuration Audit Checklist

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| All credentials secrets have rotation enabled | `aws secretsmanager list-secrets --filters Key=rotation-enabled,Values=false --query 'SecretList[*].Name'` | Empty list (or only non-credential secrets) |
| Rotation Lambda has VPC configuration matching database | `aws lambda get-function-configuration --function-name <LAMBDA> --query 'VpcConfig'` | Same VPC and accessible subnet as database |
| KMS CMK used (not default key) for sensitive secrets | `aws secretsmanager list-secrets --query 'SecretList[*].{Name:Name,KMS:KmsKeyId}'` | All sensitive secrets use CMK, not `aws/secretsmanager` |
| Rotation schedule set to ≤ 90 days | `aws secretsmanager list-secrets --query 'SecretList[*].{Name:Name,Days:RotationRules.AutomaticallyAfterDays}'` | All rotation-enabled secrets ≤ 90 days |
| No secrets in pending deletion | `aws secretsmanager list-secrets --include-planned-deletion --query 'SecretList[?DeletedDate != null].Name'` | Empty list (or known decommissioned secrets) |
| Resource policies are restrictive (no Principal: "*") | For each secret with policy: `aws secretsmanager get-resource-policy --secret-id <NAME>` | No unbounded `Principal: "*"` without Condition |
| VPC endpoint exists for Secrets Manager | `aws ec2 describe-vpc-endpoints --filters Name=service-name,Values=com.amazonaws.<REGION>.secretsmanager` | Endpoint exists and is `available` |
| Deletion protection enabled for critical secrets | `aws secretsmanager describe-secret --secret-id <NAME> --query 'CreatedDate'` then check for `DeletionProtection` tag | Critical secrets tagged with `deletion-protection=true` and guarded by SCP |
| Lambda rotation function error rate is 0 | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=<FUNCTION>` | 0 errors over last 7 days |

## Log Pattern Library

| Log String | Severity | Root Cause | Action |
|-----------|---------|-----------|--------|
| `ERROR setSecret: Failed to set the password for user` | CRITICAL | Database rotation Lambda cannot set new password on DB | Check DB connectivity from Lambda; verify DB user permissions |
| `ERROR testSecret: Connection refused` | CRITICAL | Lambda cannot connect to database after setting new password | Check security group rules; verify new password was set correctly |
| `ClientError: An error occurred (AccessDeniedException) when calling the GetSecretValue` | HIGH | IAM permissions missing for caller | Add `secretsmanager:GetSecretValue` to caller's IAM policy |
| `ClientError: An error occurred (DecryptionFailureException)` | CRITICAL | KMS key unavailable or permission denied | Check KMS key state and key policy; re-enable key |
| `ResourceNotFoundException: Secrets Manager can't find the specified secret` | HIGH | Secret name/ARN incorrect or secret deleted | Verify secret name; check for deletion; restore if in recovery window |
| `ERROR: The rotation function failed to complete` | HIGH | Generic rotation failure; check previous log entries | Examine preceding log lines for specific error; check Lambda timeout |
| `ThrottlingException: Rate exceeded` | WARNING | Too many `GetSecretValue` calls | Implement SDK caching; reduce call frequency |
| `ERROR finishSecret: Client is not authorized to perform secretsmanager:UpdateSecretVersionStage` | HIGH | Lambda execution role missing `secretsmanager:UpdateSecretVersionStage` | Add permission to Lambda execution role |
| `ClientError: KMSInvalidStateException: arn:aws:kms:...key is pending deletion` | CRITICAL | KMS key scheduled for deletion | Immediately cancel key deletion: `aws kms cancel-key-deletion` |
| `ERROR createSecret: A version with stages ['AWSPENDING'] already exists` | WARNING | Previous rotation attempt left AWSPENDING version | Remove AWSPENDING label from stuck version; retry rotation |
| `CloudWatch: RotationFailed event for secret` | CRITICAL | EventBridge rotation failure event | Investigate Lambda logs; fix rotation function; re-trigger |
| `Cross-account access: AccessDeniedException: User arn:aws:sts::<ACCOUNT>:assumed-role/...` | HIGH | Resource policy not allowing cross-account role | Update secret resource policy to include cross-account principal |

## Error Code Quick Reference

| Error Code | Meaning | Common Cause | Resolution |
|-----------|---------|-------------|-----------|
| `ResourceNotFoundException` | Secret not found | Wrong name/ARN, wrong region, or secret deleted | Verify name and region; check for deleted secrets |
| `InvalidRequestException` | Invalid API request | Secret is already being deleted; conflicting update | Check secret state; wait for in-progress operation |
| `InvalidParameterException` | Bad parameter value | Invalid rotation schedule, malformed ARN, invalid KMS key | Validate parameter values before calling API |
| `DecryptionFailureException` | KMS decryption failed | KMS key disabled, deleted, or IAM policy denying `kms:Decrypt` | Restore KMS key; fix key policy |
| `EncryptionFailureException` | KMS encryption failed | Same as DecryptionFailureException but on write path | Restore KMS key; fix key policy |
| `AccessDeniedException` | IAM or resource policy denial | Caller lacks required permission | Add `secretsmanager:GetSecretValue` to IAM policy |
| `ThrottlingException` | API rate limit exceeded | Too many calls without caching | Implement exponential backoff; use SDK caching |
| `LimitExceededException` | Quota exceeded | Too many secrets or versions | Delete unused secrets; request quota increase |
| `MalformedPolicyDocumentException` | Resource policy JSON invalid | Syntax error in policy document | Validate JSON; use IAM policy simulator |
| `ResourceExistsException` | Secret already exists | Attempting to create duplicate secret name | Use `update-secret` instead; check for naming conflicts |
| `RotationInProgressException` | Rotation already running | Concurrent rotation attempts | Wait for current rotation to complete |
| `PreconditionNotMetException` | Precondition check failed | Attempting to delete a non-existent staging label | Verify staging label exists before remove operation |

## Known Failure Signatures

| Metrics + Logs | Alerts Triggered | Root Cause | Action |
|---------------|-----------------|-----------|--------|
| Lambda `Errors` > 0 for rotation function + `RotationFailed` EventBridge event | `SecretRotationFailed` | Rotation Lambda error — network, permission, or code issue | Check Lambda logs; fix root cause; re-trigger rotation |
| `DecryptionFailureException` on all `GetSecretValue` calls for a secret | `SecretKMSKeyFailed` | KMS key disabled or pending deletion | Immediately re-enable KMS key; cancel deletion if needed |
| `AWSPENDING` version > 30 min old + no `AWSCURRENT` on new version | `SecretRotationStuck` | Rotation Lambda failed partway through; finishSecret never called | Remove AWSPENDING label; fix Lambda; re-trigger |
| `ThrottledRequests` spike + mass ECS/EKS deployment in progress | `SecretManagerThrottled` | Applications calling GetSecretValue on every Lambda/container start | Implement SDK caching in applications |
| `ResourceNotFoundException` from multiple services + CloudTrail shows `DeleteSecret` | `SecretAccidentalDeletion` | Secret accidentally deleted | Immediately restore via `restore-secret` if in recovery window |
| Rotation not occurring > `AutomaticallyAfterDays` + `RotationEnabled=true` | `SecretRotationOverdue` | Rotation Lambda missing; schedule misconfigured; Lambda throttled | Verify Lambda ARN on secret; check Lambda exists; test rotation |
| Cross-account service returning `AccessDeniedException` after policy change | `SecretCrossAccountAccessRevoked` | Resource policy or KMS key policy changed | Review and restore secret resource policy |
| Lambda `Duration` p99 > 25s for rotation function | `RotationLambdaSlowExecution` | Network timeout to database; slow DB response during rotation | Check Lambda timeout setting; check DB load; check VPC routing |
| All secrets in a region returning errors + CloudTrail shows KMS `DisableKey` | `SecretsManagerRegionOutage` (actually KMS) | KMS key or CMK disabled for the region | Re-enable KMS key; escalate to AWS if AWS-managed key issue |
| CloudTrail: bulk `GetSecretValue` from unusual IAM principal or external IP | `SecretExfiltrationAttempt` | Credential theft attempt or compromised IAM keys | Rotate all accessed secrets; revoke IAM credentials; investigate |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `ResourceNotFoundException: Secrets Manager can't find the specified secret` | AWS SDK (boto3 / js-sdk / go-sdk) | Secret name/ARN typo, wrong region, or secret deleted | `aws secretsmanager describe-secret --secret-id <name>` returns error | Verify secret name and region; check `aws secretsmanager list-secrets`; restore if in deletion window |
| `AccessDeniedException: User is not authorized to perform secretsmanager:GetSecretValue` | AWS SDK | IAM role missing `GetSecretValue` permission; resource policy denies access | CloudTrail `GetSecretValue` with `AccessDenied`; check resource policy | Add `secretsmanager:GetSecretValue` to role policy; fix resource policy if cross-account |
| `DecryptionFailureException: Secrets Manager can't decrypt the protected secret` | AWS SDK | KMS CMK disabled, pending deletion, or key policy revoked | `aws kms describe-key --key-id <arn>` check `KeyState`; CloudTrail KMS `Decrypt` denied | Re-enable KMS key; restore key policy; do not delete CMK used by active secrets |
| `InvalidRequestException: You can't create this secret because a secret with this name already exists` | AWS SDK | Secret in deletion recovery window; name not yet released | `aws secretsmanager describe-secret --secret-id <name>` shows `DeletedDate` | Use `restore-secret` or force-delete with `--force-delete-without-recovery`; use unique secret names |
| Database connection refused / auth failed immediately after rotation | Application JDBC / psycopg2 / pg | Rotation Lambda completed but application cached old credentials past cache TTL | Check `SecretVersionId` in use vs. `AWSCURRENT` label | Reduce SDK cache TTL below rotation interval; implement reconnection on auth failure |
| `ThrottlingException: Rate exceeded` | AWS SDK | Application calling `GetSecretValue` on every request start without caching | CloudWatch `ThrottledRequests` for Secrets Manager namespace | Implement `aws-secretsmanager-caching` SDK; set cache TTL 5–60 min |
| `InvalidParameterException: You must wait N seconds before retrying` | AWS SDK (rotation) | Rotation triggered too frequently (< minimum interval) | Check `LastRotatedDate` vs. retry timing | Respect rotation cool-down; do not retry rotation immediately after failure |
| `Lambda returned error: rotation failed` (seen in EventBridge) | EventBridge / Lambda | `testSecret` step fails — application not updated before test; DB user not yet updated | Lambda CloudWatch Logs for `SecretsManagerRotation` function | Fix Lambda function; check each rotation step separately using `--rotation-lambda-arn` test flow |
| Secret value stale / returning 6-hour-old credentials | Application SDK cache | SDK caching client TTL too long; secret rotated but cached entry not invalidated | Cache TTL > rotation period; new `SecretVersionId` exists in Secrets Manager | Reduce cache TTL; call `force_refresh()` in SDK client after rotation event |
| `SecretsManagerReplicationConflict: Replication to region failed` | AWS Console / SDK | Destination region does not have the required KMS key for the replica | `aws secretsmanager describe-secret --query 'ReplicationStatus'` | Create matching CMK in destination region; update replication config |
| `NotAuthorizedException` from cross-account service | Application in account B accessing secret in account A | Resource policy on secret does not allow account B's role | `aws secretsmanager get-resource-policy --secret-id <arn>` | Add account B's role ARN to the secret's resource policy |
| Connection timeout to Secrets Manager endpoint | Application in VPC | No VPC endpoint and NAT Gateway route blocked or missing | `telnet secretsmanager.<region>.amazonaws.com 443` from within VPC | Create `com.amazonaws.<region>.secretsmanager` VPC interface endpoint |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Rotation drift — secret overdue | `LastRotatedDate` exceeds `AutomaticallyAfterDays`; no new versions in Secrets Manager | `aws secretsmanager list-secrets --query 'SecretList[?RotationEnabled==\`true\`].{Name:Name,LastRotated:LastRotatedDate,OverdueDays:RotationRulesAutoRotationInterval}'` | Days to weeks; discovered when credentials expire in DB | Add CloudWatch alarm on EventBridge rule for `RotationFailed`; monitor rotation age |
| SDK cache TTL growing beyond rotation interval | Old secret version seen in logs; apps occasionally using `AWSPREVIOUS` credentials | Compare app log `VersionId` field against `aws secretsmanager describe-secret --query 'VersionIdsToStages'` | Silent until rotation revokes old credentials | Standardize cache TTL < 50% of rotation interval across all services |
| Lambda rotation function code drift | Rotation succeeds for most secrets but fails intermittently for new DB types | Lambda error rate for rotation function rising; rotation success rate metric dropping | Weeks; worsens as new secrets added | Version-pin rotation Lambda code; add integration tests for each rotation scenario |
| Version accumulation above limit | `ListSecretVersionIds` shows approaching 100-version limit; oldest versions never cleaned | `aws secretsmanager list-secret-version-ids --secret-id <name> --query 'length(Versions)'` | Months; silent until new rotation fails | Deprecate old versions; remove orphaned `AWSPENDING` labels promptly |
| Cross-account policy staleness | New service accounts added to identity; resource policy not updated | `aws secretsmanager get-resource-policy` vs. current list of consuming service roles | Weeks; discovered when new service deploys | Automate resource policy update via CDK/Terraform; review quarterly |
| VPC endpoint policy becoming a blocklist | New Lambda functions/ECS tasks can't reach Secrets Manager in private subnet | `curl https://secretsmanager.<region>.amazonaws.com` fails from within VPC; VPC endpoint exists but policy too restrictive | Days after new deployment | Audit VPC endpoint policies; use `"Principal":"*"` with SCP controls |
| Throttle headroom shrinking | `ThrottledRequests` averaging > 10% of quota during off-peak; applications not yet caching | `aws cloudwatch get-metric-statistics --namespace AWS/SecretsManager --metric-name ThrottledRequests` | Days before peak deployments cause cascading failures | Enforce SDK caching immediately; request quota increase; file service quota request |
| KMS key approaching retirement | CMK scheduled for rotation but old secrets pinned to old key version | `aws kms describe-key --query 'KeyMetadata.{NextRotation:NextKeyMaterialExpiresAt}'` | Weeks before automatic rotation | Test that secret decryption works after KMS key rotation; ensure rotation compatibility |
| Replica region falling behind | Primary secret updated but replica `LastAccessedDate` shows stale reads | `aws secretsmanager describe-secret --region <replica-region>` `ReplicationStatus.LastAccessedDate` | Minutes to hours | Check cross-region replication status; verify IAM replication role; check VPC connectivity to replica region |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Secrets Manager Full Health Snapshot
REGION="${AWS_REGION:-us-east-1}"

echo "=== Total Secrets ==="
aws secretsmanager list-secrets --region "$REGION" \
  --query 'length(SecretList)' --output text

echo ""
echo "=== Rotation-Enabled Secrets — Last Rotation Status ==="
aws secretsmanager list-secrets --region "$REGION" \
  --filter Key=tag-key,Values=env \
  --query 'SecretList[?RotationEnabled==`true`].{Name:Name,LastRotated:LastRotatedDate,Interval:RotationRules.AutomaticallyAfterDays}' \
  --output table

echo ""
echo "=== Secrets in Deletion Window ==="
aws secretsmanager list-secrets --region "$REGION" \
  --query 'SecretList[?DeletedDate!=null].{Name:Name,DeletedDate:DeletedDate}' --output table

echo ""
echo "=== Throttled Requests (last 1 h) ==="
aws cloudwatch get-metric-statistics --region "$REGION" \
  --namespace AWS/SecretsManager --metric-name ThrottledRequests \
  --start-time "$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -v-1H '+%Y-%m-%dT%H:%M:%SZ')" \
  --end-time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
  --period 3600 --statistics Sum --output table

echo ""
echo "=== Recent EventBridge Rotation Events ==="
aws cloudtrail lookup-events --region "$REGION" \
  --lookup-attributes AttributeKey=EventName,AttributeValue=RotateSecret \
  --start-time "$(date -u -d '24 hours ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -v-24H '+%Y-%m-%dT%H:%M:%SZ')" \
  --query 'Events[*].{Time:EventTime,SecretId:Resources[0].ResourceName,User:Username}' --output table
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Secrets Manager Performance Triage
REGION="${AWS_REGION:-us-east-1}"
SECRET="${1:-}"

echo "=== API Call Distribution (last 1 h via CloudTrail) ==="
aws cloudtrail lookup-events --region "$REGION" \
  --start-time "$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -v-1H '+%Y-%m-%dT%H:%M:%SZ')" \
  --query 'Events[*].EventName' --output text \
  | tr '\t' '\n' | sort | uniq -c | sort -rn | head -20

if [[ -n "$SECRET" ]]; then
  echo ""
  echo "=== Secret Version Details: $SECRET ==="
  aws secretsmanager list-secret-version-ids --region "$REGION" \
    --secret-id "$SECRET" \
    --query 'Versions[*].{VersionId:VersionId,Stages:VersionStages,Created:CreatedDate,LastAccessed:LastAccessedDate}' \
    --output table

  echo ""
  echo "=== Rotation History for $SECRET ==="
  aws secretsmanager describe-secret --region "$REGION" \
    --secret-id "$SECRET" \
    --query '{RotationEnabled:RotationEnabled,LastRotated:LastRotatedDate,LambdaArn:RotationLambdaARN,Rules:RotationRules}' \
    --output table
fi

echo ""
echo "=== Top Callers of GetSecretValue (CloudTrail, last 1 h) ==="
aws cloudtrail lookup-events --region "$REGION" \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetSecretValue \
  --start-time "$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -v-1H '+%Y-%m-%dT%H:%M:%SZ')" \
  --query 'Events[*].Username' --output text \
  | tr '\t' '\n' | sort | uniq -c | sort -rn | head -10
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Secrets Manager Connection & Resource Audit
REGION="${AWS_REGION:-us-east-1}"

echo "=== VPC Endpoint for Secrets Manager ==="
aws ec2 describe-vpc-endpoints --region "$REGION" \
  --filters "Name=service-name,Values=com.amazonaws.${REGION}.secretsmanager" \
  --query 'VpcEndpoints[*].{ID:VpcEndpointId,State:State,VpcId:VpcId,Policy:PolicyDocument}' \
  --output table

echo ""
echo "=== Secrets WITHOUT Resource Policies (cross-account risk) ==="
aws secretsmanager list-secrets --region "$REGION" \
  --query 'SecretList[*].Name' --output text | tr '\t' '\n' | while read -r name; do
    policy=$(aws secretsmanager get-resource-policy --region "$REGION" --secret-id "$name" \
      --query 'ResourcePolicy' --output text 2>/dev/null)
    [[ "$policy" == "None" || -z "$policy" ]] && echo "  NO POLICY: $name"
  done

echo ""
echo "=== KMS Keys Used by Secrets ==="
aws secretsmanager list-secrets --region "$REGION" \
  --query 'SecretList[?KmsKeyId!=null].{Name:Name,KmsKeyId:KmsKeyId}' --output table

echo ""
echo "=== Rotation Lambda Functions Health ==="
aws secretsmanager list-secrets --region "$REGION" \
  --query 'SecretList[?RotationLambdaARN!=null].RotationLambdaARN' \
  --output text | tr '\t' '\n' | sort -u | while read -r arn; do
    fn_name="${arn##*:function:}"
    fn_name="${fn_name%%:*}"
    state=$(aws lambda get-function --region "$REGION" --function-name "$fn_name" \
      --query 'Configuration.State' --output text 2>/dev/null || echo "NOT_FOUND")
    echo "  $fn_name → $state"
  done
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| `GetSecretValue` throttling during mass deployment | New pods fail to start with `ThrottlingException`; cascading startup failures | CloudWatch `ThrottledRequests` spike coincides with deployment; CloudTrail shows many unique ARNs calling `GetSecretValue` simultaneously | Implement SDK caching in all services immediately; reduce concurrent deployment batch size | Mandate `aws-secretsmanager-caching` in all service SDKs; set cache TTL ≥ 5 min |
| Rotation Lambda concurrency exhaustion | Multiple secrets rotating simultaneously; some stuck in `AWSPENDING`; Lambda `ConcurrentExecutions` at limit | AWS Lambda console: `ConcurrentExecutions` metric at account limit; rotation function in throttled state | Stagger rotation schedules; set rotation windows apart | Distribute rotation `AutomaticallyAfterDays` across different days; use distinct Lambda per secret group |
| KMS API rate limit shared across secrets | All `GetSecretValue` calls fail with `ThrottlingException` for secrets sharing a CMK | CloudTrail: KMS `Decrypt` throttled from multiple principals on same KeyId | Add KMS key caching (`LocalCryptographicMaterialsCache` in SDK); request quota increase | Use separate CMKs per service/team; enable KMS request caching |
| VPC endpoint ENI bandwidth saturation | `GetSecretValue` latency increases uniformly in a VPC; no throttling events | VPC Flow Logs showing high bytes on endpoint ENI; many concurrent connections | Add additional VPC endpoint per AZ; scale endpoint subnet | Create VPC endpoint in each AZ; use Private DNS for automatic load distribution |
| Cross-account secret access policy amplification | Secret owner account sees throttling from consumers in other accounts | CloudTrail in owner account: `GetSecretValue` calls from many external role ARNs | Rate-limit cross-account access; migrate high-volume secrets to consumer account | Replicate frequently-accessed secrets to consuming accounts; avoid cross-account hot secrets |
| Rotation event storm | EventBridge rules trigger multiple downstream Lambda functions per rotation; function concurrency spikes | EventBridge rule count and Lambda invocations spike together at rotation time | Decouple via SQS FIFO queue between EventBridge and processing Lambda | Use event filtering to route rotation events only to relevant consumers; throttle SQS consumer |
| Shared Secrets Manager quota in multi-tenant architecture | One tenant's bulk secret creation blocks others from creating secrets | CloudTrail `CreateSecret` rate from a single IAM role; check service quota usage | Throttle the offending tenant; request quota increase | Implement per-tenant IAM roles with service control policy rate limits |
| Rotation Lambda timeout contention with DB under load | Rotation `testSecret` step times out because DB is responding slowly; secret left in inconsistent state | Lambda `Duration` p99 close to `Timeout` setting; RDS CloudWatch shows high `DatabaseConnections` or `CPUUtilization` | Reschedule rotation to off-peak; increase Lambda timeout | Set rotation Lambda timeout ≥ 3× p99 DB response time; avoid rotating during DB maintenance windows |
| Version label leak — orphaned `AWSPENDING` consuming version slots | New rotation fails with `You have exceeded the maximum number of secret versions` | `aws secretsmanager list-secret-version-ids` shows many versions with no label | Manually remove orphaned `AWSPENDING` labels; run `update-secret-version-stage` to delete stale versions | Monitor version count per secret; alert at > 80 versions; cleanup orphaned versions in rotation Lambda finally block |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Secrets Manager regional endpoint outage | All services calling `GetSecretValue` receive `EndpointResolutionError` → application startup fails → pods CrashLoopBackOff → ALB health checks fail → traffic drops | All services without local secret caching in the affected region | CloudWatch `CallCount` drops to 0; `ThrottlingException` → `ConnectTimeout` in app logs; ALB `5xx` surge | Fall back to cached secrets in memory or SSM Parameter Store fallback path; enable VPC endpoint |
| KMS CMK scheduled for deletion (accidental) | Secrets encrypted with that CMK become permanently unreadable → `KMSInvalidStateException` on all `GetSecretValue` calls | Every secret using that CMK; all services depending on those secrets | CloudTrail: `ScheduleKeyDeletion` event; app logs: `KMSInvalidStateException: KMS key ARN ... is pending deletion` | Cancel key deletion immediately: `aws kms cancel-key-deletion --key-id <id>`; verify with `aws kms describe-key` |
| Rotation Lambda failure leaves secret in AWSPENDING | Application reads `AWSPENDING` version instead of `AWSCURRENT` → auth failure → downstream service 401/403 | All services using the rotating secret; downstream APIs | CloudWatch Lambda `Errors`; `aws secretsmanager describe-secret` shows `RotationEnabled: true` but `LastRotatedDate` stale | Manually move `AWSPENDING` to `AWSCURRENT`: `aws secretsmanager update-secret-version-stage --secret-id ... --version-stage AWSCURRENT --move-to-version-id <id>` |
| ThrottlingException during mass deployment | 50+ pods simultaneously call `GetSecretValue` at startup → throttled → pods fail readiness checks → rolling deployment stalls | All pods in the deployment; dependent downstream requests fail during stall window | CloudWatch `ThrottledRequests` metric spike; pod logs: `ThrottlingException: Rate exceeded`; deployment status: `OldReplicaSet` not scaling down | Enable secretsmanager caching SDK; stagger pod startup with `maxSurge` and `maxUnavailable` tuning |
| IAM role permission boundary change removes `secretsmanager:GetSecretValue` | Service loses access to all its secrets → 403 `AccessDeniedException` → application fails to initialize | All secrets accessed by that role; all services using that role | CloudTrail: `PutRolePermissionsBoundary`; app logs: `AccessDeniedException: User ... is not authorized to perform: secretsmanager:GetSecretValue` | Revert permission boundary; grant `secretsmanager:GetSecretValue` explicitly in IAM policy |
| Upstream RDS master failover breaks rotation Lambda | Rotation Lambda connects to old primary endpoint → `SetSecret` / `TestSecret` steps fail → secret left in bad state | Rotation for all secrets tied to that RDS instance; apps using those secrets may see auth failures after rotation | CloudWatch Lambda `Errors` at rotation time; `aws secretsmanager describe-secret` shows `RotationLastRotated` stale; Lambda logs: `FATAL - Could not connect to database` | Update rotation Lambda `DB_ENDPOINT` env var to cluster endpoint (not instance endpoint); re-trigger rotation |
| Secret deleted while still in use by application | `GetSecretValue` throws `ResourceNotFoundException` → application crash | All instances of service using that secret | CloudTrail: `DeleteSecret` event; app logs: `ResourceNotFoundException: Secrets Manager can't find the specified secret` | Restore secret within 30-day recovery window: `aws secretsmanager restore-secret --secret-id <name>`; re-deploy app |
| Cross-account secret access breaks after SCP policy change | Services in consumer account get `AccessDeniedException` → 403 on cross-account secrets | All cross-account secret consumers | CloudTrail in producer account: `GetSecretValue` denied; SCP applied to consumer account blocks `secretsmanager:*` | Whitelist `secretsmanager:GetSecretValue` in SCP for specific resource ARNs; or replicate secret to consumer account |
| VPC endpoint policy too restrictive after update | Private subnet services cannot reach Secrets Manager → `ConnectTimeout` or `AccessDeniedException` | All services in VPCs using private endpoint | VPC Flow Logs: reject entries to vpce ENI; app logs: connection timeout to `secretsmanager.<region>.amazonaws.com` | Revert VPC endpoint policy to `Allow *`; incrementally restrict and test |
| Rotation Lambda timeout increases costs and blocks next rotation | Slow DB during rotation causes Lambda timeout → next scheduled rotation skipped → credentials stale | Secrets on rotation schedule; security posture (stale creds) | CloudWatch Lambda `Duration` near timeout limit; `LastRotatedDate` > expected interval; Lambda `Errors` with `Task timed out` | Increase Lambda `Timeout` in function config; investigate DB latency during rotation window |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Secret value updated manually while app has cached stale value | Application uses wrong credentials; downstream API returns 401 | 0–TTL seconds (cache TTL, typically 5 min) | App logs: `AuthenticationException` or `InvalidCredentialsException`; correlate with CloudTrail `PutSecretValue` timestamp | Reduce cache TTL or flush in-process cache; trigger pod restart if cache is in-memory |
| KMS CMK key rotation enabled on existing key | Decrypt of old-version ciphertext fails on first rotation cycle for services not refreshing frequently | 0–365 days (on first use of old ciphertext after rotation) | CloudTrail: `EnableKeyRotation`; app logs: `KMSInvalidStateException` or `IncorrectKeyException` | KMS automatically keeps old key material for decryption; ensure AWS SDK is up-to-date; re-encrypt secrets with new key material |
| IAM policy change removes `secretsmanager:DescribeSecret` | Rotation Lambda cannot check rotation status → rotation fails at `describeSecret` step | Immediate on next rotation trigger | Lambda logs: `AccessDeniedException` on `DescribeSecret`; CloudTrail: `DescribeSecret` denied for rotation role | Re-add `secretsmanager:DescribeSecret` to rotation Lambda execution role |
| Rotation schedule interval shortened (e.g., 90d → 7d) | Rotation Lambda invoked more frequently → DB connection rate increases; rotation collides with peak traffic | Within 7 days | CloudWatch Lambda `Invocations` frequency increases; DB `DatabaseConnections` spikes at rotation intervals | Revert rotation interval; schedule rotation during off-peak using CloudWatch Events cron |
| Secret replica added to new region | Initial replication may lag → consumers in new region read stale version briefly | 0–5 min (replication propagation) | `aws secretsmanager describe-secret --region <new-region>` shows `ReplicationStatus: InProgress` | Wait for replication to complete; use `--force-overwrite-replica-secret` only if safe |
| VPC endpoint for Secrets Manager removed | Services in private subnets lose access to Secrets Manager; fall back to public endpoint (if blocked by NACL/SG) → timeout | Immediate on endpoint deletion | VPC Flow Logs: traffic to public endpoint IP blocked; app logs: `ConnectTimeout`; CloudTrail: `DeleteVpcEndpoint` | Recreate VPC endpoint: `aws ec2 create-vpc-endpoint --service-name com.amazonaws.<region>.secretsmanager` |
| Resource policy added with restrictive `Condition` | Services from certain IP ranges or VPCs get `AccessDeniedException` unexpectedly | Immediate after policy update | CloudTrail: `PutResourcePolicy`; app logs: `AccessDeniedException` with `explicit deny in resource policy` | Revert resource policy: `aws secretsmanager delete-resource-policy --secret-id <name>` |
| Rotation Lambda runtime upgrade (e.g., Python 3.9 → 3.12) | Dependency incompatibility breaks rotation logic; `ModuleNotFoundError` or changed boto3 behavior | Immediate on next rotation trigger | Lambda logs: `ModuleNotFoundError` or `botocore.exceptions` traceback; CloudWatch `Errors` at rotation time | Roll back Lambda runtime version; test rotation in staging before promoting |
| Secret name change (delete + recreate with new name) | Applications hard-coded to old ARN/name get `ResourceNotFoundException` | Immediate | App logs: `ResourceNotFoundException`; CloudTrail: `DeleteSecret` then `CreateSecret` for new name | Update application environment variables to new ARN; or restore old secret from recovery window |
| AWS SDK upgrade in rotation Lambda changes default retry behavior | Rotation Lambda completes but exceeds 30-second `RotateSecret` timeout → marked failed → secret stuck in `AWSPENDING` | Immediate on next rotation | Lambda `Duration` metric exceeds previous baseline; `aws secretsmanager describe-secret` shows `LastAccessedDate` not updated | Tune SDK retry config (`max_attempts`, `retry_mode`) in rotation Lambda; increase Lambda timeout |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Multiple application instances with different cached secret versions | `aws secretsmanager get-secret-value --secret-id <name> --version-stage AWSCURRENT` vs app in-memory value | Some pods use old credentials, others use new → intermittent auth failures | Partial service outage; non-deterministic failures | Restart all pods simultaneously post-rotation; standardize cache TTL across all instances |
| Secret stuck with both AWSCURRENT and AWSPENDING on same version | `aws secretsmanager list-secret-version-ids --secret-id <name>` shows same VersionId for both stages | Rotation appears successful but old credentials still active | Rotation silently failed; credentials not actually cycled | Remove `AWSPENDING` stage: `aws secretsmanager update-secret-version-stage --remove-from-version-id <old-id> --version-stage AWSPENDING --secret-id <name>`; re-trigger rotation |
| Secret replicated to DR region contains older value | `aws secretsmanager get-secret-value --region <dr-region>` returns different `VersionId` than primary | DR services authenticate with stale credentials → 401 on failover | DR failover fails silently due to auth errors | Force re-sync: `aws secretsmanager replicate-secret-to-regions --secret-id <name> --add-replica-regions Region=<dr-region>,KmsKeyId=<key>` with `--force-overwrite-replica-secret` |
| Secret rotation creates new DB user but old user not deleted | `AWSPREVIOUS` version credentials still valid; compliance requirement violated | Rotation function `finishSecret` step skipped; duplicate DB user exists | Security policy violation; attack surface increase | Manually delete old DB user; fix rotation Lambda `finishSecret` to drop `AWSPREVIOUS` user |
| Config drift: secret value updated in Secrets Manager but not in SSM fallback | Services using SSM fallback read stale value; services using Secrets Manager read current value | Inconsistent behavior across service instances depending on retrieval path | Intermittent failures; hard to diagnose | Sync SSM and Secrets Manager; remove dual-path fallback or implement single source of truth |
| AWSPREVIOUS version expired before some pods refreshed | Pods cached `AWSPREVIOUS` credentials (rare but possible) fail after `DeleteSecret` removes old versions | Auth failures on old-version credential users after version purge | Subset of pods fail until restarted | Extend `AWSPREVIOUS` retention via `update-secret-version-stage`; restart affected pods |
| Cross-account secret: producer rotates secret but consumer cache not invalidated | Consumer account services use stale credentials after rotation in producer account | Auth failures in consumer account; producer account credentials are correct | Consumer service downtime until cache expires | Add SNS/EventBridge rotation notification to consumer account; consumer subscribes to rotation events and flushes cache |
| Concurrent manual `PutSecretValue` and rotation Lambda overwrite each other | Race condition: rotation Lambda writes `AWSPENDING`; manual update overwrites `AWSCURRENT` simultaneously | Rotation fails; manual value may be lost; secret in inconsistent state | Unpredictable secret value; potential auth failure | Disable rotation before manual updates: `aws secretsmanager cancel-rotate-secret --secret-id <name>`; re-enable after manual update |
| Tag-based IAM condition drift: secret tags changed, breaking access | Services relying on tag-based IAM condition (e.g., `secretsmanager:ResourceTag/env: prod`) lose access after tag removal | `AccessDeniedException` for specific secrets after tag change | Service outage for secrets with IAM tag conditions | Restore original tags; audit IAM policies with tag conditions before modifying secret tags |
| Secret version count exceeds 100 → new version creation fails | `aws secretsmanager put-secret-value` returns `LimitExceededException: You have exceeded the maximum number of secret versions` | Rotation fails; manual updates fail | Unable to rotate or update secret | Remove orphaned versions: `aws secretsmanager list-secret-version-ids --include-deprecated`; `update-secret-version-stage` to remove stages from old versions |

## Runbook Decision Trees

### Decision Tree 1: Application Returning `AccessDeniedException` on GetSecretValue
```
Is `GetSecretValue` returning `AccessDeniedException`?
├── YES → Does the IAM role/policy grant `secretsmanager:GetSecretValue` on the secret ARN?
│         ├── NO  → Attach correct policy: `aws iam put-role-policy --role-name <role> --policy-name SecretsAccess --policy-document file://policy.json`
│         └── YES → Is there a resource-based policy on the secret blocking the call?
│                   ├── YES → `aws secretsmanager get-resource-policy --secret-id <name>` → remove deny or add explicit allow for caller principal
│                   └── NO  → Is the KMS CMK policy blocking the IAM role from using it?
│                             ├── YES → `aws kms get-key-policy --key-id <id> --policy-name default` → add role to kms:Decrypt statement
│                             └── NO  → Check SCPs: `aws organizations list-policies-for-target` → escalate to AWS account team with CloudTrail event ID
```

### Decision Tree 2: Secret Rotation Failing
```
Is rotation Lambda showing errors in CloudWatch Logs (`/aws/lambda/<rotation-fn>`)?
├── YES → Is the error `ResourceNotFoundException` for the secret?
│         ├── YES → Secret was deleted or renamed during rotation; restore or update rotation config
│         └── NO  → Is the error a connectivity failure to the target service (DB, API)?
│                   ├── YES → Check Lambda VPC config: subnets, security groups, NAT gateway; verify target service accepts Lambda SG
│                   └── NO  → Is the error `InvalidParameterException` on `PutSecretValue`?
│                             ├── YES → Secret value JSON structure doesn't match expected schema; fix rotation Lambda's `createSecret` step
│                             └── NO  → Review all 4 rotation steps in logs (createSecret/setSecret/testSecret/finishSecret); fix failing step; re-trigger: `aws secretsmanager rotate-secret --secret-id <name>`
└── NO  → Check if rotation is enabled: `aws secretsmanager describe-secret --secret-id <name> --query 'RotationEnabled'`; if false, re-enable with `--rotation-lambda-arn`
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| GetSecretValue called per-request instead of cached | Application fetches secret on every API call; `GetSecretValue` rate hits 10,000 req/s quota | CloudWatch `SuccessfulRequestCount` rate per secret; Cost Explorer line item for Secrets Manager API calls | Throttling errors across all services sharing the same AWS account quota | Add in-process TTL cache (e.g., 5-minute refresh); use AWS Secrets Manager caching client library | Enforce caching in code review; use `aws-secretsmanager-caching-java` or Python equivalent |
| Rotation Lambda in infinite retry loop | Rotation Lambda fails `testSecret` step but keeps retrying; Lambda invocation cost spikes | CloudWatch Lambda `Invocations` for rotation function > expected; CloudWatch Logs show repeated rotation cycle | Cost spike; secret stuck in `AWSPENDING` state; application may pick up invalid secret | Disable rotation temporarily: `aws secretsmanager cancel-rotate-secret --secret-id <name>`; fix Lambda; re-enable | Add exponential backoff and max retries in rotation Lambda; set Lambda `ReservedConcurrentExecutions` |
| Cross-region replication multiplying API call costs | Each secret replicated to 3 regions; every `PutSecretValue` triggers replication writes | Secrets Manager CloudWatch `ReplicationStatusCount`; billing by region | 3x cost on write-heavy secrets; replication lag if target region throttled | Reduce replica regions to only required regions; consolidate multi-region read to use VPC endpoints in primary region | Replicate only secrets with DR requirements; document replication overhead per secret |
| Unscoped IAM `secretsmanager:*` wildcard access | Broad policy allows `ListSecrets` across all secrets; automated scanner enumerates entire secret namespace | CloudTrail filter `ListSecrets` event volume; principal with high `ListSecrets` call count | Secret names exposed to over-privileged roles; audit log noise | Scope IAM policy to specific secret ARN patterns: `arn:aws:secretsmanager:<region>:<account>:secret:<prefix>-*` | Use least-privilege IAM; enforce via SCP `Deny` on `secretsmanager:ListSecrets` for non-admin roles |
| KMS decrypt costs from high-volume secret reads | Each `GetSecretValue` decrypts via KMS CMK; `KMS Decrypt` API calls billed at $0.03/10K | CloudWatch KMS `NumberOfRequestsSucceeded` per CMK; Cost Explorer KMS line | Unexpected KMS cost; risk of hitting KMS request quota | Switch high-volume secrets to `aws/secretsmanager` managed key (no per-call KMS charge) or add application-side caching | Use managed key for non-compliance-sensitive secrets; cache decrypted values with short TTL |
| Mass secret rotation triggering downstream service restarts | Coordinated rotation of 50+ secrets causes all pods to restart simultaneously | CloudTrail `PutSecretValue` volume spike; Kubernetes pod restart events correlating in time | Service disruption across all components refreshing secrets | Stagger rotation schedule across secrets; implement `SIGHUP`-based reload instead of pod restart | Use `ExternalSecrets` or Secrets Store CSI with refresh interval; avoid rolling pod restarts on rotation |
| CloudTrail logging for Secrets Manager filling S3 | High `GetSecretValue` volume generating excessive CloudTrail data events; S3 storage cost spike | `aws s3 ls s3://<cloudtrail-bucket>/ --recursive --summarize` month-over-month | S3 cost spike; potential CloudTrail log processing backlog | Disable data events for high-volume secrets; use CloudTrail Insights instead of full event logging | Enable CloudTrail data events only for management-plane operations (`PutSecretValue`, `DeleteSecret`) |
| Secrets over quota (default 500K per region) | Application auto-creates ephemeral secrets per job/session exceeding regional quota | `aws secretsmanager list-secrets --query 'length(SecretList)'`; request quota via Service Quotas console | `LimitExceededException` on secret creation; new deployments fail | Delete unused secrets immediately: `aws secretsmanager delete-secret --secret-id <name> --force-delete-without-recovery` | Use Parameters Store for short-lived config; reserve Secrets Manager for long-lived credentials |
| Hard-deleted secret immediately re-created causing dual rotation | Accidental delete followed by recreation with same name; rotation Lambda still firing on old ARN | CloudTrail sequence: `DeleteSecret` → `CreateSecret` same name; two rotation schedules active | Credential collision; rotation Lambda writing to wrong version | Cancel rotation on old ARN; verify new secret has correct rotation ARN | Always use `--recovery-window-in-days` (default 30 days) to prevent accidental instant deletion |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| GetSecretValue called per-request (no caching) | Application latency adds 20–80 ms per request; `GetSecretValue` call count equals request rate | `aws cloudwatch get-metric-statistics --namespace AWS/SecretsManager --metric-name SuccessfulRequestCount --period 60 --statistics Sum --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` | No in-process cache; secret fetched on every invocation | Add 5-minute TTL in-memory cache; use `aws-secretsmanager-caching-java` or `aws_secretsmanager_caching` Python library |
| Secrets Manager VPC endpoint saturated | All `GetSecretValue` calls timeout from within VPC; latency > 2s | `aws ec2 describe-vpc-endpoints --filters Name=service-name,Values=com.amazonaws.<region>.secretsmanager --query 'VpcEndpoints[*].State'`; CloudWatch `VpcEndpointPacketsDropped` | Single VPC endpoint handling too many concurrent connections; interface endpoint has per-AZ bandwidth limits | Add interface endpoints in additional AZs; distribute SDK calls across AZ-local endpoints |
| Cross-region secret read adding round-trip latency | Applications in `us-west-2` reading primary secret in `us-east-1`; 80–150 ms added per call | CloudTrail: `GetSecretValue` events with caller region ≠ secret region; `curl -w "%{time_total}" https://secretsmanager.us-east-1.amazonaws.com` | No replica configured in local region; SDK defaulting to primary region endpoint | Create replica: `aws secretsmanager replicate-secret-to-regions --secret-id <name> --add-replica-regions Region=us-west-2`; update SDK to use local region endpoint |
| Rotation Lambda cold start delay blocking application startup | App startup takes 10–30s; first secret fetch after Lambda cold start is slow | CloudWatch Lambda `InitDuration` metric for rotation function; `aws logs filter-log-events --log-group-name /aws/lambda/<fn> --filter-pattern "INIT_START"` | Lambda cold start initializes DB connection and calls `testSecret` step; no provisioned concurrency | Enable Lambda Provisioned Concurrency for rotation function; separate rotation Lambda from read path |
| KMS decrypt latency under high `GetSecretValue` volume | P99 `GetSecretValue` latency > 100 ms; CloudWatch KMS `NumberOfRequestsSucceeded` rate near quota | `aws cloudwatch get-metric-statistics --namespace AWS/KMS --metric-name NumberOfRequestsSucceeded --period 60 --statistics Sum` | KMS API has per-CMK request quota (5,500–30,000 req/s); high volume saturates decrypt quota | Switch to `aws/secretsmanager` managed key (higher quota); add application-side caching to reduce KMS decrypt calls |
| IAM policy evaluation latency on assume-role chains | Long assume-role chain adds 200–500 ms to each `GetSecretValue` call | `aws sts get-caller-identity` timing; CloudTrail `AssumeRole` latency in event metadata | Deep IAM role chaining (>3 hops) with SCP evaluation on every call; no credential caching | Cache STS credentials for full session duration (up to 1 hour); use SDK credential provider with caching; shorten role chains |
| Throttling causing retry storms amplifying latency | Application P99 latency spikes 5–10x; `ThrottledRequests` CloudWatch metric rising; SDK retries with backoff | `aws cloudwatch get-metric-statistics --namespace AWS/SecretsManager --metric-name ThrottledRequests --period 60 --statistics Sum` | Burst of `GetSecretValue` calls exceeds account-level quota; SDK retries amplify load | Implement exponential backoff with jitter in application; add circuit breaker; stagger pod startup to prevent simultaneous secret fetches |
| Serialization overhead from large secret values | `GetSecretValue` takes 50–200 ms; secret value is a large JSON blob > 64 KB | `aws secretsmanager describe-secret --secret-id <name> --query 'SecretVersionsToStages'`; time `aws secretsmanager get-secret-value --secret-id <name>` | Storing entire application config or certificate chain in one secret; JSON deserialization adds latency | Split large secrets into smaller purpose-specific secrets; keep individual secrets < 10 KB; use S3 for large config files |
| Batch describe calls in metadata-heavy automation | Automation tool calls `ListSecrets` + `DescribeSecret` per secret on every run; API call count high | CloudTrail `ListSecrets` call frequency; `aws secretsmanager list-secrets --query 'length(SecretList)'` | No caching of secret metadata; automation rescans entire namespace on every invocation | Cache `DescribeSecret` metadata with TTL; use tags-based filtering: `aws secretsmanager list-secrets --filters Key=tag-key,Values=<tag>` |
| Downstream dependency (RDS/Redis) latency inflating rotation test step | Rotation Lambda `testSecret` step takes > 10s; Lambda timeout triggers retry | CloudWatch Lambda `Duration` for rotation function; logs: `Connection timed out` in testSecret step | Target database slow to accept new credentials; network latency between Lambda and target | Increase Lambda timeout for rotation function to 5 minutes; add retry with backoff in `testSecret` step; pre-warm DB connections |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on VPC interface endpoint | `GetSecretValue` returns `SSL: CERTIFICATE_VERIFY_FAILED` from within VPC | `openssl s_client -connect vpce-<id>.secretsmanager.<region>.vpce.amazonaws.com:443 2>&1 | grep -E "notAfter|Verify"` | All in-VPC secret reads fail; applications cannot start or refresh credentials | AWS manages VPC endpoint TLS certs; if seeing this error, check for clock skew on client: `chronyc tracking`; update CA bundle: `pip install --upgrade certifi` |
| mTLS rotation failure — Lambda cannot reach target service | Rotation Lambda fails `setSecret` step; CloudWatch Logs show `Connection refused` or `SSL handshake failed` | `aws logs filter-log-events --log-group-name /aws/lambda/<rotation-fn> --filter-pattern "SSL\|handshake\|refused"`; check Lambda security group allows outbound to target | Secret in `AWSPENDING` state; application still uses `AWSCURRENT` | Fix Lambda VPC/SG config to allow outbound TLS to target; verify target's TLS cert is valid; re-trigger rotation |
| DNS resolution failure to Secrets Manager endpoint | Applications get `Name or service not known` when calling Secrets Manager | `dig secretsmanager.<region>.amazonaws.com`; within VPC: `dig vpce-<id>.secretsmanager.<region>.vpce.amazonaws.com` | All `GetSecretValue` calls fail; applications cannot start | Check VPC DNS settings: `enableDnsSupport` and `enableDnsHostnames` must be true; verify Route 53 resolver rules not blocking `.amazonaws.com` |
| TCP connection exhaustion to Secrets Manager | `getaddrinfo ENOTFOUND` or `ECONNRESET` from SDK; connection pool full | `ss -tnp | grep secretsmanager | wc -l`; check SDK `maxConnections` config | New secret fetch requests fail; existing cached secrets still valid | Increase SDK max connections; set `http.max_connections` in boto3 session config; add caching to reduce connection frequency |
| VPC endpoint policy blocking secret access | `GetSecretValue` returns `AccessDeniedException` from within VPC even with correct IAM role | `aws ec2 describe-vpc-endpoints --query 'VpcEndpoints[*].PolicyDocument'`; CloudTrail shows `AccessDenied` with source VPC endpoint | Applications in VPC cannot access secrets despite correct IAM permissions | Update VPC endpoint policy to allow `secretsmanager:GetSecretValue` for specific secret ARNs or `*` with resource restriction |
| Packet loss between Lambda and Secrets Manager VPC endpoint | Rotation Lambda intermittently fails; CloudWatch logs show timeout on some but not all executions | CloudWatch Lambda `Errors` metric; logs: `Read timeout` or `Connect timeout` in rotation Lambda; `traceroute` from Lambda-like EC2 in same subnet | Intermittent rotation failures; secrets get stuck in `AWSPENDING` | Check Lambda subnet route table for VPC endpoint routes; verify security group allows TCP 443 outbound; verify VPC endpoint in same AZ as Lambda |
| MTU mismatch on Lambda-to-VPC-endpoint path | Lambda connects to Secrets Manager but large responses (> 1 KB secret) timeout; small operations succeed | Test from EC2 in Lambda's subnet: `ping -M do -s 8972 <vpce-ip>`; check VPC endpoint interface MTU | Large secret values (certificates, JSON configs) fail to return; small secrets succeed | Set Lambda ENI MTU to 1500; avoid jumbo frames on the path to VPC interface endpoints; ensure Lambda security group rules do not block ICMP |
| Security group change blocking Secrets Manager API | `GetSecretValue` from EC2/ECS/Lambda starts failing after a security group update | CloudTrail: `ModifyNetworkInterfaceAttribute` or `AuthorizeSecurityGroupEgress` events near failure time; `aws ec2 describe-security-groups --group-ids <sg-id>` | All secret reads from affected compute fail; services unable to refresh credentials | Restore security group egress rule allowing TCP 443 to `com.amazonaws.<region>.secretsmanager` prefix list; or via VPC endpoint SG |
| SSL handshake timeout under concurrent rotation | Multiple rotation Lambdas fire simultaneously; Secrets Manager TLS negotiation queues | CloudWatch Secrets Manager `ThrottledRequests`; Lambda logs: `SSL handshake timed out` | Concurrent rotations fail; secrets left in `AWSPENDING` | Stagger rotation schedules; set Lambda `ReservedConcurrentExecutions=5` per rotation function; add retry logic in rotation Lambda |
| Connection reset by NAT gateway idle timeout | Lambda inside VPC uses NAT gateway (not VPC endpoint) to reach Secrets Manager; idle connections reset after 350s | CloudTrail: `GetSecretValue` called via NAT (source IP is NAT EIP, not VPC endpoint IP); connection resets after ~6 minutes idle | Cached SDK connections fail silently; next request fails then SDK retries | Switch to Secrets Manager VPC interface endpoint to avoid NAT; set SDK `tcp_keepalive=True` in boto3; set connection max age to 300s |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill on rotation Lambda | Lambda function timeout or OOM error; secret stuck in `AWSPENDING` | `aws logs filter-log-events --log-group-name /aws/lambda/<rotation-fn> --filter-pattern "Runtime exited\|OOMKilled"`; CloudWatch Lambda `Errors` with `Runtime.ExitError` | Increase Lambda memory: `aws lambda update-function-configuration --function-name <fn> --memory-size 512`; re-trigger rotation | Set rotation Lambda to 256–512 MB minimum; load test rotation Lambda against target DB before enabling; monitor Lambda `MaxMemoryUsed` |
| Secret version accumulation filling storage quota | `CreateSecretValue` fails with `LimitExceededException`; secret has hundreds of versions | `aws secretsmanager list-secret-version-ids --secret-id <name> --query 'length(Versions)'`; `aws secretsmanager list-secret-version-ids --secret-id <name> --include-deprecated` | Remove deprecated versions (no staging labels): automated after next successful rotation; or manually `aws secretsmanager delete-secret --secret-id <name>` (only if wrong secret) | Secrets Manager auto-purges versions with no staging labels after rotation; ensure rotation completes `finishSecret` step to label `AWSCURRENT` |
| Secrets count quota exhaustion (500K per region) | `LimitExceededException` when creating new secrets; `aws secretsmanager list-secrets` approaching quota | `aws secretsmanager list-secrets --query 'length(SecretList)'`; `aws service-quotas get-service-quota --service-code secretsmanager --quota-code L-2D4F6B82` | Delete unused secrets: `aws secretsmanager delete-secret --secret-id <name> --force-delete-without-recovery`; request quota increase via Service Quotas | Audit and purge ephemeral secrets quarterly; use SSM Parameter Store for non-credential config; tag secrets with lifecycle metadata |
| File descriptor exhaustion in rotation Lambda | Lambda function errors with `too many open files`; DB connection leaked | Lambda `Errors` metric; rotation Lambda logs: `OSError: [Errno 24] Too many open files` | Fix rotation Lambda to close DB connections in `finally` block; set Lambda `ulimit` via env var `AWS_LAMBDA_FUNCTION_MEMORY_SIZE` | Ensure rotation Lambda uses context manager (`with`) for all DB connections; add connection timeout in Lambda init; use connection pooling with max 1 connection |
| Lambda concurrency exhaustion from rotation storm | Rotation of 100+ secrets fires simultaneously; Lambda account concurrency limit hit; rotations queue | CloudWatch Lambda `ConcurrentExecutions` at account limit; `aws lambda get-account-settings --query 'TotalCodeSize'`; Secrets Manager rotation `FAILED` events | Stagger rotation windows across secrets; request Lambda concurrency increase; set `ReservedConcurrentExecutions=5` per rotation function | Stagger rotation schedules by ±1 day across secrets; use rotation window with jitter; set per-function reserved concurrency |
| CloudWatch Logs log group storage exhaustion from verbose rotation | Lambda log group fills CloudWatch Logs storage; log ingestion throttled; log data lost | `aws logs describe-log-groups --query 'logGroups[?logGroupName==`/aws/lambda/<rotation-fn>`]'`; CloudWatch `IncomingBytes` rate | Set log group retention: `aws logs put-retention-policy --log-group-name /aws/lambda/<fn> --retention-in-days 14` | Set retention policy on all rotation Lambda log groups at creation; default is never-expire if not set |
| KMS CMK request quota exhaustion | `GetSecretValue` returns `ThrottlingException: Rate exceeded` from KMS | `aws cloudwatch get-metric-statistics --namespace AWS/KMS --metric-name NumberOfRequestsThrottled --period 60 --statistics Sum`; `aws kms describe-key --key-id <id> --query 'KeyMetadata.KeyUsage'` | Request KMS quota increase via Service Quotas; switch to `aws/secretsmanager` managed key for high-volume secrets | Use `aws/secretsmanager` managed key unless BYOK required; add application-side secret caching to reduce KMS call volume |
| Network socket buffer exhaustion in high-volume caching client | Application caching library opens many parallel connections to Secrets Manager; TCP send buffer fills | `ss -s` on application host; `sysctl net.core.wmem_max`; SDK debug logging shows connection stalls | Increase `net.core.wmem_max=16777216`; reduce application-side connection pool max size | Limit concurrent Secrets Manager connections in application; cache secrets at process level to reduce parallel fetches |
| Ephemeral port exhaustion from per-request secret fetches | Application gets `OSError: [Errno 99] Cannot assign requested address`; ephemeral ports in TIME-WAIT | `ss -s | grep TIME-WAIT`; `sysctl net.ipv4.ip_local_port_range` | Enable TCP TIME-WAIT reuse: `sysctl -w net.ipv4.tcp_tw_reuse=1`; increase port range: `sysctl -w net.ipv4.ip_local_port_range="1024 65535"` | Cache secrets — never open new HTTPS connection per request; use persistent HTTP keep-alive in SDK; reuse `boto3.client('secretsmanager')` singleton |
| IAM token refresh rate exhaustion | Applications requesting STS tokens too frequently to access Secrets Manager; `ThrottlingException` on `AssumeRole` | CloudTrail `AssumeRole` event rate per role; `aws cloudwatch get-metric-statistics --namespace AWS/STS --metric-name ThrottledRequests` | Cache STS credentials for full session duration (1 hour); use `botocore` credential cache | Use SDK with credential caching (`~/.aws/cli/cache/`); do not call `assume-role` on every secret access; use instance profiles where possible |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Rotation partial failure leaving secret in AWSPENDING | Rotation Lambda fails `finishSecret` step; `AWSPENDING` version exists but `AWSCURRENT` not updated | `aws secretsmanager list-secret-version-ids --secret-id <name> --query 'Versions[*].{ID:VersionId,Labels:VersionStages}'`; look for `AWSPENDING` with no `AWSCURRENT` promoted | Application continues using old credential; new credential active on target DB but not in Secrets Manager | Re-trigger rotation: `aws secretsmanager rotate-secret --secret-id <name>`; or manually promote: `aws secretsmanager update-secret-version-stage --secret-id <name> --version-stage AWSCURRENT --move-to-version-id <pending-id> --remove-from-version-id <old-id>` |
| Dual-version collision during concurrent rotation requests | Two simultaneous `RotateSecret` calls create two `AWSPENDING` versions; only one can be promoted | CloudTrail: two `RotateSecret` events within seconds; `list-secret-version-ids` shows two `AWSPENDING` versions | One rotation clobbers the other; final secret version may not match DB password | Cancel second rotation; verify DB password matches `AWSCURRENT` version; re-run rotation cleanly | Prevent concurrent rotation calls via SQS FIFO or Lambda idempotency token |
| Cross-region replication lag causing stale secret reads | Application in replica region reads `AWSCURRENT` version that differs from primary; DB connection fails | `aws secretsmanager describe-secret --secret-id <arn-in-replica-region> --query 'ReplicationStatus'`; compare `VersionId` between primary and replica regions | Applications in replica region use stale credentials; DB connection failures until replication catches up | Force replication sync: `aws secretsmanager replicate-secret-to-regions --secret-id <name> --add-replica-regions Region=<replica>`; implement retry with backoff in application | Monitor replication lag via CloudTrail `ReplicateSecretToRegions` events; add replication health check to deployment pipeline |
| Secret deleted during rotation mid-flight | Secret deleted (accidentally or by automation) while rotation Lambda is executing | CloudTrail sequence: `RotateSecret` → `DeleteSecret` within minutes; rotation Lambda logs `ResourceNotFoundException` | Rotation Lambda cannot complete; credentials may be in inconsistent state on target | Restore secret from recovery window: `aws secretsmanager restore-secret --secret-id <name>`; verify DB credentials still match; re-run rotation | Set `--recovery-window-in-days 30` as default; add `aws:RequestedRegion` SCP denying `DeleteSecret` on rotation-active secrets |
| At-least-once delivery duplicate — application reads both AWSCURRENT and AWSPREVIOUS | Application fetches secret by VersionStage but race condition during rotation returns different version to different pods | Compare secret `VersionId` across pods: `aws secretsmanager get-secret-value --secret-id <name> --version-stage AWSCURRENT --query 'VersionId'` from multiple app hosts | Some pods use old credentials, some use new; DB session errors for pods with mismatched versions | Roll pod restarts after rotation completes to ensure all pods pick up `AWSCURRENT`; use `SIGHUP` reload without restart where possible |
| Compensating transaction failure — rotation rollback not implemented | Rotation Lambda's `setSecret` step succeeds (new creds set on DB) but `testSecret` fails; no rollback to old password | CloudWatch Logs rotation Lambda: `testSecret FAILED`; DB has new password but Secrets Manager still shows old `AWSCURRENT` | DB password changed but `AWSCURRENT` secret value is wrong; all applications fail DB auth | Manually set DB password back to `AWSCURRENT` secret value; or update `AWSCURRENT` to new password via `aws secretsmanager put-secret-value`; re-run rotation | Implement rollback in `testSecret`: if test fails, restore old password on target before returning failure |
| Out-of-order secret version promotion across regions | Multi-region application promotes `AWSCURRENT` in replica before primary; regions have different `AWSCURRENT` versions | `aws secretsmanager get-secret-value --secret-id <arn> --query 'VersionId'` in each region; compare version IDs | Region A uses password V2, Region B uses V1; cross-region DB writes use different auth contexts | Always initiate rotation from primary region only; verify replication status before applications in replica regions restart | Lock rotation operations to primary region via IAM condition: `aws:RequestedRegion StringEquals <primary-region>` |
| Distributed lock expiry during credential propagation | Application holds distributed lock based on secret version; lock expires before all pods read new `AWSCURRENT` | Check lock TTL vs. pod restart duration: if pods take 5 min to restart and lock TTL is 3 min, overlap occurs; monitor pod startup times | Two application generations run simultaneously with different credentials; overlapping DB sessions | Set distributed lock TTL = max pod rolling restart duration + 2× margin; use Kubernetes `maxUnavailable=1` during secret rotation events |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — one tenant's rotation Lambda consuming all concurrent executions | CloudWatch Lambda `ConcurrentExecutions` at account limit; other tenants' rotation Lambdas queued | Other tenants' secret rotations delayed or failed; services using stale credentials | `aws lambda get-function-concurrency --function-name <noisy-fn>`; `aws lambda put-function-concurrency --function-name <noisy-fn> --reserved-concurrent-executions 5` | Set `ReservedConcurrentExecutions` per rotation Lambda to cap each tenant's Lambda concurrency |
| Memory pressure from adjacent tenant's large secret value serialization | Lambda OOM when processing rotation for tenant with 256 KB secret (certificate chain) alongside tenants with 1 KB secrets | Rotation Lambda OOM-killed; secrets for co-located tenants fail rotation | `aws lambda get-function-configuration --function-name <fn> --query 'MemorySize'`; `aws logs filter-log-events --log-group-name /aws/lambda/<fn> --filter-pattern "OOMKilled"` | Increase Lambda memory for rotation function: `aws lambda update-function-configuration --function-name <fn> --memory-size 1024`; use dedicated rotation Lambda per large-secret tenant |
| Disk I/O saturation from tenant's bulk secret creation | CloudTrail shows one account creating thousands of secrets per minute; Secrets Manager API latency rising for all callers | `GetSecretValue` latency increases account-wide due to service-side load from bulk creation | Check `CreateSecret` call rate: `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=CreateSecret`; identify source account | Apply SCP or resource control policy limiting `secretsmanager:CreateSecret` calls per principal; request account-level quota investigation from AWS |
| Network bandwidth monopoly from tenant bulk `GetSecretValue` | VPC interface endpoint bandwidth saturated; other tenants' applications see connection timeouts | All traffic through shared VPC endpoint slowed; secret fetch latency spikes for all tenants in VPC | Check VPC endpoint metrics: `aws cloudwatch get-metric-statistics --namespace AWS/PrivateLinkEndpoints --metric-name BytesProcessed --period 60 --statistics Sum` | Add per-AZ VPC endpoints; apply IAM rate limiting per principal; add application-side caching to reduce volume through endpoint |
| Connection pool starvation — tenant app exhausting VPC endpoint connection slots | New `GetSecretValue` connections refused from some application pods; VPC endpoint at max connections | Specific tenant's application pods cannot fetch secrets; startup fails; existing pods unaffected | `aws ec2 describe-vpc-endpoints --query 'VpcEndpoints[*].NetworkInterfaceIds'`; `ss -tnp | grep vpce | wc -l` from affected pods | Add caching in noisy tenant's application; add additional VPC endpoints; enforce connection pool max per application pod |
| Quota enforcement gap — tenant bypassing account-level secret count limit | `LimitExceededException` on new secret creation; tenant using ephemeral secrets without cleanup | Other tenants unable to create new secrets; automation blocked | `aws secretsmanager list-secrets --query 'length(SecretList)'` vs `aws service-quotas get-service-quota --service-code secretsmanager --quota-code L-2D4F6B82` | Delete ephemeral secrets from offending tenant: tag and sweep; enforce naming convention with automated cleanup Lambda; request quota increase per tenant namespace |
| Cross-tenant data leak risk from shared KMS CMK | Multiple tenants' secrets encrypted with same KMS CMK; one tenant's IAM policy grants CMK `kms:Decrypt` to unauthorized principal | Unauthorized principal can decrypt any secret using the shared CMK | `aws kms list-grants --key-id <cmk-id>`; compare grant list to expected principals per tenant | Create per-tenant CMKs; revoke unauthorized grants: `aws kms revoke-grant --key-id <cmk> --grant-id <id>`; re-encrypt secrets with tenant-specific CMK |
| Rate limit bypass — tenant using service-linked role with no throttle | One tenant's automation uses service-linked role exempt from per-principal throttling; monopolizes `GetSecretValue` quota | Other tenants experience `ThrottledRequests` | CloudTrail: identify `userIdentity.type: AWSService` calls vs `IAMUser`/`AssumedRole`; CloudWatch `ThrottledRequests` metric by `Principal` dimension | Request AWS quota increase for account; enforce caching at application layer; escalate to AWS support if service-linked role bypass is confirmed |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure — CloudWatch SES metrics not publishing | Grafana dashboard shows `No Data` for `SuccessfulRequestCount`; alerts not firing | CloudWatch `AWS/SecretsManager` namespace has no data if no API calls made; or metric filter misconfigured | `aws cloudwatch list-metrics --namespace AWS/SecretsManager` — verify metrics exist; test with `aws secretsmanager get-secret-value --secret-id <name>` and check metrics update | Ensure at least one `GetSecretValue` call per minute via synthetic monitor; add CloudWatch alarm with `INSUFFICIENT_DATA` action |
| Trace sampling gap — rotation failures missing from X-Ray traces | X-Ray traces show no rotation Lambda invocations during failed rotation period | Rotation Lambda not instrumented with X-Ray SDK; or X-Ray sampling rate set to 0 | Check Lambda X-Ray configuration: `aws lambda get-function-configuration --function-name <fn> --query 'TracingConfig'`; if `PassThrough`, no traces | Enable X-Ray active tracing on rotation Lambda: `aws lambda update-function-configuration --function-name <fn> --tracing-config Mode=Active` |
| Log pipeline silent drop — rotation Lambda logs lost | Rotation failure not recorded; CloudWatch Logs shows no entries for rotation execution time | Lambda log group retention policy expired old logs before investigation; or Lambda execution role missing `logs:CreateLogGroup` | Check log group existence: `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/<rotation-fn>`; check execution role permissions for `logs:PutLogEvents` | Set log group retention before relying on it: `aws logs put-retention-policy --log-group-name /aws/lambda/<fn> --retention-in-days 90`; add `logs:*` to Lambda execution role |
| Alert rule misconfiguration — rotation failure alert never triggers | Secrets stuck in `AWSPENDING` state for hours; no alert fires; services use stale credentials | CloudWatch alarm based on `RotationFailed` EventBridge event but event pattern does not match Secrets Manager event format | Test alert manually: `aws events put-events --entries '[{"Source":"aws.secretsmanager","DetailType":"AWS API Call via CloudTrail","Detail":"{}"}]'`; verify EventBridge rule pattern matches | Use correct EventBridge pattern for Secrets Manager rotation failure: `detail.eventName: RotationFailed`; test pattern in EventBridge console |
| Cardinality explosion — one CloudWatch metric dimension per secret | Dashboards show thousands of metric streams; CloudWatch costs spike; queries time out | Application publishing custom CloudWatch metrics with `SecretName` as dimension; thousands of secrets create thousands of metric streams | `aws cloudwatch list-metrics --namespace MyApp/Secrets | python3 -c "import sys,json; print(len(json.load(sys.stdin)['Metrics']))"` — count metric streams | Aggregate secret access metrics by secret type or environment, not individual secret name; use CloudWatch EMF with low-cardinality dimensions |
| Missing health endpoint for rotation status | No visibility into whether all secrets have been successfully rotated within their schedule | Secrets Manager has no native "rotation overdue" alert; secrets can silently fail to rotate for weeks | Query for overdue rotations: `aws secretsmanager list-secrets --query 'SecretList[?RotationEnabled==\`true\` && LastRotatedDate!=null]'`; calculate days since rotation | Create Lambda that runs daily, queries `ListSecrets`, and alerts if `LastRotatedDate` > rotation schedule + 24 hours; publish result to CloudWatch custom metric |
| Instrumentation gap — application secret fetch errors not logged | Application silently retries `GetSecretValue` on `ThrottlingException`; errors not logged; root cause invisible | AWS SDK default retry swallows `ThrottlingException` before it surfaces to application code; no custom error metric | Add CloudWatch metric filter on Lambda/application logs for `ThrottlingException`; `aws logs put-metric-filter --filter-pattern "ThrottlingException" --metric-name SecretsFetchThrottled` | Instrument SDK with custom retry handler that publishes `ThrottlingException` count to CloudWatch; add X-Ray subsegment around every `GetSecretValue` call |
| PagerDuty / Alertmanager outage silencing Secrets Manager rotation alerts | Production rotation failures occur; no PagerDuty incident created; services degrade silently | Alertmanager pod down; SNS topic subscription for rotation events unconfirmed or endpoint unreachable | Verify SNS subscription: `aws sns list-subscriptions-by-topic --topic-arn <rotation-alerts-topic> --query 'Subscriptions[*].SubscriptionArn'` — check for `PendingConfirmation` | Confirm SNS subscription; add dead-man's-switch: scheduled EventBridge rule triggers synthetic alert to PagerDuty every 15 minutes — confirms alert path is working |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor rotation Lambda version upgrade rollback | New Lambda version breaks rotation (`testSecret` fails); secrets stuck in `AWSPENDING` | `aws lambda list-versions-by-function --function-name <fn> --query 'Versions[*].{Version:Version,Modified:LastModified}'`; CloudWatch Logs for `FAILED` in rotation Lambda | Roll back Lambda alias to previous version: `aws lambda update-alias --function-name <fn> --name LIVE --function-version <prev-version>`; re-trigger rotation | Use Lambda versioning with aliases; deploy new rotation Lambda version to `CANARY` alias first; validate rotation on a non-critical secret before promoting to `LIVE` |
| Schema migration partial completion — new rotation Lambda requires new secret format | Rotation Lambda updated to expect JSON key `db_password_v2` but existing secrets have `db_password`; `setSecret` step fails | CloudWatch Logs rotation Lambda: `KeyError: db_password_v2`; `aws secretsmanager list-secret-version-ids --secret-id <name>` shows `AWSPENDING` with no `AWSCURRENT` promotion | Roll back Lambda to previous version; update secret format via `aws secretsmanager put-secret-value --secret-id <name> --secret-string '{"db_password_v2":"<val>"}'`; re-trigger rotation | Version secret schema; deploy Lambda that handles both old and new format during migration; never change required JSON keys without backward compatibility |
| Rolling upgrade version skew — mixed rotation Lambda versions during blue-green | Blue Lambda expects `host` key; green Lambda expects `hostname` key; some rotations use wrong Lambda version | CloudTrail: `UpdateFunctionCode` on rotation Lambda; check which Lambda version is mapped to secret: `aws secretsmanager describe-secret --secret-id <name> --query 'RotationLambdaARN'` | Point all secrets back to blue Lambda ARN: `aws secretsmanager rotate-secret --secret-id <name> --rotation-lambda-arn <blue-arn>`; redeploy green Lambda with backward compatibility | Never rename required JSON keys in rotation Lambda; use additive schema changes; deploy new Lambda version alongside old before switching ARN on secrets |
| Zero-downtime migration gone wrong — secret ARN change breaking app references | Application migrated to reference new secret by name but old ARN cached in environment variables; old secret deleted | `aws secretsmanager describe-secret --secret-id <new-name>`; application logs: `ResourceNotFoundException` for old ARN | Recreate old secret with same ARN (not possible — ARNs are immutable); restore from `--recovery-window-in-days` if recently deleted: `aws secretsmanager restore-secret --secret-id <old-name>` | Never delete secrets without updating all references first; use Secrets Manager aliases or SSM Parameter Store pointers for indirection; audit all references before deletion |
| Config format change — KMS key rotation breaking secret decryption | KMS CMK rotated; Secrets Manager cannot decrypt old secret versions using new key material | `aws kms describe-key --key-id <key-id> --query 'KeyMetadata.KeyRotationEnabled'`; `aws secretsmanager get-secret-value --secret-id <name>` returns `KMSInvalidStateException` | AWS KMS automatic rotation retains old key material for decryption — this should not break existing secrets; if using BYOK: re-import old key material via `aws kms import-key-material` | Understand KMS rotation: automatic rotation only replaces key material for new encryptions; old versions remain decryptable; for BYOK, never delete old key material |
| Data format incompatibility — Terraform rotate block added to existing secret | Terraform adds `rotation_rules` to a secret that was never rotated; `rotation_lambda_arn` points to function not compatible with existing secret value format | `terraform plan` shows no-op but `apply` triggers first rotation; check: `aws secretsmanager describe-secret --secret-id <name> --query 'RotationEnabled'` changed to `true` | Disable rotation: `aws secretsmanager cancel-rotate-secret --secret-id <name>`; verify `AWSCURRENT` version is still correct; fix rotation Lambda for existing format | Test rotation Lambda against existing secret format in staging before enabling rotation in Terraform; add `lifecycle { ignore_changes = [rotation_rules] }` until Lambda is validated |
| Feature flag rollout — new `SecretString` JSON structure causing parser failures | Application updated to parse new JSON structure from secret; old secret value still in old format; parse error on startup | Application logs: `KeyError`, `JSONDecodeError`; check secret value format: `aws secretsmanager get-secret-value --secret-id <name> --query 'SecretString'` — compare to new expected format | Roll back application to previous version; or update secret to new format: `aws secretsmanager put-secret-value --secret-id <name> --secret-string '{"newKey":"val"}'` | Use backward-compatible secret schema changes; add both old and new keys during transition; deploy application that reads new key with fallback to old key |
| Dependency version conflict — boto3/botocore upgrade breaking rotation Lambda | Rotation Lambda fails after Lambda layer upgrade; `AttributeError` on `secretsmanager` client method | CloudWatch Logs: `AttributeError: 'SecretsManager' object has no attribute 'rotate_secret'`; check Lambda layer version: `aws lambda get-layer-version --layer-name <name> --version-number <n>` | Roll back Lambda layer: `aws lambda update-function-configuration --function-name <fn> --layers <previous-layer-arn>`; redeploy rotation Lambda with pinned boto3 version | Pin `boto3==1.x.y` in rotation Lambda `requirements.txt`; use Lambda layer with explicit version; test Lambda in staging after any layer upgrade |

## Kernel/OS & Host-Level Failure Patterns

| Failure | Symptom | Why It Happens | Detection Command | Remediation |
|---------|---------|---------------|-------------------|-------------|
| OOM killer terminates rotation Lambda container | Secret rotation fails silently; secret stuck in `AWSPENDING`; CloudWatch Logs show no invocation record | Lambda container memory limit exceeded during rotation (e.g., large secret payload or heavy SDK initialization) | `aws lambda get-function-configuration --function-name <rotation-fn> --query 'MemorySize'`; CloudWatch Logs: `aws logs filter-log-events --log-group-name /aws/lambda/<fn> --filter-pattern "Runtime exited with error"` | Increase Lambda memory: `aws lambda update-function-configuration --function-name <rotation-fn> --memory-size 512`; optimize rotation code to reduce memory footprint |
| Inode exhaustion on EC2 instance running secret-caching sidecar | Application cannot write cached secret values to local disk; `GetSecretValue` calls spike as cache misses increase | Secret caching daemon writes one file per secret version; thousands of secrets with frequent rotation exhaust inodes | `df -i /var/cache/secrets/` on application host; `ls /var/cache/secrets/ \| wc -l` — count cached secret files | Clean stale cache files: `find /var/cache/secrets/ -mtime +7 -delete`; switch to in-memory caching (AWS SDK SecretCache library) instead of disk-based cache |
| CPU steal on EC2 host delays rotation Lambda cold start | Rotation Lambda execution takes >5 min; `setSecret` step times out; rotation marked failed | Lambda cold start on shared hardware with CPU steal >10%; SDK initialization and KMS decrypt calls delayed | CloudWatch Lambda metrics: `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Duration --dimensions Name=FunctionName,Value=<rotation-fn> --period 300 --statistics p99` — check for p99 >60s | Use Lambda provisioned concurrency for rotation function: `aws lambda put-provisioned-concurrency-config --function-name <fn> --qualifier <alias> --provisioned-concurrent-executions 1` |
| NTP skew causes rotation Lambda STS token expiration | Rotation Lambda receives `ExpiredTokenException` on `GetSecretValue` call; rotation fails at `testSecret` step | Lambda container clock drifted; STS temporary credentials signature validation fails due to skew >5 min | CloudWatch Logs: `aws logs filter-log-events --log-group-name /aws/lambda/<fn> --filter-pattern "ExpiredTokenException"`; check Lambda execution time vs token validity | Retry rotation: `aws secretsmanager rotate-secret --secret-id <name>`; if persistent, report to AWS Support (Lambda platform clock skew is AWS-side issue) |
| File descriptor exhaustion on application host fetching many secrets | Application startup fails with `Too many open files`; `GetSecretValue` calls fail with SDK socket error | Application fetches 500+ secrets at startup; each HTTPS connection to Secrets Manager consumes a file descriptor; default ulimit 1024 | `cat /proc/<app-pid>/limits \| grep "open files"`; `ls /proc/<app-pid>/fd \| wc -l` — count open fds; correlate with number of secrets fetched | Increase ulimit: `ulimit -n 65535`; use batch fetching with `BatchGetSecretValue` API (up to 20 secrets per call); implement connection pooling in SDK client |
| TCP conntrack saturation from high-frequency secret polling | Application secret refresh calls fail intermittently; `dmesg` shows `nf_conntrack: table full` on host | Application polls `GetSecretValue` every 10s for 100+ secrets; each poll creates new HTTPS connection; conntrack table exhausted | `dmesg -T \| grep conntrack` on application host; `sysctl net.netfilter.nf_conntrack_count` vs `net.netfilter.nf_conntrack_max` | Increase conntrack: `sysctl -w net.netfilter.nf_conntrack_max=262144`; reduce polling frequency; use AWS SDK `SecretCache` with 1-hour TTL instead of per-request fetching |
| Kernel TLS handshake failure on FIPS-enabled host blocks Secrets Manager access | Application receives `SSLError` when calling Secrets Manager API; all secret operations fail | FIPS-enabled kernel restricts TLS cipher suites; Secrets Manager endpoint requires cipher not in FIPS-approved list for older SDK versions | `openssl s_client -connect secretsmanager.<region>.amazonaws.com:443 -tls1_2` on host; check SDK version: `pip show boto3` | Upgrade boto3/botocore to latest version with FIPS endpoint support; use FIPS endpoint explicitly: `aws secretsmanager get-secret-value --endpoint-url https://secretsmanager-fips.<region>.amazonaws.com --secret-id <name>` |
| NUMA imbalance on multi-socket host delays KMS decrypt during secret retrieval | `GetSecretValue` latency p99 spikes on specific application instances; KMS decrypt step takes 3x longer than baseline | Application process scheduled on NUMA node remote from network adapter; KMS API calls have higher latency due to cross-NUMA memory access | Compare `GetSecretValue` latency across instances: `aws cloudwatch get-metric-statistics --namespace MyApp --metric-name SecretFetchLatency --dimensions Name=InstanceId,Value=<id> --period 300 --statistics p99` | Pin application process to NUMA node with NIC: `numactl --cpunodebind=0 --membind=0 <app-binary>`; or use smaller instance types with single NUMA node |

## Deployment Pipeline & GitOps Failure Patterns

| Failure | Symptom | Why It Happens | Detection Command | Remediation |
|---------|---------|---------------|-------------------|-------------|
| Terraform apply creates secret but rotation Lambda not yet deployed | Secret created with `rotation_rules` but rotation fails immediately; `RotationFailed` event in EventBridge | Terraform creates secret and rotation config in one apply; Lambda function referenced by ARN not yet deployed by separate pipeline | `aws secretsmanager describe-secret --secret-id <name> --query '{Rotation: RotationEnabled, Lambda: RotationLambdaARN}'`; `aws lambda get-function --function-name <arn>` — check if function exists | Deploy rotation Lambda before creating secret: reorder Terraform `depends_on`; or use `aws secretsmanager cancel-rotate-secret --secret-id <name>` until Lambda ready |
| Helm chart secret reference uses wrong Secrets Manager ARN after environment promotion | Application pod fails to start; init container fetching secret gets `ResourceNotFoundException` | Helm values file promoted from staging to prod without updating secret ARN; staging ARN does not exist in prod account | `kubectl logs <pod> -c init-secrets \| grep ResourceNotFoundException`; `helm get values <release> -o yaml \| grep secretArn` | Update Helm values with correct prod ARN; use SSM Parameter Store to store secret ARNs per environment: `aws ssm get-parameter --name /<env>/secret-arn` |
| ArgoCD sync overwrites ExternalSecret CR with stale secret version | ExternalSecret CRD refreshes but ArgoCD sync reverts `refreshInterval` to old value; secrets not refreshing | ArgoCD sync prunes ExternalSecret modifications made by external-secrets operator; Git manifest has stale `refreshInterval` | `kubectl get externalsecret <name> -o yaml \| grep refreshInterval`; `argocd app diff <app> --local <path>` — check ExternalSecret fields | Add `ignoreDifferences` for ExternalSecret `.status` fields in ArgoCD Application; update Git manifest to match desired `refreshInterval` |
| PDB blocking secret-injector webhook pod restart | Secrets Manager webhook cannot process new pod admissions; pods stuck `Pending` waiting for secret injection | PodDisruptionBudget on secret-injector deployment prevents rollout; single replica cannot be evicted | `kubectl get pdb -A \| grep secret`; `kubectl rollout status deploy/secret-injector`; `kubectl get events \| grep "Cannot evict"` | Temporarily delete PDB: `kubectl delete pdb secret-injector-pdb`; restart pod; recreate PDB; or scale to 2 replicas before applying PDB |
| Blue-green deployment uses old secret version in green environment | Green deployment connects to database with old credentials; rotation happened between blue and green deploy | Green environment cached secret value at deploy time; rotation updated secret after green was deployed but before cutover | `aws secretsmanager describe-secret --secret-id <name> --query 'VersionIdsToStages'` — check `AWSCURRENT` timestamp vs green deploy time | Force green to re-fetch secrets before cutover: restart green pods; or use ExternalSecret with `refreshInterval: 30s` during cutover window |
| ConfigMap containing Secrets Manager secret ARN drifted from Git | Application fetching wrong secret; ConfigMap manually edited during incident; Git still has old ARN | SRE manually updated ConfigMap during rotation incident; ArgoCD auto-sync disabled; Git and live state diverged | `kubectl get cm <name> -o yaml \| grep secretArn`; `git show HEAD:<configmap.yaml> \| grep secretArn` — compare | Commit live ConfigMap value to Git; re-enable ArgoCD auto-sync; add `argocd.argoproj.io/managed-by` annotation to prevent manual edits |
| CI/CD pipeline caches secret value in build artifact | Leaked secret in container image layer; rotation does not invalidate cached value; old credential persists | Dockerfile `RUN aws secretsmanager get-secret-value` at build time bakes secret into image layer | `docker history <image>` — check for `GetSecretValue` in build steps; scan image: `trivy image <image> --scanners secret` | Never fetch secrets at build time; fetch at runtime via init container or sidecar; use `--secret` flag in Docker BuildKit for build-time secrets |
| GitOps secret rotation trigger race condition | Two ArgoCD applications both trigger rotation on same secret; second rotation fails with `PreviousRotationNotComplete` | Multiple ArgoCD apps reference same secret with rotation hooks; both trigger simultaneously on sync | CloudWatch Logs: `aws logs filter-log-events --log-group-name /aws/lambda/<fn> --filter-pattern "PreviousRotationNotComplete"`; `aws secretsmanager describe-secret --secret-id <name> --query 'LastRotatedDate'` | Centralize rotation trigger: one ArgoCD app owns rotation; other apps use `refreshInterval` to pick up new values; add `PreviousRotationNotComplete` retry logic to rotation Lambda |

## Service Mesh & API Gateway Edge Cases

| Failure | Symptom | Why It Happens | Detection Command | Remediation |
|---------|---------|---------------|-------------------|-------------|
| Envoy sidecar blocks rotation Lambda VPC connectivity to Secrets Manager endpoint | Rotation Lambda in VPC cannot reach Secrets Manager VPC endpoint; `setSecret` step times out | Lambda VPC ENI assigned Envoy sidecar via init container; Envoy intercepts HTTPS to VPC endpoint but lacks proper upstream config | CloudWatch Logs: `aws logs filter-log-events --log-group-name /aws/lambda/<fn> --filter-pattern "ConnectTimeoutError"`; check VPC endpoint: `aws ec2 describe-vpc-endpoints --filters Name=service-name,Values=com.amazonaws.<region>.secretsmanager` | Exclude Lambda from service mesh injection; or add Secrets Manager VPC endpoint to Envoy passthrough: add `istio.io/inject: false` annotation on Lambda ENI security group; verify VPC endpoint security group allows Lambda |
| Rate limiting on API Gateway fronting secret management API | Application `GetSecretValue` calls throttled by API Gateway rate limit; secret fetch failures cascade | Custom API Gateway proxy in front of Secrets Manager for audit/auth; rate limit set too low for burst secret fetching at pod startup | `aws apigateway get-usage --usage-plan-id <id> --start-date <date> --end-date <date>` — check throttled requests; application logs: `429 Too Many Requests` | Increase API Gateway rate limit: `aws apigateway update-usage-plan --usage-plan-id <id> --patch-operations op=replace,path=/throttle/rateLimit,value=1000`; implement exponential backoff in SDK; use direct Secrets Manager endpoint instead of API GW proxy |
| Stale secret value cached in Envoy HTTP header injection | Service mesh injects secret as HTTP header via Lua filter; secret rotated but Envoy cache not refreshed | Envoy Lua filter caches `GetSecretValue` result with 1-hour TTL; rotation happens between cache refreshes; downstream receives old credential | `istioctl proxy-config route <pod> -o json \| jq '.. \| .requestHeadersToAdd? // empty'` — check for stale secret values; compare to `aws secretsmanager get-secret-value --secret-id <name> --query 'VersionId'` | Reduce Envoy Lua filter cache TTL to match rotation frequency; or replace header injection with application-level secret fetching using SDK SecretCache |
| mTLS rotation breaks Secrets Manager VPC endpoint connectivity | Application suddenly cannot reach Secrets Manager VPC endpoint; `GetSecretValue` returns `ConnectionError` | Istio rotated workload certificates; VPC endpoint security group does not recognize new certificate source IP after pod reschedule | `aws ec2 describe-vpc-endpoints --vpc-endpoint-ids <id> --query 'VpcEndpoints[*].Groups'` — check security group rules; `openssl s_client -connect vpce-<id>.secretsmanager.<region>.vpce.amazonaws.com:443` | Update VPC endpoint security group to allow all pod CIDR ranges; or configure Secrets Manager VPC endpoint policy to allow by IAM role (not source IP) |
| Retry storm on Secrets Manager API during mass pod restart | All pods restart simultaneously; each fetches 10+ secrets; `GetSecretValue` API rate limit (5000 req/s) exceeded; `ThrottlingException` cascade | Kubernetes rolling restart with `maxSurge=100%` causes all pods to call Secrets Manager concurrently during init | `aws cloudwatch get-metric-statistics --namespace AWS/SecretsManager --metric-name APICallCount --period 60 --statistics Sum` — check for spike; application logs: `ThrottlingException` | Set `maxSurge=25%` on deployment; implement exponential backoff with jitter in secret fetching init container; use secrets caching sidecar (Kubernetes Secrets Store CSI Driver) |
| gRPC secret injection sidecar hangs on Secrets Manager timeout | gRPC service readiness probe fails; pods marked `NotReady`; secret injection sidecar blocking gRPC server startup | Sidecar container fetches secrets before main container starts; Secrets Manager API timeout (30s) exceeded; init container never exits | `kubectl describe pod <pod> \| grep -A5 "Init Containers"`; `kubectl logs <pod> -c secret-init \| tail -20` | Add timeout and fallback to secret init container: exit after 60s with cached fallback secret; configure `startupProbe` with longer timeout on main container |
| Trace context lost between secret-fetching sidecar and application | Distributed trace shows gap during secret retrieval; cannot correlate slow secret fetch with downstream latency | Secret-fetching sidecar makes separate HTTPS call to Secrets Manager without propagating parent trace context | `aws xray get-trace-summaries --start-time <time> --end-time <time> --filter-expression 'service("secretsmanager")'` — check for orphaned spans | Instrument secret-fetching sidecar with X-Ray SDK; propagate `X-Amzn-Trace-Id` header from parent request to Secrets Manager API calls |
| Envoy connection pool exhaustion from concurrent secret rotations | Multiple secrets rotating simultaneously; each rotation Lambda opens VPC connections through mesh; Envoy `cx_active` limit reached | 50+ secrets with same rotation schedule (midnight); all rotation Lambdas invoke simultaneously; Envoy circuit breaker trips | `istioctl proxy-config cluster <pod> -o json \| jq '.. \| .circuitBreakers?.thresholds[]?.maxConnections'`; `aws secretsmanager list-secrets --query "SecretList[?RotationEnabled] \| length(@)"` | Stagger rotation schedules: `aws secretsmanager rotate-secret --secret-id <name> --rotation-rules '{"ScheduleExpression":"cron(0 <staggered-hour> * * ? *)"}'`; increase Envoy connection pool limits via DestinationRule |
