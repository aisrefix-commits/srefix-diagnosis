---
name: openshift-agent
description: >
  Red Hat OpenShift specialist agent. Handles enterprise Kubernetes issues
  including ClusterOperator degradation, route failures, build problems,
  SCC misconfigurations, and upgrade issues.
model: sonnet
color: "#EE0000"
skills:
  - openshift/openshift
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-openshift-agent
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

You are the OpenShift Agent — the Red Hat enterprise Kubernetes expert. When
any alert involves OpenShift ClusterOperators, Routes, Builds, ImageStreams,
SCCs, or cluster upgrades, you are dispatched to diagnose and remediate.

# Activation Triggers

- Alert tags contain `openshift`, `ocp`, `route`, `buildconfig`, `scc`
- Metrics from OpenShift monitoring stack
- Error messages contain OpenShift terms (ClusterOperator, DeploymentConfig, SCC, ImageStream)

# Cluster Visibility

Quick commands to get a cluster-wide OpenShift overview:

```bash
# Overall cluster health
oc get clusteroperators                            # All ClusterOperator status
oc get clusterversion                              # Version and upgrade status
oc get nodes                                       # Node readiness
oc adm top nodes                                   # Node resource utilization

# Control plane status
oc get pods -n openshift-kube-apiserver            # API server pods
oc get pods -n openshift-etcd                      # etcd pods
oc get pods -n openshift-kube-scheduler            # Scheduler pods
oc get pods -n openshift-kube-controller-manager   # Controller manager pods

# Resource utilization snapshot
oc adm top pods -A --sort-by=memory | head -20    # Top memory consumers
oc get clusteroperators -o json | jq '[.items[] | select(.status.conditions[] | select(.type=="Degraded" and .status=="True"))] | length'  # Degraded operator count
oc get nodes -o json | jq '.items[] | {name:.metadata.name, ready:.status.conditions[] | select(.type=="Ready") | .status}'

# Topology/cluster view
oc get clusteroperators -o custom-columns=NAME:.metadata.name,AVAILABLE:.status.conditions[0].status,PROGRESSING:.status.conditions[1].status,DEGRADED:.status.conditions[2].status
oc get infrastructures cluster -o json | jq '{platform:.spec.platformSpec.type, apiServerURL:.status.apiServerURL}'
oc get routes -A | grep -v "Admitted"              # Routes not admitted
```

# Global Diagnosis Protocol

Structured step-by-step OpenShift cluster diagnosis:

**Step 1: Control plane health**
```bash
oc get clusteroperators | grep -E "False|True.*True"  # Available=False or Degraded=True
oc get clusterversion                              # Cluster version, upgrade state
oc get pods -n openshift-etcd -o wide             # etcd pod health
oc get pods -n openshift-kube-apiserver | grep -v Running  # API server issues
oc adm node-logs --role=master --unit=crio --tail=50  # CRI-O logs on masters
```

**Step 2: Data plane health**
```bash
oc get nodes                                       # All Ready?
oc get pods -A | awk '$4 != "Running" && $4 != "Completed"' | head -30
oc get machineconfigpools                          # MCP state (degraded = nodes not updated)
oc get machines -n openshift-machine-api           # Machine provisioning status
```

**Step 3: Recent events/errors**
```bash
oc get events -A --sort-by='.lastTimestamp' | grep -i "Warning\|Error" | tail -30
oc adm node-logs --role=master --unit=kubelet --tail=50
oc get events -n openshift-monitoring --sort-by='.lastTimestamp' | tail -20
oc describe clusteroperator <degraded-operator>    # Degraded operator messages
```

**Step 4: Resource pressure check**
```bash
oc adm top nodes
oc get nodes -o json | jq '.items[] | select(.status.conditions[] | select(.type=="MemoryPressure" and .status=="True")) | .metadata.name'
oc get nodes -o json | jq '.items[] | select(.status.conditions[] | select(.type=="DiskPressure" and .status=="True")) | .metadata.name'
oc get quota -A | grep -v "0/0"                   # ResourceQuotas with usage
```

**Severity classification:**
- CRITICAL: API server unavailable, etcd quorum lost, multiple ClusterOperators Degraded, > 30% nodes NotReady
- WARNING: single ClusterOperator Degraded, MachineConfigPool degraded (nodes not updating), upgrade stuck, router pod count reduced
- OK: all ClusterOperators Available=True/Degraded=False, all nodes Ready, etcd healthy, no upgrade in progress

---

## Prometheus Metrics and Alert Thresholds

OpenShift ships a pre-configured Prometheus stack in `openshift-monitoring`. Key
metrics come from the cluster-monitoring operator. Access via Thanos Querier:
`https://thanos-querier.openshift-monitoring.svc:9091`.

| Metric | Description | WARNING | CRITICAL |
|--------|-------------|---------|----------|
| `cluster_operator_conditions{condition="Degraded"}` | ClusterOperator degraded count | > 0 | > 2 |
| `cluster_operator_conditions{condition="Available"}` == 0 | ClusterOperator availability | == 0 | == 0 |
| `etcd_server_leader_changes_seen_total` rate(5m) | etcd leader changes per minute | > 0.1 | > 0.5 |
| `etcd_disk_wal_fsync_duration_seconds` p99 | etcd WAL fsync latency | > 10ms | > 100ms |
| `etcd_disk_backend_commit_duration_seconds` p99 | etcd backend commit latency | > 25ms | > 250ms |
| `etcd_server_proposals_failed_total` rate(5m) | etcd proposal failures | > 0 | > 0.5/s |
| `apiserver_request_duration_seconds` p99 (by verb) | API server request latency | > 1s | > 5s |
| `apiserver_request_total{code=~"5.."}` rate(5m) | API server 5xx rate | > 0.1/s | > 1/s |
| `container_memory_working_set_bytes / container_spec_memory_limit_bytes` | Container memory ratio | > 0.85 | > 0.95 |
| `container_oom_events_total` rate(5m) | Container OOM kills | > 0 | > 0 |
| `container_cpu_cfs_throttled_periods_total / container_cpu_cfs_periods_total` | CPU throttle ratio | > 0.25 | > 0.50 |
| `kube_node_status_condition{condition="Ready",status="true"}` | Node Ready count | < total | < 70% of total |
| `kube_deployment_status_replicas_unavailable` | Unavailable replicas per deployment | > 0 | = desired count |
| `haproxy_backend_current_queue` (router) | HAProxy queue depth per backend | > 10 | > 50 |
| `haproxy_backend_connection_errors_total` rate(5m) | HAProxy connection errors | > 0.1/s | > 1/s |
| `cluster:capacity_cpu_cores:sum` vs `cluster:usage:cpu:sum` | Cluster CPU utilization ratio | > 0.80 | > 0.95 |

### PromQL Alert Expressions

```yaml
# Any ClusterOperator degraded
- alert: ClusterOperatorDegraded
  expr: cluster_operator_conditions{condition="Degraded",name!~"insights"} == 1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "ClusterOperator {{ $labels.name }} is degraded"

# ClusterOperator unavailable (not available)
- alert: ClusterOperatorNotAvailable
  expr: cluster_operator_conditions{condition="Available"} == 0
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "ClusterOperator {{ $labels.name }} is not available"

# etcd leader changes too frequent (> 1 per 5 minutes)
- alert: EtcdLeaderChangesFrequent
  expr: rate(etcd_server_leader_changes_seen_total[5m]) > 0.2
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "etcd leader election rate {{ $value }} changes/s — control plane unstable"

# etcd WAL fsync latency p99 > 100ms (SSD I/O degraded)
- alert: EtcdWALSyncSlow
  expr: histogram_quantile(0.99, rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])) > 0.1
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "etcd WAL fsync p99={{ $value }}s — disk I/O degraded, control plane at risk"

# API server 5xx error rate > 1% of requests
- alert: KubeAPIServerErrorRateHigh
  expr: |
    (
      rate(apiserver_request_total{code=~"5.."}[5m])
      / rate(apiserver_request_total[5m])
    ) > 0.01
  for: 5m
  labels:
    severity: warning

# Container OOM kill
- alert: OpenShiftContainerOOMKilled
  expr: rate(container_oom_events_total{namespace!~"openshift-.*"}[5m]) > 0
  for: 0m
  labels:
    severity: critical
  annotations:
    summary: "OOM kill in {{ $labels.namespace }}/{{ $labels.pod }}/{{ $labels.container }}"

# CPU throttling > 25% for workload containers
- alert: OpenShiftCPUThrottling
  expr: |
    (
      rate(container_cpu_cfs_throttled_periods_total{container!="",namespace!~"openshift-.*"}[5m])
      / rate(container_cpu_cfs_periods_total{container!="",namespace!~"openshift-.*"}[5m])
    ) > 0.25
  for: 10m
  labels:
    severity: warning

# HAProxy queue depth > 10 (router saturation)
- alert: OpenShiftRouterQueueSaturation
  expr: haproxy_backend_current_queue > 10
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "HAProxy backend {{ $labels.route }} queue depth {{ $value }}"
```

---

# Focused Diagnostics

### Scenario 1: ClusterOperator Degraded

**Symptoms:** `oc get clusteroperators` shows `Degraded=True`, related functionality impaired

**Metrics to check:** `cluster_operator_conditions{condition="Degraded"} == 1`; API server error rate spike; etcd latency increase if `kube-apiserver` operator degraded

```bash
oc describe clusteroperator <operator-name>        # Conditions, version, related objects
oc get pods -n openshift-<operator-name>           # Operator pods health
oc logs -n openshift-<operator-name> -l app=<operator> --tail=100 | grep -iE "error|degraded"
oc get events -n openshift-<operator-name> --sort-by='.lastTimestamp' | tail -20
oc get clusteroperator <name> -o json | jq '.status.conditions[] | select(.type=="Degraded")'
```

**Key indicators:** Operator pod CrashLooping, underlying resource unhealthy (e.g., etcd for kube-apiserver), config change failure, certificate issue

### Scenario 2: Container OOM Kill / Memory Limit Exceeded

**Symptoms:** Pods killed with OOMKilled; exit code 137; application logs cut off mid-execution

**Metrics to check:** `container_oom_events_total` rate > 0; `container_memory_working_set_bytes / container_spec_memory_limit_bytes > 0.95`

```bash
# Identify recently OOMKilled containers
oc get pods -A -o json | jq '.items[] | select(.status.containerStatuses[]?.lastState.terminated.reason=="OOMKilled") | {ns:.metadata.namespace, pod:.metadata.name, container:.status.containerStatuses[].name}'
# Check memory limits and current usage
oc describe pod <pod> -n <ns> | grep -A5 "Limits\|Requests\|OOM"
oc adm top pod <pod> -n <ns> --containers
# Prometheus: recent OOM events
# rate(container_oom_events_total{namespace="<ns>"}[5m]) > 0
# Current memory pressure
# container_memory_working_set_bytes{pod="<pod>"} / container_spec_memory_limit_bytes{pod="<pod>"}
oc get limitrange -n <ns>                          # Check namespace limits
```

**Indicators:** `lastState.terminated.reason == OOMKilled`; high `container_memory_working_set_bytes`; missing or low memory limit in pod spec

### Scenario 3: Image Pull Failure Causing Deployment Stall

**Symptoms:** Pods stuck in `ImagePullBackOff` or `ErrImagePull`; deployment stuck with no new pods starting

**Metrics to check:** `kube_deployment_status_replicas_unavailable > 0` sustained; `container_tasks_state{state="created"}` not transitioning

```bash
oc get pods -n <ns> | grep -E "ImagePull|Pending"
oc describe pod <pod> -n <ns> | grep -A10 "Events:"
# Test image pull manually from a node
oc debug node/<node> -- chroot /host crictl pull <image>
# Check internal registry
oc get pods -n openshift-image-registry
oc get route -n openshift-image-registry
# Check ImageStream tags
oc describe imagestream <name> -n <ns> | grep -E "tag|Image|Digest"
# Registry connectivity
oc exec <pod> -n <ns> -- curl -v https://image-registry.openshift-image-registry.svc:5000/v2/
# Pull secret valid?
oc get secret pull-secret -n openshift-config -o json | jq '.data[".dockerconfigjson"]' | base64 -d | jq 'keys'
```

**Indicators:** `unauthorized` or `connection refused` on pull; global pull secret expired or missing registry entry; ImageStream pointing to deleted tag; network policy blocking registry access

### Scenario 4: Route Not Admitted / External Traffic Not Reaching App

**Symptoms:** Route shows `HostAdmitted: False`, 503 from HAProxy, app unreachable externally

**Metrics to check:** `haproxy_backend_current_queue > 10`; `haproxy_backend_connection_errors_total` rate; zero active healthy backends for the route

```bash
oc get routes -A | grep -v Admitted               # Non-admitted routes
oc describe route <name> -n <ns>                   # Route conditions and host
oc get pods -n openshift-ingress                   # Router pods
oc logs -n openshift-ingress -l ingresscontroller.operator.openshift.io/deployment-ingresscontroller=default --tail=100
oc get ingresscontroller default -n openshift-ingress-operator -o yaml  # Ingress config
oc get svc <service> -n <ns>                      # Service selector correct?
# Router metrics
oc exec -n openshift-ingress <router-pod> -- curl -s http://localhost:1936/metrics | grep -E "haproxy_backend_active_servers|haproxy_backend_current_queue"
```

**Key indicators:** Hostname collision (same host on multiple routes), service selector mismatch, router pod crashed, wildcard DNS not pointing to router VIP

### Scenario 5: SCC Violation / Pod Security Denied

**Symptoms:** Pods fail with `unable to validate against any security context constraint`, permission denied errors

```bash
oc describe pod <pod> -n <ns>                      # SCC violation in events
oc get pod <pod> -n <ns> -o json | jq '.metadata.annotations."openshift.io/scc"'
oc adm policy who-can use scc <scc-name>           # Who can use which SCC
oc get scc                                         # All SCCs and priority order
oc adm policy scc-subject-review -f <pod-spec.yaml>  # Which SCC would apply
```

**Key indicators:** ServiceAccount lacks `use` verb on required SCC, container requesting capabilities not allowed by current SCC, runAsUser outside allowed range

### Scenario 6: Node Stuck in NotReady / MachineConfigPool Degraded

**Symptoms:** Node NotReady, MachineConfigPool shows degraded, node not applying config update

**Metrics to check:** `kube_node_status_condition{condition="Ready",status="true"}` count drops; node memory or disk pressure

```bash
oc get nodes                                       # NotReady nodes
oc get machineconfigpools                          # MCP state: degraded/updating
oc describe machineconfigpool worker               # Which machine is degraded?
oc get machines -n openshift-machine-api | grep -v Running  # Machine provisioning?
oc adm node-logs <node> --unit=machine-config-daemon --tail=100  # MCD logs on node
ssh core@<node> "sudo systemctl status machine-config-daemon"
# Memory/disk pressure on node
oc describe node <node> | grep -A5 "Conditions:"
```

**Key indicators:** MachineConfig applying failed (incorrect config syntax), node drained during upgrade but not rebooted, crio/kubelet version mismatch after MachineConfig update

### Scenario 7: etcd Health Degraded Causing API Server Slowness

**Symptoms:** API server responses slow (> 2s for `kubectl get pods`); etcd leader election events in logs; `oc get clusteroperator etcd` shows `Degraded=True`; write operations (apply, delete) timing out

**Root Cause Decision Tree:**
- `etcd_disk_wal_fsync_duration_seconds` p99 > 100ms → disk I/O degradation on etcd nodes (cloud IOPS throttled)
- `etcd_server_leader_changes_seen_total` rate > 1/5m → network instability between etcd members
- Single etcd member `failed` → quorum still maintained but degraded; one member needs recovery
- etcd DB size growing unbounded → `etcd_mvcc_db_total_size_in_bytes > 8 GB`; compaction not running

