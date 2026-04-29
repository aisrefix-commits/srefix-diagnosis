---
name: gke-agent
provider: gcp
domain: gke
aliases:
  - google-kubernetes-engine
  - google-gke
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - provider-gcp
  - component-gke-agent
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
# GKE SRE Agent

## Role
Site Reliability Engineer specializing in Google Kubernetes Engine. Responsible for cluster health across Autopilot and Standard modes, node pool lifecycle, Workload Identity federation, GKE Dataplane V2 (Cilium-based), GKE Ingress via Google Cloud Load Balancer, Binary Authorization enforcement, and cluster upgrade strategies. Bridges GCP-managed control plane operations with Kubernetes workload reliability.

## Architecture Overview

```
Cloud DNS / External DNS
        │
        ▼
Google Cloud Load Balancer (GLB)
        │  ← GKE Ingress Controller (Anthos / Built-in)
        ▼
┌──────────────────────────────────────────────────┐
│         GKE Control Plane (Google-managed)       │
│  API Server │ etcd │ Scheduler │ CM │ Cloud Ops  │
│  Health via: /healthz + GCP Console / Cloud Mon. │
└──────────────┬───────────────────────────────────┘
               │ Private / Public endpoint
    ┌──────────▼──────────────────────┐
    │  Node Pools (Standard mode)     │  ← GCE Instance Groups (MIG)
    │  ┌─────────────────────────┐    │
    │  │ Regular pool            │    │
    │  │ Spot VM pool            │    │
    │  │ GPU pool                │    │
    │  └─────────────────────────┘    │
    │                                 │
    │  Autopilot (serverless pods)    │  ← No node management
    │                                 │
    │  GKE Dataplane V2 (Cilium):     │
    │  ├── eBPF-based networking      │
    │  ├── NetworkPolicy enforcement  │
    │  └── Hubble observability       │
    └─────────────────────────────────┘
         │
         ▼
    VPC Networking
    ├── Alias IP ranges for pods (secondary ranges)
    ├── Workload Identity (no SA keys)
    └── Private Google Access
```

GKE Standard gives full node control; Autopilot abstracts nodes entirely (Google manages node provisioning, upgrades, and scaling). Workload Identity replaces service account key files. GKE Dataplane V2 replaces iptables/kube-proxy with eBPF (Cilium).

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| `kubernetes.io/container/cpu/request_utilization` | > 80% | > 95% | Cloud Monitoring, per container |
| `kubernetes.io/container/memory/request_utilization` | > 80% | > 95% | OOM risk if limit == request |
| `kubernetes.io/node/cpu/allocatable_utilization` | > 75% | > 90% | Trigger node auto-provisioning |
| Node pool size vs max | > 80% of max nodes | > 95% of max | NAP scale-out before hitting ceiling |
| `kubernetes.io/pod/volume/total_bytes` / capacity | > 80% | > 90% | Persistent disk near full |
| GKE Ingress 5xx error rate | > 0.1% | > 1% | Via Cloud Monitoring HTTP LB metrics |
| `container/uptime` (restart_count delta) | > 3 restarts/hr | > 10 restarts/hr | Container instability |
| Cluster upgrade age (days behind latest) | > 60 days | > 90 days (auto-upgrade triggers) | Rapid channel may auto-upgrade |
| Workload Identity token errors | Any | > 5/min | `iam.serviceAccounts.actAs` failures |
| `dataplane_v2/drop_count` (Cilium drops) | > 10/min | > 100/min | NetworkPolicy misconfiguration |

## Alert Runbooks

### Alert: Node Pool Nodes Not Ready
**Symptom:** `kubernetes_node_status_condition{condition="Ready",status="false"} >= 1`

**Triage:**
```bash
# Identify not-ready nodes and their pools
kubectl get nodes -o wide | grep -v Ready
kubectl get nodes --show-labels | grep -v Ready | grep 'cloud.google.com/gke-nodepool'

# Describe node for events
kubectl describe node <node-name>

# Check GCE instance health
gcloud compute instances describe <instance-name> \
  --zone=<zone> \
  --format='yaml(status,networkInterfaces,metadata.items[key=kube-env])'

# View serial console output for boot issues
gcloud compute instances get-serial-port-output <instance-name> --zone=<zone> | tail -100

# Check if it's an auto-repair action already in progress
gcloud container operations list \
  --filter="operationType=REPAIR_CLUSTER" \
  --sort-by="~startTime" --limit=5

# Manual node pool repair (triggers GCP auto-repair)
gcloud container node-pools update <pool-name> \
  --cluster=<cluster> --region=<region> \
  --enable-autorepair
```

### Alert: Workload Identity Federation Failure
**Symptom:** Pods log `Permission denied on resource project... (or it may not exist)` or `Failed to retrieve access token`

**Triage:**
```bash
# Check if Workload Identity is enabled on cluster
gcloud container clusters describe <cluster> --region=<region> \
  --format='value(workloadIdentityConfig.workloadPool)'
# Expected: <project>.svc.id.goog

# Verify node pool has metadata server enabled
gcloud container node-pools describe <pool> --cluster=<cluster> --region=<region> \
  --format='value(config.workloadMetadataConfig.mode)'
# Expected: GKE_METADATA

# Check Kubernetes ServiceAccount annotation
kubectl get sa <sa-name> -n <namespace> \
  -o jsonpath='{.metadata.annotations.iam\.gke\.io/gcp-service-account}'

# Verify IAM binding (KSA → GSA)
gcloud iam service-accounts get-iam-policy <gsa>@<project>.iam.gserviceaccount.com \
  --format=json | jq '.bindings[] | select(.role=="roles/iam.workloadIdentityUser")'

# Test from inside a pod
kubectl exec -it <pod> -- curl -s -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token | \
  python3 -m json.tool
```

### Alert: GKE Ingress Backend Unhealthy
**Symptom:** Cloud Load Balancer returning 502/503; backend service shows `0 healthy instances`

**Triage:**
```bash
# List backend services created by GKE Ingress
kubectl describe ingress <ingress-name> -n <ns> | grep -A20 "Annotations\|Rules\|Backend"

# Get the backend service name from annotation
gcloud compute backend-services list --filter="name~k8s" --global

# Check backend health
gcloud compute backend-services get-health <backend-service-name> --global

# Verify firewall rule for health check (130.211.0.0/22, 35.191.0.0/16)
gcloud compute firewall-rules list --filter="direction=INGRESS" | grep "health-check\|allow"

# Check NEG (Network Endpoint Group) endpoints
gcloud compute network-endpoint-groups list --filter="name~<cluster>"
gcloud compute network-endpoint-groups list-network-endpoints <neg-name> --zone=<zone>

# Verify pod readiness probes match health check path
kubectl get svc <svc-name> -n <ns> -o yaml | grep -A5 "healthCheckNodePort\|targetPort"
```

### Alert: Binary Authorization Policy Violation
**Symptom:** Pods fail to start with `Policy violated for image`

**Triage:**
```bash
# Check the violation details in audit log
gcloud logging read 'resource.type="k8s_cluster" protoPayload.methodName="pods.create" protoPayload.status.code=403' \
  --project=<project> --limit=10 --format=json | \
  jq '.[].protoPayload.response.message'

# View current Binary Authorization policy
gcloud container binauthz policy export

# Check attestation on the image
gcloud container binauthz attestations list \
  --attestor=<attestor> --attestor-project=<project> \
  --artifact-url=<image-uri-with-digest>

# Temporarily switch to dry-run mode for emergency (NOT production recommendation)
gcloud container binauthz policy import <(gcloud container binauthz policy export | \
  sed 's/ALWAYS_DENY/ALWAYS_ALLOW/g')
```

## Common Issues & Troubleshooting

### Issue 1: Autopilot Pod Pending — Resource Class Mismatch
**Symptom:** Pod stays `Pending` in Autopilot cluster; event: `no nodes available to schedule pods`

```bash
# Check pod resource requests (Autopilot requires explicit requests)
kubectl describe pod <pod> -n <ns> | grep -A15 "Requests:"

# Autopilot enforces resource class tiers; check if requests match available classes
# Tiers: small (0.5 CPU/2Gi), medium (1/4Gi), large (2/8Gi), xlarge (4/16Gi), 2xlarge (8/32Gi)

# Check pod events for specific Autopilot rejection reason
kubectl get events -n <ns> | grep <pod-name>

# View Autopilot node provisioning state
kubectl get nodes -l cloud.google.com/gke-provisioning=standard
kubectl get nodes -l cloud.google.com/gke-provisioning=spot

# Autopilot pods need Spot toleration if targeting Spot nodes
kubectl get pod <pod> -o yaml | grep -A5 "tolerations"
# Must include: cloud.google.com/gke-spot: "true"

# Check if workload uses unsupported features (host networking, privileged containers)
kubectl get pod <pod> -o yaml | grep -E "hostNetwork|privileged|hostPID"
```

### Issue 2: Node Auto-Provisioning (NAP) Creating Wrong Machine Type
**Symptom:** NAP creates very large or very small nodes, causing resource waste or scheduling failures.

```bash
# View NAP configuration
gcloud container clusters describe <cluster> --region=<region> \
  --format='yaml(autoscaling.autoprovisioningNodePoolDefaults,autoscaling.resourceLimits)'

# Check which NAP node pools exist
gcloud container node-pools list --cluster=<cluster> --region=<region> | grep nap-

# View NAP decisions in logs
gcloud logging read 'resource.type="k8s_cluster" jsonPayload.message=~"ExpanderOption\|scale-up\|provision"' \
  --project=<project> --limit=20

# Update NAP machine type constraints
gcloud container clusters update <cluster> --region=<region> \
  --autoprovisioning-min-cpu-platform="Intel Cascade Lake" \
  --autoprovisioning-max-accelerators="type=nvidia-tesla-t4,count=4" \
  --autoprovisioning-resource-limits="cpu=500,memory=1000Gi"

# Delete problematic NAP pool (will respawn if needed)
gcloud container node-pools delete <nap-pool-name> --cluster=<cluster> --region=<region> --async
```

### Issue 3: GKE Dataplane V2 NetworkPolicy Dropping Traffic
**Symptom:** Intermittent connectivity between services that was working before; Cilium drops visible.

```bash
# Enable Hubble for Cilium observability
kubectl -n kube-system exec ds/cilium -- cilium status

# Check Cilium endpoint status
kubectl -n kube-system exec ds/cilium -- cilium endpoint list | grep -v ready

# View Cilium drop reasons
kubectl -n kube-system exec ds/cilium -- cilium monitor --type drop 2>&1 | head -50

# Check NetworkPolicy affecting the namespace
kubectl get networkpolicy -n <ns>
kubectl describe networkpolicy <policy-name> -n <ns>

# Hubble CLI for flow observability (if Hubble relay deployed)
kubectl -n kube-system exec deploy/hubble-relay -- \
  hubble observe --namespace <ns> --verdict DROPPED --last 100

# Verify Cilium DaemonSet is healthy
kubectl -n kube-system get ds cilium -o wide
kubectl -n kube-system rollout status ds/cilium
```

### Issue 4: GKE Cluster Upgrade Stuck on Node Pool Drain
**Symptom:** Node pool upgrade shows `UPGRADING` for > 1 hour; nodes not draining.

```bash
# Check upgrade operation status
gcloud container operations list \
  --filter="operationType=UPGRADE_NODES AND status=RUNNING" \
  --sort-by="~startTime"

gcloud container operations describe <operation-id> --region=<region>

# Find nodes being upgraded (old version)
kubectl get nodes --sort-by='.status.nodeInfo.kubeletVersion'

# Check for PodDisruptionBudgets blocking drain
kubectl get pdb -A
kubectl get pdb -A -o json | jq '.items[] | select(.status.disruptionsAllowed == 0) | "\(.metadata.namespace)/\(.metadata.name)"'

# Check stuck terminating pods on node being drained
kubectl get pods -A --field-selector=status.phase=Running -o wide | grep <node-name>

# Manually drain if GCP is stuck
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --force --grace-period=30

# For Blue/Green upgrade strategy (Standard mode) — surge upgrade
gcloud container node-pools update <pool> --cluster=<cluster> --region=<region> \
  --max-surge-upgrade=1 --max-unavailable-upgrade=0
```

### Issue 5: Private Cluster Nodes Cannot Pull Images from Artifact Registry
**Symptom:** `ImagePullBackOff`; event: `Failed to pull image: 403 Forbidden` or `dial tcp: no route to host`

```bash
# Check Private Google Access is enabled on subnets
gcloud compute networks subnets describe <subnet> --region=<region> \
  --format='value(privateIpGoogleAccess)'
# Must be: True

# Verify node service account has Artifact Registry reader role
gcloud projects get-iam-policy <project> \
  --flatten='bindings[].members' \
  --filter='bindings.role=roles/artifactregistry.reader' \
  --format='value(bindings.members)'

# Check if Cloud NAT is configured for nodes (if using public Artifact Registry endpoint)
gcloud compute routers list --filter="region:<region>"
gcloud compute routers nats list --router=<router-name> --region=<region>

# Test connectivity from a node (via SSH or pod)
kubectl run ar-test --image=gcr.io/google.com/cloudsdktool/cloud-sdk:latest --rm -it \
  --restart=Never -- gcloud artifacts repositories list
```

