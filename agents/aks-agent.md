---
name: aks-agent
provider: azure
domain: aks
aliases:
  - azure-kubernetes-service
  - azure-aks
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - provider-azure
  - component-aks-agent
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
# AKS SRE Agent

## Role
Site Reliability Engineer specializing in Azure Kubernetes Service. Responsible for managed control plane health, Azure CNI and kubenet networking, AAD-integrated authentication via Workload Identity, cluster autoscaler with Azure VMSS, AKS upgrade lifecycle (cordon/drain), Azure Monitor Container Insights, Microsoft Defender for Containers, private cluster connectivity, and ACR integration. Bridges Azure-managed infrastructure with Kubernetes workload reliability.

## Architecture Overview

```
Azure DNS / External DNS
        │
        ▼
Azure Application Gateway (AGIC) / Azure Load Balancer
        │  ← Ingress Controller
        ▼
┌──────────────────────────────────────────────────────┐
│          AKS Control Plane (Microsoft-managed)       │
│  API Server │ etcd │ Scheduler │ Controller Manager  │
│  Accessible via: Public endpoint / Private endpoint  │
│  Health: az aks show + Azure Monitor                 │
└──────────────┬───────────────────────────────────────┘
               │
    ┌──────────▼──────────────────────────────┐
    │  Node Pools                             │
    │  ┌──────────────────────────────────┐   │
    │  │ System Node Pool (CriticalAddons) │   │  ← Required; tainted
    │  │ User Node Pools (workloads)       │   │  ← VMSS-backed
    │  │ Spot Node Pool                    │   │  ← Evictable VMs
    │  └──────────────────────────────────┘   │
    │                                         │
    │  Azure CNI:                             │
    │  ├── Pod IPs from VNet subnet            │
    │  ├── Azure CNI Overlay (shared subnet)  │
    │  └── kubenet (NAT-based, limited)       │
    │                                         │
    │  DaemonSets:                            │
    │  ├── azure-ip-masq-agent               │
    │  ├── csi-azuredisk-node                │
    │  └── microsoft-defender-collector      │
    └─────────────────────────────────────────┘
         │
         ▼
    Azure VNet / Private Link / Service Endpoints
    ├── ACR (container images)
    ├── Azure Key Vault (secrets via CSI driver)
    └── Azure SQL / Cosmos DB / Service Bus
```

AKS manages the Kubernetes control plane (API server, etcd, scheduler) at no extra cost. Node pools run on Azure VMSS (Virtual Machine Scale Sets). Azure CNI assigns real VNet IPs to pods; kubenet uses NAT. Workload Identity replaces Pod Identity (deprecated) and AAD Pod Identity, enabling pods to use managed identities without credential files.

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| `kube_node_status_condition{condition="Ready",status="false"}` | 1 | > 2 | Node NotReady; check VMSS health |
| `node_cpu_usage_percentage` | > 75% | > 90% | Azure Monitor / Container Insights |
| `node_memory_working_set_percentage` | > 80% | > 95% | Working set excludes file cache |
| `kube_pod_status_phase{phase="Pending"}` | > 0 for 3m | > 10 for 5m | Autoscaler may be stuck |
| VMSS instance health check failures | Any | > 2 | VMSS auto-repair trigger |
| `container_cpu_cfs_throttled_seconds_total` rate | > 10% | > 30% | CPU limit too low |
| AKS upgrade available (version lag) | > 1 minor version | > 2 minor versions | AKS retires old versions |
| Azure CNI IP allocation remaining | < 20% of subnet | < 10% | Subnet exhaustion risk |
| Container Insights missing data | > 5 min gap | > 15 min gap | OMS agent pod failing |
| Defender for Containers alerts | Medium severity | High/Critical | Security threat detected |

## Alert Runbooks

### Alert: Node Pool VMSS Instance Unhealthy
**Symptom:** `kube_node_status_condition{condition="Ready",status="false"} >= 1` or VMSS instance in `Failed` provisioning state

**Triage:**
```bash
# Identify not-ready nodes
kubectl get nodes | grep -v Ready

# Map node to VMSS instance
NODE_NAME=<node-name>
# AKS node names are VMSS instance IDs, e.g., aks-nodepool1-12345678-vmss000002
VMSS_NAME=$(kubectl get node $NODE_NAME -o jsonpath='{.metadata.labels.agentpool}')
RESOURCE_GROUP=$(az aks show -n <cluster> -g <rg> --query nodeResourceGroup -o tsv)

# Check VMSS instance state
az vmss list-instances -n $VMSS_NAME -g $RESOURCE_GROUP \
  --query '[].{ID:instanceId,State:provisioningState,Health:healthStatus}' -o table

# View VM instance health details
INSTANCE_ID=$(echo $NODE_NAME | grep -oP '\d+$')
az vmss get-instance-view -n $VMSS_NAME -g $RESOURCE_GROUP --instance-id $INSTANCE_ID \
  --query '{ProvisioningState:statuses[0].displayStatus,PowerState:statuses[1].displayStatus}'

# Reimage unhealthy VMSS instance (will re-join cluster)
az vmss reimage -n $VMSS_NAME -g $RESOURCE_GROUP --instance-ids $INSTANCE_ID

# Or delete instance and let autoscaler replace
az vmss delete-instances -n $VMSS_NAME -g $RESOURCE_GROUP --instance-ids $INSTANCE_ID
```

### Alert: AKS Workload Identity Token Failure
**Symptom:** Pod logs: `DefaultAzureCredential: Failed to retrieve a token from the included credentials` or `AADSTS70011: The provided value for the input parameter 'scope' is not valid`

**Triage:**
```bash
# Confirm Workload Identity is enabled on cluster
az aks show -n <cluster> -g <rg> --query securityProfile.workloadIdentity.enabled

# Verify OIDC issuer URL
az aks show -n <cluster> -g <rg> --query oidcIssuerProfile.issuerUrl -o tsv

# Check ServiceAccount annotation
kubectl get sa <sa-name> -n <ns> \
  -o jsonpath='{.metadata.annotations.azure\.workload\.identity/client-id}'
# Must match the managed identity client ID

# Verify federated identity credential on managed identity
MANAGED_IDENTITY_CLIENT_ID=<client-id>
MANAGED_IDENTITY_NAME=<mi-name>
az identity federated-credential list --identity-name $MANAGED_IDENTITY_NAME -g <rg>
# Check: issuer matches OIDC URL, subject matches system:serviceaccount:<ns>:<sa>

# Check if pod has label azure.workload.identity/use=true
kubectl get pod <pod> -n <ns> -o jsonpath='{.metadata.labels}'

# Test token acquisition from inside pod
kubectl exec -it <pod> -- curl -s -H "Metadata: true" \
  "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/"
```

### Alert: Azure CNI Pod IP Exhaustion
**Symptom:** New pods stuck `Pending`; event: `Failed to allocate IP address: subnet has insufficient available IPs`

**Triage:**
```bash
# Check current IP usage in node pool subnet
VNET_RG=$(az aks show -n <cluster> -g <rg> --query nodeResourceGroup -o tsv)
az network vnet subnet show \
  -n <subnet-name> --vnet-name <vnet-name> -g <rg> \
  --query '{Total:addressPrefix,Available:ipAllocations}' -o json

# Count pods per node vs max pods
kubectl get nodes -o json | \
  jq '.items[] | {name: .metadata.name, allocatablePods: .status.allocatable.pods}'

# Check Azure CNI node capacity (pre-allocated IPs)
kubectl get node <node> -o json | jq '.status.capacity.pods'

# Increase max-pods per node (requires node pool recreate)
az aks nodepool update -n <nodepool> --cluster-name <cluster> -g <rg>
# Note: max-pods cannot be changed in-place; create new pool with --max-pods=<n>

# Switch to Azure CNI Overlay (avoids subnet exhaustion; requires re-deployment)
# Or add larger subnet and update AKS networking config
az network vnet subnet create -n new-subnet --vnet-name <vnet> -g <rg> \
  --address-prefix 10.240.32.0/20

az aks update -n <cluster> -g <rg> \
  --nodepool-name <pool> --vnet-subnet-id /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/new-subnet
```

### Alert: AKS Upgrade Stuck or Failed
**Symptom:** `az aks show` shows `provisioningState: Upgrading` for > 2 hours or `Failed`

**Triage:**
```bash
# Check cluster upgrade status
az aks show -n <cluster> -g <rg> \
  --query '{ProvisioningState:provisioningState,KubeVersion:kubernetesVersion,AgentPoolVersion:agentPoolProfiles[].kubernetesVersion}' -o json

# Check for activity log errors
az monitor activity-log list \
  --resource-group <rg> \
  --caller "Microsoft.ContainerService" \
  --start-time $(date -d '3 hours ago' '+%Y-%m-%dT%H:%M:%S') \
  --query '[?level==`Error`].{Time:eventTimestamp,Operation:operationName.value,Status:status.value,Message:properties.message}' \
  --output table

# Check for PDB blocking node drain
kubectl get pdb -A
kubectl get pdb -A -o json | \
  jq -r '.items[] | select(.status.disruptionsAllowed == 0) | "\(.metadata.namespace)/\(.metadata.name): maxUnavailable=\(.spec.maxUnavailable // "nil"), current_disruptions=\(.status.currentHealthy)"'

# Check stuck terminating pods
kubectl get pods -A | grep Terminating
kubectl delete pod <pod> -n <ns> --force --grace-period=0

# If upgrade truly stuck, retry via portal or CLI (safe to re-run)
az aks upgrade -n <cluster> -g <rg> --kubernetes-version <target-version> --yes

# Check node pool specific upgrade status
az aks nodepool show -n <pool> --cluster-name <cluster> -g <rg> \
  --query '{State:provisioningState,Version:orchestratorVersion}'
```

## Common Issues & Troubleshooting

### Issue 1: AKS Private Cluster kubectl Cannot Connect
**Symptom:** `Unable to connect to the server: dial tcp <private-ip>:443: i/o timeout`

```bash
# Confirm it's a private cluster
az aks show -n <cluster> -g <rg> \
  --query 'apiServerAccessProfile.{EnablePrivateCluster:enablePrivateCluster,PublicFQDN:publicFqdn}'

# Check private endpoint exists
RESOURCE_GROUP_MC=$(az aks show -n <cluster> -g <rg> --query nodeResourceGroup -o tsv)
az network private-endpoint list -g $RESOURCE_GROUP_MC --query '[].{Name:name,PrivateIP:customDnsConfigs[0].ipAddresses[0]}'

# Verify DNS resolution (must resolve to private IP)
nslookup $(az aks show -n <cluster> -g <rg> --query privateFqdn -o tsv)

# If using Azure Bastion or VPN — verify routing
az network vnet peering list --vnet-name <vnet> -g <rg>

# Use Azure Cloud Shell (always has access to private clusters via managed VNet)
# Or use AKS run-command API (no VPN needed)
az aks command invoke -n <cluster> -g <rg> \
  --command "kubectl get nodes"

# Enable authorized IP ranges for hybrid access (temporarily)
az aks update -n <cluster> -g <rg> \
  --api-server-authorized-ip-ranges <your-ip>/32
```

### Issue 2: ACR Image Pull Failure
**Symptom:** `ImagePullBackOff`; event: `unauthorized: authentication required` or `403 Forbidden`

```bash
# Check if ACR is attached to AKS cluster
az aks check-acr -n <cluster> -g <rg> --acr <acr-name>

# Attach ACR to AKS (grants AcrPull role to cluster managed identity)
az aks update -n <cluster> -g <rg> --attach-acr <acr-name>

# Verify role assignment
CLUSTER_IDENTITY=$(az aks show -n <cluster> -g <rg> --query identityProfile.kubeletidentity.objectId -o tsv)
ACR_ID=$(az acr show -n <acr-name> --query id -o tsv)
az role assignment list --assignee $CLUSTER_IDENTITY --scope $ACR_ID \
  --query '[].{Role:roleDefinitionName,Scope:scope}'

# Check if VNet service endpoint or Private Link to ACR is required
az acr show -n <acr-name> --query 'publicNetworkAccess'
# If Disabled, need ACR private endpoint in AKS VNet

# Test image pull from a node (via AKS run-command)
az aks command invoke -n <cluster> -g <rg> \
  --command "kubectl run pull-test --image=<acr-name>.azurecr.io/<image>:<tag> --restart=Never && kubectl delete pod pull-test"
```

### Issue 3: Cluster Autoscaler Not Scaling Out (VMSS Quota Exceeded)
**Symptom:** Pods stuck `Pending`; CA logs: `scale up failed: failed to create nodes for node group`

```bash
# Check cluster autoscaler logs
kubectl -n kube-system logs -l app=cluster-autoscaler --tail=200 | \
  grep -E "ERROR|scale up|failed|quota"

# Check Azure subscription vCPU quota
az vm list-usage --location <region> \
  --query '[?contains(name.value,`cores`) && currentValue > `0`].{Name:name.localizedValue,Used:currentValue,Limit:limit}' \
  -o table

# Check specific VM family quota
az vm list-usage --location <region> \
  --query "[?name.value=='Standard DSv3 Family vCPUs'].{Used:currentValue,Limit:limit}" -o table

# Request quota increase
az vm list-usage --location <region> --query '[?name.value==`<quota-name>`]'
# Submit via: portal.azure.com > Subscriptions > Usage + Quotas

# Workaround: add node pool with different VM size that has available quota
az aks nodepool add -n fallbackpool --cluster-name <cluster> -g <rg> \
  --node-count 1 --node-vm-size Standard_D4s_v5 \
  --enable-cluster-autoscaler --min-count 0 --max-count 20

# Verify VMSS autoscale settings (CA should override, not fight, VMSS autoscale)
RESOURCE_GROUP_MC=$(az aks show -n <cluster> -g <rg> --query nodeResourceGroup -o tsv)
az monitor autoscale list -g $RESOURCE_GROUP_MC
```

### Issue 4: Azure Disk PVC Mount Failing After Node Migration
**Symptom:** Pod stuck `ContainerCreating`; event: `Unable to attach or mount volumes: timed out waiting for the condition`