**Diagnosis:**
```bash
oc get pods -n openshift-etcd                      # All etcd pods Running?
oc get clusteroperator etcd -o json | jq '.status.conditions[] | select(.type=="Degraded")'
# etcd member health
oc rsh -n openshift-etcd etcd-<master-node> etcdctl --cert /etc/kubernetes/static-pod-certs/secrets/etcd-all-certs/etcd-peer-<node>.crt \
  --key /etc/kubernetes/static-pod-certs/secrets/etcd-all-certs/etcd-peer-<node>.key \
  --cacert /etc/kubernetes/static-pod-certs/configmaps/etcd-all-bundles/server-ca-bundle.crt \
  --endpoints https://localhost:2379 endpoint health
# Leader and DB size
oc rsh -n openshift-etcd etcd-<master-node> etcdctl endpoint status \
  --cluster -w table 2>/dev/null
# Disk I/O on master nodes
oc adm node-logs --role=master --unit=etcd | grep -iE "disk|fsync|slow|latency" | tail -20
# PromQL: etcd WAL fsync p99
# histogram_quantile(0.99, rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m]))
```

**Thresholds:**
- WARNING: `etcd_disk_wal_fsync_duration_seconds` p99 > 10ms; leader changes > 1 in 5 min; DB size > 4 GB
- CRITICAL: p99 > 100ms; quorum lost (< 2 of 3 members healthy); DB size > 8 GB; defragmentation needed

### Scenario 8: Image Pull Failure from Internal Registry Quota

**Symptoms:** Pods failing with `ErrImagePull` against `image-registry.openshift-image-registry.svc:5000`; `oc get pods -n openshift-image-registry` shows registry pods running; registry logs show `quota exceeded`

**Root Cause Decision Tree:**
- `ImagePullBackOff` with `429 Too Many Requests` → image registry per-namespace pull quota hit
- Registry pods restarting → storage backend (PVC) full or backing object store unreachable
- `unauthorized: authentication required` → service account missing `system:image-puller` role
- Registry pod CPU/memory limit hit → requests slow or dropped during pulls
- `layer already exists` conflicts → registry storage inconsistency after failed push

**Diagnosis:**
```bash
oc get pods -n openshift-image-registry -o wide    # Registry pod status
oc logs -n openshift-image-registry -l docker-registry=default --tail=100 | grep -iE "quota|error|denied|limit"
# Registry storage usage
oc get pvc -n openshift-image-registry
oc exec -n openshift-image-registry deployment/image-registry -- df -h /registry
# Check image quota on specific namespace
oc describe appliedclusterresourcequota -n <ns> | grep -A5 "images\|imagestreams"
oc get imagestream -n <ns> | wc -l
# Registry resource limits
oc get deployment image-registry -n openshift-image-registry -o json | jq '.spec.template.spec.containers[0].resources'
# Pull secret validity
oc whoami --show-token | xargs -I{} oc get is -n <ns> --token={}  # auth test
```

**Thresholds:**
- WARNING: Registry pod CPU > 80% of limit; storage > 75% full; pull latency > 10s
- CRITICAL: Registry pods OOMKilled or CrashLooping; storage full (0 bytes free); all pulls failing

### Scenario 9: Route Not Working After Certificate Rotation

**Symptoms:** External HTTPS traffic returns `SSL_ERROR_RX_RECORD_TOO_LONG` or certificate mismatch error after cluster upgrade or certificate rotation; `oc get route` shows route Admitted; HAProxy logs show TLS handshake failures

**Root Cause Decision Tree:**
- Route uses `edge` TLS termination with custom cert → cert/key in route secret expired or not rotated
- Ingress operator `defaultCertificate` secret not updated → wildcard cert expired; all routes affected
- Route reencrypt cert mismatch → backend service cert rotated but route destination CA not updated
- HAProxy pod not reloaded after cert secret update → serving stale cert from memory

**Diagnosis:**
```bash
# Check route TLS configuration
oc get route <name> -n <ns> -o json | jq '.spec.tls | {termination, certificate: (.certificate // "none"), key: (.key // "none")}'
# Verify cert expiry on the route
oc get route <name> -n <ns> -o jsonpath='{.spec.tls.certificate}' | openssl x509 -noout -dates 2>/dev/null
# Check default ingress cert
oc get secret router-certs-default -n openshift-ingress -o json | jq '.data["tls.crt"]' | base64 -d | openssl x509 -noout -subject -dates
# Wildcard cert from ingress operator
oc get ingresscontroller default -n openshift-ingress-operator -o json | jq '.spec.defaultCertificate'
# HAProxy TLS errors
oc logs -n openshift-ingress -l ingresscontroller.operator.openshift.io/deployment-ingresscontroller=default | grep -i "tls\|ssl\|cert\|handshake" | tail -20
# External cert check
echo | openssl s_client -servername <route-host> -connect <router-ip>:443 2>/dev/null | openssl x509 -noout -dates
```

**Thresholds:**
- WARNING: Certificate expiring in < 30 days; occasional TLS handshake errors in HAProxy logs
- CRITICAL: Certificate expired; all HTTPS routes returning SSL errors; external traffic failing

### Scenario 10: Machine Config Update Causing Rolling Node Reboot Storm

**Symptoms:** Multiple worker nodes simultaneously rebooting; cluster workloads disrupted; `oc get machineconfigpool worker` shows high `updatingCount`; pods evicted faster than they reschedule; `DEGRADED=True` on MCP

**Root Cause Decision Tree:**
- `maxUnavailable` set to large value or percentage in MCP → too many nodes draining simultaneously
- MCO rolling out to all nodes at once → new MachineConfig or rendered config triggered cluster-wide
- Node fails to apply MachineConfig → MCO retries and eventually marks MCP Degraded
- Pause not set on MCP → MCO processes updates without operator approval

**Diagnosis:**
```bash
oc get machineconfigpool                           # Updating, Degraded, ReadyMachineCount
oc describe machineconfigpool worker | grep -A10 "Machine Config Selector\|MaxUnavailable\|Updating"
# Which nodes are updating right now
oc get nodes | grep "SchedulingDisabled"
# MCD logs on an updating node
oc adm node-logs <updating-node> --unit=machine-config-daemon --tail=100 | grep -iE "error|applying|pivot|drain"
# Which MachineConfig triggered the update
oc get node <node> -o json | jq '.metadata.annotations["machineconfiguration.openshift.io/currentConfig"]'
oc get node <node> -o json | jq '.metadata.annotations["machineconfiguration.openshift.io/desiredConfig"]'
# Count simultaneously cordoned nodes
oc get nodes | grep "SchedulingDisabled" | wc -l
```

**Thresholds:**
- WARNING: `updatingCount > 1` simultaneously; 1 node per rolling update expected; `maxUnavailable > 1`
- CRITICAL: > 30% of nodes simultaneously draining; workloads cannot reschedule; MCP `DEGRADED=True`

### Scenario 11: Cluster Operator Upgrade Failure (Failing=True)

**Symptoms:** `oc get clusterversion` shows `Progressing=True` for > 30 min with no change; `oc describe clusterversion` shows a ClusterOperator with `Failing=True`; upgrade percentage stuck; alerts firing for `ClusterOperatorDegraded`

**Root Cause Decision Tree:**
- Operator pod CrashLooping during upgrade → new operator version incompatible with current resource state
- Upgrade stuck on MachineConfigPool update → nodes failing to drain or reboot
- Cluster pre-checks failing → node resource pressure, non-compliant workloads, storage degraded
- OLM-managed operator upgrade failure → CSV in `Failed` state blocking cluster upgrade
**Diagnosis:**
```bash
oc get clusterversion                              # Version, progressing reason
oc describe clusterversion | grep -A20 "Conditions\|Failing"
# Which operator is failing
oc get clusteroperators -o json | jq '[.items[] | select(.status.conditions[] | select(.type=="Failing" and .status=="True"))] | .[].metadata.name'
# Operator-specific diagnosis
oc describe clusteroperator <failing-operator>
oc get pods -n openshift-<failing-operator> | grep -v Running
oc logs -n openshift-<failing-operator> -l app=<failing-operator> --tail=100 | grep -iE "error|failed|panic"
# MCP blocking upgrade
oc get machineconfigpool | grep -E "Degraded=True|Updating=True"
# Upgrade history
oc get clusterversion -o json | jq '.items[0].status.history[:3]'
```

**Thresholds:**
- WARNING: Upgrade progressing > 20 min per operator; single non-critical operator Failing
- CRITICAL: Upgrade stuck > 60 min; critical operator (kube-apiserver, etcd, network) Failing; cluster rollback needed

### Scenario 12: OAuth Authentication Failure (Identity Provider Misconfiguration)

**Symptoms:** Users cannot log into OpenShift console or `oc login`; `oc login` returns `401 Unauthorized` or `invalid_client`; OAuth server logs show identity provider errors; service accounts unaffected

**Root Cause Decision Tree:**
- LDAP/AD bind DN password expired → OAuth operator cannot authenticate to LDAP
- OIDC client secret rotated on IdP but not updated in OpenShift → `invalid_client` error
- `oauth-openshift` route certificate expired → browser shows SSL error on login page
- `cluster-admin` OAuth bypass → kubeadmin password deleted but no other admin user configured
- Network policy blocking OAuth pods from reaching external IdP

**Diagnosis:**
```bash
oc get pods -n openshift-authentication                # OAuth server pods
oc logs -n openshift-authentication -l app=oauth-openshift --tail=100 | grep -iE "error|denied|failed|provider"
# OAuth configuration
oc get oauth cluster -o json | jq '.spec.identityProviders[] | {name, type, mappingMethod}'
# Test LDAP connectivity (if LDAP provider)
oc logs -n openshift-authentication -l app=oauth-openshift | grep -i "ldap\|bind\|search" | tail -20
# OIDC secret validity
oc get secret <oidc-secret-name> -n openshift-config -o json | jq '.data.clientSecret' | base64 -d
# OAuth route certificate
oc get route oauth-openshift -n openshift-authentication -o jsonpath='{.spec.tls.certificate}' | openssl x509 -noout -dates 2>/dev/null
# Recent auth cluster operator condition
oc get clusteroperator authentication -o json | jq '.status.conditions[] | select(.type=="Degraded" or .type=="Available")'
```

**Thresholds:**
- WARNING: Some users cannot authenticate (specific IdP affected); OAuth pod restarts > 2; latency > 5s on login
- CRITICAL: All users locked out; OAuth pods CrashLooping; `authentication` ClusterOperator `Available=False`

### Scenario 13: Kerberos / LDAP Group Sync Failure Causing RBAC Authorization Gaps in Production

**Symptoms:** Users who are members of AD/LDAP groups can authenticate to the cluster in staging but receive `403 Forbidden` in production; `oc whoami` returns correct identity but `oc projects` is empty; ClusterRoleBinding references the group `system:authenticated:oauth` or a specific LDAP group, but the user is not recognized as a member; group sync CronJob shows failures in logs.

**Root Cause Decision Tree:**
- If `oc get group` shows group exists but membership is stale: → LDAP group sync CronJob not running in prod; sync interval too long; LDAP server change not reflected
- If LDAP sync fails with TLS error: → Production LDAP server enforces LDAPS (636/tcp); sync config still points to port 389 or lacks CA bundle
- If sync fails with `invalid credentials`: → Service account DN/password for LDAP sync rotated in prod vault but not updated in the sync secret
- If user present in LDAP but not in OpenShift group: → User DN format in prod differs from staging (e.g., `cn=` vs `uid=`); sync tolerates mismatch in staging but is strict in prod due to different LDAP schema

```bash
# Check group sync CronJob status and last run
oc get cronjob -n openshift-authentication 2>/dev/null || \
  oc get cronjob -A | grep -i "ldap\|group-sync"
oc get jobs -A | grep -i "ldap\|group-sync" | sort -k6 -r | head -10

# Check sync job logs for TLS or auth errors
oc logs -n <sync-namespace> job/<last-sync-job-name> | tail -50

# View current OpenShift group memberships
oc get group <group-name> -o yaml | grep -A20 "users:"

# Manually test LDAP connectivity from sync pod namespace
oc run ldap-test --image=registry.redhat.io/ubi9/ubi --restart=Never -n <sync-namespace> \
  --command -- ldapsearch -H ldaps://<ldap-host>:636 \
  -D "cn=svc-ocp-sync,ou=svcaccts,dc=example,dc=com" \
  -w "$LDAP_BIND_PASSWORD" \
  -b "ou=groups,dc=example,dc=com" "(cn=ocp-admins)" member 2>&1 | head -30

# Check the LDAP sync secret is current
oc get secret ldap-sync-secret -n <sync-namespace> -o jsonpath='{.data.bindPassword}' | base64 -d

# Run a dry-run LDAP sync to preview changes without applying
oc adm groups sync --sync-config=/path/to/ldap-sync-config.yaml --confirm=false 2>&1 | head -40

# Verify RBAC binding references the correct group name
oc get clusterrolebindings,rolebindings -A -o wide | grep <group-name>
```

**Thresholds:** Warning: group sync > 2× its configured interval without success; Critical: any production group sync failure lasting > 1 sync cycle where security groups (cluster-admins, auditors) are affected.

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `Error from server (Forbidden): xxx is forbidden: User "xxx" cannot xxx` | RBAC or SCC denial for the user or service account | `oc adm policy who-can <verb> <resource>` |
| `error: container xxx has runAsNonRoot and image has non-numeric user` | SCC `runAsUser` policy conflicts with non-numeric USER in image | `oc get pod <pod> -o yaml \| grep securityContext` |
| `Error creating: pods "xxx" is forbidden: unable to validate against any security context constraint` | No SCC in the namespace matches pod's security requirements | `oc describe scc restricted` |
| `Quota exceeded: pods` | Namespace resource quota for pods reached | `oc describe quota -n <namespace>` |
| `ImagePullBackOff` with `unauthorized: authentication required` | ImageStream pull secret missing or not linked to service account | `oc create secret docker-registry regcred --docker-server=<registry>` |
| `error: oc login: unauthorized` | OAuth token expired | `oc login --server=<api-url>` |
| `Build failed: exit code 1` | Source-to-image (S2I) build failed during assemble phase | `oc logs build/<build-name>` |
| `Route xxx: HostAlreadyClaimed` | Route hostname already used by another route in the cluster | `oc get routes --all-namespaces \| grep <hostname>` |
| `CrashLoopBackOff` in `openshift-etcd` namespace | etcd member unhealthy or quorum lost | `oc get pods -n openshift-etcd` |
| `ClusterOperator xxx is not available` | Core OpenShift operator degraded, blocking upgrades | `oc describe co <operator-name>` |

# Capabilities

1. **Cluster health** — ClusterOperator status, upgrade management, etcd health
2. **Routing** — Route/ingress debugging, HAProxy, TLS termination
3. **Builds** — S2I, Docker builds, ImageStream triggers, registry issues
4. **Security** — SCC management, RBAC, certificate rotation
5. **Operators** — OLM lifecycle, OperatorHub, CSV troubleshooting
6. **Migration** — DeploymentConfig to Deployment, API deprecations

# Critical Metrics to Check First

1. `cluster_operator_conditions{condition="Degraded"} > 0` — degraded operators break cluster features
2. `etcd_disk_wal_fsync_duration_seconds` p99 > 100ms — disk I/O degraded, control plane at risk
3. `etcd_server_leader_changes_seen_total` rate — frequent changes indicate instability
4. `container_oom_events_total` rate — OOM kills immediately impact workloads
5. `apiserver_request_total{code=~"5.."}` rate — API errors affect all cluster operations
6. `kube_node_status_condition{condition="Ready",status="true"}` count — node loss reduces capacity

# Output