### Issue 6: Cluster Autoscaler Not Scaling Down (Scale-in Stuck)
**Symptom:** Idle nodes not removed; cluster costs accumulating; CA logs show "not safe to scale down."

```bash
# Check CA status
kubectl -n kube-system get cm cluster-autoscaler-status -o yaml | grep -A50 "status:"

# Look for scale-down blockers
kubectl -n kube-system logs -l app=cluster-autoscaler --tail=200 | \
  grep -E "scale down|not safe|pod.eviction\|cannot be removed"

# Check for pods with annotations blocking eviction
kubectl get pods -A -o json | \
  jq -r '.items[] | select(.metadata.annotations["cluster-autoscaler.kubernetes.io/safe-to-evict"] == "false") | "\(.metadata.namespace)/\(.metadata.name)"'

# Check for local storage usage blocking scale-down
kubectl get pods -A -o json | \
  jq -r '.items[] | select(.spec.volumes[]?.emptyDir != null) | "\(.metadata.namespace)/\(.metadata.name)"'

# Check kube-system pods (CA won't drain nodes with non-DaemonSet kube-system pods by default)
kubectl get pods -n kube-system -o wide | grep -v "DaemonSet\|<none>"
```

## Key Dependencies

- **GCP VPC** — secondary IP ranges for pods/services must be pre-allocated; VPC peering for private clusters
- **Google Cloud IAM** — Workload Identity binds KSA to GSA; `iam.workloadIdentityUser` role binding required
- **Artifact Registry / GCR** — container image pulls; node service account needs `artifactregistry.reader`
- **Cloud Load Balancing** — GKE Ingress creates GLB; NEG (Network Endpoint Groups) health checks require firewall rules
- **Cloud DNS** — GKE DNS add-on; Cloud DNS for GKE enables VPC-scoped DNS for Services
- **GCS / Filestore** — persistent storage via GCSFuse CSI and NFS CSI; requires Workload Identity binding
- **Binary Authorization** — enforces image signing; blocks unauthorized deployments cluster-wide
- **Cloud Monitoring / Cloud Logging** — metrics, logs, alerts; Node pool auto-repair uses health signals
- **Anthos Service Mesh** — if enabled, manages sidecar injection, mTLS, and traffic policies

## Cross-Service Failure Chains

- **Spot VM preemption surge** → NAP Spot node pool loses > 50% capacity → pods unscheduled → workloads degrade → CA creates on-demand nodes → cost spike
- **Workload Identity metadata server down** → all IRSA-equivalent calls fail → Cloud Storage, BigQuery, Pub/Sub all return 401 → application errors across all namespaces simultaneously
- **Cloud DNS for GKE failure** → in-cluster DNS breaks → service discovery fails → all inter-service calls fail → health checks fail → GLB removes all backends → 502s to users
- **Binary Authorization strict mode + unsigned image** → deployment blocked → rollback triggered → rollback also blocked → service stuck on old version; on-call must emergency-disable policy
- **GKE Dataplane V2 Cilium crash** → eBPF rules not enforced → NetworkPolicy bypassed (traffic flows anyway) OR eBPF rules orphaned → traffic drops cluster-wide

## Partial Failure Patterns

- **Single zone node pool with zonal outage**: Regional clusters with multi-zone pools are resilient; single-zone clusters lose all capacity in a zonal event
- **Autopilot quota exhaustion**: New pods provisioned but hit project CPU/memory quota before node is ready; pods remain Pending with no obvious error
- **Surge upgrade overloads backends**: `maxSurge=1` doubles node count briefly; Cloud SQL connection limits may be hit during surge
- **Cloud DNS propagation delay**: New services take up to 30s to resolve after creation; readiness probes that use DNS may fail during propagation

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|----------|
| Pod scheduling latency (Standard) | < 2s | 2–10s | > 10s |
| Autopilot pod provisioning time | < 90s | 90s–3min | > 5min |
| Node pool surge upgrade (per node) | < 4 min | 4–10 min | > 15 min |
| GLB backend health propagation | < 30s | 30–90s | > 2 min |
| Workload Identity token fetch | < 500ms | 500ms–2s | > 5s |
| Persistent disk attach | < 30s | 30–90s | > 3 min |
| API server p99 latency | < 500ms | 500ms–2s | > 5s |
| Cloud DNS query time (in-cluster) | < 5ms | 5–100ms | > 500ms |

## Capacity Planning Indicators

| Indicator | Source | Trigger | Action |
|-----------|--------|---------|--------|
| Node pool utilization > 80% avg CPU | Cloud Monitoring `kubernetes.io/node/cpu/allocatable_utilization` | Sustained 1 hour | Increase max nodes or enable NAP |
| Pod density per node > 90% of max | `kube_node_status_allocatable{resource="pods"}` | Any node | Add more nodes with higher pod density or enable Autopilot |
| Cluster version N-2 behind latest | `gcloud container get-server-config` | Immediate | Plan upgrade; GKE auto-upgrade at N-3 |
| Spot preemption rate > 5%/day | Cloud Logging preemption events | Trending up | Diversify VM families; add on-demand fallback |
| Alias IP range utilization > 75% | VPC subnet secondary ranges | Sustained | Add secondary range or new subnet before exhaustion |
| Cloud SQL connection count > 80% | Cloud Monitoring `database/postgresql/num_backends` | Trending | Enable connection pooling (PgBouncer/Pgpool) |
| GLB backend healthy instance % < 80% | Cloud Monitoring LB metrics | Any | Investigate pod health; scale up node pool |
| Binary Authorization violations > 0 | Cloud Audit Logs | Any | Audit and attest new images immediately |

## Diagnostic Cheatsheet

