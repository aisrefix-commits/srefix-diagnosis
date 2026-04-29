---
name: cloudwatch-agent
provider: aws
domain: cloudwatch
aliases:
  - aws-cloudwatch
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - provider-aws
  - component-cloudwatch-agent
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
# CloudWatch SRE Agent

## Role
Site Reliability Engineer specializing in AWS CloudWatch metrics and alarms platform. Responsible for alarm state management (ALARM/INSUFFICIENT_DATA/OK), composite alarm topology, metric math expressions, anomaly detection models, dashboard reliability, PutMetricData throttling, CloudWatch agent deployment on EC2, metric retention and resolution strategies, cross-account observability, and the health of the observability infrastructure itself. Distinct from CloudWatch Logs (separate agent scope).

## Architecture Overview

```
Data Sources
├── AWS Services (native metrics, 1-min resolution)
│   ├── EC2, ECS, EKS, Lambda, RDS, SQS, ALB, etc.
│   └── Published directly into AWS/[Namespace]
├── CloudWatch Agent (EC2 / on-prem)
│   ├── System metrics: cpu, mem, disk, net
│   ├── StatsD / collectd receiver (port 8125/25826)
│   └── Custom metrics via PutMetricData API
└── SDK / CLI: aws cloudwatch put-metric-data
        │
        ▼
┌──────────────────────────────────────────────────────┐
│               CloudWatch Metric Store                │
│  ├── Standard Resolution (60s)                       │
│  ├── High Resolution (1s, custom metrics only)       │
│  ├── Retention: 1s→3h, 60s→15d, 5m→63d, 1hr→15mo   │
│  └── Namespace / Dimension / MetricName hierarchy    │
└──────────────┬───────────────────────────────────────┘
               │
    ┌──────────▼──────────────────────────────────────┐
    │  Alarm Engine                                   │
    │  ├── Simple Metric Alarm                        │
    │  │   └── States: OK | ALARM | INSUFFICIENT_DATA │
    │  ├── Composite Alarm                            │
    │  │   └── Boolean logic on child alarm states    │
    │  ├── Anomaly Detection Alarm                    │
    │  │   └── ML band; ALARM when outside band       │
    │  └── Metric Math Alarm                          │
    │      └── Expression evaluates to alarm          │
    └──────────┬──────────────────────────────────────┘
               │ Alarm Actions
    ┌──────────▼─────────────────────────┐
    │  Actions                           │
    │  ├── SNS Topic → PagerDuty/Slack   │
    │  ├── Auto Scaling Policy           │
    │  ├── EC2 Action (reboot/recover)   │
    │  └── Lambda / SSM OpsItem          │
    └────────────────────────────────────┘
               │
    Cross-account: CloudWatch Observability (source account → monitoring account)
```

CloudWatch alarms are independent of CloudWatch Logs. Each alarm evaluates a single metric or metric math expression against a threshold over an evaluation period. States transition through OK → ALARM → INSUFFICIENT_DATA depending on data availability and threshold comparison.

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| `AWS/CloudWatch/MetricsPutCount` (custom metrics count) | > 80% of account limit | > 90% | 10M metrics/month free; charges beyond |
| `PutMetricData` API error rate (ThrottlingException) | > 0.1% of calls | > 1% | 150 TPS per account default limit |
| Alarms in INSUFFICIENT_DATA state | > 5% of alarms | > 20% | Missing data; check metric source |
| Alarms in ALARM state without acknowledged incident | Any critical | > 3 simultaneously | Alarm storm may indicate cascading failure |
| CloudWatch Agent `mem_used_percent` (on agent host) | > 80% | > 95% | Agent itself may OOM |
| Dashboard load time | > 3s | > 10s | Too many metrics or complex metric math |
| Anomaly detection model accuracy | > 5% false positive rate | > 20% | Retrain or adjust sensitivity |
| Cross-account metric share latency | > 30s | > 5 min | Monitoring account data delay |
| `GetMetricData` API throttling | Any | > 10 calls/min throttled | Dashboards or automation over-querying |
| Alarm evaluation errors | Any | > 1% of evaluations | Missing IAM permissions or metric math error |

## Alert Runbooks

### Alert: Alarm Storm — Multiple Critical Alarms Firing Simultaneously
**Symptom:** > 5 alarms transition to ALARM within 5 minutes; PagerDuty storm; may indicate shared dependency failure

**Triage:**
```bash
# List all alarms currently in ALARM state
aws cloudwatch describe-alarms \
  --state-value ALARM \
  --query 'MetricAlarms[*].{Name:AlarmName,Metric:MetricName,Namespace:Namespace,StateReason:StateReason}' \
  --output table

# Look for pattern in alarm names (same service? same region? same namespace?)
aws cloudwatch describe-alarms \
  --state-value ALARM \
  --query 'MetricAlarms[*].AlarmName' --output text | tr '\t' '\n' | sort

# Check if composite alarm is correctly aggregating child alarms
aws cloudwatch describe-alarms \
  --alarm-types CompositeAlarm \
  --query 'CompositeAlarms[*].{Name:AlarmName,Rule:AlarmRule,State:StateValue}'

# Check StateReason for shared root cause clue
aws cloudwatch describe-alarms --state-value ALARM \
  --query 'MetricAlarms[*].{Name:AlarmName,Reason:StateReason,Since:StateUpdatedTimestamp}' \
  --output table | sort -k3

# Temporarily disable notifications during storm investigation (suppress, don't delete)
aws cloudwatch disable-alarm-actions --alarm-names <alarm1> <alarm2>
# RE-ENABLE after investigation:
aws cloudwatch enable-alarm-actions --alarm-names <alarm1> <alarm2>
```

### Alert: PutMetricData Throttling
**Symptom:** Application logs show `ThrottlingException: Rate exceeded` when pushing custom metrics; dashboards show gaps

**Triage:**
```bash
# Check current PutMetricData call rate
aws cloudwatch get-metric-statistics \
  --namespace AWS/CloudWatch \
  --metric-name CallCount \
  --dimensions Name=Service,Value=MetricService Name=Class,Value=None Name=Type,Value=API Name=Resource,Value=PutMetricData \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 --statistics Sum

# Check for errors
aws cloudwatch get-metric-statistics \
  --namespace AWS/CloudWatch \
  --metric-name ErrorCount \
  --dimensions Name=Service,Value=MetricService Name=Class,Value=None Name=Type,Value=API Name=Resource,Value=PutMetricData \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 --statistics Sum

# Identify which hosts/services are throttling (check CloudWatch Agent logs)
# On EC2 with CloudWatch Agent:
sudo tail -f /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log | \
  grep -i "throttl\|error\|failed"

# Mitigation: increase buffering in CloudWatch Agent config to batch more metrics
sudo jq '.metrics.metrics_collection_interval = 60' \
  /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json > /tmp/cw-config-updated.json
sudo cp /tmp/cw-config-updated.json /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
sudo systemctl restart amazon-cloudwatch-agent

# Request limit increase (default 150 TPS, can be raised to 1500 TPS)
aws service-quotas get-service-quota \
  --service-code monitoring --quota-code L-A5D9FCFA
```

### Alert: INSUFFICIENT_DATA Alarms Spreading
**Symptom:** Multiple alarms flip to INSUFFICIENT_DATA; metrics missing from dashboards

**Triage:**
```bash
# List all INSUFFICIENT_DATA alarms
aws cloudwatch describe-alarms \
  --state-value INSUFFICIENT_DATA \
  --query 'MetricAlarms[*].{Name:AlarmName,Namespace:Namespace,Metric:MetricName,Dims:Dimensions}' \
  --output table

# Check if the underlying metric exists and has recent data
ALARM_NAME="<alarm-name>"
METRIC_INFO=$(aws cloudwatch describe-alarms --alarm-names $ALARM_NAME \
  --query 'MetricAlarms[0].{NS:Namespace,Metric:MetricName,Dims:Dimensions}')

aws cloudwatch get-metric-statistics \
  --namespace $(echo $METRIC_INFO | jq -r '.NS') \
  --metric-name $(echo $METRIC_INFO | jq -r '.Metric') \
  --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 --statistics Average

# If no data: check if CloudWatch Agent is running on EC2
aws ec2 describe-instance-status \
  --filter "Name=tag:Environment,Values=production" \
  --query 'InstanceStatuses[*].{ID:InstanceId,Status:InstanceStatus.Status}'

# Check CloudWatch Agent health via SSM
aws ssm send-command \
  --instance-ids <instance-id> \
  --document-name "AWS-RunShellScript" \
  --parameters commands='sudo systemctl status amazon-cloudwatch-agent'

# For alarms with TreatMissingData, review current setting
aws cloudwatch describe-alarms --alarm-names <alarm-name> \
  --query 'MetricAlarms[0].TreatMissingData'
# Verify: breaching vs notBreaching vs ignore vs missing
```

### Alert: Composite Alarm Not Triggering as Expected
**Symptom:** Child alarms are in ALARM but composite alarm remains OK; or composite alarm fires when it shouldn't

**Triage:**
```bash
# Describe composite alarm and its rule
aws cloudwatch describe-alarms \
  --alarm-names <composite-alarm-name> \
  --alarm-types CompositeAlarm \
  --query 'CompositeAlarms[0].{State:StateValue,Rule:AlarmRule,Reason:StateReason}'

# Check all child alarm states referenced in the rule
aws cloudwatch describe-alarms \
  --query 'MetricAlarms[*].{Name:AlarmName,State:StateValue}' \
  --state-value ALARM | grep -E "child-alarm-name-1|child-alarm-name-2"

# Describe children explicitly
CHILD_NAMES="alarm-1 alarm-2 alarm-3"
aws cloudwatch describe-alarms --alarm-names $CHILD_NAMES \
  --query 'MetricAlarms[*].{Name:AlarmName,State:StateValue,Updated:StateUpdatedTimestamp}'

# Validate the AlarmRule expression syntax
# Rule format: ALARM("child-1") AND ALARM("child-2")  OR  ALARM("child-1") OR NOT OK("child-3")
# Must include quotes around alarm names in rule

# Test composite alarm behavior by temporarily setting child to ALARM
aws cloudwatch set-alarm-state \
  --alarm-name <child-alarm> \
  --state-value ALARM \
  --state-reason "Testing composite alarm evaluation"

# Watch composite alarm state update (up to 60s delay)
watch -n 5 "aws cloudwatch describe-alarms --alarm-names <composite-alarm> --query 'CompositeAlarms[0].{State:StateValue,Reason:StateReason}' --output table"

# Restore child alarm to OK
aws cloudwatch set-alarm-state --alarm-name <child-alarm> --state-value OK --state-reason "Test complete"
```

## Common Issues & Troubleshooting

### Issue 1: Metric Math Expression Alarm Always INSUFFICIENT_DATA
**Symptom:** Metric math alarm stays in INSUFFICIENT_DATA even when component metrics have data

```bash
# Get alarm details including metric math expression
aws cloudwatch describe-alarms --alarm-names <alarm-name> \
  --query 'MetricAlarms[0].Metrics'

# Test the metric math expression manually using GetMetricData
aws cloudwatch get-metric-data \
  --metric-data-queries '[
    {
      "Id": "m1",
      "MetricStat": {
        "Metric": {
          "Namespace": "AWS/ApplicationELB",
          "MetricName": "HTTPCode_Target_5XX_Count",
          "Dimensions": [{"Name": "LoadBalancer", "Value": "<alb-id>"}]
        },
        "Period": 60,
        "Stat": "Sum"
      }
    },
    {
      "Id": "m2",
      "MetricStat": {
        "Metric": {
          "Namespace": "AWS/ApplicationELB",
          "MetricName": "RequestCount",
          "Dimensions": [{"Name": "LoadBalancer", "Value": "<alb-id>"}]
        },
        "Period": 60,
        "Stat": "Sum"
      }
    },
    {
      "Id": "error_rate",
      "Expression": "100 * m1 / m2",
      "Label": "Error Rate %"
    }
  ]' \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S)

# Common cause: division by zero when RequestCount == 0 returns NaN → INSUFFICIENT_DATA
# Fix: use IF() to handle zero denominator
# Expression: IF(m2 > 0, 100 * m1 / m2, 0)

# Update alarm expression
aws cloudwatch put-metric-alarm \
  --alarm-name <alarm-name> \
  --metrics '[{"Id":"m1",...},{"Id":"m2",...},{"Id":"e1","Expression":"IF(m2>0,100*m1/m2,0)","Label":"Error Rate","ReturnData":true}]' \
  --comparison-operator GreaterThanThreshold \
  --threshold 5 \
  --evaluation-periods 3 \
  --treat-missing-data notBreaching
```

### Issue 2: Anomaly Detection Alarm False Positives After Deployment
**Symptom:** Anomaly detection alarm fires during expected traffic pattern changes (deployments, batch jobs, business hours)

```bash
# View current anomaly detection configuration
aws cloudwatch describe-anomaly-detectors \
  --namespace <namespace> --metric-name <metric-name>

# Check anomaly detection band visually (describe model)
aws cloudwatch describe-anomaly-detectors \
  --query 'AnomalyDetectors[*].{Metric:SingleMetricAnomalyDetector.MetricName,Config:Configuration,State:StateValue}'

# Exclude known anomaly periods (scheduled jobs, maintenance windows)
aws cloudwatch put-anomaly-detector \
  --namespace <namespace> \
  --metric-name <metric-name> \
  --stat Average \
  --configuration '{
    "ExcludedTimeRanges": [
      {"StartTime": "2026-04-15T01:00:00Z", "EndTime": "2026-04-15T03:00:00Z"},
      {"StartTime": "2026-04-16T01:00:00Z", "EndTime": "2026-04-16T03:00:00Z"}
    ],
    "MetricTimezone": "America/Los_Angeles"
  }'

# Adjust alarm band width (AnomDetectionBandWidth, default 2 standard deviations)
# In alarm definition, AnomalyDetectorBandWidth determines sensitivity:
aws cloudwatch put-metric-alarm \
  --alarm-name <alarm-name> \
  --comparison-operator GreaterThanUpperThreshold \
  --threshold-metric-id ad1 \
  --metrics '[{"Id":"m1",...},{"Id":"ad1","Expression":"ANOMALY_DETECTION_BAND(m1, 3)","Label":"Expected","ReturnData":true}]' \
  --evaluation-periods 5

# Force model retrain after major infrastructure change
# Models are retrained automatically; exclusion periods speed this up
# For immediate reset: delete and recreate anomaly detector
aws cloudwatch delete-anomaly-detector \
  --namespace <namespace> --metric-name <metric-name> --stat Average
aws cloudwatch put-anomaly-detector \
  --namespace <namespace> --metric-name <metric-name> --stat Average
```

### Issue 3: CloudWatch Agent High Memory / CPU on EC2
**Symptom:** `amazon-cloudwatch-agent` process consuming > 500MB RAM or > 50% CPU on EC2 instance