Standard diagnosis/mitigation format. Always include: cluster version,
affected operator/component, node status summary, key metric values, and recommended
remediation steps.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| Router returning 503 for specific routes | Backend pod liveness probe failing; endpoints removed from service but route still exists | `oc get events -n <namespace> --sort-by='.lastTimestamp' \| tail -20` |
| ImageStream-triggered builds failing with `pull access denied` | Internal registry (image-registry operator) degraded; image pushes/pulls failing cluster-wide | `oc get co image-registry` |
| Pods stuck in `Pending` with `0/N nodes available` | Node selector or SCC constraint mismatch; pods require privileged SCC that is not granted | `oc describe pod <pod-name> -n <namespace> \| grep -A5 'Events'` |
| `oc rollout` hanging indefinitely | Cluster autoscaler failing to provision new nodes; new pods cannot be scheduled | `oc get machinesets -n openshift-machine-api` and `oc get machines -n openshift-machine-api` |
| OAuth login broken for all users | `authentication` ClusterOperator degraded; OAuth server pods restarting or misconfigured | `oc describe co authentication` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N worker nodes in `NotReady` state | `kube_node_status_condition{condition="Ready",status="true"}` drops by 1; pods on that node evicted or unschedulable | Reduced cluster capacity; pods from the affected node rescheduled, causing temporary disruption | `oc get nodes` and `oc describe node <NotReady-node> \| grep -A10 Conditions` |
| 1 of 3 HAProxy router pods not receiving config updates | Route changes applied to 2/3 router pods; ~1/3 of requests routed by stale config | Intermittent 503s or wrong backend for recently changed routes | `oc get pods -n openshift-ingress -o wide` then `oc logs -n openshift-ingress <stale-router-pod> \| grep reload` |
| 1 of 3 etcd members has high disk fsync latency | `etcd_disk_wal_fsync_duration_seconds` p99 elevated on one member; leader elections more frequent | Increased API server latency cluster-wide; risk of etcd leader change under write load | `oc exec -n openshift-etcd etcd-<member> -- etcdctl endpoint status --cluster -w table` |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Cluster operator degraded count | > 0 | > 3 | `oc get clusteroperators \| grep -v True` |
| etcd WAL fsync duration p99 (ms) | > 10ms | > 50ms | `oc exec -n openshift-etcd etcd-<member> -- etcdctl endpoint status --cluster -w json \| jq '.[].Status.dbSize'` |
| API server request latency p99 (ms, non-streaming) | > 1000ms | > 3000ms | `oc get --raw /metrics \| grep 'apiserver_request_duration_seconds_bucket'` |
| Node NotReady count | > 0 | > 2 | `oc get nodes \| grep -c NotReady` |
| etcd database size (GB) | > 6GB | > 7.5GB | `oc exec -n openshift-etcd etcd-<member> -- etcdctl endpoint status --cluster -w table \| awk '{print $8}'` |
| HAProxy router 5xx error rate (%) | > 1% | > 5% | `oc exec -n openshift-ingress <router-pod> -- curl -s localhost:1936/metrics \| grep 'haproxy_backend_http_responses_total{code="5xx"}'` |
| CPU utilization across all nodes (%) | > 75% | > 90% | `oc adm top nodes \| awk 'NR>1 {print $3}' \| sort -n \| tail -1` |
| Pending pod count (unschedulable) | > 5 | > 20 | `oc get pods --all-namespaces --field-selector=status.phase=Pending \| wc -l` |
| 1 of N MachineConfigPool nodes not yet applied latest MachineConfig | `machineconfiguration.openshift.io/state` annotation shows `Degraded` for that node | Node running old config (kernel params, certs, etc.); security/compliance drift | `oc get nodes -o custom-columns='NAME:.metadata.name,MCP:.metadata.annotations.machineconfiguration\.openshift\.io/state'` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| etcd DB size | Trending above 6GB (hard limit 8GB); weekly growth rate > 500MB | Run compaction and defrag; audit large key spaces; consider etcd quota increase | 2–4 weeks |
| Node CPU allocatable utilization | Cluster-wide requested CPU >75% of allocatable across worker nodes | Add worker nodes or right-size overprovisioned workloads before reaching 90% | 1–2 weeks |
| Node memory allocatable utilization | Cluster-wide requested memory >80% of allocatable | Add nodes; review memory requests for overprovisioned namespaces with `oc describe node` | 1–2 weeks |
| Persistent Volume Claim usage | PVC utilization trending above 80% on stateful workloads | Expand PVCs proactively: `oc patch pvc <name> -p '{"spec":{"resources":{"requests":{"storage":"<new-size>"}}}}'` | 3–7 days |
| Image registry disk usage | Internal registry PV trending above 70% | Enable image pruning policy: `oc adm prune images --confirm`; schedule recurring prune cronjob | 1–2 weeks |
| API server request rate | `oc get --raw /metrics \| grep apiserver_request_total` growing toward known saturation (~3000 RPS) | Identify top API clients via audit logs; implement ResourceQuota and rate limiting per namespace | 1–2 weeks |
| Cluster operator degraded count | Any `oc get co \| grep -v "True.*False.*False"` showing degraded operators for >15 min | Investigate degraded operator immediately to prevent cascading failures | Immediate |
| Certificate expiry (internal PKI) | `oc get secret -A -o json \| jq '.items[] \| select(.metadata.annotations["auth.openshift.io/certificate-not-after"] != null) \| {ns: .metadata.namespace, name: .metadata.name, expiry: .metadata.annotations["auth.openshift.io/certificate-not-after"]}'` approaching within 60 days | Trigger manual certificate rotation for any cert not auto-renewed; verify cert-manager is operational | 60 days |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check overall cluster node status
oc get nodes -o wide

# List all pods not in Running/Completed state across all namespaces
oc get pods -A --field-selector='status.phase!=Running,status.phase!=Succeeded' -o wide

# Tail API server logs for authentication or authorization errors
oc logs -n openshift-kube-apiserver -l app=openshift-kube-apiserver --since=5m | grep -E "error|Unauthorized|Forbidden" | tail -50

# Check cluster operator health (any degraded or unavailable operators)
oc get clusteroperators | grep -v "True.*False.*False"

# Inspect recent cluster events sorted by timestamp
oc get events -A --sort-by='.lastTimestamp' | tail -30

# Check etcd cluster health and member status
oc exec -n openshift-etcd etcd-$(oc get pods -n openshift-etcd -l app=etcd -o jsonpath='{.items[0].metadata.name}') -- etcdctl endpoint health --cluster

# Verify all master nodes are scheduling and not cordoned
oc get nodes -l node-role.kubernetes.io/master -o custom-columns='NAME:.metadata.name,STATUS:.status.conditions[-1].type,SCHEDULABLE:.spec.unschedulable'

# Check PersistentVolume claim binding issues
oc get pvc -A | grep -v Bound

# List any failing or backoff-looping deployments
oc get deployments -A -o json | jq '.items[] | select(.status.unavailableReplicas > 0) | {ns: .metadata.namespace, name: .metadata.name, unavailable: .status.unavailableReplicas}'

# Audit who has cluster-admin across all bindings
oc get clusterrolebindings -o json | jq '.items[] | select(.roleRef.name=="cluster-admin") | {binding: .metadata.name, subjects: .subjects}'
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| API server availability | 99.95% | `1 - (rate(apiserver_request_total{code=~"5.."}[5m]) / rate(apiserver_request_total[5m]))` | 21.9 min | >72x burn rate |
| API server request latency p99 < 1s | 99.9% | `histogram_quantile(0.99, rate(apiserver_request_duration_seconds_bucket{verb!~"WATCH|LIST"}[5m])) < 1` | 43.8 min | >36x burn rate |
| Node readiness (fraction of nodes in Ready state) | 99.5% | `sum(kube_node_status_condition{condition="Ready",status="true"}) / count(kube_node_status_condition{condition="Ready"})` | 3.6 hr | >6x burn rate |
| Workload pod availability (desired == available) | 99% | `sum(kube_deployment_status_replicas_available) / sum(kube_deployment_spec_replicas)` | 7.3 hr | >5x burn rate |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| No cluster-admin wildcard role bindings | `oc get clusterrolebindings -o json \| jq '.items[] \| select(.roleRef.name=="cluster-admin") \| {binding: .metadata.name, subjects: .subjects}'` | Only explicitly approved service accounts and break-glass users listed |
| API server audit logging enabled | `oc get apiservers cluster -o jsonpath='{.spec.audit.profile}'` | `Default`, `WriteRequestBodies`, or `AllRequestBodies` (not `None`) |
| Etcd encryption at rest enabled | `oc get apiserver cluster -o jsonpath='{.spec.encryption.type}'` | `aescbc` or `aesgcm` |
| Image registry storage is persistent | `oc get configs.imageregistry.operator.openshift.io cluster -o jsonpath='{.spec.storage}'` | `pvc` or object storage configured; not `emptyDir` |
| SCC restricted-v2 applied to workloads | `oc get pods -A -o json \| jq '[.items[] \| select(.metadata.annotations["openshift.io/scc"] == "privileged") \| {ns: .metadata.namespace, pod: .metadata.name}]'` | Only system-level pods use `privileged` SCC |
| Network policy default-deny in place | `oc get networkpolicy -A -o json \| jq '[.items[] \| select(.spec.podSelector == \\{\\}) \| {ns: .metadata.namespace, name: .metadata.name}]'` | Each tenant namespace has a default-deny NetworkPolicy |
| Machine config pool not degraded | `oc get mcp` | All pools show `DEGRADED=False` and `UPDATED=True` |
| OAuth identity provider configured | `oc get oauth cluster -o jsonpath='{.spec.identityProviders}'` | At least one external identity provider present (not only `htpasswd` in production) |
| Cluster certificate expiry > 30 days | `oc get secret -n openshift-ingress router-certs-default -o jsonpath='{.data.tls\.crt}' \| base64 -d \| openssl x509 -noout -enddate` | Expiry date more than 30 days from today |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `unable to authenticate the request` | ERROR | Invalid token, expired kubeconfig, or OAuth session timed out | Re-login: `oc login`; check OAuth server pod health |
| `forbidden: User "system:anonymous" cannot` | WARN | Unauthenticated request reaching API server | Verify RBAC and ensure callers present valid bearer tokens |
| `etcd cluster is unavailable` | CRITICAL | etcd quorum lost; API server cannot persist state | Check etcd pod status in `openshift-etcd` namespace; restore quorum |
| `node not ready` | ERROR | kubelet unresponsive or node conditions (DiskPressure, MemoryPressure) | SSH to node; check `systemctl status kubelet`; review node conditions |
| `failed to garbage collect required amount of images` | WARN | Node disk nearly full; image GC cannot free enough space | Clean unused images: `crictl rmi --prune`; add disk capacity |
| `machine config pool degraded` | ERROR | MachineConfig rollout failed on one or more nodes | `oc describe mcp <pool-name>` to find failing node; check `machine-config-daemon` logs on that node |
| `failed to list *v1.Pod: the server is currently unable to handle the request` | ERROR | API server overloaded or in partial failure | Check `kube-apiserver` pod logs in `openshift-kube-apiserver`; check etcd latency |
| `OAuthClient redirect URI does not match` | WARN | OAuth client registered with wrong or outdated redirect URI | Update OAuthClient redirect URI: `oc edit oauthclient <name>` |
| `image pull failed for` | ERROR | Image registry unreachable or image tag not found | Check internal registry pod; verify `ImagePullSecret`; test registry route |
| `insufficient cpu` / `insufficient memory` | WARN | No node has enough resources to schedule pod | Add nodes or adjust resource requests; check `oc describe pod <name>` for events |
| `certificate has expired or is not yet valid` | CRITICAL | TLS certificate expired on API server, ingress, or service mesh | Run `oc get csr -A` for pending certs; check cert-manager logs; rotate certs manually |
| `NetworkPolicy blocking` | WARN | Pod-to-pod traffic denied by NetworkPolicy | Review NetworkPolicies in namespace; check OVN-Kubernetes flow table |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `403 Forbidden` from API server | RBAC policy denies the requested verb on resource | Operation fails; workload may be degraded | Grant appropriate Role/ClusterRole; check `oc auth can-i` |
| `429 Too Many Requests` from API server | API server rate limit exceeded by client | Throttled clients experience delays | Review audit logs for chatty clients; reduce polling frequency; increase `--max-requests-inflight` |
| `503 Service Unavailable` on Routes | Ingress controller (HAProxy Router) cannot reach backend pod | External traffic fails for affected Route | Check pod readiness; verify Route `targetPort` matches Service |
| `ImagePullBackOff` | Image cannot be pulled from registry | Pod stuck, workload unavailable | Verify image exists; check pull secret; test: `oc debug node/<node> -- crictl pull <image>` |
| `CrashLoopBackOff` | Container repeatedly crashes after start | Workload unavailable | `oc logs <pod> --previous`; check liveness probe settings |
| `Degraded` MachineConfigPool | MachineConfig rollout failed on a node | Node stuck at old config; cluster update blocked | `oc describe mcp <pool>` → find failing node; check `machine-config-daemon` logs |
| `Progressing=True` longer than expected (ClusterOperator) | Cluster operator update taking too long | Cluster upgrade stalled | `oc get co`; `oc describe co <operator-name>` for events and conditions |
| `Available=False` (ClusterOperator) | Cluster operator degraded; component not serving | Core platform feature unavailable | Check operator pod logs in its namespace; review operator conditions |
| `etcd: request timed out` | etcd write or read exceeded deadline | API server operations slow or failing | Check etcd member health; investigate disk I/O on etcd nodes |
| `SCC denied` | Pod security admission rejected pod spec | Pod fails to start | Adjust SCC or pod security context to match; avoid `privileged` unless required |
| `node is being drained` / `pod evicted` | Node cordon + drain in progress | Pods rescheduled; brief disruption | Ensure PodDisruptionBudgets set; monitor rescheduling in target namespace |
| `certificate signing request pending` | CSR awaiting approval (kubelet cert rotation) | Node may go `NotReady` if cert expires | `oc get csr -A`; approve pending CSRs: `oc adm certificate approve <csr>` |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| etcd I/O Saturation | `etcd_disk_wal_fsync_duration_seconds` p99 > 10ms; API server latency rising | `etcd: request timed out`; `slow fdatasync` | `etcdHighFsyncDuration` | etcd node disk I/O saturated; often shared storage under load | Move etcd to dedicated NVMe; isolate etcd nodes from storage-heavy workloads |
| Node NotReady — DiskPressure | Node condition `DiskPressure=True`; pods evicting | `failed to garbage collect required amount of images` | `NodeDiskPressure` | Node disk full; ephemeral storage or image cache exhausted | Free disk on node; extend PVC; add node to cluster |
| API Server Certificate Expiry | API server TLS errors; kubectl auth failures | `certificate has expired` | `KubeAPIServerCertExpiry` | API server or kubelet cert expired; auto-rotation failed | Approve pending CSRs; manually rotate cert; check cert-manager |
| Image Registry Route Down | `ImagePullBackOff` across all namespaces simultaneously | `image pull failed` for internal registry FQDN | `InternalRegistryDown` | Internal image registry pod unhealthy or route misconfigured | Check `image-registry` operator and pod in `openshift-image-registry` |
| MCP Degraded After Config Push | `mcp_degraded_machine_count > 0` | `machine-config-daemon: failed to apply config` | `MCPDegraded` | MachineConfig contains syntax error or conflicting config | Identify bad MC; pause MCP; revert MC; resume MCP |
| SCC Policy Regression | Pod creation failures across namespace post-upgrade | `SCC denied` in admission webhook logs | `PodAdmissionFailureRate` | OpenShift upgrade tightened default SCC; pod specs use removed capabilities | Audit pod specs; apply `restricted-v2` compliant security context |
| OAuth Server Down | All web console logins fail; `oc login` returns 503 | `oauth-server: failed to authenticate` | `OAuthServerDown` | `oauth-server` pod crashed or misconfigured identity provider | Restart oauth-server pod; check identity provider connectivity |
| ClusterOperator Available=False | `cluster_operator_conditions{condition="Available",status="False"}` | Operator logs showing reconcile failure | `ClusterOperatorDegraded` | Bug in operator version or dependent resource missing | Review operator logs; check if CRD or ConfigMap dependency deleted |
| Upgrade Stalled on Operator | `cluster_version_operator_update_duration` plateauing | `waiting for cluster operator <name>` in CVO logs | `ClusterUpgradeStalled` | Operator pod failing during upgrade; dependency deadlock | Force-restart stuck operator pod; check operator dependencies |
| RBAC Misconfiguration After Namespace Migration | Pods returning `403 Forbidden` for service-account calls | `forbidden: User "system:serviceaccount:..."` | `RBACDenialRateHigh` | RoleBinding pointing to wrong namespace or deleted ServiceAccount | Re-create RoleBinding in correct namespace; verify ServiceAccount exists |