```bash
# Full cluster health overview
gcloud container clusters list --format='table(name,location,status,currentNodeVersion,currentMasterVersion)'

# All nodes grouped by pool and status
kubectl get nodes -L cloud.google.com/gke-nodepool,cloud.google.com/gke-spot --sort-by='.metadata.labels.cloud\.google\.com/gke-nodepool'

# Pods consuming most memory (top 10)
kubectl top pods -A --sort-by=memory | head -15

# Events for scheduling failures in last 10 minutes
kubectl get events -A --field-selector='reason=FailedScheduling' --sort-by='.lastTimestamp'

# Check Cilium/Dataplane V2 health across all nodes
kubectl -n kube-system exec ds/cilium -- cilium status --brief

# View all Workload Identity bindings in use
kubectl get sa -A -o json | jq -r '.items[] | select(.metadata.annotations["iam.gke.io/gcp-service-account"] != null) | "\(.metadata.namespace)/\(.metadata.name) → \(.metadata.annotations["iam.gke.io/gcp-service-account"])"'

# List all ongoing GKE operations (upgrades, repairs, etc.)
gcloud container operations list --filter="status!=DONE" --sort-by="~startTime"

# Get Cloud Monitoring recent alerts for GKE
gcloud alpha monitoring incidents list --project=<project> \
  --filter="resource_type_display_name=GKE" --limit=10

# Check node pool auto-upgrade and auto-repair settings
gcloud container node-pools list --cluster=<cluster> --region=<region> \
  --format='table(name,config.machineType,autoscaling.enabled,autoscaling.minNodeCount,autoscaling.maxNodeCount,management.autoUpgrade,management.autoRepair)'

# View Autopilot node provisioning queue
kubectl get events -A | grep -E "ProvisioningStarted|ProvisioningSucceeded|ProvisioningFailed"
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|-------------------|-------------|
| GKE API Availability (managed control plane) | 99.95% | 21.6 min/month | GCP SLA; monitored via `/healthz` prober |
| Workload Availability (pods healthy) | 99.5% | 3.6 hr/month | `kube_deployment_status_replicas_available / kube_deployment_spec_replicas` |
| Ingress Success Rate (5xx < threshold) | 99.9% | 43.2 min/month | Cloud Monitoring HTTP LB 5xx rate < 0.1% over 5m |
| Pod Scheduling Time (< 30s for 99th percentile) | 99.0% | 7.2 hr/month | `kube_pod_status_scheduled_time - kube_pod_created < 30s` |

## Configuration Audit Checklist

| Check | Command | Expected |
|-------|---------|----------|
| Cluster version N-1 or better | `gcloud container clusters describe <c> --format='value(currentMasterVersion)'` | Within 1 minor of latest stable |
| Workload Identity enabled | `gcloud container clusters describe <c> --format='value(workloadIdentityConfig)'` | `<project>.svc.id.goog` |
| Node pool auto-upgrade enabled | `gcloud container node-pools describe <p> --cluster=<c> --format='value(management.autoUpgrade)'` | `True` |
| Node pool auto-repair enabled | `gcloud container node-pools describe <p> --cluster=<c> --format='value(management.autoRepair)'` | `True` |
| Binary Authorization policy | `gcloud container binauthz policy export` | `evaluationMode: ALWAYS_DENY` or project attestor |
| Private cluster endpoint only | `gcloud container clusters describe <c> --format='value(privateClusterConfig.enablePrivateEndpoint)'` | `True` for production |
| Shielded nodes enabled | `gcloud container node-pools describe <p> --cluster=<c> --format='value(config.shieldedInstanceConfig)'` | `enableSecureBoot: true` |
| Master authorized networks | `gcloud container clusters describe <c> --format='value(masterAuthorizedNetworksConfig.enabled)'` | `True`; CIDR allowlist set |
| GKE Dataplane V2 | `gcloud container clusters describe <c> --format='value(networkConfig.datapathProvider)'` | `ADVANCED_DATAPATH` |
| Logging and Monitoring enabled | `gcloud container clusters describe <c> --format='value(loggingConfig,monitoringConfig)'` | System + workloads components |

## Log Pattern Library

| Log Pattern | Source | Meaning |
|-------------|--------|---------|
| `container exited with non-zero exit code` | Cloud Logging, container runtime | Application crash; check pod logs |
| `Failed to pull image ... 403 Forbidden` | kubelet | Node SA lacks `artifactregistry.reader`; or wrong project |
| `WebIdentityErr\|No service account found` | Workload Identity | `GKE_METADATA` mode not set on node pool; or KSA annotation missing |
| `node.cloudprovider.kubernetes.io/uninitialized` | k8s node event | Node not yet recognized by cloud controller; transient during scale-up |
| `Evict pod <name> (Spot preemption)` | CA / node event | GCE Spot VM preempted; expected behavior |
| `PERMISSION_DENIED: Request had insufficient authentication` | GCP API client | GSA missing required role; or Workload Identity misconfigured |
| `Connection reset by peer` in service logs | Application | NetworkPolicy dropped packet; check Cilium monitor |
| `failed to sync ConfigMap ... operation cannot be fulfilled` | kube-controller-manager | Stale resource version; retry is automatic |
| `node pool has insufficient resources to scale up` | cluster-autoscaler | Quota exceeded or no suitable machine type available |
| `FailedAttachVolume ... already attached to node` | kubelet | PD stuck on old node; need force-detach |
| `image uses an unsafe syscall` | Binary Authorization / OPA | Container violates security policy; image needs re-attestation |
| `exceeds the 100 connection limit` | Cloud SQL Proxy | Connection pool exhaustion; scale proxy or add PgBouncer |

## Error Code Quick Reference

| Error | Service | Meaning | Fix |
|-------|---------|---------|-----|
| `QUOTA_EXCEEDED` | GCP Compute | CPU/IP/disk quota hit during scale-out | Request quota increase in GCP Console |
| `PERMISSION_DENIED` | GCP IAM | Service account missing required role | Add IAM role via `gcloud projects add-iam-policy-binding` |
| `NET_ADMIN capability required` | Kubernetes | Container needs privileged networking | Enable Autopilot workload type or use Standard with PSP |
| `Policy violated for image` | Binary Authorization | Image not attested or attestor not satisfied | Sign image with `gcloud container binauthz attestations create` |
| `FailedScheduling: Insufficient memory` | Kubernetes scheduler | No node has enough free memory | Scale node pool or resize pods |
| `ZONE_RESOURCE_POOL_EXHAUSTED` | GCE | Machine type unavailable in zone | Use multi-zone pool or different machine type |
| `failed to assign IP: no available IPs` | GKE CNI (alias IP) | Secondary range exhausted | Expand secondary CIDR or add new subnet |
| `InvalidArgument: Resource limit exceeded` | GKE Autopilot | Requested more than Autopilot tier max | Reduce resource requests to fit tier |
| `i/o timeout` connecting to GKE master | kubectl | Authorized networks blocking client IP | Add client IP to master authorized networks |
| `CrashLoopBackOff` | Kubernetes | Container crash loop | `kubectl logs <pod> --previous` to see crash reason |
| `toomanyrequests` | GCR / Artifact Registry | Pull rate limit from public endpoint | Configure VPC service control or use Private endpoint |
| `cloud.google.com/gke-spot NoSchedule` | Kubernetes | Pod missing Spot toleration | Add toleration `cloud.google.com/gke-spot: "true"` |

## Known Failure Signatures

| Signature | Root Cause | Distinguishing Indicator |
|-----------|-----------|------------------------|
| All pods pending, nodes healthy | Secondary IP range exhausted | CA logs: `no IP addresses available`; VPC subnet secondary range full |
| kubectl auth fails for all users | OIDC/webhook misconfiguration or control plane issue | `Error from server (ServiceUnavailable)` from all principals |
| Random pod evictions every ~10 min | Spot VM preemptions | Node events: `Spot preemption notice`; all evicted pods were on spot nodes |
| DNS works for Services, not Pods | kube-dns `ndots` misconfiguration | `nslookup my-svc.ns` works; `nslookup my-pod-ip.ns.pod.cluster.local` fails |
| Deployments not updating | Binary Authorization blocking new image | Deployment stuck; events show `Policy violated for image` |
| Node pool stuck in RECONCILING | NAP trying to provision unsupported machine type | `gcloud container operations describe` shows repeated retry |
| Cilium drops between pods in same namespace | NetworkPolicy `podSelector` too restrictive | `cilium monitor --type drop` shows ingress/egress drops for specific flows |
| Autopilot pods OOMKilled immediately | Resource tier mismatch (memory limit too low) | Pod events: `OOMKilled`; actual usage far below Autopilot tier limit |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `dial tcp: connection refused` to ClusterIP | HTTP client / gRPC / JDBC | kube-proxy (iptables/ipvs) rules not synced; GKE Dataplane V2 (Cilium) policy drop | `kubectl get endpoints <svc>`; `cilium monitor --type drop` for Dataplane V2 clusters | Restart kube-proxy or relevant Cilium pods; verify NetworkPolicy allows the flow |
| `Error from server (ServiceUnavailable)` on kubectl | kubectl / client-go | Control plane regional downtime or maintenance window | GCP Status page; `gcloud container clusters describe <name> --format='value(status)'` | Wait for GCP maintenance; check cluster for auto-repair in progress |
| `failed to get credentials from Workload Identity` | Google Cloud SDK / OIDC clients | Workload Identity binding missing or wrong GSA/KSA pair | `gcloud iam service-accounts get-iam-policy <gsa>`; check `iam.workloadIdentityUser` binding | Re-bind: `gcloud iam service-accounts add-iam-policy-binding`; verify KSA annotation |
| `403 Forbidden` from Google Cloud APIs | Google Cloud client libraries | Workload Identity GSA missing required IAM role on the target GCP resource | `gcloud projects get-iam-policy <project> \| grep <gsa-email>` | Grant required role to GSA (e.g., `roles/storage.objectViewer`) |
| `OOMKilled` / `Evicted` — `The node was low on resource: memory` | kubelet eviction | Autopilot node scaled but pod has no `requests`; Standard pool overcommitted | `kubectl top nodes`; `kubectl describe node` → `MemoryPressure` | Set `requests`/`limits`; Autopilot requires explicit requests; enable VPA |
| Pod stuck `ContainerCreating` — `failed to mount volume: googleapi: Error 403` | CSI driver (Filestore / PD CSI) | Workload Identity or service account missing `compute.disks.use` / `file.instances.use` | `kubectl describe pod` → Events; check CSI driver logs | Grant Compute Storage Admin or appropriate role to node SA or Workload Identity SA |
| Binary Authorization `Denied by policy` — deployment blocked | kubectl apply / Helm / CI | Image not attested or attestor key revoked | `kubectl describe pod` → `Policy violated`; `gcloud container binauthz policy export` | Create attestation for image; update policy to allow exemptions for dev namespaces |
| DNS `NXDOMAIN` for service names | Application DNS resolver | Cloud DNS stub zones or kube-dns override misconfigured; NodeLocal DNSCache failing | `kubectl exec <pod> -- nslookup kubernetes.default`; check `node-local-dns` DaemonSet | Restart node-local-dns DaemonSet; restore kube-dns ConfigMap |
| `GaxError: UNAVAILABLE: Connection reset by peer` from Pub/Sub / BigQuery | Google Cloud client libraries | GKE node IP changed; Workload Identity token expired; VPC firewall egress blocked | Token expiry in pod logs; `gcloud compute firewall-rules list` for egress to `*.googleapis.com` | Ensure Private Google Access enabled on subnet; fix firewall egress rules |
| Ingress returning 502 — backend health check failing | Browser / HTTP client | GKE Ingress (GCLB) backend health check protocol mismatch; pod readiness probe failing | `gcloud compute backend-services get-health <backend>`; `kubectl describe ingress` | Fix pod readiness probe; set correct backend health check protocol in GKE Ingress annotation |
| `Spot preemption` — pod evicted mid-request | Application client sees connection drop | GCE Spot VM preempted; pods evicted without grace period | Node events: `Spot preemption notice`; pod `reason: Evicted` | Use `PodDisruptionBudget`; pin critical pods to standard (non-spot) node pools |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Secondary IP range exhaustion | New pods pending on nodes with available CPU/memory; CA cannot add nodes | `gcloud compute networks subnets describe <subnet> --format='value(secondaryIpRanges)'`; check `ipCidrRange` utilization | Hours before scheduling fully blocked | Add secondary range alias to subnet; enable VPC-native IP aliasing with larger range |
| GKE control plane version falling behind supported skew | Node version > 2 minor versions behind control plane; risk of unsupported kubelet | `gcloud container clusters describe <name> --format='value(currentMasterVersion,currentNodeVersion)'` | Weeks before forced upgrade | Enable auto-upgrade on node pools; schedule manual upgrade before skew limit reached |
| Binary Authorization attestation lag | New images deployed without attestations; builds completing but attestations not created | `gcloud container binauthz attestations list --attestor=<name>` — recent images missing | Days; discovered when new image deployed | Integrate attestation creation into CI pipeline; add pipeline gate before deployment |
| Autopilot pod churn — resource class over-provisioning | Autopilot billing increasing; pods repeatedly scaled and packed differently | `kubectl top pods -A --sort-by=cpu`; Cloud Billing Autopilot pod cost report | Weeks of cost accumulation | Tune pod `requests` to actual usage; use VPA in recommendation mode |
| NodeLocal DNSCache hit ratio decline | DNS latency p99 rising on nodes; queries bypassing cache | Node-local-dns metrics: `coredns_cache_misses_total / coredns_dns_requests_total` | Days before DNS bottleneck | Tune cache TTL in node-local-dns ConfigMap; add cache entries for frequently resolved names |
| GKE node pool auto-upgrade causing rolling restarts | Service degradation during maintenance window; load spikes on remaining pods | Cloud Logging: `container.googleapis.com` node upgrade events; check maintenance window config | Hours warning before maintenance | Set maintenance exclusions for critical periods; configure `maxSurge`/`maxUnavailable` in upgrade settings |
| Filestore NFS share approaching capacity | Write errors appear in pods using Filestore; `df -h` on mount shows > 85% | `gcloud filestore instances describe <instance>` → `capacityGb` vs. usage | Days before writes blocked | Expand Filestore capacity; move to Filestore Enterprise tier with dynamic resizing |
| GKE Ingress backend timeout accumulation | GCLB logs showing increased 502s; backend `TIMEOUT` health check failures | `gcloud logging read 'resource.type="http_load_balancer" AND jsonPayload.statusDetails="backend_timeout"'` | Hours before SLO breach | Increase GKE Ingress `backendConfig` `timeoutSec`; fix slow pod startup / processing |
| Spot pool exhaustion in a zone | Cluster autoscaler cannot provision nodes; pending pod count grows | CA logs: `ZoneResourcePoolExhausted`; `gcloud compute operations list \| grep RUNNING` | Minutes to hours | Add fallback standard node pool; expand to multi-zone Spot provisioning |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# GKE Full Health Snapshot
CLUSTER="${GKE_CLUSTER:-}"
REGION="${GKE_REGION:-us-central1}"
PROJECT="${GCP_PROJECT:-}"

if [[ -z "$CLUSTER" || -z "$PROJECT" ]]; then
  echo "Usage: GKE_CLUSTER=<name> GCP_PROJECT=<project> $0"; exit 1
fi

echo "=== Cluster Status ==="
gcloud container clusters describe "$CLUSTER" --region "$REGION" --project "$PROJECT" \
  --format='table(name,status,currentMasterVersion,currentNodeVersion,location)'

echo ""
echo "=== Node Pool Status ==="
gcloud container node-pools list --cluster "$CLUSTER" --region "$REGION" --project "$PROJECT" \
  --format='table(name,status,config.machineType,autoscaling.enabled,autoscaling.minNodeCount,autoscaling.maxNodeCount)'

echo ""
echo "=== Node Status ==="
kubectl get nodes -o wide --no-headers

echo ""
echo "=== Not-Running Pods (all namespaces) ==="
kubectl get pods -A --field-selector='status.phase!=Running,status.phase!=Succeeded' --no-headers | head -30

echo ""
echo "=== Binary Authorization Policy ==="
gcloud container binauthz policy export --project "$PROJECT" 2>/dev/null \
  | grep -E 'evaluationMode|requireAttestationsBy' || echo "(Binary Authorization not configured)"

echo ""
echo "=== Recent Node Upgrade Operations ==="
gcloud container operations list --filter="operationType=UPGRADE_NODES AND status=RUNNING" \
  --region "$REGION" --project "$PROJECT" --format='table(name,operationType,status,startTime)'
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# GKE Performance Triage — API server, pods, Dataplane V2
CLUSTER="${GKE_CLUSTER:-}"
NAMESPACE="${NAMESPACE:-default}"

echo "=== Top CPU Pods in $NAMESPACE ==="
kubectl top pods -n "$NAMESPACE" --sort-by=cpu 2>/dev/null | head -15

echo ""
echo "=== Top Memory Pods in $NAMESPACE ==="
kubectl top pods -n "$NAMESPACE" --sort-by=memory 2>/dev/null | head -15

echo ""
echo "=== Pods with Restarts > 5 ==="
kubectl get pods -A --no-headers | awk '$5 > 5 {print $1, $2, $5}' | sort -k3 -rn | head -20

echo ""
echo "=== Cilium / Dataplane V2 Drop Count ==="
kubectl -n kube-system get pods -l k8s-app=cilium --no-headers | awk '{print $1}' | head -3 | while read -r pod; do
  echo "-- $pod --"
  kubectl -n kube-system exec "$pod" -- cilium monitor --type drop -n 3 2>/dev/null || echo "  cilium not available"
done

echo ""
echo "=== GKE Ingress Backend Health ==="
kubectl get ingress -A --no-headers | while read -r ns name _rest; do
  echo "-- $ns/$name --"
  kubectl describe ingress -n "$ns" "$name" 2>/dev/null | grep -E 'Address|Rules|Backend|Annotations' | head -10
done

echo ""
echo "=== Node Resource Pressure ==="
kubectl describe nodes | grep -A5 'Conditions:' | grep -E 'MemoryPressure|DiskPressure|PIDPressure|Ready'
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# GKE Connection & Resource Audit — Workload Identity, networking, quotas
CLUSTER="${GKE_CLUSTER:-}"
REGION="${GKE_REGION:-us-central1}"
PROJECT="${GCP_PROJECT:-}"

echo "=== Workload Identity Bindings ==="
kubectl get serviceaccounts -A -o json \
  | jq -r '.items[] | select(.metadata.annotations["iam.gke.io/gcp-service-account"] != null) | "\(.metadata.namespace)/\(.metadata.name) → \(.metadata.annotations["iam.gke.io/gcp-service-account"])"'

echo ""
echo "=== Secondary IP Range Utilization ==="
SUBNET=$(gcloud container clusters describe "$CLUSTER" --region "$REGION" --project "$PROJECT" \
  --format='value(nodeConfig.subnetwork)' 2>/dev/null)
if [[ -n "$SUBNET" ]]; then
  gcloud compute networks subnets describe "$SUBNET" --region "$REGION" --project "$PROJECT" \
    --format='table(secondaryIpRanges[].rangeName,secondaryIpRanges[].ipCidrRange)'
fi

echo ""
echo "=== Firewall Rules Affecting Cluster ==="
gcloud compute firewall-rules list --project "$PROJECT" \
  --filter="name~'gke-${CLUSTER}'" \
  --format='table(name,direction,priority,sourceRanges,targetTags,allowed)'

echo ""
echo "=== NodeLocal DNSCache Status ==="
kubectl -n kube-system get pods -l k8s-app=node-local-dns -o wide

echo ""
echo "=== GCE Quota Usage in Region ==="
gcloud compute regions describe "$REGION" --project "$PROJECT" \
  --format='table(quotas[].metric,quotas[].limit,quotas[].usage)' \
  | grep -E 'CPUS|IN_USE_ADDRESSES|DISKS|INSTANCE'
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| CPU throttling from missing limits in Autopilot | Adjacent pods slow; `container_cpu_cfs_throttled_seconds_total` rising | `kubectl top pods -A --sort-by=cpu`; Autopilot uses `requests` as `limits` — find uncapped pods | Set explicit `requests` on all pods; Autopilot will enforce | Use Autopilot best practice: always set `requests`; use VPA in recommendation mode |
| Spot VM preemption spike | Multiple pods evicted simultaneously; brief service degradation | Node events: `Spot preemption notice`; all evictions cluster to same time window | `PodDisruptionBudget` limits simultaneous evictions; taint critical pods to standard pool | Diversify across standard + spot; use `topologySpreadConstraints` across pools |
| GKE Dataplane V2 (Cilium) policy evaluation overhead | Pod-to-pod latency increase after new NetworkPolicy added; no packet drop but higher latency | `cilium endpoint list`; `cilium monitor --type policy-verdict` for verbose policy traces | Simplify NetworkPolicy rules; reduce `podSelector` complexity | Prefer namespace-level selectors over fine-grained pod selectors; test policy changes in staging |
| Shared GCS bucket throttling affecting multiple pods | `RESOURCE_EXHAUSTED` from Cloud Storage; affects all pods reading/writing same bucket | GCS `gcs.googleapis.com/api/request_count` metric filtered by bucket name and response_code 429 | Implement exponential backoff; shard data across multiple buckets | Use separate GCS buckets per team; enable object lifecycle management to control object count |
| Secondary IP range exhaustion during fast scale-out | New pods pending on healthy nodes; CA provisioning nodes but pods still unschedulable | `kubectl describe pod` → `no IPs available in range`; `gcloud compute networks subnets` usage | Cordon new nodes temporarily; add larger secondary range | Size secondary ranges for 2× max expected pod count; use `/16` or larger ranges |
| Cloud DNS query quota exhaustion | DNS failures for all workloads in a project; affects cross-project resolution | Cloud Monitoring: `dns.googleapis.com/query/request_count` approaching 1000 QPS per project limit | Increase `ndots` threshold to reduce FQDN search; enable NodeLocal DNSCache | Enable NodeLocal DNSCache to absorb intra-cluster DNS; reduce external DNS resolution frequency |
| Filestore NFS throughput contention | Pods writing to Filestore show high latency; `io_time` metric elevated | Filestore `ops_count` and `write_ops_count` per instance; identify top writers via pod logs | Throttle highest-volume writer; expand Filestore to higher tier | Use Filestore Enterprise or Zonal for high-throughput workloads; separate Filestore per team |
| Binary Authorization attestation service rate limit | CI pipeline blocked; attestation creation calls returning 429 | Cloud Logging: `containeranalysis.googleapis.com` 429 errors in pipeline logs | Retry with backoff; serialize attestation creation | Cache attestations per image digest; do not re-attest already-attested images |
| GKE Ingress GCLB backend connection pool exhaustion | Intermittent 502/503 from GCLB; backend reports healthy but requests dropped | GCLB access logs: `statusDetails: "failed_to_connect_to_backend"`; check pod connection limits | Increase pod `maxConnections` via `BackendConfig`; scale pod replicas | Set appropriate `sessionAffinity` and `maxRatePerEndpoint` in `BackendConfig` |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| GKE control plane upgrade (master unavailable window) | `kubectl` API calls return `connection refused`; cluster autoscaler stops; HPA cannot scale; new pods cannot be scheduled | All in-flight control plane operations; running pods continue serving but cannot be changed | `gcloud container clusters describe $CLUSTER --region $REGION --format='value(status)'` shows `RECONCILING`; `kubectl get nodes` times out | Pre-scale deployments before upgrade window; rely on existing PDBs; avoid any `kubectl apply` during upgrade |
| Node pool spot VM mass preemption | Multiple nodes simultaneously removed; pods evicted and pending; scheduler races to reschedule; image pulls on surviving nodes | All workloads on preempted nodes; if no on-demand fallback, cluster capacity drops suddenly | `kubectl get events -A \| grep -i preempt`; node count drops in `kubectl get nodes`; multiple pods in `Pending` state | `gcloud container node-pools create $ON_DEMAND_POOL --cluster $CLUSTER --region $REGION` to add capacity |
| Cluster autoscaler (CA) stuck | Pending pods never scheduled despite available quota; new nodes not provisioned; backlog of unschedulable pods grows | All workloads that need horizontal scale-out; new deployments queue indefinitely | `kubectl describe pod $PENDING_POD \| grep Events` shows `FailedScheduling`; CA logs: `kubectl logs -n kube-system -l app=cluster-autoscaler \| grep -i "scale up"` | Manually scale node pool: `gcloud container clusters resize $CLUSTER --node-pool $POOL --num-nodes $COUNT --region $REGION` |
| Kubernetes etcd backup failure (GKE managed) | No user-visible impact initially; if etcd corruption occurs without backup, cluster state unrecoverable | If combined with etcd failure: total cluster loss | GKE activity logs: `cloudaudit.googleapis.com/activity` — check for etcd backup errors; GKE cluster health in Cloud Console | GKE manages etcd; open P1 support case immediately; ensure Velero or equivalent backup running for workloads |
| Cloud NAT SNAT port exhaustion | Pods making external TCP connections get `connection refused` or `ETIMEDOUT` to external IPs; gRPC streams drop | All pods requiring external internet access; intra-cluster traffic unaffected | VPC flow logs show `SYN_SENT` with no response from external destinations; Cloud NAT logs: `OUT_OF_RESOURCES`; `kubectl exec -it $POD -- curl -v https://external.api.com` times out | Increase Cloud NAT `min_ports_per_vm`; add Cloud NAT IP addresses: `gcloud compute routers nats update $NAT --min-ports-per-vm=128` |
| Workload Identity binding broken | Pods fail to access GCS, BigQuery, Pub/Sub — all GCP API calls return `403 PERMISSION_DENIED`; no K8s-level errors visible | All workloads using Workload Identity for GCP auth | Pod logs: `google.api.core.exceptions.PermissionDenied: 403`; `kubectl exec -it $POD -- curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token` returns error | Use static SA key as emergency fallback: `kubectl create secret generic gcp-sa-key --from-file=key.json` |
| CoreDNS pod crash / OOM | All service discovery DNS lookups fail; HTTP calls between pods fail with `no such host`; entire service mesh collapses | All inter-service traffic using DNS; affects all pods simultaneously | `kubectl get pods -n kube-system -l k8s-app=kube-dns`; pod `OOMKilled` status; `kubectl exec $POD -- nslookup kubernetes` times out | `kubectl rollout restart deployment coredns -n kube-system`; increase CoreDNS memory limit immediately |
| Ingress controller (nginx/GCLB) failure | All external HTTP traffic returns 502/503; internal services running fine; health check target unreachable | All external users; all public-facing services via that ingress | `kubectl get pods -n ingress-nginx`; GCLB health check status: `gcloud compute backend-services get-health $BACKEND --global` | `kubectl rollout restart deployment ingress-nginx-controller -n ingress-nginx`; switch DNS to secondary ingress or Cloud CDN |
| PersistentVolume (GCE PD) attachment failure | Pod stuck in `ContainerCreating`; volume attach fails; StatefulSet pod cannot restart; data inaccessible | Single pod per PV (RWO); StatefulSet replicas for affected shard | `kubectl describe pod $POD \| grep -A 5 Events` shows `FailedAttachVolume`; `gcloud compute disks describe $DISK --zone $ZONE` shows `users` pointing to terminated instance | Force detach: `gcloud compute instances detach-disk $OLD_INSTANCE --disk $DISK --zone $ZONE`; reschedule pod to trigger re-attach |
| Horizontal Pod Autoscaler (HPA) flapping | Pods continuously scale up and down; repeated pod restarts; resource thrash; service instability | Services behind the flapping HPA; upstream callers may see intermittent errors during pod restart | `kubectl describe hpa $HPA_NAME` shows rapid `ScalingActive` events; `kubectl get events --field-selector reason=SuccessfulRescale` shows high frequency | Set HPA stabilization window: `kubectl patch hpa $HPA_NAME --patch '{"spec":{"behavior":{"scaleDown":{"stabilizationWindowSeconds":300}}}}'` |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| GKE node pool version upgrade | Nodes cordon/drain; pods evicted; if PDB prevents eviction, upgrade stalls; pods on new kernel version may exhibit different behavior | During upgrade (rolling, one zone at a time) | `gcloud container node-pools describe $POOL --cluster $CLUSTER --region $REGION --format='value(upgradeSettings)'`; `kubectl get events \| grep Evict` | Pause upgrade via GKE maintenance exclusion; roll back node pool via `gcloud container node-pools rollback` |
| Kubernetes version upgrade (control plane) | Deprecated APIs removed in new version; existing manifests fail to apply; old CustomResourceDefinition versions unavailable | Immediately post-upgrade for deprecated APIs | `kubectl get apiservices`; `kubectl deprecations` (pluto tool); compare API groups before/after | Apply updated manifests using new API versions; Helm chart upgrade may be needed |
| NetworkPolicy applied too broadly | Traffic between previously communicating pods blocked; services return connection refused; DNS resolution across namespaces fails | Immediately on `kubectl apply` | `kubectl describe networkpolicy $POLICY -n $NS`; test connectivity: `kubectl exec $POD -- nc -zv $TARGET_SVC $PORT` | `kubectl delete networkpolicy $POLICY -n $NS` to remove restrictive policy; restore precise policy with correct selectors |
| ConfigMap/Secret change without pod restart | Pods still using cached old configuration; behavior mismatch between old and new pods during rolling deploy | Only after pod restart (Rolling update or crash restart) | `kubectl exec $POD -- env \| grep $VAR` shows old value; check `kubectl describe pod $POD` `envFrom` source vs ConfigMap resourceVersion | Force rolling restart: `kubectl rollout restart deployment $DEPLOY` to pick up new config |
| Helm chart upgrade with breaking values change | Pods crash with wrong configuration; services misconfigured; potential data loss if storage class changes | Immediately on `helm upgrade` | `helm diff upgrade $RELEASE $CHART` before upgrading; `helm history $RELEASE` shows previous revision | `helm rollback $RELEASE $PREV_REVISION` to revert to last good release |
| GKE Dataplane V2 enablement | Existing NetworkPolicies behave differently; eBPF replaces iptables; some iptables-based tools break | Immediately on cluster update | Compare `kubectl get pods -n kube-system` for cilium pods appearing; test NetworkPolicy enforcement | Cannot easily roll back Dataplane V2 once enabled; test all NetworkPolicies in staging with V2 first |
| StorageClass default changed | New PVCs provisioned on wrong storage class; wrong disk type (HDD instead of SSD); performance degradation for stateful workloads | On next PVC creation after change | `kubectl get storageclass` — check `(default)` annotation; compare with team's expected default | `kubectl patch storageclass $CORRECT_SC -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'` |
| GKE Binary Authorization policy change | New pod deployments blocked with `Attestation required and not found`; CI/CD pipeline deployments fail at apply step | Immediately on next deployment after policy change | Pod event: `admission webhook denied the request: Binary Authorization denied the request`; check attestation in Container Analysis | Create missing attestation: `gcloud container binauthz attestations create --artifact-url=$IMAGE_URL --attestor=$ATTESTOR` |
| Resource quota reduction (LimitRange tightened) | New pods cannot be created: `pods "x" is forbidden: exceeded quota`; rolling updates stall | On next deployment after quota change | `kubectl describe resourcequota -n $NAMESPACE`; `kubectl get events -n $NAMESPACE \| grep exceeded` | Increase quota: `kubectl patch resourcequota $QUOTA -n $NS --patch '{"spec":{"hard":{"cpu":"20"}}}'` |
| Container image tag changed from mutable to digest-pinned | Old pods continue running fine; new rollout pulls different layer digest; potential behavior divergence | On next deployment after image reference change | `kubectl get deployment $DEPLOY -o json \| jq '.spec.template.spec.containers[].image'` shows new digest; compare with what's running | Verify digest matches expected image: `docker manifest inspect $IMAGE@$DIGEST` |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| StatefulSet pod rescheduled to different zone — PV unavailable | `kubectl describe pod $POD \| grep -E "Node:\|FailedAttachVolume"` | Pod stuck in `ContainerCreating`; PD in zone A cannot attach to node in zone B | StatefulSet replica unavailable; if quorum-based app, potential service degradation | Add `volumeNodeAffinity` to PV; or add node in same zone as PV: `gcloud container node-pools create $POOL --node-locations=$ZONE` |
| ConfigMap read inconsistency during rolling update | `kubectl get pods -o wide` shows pods at different image versions; test endpoint responses differ | Some pods use new config, some use old; A/B behavior from single service | Intermittent errors; inconsistent responses for clients within same deployment | Use versioned ConfigMap names (e.g., `config-v2`) and update deployment atomically; avoid in-place ConfigMap updates |
| etcd watch cache stale (API server cache inconsistency) | `kubectl get pods` returns stale state; `--watch` misses events; controllers take longer to react | Intermittent: API server returns cached state differing from etcd | Flaky controllers; HPA/CA slow to react to real cluster state | Force re-read: `kubectl get pods --show-managed-fields`; restart API server component (GKE managed — requires support case) |
| Velero backup inconsistency (namespace partially backed up) | `velero backup describe $BACKUP_NAME --details` shows some resources in `Failed` state | Restore creates partial namespace; some Deployments/Services missing | Incomplete application stack after disaster recovery | Re-run backup: `velero backup create $NEW_BACKUP --include-namespaces $NS`; verify all resource kinds included |
| Persistent volume retain policy causes orphaned disk | `kubectl get pv \| grep Released` shows PVs in `Released` state after PVC deletion | Old GCE PD disks persist billing costs; if reclaimed by another PVC, old data accessible | Data leak between tenants; cost waste | Audit released PVs: `kubectl get pv \| grep Released`; delete unused: `kubectl delete pv $PV_NAME`; delete underlying disk: `gcloud compute disks delete $DISK --zone $ZONE` |
| Multi-cluster service divergence (Config Sync / Fleet) | `gcloud alpha container fleet config-management status` shows sync error on cluster | Cluster A has updated policy/config; cluster B running stale version; traffic behaves differently | Policy inconsistency across fleet; security config drift | Force re-sync: `kubectl annotate rootsync root-sync -n config-management-system reconciler.configmanagement.gke.io/last-sync-token-id-` |
| Ingress backend routing inconsistency (multiple path rules) | Some requests return 404 despite backend pods healthy; only specific URL paths affected | Ingress annotation change not propagated to all GCLB backends; partial rollout | Subset of user traffic returning 404/502 | `kubectl describe ingress $INGRESS -n $NS`; compare GCLB backend services: `gcloud compute url-maps export $URL_MAP`; recreate Ingress if GCLB state corrupted |
| HPA target utilization mismatch (CPU vs custom metric) | HPA shows incorrect replica count; custom metric not available; HPA falls back to CPU creating unexpected scaling | `kubectl describe hpa $HPA` shows `<unknown>` for custom metric target | Unexpected pod counts; capacity planning assumptions violated | Verify custom metrics adapter: `kubectl get apiservices \| grep custom.metrics`; check Stackdriver adapter logs |
| PodDisruptionBudget blocking node drain | Node drain stalls: `kubectl drain` command hangs indefinitely; upgrade blocked by over-constrained PDB | `kubectl get pdb -A \| awk '$4==0'` shows PDBs with 0 allowed disruptions | GKE upgrades stall; maintenance windows exceeded | Temporarily relax PDB: `kubectl patch pdb $PDB -p '{"spec":{"minAvailable":0}}'`; restore after drain completes |
| Cluster autoscaler unable to scale down (pod stuck) | Node pool count never decreases despite low utilization; cost increases; `kube-system` pods blocking scale-down | `kubectl logs -n kube-system -l app=cluster-autoscaler \| grep "scale down"` shows pods preventing scale-down | Unnecessary cost; resource waste | Identify blocking pods: CA logs show pod names; annotate with `cluster-autoscaler.kubernetes.io/safe-to-evict: "true"` for non-critical pods |

## Runbook Decision Trees

### Tree 1: Pod Stuck in Pending State

```
Pod stuck in Pending?
├── kubectl describe pod $POD → Events?
│   ├── "Insufficient cpu/memory" → Node capacity exhausted
│   │   ├── kubectl get nodes → All nodes at capacity?
│   │   │   ├── YES → CA should scale up; check CA logs:
│   │   │   │   kubectl logs -n kube-system -l app=cluster-autoscaler | grep "scale up"
│   │   │   │   ├── CA says "no node group" → Add matching node pool
│   │   │   │   └── CA scaling → Wait 3-5 min; manually resize if urgent:
│   │   │   │       gcloud container clusters resize $CLUSTER --node-pool $POOL --num-nodes $N --region $REGION
│   │   │   └── NO → Check pod resource requests vs. available allocatable:
│   │   │       kubectl describe nodes | grep -A 5 "Allocated resources"
│   │   │       → Reduce resource requests in pod spec or add node
│   ├── "Unschedulable: node(s) didn't match node affinity" → Affinity/taint mismatch
│   │   ├── Check pod nodeSelector/affinity: kubectl get pod $POD -o json | jq '.spec.affinity'
│   │   ├── Check available node labels: kubectl get nodes --show-labels
│   │   └── Fix: update affinity rules or label the target node:
│   │       kubectl label node $NODE $LABEL_KEY=$LABEL_VALUE
│   └── "FailedAttachVolume" → PV attachment issue
│       → See Tree 2 below
└── No events → kubectl get pod $POD -o json | jq '.status.conditions'
    ├── ContainersReady=False + reason=PodScheduled=True → Scheduler issue
    │   → kubectl logs -n kube-system kube-scheduler-* | grep $POD
    └── PodScheduled=False → Scheduler not running or quota exceeded
        → kubectl get resourcequota -n $NAMESPACE