```bash
# Check agent resource usage
ps aux | grep amazon-cloudwatch-agent
top -p $(pgrep amazon-cloudwatch-agent) -b -n 1

# Check agent logs for issues
sudo tail -200 /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log | \
  grep -E "error|warn|throttl|buffer"

# View current config — look for high-frequency metrics or many log streams
sudo cat /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json | \
  python3 -m json.tool | grep -E "metrics_collection_interval|log_group|file_path"

# Reduce metric collection frequency (minimum 10s for high-res, 60s for standard)
sudo jq '.metrics.metrics_collection_interval = 60 |
  .metrics.aggregation_dimensions = [["InstanceId"]]' \
  /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
  > /tmp/cw-config.json
sudo cp /tmp/cw-config.json /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

# Remove unused metric collections from config
# Check if StatsD or collectd receiver is enabled unnecessarily
sudo jq '.metrics.metrics_collected | keys' \
  /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

# Restart agent
sudo systemctl restart amazon-cloudwatch-agent
sudo systemctl status amazon-cloudwatch-agent
```

### Issue 4: Cross-Account Metric Sharing Broken
**Symptom:** Monitoring account dashboards show `No Data` for source account metrics; cross-account alarms show INSUFFICIENT_DATA

```bash
# Check CloudWatch cross-account observability configuration in monitoring account
aws cloudwatch list-managed-insight-rules --resource-arn arn:aws:cloudwatch:<region>:<monitoring-account-id>:*

# Verify sharing configuration in source account
aws cloudwatch get-metric-stream --name <metric-stream-name> 2>/dev/null || \
  echo "No metric stream; using CloudWatch cross-account sharing"

# Check cross-account sharing is enabled in source account
aws cloudwatch describe-insight-rules 2>/dev/null
# Or check via RAM (Resource Access Manager)
aws ram list-resources --resource-owner SELF --resource-type AWS::CloudWatch::Dashboard

# Check the cross-account observability link
aws oam list-links
aws oam get-link --identifier <link-arn> \
  --query '{Source:label,SinkIdentifier:sinkIdentifier,ResourceTypes:resourceTypes}'

# Check if OAM link is active
aws oam list-attached-policies --resource-identifier <link-arn>

# Verify IAM permissions in monitoring account allow cross-account GetMetricData
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::<monitoring-account>:role/<role> \
  --action-names cloudwatch:GetMetricData \
  --resource-arns "*"

# Recreate OAM link if broken
aws oam create-link \
  --label-template "\$AccountEmail" \
  --resource-types "AWS::CloudWatch::Metric" \
  --sink-identifier arn:aws:oam:<region>:<monitoring-account>:sink/<sink-id>
```

### Issue 5: Dashboard Loading Extremely Slowly or Timing Out
**Symptom:** CloudWatch dashboard takes > 30s to load; `GetMetricData` API calls timing out

```bash
# Check how many metrics a dashboard has (widget by widget)
aws cloudwatch get-dashboard --dashboard-name <name> \
  --query 'DashboardBody' --output text | python3 -m json.tool | \
  jq '[.widgets[] | .properties.metrics | length] | add // 0'

# Count total GetMetricData calls a dashboard would make
# (Each widget = 1 GetMetricData call; each call can have up to 500 metric queries)

# Check GetMetricData throttling in your account
aws cloudwatch get-metric-statistics \
  --namespace AWS/CloudWatch \
  --metric-name ThrottledRequests \
  --dimensions Name=Service,Value=CloudWatch Name=Type,Value=API \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 --statistics Sum

# Optimize dashboard: split into multiple focused dashboards
# Or increase period to reduce data points (1 day = 24 data points vs 1440 at 1-min resolution)

# Check for metric math that calls SEARCH() function (expensive)
aws cloudwatch get-dashboard --dashboard-name <name> \
  --query 'DashboardBody' --output text | grep -o '"SEARCH[^"]*"' | head -10

# Replace SEARCH() with explicit metric queries where possible
# SEARCH returns all matching metrics which is expensive for broad patterns

# Check if dashboard uses cross-account metrics (adds latency)
aws cloudwatch get-dashboard --dashboard-name <name> \
  --query 'DashboardBody' --output text | grep -o '"accountId":"[^"]*"' | sort -u
```

### Issue 6: Alarm Not Triggering Despite Threshold Crossed
**Symptom:** Metric clearly exceeds threshold on console graph but alarm stays OK

```bash
# Describe alarm configuration in full detail
aws cloudwatch describe-alarms --alarm-names <alarm-name> \
  --query 'MetricAlarms[0]'

# Key fields to check:
# - EvaluationPeriods: N periods must all exceed threshold (strict AND logic)
# - DatapointsToAlarm: can be less than EvaluationPeriods (M of N evaluation)
# - Period: how often metric is sampled
# - Statistic: Sum vs Average vs Maximum (easy to confuse)
# - ComparisonOperator: GreaterThanThreshold vs GreaterThanOrEqualToThreshold

# Check actual metric value vs threshold using same Period and Statistic as alarm
aws cloudwatch get-metric-statistics \
  --namespace <namespace> \
  --metric-name <metric-name> \
  --dimensions Name=<dim-name>,Value=<dim-value> \
  --start-time $(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period <alarm-period> \
  --statistics <alarm-statistic>

# Common mistake: alarm uses Sum but graph shows Average — Sum may be tiny per period
# Common mistake: EvaluationPeriods=5 means 5 consecutive periods must ALL exceed threshold

# Test by manually forcing alarm state
aws cloudwatch set-alarm-state \
  --alarm-name <alarm-name> \
  --state-value ALARM \
  --state-reason "Manual test - verifying alarm actions work"

# Check SNS topic still exists and has correct subscription
ALARM_ACTIONS=$(aws cloudwatch describe-alarms --alarm-names <alarm-name> \
  --query 'MetricAlarms[0].AlarmActions' --output text)
aws sns get-topic-attributes --topic-arn $ALARM_ACTIONS
aws sns list-subscriptions-by-topic --topic-arn $ALARM_ACTIONS
```

## Key Dependencies

- **AWS IAM** — `cloudwatch:PutMetricData`, `cloudwatch:GetMetricData`, `cloudwatch:PutMetricAlarm`; CloudWatch Agent requires an instance profile or IAM role with `CloudWatchAgentServerPolicy`
- **SNS** — alarm actions route to SNS topics; SNS delivery to PagerDuty/Slack/email; SNS topic policies must allow CloudWatch principal to publish
- **EC2 Instance Profile** — CloudWatch Agent needs instance profile; if using SSM for agent config, requires `AmazonSSMManagedInstanceCore` policy
- **Lambda** — alarm actions can invoke Lambda; Lambda execution role must allow CloudWatch to invoke
- **Auto Scaling** — scaling policy triggered by alarms; CloudWatch must be able to `autoscaling:ExecutePolicy`
- **AWS OAM (Observability Access Manager)** — for cross-account metric sharing; sink/link pair required in source and monitoring accounts
- **Service Quotas** — PutMetricData 150 TPS default; GetMetricData 50 TPS; metrics per alarm 10; alarms per account 5000 default (soft limit)

## Cross-Service Failure Chains

- **EC2 instance profile removed** → CloudWatch Agent loses PutMetricData permission → custom and system metrics stop → alarms go INSUFFICIENT_DATA → alerting blind → incidents go undetected
- **SNS topic deleted** → all alarm notifications fail silently → alarms still fire (ALARM state changes) but no PagerDuty/Slack notification → on-call team not paged
- **PutMetricData throttling** → metric gaps → INSUFFICIENT_DATA alarms → false all-clear signals → cascading failure goes unnoticed
- **Composite alarm misconfigured** → child alarms fire but composite stays OK → incident management tool shows "all ok" → delayed response
- **Auto Scaling alarm with wrong statistic** → scale-out never triggers on CPU spike → traffic spike overwhelms existing instances → request timeouts cascade

## Partial Failure Patterns

- **Dashboard partial load failure**: Widget with SEARCH() function fails but other widgets succeed; dashboard appears partially populated; easy to miss missing data
- **Mixed resolution metrics in same alarm**: Combining 1-second and 60-second metrics in metric math causes evaluation period mismatches; alarm evaluation may use interpolated values
- **Anomaly detector not trained for new metric**: New metric has < 2 weeks of data; anomaly detector model incomplete; band is very wide; sensitivity near zero during learning period
- **Cross-account metric sharing one-way latency**: Source account metrics are delayed 30–120s in monitoring account; alarms in monitoring account trigger later than alarms in source account

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|----------|
| PutMetricData API latency | < 100ms | 100–500ms | > 1s or ThrottlingException |
| GetMetricData API latency | < 1s | 1–5s | > 10s (dashboard timeout) |
| Alarm state evaluation delay | < 60s | 60s–5min | > 10min (alarm evaluation backlog) |
| Alarm action (SNS publish) | < 30s | 30–120s | > 5 min |
| CloudWatch Agent metric flush | < 60s | 60–300s | > 5 min (data loss risk) |
| Dashboard load time | < 3s | 3–15s | > 30s (browser timeout) |
| Cross-account metric propagation | < 60s | 60–300s | > 10 min |
| Anomaly detection model convergence | < 14 days (new model) | 14–21 days | > 21 days (retrain needed) |

## Capacity Planning Indicators

| Indicator | Source | Trigger | Action |
|-----------|--------|---------|--------|
| Custom metric count > 7000 per namespace | `aws cloudwatch list-metrics` count | Trending up | Review metric cardinality; use dimensions sparingly |
| PutMetricData call rate > 120 TPS | CloudWatch API metrics | Sustained | Request limit increase (150 → 1500 TPS) |
| Number of alarms > 4000 | `aws cloudwatch describe-alarms --query 'length(MetricAlarms)'` | > 80% of 5000 default limit | Request limit increase or audit unused alarms |
| Dashboard widget count > 100 per dashboard | Console inspection | Any | Split dashboard; GetMetricData rate impacts load time |
| Metric data retention approaching custom limits | Any metric with 1-second resolution | > 2 days of 1s data | Shift to 60s resolution; 1s data expires after 3 hours natively |
| Cross-account observability links > 5 | `aws oam list-links` | Trending | Review monitoring architecture; consolidate into fewer sink accounts |
| Log Analytics / CloudWatch cost > budget | AWS Cost Explorer | Any | Review high-frequency custom metrics; reduce PutMetricData cardinality |
| Anomaly detectors > 200 per account | `aws cloudwatch describe-anomaly-detectors` | > 150 | Prioritize which metrics truly need anomaly detection |

## Diagnostic Cheatsheet