---

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `kubectl`/`oc` returns `Error from server (ServiceUnavailable)` | oc CLI, kubectl, Kubernetes SDK | API server pod down or overloaded; etcd quorum lost | `oc get clusteroperators kube-apiserver`; check `oc get pods -n openshift-kube-apiserver` | Restart API server pods; fix etcd issues; check master node health |
| `Error from server (Forbidden): pods is forbidden` | oc, kubectl, Helm, Terraform | RBAC misconfiguration; ServiceAccount missing role | `oc auth can-i <verb> <resource> --as=system:serviceaccount:<ns>:<sa>` | Add RoleBinding; verify ServiceAccount name matches deployment |
| `ImagePullBackOff` / `ErrImagePull` for internal images | Kubernetes (kubelet) | Internal registry unavailable; ServiceAccount missing image-puller role; quota exceeded | `oc get pods -n openshift-image-registry`; `oc describe pod <pod>` for pull error detail | Fix registry; add `system:image-puller` role; increase quota |
| `OOMKilled` — pod restarts repeatedly | Application runtime | Memory limits too low; memory leak in app | `oc describe pod <pod>` shows `OOMKilled`; check `container_memory_working_set_bytes` | Increase memory limit; profile app memory; enable VPA |
| `CrashLoopBackOff` on pod startup | Application | Application error on init; missing ConfigMap or Secret; SCC violation | `oc logs <pod> --previous`; `oc describe pod <pod>` events | Fix app startup error; ensure all env/secrets mounted; check SCC |
| Route returning `503 Application Not Available` | Browser, HTTP client | No healthy pod endpoints behind service; all pods failing readiness | `oc get endpoints <svc>`; `oc get pods -l <selector>` | Fix readiness probe; fix crashing pods; check HPA min replicas |
| `429 Too Many Requests` from API server | oc, kubectl, CI/CD pipelines | API server rate-limiting client (FlowControl); too many requests per second | Check `apiserver_flowcontrol_rejected_requests_total` metric; look at request rate per user | Reduce polling frequency in controllers; implement client-side backoff; increase flow control quota |
| `context deadline exceeded` on `oc apply` | kubectl, Terraform, Helm | API server slow (etcd degraded); network latency to master nodes | `time oc get pods` for latency; check etcd WAL fsync latency | Fix etcd disk I/O; increase client timeout; investigate network between client and master |
| `admission webhook denied` with no clear message | oc, kubectl, Helm | OPA/Gatekeeper or custom admission webhook blocking resource | `oc describe pod <pod>` for webhook error message; `oc get validatingwebhookconfigurations` | Fix policy violation; disable webhook temporarily for debugging; check webhook logs |
| Pod stuck in `Pending` indefinitely | kubectl, monitoring | Insufficient node resources; node selector mismatch; PVC not bound | `oc describe pod <pod>` events section; `oc get nodes` for capacity | Add nodes; fix resource requests; fix PVC storage class; remove conflicting node selectors |
| `TLS handshake error` connecting to OpenShift API | Any TLS client | API server certificate expired; CA bundle outdated in client config | `openssl s_client -connect <api-server>:6443`; check cert expiry | Approve pending CSRs; renew API server cert; update kubeconfig CA |
| `oc login` returns `401 Unauthorized` | oc CLI | OAuth server down; identity provider misconfigured; token expired | `oc get pods -n openshift-authentication`; check OAuth server logs | Restart oauth-server pods; fix identity provider config; re-login |

---

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| etcd DB size growth toward 8 GB limit | `etcd_mvcc_db_total_size_in_bytes` increasing > 50 MB/day | `oc rsh -n openshift-etcd etcd-<node> etcdctl endpoint status --cluster -w table` | 1–2 weeks before compaction emergency | Enable auto-compaction; schedule periodic defragmentation on each member |
| Node resource headroom shrinking | `allocatable_cpu` and `allocatable_memory` decreasing across fleet as workloads expand | `kubectl describe nodes \| grep -A5 "Allocated resources"` | Days | Add new nodes via MachineSet; review and reduce oversized resource requests via VPA |
| MachineConfig update queue growing | `MachineConfigPool` showing increasing number of machines with outdated config | `oc get mcp -o json \| jq '.items[] \| {name:.metadata.name,degraded:.status.degradedMachineCount,updating:.status.updatingMachineCount}'` | Hours to days | Investigate degraded machines; pause MCP if update is problematic; fix failed nodes first |
| Cluster operator degraded state accumulating | More ClusterOperators entering `Degraded=True` after upgrade or change | `oc get clusteroperators \| grep -v True.*False.*False` | Hours before user-visible failures | Investigate each degraded operator; prioritize kube-apiserver, etcd, network operators |
| Image registry storage growth | Registry PVC usage approaching capacity limit | `oc exec -n openshift-image-registry deployment/image-registry -- df -h /registry` | Days | Prune unused images: `oc adm prune images --confirm`; expand PVC |
| Certificate expiry across cluster PKI | Internal certificate `notAfter` dates within 90 days | `oc -n openshift-config get secret \| xargs -I{} sh -c 'oc get secret {} -n openshift-config -o json 2>/dev/null \| jq -r ".data.\"tls.crt\" // empty \| @base64d" 2>/dev/null \| openssl x509 -noout -dates 2>/dev/null'` | 30–90 days | Enable auto-rotation; force rotation via cluster operator; check cert-manager CRs |
| API server audit log volume growth | Audit log partition usage increasing; log rotation not keeping pace | `oc adm node-logs --role=master --path=kube-apiserver/audit.log \| wc -c` | Days | Tune audit policy verbosity; increase log rotation frequency; forward to external SIEM |
| Node OS disk usage growth from pod logs | Node `DiskPressure` condition appearing intermittently before sustained pressure | `oc adm node-logs <node> --unit=kubelet \| grep DiskPressure` | Hours | Configure container log rotation (`--container-log-max-size`); add monitoring for node disk usage |

---

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# OpenShift full health snapshot
set -euo pipefail

echo "=== Cluster Version and Update Status ==="
oc get clusterversion

echo ""
echo "=== Cluster Operators Health ==="
oc get clusteroperators

echo ""
echo "=== Node Status ==="
oc get nodes -o wide

echo ""
echo "=== etcd Member Health ==="
ETCD_POD=$(oc get pods -n openshift-etcd -l app=etcd -o name | head -1)
[ -n "$ETCD_POD" ] && oc rsh -n openshift-etcd "$ETCD_POD" etcdctl endpoint health --cluster 2>/dev/null || echo "(etcd pod not found)"

echo ""
echo "=== MachineConfigPool Status ==="
oc get machineconfigpools

echo ""
echo "=== Recent Cluster Events (warnings) ==="
oc get events --all-namespaces --field-selector type=Warning --sort-by='.lastTimestamp' 2>/dev/null | tail -20

echo ""
echo "=== Pod Restarts > 3 (All Namespaces) ==="
oc get pods --all-namespaces -o json | \
  jq -r '.items[] | select(.status.containerStatuses[]?.restartCount > 3) | "\(.metadata.namespace)/\(.metadata.name): restarts=\(.status.containerStatuses[0].restartCount)"' | sort -t= -k2 -rn | head -15

echo ""
echo "=== Image Registry Health ==="
oc get pods -n openshift-image-registry
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# OpenShift performance triage
echo "=== API Server Response Latency (kubectl benchmark) ==="
time oc get pods --all-namespaces > /dev/null

echo ""
echo "=== etcd WAL fsync Latency (from metrics) ==="
# Run from a pod with metrics access or via Prometheus
oc exec -n openshift-monitoring -l prometheus=k8s prometheus-k8s-0 -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m]))" 2>/dev/null | \
  jq '.data.result[] | {instance:.metric.instance, p99:.value[1]}' || echo "(Prometheus not accessible)"

echo ""
echo "=== Node Resource Allocation Summary ==="
oc describe nodes | grep -A4 "Allocated resources:" | grep -E "cpu|memory" | \
  awk 'NR%2==1{cpu=$0} NR%2==0{print cpu, $0}'

echo ""
echo "=== Top CPU/Memory Consuming Pods ==="
oc adm top pods --all-namespaces --sort-by=cpu 2>/dev/null | head -20

echo ""
echo "=== Scheduler Pending Pods ==="
oc get pods --all-namespaces --field-selector status.phase=Pending 2>/dev/null | head -20

echo ""
echo "=== API Server Flow Control — Rejected Requests ==="
oc exec -n openshift-monitoring -l prometheus=k8s prometheus-k8s-0 -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=sum(rate(apiserver_flowcontrol_rejected_requests_total[5m]))by(flow_schema)" 2>/dev/null | \
  jq '.data.result[] | {flow_schema:.metric.flow_schema, rate:.value[1]}' | head -10 || echo "(Prometheus not accessible)"
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# OpenShift connection and resource audit

echo "=== Namespace Resource Quota Summary ==="
oc get resourcequota --all-namespaces 2>/dev/null | head -30