```bash
# Check PVC and PV status
kubectl describe pvc <pvc-name> -n <ns>
kubectl describe pv <pv-name>

# Check Azure Disk CSI driver pods
kubectl -n kube-system get pods -l app=csi-azuredisk-controller
kubectl -n kube-system logs -l app=csi-azuredisk-controller -c csi-attacher --tail=50

# Get the Azure Disk ID from PV
kubectl get pv <pv-name> -o jsonpath='{.spec.csi.volumeHandle}'
# Format: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Compute/disks/<disk-name>

# Check disk state in Azure
az disk show --ids <disk-id> --query '{State:diskState,AttachedVM:managedBy}'
# If state is "Attached" to a non-existent/old node, force detach
az disk update --ids <disk-id> --no-wait  # Sometimes triggers state refresh

# Check if disk is in wrong zone (topology constraint)
kubectl get pv <pv-name> -o jsonpath='{.spec.nodeAffinity}'
kubectl get node <new-node> -o jsonpath='{.metadata.labels.topology\.kubernetes\.io/zone}'

# If disk stuck attached, delete the attachment forcefully (last resort)
az vm disk detach -g <vm-rg> --vm-name <vm-name> -n <disk-name>
```

### Issue 5: Azure Monitor Container Insights OMS Agent Not Reporting
**Symptom:** Azure Monitor shows no container metrics; `Container Insights` health shows degraded

```bash
# Check OMS agent daemonset
kubectl -n kube-system get ds omsagent
kubectl -n kube-system get pods -l component=oms-agent -o wide

# Check agent logs
kubectl -n kube-system logs <omsagent-pod> --tail=100 | grep -E "ERROR|warning|failed"

# Verify Log Analytics workspace is connected
az aks show -n <cluster> -g <rg> \
  --query addonProfiles.omsagent.config.logAnalyticsWorkspaceResourceID

# Check network connectivity to Log Analytics endpoint
kubectl -n kube-system exec <omsagent-pod> -- \
  curl -s -o /dev/null -w "%{http_code}" \
  https://<workspace-id>.ods.opinsights.azure.com/OperationalData.svc/PostJsonDataItems

# Re-enable Container Insights addon if misconfigured
WORKSPACE_ID=$(az monitor log-analytics workspace show -n <workspace> -g <rg> --query id -o tsv)
az aks enable-addons -n <cluster> -g <rg> \
  --addons monitoring --workspace-resource-id $WORKSPACE_ID

# Restart OMS agent
kubectl -n kube-system rollout restart ds/omsagent
```

### Issue 6: System Node Pool Pressure Evicting User Workloads
**Symptom:** System pods (CoreDNS, metrics-server) evicted or preempted; control plane add-ons failing

```bash
# Check system node pool taint
kubectl get nodes -l agentpool=<system-pool> -o custom-columns=\
  NAME:.metadata.name,TAINTS:.spec.taints

# System node pool should have taint: CriticalAddonsOnly=true:NoSchedule
# Verify user workloads don't have this toleration
kubectl get pods -A -o json | \
  jq -r '.items[] | select(.spec.tolerations[]?.key == "CriticalAddonsOnly") | "\(.metadata.namespace)/\(.metadata.name)"'

# Check system pool resource usage
kubectl describe nodes -l agentpool=<system-pool> | grep -A10 "Allocated resources"

# If system pool is under-resourced, scale it up
az aks nodepool scale -n <system-pool> --cluster-name <cluster> -g <rg> \
  --node-count 3  # Minimum 3 for HA

# For VM size upgrade of system pool: create new pool, drain old
az aks nodepool add -n newsyspool --cluster-name <cluster> -g <rg> \
  --node-count 3 --node-vm-size Standard_D4s_v3 \
  --node-taints CriticalAddonsOnly=true:NoSchedule \
  --mode System
```

## Key Dependencies

- **Azure VNet** — subnet CIDRs must accommodate pod IP allocation (Azure CNI) or NAT range (kubenet); NSG rules must allow control plane communication
- **Azure Active Directory** — AKS RBAC with AAD integration; managed identity for cluster operations; Workload Identity OIDC issuer
- **Azure VMSS** — node pools backed by VMSS; VMSS quota determines max scale-out capacity; VMSS update policies affect upgrade behavior
- **Azure Container Registry (ACR)** — image pulls; attached via `AcrPull` role on kubelet managed identity
- **Azure Disk / Azure Files / Azure Blob** — persistent storage via CSI drivers; requires managed identity permissions
- **Azure Key Vault** — secrets via CSI Secrets Store driver; Workload Identity for access
- **Azure Monitor / Log Analytics** — Container Insights; alerts; requires workspace connected via addon
- **Azure Load Balancer / Application Gateway** — LoadBalancer services and Ingress; NSG must allow health probe traffic
- **Private DNS Zone** — for private clusters; must be linked to VNets that need access to API server

## Cross-Service Failure Chains

- **Azure Active Directory outage** → Workload Identity token requests fail → all Azure SDK calls fail (Storage, CosmosDB, Service Bus) → application errors across all namespaces; kubeconfig auth also fails if AAD-integrated RBAC enabled
- **VMSS quota exceeded** → cluster autoscaler cannot scale → pods remain Pending → HPA adds more pod replicas but they can't schedule → request queue backs up → timeouts propagate to users
- **Azure CNI subnet exhaustion** → no new pod IPs available → rolling deployments fail → new pods can't start → canary deployments stuck → potential version pileup
- **Log Analytics workspace deleted** → OMS agent fails silently → no metrics or logs → alerts stop firing → silent failures go undetected; Defender for Containers also goes dark
- **Private DNS zone peering removed** → private cluster API server unreachable from dev machines → kubectl fails for all operators → incident response via AKS run-command only

## Partial Failure Patterns

- **Single-zone node pool with zonal outage**: Node pool without availability zones loses all capacity; system pool HA requires zone-redundant setup (3 nodes across zones)
- **Spot pool preemption during business hours**: Spot instances can be preempted with 30-second notice; pods evicted but not immediately rescheduled if on-demand pool is at capacity
- **OMS agent version mismatch after upgrade**: Agent may collect incorrect metrics after cluster upgrade until manually updated; metric gaps appear in Container Insights
- **AAD group sync delay**: Adding a user to an AAD group doesn't immediately propagate to AKS RBAC; up to 15 minutes for token cache refresh

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|----------|
| Pod scheduling latency | < 5s | 5–15s | > 30s |
| Node join time (VMSS scale-out) | < 5 min | 5–10 min | > 15 min |
| AKS API server p99 latency | < 1s | 1–3s | > 5s |
| Azure Disk attach time | < 60s | 60–180s | > 5 min |
| Cluster upgrade (per node) | < 10 min | 10–20 min | > 30 min |
| ACR image pull (first pull) | < 30s | 30–120s | > 5 min |
| Container Insights metric delay | < 3 min | 3–5 min | > 15 min |
| Azure Load Balancer backend update | < 30s | 30–90s | > 3 min |

## Capacity Planning Indicators

| Indicator | Source | Trigger | Action |
|-----------|--------|---------|--------|
| Node pool CPU avg > 70% (7-day trend) | Azure Monitor, Container Insights | Sustained | Increase VMSS max, upgrade VM size, or add node pool |
| Azure CNI subnet utilization > 75% | VNet subnet `ipAllocations` | Trending | Plan subnet expansion or migrate to CNI Overlay |
| Subscription vCPU quota usage > 80% | `az vm list-usage` | Threshold | Request quota increase with 2-week lead time |
| Azure Disk quota per subscription | `az disk list` count | > 80% of 50K limit | Request quota increase or use Azure Files for PVCs |
| Cluster version N-2 behind latest | `az aks get-versions` vs current | Any | Plan upgrade; N-3 loses support and auto-upgrade triggers |
| Log Analytics ingest > 80% of plan | Azure Monitor workspace | Daily trend | Upgrade workspace SKU or filter excessive logs |
| Active pods approaching max-pods limit | `kubectl get nodes -o json \| jq ...allocatable.pods` | > 80% of any node | Increase max-pods (requires node pool recreate) |
| Spot pool availability < 30% of target | CA scale-up failures | Any | Diversify Spot VM families or pre-allocate on-demand capacity |

## Diagnostic Cheatsheet

```bash
# Full AKS cluster health summary
az aks show -n <cluster> -g <rg> --query '{State:provisioningState,Version:kubernetesVersion,FQDN:fqdn,Pools:agentPoolProfiles[].{Name:name,State:provisioningState,Count:count,VMSize:vmSize}}' -o json

# List all node pools with autoscaler status
az aks nodepool list --cluster-name <cluster> -g <rg> \
  --query '[].{Name:name,VM:vmSize,Count:count,Min:minCount,Max:maxCount,AutoScale:enableAutoScaling,State:provisioningState}' -o table

# Find all pods not running/completed and their nodes
kubectl get pods -A --field-selector='status.phase!=Running,status.phase!=Succeeded' -o wide

# Check recent cluster autoscaler decisions
kubectl -n kube-system logs -l app=cluster-autoscaler --tail=100 | \
  grep -E "scale (up|down)|New Node Group|removing node|not safe"

# List all Workload Identity service accounts and their bound identities
kubectl get sa -A -o json | \
  jq -r '.items[] | select(.metadata.labels["azure.workload.identity/use"] == "true") | "\(.metadata.namespace)/\(.metadata.name): \(.metadata.annotations["azure.workload.identity/client-id"])"'

# Check VMSS instance health for all node pools
RG=$(az aks show -n <cluster> -g <rg> --query nodeResourceGroup -o tsv)
az vmss list -g $RG --query '[].name' -o tsv | \
  xargs -I{} az vmss list-instances -n {} -g $RG \
  --query '[?provisioningState!=`Succeeded`].{VMSS:name,ID:instanceId,State:provisioningState}'

# List Azure Monitor alert rules for AKS
az monitor alert list -g <rg> --query '[].{Name:name,Severity:severity,Enabled:isEnabled,Condition:criteria}' -o table

# Check all PVCs and their backing Azure resources
kubectl get pvc -A -o json | \
  jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name): \(.spec.volumeName) [\(.status.phase)]"'

# Run ad-hoc command without kubectl access (private cluster)
az aks command invoke -n <cluster> -g <rg> --command "kubectl top nodes"

# Check recent upgrade availability
az aks get-upgrades -n <cluster> -g <rg> -o table
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|-------------------|-------------|
| AKS API Server Availability | 99.9% | 43.2 min/month | Azure SLA; `/healthz` prober from within VNet |
| Node Pool Healthy Instance Ratio | 99.0% | 7.2 hr/month | `(ready nodes / total nodes) >= 0.95` avg over 5m |
| Pod Scheduling Success Rate (< 30s) | 99.5% | 3.6 hr/month | `kube_pod_status_scheduled_time - kube_pod_created < 30s` |
| Persistent Volume Attach Success Rate | 99.0% | 7.2 hr/month | CSI driver operation success rate from Container Insights |

## Configuration Audit Checklist

| Check | Command | Expected |
|-------|---------|----------|
| Cluster version within N-1 | `az aks show -n <c> -g <rg> --query kubernetesVersion` | Within 1 minor of latest AKS |
| Workload Identity enabled | `az aks show -n <c> -g <rg> --query securityProfile.workloadIdentity.enabled` | `true` |
| OIDC issuer enabled | `az aks show -n <c> -g <rg> --query oidcIssuerProfile.enabled` | `true` |
| System node pool zone redundant | `az aks nodepool show --mode System --query availabilityZones` | `["1","2","3"]` |
| Autoscaler enabled on user pools | `az aks nodepool show --query enableAutoScaling` | `true` |
| Defender for Containers enabled | `az aks show --query securityProfile.defender.securityMonitoring.enabled` | `true` |
| Container Insights enabled | `az aks show --query addonProfiles.omsagent.enabled` | `true` |
| Azure Policy add-on enabled | `az aks show --query addonProfiles.azurepolicy.enabled` | `true` |
| Private cluster (production) | `az aks show --query apiServerAccessProfile.enablePrivateCluster` | `true` for prod |
| Authorized IP ranges set (non-private) | `az aks show --query apiServerAccessProfile.authorizedIpRanges` | Non-empty, not `0.0.0.0/0` |

## Log Pattern Library

| Log Pattern | Source | Meaning |
|-------------|--------|---------|
| `AADSTS70011: The provided value for the input parameter 'scope' is not valid` | Application/Workload Identity | Wrong scope or audience in MSAL token request |
| `Failed to allocate address in range` | Azure CNI | Subnet IP exhaustion; pods cannot start |
| `Error response from daemon: failed to create shim task` | containerd | Container runtime failure; check containerd service |
| `azure.BearerAuthorizer#WithAuthorization: Failed to refresh the Token` | Azure SDK | Managed identity metadata server unreachable or MSI not assigned |
| `Failed to attach disk ... OperationNotAllowed` | Azure Disk CSI | Disk encrypted with CMK missing key vault access |
| `FailedMount: Unable to mount volumes for pod` | kubelet | CSI driver pod failing or Azure Disk quota exceeded |
| `node.kubernetes.io/not-ready:NoExecute` toleration expired | kubelet | Pod evicted from not-ready node; check node condition |
| `OOMKilled` | kubelet | Container exceeded memory limit |
| `Error: ImagePullBackOff` | kubelet | ACR not attached or network policy blocking pull |
| `Readiness probe failed: HTTP probe failed with statuscode: 503` | kubelet | Application not ready; check deployment logs |
| `scale_up_error... ResourceQuotaExceeded` | cluster-autoscaler | Azure quota exhausted; cannot add VMSS instances |
| `error from cluster: ... azure: Service returned an error. Status=429` | azure-cloud-provider | Azure API throttling; too many VMSS calls |

## Error Code Quick Reference