```bash
# Count alarms by state
aws cloudwatch describe-alarms \
  --query 'MetricAlarms[*].StateValue' --output text | \
  tr '\t' '\n' | sort | uniq -c

# List all alarms that haven't transitioned state in > 7 days (stale alarms)
aws cloudwatch describe-alarms \
  --query "MetricAlarms[?StateUpdatedTimestamp<'$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)'].{Name:AlarmName,State:StateValue,Since:StateUpdatedTimestamp}" \
  --output table

# Find alarms with no alarm actions (silently firing)
aws cloudwatch describe-alarms \
  --query 'MetricAlarms[?length(AlarmActions)==`0`].AlarmName' --output text

# List all custom metric namespaces in account
aws cloudwatch list-metrics --query 'Metrics[].Namespace' --output text | \
  tr '\t' '\n' | sort -u | grep -v '^AWS/'

# Check for metrics with no data in last 24 hours (orphaned metric dimensions)
aws cloudwatch list-metrics --namespace CWAgent \
  --query 'Metrics[*].Dimensions' --output text | \
  tr '\t' '\n' | sort | uniq -c | sort -rn | head -20

# Verify CloudWatch Agent is installed and running (via SSM to all prod instances)
aws ssm send-command \
  --targets "Key=tag:Environment,Values=production" \
  --document-name "AWS-RunShellScript" \
  --parameters '{"commands":["systemctl is-active amazon-cloudwatch-agent"]}' \
  --query 'Command.CommandId' --output text

# Get alarm history for an alarm (useful for flapping detection)
aws cloudwatch describe-alarm-history \
  --alarm-name <alarm-name> \
  --history-item-type StateUpdate \
  --start-date $(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%S) \
  --query 'AlarmHistoryItems[*].{Time:Timestamp,State:HistorySummary}' --output table

# Check PutMetricData success rate
aws cloudwatch get-metric-statistics \
  --namespace AWS/CloudWatch \
  --metric-name CallCount \
  --dimensions Name=Service,Value=MetricService Name=Type,Value=API Name=Resource,Value=PutMetricData Name=Class,Value=None \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 --statistics Sum

# List all composite alarms and their rules
aws cloudwatch describe-alarms \
  --alarm-types CompositeAlarm \
  --query 'CompositeAlarms[*].{Name:AlarmName,State:StateValue,Rule:AlarmRule}' --output table
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|-------------------|-------------|
| Alarm Notification Delivery | 99.9% | 43.2 min/month | Time from alarm state change to SNS publish < 60s |
| Metric Ingestion Completeness | 99.5% | 3.6 hr/month | PutMetricData success rate; < 0.5% throttled or errored calls |
| Critical Alarm False Positive Rate | < 1% | < 1% of alarm firings | Alarms manually reset to OK without incident follow-through |
| Dashboard Availability (< 5s load) | 99.0% | 7.2 hr/month | Synthetic monitoring of dashboard API response time |

## Configuration Audit Checklist

| Check | Command | Expected |
|-------|---------|----------|
| All critical alarms have alarm actions | `aws cloudwatch describe-alarms --query 'MetricAlarms[?length(AlarmActions)==\`0\`].AlarmName'` | Empty list |
| All alarms use TreatMissingData appropriately | `aws cloudwatch describe-alarms --query 'MetricAlarms[*].{N:AlarmName,T:TreatMissingData}'` | `notBreaching` for rate metrics; `breaching` for availability |
| CloudWatch Agent running on all EC2 | SSM Run Command check across fleet | `active` on all instances |
| Composite alarms cover all critical paths | Manual review of composite alarm rules | All critical service alarms covered by composite |
| SNS topics for alarm actions are active | `aws sns list-topics` + `list-subscriptions-by-topic` for each | Active subscriptions with confirmed endpoints |
| Anomaly detectors configured for key metrics | `aws cloudwatch describe-anomaly-detectors` | Key SLO metrics have detectors |
| Cross-account links are authorized | `aws oam list-links` | Only known monitoring account sinks |
| PutMetricData quota sufficient | `aws service-quotas get-service-quota --service-code monitoring --quota-code L-A5D9FCFA` | Current usage < 80% of quota |
| Alarms have descriptions | `aws cloudwatch describe-alarms --query 'MetricAlarms[?AlarmDescription==null].AlarmName'` | Empty list; all alarms documented |
| High-resolution alarms on critical metrics | Review period setting on P0 alarms | Period <= 60s for latency/error rate alarms |

## Log Pattern Library

| Log Pattern | Source | Meaning |
|-------------|--------|---------|
| `ThrottlingException: Rate exceeded` | CloudWatch API (agent log or SDK) | PutMetricData TPS limit hit; increase interval or request quota raise |
| `Failed to describe metric: AccessDeniedException` | CloudWatch Agent | Instance profile missing `cloudwatch:DescribeAlarms` or `cloudwatch:GetMetricData` |
| `unable to write to disk: no space left on device` | CloudWatch Agent | Agent buffer disk full; clean `/opt/aws/amazon-cloudwatch-agent/` spool |
| `Drop metrics from queue: xxxx metrics dropped` | CloudWatch Agent | Flush buffer overflow; agent cannot publish fast enough |
| `DataAlreadyAcceptedException` | PutMetricData API | Duplicate data submission (idempotent; safe to ignore) |
| `InvalidParameterValueException: The value xxxx for parameter MetricName is invalid` | PutMetricData | Metric name contains invalid characters or is too long |
| `Alarm transitioned to INSUFFICIENT_DATA` | CloudWatch alarm history | Metric stopped reporting; check data source |
| `Alarm transitioned to ALARM` | CloudWatch alarm history | Threshold breach confirmed over evaluation period |
| `amazon-cloudwatch-agent: plugin: M inputs gathered` | CloudWatch Agent | Normal operation; M metrics collected this cycle |
| `Failed to load config: error parsing JSON` | CloudWatch Agent | Config file syntax error; validate with `amazon-cloudwatch-agent-config-wizard` |
| `W! [outputs.cloudwatchmetrics] Metric dropped: namespace contains illegal character` | CloudWatch Agent (telegraf-based) | Namespace or dimension value with `/` or other illegal chars |
| `OAM Link creation failed: sink not found` | OAM cross-account | Sink ARN invalid or wrong region; check sink exists in monitoring account |

## Error Code Quick Reference

| Error | Service | Meaning | Fix |
|-------|---------|---------|-----|
| `ThrottlingException` | CloudWatch API | TPS limit exceeded | Reduce call frequency; request quota increase |
| `InvalidParameterCombination` | PutMetricData | Conflicting parameters (e.g., both Value and StatisticValues) | Fix API call; use one or the other |
| `InvalidParameterValueException` | Various | Metric name/namespace format violation | Check AWS naming restrictions |
| `ResourceNotFoundException` | GetMetricData / DescribeAlarms | Alarm or resource doesn't exist | Verify name/ARN; check region |
| `LimitExceededException` | PutMetricAlarm | Alarm quota (5000 default) reached | Delete unused alarms or request limit increase |
| `ValidationError` | PutMetricAlarm | Invalid alarm configuration (e.g., bad expression) | Validate metric math expression syntax |
| `AccessDeniedException` | Any API | Missing IAM permission | Add required cloudwatch action to IAM policy |
| `ServiceUnavailableException` | Any CloudWatch API | Transient AWS-side error | Retry with exponential backoff |
| `INSUFFICIENT_DATA` | Alarm state | No data available for evaluation period | Check metric source; fix agent; review TreatMissingData |
| `InvalidFormatException` | PutMetricData with EMF | Embedded Metric Format JSON malformed | Validate against EMF schema |
| `DatapointsToAlarmRequired` | PutMetricAlarm | DatapointsToAlarm cannot exceed EvaluationPeriods | Set DatapointsToAlarm <= EvaluationPeriods |
| `OAM: ConflictException` | `CreateLink` | Link already exists to this sink | Use `UpdateLink` instead or delete existing link |

## Known Failure Signatures

| Signature | Root Cause | Distinguishing Indicator |
|-----------|-----------|------------------------|
| All CWAgent namespaced metrics missing, AWS service metrics fine | CloudWatch Agent stopped or IAM revoked | `list-metrics --namespace CWAgent` returns empty; AWS/ namespaces have data |
| Alarm flapping OK↔ALARM every 5 min | EvaluationPeriods=1, metric inherently noisy | Alarm history shows rapid alternation; fix with M-of-N evaluation |
| All alarms in INSUFFICIENT_DATA after midnight UTC | Metric stopped publishing (cron job failed, batch finished) | All data gaps start at same time; check data source scheduled task |
| Composite alarm OK despite children in ALARM | Boolean rule logic error; wrong child alarm names in rule | `describe-alarms` shows child names don't exactly match rule strings (quotes, spaces) |
| Dashboard shows "no data" for last 15 minutes only | CloudWatch metric resolution gap (1-min data; 15-min aging into 5-min buckets) | Normal for standard resolution metrics; use period >= 300s for consistency |
| PutMetricData succeeds but metric never appears | Incorrect dimensions (extra space, wrong case) | Metric appears under different dimension; `list-metrics` with exact filter |
| Anomaly detector never leaves "learning" state | Insufficient historical data or excluded time ranges cover all data | Model state shows < 2 weeks of data; check exclusion range config |
| Alarm action triggers but PagerDuty not paged | SNS subscription unconfirmed or HTTPS endpoint returned non-200 | Check SNS delivery status; `list-subscriptions-by-topic` for subscription status |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `ThrottlingException: Rate exceeded` on `PutMetricData` | AWS SDK (boto3 / CloudWatch SDK) | Sending > 1000 metrics/s per account or > 150 TPS per namespace | CloudWatch `ThrottledRequests` metric for `AWS/CloudWatch`; check publish rate | Batch `PutMetricData` to 20 metrics per call; distribute publishing across time |
| Alarm stays `INSUFFICIENT_DATA` indefinitely | AWS Console / SNS notifications | Metric stopped publishing; dimension mismatch; wrong metric name (case-sensitive) | `aws cloudwatch list-metrics --namespace <ns> --metric-name <name>` — verify exact name/dimensions | Fix metric name/dimensions in alarm config; verify data source still publishing |
| Alarm fires but SNS notification never arrives | Application alert receiver (PagerDuty, Slack) | SNS topic policy denies CloudWatch publish; topic deleted; subscription unconfirmed | `aws sns list-subscriptions-by-topic --topic-arn <arn>`; check SNS delivery logs in CloudWatch Logs | Confirm subscription; check SNS topic access policy allows `cloudwatch.amazonaws.com` to publish |
| Dashboard shows stale data / stops updating at a fixed time | Browser / CloudWatch Console | Metric resolution changed (1-min data aging into 5-min bucket); dashboard period too short | `aws cloudwatch get-metric-statistics --period 60 --statistics Average` for recent period | Increase dashboard widget `period` to match data retention resolution (300s for > 3 h data) |
| `InvalidParameterCombination: At most 500 metrics` | AWS SDK (metric math) | Metric Insights query or metric math expression references > 500 metrics | Count metrics in `MetricDataQueries`; check `SEARCH()` expression scope | Narrow `SEARCH()` filter; split into multiple API calls; use metric math `SORT()` with `LIMIT` |
| `ValidationError: The value ... is not supported` in metric math | AWS SDK / CloudWatch Dashboards | Unsupported function or invalid time window in metric math expression | Validate expression locally; check CloudWatch metric math function docs | Use supported functions; validate expression in console before automating |
| No data points returned for custom metrics from EC2 | Application custom metric publisher | CloudWatch Agent stopped; IAM role missing `cloudwatch:PutMetricData` | `systemctl status amazon-cloudwatch-agent`; CloudTrail `PutMetricData` access denied | Restart CWAgent; add `CloudWatchAgentServerPolicy` to instance role |
| Composite alarm never transitions to ALARM despite children in ALARM | CloudWatch Alarms | Boolean rule logic error; child alarm name mismatch (spaces, quotes) in `AlarmRule` | `aws cloudwatch describe-alarms --alarm-names <composite>` → check `AlarmRule` child names exactly | Fix `AlarmRule` to match child alarm names exactly; re-save composite alarm |
| CloudWatch Logs Insights query returns partial results | CloudWatch Logs Insights console / SDK | Query scanned > 20 GB or hit 15-min query timeout | Check query stats: `Statistics.RecordsScanned`; reduce time range | Narrow time range; add `filter` before `stats` to reduce scanned records; use log groups selectively |
| Metric anomaly detector alarm always in `INSUFFICIENT_DATA` | CloudWatch Alarms | Anomaly detector model still in training (requires 2 weeks of data) | `aws cloudwatch describe-alarms --query '[].{Name:AlarmName,Model:AnomalyDetectors}'` | Wait for model training; ensure metric has sufficient historical data; remove excluded time ranges |
| `AccessDeniedException` on `GetMetricData` for cross-account metrics | AWS SDK | CloudWatch cross-account observability sink or source policy not configured | `aws cloudwatch describe-metric-streams`; check OAM sink/link policy | Configure Observability Access Manager (OAM) sink in monitoring account; link source accounts |
| StatsD metrics from CloudWatch Agent never appear in CloudWatch | Application using StatsD library | CWAgent `statsd` listener not enabled in config; port 8125 blocked by security group | Check CWAgent config `statsd` section; `nc -u localhost 8125` from instance | Enable `statsd` in `amazon-cloudwatch-agent.json`; open SG port 8125 UDP inbound on localhost |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Metric cardinality explosion | Namespace metric count growing unboundedly; `ListMetrics` responses growing; PutMetricData costs rising | `aws cloudwatch list-metrics --namespace <ns> --query 'length(Metrics)'` trending up week-over-week | Weeks; billing impact before operational impact | Add dimension cardinality guard in publisher; use fixed-cardinality dimensions only |
| Dashboard widget count approaching limit (2500 per account) | Dashboard creation fails; `ResourceLimitExceededException` | `aws cloudwatch list-dashboards --query 'length(DashboardEntries)'` | Weeks; discovered when automation tries to create new dashboards | Audit and delete stale dashboards; consolidate per-service dashboards |
| Alarm count approaching limit (5000 composite + metric per account) | New alarm creation fails; `LimitExceededException` | `aws cloudwatch describe-alarms --query 'length(MetricAlarms)'` | Weeks; discovered during incident runbook automation | Delete unused alarms; request quota increase; move to composite alarms to reduce count |
| CloudWatch Agent memory leak on long-running EC2 | CWAgent process memory growing > 500 MB; instance OOM killer eventually kills agent | `ps aux \| grep amazon-cloudwatch-agent` on instance; check `/proc/<pid>/status` VmRSS | Weeks; eventually agent crash + metric gap | Upgrade CWAgent to latest version; restart CWAgent weekly via cron as interim fix |
| Log ingestion rate hitting account limits | `DataAlreadyAcceptedException` or ingestion delays in CloudWatch Logs; CWAgent log queue growing | `aws cloudwatch get-metric-statistics --namespace AWS/Logs --metric-name IncomingLogEvents` | Hours; log gaps before PutLogEvents throttled | Request Logs ingestion quota increase; filter low-value logs at source (CWAgent config `filters`) |
| Metric math expression complexity growing | Dashboard load time increasing; `GetMetricData` p99 latency climbing | Console: time widgets to render; `aws cloudwatch get-metric-data` response time | Weeks of incremental dashboard additions | Simplify metric math; replace complex expressions with pre-computed custom metrics via Lambda |
| CloudWatch Logs retention not set | Log storage cost growing unboundedly; old log groups retaining data indefinitely | `aws logs describe-log-groups --query 'logGroups[?retentionInDays==null].logGroupName'` | Months; billing impact only | Set retention on all log groups: `aws logs put-retention-policy --log-group-name <n> --retention-in-days 30` |
| Cross-account OAM link token expiration | Cross-account metrics and alarms stop updating; source accounts healthy | `aws oam list-links --query 'Items[*].{Source:SourceAccountId,Status:Status}'` | Days; silent until dashboard queries fail | Refresh OAM link; re-establish account association if link broken |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# CloudWatch Full Health Snapshot
REGION="${AWS_REGION:-us-east-1}"

echo "=== Alarm State Summary ==="
aws cloudwatch describe-alarms --region "$REGION" \
  --query '{Total:length(MetricAlarms), ALARM:length(MetricAlarms[?StateValue==`ALARM`]), INSUFFICIENT:length(MetricAlarms[?StateValue==`INSUFFICIENT_DATA`]), OK:length(MetricAlarms[?StateValue==`OK`])}' \
  --output table

echo ""
echo "=== Alarms Currently in ALARM State ==="
aws cloudwatch describe-alarms --region "$REGION" --state-value ALARM \
  --query 'MetricAlarms[*].{Name:AlarmName,Namespace:Namespace,Metric:MetricName,Reason:StateReason}' \
  --output table | head -40

echo ""
echo "=== PutMetricData Throttling (last 1 h) ==="
aws cloudwatch get-metric-statistics --region "$REGION" \
  --namespace AWS/CloudWatch --metric-name ThrottledRequests \
  --start-time "$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -v-1H '+%Y-%m-%dT%H:%M:%SZ')" \
  --end-time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
  --period 3600 --statistics Sum --output table

echo ""
echo "=== Log Groups Without Retention Policy ==="
aws logs describe-log-groups --region "$REGION" \
  --query 'logGroups[?retentionInDays==null].{LogGroup:logGroupName,StoredGB:storedBytes}' \
  --output table | head -20

echo ""
echo "=== Dashboard Count ==="
aws cloudwatch list-dashboards --region "$REGION" \
  --query 'length(DashboardEntries)' --output text
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# CloudWatch Performance Triage — metric gaps, alarm flap, query latency
REGION="${AWS_REGION:-us-east-1}"
NAMESPACE="${CW_NAMESPACE:-}"

echo "=== Alarms Flapping (State Changed > 3 Times in Last 24 h) ==="
aws cloudwatch describe-alarms --region "$REGION" \
  --query 'MetricAlarms[*].AlarmName' --output text | tr '\t' '\n' | while read -r alarm; do
    count=$(aws cloudwatch describe-alarm-history --region "$REGION" \
      --alarm-name "$alarm" \
      --start-date "$(date -u -d '24 hours ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -v-24H '+%Y-%m-%dT%H:%M:%SZ')" \
      --history-item-type StateUpdate \
      --query 'length(AlarmHistoryItems)' --output text 2>/dev/null)
    [[ "$count" -gt 3 ]] && echo "  FLAPPING ($count transitions): $alarm"
  done

if [[ -n "$NAMESPACE" ]]; then
  echo ""
  echo "=== Metric Count in Namespace $NAMESPACE ==="
  aws cloudwatch list-metrics --region "$REGION" --namespace "$NAMESPACE" \
    --query 'length(Metrics)' --output text

  echo ""
  echo "=== Dimensions Cardinality in $NAMESPACE ==="
  aws cloudwatch list-metrics --region "$REGION" --namespace "$NAMESPACE" \
    --query 'Metrics[*].Dimensions[*].Name' --output text \
    | tr '\t' '\n' | sort | uniq -c | sort -rn | head -10
fi

echo ""
echo "=== CloudWatch Agent Status on EC2 (SSM Run Command) ==="
echo "  (Run manually: aws ssm send-command --document-name AWS-RunShellScript --parameters commands='systemctl status amazon-cloudwatch-agent')"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# CloudWatch Connection & Resource Audit — IAM, VPC endpoints, metric streams
REGION="${AWS_REGION:-us-east-1}"

echo "=== VPC Endpoint for CloudWatch ==="
aws ec2 describe-vpc-endpoints --region "$REGION" \
  --filters "Name=service-name,Values=com.amazonaws.${REGION}.monitoring" \
  --query 'VpcEndpoints[*].{ID:VpcEndpointId,State:State,VpcId:VpcId}' --output table

echo ""
echo "=== Metric Streams ==="
aws cloudwatch list-metric-streams --region "$REGION" \
  --query 'Entries[*].{Name:Name,State:State,OutputFormat:OutputFormat,Destination:FirehoseArn}' --output table

echo ""
echo "=== OAM Links (Cross-Account Observability) ==="
aws oam list-links --region "$REGION" \
  --query 'Items[*].{Source:SourceAccountId,ResourceTypes:ResourceTypes,Arn:Arn}' --output table 2>/dev/null || echo "(OAM not configured)"

echo ""
echo "=== IAM Roles with CloudWatch PutMetricData ==="
aws iam list-roles --query 'Roles[*].RoleName' --output text | tr '\t' '\n' | while read -r role; do
  policies=$(aws iam list-attached-role-policies --role-name "$role" \
    --query 'AttachedPolicies[?contains(PolicyName,`CloudWatch`)].PolicyName' --output text 2>/dev/null)
  [[ -n "$policies" ]] && echo "  $role → $policies"
done | head -20

echo ""
echo "=== Anomaly Detector Models ==="
aws cloudwatch describe-anomaly-detectors --region "$REGION" \
  --query 'AnomalyDetectors[*].{Namespace:SingleMetricAnomalyDetector.Namespace,Metric:SingleMetricAnomalyDetector.MetricName,State:StateValue}' \
  --output table 2>/dev/null || echo "(No anomaly detectors)"
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| `PutMetricData` TPS throttling from high-cardinality publisher | All custom metric publishers in account see throttling; AWS service metrics unaffected | CloudWatch `ThrottledRequests` spike; CloudTrail shows single IAM role submitting bulk `PutMetricData` | Throttle or batch the offending publisher; stagger publish intervals | Enforce max 20 metrics per `PutMetricData` call; use high-resolution metric sparingly |
| `GetMetricData` API quota exhaustion from dashboard auto-refresh | All dashboards slow; `ThrottledRequests` for `GetMetricData` rising | CloudTrail: `GetMetricData` calls from multiple browser sessions; check dashboard refresh rate | Increase dashboard refresh interval to ≥ 60 s; disable auto-refresh for non-critical dashboards | Set per-widget refresh period ≥ 60 s; limit concurrent open dashboards per user |
| CloudWatch Logs ingestion flooding from a single noisy application | Other applications' log delivery delayed; `IncomingLogEvents` dominated by one log group | `aws logs describe-log-groups --query 'sort_by(@,&storedBytes)[-5:].logGroupName'` | Throttle log publisher; add `filter` in CWAgent config for noisy log source | Set per-log-group ingestion quotas via CWAgent `filters`; drop DEBUG logs below ERROR level in prod |
| Metric stream Kinesis Firehose delivery congestion | Metric stream consumers see delayed data; Firehose `DeliveryToS3.DataFreshness` > 5 min | Firehose `IncomingBytes` vs. `DeliveryToS3.Success` rate; check Firehose destination latency | Scale Firehose destination (S3 multipart upload rate); add Firehose parallelization factor | Use multiple metric streams per namespace instead of one stream for all namespaces |
| SNS topic fan-out delay during high-alarm-volume incident | Alert notifications delayed by minutes; multiple alarms firing simultaneously | SNS `NumberOfNotificationsFailed` metric; `PublishSize` vs. `NumberOfMessagesPublished` ratio | Deduplicate alarms into composite alarms to reduce SNS publish volume | Use composite alarms to aggregate related alarms into single notification; reduce alarm sensitivity |
| Anomaly detector model retraining contention | Multiple anomaly detectors entering training simultaneously; alarms flap to `INSUFFICIENT_DATA` | Check `describe-anomaly-detectors` for multiple models in `TRAINED_INSUFFICIENT_DATA` state | Stagger anomaly detector creation; do not create all detectors simultaneously | Spread anomaly detector creation over time; exclude retraining windows in alarm evaluation |
| CloudWatch Agent CPU spike on metrics-dense host | Instance workloads slow; `amazon-cloudwatch-agent` process at > 200% CPU | `top` on instance; CWAgent config with too many `measurement` entries or high-resolution (1s) metrics | Reduce `measurement` granularity; increase `metrics_collection_interval` from 10s to 60s | Audit CWAgent config; use 60s collection for non-latency-critical metrics; disable unnecessary `measurement` |
| Cross-account OAM sink query amplification | Monitoring account GetMetricData quota exhausted; account-level alarms degraded | `aws oam list-links` — many source accounts; monitoring account `ThrottledRequests` high | Limit source accounts linked to sink; use metric streams instead of pull for bulk data | Use CloudWatch metric streams (push) for cross-account high-volume metrics; use OAM pull only for ad-hoc queries |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| CloudWatch API regional degradation | Alarms stop evaluating → no auto-scaling triggers → capacity doesn't scale with demand → services degrade under load | All alarms in the region; all auto-scaling policies using CloudWatch alarms; all dashboards | AWS Health Dashboard event for CloudWatch; `aws cloudwatch describe-alarms --state-value ALARM` returns throttling; auto-scaling group stuck at current capacity | Switch to time-based or predictive auto-scaling temporarily; manually scale capacity; trigger PagerDuty alerts via direct API during CloudWatch outage |
| `PutMetricData` throttling → alarms stuck in INSUFFICIENT_DATA | Custom metric-based alarms have no data → alarms flip to `INSUFFICIENT_DATA` → if `treat_missing_data=alarm`, false alarms trigger → SNS floods | All custom metric alarms in account; downstream incident management pipelines flooded with false positives | `aws cloudwatch describe-alarms --query 'MetricAlarms[?StateValue==\`INSUFFICIENT_DATA\`].AlarmName'`; SNS delivery count spikes | Set `treat_missing_data=ignore` for known-intermittent metrics; rate-limit SNS destinations; silence known-false alarms |
| CloudWatch Logs agent crash on critical host | Application logs stop flowing → log-based alarms stop evaluating → anomalies go undetected → incident not paged | Observability blind spot for the affected host; all metric filters on that log group stop producing metrics | CWAgent process not running: `systemctl status amazon-cloudwatch-agent`; log group `IncomingLogEvents` drops to 0 | Restart CWAgent: `systemctl restart amazon-cloudwatch-agent`; alert on `CWAgentHeartbeat` metric gap > 5 min |
| Alarm in ALARM state triggering auto-scaling loop | Scale-out adds capacity → metric doesn't drop immediately → alarm stays in ALARM → another scale-out → over-provisioning → cost spike | Auto-scaling group; cost budget; downstream capacity bottlenecks (database, queues) | ASG `DesiredCapacity` growing unbounded; CloudWatch `AutoScalingGroupDesiredCapacity` metric rising continuously | Set ASG `MaxSize` to prevent runaway scale-out; disable the alarm temporarily while investigating; check alarm cooldown period |
| SNS topic used by alarm is deleted or unreachable | Alarms fire but no notifications delivered; on-call team not paged; incident goes unresponded | All alarms using the deleted SNS topic; PagerDuty/OpsGenie integrations relying on SNS | Alarm transitions to ALARM state but no page received; `aws sns list-topics` confirms topic is gone; CloudWatch console shows alarm in ALARM | Re-create SNS topic and update alarm actions: `aws cloudwatch put-metric-alarm --alarm-actions arn:aws:sns:$REGION:$ACCOUNT:new-topic` for all affected alarms |
| Dashboard widget queries overwhelming `GetMetricData` quota | Dashboard auto-refresh floods API → API throttled → other tooling (automated monitoring, Lambda alert enrichment) also throttled → incident response slowed | All `GetMetricData` consumers in account including programmatic monitoring | `aws cloudwatch get-metric-statistics --namespace AWS/CloudWatch --metric-name ThrottledRequests` rising; dashboards show `Loading...` indefinitely | Disable auto-refresh on non-essential dashboards; close unused dashboard tabs; reduce dashboard widget count |
| Metric math alarm referencing deleted metric | Alarm moves to `INSUFFICIENT_DATA` or evaluates incorrectly; dependent scaling or paging breaks | Single alarm and all resources dependent on it for scaling or incident response | `aws cloudwatch describe-alarms --alarm-names $ALARM_NAME --query 'MetricAlarms[].StateReason'` shows `No datapoints`; CloudTrail shows metric source was removed | Identify and fix or remove the metric math expression; create a replacement metric or update alarm expression |
| CloudWatch Synthetics canary failure during region degradation | Synthetic checks fail → alarm fires → paging begins → on-call investigates → but issue is canary infrastructure, not real user traffic | On-call team alerted for a false positive; real incidents may be masked by noise | Canary logs in S3 show `net::ERR_CONNECTION_REFUSED` from canary VPC, not user network; `aws synthetics get-canary-runs --name $CANARY_NAME` shows failures from canary subnet | Verify with real user RUM data before treating canary failure as user-impacting; have a secondary RUM-based alarm |
| EventBridge rule failure cascading from CloudWatch alarm → Lambda | Alarm fires → EventBridge rule triggers → Lambda function fails → events dead-lettered → downstream automation (e.g., auto-remediation) silently broken | Automated remediation pipeline; runbooks that depend on Lambda being triggered by alarms | EventBridge `FailedInvocations` metric rising; Lambda DLQ depth increasing; remediation not happening despite alarm | Check Lambda errors: `aws logs filter-log-events --log-group-name /aws/lambda/$FN --filter-pattern "ERROR"`; fix Lambda; re-process DLQ messages |
| Composite alarm component alarms all in INSUFFICIENT_DATA | Composite alarm evaluates to INSUFFICIENT_DATA → pages not sent even though underlying service may be down | All paging dependent on the composite alarm; potential missed incident | `aws cloudwatch describe-alarms --alarm-names $COMPOSITE_ALARM_NAME --query 'CompositeAlarms[].StateReason'`; component alarms all in INSUFFICIENT_DATA | Set each component alarm's `treat_missing_data` appropriately; composite alarm evaluates `INSUFFICIENT_DATA` as `OK` by default — verify intent; add heartbeat alarms |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Alarm threshold change | Previously acceptable metric values now trigger alarms; on-call flooded with false positives | Immediate on next alarm evaluation cycle (1–5 min) | CloudTrail: `PutMetricAlarm` event; `aws cloudwatch describe-alarm-history --alarm-name $ALARM_NAME --history-item-type ConfigurationUpdate` shows threshold change | Revert threshold: `aws cloudwatch put-metric-alarm --alarm-name $ALARM_NAME --threshold <previous_value>` with all other parameters unchanged |
| CWAgent config update adding high-resolution metrics | Instance CPU spikes; CWAgent process consuming > 50% CPU; increased CloudWatch cost | Within minutes of agent restart with new config | `/opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log` shows new metric collection; `top` confirms CWAgent CPU usage | Revert CWAgent config: `aws ssm send-command --document-name AmazonCloudWatch-ManageAgent --parameters action=configure,optionalConfigurationSource=ssm,optionalConfigurationLocation=$PREV_CONFIG`; restart agent |
| Log retention period reduction | Old log events deleted; historical debugging data lost; compliance audit fails | Effective immediately for logs outside new retention window | `aws logs describe-log-groups --query 'logGroups[*].{Name:logGroupName,Retention:retentionInDays}'`; compare with pre-change values in CloudTrail | Cannot recover deleted logs; increase retention period immediately: `aws logs put-retention-policy --log-group-name $LG --retention-in-days 90`; export to S3 before retention reductions |
| Metric namespace or dimension change in application | Existing alarms based on old namespace/dimensions get no data → INSUFFICIENT_DATA → missed incidents | Immediately after application deployment | `aws cloudwatch list-metrics --namespace OldNamespace` returns no recent datapoints; alarms flip to `INSUFFICIENT_DATA` | Update alarm metric definitions to new namespace/dimensions; deploy new alarms before removing old ones during migration |
| IAM policy change removing CloudWatch `PutMetricData` permission | Application stops publishing custom metrics; metric-based alarms go to INSUFFICIENT_DATA | Immediately after IAM change | `aws cloudwatch get-metric-statistics --namespace CustomApp/Metrics` returns no datapoints; CloudTrail shows `AccessDenied` for `cloudwatch:PutMetricData` from app role | Re-add `cloudwatch:PutMetricData` permission to the IAM role; verify with test metric: `aws cloudwatch put-metric-data --namespace Test --metric-name Test --value 1` |
| Dashboard moved between accounts or regions | Dashboard not found; users lose operational visibility | Immediate | CloudTrail shows `DeleteDashboard` event; users see `Dashboard not found` error | Re-create dashboard from saved JSON: `aws cloudwatch put-dashboard --dashboard-name $NAME --dashboard-body file://dashboard-backup.json` |
| SNS subscription filter policy added | Alarm notifications not reaching PagerDuty; on-call not paged for matching alarms | Immediate for new alarm notifications | SNS `NumberOfNotificationsFilteredOut-InvalidAttributes` metric rising; check subscription filter policy: `aws sns get-subscription-attributes --subscription-arn $ARN` | Remove or fix filter policy: `aws sns set-subscription-attributes --subscription-arn $ARN --attribute-name FilterPolicy --attribute-value '{}'` |
| EventBridge rule target changed | Alarm-triggered automation points to wrong Lambda or SQS queue; remediation broken | Immediate after EventBridge rule update | CloudTrail: `PutTargets` event; Lambda invocation count drops; SQS message count on wrong queue | Revert EventBridge target: `aws events put-targets --rule $RULE_NAME --targets Id=1,Arn=<correct-arn>` |
| Metric stream filter narrowed (removing namespaces) | Downstream analytics (S3, Redshift) stops receiving metrics for removed namespaces | Immediate after stream update | Firehose ingestion bytes drop; downstream reports show data gaps; `aws cloudwatch get-metric-stream --name $STREAM_NAME` shows updated `IncludeFilters` | Update metric stream to re-include missing namespaces: `aws cloudwatch put-metric-stream --name $STREAM_NAME --include-filters Namespace=AWS/EC2 --firehose-arn $ARN --role-arn $ROLE_ARN --output-format json` |
| CloudWatch Agent SSM parameter update with invalid JSON | CWAgent fails to restart; no metrics collected from affected hosts | On next agent restart or SSM parameter push | `amazon-cloudwatch-agent.log` shows `JSON configuration parse error`; `systemctl status amazon-cloudwatch-agent` shows failed | Validate config: `/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c ssm:/path/to/config`; fix SSM parameter JSON and re-push |
| Alarm evaluation period change (e.g., 1 min → 15 min) | Alarm now requires extended sustained threshold breach; fast-moving incidents not detected promptly | Next alarm evaluation | `aws cloudwatch describe-alarm-history --alarm-name $ALARM_NAME --history-item-type ConfigurationUpdate` shows EvaluationPeriods change; alarm delayed in responding | Revert evaluation period for latency-sensitive alarms; use shorter periods for P0 alarms |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Metric data gap from CWAgent restart | `aws cloudwatch get-metric-statistics --namespace CWAgent --metric-name mem_used_percent --start-time <restart-time> --end-time <now> --period 60 --statistics Average` | Gap in memory/disk metrics; alarms evaluate with missing data; INSUFFICIENT_DATA state | Missed capacity alarms; anomaly detector gaps cause model retraining | Restart CWAgent to resume collection; for critical gaps, backfill using `PutMetricData` from stored application logs |
| Conflicting alarms on same metric with different thresholds | `aws cloudwatch describe-alarms --alarm-name-prefix $PREFIX --query 'MetricAlarms[*].{Name:AlarmName,Threshold:Threshold,Actions:AlarmActions}'` | One alarm fires at a low threshold; another at a higher threshold for same metric; duplicate pages | Duplicate on-call pages; confusion about which alarm to acknowledge | Deduplicate: keep one alarm per metric/threshold; use composite alarms to combine related conditions |
| Cross-account metric data not appearing in monitoring account | `aws cloudwatch get-metric-data --region us-east-1` in monitoring account returns no data for source account metrics | OAM-linked source account metrics invisible in monitoring account dashboards and alarms | Observability gap; cross-account alarms not evaluating | Check OAM link status: `aws oam list-links`; verify source account OAM sink policy includes target namespace; re-create link if broken |
| CloudWatch Logs metric filter counting duplicates | `aws logs describe-metric-filters --log-group-name $LG` shows multiple filters extracting same pattern | Custom metric over-counts events; alarms trigger too aggressively | False positives; incorrect SLI calculations | Audit metric filters per log group; remove duplicates; verify filter pattern with `aws logs filter-log-events --log-group-name $LG --filter-pattern $PATTERN` |
| Alarm state divergence between AWS Console and API | Console shows ALARM; `aws cloudwatch describe-alarms` returns OK (or vice versa) | Confusion during incidents; inconsistent incident management tool state | Wrong on-call response; potential ignored true alarm | Refresh alarm state: `aws cloudwatch set-alarm-state --alarm-name $ALARM_NAME --state-value ALARM --state-reason "Manual re-evaluation"` to force re-evaluation; check for eventual consistency during high API load |
| Metric stream delivering duplicate data points | Firehose destination (S3/Redshift) receiving same metric multiple times; aggregated values doubled | `aws cloudwatch get-metric-stream --name $STREAM_NAME` shows stream healthy; downstream analytics shows 2x values | Incorrect dashboards and alerts based on downstream data; cost and usage overreported | Check Firehose delivery retries causing duplicates; implement idempotency in downstream consumer using metric timestamp as dedup key |
| Anomaly detector bands inconsistent after seasonal adjustment | `aws cloudwatch describe-anomaly-detectors --query 'AnomalyDetectors[].Configuration'` shows exclusion windows | Alarm based on anomaly detector flips state unexpectedly at certain times of day; on-call paged for expected traffic patterns | False positive pages; erosion of alarm trust | Update anomaly detector exclusion windows to match known traffic patterns; retrain by updating `Configuration.ExcludedTimeRanges` |
| Dashboards showing different time ranges across widgets | No API detection — visual inspection only; widgets show different `period` and `start`/`end` params | Operators see inconsistent snapshots during incidents; one widget shows current data, another shows 3 hours old | Delayed incident detection; incorrect root cause analysis during outage | Edit dashboard JSON to normalize all widget `period` values; set dashboard-level time range and ensure all widgets inherit it |
| Log insights query results inconsistent for same time range | `aws logs start-query --log-group-name $LG --start-time $T1 --end-time $T2 --query-string "fields @message"` returns different result counts on repeated runs | Different operators running identical queries see different counts; indexing not yet complete | Unreliable forensic analysis; incorrect incident impact estimates | Wait 5–10 min for log indexing to complete before running forensic queries; note that Log Insights queries near the live edge have incomplete indexes |
| Metric resolution mismatch between alarm and publisher | `aws cloudwatch list-metrics --namespace CustomApp --metric-name RequestCount` shows `storageResolution=1` but alarm configured for 60s period | Alarm evaluates on 60s aggregated metric but publisher writes at 1s resolution; alarm may miss short spikes | Spike-based alarms not firing when they should; `alarm_period` must be a multiple of storage resolution | Ensure alarm period matches metric storage resolution; for 1s metrics set alarm period to `10` or `30`; for standard metrics use `60` minimum |