echo ""
echo "=== SCC Assignments by ServiceAccount (spot check) ==="
oc get pods --all-namespaces -o json 2>/dev/null | \
  jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name): scc=\(.metadata.annotations."openshift.io/scc" // "none")"' | sort | uniq -c | sort -rn | head -20

echo ""
echo "=== Certificate Expiry — API Server and Ingress ==="
for ns in openshift-config openshift-ingress; do
  echo "--- Namespace: $ns ---"
  oc get secrets -n "$ns" -o json 2>/dev/null | \
    jq -r '.items[] | select(.type=="kubernetes.io/tls") | "\(.metadata.name): \(.data["tls.crt"] // "" | @base64d)"' | \
    grep -v '^$' | while IFS=: read name cert; do
      echo "$name" | xargs -I{} sh -c 'echo "{}: " && echo "'"$cert"'" | openssl x509 -noout -dates 2>/dev/null'
    done 2>/dev/null || echo "(could not read secrets in $ns)"
done

echo ""
echo "=== etcd DB Size and Leader ==="
ETCD_POD=$(oc get pods -n openshift-etcd -l app=etcd -o name 2>/dev/null | head -1)
[ -n "$ETCD_POD" ] && oc rsh -n openshift-etcd "$ETCD_POD" etcdctl endpoint status --cluster -w table 2>/dev/null || echo "(etcd not accessible)"

echo ""
echo "=== MachineSet Scaling Capacity ==="
oc get machinesets -n openshift-machine-api -o json 2>/dev/null | \
  jq -r '.items[] | "\(.metadata.name): desired=\(.spec.replicas) ready=\(.status.readyReplicas // 0)"'

echo ""
echo "=== Admission Webhook Configurations ==="
oc get validatingwebhookconfigurations,mutatingwebhookconfigurations 2>/dev/null | head -20
```

---

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Namespace CPU/memory quota exhaustion by one team | Other teams in same cluster cannot schedule pods; `Insufficient cpu` or `memory` in scheduler events | `oc describe namespace <ns> \| grep -A10 quota`; identify namespace consuming most resources | Reduce quota for over-consuming namespace; add nodes | Enforce per-namespace `ResourceQuota` with CPU/memory limits; use LimitRange for per-pod defaults |
| etcd write contention from many controller reconcile loops | API server write latency increases; `etcd_request_duration_seconds` p99 rising | Check `etcd_server_proposals_pending` metric; audit ClusterOperator reconcile rates | Throttle overly aggressive controllers; reduce informer resync period for non-critical operators | Set appropriate resync periods; use work queue rate limiting in custom operators |
| Node DiskPressure from one namespace's pod logs | kubelet evicts pods cluster-wide from same node | `kubectl describe node <node>` shows DiskPressure; `du -sh /var/lib/docker/containers/*` on node | Drain and cordon node; enforce log rotation for noisy namespace | Set `--container-log-max-size 50Mi --container-log-max-files 3` in kubelet config via MachineConfig |
| Image registry overloaded by CI/CD pipeline pulls | Application pods across cluster experiencing slow image pulls | `oc get pods -n openshift-image-registry`; registry pod CPU/memory metrics | Add image pull rate limiting; enable node-level image caching | Use `imagePullPolicy: IfNotPresent`; deploy registry with replicas; add resource limits to registry |
| High-cardinality custom metrics flooding Prometheus | Prometheus memory OOM; scrape timeouts affecting all cluster monitoring | `prometheus_tsdb_head_series` total; identify labels with high cardinality via `topk(10, count by(__name__,job)({__name__=~".+"}))` | Drop high-cardinality metrics at scrape time with `metricRelabelings`; add `honorLabels: false` | Enforce metric cardinality standards in operator review; use `MetricRelabelConfig` in ServiceMonitor |
| MachineConfig rollout monopolizing nodes during business hours | Rolling node reboots causing pod churn cluster-wide; HPA autoscaling fighting reboots | `oc get mcp` showing `UPDATING=True`; check node drain events | Pause MCP during business hours: `oc patch mcp worker --patch '{"spec":{"paused":true}}'` | Schedule MachineConfig rollouts during maintenance windows; use maxUnavailable=1 in MCP |
| Cluster-admin misconfigured RBAC allowing namespace sprawl | Namespace count growing; API server list operations slow; etcd object count high | `oc get namespaces \| wc -l`; `etcd_object_counts` metric | Set namespace limit via LimitRange or quota controller; delete unused namespaces | Implement namespace lifecycle policy; require team ownership tags; use Namespace admission controller |
| Ingress controller shared by all namespaces — bandwidth monopoly | Certain namespaces' traffic causing ingress throttling for others | Check ingress controller `nginx_ingress_controller_requests` rate by namespace; CPU/memory of ingress pod | Dedicate separate ingress controller per high-traffic namespace; add rate limiting annotations | Provision separate ingress classes per tier; use `IngressClass` to isolate traffic |
| Route certificate renewal storm | All routes attempting ACME renewal simultaneously; cert-manager rate-limited | `oc get challenges --all-namespaces`; check cert-manager logs for `429 Too Many Requests` from ACME | Stagger renewals by using different `renewBefore` values; manually renew critical certs first | Set varied `renewBefore` windows (e.g., 720h, 600h, 480h) across certificates to spread ACME load |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| etcd quorum loss (2/3 members down) | API server becomes read-only → all controllers stop reconciling → no new pods scheduled → running workloads unaffected but no healing | Entire cluster control plane; all namespaces lose deployments, scaling, service discovery updates | `etcdctl endpoint health --cluster` shows unhealthy; `oc get nodes` hangs; `apiserver_request_total` drops to near 0 | Restore etcd member from snapshot; do NOT restart API servers until etcd quorum restored |
| Node NotReady storm (>30% nodes fail) | Scheduler cannot place evicted pods → pending pod queue floods → Horizontal Pod Autoscaler triggers scale-up → cloud provider node quota hit | All namespaces; stateful apps lose PV access if PVs are zone-local | `oc get nodes \| grep NotReady \| wc -l`; `kube_node_status_condition{condition="Ready",status="false"}` spikes | Cordon affected nodes; taint with `node.kubernetes.io/unreachable:NoSchedule`; check cloud provider status |
| Ingress controller crash/restart loop | All external HTTP/HTTPS traffic drops → services unreachable → health checks fail → upstream load balancers mark backends down | All routes served by that ingress controller; affects all tenants sharing it | `oc get pods -n openshift-ingress \| grep CrashLoop`; `haproxy_backend_active_servers` drops to 0 | Scale up ingress replicas: `oc scale deployment router-default -n openshift-ingress --replicas=3`; check HAProxy config |
| Image registry unavailable | New pod deployments fail with `ErrImagePull`; CI/CD pipelines stall; rolling updates hang | All namespaces that pull images from internal registry; ongoing deployments freeze mid-rollout | `oc get pods -n openshift-image-registry`; `container_pull_errors_total` rises; events show `Back-off pulling image` | Pin deployments to already-pulled images with `imagePullPolicy: Never`; restore registry PVC or S3 backend |
| Cluster autoscaler misconfiguration removes needed nodes | Pods evicted → rescheduled → autoscaler removes replacement nodes too → eviction loop | Production namespaces with PodDisruptionBudget violations; stateful sets lose replicas | `oc get machineset -n openshift-machine-api`; autoscaler logs: `scale down blocked by PDB`; pod pending duration > 10 min | Pause autoscaler: `oc annotate clusterautoscaler default cluster-autoscaler.kubernetes.io/safe-to-evict=false`; manually scale machineset |
| Upstream DNS failure (CoreDNS crash) | Service discovery breaks → all inter-service calls fail → cascades to every microservice | All namespaces; cluster-internal DNS stops resolving `svc.cluster.local` names | `oc exec -n default <pod> -- nslookup kubernetes.default` fails; CoreDNS pod restarts in `oc get pods -n openshift-dns` | Restart CoreDNS: `oc rollout restart deployment/dns-default -n openshift-dns`; check ConfigMap for corrupt zones |
| MachineConfig operator stuck → nodes never reboot | Security patches not applied; new MachineConfig changes queued indefinitely; nodes diverge from desired state | All nodes in affected MachineConfigPool; long-term security compliance drift | `oc get mcp worker -o jsonpath='{.status.conditions}'`; MCO pod logs: `waiting for node to finish draining`; `DEGRADED=True` | Drain stuck node manually: `oc adm drain <node> --ignore-daemonsets --delete-emptydir-data`; unpause MCP |
| OAuth/API server cert expiry | All oc/kubectl clients get `x509: certificate has expired`; CI/CD breaks; operators lose API access | Entire cluster administration; all automated tooling fails simultaneously | `oc get secret kube-apiserver-to-kubelet-signer -n openshift-kube-apiserver-operator -o json \| jq '.metadata.annotations'`; TLS handshake errors in API server logs | Rotate certs: `oc adm ocp-certificates regenerate-top-level`; check `openshift-kube-apiserver-operator` pod logs |
| Prometheus/Alertmanager OOM | No alerting; runaway metrics undetected; capacity planning blind; SLO burn undetected | Cluster-wide observability; all teams lose dashboards and alerts silently | `oc get pods -n openshift-monitoring \| grep OOMKilled`; `prometheus_tsdb_head_series` > 10M; node memory pressure | Increase Prometheus memory: patch `prometheusK8s` in `cluster-monitoring-config`; drop high-cardinality metrics |
| Admission webhook timeout/failure (ValidatingWebhookConfiguration) | All API write requests rejected cluster-wide if webhook has `failurePolicy: Fail`; deployments, configmaps, secrets all blocked | Every namespace on the cluster; GitOps/CD pipelines completely blocked | `oc get events --all-namespaces \| grep webhook`; API server logs: `failed calling webhook`; `apiserver_admission_webhook_rejection_count` spikes | Disable failing webhook: `oc delete validatingwebhookconfiguration <name>`; or patch `failurePolicy: Ignore` temporarily |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| OpenShift minor version upgrade (e.g., 4.13→4.14) | API deprecations break existing manifests; operators restart; nodes reboot sequentially causing brief pod disruption | 30–90 min during upgrade rollout | `oc get clusterversion`; `oc get co` shows operators `DEGRADED`; check `oc adm upgrade` history | `oc adm upgrade --to-image=<previous>` if supported; else forward-patch broken manifests |
| MachineConfig change (kernel args, sysctl, file addition) | Nodes reboot one-by-one (MCP maxUnavailable); pods disrupted per drain cycle; if config is invalid, nodes enter `Degraded` | 5–15 min per node in pool | `oc get mcp`; `oc describe mcp worker` shows `Degraded`; journal on node: `journalctl -u machine-config-daemon` | Revert MachineConfig: `oc delete mc <bad-mc>`; MCD will re-render and re-apply previous config |
| Ingress controller default certificate rotation | Wildcard cert updated but old clients cached old cert → TLS mismatch for in-flight connections; brief 503s during rotation | Seconds to minutes (connection teardown) | Ingress controller logs: `tls: no certificates configured`; `openssl s_client -connect <route>:443` shows cert mismatch | `oc patch ingresscontroller default -n openshift-ingress-operator --patch '{"spec":{"defaultCertificate":{"name":"<old-secret>"}}}'` |
| NetworkPolicy change blocking inter-namespace traffic | Services suddenly unreachable across namespaces; apps return connection refused | Immediate (policy applied in seconds via OVN-Kubernetes) | `oc describe networkpolicy -n <ns>`; `oc exec <pod> -- curl -v http://<service>.<ns>.svc` returns `Connection refused` | Revert NetworkPolicy: `oc delete networkpolicy <name> -n <ns>`; re-apply previous YAML |
| StorageClass or PV reclaim policy change | PVs deleted on PVC removal (Reclaim: Delete vs Retain); data loss if policy changed to Delete retroactively | Instant on PVC deletion | `oc get pv -o custom-columns=NAME:.metadata.name,POLICY:.spec.persistentVolumeReclaimPolicy,STATUS:.status.phase` | Recover from volume snapshot if available; restore reclaim policy: `oc patch pv <pv> -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'` |
| RBAC/SCC change tightening permissions | Existing pods continue running; new pods or restarts fail with `unable to validate against any security context constraint` | On next pod restart or new deployment | `oc get events -n <ns> \| grep SCC`; `oc adm policy who-can use scc anyuid` | Re-grant SCC: `oc adm policy add-scc-to-serviceaccount <scc> -z <sa> -n <ns>`; audit what was removed |
| etcd compaction / defragmentation during business hours | API server write latency spike during defrag (seconds to minutes); controllers queue up; brief reconcile storms after defrag completes | Immediate during defrag window | `etcd_disk_wal_fsync_duration_seconds` p99 spikes; API server slow log shows > 1s requests; etcd leader election events | Schedule defrag during off-hours; if ongoing: `etcdctl defrag --cluster` from etcd pod with `--command-timeout=30s` |
| Cluster Autoscaler min/max node count change | Immediate scale-down if new max < current nodes; pods evicted unexpectedly | Within autoscaler sync period (10s default) | `oc get clusterautoscaler -o yaml`; autoscaler logs: `removing node`; pod eviction events | Patch autoscaler min back: `oc patch clusterautoscaler default --patch '{"spec":{"resourceLimits":{"maxNodesTotal":N}}}'` |
| Operator version bump (OLM upgrade) | Operator CRDs change; existing CRs may become invalid; operator reconcile loop errors until CRs updated | Minutes to hours after upgrade | `oc get csv -n <operator-ns>`; operator pod logs show `no kind is registered for the type`; `oc get events -n <ns> \| grep failed` | Pin operator version: `oc patch subscription <name> -n <ns> --patch '{"spec":{"startingCSV":"<previous-csv>"}}'`; `oc delete installplan <ip>` |
| Prometheus scrape config change adding high-cardinality target | Prometheus heap grows rapidly → OOM → monitoring blackout | 5–30 min after config applied | `prometheus_tsdb_head_series` crosses 10M; Prometheus pod memory usage in `oc top pod -n openshift-monitoring`; OOMKilled events | Remove scrape config: revert `additionalScrapeConfig` secret; restart Prometheus; add cardinality limits to ServiceMonitor |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| etcd split-brain (network partition between etcd members) | `oc rsh -n openshift-etcd <etcd-pod> etcdctl endpoint status --cluster -w table` shows diverging `RAFT_TERM` | Two members elect themselves leader; API server may connect to minority partition; writes rejected | Cluster control plane unreachable; no pod scheduling; state changes lost | Isolate minority partition; force-restore majority quorum; rejoin minority member from snapshot |
| etcd member data divergence after failed restore | `etcdctl endpoint status` shows member with `DB_SIZE` significantly smaller than peers | Restored member has stale state; controllers see phantom or missing objects | Inconsistent object state; operators fight over object ownership | Remove diverged member: `etcdctl member remove <ID>`; add fresh member: `etcdctl member add`; let it sync from leader |
| etcd revision lag (slow follower) | `etcdctl endpoint status` shows `RAFT_APPLIED_INDEX` far behind leader | Member behind is serving stale reads if client connects to it | Applications reading stale config, secrets, or service endpoint data | Investigate disk I/O on slow member node (`iostat -x 1`); if persistent, remove and re-add member |
| Config drift between MachineConfig rendered and applied on node | `oc get node <node> -o jsonpath='{.metadata.annotations.machineconfiguration\.openshift\.io/currentConfig}'` vs `desiredConfig` differ | Node in `Degraded` state; MCO cannot reconcile; applications potentially running on non-compliant node | Security and compliance drift; node may not have required kernel args or files | SSH to node; check `journalctl -u machine-config-daemon -n 100`; force re-render: `oc delete pod -n openshift-machine-config-operator -l k8s-app=machine-config-daemon --field-selector spec.nodeName=<node>` |
| Namespace quota vs actual usage inconsistency | `oc describe resourcequota -n <ns>` shows used > hard for CPU/memory | Pods cannot be scheduled despite resources appearing available; quota controller has stale counts | New deployments blocked; false resource exhaustion alerts | Force quota recalculation: `oc delete resourcequota <name> -n <ns>` and re-apply; or restart quota-controller pod |
| Service endpoints stale after pod IP change | `oc get endpoints <svc> -n <ns>` shows old pod IPs not matching `oc get pods -n <ns> -o wide` | Traffic routed to terminated pods → connection refused; only affects connections through Service | Random request failures; load balancer health checks passing but backends dead | Restart kube-proxy/OVN pod on affected node: `oc delete pod -n openshift-ovn-kubernetes -l app=ovnkube-node --field-selector spec.nodeName=<node>` |
| Image registry mirror cache serving stale layers | `oc debug node/<node>` then `crictl images` shows old digest for same tag | Pods running old code despite successful image push; image tag points to new digest but node cache has old | Silent code rollback; pods run wrong version without error | Delete image from node cache: `crictl rmi <image>`; force pod restart with `imagePullPolicy: Always` |
| Persistent Volume claim bound to wrong PV after node failure | `oc get pvc -n <ns>` shows Bound but pod mounts wrong data volume | Application reads/writes to incorrect data set; data corruption possible | Data integrity violation; potential cross-tenant data exposure | Unmount immediately; compare PV `claimRef` vs PVC `volumeName`; correct binding: delete PVC, patch PV claimRef, re-create PVC |
| Route certificate mismatch (edge vs passthrough TLS termination) | `openssl s_client -connect <route-host>:443 \| openssl x509 -noout -subject` shows wrong CN | Clients get cert for different hostname; TLS errors; browser warnings | Users unable to connect securely; API clients reject connection | `oc patch route <name> -n <ns> --patch '{"spec":{"tls":{"termination":"edge","certificate":"...", "key":"..."}}}'`; verify with openssl |
| OAuth token cache serving revoked tokens | `oc get oauthaccesstokens \| grep <user>` shows deleted token still accepted | Users whose tokens were revoked can still authenticate; security control bypass | Security incident — unauthorized access window until token TTL expires | Restart oauth-server: `oc rollout restart deployment/oauth-openshift -n openshift-authentication`; purge token cache: delete all OAuthAccessToken objects for user |

## Runbook Decision Trees

### Decision Tree 1: API Server Unavailable / Cluster Unreachable

```
Is `oc get nodes` responding?
├── YES → Is `oc get co` showing degraded operators?
│         ├── YES → Check which operator is degraded: `oc get co | grep -v "True.*False.*False"`
│         │         └── Inspect operator pod logs: `oc logs -n <operator-ns> <pod> --tail=100`
│         └── NO  → Transient blip resolved; confirm with `oc get cs` and close ticket
└── NO  → Is the API server pod running? (check: `oc get pods -n openshift-kube-apiserver`)
          ├── YES → Is the pod OOMKilled? (`oc describe pod <apiserver-pod> -n openshift-kube-apiserver | grep OOM`)
          │         ├── YES → Root cause: API server OOM → Fix: increase memory limit in `kubeapiserver` CR; rolling restart
          │         └── NO  → Check TLS cert expiry: `openssl x509 -enddate -noout -in /etc/kubernetes/static-pod-resources/configmaps/kube-apiserver-cert-syncer-kubeconfig/kubeconfig`
          └── NO  → Is etcd healthy? (`oc exec -n openshift-etcd <etcd-pod> -- etcdctl endpoint health --cluster`)
                    ├── YES → Root cause: API server crashed → Fix: `oc delete pod -n openshift-kube-apiserver <crashed-pod>` to force restart
                    └── NO  → Root cause: etcd quorum lost → Fix: follow etcd DR runbook; `etcdctl member list`; restore from snapshot if < 2 members healthy
                              └── NO  → Escalate: OpenShift support + bring etcd backup location, etcd member list output, and cluster-info dump
```

### Decision Tree 2: Node Not Ready — Workload Eviction Risk

```
Is `oc get nodes | grep NotReady` showing affected nodes?
├── NO  → Check for taints blocking scheduling: `oc describe node <node> | grep Taint`
│         └── Remove unwanted taint: `oc adm taint node <node> <key>-`
└── YES → Is kubelet running on the node? (check: `systemctl status kubelet` via SSH or `oc debug node/<node>`)
          ├── NO  → Root cause: kubelet crash → Fix: `systemctl restart kubelet`; check logs `journalctl -u kubelet -n 200 --no-pager`
          └── YES → Is disk pressure present? (`oc describe node <node> | grep -A5 Conditions | grep DiskPressure`)
                    ├── YES → Root cause: disk pressure eviction → Fix: clear container image cache `crictl rmi --prune`; clean up /var/log
                    └── NO  → Is memory pressure present? (`oc describe node <node> | grep MemoryPressure`)
                              ├── YES → Root cause: memory pressure → Fix: identify top consumers `oc adm top pods -A --sort-by=memory`; evict low-priority pods
                              └── NO  → Root cause: network partition or CNI failure → Fix: restart OVN-Kubernetes pod on node `oc delete pod -n openshift-ovn-kubernetes <ovn-pod-on-node>`
                                        └── Escalate: network team + node OS team with `oc must-gather` output
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Namespace quota exhaustion | Pod bursts hit `ResourceQuota` limits; new pods Pending | `oc get resourcequota -n <ns>; oc describe resourcequota -n <ns>` | All new workloads in namespace blocked | Temporarily raise quota: `oc patch resourcequota <name> -n <ns> --type=merge -p '{"spec":{"hard":{"requests.cpu":"20"}}}'` | Set quota headroom policy; alert at 80% consumption |
| Unbound PersistentVolumeClaims accumulating | Storage costs rising; PVCs in Pending or Released state | `oc get pvc -A \| grep -v Bound` | Wasted storage billing; capacity exhaustion | Delete unused PVCs: `oc delete pvc <name> -n <ns>`; reclaim Released PVs | Set PVC reclaim policy to Delete for ephemeral workloads; automated PVC cleanup job |
| ImageRegistry storage bloat | Internal registry disk usage > 80%; push operations slow | `oc exec -n openshift-image-registry <registry-pod> -- du -sh /registry` | Registry push/pull latency; disk full stops deployments | Prune old image tags: `oc adm prune images --confirm --keep-tag-revisions=3` | Schedule weekly `oc adm prune images` CronJob; enforce image tag TTL policy |
| LimitRange too permissive — pods requesting max | Cluster node resource over-commitment; OOM on nodes | `oc describe nodes \| grep -A10 "Allocated resources"` | Node OOM → workload evictions | Set `LimitRange` default limits: `oc apply -f limitrange.yaml`; drain over-committed nodes | Enforce LimitRange with `defaultRequest` and `default` in every namespace |
| RunAway HorizontalPodAutoscaler scaling up | HPA maxReplicas hit; node group autoscaler provisioning excess nodes | `oc get hpa -A; oc get nodes \| wc -l` | Cloud VM cost surge; quota exhaustion | Set HPA `maxReplicas` lower: `oc patch hpa <name> -n <ns> -p '{"spec":{"maxReplicas":5}}'` | Define cloud provider spending alerts; set conservative HPA maxReplicas with review process |
| MachineSet over-provisioning during incident | MachineSet replicas left elevated after incident scale-up | `oc get machinesets -n openshift-machine-api` | Unnecessary cloud instance cost | Scale down: `oc scale machineset <name> -n openshift-machine-api --replicas=<target>` | Automate MachineSet scale-down after incident resolution; track replica counts in cost review |
| etcd DB size growth from orphaned objects | etcd db size > 6 GB; API server slow; etcd compaction lagging | `oc exec -n openshift-etcd <pod> -- etcdctl endpoint status --write-out=table` | API server latency; etcd alerts | Compact and defrag: `etcdctl compact $(etcdctl endpoint status --write-out=json \| jq '.[0].Status.header.revision')`; then `etcdctl defrag` | Enable auto-compaction `--auto-compaction-mode=periodic --auto-compaction-retention=1h`; alert on DB size > 4 GB |
| CrashLoopBackOff pods consuming restart quota | Containers restarting every 30s; exponential backoff eating CPU | `oc get pods -A \| grep CrashLoop \| awk '{print $1, $2, $5}'` | CPU budget wasted; node thrashing | Delete and redeploy: `oc delete pod <name> -n <ns>`; inspect logs before restart | Set `restartPolicy: OnFailure` for batch jobs; add liveness probe with `initialDelaySeconds` |
| Logging EFK stack ingesting unbounded logs | Elasticsearch disk near full; Fluentd dropping logs | `oc exec -n openshift-logging <es-pod> -- curl -s localhost:9200/_cat/indices?v` | Log loss; ES cluster going red | Apply Fluentd throttle filter; set index max size: `oc edit clusterlogging instance` | Set per-namespace log rate limits in ClusterLogForwarder; rotate and prune indices on schedule |
| Build pods not cleaned up after completion | Completed/failed build pods accumulating; etcd object count growing | `oc get builds -A \| grep -c "Complete\|Failed"` | etcd bloat; namespace list slowness | Prune builds: `oc adm prune builds --confirm --keep-complete=3 --keep-failed=1` | Configure `BuildConfig` with `successfulBuildsHistoryLimit` and `failedBuildsHistoryLimit` |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot shard on etcd key space | API server latency spikes for specific resource type; etcd slow request warnings | `oc exec -n openshift-etcd <pod> -- etcdctl check perf --load=s` | All writes to one key prefix serialized in etcd Raft | Spread writes using namespace partitioning; reduce watch count with `--watch-cache-sizes` |
| API server connection pool exhaustion | `kubectl` commands hang; API server `MaxInFlight` errors; `429 Too Many Requests` | `oc get --raw /metrics \| grep apiserver_current_inflight_requests` | Too many concurrent API requests; low `--max-requests-inflight` | Increase `--max-requests-inflight` and `--max-mutating-requests-inflight` in `kube-apiserver` CR; enable API priority and fairness |
| etcd GC/defrag memory pressure | etcd memory grows over time; slow commit latencies > 100 ms | `oc exec -n openshift-etcd <pod> -- etcdctl endpoint status --write-out=table` — check `DB SIZE` | etcd DB size bloat; fragmentation after compaction | Run defrag: `oc exec -n openshift-etcd <pod> -- etcdctl defrag`; enable auto-compaction |
| Node controller thread pool saturation | Node status updates stale; pod scheduling delayed; controller-manager log shows `queue full` | `oc logs -n openshift-controller-manager <pod> --tail=200 \| grep 'queue\|delay'` | Controller work queue backed up from too many nodes/pods | Scale controller-manager; tune `--concurrent-*-syncs` flags |
| Slow image pull from internal registry | Pod startup time > 5 min; `ImagePullBackOff` delays | `oc adm top pods -n openshift-image-registry --sort-by=cpu` | Registry pod CPU throttled; storage backend I/O slow | Increase registry pod CPU limits; use external registry with CDN |
| CPU steal degrading node workloads | High run queue on nodes; container CPU throttle despite low usage | `oc adm node-logs <node> --path=journal \| grep 'cpu steal'`; `oc debug node/<node> -- chroot /host top -d1 -b -n3 \| grep Cpu` | Hypervisor contention on cloud VM; other VMs stealing CPU cycles | Move to dedicated/isolated VM instances; use CPU-pinned pods via `cpuManagerPolicy: static` |
| API server serialization overhead | Large list operations slow; `etcd` shows large value writes | `oc get --raw '/metrics' \| grep apiserver_request_duration_seconds` | Object marshaling for large resource lists (CRD with many instances) | Enable API server `EtcdStorageType=protobuf`; use `resourceVersion` based watch instead of full list |
| Scheduler latency from pod churn | New pods take > 30 s to be assigned; scheduler queue depth growing | `oc get --raw '/metrics' \| grep scheduler_pending_pods` | High pod creation rate overwhelming scheduler; priority queue backlog | Tune scheduler `--kube-api-qps` and `--kube-api-burst`; reduce pod creation rate; use pod topology hints |
| Batch job misconfigured parallelism overwhelming API server | API server request rate 10× normal; etcd latency spikes | `oc get --raw /metrics \| grep apiserver_current_inflight_requests`; `oc get jobs -A \| awk '{print $3,$4}'` | Job `parallelism` too high; each pod hitting API on start | Set `parallelism` and `completions` proportionally; stagger job starts with `startingDeadlineSeconds` |
| Downstream Prometheus scrape increasing pod restart latency | Pods have slow liveness probe response during scrape | `oc get --raw '/metrics' \| grep scrape_duration_seconds`; `oc describe pod <pod> \| grep Liveness` | Expensive metrics collection blocking liveness probe endpoint | Separate metrics and health endpoints; increase liveness probe `timeoutSeconds` |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| Ingress router TLS cert expiry | `curl -vI https://<route>` shows cert expired; browser ERR_CERT_DATE_INVALID | `IngressController` cert not renewed; cert-manager not rotating | All HTTPS traffic to cluster routes fails | Rotate cert: update `spec.defaultCertificate` secret in `IngressController`; enable cert-manager `Certificate` CR with auto-renewal |
| Cluster API server TLS cert expiry | `oc login` fails with `x509: certificate has expired`; all cluster access broken | `openssl s_client -connect api.<cluster>:6443 2>/dev/null \| openssl x509 -noout -dates` | Cluster entirely inaccessible until cert renewed | Run `oc adm ocp-bundles` cert rotation; use `oc adm update-tls` for manual cert renewal |
| mTLS rotation failure between OVN-Kubernetes components | `NetworkPolicy` enforcement drops packets; inter-pod communication fails intermittently | `oc logs -n openshift-ovn-kubernetes -l app=ovnkube-node --tail=100 \| grep -i 'tls\|cert\|x509'` | Partial pod-to-pod network failures; service mesh disruption | Restart OVN-Kubernetes daemonset pods: `oc rollout restart ds/ovnkube-node -n openshift-ovn-kubernetes` |
| DNS resolution failure for `cluster.local` | Service discovery broken; pods cannot resolve `<svc>.<ns>.svc.cluster.local` | `oc debug node/<node> -- chroot /host nslookup kubernetes.default.svc.cluster.local`; `oc get pods -n openshift-dns` | All service-to-service calls fail with DNS errors | Check CoreDNS pods: `oc rollout restart deployment/dns-default -n openshift-dns`; check upstream DNS config in `dns.operator` CR |
| TCP connection exhaustion on node | New connections refused on node; `nf_conntrack_max` hit | `oc adm node-logs <node> --path=journal \| grep 'nf_conntrack'`; `oc debug node/<node> -- chroot /host cat /proc/sys/net/netfilter/nf_conntrack_count` | Pods on affected node cannot open new connections | Increase `nf_conntrack_max`: apply `MachineConfig` setting `net.netfilter.nf_conntrack_max=1048576` |
| Ingress load balancer misconfiguration after upgrade | Some traffic failing; load balancer health checks failing on new router pods | `oc get svc -n openshift-ingress`; `oc describe ingresscontroller default -n openshift-ingress-operator` | Subset of traffic dropped; external load balancer routing to unhealthy backends | Verify `IngressController` replicas match cloud LB backend count; force LB health check re-registration |
| Packet loss due to MTU mismatch between SDN and cloud VPC | Intermittent connection resets; large HTTP responses truncated; `ping -s 8972 <pod-ip>` fails | `oc debug node/<node> -- chroot /host ping -M do -s 8900 <gateway>` | Intermittent TCP RST for large payloads; gRPC streams dropped | Set OVN-Kubernetes `MTU` in network.operator CR to match cloud VPC MTU minus overlay overhead (cloud MTU − 100) |
| Firewall rule change blocking cluster internal traffic | `etcd` cluster unreachable from API server; OVN southbound DB connection lost | `oc exec -n openshift-etcd <pod> -- etcdctl endpoint health --cluster`; `oc get co etcd` | API server cannot persist state; cluster enters read-only or degraded mode | Restore firewall rules for port 2379/2380 (etcd) and 6443 (API); check security group changes in cloud console |
| SSL handshake timeout from expired intermediate CA | Some clients succeed; others fail with `x509: certificate signed by unknown authority` | `openssl s_client -connect <service>:443 -showcerts 2>/dev/null \| grep -A1 'Certificate chain'` — check intermediate CA dates | Partial client access failures; intermittent errors from different clients | Update cluster CA bundle: `oc create configmap custom-ca --from-file=ca-bundle.crt=<newca.crt> -n openshift-config`; patch `proxy/cluster` |
| Connection reset from OVN ACL rule after NetworkPolicy change | Existing long-lived connections drop immediately after NetworkPolicy apply | `oc logs -n openshift-ovn-kubernetes <ovnkube-node-pod> \| grep 'ACL\|drop'`; `oc describe networkpolicy <name> -n <ns>` | Active TCP sessions terminated; database connections broken | Review NetworkPolicy for missing `ingress`/`egress` rules; add explicit rules for existing long-lived connections |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of kubelet | Node goes NotReady; kubelet not responsive; pods evicted | `oc adm node-logs <node> --path=journal \| grep 'Out of memory\|oom_kill'` | Cordon node; drain pods: `oc adm drain <node> --ignore-daemonsets`; restart kubelet via MachineConfig | Set `evictionHard` memory thresholds; reserve node memory with `--kube-reserved` and `--system-reserved` |
| etcd data dir disk full | ORA-equivalent: etcd returns `no space left on device`; API server writes failing | `oc exec -n openshift-etcd <pod> -- df -h /var/lib/etcd` | Delete old revisions: `etcdctl compact $(etcdctl endpoint status --write-out=json \| jq '.[0].Status.header.revision')`; then `etcdctl defrag` | Monitor etcd disk at 70%/85%; use fast dedicated SSD for etcd (separate from OS disk) |
| Log partition full on node | Pods fail to write logs; container runtime disk I/O errors; new containers can't start | `oc debug node/<node> -- chroot /host df -h /var/log` | Delete old journal logs: `oc adm node-logs <node> --path=journal`; `journalctl --vacuum-size=1G` on node | Configure `log_rotation_size` in kubelet; set `journald` `SystemMaxUse=2G` via MachineConfig |
| File descriptor exhaustion in API server pod | API server returns 500 errors; `too many open files` in logs | `oc exec -n openshift-kube-apiserver <pod> -- cat /proc/$(pidof kube-apiserver)/limits \| grep 'open files'`; `ls /proc/$(pidof kube-apiserver)/fd \| wc -l` | Restart API server pod (rolling): `oc delete pod -n openshift-kube-apiserver <pod>`; cluster will reschedule | Set `LimitNOFILE=1048576` for API server process via node tuning; monitor FD count |
| Inode exhaustion on container runtime partition | Container creation fails with `no space left on device` despite free disk; `df -i` shows 100% | `oc debug node/<node> -- chroot /host df -i /var/lib/containers` | Clean dangling images: `podman image prune -a`; remove unused overlayfs layers: `crictl rmi --prune` | Schedule periodic image garbage collection via `imageGCHighThresholdPercent=85` in kubelet config |
| CPU throttle from cgroup limits | Pods CPU throttled > 25%; request latency elevated despite low node CPU usage | `oc adm top pods -n <ns> --containers`; `oc exec <pod> -- cat /sys/fs/cgroup/cpu/cpu.stat \| grep throttled` | Increase pod CPU limits: `oc set resources deployment <name> --limits=cpu=2`; use `BestEffort` class for batch | Set CPU requests based on measured usage; avoid setting requests = limits for latency-sensitive services |
| Swap exhaustion causing process OOM | Node memory pressure condition; pods killed by OOM killer; slow swap I/O | `oc adm node-logs <node> --path=journal \| grep 'swapping\|swap'`; `oc debug node/<node> -- chroot /host free -m` | Disable swap on node: `sudo swapoff -a` (apply via MachineConfig for persistent); drain and restart node | OpenShift 4.x recommends swap disabled; enforce via MachineConfig `kernel-arguments: - nosmt` alternative: monitor swap use |
| Kernel PID limit exhaustion | Pods cannot fork new processes; `fork: retry: Resource temporarily unavailable` in logs | `oc adm node-logs <node> --path=journal \| grep 'pid'`; `oc debug node/<node> -- chroot /host cat /proc/sys/kernel/threads-max` | Increase PID limit via MachineConfig: `sysctl kernel.pid_max=4194304`; kill runaway forking pods | Set `podPidsLimit` in kubelet config; monitor PID count per pod |
| Network socket buffer exhaustion | TCP connections drop under load; `netstat -s` shows drops; backend services unreachable | `oc debug node/<node> -- chroot /host netstat -s \| grep 'failed\|drop\|overflow'` | Increase socket buffers via MachineConfig: `net.core.rmem_max=16777216`, `net.core.wmem_max=16777216` | Apply tuning MachineConfig to all worker nodes; monitor socket buffer usage with `ss -m` |
| Ephemeral port exhaustion on node | Intermittent `connect: cannot assign requested address`; NAT masquerade failures | `oc debug node/<node> -- chroot /host cat /proc/sys/net/ipv4/ip_local_port_range`; `ss -tan state time-wait \| wc -l` | Reduce `TIME_WAIT` via `net.ipv4.tcp_tw_reuse=1`; widen ephemeral range: `echo "1024 65535" > /proc/sys/net/ipv4/ip_local_port_range` | Set port range in MachineConfig; enable `tcp_tw_reuse`; use connection pooling in pods |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Duplicate pod creation from idempotency race in controller | Same pod created twice; `kubectl get pods` shows `<name>-abc` and `<name>-abc-dup`; controller log shows duplicate reconcile | `oc logs -n openshift-controller-manager <pod> \| grep 'already exists'`; `oc get pods -A --sort-by='.metadata.creationTimestamp' \| grep -E '^(name-prefix)'` | Resource quota over-consumption; undefined behavior for singletons | Delete duplicate pod; verify controller has correct leader election (`--leader-elect=true`) |
| Operator reconcile loop partial failure — CRD status desync | Operator CR `status.conditions` shows `Progressing: True` indefinitely; actual resource state diverged | `oc get <crd-kind> -A -o json \| jq '.items[] \| select(.status.conditions[] \| select(.type=="Progressing" and .status=="True"))'` | Cluster component stuck in mid-upgrade; new features unavailable; potential data loss if partially applied | Force re-reconcile: delete and re-create CR or patch `status` to clear stale condition; restart operator pod |
| MachineConfig rolling update replay causing double-drain | Node drained twice during MCO rolling update; running workloads evicted unexpectedly | `oc get machineconfigpool -o wide`; `oc logs -n openshift-machine-config-operator <pod> \| grep 'drain\|cordoned'` | Excessive workload disruption; PDBs violated; downtime beyond maintenance window | Pause MCO: `oc patch mcp worker --type=merge -p '{"spec":{"paused":true}}'`; investigate MCO state before resuming |
| Cross-namespace resource quota deadlock | Two namespaces each waiting for resource from the other; both stuck `Pending` | `oc get pods -A \| grep Pending`; `oc describe pod <pending-pod> -n <ns> \| grep Events` — `Insufficient` on both sides | Circular resource dependency; workloads never start | Identify the dependency chain; manually free resources in one namespace; redesign to avoid cross-NS dependency |
| Out-of-order CRD version migration during upgrade | Old controller processes new-schema CRs; new controller processes old-schema CRs; field semantics differ | `oc get crd <name> -o json \| jq '.status.storedVersions'`; `oc logs <operator-pod> \| grep 'unknown field\|conversion'` | CRD objects in inconsistent state; admission webhook rejections; operator infinite reconcile | Run CRD migration job; ensure all objects stored in latest version: `oc get <crd-kind> -A -o json \| kubectl apply -f -` after conversion webhook |
| At-least-once work queue redelivery causing duplicate Job execution | Kubernetes Job runs twice; data processed twice; `completions=1` jobs show 2 succeeded pods | `oc get jobs -A -o json \| jq '.items[] \| select(.status.succeeded > .spec.completions)'` | Duplicate data writes; idempotency violations; downstream data corruption | Delete extra completed pods; add idempotency key in job payload; use `ttlSecondsAfterFinished` | 
| Compensating rollback failure during cluster upgrade via ClusterVersion | `ClusterVersion` upgrade rolled back but some operators remain at new version; mixed-version state | `oc get clusterversion version -o json \| jq '.status.history'`; `oc get co \| grep -v "True.*False.*False"` | Partial mixed-version cluster; API compatibility broken between components | Force operator reconcile: `oc patch co <name> --type=merge -p '{"spec":{}}'`; escalate to Red Hat Support with `oc adm must-gather` |
| Distributed lock expiry during etcd compaction | Controller loses leader election lock during etcd compaction-induced latency spike; two leaders briefly active | `oc logs -n openshift-controller-manager <pod> \| grep 'leader\|lost lock\|acquired'`; check etcd compaction timing vs leader churn | Brief dual-leader race; duplicate reconcile events; potential conflicting writes | Increase `leaseDuration` and `renewDeadline` in controller-manager; reduce etcd compaction frequency to avoid latency spikes |
| Saga failure: multi-step pod admission (webhook chain) partial rejection | Pod partially processed by admission chain; some mutations applied, admission ultimately rejected; pod in `Pending` with partial annotations | `oc describe pod <pod> -n <ns> \| grep 'Events\|Annotations'`; `oc logs -n <webhook-ns> <webhook-pod> \| grep 'admitted\|rejected'` | Inconsistent pod annotations/labels; potential resource reservation without running pod; webhook state machine violated | Delete the pending pod; fix webhook ordering or make each webhook idempotent; re-submit pod |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: namespace over-consuming node CPU | `oc adm top pods -n <noisy-ns> --sort-by=cpu`; `oc describe node <node> \| grep -A20 'Allocated resources'` | Other namespaces on same node CPU-throttled; latency spikes | `oc patch namespace <noisy-ns> -p '{"metadata":{"annotations":{"scheduler.alpha.kubernetes.io/node-selector":"dedicated=<noisy>"}}}'` | Set `LimitRange` and `ResourceQuota` in noisy namespace; evict pod to node with more headroom: `oc adm drain <node>` |
| Memory pressure from adjacent tenant triggering OOM | `oc adm node-logs <node> --path=journal \| grep 'oom_kill'`; `oc adm top pods -A --sort-by=memory \| head -20` | Neighboring tenant pods OOM-killed by kernel; workload disruption | `oc label node <node> dedicated=<tenant> --overwrite` and set `nodeAffinity` for affected tenant | Set `requests.memory = limits.memory` for Guaranteed QoS in noisy namespace; enable memory `LimitRange` |
| Disk I/O saturation from single namespace | `oc debug node/<node> -- chroot /host iostat -x 1 10 \| grep -v '^$'`; `oc adm top pods -n <ns> \| sort -k4 -rn` | All pods on same node experience I/O wait; database queries slow | Move I/O-intensive workload to dedicated node: `oc patch deploy <name> -n <ns> -p '{"spec":{"template":{"spec":{"nodeName":"<dedicated-node>"}}}}'` | Apply `blkio` cgroup limits via OpenShift `Tuned` CR; use dedicated storage nodes for I/O-heavy tenants |
| Network bandwidth monopoly from bulk transfer pod | `oc debug node/<node> -- chroot /host iftop -t -s 30 2>/dev/null`; OVN meter stats: `oc exec -n openshift-ovn-kubernetes <pod> -- ovn-nbctl meter-list` | Other pods on node/VXLAN segment experience high packet loss and retransmits | Apply OVN bandwidth limit: `oc annotate pod <pod> -n <ns> k8s.ovn.org/pod-networks='{"default":{"ingress_rate":"100","egress_rate":"100"}}'` | Configure `IngressController` rate limits; use OVN `Policy-Based Routing` to limit bulk transfer bandwidth |
| Connection pool starvation: tenant exhausting PgBouncer connections | `psql -h pgbouncer -p 6432 pgbouncer -c "SHOW pools" \| grep <tenant-db>` — `cl_waiting` elevated | Other tenant databases cannot acquire connections; application timeouts | `psql -h pgbouncer -p 6432 pgbouncer -c "KILL <tenant-db>"` — release pooler resources | Set per-database `pool_size` limit in PgBouncer config; enforce `ResourceQuota` on namespace connection count |
| Quota enforcement gap: namespace exceeds CPU quota silently | `oc describe resourcequota -n <ns>`; `oc get resourcequota -A -o json \| jq '.items[] \| select(.status.used["limits.cpu"] > .status.hard["limits.cpu"])'` | Other namespaces starved of CPU because quota not enforced at request level | `oc patch resourcequota <name> -n <ns> --type=merge -p '{"spec":{"hard":{"requests.cpu":"8","limits.cpu":"16"}}}'` | Enable `LimitRange` with defaults to ensure all pods have requests set; audit pods missing resource requests |
| Cross-tenant data leak risk via shared `ConfigMap` in openshift-config | `oc get configmap -n openshift-config -o json \| jq '.items[] \| select(.metadata.annotations["kubectl.kubernetes.io/last-applied-configuration"] \| strings \| contains("password\|token\|key"))' \| .metadata.name` | Sensitive config from one tenant readable by service accounts from other namespaces | `oc patch configmap <name> -n openshift-config --type=json -p '[{"op":"remove","path":"/data/<sensitive-key>"}]'` | Move sensitive data to namespace-scoped `Secret`; use `SealedSecret` or Vault for cross-namespace secrets; audit RBAC for `get` on `configmaps` |
| Rate limit bypass: tenant using internal service IP to skip route rate limiting | Check ingress router access log for requests hitting pod IP directly vs route IP: `oc exec -n openshift-ingress <router> -- grep '<pod-cidr>' /var/log/router/access.log` | Tenant bypasses `haproxy.router.openshift.io/rate-limit-connections` by calling internal pod IP | Apply `NetworkPolicy` to block direct pod-to-pod traffic from other namespaces: `oc apply -f deny-cross-ns-policy.yaml` | Enforce `NetworkPolicy` requiring all inter-namespace traffic to go through `Service`; block direct pod IP access |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Prometheus scrape failure for cluster operator metrics | No data in `cluster:operator:*` recording rules; alerting rules based on operator health silent | `ServiceMonitor` target down due to API server cert rotation breaking scrape auth | `oc get --raw /metrics -n openshift-monitoring \| grep 'prometheus_target_scrape_duration'`; check `oc get servicemonitor -n openshift-monitoring` | Fix scrape auth: `oc delete secret prometheus-k8s-htpasswd -n openshift-monitoring`; let CMO regenerate |
| Trace sampling gap missing slow outlier requests | p99 latency invisible in Jaeger/Tempo; only fast requests sampled; SLO breaches undetected | Head-based sampling at 1% discards 99% of traces including rare slow outliers | Deploy OpenTelemetry `tail_sampling` processor; alert on `histogram_quantile(0.99, rate(http_request_duration_bucket[5m]))` from metrics | Switch to tail-based sampling preserving 100% of slow (> 500 ms) and error traces |
| Log pipeline silent drop from fluentd buffer overflow | Application errors not appearing in Elasticsearch/Loki; `fluentd` pod running but not outputting | fluentd buffer on node disk full; `chunk_limit_size` exceeded; logs silently dropped without alerting | `oc exec -n openshift-logging <fluentd-pod> -- tail -50 /var/log/fluentd/fluentd.log \| grep 'chunk\|drop\|overflow'` | Increase buffer size in `ClusterLogging` CR; add `oc get clusterlogging instance -n openshift-logging` alert on buffer full |
| Alert rule misconfiguration: `absent()` firing for non-critical missing series | False alerts for metrics that only exist when condition occurs (e.g., error counters); on-call fatigued | `absent()` rule firing because error counter metric not exposed when no errors | `oc get prometheusrule -A -o json \| jq '.items[].spec.groups[].rules[] \| select(.expr \| contains("absent"))' \| .alert, .expr` | Replace `absent(metric)` with `absent(metric) AND on() vector(1)`; or use `unless` with always-present metric |
| Cardinality explosion from label proliferation blinding dashboards | Grafana dashboards timing out; Prometheus TSDB head blocks growing > 10 GB; scrape latency > 10 s | Pod name or request ID added as Prometheus label; TSDB series count explodes | `oc exec -n openshift-monitoring prometheus-k8s-0 -- promtool tsdb analyze /prometheus \| head -40` — check highest-cardinality metrics | Drop offending label in `MetricRelabelConfig` in `ServiceMonitor`; `oc edit servicemonitor <name> -n openshift-monitoring` |
| Missing health endpoint for custom operator | Operator pod OOMKilled but cluster operator reports `Available: True`; no liveness/readiness probe | Custom operator not exposing `/healthz` endpoint; `ClusterOperator` status not updated on crash | Check readiness: `oc get co \| grep -v "True.*False.*False"`; `oc describe co <operator> \| grep -A5 'Conditions'` | Add `livenessProbe` and `readinessProbe` to operator `Deployment`; implement `/healthz` endpoint in operator |
| Instrumentation gap in API server admission webhook critical path | Webhook latency not measured; API server slow from webhook but no visibility | Admission webhooks not exporting Prometheus metrics; latency attributed to API server generally | `oc get --raw /metrics -n openshift-kube-apiserver \| grep 'apiserver_admission_webhook_admission_duration'` | Add metrics to webhook; set `timeoutSeconds` in `ValidatingWebhookConfiguration`; alert on admission latency > 500 ms |
| Alertmanager outage silencing all OpenShift cluster alerts | No pages received for any OpenShift alert; cluster degrading silently | Alertmanager pods crashlooping or unreachable from Prometheus | `oc get pods -n openshift-monitoring \| grep alertmanager`; `oc get --raw /api/v1/namespaces/openshift-monitoring/services/alertmanager-main:9093/proxy/api/v2/status` | Restore Alertmanager: `oc rollout restart statefulset/alertmanager-main -n openshift-monitoring`; set up dead-man's-switch alert |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor OCP version upgrade rollback (e.g., 4.14 → 4.14.x) | `ClusterVersion` shows `Failing: True`; specific operator `Degraded`; upgrade stalled | `oc get clusterversion version -o json \| jq '.status.conditions[] \| select(.type=="Failing")'`; `oc get co \| grep -v "True.*False.*False"` | Force rollback to previous version: `oc adm upgrade --to=<previous-version> --allow-not-recommended`; restore operator config from backup | Run `oc adm upgrade --to=<target> --allow-not-recommended` only in staging first; check `oc adm upgrade --include-not-recommended` for known issues |
| Major OCP upgrade (e.g., 4.13 → 4.14) breaking deprecated API usage | Workloads referencing removed API versions fail; `ValidatingWebhookConfiguration` using old apiVersion broken | `oc api-resources --verbs=list --namespaced -o name \| xargs -I{} oc get {} -A 2>/dev/null`; check removed APIs: `oc get apirequestcounts \| awk '$4 > 0'` | Block upgrade until deprecated APIs removed: `oc patch clusterversion/version --type=merge -p '{"spec":{"desiredUpdate":null}}'` | Run `oc get apirequestcount -A` before upgrade; remove all deprecated API usage; use `oc-migrate` tool |
| Schema migration partial completion (operator CRD schema change) | Operator CRD stored versions include both old and new; `conversion webhook` failing for some objects | `oc get crd <name> -o json \| jq '.status.storedVersions'`; `oc logs <conversion-webhook-pod> \| grep 'error\|conversion'` | Deploy previous operator version: `oc set image deployment/<operator> manager=<old-image>`; revert CRD version | Implement conversion webhook with full bidirectional conversion; test CRD migration in staging before production |
| Rolling upgrade version skew between control plane and workers | Worker nodes running older kubelet; API server features unavailable on workers; pod scheduling fails for new features | `oc get nodes -o wide \| awk '{print $1,$5}' \| sort -k2`; `oc get clusterversion version -o json \| jq '.status.history[0:3]'` | Pause MachineConfigPool to stop worker upgrade: `oc patch mcp worker --type=merge -p '{"spec":{"paused":true}}'` | Never upgrade control plane ahead of workers by more than 1 minor version; monitor `oc get nodes` version skew |
| Zero-downtime migration to new storage class gone wrong | PVC migration fails mid-way; pods with new PVC error; old PVC data inaccessible | `oc get pvc -A \| grep -v Bound`; `oc get events -A \| grep 'FailedMount\|ProvisioningFailed'` | Re-bind pods to original PVC: `oc set volume deployment/<name> --add --name=<vol> --claim-name=<old-pvc>`; restore from latest backup | Use `oc-migrate pvc` in dry-run mode first; maintain backup of data before migration; test in staging |
| Config format change breaking old MachineConfig nodes | Nodes fail to apply new MachineConfig; `MachineConfigPool` degraded; nodes stuck in `Updating` | `oc get mcp`; `oc get nodes \| grep SchedulingDisabled`; `oc adm node-logs <stuck-node> -u machine-config-daemon` | Revert MachineConfig: `oc apply -f previous-machineconfig.yaml`; force MCD restart: `oc delete pod -n openshift-machine-config-operator <mcd-pod>` | Validate MachineConfig syntax with `oc create --dry-run=client -f mc.yaml`; apply to single node first using node selector |
| Data format incompatibility: etcd schema version mismatch after upgrade | API server cannot read existing objects from etcd; `resource version conflict` errors; CRD objects corrupted | `oc get apiserver cluster -o json \| jq '.spec.encryption'`; `oc exec -n openshift-etcd <pod> -- etcdctl get / --prefix --keys-only \| head -20` | Contact Red Hat Support; restore etcd from pre-upgrade snapshot: `oc exec -n openshift-etcd <pod> -- etcdctl snapshot restore /tmp/pre-upgrade.db` | Always capture `oc adm etcd-snapshot save` before any major upgrade; test etcd restore in staging |
| Feature flag rollout causing admission webhook regression | New feature flag in operator causes admission webhook to reject previously valid pods | `oc get validatingwebhookconfigurations -o json \| jq '.items[].webhooks[].failurePolicy'`; `oc logs -n <webhook-ns> <webhook-pod> \| grep 'denied\|reject'` | Set webhook `failurePolicy: Ignore` temporarily: `oc patch validatingwebhookconfiguration <name> --type=json -p '[{"op":"replace","path":"/webhooks/0/failurePolicy","value":"Ignore"}]'` | Deploy admission webhooks with `failurePolicy: Ignore` during rollout; use `canary` namespace to test new feature flags |
| Dependency version conflict: operator requiring newer CRD version not yet installed | Operator fails to start; `CRD not found` in operator logs; dependent operator upgrade blocked | `oc get csv -A \| grep -v Succeeded`; `oc logs <operator-pod> \| grep 'CRD\|no kind\|not found'` | Rollback operator via OLM: `oc patch subscription <sub> -n <ns> --type=merge -p '{"spec":{"startingCSV":"<previous-csv>"}}'` | Define explicit `spec.dependencies` in `ClusterServiceVersion`; use OLM dependency resolution; upgrade dependent operators first |

## Kernel/OS & Host-Level Failure Patterns
**Minimum cross-cutting cases to evaluate here:** OOM killer false kill, inode exhaustion, CPU steal, NTP skew affecting locks, leases, or coordination, file descriptor exhaustion, and TCP conntrack table saturation.

| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|------------------|--------------|-------------|----------------------|
| OOM killer activates on OpenShift worker node | `oc adm node-logs <node> --path=journal | grep -E 'oom_kill|Out of memory' | tail -20` | Pod memory limits missing or too high; kernel invokes OOM killer to reclaim memory | Pods killed unexpectedly; workload disruption; potential data loss for stateful pods | `oc adm drain <node> --ignore-daemonsets --delete-emptydir-data`; set memory limits on offending namespace: `oc set resources deployment <name> --limits=memory=512Mi` |
| Inode exhaustion on container image storage partition | `oc debug node/<node> -- chroot /host df -i /var/lib/containers` shows 100%; new pod creation fails | Excessive small files from container layers or log files; image layers accumulate without GC | New containers cannot be created on the node; `ImagePullBackOff` on all pods assigned to node | `oc debug node/<node> -- chroot /host crictl rmi --prune`; trigger kubelet image GC: `oc debug node/<node> -- chroot /host systemctl kill -s SIGUSR1 kubelet` |
| CPU steal spike on virtualized worker node | `oc debug node/<node> -- chroot /host top -b -n3 | grep '%Cpu' | awk '{print $9}'`; compare with `mpstat 1 5` | Hypervisor over-provisioning; noisy neighbor VMs on same physical host | All pods on node experience latency spikes; CPU-sensitive workloads SLO breach | Cordon node: `oc adm cordon <node>`; request live migration or dedicated pCPUs from infrastructure team; drain to healthy nodes |
| NTP clock skew causing etcd election instability | `oc debug node/<node> -- chroot /host chronyc tracking | grep 'System time'`; `oc exec -n openshift-etcd <pod> -- etcdctl endpoint status -w table` shows leader changes | NTP server unreachable or chrony misconfigured; clock drift > 500ms triggers etcd timeout | etcd leader elections increase; API server write latency spikes; audit log timestamps inconsistent | `oc debug node/<node> -- chroot /host chronyc makestep`; verify NTP config via MachineConfig: `oc get machineconfig 99-worker-chrony -o yaml` |
| File descriptor exhaustion on OpenShift router pod | `oc exec -n openshift-ingress <router-pod> -- cat /proc/$(pidof haproxy)/limits | grep 'open files'`; `ls /proc/$(pidof haproxy)/fd | wc -l` | High connection volume from external traffic; each active connection consumes FDs | HAProxy unable to accept new connections; 503 errors for new requests; existing connections unaffected | `oc set env deployment/router -n openshift-ingress ROUTER_MAX_CONNECTIONS=50000`; restart router: `oc rollout restart deployment/router -n openshift-ingress` |
| TCP conntrack table full blocking new cluster traffic | `oc debug node/<node> -- chroot /host dmesg | grep 'nf_conntrack: table full'`; `oc debug node/<node> -- chroot /host cat /proc/sys/net/netfilter/nf_conntrack_count` vs `nf_conntrack_max` | High pod-to-pod connection rate; short-lived connections not expiring fast enough | New TCP connections silently dropped; intermittent pod-to-pod failures; service endpoints unreachable | Apply MachineConfig to increase conntrack: `nf_conntrack_max=524288`; reduce `nf_conntrack_tcp_timeout_established=300` via `sysctl` MachineConfig |
| Kernel panic causing unexpected node crash | `oc get nodes | grep NotReady`; `oc adm node-logs <node> --path=journal | grep -E 'kernel: BUG|kernel: panic|kernel: Oops'` | Kernel bug in OVN-Kubernetes kernel module; memory corruption; hardware fault | Node goes NotReady; all pods evicted; potential data loss for local-storage workloads | Uncordon after auto-recovery: `oc adm uncordon <node>`; capture vmcore: `oc debug node/<node> -- chroot /host ls /var/crash`; escalate to Red Hat with `oc adm must-gather` |
| NUMA memory imbalance causing elevated latency on multi-socket worker | `oc debug node/<node> -- chroot /host numactl --hardware`; `oc debug node/<node> -- chroot /host numastat -c | head -20` | Pods scheduled without NUMA affinity; memory allocated cross-NUMA; high remote memory access latency | Memory-intensive pods experience 2-3x latency; CPU cache miss rate increases | Enable NUMA-aware scheduling via `TopologyManager` policy: `oc patch kubeletconfig worker-latency-profile --type=merge -p '{"spec":{"topologyManagerPolicy":"single-numa-node"}}'` |

## Deployment Pipeline & GitOps Failure Patterns
**Minimum cross-cutting cases to evaluate here:** image pull failure (rate limit or auth), Helm drift, ArgoCD sync stuck, PodDisruptionBudget-blocked rollout, blue-green cutover failure, and ConfigMap or Secret drift.

| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Image pull rate limit from Docker Hub on OpenShift nodes | `oc get events -A | grep 'pull rate limit'`; pods stuck in `ImagePullBackOff` | `oc describe pod <pod> -n <ns> | grep -A5 'Events'` shows `toomanyrequests` from `registry-1.docker.io` | Switch to mirrored registry: `oc set image deployment/<name> <container>=quay.io/<org>/<image>:<tag>`; patch `ImageContentSourcePolicy` | Configure OpenShift cluster-wide registry mirror via `ImageContentSourcePolicy`; use Red Hat registry or internal Quay |
| Image pull auth failure after registry credential rotation | Pods fail with `ErrImagePull`; `unauthorized: authentication required` in event log | `oc get events -n <ns> | grep 'unauthorized'`; `oc get secret <pull-secret> -n <ns> -o json | jq '.data[".dockerconfigjson"]' | base64 -d | jq .` | Patch pull secret: `oc create secret docker-registry <name> --docker-server=<reg> --docker-username=<u> --docker-password=<p> -n <ns> --dry-run=client -o yaml | oc apply -f -` | Automate secret rotation with External Secrets Operator syncing from Vault; set pull secret expiry alerts |
| Helm chart values drift between ArgoCD desired and live cluster state | ArgoCD app shows `OutOfSync`; `oc diff` reveals unexpected field values in deployed resources | `oc argo app diff <app-name>` or via ArgoCD UI; `helm get values <release> -n <ns>` vs GitOps repo values | Trigger ArgoCD sync: `oc argo app sync <app-name> --prune`; or `helm upgrade <release> <chart> -f values.yaml -n <ns>` | Enable ArgoCD auto-sync with `selfHeal: true`; use `helm template` in CI to validate rendered manifests before merge |
| ArgoCD sync stuck due to resource hook failure | ArgoCD application stuck in `Progressing`; sync hook `Job` failed; app never reaches `Synced` | `oc get applications -n openshift-gitops`; `oc logs job/<hook-job> -n <ns>`; `oc argo app get <name> --show-operation` | Delete stuck hook job: `oc delete job <hook-job> -n <ns>`; retry sync: `oc argo app sync <name> --retry-limit 3` | Set `ttlSecondsAfterFinished` on hook Jobs; add `hook.argocd.argoproj.io/delete-policy: HookSucceeded` annotation |
| PodDisruptionBudget blocking rolling deployment on OpenShift | `oc rollout status deployment/<name>` hangs; `oc get events | grep 'Cannot evict pod'`; deployment paused | `oc describe pdb <name> -n <ns> | grep -E 'Allowed Disruptions|Min Available|Current Healthy'` | Temporarily patch PDB: `oc patch pdb <name> -n <ns> --type=merge -p '{"spec":{"maxUnavailable":2}}'`; complete rollout; restore PDB | Set `minAvailable` as percentage (e.g. `50%`) not absolute integer; ensure replica count > minAvailable before deploy |
| Blue-green traffic switch failure via OpenShift Route weight | `oc get route <name> -n <ns> -o json | jq '.spec.alternateBackends'`; weight not shifting; canary pods still receiving 0% traffic | `oc describe route <name> -n <ns>`; check router pod logs: `oc logs -n openshift-ingress <router-pod> | grep 'weight\|backend'` | Force traffic back to blue: `oc patch route <name> -n <ns> --type=json -p '[{"op":"remove","path":"/spec/alternateBackends"}]'` | Test route weight changes in staging; use `oc patch route` atomically; monitor error rate before increasing canary weight |
| ConfigMap/Secret drift from GitOps source after manual `oc edit` | ArgoCD detects drift; `oc diff` shows live cluster ConfigMap differs from Git; manual change lost or unintended | `oc argo app diff <app>`; `git diff HEAD -- <configmap>.yaml` | Revert manual change: `oc apply -f <configmap-from-git>.yaml -n <ns>`; trigger ArgoCD sync to restore desired state | Enable ArgoCD `selfHeal`; add `kubectl.kubernetes.io/last-applied-configuration` annotation audit; restrict `oc edit` access via RBAC |
| Feature flag ConfigMap stuck in old value after pipeline deploy | Application reads stale feature flag; new behavior not activated despite successful image rollout | `oc get configmap <feature-flags> -n <ns> -o json | jq '.data'`; compare with pipeline expected values; `oc rollout history deployment/<name>` | Force ConfigMap update: `oc create configmap <name> --from-literal=flag=true -n <ns> --dry-run=client -o yaml | oc apply -f -`; trigger rolling restart: `oc rollout restart deployment/<name>` | Mount feature flags as environment variables (not file) for faster propagation; use `--force` flag in pipeline apply step |

## Service Mesh & API Gateway Edge Cases
**Minimum cross-cutting cases to evaluate here:** circuit breaker false positives, rate limiting on legitimate traffic, stale service discovery endpoints, mTLS rotation interruption, retry storm amplification, gRPC keepalive or max-message failures, and trace context loss.

| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive on healthy OpenShift service | Istio/Service Mesh circuit breaker opens despite service responding; `oc get destinationrule` shows ejection | `oc exec -n istio-system <istiod-pod> -- pilot-agent request GET /clusters | grep 'outlier_detection'`; check ejection count | Legitimate traffic rejected; cascading failure in dependent services | Tune `OutlierDetection` in `DestinationRule`: `oc edit destinationrule <name> -n <ns>` — increase `consecutiveErrors` threshold; add `baseEjectionTime: 10s` |
| Rate limit policy hitting legitimate high-throughput internal service | `oc get ratelimitconfig -n openshift-service-mesh`; 429 responses in service logs for internal callers | `oc logs -n openshift-service-mesh <rate-limit-svc-pod> | grep 'OVER_LIMIT'`; `istioctl x describe pod <pod>` | Internal bulk processing blocked; SLO breach for batch jobs | Exempt internal service CIDR from rate limit via `EnvoyFilter`: `oc apply -f rate-limit-bypass.yaml`; increase burst limit for internal callers |
| Stale service discovery endpoints after pod replacement | Old pod IP in Envoy's EDS after new pod starts; requests routed to terminated pod; connection refused | `istioctl proxy-config endpoints <pod>.<ns> | grep '<old-ip>'`; compare with `oc get endpoints <svc> -n <ns>` | Intermittent 503s; health check endpoints return stale data | Force Envoy EDS refresh: `istioctl x authz check <pod>.<ns>`; restart affected sidecar: `oc exec <pod> -n <ns> -c istio-proxy -- curl -X POST http://localhost:15000/drain_listeners` |
| mTLS certificate rotation breaking existing persistent connections | After cert rotation, long-lived gRPC connections fail with `tls: certificate required`; short-lived connections recover | `oc get peerauthentication -A`; `istioctl authn tls-check <pod>.<ns>`; check cert expiry: `oc get secret istio-ca-secret -n istio-system -o json | jq '.data."ca-cert.pem"' | base64 -d | openssl x509 -noout -dates` | Persistent connections broken; gRPC streaming services disrupted until clients reconnect | Gracefully drain connections before cert rotation: `oc annotate pod <pod> -n <ns> sidecar.istio.io/inject=false`; rolling restart of affected pods |
| Retry storm from Istio virtual service retries amplifying error rate | Error rate increases after enabling retries; upstream services receive 5-10x expected traffic; cascading overload | `istioctl proxy-config log <pod>.<ns> --level debug`; check retry metrics: `oc exec -n openshift-service-mesh <pod> -- curl http://localhost:15000/stats | grep retry` | Upstream service overloaded by retry amplification; database connection pool exhausted | Reduce retry attempts: `oc edit virtualservice <name> -n <ns>` — set `retries.attempts: 1`; add `retryOn: 5xx` not `connect-failure` |
| gRPC max message size exceeded causing silent stream drop | gRPC streaming silently drops messages > 4MB default; consumer receives incomplete data without error | `oc logs <pod> -n <ns> | grep 'grpc: received message larger than max'`; check EnvoyFilter for `grpc_max_request_bytes` | Data truncation in gRPC streams; inconsistent state between producer and consumer | Patch EnvoyFilter for gRPC max message: `oc apply -f envoyfilter-grpc-max.yaml` with `grpc_stats` filter; configure client with `WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(16*1024*1024))` |
| Distributed trace context propagation gap at OpenShift ingress boundary | Jaeger/Tempo traces show broken spans at ingress; inner service traces disconnected from root trace | `oc exec -n openshift-ingress <router-pod> -- grep 'x-b3-traceid\|traceparent' /var/log/router/access.log`; check `EnvoyFilter` for trace header injection | Distributed traces unlinked; latency attribution impossible; SLO root cause analysis blind | Enable trace header propagation in `IngressController`: `oc patch ingresscontroller default -n openshift-ingress --type=merge -p '{"spec":{"logging":{"access":{"destination":{"type":"Container"}}}}}' `; add `tracingConfig` to Istio mesh config |
| Load balancer health check misconfiguration causing premature traffic | HAProxy router health check using TCP vs HTTP; pods receiving traffic before application ready | `oc get route <name> -n <ns> -o json | jq '.metadata.annotations'`; `oc exec -n openshift-ingress <router-pod> -- grep 'option httpchk\|check' /var/lib/haproxy/conf/haproxy.config` | Requests routed to pods still initializing; transient errors during deployments | Add HTTP readiness check annotation: `oc annotate route <name> -n <ns> router.openshift.io/haproxy.health.check.interval=5s`; define `readinessProbe` in pod spec with matching path |