| Error | Service | Meaning | Fix |
|-------|---------|---------|-----|
| `QuotaExceeded` | Azure Compute | vCPU or resource quota hit | Request quota increase via Azure Portal |
| `AADSTS65001` | Azure AD | Missing consent for required AAD permission | Grant admin consent for application |
| `AuthorizationFailed` | Azure RBAC | Managed identity missing Azure RBAC role | Add role assignment (`az role assignment create`) |
| `OperationNotAllowed` | Azure Disk | Disk operation blocked (encryption, lock, policy) | Check Key Vault access for CMK-encrypted disks |
| `NodeNotReady` | Kubernetes | Node failed health check | Check VMSS instance state; reimage or delete |
| `PodSecurityViolation` | Azure Policy / OPA | Pod spec violates security baseline | Update pod spec to comply with policy |
| `ContextDeadlineExceeded` | Various | Timeout between components | Network issue or overloaded API server |
| `InvalidTargetPort` | AKS / LB | Service targetPort doesn't match container port | Fix service spec targetPort |
| `HTTP 401 Unauthorized` | ACR / API Server | Expired token or missing auth | Refresh credentials; check AAD integration |
| `AttachVolume.Attach failed... AlreadyInUse` | Azure Disk | Disk attached to old node | Force detach from old VM; delete PVC and re-create |
| `UpgradeFailed: max surge exceeded` | AKS Upgrade | Node pool upgrade failed during surge | Check quota; reduce surge count; retry upgrade |
| `VMSS capacity 0 on scale in` | Cluster Autoscaler | Scale-in attempted below minimum count | Check autoscaler min-count configuration |

## Known Failure Signatures

| Signature | Root Cause | Distinguishing Indicator |
|-----------|-----------|------------------------|
| All pods stuck Pending, nodes healthy | Azure CNI subnet full | `kubectl describe pod`: `Failed to allocate IP`; subnet `ipAllocations` at max |
| kubectl auth fails for all AAD users | AAD / OIDC endpoint unreachable or cluster state `Failed` | `az aks show` provisioningState=`Failed`; AAD service health check |
| Node pool at 0 capacity despite demand | VMSS quota exceeded or Spot capacity unavailable | CA logs: `ResourceQuotaExceeded` or `ZoneResourcePoolExhausted` |
| Workload Identity fails only in one namespace | Federated credential subject mismatch | Token error 404 vs 401; check subject includes correct namespace/SA |
| Disk attach hangs for > 5 min | Disk stuck in `Attached` state on terminated VM | `az disk show` shows `managedBy` pointing to non-existent VM |
| CoreDNS OOMKilled repeatedly | Insufficient memory limit for cluster size | `kubectl logs coredns --previous`: OOMKilled; top shows memory spike |
| All external traffic failing | Azure Load Balancer NSG rule removed | `kubectl get svc` shows external IP but LB health probe failing; check NSG |
| Node rejoin loop after VMSS reimage | Cluster upgrade left nodes in incompatible version | Node events: `kubelet version mismatch`; `az aks nodepool show` version mismatch |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `dial tcp: connection refused` to ClusterIP | HTTP client / gRPC / JDBC | kube-proxy iptables rules stale; Azure CNI overlay route missing after node replacement | `kubectl get endpoints <svc>`; `kubectl -n kube-system logs -l component=kube-proxy` | Restart kube-proxy; re-register node; check Azure CNI IPAM log |
| `Error from server (ServiceUnavailable)` on kubectl | kubectl / client-go | AKS API server in upgrade or node pool operation; control plane VMSS scaling | `az aks show --query 'provisioningState'`; Azure Portal AKS activity log | Wait for in-progress operation; check `az aks get-credentials` for correct kubeconfig |
| `AADSTS700016: Application not found` on pod startup | MSAL / Azure SDK (Workload Identity) | Federated credential subject mismatch; AKS OIDC issuer URL not registered in AAD app | `az identity federated-credential list --identity-name <name>`; compare `subject` with `system:serviceaccount:<ns>:<sa>` | Recreate federated credential with correct subject; verify namespace and SA name exactly |
| `403: The client does not have authorization` from Azure Storage/KeyVault | Azure SDK | Managed Identity not assigned required Azure RBAC role on the resource | `az role assignment list --assignee <client-id>`; compare against required role | `az role assignment create --assignee <client-id> --role <role> --scope <resource>` |
| Pod stuck `Pending` — `Failed to allocate IP` | kubelet / Azure CNI | Azure CNI subnet IP pool exhausted; VMSS NIC IP configuration at limit | `kubectl describe pod`; `az network vnet subnet show --query 'ipConfigurations \| length'` | Add larger subnet via CNI overlay or BYOCNI; scale out to new node pool in different subnet |
| `x509: certificate signed by unknown authority` connecting to ACR | containerd / Docker | ACR firewall rule blocking node IPs; private endpoint DNS resolving to public IP | `nslookup <acr>.azurecr.io` from node; `az acr show --query 'publicNetworkAccess'` | Enable private endpoint DNS zone; whitelist AKS node subnet in ACR network rules |
| `MSI response failed: status 404` or `Identity not found` | Azure Instance Metadata Service / MSAL | Pod Identity (legacy AAD Pod Identity) deleted; VMSS identity assignment removed | `az vmss identity show --name <vmss>`; check aad-pod-identity controller logs | Re-attach managed identity to VMSS; restart aad-pod-identity NMI daemonset |
| Disk attach timeout — pod stuck `ContainerCreating` | kubelet / Azure Disk CSI | Disk stuck `Attached` on terminated VM; VMSS scale-in did not detach | `az disk show --query 'managedBy'`; disk CSI controller logs | Force-detach disk: `az disk update --disk-access-id ""`; restart CSI controller |
| DNS `NXDOMAIN` for `<svc>.<ns>.svc.cluster.local` | Application DNS resolver | CoreDNS OOMKilled due to insufficient memory limit; Upstream Azure DNS resolution broken | `kubectl -n kube-system describe pod -l k8s-app=kube-dns`; check OOMKilled events | Increase CoreDNS memory limit; check `coredns-custom` ConfigMap for misconfiguration |
| `connection reset by peer` from external Load Balancer | HTTP client | Azure Load Balancer NSG rule removed; LB health probe failing; idle timeout mismatch | `kubectl get svc`; `az network lb probe show`; NSG inbound rules for NodePort range | Restore NSG rule; fix health probe; set `azure-load-balancer-tcp-idle-timeout` annotation |
| `429 TooManyRequests` from Azure Resource Manager | Azure SDK / Terraform | AKS control plane and workloads sharing ARM throttle budget on same subscription | Azure Monitor: `Microsoft.ContainerService/managedClusters` ARM throttle metric | Batch ARM calls; spread ARM-heavy operations across subscriptions |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Azure CNI subnet approaching exhaustion | New pods scheduled but IP allocation takes > 30 s; subnet `ipConfigurations` count rising | `az network vnet subnet show --query 'ipConfigurations \| length(@)'` vs. subnet CIDR capacity | Hours before new nodes can't join | Migrate to CNI overlay (`--network-plugin-mode overlay`); add new subnet node pool |
| Node pool AMI (VHD) falling behind supported skew | Nodes on old VHD version; `az aks nodepool show --query 'nodeImageVersion'` shows stale image | `az aks nodepool get-upgrades --cluster-name <name> --nodepool-name <pool>` | Weeks; blocks AKS version upgrades | Enable auto-upgrade channel on node pool; schedule rolling upgrades during low-traffic windows |
| Azure Disk CSI driver provisioning latency creep | PVC binding time p99 increasing; CSI driver logs showing ARM API calls taking > 10 s | `kubectl -n kube-system logs -l app=csi-azuredisk-controller --since=1h \| grep "elapsed"` | Days before PVC creation times out | Check ARM throttle budget; upgrade Azure Disk CSI driver; use Premium SSD v2 for lower ARM overhead |
| AAD / Entra conditional access policy change blocking kubectl | Intermittent auth failures; affected users vary by location or device compliance | Azure AD Sign-in logs: failed logins from AKS kubeconfig client ID | Hours; users fail one by one as token cache expires | Review and update conditional access policy; whitelist AKS management traffic |
| VMSS quota approaching region limit | Cluster autoscaler logs `ResourceQuotaExceeded`; scale-out requests silently fail | `az vm list-usage --location <region> --query '[?name.value==\`cores\`]'` | Days before CA fully blocked | Request quota increase; add node pools in alternate regions |
| CoreDNS cache miss rate rising | DNS latency p99 increasing; CoreDNS CPU spike during traffic peaks | CoreDNS metrics: `coredns_cache_misses_total / coredns_dns_requests_total` ratio via Prometheus/Container Insights | Days before DNS becomes bottleneck | Scale CoreDNS replicas; tune `cache` TTL in `coredns-custom` ConfigMap |
| Azure Monitor Container Insights agent disk fill | Agent `omsagent` pods restarting; metric and log gaps in Azure Monitor workspace | `kubectl -n kube-system top pods -l component=oms-agent`; check agent disk usage | Days; metrics loss before disk full | Increase node disk size; reduce agent collection interval; filter low-value namespaces from collection |
| Workload Identity federation token expiry misalignment | MSAL token refresh failing periodically; `401 Unauthorized` spikes every 1 h | MSAL logs in pod; check `tokenExpirationSeconds` on projected SA volume vs. Entra token lifetime | Silent until token expires and refresh blocked | Align SA token `expirationSeconds` with Entra conditional access token lifetime policy |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# AKS Full Health Snapshot
CLUSTER="${AKS_CLUSTER:-}"
RG="${AKS_RG:-}"

if [[ -z "$CLUSTER" || -z "$RG" ]]; then
  echo "Usage: AKS_CLUSTER=<name> AKS_RG=<rg> $0"; exit 1
fi

echo "=== Cluster Provisioning State ==="
az aks show --name "$CLUSTER" --resource-group "$RG" \
  --query '{ProvisioningState:provisioningState,Version:kubernetesVersion,FQDN:fqdn,PowerState:powerState.code}' -o table

echo ""
echo "=== Node Pool Status ==="
az aks nodepool list --cluster-name "$CLUSTER" --resource-group "$RG" \
  --query '[*].{Name:name,Mode:mode,State:provisioningState,Count:count,VM:vmSize,NodeVersion:nodeImageVersion}' -o table

echo ""
echo "=== Node Status ==="
kubectl get nodes -o wide --no-headers

echo ""
echo "=== Not-Running Pods (all namespaces) ==="
kubectl get pods -A --field-selector='status.phase!=Running,status.phase!=Succeeded' --no-headers | head -30

echo ""
echo "=== Azure CNI Subnet IP Usage ==="
az aks show --name "$CLUSTER" --resource-group "$RG" \
  --query 'agentPoolProfiles[*].{Pool:name,Subnet:vnetSubnetId}' -o table

echo ""
echo "=== Container Insights Agent Status ==="
kubectl -n kube-system get pods -l component=oms-agent -o wide

echo ""
echo "=== Recent AKS Activity Log ==="
az monitor activity-log list --resource-group "$RG" --offset 1h \
  --query '[?contains(resourceId,`Microsoft.ContainerService`)].{Time:eventTimestamp,Operation:operationName.localizedValue,Status:status.localizedValue}' -o table 2>/dev/null | head -20
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# AKS Performance Triage — pods, nodes, CSI, DNS
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
echo "=== Node Resource Pressure ==="
kubectl describe nodes | grep -A5 'Conditions:' | grep -E 'MemoryPressure|DiskPressure|PIDPressure|Ready'

echo ""
echo "=== Azure Disk CSI Controller Logs (errors, last 50 lines) ==="
kubectl -n kube-system logs -l app=csi-azuredisk-controller --tail=50 2>/dev/null \
  | grep -iE 'error|failed|timeout|throttl' | tail -20

echo ""
echo "=== CoreDNS Status and Memory ==="
kubectl -n kube-system get pods -l k8s-app=kube-dns -o wide
kubectl -n kube-system top pods -l k8s-app=kube-dns 2>/dev/null

echo ""
echo "=== PodDisruptionBudgets Blocking Eviction ==="
kubectl get pdb -A --no-headers | awk '$5 == "0" {print $1, $2, $3, $4, $5}'
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# AKS Connection & Resource Audit — identity, networking, quotas
CLUSTER="${AKS_CLUSTER:-}"
RG="${AKS_RG:-}"
LOCATION="${AKS_LOCATION:-eastus}"

echo "=== Workload Identity Federated Credentials ==="
kubectl get serviceaccounts -A -o json \
  | jq -r '.items[] | select(.metadata.annotations["azure.workload.identity/client-id"] != null) | "\(.metadata.namespace)/\(.metadata.name) → \(.metadata.annotations["azure.workload.identity/client-id"])"'

echo ""
echo "=== Managed Identities on Node VMSS ==="
az vmss list --resource-group "MC_${RG}_${CLUSTER}_${LOCATION}" 2>/dev/null \
  --query '[*].{VMSS:name,Identity:identity.type,UserAssigned:keys(identity.userAssignedIdentities)}' -o table

echo ""
echo "=== ARM API Throttle Status (last 1 h) ==="
az monitor metrics list \
  --resource "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/${RG}/providers/Microsoft.ContainerService/managedClusters/${CLUSTER}" \
  --metric "apiserver_request_total" --interval PT5M 2>/dev/null \
  --query 'value[*].timeseries[*].data[-1]' -o table || echo "(Metric not available)"

echo ""
echo "=== NSG Rules on Node Subnet ==="
NSG=$(az network nsg list --resource-group "MC_${RG}_${CLUSTER}_${LOCATION}" \
  --query '[0].name' -o tsv 2>/dev/null)
if [[ -n "$NSG" ]]; then
  az network nsg rule list --nsg-name "$NSG" \
    --resource-group "MC_${RG}_${CLUSTER}_${LOCATION}" \
    --query '[*].{Name:name,Priority:priority,Direction:direction,Access:access,Port:destinationPortRange}' -o table
fi

