---
name: pulsar-agent
description: >
  Apache Pulsar specialist agent. Handles broker failures, BookKeeper issues,
  subscription backlogs, geo-replication lag, tiered storage problems,
  and multi-tenant messaging troubleshooting.
model: sonnet
color: "#188FFF"
skills:
  - pulsar/pulsar
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-pulsar-agent
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

You are the Pulsar Agent — the multi-tenant messaging expert. When any alert
involves Pulsar brokers, BookKeeper bookies, subscriptions, topic ownership,
or geo-replication, you are dispatched to diagnose and remediate.

# Activation Triggers

- Alert tags contain `pulsar`, `bookie`, `bookkeeper`, `subscription-backlog`
- Metrics from Pulsar Prometheus endpoint (`http://<broker>:8080/metrics`)
- Error messages contain Pulsar-specific terms (managed ledger, topic ownership, bookie)

# Prometheus Metrics Reference

Source: https://pulsar.apache.org/docs/next/reference-metrics

Metrics endpoint: `http://<broker>:8080/metrics`

**Note:** Metric granularity is controlled by broker config:
- `exposeTopicLevelMetricsInPrometheus=true` enables per-topic metrics
- `exposeConsumerLevelMetricsInPrometheus=true` enables per-consumer metrics
- `bookkeeperClientExposeStatsToPrometheus=true` enables BookKeeper client metrics

## Broker-Level Metrics

| Metric | Type | Description | Warning | Critical |
|--------|------|-------------|---------|----------|
| `pulsar_broker_rate_in` | Gauge | Total message ingestion rate (msg/s) for this broker | sudden drop > 50 % | = 0 with producers active |
| `pulsar_broker_rate_out` | Gauge | Total message dispatch rate (msg/s) from this broker | sudden drop > 50 % | = 0 with consumers active |
| `pulsar_broker_throughput_in` | Gauge | Total ingress throughput (bytes/s) | > 80 % of NIC capacity | > 95 % of NIC capacity |
| `pulsar_broker_throughput_out` | Gauge | Total egress throughput (bytes/s) | > 80 % of NIC capacity | > 95 % of NIC capacity |
| `pulsar_broker_storage_size` | Gauge | Total managed ledger storage size (bytes) | > 80 % of disk | > 90 % of disk |
| `pulsar_broker_msg_backlog` | Gauge | Total unacknowledged entries across broker | > 1 000 000 | > 10 000 000 |

## Subscription / Consumer Metrics (per topic+subscription)

| Metric | Type | Description | Warning | Critical |
|--------|------|-------------|---------|----------|
| `pulsar_subscription_back_log` | Gauge | Unacknowledged entries for a subscription | > backlog quota / 2 | > backlog quota (throttling) |
| `pulsar_subscription_delayed` | Gauge | Messages delayed for scheduled delivery | growing unbounded | — |
| `pulsar_subscription_msg_rate_redeliver` | Gauge | Message redelivery rate (nack/ack-timeout) | > 100/s | > 1 000/s (consumer crash loop) |
| `pulsar_subscription_blocked_on_unacked_messages` | Gauge | 1 = subscription blocked; 0 = clear | — | = 1 sustained |
| `pulsar_consumer_unacked_messages` | Gauge | Unacked messages held by a consumer | > max_unacked_messages × 0.8 | = max_unacked_messages |
| `pulsar_consumer_blocked_on_unacked_messages` | Gauge | 1 = consumer blocked by unacked limit | — | = 1 |

## Topic-Level Metrics

| Metric | Type | Description | Warning | Critical |
|--------|------|-------------|---------|----------|
| `pulsar_producers_count` | Gauge | Active producer connections for topic | drops to 0 unexpectedly | = 0 with producers expected |
| `pulsar_consumers_count` | Gauge | Active consumer connections for topic | drops to 0 on active sub | = 0 on active subscription |
| `pulsar_subscriptions_count` | Gauge | Active subscriptions on topic | — | = 0 |
| `pulsar_storage_backlog_age_seconds` | Gauge | Age of oldest unacknowledged message | > 1 hour | > retention policy |

## Storage / Latency Metrics

| Metric | Type | Description | Warning | Critical |
|--------|------|-------------|---------|----------|
| `pulsar_storage_write_latency_le_1` | Histogram bucket | Write ops completed in ≤ 1 ms | — | — |
| `pulsar_storage_write_latency_le_5` | Histogram bucket | Write ops completed in ≤ 5 ms | — | — |
| `pulsar_storage_write_latency_le_10` | Histogram bucket | Write ops completed in ≤ 10 ms | — | — |
| `pulsar_storage_write_latency_le_20` | Histogram bucket | Write ops completed in ≤ 20 ms | p99 > 20 ms | p99 > 100 ms |
| `pulsar_storage_write_latency_le_50` | Histogram bucket | Write ops completed in ≤ 50 ms | — | — |
| `pulsar_storage_write_latency_le_100` | Histogram bucket | Write ops completed in ≤ 100 ms | — | — |
| `pulsar_storage_write_latency_le_200` | Histogram bucket | Write ops completed in ≤ 200 ms | — | — |
| `pulsar_storage_write_latency_overflow` | Histogram bucket | Write ops taking > 200 ms | > 0 | > 1 % of all writes |
| `pulsar_storage_write_rate` | Gauge | Managed ledger write rate (batches/s) | — | — |
| `pulsar_storage_read_rate` | Gauge | Managed ledger read rate (batches/s) | — | — |
| `pulsar_storage_backlog_quota_exceeded_evictions_total` | Counter | Messages evicted because backlog quota exceeded | rate > 0 | — |

## BookKeeper Metrics (bookie)

| Metric | Type | Description | Warning | Critical |
|--------|------|-------------|---------|----------|
| `bookie_SERVER_STATUS` | Gauge | Bookie state: 1 = writable, 0 = read-only | — | = 0 |
| `bookkeeper_server_ADD_ENTRY_REQUEST` | Summary | Latency (ms) for ADD_ENTRY requests | p99 > 10 ms | p99 > 50 ms |
| `bookkeeper_server_READ_ENTRY_REQUEST` | Summary | Latency (ms) for READ_ENTRY requests | p99 > 20 ms | p99 > 100 ms |
| `bookie_WRITE_BYTES` | Counter | Total bytes written to bookie | — | — |
| `bookie_READ_BYTES` | Counter | Total bytes read from bookie | — | — |
| `bookie_journal_JOURNAL_SYNC_count` | Counter | Journal fsync operations | rate > 5 000/s | — |

## Connection / Auth Metrics

| Metric | Type | Description | Warning | Critical |
|--------|------|-------------|---------|----------|
| `pulsar_active_connections` | Gauge | Active client connections to broker | > 10 000 | > 50 000 |
| `pulsar_connection_create_fail_count` | Gauge | Failed connection attempts | rate > 10/min | rate > 100/min |
| `pulsar_authentication_failures_total` | Counter | Failed auth operations by reason | rate > 0 | rate > 100/min |
| `pulsar_broker_throttled_connections` | Gauge | Connections throttled due to rate limits | > 0 | > 100 |

# PromQL Alert Expressions

```promql
# Subscription backlog exceeds 1 million entries
pulsar_subscription_back_log > 1000000

# Subscription fully blocked on unacked messages
pulsar_subscription_blocked_on_unacked_messages == 1

# Storage write latency P99 > 100 ms (using histogram buckets)
# (fraction of writes NOT completed in ≤ 100 ms)
1 - (
  rate(pulsar_storage_write_latency_le_100[5m])
  / rate(pulsar_storage_write_rate[5m])
) > 0.01

# Bookie has gone read-only or unreachable
bookie_SERVER_STATUS == 0

# Zero consumers on a subscription
pulsar_consumers_count == 0

# Redelivery storm — consumer stuck in crash loop
pulsar_subscription_msg_rate_redeliver > 500

# Broker storage exceeds 85 % of allocated disk
pulsar_broker_storage_size / <disk_capacity_bytes> > 0.85

# Broker message rate dropped to zero (broker down or no producers)
rate(pulsar_broker_rate_in[5m]) == 0

# Backlog quota evictions occurring
rate(pulsar_storage_backlog_quota_exceeded_evictions_total[5m]) > 0

# Broker connection failures spiking
rate(pulsar_connection_create_fail_count[5m]) > 10

# Authentication failures spiking
rate(pulsar_authentication_failures_total[5m]) > 50
```

# Cluster Visibility

```bash
# Broker list and health
pulsar-admin brokers list <cluster-name>
pulsar-admin brokers healthcheck

# BookKeeper bookie status
pulsar-admin bookies list-bookies
pulsar-admin bookies racks-placement

# Topic backlog overview (per namespace)
pulsar-admin namespaces get-backlog-quotas <tenant>/<namespace>
pulsar-admin topics stats-internal <topic>

# Subscription backlog per topic
pulsar-admin topics subscriptions <topic>
pulsar-admin topics stats <topic> | python3 -c "
import sys, json
d = json.load(sys.stdin)
for sub, s in d.get('subscriptions', {}).items():
    print(sub, 'backlog:', s.get('msgBacklog', 0), 'consumers:', len(s.get('consumers', [])))
"

# Load balancing — which broker owns most topics
pulsar-admin broker-stats load-report | python3 -m json.tool | head -30

# ZooKeeper health
pulsar-admin zookeeper-shell stat /ledgers 2>/dev/null | head -5

# Prometheus metrics — key signals
curl -s http://<broker>:8080/metrics | grep -E \
  "pulsar_subscription_back_log|pulsar_broker_msg_backlog|bookie_SERVER_STATUS|pulsar_storage_write_latency_overflow"

# Web UI: Pulsar Manager at http://<host>:9527
```

# Global Diagnosis Protocol

**Step 1: Service health — is Pulsar up?**
```bash
pulsar-admin brokers healthcheck
pulsar-admin clusters list

# Is broker responding?
curl -s "http://<broker>:8080/admin/v2/brokers/health"
```
- CRITICAL: Health check fails; no brokers listed; BookKeeper has insufficient writable bookies
- WARNING: One bookie down; topic ownership rebalancing in progress; proxy connectivity degraded
- OK: All brokers healthy; all bookies writable; health endpoint returns `ok`

**Step 2: Critical metrics check**
```bash
# Subscription backlogs — Prometheus
curl -s http://<broker>:8080/metrics | grep pulsar_subscription_back_log | sort -t' ' -k2 -rn | head -10

# Subscription backlogs — pulsar-admin
pulsar-admin topics stats <topic> 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(k,'backlog:',v['msgBacklog']) for k,v in d.get('subscriptions',{}).items()]"

# Bookie writable status
curl -s http://<broker>:8080/metrics | grep bookie_SERVER_STATUS

# Storage write latency overflow bucket (writes > 200 ms)
curl -s http://<broker>:8080/metrics | grep pulsar_storage_write_latency_overflow
```
- CRITICAL: Backlog > quota limit (producers throttled); bookie count < write quorum; storage latency P99 > 1 s
- WARNING: Backlog growing > 10 K/min; 1 bookie down; storage latency P99 > 100 ms
- OK: Backlog stable; all bookies writable; storage P99 < 10 ms

**Step 3: Error/log scan**
```bash
grep -iE "ERROR|WARN.*ledger|BookKeeperException|ManagedLedger.*error|topic.*ownership" \
  /pulsar/logs/pulsar-broker-*.log | tail -30

# Bookie issues
grep -iE "ERROR|IOException|DiskFullException|not writable" \
  /pulsar/logs/bookkeeper*.log 2>/dev/null | tail -20
```
- CRITICAL: `BKNotEnoughBookiesException`; `ManagedLedgerFencedException`; OOM
- WARNING: `Slow bookie`; `ledger recovery` in progress; topic ownership transfer

**Step 4: Dependency health (ZooKeeper + BookKeeper)**
```bash
# ZooKeeper connectivity
echo "ruok" | nc <zk-host> 2181 && echo "ZK OK"
echo "stat" | nc <zk-host> 2181 | grep -E "Mode:|Connections:|Outstanding"

# BookKeeper ledger store health
pulsar-admin bookies list-bookies

# Ensemble availability check
pulsar-admin topics stats-internal <topic> | python3 -c "
import sys,json; d=json.load(sys.stdin)
for seg in d.get('ledgers', []):
    print('ledger:', seg.get('ledgerId'), 'entries:', seg.get('entries'), 'size:', seg.get('size'))
" | tail -5
```
- CRITICAL: ZooKeeper unreachable (all operations fail); BookKeeper has < write_quorum writable bookies
- WARNING: ZooKeeper leader re-election; 1 bookie returning read errors

# Focused Diagnostics

## 1. Subscription Backlog Surge

**Symptoms:** `pulsar_subscription_back_log` growing; `pulsar_subscription_msg_rate_redeliver` high; backlog quota alarm triggers producer throttling

**Diagnosis:**
```bash
# Backlog per subscription via Prometheus
curl -s http://<broker>:8080/metrics | grep pulsar_subscription_back_log | sort -t' ' -k2 -rn | head -15

# Detailed consumer analysis
pulsar-admin topics stats <topic> | python3 -c "
import sys,json
d = json.load(sys.stdin)
for sub, s in d.get('subscriptions', {}).items():
    print('Sub:', sub)
    print('  backlog:', s.get('msgBacklog'))
    print('  consumers:', len(s.get('consumers', [])))
    print('  type:', s.get('type'))
    print('  redeliver_rate:', s.get('msgRateRedeliver', 0))
    for c in s.get('consumers', []):
        print('  consumer:', c.get('consumerName'), 'msgRateOut:', c.get('msgRateOut'),
              'unacked:', c.get('unackedMessages'))
"

# Consumer blocked on unacked
curl -s http://<broker>:8080/metrics | grep pulsar_subscription_blocked_on_unacked_messages | grep -v " 0$"
```

**Thresholds:**
- `pulsar_subscription_back_log` > backlog quota → producers throttled → WARNING
- `pulsar_subscription_back_log` > 10 000 000 messages → CRITICAL; apply skip or scale
- `pulsar_consumers_count` = 0 → CRITICAL (no consumers draining the backlog)
- `pulsar_subscription_msg_rate_redeliver` > 1 000/s → consumer crash loop → CRITICAL

## 2. BookKeeper Bookie Failure

**Symptoms:** `bookie_SERVER_STATUS` = 0; `BKNotEnoughBookiesException` in broker logs; topic produces fail; ledger creation errors

**Diagnosis:**
```bash
# Prometheus: bookie status
curl -s http://<broker>:8080/metrics | grep bookie_SERVER_STATUS

# Which bookies are writable?
pulsar-admin bookies list-bookies
for bookie in $(pulsar-admin bookies list-bookies | awk '{print $1}'); do
  echo -n "$bookie: "
  curl -s "http://$bookie:8000/api/v1/bookie/is_ready" 2>/dev/null || echo "UNREACHABLE"
done

# BookKeeper write latency — is a bookie slow?
curl -s http://<bookie>:8000/metrics | grep bookkeeper_server_ADD_ENTRY_REQUEST

# Ledger placement — which bookies hold topic data
pulsar-admin topics stats-internal <topic> | python3 -c "
import sys,json; d=json.load(sys.stdin)
for l in d.get('ledgers',[]): print('ledger:', l.get('ledgerId'), 'ensembles:', l.get('metadata',{}).get('ensembles',{}))
"

# Bookie disk usage
ssh <bookie-host> "df -h /pulsar/data/bookkeeper/"
```

**Thresholds:**
- Writable bookies < ensemble size → CRITICAL; new topics/ledgers cannot be created
- `bookkeeper_server_ADD_ENTRY_REQUEST` p99 > 50 ms → WARNING; bookie under I/O pressure
- 1 bookie down with ensemble > 1 → WARNING (tolerable but no fault tolerance)

## 3. Topic Ownership Thrashing

**Symptoms:** Topics rapidly changing brokers; high load-balance churn in logs; topic stats not available; client reconnecting frequently

**Diagnosis:**
```bash
# Which broker owns a topic?
pulsar-admin topics lookup <topic>

# Broker load report (identify overloaded brokers)
pulsar-admin broker-stats load-report | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('bundles:', d.get('numBundles'), 'throughputIn:', d.get('msgThroughputIn'), 'cpu:', d.get('cpu', {}).get('usage','?'))
"

# Prometheus: topic count and rate per broker
curl -s http://<broker>:8080/metrics | grep -E "pulsar_producers_count|pulsar_consumers_count" | \
  awk '{sum+=$2} END {print "total producers+consumers:", sum}'
```

**Thresholds:** A broker with > 3× average topics = WARNING (needs rebalance); frequent bundle unloads in logs = CRITICAL thrashing

## 4. Geo-Replication Lag

**Symptoms:** `replicationBacklog` in topic stats growing; cross-cluster consumers stale; `connected=false` on replication link

**Diagnosis:**
```bash
# Replication status per topic
pulsar-admin topics stats <topic> | python3 -c "
import sys,json; d=json.load(sys.stdin)
for cluster, r in d.get('replication', {}).items():
    print(cluster, 'backlog:', r.get('replicationBacklog'), 'rate:', r.get('msgRateOut'), 'connected:', r.get('connected'))
"

# Prometheus: replication backlog (topic-level metric)
curl -s http://<broker>:8080/metrics | grep pulsar_replication_backlog | sort -t' ' -k2 -rn | head -10

# Namespace replication config
pulsar-admin namespaces get-clusters <tenant>/<namespace>

# Is the remote cluster reachable?
pulsar-admin clusters get <remote-cluster>
```

**Thresholds:**
- `replicationBacklog` > 0 with `connected=false` → CRITICAL; no data flowing to remote cluster
- `replicationBacklog` growing steadily (rate > 0) → WARNING; network or remote broker issue

## 5. Tiered Storage / Offload Stall

**Symptoms:** `offloadThreshold` reached but ledgers not offloading; cold data reads slow; storage errors in broker log

**Diagnosis:**
```bash
# Offload status for a topic
pulsar-admin topics offload-status <topic>

# Broker log for offload errors
grep -i "offload\|tiered\|s3\|gcs" /pulsar/logs/pulsar-broker-*.log | tail -20

# Check offload policy
pulsar-admin namespaces get-offload-policies <tenant>/<namespace>

# Prometheus: storage size growing (offload not clearing local storage)
curl -s http://<broker>:8080/metrics | grep pulsar_broker_storage_size
```

**Thresholds:** Offload stall > 1 hour with data exceeding threshold = WARNING; storage backend I/O errors = CRITICAL

## 6. BookKeeper Ledger Write Failure

**Symptoms:** `bookkeeper_server_ADD_ENTRY_failed` rate rising; broker logs show `BKNotEnoughBookiesException`; topic produce latency spiking; specific bookies timing out

**Root Cause Decision Tree:**
- If `bookie_SERVER_STATUS` = 0 on one bookie: → that bookie went read-only (disk full or I/O error); ensemble needs to change
- If `bookkeeper_server_ADD_ENTRY_REQUEST` p99 > 50 ms on specific bookie: → I/O bottleneck on that bookie; disk contention or GC pause
- If all bookies healthy but writes failing: → ensemble size exceeds available writable bookies; reduce ensemble or add bookie
- If failures started after broker restart: → ledger fencing; old broker still holds fence; wait for fence timeout or force recovery