```

### Tree 2: PersistentVolume Attach Failure

```
Pod stuck in ContainerCreating with FailedAttachVolume?
├── kubectl describe pod $POD | grep "AttachVolume"
│   ├── "Multi-Attach error for volume" → PV still attached to another node
│   │   ├── Find node holding disk:
│   │   │   gcloud compute disks describe $DISK --zone $ZONE --format='value(users[])'
│   │   ├── Is old node still alive?
│   │   │   ├── YES (old node healthy) → Pod crashed but node survived; wait for GKE to detach (up to 6 min)
│   │   │   │   → Force detach after timeout:
│   │   │   │   gcloud compute instances detach-disk $OLD_NODE --disk $DISK --zone $ZONE
│   │   │   └── NO (old node gone) → Disk stuck, force detach:
│   │   │       gcloud compute instances detach-disk $DELETED_NODE --disk $DISK --zone $ZONE
│   │   │       → Verify: gcloud compute disks describe $DISK --zone $ZONE | grep users
│   └── "Volume zone does not match node zone" → Zonal disk / node mismatch
│       ├── Check PV zone: kubectl get pv $PV -o jsonpath='{.metadata.labels.topology\.kubernetes\.io/zone}'
│       ├── Check pod's target node zone: kubectl get node $NODE --show-labels | grep topology
│       └── Fix: Add node affinity to PV or create new PV in correct zone
│           → For regional clusters: use regional PDs (replication-type=regional-pd)
└── kubelet logs on node: journalctl -u kubelet | grep -i "attach\|volume"
    → If kubelet CSI driver error: kubectl get pods -n kube-system | grep csi
    → Restart CSI node driver: kubectl rollout restart ds csi-gce-pd-node -n kube-system
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Node pool autoscaler uncapped growth | No `maxCount` set or set too high; spike in pending pods triggers runaway scale-out | `gcloud container node-pools describe $POOL --cluster $CLUSTER --region $REGION --format='value(autoscaling.maxNodeCount)'` | Uncontrolled compute billing; project quota exhaustion | `gcloud container node-pools update $POOL --cluster $CLUSTER --region $REGION --max-nodes=10` | Always set `maxCount` per node pool; set GCP billing alerts at 80% of monthly budget |
| GPU node pool left running after batch job | GPU VMs not scaled to 0 after job completion; idle expensive nodes | `gcloud container node-pools describe $GPU_POOL --cluster $CLUSTER --region $REGION --format='value(autoscaling.minNodeCount)'` | ~$2-15/hour per idle GPU node | `gcloud container node-pools update $GPU_POOL --cluster $CLUSTER --region $REGION --min-nodes=0` | Set `minCount=0` for GPU pools; use node auto-provisioning with scale-to-zero |
| Persistent disk orphan accumulation | PV delete policy `Retain`; PVCs deleted but GCE PDs persist | `kubectl get pv | grep Released` and `gcloud compute disks list --filter='NOT users:*'` | Storage billing for unused disks | `gcloud compute disks delete $DISK --zone $ZONE` for confirmed orphans | Audit `Retain` PV policies; implement periodic orphan disk cleanup automation |
| Cross-region egress from GKE pods | Services communicating across regions over public IPs instead of private | VPC flow logs; GCP billing → Networking → Egress breakdown | Significant egress billing; $0.08-0.12/GB cross-region | Force intra-region traffic via internal load balancers; use VPC peering | Use GKE topology-aware routing; deploy multi-regional workloads with local-only service endpoints |
| External load balancer proliferation | Each `Service type: LoadBalancer` creates a GCE LB; dev namespaces left running | `kubectl get svc -A --field-selector spec.type=LoadBalancer | wc -l` | Per-LB cost + forwarding rule quota exhaustion | Delete unused LoadBalancer services; replace with Ingress for HTTP workloads | Use Ingress instead of per-service LBs; enforce quotas on non-production namespaces |
| Container image pull from non-cached registry | Each node pulls full images from Docker Hub instead of Artifact Registry; egress cost | `kubectl get pods -A -o json | jq '.items[].spec.containers[].image' | grep -v gcr.io` | Egress charges per pull from external registry | Mirror images to Artifact Registry: `docker pull $IMG && docker tag $IMG $AR_REPO/$IMG && docker push $AR_REPO/$IMG` | Standardize on Artifact Registry for all images; use image streaming for large images |
| Logging sink exporting too much data | All `stderr/stdout` sent to Cloud Logging without exclusion filters; heavy log-writing apps | `gcloud logging metrics list`; GCP billing → Logging | Logging ingestion charges exceed $1000s/mo | Exclude verbose logs: `gcloud logging sinks update $SINK --exclusion name=verbose,filter='resource.type=k8s_container AND severity<WARNING'` | Define log exclusion filters from day 1; review logging costs monthly |
| Reserved IP addresses not in use | IPs allocated for services/ingress but services deleted; IPs charged even when unused | `gcloud compute addresses list --filter='status=RESERVED'` | Per-IP hourly charge | Release unused IPs: `gcloud compute addresses delete $IP_NAME --region $REGION` | Automate IP cleanup when services are deleted; include in teardown runbooks |
| Spot/preemptible nodes provisioned in wrong pool | Workloads requiring guaranteed nodes schedule on spot; forced eviction triggers excessive restarts | `kubectl get pods -o wide | grep $SPOT_NODE` | Instability for non-fault-tolerant workloads + wasted restart overhead | Taint spot nodes: `kubectl taint node $NODE cloud.google.com/gke-spot=true:NoSchedule`; add tolerations only to fault-tolerant workloads | Use node pool taints from creation; enforce via OPA/Kyverno policy |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot shard on GKE node | Single node CPU at 100% while others idle; pods imbalanced across nodes | `kubectl top nodes` ; `kubectl get pods -o wide \| grep $HOT_NODE \| wc -l` | Pods with same affinity/nodeSelector all land on same node; pod disruption budget blocking rescheduling | Add `podAntiAffinity` rules; use `topologySpreadConstraints`; remove conflicting nodeSelector |
| Connection pool exhaustion to Cloud SQL (via Cloud SQL Proxy) | Application returns "too many connections"; `pg_stat_activity` near `max_connections` | `kubectl exec $PROXY_POD -- sh -c "ss -s"` ; `kubectl logs $PROXY_POD \| grep "max connections"` | Cloud SQL Proxy not using connection pooling; too many app replicas each holding idle connections | Deploy PgBouncer sidecar; reduce pool size per replica; increase Cloud SQL `max_connections` via `gcloud sql instances patch` |
| GC pressure causing pod OOM kill | Pod restart loop; `kubectl describe pod $POD` shows `OOMKilled`; JVM/Go heap growing | `kubectl describe pod $POD \| grep -A5 "OOMKilled"` ; `kubectl top pod $POD --containers` | Container memory limit too tight; JVM heap not bounded; application memory leak | Increase memory limit: `kubectl set resources deployment $DEPLOY -c $CONTAINER --limits=memory=2Gi`; set `-Xmx` for JVM |
| HPA thread pool saturation | HPA not scaling fast enough; pods CPU-saturated while new pods initializing | `kubectl get hpa $HPA -o json \| jq '{currentReplicas, desiredReplicas, currentCPU: .status.currentMetrics}'` ; `kubectl describe hpa $HPA` | HPA scale-up period too conservative (`--horizontal-pod-autoscaler-sync-period`); pods have long startup time | Lower HPA `scaleUp.stabilizationWindowSeconds`; implement readiness probe correctly; use Keda for event-driven scaling |
| Slow Cloud Filestore / GCE PD I/O | Application writes slow; latency spikes; `iostat` inside pod shows high await | `kubectl exec $POD -- iostat -x 1 5` ; `gcloud compute disks describe $DISK --zone $ZONE --format='value(lastAttachTimestamp,type)'` | PD-Standard instead of PD-SSD; Filestore tier too low; IOPS limit reached | Migrate to PD-SSD: create new disk and migrate; or upgrade to `pd-ssd` StorageClass |
| CPU steal on GKE preemptible/spot nodes | Workload latency spikes every few minutes; `top` shows `st` steal percentage | `kubectl exec $POD -- top -b -n 3 \| grep "%Cpu"` (check `st`) | Spot/preemptible node hypervisor contention | Migrate latency-sensitive workloads to standard nodes via nodeSelector/taint; use `cloud.google.com/gke-spot=true:NoSchedule` taint |
| Lock contention on distributed cache (Memorystore Redis) | App latency high; Redis `SLOWLOG` shows blocked commands | `kubectl exec $REDIS_CLIENT_POD -- redis-cli -h $REDIS_IP SLOWLOG GET 10` | Hot key accessed by thousands of pods; Redis single-threaded blocking | Use Redis Cluster to shard hot keys; add local in-memory cache in app; use `WAIT 0 0` to reduce replication blocking |
| Serialization overhead in Kubernetes API server | kubectl commands slow; controllers lag; API server latency metrics high | `kubectl get --raw /metrics \| grep apiserver_request_duration_seconds \| sort -t'"' -k2 -rn \| head -20` | Too many CRDs; large secrets/configmaps; excessive watch connections | Limit watch connections; reduce CRD count; increase API server resources via `gcloud container clusters update` |
| Batch size misconfiguration in Kubernetes Jobs | Jobs completing slowly; many tiny pods created and destroyed; scheduler overloaded | `kubectl get jobs -n $NS -o json \| jq '.items[] \| {name, parallelism: .spec.parallelism, completions: .spec.completions}'` | `parallelism: 1` on batch job that could be parallelized; or too many completions with tiny workloads | Tune `parallelism` and `completions`; use indexed jobs for better work distribution |
| Downstream dependency latency (GCP API throttling) | App logs show `RESOURCE_EXHAUSTED` from GCP API; retries increase latency | `gcloud logging read 'resource.type=k8s_container jsonPayload.message:"RESOURCE_EXHAUSTED"' --limit=10` | App making too many GCP API calls per second; quota exceeded | Implement exponential backoff; request quota increase: `gcloud alpha quotas requests create`; cache GCP API responses |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on Ingress | Browser shows expired cert; `kubectl describe ingress $ING \| grep "ssl-certificate"` | GKE Managed Certificate not renewed; DNS not propagated to GCP for managed cert | All HTTPS traffic fails | `kubectl describe managedcertificate $CERT`; re-provision: `kubectl delete managedcertificate $CERT && kubectl apply -f cert.yaml` |
| mTLS rotation failure (Istio/ASM) | Service-to-service calls fail with `CERTIFICATE_VERIFY_FAILED`; Istio sidecar logs show TLS errors | `kubectl exec $POD -c istio-proxy -- curl -s localhost:15000/config_dump \| jq '.configs[] \| select(.["@type"] \| contains("CertsDump"))'` | Istio CA cert rotation not completed; old sidecars holding expired certs | Force cert rotation: `kubectl rollout restart deployment -n $NS`; check Istiod: `kubectl logs -n istio-system -l app=istiod \| grep cert` |
| DNS resolution failure (CoreDNS overloaded) | Pods get intermittent `NXDOMAIN`; service discovery breaks | `kubectl exec $POD -- nslookup $SVC.svc.cluster.local`; `kubectl top pod -n kube-system -l k8s-app=kube-dns` | CoreDNS overloaded; `ndots:5` causing excessive DNS lookups; CoreDNS memory OOM | Scale CoreDNS: `kubectl scale deployment coredns -n kube-system --replicas=4`; tune `ndots` in pod `dnsConfig` |
| TCP connection exhaustion (GKE node conntrack table) | New connections fail with `ip_conntrack: table full`; kernel logs on node | `kubectl debug node/$NODE -it --image=ubuntu -- bash -c "cat /proc/sys/net/netfilter/nf_conntrack_count"` | Conntrack table exhausted; no new TCP connections possible on node | Increase conntrack table: `kubectl debug node/$NODE -it --image=ubuntu -- sysctl -w net.netfilter.nf_conntrack_max=524288` |
| GKE Ingress backend health check failure | Ingress shows backends as `UNHEALTHY`; traffic not reaching pods | `gcloud compute backend-services get-health $BACKEND_SVC --global` ; `kubectl describe svc $SVC` (check nodePort) | Health check port/path mismatch; pods failing readiness probe; firewall blocking health check port | Fix readiness probe path; update GCE backend health check: `gcloud compute health-checks update http $HC --request-path=/healthz` |
| Packet loss between GKE nodes (VPC routing issue) | Inter-pod communication across nodes fails; cross-node latency spikes | `kubectl exec $POD -- ping -c 50 $OTHER_NODE_POD_IP` (check loss%); `gcloud compute routes list --filter="network=$VPC"` | Missing VPC route for pod CIDR; GKE alias IP range misconfiguration | Check VPC routes: `gcloud compute routes list`; repair alias IP: `gcloud container clusters update $CLUSTER --region $REGION` |
| MTU mismatch on GKE with VPN/interconnect | Large pod-to-pod packets silently dropped; TCP connections hang for large payloads | `kubectl exec $POD -- ping -s 1400 -M do $PEER_POD_IP` | MTU of VPN tunnel < GKE pod network MTU (1460) | Set GKE node MTU: add `--network-performance-config` flag; reduce app MTU via TCP MSS clamping in iptables |
| Firewall rule change blocking pod-to-node-port | NodePort services unreachable; LoadBalancer backends unhealthy | `gcloud compute firewall-rules list --filter="network=$VPC" \| grep nodeport` | GKE auto-created firewall rule deleted or modified | Re-apply GKE firewall rules: `gcloud compute firewall-rules create gke-$CLUSTER-nodeports --allow tcp:30000-32767 --source-ranges $LB_CIDR --network $VPC` |
| SSL handshake timeout to Artifact Registry | `docker pull` from Artifact Registry times out; pod fails to start | `kubectl describe pod $POD \| grep "Back-off pulling image"` ; `gcloud artifacts docker images list $REGISTRY` | Artifact Registry IAM misconfiguration; Workload Identity not granting pull access | Bind IAM: `gcloud artifacts repositories add-iam-policy-binding $REPO --member serviceAccount:$SA --role roles/artifactregistry.reader` |
| Connection reset from GKE Load Balancer on long idle | WebSocket or long-poll connections drop after 600 seconds | App logs showing connection reset; LB timeout value: `gcloud compute backend-services describe $BE_SVC --global --format='value(timeoutSec)'` | GKE HTTP LB default 600s backend timeout | Increase backend timeout: `gcloud compute backend-services update $BE_SVC --global --timeout=3600`; add application-level keepalive |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill on GKE pod | Pod in `OOMKilled` CrashLoopBackOff; container restarting every few minutes | `kubectl describe pod $POD \| grep -A10 "OOMKilled"` ; `kubectl top pod $POD --containers` | Memory limit too low; memory leak in application | `kubectl set resources deployment $DEPLOY --limits=memory=4Gi`; profile memory with `kubectl exec $POD -- go tool pprof` |
| GCE PD disk full (data partition) | Application write failures; Postgres or app data directory at 100% | `kubectl exec $POD -- df -h /data` ; `gcloud compute disks list --filter="name:$DISK"` | PVC size insufficient; no data lifecycle management | Resize PVC: `kubectl patch pvc $PVC -p '{"spec":{"resources":{"requests":{"storage":"100Gi"}}}}'` ; then `gcloud compute disks resize $DISK --size=100` |
| GCE PD disk full (log partition) | Application logs filling `/var/log`; log writes fail | `kubectl exec $POD -- df -h /var/log` | No log rotation; verbose logging; misconfigured log driver | Configure logging to stdout/stderr (GKE collects these automatically); delete old log files: `kubectl exec $POD -- find /var/log -mtime +1 -delete` |
| File descriptor exhaustion in pod | Application cannot open new connections; `too many open files` errors | `kubectl exec $POD -- cat /proc/1/limits \| grep "open files"` ; `ls /proc/1/fd \| wc -l` | Default FD limit (1024) too low for highly concurrent apps | Set via securityContext: `kubectl patch deployment $DEPLOY -p '{"spec":{"template":{"spec":{"containers":[{"name":"$C","securityContext":{"ulimits":[{"name":"nofile","hard":65536,"soft":65536}]}}]}}}}'` |
| Inode exhaustion on GKE node | Node goes `NotReady`; pod scheduling fails with disk pressure | `kubectl debug node/$NODE -it --image=ubuntu -- df -i /` | Thousands of small files from build artifacts or temp dirs | Drain node: `kubectl drain $NODE --ignore-daemonsets`; clean temp files; cordon and uncordon |
| CPU throttle on GKE (CFS throttling) | Application latency spikes despite low CPU utilization; GKE throttled CPU metric elevated | `kubectl exec $POD -- cat /sys/fs/cgroup/cpu/cpu.stat \| grep throttled_time` ; check `container_cpu_cfs_throttled_seconds_total` in Cloud Monitoring | CPU limit too low causing CFS throttle; bursty workload exceeding limit | Remove or increase CPU limit; use VPA to tune requests/limits; monitor `container_cpu_cfs_throttled_periods_total` |
| Swap exhaustion on GKE node (swap disabled by default) | Pod evicted due to memory pressure; node memory usage at 100% | `kubectl describe node $NODE \| grep -E "MemoryPressure\|Evicted"` | Container memory limits not set; pods using more memory than node has | Add memory requests/limits to all pods; enable VPA in recommendation mode; add nodes to node pool |
| GKE node PID limit exhaustion | Node goes `NotReady` with `PIDPressure` condition | `kubectl describe node $NODE \| grep PIDPressure` ; `kubectl debug node/$NODE -it --image=ubuntu -- cat /proc/sys/kernel/pid_max` | Pods running too many processes; fork bombs; misconfigured batch jobs | Set `podPidsLimit` in GKE node config; kill offending pod: `kubectl delete pod $OFFENDING_POD --grace-period=0 --force` |
| GKE node network socket buffer exhaustion | Node-level connection drops; inter-pod traffic degrades | `kubectl debug node/$NODE -it --image=ubuntu -- ss -s` (check dropped count) | High-throughput workload exceeding default socket buffer sizes | Tune socket buffers: `kubectl debug node/$NODE -it --image=ubuntu -- sysctl -w net.core.rmem_max=134217728 net.core.wmem_max=134217728` |
| Ephemeral port exhaustion on GKE node | Pods on node cannot make outbound connections; `connect: cannot assign requested address` | `kubectl debug node/$NODE -it --image=ubuntu -- ss -s` (high TIME_WAIT) | Many short-lived outbound connections; SNAT exhausting port range | Enable GKE NAT port allocation: `gcloud compute routers nats update $NAT --min-ports-per-vm=256 --router=$ROUTER --region=$REGION` |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation causing duplicate pod operations | Kubernetes controller re-applies resource after network partition; duplicate CRD objects created | `kubectl get $CRD -A --sort-by='.metadata.creationTimestamp' \| tail -20` ; check for duplicate names | Duplicate services, jobs, or CRD instances; resource conflicts | Add `metadata.labels` idempotency key to CRDs; use server-side apply: `kubectl apply --server-side --field-manager=$CONTROLLER` |
| Saga/workflow partial failure in Argo Workflows | Argo Workflow step fails mid-dag; compensating cleanup steps not executed | `kubectl get workflow $WF -n $NS -o json \| jq '.status.nodes \| to_entries[] \| select(.value.phase == "Failed") \| .value.displayName'` | Orphaned cloud resources (GCS buckets, PDs, VMs) not cleaned up | Add Argo `onExit` template with cleanup steps; re-trigger cleanup: `argo resubmit $WF -n $NS --memoize` |
| Message replay causing duplicate Pub/Sub processing | GKE consumer pod restarts mid-message; message re-delivered and processed twice | `gcloud pubsub subscriptions describe $SUB --format='value(ackDeadlineSeconds)'` ; check dead-letter topic for duplicates | Duplicate business events processed; data inconsistency | Implement idempotency key in Pub/Sub message attributes; use Cloud Spanner or Firestore to track processed message IDs |
| Cross-service deadlock via Kubernetes leader election | Two controllers both believe they are leader; conflicting reconciliation loops | `kubectl get lease -n kube-system` ; `kubectl get endpoints $LEADER_ENDPOINT -n $NS -o json \| jq '.metadata.annotations'` | Both controllers making conflicting changes; CRD objects flip-flopping | Delete stale lease: `kubectl delete lease $LEASE_NAME -n $NS`; ensure only one controller replica or use proper leader election timeout |
| Out-of-order event processing from Kubernetes informers | Controller processes DELETE event before CREATE for same resource; reconciliation fails | Controller logs showing `resource not found` followed by reconcile errors; check controller `work queue` depth | Controller stuck in error loop; resource never reaches desired state | Implement retry with exponential backoff in controller; check resource version: `kubectl get $RESOURCE -o json \| jq '.metadata.resourceVersion'` |
| At-least-once delivery duplicate from GKE Job restart | Kubernetes Job pod fails and restarts; job work unit re-processed | `kubectl get job $JOB -o json \| jq '{succeeded: .status.succeeded, failed: .status.failed, active: .status.active}'` | Duplicate data processing; external side effects repeated | Set `spec.completionMode: Indexed` with idempotent work assignment; track completion in Cloud Spanner with job index |
| Compensating transaction failure after GKE node preemption | Spot node preempted mid-transaction; compensating cleanup pod scheduled on new node but fails to find resource | `kubectl get events --field-selector reason=Preempting -n $NS` ; `kubectl get pods --field-selector status.phase=Failed -n $NS` | Orphaned cloud resources; inconsistent application state | Run compensating job: `kubectl create job cleanup-$TS --image=$IMG -- /cleanup.sh`; reconcile state against source of truth (GCS/Spanner) |
| Distributed lock expiry mid-operation (etcd-based lock) | Application uses etcd lease for distributed lock; GKE API server slowdown causes lease renewal failure; lock expires | `kubectl exec $POD -- etcdctl lease list 2>/dev/null` ; check application logs for "lease expired" or "lock lost" | Two pods enter critical section simultaneously; data corruption possible | Implement lock fencing with Kubernetes resourceVersion; use `kubectl annotate $RESOURCE lock-holder=$POD_NAME --resource-version=$RV` as atomic compare-and-swap |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: pod without limits monopolizing node | `kubectl top nodes` shows one node at 100% CPU; `kubectl top pods --all-namespaces \| sort -k3 -rn \| head -5` shows runaway pod | Other pods on same node throttled; p99 latency spikes for tenant apps | `kubectl delete pod $RUNAWAY_POD --grace-period=0 --force` to reschedule | Set CPU limits on all pods; use LimitRange to enforce defaults: `kubectl apply -f limitrange.yaml` in each namespace |
| Memory pressure from adjacent tenant's pod | `kubectl describe node $NODE \| grep -A10 "Conditions:"` shows `MemoryPressure: True`; pods evicted | Tenant pods evicted mid-operation; data loss for stateful workloads | `kubectl cordon $NODE` to prevent new scheduling; drain: `kubectl drain $NODE --ignore-daemonsets` | Set memory limits on all pods; use PodDisruptionBudget to protect critical pods from eviction |
| Disk I/O saturation from log-heavy pod | `kubectl exec $POD -- iostat -x 1 5` shows disk at 100%; log volume filling rapidly | Other pods on same node experience slow filesystem operations | `kubectl delete pod $LOG_HEAVY_POD`; redirect logs to stdout | Configure logging to stdout/stderr (avoid writing to disk); set `resources.limits.ephemeral-storage` on log-heavy pods |
| Network bandwidth monopoly from data-processing pod | `kubectl exec $POD -- iftop -i eth0` shows pod consuming full node bandwidth | Other pods on node experience packet loss; inter-service latency spikes | `kubectl delete pod $BANDWIDTH_HOG_POD` to reschedule | Apply NetworkPolicy with `egressBandwidthRate` annotation (GKE supports via `cloud.google.com/l4-rbs`); use node with dedicated NIC |
| Connection pool starvation to Cloud SQL | `kubectl exec $PROXY_POD -- sh -c "ss -s"` shows Cloud SQL Proxy connections saturated; app returns `connection timeout` | One tenant's app holding all Cloud SQL connections; others starved | Restart Cloud SQL Proxy for offending tenant: `kubectl rollout restart deployment $PROXY_DEPLOY -n $TENANT_NS` | Deploy separate Cloud SQL Proxy per tenant; use PgBouncer; set `max_connections` per tenant database |
| Quota enforcement gap: namespace exceeding resource quota | `kubectl get resourcequota -n $TENANT_NS` shows quota exceeded but pods still running (quota not enforced on existing pods) | ResourceQuota added after pods already running; quota not retroactively enforced | Delete over-quota pods: `kubectl delete pods --all -n $TENANT_NS` then reapply with proper limits | Apply ResourceQuota at namespace creation; use admission controller to block over-quota deployments |
| Cross-tenant data leak via shared PersistentVolume | Two tenant deployments mounting same PVC due to misconfigured volume claim name collision | Tenant B reads/writes Tenant A's data | `kubectl get pv -A \| grep "Bound"` and verify each PV is bound to single PVC in correct namespace | Rename colliding PVCs; use storage class with `reclaimPolicy: Delete` and unique names; add namespace-scoped naming conventions |
| Rate limit bypass via pod IP cycling | Tenant cycles pod IPs to bypass per-IP API rate limiting; creates new pods rapidly | GKE API server overloaded; other tenants' deployments slow | `kubectl get pods -n $TENANT_NS --sort-by='.metadata.creationTimestamp' \| tail -20` to detect rapid pod churn | Implement rate limiting at service account level; use GKE Workload Identity for per-workload identity; apply NetworkPolicy |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure | Prometheus shows no pod metrics; dashboards blank | kube-state-metrics pod OOMKilled; or Prometheus RBAC permissions revoked | `kubectl get pods -n monitoring -l app=kube-state-metrics` (check status); `kubectl top pods -n $NS` directly | Restart kube-state-metrics: `kubectl rollout restart deployment kube-state-metrics -n monitoring`; alert on `up{job="kube-state-metrics"} == 0` |
| Trace sampling gap: pod crash not traced | Pod OOMKill or crash not captured in distributed traces | Jaeger/Zipkin sampling at 1%; crashes happen between samples; container exit event not a trace span | `kubectl describe pod $POD \| grep -A5 "Last State:"` for crash details; `kubectl get events -n $NS` | Set sampling to 100% for error traces; add `container_last_seen` alert; use `kubelet_pod_worker_duration_seconds` metric |
| Log pipeline silent drop: pod logs not in Cloud Logging | Pod stdout logs absent from Cloud Logging during incident | GKE log agent (Fluent Bit) crashed or pod evicted; or log entry size exceeds 256KB limit | `kubectl logs $POD -n $NS --previous` directly from kubelet | Check Fluent Bit: `kubectl get pods -n kube-system -l k8s-app=fluentbit-gke`; alert on Fluent Bit pod restarts |
| Alert rule misconfiguration | No alert fires when HPA cannot scale due to resource quota | Alert on `kube_deployment_status_replicas_unavailable` but HPA blocked by quota fires different metric | `kubectl describe hpa $HPA -n $NS \| grep "ScalingDisabled\|insufficient"` | Add alert for `kube_horizontalpodautoscaler_status_condition{condition="ScalingActive",status="false"}`; test all HPA alert rules |
| Cardinality explosion from pod label metrics | Prometheus high memory; GKE pod metrics causing cardinality explosion | Each pod has unique labels (e.g., `pod_template_hash`); multiplied by hundreds of metrics | Aggregate with recording rules: `sum by (namespace, deployment) (container_cpu_usage_seconds_total)` | Drop high-cardinality pod labels in `metric_relabel_configs`; use deployment-level aggregation |
| Missing health endpoint: pod passes readiness but functionally broken | Traffic routes to broken pod; users get errors | Readiness probe checks HTTP 200 on `/` but service is returning stale data or failed DB connection | `kubectl exec $POD -- curl -s localhost:$PORT/readyz \| jq` to check internal health | Implement `/readyz` deep health check testing DB connection and critical dependencies; use GKE pod readiness gates |
| Instrumentation gap: no visibility into node-level events | Node going `NotReady` not caught until pods fail to schedule | No alert on `kubectl get nodes` showing `NotReady`; only pod-level alerts configured | `kubectl get nodes \| grep -v " Ready"` manually; `gcloud logging read 'resource.type=k8s_node' --freshness=10m` | Add alert: `kube_node_status_condition{condition="Ready",status="false"} == 1`; configure GKE cluster notification via Cloud Pub/Sub |
| Alertmanager/PagerDuty outage during GKE cluster incident | GKE incident occurs; Alertmanager pod evicted; no pages sent | Alertmanager running on GKE cluster being investigated; cluster issues evict monitoring pods | `gcloud monitoring alert-policies list --filter='displayName:GKE'` to check Cloud Monitoring fallback | Run Alertmanager outside GKE (Cloud Run or dedicated VM); use GKE cluster notification separate from in-cluster monitoring |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| GKE minor version upgrade rollback | After GKE node pool upgrade, pods fail to schedule; kubelet version mismatch | `gcloud container node-pools describe $POOL --cluster $CLUSTER --region $REGION --format='value(version)'` | Upgrade/downgrade node pool: `gcloud container node-pools upgrade $POOL --cluster $CLUSTER --region $REGION --node-version=$PREV_VERSION` | Enable GKE maintenance windows; test upgrades in staging cluster; use surge upgrades for zero downtime |
| GKE major version upgrade rollback | After upgrading to new major GKE version, deprecated API versions removed; Helm releases broken | `kubectl api-versions \| grep -E "extensions\|apps/v1beta"` missing post-upgrade; `kubectl get events \| grep "no kind"` | Cannot downgrade GKE major version; fix manifests to use new API versions; restore workloads from Velero backup if needed | Run `pluto detect-helm` before upgrade to find deprecated APIs; update all manifests to current API versions pre-upgrade |
| Schema migration partial completion | Database migration job succeeded but only ran on some app pods due to rolling update; DB and code out of sync | `kubectl exec $DB_POD -- psql -c "SELECT version FROM schema_migrations ORDER BY installed_on DESC LIMIT 3;"` | Rollback deployment: `kubectl rollout undo deployment/$DEPLOY`; re-run migration job | Use init container to run migration before new app pods start; add `initContainers:` migration step to Deployment |
| Rolling upgrade version skew | Old and new pod versions running simultaneously; incompatible in-flight requests between versions | `kubectl get pods -o json \| jq '.items[] \| {name: .metadata.name, image: .spec.containers[].image}' \| sort` shows mixed versions | Force rollout restart: `kubectl rollout restart deployment/$DEPLOY` to complete to single version | Maintain backward-compatible APIs during rolling updates; use `maxSurge: 1, maxUnavailable: 0` for controlled rollout |
| Zero-downtime migration gone wrong | Deployment paused mid-rollout; mixed version fleet; old pods not terminating | `kubectl rollout status deployment/$DEPLOY --timeout=300s` hangs; `kubectl get pods \| grep Terminating` | Abort rollout: `kubectl rollout undo deployment/$DEPLOY`; force delete stuck pods: `kubectl delete pod $POD --grace-period=0 --force` | Set `terminationGracePeriodSeconds` appropriately; implement proper `preStop` hooks; test rollout in staging |
| Config format change in ConfigMap breaking old pods | ConfigMap updated with new format; existing pods reading old format fail | `kubectl describe pod $POD \| grep "Error\|invalid\|parse"` in events; pod logs showing config parse error | Rollback ConfigMap: `kubectl rollout undo deployment/$DEPLOY`; `kubectl edit configmap $CM` to restore previous content | Version ConfigMaps (e.g., `config-v2`); use Helm chart versioning; test ConfigMap changes before rolling out |
| Data format incompatibility: etcd backup/restore format change | After GKE version upgrade, etcd data format changed; restore from old backup fails | `gcloud container operations list --filter="operationType=UPGRADE_NODES"` to correlate timing; check etcd version | Restore from Velero application-level backup instead of etcd snapshot; `velero restore create --from-backup $BACKUP` | Use Velero for application-level backups rather than etcd snapshots; test restore procedure before each major GKE upgrade |
| Dependency version conflict: Helm chart with deprecated GKE API | New GKE version removed `policy/v1beta1` PodDisruptionBudget; Helm chart uses old API; upgrade blocks | `helm list -A \| xargs -I{} helm get manifest {} \| grep "apiVersion: policy/v1beta1"` | Patch Helm chart to use `policy/v1` PDB API; `helm upgrade $RELEASE $CHART --set apiVersion=policy/v1` | Run `pluto detect-helm -o wide` before GKE upgrades; maintain Helm chart versions compatible with target GKE version |