echo ""
echo "=== Region Core Quota ==="
az vm list-usage --location "$LOCATION" \
  --query '[?name.value==`cores` || name.value==`standardDSv3Family`].{Name:name.localizedValue,Limit:limit,Current:currentValue}' -o table
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| CPU throttling from missing limits | Neighboring pods degrade; `container_cpu_cfs_throttled_periods_total` high | `kubectl top pods -A --sort-by=cpu`; find pods without `resources.limits.cpu` | Set CPU limit on offending pod; cordon node if severe | Enforce `LimitRange` in all namespaces; use Kyverno/OPA policy requiring limits |
| Azure Disk attach serialization — only one disk attached per node at a time | Multiple pods stuck `ContainerCreating`; disk attach queue builds up | Azure Disk CSI logs: concurrent attach requests queued; `az disk show --query 'diskState'` | Add `--max-concurrent-disk-attachments` to CSI driver; scale out nodes | Use one PVC per pod; avoid bulk scheduling pods with new disk mounts |
| VMSS over-provisioning starvation | Cluster autoscaler provisions nodes but Azure returns partial capacity; some nodes never Ready | CA logs: node provisioned but stays `NotReady`; `az vmss list-instances` shows instances in `Failed` state | Reduce VMSS max count; switch to a different VM SKU | Diversify VM SKU list in node pool; use at least 3 SKU options for CA fallback |
| ARM API throttle contention between AKS and other services | AKS operations (node pool scale, upgrade) fail intermittently with `429`; Terraform runs same time | Azure Monitor: ARM throttle events from multiple principals at same time | Stagger AKS operations and IaC deployments; use different service principals per system | Separate managed identity for AKS from IaC deployment principal; avoid concurrent ARM-heavy operations |
| Azure Load Balancer SNAT port exhaustion | Outbound connection failures from pods; `502/503` to external endpoints; `SNAT port exhausted` in ALB logs | `az network lb show --query 'outboundRules'`; check `allocatedSnatPorts` vs. connection count | Add outbound rule with higher SNAT port allocation; enable NAT Gateway | Use NAT Gateway instead of default SNAT; set explicit `outboundRules` with sufficient port count |
| CoreDNS memory pressure from large cluster | CoreDNS OOMKilled; DNS failures cluster-wide | `kubectl -n kube-system top pods -l k8s-app=kube-dns`; check CoreDNS memory limit | Increase CoreDNS memory limit; add replicas | Scale CoreDNS replicas proportional to node count (1 per 100 nodes); set `autoscaler` ConfigMap |
| Azure Monitor Container Insights agent CPU spike | `omsagent` pods consuming > 500m CPU; node CPU available for workloads reduced | `kubectl top pods -n kube-system -l component=oms-agent`; check collection interval | Reduce collection frequency (`containerLogTailPersistenceEnabled: false`); filter namespaces | Tune Container Insights `ConfigMap` to exclude high-volume namespaces from log collection |
| Ingress controller (AGIC) ARM poll loop | AGIC pods causing sustained ARM API calls; shares throttle budget with cluster operations | AGIC pod logs: frequent ARM GET operations; ARM throttle monitor shows AGIC service principal | Reduce AGIC reconciliation interval; pin AGIC version with tunable poll interval | Set AGIC `reconcilePeriodSeconds` to 60+; use separate service principal for AGIC with higher quota |
| Azure Key Vault CSI driver secret sync flood | All pods trigger Key Vault API calls at startup; `429` from Key Vault | Key Vault diagnostic logs: `GetSecret` calls from multiple pod IPs simultaneously | Enable Key Vault CSI driver cache (`rotationPollInterval: 2m`); stagger pod startups | Use `SecretProviderClass` with `syncSecret: true` + rotation polling; avoid per-request Key Vault calls |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| etcd quorum loss on control plane | API server cannot commit writes → `kubectl apply` returns 503 → workload controllers (Deployment, ReplicaSet) cannot reconcile → pods not rescheduled on failures | Entire cluster; no new scheduling, scaling, or rollouts | `kubectl get nodes` hangs; API server log: `etcdserver: request timed out`; `az aks show --query 'provisioningState'` shows `Failed` | AKS control plane is Microsoft-managed — open P1 support case; mitigate by avoiding any cluster mutations |
| Node pool VM VMSS allocation failure | Cluster Autoscaler requests new VMs → Azure returns `InsufficientCapacity` → pods stay `Pending` → HPA cannot scale → application SLA missed | All workloads requiring scale-out; new pods stuck in `Pending` | CA logs: `Failed to scale up: 429 Quota exceeded / InsufficientCapacity`; `kubectl get events | grep FailedScale` | Failover CA to a second node pool with different SKU; use spot fallback pool; request quota increase |
| CoreDNS crash loop | All pod DNS lookups fail → services cannot connect to each other → cascading application 500 errors | Entire cluster; all inter-service communication broken | `kubectl -n kube-system get pods -l k8s-app=kube-dns` shows `CrashLoopBackOff`; application logs: `dial tcp: lookup <svc> on 10.0.0.10:53: read udp: i/o timeout` | `kubectl -n kube-system rollout restart deployment/coredns`; temporarily set pod `dnsPolicy: None` with hardcoded IP as interim |
| Azure Load Balancer rule limit reached (250 rules) | New Services of type `LoadBalancer` fail to provision → applications cannot receive external traffic | New service exposures blocked; existing services unaffected | `kubectl describe svc <new-svc>` shows `Error syncing load balancer: number of rules exceeds limit`; Azure Portal ALB shows 250/250 rules | Remove unused LoadBalancer services; consolidate with Ingress controller; request ALB limit increase |
| Kube-proxy iptables rule overflow | Iptables rules count exceeds kernel limit on large clusters → new Service endpoints not routed → connection refused to some services | New Services and Endpoints not reachable; existing routes may still work | `iptables -L | wc -l` approaching limit; kube-proxy log: `Failed to sync iptables rules`; application connection refused errors | Switch kube-proxy to IPVS mode: set `--proxy-mode=ipvs` in kube-proxy ConfigMap and restart |
| Azure CNI IP exhaustion in subnet | New pods cannot be assigned IPs → stuck in `ContainerCreating` with `failed to allocate IP` | Entire node pool if subnet is full; no new pods can start | `kubectl describe node <node>` shows IP allocation failure; `az network vnet subnet show --query 'ipConfigurations | length(@)'` at maximum | Add a new subnet CIDR block; provision a new node pool in the new subnet; drain old pool |
| Workload Identity token fetch failure (OIDC federation issue) | Pods using Workload Identity cannot authenticate to Azure services (Key Vault, Storage) → application errors cascade | All pods in namespace using the broken service account | Pod log: `AADSTS700016: Application not found in directory`; `kubectl get federatedidentitycredential` mismatch | Verify `federatedIdentityCredential` issuer matches cluster OIDC URL: `az aks show --query 'oidcIssuerProfile.issuerUrl'` |
| Node Not Ready due to disk pressure | Kubelet on pressured node evicts pods → pods rescheduled to other nodes → other nodes become overloaded → cascading evictions | All pods on affected node; potential cascade to neighboring nodes | `kubectl describe node <node>` shows `DiskPressure=True`; `kubectl get events | grep Evicted`; `df -h` on node > 85% | Cordon node: `kubectl cordon <node>`; clean up images: `crictl rmi --prune`; scale node pool to add fresh nodes |
| Kubernetes API server ARM rate throttling | kubectl operations return `429 Too Many Requests` → CI/CD pipelines stall → autoscaler cannot reconcile → SLO degrades | All clients making ARM API calls (CI, monitoring, AKS itself) | `kubectl get events` shows `403/429` from API server; Azure Monitor: ARM throttle events | Stagger CI/CD pipelines; reduce monitoring poll frequency; distribute workloads across multiple subscriptions |
| Cluster upgrade failure mid-roll | Old nodes removed before new nodes fully Ready → pod disruption budget violations → applications lose capacity | Pods on upgraded nodes experience disruption; may violate PDB | `kubectl get nodes` shows mix of old/new versions; some nodes `NotReady`; `kubectl describe node <upgrading-node>` shows upgrade in progress | Stop upgrade: `az aks nodepool upgrade --pause`; drain healthy old nodes to new ones; investigate failing node logs |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| AKS cluster version upgrade | API resource versions deprecated → existing YAML with old apiVersion rejected → CI/CD pipelines fail | Immediate on first `kubectl apply` after upgrade | Check removed APIs: `kubectl convert --dry-run=client -f manifests/`; `kubectl api-versions` diff pre/post upgrade | Update manifests to new API versions before upgrading; use `pluto` or `kubent` to scan for deprecated APIs |
| Node pool OS disk SKU change | New nodes provisioned with different disk IOPS → stateful workloads see different I/O performance → latency SLOs breached | Minutes (on node replacement/scale-out) | Correlate I/O latency metrics with node provisioning timestamps in Azure Monitor | Revert node pool OS disk SKU via `az aks nodepool update`; validate with load test before change |
| Container registry (ACR) geo-replication removal | Image pulls from region fail → pods stuck in `ImagePullBackOff` → deployments cannot roll out | Immediate on ACR geo-rep removal | `kubectl get events | grep ImagePullBackOff`; `az acr replication list` | Re-add ACR geo-replication for affected region; use `imagePullPolicy: IfNotPresent` to reduce pull frequency |
| NSG rule change blocking kubelet to API server | Kubelets cannot heartbeat → nodes go `NotReady` → pods evicted → applications crash | 5–10 min (kubelet heartbeat timeout) | `kubectl describe node | grep -A10 Conditions` — HeartbeatTime stale; NSG flow logs show denied traffic on 443/10250 | Revert NSG rule; restore traffic to AKS control plane IPs (AzureKubernetesService service tag) |
| Helm chart values change (resource limits) | Pods OOMKilled or CPU throttled after limit change; rollout changes only new pod instances | On next pod restart or rolling update | Compare `kubectl describe pod <pod>` resources before/after Helm upgrade; correlate with Helm revision timestamp | `helm rollback <release> <previous-revision>` |
| Azure Policy assignment (deny effect) on AKS | Pod creation rejected: `admission webhook denied the request`; new deployments fail | Immediate on policy assignment | `kubectl get events | grep webhook`; `az policy assignment list --scope /subscriptions/<id>/resourceGroups/<rg>` | Exclude AKS namespace from policy scope; or set policy to `Audit` effect temporarily |
| Cluster autoscaler configuration change (min/max) | Autoscaler scales below minimum active pods → PDB violated → application unavailability | Minutes (CA reconcile cycle) | CA log: `Scale down: removing node despite PDB violation`; `kubectl get events | grep TerminationGracePeriod` | Revert CA min/max via `az aks update`; check PDB definitions |
| Kubernetes RBAC change removing service account permissions | Pods using affected service account receive `403 Forbidden`; operator/controller stops reconciling | Immediate on permission removal | `kubectl auth can-i <verb> <resource> --as=system:serviceaccount:<ns>:<sa>` returns `no` | Restore RBAC role binding; `kubectl create rolebinding` to re-grant permissions |
| etcd encryption-at-rest key rotation | Secrets temporarily unreadable if rotation incomplete; pods that restart during rotation fail to retrieve secrets | During rotation window | API server log: `failed to decrypt data key`; pods show `CreateContainerConfigError` | Complete the key rotation immediately; do not interrupt mid-rotation; restore from etcd backup if stuck |
| Azure Defender for Containers policy enforcement | New pods blocked by admission controller for policy violations (e.g., privileged containers) | Immediate for newly scheduled pods | `kubectl get events | grep DefenderForContainers`; Pod status: `FailedCreate` | Add exclusion for affected workloads in Defender policy; remediate the policy violation if legitimate |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| etcd data split (AKS control plane — Microsoft-managed) | `az aks show --query 'provisioningState'`; `kubectl get --raw /healthz` returns non-200 | API server serves stale reads; writes fail | Cluster unable to schedule or reconcile resources | AKS control plane is fully managed — file P1 support; stop all cluster mutations until MS resolves |
| ConfigMap/Secret version skew between nodes | `kubectl get configmap <cm> -o yaml --show-managed-fields | grep resourceVersion` — compare values seen by pods on different nodes | Different pods on different nodes see different config versions; behavior diverges | Non-deterministic application behavior; hard-to-reproduce bugs | Use `Deployment` rolling update to ensure all pods restart with new config; avoid config hot-reload race conditions |
| Persistent volume claims stuck in `Terminating` | `kubectl get pvc -A | grep Terminating`; `kubectl describe pvc <pvc>` shows `finalizer: kubernetes.io/pvc-protection` | New pods cannot mount same storage; old pods hold volume | Data access blocked for new replicas | Remove finalizer manually: `kubectl patch pvc <pvc> -p '{"metadata":{"finalizers":null}}'` after confirming no pod uses it |
| Azure Disk attachment divergence (disk attached to wrong node) | `kubectl describe pv <pv>` shows `nodeAffinity` pointing to terminated node; `az disk show --query 'diskState'` shows `Attached` to old node | Pod stuck in `ContainerCreating`; volume cannot be mounted | Stateful workload unavailable | Force-detach disk: `az disk update --name <disk> --resource-group <rg> --disk-state Unattached`; then delete and re-create PVC |
| Namespace stuck in `Terminating` | `kubectl get ns <ns> -o json | jq '.spec.finalizers'`; `kubectl api-resources --verbs=list --namespaced -o name | xargs -I{} kubectl get {} -n <ns>` | Namespace resources orphaned; new resources cannot be created in same namespace name | Namespace unavailable; potential resource leak | Patch namespace to remove finalizers: `kubectl get ns <ns> -o json | jq '.spec.finalizers=[]' | kubectl replace --raw /api/v1/namespaces/<ns>/finalize -f -` |
| Rolling update version skew between pods | `kubectl get pods -l app=<app> -o jsonpath='{range .items[*]}{.spec.containers[0].image}{"\n"}{end}'` shows mixed versions during rollout | Old and new pod versions running simultaneously; API incompatibility causes 500s | Service degradation during rollout | Pause rollout: `kubectl rollout pause deployment/<deploy>`; investigate compatibility; resume or roll back |
| RBAC policy cache stale after role update | `kubectl auth can-i <verb> <resource> --as=<user>` returns old result immediately after role change | Newly granted permissions not effective; newly revoked permissions still work for cached credentials | Security policy bypass or legitimate user lockout | Force RBAC cache refresh: restart kube-apiserver pods (for AKS, done via control plane update); verify with `kubectl auth can-i` |
| Node labels/taints drifted from expected values | `kubectl get nodes --show-labels | grep -v <expected-label>`; `kubectl describe node | grep Taints` | Pods scheduled on wrong nodes; workload isolation broken; data-plane affinity rules violated | Security or compliance violations; performance degradation | Re-apply labels/taints: `kubectl label node <node> <key>=<value> --overwrite`; enforce via Azure Policy |
| Ingress TLS cert version mismatch (cert-manager rotation) | `kubectl describe certificate <cert> -n <ns>` shows `Ready=False` after rotation; old secret still mounted | Some pods use old cert, others use new cert during rotation | TLS handshake failures from clients expecting new cert | Force pod restart to pick up new secret: `kubectl rollout restart deployment/<deploy>`; verify with `openssl s_client -connect <host>:443` |
| StorageClass default annotation drift | Multiple StorageClasses marked as default; PVCs provisioned with wrong class | `kubectl get sc | grep default` shows two defaults; PVCs randomly bound to wrong class | Wrong storage tier (e.g., HDD vs SSD) used for performance-sensitive workloads | Remove default annotation from wrong class: `kubectl patch sc <wrong-sc> -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}'` |