**Diagnosis:**
```bash
# Failed ADD_ENTRY rate per bookie
curl -s "http://<bookie>:8000/metrics" | grep bookkeeper_server_ADD_ENTRY_failed
# Repeat for each bookie to identify the outlier

# Write latency per bookie (identify slow bookie)
for bookie in <bookie1> <bookie2> <bookie3>; do
  echo "=== $bookie ==="
  curl -s "http://$bookie:8000/metrics" | \
    grep "bookkeeper_server_ADD_ENTRY_REQUEST" | grep -E "0\.99|sum|count"
done

# Which bookies are writable vs read-only?
pulsar-admin bookies list-bookies
# readOnly: true = this bookie is not accepting writes

# Broker: failed ledger open attempts in logs
grep -E "BKNotEnoughBookies|LedgerFenced|AddEntryFailed" \
  /pulsar/logs/pulsar-broker-*.log | tail -20

# Check bookie disk
ssh <bookie-host> "df -h /pulsar/data/bookkeeper/ && iostat -x 1 3"

# Current ensemble for a topic's active ledger
pulsar-admin topics stats-internal <topic> | python3 -c "
import sys,json; d=json.load(sys.stdin)
ledgers = d.get('ledgers', [])
if ledgers:
    last = ledgers[-1]
    print('active ledger:', last.get('ledgerId'))
    print('ensemble:', last.get('metadata',{}).get('ensembles',{}))
"
```

**Thresholds:**
- `bookkeeper_server_ADD_ENTRY_failed` rate > 0 = WARNING; any write failures signal bookie issue
- `bookkeeper_server_ADD_ENTRY_REQUEST` p99 > 50 ms on any bookie = WARNING; > 200 ms = CRITICAL
- Writable bookies < ensemble size = CRITICAL; writes will block or fail

## 7. Broker Bundle Unloading Storm

**Symptoms:** Frequent topic ownership changes in logs; `broker_bundle_unload_rate` elevated; clients reconnecting repeatedly; load balancer CPU high; producer/consumer disconnects every few seconds

**Root Cause Decision Tree:**
- If all brokers are at similar load but unloads still happening: → load balancer threshold too sensitive; reduce `loadBalancerBrokerMaxTopics` or increase `loadBalancerResourceQuotaUpdateIntervalMinutes`
- If one broker repeatedly receives bundles and then sheds them: → bundle placement imbalance; that broker reports overload because it has a hot bundle
- If unloads spike after new topics are created: → topic distribution triggered auto-split and re-balance; normal if transient
- If unloads correlate with ZooKeeper latency spikes: → ZooKeeper sessions timing out during ownership transfer; cascading reconnects

**Diagnosis:**
```bash
# Bundle unload rate per broker (from Pulsar metrics)
curl -s "http://<broker>:8080/metrics" | grep -E "pulsar_broker_bundle_unload|broker_bundle"

# Load reports — which broker is triggering unloads?
pulsar-admin broker-stats load-report | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('bundles:', d.get('numBundles'))
print('throughput_in:', d.get('msgThroughputIn'))
print('throughput_out:', d.get('msgThroughputOut'))
print('underLoaded:', d.get('underLoaded'))
print('overLoaded:', d.get('overLoaded'))
"

# Check for hot bundle (one bundle > 30% of broker throughput)
for broker in <broker1> <broker2> <broker3>; do
  echo "=== $broker ==="
  curl -s "http://$broker:8080/admin/v2/broker-stats/bundles" | \
    python3 -c "
import sys,json
bundles=json.load(sys.stdin)
for ns,b in bundles.items():
    data=b.get('data',{})
    rate=data.get('msgRateIn',0)+data.get('msgRateOut',0)
    if rate > 1000:
        print(ns, 'msg_rate:', rate)
" 2>/dev/null
done

# Recent bundle unload events in broker logs
grep -E "unload.*bundle|BundleUnload|ownership.*transfer" \
  /pulsar/logs/pulsar-broker-*.log | tail -30
```

**Thresholds:**
- Bundle unload rate > 1/min sustained = WARNING; thrashing in progress
- Client disconnect rate spiking alongside bundle unloads = CRITICAL; service disruption

## 8. Topic Backlog Quota Exceeded (Producers Blocked)

**Symptoms:** Producers getting `ProducerBlockedQuotaExceededException`; `pulsar_storage_backlog_quota_exceeded_evictions_total` counter rising; specific topics in `backlog_exceeded` state; producer throughput drops to zero on affected topics

**Root Cause Decision Tree:**
- If `pulsar_subscription_back_log` is large but consumers are active: → consumers too slow; consumer throughput < producer rate
- If `pulsar_consumers_count` = 0 on the subscription: → no consumers draining the backlog; consumer process down or misconfigured subscription name
- If backlog quota is very small: → namespace quota set too low for expected throughput; increase quota
- If quota policy is `producer_exception` (not `producer_request_hold`): → producer receives error immediately rather than being held; check client error handling

**Diagnosis:**
```bash
# Topics exceeding backlog quota
curl -s "http://<broker>:8080/metrics" | \
  grep pulsar_storage_backlog_quota_exceeded_evictions_total | grep -v " 0$"

# Subscription backlog per topic
curl -s "http://<broker>:8080/metrics" | \
  grep pulsar_subscription_back_log | sort -t' ' -k2 -rn | head -10

# Current backlog quota for namespace
pulsar-admin namespaces get-backlog-quotas <tenant>/<namespace>

# Topic-level backlog details
pulsar-admin topics stats <topic> | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('storageSize:', d.get('storageSize'))
print('backlogSize:', d.get('backlogSize'))
for sub, s in d.get('subscriptions', {}).items():
    print(sub, 'backlog:', s.get('msgBacklog'), 'consumers:', len(s.get('consumers',[])),
          'blocked:', s.get('blockedSubscriptionOnUnackedMsgs'))
"

# Is the producer blocked?
pulsar-admin topics stats <topic> | python3 -c "
import sys,json; d=json.load(sys.stdin)
for pname, p in d.get('publishers', {}).items():
    print(pname, 'blocked:', p.get('blockedPublisher'), 'rate:', p.get('msgRateIn'))
"
```

**Thresholds:**
- `pulsar_subscription_back_log` > 50% of quota = WARNING
- `pulsar_subscription_back_log` > quota = CRITICAL; producers blocked
- `pulsar_storage_backlog_quota_exceeded_evictions_total` rate > 0 = CRITICAL; data being dropped (if policy is `consumer_backlog_eviction`)

## 9. Geo-Replication Lag (Extended)

**Symptoms:** `replicationBacklog` in topic stats growing between clusters; `pulsar_replication_backlog` metric non-zero; `connected=false` on replication link; cross-region consumers serving stale data

**Root Cause Decision Tree:**
- If `connected=false` for a remote cluster: → replication link down; check remote cluster URL and broker connectivity
- If `connected=true` but backlog growing: → replication rate throttled (`replicationRateLimitBytes`) or remote broker under pressure
- If backlog growing on specific topics only: → topic-level replication enabled but remote consumer not acking; check remote subscription
- If geo-replication was recently enabled: → initial catch-up; normal if backlog trending down over time

**Diagnosis:**
```bash
# Replication status per topic
pulsar-admin topics stats <topic> | python3 -c "
import sys,json; d=json.load(sys.stdin)
for cluster, r in d.get('replication', {}).items():
    print('cluster:', cluster)
    print('  backlog:', r.get('replicationBacklog'))
    print('  rate_out:', r.get('msgRateOut'))
    print('  connected:', r.get('connected'))
    print('  replicationDelayInSeconds:', r.get('replicationDelayInSeconds'))
"

# Prometheus: geo-replication backlog per cluster
curl -s "http://<broker>:8080/metrics" | \
  grep pulsar_replication_backlog | sort -t' ' -k2 -rn | head -10

# Is remote cluster reachable?
pulsar-admin clusters get <remote-cluster>
curl -s "http://<remote-broker>:8080/admin/v2/brokers/health"

# Replication rate limit configured?
pulsar-admin namespaces get-replicator-dispatch-rate <tenant>/<namespace>

# Check replication producer on remote cluster
pulsar-admin topics stats <topic> --get-precise-backlog | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('producers on remote:', d.get('publishers'))
" 2>/dev/null
```

**Thresholds:**
- `replicationBacklog` > 0 with `connected=false` = CRITICAL; no data flowing
- `replicationBacklog` growing at > 10 000 msg/min = WARNING; throttling or remote pressure
- `replicationDelayInSeconds` > 60 = WARNING; > 300 = CRITICAL

## 10. Schema Registry Conflict

**Symptoms:** Producers failing with `IncompatibleSchemaException`; schema version mismatch between producer and consumer; new deployment broke producers; `pulsar_authentication_failures_total` may be zero (auth is fine, schema is wrong)

**Root Cause Decision Tree:**
- If producer was updated and consumer was not: → schema evolved incompatibly; check evolution strategy (FULL vs BACKWARD vs FORWARD)
- If `schemaValidationEnforced=true` on namespace: → broker rejecting producers that don't match registered schema
- If schema was deleted and re-created with different definition: → version number reset but consumers have old fingerprint cached
- If multiple producer versions deployed simultaneously: → rolling deployment with incompatible schema change; pin to one version until cutover

**Diagnosis:**
```bash
# List schema versions for a topic
pulsar-admin schemas get <topic>

# Schema compatibility strategy for namespace
pulsar-admin namespaces get-schema-validation-enforce <tenant>/<namespace>
pulsar-admin schemas compatibility <tenant>/<namespace>

# All schema versions and their fingerprints
curl -s -u admin:admin \
  "http://<broker>:8080/admin/v2/schemas/<tenant>/<namespace>/<topic>/schema" | \
  python3 -c "
import sys,json; d=json.load(sys.stdin)
print('version:', d.get('version'))
print('type:', d.get('type'))
print('schemaData (truncated):', str(d.get('data',''))[:200])
"

# Check schema version history
curl -s "http://<broker>:8080/admin/v2/schemas/<tenant>/<namespace>/<topic>/versions" | \
  python3 -m json.tool

# Broker logs for schema rejection events
grep -iE "IncompatibleSchema|schema.*mismatch|schema.*incompatible" \
  /pulsar/logs/pulsar-broker-*.log | tail -20
```

**Thresholds:**
- Any `IncompatibleSchemaException` in producer = CRITICAL; producers cannot publish
- Schema registry unavailable (ZooKeeper node unreachable) = CRITICAL

## 11. Pulsar Function / IO Connector Failure

**Symptoms:** Function worker logs show `exception` rate; connector tasks not processing; `pulsar_functions_<function>_user_exceptions_total` rising; worker pod OOM or checkpoint failure; output topic receiving no messages

**Root Cause Decision Tree:**
- If `user_exceptions_total` growing: → function logic throwing exceptions; inspect function logs for stack trace
- If function stuck at same input sequence: → checkpoint failure; function cannot advance past a poison message
- If worker pod restarting (OOM): → function heap not sized correctly; `--ram 256m` too low for message volume
- If input topic has zero consumers from function: → function not running (worker down) or source subscription cursor lost

**Diagnosis:**
```bash
# Function status
pulsar-admin functions status --name <function-name> --namespace <tenant>/<namespace>
pulsar-admin functions stats --name <function-name> --namespace <tenant>/<namespace>

# Exception count and last exception
pulsar-admin functions stats --name <function-name> --namespace <tenant>/<namespace> | \
  python3 -c "
import sys,json; d=json.load(sys.stdin)
print('processedSuccessfully:', d.get('processedSuccessfully'))
print('userExceptions:', d.get('userExceptions'))
print('systemExceptions:', d.get('systemExceptions'))
for i in d.get('latestUserExceptions',[]):
    print('  exception:', i.get('exceptionString','')[:200])
    print('  at:', i.get('firstOccurrenceTime'))
"

# Function worker health
pulsar-admin functions-worker get-cluster
curl -s "http://<worker>:6750/admin/v2/worker/cluster" | python3 -m json.tool

# Checkpoint lag — is function stuck?
pulsar-admin topics stats <input-topic> | python3 -c "
import sys,json; d=json.load(sys.stdin)
for sub, s in d.get('subscriptions',{}).items():
    if '<function-name>' in sub:
        print('function sub:', sub, 'backlog:', s.get('msgBacklog'))
"

# Worker logs for OOM or checkpoint errors
grep -iE "OOM|checkpoint.*fail|exception.*function" \
  /pulsar/logs/functions-worker*.log 2>/dev/null | tail -20
```

**Thresholds:**
- `userExceptions` rate > 0 = WARNING; function logic errors
- `systemExceptions` rate > 0 = CRITICAL; infrastructure/worker failures
- Function backlog on input topic growing = WARNING; function not keeping up

## 12. Namespace Isolation Policy Conflict

**Symptoms:** Bundles violating isolation policy (placed on wrong broker group); `pulsar-admin namespaces get-isolation-policy` shows rules; forced bundle unload causing consumer disconnects; specific broker receiving traffic it shouldn't

**Root Cause Decision Tree:**
- If bundle is on a broker outside the primary group: → load balancer placed bundle before isolation policy was applied; needs forced unload
- If isolation policy recently changed: → existing bundles not migrated; they stay until next unload cycle
- If primary brokers all overloaded: → fallback brokers being used (expected by design); either add more primary brokers or loosen isolation
- If bundles keep returning to wrong brokers: → isolation policy not configured correctly; check `primary` and `secondary` broker regex patterns

**Diagnosis:**
```bash
# List namespace isolation policies for the cluster
pulsar-admin ns-isolation-policy list <cluster-name>
# or
pulsar-admin ns-isolation-policy get <cluster-name> <policy-name>

# Which broker currently owns a topic (check if in primary group)
pulsar-admin topics lookup <topic>

# Broker groups from isolation policy
pulsar-admin ns-isolation-policy list <cluster-name> | python3 -c "
import sys,json
policies=json.load(sys.stdin)
for name, p in policies.items():
    print('policy:', name)
    print('  namespaces:', p.get('namespaces'))
    print('  primary:', p.get('primary'))
    print('  secondary:', p.get('secondary'))
    print('  auto_failover_policy:', p.get('auto_failover_policy'))
"

# Prometheus: broker load distribution (identify policy violations)
for broker in <broker1> <broker2> <broker3>; do
  echo "=== $broker ==="
  curl -s "http://$broker:8080/metrics" | grep pulsar_producers_count | \
    awk '{sum+=$2} END {print "total_producers:", sum}'
done

# Check broker tags/labels (used by isolation policy)
pulsar-admin brokers list <cluster> | xargs -I{} pulsar-admin brokers get-all-dynamic-config {}
```

**Thresholds:**
- Any bundle on broker outside isolation primary group = WARNING (policy violation)
- All primary brokers overloaded and falling back to secondary = WARNING; capacity issue

## 13. Broker Overload Causing Topic Migration Storm

**Symptoms:** `pulsar_broker_rate_in` and `pulsar_broker_rate_out` fluctuating across brokers; `pulsar_producers_count` and `pulsar_consumers_count` oscillating; client logs show repeated `TopicDoesNotExistException` or `BrokerAssignmentException`; producers and consumers disconnect and reconnect repeatedly; overall throughput drops 30–70% during the storm; load balancer metrics show `LoadBalancerBrokerUnderloadedCount` and `OverloadedCount` both positive simultaneously

**Cascade Chain:**
1. One broker becomes overloaded (CPU > `loadBalancerBrokerOverloadedThresholdPercentage`, default 85%)
2. Load balancer decides to shed bundles from overloaded broker → initiates bundle unload
3. Bundle unload causes all producers and consumers on that bundle to disconnect
4. Clients reconnect → broker lookup causes bundles to be assigned to other brokers
5. Those brokers receive an influx of reconnecting clients → temporarily overloaded during connection storm
6. Load balancer detects new overloaded brokers → sheds more bundles → more client reconnects
7. Oscillating state: brokers cycle between overloaded and underloaded as bundles migrate continuously
8. High reconnect traffic itself consumes broker CPU → threshold re-triggered → loop continues

**Root Cause Decision Tree:**
- If load balancer shedding rate is high: `loadBalancerBrokerUnderloadedThresholdPercentage` and overloaded threshold are too close together → add hysteresis
- If all brokers are near the threshold: genuine capacity issue — add brokers
- If migration storm started after a single broker failure: broker failure caused remaining brokers to absorb redistributed bundles → cascading overload
- If `loadBalancerAutoBundleSplitEnabled=true` and topics have large fan-out: bundle splits increase total bundle count → more migration events

**Diagnosis:**
```bash
# Broker load distribution
pulsar-admin brokers list <cluster>
for broker in $(pulsar-admin brokers list <cluster>); do
  echo "=== $broker ==="
  pulsar-admin --admin-url "http://$broker:8080" broker-stats load-report
done

# Bundle unload events in broker log
grep -E "Unloading|bundle.*overload|LoadShedding" \
  /var/log/pulsar/broker.log | tail -50

# Current bundle count per broker
pulsar-admin brokers get-internal-config | grep -i bundle

# Prometheus: broker CPU and rate metrics
# pulsar_broker_rate_in{broker="..."} — watch for oscillation
# rate(pulsar_broker_rate_in[1m]) — should be stable, not oscillating

# Client reconnect rate (high = migration storm)
# pulsar_producer_connections and pulsar_consumer_connections rate
```

**Thresholds:**
- Broker CPU oscillating ± 20% within 5 min = 🟡 load balancer instability
- Bundle unload rate > 5/min per broker = 🔴
- Client reconnect rate > 100/min = 🔴 (migration storm)

## 14. BookKeeper Ledger Fragment Recovery Taking Too Long

**Symptoms:** After a bookie failure or restart, `pulsar_storage_write_latency_overflow` increases; broker logs show `LedgerFragment recovery in progress`; write latency p99 climbs; some topics become read-only during recovery; `pulsar_bookie_journal_write_latency` elevated on recovery bookies; recovery takes hours for large ledgers with many fragments; broker may mark topics as fenced until recovery completes

**Root Cause Decision Tree:**
- If bookie was down for a long time: more ledger fragments need recovery → recovery takes longer proportional to fragment count and size
- If `autoRecoveryDaemonEnabled=false` on bookies: automatic recovery is disabled; requires manual `bookkeeper auto-recovery` trigger
- If `rereplicationEntryBatchSize` is small: recovery is throttled by small batch sizes → slow throughput
- If recovery parallelism (`numRecoveryWorkers`) is low vs. number of fragments: recovery serialized unnecessarily
- If the replaced bookie has insufficient disk I/O: recovery writes are throttled by disk performance

**Diagnosis:**
```bash
# Check auto-recovery status
bookkeeper auto-recovery status

# List under-replicated ledger fragments
bookkeeper shell listunderreplicated | head -50

# Under-replicated fragment count (should be 0 in steady state)
bookkeeper shell listunderreplicated | wc -l

# Recovery progress via BookKeeper admin
bookkeeper shell bookiesanity

# Bookie journal and ledger write latency
# pulsar_bookie_journal_write_latency{bookie="..."}
# pulsar_storage_write_latency_overflow — indicates slow writes during recovery

# Check which topics are affected (fenced or read-only)
pulsar-admin topics list <tenant>/<namespace> | while read topic; do
  status=$(pulsar-admin topics stats "$topic" 2>/dev/null | \
    python3 -c "import sys,json; s=json.load(sys.stdin); print(s.get('state','unknown'))" 2>/dev/null)
  [ "$status" != "unknown" ] && echo "$topic: $status"
done

# Broker log for recovery events
grep -E "LedgerFragment|underReplicated|recovery" \
  /var/log/pulsar/broker.log | tail -30
```