## Runbook Decision Trees

### Decision Tree 1: Critical Alarm Not Firing Despite Known Issue

```
Is the critical alarm stuck in INSUFFICIENT_DATA or OK despite a real production issue?
├── YES → Is the alarm's metric being published? (check: aws cloudwatch get-metric-statistics --namespace <ns> --metric-name <metric> --start-time <1h ago> --end-time <now> --period 60 --statistics Average)
│         ├── NO DATA RETURNED → Root cause: metric not being published → Fix: check CWAgent status on source host (systemctl status amazon-cloudwatch-agent); check application metric publisher logs
│         └── DATA PRESENT     → Is the alarm threshold correct? (check: aws cloudwatch describe-alarms --alarm-names <alarm>)
│                                ├── Threshold too high → Root cause: misconfigured threshold → Fix: aws cloudwatch put-metric-alarm with corrected threshold
│                                └── Threshold correct → Is the evaluation period too long? (DatapointsToAlarm vs EvaluationPeriods)
│                                                        ├── YES → Root cause: alarm has too many required datapoints → Fix: reduce EvaluationPeriods or DatapointsToAlarm
│                                                        └── NO  → Is the alarm using an anomaly detector that's still training? (check: aws cloudwatch describe-anomaly-detectors)
│                                                                  ├── PENDING_TRAINING → Root cause: model not ready → Fix: temporarily switch to static threshold alarm; monitor anomaly detector training status
│                                                                  └── TRAINED → Check TreatMissingData setting: if 'missing' and data gaps exist, alarm won't trigger → Fix: set TreatMissingData to 'breaching'
└── NO  → Alarm is working; check SNS notification delivery: aws sns list-subscriptions-by-topic --topic-arn <arn>
```