## Runbook Decision Trees

### Decision Tree 1: Nodes Entering NotReady State

```
Are one or more nodes in NotReady status?
(check: kubectl get nodes --no-headers | grep -v ' Ready')
├── YES → Is the node NotReady for > 3 minutes?
│         ├── YES — single node → Is the Azure VM running?
│         │   (check: az vm get-instance-view --resource-group MC_<rg>_<cluster>_<location> --name <vm-name> --query instanceView.statuses)
│         │   ├── VM stopped or deallocated → Root cause: VMSS instance failed; Azure host issue
│         │   │   Fix: az vmss restart --resource-group MC_<rg>_<cluster>_<location> --name <vmss-name> --instance-ids <id>
│         │   │   If Azure host fault: let CA provision replacement node; cordon and drain failed node:
│         │   │   kubectl cordon <node> && kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
│         │   └── VM running but node NotReady → Root cause: kubelet crash or disk pressure
│         │         Fix: SSH to node; check kubelet: systemctl status kubelet; journalctl -u kubelet -n 100
│         │         If disk full: kubectl describe node <node> | grep -A5 DiskPressure; clean up logs/images
│         ├── YES — multiple nodes → Are all nodes in same node pool / VMSS?
│         │   ├── YES → Root cause: VMSS-level failure or Azure maintenance event
│         │   │   Check: az maintenance event list --resource-group MC_<rg>_<cluster>_<location>
│         │   │   Fix: Check Azure Service Health dashboard; contact Azure support; trigger node pool upgrade to force VM replacement
│         │   └── NO (spread across pools) → Root cause: AKS control plane or network issue
│         │         Check: az aks show --resource-group <rg> --name <cluster> --query provisioningState
│         │         Fix: Check AKS control plane status; validate CNI: kubectl get pods -n kube-system -l k8s-app=azure-cni-networkmonitor
│         └── NO — flapping in/out of Ready → Root cause: Intermittent network or resource pressure
│               Fix: Check node conditions: kubectl describe node <node> | grep -A10 Conditions
│               If MemoryPressure: evict large pods; increase node memory or change VM SKU
└── NO → All nodes Ready; check pod-level issues instead
```

### Decision Tree 2: Pods Stuck in Pending — Failed Scheduling