**Thresholds:**
- Under-replicated fragment count > 0 = 🟡; growing > 1 000 = 🔴
- Recovery duration > 1 hour = 🟡; > 4 hours = 🔴
- `pulsar_storage_write_latency_overflow` > 0.1% of writes = 🟡

## 15. Schema Compatibility Check Breaking Producer After Schema Evolution

**Symptoms:** Producer starts failing with `IncompatibleSchemaException` or `SchemaSerializationException` after a schema change deployment; existing consumers continue working; new producer version cannot publish; rolling deployment is blocked; `pulsar-admin schemas` shows version conflict; if `BACKWARD` compatibility is set, new schema must be readable by old consumers — but producer has added a field that old consumers cannot handle

**Root Cause Decision Tree:**
- If `schemaCompatibilityStrategy=BACKWARD` and new schema added a required field: old consumers cannot read new messages (missing field) → incompatible
- If `schemaCompatibilityStrategy=FORWARD` and new schema removed a field: old producers cannot produce messages readable by new consumers → incompatible
- If `schemaCompatibilityStrategy=FULL` (most restrictive): both must hold — only safe changes are adding optional fields with defaults
- If `schemaCompatibilityStrategy=ALWAYS_INCOMPATIBLE`: every schema change is rejected → cannot evolve schema at all
- If `schemaValidationEnforced=false` on the namespace: compatibility check bypassed → silent deserialization errors at consumer

**Schema Compatibility Matrix:**
- `BACKWARD`: new schema can read data written by old schema → remove fields, add optional fields with defaults
- `FORWARD`: old schema can read data written by new schema → add fields, remove optional fields
- `FULL`: both BACKWARD and FORWARD — only add/remove optional fields with defaults
- `BACKWARD_TRANSITIVE` / `FORWARD_TRANSITIVE`: checked against ALL previous versions, not just latest

**Diagnosis:**
```bash
# Check current schema and compatibility strategy for a topic
pulsar-admin schemas get persistent://<tenant>/<namespace>/<topic>
pulsar-admin namespaces get-schema-compatibility-strategy <tenant>/<namespace>

# Get schema metadata (versions, etc.)
pulsar-admin schemas metadata persistent://<tenant>/<namespace>/<topic>

# Check namespace-level schema enforcement settings
pulsar-admin namespaces get-schema-autoupdate-strategy <tenant>/<namespace>
pulsar-admin namespaces get-schema-validation-enforce <tenant>/<namespace>

# Try schema compatibility check before pushing new schema
pulsar-admin schemas compatibility \
  --filename <new-schema.json> \
  persistent://<tenant>/<namespace>/<topic>

# Producer error in application logs
# Look for: org.apache.pulsar.client.api.SchemaSerializationException
# or:        IncompatibleSchemaException
```

**Thresholds:**
- Any `IncompatibleSchemaException` on producer = 🔴 (producers unable to publish)
- Schema version mismatch between producer and consumer = 🟡

## 16. Geo-Replication Cursor Falling Behind During Region Unavailability

**Symptoms:** `pulsar_replication_backlog` growing on the source cluster for a remote cluster; replication rate drops to 0 for affected destination; `pulsar_replication_rate_out` = 0 for the unavailable region; after region recovers, replication backlog takes hours to drain; consumers in the recovered region are missing messages that were published during the outage window; cursor age increases continuously

**Cascade Chain:**
1. Remote region becomes unavailable (network, datacenter, or cluster failure)
2. Replication cursor for that region stops advancing — the source broker holds the cursor position at the last acknowledged message to the remote region
3. Messages published to the source cluster during the outage accumulate in the backlog (cannot be deleted — cursor has not advanced past them)
4. If backlog quota is set and exceeded: producers may be blocked or messages dropped (`ProducerBlockedQuotaExceeded`)
5. Region recovers → replication resumes from cursor position → must replay all backlogged messages
6. Replay creates a burst of replication traffic → remote region may re-enter overload
7. Cursors for other subscriptions are unaffected — only replication cursors for the down region lag

**Root Cause Decision Tree:**
- If `pulsar_replication_backlog > 0` for a specific remote cluster: that cluster is lagging or unreachable
- If replication backlog grows faster than the remote cluster can consume: remote cluster throughput is insufficient for catch-up
- If backlog quota is configured and exceeded: check `backlogQuotaExceededPolicy` — `producer_exception` vs `consumer_backlog_eviction`
- If cursor recovery is slow: remote cluster disk write throughput limiting → may need to throttle replication replay rate

**Diagnosis:**
```bash
# Replication backlog per topic per remote cluster
pulsar-admin topics stats persistent://<tenant>/<namespace>/<topic> | \
  python3 -c "
import sys, json
stats = json.load(sys.stdin)
for cluster, rep in stats.get('replication', {}).items():
    print('cluster:', cluster,
          'inboundRate:', rep.get('inboundMsgRate', 0),
          'outboundRate:', rep.get('outboundMsgRate', 0),
          'replicationBacklog:', rep.get('replicationBacklog', 0))
"

# Check replication status across all topics
pulsar-admin namespaces get-clusters <tenant>/<namespace>

# Current backlog quota settings
pulsar-admin namespaces get-backlog-quotas <tenant>/<namespace>

# Replication connection status
pulsar-admin clusters get <remote-cluster>

# Prometheus: replication backlog across all topics
# sum(pulsar_replication_backlog{cluster="<remote>"}) by (topic)
```

**Thresholds:**
- `pulsar_replication_backlog > 1 000 000` messages = 🟡; > 10 000 000 = 🔴
- Replication rate = 0 with active producers = 🔴 (replication link down)
- Backlog age > retention period = 🔴 (data may be deleted before replicated)

## 17. Topic Compaction Not Running, Causing Key Retention Beyond TTL

**Symptoms:** Topic with TTL set and key-based compaction expected; `pulsar_storage_backlog_age_seconds` reports age beyond configured TTL; consumers reading compacted topic get stale data for keys that should have been evicted; disk usage grows beyond expected bounds; `pulsar-admin topics compaction-status` shows last compaction timestamp is old or never ran; `pulsar_storage_size` grows unboundedly even though retention.sizeInMB is set

**Root Cause Decision Tree:**
- If `compactionThreshold` is set but never triggered: topic write rate is below the threshold → compaction never fires automatically; must be triggered manually or reduce threshold
- If compaction ran but TTL-expired messages are still present: compaction does NOT delete messages by TTL — only retention policies do; compaction only retains the latest value per key within the retention window
- If TTL messages have non-null value (tombstones not sent): compaction retains them indefinitely — must explicitly send null-valued messages to signal deletion
- If retention is `infinite` (`retentionSizeInMB=-1, retentionTimeInMinutes=-1`): messages are never deleted regardless of TTL; retention and TTL interact — TTL only applies to subscriptions, not storage

**Key Concept — TTL vs Retention vs Compaction:**
- TTL (`messageTTLSeconds`): marks messages as expired for subscription cursors — allows cursors to skip; does NOT delete storage
- Retention (`retentionTimeInMinutes`, `retentionSizeInMB`): controls how long acknowledged messages are kept in storage
- Compaction: for persistent keyed topics — retains only the latest message per key within the retention window; does not enforce TTL

**Diagnosis:**
```bash
# Check compaction status for a topic
pulsar-admin topics compaction-status persistent://<tenant>/<namespace>/<topic>

# Check topic TTL, retention, and compaction threshold settings
pulsar-admin topics get-message-ttl persistent://<tenant>/<namespace>/<topic>
pulsar-admin namespaces get-retention <tenant>/<namespace>
pulsar-admin namespaces get-compaction-threshold <tenant>/<namespace>

# Storage size vs expected
pulsar-admin topics stats persistent://<tenant>/<namespace>/<topic> | \
  python3 -c "
import sys,json
s=json.load(sys.stdin)
print('storageSize:', s.get('storageSize',0),
      'backlogSize:', s.get('backlogSize',0),
      'msgBacklog:', s.get('msgBacklog',0))
"

# Age of oldest message in storage
# pulsar_storage_backlog_age_seconds{topic="..."}

# Check if compaction topic exists (for compacted reads)
pulsar-admin topics stats-internal persistent://<tenant>/<namespace>/<topic>/__compaction
```

**Thresholds:**
- Last compaction > 24 hours ago for a key-based topic with active writes = 🟡
- `pulsar_storage_backlog_age_seconds` > retention policy = 🔴
- Topic storage size growing unboundedly with no bound set = 🟡

## 18. Pulsar Functions State Store (BookKeeper) Bottleneck Causing Function Processing Lag

**Symptoms:** Pulsar Function processing lag growing (`pulsar_function_user_metric` or `pulsar_function_last_invocation` stale); function instance logs show high latency on state read/write operations; `putState()` / `getState()` calls in function code timing out; BookKeeper write latency elevated; multiple function instances competing for the same state keys; throughput drops proportional to state operation frequency

**Cascade Chain:**
1. Pulsar Functions with state enabled use BookKeeper as the state store via the StateStore API
2. Each `putState()` call writes an entry to a BookKeeper ledger — same BookKeeper cluster serving message storage
3. High function throughput + frequent state updates = BookKeeper write amplification
4. BookKeeper write latency increases → function `process()` method waits on state write → processing latency grows
5. Function consumer falls behind → subscription backlog accumulates
6. Function framework may restart instances that appear stuck → restart causes state replay
7. If multiple function instances write the same key concurrently: compare-and-swap conflicts → retry storms

**Root Cause Decision Tree:**
- If `pulsar_storage_write_latency_overflow` elevated AND function lag growing: BookKeeper I/O bottleneck confirmed as root cause
- If only one function topic is affected: check function state key cardinality — low cardinality keys cause hot-spot writes
- If function lag started after scaling up instances: more instances = more concurrent state writes → BookKeeper contention
- If state operations are reads (not writes): check if compaction of the state topic is lagging — reads scan uncompacted entries

**Diagnosis:**
```bash
# Function processing lag and last invocation time
pulsar-admin functions stats --name <function-name> --namespace <tenant>/<namespace>

# Function instance status (look for state operation latency)
pulsar-admin functions status --name <function-name> \
  --namespace <tenant>/<namespace> --instance-id 0

# BookKeeper write latency
# pulsar_storage_write_latency_le_20 and pulsar_storage_write_latency_overflow

# State store topic stats (BookKeeper table service)
pulsar-admin topics stats persistent://<tenant>/<namespace>/<function-name>-state

# Count function instances and compare to state write rate
pulsar-admin functions get --name <function-name> --namespace <tenant>/<namespace> | \
  python3 -c "import sys,json; c=json.load(sys.stdin); print('parallelism:', c.get('parallelism'))"

# Prometheus: function metrics endpoint
# pulsar_function_process_latency_ms — processing time per invocation
# pulsar_function_last_invocation — timestamp of last invocation (stale = lagging)
```

**Thresholds:**
- Function subscription backlog growing > 1 000/min = 🟡; > 10 000/min = 🔴
- `pulsar_function_process_latency_ms` p99 > 1 000 ms = 🟡; > 5 000 ms = 🔴
- `pulsar_storage_write_latency_overflow` > 0.5% of writes = 🟡

## 21. Silent Message Deduplication Failure

**Symptoms:** Downstream systems observe duplicate messages despite the Pulsar producer being configured with `producerName`. No errors appear in producer or broker logs. The duplication is only discovered through business-level reconciliation or idempotency key conflicts in the database.

**Root Cause Decision Tree:**
- If the producer process restarted or reconnected without preserving the same `producerName` AND `InitialSequenceId` → the broker's deduplication window treats the new producer as a different entity; previously sent sequence IDs are unknown, so messages are re-accepted as new
- If `brokerDeduplicationEnabled=false` on the namespace → deduplication is entirely disabled regardless of producer configuration
- If `brokerDeduplicationEntriesInterval` is too small or the dedup snapshot is too old → evicted entries from the dedup cursor allow old sequence IDs to be re-accepted
- If producer uses a shared `producerName` across multiple instances → sequence IDs from different instances collide or skip, defeating deduplication

**Diagnosis:**
```bash
# Check if deduplication is enabled on the namespace
pulsar-admin namespaces get-deduplication <tenant>/<namespace>

# Get namespace configuration including dedup interval
pulsar-admin namespaces policies <tenant>/<namespace> | grep -E "dedup|sequence"

# Check topic internal stats for deduplication cursor position
pulsar-admin topics stats-internal persistent://<tenant>/<namespace>/<topic> \
  | python3 -m json.tool | grep -E "dedup|sequence"

# Verify broker-level dedup config
pulsar-admin brokers get-all-dynamic-config | grep dedup
```

## 22. Partial Ledger Loss (1 Bookie Down)

**Symptoms:** A subset of topics become read-only or throw `LedgerClosedException` / `ManagedLedgerException: NoBookieAvailableException`. Other topics on the same broker function normally. Producers to affected topics get errors; consumers can read up to the last successfully written entry but no further. `bookkeeper shell listbookies` shows a reduced bookie count.

**Root Cause Decision Tree:**
- If one BookKeeper bookie is in `READONLY` state → its disk is full or an I/O error has occurred; the bookie rejects new writes but still serves existing reads
- If the bookie has been fully lost or shut down → any ledger fragment that had its ensemble member on that bookie cannot be written; new ledger creation fails if `writequorum` cannot be satisfied
- If `redpanda_cluster_unavailable_partitions` (or equivalent bookie `UnderReplicatedLedger` count) is non-zero → data is at risk; under-replicated ledgers need auto-recovery
- If `pulsar_storage_write_latency_overflow` spikes for specific topics → those topics' ledgers are on the degraded bookie

**Diagnosis:**
```bash
# List bookies and their state (rw = read-write, ro = read-only)
bookkeeper shell listbookies -rw
bookkeeper shell listbookies -ro

# Find under-replicated ledger fragments
bookkeeper shell listunderreplicatedledger

# Get internal topic ledger info to see which bookies host the fragments
pulsar-admin topics info-internal persistent://<tenant>/<namespace>/<topic>

# Check bookie disk and health
bookkeeper shell bookieformat -noninteractive -expandStorage 2>&1 | grep -E "error|full"
```

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|------------|---------------|
| `org.apache.pulsar.client.api.PulsarClientException$TopicDoesNotExistException` | Auto-create disabled (`allowAutoTopicCreation=false`) or wrong tenant/namespace path in topic URL | `pulsar-admin topics list <tenant>/<namespace>` |
| `org.apache.pulsar.client.api.PulsarClientException$ProducerBusyException` | Producer name already registered — another producer holds exclusive access on this topic | `pulsar-admin topics stats persistent://<tenant>/<ns>/<topic> \| grep producerName` |
| `org.apache.pulsar.client.api.PulsarClientException$AuthorizationException` | ACL not granted for the operation (produce/consume/admin) for this role on the namespace | `pulsar-admin namespaces permissions <tenant>/<namespace>` |
| `org.apache.pulsar.client.api.PulsarClientException$BrokerPersistenceException` | BookKeeper write failure — bookie unavailable, ledger full, or disk error on bookie | `pulsar-admin brokers get-all-dynamic-config \| grep bookkeeper; journalctl -u bookkeeper -n 50` |
| `org.apache.pulsar.client.api.PulsarClientException$TooManyRequestsException` | Rate limit hit — namespace-level `publishRate` or `dispatchRate` throttling active | `pulsar-admin namespaces get-publish-rate <tenant>/<namespace>` |
| `TOPIC_TERMINATED` | Topic has been administratively terminated — no further produces accepted; consumers should drain and stop | `pulsar-admin topics stats persistent://<tenant>/<ns>/<topic> \| grep terminated` |
| `CHECKSUM_ERROR` | Message corrupted in transit or at rest — BookKeeper entry checksum mismatch | `pulsar-admin topics stats-internal persistent://<tenant>/<ns>/<topic>` |
| `ConsumerBusy` | Exclusive subscription already has an active consumer — second consumer rejected | `pulsar-admin topics subscriptions persistent://<tenant>/<ns>/<topic>` |

---

## 19. Shared BookKeeper Cluster Between Multiple Pulsar Tenants Causes Bookie Write Contention

**Symptoms:** Write latency (`pulsar_storage_write_latency_overflow`) spikes for topics belonging to multiple tenants simultaneously; bookie CPU at 100%; `pulsar_storage_write_rate` drops across all brokers; individual tenant throughput falls even though their topic's publish rate has not increased; BookKeeper `WriteRequestsInQueue` metric grows; journals on bookies show `journal_write_cb` waiting; recovery time after bookie restart is much longer than expected

**Root Cause Decision Tree:**
- If all tenants share the same bookie cluster without `isolationGroup` assignment: a bursty tenant monopolizes bookie journal write throughput; other tenants' ledgers queue behind
- If `journalWriteBufferSizeKB` is too small: many small write requests each trigger a journal flush instead of being batched → journal write rate limited by fsync IOPS not throughput
- If `numAddWorkerThreads` and `numReadWorkerThreads` are under-provisioned relative to number of concurrent ledgers: bookie thread pool saturates with moderate multi-tenant concurrency
- If disk holding journal and data ledgers is the same device: sequential journal writes compete with random ledger entry reads for catch-up subscriptions
- If `dbStorage_rocksDB_blockCacheSize` is too small for many concurrent ledgers: RocksDB cache evictions cause extra disk reads, competing with journal writes

**Diagnosis:**
```bash
# Write latency histogram — how many ops exceed 200 ms?
curl -s http://<broker>:8080/metrics | \
  grep "pulsar_storage_write_latency" | grep -v "^#"

# Bookie journal write queue depth (BookKeeper JMX or Prometheus)
curl -s http://<bookie>:8000/metrics | grep -E "journal_queue|WriteRequestsInQueue"

# Per-tenant throughput breakdown
pulsar-admin brokers get-runtime-config | grep isolationGroup
pulsar-admin resource-quotas get --namespace <tenant>/<namespace>

# Bookie disk I/O breakdown — journal vs ledger disks
iostat -xm 2 10 | grep -E "Device|sd|nvme"

# Which tenants are writing most?
curl -s http://<broker>:8080/metrics | \
  grep 'pulsar_publish_rate.*namespace' | sort -t= -k2 -rn | head -20
```

**Thresholds:**
- `pulsar_storage_write_latency_overflow` > 0 = 🟡; > 1 % of writes = 🔴
- Bookie `WriteRequestsInQueue` > 1 000 = 🔴
- Journal disk `%util` > 80 % = 🟡; = 100 % = 🔴
- Bookie CPU > 85 % sustained = 🟡

## 20. Exclusive Subscription Failover Delay Causing Consumer Blackout

**Symptoms:** An exclusive subscription consumer process crashes; `pulsar_consumers_count` drops to 0; a standby consumer is running but Pulsar does not switch it to active for 30–60 s; `pulsar_subscription_back_log` grows during the gap; applications report message processing blackout even though the backup consumer is connected; after reconnect the backup consumer eventually receives messages but with a gap in timing