## Kernel/OS & Host-Level Failure Patterns

| Failure Mode | Impact on GKE | Detection Command | Remediation |
|-------------|---------------|-------------------|-------------|
| OOM killer terminates container on GKE node | Pod killed with `OOMKilled` exit code; container restarts; application loses in-flight requests | `kubectl get pods -A -o json \| jq '.items[] \| select(.status.containerStatuses[]?.lastState.terminated.reason=="OOMKilled") \| {ns: .metadata.namespace, name: .metadata.name}'`; `gcloud logging read 'resource.type="k8s_node" AND jsonPayload.message=~"oom-kill"' --limit 10` | Increase container memory limits in deployment spec; add `resources.requests.memory` equal to expected working set; enable GKE Vertical Pod Autoscaler: `gcloud container clusters update $CLUSTER --enable-vertical-pod-autoscaling` |
| Inode exhaustion on GKE node | Pods fail to start with `cannot create temporary file`; new containers cannot be created on node; DaemonSet pods evicted | `gcloud compute ssh $NODE -- "df -i /var/lib/containerd"` to check node inode usage; `kubectl describe node $NODE \| grep -A5 "Conditions" \| grep "DiskPressure"` | Cordon and drain node: `kubectl cordon $NODE && kubectl drain $NODE --ignore-daemonsets`; clean container images: `gcloud compute ssh $NODE -- "crictl rmi --prune"`; use larger boot disk: `gcloud container node-pools create $POOL --disk-size=200GB` |
| CPU steal on GKE preemptible/spot nodes | Pod CPU throttling; application latency increases; HPA scales up but new pods also throttled | `kubectl top nodes \| sort -k3 -rn`; `gcloud compute ssh $NODE -- "sar -u 1 5 \| grep steal"`; `kubectl describe node $NODE \| grep -E "Allocatable\|Capacity" -A5` | Migrate critical workloads off preemptible nodes: add `nodeSelector` or `nodeAffinity` for on-demand node pool; use GKE Autopilot for guaranteed resources; set `resources.requests.cpu` to guaranteed QoS |
| NTP skew on GKE node causing cert validation failures | Pod-to-pod TLS handshakes fail with `certificate is not yet valid`; Istio mTLS broken; leader election lease expired prematurely | `gcloud compute ssh $NODE -- "timedatectl status \| grep 'System clock synchronized'"` ; `kubectl logs -n istio-system deployment/istiod \| grep -i "clock\|time\|cert.*not yet"` | GKE nodes should auto-sync NTP; if drifted, restart node: `gcloud compute instances reset $NODE`; for persistent issues, check GKE node image: `gcloud container node-pools describe $POOL --cluster $CLUSTER --format='value(config.imageType)'` |
| File descriptor exhaustion on GKE node | Pods cannot open new connections; kubelet health check fails; node goes `NotReady` | `gcloud compute ssh $NODE -- "cat /proc/sys/fs/file-nr"` — third column is max; `kubectl describe node $NODE \| grep "Ready.*False"` | Increase node fd limit via DaemonSet init container: `sysctl -w fs.file-max=1048576`; reduce per-pod fd usage; use GKE node pools with larger machine types that have higher default limits |
| Conntrack table saturation on GKE node | New connections fail; kube-proxy cannot create SNAT entries; Services return connection timeouts; pods on affected node lose connectivity | `gcloud compute ssh $NODE -- "sysctl net.netfilter.nf_conntrack_count && sysctl net.netfilter.nf_conntrack_max"`; `kubectl get events --field-selector reason=ConntrackFull` | Increase conntrack via node config: `gcloud container node-pools create $POOL --system-config-from-file=sysctl.yaml` with `net.netfilter.nf_conntrack_max: 524288`; use GKE Dataplane V2 (Cilium) which bypasses conntrack for pod-to-pod traffic |
| Kernel panic on GKE node | All pods on node lost; node goes `NotReady`; workloads rescheduled to other nodes after `pod-eviction-timeout` | `kubectl get nodes \| grep NotReady`; `gcloud compute instances describe $NODE --format='value(status)'` shows `TERMINATED`; `gcloud logging read 'resource.type="gce_instance" AND jsonPayload.message=~"kernel panic"' --limit 5` | GKE auto-repairs unhealthy nodes: verify `gcloud container node-pools describe $POOL --cluster $CLUSTER --format='value(management.autoRepair)'` is `True`; for immediate recovery: `gcloud compute instances reset $NODE`; ensure PDB allows rescheduling |
| NUMA imbalance on GKE multi-socket nodes | Pods on same node show inconsistent latency; some pods fast, others slow; CPU usage appears balanced but cache misses high | `gcloud compute ssh $NODE -- "numactl --hardware"`; `gcloud compute ssh $NODE -- "numastat -c $(pgrep -f 'container' \| head -5 \| tr '\n' ' ')"` | Use GKE `cpuManagerPolicy: static` in node pool config for guaranteed QoS pods; use `topologySpreadConstraints` to distribute pods; choose single-NUMA machine types (e.g., `n2-standard-8` instead of `n2-standard-64`) |