```
Are pods stuck in Pending state for > 2 minutes?
(check: kubectl get pods -A --field-selector=status.phase=Pending)
├── YES → What does the scheduling failure event say?
│         (check: kubectl describe pod <pod-name> -n <namespace> | grep -A5 Events)
│         ├── "Insufficient cpu/memory" → Root cause: No node has enough capacity
│         │   Check current node capacity: kubectl describe nodes | grep -A3 "Allocated resources"
│         │   Is cluster autoscaler enabled?
│         │   (check: kubectl -n kube-system logs -l app=cluster-autoscaler --tail=50 | grep -E 'scale up|no.node.group')
│         │   ├── CA enabled and working → Wait for scale-out; should complete in 3-5 min
│         │   │   If CA stuck: check VMSS quota: az vm list-usage --location <region> -o table
│         │   └── CA not working → Manually scale node pool:
│         │         az aks nodepool scale --resource-group <rg> --cluster-name <cluster> --name <pool> --node-count <n>
│         ├── "node(s) had taint that the pod didn't tolerate" → Root cause: Taint mismatch
│         │   Fix: Add toleration to pod spec; or remove taint: kubectl taint node <node> <key>-
│         ├── "pod has unbound immediate PersistentVolumeClaims" → Root cause: PVC not provisioned
│         │   Check: kubectl describe pvc <name> -n <ns>; verify StorageClass provisioner is running:
│         │   kubectl get pods -n kube-system | grep csi
│         │   Fix: Check Azure Disk CSI driver; verify disk quota in subscription
│         └── "0/N nodes are available: N node(s) didn't match node affinity" → Root cause: Node affinity too restrictive
│               Fix: Review pod nodeSelector/affinity; verify target node labels: kubectl get nodes --show-labels
└── NO → Verify: Are pods in CrashLoopBackOff or OOMKilled?
          kubectl get pods -A | grep -v 'Running\|Completed\|Pending'
          → If yes: inspect pod logs: kubectl logs <pod> -n <ns> --previous
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Cluster autoscaler over-provisioning — VMSS scale-out loop | Pending pods trigger repeated scale-out; nodes not utilized but CA keeps adding due to DaemonSet overhead | `kubectl -n kube-system logs -l app=cluster-autoscaler --tail=100 \| grep 'scale up'`; compare `kubectl top nodes` | Cloud compute bill spikes; idle VMs accruing hourly cost | Set `scale-down-utilization-threshold=0.5` and `scale-down-delay-after-add=10m` in CA config | Set `requests` on all pods (not just limits); CA uses requests to calculate fit |
| Premium SSD (P-series) PVCs created when Standard would suffice | Default StorageClass maps to `Premium_LRS`; all PVCs created as P30+ even for low-IOPS workloads | `kubectl get pvc -A -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.storageClassName}{"\n"}{end}'` | Excess storage cost for non-IO-intensive workloads | Migrate PVCs to `Standard_LRS` StorageClass where applicable | Create `Standard_LRS` StorageClass; set it as default for non-production namespaces |
| Azure Load Balancer per-service IP accumulation | Each `Service type=LoadBalancer` creates a new Azure Public IP and LB; hundreds of services in a large cluster | `kubectl get svc -A --field-selector=spec.type=LoadBalancer \| wc -l`; `az network public-ip list --resource-group MC_<rg>_<cluster>_<location> -o table` | Azure LB IP cost ($0.005/hr per IP); soft limit of 50 public IPs per region | Migrate to shared ingress controller (AGIC or nginx); delete unused LB services | Use `Ingress` resources backed by a single LB; reserve `LoadBalancer` type for truly external services only |
| ARM API quota exhaustion from concurrent AKS operations | Multiple pipelines calling `az aks` simultaneously; ARM returns `429 TooManyRequests`; AKS operations fail | `az monitor activity-log list --status Failed \| grep ThrottlingException`; Azure Monitor ARM throttle metrics | Node pool scale/upgrade/rolling deploys fail; CA unable to provision nodes | Serialize AKS operations; wait for `az aks show --query provisioningState` to return `Succeeded` before next call | Implement pipeline serialization with distributed lock; use separate Azure subscriptions for prod vs. CI |
| Orphaned Azure Disk PVs — PVs not released after PVC deletion | `reclaimPolicy: Retain` set on StorageClass; disks remain even after workload deleted | `kubectl get pv --field-selector=status.phase=Released -o wide`; `az disk list --resource-group MC_<rg>_<cluster>_<location> -o table` | Unused Azure Disk cost accumulating; could reach thousands of orphaned disks | Delete orphaned PVs: `kubectl delete pv <name>`; corresponding Azure disk auto-deleted if `reclaimPolicy=Delete` | Use `reclaimPolicy: Delete` for ephemeral workloads; audit PVs quarterly |
| Azure Container Registry (ACR) pull-through cache miss — egress cost | Pods pulling large images from public registries via ACR; egress charges accruing | `az acr show-usage --name <acr_name> -o table`; check ACR bandwidth metrics in Azure Monitor | Unexpected egress charges; also slows pod startup | Enable ACR cache rules for frequently pulled images; restrict pod image sources to ACR | Import all external images into ACR; enforce `imagePullPolicy: IfNotPresent`; use image caching Daemonsets |
| Node OS disk ephemeral storage fill — eviction cascade | High-log-volume pods filling node OS disk; kubelet triggers pod eviction; evicted pods rescheduled on same nodes | `kubectl describe nodes \| grep -A5 "Ephemeral Storage"`; `df -h` via node-shell | Eviction cascade across node pool; application instability | Enable ephemeral storage limits per container; set `resources.limits.ephemeral-storage` | Set `limits.ephemeral-storage` on all containers; use remote logging (Azure Monitor); rotate logs aggressively |
| Azure Monitor Container Insights — high data ingestion cost | All namespaces sending logs and metrics to Log Analytics workspace; high GB/day ingest | Azure Portal → Log Analytics workspace → Usage and estimated costs; `ContainerLog \| summarize count() by TimeGenerated` | Log Analytics ingestion bill can exceed compute cost on verbose clusters | Configure Container Insights ConfigMap to exclude high-volume namespaces from log collection | Set `log_collection_settings.stdout.exclude_namespaces` in Container Insights ConfigMap; filter noisy workloads |
| VMSS spot instance interruption leaving cluster under capacity | Spot node pool receives Azure eviction notices; nodes drain simultaneously; workloads lose capacity | CA logs: `scale-down` events; `kubectl get nodes \| grep spot`; Azure Spot eviction events in activity log | Sudden capacity loss; pods evicted and may not fit on remaining on-demand nodes | Ensure workloads have pod disruption budgets; maintain on-demand node pool as fallback | Use spot nodes only for batch/non-critical; maintain on-demand baseline; set `--spot-max-price` |
| Cluster upgrade VMSS surge creating extra nodes indefinitely | AKS upgrade uses surge nodes (`maxSurge`); if upgrade stalls, surge nodes remain and accrue cost | `az aks nodepool show --resource-group <rg> --cluster-name <cluster> --name <pool> --query upgradeSettings` | Extra VM cost for duration of stalled upgrade | Resume or abort stalled upgrade: `az aks upgrade --resource-group <rg> --name <cluster> --kubernetes-version <ver>` | Set `maxSurge=1` for large node pools; monitor upgrade status and alert if `provisioningState=Upgrading` > 1h |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot node — pod scheduling imbalance | One node CPU/memory near limit while others idle; pods evicted from hot node | `kubectl top nodes`; `kubectl describe nodes | grep -A5 'Allocated resources'`; `kubectl get pods -A -o wide | awk '{print $8}' | sort | uniq -c | sort -rn` | Affinity rules, DaemonSet overhead, or CA provisioning bias; missing `topologySpreadConstraints` | Add `topologySpreadConstraints` on deployments; use `podAntiAffinity` to spread across nodes |
| Azure LB connection pool exhaustion — SNAT port exhaustion | Intermittent connection failures from pods to external endpoints; `SNAT port exhaustion` in Azure Monitor | `az network nic show --resource-group MC_<rg>_<cluster>_<loc> --name <nic> --query 'ipConfigurations[].`; Azure Monitor: `SnatConnectionCount` metric on Load Balancer | Default SNAT port allocation (1024 per backend IP) too low for outbound connection rate | Enable `outboundType: managedNATGateway`; or configure explicit outbound rules with higher SNAT port allocation |
| JVM GC pressure in Java workloads causing OOMKilled pods | Java pods repeatedly OOMKilled; `kubectl describe pod` shows `OOMKilled` as last state | `kubectl get pods -A | grep -i oomkilled`; `kubectl describe pod <pod> -n <ns> | grep -A5 'Last State'` | JVM heap exceeds container memory limit; GC overhead makes container appear stuck | Set JVM `-XX:MaxRAMPercentage=75.0` to respect container limits; increase pod `resources.limits.memory` |
| CoreDNS thread pool saturation | DNS resolution latency > 5s cluster-wide; pods unable to reach services by name | `kubectl -n kube-system top pods -l k8s-app=kube-dns`; `kubectl -n kube-system logs -l k8s-app=kube-dns | grep latency`; `kubectl run -it --rm dnstest --image=busybox -- nslookup kubernetes.default` | CoreDNS overloaded; `ndots:5` causing 5 DNS queries per lookup; no node-local DNS cache | Deploy `node-local-dns` DaemonSet; reduce `ndots` to 2 in pod DNS config; scale CoreDNS replicas |
| Azure Disk I/O throttling — PersistentVolume I/O saturation | StatefulSet pods show slow writes; `iostat` on node shows disk at IOPS limit | `kubectl exec <pod> -- iostat -x 1 5`; Azure Monitor: `Disk Read/Write Operations/Sec` for the VM | Azure Disk per-VM IOPS quota reached; burstable disk exhausted credit | Upgrade disk SKU to Premium SSD v2 or Ultra Disk; switch to Azure Files for shared workloads; cache with `ReadWriteOnce + cachingMode: ReadOnly` |
| CPU steal from Azure VM bursting exhausted credits | Pod CPU throttle visible; `container_cpu_throttled_seconds_total` rising; workload performance degrades | `kubectl top pods -n <ns>`; Azure Monitor: `Percentage CPU Credits Consumed` for VMs in node pool | Burstable VM (B-series) exhausted CPU credit; CPU throttled to baseline | Scale out to additional nodes; switch node pool to non-burstable SKU (D-series); use HPA to distribute load |
| etcd lock contention from too many Kubernetes API calls | `kubectl` commands slow; API server latency > 200ms; events backlogged | `kubectl get --raw /metrics | grep apiserver_request_duration`; `az aks show --resource-group <rg> --name <cluster> --query 'addonProfiles'` | CI/CD or operators making high API call rate; watch connection explosion from too many controllers | Implement rate limiting on CI/CD kubectl calls; audit controllers for watch leak; use `kubectl --cache-dir` |
| Serialization overhead in API server from large CRD objects | CRD-based operators slow; large `spec` fields in CRs cause long serialization | `kubectl get --raw /metrics | grep etcd_request_duration`; `kubectl get <crd-resource> -o json | jq 'length'` | CRD objects storing large blobs (> 1MB) in etcd; etcd serialization overhead | Store large data in ConfigMaps or external stores (Azure Blob); keep CRD spec minimal |
| Node pool scaling lag — CA delayed provisioning | Pending pods wait > 5 minutes for new nodes; CA log shows throttle | `kubectl -n kube-system logs -l app=cluster-autoscaler | grep -E 'scale-up|waiting'`; `kubectl get pods -A --field-selector=status.phase=Pending` | ARM API throttling; CA `scan-interval` too long; node pool provisioning latency | Pre-warm a minimum node count (`minCount`); increase CA `scale-down-delay-after-add`; use multiple smaller node pools |
| Downstream Azure service latency — Key Vault or Service Bus adds request overhead | Pod response time increases proportional to Azure dependency calls; no internal bottleneck | `kubectl exec <pod> -- curl -w '%{time_total}\n' -o /dev/null -s https://<keyvault>.vault.azure.net/`; pod APM traces | Azure regional service latency; missing connection pooling or caching in workload | Cache Key Vault secrets with CSI driver; use connection pooling for Service Bus; implement circuit breaker |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on Ingress | Browser shows `ERR_CERT_EXPIRED`; external traffic returns 525; `kubectl describe ingress` shows cert secret name | `kubectl get secret <tls-secret> -n <ns> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -dates`; check cert-manager: `kubectl get certificate -A` | Expired TLS secret on Ingress; cert-manager renewal failed | Manually renew: `kubectl cert-manager renew <cert> -n <ns>`; or re-issue with `kubectl delete certificate`; restore from ACR/Azure Key Vault |
| mTLS rotation failure in service mesh (OSM/Istio) | Services return 503; mesh sidecar logs show `certificate has expired`; traffic between pods fails | `kubectl get pods -n kube-system -l app=osm-controller`; `osm mesh get meshconfig -n kube-system`; `istioctl proxy-config secret <pod>` | Service mesh CA failed to rotate workload certificates; pods running with expired mTLS certs | Force certificate rotation: `osm control plane cert rotate`; or rollout restart affected deployments |
| DNS resolution failure — CoreDNS pod CrashLoopBackOff | All pod-to-service DNS fails; application logs show `dial tcp: lookup <service>: no such host` | `kubectl get pods -n kube-system -l k8s-app=kube-dns`; `kubectl -n kube-system logs -l k8s-app=kube-dns --tail=50`; `kubectl run -it --rm test --image=busybox -- nslookup kubernetes.default` | CoreDNS crash; ConfigMap misconfiguration; upstream DNS unreachable | Restart CoreDNS: `kubectl rollout restart deploy/coredns -n kube-system`; validate ConfigMap: `kubectl get cm coredns -n kube-system -o yaml` |
| TCP connection exhaustion — node conntrack table full | Intermittent connection failures; kernel log: `nf_conntrack: table full, dropping packet` | `kubectl debug node/<node> -it --image=alpine -- chroot /host sysctl net.netfilter.nf_conntrack_count net.netfilter.nf_conntrack_max`; `dmesg | grep conntrack` | High connection rate; `nf_conntrack_max` too low for node's workload | Increase: `sysctl -w net.netfilter.nf_conntrack_max=524288` on affected nodes; use node-level `DaemonSet` to apply sysctl |
| Azure Load Balancer health probe misconfiguration | External traffic fails; pods healthy but LB health check returns unhealthy | `kubectl describe svc <service> -n <ns>`; `az network lb probe list --resource-group MC_<rg>_<cluster>_<loc> --lb-name <lb>` | LB health probe port/path mismatch with actual pod readiness endpoint | Update Service `spec.ports` to match pod health endpoint; verify `readinessProbe` on pods returns 200 |
| Packet loss on inter-node CNI overlay | Pod-to-pod latency spikes; `kubectl exec <pod> -- ping <pod-ip>` shows packet loss; cross-node traffic affected | `kubectl exec <pod> -- ping -c 100 -i 0.1 <remote-pod-ip> | tail -3`; `kubectl debug node/<node> -it --image=nicolaka/netshoot -- tcpdump -i eth0 -c 50 -w /tmp/cap.pcap` | Azure CNI plugin issue; MTU misconfiguration on overlay; VM NIC failure | Restart affected node's Azure CNI: `kubectl cordon <node>`; drain and reimage if hardware-level issue |
| MTU mismatch with VPN/ExpressRoute causing fragmentation | Large payloads (> 1400 bytes) between cluster and on-prem fail silently; small requests work | `kubectl exec <pod> -- ping -M do -s 1420 <on-prem-ip>`; `kubectl debug node/<node> -it --image=alpine -- ip link show eth0` | VPN/ExpressRoute MTU lower than Azure VNet default; CNI not setting `mtu` consistently | Set CNI MTU to match VPN MTU: update `azure-cni` ConfigMap; set `--network-plugin-mtu` in AKS config |
| NSG / firewall rule change blocking pod egress | Pods suddenly cannot reach external endpoints; no code change; Azure Activity Log shows NSG modification | `az network nsg rule list --resource-group MC_<rg>_<cluster>_<loc> --nsg-name <nsg-name>`; `kubectl exec <pod> -- curl -v https://api.example.com` | New NSG rule blocking port; Azure Policy enforcement change | Identify blocking rule in NSG; add allow rule or revert: `az network nsg rule create ...`; check Azure Policy assignments |
| Kubernetes API server TLS handshake timeout | `kubectl` commands hang; `Unable to connect to the server: net/http: TLS handshake timeout` | `curl -k --connect-timeout 5 https://<cluster-fqdn>:443/healthz`; `az aks show --resource-group <rg> --name <cluster> --query 'provisioningState'` | API server overloaded; kube-apiserver pod resource-constrained; network interruption | Wait for API server recovery; check AKS provisioning state: `az aks show`; scale down noisy controllers |
| Connection reset by Azure LB idle timeout | Long-running WebSocket or keep-alive connections reset after 4 minutes of inactivity | `kubectl get svc <service> -n <ns> -o yaml | grep loadBalancerIP`; capture resets: `tcpdump -i eth0 port <svc-port> -w /tmp/reset.pcap` on pod | Azure LB default idle timeout is 4 minutes; TCP keepalive not enabled in application | Set LB idle timeout annotation: `service.beta.kubernetes.io/azure-load-balancer-tcp-idle-timeout: "30"`; enable TCP keepalive in app |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOMKilled pod — container memory limit exceeded | Pod restarts with `OOMKilled`; `kubectl describe pod` shows `Exit Code: 137` | `kubectl get pods -A | grep OOMKilled`; `kubectl describe pod <pod> -n <ns> | grep -A10 'Last State'`; `kubectl top pod <pod> -n <ns>` | Increase `resources.limits.memory`; tune application heap/cache sizes; rollout restart | Set memory `requests` == `limits` for production workloads; use VPA to right-size; monitor `container_memory_working_set_bytes` |
| Azure Disk PV full — StatefulSet pod crashes | StatefulSet pod enters `CrashLoopBackOff`; application logs show `No space left on device` | `kubectl exec <pod> -n <ns> -- df -h`; `kubectl get pvc -n <ns>`; `kubectl describe pvc <pvc>` | Expand PVC: `kubectl patch pvc <pvc> -n <ns> -p '{"spec":{"resources":{"requests":{"storage":"50Gi"}}}}'` | Set PVC storage with 30% headroom; monitor `kubelet_volume_stats_available_bytes`; enable automatic PVC expansion in StorageClass |
| Node log partition full — kubelet degraded | Node enters `NotReady`; `kubectl describe node` shows `DiskPressure`; container logs not collecting | `kubectl debug node/<node> -it --image=alpine -- chroot /host df -h /var/log`; `kubectl get node <node> -o jsonpath='{.status.conditions}'` | Drain node: `kubectl drain <node> --ignore-daemonsets`; SSH and clear stale logs; reimage node | Enable `logrotate` for container logs; set `containerLogMaxSize: 50Mi` and `containerLogMaxFiles: 5` in kubelet config |
| File descriptor exhaustion in pod | Application throws `too many open files`; pods not restarting but requests failing | `kubectl exec <pod> -- cat /proc/1/limits | grep 'open files'`; `kubectl exec <pod> -- ls /proc/1/fd | wc -l` | Set `ulimit` via pod `securityContext`: `securityContext.sysctls`; or increase via `LimitRange` in namespace | Add `resources` and FD limits to pod spec; use `LimitRange` to enforce defaults in namespace |
| Inode exhaustion on node ephemeral storage | New pods cannot start on node; `kubectl describe node` shows `DiskPressure`; existing pods unaffected | `kubectl debug node/<node> -it --image=alpine -- chroot /host df -i /var/lib/docker`; `kubectl debug node/<node> -it --image=alpine -- chroot /host find /var/log/pods -maxdepth 3 | wc -l` | Delete terminated pod log directories on node; or cordon and drain node | Enable kubelet `imageGCHighThresholdPercent`; set `containerLogMaxFiles`; clean up completed pod log dirs via DaemonSet |
| CPU throttling — requests set too low | Pod running but p99 latency elevated; `container_cpu_throttled_seconds_total` rising | `kubectl top pods -n <ns>`; Prometheus: `rate(container_cpu_throttled_seconds_total{namespace="<ns>"}[5m])`; `kubectl describe pod <pod> | grep -A3 Limits` | CPU `limits` too low relative to actual usage; HPA cannot scale fast enough | Increase CPU limits; use VPA in recommendation mode; set `requests` = 50% of peak, `limits` = 100% of peak |
| Swap / memory pressure — node evicting pods | Multiple pods evicted across a node; `kubectl describe node` shows `MemoryPressure` | `kubectl describe node <node> | grep -A5 'MemoryPressure'`; `kubectl get events -A | grep Evict`; `kubectl debug node/<node> -it --image=alpine -- chroot /host free -h` | Cordon node: `kubectl cordon <node>`; drain to other nodes; investigate memory-hungry pods | Set memory `requests` accurately for all pods; configure node `--eviction-hard` thresholds; scale out node pool |
| Kubernetes API server etcd storage approaching quota | `kubectl get nodes` returns `etcdserver: mvcc: database space exceeded`; cluster operations fail | `kubectl get --raw /metrics | grep etcd_db_total_size`; `az aks show --resource-group <rg> --name <cluster> --query 'storageProfile'` | etcd database growing from accumulated events, Helm releases, or large CRD objects | Compact etcd: AKS manages this automatically; purge old Helm releases: `helm ls -A | grep superseded`; clean up old events |
| Azure VMSS ephemeral OS disk full on node | Node enters `NotReady`; kubelet cannot write; `df -h` on node shows OS disk full | `kubectl debug node/<node> -it --image=alpine -- chroot /host df -h /`; check node `DiskPressure` condition | Add temporary disk space; cordon and reimage the node: `az vmss reimage --resource-group MC_<rg>_<cluster>_<loc> --name <vmss> --instance-ids <id>` | Use ephemeral OS disk with appropriate size; enable kubelet garbage collection; stream logs to Azure Monitor |
| Ephemeral port exhaustion on node — SNAT port exhaustion | Pods cannot make outbound connections; errors: `connect: cannot assign requested address` | `kubectl debug node/<node> -it --image=alpine -- chroot /host ss -s | grep TIME-WAIT`; Azure Monitor LB `SNAT Connection Count` metric | High outbound connection churn; Azure LB SNAT port allocation exhausted | Enable Azure NAT Gateway: `az aks update --outbound-type managedNATGateway`; or pre-allocate SNAT ports on LB |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation from pod restart mid-transaction | Pod OOMKilled mid-write to external DB; Kubernetes restarts pod; operation replayed without idempotency check | `kubectl get pods -n <ns> | grep Restart`; check application logs for duplicate operation IDs; `kubectl describe pod <pod> | grep 'Restart Count'` | Duplicate records in downstream DB; double charges; duplicate API side effects | Add idempotency key in external DB write; use Kubernetes `Job` with `completionMode: Indexed` for exactly-once semantics |
| Helm upgrade partial rollout — some pods on v2, some on v1 | Rolling update in progress; two versions of application running simultaneously; incompatible schema changes cause errors | `kubectl rollout status deploy/<app> -n <ns>`; `kubectl get pods -n <ns> -o jsonpath='{.items[*].spec.containers[0].image}'` | Mixed-version traffic; requests to v1 pods fail on new schema; inconsistent responses | Pause rollout: `kubectl rollout pause deploy/<app>`; roll back: `kubectl rollout undo deploy/<app>`; use blue-green instead |
| ConfigMap or Secret hot-reload race — pod reads stale config during rotation | Mounted ConfigMap updated via `kubectl apply`; some pods re-read mid-request; others still serving old config | `kubectl describe configmap <cm> -n <ns>`; check pod annotations for `checksum/config`; `kubectl exec <pod> -- cat /etc/config/<key>` | Request handling inconsistent between pods; auth failures for pods with stale secret; partial feature rollout | Trigger rolling restart after ConfigMap change: `kubectl rollout restart deploy/<app> -n <ns>` |
| Cross-namespace service call ordering failure — dependency deployed before dependency is ready | Dependent service starts making calls before upstream is Ready; requests fail during startup window | `kubectl get pods -A --sort-by='.status.startTime'`; check `readinessProbe` on upstream: `kubectl describe deploy <upstream> -n <ns>` | Startup errors; crash loops in dependent service; lost requests during initial deployment | Add `initContainers` to wait for upstream: `until kubectl get endpoints <svc> -n <ns>; do sleep 2; done`; use proper `readinessProbe` on all services |
| Out-of-order event processing from Kafka consumer pod scaling | HPA scales Kafka consumer deployment; partition rebalance causes some messages processed twice and some out of order | `kubectl get hpa -n <ns>`; check consumer group lag: `kafka-consumer-groups.sh --bootstrap-server <broker> --group <group> --describe`; application logs for rebalance events | Duplicate or out-of-order event processing; downstream state incorrect | Set `min.insync.replicas` and use static partition assignment; disable HPA for Kafka consumers; use StatefulSet instead |
| At-least-once Kubernetes Job completion — Job retries process message multiple times | Kubernetes `Job` fails and retries; each retry re-processes same work item; no idempotency in task logic | `kubectl get jobs -n <ns>`; `kubectl describe job <job> -n <ns> | grep -E 'Completions|Succeeded|Failed'`; check application logs for duplicate processing | Data written multiple times; incorrect aggregation results | Add idempotency check at task start; use `ttlSecondsAfterFinished` to clean up; implement output-exists guard before writing |
| Compensating rollback failure — Argo Rollout canary left in partial state | Canary rollout analysis fails; automated rollback starts but is interrupted (node failure, operator crash) | `kubectl argo rollouts get rollout <app> -n <ns>`; `kubectl argo rollouts list rollouts -n <ns>`; `kubectl get rollout <app> -n <ns> -o yaml | grep phase` | Canary and stable versions both partially live; traffic split stuck; service degraded | Force complete rollback: `kubectl argo rollouts abort <app> -n <ns>` then `kubectl argo rollouts undo <app> -n <ns>` |
| Distributed lock expiry mid-Kubernetes operator reconcile — two operator instances race | Two operator replicas both believe they are leader; both reconcile same CR simultaneously | `kubectl get lease -n <operator-ns>`; `kubectl logs -l app=<operator> -n <operator-ns> | grep -E 'leader|acquired|lost'` | CRD resources reconciled twice; conflicting updates; external system state corrupted | Ensure operator `leaderElection: true` with correct RBAC for `leases`; check lease TTL vs reconcile duration; delete stale lease to force re-election |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor — one namespace's pods consuming all CPU on shared node | `kubectl top nodes`; `kubectl top pods -A --sort-by=cpu | head -20`; `kubectl describe node <node> | grep -A10 'Allocated resources'` | Adjacent namespace pods CPU-throttled; P99 latency increases | Evict noisy pod: `kubectl drain <node> --ignore-daemonsets --pod-selector=<label>` or cordon node | Enforce CPU `limits` on all pods; add `topologySpreadConstraints` to spread across nodes; use dedicated node pools per tenant namespace |
| Memory pressure — one namespace triggering node-level pod eviction | `kubectl get events -A | grep Evict`; `kubectl describe node <node> | grep MemoryPressure` | Other namespace pods evicted; StatefulSets disrupted | Cordon node to stop new scheduling: `kubectl cordon <node>`; identify memory-hungry pods: `kubectl top pods -A --sort-by=memory | head -10` | Set memory `requests == limits` for Guaranteed QoS; configure per-namespace `LimitRange`; separate high-memory workloads to dedicated node pool |
| Disk I/O saturation — one team's StatefulSet writing at full SSD IOPS | `kubectl exec <pod> -- iostat -x 1 5`; Azure Monitor VM disk IOPS metric for node | Adjacent pods with PVCs on same underlying disk experience slow writes | Move I/O-intensive pod to dedicated node: `kubectl label node <node> diskio=heavy` + `nodeAffinity` | Use separate Azure Disk PVCs with `Premium_LRS` per tenant; configure `storageClass` with IOPS limits using Ultra Disk or Premium SSD v2 |
| Network bandwidth monopoly — one team's data pipeline pods saturating node NIC | `kubectl debug node/<node> -it --image=alpine -- chroot /host sar -n DEV 1 10 | grep eth0`; Azure Monitor NIC throughput metric | Other pods on node experience packet loss and elevated latency | Cordon node: `kubectl cordon <node>`; reschedule bandwidth-heavy pods to isolated node pool | Apply bandwidth annotations via Azure CNI; use dedicated node pool for bulk data transfer workloads |
| Connection pool starvation — one team's service exhausting cluster-wide DNS | `kubectl -n kube-system top pods -l k8s-app=kube-dns`; `kubectl run -it --rm dnstest --image=busybox -- nslookup kubernetes.default` for latency | All pods cluster-wide experience DNS resolution slowness; service discovery fails | Identify DNS-heavy namespace: `kubectl logs -n kube-system -l k8s-app=kube-dns | grep -c NXDOMAIN` | Deploy `node-local-dns` DaemonSet; reduce `ndots` in misbehaving namespace: `dnsConfig.options: [{name: ndots, value: "2"}]` |
| Quota enforcement gap — one namespace consuming all cluster ResourceQuota | `kubectl describe resourcequota -A`; `kubectl get pods -A --field-selector=status.phase=Running | wc -l` | Other namespaces cannot schedule new pods despite available cluster capacity | Reduce offending namespace quota temporarily: `kubectl patch resourcequota <quota> -n <ns> -p '{"spec":{"hard":{"cpu":"10"}}}'` | Enforce `ResourceQuota` on every namespace; configure `LimitRange` defaults; alert when namespace utilization > 80% of quota |
| Cross-tenant data leak risk — shared PersistentVolume between namespaces | `kubectl get pv -o json | jq '.items[] | select(.spec.claimRef) | {pv: .metadata.name, ns: .spec.claimRef.namespace}'` | PV with `ReadWriteMany` accessible from multiple tenant namespaces | Revoke access: change PV `claimRef` to single namespace; apply NetworkPolicy to storage nodes | Use `ReadWriteOnce` PVs per tenant; enforce namespace isolation via RBAC on PVC creation; audit PV access modes |
| Rate limit bypass — one team's CI/CD kubectl calls exhausting API server rate limit | `kubectl get --raw /metrics | grep apiserver_request_total`; Azure Monitor API server latency metric | Other teams' `kubectl` commands slow; cluster operations delayed | Identify heavy API callers via audit logs: `az monitor log-analytics query ... | where Category=="kube-audit" | summarize count() by user_s` | Configure `--max-requests-inflight` on API server; enforce per-user rate limits via Azure AD app throttling; cache kubectl results in CI/CD |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Prometheus scrape failure — metrics-server or custom metrics unavailable | `kubectl top nodes` returns `error from server`; HPA unable to scale; dashboards dark | `metrics-server` pod crashed; Prometheus scrape config missing new namespace | `kubectl get pods -n kube-system -l k8s-app=metrics-server`; `kubectl top nodes` fallback to Azure Monitor: `az monitor metrics list --resource <cluster-id>` | Restart metrics-server: `kubectl rollout restart deploy/metrics-server -n kube-system`; verify Prometheus scrape config includes new namespaces |
| Trace sampling gap — slow pod startup not captured | P99 pod startup latency incidents invisible; deployment incidents missed | Kubernetes slow-start events not propagated to APM; default sampling too low | `kubectl get events -A --sort-by='.lastTimestamp' | grep -i 'slow\|timeout\|backoff'`; `kubectl describe pod <pod>` | Enable Kubernetes OpenTelemetry operator; instrument pod start events; increase sampling for deployment traces |
| Log pipeline silent drop — container logs not reaching Loki/ELK | Pod error logs invisible in Grafana Loki; alerts not firing | Fluent Bit DaemonSet pod crashed on noisy node; Azure Monitor agent OOMKilled | `kubectl get pods -n kube-system -l app=fluent-bit`; fallback: `kubectl logs <pod> -n <ns> --previous` | Restart log collector DaemonSet: `kubectl rollout restart daemonset/fluent-bit -n kube-system`; increase DaemonSet memory limits |
| Alert rule misconfiguration — `KubeNodeNotReady` alert never fires | Nodes go NotReady silently; no on-call page | Alert uses wrong `condition` label value; kube-state-metrics scrape failing | `kubectl get nodes`; `curl -G 'http://prometheus:9090/api/v1/query' --data-urlencode 'query=kube_node_status_condition{condition="Ready",status="false"}'` | Verify kube-state-metrics is running: `kubectl get pods -n kube-system -l app.kubernetes.io/name=kube-state-metrics`; fix alert label matchers |
| Cardinality explosion — per-pod labels creating millions of time series | Grafana dashboards timeout; Prometheus out of memory; scrape takes > 30s | Application exposing `pod_name` or `request_id` as Prometheus label | `kubectl exec -n monitoring prometheus-0 -- promtool query series '{__name__=~".+"}' | wc -l`; identify offenders: `topk(10, count by (__name__, pod)({pod=~".+"}))` | Remove high-cardinality labels via Prometheus `metric_relabel_configs`; enforce label cardinality policy via admission webhook |
| Missing health endpoint — AKS node health not surfaced to application team | Node DiskPressure or MemoryPressure unknown until pods evicted | Node conditions not mapped to application-visible alert; only infra team monitors nodes | `kubectl get nodes -o custom-columns=NAME:.metadata.name,STATUS:.status.conditions[-1].type,REASON:.status.conditions[-1].reason` | Add node condition alerts: `kube_node_status_condition{condition=~"DiskPressure|MemoryPressure",status="true"}`; configure Application Insights availability tests |
| Instrumentation gap — AKS cluster autoscaler scaling decisions not logged to APM | Pods pending for 10 minutes with no observable reason in dashboards | CA logs not forwarded to log aggregation; no alert on pending pods | `kubectl -n kube-system logs -l app=cluster-autoscaler | tail -100`; `kubectl get pods -A --field-selector=status.phase=Pending` | Forward CA logs to Azure Monitor Logs; add alert: `kube_pod_status_phase{phase="Pending"} > 0 for 5m` |
| Alertmanager/PagerDuty outage silencing AKS cluster alerts | Node down, pods crashing — no pages | Alertmanager StatefulSet evicted due to disk pressure; PagerDuty integration key rotated | `kubectl get pods -n monitoring -l app.kubernetes.io/name=alertmanager`; fallback: `kubectl get nodes` | Implement dead-man's switch: `absent(up{job="kube-state-metrics"}) for 2m` with Azure Monitor alert as independent fallback |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| AKS minor version upgrade (e.g., 1.28 → 1.29) | Node pool upgrade fails; nodes stuck in `Upgrading` state; workloads disrupted on upgraded nodes | `az aks nodepool list -g <rg> --cluster-name <cluster> --query '[].{name:name,state:provisioningState,version:orchestratorVersion}'`; `kubectl get nodes` | Stop upgrade: `az aks nodepool upgrade --no-wait` cannot be cancelled; cordon failed nodes; contact Azure Support for stuck upgrades | Test upgrade on staging cluster first; check deprecation: `kubectl deprecations`; ensure PodDisruptionBudgets are set; use `--max-surge 1` |
| AKS control plane upgrade version skew — control plane upgraded before node pools | Kubelet version on nodes incompatible with new API server; `kubectl` operations intermittently fail | `az aks show -g <rg> -n <cluster> --query 'kubernetesVersion'`; `kubectl get nodes -o custom-columns=NAME:.metadata.name,KUBELET:.status.nodeInfo.kubeletVersion` | Upgrade node pools to match control plane: `az aks nodepool upgrade -g <rg> --cluster-name <cluster> --name <pool> --kubernetes-version <version>` | Upgrade control plane and node pools in same window; control plane supports n-2 kubelet version skew |
| Schema migration partial completion — CRD version upgrade | CRDs updated to v1beta2 while operators still use v1beta1; operator crashes; CRs become unmanaged | `kubectl get crd <crd> -o jsonpath='{.spec.versions[*].name}'`; `kubectl get <cr-kind> -A` | Downgrade CRD: restore previous CRD manifest: `kubectl apply -f <previous-crd>.yaml`; roll back operator deployment | Test CRD version migration in staging; ensure backward compatibility via `served: true` on old version |
| Rolling upgrade version skew — Helm chart upgrade partially applied | Some pods on new chart version, some on old; incompatible schema changes cause inter-pod errors | `kubectl rollout status deploy/<app> -n <ns>`; `kubectl get pods -n <ns> -o jsonpath='{.items[*].spec.containers[0].image}'` | Roll back Helm release: `helm rollback <release> <revision> -n <ns>`; verify: `helm history <release> -n <ns>` | Use blue-green Helm upgrades; set `maxUnavailable: 0` in deployment rolling update strategy; test Helm chart in staging |
| Zero-downtime migration gone wrong — Kubernetes-to-Kubernetes namespace migration | Pods running in both old and new namespace; duplicate processing; data written twice | `kubectl get pods -A | grep <app-name>`; `kubectl get svc -A | grep <svc-name>` | Delete pods from old namespace; update DNS/service discovery to new namespace; clean up old resources | Use `kubectl cp` to migrate state; validate new namespace before deleting old; use ArgoCD sync waves for ordered migration |
| Config format change — deprecated API version (`apps/v1beta1`) removed in upgrade | Helm or kubectl apply fails with `no matches for kind`; workloads not deployed after upgrade | `kubectl api-versions | grep apps`; `helm template <release> | grep apiVersion` | Downgrade AKS version (not recommended); update manifests to `apps/v1`; apply updated Helm chart | Run `kubectl deprecations` before every upgrade; update all manifests to stable API versions; enforce in CI/CD with `kubeval` |
| Data format incompatibility — etcd version upgrade changing serialization | Objects stored with old etcd encoding unreadable after upgrade; `kubectl get` returns decode errors | `kubectl get --raw /apis/apps/v1/deployments` for decode errors; AKS managed etcd logs in Azure Monitor | AKS manages etcd upgrades; contact Azure Support; restore from AKS etcd backup if available | AKS auto-upgrades etcd; enable AKS automatic channel: `az aks update --auto-upgrade-channel patch`; maintain regular cluster backup via Velero |
| Feature flag rollout causing regression — new Azure CNI overlay mode causing pod connectivity loss | Pods in new node pools cannot reach services in old node pools; CNI mismatch | `az aks show -g <rg> -n <cluster> --query 'networkProfile'`; `kubectl exec <pod> -- ping <cross-pool-pod-ip>` | Roll back node pool: delete new node pool and recreate with original CNI mode; migrate pods back | Test CNI configuration changes in isolated staging cluster; do not mix CNI modes across node pools in same cluster |
| Dependency version conflict — cert-manager upgrade breaking Ingress TLS | `Certificate` objects fail to issue after cert-manager upgrade; Ingress returns TLS error | `kubectl get certificate -A`; `kubectl describe certificate <cert> -n <ns>`; `kubectl logs -n cert-manager deploy/cert-manager | grep -i error | tail -20` | Roll back cert-manager: `helm rollback cert-manager <prev-revision> -n cert-manager` | Test cert-manager upgrade in staging; review cert-manager release notes for API changes; verify `ClusterIssuer` compatibility before upgrade |
| Azure Disk / PVC state | Kubernetes PV/PVC objects + Azure Disk resource | `kubectl get pv,pvc -A -o yaml > /incident/pv_pvc.yaml`; `az disk list -g MC_<rg>_<cluster>_<location> -o json` | Low risk — persists unless resources deleted |