**Root Cause Decision Tree:**
- If subscription type is `Exclusive` and `subscriptionExpiryDurationMinutes` is not set: Pulsar waits for the cursor to be garbage-collected before another consumer can attach → slow failover
- If `clientOperationTimeoutMs` is high on the backup consumer: retry backoff delays the reconnect attempt after the primary's TCP connection closes
- If `brokerClientAuthenticationEnabled` and TLS is in use: certificate handshake on reconnect adds latency, especially if OCSP or CRL checking is enabled
- If the failed consumer held an `exclusiveConsumerEnabled` lock and the broker has not yet timed it out: new consumer cannot attach until broker-side timeout fires
- If Failover subscription type was intended but Exclusive was configured by mistake: use Failover for active/standby patterns

**Diagnosis:**
```bash
# Confirm subscription type and active consumer count
pulsar-admin topics stats persistent://<tenant>/<ns>/<topic> \
  | python3 -c "import sys,json; s=json.load(sys.stdin); \
    [print(k,v.get('type'),v.get('consumers')) for k,v in s['subscriptions'].items()]"

# Check cursor expiry policy
pulsar-admin namespaces get-subscription-expiration-time <tenant>/<namespace>

# Subscription backlog during failover gap
curl -s "http://<broker>:8080/metrics" | \
  grep 'pulsar_subscription_back_log{.*<topic>.*}'

# Consumer connect/disconnect events in broker log
journalctl -u pulsar -n 200 | grep -E "Exclusive|consumer.*connected|consumer.*closed"

# Active consumers per subscription
pulsar-admin topics subscriptions persistent://<tenant>/<ns>/<topic>
pulsar-admin topics stats persistent://<tenant>/<ns>/<topic> \
  | grep -A5 "consumers"
```

**Thresholds:**
- Backlog growth during failover > 100 K messages = 🔴
- `subscriptionExpiryDurationMinutes` unset with exclusive subscriptions in production = misconfiguration (🟡)

# Capabilities

1. **Broker health** — Topic ownership, load balancing, OOM issues
2. **BookKeeper** — Bookie failures, ledger recovery, disk management
3. **Subscriptions** — Backlog monitoring, cursor management, consumer scaling
4. **Geo-replication** — Cross-cluster replication lag, link failures
5. **Tiered storage** — Offload status, cold data retrieval
6. **Multi-tenancy** — Namespace policies, resource quotas, isolation

# Critical Metrics to Check First

1. `pulsar_subscription_back_log` — growing backlog means consumers falling behind
2. `bookie_SERVER_STATUS` — 0 means bookie is read-only; new writes at risk
3. `pulsar_storage_write_latency_overflow` — any non-zero value means > 200 ms write latency
4. `pulsar_subscription_blocked_on_unacked_messages` — 1 means consumers deadlocked
5. `pulsar_connection_create_fail_count` — spiking means broker connectivity issue

# Output

Standard diagnosis/mitigation format. Always include: affected topics/subscriptions,
broker and bookie IDs, namespace, and recommended pulsar-admin commands.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| Consumer lag growing despite consumers running | Bookie disk full; broker write failures cause message drops and re-delivery storms | `pulsar-admin brokers get-internal-config` and `df -h` on each bookie node |
| Producer `PRODUCER_FENCED` errors | Another producer claimed exclusive ownership after a network blip; broker still holds old lease | `pulsar-admin topics stats persistent://tenant/ns/topic | jq '.publishers'` |
| Geo-replication lag > 5 min | DNS resolution failure in remote cluster; replicator cannot resolve remote broker address | `kubectl exec -n pulsar broker-0 -- nslookup <remote-broker-service>` |
| Subscription cursor stuck; no messages acknowledged | Consumer application OOM killed mid-batch; uncommitted ack batch held in memory | `kubectl describe pod <consumer-pod>` and check `OOMKilled` in last state |
| Topic lookup timeout on client | ZooKeeper ensemble down to 1 healthy node; broker metadata reads timing out | `echo ruok | nc zookeeper-0 2181` (repeat for each ZK node); check for quorum |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of 5 bookies has a failing disk; ensemble writes still succeed at WQ=2 | `bookie_SERVER_STATUS{pod="bookie-2"} == 0`; cluster-level write success masks it | Write latency p99 elevated; when a second bookie degrades, WQ cannot be met and writes stall | `pulsar-admin bookies list-bookie-info` then `curl http://bookie-2:8000/api/v1/bookie/info` |
| 1 broker partition leader is slow (GC pause); other partitions healthy | Per-partition produce latency histogram shows one outlier; aggregate p99 looks acceptable | Producers assigned to the slow partition experience timeouts; others are unaffected | `pulsar-admin topics partitioned-stats persistent://tenant/ns/topic --per-partition | jq '.partitions | to_entries[] | select(.value.producerCount > 0)'` |
| 1 of 3 Pulsar proxy pods has stale routing table after broker restart | Intermittent `TopicNotFoundException` affecting ~33% of connections; retries mask the issue | Clients see occasional failures proportional to traffic routed through the stale proxy | `kubectl logs -n pulsar pulsar-proxy-1 | grep -c 'TopicNotFound'` (compare across pods) |
| 1 subscription on a partitioned topic has a stuck cursor on partition 3 only | `pulsar_subscription_back_log` growing on partition 3; other partitions draining | End-to-end processing appears mostly healthy; lag grows silently on one shard | `pulsar-admin topics stats persistent://tenant/ns/topic-partition-3 | jq '.subscriptions'` |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Producer/consumer backlog (messages) | > 1,000 | > 1,000,000 | `pulsar-admin topics stats persistent://tenant/ns/<topic> \| jq '.backlogSize'` |
| Publish latency p99 (ms) | > 100 | > 500 | `curl -s http://broker:8080/metrics \| grep 'pulsar_publish_latency_ms{quantile="0.99"}'` |
| Bookie journal write latency p99 (ms) | > 5 | > 50 | `curl -s http://bookie:8000/metrics \| grep 'bookkeeper_server_journal_ADD_ENTRY_latency_ms{quantile="0.99"}'` |
| Broker topic backlog quota violations total | > 10 | > 100 | `curl -s http://broker:8080/metrics \| grep pulsar_storage_backlog_quota_exceeded_evictions_total` |
| ZooKeeper request latency p99 (ms) | > 50 | > 200 | `curl -s http://zookeeper:8000/metrics \| grep 'zookeeper_latency{quantile="0.99"}'` |
| Subscription unacked messages | > 10,000 | > 500,000 | `pulsar-admin topics stats persistent://tenant/ns/<topic> \| jq '.subscriptions[].unackedMessages'` |
| Bookie ledger disk usage (%) | > 75 | > 90 | `curl -s http://bookie:8000/api/v1/bookie/info \| jq '.freeSpace,.totalSpace'` |
| Broker dispatch throttle rejections total (rate/min) | > 100 | > 1,000 | `curl -s http://broker:8080/metrics \| grep pulsar_broker_throttled_message_rate` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| BookKeeper bookie disk usage | >70% on any bookie and growing >5%/week | Add bookie nodes or expand volumes; `pulsar-admin bookies set-bookie-rack` to rebalance ledger placement | 2–3 weeks |
| `pulsar_storage_backlog_size` per subscription | Backlog growing steadily for >1 hour | Scale consumer replicas; inspect consumer lag: `pulsar-admin topics stats persistent://t/n/topic`; check DLQ for stuck messages | Hours |
| Managed ledger write cache size | `managedLedgerCacheUsedSize` / `managedLedgerCacheSize` > 80% | Increase `managedLedgerCacheSize` in `bookkeeper.conf`; add RAM to brokers | 1 week |
| ZooKeeper node count | `zk_approximate_data_size` or inode count growing rapidly | Enable ZooKeeper compaction; set `autopurge.purgeInterval=24`; consider migrating to Oxia metadata store | 3 weeks |
| `pulsar_producer_msg_rate_in` vs. throughput headroom | Sustained at >80% of topic retention throughput limit | Increase `maxProducerMessageQueueSize` and partition count: `pulsar-admin topics update-partitioned-topic` | 1 week |
| Geo-replication lag `pulsar_replication_backlog` | Growing lag on any remote cluster | Check remote cluster connectivity and quota; scale replication workers: `pulsar-admin namespaces set-clusters` | Days |
| `pulsar_broker_components_max_event_loop_queue_size` | Consistently >1 000 | Broker is CPU-bound; increase broker replicas: `kubectl scale statefulset/pulsar-broker --replicas=N` | 1 week |
| Bookie journal disk write latency | `bookkeeper_server_ADD_ENTRY_REQUEST_latency_ms` p99 > 20 ms trending up | Check for journal disk I/O saturation; separate journal disk from ledger disk; upgrade to NVMe | 1 week |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check overall broker health and cluster status
pulsar-admin brokers list standalone 2>/dev/null || pulsar-admin brokers list pulsar-cluster

# Show all topics with backlog above threshold
pulsar-admin topics stats-internal persistent://public/default/topic | jq '.ledgers | length'

# List top namespaces by storage backlog
pulsar-admin namespaces stats public | jq '.[] | select(.storageSize > 1073741824) | {name: .name, sizeGB: (.storageSize/1073741824)}'

# Get broker resource usage (CPU, memory, throughput)
curl -s http://localhost:8080/admin/v2/broker-stats/load-report | jq '{cpu: .cpu, memory: .memory, msgRateIn: .msgRateIn, msgRateOut: .msgRateOut}'

# Find topics with most unacknowledged messages
pulsar-admin persistent backlog --global | sort -t'|' -k3 -rn | head -10

# Check ZooKeeper session status from broker
curl -s http://localhost:8080/admin/v2/brokers/health

# List all subscriptions for a topic and their lag
pulsar-admin topics stats persistent://public/default/your-topic | jq '.subscriptions | to_entries[] | {sub: .key, backlog: .value.msgBacklog, consumers: (.value.consumers | length)}'

# Check BookKeeper bookie status and disk usage
curl -s http://localhost:8080/admin/v2/bookies/all | jq '.[] | {bookie: .bookieId, state: .state}'

# Monitor replication lag across geo-clusters
pulsar-admin topics stats persistent://public/default/your-topic | jq '.replication | to_entries[] | {cluster: .key, replicationBacklog: .value.replicationBacklog}'

# Check function worker cluster health
curl -s http://localhost:6750/admin/v2/worker/cluster | jq '.[] | {workerId: .workerId, hostname: .hostname}'
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Message Delivery Success Rate | 99.9% | `1 - (rate(pulsar_producer_send_fail_total[5m]) / rate(pulsar_producer_msg_rate_in[5m]))` | 43.8 min | >14.4x |
| End-to-End Message Latency p99 ≤ 500 ms | 99.5% | `histogram_quantile(0.99, rate(pulsar_broker_publish_latency_bucket[5m])) < 0.5` | 3.6 hr | >7.2x |
| Consumer Backlog SLO (lag < 10K msgs) | 99% | `pulsar_subscription_back_log < 10000` per subscription | 7.3 hr | >2.4x |
| Broker Availability | 99.95% | `avg(up{job="pulsar-broker"})` across broker pods | 21.9 min | >28.8x |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Broker advertised address | `grep advertisedAddress /conf/broker.conf` | Set to the external/VIP hostname, not `localhost` |
| Retention policy on namespaces | `pulsar-admin namespaces get-retention public/default` | `retentionTimeInMinutes` and `retentionSizeInMB` both non-zero for durable topics |
| Replication clusters configured | `pulsar-admin clusters list` | All expected geo-clusters present |
| TLS enabled for broker | `grep tlsEnabled /conf/broker.conf` | `tlsEnabled=true` with valid cert and key paths |
| Authentication enabled | `grep authenticationEnabled /conf/broker.conf` | `authenticationEnabled=true` in production |
| Topic auto-creation policy | `pulsar-admin namespaces get-auto-topic-creation public/default` | `allowAutoTopicCreation=false` or type restricted to `non-partitioned` |
| BookKeeper ensemble/write/ack quorum | `grep -E 'managedLedgerDefault(Ensemble\|WriteQuorum\|AckQuorum)Size' /conf/broker.conf` | Ensemble ≥ 3, WriteQuorum ≥ 2, AckQuorum ≥ 2 for HA |
| Max consumers per subscription | `pulsar-admin namespaces get-max-consumers-per-subscription public/default` | Positive integer set, not unlimited (0) in shared-tenancy environments |
| Offload threshold configured | `pulsar-admin namespaces get-offload-threshold public/default` | Non-negative value if tiered storage is enabled |
| Schema compatibility strategy | `pulsar-admin namespaces get-schema-compatibility-strategy public/default` | Set to `BACKWARD` or `FULL` to prevent breaking consumers |

---

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `Failed to create ledger for topic ... BKException: Not enough bookies` | ERROR | Fewer BookKeeper bookies available than ensemble size | Check bookie health; `pulsar-admin bookies list`; add bookie or reduce ensemble size |
| `Could not find owner for topic ... 503 Service Unavailable` | ERROR | Broker load balancer has not yet assigned topic ownership | Wait for ownership re-assignment; check broker leader election in ZooKeeper |
| `Backlog quota exceeded for topic` | WARN | Producer backlog exceeds namespace quota policy | Drain consumers; raise quota; or enable `retention-backlog` policy |
| `org.apache.pulsar.broker.service.BrokerServiceException$TopicFencedException` | ERROR | Topic fenced after ledger error; writes blocked | `pulsar-admin topics unload <topic>` to force re-ownership; investigate underlying BK error |
| `Ledger ... is marked as fenced` | ERROR | BookKeeper ledger fenced due to split-brain write attempt | Identify and stop the stale writer; run ledger recovery via BookKeeper admin |
| `Failed to connect to ZooKeeper ... SessionExpiredException` | FATAL | ZooKeeper session expired; broker loses cluster membership | Check ZooKeeper ensemble health; verify `zookeeperSessionExpirePercent` timeout config |
| `Compaction failed for topic ... OutOfMemoryError` | ERROR | Compaction heap exhausted during key deduplication pass | Increase broker JVM heap (`-Xmx`); reduce compaction threshold; trigger off-peak |
| `Deduplication ... sequence ID ... is not valid` | WARN | Producer resent message with out-of-order sequence ID | Check producer retry logic; enable `enableProducerIdempotency` correctly |
| `Topic ... is being fenced because of a metastore conflict` | ERROR | Two brokers simultaneously claiming topic ownership | Wait for conflict resolution by coordinator; inspect ZooKeeper node for topic |
| `Rate limit exceeded for consumer ... throttled` | WARN | Consumer dispatch rate exceeds namespace policy | Increase dispatch rate limit or scale out consumers |
| `Schema incompatible for topic ... FORWARD_TRANSITIVE check failed` | ERROR | Producer publishing schema incompatible with registered schema | Fix schema change to be backward/forward compatible; or update schema strategy |
| `Replication backlog growing for cluster ... cursor lag` | WARN | Geo-replication lagging; replication cursor falling behind | Check remote cluster connectivity; verify replication dispatch rate; scale replicators |

---

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `TOPIC_NOT_FOUND` | Topic does not exist on broker | Producers/consumers get connection error | Create topic: `pulsar-admin topics create <topic>`; check namespace |
| `PRODUCER_BLOCKED_QUOTA_EXCEEDED` | Namespace backlog quota hit; producer blocked | No new messages accepted on topic | Increase backlog quota; speed up consumers; or enable `producer_exception` policy |
| `CONSUMER_ASSIGNED` (subscription conflict) | Two consumers with same subscription name and exclusive type | Second consumer cannot attach | Change subscription type to `Shared` or `Key_Shared`; close conflicting consumer |
| `INCOMPATIBLE_SCHEMA` | Schema registry rejects schema change | Producer fails to publish | Align schema with evolution rules (BACKWARD/FORWARD); or delete and re-register schema |
| `MESSAGE_EXPIRE` | TTL expired; message deleted before consumer processed it | Consumer misses messages | Increase namespace TTL; optimize consumer throughput |
| `CURSOR_DOES_NOT_EXIST` | Subscription cursor lost (e.g., topic deletion/recreation) | Consumer resumes from latest instead of last position | Restore backup; or accept data loss and seek consumer to earliest available |
| `NOT_ENOUGH_BOOKIES` | Ensemble write quorum cannot be satisfied | Topic publishing fails entirely | Add BookKeeper bookies; reduce `managedLedgerDefaultEnsembleSize` |
| `FENCED` (topic state) | Topic fenced to prevent split-brain writes | All producers blocked on topic | `pulsar-admin topics unload <topic>` to force re-ownership after resolving ownership conflict |
| `AUTHENTICATION_REQUIRED` | Client presents no or invalid credentials | Connection rejected | Provide valid JWT/mTLS credentials; check `authenticationEnabled` on broker |
| `AUTHORIZATION_ERROR` | Client authenticated but lacks permission on topic/namespace | Operation rejected | Grant permission: `pulsar-admin namespaces grant-permission`; check role bindings |
| `LEDGER_NOT_EXIST` (BookKeeper) | Broker references a ledger ID that no longer exists | Data loss for affected segment | Restore from offload/backup; acknowledge loss; recreate topic if needed |
| `TRANSACTION_CONFLICT` | Transactional message commit conflicts with concurrent transaction | Transaction aborted | Implement retry with backoff in producer; check transaction coordinator health |