### Decision Tree 2: `PutMetricData` Throttling Causing Metric Gaps

```
Are CloudWatch custom metrics showing gaps or are publishers receiving ThrottlingException?
├── YES → Is the throttling account-wide or from a single publisher? (check: aws cloudwatch get-metric-statistics --metric-name ThrottledRequests --namespace AWS/CloudWatch --period 300 --statistics Sum)
│         ├── ACCOUNT-WIDE THROTTLING → Is there a new high-frequency publisher? (check: CloudTrail for PutMetricData calls by IAM principal in last 1h)
│         │                             ├── YES → Root cause: new publisher flooding PutMetricData → Fix: throttle or stagger the new publisher; request quota increase via Service Quotas
│         │                             └── NO  → Root cause: legitimate aggregate usage at quota limit → Fix: switch to EMF (Embedded Metric Format) for high-frequency metrics; use metric streams for bulk export
│         └── SINGLE PUBLISHER        → Is the publisher sending individual metrics instead of batches? (check: PutMetricData call count in CloudTrail vs metric count)
│                                       ├── YES → Root cause: unbatched PutMetricData calls → Fix: batch up to 20 metrics per PutMetricData API call in publisher code
│                                       └── NO  → Root cause: publisher at per-principal TPS limit → Fix: distribute publishing across multiple IAM roles or add jitter to publish interval
└── NO  → Metric gaps are from missing data (source not publishing) — check source health, not CloudWatch
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| High-resolution custom metric overuse | Application publishing 1-second resolution metrics for every request/user | `aws cloudwatch list-metrics --namespace <ns> \| jq '.Metrics \| length'`; check per-metric cost in Cost Explorer | $0.30/metric/month × hundreds of high-cardinality metrics = significant monthly spend | Change metric resolution from 1s to 60s: update publisher to use `StorageResolution: 60` | Require SRE approval for high-resolution metrics; use 60s resolution by default |
| GetMetricData API quota exhaustion from dashboard proliferation | Many auto-refreshing dashboards × many widgets × short refresh intervals | CloudTrail: count `GetMetricData` calls per hour; `aws cloudwatch get-dashboard` to list all dashboards | `ThrottlingException` for all GetMetricData callers; dashboards fail to load | Set dashboard refresh intervals to ≥ 60s; close unused dashboard tabs | Enforce minimum 60s refresh on all dashboards; limit dashboard creation per team |
| Anomaly detector count runaway | Automation creating anomaly detectors for every new metric; detectors never cleaned up | `aws cloudwatch describe-anomaly-detectors \| jq '.AnomalyDetectors \| length'` (limit 1500/account) | Approaching account limit; new detectors fail to create; alarm evaluation errors | Delete unused detectors: `aws cloudwatch delete-anomaly-detector --namespace <ns> --metric-name <name>` | Require IaC management for anomaly detectors; enforce naming convention tied to active alarms only |
| Composite alarm fan-out creating excessive evaluation load | Deeply nested composite alarms with hundreds of constituent alarms evaluated at every state change | `aws cloudwatch describe-alarms --alarm-types CompositeAlarm \| jq '.CompositeAlarms \| length'`; check nesting depth | Alarm evaluation lag during state change storms; delayed notifications | Flatten composite alarm hierarchy; reduce constituent alarm count per composite | Limit composite alarm nesting to 2 levels; cap constituent alarms at 100 per composite |
| Log-derived metric filter on high-volume log group | Metric filter on a log group receiving GB/hour; each log event evaluated against all filters | `aws logs describe-metric-filters \| jq '.metricFilters \| length'`; correlate with `IncomingLogEvents` volume | CloudWatch Logs processing costs; metric filter evaluation adds ingestion cost | Move metric extraction to Logs Insights scheduled queries instead of real-time metric filters | Use metric filters only for low-to-medium volume log groups; prefer structured logging + Logs Insights for high volume |
| Alarm SNS fan-out with many subscriptions | Alarm firing to SNS topic with 100+ subscriptions (email, Lambda, HTTP endpoints) | `aws sns list-subscriptions-by-topic --topic-arn <arn> \| jq '.Subscriptions \| length'` | SNS delivery cost × alarm fire rate; downstream Lambda invocations | Consolidate to fewer subscriptions; use SNS message filtering to route to appropriate subscribers | Audit SNS subscriptions quarterly; use SNS filtering instead of separate topics per team |
| CloudWatch Agent collecting duplicate metrics | CWAgent config collecting metrics already published by AWS (e.g., EC2 CPU already in AWS/EC2 namespace) | Compare `aws cloudwatch list-metrics --namespace CWAgent` vs `aws cloudwatch list-metrics --namespace AWS/EC2` for same instance | Double billing for metrics already included in CloudWatch basic monitoring | Remove duplicate `measurement` entries from CWAgent config; restart agent | Audit CWAgent config against AWS default metrics; only collect CWAgent metrics not available natively |
| Metric stream sending all namespaces | Metric stream configured with no namespace filter; sending every metric in account to Firehose | `aws cloudwatch list-metric-streams \| jq '.Entries[] \| {Name, FirehoseArn, IncludeFilters}'` | Kinesis Firehose PUT charges + S3 storage for all metrics; very high for large accounts | Add `IncludeFilters` to limit stream to critical namespaces: `aws cloudwatch put-metric-stream --include-filters '[{"Namespace":"AWS/EC2"}]'` | Always configure `IncludeFilters` on metric streams; use separate streams per namespace if needed |
| CloudWatch dashboard widgets using math expressions on many metrics | Dashboard widget with `METRICS()` or `SEARCH()` function pulling all metrics matching a pattern | Open dashboard in browser with network DevTools; observe `GetMetricData` payload size | High GetMetricData API usage; potential throttling | Replace SEARCH() with explicit metric specification; reduce number of metrics per widget | Avoid `SEARCH()` expressions in dashboards for frequently-opened dashboards; pre-compute aggregations |

## Latency & Performance Degradation Patterns
| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot metric namespace causing GetMetricData throttling | Dashboard widgets timing out; `ThrottlingException` for GetMetricData API calls | `aws cloudwatch get-metric-statistics --namespace AWS/CloudWatch --metric-name ThrottledRequests --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) --period 60 --statistics Sum` | Too many concurrent GetMetricData calls from multiple dashboards + alarms hitting account-level TPS limit | Increase dashboard refresh interval to ≥ 60s; close unused dashboards; stagger alarm evaluation periods |
| Connection pool exhaustion to CloudWatch endpoint | Application PutMetricData calls failing with `RequestTimeout`; CloudWatch SDK not reusing connections | `aws cloudwatch get-metric-statistics --namespace AWS/CloudWatch --metric-name CallCount --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) --period 60 --statistics Sum` | CloudWatch SDK configured to create new HTTP connection per API call; connection establishment overhead | Configure AWS SDK `http.client` with connection pooling and keep-alive; use EMF (Embedded Metric Format) to batch metric publishing |
| GC / memory pressure in CWAgent on host | CWAgent process consuming > 200MB RAM on memory-constrained instance; CloudWatch metrics delayed | `ps aux \| grep cloudwatch-agent`; `cat /var/log/amazon/amazon-cloudwatch-agent/amazon-cloudwatch-agent.log \| grep -i "memory"` | CWAgent collecting high-frequency (1s) metrics from many sources; in-memory buffer growing | Increase CWAgent `force_flush_interval` to 60s; reduce collection interval; switch to 60s `measurement` resolution |
| Thread pool saturation in CWAgent metric collection | CWAgent log showing `collection_jitter`; metrics arriving with increasing delay | `sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a status` — check `status` field; `cat /var/log/amazon/amazon-cloudwatch-agent/amazon-cloudwatch-agent.log \| grep -i "plugin"` | CWAgent collecting metrics from too many plugins simultaneously; collection tasks queuing | Reduce number of CWAgent plugins; increase `collection_jitter` to spread collection load; upgrade instance type |
| Slow GetMetricData query due to SEARCH() expression | Dashboard widget with `SEARCH('{AWS/EC2} MetricName="CPUUtilization"')` scanning thousands of metrics | Open dashboard in browser DevTools → Network tab → find GetMetricData call → inspect `MetricDataQueries` payload | SEARCH() expression scanning entire metric namespace; scales linearly with metric count | Replace SEARCH() with explicit metric specifications; pre-compute aggregations in a scheduled Lambda |
| CPU steal on CWAgent host | CWAgent metric collection intervals drifting; metrics arriving late | `top -b -n1 \| grep "%Cpu" \| awk '{print $8}'` — check `%st` (steal percentage); `vmstat 1 5` | EC2 instance experiencing CPU steal from noisy neighbor | Move CWAgent to a dedicated monitoring instance; upgrade to larger instance type; use Graviton for better CPU isolation |
| Alarm evaluation lock contention | Composite alarm state changes triggering a cascade of child alarm re-evaluations; alarm state change lag > 60s | `aws cloudwatch describe-alarm-history --alarm-name $ALARM_NAME --history-item-type StateUpdate --start-date $(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%SZ)` | Deeply nested composite alarms creating evaluation fan-out; CloudWatch alarm evaluation thread pool saturated | Flatten composite alarm hierarchy; reduce number of constituent alarms per composite; use EventBridge for complex alarm routing |
| Metric filter serialization overhead on high-volume log groups | CloudWatch Logs `FilteredLogEventsCount` metric lagging behind `IncomingLogEvents` | `aws cloudwatch get-metric-statistics --namespace AWS/Logs --metric-name FilteredLogEventsCount --dimensions Name=LogGroupName,Value=$LG --period 60 --statistics Sum` | Too many metric filters on a single log group; each event evaluated against all filters in sequence | Consolidate multiple metric filters into fewer filters with `?ERROR ?WARN` pattern; move complex extraction to Logs Insights queries |
| Batch size misconfiguration in PutMetricData | Application publishing 1 metric per API call instead of batching up to 20; high API call volume | `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=PutMetricData \| jq '.Events \| length'` per minute | PutMetricData called with single `MetricData` entry per request; API call overhead exceeds metric submission throughput | Batch up to 20 `MetricData` entries per `PutMetricData` call in publisher code; use EMF for high-frequency metric publishing |
| Downstream dependency latency from SNS alarm notification | Alarm state change notifications delayed; SNS delivery taking > 60s due to slow HTTPS endpoint subscription | `aws cloudwatch get-metric-statistics --namespace AWS/SNS --metric-name PublishSize --dimensions Name=TopicName,Value=$TOPIC_NAME --period 60 --statistics Sum` | SNS HTTPS subscription endpoint slow to respond; SNS retrying with backoff | Move slow processing to Lambda (async); reduce SNS HTTPS endpoint response time to < 1s; use SQS as buffer between SNS and slow consumer |

## Network & TLS Failure Patterns
| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry for CWAgent endpoint | CWAgent log showing `x509: certificate has expired or is not yet valid`; metrics stop arriving | `cat /var/log/amazon/amazon-cloudwatch-agent/amazon-cloudwatch-agent.log \| grep -i "tls\|certificate\|x509"` | System clock skew causing TLS certificate validation to fail; or CA bundle outdated on host | Fix system time: `chronyc makestep`; update CA bundle: `update-ca-trust` (RHEL) or `update-ca-certificates` (Debian) |
| mTLS rotation failure for CloudWatch VPC endpoint | CWAgent or SDK calls failing after VPC endpoint certificate rotation | `aws ec2 describe-vpc-endpoint-services --service-names com.amazonaws.$REGION.monitoring` — check certificate status | VPC endpoint certificate rotated by AWS; CWAgent TLS validation cache needs refresh | Restart CWAgent: `sudo systemctl restart amazon-cloudwatch-agent`; reload AWS SDK credential cache |
| DNS resolution failure for CloudWatch endpoint | CWAgent metrics not arriving; CWAgent log showing `dial tcp: lookup monitoring.us-east-1.amazonaws.com: no such host` | `dig monitoring.$REGION.amazonaws.com`; `cat /etc/resolv.conf` | VPC DNS resolver not reachable; Route53 resolver rule not configured for Private Endpoint; DNS search domain misconfigured | Enable DNS resolution for VPC: `aws ec2 modify-vpc-attribute --vpc-id $VPC_ID --enable-dns-support`; verify Route53 resolver rules |
| TCP connection exhaustion from CWAgent | CWAgent failing to send metrics; `connection refused` or `too many open files` in CWAgent logs | `ss -tnp \| grep cloudwatch`; `lsof -p $(pgrep cloudwatch-agent) \| wc -l` | CWAgent opening new TCP connection per API call without connection reuse; file descriptor limit hit | Set `ulimit -n 65536` for CWAgent process; configure CWAgent with HTTP keep-alive; restart CWAgent to reset connections |
| Load balancer misconfiguration for CloudWatch VPC endpoint | VPC endpoint for CloudWatch returning `503 Service Unavailable` after VPC endpoint policy update | `aws ec2 describe-vpc-endpoints --filters Name=service-name,Values=com.amazonaws.$REGION.monitoring` | VPC endpoint policy restricting the CWAgent IAM role from calling CloudWatch APIs | Update VPC endpoint policy to allow `cloudwatch:PutMetricData` for the CWAgent IAM role |
| Packet loss causing PutMetricData timeouts | CWAgent retrying metric uploads; CloudWatch metric gaps despite healthy application | `ping -c 100 monitoring.$REGION.amazonaws.com` — check packet loss; `traceroute monitoring.$REGION.amazonaws.com` | Network instability between EC2 instance and CloudWatch endpoint; VPC gateway congestion | Increase CWAgent `force_flush_interval` to tolerate transient packet loss; use CloudWatch VPC endpoint to avoid public internet path |
| MTU mismatch between instance and CloudWatch VPC endpoint | Large CWAgent batches (many metrics) silently dropped; small batches succeed | `ping -M do -s 1400 monitoring.$REGION.amazonaws.com` | MTU mismatch on path to VPC endpoint causing large UDP/TCP frames to be dropped | Reduce CWAgent `metrics_collection_interval` batch size; set instance MTU: `ip link set eth0 mtu 1400`; use TCP for metrics transport |
| Security group blocking CWAgent outbound HTTPS | CWAgent metrics not arriving; no errors in CWAgent log (silent drop by firewall) | `aws ec2 describe-security-groups --group-ids $INSTANCE_SG \| jq '.SecurityGroups[].IpPermissions'` — check port 443 egress | Security group or NACL blocking outbound port 443 after a firewall change | Add outbound rule: `aws ec2 authorize-security-group-egress --group-id $SG_ID --protocol tcp --port 443 --cidr 0.0.0.0/0` |
| SSL handshake timeout for CWAgent on FIPS endpoint | CWAgent failing TLS handshake when configured to use FIPS endpoint on non-FIPS instance | `curl -v https://monitoring-fips.$REGION.amazonaws.com` — check TLS negotiation | CWAgent configured with `endpoint_override` pointing to FIPS endpoint; instance using OpenSSL without FIPS module | Remove FIPS endpoint override from CWAgent config unless instance is FIPS-validated; use standard monitoring endpoint |
| Connection reset during CloudWatch metric stream | Kinesis Firehose stream connection reset by CloudWatch during large metric burst | `aws cloudwatch get-metric-statistics --namespace AWS/Kinesis --metric-name GetRecords.IteratorAgeMilliseconds --dimensions Name=StreamName,Value=$STREAM --period 60 --statistics Average` | CloudWatch metric stream rate exceeding Kinesis Firehose shard throughput; back-pressure causing connection resets | Increase Kinesis Firehose shard count; add `IncludeFilters` to reduce metric stream volume; enable Kinesis on-demand mode |