## Kernel/OS & Host-Level Failure Patterns

| Symptom | Detection Command | Likely Cause | Host Impact | Immediate Remediation |
|---------|-------------------|--------------|-------------|----------------------|
| OOM killer terminates kubelet or container runtime on AKS node | `kubectl get events --field-selector reason=OOMKilling -A`; `az serial-console connect` then `dmesg -T | grep -i oom` | Pod without memory limits consuming all node memory; kubelet memory leak in older AKS node image; system-reserved memory too low | Node becomes NotReady; all pods on node evicted; workloads rescheduled to surviving nodes potentially causing cascade | Cordon node: `kubectl cordon <node>`; drain: `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`; increase `--system-reserved` memory in AKS node pool config; set memory limits on all pods |
| Inode exhaustion on AKS node OS disk | `kubectl debug node/<node> -it --image=busybox -- df -i /host`; or SSH via `az aks command invoke -g <rg> -n <cluster> --command "df -i"` | Container images with many small files; excessive log file creation; ephemeral storage not cleaned | Node goes NotReady; new pods cannot be scheduled; container runtime fails to create containers | Delete unused images: `crictl rmi --prune`; clean old logs: `journalctl --vacuum-time=1d`; increase OS disk size in node pool; use ephemeral OS disks: `az aks nodepool add --node-osdisk-type Ephemeral` |
| CPU steal spike on AKS node (burstable VM SKU) | `kubectl top nodes`; `az aks command invoke -g <rg> -n <cluster> --command "cat /proc/stat" | awk '/^cpu / {print $9}'` | Using burstable B-series VM SKU for AKS node pool; CPU credits exhausted under sustained load | Pod CPU throttling; scheduler heartbeat delayed; application latency increases across all pods on node | Migrate to non-burstable VM SKU: `az aks nodepool add -g <rg> --cluster-name <cluster> --name newpool --node-vm-size Standard_D4s_v5`; cordon and drain old burstable nodes |
| NTP clock skew on AKS node | `az aks command invoke -g <rg> -n <cluster> --command "chronyc tracking"`; `kubectl exec <pod> -- date` vs host time | Azure VM time sync service (VMICTimeSync) disabled or broken after node image update | Kubernetes lease-based leader election fails; certificate validation errors (NotBefore/NotAfter); log timestamps out of order across nodes | Restart chrony on node: `az aks command invoke --command "systemctl restart chronyd"`; verify VMICTimeSync: `az aks command invoke --command "systemctl status vmicommtimesync"` |
| File descriptor exhaustion on AKS node | `az aks command invoke -g <rg> -n <cluster> --command "cat /proc/sys/fs/file-nr"`; `kubectl exec <pod> -- cat /proc/sys/fs/file-nr` | Application pods not closing connections; kubelet/containerd fd leak; too many pods per node | New containers fail to start; kubelet becomes unresponsive; API server connections from node fail with `too many open files` | Increase node fd limit: customize AKS Linux OS config with `az aks nodepool add --linux-os-config '{"fsFileMax": 1048576}'`; reduce pods per node; identify leaking pod and restart |
| TCP conntrack table full on AKS node | `az aks command invoke -g <rg> -n <cluster> --command "dmesg | grep conntrack"`; check `nf_conntrack_count` vs `nf_conntrack_max` | High pod density with many short-lived connections; kube-proxy iptables mode with high service count | New connections randomly dropped; intermittent connection timeouts across all pods on node; health checks fail | Increase conntrack max via AKS Linux OS config: `az aks nodepool add --linux-os-config '{"netNetfilterNfConntrackMax": 1048576}'`; consider switching to kube-proxy IPVS mode |
| Kernel panic or node crash on AKS | `kubectl get nodes`; node shows `NotReady`; `az aks command invoke --command "last -x reboot"`; check Azure Activity Log for VM events | Azure host maintenance event; kernel bug in AKS node image; hardware failure on underlying Azure host | Node lost; all non-replicated pods lost; StatefulSet pods lose local data; PVs remain but detach | AKS auto-replaces failed nodes; verify node pool autoscaler active: `az aks show -g <rg> -n <cluster> --query 'agentPoolProfiles[].enableAutoScaling'`; check replacement node: `kubectl get nodes --watch`; recover StatefulSet pods manually if needed |
| NUMA memory imbalance on multi-vCPU AKS node | `az aks command invoke -g <rg> -n <cluster> --command "numactl --hardware"`; `numastat -m` | Large VM SKUs (Standard_D16+) with NUMA topology; container memory allocated from single NUMA node | Pods on one NUMA node experience OOM while other NUMA node has free memory; inconsistent pod performance | Use Topology Manager in kubelet: set `topologyManagerPolicy: best-effort` in AKS node pool config; use guaranteed QoS pods for NUMA-sensitive workloads; consider single-NUMA VM SKUs |