---

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| BookKeeper Quorum Failure | `pulsar_broker_publish_latency` spike; `pulsar_storage_write_errors` rising | `Not enough bookies`; `Ledger fenced` | BrokerPublishError alert | Bookie pod(s) crashed or network partition to bookies | `bookkeeper shell listunderreplicated`; restore bookie; trigger auto-recovery |
| ZooKeeper Session Expiry | Broker restarting; topics going `UNOWNED` | `SessionExpiredException`; `Failed to connect to ZooKeeper` | BrokerDown alert | ZooKeeper ensemble overloaded or network hiccup exceeding session timeout | Check ZK ensemble; increase `zookeeperSessionExpirePercent`; tune GC on ZK nodes |
| Consumer Backlog Surge | `pulsar_consumer_msg_backlog` rising continuously; `pulsar_throughput_out` near zero | `Backlog quota exceeded`; `Rate limit exceeded for consumer` | BacklogHigh alert | Consumer group scaled down or processing too slow for ingestion rate | Scale out consumers; check for consumer errors; raise dispatch rate limit |
| Schema Registry Conflict | Producer connection failures on specific topics | `INCOMPATIBLE_SCHEMA`; `Schema incompatible` | ProducerError alert | Incompatible schema deployed; schema evolution strategy mismatch | Roll back schema change; align with BACKWARD/FORWARD_TRANSITIVE strategy |
| Geo-Replication Lag | `pulsar_replication_backlog` growing; `pulsar_replication_throughput_out` low | `Replication backlog growing for cluster` | ReplicationLag alert | WAN connectivity degraded between clusters; remote cluster overloaded | Check WAN link; increase `replicatorDispatchRateInMessages`; scale replicators |
| Topic Fence Cascade | Multiple topics showing `FENCED` state; all produce operations failing | `Topic fenced because of metastore conflict`; `TopicFencedException` | TopicFenced alert | Rolling restart during high traffic caused split-brain ownership | `pulsar-admin topics unload` for each fenced topic to force re-ownership; ensure graceful restart |
| Compaction OOM | `pulsar_compaction_failed_rate` rising; broker JVM heap at max | `Compaction failed ... OutOfMemoryError` | BrokerOOMAlert | Compaction of large topic exceeds broker heap | Increase broker heap; reduce `managedLedgerMinLedgerRolloverTimeMinutes`; run compaction off-peak |
| Namespace Auth Misconfiguration | Mass client disconnect after config push; reconnect attempts all fail | `AUTHENTICATION_REQUIRED`; `AUTHORIZATION_ERROR` on all topics | ClientConnectionError spike | Auth policy pushed without updating client credentials | Revert namespace policy; push correct token/mTLS credentials to clients |
| Offload Failure / Storage Tier Gap | `pulsar_storage_offloaded_size` not growing; broker disk filling | `Failed to offload ledger to ... TieredStorageException` | DiskUsageHigh alert | Object store credentials expired or bucket policy changed | Rotate credentials in broker config; verify bucket ACL; `pulsar-admin topics offload` |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `ProducerQueueIsFullError` | Pulsar Java/Python/Go client | Producer send queue full; broker back-pressuring producer | `pulsar_producer_msg_publish_pendingQueueSize` near `maxPendingMessages` | Increase `maxPendingMessages`; scale broker; reduce publish rate |
| `TopicNotFoundException` | Pulsar client SDK | Topic auto-creation disabled; namespace not initialized | `pulsar-admin topics list <namespace>` | Enable `allowAutoTopicCreation`; pre-create topic; check namespace policy |
| `AuthenticationException` / `AuthorizationException` | Pulsar client SDK | Expired JWT token; role lacks permission on topic | Check broker logs for `AUTHENTICATION_REQUIRED`; inspect token expiry | Rotate JWT token; grant correct role permissions via `pulsar-admin namespaces grant-permission` |
| `IncompatibleSchemaException` | Pulsar Schema-aware client | Schema registry rejects new schema as incompatible | `pulsar-admin schemas get persistent://tenant/ns/topic` | Align schema evolution strategy; use `ALWAYS_COMPATIBLE` for migration |
| `LookupException: Failed to find broker` | Pulsar client SDK | ZooKeeper unavailable; discovery endpoint unreachable | `pulsar-admin brokers list`; check ZK health | Restore ZK quorum; verify discovery URL in client config |
| Consumer `receive()` hangs / timeout | Java client `consumer.receive(timeout)` | No messages in topic; consumer partition not assigned | `pulsar-admin topics stats persistent://...` — check backlog and consumers | Ensure subscription is created; verify topic partitioning; check consumer filter |
| `MessageTooLargeException` | Pulsar client SDK | Message size exceeds `maxMessageSize` broker config | Check message byte size vs `maxMessageSize` (default 5 MB) | Split large messages; increase `maxMessageSize` in broker.conf; use chunking API |
| `ProducerBusyException` on exclusive topic | Pulsar client SDK | Another producer already holds exclusive access | Check `pulsar-admin topics stats` — `publishers` field | Use shared/partitioned topics; coordinate exclusive producer lifecycle |
| HTTP 503 on Pulsar Admin API | curl, Terraform, Pulsar admin SDK | Broker overloaded or in GC pause; service port unbound | `curl http://broker:8080/metrics`; check GC pause metrics | Restart affected broker; tune JVM GC; check heap usage |
| Consumer message redelivery loop | Application consumer logic | `nack()` called repeatedly; message processing fails | `pulsar_consumer_msg_redelivery_count` high; inspect `deadLetterTopic` | Fix consumer processing bug; configure DLQ; set `ackTimeout` appropriately |
| `BacklogQuotaExceededException` | Pulsar client SDK | Namespace backlog quota hit; new messages dropped or blocked | `pulsar-admin namespaces get-backlog-quotas <ns>` | Increase backlog quota; scale consumers; set retention policy |
| Partition metadata timeout | Pulsar client (partitioned topic producer) | ZooKeeper slow; broker unable to return partition count | Broker logs `Failed to get partition metadata`; check ZK latency | Optimize ZK; retry partition metadata fetch; increase client timeout |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Subscription backlog growth | `pulsar_consumer_msg_backlog` growing 1–5% per hour | `pulsar-admin topics stats persistent://<ns>/<topic> | jq '.subscriptions'` | Hours to days | Scale consumer group; optimize consumer processing latency; check for consumer errors |
| BookKeeper ledger fragmentation | `bookie_ledger_count` growing; disk usage rising without proportional data growth | `bookkeeper shell bookiesanity` | Weeks | Run `bookkeeper shell compact`; schedule ledger garbage collection |
| ZooKeeper watcher leak | ZK connection count growing; ZK latency p99 rising | `echo mntr | nc zk-host 2181 | grep zk_num_alive_connections` | Days | Restart brokers one-by-one; audit watcher registrations in broker code |
| Journal write latency degradation on BookKeeper | Producer publish latency rising gradually; `bookie_journal_SYNC_ms` p99 increasing | `curl http://bookie:8000/metrics | grep journal_SYNC` | Hours | Check journal disk health; separate journal and ledger disks; increase journal write buffer |
| Topic ownership imbalance | Some brokers serving 10× more topics than others; `pulsar_broker_topics_count` skewed | `pulsar-admin brokers list-dynamic-config`; compare per-broker topic counts | Days | Trigger load balancer: `pulsar-admin namespaces unload <ns>`; adjust `loadBalancerBrokerMaxTopics` |
| Geo-replication backlog buildup | `pulsar_replication_backlog` rising on one direction | `pulsar-admin topics stats-internal persistent://<ns>/<topic>` | Hours | Check WAN link; increase `replicatorDispatchRateInMessages`; scale replication workers |
| JVM old-gen heap drift (broker) | GC pause frequency rising week-over-week; heap after full GC not recovering baseline | JVM GC logs: `jstat -gcutil <pid> 5s 20` | Days to weeks | Profile heap with heap dump; identify leaked caches; upgrade Pulsar version |
| Compaction lag on compacted topic | Compacted topic delivering old values on subscribe; compaction cycle time rising | `pulsar-admin topics compaction-status persistent://...` | Hours | Increase compaction worker memory; run manual: `pulsar-admin topics compact persistent://...` |
| Dead-letter topic accumulating | `pulsar_consumer_msg_redelivery_count` not clearing; DLQ depth growing | `pulsar-admin topics stats persistent://<ns>/<topic>-DLQ` | Hours | Investigate and fix consumer bug; replay DLQ messages after fix |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# pulsar-health-snapshot.sh — Cluster-wide health overview
set -euo pipefail
ADMIN="${PULSAR_ADMIN:-http://localhost:8080}"

echo "=== Apache Pulsar Health Snapshot $(date -u) ==="

echo -e "\n--- Broker Status ---"
curl -sf "$ADMIN/admin/v2/brokers/ready" && echo " [READY]" || echo " [NOT READY]"

echo -e "\n--- Active Brokers ---"
curl -sf "$ADMIN/admin/v2/brokers/internal-configuration" | python3 -c "import sys,json; d=json.load(sys.stdin); print('Metadata store:', d.get('zookeeperServers','n/a'))"
curl -sf "$ADMIN/admin/v2/brokers/list" 2>/dev/null | python3 -c "import sys,json; bs=json.load(sys.stdin); print(f'Brokers: {len(bs)}'); [print(' ', b) for b in bs[:10]]"

echo -e "\n--- BookKeeper Bookie Health ---"
curl -sf "$ADMIN/admin/v2/bookies/all" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('All bookies:', list(d.get('bookieInfoMap', {}).keys())[:10])
" || echo "BookKeeper API unavailable"

echo -e "\n--- Tenants & Namespaces ---"
TENANTS=$(curl -sf "$ADMIN/admin/v2/tenants" | python3 -c "import sys,json; t=json.load(sys.stdin); print(len(t), 'tenants:', t[:5])")
echo "$TENANTS"

echo -e "\n--- Top Backlog Subscriptions (sample first namespace) ---"
FIRST_NS=$(curl -sf "$ADMIN/admin/v2/namespaces/public" 2>/dev/null | python3 -c "import sys,json; ns=json.load(sys.stdin); print(ns[0] if ns else '')" 2>/dev/null || echo "")
if [ -n "$FIRST_NS" ]; then
  curl -sf "$ADMIN/admin/v2/persistent/$FIRST_NS?bundle=0" 2>/dev/null \
    | python3 -c "import sys,json; [print(t) for t in json.load(sys.stdin)[:10]]" 2>/dev/null || true
fi

echo -e "\n--- Recent Broker Errors ---"
journalctl -u pulsar -n 50 --no-pager 2>/dev/null | grep -iE 'ERROR|WARN|exception' | tail -20 || \
  find /pulsar/logs -name "*.log" -newer /tmp/.last_check 2>/dev/null | xargs grep -iE 'ERROR|exception' | tail -20 || \
  echo "Log source not found"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# pulsar-perf-triage.sh — Throughput, latency, and backlog analysis
ADMIN="${PULSAR_ADMIN:-http://localhost:8080}"
PROM="${PULSAR_PROM:-http://localhost:8080/metrics}"

echo "=== Pulsar Performance Triage $(date -u) ==="

echo -e "\n--- Broker Publish Latency (from metrics) ---"
curl -sf "$PROM" 2>/dev/null | grep -E 'pulsar_publish_latency_ms_quantile|pulsar_broker_publish_latency' | head -20 || echo "Metrics endpoint unavailable"

echo -e "\n--- Top 10 Topics by Backlog ---"
# Iterate namespaces to find backlogs
curl -sf "$ADMIN/admin/v2/namespaces/public" 2>/dev/null | python3 -c "
import sys, json, urllib.request, os
admin = os.environ.get('PULSAR_ADMIN','http://localhost:8080')
namespaces = json.load(sys.stdin)
for ns in namespaces[:5]:
    try:
        with urllib.request.urlopen(f'{admin}/admin/v2/persistent/{ns}') as r:
            topics = json.loads(r.read())
        for t in topics[:3]:
            name = t.split('://',1)[1]
            with urllib.request.urlopen(f'{admin}/admin/v2/persistent/{name}/stats') as r:
                stats = json.loads(r.read())
            subs = stats.get('subscriptions', {})
            for sub, sd in subs.items():
                backlog = sd.get('msgBacklog', 0)
                if backlog > 0:
                    print(f'  backlog={backlog:>10} topic={t} sub={sub}')
    except Exception as e:
        pass
" 2>/dev/null | sort -rn | head -10 || echo "Could not enumerate topics"

echo -e "\n--- Replication Lag ---"
curl -sf "$PROM" 2>/dev/null | grep 'pulsar_replication_backlog' | head -10 || true

echo -e "\n--- Consumer Redelivery Counts ---"
curl -sf "$PROM" 2>/dev/null | grep 'pulsar_consumer_msg_redelivery_count' | sort -t' ' -k2 -rn | head -10 || true
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# pulsar-resource-audit.sh — JVM heap, ZK connections, BookKeeper disk
ADMIN="${PULSAR_ADMIN:-http://localhost:8080}"

echo "=== Pulsar Resource Audit $(date -u) ==="

echo -e "\n--- JVM Heap (broker process) ---"
PULSAR_PID=$(pgrep -f 'pulsar.PulsarBrokerStarter\|org.apache.pulsar.PulsarBrokerStarter' | head -1)
if [ -n "$PULSAR_PID" ]; then
  jstat -gcutil "$PULSAR_PID" 2>/dev/null | tail -2 || echo "jstat unavailable"
  grep -E 'VmRSS|VmPeak' /proc/$PULSAR_PID/status 2>/dev/null || true
else
  echo "Broker process not found locally"
fi

echo -e "\n--- ZooKeeper Connection Count ---"
for ZK_HOST in ${ZK_HOSTS:-localhost}; do
  echo -n "$ZK_HOST: "
  echo mntr | nc "$ZK_HOST" 2181 2>/dev/null | grep -E 'zk_num_alive_connections|zk_avg_latency|zk_outstanding_requests' || echo "unreachable"
done

echo -e "\n--- BookKeeper Disk Usage ---"
curl -sf "$ADMIN/admin/v2/bookies/disk-usage" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
for bookie, info in d.items():
    used = info.get('usedBytes', 0) // 1024**3
    free = info.get('freeBytes', 0) // 1024**3
    print(f'  {bookie}: used={used}GB free={free}GB')
" 2>/dev/null || echo "Bookie disk API not available"

echo -e "\n--- Network Connections to Broker Port 6650 ---"
ss -tnp | grep ':6650' | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -15 || true