## Deployment Pipeline & GitOps Failure Patterns

| Failure Mode | Impact on GKE | Detection Command | Remediation |
|-------------|---------------|-------------------|-------------|
| Image pull failure from GCR/Artifact Registry | Pod stuck in `ImagePullBackOff`; deployment rollout blocked; new version not deployed | `kubectl describe pod $POD \| grep -A3 "Events" \| grep "ImagePullBackOff\|ErrImagePull"`; `gcloud artifacts docker images describe $IMAGE --format='value(image_summary.digest)'` | Verify image exists: `gcloud artifacts docker images list $REPO --filter="package=$IMAGE"`; check IAM: `gcloud projects get-iam-policy $PROJECT \| grep artifactregistry`; ensure node SA has `artifactregistry.reader` role |
| Registry auth failure after SA key rotation | All new pod creations fail with `unauthorized: authentication required`; existing pods unaffected until restart | `kubectl get events -A \| grep "unauthorized"`; `gcloud auth print-access-token --impersonate-service-account=$SA 2>&1 \| grep error` | Recreate image pull secret: `kubectl create secret docker-registry gcr-key --docker-server=gcr.io --docker-username=_json_key --docker-password="$(cat sa-key.json)"`; prefer Workload Identity: `gcloud container clusters update $CLUSTER --workload-pool=$PROJECT.svc.id.goog` |
| Helm drift between Git and live GKE cluster | `helm diff` shows unexpected changes not in Git; manual `kubectl edit` overrides Helm-managed resources | `helm diff upgrade $RELEASE $CHART --values values.yaml 2>&1 \| head -50`; `helm get values $RELEASE -o json \| diff - values.yaml` | Enforce GitOps: add `helm upgrade --force` in CD pipeline to overwrite manual changes; enable ArgoCD or Config Sync: `gcloud beta container fleet config-management apply --membership=$CLUSTER --config=config-sync.yaml` |
| ArgoCD sync stuck on GKE cluster | ArgoCD Application shows `OutOfSync` and `Progressing` for > 10 minutes; new deployment not rolling out | `argocd app get $APP --output json \| jq '{sync: .status.sync.status, health: .status.health.status}'`; `kubectl get application $APP -n argocd -o jsonpath='{.status.conditions[*].message}'` | Force sync: `argocd app sync $APP --force --prune`; check for resource hooks blocking sync: `argocd app resources $APP --orphaned`; check GKE RBAC: `kubectl auth can-i create deployments --as=system:serviceaccount:argocd:argocd-application-controller -n $NS` |
| PDB blocking GKE node pool upgrade | GKE node pool upgrade stalls; nodes cannot drain because PDB `disruptionsAllowed: 0`; upgrade timeout | `gcloud container operations list --filter="operationType=UPGRADE_NODES AND status=RUNNING" --format='table(name,status,statusMessage)'`; `kubectl get pdb -A -o json \| jq '.items[] \| select(.status.disruptionsAllowed==0) \| {ns: .metadata.namespace, name: .metadata.name}'` | Temporarily relax PDB: `kubectl patch pdb $PDB -n $NS --type merge -p '{"spec":{"minAvailable":0}}'`; complete upgrade; restore PDB; use surge upgrades: `gcloud container node-pools update $POOL --cluster $CLUSTER --max-surge-upgrade=3 --max-unavailable-upgrade=0` |
| Blue-green cutover failure on GKE | Traffic switched to green deployment via Service selector change; green pods not ready; users hit errors | `kubectl get svc $SVC -o json \| jq '.spec.selector'`; `kubectl get pods -l version=green -o json \| jq '.items[] \| {name: .metadata.name, ready: .status.containerStatuses[].ready}'` | Rollback Service selector: `kubectl patch svc $SVC -p '{"spec":{"selector":{"version":"blue"}}}'`; add readiness gate before cutover; use GKE Gateway API for traffic splitting: `kubectl apply -f httproute-canary.yaml` |
| ConfigMap drift from manual kubectl edit on GKE | ConfigMap manually edited in cluster; next Helm/ArgoCD sync overwrites manual fix; app breaks again | `kubectl get configmap $CM -n $NS -o yaml \| diff - manifests/configmap.yaml`; `kubectl get configmap $CM -n $NS -o json \| jq '.metadata.managedFields'` | Use Config Sync or ArgoCD to enforce Git as source of truth; annotate manually managed ConfigMaps: `kubectl annotate configmap $CM argocd.argoproj.io/compare-options=IgnoreExtraneous`; use Kustomize overlays for environment-specific config |
| Feature flag misconfiguration during GKE rollout | Canary deployment enables feature flag for all users instead of 5% sample; feature not ready for 100% traffic | `kubectl get configmap feature-flags -n $NS -o json \| jq '.data'`; `kubectl logs deployment/$DEPLOY -n $NS \| grep -i "feature.*flag\|toggle"` | Rollback feature flag ConfigMap: `kubectl rollout undo deployment/$DEPLOY`; use GKE Gateway API traffic splitting for canary: `weight: 5` in HTTPRoute; integrate with feature flag service (LaunchDarkly/Unleash) instead of ConfigMap |