## Resource Exhaustion Patterns
| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| CWAgent OOM kill | CWAgent process killed by OOM killer; metrics stop arriving; `dmesg \| grep -i oom-kill` shows cloudwatch-agent | `dmesg -T \| grep -i "oom\|killed process" \| tail -20`; `cat /proc/$(pgrep cloudwatch-agent)/status \| grep VmRSS` | CWAgent collecting high-frequency metrics from many sources; memory usage exceeds instance RAM | Restart CWAgent; reduce metric collection frequency; disable unused plugins in CWAgent config | Set `collection_jitter` and reduce `metrics_collection_interval`; add cgroup memory limit for CWAgent process |
| CloudWatch custom metric limit (10 metrics/namespace free tier) | `LimitExceededException` on `PutMetricData` calls; metrics silently dropped | `aws cloudwatch list-metrics --namespace $NAMESPACE \| jq '.Metrics \| length'`; compare to account limit | Account-level custom metric limit reached; common on accounts with many microservices each publishing metrics | Delete unused metrics by stopping publishers; request limit increase via AWS Service Quotas | Audit custom metric count monthly; use metric dimensions carefully to avoid cardinality explosion |
| Alarm limit exhaustion (5000 alarms/region/account) | `LimitExceededException` when creating new alarms via IaC | `aws cloudwatch describe-alarms \| jq '.MetricAlarms \| length'`; add `CompositeAlarms` count | Accumulated alarms from IaC deployments without cleanup; alarms for deleted resources still consuming quota | Delete stale alarms: `aws cloudwatch delete-alarms --alarm-names $(aws cloudwatch describe-alarms --query 'MetricAlarms[?StateValue==\`INSUFFICIENT_DATA\`].AlarmName' --output text)` | Enforce alarm cleanup in IaC destroy; use composite alarms to reduce total alarm count |
| Disk full on CWAgent log partition | CWAgent logging verbosely to `/var/log/amazon/amazon-cloudwatch-agent/`; disk fills | `df -h /var/log`; `du -sh /var/log/amazon/amazon-cloudwatch-agent/` | CWAgent log level set to DEBUG; log rotation not configured | Set log level to WARN in CWAgent config: `"log_level": "WARN"`; run `logrotate -f /etc/logrotate.d/amazon-cloudwatch-agent` | Configure CWAgent log rotation; set `"log_level": "INFO"` in production; monitor `/var/log` disk usage |
| File descriptor exhaustion from CWAgent plugin threads | CWAgent failing to open new metric collection sockets; `EMFILE: too many open files` | `lsof -p $(pgrep amazon-cloudwatch-agent) \| wc -l`; `cat /proc/sys/fs/file-nr` | CWAgent running too many plugins; each plugin holding open file descriptors for named pipes or sockets | `systemctl stop amazon-cloudwatch-agent && ulimit -n 65536 && systemctl start amazon-cloudwatch-agent` | Set `LimitNOFILE=65536` in CWAgent systemd service file; reduce number of CWAgent input plugins |
| CPU throttle on t-series burstable instance running CWAgent | CWAgent metric collection intervals drifting; CloudWatch gaps; CPU credit balance depleted | `aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name CPUCreditBalance --dimensions Name=InstanceId,Value=$INSTANCE_ID --period 300 --statistics Average` | CWAgent high-frequency collection exhausting CPU credits on burstable instance | Switch to standard (non-burstable) instance type for monitoring hosts; or reduce CWAgent collection frequency | Do not run CWAgent with 1-second collection intervals on t2/t3 instances; use `force_flush_interval=60` |
| Swap exhaustion from CWAgent memory leak | Host swap usage growing; CWAgent consuming increasing RAM over days | `free -h`; `cat /proc/$(pgrep amazon-cloudwatch-agent)/status \| grep VmSwap` | CWAgent memory leak (known issue in older versions); plugin holding references to collected metric data | Restart CWAgent; update CWAgent to latest version: `sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a stop && yum update amazon-cloudwatch-agent` | Pin CWAgent version in AMI; set up automatic CWAgent version update pipeline; monitor CWAgent process RSS memory |
| Kernel thread limit from CWAgent goroutine leak | Host `EAGAIN: fork/exec` errors; CWAgent spawning too many goroutines | `cat /proc/sys/kernel/threads-max`; `cat /proc/$(pgrep amazon-cloudwatch-agent)/status \| grep Threads` | CWAgent goroutine leak in procstat or custom metrics plugin | Restart CWAgent; disable the leaking plugin temporarily; upgrade CWAgent version | Monitor CWAgent thread count via `procstat` plugin; alert if CWAgent thread count > 500 |
| Kinesis Firehose shard throughput exhaustion from metric stream | CloudWatch metric stream delivery to Firehose failing; `WriteProvisionedThroughputExceeded` metric rising | `aws cloudwatch get-metric-statistics --namespace AWS/Kinesis --metric-name WriteProvisionedThroughputExceeded --dimensions Name=StreamName,Value=$STREAM --period 60 --statistics Sum` | CloudWatch publishing metrics faster than Kinesis Firehose can accept; 1MB/s per shard limit exceeded | Add Kinesis shards or switch to Firehose on-demand mode; add `IncludeFilters` on metric stream | Use Firehose on-demand capacity mode for variable metric volume; set namespace filters on CloudWatch metric streams |
| Ephemeral port exhaustion on monitoring Lambda | Lambda publishing custom metrics in a loop exhausting TCP ports; `cannot assign requested address` | `aws cloudwatch filter-log-events --log-group-name /aws/lambda/$LAMBDA --filter-pattern "cannot assign"` | Lambda function calling `PutMetricData` in a tight loop without connection reuse | Reuse AWS SDK client across Lambda invocations (declare outside handler); use EMF for metric publishing | Declare AWS SDK clients at module level in Lambda; use EMF logger which batches via structured log output |