echo -e "\n--- Broker Thread Pool Stats ---"
curl -sf "$ADMIN/admin/v2/brokers/internal-configuration" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'{k}: {v}') for k,v in d.items() if 'thread' in k.lower() or 'worker' in k.lower()]" || true
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| High-throughput producer flooding a shared broker | Other tenants experiencing publish latency spikes; `pulsar_broker_publish_latency` p99 rising | `pulsar_producer_msg_publish_rate` per topic — identify outlier | Apply per-namespace `publishMaxMessageRate`; throttle offending producer | Set namespace-level publish rate limits for all tenants at provisioning time |
| Unthrottled consumer triggering dispatch storm | Broker CPU spikes; other consumers starved of dispatch credits | `pulsar_consumer_msg_out_counter` per subscription — find saturating consumer | Set `receiverQueueSize` cap; apply `rateLimit` on subscription dispatch | Configure `dispatchRateInMessages` per subscription at namespace policy level |
| ZooKeeper watcher flood from chatty broker | ZK avg_latency rising; all brokers affected by metadata slowdown | `echo mntr | nc zk 2181 | grep zk_watch_count` — identify high watcher broker | Restart chatty broker; rate-limit metadata operations | Upgrade to BookKeeper metadata store (Oxia/etcd) to replace ZooKeeper for scalability |
| BookKeeper journal disk shared with OS/app logs | `bookie_journal_SYNC_ms` spikes; producer latency rises when log volume is high | `iostat -x 1 10`; `lsof +D /journal` to find non-Bookie writers | Move Bookie journal to a dedicated disk or partition | Provision dedicated SSDs for Bookie journal; use separate mount points |
| Large message batch saturating broker NIC | All topics on broker showing latency spikes during batch publishes | `iftop -i <nic>`; correlate with `pulsar_producer_msg_publish_rate * avgMsgSize` | Throttle batch producer; enable message chunking | Enforce `maxMessageSize`; require chunking for messages > 1 MB; use dedicated broker for batch |
| Compaction job consuming broker heap | Broker GC pauses during compaction; latency spikes on unrelated topics | JVM GC logs; `jstat -gcutil <pid>`; correlate with compaction schedule | Reschedule compaction to off-peak; move compaction to offload workers | Run compaction via dedicated offloader; set `compactionThreshold` to limit frequency |
| Schema registry lookup contention | Producer connection time rising for schema-checked topics; CPU on schema registry threads | `rate(pulsar_schema_count[1m])` — look for schema-heavy topics | Cache schemas aggressively; increase schema registry thread pool | Pre-register schemas at topic creation; avoid runtime schema evolution in hot paths |
| Geo-replication consuming inter-cluster bandwidth affecting application WAN traffic | Application cross-region latency rising; replication throughput saturating WAN link | Check WAN utilization from cloud flow logs; `pulsar_replication_throughput_out` | Throttle `replicatorDispatchRateInBytes`; schedule off-peak replication | Provision dedicated WAN circuit or VPN tunnel for Pulsar replication traffic |
| Shared Kubernetes node with memory-hungry pods causing OOM on broker | Broker pod OOM-killed; subscriptions dropped | `kubectl top pod -n pulsar`; check co-located pods on same node | Move broker pod to dedicated node with taints/tolerations | Set Kubernetes `requests` and `limits` for broker; use `PodAffinity` to separate from heavy workloads |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Broker OOM-killed | All producers/consumers on that broker reconnect simultaneously (thundering herd); ZooKeeper watcher floods; topics unload; schema registry connections fail | All tenants on the broker; reconnect storm can cascade to other brokers | `pulsar_broker_topics_count` drops to 0 on affected broker; reconnect storm visible in other brokers' `pulsar_broker_producer_count` spike | Pre-configure bundle anti-affinity; reduce JVM heap pressure with lazy queues; limit concurrent reconnects via broker `connectionLivenessTimeoutMs` |
| ZooKeeper quorum loss | Brokers unable to perform metadata operations; topic ownership lookups fail; new connections rejected; existing subscriptions stall | All Pulsar tenants cluster-wide lose new connections; persistent topic operations freeze | Brokers log `KeeperException: ConnectionLoss`; `pulsar_zookeeper_request_latency_ms` spikes to > 5000ms | Switch metadata store to Oxia/etcd if possible; restore ZK quorum; restart Pulsar brokers after ZK recovery |
| BookKeeper bookie failure (1 of 3) | Write quorum (Qw=2) still achievable; but if 2 fail, producers block on acknowledgement; topics with `writeQuorum=2,ackQuorum=2` fail immediately | Topics where the failed bookie holds pending ledger entries | `pulsar_broker_publish_latency` p99 spikes; bookie `bookkeeper_server_ADD_ENTRY_failures` rising | Increase bookie count before write quorum is unachievable; Pulsar auto-recovers ledger entries via `autorecovery` daemon |
| Network partition between brokers and BookKeeper | Producers receive `BKBookieHandleNotAvailableException`; messages not written to ledger | All write-path operations across all topics on affected brokers | Broker logs `BookieException: Failed to write to bookie`; `pulsar_storage_write_latency` > 500ms | Isolate brokers from accepting new connections; fix network path; brokers recover automatically when BK reconnects |
| Schema registry unavailable | Schema-validated producers fail to connect; consumers with schema checking enabled reject messages | All applications using schema registry — no unschematised producers affected | Broker logs `SchemaException: Failed to fetch schema from registry`; `pulsar_broker_publish_rate` drops for schema-aware producers | Disable schema enforcement temporarily in namespace: `pulsar-admin namespaces set-schema-validation-enforce --disable <ns>` |
| Geo-replication link failure | Messages accumulate in replication cursor backlog; replication lag grows; remote cluster data diverges | Teams relying on cross-region message replication for DR or fan-out | `pulsar_replication_backlog` rising; `pulsar_replication_throughput_out` drops to 0 for affected remote cluster | Disable replication to failed cluster: `pulsar-admin namespaces set-clusters --clusters local <ns>`; re-enable after remote recovery |
| Pulsar Proxy failure | Clients using proxy endpoint lose connectivity; topics themselves remain healthy but unreachable externally | All external clients routing through the proxy; internal-only clients unaffected | `pulsar_proxy_active_connections` drops to 0; client logs `Connection refused: proxy:6650` | Route clients directly to broker address: update `brokerServiceUrl` in client config; scale up proxy |
| Autorecovery daemon crash | Under-replicated ledger entries not re-replicated; extended bookie failure leads to data loss risk | Durability of data written during bookie failure period | `bookkeeper_server_AUDITOR_UNDERREPLICATED_LEDGER_TOTAL` counter growing without decrease | Restart autorecovery: `docker restart bookie-autorecovery`; monitor `bookkeeper_server_REPLICATE_OPS_DELAYED` |
| Compaction failure leaving large topic unconsolidated | Consumer re-reads accumulate; latest-key reads from producers slow; storage grows unbounded | Tenants using compacted topics (key-value semantics) get stale or duplicate reads | `pulsar_compaction_failed_count` increasing; topic `backlogSize` growing despite compaction trigger | Manually trigger compaction: `pulsar-admin topics compact persistent://<tenant>/<ns>/<topic>`; check compaction worker logs |
| Upstream microservice publishing poison messages | Consumers throw deserialisation exceptions; dead-letter topic fills; downstream pipeline stalls | All consumer groups subscribed to the affected topic | Consumer logs `DeserializationException`; DLQ depth rising; processing pipeline downstream shows 0 throughput | Pause consumption: `pulsar-admin topics expire-messages`; purge DLQ; patch producer to validate schema before publish |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Pulsar broker version upgrade (rolling) | During rolling restart, bundle ownership transfer triggers client reconnects; brief publish failures | Immediately per broker restart, full cluster in 5–15 min | Broker logs `Unloading bundle`; client logs `Topic temporarily unavailable`; `pulsar_broker_topics_count` fluctuates | Slow down rolling restart cadence; increase client `operationTimeoutMs`; abort and rollback broker binary |
| Increasing `managedLedgerMaxEntriesPerLedger` | Existing ledgers remain unchanged; new ledgers grow larger; bookie disk pressure changes unexpectedly | Hours to days (next ledger rollover) | Disk usage on bookies increases faster than expected; new ledger sizes visible via `pulsar-admin topics internal-info <topic>` | Revert config in `broker.conf`; restart brokers |
| Enabling message deduplication on busy topic | Producer CPU spike for dedup hash computation; broker memory grows for dedup state cache | Immediately on first produces after enabling | `pulsar_broker_deduplication_cursor_age` growing; producer `publish_latency` p99 rises | Disable deduplication: `pulsar-admin namespaces set-deduplication --disable <ns>` |
| Adding geo-replication to existing namespace | Replication backlog immediately created for all existing messages; bookie write bandwidth spikes as replication catches up | Immediately on namespace policy change | `pulsar_replication_backlog` large at start; WAN bandwidth spike visible in cloud metrics | Throttle replication: `pulsar-admin namespaces set-replicator-dispatch-rate --msg-dispatch-rate 1000 <ns>` |
| Increasing number of partitions on existing partitioned topic | Consumers with manual partition assignment miss new partitions; load imbalance until reassignment | Immediately for manual-assignment consumers; transparent for `TopicsConsumer` | Consumer logs no messages from new partition IDs; `pulsar_producer_msg_publish_rate` uneven across partitions | Rebalance partition assignment in consumer code; use `PatternConsumer` with auto-partition discovery |
| Changing `ackTimeoutMs` to shorter value in consumer config | Messages that previously timed out gracefully now nack and redeliver faster; DLQ fills rapidly if processing is slow | Immediately on consumer restart | `pulsar_consumer_msg_ack_rate` drops; `pulsar_consumer_unacked_messages_count` rises; DLQ depth growing | Revert `ackTimeoutMs`; fix slow processing before shortening timeout |
| Updating BookKeeper `journalMaxSizeMB` to lower value | Journal rolls more frequently; write latency spikes during rolls; `bookie_journal_SYNC_ms` increases | Immediately on bookie restart with new config | `bookie_journal_SYNC_ms` p99 jumps; correlate with journal roll events in bookie logs | Revert `journalMaxSizeMB`; set value appropriate to disk I/O capacity |
| Enabling TLS on broker without updating client truststore | All producers/consumers fail to reconnect after broker restart with TLS; `SSLHandshakeException` in client logs | Immediately on broker restart | Client logs `javax.net.ssl.SSLHandshakeException: PKIX path building failed`; `pulsar_broker_producer_count` drops to 0 | Disable TLS or distribute CA cert to all clients before enabling; roll back broker config |
| Schema evolution with incompatible field removal | Old consumers that expect removed field receive `SchemaException` | Immediately when new producer version connects and registers new schema | Broker schema compatibility check errors in logs; consumer throws `IncompatibleSchemaException` | Register schema with `BACKWARD` compatibility check; never remove required fields; deploy consumers before producers |
| Upgrading ZooKeeper alongside Pulsar | ZK data format migration causes temporary quorum loss; Pulsar brokers disconnected from metadata | Immediately during ZK upgrade window | Broker logs `KeeperException`; all topic operations freeze; `pulsar_broker_topics_count` drops | Maintain ZK rolling upgrade with quorum at all times; test in staging; have broker restart runbook ready |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Broker split-brain due to ZooKeeper partition | Two brokers claim ownership of the same topic bundle | Messages published to both brokers, double-consuming possible; duplicate message IDs | Data duplication; consumer idempotency broken | Restore ZK connectivity; brokers will negotiate ownership via ZK; dedup at consumer layer until resolved |
| Replication cursor lag causing stale reads on remote cluster | `pulsar-admin topics stats persistent://<ns>/<topic> | jq '.replication'` — check `replicationBacklog` | Remote cluster consumers reading days-old messages while primary is current | DR failover to remote cluster serves stale data | Monitor `pulsar_replication_backlog` continuously; set alert at 10000 messages; pause new replication before failover |
| BookKeeper ensemble degradation (1 bookie down) | `pulsar-admin bookies list-bookies` — verify all bookies `State: RW` | New ledger entries written with reduced ensemble; autorecovery attempting to re-replicate | Risk of data loss if 2nd bookie fails before re-replication completes | Add new bookie immediately; verify autorecovery progress: `bookkeeper shell listunderreplicated` |
| Subscription cursor desync after broker crash | `pulsar-admin topics stats <topic> | jq '.subscriptions'` — compare `msgBacklog` across subscriptions | One subscription has much larger backlog than others; consumers processing old messages | Out-of-order processing; downstream state machines may receive events out of sequence | Reset cursor to latest if old messages are safe to skip: `pulsar-admin topics reset-cursor --subscription <subscription> --time <timestamp> <topic>` |
| Compacted topic serving stale key-value snapshot | `pulsar-admin topics compaction-status <topic>` shows last compaction timestamp old | Consumers using `readCompacted=true` get stale value for recently updated keys | Incorrect state served to consumers depending on compacted view | Force compaction: `pulsar-admin topics compact <topic>`; wait for `Status: Done` |
| MessageId ordering broken across partitions | No direct detection command; application-level ordering violation observed | Messages from partition 0 and partition 2 arrive out of global order at consumer | Applications requiring global total order receive incorrect sequence | Redesign for per-partition ordering only; use single-partition topic for total-order requirements |
| Schema version mismatch after producer redeploy | `pulsar-admin schemas get <topic>` — compare schema version between old and new producers | Consumers using auto-schema deserialisation may silently drop fields added in new schema | Data loss in fields added by new producer version | Enforce schema compatibility mode: `pulsar-admin namespaces set-schema-compatibility-strategy --compatibility FULL <ns>` |
| Duplicate messages after producer reconnect without deduplication | No built-in detection; check consumer processing logs for duplicate business IDs | Consumer sees same message twice within short window after producer reconnect | Business logic processes same event twice; idempotency required | Enable deduplication: `pulsar-admin namespaces set-deduplication --enable <ns>`; implement consumer-side idempotency |
| Namespace policy drift between brokers during partial config push | `pulsar-admin namespaces get-retention <ns>` on different brokers returns different values | Some brokers apply new retention; others still apply old policy; uneven storage behaviour | Inconsistent message retention; some messages deleted sooner than expected | Re-apply namespace policy to force ZK metadata sync: `pulsar-admin namespaces set-retention --size -1 --time 168h <ns>` |
| Tiered storage offload leaving gaps (partial offload failure) | `pulsar-admin topics offload-status <topic>` shows `Error` | Some ledger segments offloaded to S3; others still on BookKeeper; segments in failed state unreadable | Long-term message reads fail for offloaded window; topic restore incomplete | Retry offload: `pulsar-admin topics offload --size-threshold 1G <topic>`; check S3 bucket permissions |

## Runbook Decision Trees

### Decision Tree 1: Consumer backlog growing uncontrollably
```
Is the producer publish rate spiking? (check: pulsar-admin topics stats <topic> | jq '.msgRateIn')
├── YES → Is this expected traffic surge?
│         ├── YES → Scale consumers: kubectl scale deployment <consumer> --replicas=<N>
│         │         Check max parallelism: pulsar-admin topics get-max-consumers <topic>
│         └── NO  → Runaway producer → Identify: pulsar-admin topics stats <topic> | jq '.publishers'
│                   Throttle producer: pulsar-admin namespaces set-publish-rate <ns> --msg-publish-rate <N>
└── NO  → Producers normal → Are consumers actively receiving messages?
          (check: pulsar-admin topics stats <topic> | jq '.subscriptions.<name>.msgRateOut')
          ├── Rate is 0 → Consumer group stalled → Check consumer logs for errors
          │               Are consumers connected? pulsar-admin topics stats <topic> | jq '.subscriptions.<name>.consumers | length'
          │               ├── 0 consumers → Consumers crashed → Restart consumer deployment
          │               └── Consumers connected but rate=0 → Negative ack storm or flow control
          │                   Check: pulsar-admin topics stats <topic> | jq '.subscriptions.<name>.msgRateRedeliver'
          │                   High redelivery → Fix poison message: pulsar-admin topics skip <topic> -s <sub> -n 1
          └── Rate > 0 but backlog growing → Throughput insufficient → Scale consumers
                Verify partition count: pulsar-admin topics get-partitioned-topic-metadata <topic>
                Increase partitions if at max consumer count: pulsar-admin topics update-partitioned-topic <topic> -p <N>
```