## Deployment Pipeline & GitOps Failure Patterns

| Change Type | Failure Signal | Detection Command | Rollback Step | Prevention |
|-------------|---------------|-------------------|---------------|------------|
| Image pull rate limit — Docker Hub throttling AKS image pulls | `kubectl get events -A --field-selector reason=Failed | grep -i 'pull\|429\|rate'` | `kubectl describe pod <pod> | grep -A5 'Events'`; look for `toomanyrequests` | Switch to ACR-cached image: `kubectl set image deploy/<app> <container>=<acr>.azurecr.io/<image>:<tag>`; or add `imagePullPolicy: IfNotPresent` | Mirror all images to Azure Container Registry; enable ACR artifact cache for Docker Hub; attach ACR to AKS: `az aks update -g <rg> -n <cluster> --attach-acr <acr-name>` |
| Image pull auth failure — ACR token or service principal expired | `kubectl get events -A --field-selector reason=Failed | grep -i 'unauthorized\|403'`; pods in `ImagePullBackOff` | `az acr login --name <acr> --expose-token 2>&1`; `kubectl get secret acr-secret -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d` | Re-attach ACR: `az aks update -g <rg> -n <cluster> --attach-acr <acr-name>`; or recreate pull secret with fresh token | Use AKS managed identity for ACR pull (no token expiry); enable `acrpull` role assignment: `az role assignment create --assignee <aks-identity> --role AcrPull --scope <acr-id>` |
| Helm chart drift — AKS cluster Helm releases differ from Git state | `helm list -A`; compare with Git-declared releases; `helm diff upgrade <release> <chart> -f <values> -n <ns>` | `az aks command invoke --command "helm list -A -o json"` and diff against GitOps repo | Reconcile: `helm upgrade <release> <chart> -f <values> -n <ns>`; or rollback: `helm rollback <release> <revision> -n <ns>` | Enforce ArgoCD/Flux for all Helm releases; RBAC deny `helm upgrade` from user contexts; enable Flux `helmrelease` drift detection |
| ArgoCD/Flux sync stuck on AKS application | ArgoCD app status `OutOfSync` or Flux `HelmRelease` shows `False` ready condition | `argocd app get <app> --show-operation`; `kubectl get helmrelease -A`; `kubectl get kustomization -A` | Force sync: `argocd app sync <app> --force`; or for Flux: `flux reconcile helmrelease <release> -n <ns> --force` | Set sync retry policy in ArgoCD Application; configure Flux reconciliation interval; increase repo-server memory for large Helm charts |
| PDB blocking AKS node pool upgrade or workload rollout | `kubectl get pdb -A`; `kubectl describe pdb <pdb> | grep 'Allowed disruptions: 0'`; node pool upgrade stalls | `az aks nodepool show -g <rg> --cluster-name <cluster> --name <pool> --query provisioningState`; `kubectl get nodes -l agentpool=<pool>` | Temporarily relax PDB: `kubectl patch pdb <pdb> -n <ns> -p '{"spec":{"maxUnavailable":1}}'`; or delete PDB during maintenance | Set PDB `maxUnavailable: 1` for all workloads; coordinate AKS upgrades with PDB owners; use `az aks nodepool upgrade --max-surge 33%` |
| Blue-green traffic switch failure during AKS workload migration | Old pods terminated before new pods pass readiness; users see 502/503 during switchover | `kubectl get endpoints <svc> -n <ns>`; check if endpoint list empty; `kubectl get pods -n <ns> -l version=green -o wide` | Route traffic back to blue: `kubectl patch svc <svc> -n <ns> -p '{"spec":{"selector":{"version":"blue"}}}'` | Use Istio or Nginx canary annotations for gradual traffic shift; set `minReadySeconds: 30`; configure proper readiness probes |
| ConfigMap/Secret drift — AKS workload config modified via kubectl, diverges from Git | `kubectl get configmap <cm> -n <ns> -o yaml | diff - <git-version>`; ArgoCD shows `OutOfSync` on specific resource | Manual `kubectl edit` bypassed GitOps pipeline; Flux/ArgoCD not configured with `selfHeal` or `prune` | Restore from Git: `kubectl apply -f <git-configmap>.yaml`; enable ArgoCD `selfHeal: true` or Flux `prune: true` | Set RBAC to deny `kubectl edit/patch` in production namespaces; enable ArgoCD auto-sync with self-heal; use sealed-secrets for Secret management |
| Feature flag stuck — AKS cluster feature registration or addon toggle not propagating | `az feature show --namespace Microsoft.ContainerService --name <feature>` shows `Registered` but behavior unchanged | Feature registered but AKS cluster not reconciled; addon enabled but pods not deployed | Cluster behavior inconsistent with declared feature state; debugging confusion | Re-register provider: `az provider register -n Microsoft.ContainerService`; reconcile cluster: `az aks update -g <rg> -n <cluster>`; verify addon pods: `kubectl get pods -n kube-system -l <addon-label>` |

## Service Mesh & API Gateway Edge Cases

| Pattern | Detection Signal | Root Cause | Impact | Resolution |
|---------|-----------------|------------|--------|------------|
| Circuit breaker false positive — Istio/OSM on AKS ejecting healthy pods | Intermittent 503 from service mesh sidecar; pod healthy but ejected from load balancing | Istio `outlierDetection` too aggressive; AKS pod slow during JVM warmup triggering consecutive 5xx threshold | Healthy pods removed from service mesh routing; remaining pods overloaded; cascading ejection | Tune outlier detection: increase `consecutiveErrors` to 10, `interval` to 30s; add warmup configuration: `spec.trafficPolicy.connectionPool.http.h2UpgradePolicy: DO_NOT_UPGRADE` |
| Rate limit false positive — Azure API Management or Nginx Ingress throttling legitimate traffic | Application returns 429; clients retry causing more 429s; legitimate users blocked | Rate limit per-IP too low for NAT'd clients (multiple users behind single IP); burst allowance insufficient | Customer-facing API returns errors; automated clients enter retry loops amplifying load | Increase rate limit for known IPs; switch to API-key-based rate limiting; configure burst allowance in Nginx Ingress: `nginx.ingress.kubernetes.io/limit-burst-multiplier: "5"` |
| Stale service discovery — CoreDNS returning old pod IPs after AKS node scale-down | Application intermittently connects to non-existent pod IP; connection timeouts every few requests | CoreDNS cache TTL longer than pod lifecycle; AKS node scale-down removed pods before DNS cache expired | Random request failures; connection timeouts to stale endpoints; inconsistent application errors | Reduce CoreDNS cache TTL: edit CoreDNS ConfigMap `kubectl edit configmap coredns -n kube-system`; add `pods verified` to Kubernetes plugin; restart CoreDNS: `kubectl rollout restart deploy/coredns -n kube-system` |
| mTLS rotation break — Istio/cert-manager certificate rotation on AKS breaking inter-service communication | Services return `TLS handshake error`; Istio proxy logs show `CERTIFICATE_VERIFY_FAILED` | Istio CA certificate rotated but sidecars not restarted; cert-manager issuer CA changed without sidecar restart | All mTLS-protected service-to-service communication fails; cascade failure across mesh | Restart affected pods to pick up new certs: `kubectl rollout restart deploy -n <ns>`; if Istio: `istioctl proxy-config secret <pod> -n <ns>` to verify cert; consider enabling Istio auto-rotation with `caCertificates` |
| Retry storm — AKS service mesh retry policy amplifying failures across services | Downstream service returning 503; upstream services retrying 3x each; total load = requests * 3^depth | Istio VirtualService retry policy `attempts: 3` at each service hop; 3-hop chain = 27x amplification | Downstream service overwhelmed; entire service chain degrades; resource exhaustion on AKS nodes | Reduce retry attempts: set `retries.attempts: 1` in VirtualService; add retry budget: `retries.retryOn: "5xx,reset,connect-failure"`; implement circuit breaker at each hop |
| gRPC keepalive/max-message issue on AKS | gRPC services intermittently fail with `RESOURCE_EXHAUSTED` or `UNAVAILABLE: keepalive ping`; Azure Load Balancer dropping idle gRPC connections | Azure Standard LB idle timeout (4 min) shorter than gRPC keepalive interval; max message size mismatch between client and server | Long-running gRPC streams silently dropped; batch operations fail with message size errors | Set gRPC keepalive < 4 min: `GRPC_KEEPALIVE_TIME_MS=180000`; configure Azure LB idle timeout: `service.beta.kubernetes.io/azure-load-balancer-tcp-idle-timeout: "30"`; increase max message size in both client and server |
| Trace context gap — OpenTelemetry trace context lost at AKS Ingress boundary | Traces start at application but miss Ingress span; cannot trace from client to backend | Nginx Ingress not configured to propagate `traceparent` header; or Azure Application Gateway stripping trace headers | Cannot trace request latency through Ingress; blind spot in end-to-end tracing | Enable trace propagation in Nginx Ingress: `enable-opentelemetry: "true"` in ConfigMap; for App Gateway: ensure `X-Request-ID` and `traceparent` in preserved headers; verify: `kubectl exec <pod> -- curl -v -H "traceparent: ..." http://localhost` |
| LB health check misconfiguration — Azure Load Balancer marking healthy AKS pods as down | Service intermittently unreachable via Azure LB; direct pod access works; LB backend pool shows unhealthy instances | Azure LB health probe path returns non-200; probe interval too short for slow-starting pods; wrong port configured | Traffic not routed to healthy pods; service appears down to external clients | Fix health probe in Service annotation: `service.beta.kubernetes.io/azure-load-balancer-health-probe-request-path: /healthz`; increase probe interval: `service.beta.kubernetes.io/azure-load-balancer-health-probe-interval: "15"` |