## Distributed Transaction & Event Ordering Failures
| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation in custom metric publishing | Application publishing duplicate `PutMetricData` calls during retry storm; metrics double-counted in alarms | `aws cloudwatch get-metric-statistics --namespace $NAMESPACE --metric-name $METRIC --period 60 --statistics Sum` — compare to expected values | Alarms firing spuriously; dashboards showing inflated metric values | CloudWatch PutMetricData is inherently idempotent for the same timestamp+dimension combination; fix retry logic to avoid different timestamp on retry | Always use the same timestamp for retried `PutMetricData` calls; do not use `time.Now()` on retry |
| Alarm state machine partial failure during SNS delivery | Alarm transitions to `ALARM` state but SNS notification delivery fails; incident not paged | `aws cloudwatch describe-alarm-history --alarm-name $ALARM_NAME --history-item-type Action \| jq '.AlarmHistoryItems'` | On-call team not notified; SLO breach continues undetected | Manually send notification; verify SNS topic subscriptions: `aws sns list-subscriptions-by-topic --topic-arn $TOPIC_ARN`; check SNS delivery logs | Enable SNS delivery status logging; use redundant notification channels (SNS + PagerDuty + email) |
| Metric filter double-counting due to overlapping filter patterns | Two metric filters on same log group with overlapping patterns both incrementing the same metric | `aws logs describe-metric-filters --log-group-name $LG \| jq '.metricFilters[].filterPattern'` — check for overlap | Metric value is 2x actual event count; alarms fire at half the real threshold | Remove duplicate metric filter; consolidate into single filter with combined pattern | Enforce unique metric filters per metric name per log group in IaC; test filter patterns with `aws logs test-metric-filter` |
| Cross-account alarm delivery failure after IAM role rotation | Cross-account composite alarm dependencies broken after IAM role change | `aws cloudwatch describe-alarms --alarm-names $ALARM_NAME \| jq '.CompositeAlarms[].AlarmRule'` | Composite alarm stuck in `INSUFFICIENT_DATA`; cross-account alarm rule cannot evaluate constituent alarms | Re-establish cross-account alarm sharing: update resource policy on source account alarms to allow new IAM role ARN | Use CloudFormation StackSets for cross-account alarm configuration; avoid manual IAM role management |
| Out-of-order metric timestamp causing alarm evaluation error | Application publishing metrics with past timestamps outside CloudWatch's 2-week acceptance window | `aws cloudwatch put-metric-data --namespace $NS --metric-data '[{"MetricName":"test","Timestamp":"2020-01-01T00:00:00Z","Value":1,"Unit":"Count"}]'` — observe rejection | Metrics silently dropped; time-series gaps causing `INSUFFICIENT_DATA` alarm state | Fix application to publish metrics with current timestamp; add clock skew detection in publisher | Validate metric timestamp before publishing; reject metrics older than 1 hour in publisher code |
| At-least-once EventBridge alarm delivery duplicate | EventBridge rule triggered twice by the same CloudWatch alarm state change; two Lambda invocations | `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Invocations --dimensions Name=FunctionName,Value=$LAMBDA --period 60 --statistics Sum` — check for unexpected doubles | Duplicate incident creation in PagerDuty or ticket system | Implement idempotency in EventBridge target Lambda using alarm `StateChangeTime` as deduplication key | Store processed alarm state changes in DynamoDB with `StateChangeTime` as key; skip if already processed |
| Compensating action failure in auto-remediation Lambda | Auto-remediation Lambda triggered by CloudWatch alarm fails mid-remediation; partial rollback state | `aws cloudwatch filter-log-events --log-group-name /aws/lambda/$REMEDIATION_LAMBDA --filter-pattern "ERROR"` | System in partially remediated state; original issue may persist while some remediation was applied | Manually complete or reverse the partial remediation; update alarm to `OK` state if resolved: `aws cloudwatch set-alarm-state --alarm-name $ALARM_NAME --state-value OK --state-reason "manual"` | Implement idempotent remediation steps; use Step Functions for multi-step auto-remediation with compensation |
| Distributed lock expiry during CloudWatch Logs Insights query | Scheduled Insights query Lambda holding DynamoDB lock expires; second invocation starts same query | `aws logs describe-queries --status Running \| jq '[.queries[] \| select(.logGroupName == $LG)] \| length'` — check for duplicate running queries | Two identical Insights queries running simultaneously; double the scan cost; potential duplicate alerting | Stop duplicate query: `aws logs stop-query --query-id $DUPLICATE_QUERY_ID`; implement DynamoDB conditional lock with TTL | Use EventBridge Scheduler with `GroupName` idempotency token; implement lock with `expiresAt` TTL in DynamoDB before starting Insights queries |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor from GetMetricData burst | `aws cloudwatch get-metric-statistics --namespace AWS/CloudWatch --metric-name ThrottledRequests --period 60 --statistics Sum --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` — `ThrottledRequests` spike | Other teams' dashboards and alarms experiencing GetMetricData throttling | No per-IAM-user throttling; enforce at application layer | Stagger dashboard refresh intervals; reduce concurrent GetMetricData calls; implement metric caching layer in front of CloudWatch |
| Memory pressure from CWAgent collecting too many processes | `ps aux \| grep amazon-cloudwatch-agent \| awk '{print $6}'` — RSS growing; `cat /var/log/amazon/amazon-cloudwatch-agent/amazon-cloudwatch-agent.log \| grep -i memory` | CWAgent memory growth starving application processes on shared instance | No per-plugin memory limit in CWAgent; disable offending plugin | Reduce `procstat` plugin scope; disable high-memory CWAgent plugins; set cgroup memory limit for CWAgent process |
| Disk I/O saturation from CWAgent metric spooling | `iostat -x 1 5` — disk utilization high; `iotop` showing `amazon-cloudwatch-agent` as top I/O consumer | Application disk I/O starved; write latency elevated | `systemctl stop amazon-cloudwatch-agent` — temporarily stop CWAgent to confirm it is the I/O source | Move CWAgent spool directory to separate disk; reduce CWAgent collection frequency; switch to in-memory-only buffer |
| Network bandwidth monopoly from CWAgent metric upload | `iftop -i eth0` or `nethogs` — CWAgent consuming available bandwidth on low-bandwidth instance | Application network calls experiencing packet loss or delay | Throttle CWAgent HTTP uploads by setting lower `force_flush_interval` and `credentials` timeout | Move CWAgent to dedicated monitoring instance; use VPC endpoint to avoid shared NAT gateway bandwidth |
| Connection pool starvation from CloudWatch SDK | `aws cloudwatch get-metric-statistics --namespace AWS/CloudWatch --metric-name CallCount --period 60 --statistics Sum --start-time $(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` — burst in API calls | CloudWatch SDK creating too many connections; application thread pool starved | Enforce per-service CloudWatch API rate limit using token bucket in application middleware | Use connection pooling and batching in CloudWatch SDK; share a single AWS SDK client per process |
| Quota enforcement gap for custom metrics per namespace | `aws cloudwatch list-metrics --namespace $NAMESPACE \| jq '.Metrics \| length'` — metric count at account limit | Other teams unable to create new custom metrics; `LimitExceededException` on `PutMetricData` | Request quota increase: `aws service-quotas request-service-quota-increase --service-code monitoring --quota-code L-5E141235 --desired-value 10000` | Implement per-team metric namespace naming convention (`app/team/metric`); audit and delete unused metrics; enforce metric count budget per namespace |
| Cross-tenant alarm notification privacy leak | Composite alarm `AlarmRule` expression referencing another team's alarm ARN | Team A receiving alarm state notifications that include Team B's metric values | `aws cloudwatch describe-alarms --alarm-names $ALARM_NAME \| jq '.CompositeAlarms[].AlarmRule'` — audit cross-account references | Remove cross-team alarm dependencies; use SNS topic policies to restrict alarm subscriptions by IAM principal | Enforce alarm namespace and SNS topic ownership in IaC; separate CloudWatch accounts per business unit for strict isolation |
| Rate limit bypass for PutMetricData | Team publishing at 150 TPS exceeding per-account 150 TPS limit; other teams' metric publishes throttled | Legitimate metric publishers throttled; metric gaps; alarms entering `INSUFFICIENT_DATA` | `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=PutMetricData \| jq '.Events \| length'` — count calls per minute | Implement per-team PutMetricData rate limiting at application layer; switch to EMF (Embedded Metric Format) for high-frequency publishers |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure for CWAgent custom metrics | Custom namespace metrics missing from CloudWatch; dashboards show "No data" | CWAgent process crashed on source instance; IAM role missing `cloudwatch:PutMetricData` permission | `sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a status`; `tail -f /var/log/amazon/amazon-cloudwatch-agent/amazon-cloudwatch-agent.log` | Restart CWAgent; verify IAM role permissions: `aws iam simulate-principal-policy --policy-source-arn $ROLE_ARN --action-names cloudwatch:PutMetricData` |
| Trace sampling gap from X-Ray to CloudWatch ServiceMap | CloudWatch Application Signals showing missing service dependencies | X-Ray sampling rule not covering all Lambda/ECS services; sampling too low | `aws xray get-sampling-rules \| jq '.SamplingRuleRecords[] \| {RuleName, FixedRate, ReservoirSize}'` | Increase X-Ray sampling rate for critical service paths; add sampling rule targeting low-traffic services: `aws xray create-sampling-rule` |
| Log-derived metric filter producing zero values | CloudWatch alarm on metric filter output stuck in `INSUFFICIENT_DATA` despite matching log events | Metric filter namespace or metric name has a typo; or log group name changed after filter was created | `aws logs test-metric-filter --log-group-name $LG --filter-pattern "$PATTERN" --log-event-messages '[{"timestamp":1234,"message":"test ERROR event"}]'` — check `matches` output | Fix metric filter: `aws logs put-metric-filter --log-group-name $LG --filter-name $FILTER_NAME --filter-pattern "$PATTERN" --metric-transformations MetricName=$CORRECT_NAME,MetricNamespace=$CORRECT_NS,MetricValue=1` |
| Alert rule misconfiguration using wrong statistic | CloudWatch alarm uses `Average` instead of `Sum` for error count metric; alarm never triggers | `PutMetricData` publishes `Sum` but alarm evaluates `Average`; alarm threshold appears met but isn't | `aws cloudwatch describe-alarms --alarm-names $ALARM_NAME \| jq '.MetricAlarms[0] \| {Statistic, Threshold, ComparisonOperator}'` | Update alarm statistic to `Sum`: `aws cloudwatch put-metric-alarm --alarm-name $ALARM_NAME --statistic Sum ...`; validate with `aws cloudwatch set-alarm-state` |
| Cardinality explosion blinding CloudWatch dashboards | Dashboard API calls timing out; `GetMetricData` returning `ThrottlingException` | Application publishing metrics with unique `RequestId` or `UserId` dimension values | `aws cloudwatch list-metrics --namespace $NAMESPACE \| jq '.Metrics \| length'` — if > 10,000, cardinality explosion | Remove high-cardinality dimensions from `PutMetricData` calls; use CloudWatch Logs Insights for per-request analysis instead of metrics |
| Missing health endpoint for CWAgent service | CWAgent unresponsive but systemd reports it as `active (running)` | CWAgent process in zombie state; goroutine lock; not collecting metrics despite `active` status | `curl -s http://localhost:25180/` — CWAgent status endpoint; `kill -0 $(pgrep amazon-cloudwatch-agent)` | Add external health check script polling CWAgent status endpoint; restart CWAgent if endpoint unreachable: `systemctl restart amazon-cloudwatch-agent` |
| Instrumentation gap in high-resolution metric path | Sub-minute anomaly missed; alarm has 5-minute evaluation period | CloudWatch high-resolution (1s/5s/10s) metrics not configured; only standard 60s metrics published | `aws cloudwatch list-metrics --namespace $NAMESPACE \| jq '.Metrics[] \| select(.MetricName == "Latency")'` — check if high-resolution periods available | Publish high-resolution metrics: add `StorageResolution: 1` to `PutMetricData` call; update alarm to use 10s evaluation period |
| PagerDuty/SNS outage silencing CloudWatch alarm | Critical alarm firing but no page sent; incident not detected until customer complaint | SNS HTTPS subscription to PagerDuty returning 403 after PD API key rotation | `aws sns list-subscriptions-by-topic --topic-arn $TOPIC_ARN \| jq '.Subscriptions[] \| {Protocol, Endpoint, SubscriptionArn}'`; `aws cloudwatch describe-alarm-history --alarm-name $ALARM_NAME --history-item-type Action` | Update PagerDuty integration URL; add SMS or email subscription as backup; test notification: `aws sns publish --topic-arn $TOPIC_ARN --message "test"` |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| CWAgent version upgrade | New CWAgent version changes metric name format; dashboards show no data for renamed metrics | `sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a status \| grep version`; compare metric names: `aws cloudwatch list-metrics --namespace $NAMESPACE \| jq '.Metrics[].MetricName'` | Downgrade CWAgent: `yum downgrade amazon-cloudwatch-agent-$PREVIOUS_VERSION`; restart: `sudo systemctl restart amazon-cloudwatch-agent` | Test CWAgent upgrades in staging; compare metric names before and after upgrade; pin CWAgent version in AMI bake pipeline |
| Alarm threshold migration partial completion | Some alarms updated to new thresholds via IaC; others not updated due to Terraform state drift | `aws cloudwatch describe-alarms --alarm-name-prefix $PREFIX \| jq '.MetricAlarms[] \| {AlarmName, Threshold}'` — compare to IaC config | Revert IaC: `terraform plan -target=aws_cloudwatch_metric_alarm.$ALARM` then `terraform apply --auto-approve`; manually set threshold with `aws cloudwatch put-metric-alarm` | Use Terraform `for_each` for alarm sets; validate all alarm thresholds in CI with `terraform plan` before applying |
| Rolling CWAgent config update version skew | CWAgent config updated on some instances but not all; metric namespace mismatch across fleet | `aws ssm send-command --targets Key=tag:Role,Values=$ROLE --document-name AWS-RunShellScript --parameters commands=["cat /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \| jq .metrics.namespace"]` | Revert CWAgent config: push previous config via SSM: `aws ssm put-parameter --name /cloudwatch-agent/config --value file://previous-config.json --overwrite` | Use SSM Parameter Store for CWAgent config; deploy config via SSM Run Command to all instances atomically before enabling new metrics |
| Zero-downtime metric namespace migration gone wrong | Application migrating from `custom/MyApp` to `app/MyApp` namespace; alarms referencing old namespace stop receiving data | `aws cloudwatch get-metric-statistics --namespace custom/MyApp --metric-name $METRIC --period 60 --statistics Sum --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` — verify data still flowing | Publish to both old and new namespaces simultaneously during transition; update alarms to new namespace before removing old namespace publisher | Publish to both namespaces in parallel for 2 weeks; update alarms first; then remove old namespace publisher; never delete namespace before updating alarms |
| CloudWatch agent config format change breaking old instances | CWAgent 1.300034 config format not backward-compatible with older CWAgent on legacy AMIs; agent fails to start | `sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c ssm:/cloudwatch-agent/config 2>&1 \| grep -i error` | Revert to previous SSM config parameter version: `aws ssm get-parameter-history --name /cloudwatch-agent/config \| jq '.Parameters[-2]'`; push previous version | Validate CWAgent config against target CWAgent version before deploying; use `amazon-cloudwatch-agent-config-wizard` to generate version-compatible configs |
| Anomaly detector retraining disruption after metric name change | CloudWatch Anomaly Detector model trained on old metric name; after rename, anomaly band resets to default | `aws cloudwatch describe-anomaly-detectors --namespace $NAMESPACE --metric-name $NEW_METRIC_NAME` — check if model is newly created or has training history | Delete old anomaly detector: `aws cloudwatch delete-anomaly-detector --namespace $NS --metric-name $OLD_METRIC --stat AVERAGE`; create new one; accept 2-week training period | Rename metrics only after creating anomaly detector on new metric name with `ExcludedTimeRanges` to pre-train; overlap old and new metric publishing |
| Feature flag rollout enabling detailed monitoring | EC2 detailed monitoring enabled via feature flag; 1-minute metric granularity triggers high-frequency alarm evaluation; alarm noise storm | `aws ec2 describe-monitoring-attributes --instance-ids $INSTANCE_ID \| jq '.InstanceMonitorings[].Monitoring.State'` | Disable detailed monitoring: `aws ec2 unmonitor-instances --instance-ids $INSTANCE_ID`; update alarm `Period` to 300 seconds | Test alarm behavior with 60s period in staging before enabling detailed monitoring at scale; update alarm thresholds for 1-minute statistics |
| CloudWatch Logs export dependency version conflict | `aws logs create-export-task` using `--from` epoch ms format changed; automation script using old format breaks silently | `aws logs describe-export-tasks --status-code FAILED \| jq '.exportTasks[] \| {taskId, status, message}'` | Revert export script to use correct timestamp format; re-run failed export tasks: `aws logs create-export-task --log-group-name $LG --from $CORRECT_EPOCH_MS --to $END_EPOCH_MS --destination $BUCKET` | Validate timestamp format in export automation against AWS CLI changelog; add integration test for export task creation in CI |
| Alarm flapping causing SNS notification storm | Alarm oscillating between `ALARM` and `OK` within the evaluation period; hundreds of notifications per hour | `aws cloudwatch describe-alarm-history --alarm-name $ALARM_NAME --history-item-type StateUpdate \| jq '.AlarmHistoryItems \| length'` | SNS topic flooded; downstream Lambda throttled; on-call fatigue | Increase alarm `EvaluationPeriods` and `DatapointsToAlarm` to require sustained breach; add `treat_missing_data = notBreaching` | Set `EvaluationPeriods >= 3` with `DatapointsToAlarm = 2`; add alarm suppression window after state change |

## Kernel/OS & Host-Level Failure Patterns