### Decision Tree 2: Broker unavailable or refusing connections
```
Are all brokers reporting healthy? (check: pulsar-admin brokers list; pulsar-admin brokers healthcheck)
├── YES → Is ZooKeeper healthy? (check: echo ruok | nc zk1 2181; echo mntr | nc zk1 2181 | grep zk_server_state)
│         ├── ZK unhealthy → ZooKeeper leader election failure → Check ZK logs on all nodes
│         │                  Restart ZK follower: systemctl restart zookeeper (on follower only first)
│         └── ZK healthy → Check BookKeeper: bookkeeper shell simpletest --numEntries 10 --ledgerId <id>
│                          ├── BK write failure → Disk full or bookie down → df -h /var/lib/bookkeeper
│                          └── BK healthy → Broker config or auth issue → check broker.log for WARN/ERROR
└── NO  → Which brokers are down? (check: kubectl get pods -n pulsar -l component=broker)
          ├── All brokers down → Cluster-wide failure → Check ZooKeeper first (brokers depend on ZK for metadata)
          │                      Restart ZK ensemble; then restart brokers one at a time
          └── Some brokers down → Check failing broker: kubectl logs -n pulsar <broker-pod> --previous | tail -50
                                  ├── OOM killed → Increase broker heap: PULSAR_MEM="-Xms4g -Xmx4g"
                                  └── Storage error → Check bookie count: bookkeeper shell listbookies -rw
                                      Insufficient bookies → Start additional bookie pods
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Retention policy misconfiguration storing all messages indefinitely | Storage growing without bound; BookKeeper disk at 80%+ | `pulsar-admin namespaces get-retention <ns>` — shows unlimited retention | BookKeeper disk fill; broker OOM loading large ledgers | Set retention limit: `pulsar-admin namespaces set-retention <ns> --size 10G --time 7d` | Enforce retention policy at namespace creation via IaC; alert at 60% BK disk usage |
| Unacked messages accumulating in subscriptions | Subscription backlog in billions; disk consumed by undelivered messages | `pulsar-admin topics stats <topic> \| jq '.subscriptions[].msgBacklog'` | Ledger pinning preventing BookKeeper garbage collection; disk exhaustion | Enable TTL: `pulsar-admin namespaces set-message-ttl <ns> --messageTTL 86400`; force-skip stuck sub | Always set `messageTTL` per namespace; monitor backlog per subscription |
| Dead consumer leaving subscription open | Ledgers not GC'd; storage grows past expected retention | `pulsar-admin topics stats <topic> \| jq '.subscriptions[].consumers \| length'` — shows 0 for orphaned sub | All ledgers pinned until subscription deleted | Delete orphaned subscription: `pulsar-admin topics unsubscribe <topic> -s <sub>` | Monitor subscription consumer count; alert if subscription is 0-consumer for > 1 hour |
| Geo-replication replaying full backlog to new DR cluster | Network egress spike; source cluster CPU/IO elevated | `pulsar-admin topics stats <topic> \| jq '.replication'` — check replicatorBacklog | Source broker overwhelmed; egress costs spike | Throttle replication rate: `pulsar-admin namespaces set-replicator-dispatch-rate <ns> --msg-dispatch-rate 1000` | Set replication rate limits at namespace level before enabling geo-replication |
| Tiered storage offload job filling object store | S3/GCS object store costs rising faster than message volume | `pulsar-admin topics offload-status <topic>` — check offloaded bytes; cloud billing dashboard | Unexpected cloud storage bill | Set offload threshold: `pulsar-admin namespaces set-offload-threshold <ns> --size 10G` | Configure offload thresholds before enabling; set lifecycle policies on offload bucket |
| Compaction running too frequently on large topics | CPU and disk I/O high on broker; other topics slowed | `pulsar-admin topics compaction-status <topic>` — check if compaction is always running | Broker I/O saturation; degraded publish latency for all topics | Increase compaction trigger threshold: `pulsar-admin topics set-compaction-threshold <topic> --threshold 100M` | Set compaction threshold based on topic size; avoid compaction on high-frequency topics |
| Schema registry storing excessive schema versions | Schema store growing; schema validation adding latency | `pulsar-admin schemas get <topic>` — check schema version count | Increased CPU for schema validation; ZK metadata overhead | Delete old schema versions: `pulsar-admin schemas delete <topic>` (after confirming no consumers need them) | Enable schema compatibility checking; limit schema evolution frequency |
| Function worker running compute-heavy functions | Function worker CPU 100%; broker on same node affected | `pulsar-admin functions stats <function>` — check processedSuccessfully and exceptions | Noisy neighbor effect on brokers sharing same JVM | Move function workers to dedicated node: separate `functionWorkerEnabled=false` on broker nodes | Deploy Pulsar Functions workers on separate node pool; set CPU limits per function instance |
| Cursor position lag in BookKeeper causing excessive ledger reads | High `ledger_read_requests` metric; broker CPU elevated for old subscriptions | `bookkeeper shell ledgermetadata -ledgerid <id>` — compare cursor position to latest | BookKeeper I/O saturation; slow message delivery | Reset cursor to latest: `pulsar-admin topics reset-cursor <topic> -s <sub> --time 0` (loses old messages) | Monitor cursor lag per subscription; alert on subscriptions with cursor > 24h behind |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot partition (single topic partition receiving disproportionate traffic) | One broker's CPU/network saturated while others idle; latency on hot partition | `pulsar-admin topics stats persistent://<ns>/<topic> \| jq '.partitions \| to_entries[] \| {partition:.key, msgRateIn:.value.msgRateIn}'` | Producer key routing sending all messages to one partition | Rebalance producers with `RoundRobinPartition` routing; increase partition count: `pulsar-admin topics update-partitioned-topic <topic> --partitions 12` |
| Connection pool exhaustion to broker | Producers/consumers get `Too many connections` error; broker rejects new connections | `netstat -tn \| grep ':6650' \| grep ESTABLISHED \| wc -l`; broker log: `grep 'too many connections' /var/log/pulsar/broker.log` | Client creating new connection per request instead of reusing; broker `maxConcurrentLookupRequest` hit | Enable connection pooling in Pulsar client; reduce `ioThreads` in client config; increase `maxConcurrentLookupRequest` in broker.conf |
| BookKeeper GC pressure causing write latency spikes | Pulsar produce latency p99 > 1s periodically; corresponds to full GC events in bookie JVM | `bookkeeper shell bookieformat --nonInteractive 2>&1 \| grep -i gc`; JVM GC log: `grep 'Full GC' /var/log/bookkeeper/gc.log` | Bookie JVM heap sized too small for ledger cache; frequent full GC | Increase bookie heap: `BOOKIE_MEM="-Xms8g -Xmx8g -XX:MaxDirectMemorySize=8g"`; tune G1GC settings |
| Broker thread pool saturation from too many topics | Message dispatch latency increasing; broker CPU high but throughput flat | `curl http://broker:8080/metrics \| grep pulsar_broker_components_executor_queue_size` | Too many topics dispatching concurrently; single-threaded dispatch per subscription | Reduce topics per broker; enable topic-level rate limiting: `pulsar-admin topics set-publish-rate <topic> --msg-publish-rate 10000` |
| Slow acknowledgment causing redelivery storm | Consumer receives same messages repeatedly; redelivery counter rising | `pulsar-admin topics stats <topic> \| jq '.subscriptions.<sub>.msgRedeliver'` rising | Consumer ack timeout too low; processing slower than `ackTimeout`; negative acks | Increase `ackTimeout` in consumer config; use `nackRedeliveryDelay` to space redeliveries; scale consumer instances |
| CPU steal on shared BookKeeper nodes | Bookie write latency high despite low local CPU; steal time visible | `mpstat 1 5 \| grep -i steal`; `sar -u 1 5`; compare bookie write latency to steal % | Noisy neighbor VMs on same hypervisor host | Move BookKeeper to dedicated nodes; use CPU pinning; migrate to bare metal for latency-sensitive workloads |
| ZooKeeper lock contention on metadata updates | Topic creation/deletion slow; broker log shows `ZooKeeper session timeout` | `echo mntr \| nc zk1 2181 \| grep zk_outstanding_requests`; `echo stat \| nc zk1 2181 \| grep 'outstanding'` | Too many concurrent metadata operations; ZK single-threaded for writes | Batch topic operations; migrate to BookKeeper metadata store (oxia) as ZK replacement; scale ZK ensemble |
| Serialization overhead from large message payloads | Produce throughput lower than expected; broker CPU high relative to msg/s | `pulsar-admin topics stats <topic> \| jq '.msgThroughputIn / .msgRateIn'` — bytes/msg is large | Large messages (> 1MB) without batching compression | Enable compression: set `compressionType: LZ4` or `ZSTD` in producer config; use message batching |
| Batch size misconfiguration causing under-batching | High message rate but many small network writes; broker shows high `msgRateIn` but low `msgThroughputIn` | `pulsar-admin topics stats <topic> \| jq '.publishers[].averageMsgSize'` very small | Producer `batchingMaxMessages=1` or `batchingMaxPublishDelay` too low | Set `batchingMaxMessages=1000` and `batchingMaxPublishDelayMicros=5000` in producer config |
| Downstream dependency latency (ZK or BK) causing end-to-end latency | Pulsar produce/consume latency high; broker not CPU-bound | `pulsar-admin broker-stats load-report \| jq '.msgThroughputIn'`; time: `bookkeeper shell simpletest --numEntries 100` for BK latency | Cascading latency from BookKeeper or ZooKeeper slow responses | Investigate ZK and BK health independently; check disk I/O on bookie nodes: `iostat -x 1 5` |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on broker | Clients fail with `SSL handshake failed: certificate has expired`; new connections rejected | `openssl x509 -enddate -noout -in /etc/pulsar/tls/broker.cert.pem`; `pulsar-client produce <topic> --messages test 2>&1 \| grep -i tls` | Broker TLS cert not renewed before expiry | Rotate cert, update `tlsCertificateFilePath` in broker.conf, restart broker; use cert-manager for automated rotation |
| mTLS client cert rotation failure | Producers/consumers rejected with `certificate not trusted`; new certs not propagated | `openssl verify -CAfile /etc/pulsar/tls/ca.cert.pem /etc/pulsar/tls/client.cert.pem` | CA bundle updated but old client certs not yet replaced; or new CA not trusted by broker | Update broker `tlsTrustCertsFilePath` with new CA bundle before rotating client certs; rolling rotation order |
| DNS resolution failure for broker discovery | Pulsar client fails with `Failed to look up service url`; retry loop | `dig <pulsar-service-url>` from client host; `nslookup pulsar://broker:6650` | Cluster DNS misconfiguration; CoreDNS failure | Fix DNS record; use static IP in `serviceUrl` as fallback; check `ndots` setting in pod DNS config |
| TCP connection exhaustion on broker | Broker refuses connections; `netstat -tn` shows many TIME_WAIT on port 6650 | `ss -s \| grep TIME-WAIT`; broker metric: `pulsar_broker_net_in_bytes_total` plateaus | Too many short-lived connections without keepalive; TCP TIME_WAIT buildup | Enable TCP keepalive: `sysctl -w net.ipv4.tcp_keepalive_time=60`; reuse connections in Pulsar client |
| Load balancer misconfiguration breaking persistent connections | Consumers repeatedly reconnect; message delivery pauses periodically | `pulsar-admin topics stats <topic> \| jq '.subscriptions.<sub>.consumers \| length'` fluctuating | LB idle timeout shorter than Pulsar keepalive interval; LB terminating long-lived connections | Set LB idle timeout > `keepAliveIntervalSeconds` (default 30s) in Pulsar client; use NLB instead of ALB for Pulsar |
| Packet loss causing producer retry storm | Producer latency high; `sendTimeout` exceptions in logs; duplicate messages possible | `ping -c 100 <broker-ip>`; `mtr --report <broker-ip>` | Network path packet loss between producer and broker | Investigate network path; reduce `sendTimeout` to fail fast; enable idempotent producer to prevent duplicates on retry |
| MTU mismatch on geo-replication link | Large messages fail on replication; small messages succeed; replication lag grows | `tcpdump -i eth0 -n host <remote-broker> \| grep 'fragmented'`; test: `ping -M do -s 8972 <remote-broker>` | MTU set differently on cross-DC network path; message fragmentation dropped | Align MTU on all network interfaces in replication path; enable jumbo frames consistently; lower message size limit |
| Firewall rule change blocking broker-to-broker replication | Geo-replication lag growing; source topic stats show `replicatorBacklog` rising | `pulsar-admin topics stats <topic> \| jq '.replication'`; `telnet <remote-broker> 6651` | Firewall rule blocking inter-cluster replication port (default 6651) | Restore firewall rule; verify: `nc -zv <remote-broker> 6651`; check replication recovery: `pulsar-admin topics stats` |
| SSL handshake timeout from cipher suite mismatch | TLS connection attempts time out rather than fail fast; broker log shows `SSL_ERROR_RX_RECORD_TOO_LONG` | `openssl s_client -connect broker:6651 -tls1_2 -cipher 'ECDHE-RSA-AES256-GCM-SHA384'`; broker log: `grep ssl /var/log/pulsar/broker.log` | Client and broker negotiate different TLS versions or cipher suites | Align TLS configuration: set `tlsProtocols: TLSv1.2,TLSv1.3` and matching `tlsCiphers` in broker.conf and client |
| Connection reset during ZooKeeper session restoration | Producers fail mid-send with `Connection reset by peer`; broker restarts ZK session | `echo stat \| nc zk1 2181 \| grep 'Latency min/avg/max'`; broker log: `grep 'Lost connection to ZooKeeper' /var/log/pulsar/broker.log` | ZooKeeper session expired; broker reinitializing all topic metadata | Increase ZK `sessionTimeoutMs` in broker.conf; ensure ZK ensemble is responsive; use ZK observers for read scaling |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of broker JVM | Broker pod disappears; `kubectl describe pod \| grep OOMKilled`; producers/consumers reconnect | `dmesg -T \| grep -i 'oom\|killed' \| grep -i pulsar`; `kubectl get pod -n pulsar -l component=broker \| grep Restart` | Increase broker heap: `PULSAR_MEM="-Xms8g -Xmx8g"`; reduce topic count per broker; enable broker load shedding | Set broker memory limits with headroom; alert when JVM heap usage > 80%; enable G1GC |
| Disk full on BookKeeper data partition | BK writes fail; broker logs `BKException: Not enough bookies`; all write operations blocked | `df -h /var/lib/bookkeeper`; `du -sh /var/lib/bookkeeper/current/` | Add bookie nodes; remove old ledgers: `bookkeeper shell deleteledger -ledgerid <id>`; extend disk on existing node | Alert at 70% bookie disk; configure `diskUsageThreshold=0.95` in bookie.conf; use auto-tiered storage |
| Disk full on broker log partition | Broker log writes fail; JVM may crash if log4j blocks; disk alarm in metrics | `df -h /var/log/pulsar`; `ls -lh /var/log/pulsar/*.log` | Rotate logs: `journalctl --vacuum-size=1G`; clear old broker gc logs; reduce log verbosity level | Set log rotation policy in `log4j2.yaml`; keep logs on separate partition from data |
| File descriptor exhaustion on broker | Broker stops accepting connections; `too many open files` in broker logs | `lsof -p $(pgrep -f pulsar) \| wc -l`; `cat /proc/$(pgrep -f pulsar)/limits \| grep 'open files'` | Each ledger and topic consumes file descriptors; ledger cache not bounded | Increase `LimitNOFILE=1048576` in systemd unit; set `openFileLimit` in bookie.conf; restart broker | 
| Inode exhaustion on bookie journal partition | Journal file creation fails; `no space left on device` despite disk space available | `df -i /var/lib/bookkeeper/journal`; `find /var/lib/bookkeeper/journal -type f \| wc -l` | Too many small journal segment files not being cleaned up | Restart bookie to trigger journal cleanup; manually delete old journal files after confirming ledger data is in ledger storage | Use XFS for bookie partitions (superior inode density); monitor `df -i` alongside `df -h` |
| CPU steal/throttle on shared broker VM | Broker dispatch latency rising; CPU appears low in container but messages slow | `kubectl top pod -n pulsar -l component=broker`; `node_cpu_seconds_total{mode="steal"}` on broker node | CPU throttling from container limits or noisy neighbor | Remove CPU limit (keep only request) for broker pods; move to dedicated node pool | Set `resources.requests.cpu = resources.limits.cpu`; use Guaranteed QoS class for brokers |
| Swap exhaustion on BookKeeper node | Bookie write latency jumps to seconds; system thrashing; disk I/O on swap partition | `free -h`; `vmstat 1 5 \| awk '{print $7}'` (si/so columns) | Bookie ledger cache memory exceeded RAM; OS swapping pages out | Disable swap: `swapoff -a`; reduce `dbStorage.writeCacheMaxSizeMb` in bookie.conf; restart bookie | Never run BookKeeper on nodes with swap; set bookie memory limits below node RAM |
| ZooKeeper ephemeral node limit (32K children) | Topic creation fails with `KeeperException: NodeChildren is too large`; ZK error in broker logs | `echo stat \| nc zk1 2181 \| grep -i 'node count'`; `zkCli.sh -server zk1:2181 ls /managed-ledgers \| wc -l` | Too many topics creating ZK ephemeral nodes under same parent path | Migrate to BookKeeper metadata service; consolidate topics; delete unused topic ZK nodes | Monitor ZK node count; migrate to oxia/BK metadata store for >10K topics |
| Network socket buffer exhaustion on broker | Large message throughput stalls; `send buffer overflow` in kernel logs | `sysctl net.core.wmem_max net.core.rmem_max`; `ss -tnp \| grep ':6650' \| awk '{print $3}'` | Default socket buffer too small for high-throughput Pulsar workloads | `sysctl -w net.core.wmem_max=16777216 net.core.rmem_max=16777216`; persist in `/etc/sysctl.d/` | Pre-configure socket buffers in node configuration management; test with sustained high-throughput load |
| Ephemeral port exhaustion from geo-replication connections | Replication fails with `cannot assign requested address`; replicator backlog grows | `ss -s \| grep TIME-WAIT`; `sysctl net.ipv4.ip_local_port_range` | Geo-replication opening too many short-lived TCP connections | Enable port reuse: `sysctl -w net.ipv4.tcp_tw_reuse=1`; reduce replication connections with persistent TCP sessions | Configure `net.ipv4.ip_local_port_range=1024 65535`; use HTTP/2 for replication channels |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation: producer duplicate on retry causes double publish | Consumer processing same logical event twice; message ID differs but content identical | `pulsar-admin topics stats <topic> \| jq '.publishers[].producerName'`; enable deduplication: check `deduplicationEnabled` in namespace policies | Duplicate records in downstream database; incorrect counters | Enable deduplication: `pulsar-admin namespaces set-deduplication public/default --enable`; set `sendTimeout` and idempotent producer |
| Saga partial failure: Pulsar Function chain succeeds on step 1, fails on step 2 | Message consumed by Function 1 but never published to step 2 output topic; downstream consumer sees gap | `pulsar-admin functions stats <fn1> \| jq '.instances[].metrics.processedSuccessfully'`; compare step 1 output topic: `pulsar-admin topics stats <step2-topic>` | Business transaction partially applied; data inconsistency between services | Implement compensating function that publishes to a rollback topic; use Pulsar Transactions for atomic produce+ack |
| Message replay corrupting compacted topic state | Compacted topic key shows wrong final value after replay; stale tombstones | `pulsar-admin topics compaction-status <topic>`; compare consumer read with `--subscription-initial-position Earliest` vs compacted value | Incorrect state for event-sourced entities; wrong read model after compaction | Delete and re-trigger compaction: `pulsar-admin topics compact <topic>`; validate key ordering before compaction |
| Cross-service deadlock via paired request-reply topics | Service A waiting on reply from B; B is blocked waiting on message from A; both topics have 0 consumers processing | `pulsar-admin topics stats <request-topic> \| jq '.subscriptions.<sub>.msgBacklog'` — both sides growing | Complete halt of cross-service workflow; cascading timeout | Introduce timeout + dead-letter topic for unanswered replies; break circular dependency by using async notification pattern |
| Out-of-order event processing due to partition reassignment | Event sequence violated after broker restart; consumers on different partitions see misordered events | `pulsar-admin topics stats persistent://<ns>/<topic>-partition-<n> \| jq '.subscriptions.<sub>.msgBacklog'` per partition; check partition ownership: `pulsar-admin topics lookup <topic>-partition-0` | Incorrect business logic outcomes for order-sensitive workflows | Use `Key_Shared` subscription to guarantee per-key ordering; or use single-partition topic for strictly ordered data |
| At-least-once delivery duplicate after consumer crash mid-ack | Consumer crashes after processing but before ack; message redelivered to new consumer instance | `pulsar-admin topics stats <topic> \| jq '.subscriptions.<sub>.msgRedeliver'` count rising; check consumer logs for crash | Duplicate processing of payment/notification events | Enable idempotency in consumer logic (database upsert, deduplication key); use Pulsar transactions for consume+produce atomicity |
| Compensating transaction failure: rollback topic consumer not running | Saga rollback message published but no consumer reading rollback topic; partial rollback never executed | `pulsar-admin topics stats <rollback-topic> \| jq '.subscriptions.<sub>.msgBacklog'`; `pulsar-admin topics stats <rollback-topic> \| jq '.subscriptions.<sub>.consumers \| length'` | Partial forward + no rollback = inconsistent state | Deploy rollback consumer immediately; process backlog manually; add alerting on rollback topic backlog > 0 |
| Distributed lock expiry mid-operation via Pulsar exclusive producer | Exclusive producer lock expires during slow processing; second producer takes lock and publishes; ordering broken | Broker log: `grep 'Exclusive producer' /var/log/pulsar/broker.log \| grep -i expired`; `pulsar-admin topics stats <topic> \| jq '.publishers \| length'` | Concurrent writes to exclusive topic; logical ordering broken | Increase producer lock timeout in client; implement application-level leader election rather than relying on Pulsar exclusive producer timeout |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: tenant with high-rate topic monopolizing broker thread pool | `pulsar-admin broker-stats topics \| jq '.[] \| select(.msgRateIn > 100000)'` — one tenant's topic dominating; broker CPU 100% | Other tenants' producers throttled; consumers see dispatch latency rising | Set publish rate limit on noisy topic: `pulsar-admin topics set-publish-rate persistent://<ns>/<topic> --msg-publish-rate 50000` | Configure namespace-level rate limits: `pulsar-admin namespaces set-publish-rate public/<noisy-ns> --msg-publish-rate 100000`; implement broker load balancing |
| Memory pressure from adjacent tenant's deep message backlog | `pulsar-admin topics stats <topic> \| jq '.storageSize'` very large for one namespace; broker JVM heap high | Other tenants' topics have higher GC pressure; message dispatch latency rises | Enable backlog quota on noisy namespace: `pulsar-admin namespaces set-backlog-quota public/<noisy-ns> --limit 10G --policy producer_request_hold` | Set cluster-level backlog quota; deploy more broker nodes; load-shed by moving noisy tenant namespace to dedicated broker |
| Disk I/O saturation from single tenant's topic ledger writes | `iostat -x 1 5` on BookKeeper node shows disk util 100% correlated with one tenant's high-throughput topic | All tenants' write latency increases; bookie write timeout errors | Throttle tenant's ILP publish rate via namespace policy; migrate bookie to SSD | Move noisy tenant's namespace to dedicated bookie set using placement policy: `pulsar-admin namespaces set-persistence public/<ns> --bookkeeper-ensemble 3 --bookkeeper-ack-quorum 2 --bookkeeper-write-quorum 2` with isolated bookie group |
| Network bandwidth monopoly from geo-replication for one tenant | `pulsar-admin topics stats <topic> \| jq '.replication'` shows replicator backlog for one namespace consuming all replication bandwidth | Other tenants' geo-replication lags behind; disaster recovery SLO violated | Set replication rate limit: `pulsar-admin namespaces set-replicator-dispatch-rate public/<noisy-ns> --msg-dispatch-rate 50000` | Configure per-namespace replication rate limits; deploy dedicated replication channel for high-volume tenants |
| Connection pool starvation from single tenant's producer pool | `netstat -tn \| grep ':6650' \| wc -l` near broker `maxConcurrentLookupRequest`; other tenants fail lookup requests | New producers/consumers from other tenants fail with `too many requests`; topic ownership lookups time out | Kill idle connections from offending tenant: `pulsar-admin topics terminate persistent://<noisy-ns>/<topic>` to force reconnect with limits | Set `maxConcurrentLookupRequest=50000` in broker.conf; add per-client-IP connection rate limit via network policy |
| Quota enforcement gap: no namespace-level message TTL | One tenant's topic accumulating messages indefinitely; disk fills for all tenants | All BookKeeper nodes fill disk; cluster-wide disk alarm; all tenants blocked | Set TTL immediately: `pulsar-admin namespaces set-message-ttl public/<ns> --messageTTL 86400` | Enforce default namespace TTL policy at cluster creation; add admission controller that requires TTL for new namespaces |
| Cross-tenant data leak risk via shared subscription names | Consumer from tenant A subscribes to shared subscription intended for tenant B due to identical subscription name | Tenant A receives messages intended for tenant B; subscription cursor corrupted for tenant B | `pulsar-admin topics stats <topic> \| jq '.subscriptions \| keys'` — check for unexpected subscription names | Namespace isolation: deploy one namespace per tenant; enforce naming conventions for subscriptions; revoke produce/consume permissions across tenant namespaces |
| Rate limit bypass via multiple producer connections | Single logical producer opening 100 connections each below per-connection rate limit; bypassing namespace rate policy | Effective rate 100× the per-connection limit; broker overloaded; other tenants throttled | `pulsar-admin topics stats <topic> \| jq '.publishers \| length'`; identify high connection count from single client IP | Implement per-IP producer count limit via network policy; set `maxProducersPerTopic` in namespace: `pulsar-admin namespaces set-max-producers-per-topic public/<ns> --max-producers 5` |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure from broker JMX exporter | `pulsar_broker_msg_rate_in` absent from Prometheus; no alert fires | JMX exporter pod crashes or broker JMX port changes after upgrade | `curl http://prometheus:9090/api/v1/query?query=absent(pulsar_broker_msg_rate_in)` returns value | Add `absent(pulsar_broker_msg_rate_in)` alert; deploy sidecar JMX exporter with liveness probe; monitor `up{job="pulsar-broker"}` |
| Trace sampling gap missing slow consumer incidents | P99 consumer latency high in Prometheus but no distributed trace showing the slow path | Low sampling rate (0.1%) drops most consumer dispatch traces; only P50 traces captured | `pulsar-admin topics stats <topic> \| jq '.subscriptions.<sub>.msgRateOut'` vs `msgRateIn` to detect consumer lag without traces | Enable OpenTelemetry in Pulsar broker with 10% sample rate; use exemplars to link `pulsar_consumer_msg_backlog` metric to trace ID |
| Log pipeline silent drop for BookKeeper write errors | Bookie write failures never appear in alerting despite `bookkeeper_server_ADD_ENTRY_EXCEPTION_COUNT` rising | Bookie log shipped via Fluentd with buffer overflow; log lines dropped silently; no alert on log drop rate | `curl http://prometheus:9090/api/v1/query?query=rate(bookkeeper_server_ADD_ENTRY_EXCEPTION_COUNT[5m])` directly from bookie metrics | Add Prometheus alert on `rate(bookkeeper_server_ADD_ENTRY_EXCEPTION_COUNT[5m]) > 0`; bypass log pipeline for critical metrics |
| Alert rule misconfiguration for consumer backlog | Backlog alert never fires because `pulsar_storage_backlog_size` label selector uses wrong namespace format | Alert uses `namespace="public/default"` but Pulsar metric label is `namespace="public_default"` (slash replaced with underscore) | `curl 'http://prometheus:9090/api/v1/series?match[]=pulsar_storage_backlog_size' \| jq '.[0] \| keys'` to inspect actual label names | Run alert expression via `promtool query instant` against live Prometheus; add label normalization in relabeling config |
| Cardinality explosion from dynamic topic names blinding dashboards | Grafana Pulsar dashboard loads very slowly; topic-level panels time out | Producers using per-request topic names (e.g., `orders-<uuid>`) creating millions of unique topic time series | `topk(10, count by (topic)({__name__=~"pulsar_.*"}))` to find high-cardinality topics | Enforce topic naming conventions; add `metric_relabel_configs` to drop or aggregate per-UUID topic metrics; use topic-level recording rules |
| Missing Pulsar Functions health endpoint | Pulsar Function worker crashes silently; function input topic backlog grows; no alert fires | Function worker `/health` endpoint not scraped; no liveness probe configured | `pulsar-admin functions stats <function> \| jq '.instances[].status'`; check: `curl http://function-worker:6750/health` | Add function worker scrape to Prometheus; alert on `absent(pulsar_function_processed_successfully_total{function="<fn>"})`; deploy liveness probe |
| Instrumentation gap in geo-replication critical path | Geo-replication lag growing but no fine-grained metric showing which broker is the bottleneck | `pulsar_replication_backlog` only shows end-to-end lag; per-broker replication throughput not exposed | `pulsar-admin topics stats <topic> \| jq '.replication \| to_entries[] \| {cluster:.key, backlog:.value.replicationBacklog}'` per cluster | Add custom Prometheus metric scraping `pulsar-admin topics stats` per topic/cluster; use Pulsar built-in replication metrics if available in version |
| Alertmanager outage causing silent ZooKeeper failure | ZK quorum lost; broker metadata operations fail; no PagerDuty page sent | Alertmanager pod OOMKilled; Prometheus routes alerts but delivery fails silently; no dead-man's switch | `curl http://prometheus:9090/api/v1/alertmanagers` — if empty, all AMs down; check `echo ruok \| nc zk1 2181` for ZK health | Configure dead-man's switch: always-firing alert to external heartbeat monitor; deploy Alertmanager in HA mode with 3 replicas |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor Pulsar version upgrade rollback (e.g., 3.1 → 3.2) | Broker fails to start; BookKeeper ledger format incompatible; error in broker.log | `grep -E 'error\|Exception\|fatal' /var/log/pulsar/broker.log \| tail -30`; `kubectl rollout status deployment/pulsar-broker` | `kubectl rollout undo deployment/pulsar-broker`; verify: `pulsar-admin brokers list` returns all brokers | Test upgrade in staging with prod-mirrored BookKeeper data; read Pulsar upgrade guide; keep previous image tag in deployment |
| Major version upgrade rollback (e.g., 2.x → 3.x) | ZooKeeper metadata schema change causes old brokers to fail reading new metadata format | `zkCli.sh -server zk:2181 get /admin/clusters/<cluster>` — check schema version in metadata | Stop all new-version brokers; restore ZooKeeper snapshot: `cp /tmp/zk_snapshot_*/ /var/lib/zookeeper/version-2/`; restart old-version brokers | Always snapshot ZooKeeper before major upgrade: `zkCli.sh -server zk:2181 snapshot`; test ZK metadata schema compatibility |
| Schema migration partial completion (Pulsar Schema Registry) | Producer using new schema; consumers still on old schema; messages deserialization failures | `pulsar-admin schemas get persistent://<ns>/<topic> \| jq '.type'`; consumer logs: `grep 'schema\|deserializ' /var/log/app/consumer.log` | Re-publish old schema: `pulsar-admin schemas upload persistent://<ns>/<topic> -f old-schema.json`; roll back producer to old schema | Use `BACKWARD` or `FULL_TRANSITIVE` schema compatibility policy; test schema migration in staging; deploy consumers before producers |
| Rolling upgrade version skew between broker and bookie | Broker on 3.2 writes ledgers with new format; old bookie on 3.1 cannot read new format; `BKException: BookieHandleNotAvailableException` | `bookkeeper shell bookieformat --nonInteractive 2>&1 \| grep version`; broker log: `grep BKException /var/log/pulsar/broker.log` | Pause rolling upgrade; downgrade newly upgraded brokers to match bookie version | Always upgrade BookKeeper before Pulsar brokers; maintain N-1 compatibility window; test mixed-version cluster in CI |
| Zero-downtime migration to new namespace gone wrong | Topic migration with geo-replication creates duplicate consumers; message processed twice | `pulsar-admin topics stats <old-topic> \| jq '.subscriptions'`; `pulsar-admin topics stats <new-topic> \| jq '.subscriptions'` — both have active consumers | Pause consumers on old topic; drain old topic; then cut over consumers to new topic | Use Pulsar shadow topics feature if available; implement dual-write then drain-and-switch pattern; never run consumers on both topics simultaneously |
| Config format change breaking old broker nodes | `broker.conf` field renamed in new version; broker starts but silently ignores renamed field; old behavior persists | `grep 'Unrecognized configuration' /var/log/pulsar/broker.log`; diff old and new `broker.conf` against release notes | Revert to old config; restart broker; update config with new field names | Validate config with new broker binary before deployment: `pulsar broker --help \| grep <field>`; maintain config change log per version |
| Data format incompatibility after ledger metadata upgrade | Queries for old ledgers fail; bookie returns `LedgerMetadataSerializationException` | `bookkeeper shell listledgers -meta 2>&1 \| grep Exception`; check ledger metadata format version | Restore BookKeeper metadata from ZooKeeper backup; downgrade bookie version | Run BookKeeper upgrade tool in dry-run mode first: `bookkeeper upgrade --dryRun`; snapshot ZK before any bookie version change |
| Feature flag rollout regression (e.g., enabling transaction coordinator) | After enabling transactions (`transactionCoordinatorEnabled=true`), broker fails with `TransactionMetadataStore` init error | `grep 'TransactionMetadataStore\|txn\|transaction' /var/log/pulsar/broker.log \| grep -i error`; check `pulsar-admin transactions coordinator-status` | Set `transactionCoordinatorEnabled=false` in broker.conf; restart broker; disable transaction coordinator topic | Test transaction coordinator enablement in staging; ensure `__transaction_buffer_snapshot` topic exists before enabling; follow migration checklist |
| Dependency version conflict (ZooKeeper / BookKeeper / Pulsar version matrix) | Pulsar broker upgraded but ZooKeeper client library incompatible; `KeeperException` in broker logs | `grep KeeperException /var/log/pulsar/broker.log`; check ZK client version: `pulsar-admin \| grep zookeeper`; compare with Pulsar version matrix | Downgrade Pulsar broker to version compatible with current ZooKeeper version | Always check Pulsar ↔ ZooKeeper ↔ BookKeeper version compatibility matrix before upgrade; test full stack upgrade in staging |