## Service Mesh & API Gateway Edge Cases

| Failure Mode | Impact on GKE | Detection Command | Remediation |
|-------------|---------------|-------------------|-------------|
| Circuit breaker false positive on healthy GKE service | Istio/Anthos Service Mesh trips outlier detection on healthy pods during rolling update; traffic blocked to new version | `istioctl proxy-config cluster $POD.$NS \| grep "circuit_breakers"`; `kubectl logs -n istio-system deployment/istiod \| grep "outlier\|eject"` | Increase outlier detection threshold: `consecutiveGatewayErrors: 10, interval: 30s` in DestinationRule; add `preStop` lifecycle hook to ensure graceful shutdown before Envoy ejects pod |
| Rate limiting hitting legitimate GKE ingress traffic | GKE Ingress or Gateway returns `429` to legitimate users during traffic spike; revenue loss | `kubectl logs -n gke-system deployment/gke-l7-default-backend \| grep 429`; `gcloud logging read 'resource.type="http_load_balancer" AND httpRequest.status=429' --limit 20` | Adjust rate limit: increase `maxRatePerEndpoint` in BackendConfig; use GKE Cloud Armor rate limiting with higher threshold: `gcloud compute security-policies rules update 1000 --security-policy=$POLICY --rate-limit-threshold-count=500` |
| Stale service discovery endpoints in GKE | Endpoints object contains terminated pod IPs; traffic routed to non-existent pods; connection timeouts | `kubectl get endpoints $SVC -n $NS -o json \| jq '.subsets[].addresses[].ip'`; cross-reference with `kubectl get pods -n $NS -o json \| jq '.items[].status.podIP'` | Enable EndpointSlices (default in GKE 1.21+): `kubectl get endpointslices -n $NS`; add readiness probe to remove unhealthy pods from endpoints; check kubelet `--node-status-update-frequency` |
| mTLS rotation interruption in Istio on GKE | Istio root CA cert rotation causes brief mTLS handshake failures between services; connection resets during rotation window | `istioctl proxy-status \| grep -v SYNCED`; `kubectl logs -n istio-system deployment/istiod \| grep -E "cert.*rotation\|secret.*update\|error.*tls"`; `istioctl proxy-config secret $POD.$NS \| grep ACTIVE` | Wait for proxy sync: `istioctl proxy-status` until all show SYNCED; if stuck: `kubectl rollout restart deployment -n istio-system istiod`; schedule cert rotation during low-traffic window; configure `PILOT_CERT_PROVIDER=istiod` for faster rotation |
| Retry storm amplification through GKE service mesh | Service A retries to Service B which retries to Service C; cascading retries overwhelm Service C; cluster-wide latency spike | `istioctl proxy-config route $POD.$NS -o json \| jq '.. \| .retryPolicy? // empty'`; `kubectl top pods -n $NS --sort-by=cpu` — check for CPU spikes in downstream services | Set retry budget in VirtualService: `retries: { attempts: 2, retryOn: "connect-failure,refused-stream" }`; add circuit breaker DestinationRule at each hop; implement request hedging instead of retry at application level |
| gRPC keepalive/max message size mismatch on GKE | gRPC calls fail with `RESOURCE_EXHAUSTED` or connections drop silently; Envoy sidecar rejects oversized messages | `istioctl proxy-config listener $POD.$NS -o json \| jq '.. \| .maxGrpcMessageSize? // empty'`; `kubectl logs $POD -c istio-proxy -n $NS \| grep -E "grpc\|RESOURCE_EXHAUSTED"` | Configure Envoy filter for max message size: apply EnvoyFilter with `max_grpc_message_size: 16777216`; set gRPC keepalive in DestinationRule: `connectionPool.http.h2UpgradePolicy: UPGRADE`; configure app-level keepalive to match Envoy |
| Trace context propagation loss through GKE mesh | Distributed traces show broken spans across services in GKE; cannot correlate requests through Istio sidecar proxies | `istioctl proxy-config bootstrap $POD.$NS -o json \| jq '.bootstrap.tracing'`; `kubectl logs $POD -c istio-proxy -n $NS \| grep -E "tracing\|x-request-id"` | Enable Istio tracing: `istioctl install --set meshConfig.enableTracing=true --set meshConfig.defaultConfig.tracing.zipkin.address=jaeger-collector.observability:9411`; ensure apps propagate `x-request-id`, `x-b3-traceid`, `traceparent` headers |
| GKE load balancer health check marking healthy pods unhealthy | GKE NEG health check uses wrong port/path; all backends marked unhealthy; 502 errors from load balancer | `gcloud compute health-checks describe $HC --format='value(httpHealthCheck.port,httpHealthCheck.requestPath)'`; `gcloud compute backend-services get-health $BS --global --format=json \| jq '.[] \| .status.healthStatus[] \| {instance, healthState}'` | Update health check to match pod port: `gcloud compute health-checks update http $HC --port=$CORRECT_PORT --request-path=/healthz`; configure BackendConfig in GKE: `kubectl apply -f backendconfig.yaml` with correct health check settings |