| Failure | Symptom | Root Cause | Detection | Mitigation |
|---------|---------|------------|-----------|------------|
| OOM killer terminates CWAgent process | CloudWatch metrics stop arriving; `amazon-cloudwatch-agent` process disappears; `dmesg` shows OOM kill | CWAgent consuming excessive memory due to high-cardinality metric buffer; system under memory pressure | `dmesg -T \| grep -i "oom.*cloudwatch"`; `journalctl -u amazon-cloudwatch-agent --since "1 hour ago" \| grep -i "killed\|signal"` | Increase instance memory or reduce CWAgent metric buffer: set `force_flush_interval` to 5s in CWAgent config; add `MemoryLimit=512M` in systemd unit to contain blast radius |
| Inode exhaustion blocking CWAgent state files | CWAgent cannot write offset checkpoint files; logs stop being shipped; no error visible in CWAgent logs | `/opt/aws/amazon-cloudwatch-agent/logs/` and `/var/log/` consuming all inodes with rotated log fragments | `df -i /opt/aws/amazon-cloudwatch-agent/ \| awk 'NR==2{print $5}'`; `find /opt/aws/amazon-cloudwatch-agent/logs/ -type f \| wc -l` | Clean old CWAgent log files: `find /opt/aws/amazon-cloudwatch-agent/logs/ -name "*.gz" -mtime +7 -delete`; configure `logrotate` for CWAgent logs; alert on inode usage >80% |
| CPU steal causing CWAgent metric publication delays | CloudWatch metrics arrive with 2-5 minute delay; dashboards show stale data; alarms fire late | EC2 instance experiencing CPU steal >20%; CWAgent goroutines starved; `PutMetricData` calls delayed | `aws cloudwatch get-metric-statistics --namespace CWAgent --metric-name cpu_usage_steal --dimensions Name=InstanceId,Value=$INSTANCE_ID --period 60 --statistics Average --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` | Move to CPU-dedicated instance type (e.g., `c5.xlarge`); increase CWAgent `force_flush_interval`; alert on `cpu_usage_steal > 15` via CWAgent's own metric |
| NTP skew causing CloudWatch metric timestamp rejection | `PutMetricData` calls rejected with `InvalidParameterValue: Timestamp too far in the future`; metrics silently dropped | NTP daemon (`chrony`/`ntpd`) stopped or misconfigured; system clock drifted >15 minutes from UTC | `chronyc tracking \| grep "System time"`; `aws cloudwatch get-metric-statistics --namespace CWAgent --metric-name mem_used_percent --period 60 --statistics SampleCount --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` — check for gaps | Restart chrony: `systemctl restart chronyd`; verify: `chronyc sources -v`; add CWAgent config to emit `ntp_offset_seconds` metric; alert on offset >1s |
| File descriptor exhaustion blocking CWAgent log tailing | CWAgent stops tailing application logs; `tail_file` plugin emits `too many open files`; log-based metrics stop | CWAgent opens file descriptor per log file monitored; application generates thousands of log files | `ls /proc/$(pgrep amazon-cloudwatch)/fd \| wc -l`; `cat /proc/$(pgrep amazon-cloudwatch)/limits \| grep "Max open files"` | Increase CWAgent fd limit: add `LimitNOFILE=65536` to systemd unit; reduce monitored log paths; use glob patterns with fewer matches in CWAgent config |
| Conntrack table saturation blocking CWAgent API calls | CWAgent `PutMetricData` and `PutLogEvents` calls fail with timeout; metrics and logs both stop flowing | Host conntrack table full from application connections; CWAgent cannot establish new HTTPS connections to CloudWatch endpoint | `cat /proc/sys/net/netfilter/nf_conntrack_count` vs `cat /proc/sys/net/netfilter/nf_conntrack_max`; `dmesg \| grep conntrack` | Increase conntrack max: `sysctl -w net.netfilter.nf_conntrack_max=262144`; reduce application connection churn; add conntrack monitoring to CWAgent config |
| Kernel panic on EC2 host running CWAgent | CWAgent metrics stop abruptly for all metrics from one host; instance status check fails; CloudWatch shows no data gap alert because `treat_missing_data=missing` | Hardware fault or kernel bug causing panic; EC2 instance unreachable; CWAgent data stops without graceful shutdown | `aws ec2 describe-instance-status --instance-ids $INSTANCE_ID --include-all-instances \| jq '.InstanceStatuses[0].SystemStatus'`; `aws cloudwatch describe-alarms --state-value INSUFFICIENT_DATA \| jq '.MetricAlarms[] \| select(.Dimensions[0].Value == "'$INSTANCE_ID'")'` | Set `treat_missing_data=breaching` on critical host alarms; add EC2 status check alarm: `aws cloudwatch put-metric-alarm --alarm-name "$INSTANCE_ID-status" --namespace AWS/EC2 --metric-name StatusCheckFailed --dimensions Name=InstanceId,Value=$INSTANCE_ID --statistic Maximum --period 60 --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold --evaluation-periods 2` |
| NUMA imbalance causing CWAgent latency in metric collection | CWAgent metric collection cycle takes 30s+ instead of 10s on multi-socket instances; some metrics arrive late | CWAgent process pinned to NUMA node remote from network card; memory access latency high | `numactl --hardware`; `cat /proc/$(pgrep amazon-cloudwatch)/numa_maps \| grep "prefer\|bind"` | Pin CWAgent to NUMA node with NIC affinity: `numactl --cpunodebind=0 --membind=0 /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent`; or use `taskset` in systemd unit |

## Deployment Pipeline & GitOps Failure Patterns

| Failure | Symptom | Root Cause | Detection | Mitigation |
|---------|---------|------------|-----------|------------|
| CWAgent AMI bake — image pull failure for CWAgent package | New AMI build fails; instances launch without CWAgent; no metrics collected | S3 bucket hosting CWAgent RPM/DEB package unreachable during AMI bake; `yum install` times out | `aws ssm describe-instance-information --filters Key=PingStatus,Values=ConnectionLost \| jq '.InstanceInformationList[].InstanceId'`; verify CWAgent: `aws ssm send-command --instance-ids $ID --document-name AWS-RunShellScript --parameters commands=["systemctl status amazon-cloudwatch-agent"]` | Cache CWAgent package in private S3 bucket; validate AMI with post-bake test: `packer build -var test_cwagent=true`; add CWAgent health check to ASG health check grace period |
| SSM document auth failure during CWAgent config push | `AWS-ConfigureAWSPackage` SSM document fails with `AccessDeniedException`; CWAgent config not updated | IAM instance profile missing `ssm:GetParameter` permission after role policy update | `aws ssm list-command-invocations --instance-id $ID --status-filter Failed \| jq '.CommandInvocations[0].StatusDetails'` | Verify IAM permissions: `aws iam simulate-principal-policy --policy-source-arn $ROLE_ARN --action-names ssm:GetParameter --resource-arns "arn:aws:ssm:$REGION:$ACCT:parameter/cloudwatch-agent/*"` |
| Helm drift — CWAgent DaemonSet config diverges from Git | CWAgent DaemonSet in EKS cluster has manually edited ConfigMap; Git repo shows different config; helm diff shows drift | Operator `kubectl edit` on CWAgent ConfigMap without updating Helm chart values | `helm diff upgrade cwagent ./charts/cwagent -f values.yaml --namespace amazon-cloudwatch`; `kubectl get cm cwagent-config -n amazon-cloudwatch -o yaml \| diff - charts/cwagent/templates/configmap.yaml` | Enable ArgoCD self-heal: `spec.syncPolicy.automated.selfHeal: true`; use `kubectl annotate` to mark manual changes as temporary |
| ArgoCD sync stuck on CWAgent DaemonSet update | ArgoCD shows `OutOfSync` for CWAgent DaemonSet but sync never completes; pods not rolling | DaemonSet `updateStrategy.rollingUpdate.maxUnavailable` set to 1; PDB on nodes prevents eviction | `argocd app get cwagent --output json \| jq '.status.sync.status'`; `kubectl rollout status daemonset/cwagent -n amazon-cloudwatch --timeout=120s` | Increase `maxUnavailable` to 25%: `kubectl patch daemonset cwagent -n amazon-cloudwatch -p '{"spec":{"updateStrategy":{"rollingUpdate":{"maxUnavailable":"25%"}}}}'` |
| PDB blocking CWAgent pod eviction during node drain | Node drain hangs waiting for CWAgent pod; cluster upgrade stalls | PDB with `minAvailable=100%` on CWAgent DaemonSet; cannot evict any pod | `kubectl get pdb -n amazon-cloudwatch -o yaml \| grep -A5 cwagent` | Remove or relax PDB: `kubectl delete pdb cwagent-pdb -n amazon-cloudwatch`; DaemonSets should not have PDBs; rely on `maxUnavailable` in DaemonSet update strategy |
| Blue-green ASG cutover — new ASG instances missing CWAgent | New ASG launched for blue-green deployment; instances healthy but CWAgent not installed; monitoring gap | Launch template missing CWAgent user-data installation step; or SSM association not linked to new ASG | `aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $NEW_ASG \| jq '.AutoScalingGroups[0].LaunchTemplate'`; `aws ssm describe-instance-associations-status --instance-id $NEW_INSTANCE_ID` | Include CWAgent installation in launch template user-data; create SSM State Manager association targeting ASG tag; validate with post-launch health check |
| ConfigMap drift — CWAgent collecting wrong metrics after partial update | CWAgent config updated in SSM Parameter Store but only half of fleet picked up new config; mixed metric namespaces | SSM Run Command targeted by tag; some instances missing tag; partial rollout | `aws ssm list-command-invocations --command-id $CMD_ID \| jq '.CommandInvocations[] \| {InstanceId, Status}'` — check for `Failed` or missing instances | Use SSM State Manager with rate control: `aws ssm create-association --name AmazonCloudWatch-ManageAgent --targets Key=tag:Role,Values=$ROLE --apply-only-at-cron-expression "cron(0 2 ? * * *)"` |
| Feature flag enabling detailed CWAgent metrics causes throttling | Feature flag enables per-process metric collection; CWAgent publishes 10x more metrics; `PutMetricData` throttled | `procstat` plugin enabled for all processes; hundreds of metric streams per instance exceed CloudWatch API limits | `aws cloudwatch get-metric-statistics --namespace CWAgent --metric-name procstat_cpu_usage --period 60 --statistics SampleCount --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` — check sample count explosion | Limit `procstat` to specific process names: `"procstat": [{"pattern": "java\|python", "measurement": ["cpu_usage","memory_rss"]}]`; increase `force_flush_interval` to batch API calls |

## Service Mesh & API Gateway Edge Cases

| Failure | Symptom | Root Cause | Detection | Mitigation |
|---------|---------|------------|-----------|------------|
| Circuit breaker false positive — Envoy sidecar blocking CWAgent outbound | CWAgent `PutMetricData` calls fail; Envoy sidecar circuit breaker trips on CloudWatch endpoint; metrics stop | Envoy outlier detection marks CloudWatch regional endpoint as unhealthy after transient 503s | `aws ssm send-command --instance-ids $ID --document-name AWS-RunShellScript --parameters commands=["curl -s localhost:15000/clusters \| grep monitoring.*health_flags"]`; CWAgent logs: `journalctl -u amazon-cloudwatch-agent \| grep -i "connection refused\|503"` | Exclude CWAgent traffic from Envoy proxy: add `monitoring.*.amazonaws.com` to Envoy bypass list; or set `NO_PROXY=monitoring.*.amazonaws.com` in CWAgent systemd environment |
| Rate limiting on CloudWatch API — CWAgent throttled | `PutMetricData` returns `ThrottlingException`; CWAgent buffers fill; oldest metrics dropped | Multiple CWAgent instances in same account hitting CloudWatch API rate limit (150 TPS per account per region) | `aws cloudwatch get-metric-statistics --namespace AWS/Usage --metric-name CallCount --dimensions Name=Type,Value=API Name=Resource,Value=PutMetricData Name=Service,Value=CloudWatch --period 60 --statistics Sum --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ)` | Increase `force_flush_interval` to 30s; use EMF (Embedded Metric Format) via CloudWatch Logs to bypass `PutMetricData` limits; request CloudWatch API limit increase via AWS Support |
| Stale VPC endpoint for CloudWatch — CWAgent using outdated DNS | CWAgent resolves CloudWatch VPC endpoint to old IP; connection hangs; metrics delayed | VPC endpoint ENI replaced during maintenance; DNS cache on instance holds stale IP | `dig monitoring.$REGION.amazonaws.com`; `aws ec2 describe-vpc-endpoints --filters Name=service-name,Values=com.amazonaws.$REGION.monitoring \| jq '.VpcEndpoints[].DnsEntries'` | Flush DNS cache: `systemd-resolve --flush-caches`; reduce DNS TTL on VPC endpoint; restart CWAgent: `systemctl restart amazon-cloudwatch-agent` |
| mTLS rotation — CWAgent fails after instance profile credential rotation | CWAgent API calls return `ExpiredTokenException`; metrics stop flowing; CWAgent retries with stale credentials | Instance metadata service (IMDS) returning expired credentials; CWAgent credential cache not refreshed | `curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/$ROLE_NAME \| jq '.Expiration'`; `journalctl -u amazon-cloudwatch-agent \| grep -i "expired\|credential\|403"` | Restart CWAgent to force credential refresh: `systemctl restart amazon-cloudwatch-agent`; ensure IMDSv2 is enabled: `aws ec2 modify-instance-metadata-options --instance-id $ID --http-tokens required` |
| Retry storm — CWAgent retry backoff amplifying CloudWatch API pressure | CWAgent on 500 instances all retrying simultaneously after CloudWatch regional degradation; amplifies recovery time | CWAgent default retry uses fixed backoff without jitter; fleet synchronizes retry waves | `journalctl -u amazon-cloudwatch-agent --since "30 min ago" \| grep -c "retrying\|retry"`; `aws cloudwatch get-metric-statistics --namespace AWS/Usage --metric-name CallCount --dimensions Name=Type,Value=API Name=Resource,Value=PutMetricData Name=Service,Value=CloudWatch --period 60 --statistics Sum` | Add jitter to CWAgent `force_flush_interval` using offset per instance; stagger CWAgent restart times across fleet; implement client-side exponential backoff in custom metric publishers |
| gRPC OTLP receiver — CWAgent OTLP endpoint rejecting large payloads | Application sending OpenTelemetry metrics via gRPC to CWAgent OTLP receiver; large batches rejected with `RESOURCE_EXHAUSTED` | CWAgent OTLP receiver `max_recv_msg_size_mib` default too small for application metric batch | `journalctl -u amazon-cloudwatch-agent \| grep -i "RESOURCE_EXHAUSTED\|grpc\|otlp"`; CWAgent config: `cat /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \| jq '.metrics.metrics_collected.otlp'` | Increase max receive size in CWAgent config: `"otlp": {"grpc_endpoint": "0.0.0.0:4317", "max_recv_msg_size_mib": 16}`; reduce batch size in application OTLP exporter |
| Trace context loss — CWAgent X-Ray daemon dropping trace segments | X-Ray traces incomplete; spans from CWAgent-instrumented services missing parent context | CWAgent X-Ray daemon dropping segments when UDP buffer full; trace context not propagated to CloudWatch | `journalctl -u amazon-cloudwatch-agent \| grep -i "x-ray\|segment.*drop\|buffer"`; `aws xray get-trace-summaries --start-time $(date -u -d '1 hour ago' +%s) --end-time $(date -u +%s) \| jq '.TraceSummaries \| length'` | Increase CWAgent X-Ray buffer: set `xray.buffer_size_mb: 16` in CWAgent config; switch to OTLP traces instead of X-Ray UDP protocol for reliability |
| ALB health check — CWAgent status endpoint behind ALB not checked | CWAgent process crashed but ALB health check passes because it checks application port, not CWAgent status | ALB health check on port 80/443; CWAgent status endpoint on port 25180 not included | `curl -s http://localhost:25180/ \| jq '.status'`; `aws elbv2 describe-target-health --target-group-arn $TG_ARN \| jq '.TargetHealthDescriptions[].TargetHealth'` | Add composite health check script combining app + CWAgent status; register CWAgent health in application health endpoint; alert on CWAgent status endpoint failure |