## Kernel/OS & Host-Level Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| OOM killer targets BookKeeper bookie process | Bookie disappears from cluster; broker logs `BKException: BookieHandleNotAvailableException`; ledger under-replication alerts | `dmesg -T | grep -i 'oom.*bookie\|oom.*java'`; `journalctl -k --since "1h ago" | grep -i killed`; `bookkeeper shell listbookies -rw` | Ledgers on killed bookie become under-replicated; broker cannot write new entries until ensemble reforms; consumer reads delayed | Set `oom_score_adj=-1000` for bookie process; tune JVM heap: `-Xmx` < 50% of host RAM; add `OOMScoreAdjust=-1000` to bookie systemd unit |
| Inode exhaustion from BookKeeper journal and ledger files | Bookie cannot create new ledgers; `IOException: No space left on device` in bookie.log despite free disk space | `df -i /var/lib/bookkeeper/journal`; `df -i /var/lib/bookkeeper/ledgers`; `find /var/lib/bookkeeper -type f | wc -l` | New topic creation fails; existing topics cannot accept new messages; producer backpressure triggers; geo-replication stalls | Configure bookie `gcWaitTime=600` to compact sooner; `bookkeeper shell gc` to force garbage collection; mount journal/ledger on XFS with large inode count |
| CPU steal on bookie node delays write acknowledgment | Producer `send()` latency spikes; broker logs `WriteLedgerEntry timed out`; bookie on shared cloud instance | `sar -u 1 5 | grep steal`; `bookkeeper shell readlog -m <ledger-id> | grep latency`; `vmstat 1 5` | Write latency increases; producer-side timeout fires; messages retried; potential duplicates if `deduplication=off`; geo-replication lag grows | Migrate bookie to dedicated instance with SSD; increase broker `bookkeeper-client-write-timeout` to accommodate; enable message deduplication |
| NTP clock skew breaks Pulsar message publish timestamp ordering | Messages arrive with out-of-order `publishTime`; consumers using `seekByTimestamp` jump to wrong position | `chronyc tracking | grep "System time"`; `pulsar-admin topics peek-messages persistent://<ns>/<topic> -n 10 -s <sub> | jq '.publishTime'` | Consumer seek-by-timestamp returns wrong messages; message ordering guarantees violated for time-based consumers; event-time windows break | `chronyc makestep`; enable `chronyd` on all broker and bookie nodes; alert on `abs(clock_skew_seconds) > 0.5`; use event time instead of publish time |
| File descriptor exhaustion on Pulsar broker from topic explosion | Broker cannot open new managed-ledger cursors; `Too many open files` in broker.log; new subscriptions fail | `ls /proc/$(pgrep -f PulsarBroker)/fd | wc -l`; `pulsar-admin topics list persistent://<ns> | wc -l`; `ulimit -n` | Cannot create new subscriptions; topic creation fails; existing topics continue working but cursors cannot be opened for new consumers | Set `LimitNOFILE=131072` in broker systemd unit; reduce topic count with namespace bundling; enable topic-level unloading for inactive topics |
| TCP conntrack table full drops broker-to-bookie connections | Broker logs `BKException: BookieHandleNotAvailableException` intermittently; bookies are healthy | `dmesg | grep "nf_conntrack: table full"`; `sysctl net.netfilter.nf_conntrack_count`; `ss -tn | grep 3181 | wc -l` | Write operations fail intermittently; message persistence unreliable; producers receive timeouts; under-replication alarms fire | `sysctl -w net.netfilter.nf_conntrack_max=524288`; use persistent connections from broker to bookies (default); reduce short-lived admin connections |
| Kernel page cache thrashing on bookie with journal and ledger on same disk | Bookie write latency > 100ms; `iostat` shows 100% disk utilization; journal fsync delayed by ledger reads | `iostat -x 1 5 | grep <disk>`; `cat /proc/meminfo | grep -E "Dirty|Writeback"`; `bookkeeper shell readlog -m <ledger-id>` shows high latency | Journal write latency degrades all message persistence; broker marks bookie as slow; ensemble avoids this bookie; capacity effectively lost | Separate journal and ledger onto different physical disks; mount journal on NVMe SSD; set `journalDirectories` and `ledgerDirectories` to different devices in `bk_server.conf` |
| cgroup memory pressure triggers JVM GC storms on broker | Broker GC pauses > 5s; broker temporarily loses ZooKeeper session; topic ownership transferred away | `cat /sys/fs/cgroup/memory/memory.pressure`; `jstat -gcutil $(pgrep -f PulsarBroker) 1000 5`; `grep "GC pause" /var/log/pulsar/broker.log` | Broker loses topic ownership during GC pause; consumers disconnected; rebalance storm across cluster; message delivery halted for affected topics | Increase cgroup memory limit; tune JVM: use G1GC with `-XX:MaxGCPauseMillis=200`; set `managedLedgerCacheSizeMB` below cgroup limit; separate heap from direct memory |

## Deployment Pipeline & GitOps Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| Image pull failure for Pulsar broker during rolling upgrade | New broker pod stuck in `ImagePullBackOff`; old broker terminated by rolling update; topic ownership gap | `kubectl describe pod <broker-pod> | grep -A5 "Events"`; `kubectl get events --field-selector reason=Failed | grep pull` | Topics owned by terminated broker unassigned until new broker starts; producer/consumer disconnections; message delivery paused | Pre-pull images via DaemonSet; use `imagePullPolicy: IfNotPresent` with digest-pinned tags; set `maxUnavailable=0` in deployment strategy |
| Helm drift: broker.conf values in Git differ from live ConfigMap | Broker running with stale config (wrong `managedLedgerMaxEntriesPerLedger` or `maxMessageSize`) | `diff <(helm get values <release> -a) <(cat values.yaml)`; `kubectl get configmap pulsar-broker-config -o yaml | grep <param>` | Broker behavior diverges from expected; message size limits wrong; ledger rollover too frequent or infrequent | `helm upgrade <release> --values values.yaml`; enable ArgoCD auto-sync with `selfHeal: true`; add config drift detection to monitoring |
| ArgoCD sync partially applies broker StatefulSet but not ZooKeeper ConfigMap | Broker starts with new config expecting ZooKeeper parameter that doesn't exist; broker crash-loops | `argocd app diff <app>`; `kubectl logs -n pulsar <broker-pod> | grep "config\|zookeeper\|connect"`; `kubectl get configmap -n pulsar` | All brokers crash; complete Pulsar outage; producers and consumers disconnected; messages in bookie but unreadable | Apply ZooKeeper and broker configs in sync waves: ZK at wave 0, broker at wave 1; use ArgoCD resource hooks; validate config before apply |
| PDB blocks bookie pod eviction during node drain | Node drain hangs; bookie pod protected by PDB; cluster maintenance stalls; BookKeeper quorum intact | `kubectl get pdb -n pulsar | grep bookie`; `bookkeeper shell listbookies -rw` — all bookies present | Node maintenance delayed; if forced eviction, under-replication risk; cluster upgrade window exceeded | Set PDB `maxUnavailable=1`; ensure write quorum allows one bookie loss: `bookkeeper shell simpletest`; trigger graceful decommission: `bookkeeper shell decommissionbookie -b <bookie>` before drain |
| Blue-green cutover fails: new broker version incompatible with existing BookKeeper | Green brokers start but cannot write to BookKeeper; `BKException: UnexpectedVersionException` in logs | `kubectl logs -l version=green -n pulsar | grep "BKException\|version"`; `bookkeeper shell bookieformat --nonInteractive 2>&1` | Green brokers non-functional; blue brokers still serving; cutover blocked; must maintain blue deployment | Test broker-bookie version compatibility matrix in staging; upgrade BookKeeper before brokers; maintain N-1 compatibility |
| ConfigMap drift: topic retention policy overridden by stale namespace policy | Topics retaining messages longer than expected; storage cost growing; or messages deleted too early | `pulsar-admin namespaces get-retention persistent://<ns>`; `kubectl get configmap <broker-config> -o yaml | grep retention` | Storage cost overrun; or data loss from premature deletion; consumer replay window shorter than expected | `pulsar-admin namespaces set-retention persistent://<ns> --size -1 --time 72` to set correct retention; reconcile ConfigMap with namespace policy; automate drift detection |
| Secret rotation for ZooKeeper auth breaks broker connection | Broker logs `KeeperException$AuthFailedException`; brokers cannot read/write metadata; topic operations freeze | `kubectl get secret <zk-auth-secret> -o jsonpath='{.metadata.annotations}'`; `grep AuthFailed /var/log/pulsar/broker.log` | Complete metadata access failure; topic creation/deletion blocked; subscription operations fail; cluster effectively read-only | Rolling restart brokers with new credential: `kubectl rollout restart statefulset/pulsar-broker`; use Vault with grace period; implement dual-credential transition |
| Schema Registry migration job conflicts with broker rolling upgrade | Schema Registry unavailable during broker restart; producers with schema enforcement fail with `IncompatibleSchemaException` | `pulsar-admin schemas get persistent://<ns>/<topic>`; `kubectl get pods -n pulsar -l component=broker --sort-by=.status.startTime` | Schema-enforced producers blocked; new message formats rejected; consumer deserialization failures for partial rollout | Decouple Schema Registry from broker lifecycle; deploy Schema Registry as separate service; add schema validation pre-check in CI pipeline |

## Service Mesh & API Gateway Edge Cases
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| Envoy sidecar circuit breaker trips on broker binary protocol connections | Producer receives `ServerError: ServiceNotReady`; Envoy rejects TCP connections to broker port 6650 | `istioctl proxy-config cluster <pod> | grep pulsar`; `kubectl logs <pod> -c istio-proxy | grep "overflow\|circuit\|6650"` | Producers cannot connect to broker; message publishing halted; consumer receives stale messages only | Exclude Pulsar binary protocol port from mesh: `traffic.sidecar.istio.io/excludeInboundPorts: "6650"`; or increase Envoy `circuitBreakers.maxConnections` for Pulsar upstream |
| Rate limiting on API gateway blocks Pulsar admin API calls | `pulsar-admin` commands fail with 429; topic management operations blocked; namespace policy updates timeout | `kubectl logs -l app=api-gateway | grep "429.*pulsar\|429.*admin"`; `pulsar-admin topics list persistent://<ns> 2>&1 | grep "429"` | Cannot create/delete topics; retention policy updates blocked; subscription management frozen; operational blind spot | Exempt `/admin/v2/*` paths from rate limiting; route admin API through dedicated ingress; use direct broker admin port for emergency operations |
| Stale service discovery for Pulsar broker after topic ownership transfer | Producer connects to broker that no longer owns topic; `TopicMigratedException` or redirect loop | `pulsar-admin topics lookup persistent://<ns>/<topic>`; `kubectl get endpoints pulsar-broker-svc`; `pulsar-admin brokers list` | Producer stuck in redirect loop; message publishing delayed by lookup latency; consumer may connect to wrong broker | Set `lookupRequestTimeout` on client; configure broker `loadBalancerAutoBundleSplitEnabled=true` for better distribution; reduce Kubernetes DNS TTL |
| mTLS rotation interrupts Pulsar geo-replication connections | Geo-replication lag spikes; broker logs `SSL handshake failed` for remote cluster connections | `pulsar-admin clusters get-peer-clusters`; `kubectl logs <broker-pod> -n pulsar | grep "SSL\|handshake\|geo-replication"`; `istioctl proxy-config secret <pod>` | Geo-replication paused; messages accumulate on source cluster; destination cluster serves stale data; cross-region SLO violated | Configure separate TLS certificates for geo-replication outside mesh mTLS; set Istio cert rotation grace period; use `tlsAllowInsecureConnection=true` only as emergency fallback |
| Retry storm from mesh amplifies Pulsar broker load during partition rebalance | Topic ownership rebalancing; mesh retries failed lookups; broker admin API overwhelmed by retry flood | `pulsar-admin broker-stats topics | jq '.topics | length'`; `istioctl proxy-config route <pod> | grep retries`; `kubectl logs <pod> -c istio-proxy | grep "retry"` | Broker admin API saturated; topic lookup latency > 10s; rebalance takes 5x longer; producer/consumer reconnection storm | Disable mesh retries for Pulsar upstream; implement client-side backoff: `operationTimeoutMs=60000`; reduce rebalance frequency: `loadBalancerSheddingIntervalMinutes=10` |
| gRPC admin API conflicts with Pulsar binary protocol on shared port | Mesh treats Pulsar binary protocol as HTTP/2 and injects headers; broker rejects malformed frames | `kubectl logs <pod> -c istio-proxy | grep "protocol\|h2\|http2"`; `pulsar-admin --admin-url http://<broker>:8080 brokers list` — works; port 6650 fails | Binary protocol producers/consumers cannot connect through mesh; only HTTP admin API works | Configure Istio `DestinationRule` with `trafficPolicy.connectionPool.tcp` for port 6650; mark port as TCP not HTTP in Service spec: `appProtocol: tcp` |
| Trace context lost across Pulsar message publish-consume boundary | Distributed traces end at producer publish; consumer processing shows as separate trace with no parent | Check Jaeger for orphan consumer traces; `pulsar-admin topics peek-messages persistent://<ns>/<topic> -n 1 -s <sub> | jq '.properties'` — no traceparent | Cannot trace message end-to-end through Pulsar; latency attribution broken between publish and consume; debugging async flows requires log correlation | Inject trace context as Pulsar message properties: `producer.newMessage().property("traceparent", span.context())` ; extract in consumer; use OpenTelemetry Pulsar instrumentation library |
| API gateway timeout shorter than Pulsar admin bulk operation | `pulsar-admin namespaces delete persistent://<ns>` with thousands of topics; gateway returns 504; operation still running | `kubectl logs -l app=api-gateway | grep "504.*pulsar"`; `pulsar-admin namespaces list persistent://<ns>` — still shows namespace | Gateway reports failure; operator retries; duplicate delete operation; or namespace stuck in partial deletion state | Use async admin API pattern; increase gateway timeout for `/admin/v2/namespaces` path; implement polling: `pulsar-admin namespaces policies persistent://<ns>` |
