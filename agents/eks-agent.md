---
name: eks-agent
provider: aws
domain: eks
aliases:
  - aws-eks
  - elastic-kubernetes-service
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - provider-aws
  - component-eks-agent
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
# EKS SRE Agent

## Role
Site Reliability Engineer specializing in Amazon Elastic Kubernetes Service. Responsible for cluster health, control plane availability, node group lifecycle, networking (VPC CNI), IAM/IRSA authentication, EKS add-on management, and cluster upgrade operations. Bridges AWS-managed infrastructure with Kubernetes workload reliability.

## Architecture Overview

```
Route53 / External DNS
        │
        ▼
AWS ALB / NLB  ←──── AWS Load Balancer Controller (IRSA)
        │
        ▼
┌──────────────────────────────────────────────┐
│           EKS Control Plane (AWS-managed)    │
│  API Server │ etcd │ Scheduler │ Controller  │
│  Health via: /healthz + AWS Console          │
└──────────────┬───────────────────────────────┘
               │ EKS ENI (cross-account VPC)
    ┌──────────▼──────────┐
    │  Worker Node Groups │
    │  ┌───────────────┐  │
    │  │ Managed NG    │  │  ← EC2 ASG + Launch Template
    │  │ Self-Managed  │  │  ← Custom ASG
    │  │ Fargate Profile│  │  ← Serverless pods
    │  └───────────────┘  │
    │                     │
    │  DaemonSets:        │
    │  aws-node (CNI)     │  ← ENI/IP allocation
    │  kube-proxy         │
    │  aws-ebs-csi-node   │
    └─────────────────────┘
         │
         ▼
    VPC Networking
    ├── Pod CIDR from node ENI secondary IPs
    ├── Security Groups for Pods
    └── Custom Networking (if enabled)
```

EKS separates the control plane (owned and operated by AWS, billed per cluster) from data plane node groups (EC2 instances in your VPC). The `aws-node` VPC CNI plugin allocates ENI secondary IPs as pod IPs. IRSA binds Kubernetes ServiceAccounts to IAM roles via OIDC.

## Key Metrics to Monitor

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| `cluster_failed_node_count` | > 0 | > 2 | CloudWatch Container Insights |
| `node_cpu_utilization` | > 75% | > 90% | Per node, from `kube-state-metrics` |
| `node_memory_utilization` | > 80% | > 95% | Trigger node group scale-out |
| `pod_cpu_utilization` | > 80% req limit | > 100% (throttled) | `container_cpu_cfs_throttled_seconds_total` |
| `aws_node` ENI allocation errors | Any | > 5/min | `awscni_total_ip_addresses` vs `awscni_assigned_ip_addresses` |
| API server request latency (p99) | > 1s | > 5s | Via CloudWatch EKS control plane metrics |
| Pending pod count | > 0 for 2m | > 10 for 5m | Autoscaler may be failing |
| CoreDNS `coredns_dns_request_duration_seconds` p99 | > 500ms | > 2s | Namespace-level DNS failures cascade |
| `kube_node_status_condition{condition="Ready",status="false"}` | 1 | > 2 | Node NotReady state |
| EBS CSI `volume_operation_error_count` | > 0 | > 3 | Persistent volume mount failures |

## Alert Runbooks

### Alert: Node Not Ready
**Symptom:** `kube_node_status_condition{condition="Ready",status="false"} == 1`

**Triage:**
```bash
# Identify the node
kubectl get nodes -o wide | grep -v Ready

# Describe for events and conditions
kubectl describe node <node-name>

# Check kubelet logs (SSM preferred over SSH)
aws ssm start-session --target <instance-id>
sudo journalctl -u kubelet -n 200 --no-pager

# Check aws-node CNI on that node
kubectl -n kube-system get pod -l k8s-app=aws-node -o wide | grep <node-name>
kubectl -n kube-system logs <aws-node-pod> -c aws-node --tail=100

# Check for ENI exhaustion (most common cause)
kubectl -n kube-system exec -it <aws-node-pod> -- /app/grpc-health-probe -addr=:50051

# If node is stuck in cordoned state from a failed upgrade
kubectl uncordon <node-name>
```

### Alert: VPC CNI IP Exhaustion
**Symptom:** Pods stuck in `Pending` with event `0/N nodes are available: N Insufficient pods`

**Triage:**
```bash
# Check ENI capacity vs allocation
kubectl -n kube-system exec ds/aws-node -- /app/grpc-health-probe -addr=:50051
kubectl -n kube-system logs ds/aws-node -c aws-node | grep -i "ip allocation\|warm\|eni"

# View current IP allocations per node
kubectl get node -o json | jq '.items[] | {name: .metadata.name, allocatable_pods: .status.allocatable.pods}'

# Check WARM_IP_TARGET and MINIMUM_IP_TARGET settings
kubectl -n kube-system describe ds aws-node | grep -A5 "WARM_IP_TARGET\|MINIMUM_IP_TARGET"

# Check subnet free IPs (AWS side)
aws ec2 describe-subnets --filters "Name=tag:kubernetes.io/cluster/<cluster-name>,Values=owned" \
  --query 'Subnets[*].{ID:SubnetId,AZ:AvailabilityZone,FreeIPs:AvailableIpAddressCount}'

# Enable prefix delegation if subnet is large but ENIs are exhausted
kubectl set env ds aws-node -n kube-system ENABLE_PREFIX_DELEGATION=true WARM_PREFIX_TARGET=1
```

### Alert: aws-auth ConfigMap Misconfiguration
**Symptom:** `kubectl` commands return `error: You must be logged in to the server (Unauthorized)` or new nodes cannot join

**Triage:**
```bash
# View current aws-auth
kubectl -n kube-system get configmap aws-auth -o yaml

# Validate IAM mapping for node role
aws sts get-caller-identity
# Compare the role ARN against entries in aws-auth rolebindings

# Use eksctl to add a missing node role (safer than manual edit)
eksctl create iamidentitymapping \
  --cluster <cluster-name> \
  --region <region> \
  --arn arn:aws:iam::<account>:role/<NodeInstanceRole> \
  --group system:bootstrappers,system:nodes \
  --username system:node:{{EC2PrivateDNSName}}

# Verify nodes can authenticate
aws eks get-token --cluster-name <cluster-name> | jq '.status.token' | cut -c1-20
```

### Alert: IRSA Token Failure
**Symptom:** Pod logs show `WebIdentityErr: failed to retrieve credentials` or `InvalidIdentityToken`

**Triage:**
```bash
# Confirm OIDC provider is associated with cluster
aws eks describe-cluster --name <cluster-name> --query 'cluster.identity.oidc.issuer'
aws iam list-open-id-connect-providers | grep <oidc-id>

# Check service account annotation
kubectl get sa <sa-name> -n <namespace> -o jsonpath='{.metadata.annotations}'
# Expected: {"eks.amazonaws.com/role-arn": "arn:aws:iam::..."}

# Verify IAM trust policy references correct OIDC issuer and SA
aws iam get-role --role-name <role-name> --query 'Role.AssumeRolePolicyDocument'

# Check projected token mount inside pod
kubectl exec -it <pod> -- cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token | \
  cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

## Common Issues & Troubleshooting

### Issue 1: Cluster Upgrade Node Group Not Progressing
**Symptom:** Managed node group stuck in `UPDATING` state for > 30 minutes.

```bash
# Check node group status
aws eks describe-nodegroup --cluster-name <cluster> --nodegroup-name <ng-name> \
  --query 'nodegroup.{Status:status,Health:health}'

# View update history
aws eks list-updates --cluster-name <cluster> --nodegroup-name <ng-name>
aws eks describe-update --cluster-name <cluster> --nodegroup-name <ng-name> --update-id <id>

# Identify stuck nodes (MaxUnavailable may be blocking)
kubectl get nodes --sort-by='.metadata.creationTimestamp'

# Check if PodDisruptionBudgets are blocking drain
kubectl get pdb -A
kubectl get pods -A | grep Terminating

# Force-evict if PDB is too restrictive (last resort)
kubectl delete pod <stuck-pod> -n <ns> --force --grace-period=0
```

### Issue 2: EBS CSI Volume Mount Failure
**Symptom:** Pod stuck in `ContainerCreating`, event: `AttachVolume.Attach failed for volume "pvc-xxx": context deadline exceeded`

```bash
# Check EBS CSI driver pods
kubectl -n kube-system get pods -l app=ebs-csi-controller
kubectl -n kube-system logs -l app=ebs-csi-controller -c csi-provisioner --tail=50

# Check the PVC and PV
kubectl describe pvc <pvc-name> -n <ns>
kubectl describe pv <pv-name>

# Verify EBS volume exists and is available
aws ec2 describe-volumes --volume-ids <vol-id> \
  --query 'Volumes[0].{State:State,AZ:AvailabilityZone,Attachments:Attachments}'

# Check IRSA for EBS CSI controller ServiceAccount
kubectl -n kube-system get sa ebs-csi-controller-sa -o jsonpath='{.metadata.annotations}'

# If volume is in wrong AZ (topology mismatch), delete PVC and recreate with correct StorageClass
kubectl get sc ebs-sc -o yaml | grep -A5 volumeBindingMode
# Should be WaitForFirstConsumer
```

### Issue 3: CoreDNS CrashLoopBackOff
**Symptom:** CoreDNS pods restarting, DNS resolution intermittently failing across the cluster.

```bash
# Check CoreDNS pod status and logs
kubectl -n kube-system get pods -l k8s-app=kube-dns
kubectl -n kube-system logs -l k8s-app=kube-dns --tail=100

# Test DNS resolution from a debug pod
kubectl run dns-test --image=busybox:1.28 --rm -it --restart=Never -- nslookup kubernetes.default

# Check CoreDNS ConfigMap for syntax errors
kubectl -n kube-system get cm coredns -o yaml

# Check memory limits (common cause of OOM crashes)
kubectl -n kube-system describe pod -l k8s-app=kube-dns | grep -A3 "Limits:"

# Increase CoreDNS memory if OOMKilled
kubectl -n kube-system patch deployment coredns --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"256Mi"}]'

# Verify EKS add-on version matches cluster version
aws eks describe-addon --cluster-name <cluster> --addon-name coredns
```

### Issue 4: Fargate Pod Stuck in Pending
**Symptom:** Pod with Fargate profile selector never starts, remains `Pending`.

```bash
# Check Fargate profile exists and selectors match pod labels/namespace
aws eks describe-fargate-profile --cluster-name <cluster> --fargate-profile-name <profile>

# Check pod annotations and namespace match profile
kubectl describe pod <pod> -n <ns> | grep -E "namespace|labels|Annotations"

# Common cause: pod has nodeSelector or toleration that prevents Fargate scheduling
kubectl get pod <pod> -n <ns> -o yaml | grep -A10 "nodeSelector\|tolerations"

# Verify Fargate pod execution role
aws eks describe-fargate-profile --cluster-name <cluster> --fargate-profile-name <profile> \
  --query 'fargateProfile.podExecutionRoleArn'

# Check CloudTrail for IAM errors related to Fargate
aws logs filter-log-events \
  --log-group-name /aws/eks/<cluster>/cluster \
  --filter-pattern "fargate" \
  --start-time $(date -d '1 hour ago' +%s000)
```

### Issue 5: Kube-Proxy DaemonSet Mismatch After Upgrade
**Symptom:** Services unreachable after cluster version upgrade; iptables rules stale.

```bash
# Check kube-proxy version vs cluster version
kubectl -n kube-system get ds kube-proxy -o yaml | grep image
kubectl version --short

# Update kube-proxy add-on to match cluster version
aws eks update-addon --cluster-name <cluster> --addon-name kube-proxy \
  --addon-version v1.XX.X-eksbuild.1 --resolve-conflicts OVERWRITE

# Restart kube-proxy to regenerate iptables rules
kubectl -n kube-system rollout restart ds/kube-proxy

# Verify iptables rules on a node (via SSM)
aws ssm start-session --target <instance-id>
sudo iptables -t nat -L KUBE-SERVICES | head -30
```

### Issue 6: Cluster Autoscaler Not Scaling Up
**Symptom:** Pending pods not triggering new node provisioning.

```bash
# Check cluster autoscaler logs
kubectl -n kube-system logs -l app=cluster-autoscaler --tail=200 | grep -E "scale up|no candidates|error"

# Verify IRSA permissions
kubectl -n kube-system get sa cluster-autoscaler -o jsonpath='{.metadata.annotations}'

# Check ASG tags match cluster name (required for CA discovery)
aws autoscaling describe-auto-scaling-groups --query \
  "AutoScalingGroups[?Tags[?Key=='k8s.io/cluster-autoscaler/<cluster-name>' && Value=='owned']].[AutoScalingGroupName]"

# Check max size of ASG
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names <asg-name> \
  --query 'AutoScalingGroups[0].{Min:MinSize,Max:MaxSize,Desired:DesiredCapacity}'

# Look for scale-up blocking events
kubectl get events -A | grep "FailedScaleUp\|NotTriggerScaleUp"
```

## Key Dependencies

- **AWS VPC** — subnets must have sufficient free IPs for pod scheduling; route tables must route traffic correctly
- **EC2 IAM Instance Profile** — node role requires `AmazonEKSWorkerNodePolicy`, `AmazonEKS_CNI_Policy`, `AmazonEC2ContainerRegistryReadOnly`
- **AWS IAM OIDC Provider** — required for IRSA; must match cluster OIDC issuer URL
- **ECR** — container image pulls; affected by ECR throttling or VPC endpoint misconfiguration
- **EBS / EFS** — persistent volumes; CSI driver must have IRSA with `ec2:AttachVolume` permissions
- **Route 53 / External DNS** — DNS propagation for ingress; relies on correct IRSA for Route53 zones
- **AWS ALB** — AWS Load Balancer Controller creates ALBs; requires subnets tagged `kubernetes.io/role/elb=1`
- **CloudWatch Container Insights** — metrics and logs; requires `CloudWatchAgentServerPolicy` on node role

## Cross-Service Failure Chains

- **EC2 capacity shortage** → node group scale-out fails → pods remain Pending → services degraded → HPA cannot reduce load → cascading timeouts
- **VPC subnet IP exhaustion** → `aws-node` cannot allocate IPs → new pods fail to start → rolling deployments stall → rollback triggers more pod churn
- **OIDC provider deleted/rotated** → all IRSA-based ServiceAccounts fail → S3, DynamoDB, SES, etc. calls return 401 → application-level failures across all namespaces
- **CoreDNS failure** → all in-cluster DNS resolution fails → service discovery breaks → health checks fail → ALB target group marks all targets unhealthy → 502s to all users
- **aws-auth ConfigMap corrupted** → kubectl auth fails → no one can manage the cluster → incident response blocked

## Partial Failure Patterns

- **Single AZ node group degraded**: Pods with AZ-specific PVs cannot reschedule to other AZs; services with `topologySpreadConstraints` may become unbalanced
- **Mixed version kube-proxy after upgrade**: Some nodes have old iptables rules; traffic routing is non-deterministic for certain service types
- **IRSA token cache stale**: Pods use cached 15-minute tokens; failures appear ~15 minutes after IAM change and resolve on next token refresh
- **ENI secondary IP warm pool delay**: Burst pod creation outpaces CNI warm pool; pods queue briefly then recover — appears as intermittent scheduling latency spikes

## Performance Thresholds

| Operation | Acceptable | Degraded | Critical |
|-----------|-----------|----------|----------|
| Pod scheduling latency | < 2s | 2–10s | > 10s |
| Node join time (managed NG) | < 3 min | 3–7 min | > 10 min |
| API server p99 latency (read) | < 500ms | 500ms–2s | > 5s |
| API server p99 latency (mutate) | < 1s | 1–3s | > 5s |
| EBS volume attach | < 30s | 30–90s | > 3 min |
| CoreDNS p99 query time | < 10ms | 10–500ms | > 1s |
| Rolling deployment (10 replicas) | < 5 min | 5–15 min | > 20 min |
| Cluster upgrade (node group) | < 45 min | 45–90 min | > 2 hr |

## Capacity Planning Indicators

| Indicator | Monitor Source | Scale Action Trigger | Notes |
|-----------|---------------|---------------------|-------|
| Node CPU avg > 70% (7-day trend) | CloudWatch Container Insights | Add node group capacity / larger instance type | Review right-sizing with Compute Optimizer |
| Pod density > 80% of node `max-pods` | `kube_node_status_allocatable{resource="pods"}` | Enable prefix delegation or add nodes | Max pods depends on instance type and ENI limits |
| Subnet free IPs < 50 | VPC subnet metrics | Create new subnets or enable prefix delegation | ENI secondary IPs = pod IPs |
| PVC provisioning queue > 5 | EBS CSI logs | Check EBS quota (20 volumes/instance default) | Request EBS volume limit increase |
| CoreDNS QPS > 30K/s per pod | CoreDNS Prometheus metrics | Scale CoreDNS replicas | Add NodeLocal DNSCache |
| Cluster version > 2 behind latest | `aws eks list-clusters` | Plan upgrade cycle | EKS supports latest 3 minor versions |
| API server request rate > 2K rps | CloudWatch EKS metrics | Evaluate caching / rate limiting | Authn/authz webhooks add latency |
| Fargate profile count near 10 | AWS console | Consolidate selectors | Soft limit 10 profiles per cluster |

## Diagnostic Cheatsheet

```bash
# All nodes with their roles, versions, and status
kubectl get nodes -L node.kubernetes.io/instance-type,topology.kubernetes.io/zone --sort-by='.metadata.labels.topology\.kubernetes\.io/zone'

# All failing pods across cluster with node placement
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded -o wide

# Recent events sorted by time (last 30 min)
kubectl get events -A --sort-by='.lastTimestamp' | tail -50

# Check EKS cluster status and version
aws eks describe-cluster --name <cluster> --query 'cluster.{Version:version,Status:status,Endpoint:endpoint}'

# List all EKS add-ons and their status
aws eks list-addons --cluster-name <cluster> | jq -r '.addons[]' | \
  xargs -I{} aws eks describe-addon --cluster-name <cluster> --addon-name {} \
  --query 'addon.{Name:addonName,Version:addonVersion,Status:status}'

# Find pods not on the expected node type (e.g., not on on-demand)
kubectl get pods -A -o json | jq -r '.items[] | select(.spec.nodeSelector."node.kubernetes.io/lifecycle" != "spot") | "\(.metadata.namespace)/\(.metadata.name)"'

# Check IRSA token expiry for all service accounts
kubectl get sa -A -o json | jq -r '.items[] | select(.metadata.annotations."eks.amazonaws.com/role-arn" != null) | "\(.metadata.namespace)/\(.metadata.name): \(.metadata.annotations."eks.amazonaws.com/role-arn")"'

# View aws-node ENI allocation details
kubectl -n kube-system exec ds/aws-node -- cat /host/etc/cni/net.d/10-aws.conflist 2>/dev/null

# Get node group desired/actual capacity
aws autoscaling describe-auto-scaling-groups \
  --query 'AutoScalingGroups[?contains(Tags[].Key, `eks:cluster-name`)].[AutoScalingGroupName,DesiredCapacity,MinSize,MaxSize]' \
  --output table

# Tail EKS cluster control plane logs in real time
aws logs tail /aws/eks/<cluster>/cluster --follow --format short

# Check for any throttled API calls in the last hour
aws cloudwatch get-metric-statistics \
  --namespace AWS/EKS --metric-name apiserver_request_total \
  --dimensions Name=ClusterName,Value=<cluster> Name=verb,Value=throttled \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 --statistics Sum
```

## SLO Definitions

| SLO | Target | Error Budget (30d) | Measurement |
|-----|--------|-------------------|-------------|
| Control Plane API Availability | 99.9% | 43.2 min/month | `/healthz` HTTP 200 from external prober every 60s |
| Pod Scheduling Success Rate | 99.5% | 3.6 hr/month | `(kube_pod_status_scheduled_time - kube_pod_created) < 30s` over 5m window |
| DNS Resolution Success | 99.95% | 21.6 min/month | Synthetic prober: `nslookup kubernetes.default` from each node every 30s |
| Node Ready Availability | 99.0% | 7.2 hr/month | `kube_node_status_condition{condition="Ready",status="true"}` average across all nodes |

## Configuration Audit Checklist

| Check | Command | Expected |
|-------|---------|----------|
| Cluster version within N-1 of latest | `aws eks describe-cluster --name <c> --query 'cluster.version'` | Within 1 minor version of latest EKS |
| All add-ons at recommended version | `aws eks list-addons --cluster-name <c>` + describe each | Status: ACTIVE, version: recommended |
| IRSA OIDC provider exists | `aws iam list-open-id-connect-providers` | Matches cluster OIDC issuer |
| aws-auth has no wildcards in groups | `kubectl -n kube-system get cm aws-auth -o yaml` | No `system:masters` for non-admin roles |
| IMDSv2 enforced on launch template | `aws ec2 describe-launch-template-versions --query '...HttpTokens'` | `required` |
| Public endpoint access restricted | `aws eks describe-cluster --query 'cluster.resourcesVpcConfig.publicAccessCidrs'` | Not `0.0.0.0/0` (or private-only) |
| EKS control plane logging enabled | `aws eks describe-cluster --query 'cluster.logging.clusterLogging'` | All log types enabled |
| Node security group allows node-to-node | `aws ec2 describe-security-groups --group-ids <sg>` | Port 443 + 10250 between nodes and control plane |
| Subnets have capacity (> 10% free IPs) | `aws ec2 describe-subnets` | `AvailableIpAddressCount` > 10% of total |
| Cluster autoscaler version matches k8s | `kubectl -n kube-system get deploy cluster-autoscaler -o yaml \| grep image` | Matches cluster minor version |

## Log Pattern Library

| Log Pattern | Source | Meaning |
|-------------|--------|---------|
| `level=error msg="Failed to create pod sandbox"` | kubelet | CRI/containerd failure; usually CNI or image pull issue |
| `ip_address="" failed to allocate` | aws-node | ENI secondary IP pool exhausted; check subnet free IPs |
| `Unauthorized` in API audit logs | kube-apiserver | aws-auth ConfigMap missing entry or token expired |
| `error getting instance eniConfig` | aws-node | Custom Networking enabled but ENIConfig CR missing for AZ |
| `timeout: no recent network activity` | kube-proxy | Network plugin health check failure; restart kube-proxy |
| `WebIdentityErr: failed to retrieve credentials` | application pod | IRSA misconfiguration; check SA annotation and IAM trust policy |
| `toomanyrequests: Rate exceeded` | ECR pull | ECR pull rate limit; use VPC endpoint to avoid public throttling |
| `PLEG is not healthy` | kubelet | Pod Lifecycle Event Generator overloaded; node under pressure |
| `eviction manager: attempting to reclaim` | kubelet | Node memory/disk pressure; pods will be evicted |
| `failed to list *v1.Node: nodes is forbidden` | cluster-autoscaler | IRSA role missing `autoscaling:DescribeAutoScalingGroups` |
| `Error from server (ServiceUnavailable)` | kubectl | API server overloaded or temporarily unavailable |
| `failed to ensure load balancer: AccessDenied` | aws-load-balancer-controller | IRSA role missing ELB permissions |

## Error Code Quick Reference

| Error | Service | Meaning | Fix |
|-------|---------|---------|-----|
| `NodeCreationFailure` | EKS Node Group | EC2 launch failed (capacity, AZ, AMI issue) | Check EC2 limits; try different AZ |
| `PodSecurityPolicyViolation` | Kubernetes | PSP/PSA blocked pod creation | Update Pod Security Standard or policy |
| `InvalidIdentityToken` | AWS STS / IRSA | OIDC token audience mismatch | Check SA annotation and trust policy audience |
| `AccessDeniedException` | AWS IAM | Missing IAM permission for IRSA role | Add missing action to IAM policy |
| `InsufficientFreeAddressesInSubnet` | VPC CNI | Subnet has no free IPs | Add new subnets or enable prefix delegation |
| `FailedMount` | Kubernetes | CSI volume failed to attach/mount | Check EBS CSI driver and IAM permissions |
| `Evicted` | Kubernetes | Node resource pressure evicted pod | Increase node size or add memory limits |
| `CrashLoopBackOff` | Kubernetes | Container repeatedly crashing | Check logs: `kubectl logs <pod> --previous` |
| `ImagePullBackOff` | Kubernetes | Cannot pull container image | Check ECR permissions, image tag exists, VPC endpoint |
| `Forbidden: User ... cannot ... in namespace` | Kubernetes RBAC | Missing ClusterRole/RoleBinding | Create appropriate RBAC binding |
| `connection refused :443` | kubectl | API server unreachable | Check security group, cluster endpoint, VPN/PrivateLink |
| `context deadline exceeded` | Various | Timeout between components | Network issue or overloaded component |

## Known Failure Signatures

| Signature | Root Cause | Distinguishing Indicator |
|-----------|-----------|------------------------|
| All new pods pending, existing pods healthy | Subnet IP exhaustion | `aws-node` logs: `failed to allocate IP`; subnet `AvailableIpAddressCount=0` |
| kubectl commands timeout, pods fine | API server overloaded or private endpoint issue | `/healthz` timeout; CloudWatch EKS API latency spike |
| Nodes cycle NotReady → Ready every ~10 min | kubelet certificate rotation misconfigured | Node event: `client certificate rotation failed` |
| DNS fails only for external names | CoreDNS `forward` plugin misconfigured | `nslookup kubernetes.default` works; `nslookup google.com` fails |
| Pods lose connectivity after node replacement | Security group for pods not updated | New ENI missing pod security group; check `ENABLE_POD_ENI=true` node label |
| IRSA fails after 24 hours for all pods | OIDC token lifetime expired; no rotation | Check pod `AWS_WEB_IDENTITY_TOKEN_FILE` rotation; verify Kubernetes projected token volume TTL |
| New managed node group nodes never join | aws-auth missing new node role ARN | Node bootstrap log: `Unauthorized` when calling `eks:DescribeCluster` |
| PVC mount hangs after node replacement | EBS volume stuck `in-use` on terminated instance | `aws ec2 describe-volumes` shows old attachment; use `aws ec2 detach-volume --force` |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `connection refused` / `EOF` to service ClusterIP | HTTP client / gRPC / JDBC | kube-proxy rules stale after node replacement; iptables not synced | `kubectl get endpoints <svc>` — check pod IPs; `iptables -L -t nat \| grep <svc-clusterip>` | Restart kube-proxy pod; verify `kubeproxy-mode` (iptables vs. ipvs) |
| `Unable to connect to the server: dial tcp: no route to host` | kubectl / k8s client-go | EKS API server private endpoint unreachable; VPC routing or SG changed | `telnet <cluster-endpoint> 443`; check cluster SG inbound rules for port 443 | Restore SG rule for API server endpoint; check route table for private endpoint ENI subnet |
| `Error from server (ServiceUnavailable): the server is currently unable to handle the request` | kubectl / client-go | EKS control plane overloaded; too many watch connections or webhook latency | CloudWatch `APIServerRequestCount` + `APIServerRequestLatency` spike | Reduce watch connections; audit mutating webhooks for latency; scale down excessive controllers |
| Pod `CrashLoopBackOff` with `Failed to pull image: CannotPullContainerError` | kubelet / containerd | ECR auth token expired on node (> 12 h since bootstrap) or IAM role missing ECR policy | `kubectl describe pod <pod>` → Events → image pull error; check node IAM role | Ensure node IAM role has `AmazonEC2ContainerRegistryReadOnly`; use ECR credential helper |
| `OOMKilled` / `Evicted` with message `The node was low on resource: memory` | kubelet eviction | Node memory pressure; no resource limits set; noisy neighbor pod consuming excess | `kubectl top nodes`; `kubectl describe node` → Conditions → `MemoryPressure` | Set resource `requests`/`limits` on all pods; use VPA; add node group |
| `Exec format error` / `standard_init_linux.go: exec user process caused: exec format error` | Container runtime | Image built for wrong CPU architecture (amd64 image on arm64 Graviton node) | `kubectl describe pod` → image arch; `docker manifest inspect` | Build multi-arch images; use `nodeAffinity` to pin arch-specific images |
| IRSA `NoCredentialProviders: no valid providers in chain` | AWS SDK inside pod | Projected service account token volume not mounted; OIDC provider mismatch | `kubectl get pod -o yaml \| grep -A5 serviceAccount`; check IRSA annotation; `aws sts get-caller-identity` from inside pod | Verify IAM role trust policy has correct OIDC ARN; ensure SA annotation matches role ARN |
| DNS `NXDOMAIN` for `<svc>.<ns>.svc.cluster.local` | Application DNS resolver | CoreDNS CrashLoopBackOff; ConfigMap misconfiguration; pod DNS policy wrong | `kubectl -n kube-system logs -l k8s-app=kube-dns`; `kubectl exec <pod> -- nslookup kubernetes.default` | Restart CoreDNS; restore CoreDNS ConfigMap; check pod `dnsPolicy` |
| Webhook admission `failed calling webhook` timeout | kubectl apply / Helm | Mutating or validating webhook service unreachable; pod not running | `kubectl get validatingwebhookconfigurations` + check `service` and `caBundle`; webhook pod logs | Set `failurePolicy: Ignore` temporarily; fix webhook deployment; check namespace selector |
| `PersistentVolumeClaim is not bound` — pod stuck Pending | Application workload | EBS CSI driver not running; AZ mismatch; StorageClass `WaitForFirstConsumer` | `kubectl describe pvc`; `kubectl -n kube-system logs -l app=ebs-csi-controller` | Confirm EBS CSI add-on installed; ensure pod and PVC in same AZ via topology constraints |
| gRPC `transport: Error while dialing dial tcp: connection reset by peer` | gRPC client | NLB idle timeout shorter than gRPC keepalive; connection recycled mid-stream | Check NLB target group `connection_draining_timeout`; NLB `load_balancing.cross_zone` | Set gRPC `keepalive` < NLB idle timeout; use AWS LBC annotation `aws-load-balancer-target-type: ip` |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| etcd storage approaching limit | etcd database size crossing 4 GB; slow `LIST` operations | CloudWatch EKS metric `etcd_db_total_size_in_bytes` > 3.5 GB | Hours to days | Compact and defragment etcd; clean up orphaned resources; reduce watch/events retention |
| Node IP exhaustion in VPC subnets | `aws-node` logs `failed to allocate IP`; new pods pending on nodes with available CPU/memory | `aws ec2 describe-subnets --query '[*].{CIDR:CidrBlock,AvailableIPs:AvailableIpAddressCount}'` | Hours before cluster cannot scale | Add secondary CIDR; use prefix delegation (`ENABLE_PREFIX_DELEGATION=true` in aws-node) |
| IRSA OIDC token accumulation on disk | `/var/run/secrets/kubernetes.io` mount points filling; pod volume mount failures | `df -h /var/run/secrets` on affected nodes; `kubectl get pods -o yaml \| grep tokenExpirationSeconds` | Weeks; slow build-up | Reduce token `expirationSeconds`; ensure projected token volume cleanup on pod delete |
| Control plane API server admission latency creep | p99 `APIServerRequestLatency` trending up; `kubectl apply` taking > 5 s | `aws cloudwatch get-metric-statistics --namespace AWS/EKS --metric-name APIServerRequestLatency` | Days before API timeouts cascade | Audit mutating webhooks; reduce number of admission controllers; check webhook backend latency |
| Managed node group AMI drift | Nodes running AMI 3+ versions behind current; kubelet version skew approaching limit | `aws eks list-nodegroups \| xargs aws eks describe-nodegroup --query 'nodegroup.releaseVersion'` vs. `aws eks describe-cluster --query 'cluster.version'` | Weeks; blocks upgrades | Enable node group `updateConfig.maxUnavailable`; roll node group to latest AMI |
| CoreDNS cache hit ratio dropping | DNS query latency p99 increasing; CoreDNS `cache misses` metric rising | `kubectl exec -n kube-system <coredns-pod> -- curl -s localhost:9153/metrics \| grep coredns_cache` | Days before DNS becomes bottleneck | Scale CoreDNS replicas; tune cache TTL in CoreDNS ConfigMap `cache` block |
| Cluster autoscaler unable to scale down | Nodes perpetually at low utilization; CA logs `pod with local storage cannot be evicted` | `kubectl -n kube-system logs -l app=cluster-autoscaler \| grep "scale down"` | Weeks; cost accumulation | Fix PodDisruptionBudgets; move local storage workloads to PVCs; annotate CA-safe pods |
| Kubelet certificate rotation warning | Node event `certificate rotation failed`; node eventually goes NotReady | `kubectl get nodes -o yaml \| grep 'kubeletConfigKey\|certificateAuthority'`; node syslog | Hours; node drops out | Ensure `rotateCertificates: true` in kubelet config; ensure node can reach EKS API for cert renewal |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# EKS Full Health Snapshot
CLUSTER="${EKS_CLUSTER:-}"
REGION="${AWS_REGION:-us-east-1}"

if [[ -z "$CLUSTER" ]]; then
  echo "Usage: EKS_CLUSTER=<name> $0"; exit 1
fi

echo "=== Cluster Status ==="
aws eks describe-cluster --region "$REGION" --name "$CLUSTER" \
  --query 'cluster.{Version:version,Status:status,Endpoint:endpoint,Logging:logging.clusterLogging}' --output table

echo ""
echo "=== Node Group Status ==="
aws eks list-nodegroups --region "$REGION" --cluster-name "$CLUSTER" \
  --query 'nodegroups' --output text | tr '\t' '\n' | while read -r ng; do
    aws eks describe-nodegroup --region "$REGION" --cluster-name "$CLUSTER" --nodegroup-name "$ng" \
      --query '{NG:nodegroupName,Status:status,Desired:scalingConfig.desiredSize,Min:scalingConfig.minSize,Max:scalingConfig.maxSize,AMI:releaseVersion}' --output table
  done

echo ""
echo "=== Node Status ==="
kubectl get nodes -o wide --no-headers | awk '{print $1, $2, $3, $5, $6}'

echo ""
echo "=== Not-Running Pods (all namespaces) ==="
kubectl get pods -A --field-selector='status.phase!=Running,status.phase!=Succeeded' --no-headers 2>/dev/null | head -30

echo ""
echo "=== CoreDNS Health ==="
kubectl -n kube-system get pods -l k8s-app=kube-dns -o wide
kubectl -n kube-system top pods -l k8s-app=kube-dns 2>/dev/null

echo ""
echo "=== Cluster Autoscaler Logs (last 20 lines) ==="
kubectl -n kube-system logs -l app=cluster-autoscaler --tail=20 2>/dev/null || echo "(CA not installed)"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# EKS Performance Triage — API server, nodes, pods
CLUSTER="${EKS_CLUSTER:-}"
REGION="${AWS_REGION:-us-east-1}"
NAMESPACE="${NAMESPACE:-default}"

echo "=== API Server Request Latency p99 (last 30 min) ==="
aws cloudwatch get-metric-statistics --region "$REGION" \
  --namespace AWS/EKS --metric-name APIServerRequestLatency \
  --dimensions Name=ClusterName,Value="$CLUSTER" \
  --start-time "$(date -u -d '30 minutes ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -v-30M '+%Y-%m-%dT%H:%M:%SZ')" \
  --end-time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
  --period 300 --statistics p99 --output table 2>/dev/null || echo "(Metrics not available — enable control plane logging)"

echo ""
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
echo "=== PodDisruptionBudgets Blocking Eviction ==="
kubectl get pdb -A --no-headers | awk '$5 == "0" {print $1, $2, $3, $4, $5}'
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# EKS Connection & Resource Audit — networking, IRSA, subnet IPs
CLUSTER="${EKS_CLUSTER:-}"
REGION="${AWS_REGION:-us-east-1}"

echo "=== VPC Subnet IP Availability ==="
VPC_ID=$(aws eks describe-cluster --region "$REGION" --name "$CLUSTER" \
  --query 'cluster.resourcesVpcConfig.vpcId' --output text)
aws ec2 describe-subnets --region "$REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'Subnets[*].{AZ:AvailabilityZone,CIDR:CidrBlock,FreeIPs:AvailableIpAddressCount,SubnetId:SubnetId}' --output table

echo ""
echo "=== aws-node (VPC CNI) Pod Status ==="
kubectl -n kube-system get pods -l k8s-app=aws-node -o wide
kubectl -n kube-system logs -l k8s-app=aws-node --tail=5 2>/dev/null | grep -i "error\|failed\|warn" || echo "No errors"

echo ""
echo "=== IRSA-Annotated Service Accounts ==="
kubectl get serviceaccounts -A -o json \
  | jq -r '.items[] | select(.metadata.annotations["eks.amazonaws.com/role-arn"] != null) | "\(.metadata.namespace)/\(.metadata.name) → \(.metadata.annotations["eks.amazonaws.com/role-arn"])"'

echo ""
echo "=== Mutating/Validating Webhooks ==="
kubectl get mutatingwebhookconfigurations -o custom-columns='NAME:.metadata.name,FAILURE:.webhooks[*].failurePolicy'
kubectl get validatingwebhookconfigurations -o custom-columns='NAME:.metadata.name,FAILURE:.webhooks[*].failurePolicy'

echo ""
echo "=== EKS Add-on Status ==="
aws eks list-addons --region "$REGION" --cluster-name "$CLUSTER" \
  --query 'addons' --output text | tr '\t' '\n' | while read -r addon; do
    aws eks describe-addon --region "$REGION" --cluster-name "$CLUSTER" --addon-name "$addon" \
      --query '{Addon:addonName,Status:status,Version:addonVersion}' --output table
  done
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| CPU throttling from missing limits | Other pods degrade; `container_cpu_cfs_throttled_periods_total` high for specific containers | `kubectl top pods -A --sort-by=cpu`; check containers without `resources.limits.cpu` | Set CPU limit on offending pod; cordon node if severe | Enforce `LimitRange` in all namespaces; use OPA/Kyverno policy to require limits |
| Memory eviction cascade | Multiple pods evicted from same node; remaining pods see latency spikes | `kubectl describe node <node>` → eviction events; `kubectl get events -A \| grep Evicted` | `kubectl top pods` on evicted node; cordon and drain | Set `requests` = 70% of `limits`; tune kubelet `evictionHard` thresholds |
| Noisy DNS client flooding CoreDNS | CoreDNS CPU spike; all pods see DNS latency increase | `kubectl -n kube-system exec <coredns-pod> -- curl localhost:9153/metrics \| grep coredns_dns_request_count_total`; identify top-querying pod via network policy logs | Rate-limit DNS from offending pod via NetworkPolicy; restart noisy app | Set `ndots:2` in pod `dnsConfig`; use fully qualified domain names in app config |
| etcd watch storms from poorly written controllers | API server `etcd_request_latency_seconds` rising; LIST operations slow | CloudWatch `etcd_db_total_size_in_bytes`; controller logs showing repeated full re-lists | Scale down or remove noisy controller; add `resourceVersion` on watches | Use informers with `SharedIndexInformer`; never call raw LIST in a tight loop |
| PVC storage IOPS contention on shared EBS gp2 volume | Pod experiencing high disk latency (`io_time` metric); other pods on same node unaffected | `kubectl exec <pod> -- iostat -x 1 5`; check node's EBS volume `VolumeQueueLength` in CloudWatch | Migrate to gp3 with dedicated IOPS; move high-IOPS workload to dedicated node | Use gp3 StorageClass with explicit `iops` and `throughput`; set node `taints` for I/O-intensive workloads |
| Image pull bandwidth saturation on node | All pods on a node start slowly during mass rollout; `ImagePullBackOff` with timeout | `kubectl describe node` → events show multiple concurrent image pulls; `crictl stats` on node | Limit image pull concurrency with `--serialize-image-pulls=true` kubelet flag | Pre-pull images via DaemonSet or at AMI bake time; use layered images to maximize cache reuse |
| Cluster autoscaler scale-up delayed by spot interruption surge | Many pods pending; CA logs show repeated node provisioning failures | CA logs: `failed to create node group`; EC2 ASG events: `InsufficientInstanceCapacity` | Switch to On-Demand for critical node pools; add diversified instance type list | Use Karpenter with multi-instance-type provisioner; never rely on single Spot instance type |
| Webhook latency causing kubectl timeout for all users | Every `kubectl apply` takes > 30 s; unrelated to payload size | `kubectl get mutatingwebhookconfigurations -o yaml` — find webhook with `timeoutSeconds` > 10; `kubectl -n <ns> top pods` for webhook backend | Set webhook `failurePolicy: Ignore` temporarily; scale webhook backend | Set `timeoutSeconds: 5` on all webhooks; use `namespaceSelector` to exclude non-target namespaces |
| Log flooding filling node disk | Node `DiskPressure` condition; kubelet evicts pods; `/var/log/containers` fills root disk | `du -sh /var/log/containers/*` on node (via `kubectl debug node`); identify highest-volume container logs | Immediately set container log `--max-size` and `--max-file`; restart log-flooding container | Set `containerLogMaxSize` and `containerLogMaxFiles` in kubelet config; use Fluent Bit with back-pressure |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| etcd unavailable or slow | API server returns 503 for all mutation requests; controllers stop reconciling; kubectl commands hang | No new pods start; deployments freeze; HPA cannot scale; all cluster state changes stop | `kubectl get --raw /healthz/etcd` returns `etcd failed`; API server logs: `etcdserver: request timed out`; CloudWatch `etcd_server_has_leader` = 0 | Restore etcd quorum (EKS managed — contact AWS support); place cluster in read-only mode; avoid mutation until etcd recovers |
| Node NotReady cascade (all nodes in AZ) | Pods on affected nodes enter `Terminating`; kube-controller-manager begins evicting pods after `node-monitor-grace-period`; other AZs get replacement pods scheduled | All workloads with AZ affinity rules lose their AZ; some multi-replica services may go below minimum | `kubectl get nodes` shows multiple nodes `NotReady`; CloudWatch EC2 `StatusCheckFailed` for instances in one AZ; pod `Terminating` count spike | Check AZ-specific issue (AWS console); cordon affected nodes; taint with `NoSchedule`; force pod rescheduling to healthy AZs |
| CoreDNS pod crash loop | All DNS lookups inside cluster fail after cache TTL expires; services cannot resolve each other; external API calls fail | All inter-service communication breaks; services return connection errors; load balancers route to degraded backends | `kubectl get pods -n kube-system -l k8s-app=kube-dns` shows CrashLoopBackOff; `kubectl exec <pod> -- nslookup kubernetes` returns SERVFAIL; CoreDNS logs: `HINFO: read tcp: connection reset by peer` | Restart CoreDNS: `kubectl rollout restart deployment/coredns -n kube-system`; increase CoreDNS replicas to 3; check node-level iptables rules |
| kube-proxy failure on nodes | Services become unreachable; iptables/ipvs rules not updated for new endpoints; new pods not routable | New pod endpoints not added to service routing; rolling deployments break routing for new pods; load not distributed evenly | `kubectl get pods -n kube-system -l k8s-app=kube-proxy` shows errors; new pods not receiving traffic despite being healthy; `iptables -t nat -L KUBE-SERVICES` not updated | Restart kube-proxy DaemonSet: `kubectl rollout restart ds/kube-proxy -n kube-system`; verify with `kubectl exec <pod> -- curl <service-ip>` |
| Cluster autoscaler node provision failure | Pending pods unable to schedule; Spot capacity interrupted; ASG fails to launch | All workloads that need to scale out queue up as pending pods; deployment rollouts stall | `kubectl get events -A | grep -i "FailedScheduling"`; CA logs: `failed to build node from template: InsufficientInstanceCapacity`; ASG activity: `Launching a new EC2 instance failed` | Add more instance types to CA node group; switch to On-Demand temporarily; manually scale ASG: `aws autoscaling set-desired-capacity` |
| Certificate expiry (kubeconfig or API server cert) | kubectl returns `x509: certificate has expired`; all API clients fail authentication; CI/CD pipelines break | All cluster management operations fail; deployments stop; HPA cannot function | `openssl s_client -connect <api-server>:443 </dev/null 2>/dev/null | openssl x509 -noout -dates`; `kubectl get nodes` returns `certificate has expired or is not yet valid` | For EKS: AWS manages API server cert (auto-rotated); for worker node bootstrap cert: rotate via `aws eks update-kubeconfig` and rolling node replacement |
| AWS Load Balancer Controller crash | New Ingress and Service LoadBalancer resources not provisioned; existing ALBs continue working | New service deployments have no external endpoint; no-traffic alerts for new services; scale-out adds pods not reachable externally | `kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-load-balancer-controller` shows errors; Ingress `ADDRESS` field empty; controller logs: `reconcile error` | Restart controller: `kubectl rollout restart deployment/aws-load-balancer-controller -n kube-system`; check IRSA role permissions for `elasticloadbalancing:*` |
| VPC CNI (aws-node) DaemonSet pod failure | New pods on affected node cannot get IP addresses; pod stuck in `ContainerCreating`; existing pods unaffected | New pods on affected node fail to start; node effective capacity = 0 new pods | `kubectl get pods -n kube-system -l k8s-app=aws-node` shows CrashLoopBackOff; `kubectl logs -n kube-system <aws-node-pod>`; pods stuck in `ContainerCreating` with `NetworkPlugin cni failed to set up pod` | Restart aws-node DaemonSet on affected node: `kubectl delete pod -n kube-system <aws-node-pod-for-node>`; check EC2 metadata service connectivity from node |
| Webhook admission controller timeout | All `kubectl apply` / pod creation blocked; cluster effectively read-only for mutations | No new pods start; deployments stall; HPA scaling fails; entire cluster mutation halted | `kubectl apply` takes > 30s then fails; API server audit logs: `timeout calling webhook`; `kubectl get mutatingwebhookconfigurations` shows stale webhook | Temporarily disable problematic webhook: `kubectl patch mutatingwebhookconfiguration <name> --type=json -p '[{"op":"replace","path":"/webhooks/0/failurePolicy","value":"Ignore"}]'`; fix webhook backend |
| Spot node interruption storm (mass simultaneous eviction) | Many pods simultaneously evicted from Spot nodes; scheduler overwhelmed with pending pods; PDBs may block eviction | Services go below minimum replicas if PDB and capacity constraints conflict; cascading application errors | Multiple EC2 `spot-interruption-notice` events in CloudWatch; `kubectl get events -A | grep -i evict`; ASG activity log shows rapid terminations | Ensure On-Demand node pool has capacity for critical workloads; use `karpenter.sh/capacity-type: on-demand` tolerations on critical pods; pre-scale On-Demand ASG |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| EKS control plane version upgrade | Deprecated API versions (`extensions/v1beta1 Ingress`) rejected; existing manifests fail to apply; custom controllers break | Immediately after upgrade for removed APIs; gradually as controllers try to reconcile | `kubectl api-versions` no longer shows removed groups; `kubectl apply` errors: `no matches for kind "Ingress" in version "extensions/v1beta1"`; correlate with upgrade timestamp | Update manifests to use current API versions; run `kubectl convert` for migration; check EKS deprecation notices for version |
| Node group AMI update (rolling node replacement) | Rolling replacement evicts pods; if insufficient capacity during drain, PDBs block eviction; service degraded | During rolling update window | `kubectl get nodes -o wide` shows mixed AMI versions; `kubectl drain` stuck waiting; `kubectl get events` shows `Evicting pod... PodDisruptionBudget` | Check PDB allowances: `kubectl get pdb -A`; temporarily relax PDB `minAvailable`; ensure enough capacity before drain |
| CoreDNS ConfigMap modification | DNS resolution behavior changes; custom domains stop resolving; or `forward` plugin misconfigured causing all DNS to fail | Immediately after `kubectl apply` on CoreDNS ConfigMap | CoreDNS logs: `plugin/forward: no nameservers found`; `kubectl exec <pod> -- nslookup google.com` fails; correlate with ConfigMap change via `kubectl get events -n kube-system` | Revert CoreDNS ConfigMap: `kubectl edit configmap coredns -n kube-system`; restart CoreDNS: `kubectl rollout restart deployment/coredns -n kube-system` |
| IRSA (IAM Roles for Service Accounts) annotation change | Pod loses AWS IAM access; `AssumeRoleWithWebIdentity` fails; S3/DynamoDB/SQS calls return `AccessDenied` | Immediately after pod restart with updated service account | Application logs: `WebIdentityErr: failed to retrieve credentials`; CloudTrail: `AssumeRoleWithWebIdentity` denied; correlate pod restart time with annotation change | Restore service account annotation: `kubectl annotate serviceaccount <sa> eks.amazonaws.com/role-arn=<correct-arn> --overwrite`; restart pods |
| Network policy addition (default deny) | Services that worked before lose connectivity; inter-pod traffic blocked; external ingress fails | Immediately after `kubectl apply` on NetworkPolicy | `kubectl exec <pod> -- curl <service>` returns `Connection refused`; `kubectl get networkpolicies -A` shows new deny-all policy; application error logs show connection failures | Delete or modify restrictive NetworkPolicy: `kubectl delete networkpolicy <name> -n <ns>`; add explicit allow rules before applying deny-all |
| HPA metric source change (CPU → custom metric) | HPA stops scaling; either over-provisioned (no scale-in) or under-provisioned (no scale-out) | After HPA spec update | `kubectl describe hpa <name>` shows `unable to get metric <name>: ...`; `kubectl get events` shows `FailedGetScale`; HPA status `ScalingActive: False` | Revert HPA to CPU-based scaling; verify custom metric adapter running: `kubectl get pods -n monitoring -l app=prometheus-adapter`; confirm metric exists: `kubectl get --raw /apis/custom.metrics.k8s.io/v1beta1` |
| Storage class change (gp2 → gp3) | Existing PVCs not migrated; new PVCs use gp3 but old PVCs stay on gp2; mixed storage performance | New PVC creation only; existing workloads unaffected unless re-provisioned | `kubectl get pv -o jsonpath='{.items[*].spec.storageClassName}'` shows mix; `aws ec2 describe-volumes --filters Name=tag:kubernetes.io/created-for/pvc/name,Values=<pvc>`; volume type diverges | For new PVCs: default StorageClass updated correctly; for existing: migrate manually with VolumeSnapshot and restore; or use aws-ebs-csi-driver volume modifier |
| Karpenter NodePool constraint update | Nodes with now-forbidden instance types are drained and not replaced with valid types; pods pile up as pending | After NodePool CRD update | `kubectl get nodes -l karpenter.sh/provisioner-name=<pool>` count drops; `kubectl get pods -A --field-selector=status.phase=Pending`; Karpenter logs: `no instance type satisfies constraints` | Revert NodePool configuration: `kubectl edit nodepool <name>`; restore allowed instance types or relax constraints; ensure On-Demand fallback available |
| EKS add-on version update (VPC CNI, coredns, kube-proxy) | Add-on update performs rolling restart; brief disruption during update; new version may have incompatibility with workloads | During add-on update window | `aws eks describe-addon --cluster-name <c> --addon-name vpc-cni` shows `UPDATING` status; pod events show restarts; correlate with `aws eks update-addon` CloudTrail event | For EKS add-ons: `aws eks update-addon --cluster-name <c> --addon-name <name> --addon-version <prev-version>` to roll back |
| Istio / service mesh sidecar injection label change | Pods no longer get sidecar injected; mTLS breaks; traffic bypasses mesh policies | After namespace label or MutatingWebhookConfiguration change | `kubectl get pods -n <ns> -o jsonpath='{.items[*].spec.containers[*].name}'` — missing `istio-proxy`; service-to-service 403/SSL errors; correlate with webhook config change | Restore namespace label: `kubectl label namespace <ns> istio-injection=enabled`; restart affected pods to inject sidecars |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| etcd leader election storm (frequent leader changes) | `kubectl get --raw /metrics | grep etcd_server_leader_changes_seen_total` | API server latency spikes repeatedly; watches drop and reconnect; controllers briefly stall | Transient errors for all kubectl operations; HPA and other controllers miss events | EKS manages etcd — monitor via AWS support; reduce API server load; check network latency between etcd members |
| Kubeconfig pointing to wrong cluster after context switch | `kubectl config current-context`; `kubectl cluster-info` | Operator applies changes to wrong cluster; resources created in non-production cluster | Production changes missed; non-production cluster polluted with prod-scale resources | Always verify: `kubectl config current-context` before any write operation; use `kubectx` with cluster name aliases; set PS1 to show kube context |
| Node clock skew causing pod scheduling failures | `kubectl get nodes -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].message}'` — check for `clock skew` messages | `Certificate signed by unknown authority` errors; Lease renewal failures; kubelet unable to authenticate | Pods fail to schedule; running pods lose connectivity to API server | Sync clocks: `sudo chronyc makestep` on affected nodes; verify: `chronyc tracking | grep offset`; rolling node replacement for persistent skew |
| ConfigMap/Secret version skew between pods | `kubectl get pods -n <ns> -o jsonpath='{.items[*].metadata.annotations}'` — check mounted config versions | Pods started before ConfigMap update use old config; pods started after use new config; A/B behavior | Application inconsistency; hard-to-debug issues where behavior depends on pod start time | Use immutable ConfigMaps with version in name; rolling restart to ensure all pods pick up latest: `kubectl rollout restart deployment/<name>` |
| Persistent Volume stuck in Terminating | `kubectl get pv` shows `Terminating` with age > 5m; `kubectl describe pv <pv>` shows finalizer `kubernetes.io/pv-protection` | PVC/PV cannot be deleted; storage not released; new PVC bindings affected | Storage capacity leak; pod using new PVC may fail if storage class quota exhausted | Remove finalizer: `kubectl patch pv <pv> -p '{"metadata":{"finalizers":[]}}' --type=merge`; verify underlying EBS volume state with `aws ec2 describe-volumes` |
| Endpoint slice drift after service annotation change | `kubectl get endpointslices -n <ns> -l kubernetes.io/service-name=<svc> -o yaml` | Service endpoints not updated; old pods still in routing table after replacement | Traffic routed to terminated pods; `Connection refused` from clients | Force endpoint reconciliation: `kubectl delete endpointslices -n <ns> -l kubernetes.io/service-name=<svc>`; kube-proxy will recreate; verify with `kubectl exec <pod> -- curl <service>` |
| Multiple admission webhook handling same resource (policy conflict) | `kubectl get mutatingwebhookconfigurations` and `kubectl get validatingwebhookconfigurations` | Resource rejected by one webhook but accepted by another; behavior depends on webhook ordering | Non-deterministic resource creation; debugging impossible without webhook logs | Audit all webhooks: check `namespaceSelector` and `objectSelector`; ensure webhooks have non-overlapping scopes; set explicit `reinvocationPolicy` |
| PodDisruptionBudget misconfiguration blocking all eviction | `kubectl get pdb -A`; check `ALLOWED DISRUPTIONS` column | Node drain hangs indefinitely; rolling update never progresses; Cluster Autoscaler cannot scale down | Cluster management operations blocked; cannot deprovision nodes; CA incurs costs of idle nodes | Temporarily patch PDB: `kubectl patch pdb <name> -n <ns> -p '{"spec":{"minAvailable":0}}'`; complete drain; restore PDB; fix PDB to allow at least 1 disruption |
| Namespace stuck in Terminating due to finalizer | `kubectl get namespace <ns> -o jsonpath='{.metadata.finalizers}'`; `kubectl api-resources --verbs=list --namespaced -o name | xargs -I{} kubectl get {} -n <ns> --no-headers 2>/dev/null` | All resources in namespace deleted but namespace stays in Terminating; CRD finalizer holding | Namespace occupies cluster state; new namespace with same name cannot be created | Identify blocking resource; remove finalizer from blocking CRD instance; or use Kubernetes proxy: `kubectl get namespace <ns> -o json | python3 -c "import sys,json; d=json.load(sys.stdin); d['spec']['finalizers']=[]; print(json.dumps(d))" | kubectl replace --raw /api/v1/namespaces/<ns>/finalize -f -` |
| Horizontal Pod Autoscaler and KEDA ScaledObject both targeting same deployment | `kubectl get hpa,scaledobject -n <ns>` | Conflicting scale decisions; HPA scales up, KEDA scales down (or vice versa); workload thrashes | Continuous scale churn; unpredictable replica count; resource waste | Remove HPA if KEDA is managing scale; KEDA creates its own HPA internally; never run both on the same deployment |
| VPC CNI IP exhaustion (no free IPs in subnet) | `kubectl get nodes -o custom-columns=NODE:.metadata.name,ALLOCATABLE_PODS:.status.allocatable.pods` | Nodes show 0 allocatable pods; new pods stuck in `ContainerCreating`; CNI logs: `no more IP addresses available` | Cannot schedule any new pods on affected nodes; deployments stall | Enable prefix delegation for VPC CNI: `kubectl set env daemonset aws-node -n kube-system ENABLE_PREFIX_DELEGATION=true`; add larger subnet to node group; increase IP limits |

## Runbook Decision Trees

### Decision Tree 1: Pods Stuck in Pending State

```
How long has the pod been Pending?
├── < 2 min → Normal scheduling latency (especially Karpenter node provisioning) — wait
└── > 2 min → kubectl describe pod <name> -n <ns> — check Events section
              Is there a "0/N nodes are available" message?
              ├── YES → What is the constraint reason?
              │         ├── "Insufficient cpu" / "Insufficient memory" → Resource pressure
              │         │     Check node capacity: kubectl top nodes
              │         │     ├── Nodes at capacity → Scale nodegroup: aws eks update-nodegroup-config or wait for CA/Karpenter
              │         │     └── Nodes have headroom but pod won't fit → Pod requests too high; reduce requests or use larger instance type
              │         ├── "node(s) had untolerated taint" → Pod missing toleration for taint
              │         │     Fix: kubectl get nodes -o json | jq '.items[].spec.taints'; add matching toleration to pod spec
              │         ├── "node(s) didn't match Pod's node affinity/selector" → Affinity mismatch
              │         │     Fix: kubectl get nodes --show-labels; update nodeSelector or affinity rules
              │         └── "0/N nodes are available: N node(s) had volume node affinity conflict" → PV AZ mismatch
              │               Fix: delete PVC and re-create in correct AZ; or use topology-aware volume provisioning
              └── NO → Is there a "didn't match pod anti-affinity rules" message?
                        ├── YES → Too many replicas for available nodes given anti-affinity
                        │         Fix: reduce replicas; or add nodes in more AZs
                        └── NO → Is the pod waiting for a PVC to bind?
                                  kubectl get pvc -n <ns>
                                  ├── PVC Pending → Storage provisioner issue; check StorageClass: kubectl get storageclass; check aws-ebs-csi-driver pods
                                  └── PVC Bound → Likely scheduler extender or webhook blocking; check: kubectl get events -n <ns> | grep Warning
```

### Decision Tree 2: Pod CrashLoopBackOff

```
kubectl describe pod <name> -n <ns> — check last state exit code
├── Exit 137 (OOM Kill) →
│     kubectl top pod <name> -n <ns> — is memory near limit?
│     ├── YES → Increase memory limit in deployment spec; or fix memory leak
│     └── NO → Node-level OOM (kernel killed process): kubectl get events -n <ns> | grep OOMKill; check node memory pressure
│               Fix: increase node memory; add memory request/limit buffer
├── Exit 1 (Application crash) →
│     kubectl logs <name> -n <ns> --previous — read last logs before crash
│     ├── Config / env var error → Fix config; update ConfigMap or Secret; rollout restart
│     ├── Dependency unreachable (DB, external API) → Check service DNS: kubectl exec <pod> -- nslookup <service>; check NetworkPolicy
│     └── Application bug → Roll back: kubectl rollout undo deployment/<name> -n <ns>
├── Exit 2 (Misuse of shell built-in / missing executable) →
│     kubectl exec <pod> -- ls /path/to/binary — verify binary exists in image
│     Fix: rebuild image with correct entrypoint; update deployment image tag
└── Exit 0 (Process exits cleanly — not a daemon) →
      Verify entrypoint keeps process in foreground; check Dockerfile CMD vs ENTRYPOINT
      Fix: wrap in `exec <command>`; ensure process does not daemonize
```

## Cost & Quota Runaway Patterns
| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Cluster Autoscaler over-provisioning nodes due to pending pods with large requests | Workload sets CPU requests far above actual usage; CA sees pending pods and adds nodes continuously | `kubectl top nodes`; `kubectl describe nodes | grep -A5 "Allocated resources"`; compare `requests` vs `limits` vs `actual` | EC2 cost spike; risk of hitting account vCPU quota | Cordon over-provisioned nodes: `kubectl cordon <node>`; drain and terminate: `aws autoscaling terminate-instance-in-auto-scaling-group --instance-id <id>` | Set VPA in recommendation mode; enforce requests ≤ 2× actual average; use LimitRange to cap requests |
| Karpenter consolidation disabled allowing idle nodes to accumulate | `consolidationPolicy: WhenEmpty` not set or TTL too long; nodes sit idle after workload scale-down | `kubectl get nodes -o custom-columns=NODE:.metadata.name,PODS:.status.capacity.pods`; `kubectl get nodeclaims` | Idle EC2 nodes billed; wasted capacity | Enable consolidation: `kubectl edit nodepool <name>` — set `consolidationPolicy: WhenUnderutilized`; manually delete idle NodeClaims | Always configure `consolidation` in NodePool spec; set `expireAfter` for long-running nodes; alert on utilization < 10% sustained |
| Spot interruption storm triggering rapid On-Demand fallback at high price | Spot capacity withdrawal in AZ; Karpenter/CA falls back to On-Demand instance types; large fleet suddenly On-Demand | `kubectl get nodes -l karpenter.sh/capacity-type=on-demand`; `aws ec2 describe-instances --filters Name=instance-lifecycle,Values=normal` | 3–10× higher EC2 cost during fallback | Accept cost for now; rebalance to Spot when available: delete On-Demand NodeClaims after Spot capacity returns | Diversify Spot instance types across 5+ types; set Spot budget alarm; use Savings Plans for baseline On-Demand capacity |
| Persistent Volume (EBS) orphan after PVC deletion | PVC deleted but `reclaimPolicy: Retain` on StorageClass; EBS volumes remain and accrue charges | `aws ec2 describe-volumes --filters Name=status,Values=available --query 'Volumes[*].{id:VolumeId,size:Size,created:CreateTime}'` | EBS gp3 cost $0.08/GB/month for unused volumes | Delete orphaned volumes after verifying data not needed: `aws ec2 delete-volume --volume-id <id>` | Use `reclaimPolicy: Delete` for non-critical PVCs; add Lambda/EventBridge rule to alert on available EBS volumes older than 24 h |
| Namespace resource quota absent — runaway pod/PVC creation from misconfigured operator | Operator bug creates unlimited pods or PVCs; no quota to stop it | `kubectl get pods -A --no-headers | wc -l`; `kubectl get pvc -A --no-headers | wc -l`; compare with expected baseline | Exhaust cluster pod CIDR; hit EC2 instance ENI IP limits; spike EC2 + EBS costs | Scale down offending operator: `kubectl scale deployment <operator> -n <ns> --replicas=0`; bulk delete orphaned resources | Apply ResourceQuota to every namespace; alert on namespace pod count > threshold |
| Prometheus scrape interval too low causing etcd and API server metric flood | scrapeInterval set to 5 s on kube-state-metrics or API server; generates enormous metric volume | `kubectl exec -n monitoring <prometheus-pod> -- curl -s http://localhost:9090/metrics | grep prometheus_tsdb_head_samples_appended_total` | Prometheus storage explosion; TSDB WAL pressure; high memory on Prometheus pod | Increase scrapeInterval to 30 s: edit Prometheus scrape config or ServiceMonitor; restart Prometheus pod | Default scrapeInterval 30 s; use recording rules for high-cardinality queries; enable Prometheus remote_write to Thanos/Cortex |
| VPC CNI IPAMD pre-warming too many IPs per node | `WARM_IP_TARGET` or `WARM_ENI_TARGET` set too high; each node pre-warms 20+ IPs consuming subnet space | `kubectl get ds -n kube-system aws-node -o yaml | grep -A5 WARM_IP`; `aws ec2 describe-network-interfaces --filters Name=status,Values=in-use` | VPC subnet IP exhaustion; no IPs left for new pods on other nodes | Reduce warming: `kubectl set env daemonset aws-node -n kube-system WARM_IP_TARGET=2 MINIMUM_IP_TARGET=2` | Set `WARM_IP_TARGET=2` in production; use prefix delegation for IP efficiency; monitor subnet available IP count |
| ALB Ingress controller creating one ALB per Ingress (instead of shared) | `alb.ingress.kubernetes.io/group.name` annotation missing; each Ingress gets its own ALB | `aws elbv2 describe-load-balancers --query 'LoadBalancers[*].{arn:LoadBalancerArn,name:LoadBalancerName}' | wc -l` vs expected | ALB cost ~$16/month each; unused ALBs from deleted Ingresses may remain | Add `alb.ingress.kubernetes.io/group.name: shared` to all Ingress resources; AWS Load Balancer Controller will consolidate | Enforce IngressGroup annotation in admission webhook or OPA policy; add ALB count budget alert |
| ECR image pull charges from cross-region EKS workloads | EKS cluster in us-west-2 pulling images from ECR in us-east-1; no pull-through cache or replication | `aws cloudwatch get-metric-statistics --namespace AWS/ECR --metric-name DataTransfer`; check ECR repository region vs EKS cluster region | ECR data transfer $0.09/GB cross-region at scale | Set up ECR pull-through cache or ECR replication to local region: `aws ecr create-replication-configuration` | Deploy ECR repositories in same region as EKS; use `registry.k8s.io` for standard images; enable ECR pull-through cache for Docker Hub images |
| Excessive CloudWatch Logs from FluentBit/Fluentd sidecar verbose logging | Log level DEBUG on FluentBit; every HTTP request logged; 10k pods × 100 lines/s = 1 M lines/s | `aws cloudwatch get-metric-statistics --namespace AWS/Logs --metric-name IncomingBytes`; `kubectl logs -n logging <fluentbit-pod> | grep -c DEBUG` | CloudWatch Logs ingestion $0.50/GB; can easily reach $1k+/day on large clusters | Update FluentBit ConfigMap log level to WARN: `kubectl edit configmap fluent-bit-config -n logging`; restart DaemonSet | Set `log_level warn` in FluentBit config by default; use sampling for high-volume log streams; set retention on all log groups |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot node causing pod scheduling imbalance | One node at 90% CPU; others at 20%; new pods always scheduled to hot node | `kubectl top nodes`; `kubectl describe nodes \| grep -A5 "Non-terminated Pods"`; `kubectl get pods -o wide \| sort -k7` | Missing pod anti-affinity rules; Cluster Autoscaler bin-packing too aggressively | Add `podAntiAffinity` rules to spread replicas across nodes; adjust CA bin-packing: `--balance-similar-node-groups=true` |
| API server connection pool exhaustion | `kubectl` commands slow or timeout; pods stuck in `ContainerCreating`; EKS API server logs `connection refused` | `kubectl get --raw /metrics \| grep apiserver_current_inflight_requests`; `aws cloudwatch get-metric-statistics --namespace AWS/EKS --metric-name apiserver_request_total --period 60 --statistics Sum` | High API server request rate from malfunctioning controller or operator; watch connections not released | Identify rogue controller: `kubectl get --raw /metrics \| grep "apiserver_watch_events_total"` by resource; scale down offending controller; increase API server rate limits via EKS config |
| etcd GC pause causing API server latency spikes | All `kubectl` operations slow simultaneously every few minutes; API server logs `etcd request took too long` | `kubectl get --raw /metrics \| grep etcd_request_duration_seconds`; CloudWatch EKS `etcd_db_total_size_in_bytes` metric | etcd compaction and defragmentation pausing; large etcd DB from excessive CRD/event objects | Reduce etcd object count: `kubectl delete events -A --field-selector reason=BackOff`; tune etcd compaction; enable etcd metrics alarm at 2 GB DB size |
| Kubernetes controller-manager thread pool saturation | Deployment rollouts stall; ReplicaSet scaling delayed; controller logs show `workqueue full` | `kubectl logs -n kube-system kube-controller-manager-<node> \| grep -i "queue\|workers"`; check `kubectl get --raw /metrics \| grep workqueue_depth` | Default controller-manager worker count too low for cluster scale; high churn in deployments | For self-managed: increase `--concurrent-deployment-syncs`, `--concurrent-replicaset-syncs`; for EKS: scale number of managed node groups to reduce state per manager |
| Slow pod scheduling from large number of pending pods | Thousands of pods in `Pending` state; scheduler log shows `scheduling cycle taking > 100ms per pod` | `kubectl get pods -A --field-selector=status.phase=Pending \| wc -l`; `kubectl logs -n kube-system kube-scheduler-<node> \| grep "scheduling_algorithm_duration"` | Scheduler throughput limit; filter plugins evaluating all nodes for each pod | Enable parallel pod scheduling; add node affinity to narrow candidate nodes; use Karpenter for faster scheduling via NodeClaim API |
| CPU steal on EC2 managed node group | Workload performance inconsistent; node CPU steal > 5% | `kubectl debug node/<node> -it --image=ubuntu -- mpstat 1 5 \| grep steal`; CloudWatch EC2 `CPUCreditBalance` for T-series instances | T-series burstable instances credit exhausted; co-tenant noisy neighbors | `kubectl cordon <node> && kubectl drain <node>`; replace with C5/M5 node group: `aws eks update-nodegroup-config --cluster-name $CLUSTER --nodegroup-name $NG` |
| Kubernetes watch connection thrashing | API server memory grows; etcd watch fan-out overwhelming; many controllers reconnecting rapidly | `kubectl get --raw /metrics \| grep apiserver_watch_cache_capacity`; count active watches: `kubectl get --raw /metrics \| grep watch_cache_total` | Watch connection leak in controller; operator creating/deleting many objects rapidly causing watch event storm | Identify watch-heavy clients: `kubectl get --raw /api/v1/namespaces/kube-system/pods/<api-server-pod>/log \| grep "watch timeout"` — look for disconnecting clients; patch controller or install rate limiter |
| CoreDNS serialization overhead under high DNS QPS | Pod DNS lookups slow; NXDOMAIN and valid responses both slow; CoreDNS CPU high | `kubectl top pods -n kube-system -l k8s-app=kube-dns`; `kubectl exec -n kube-system <coredns-pod> -- cat /etc/coredns/Corefile`; check `cache` plugin TTL | CoreDNS replicas too few for cluster DNS QPS; no `cache` plugin configured | Scale CoreDNS: `kubectl scale deployment coredns -n kube-system --replicas=5`; add cache plugin: add `cache 30` to Corefile; enable `autopath` for pod DNS optimization |
| Large ConfigMap/Secret causing API server slow list operations | `kubectl get configmaps -A` takes minutes; admission webhook timeouts; etcd list operations slow | `kubectl get --raw /metrics \| grep etcd_object_counts`; list large objects: `kubectl get configmaps -A -o json \| jq '.items[] \| {name:.metadata.name,size:(.data // {} \| tostring \| length)} \| select(.size > 1048576)'` | Individual ConfigMaps > 1 MB (etcd max is 1.5 MB per object); etcd key space polluted | Split large ConfigMaps; move binary data to S3; clean up orphaned ConfigMaps from old Helm releases: `kubectl get configmap -A \| grep sh.helm.release` |
| Downstream AWS API latency cascading into kube-controller-manager | EBS CSI provisioner slow; ELB ingress controller slow; all AWS-backed resources take minutes to provision | `kubectl get --raw /metrics \| grep cloudprovider_aws_api_request_duration_seconds`; CloudWatch: `AWS/ApiGateway` or individual service latency | AWS regional API degradation; rate limiting on AWS APIs for the account; insufficient IAM throttle limits | Check AWS Health Dashboard: `aws health describe-events --filter '{"services":["EC2","ELB"]}'`; implement exponential backoff in controllers; use AWS API retry mode `adaptive` |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry on Ingress (cert-manager) | Browser shows certificate expired; `kubectl get certificate -A` shows `False` for `READY` | `kubectl describe certificate <cert-name> -n <ns>`; `echo \| openssl s_client -connect <domain>:443 2>/dev/null \| openssl x509 -noout -dates`; `kubectl logs -n cert-manager deploy/cert-manager \| grep -i "error\|renew"` | All HTTPS traffic to affected Ingress fails; 100% request failure | Manually trigger renewal: `kubectl delete certificate <cert-name> -n <ns>`; or `kubectl annotate certificate <cert-name> -n <ns> cert-manager.io/issue-temporary-certificate=true`; check cert-manager ACME solver pods |
| mTLS rotation failure in Istio service mesh | Service-to-service calls fail with `CERTIFICATE_VERIFY_FAILED`; direct pod-to-pod HTTP still works | `istioctl proxy-config secret <pod>.<ns>`; `kubectl exec <pod> -n <ns> -- openssl s_client -connect <service>:443 2>&1 \| grep -i "cert\|verify"`; `kubectl get secret istio-ca-secret -n istio-system -o yaml` | All Istio-managed mTLS service-to-service calls fail; service mesh traffic broken | Rotate Istio CA: `kubectl delete secret istio-ca-secret -n istio-system`; Istiod auto-regenerates; rolling restart Istiod: `kubectl rollout restart deployment/istiod -n istio-system` |
| CoreDNS DNS resolution failure for external services | Pods cannot resolve external hostnames; `nslookup kubernetes.default.svc.cluster.local` works but `nslookup google.com` fails | `kubectl exec <pod> -- nslookup google.com`; `kubectl logs -n kube-system -l k8s-app=kube-dns`; check CoreDNS Corefile for `forward` plugin | CoreDNS `forward` plugin upstream DNS server unreachable; VPC DNS (169.254.169.253) blocked by security group or NACL | Check security group on CoreDNS pods allows outbound UDP/53; verify VPC DNS: `aws ec2 describe-vpc-attribute --vpc-id $VPC_ID --attribute enableDnsSupport`; restart CoreDNS: `kubectl rollout restart deployment/coredns -n kube-system` |
| TCP connection exhaustion from pod to AWS services via NAT | Pods cannot connect to S3 or other AWS services; `connect: cannot assign requested address` | `kubectl exec <pod> -- ss -tan state time-wait \| wc -l`; CloudWatch `NatGatewayPacketDropCount`; `aws ec2 describe-nat-gateways --filter Name=state,Values=available` | All outbound connections from affected pods fail; service unable to reach AWS APIs | Add VPC Gateway Endpoints (S3, DynamoDB — free): `aws ec2 create-vpc-endpoint --vpc-id $VPC_ID --service-name com.amazonaws.$REGION.s3 --route-table-ids $RTB`; enable `tcp_tw_reuse` on nodes |
| AWS Load Balancer Controller misconfiguration creating duplicate ALBs | Multiple ALBs created for same Ingress; traffic split between them; inconsistent behavior | `aws elbv2 describe-load-balancers --query 'LoadBalancers[*].{name:LoadBalancerName,arn:LoadBalancerArn}'`; `kubectl get ingress -A -o wide` | ALB cost doubled; DNS points to one ALB; traffic to other ALB lost | Identify duplicate: delete one ALB; `aws elbv2 delete-load-balancer --load-balancer-arn <dup-arn>`; add `alb.ingress.kubernetes.io/group.name` annotation to consolidate |
| VPC CNI packet loss causing intermittent pod-to-pod failures | Random pod-to-pod connection failures; no consistent pattern; `ping` shows 1–5% packet loss | `kubectl exec <pod-a> -- ping -c 100 <pod-b-ip> \| tail -3`; check node interface stats: `kubectl debug node/<node> -it --image=ubuntu -- ethtool -S eth0 \| grep -i "drop\|error"` | EC2 network interface hardware issue; ENI attachment problem; AWS underlying infrastructure event | Check AWS Health Dashboard; replace affected EC2 node: `kubectl drain <node>`; terminate instance: `aws ec2 terminate-instances --instance-ids <id>`; CA/Karpenter will provision replacement |
| MTU mismatch in EKS VPC CNI causing TCP hangs | Large HTTP responses hang; small requests succeed; `wget` of large file stalls | `kubectl exec <pod> -- ip link show eth0 \| grep mtu`; test: `kubectl exec <pod> -- curl -o /dev/null http://example.com/large-file -w "time_total: %{time_total}\n"`; compare MTU on pod vs node | Pod MTU (1500) conflicts with VPC jumbo frames (9001) or overlay network MTU | Set VPC CNI MTU: `kubectl set env daemonset aws-node -n kube-system AWS_VPC_K8S_CNI_MTU=1500`; or match entire path MTU |
| AWS Security Group rule change blocking control plane to node communication | Nodes go `NotReady`; kubelet unable to reach API server; EKS shows `DEGRADED` | CloudTrail: `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=RevokeSecurityGroupIngress --start-time <ts>`; `aws ec2 describe-security-groups --group-ids <cluster-sg>` | Nodes cannot communicate with EKS control plane; pod scheduling and management stopped | Restore security group rule: EKS requires ports 443 and 10250 between cluster SG and node SG; `aws ec2 authorize-security-group-ingress --group-id <node-sg> --protocol tcp --port 10250 --source-group <cluster-sg>` |
| Istio Envoy SSL handshake timeout from expired SPIFFE certificate | Service mesh calls intermittently fail; Envoy proxy logs `CERTIFICATE_EXPIRED`; Istiod logs cert rotation errors | `istioctl proxy-status`; `kubectl exec <pod> -c istio-proxy -- pilot-agent request GET certs`; `kubectl logs -n istio-system deploy/istiod \| grep -i "cert\|expire"` | Istiod failed to rotate workload certificates before expiry; clock skew between pods | Force certificate rotation: `kubectl rollout restart deployment/<svc>` to trigger new Envoy cert request; restart Istiod: `kubectl rollout restart deployment/istiod -n istio-system` |
| VPC Flow Log showing rejected packets on pod CIDR | Pods losing connections to specific IP range; Flow Logs show `REJECT` for pod CIDR traffic | `aws ec2 describe-flow-logs --filter Name=resource-id,Values=$VPC_ID`; query Flow Logs in CloudWatch: `filter action="REJECT" and srcAddr like "10.x.x.x"` — match pod CIDR | NetworkPolicy misconfiguration blocking legitimate pod traffic; or NACL added for security that blocks pod-to-pod | Review NetworkPolicies: `kubectl get networkpolicies -A`; use `kubectl exec <pod> -- curl <dest>` to test; check NACL: `aws ec2 describe-network-acls --filters Name=association.subnet-id,Values=$SUBNET` |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of application pod | Pod restarts; `kubectl describe pod` shows `OOMKilled`; `exit code 137` | `kubectl describe pod <pod> -n <ns> \| grep -A5 "Last State"`; `kubectl top pods -n <ns>`; check `container_memory_working_set_bytes` in Prometheus | Increase memory limit: `kubectl set resources deployment <name> -n <ns> --limits=memory=2Gi`; investigate memory leak: `kubectl exec <pod> -- jmap -histo $(pgrep java) \| head -30` | Set memory requests = P95 observed; limits = requests × 1.5; enable VPA in recommendation mode; alert at 80% memory utilization |
| etcd data directory disk full | API server returns 500 for all write operations; etcd logs `mvcc: database space exceeded` | `kubectl exec -n kube-system etcd-<master> -- df -h /var/lib/etcd`; etcd metric: `etcd_mvcc_db_total_size_in_bytes` | etcd compaction not running; large number of events or CRD objects accumulated | Defragment etcd: `kubectl exec -n kube-system etcd-<master> -- etcdctl defrag --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key`; delete old events |
| Node disk full from container logs on pod data partition | Node shows `DiskPressure`; pod evictions start; new pods cannot be scheduled | `kubectl describe node <node> \| grep -A5 DiskPressure`; `kubectl debug node/<node> -it --image=ubuntu -- df -h`; `du -sh /var/log/containers/*` | Container log rotation not configured; verbose application logging; long-running pods without log limits | `kubectl debug node/<node> -it --image=ubuntu -- journalctl --vacuum-size=500M`; `find /var/log/containers -name "*.log" -size +100M -exec truncate -s 0 {} \;`; add log rotation in kubelet config |
| Kubernetes API server file descriptor exhaustion | API server unable to accept new connections; `too many open files` in API server logs | `kubectl logs -n kube-system kube-apiserver-<master> \| grep "too many open files"`; SSH to master: `ls /proc/$(pgrep kube-apiserver)/fd \| wc -l` | Each watch connection and etcd connection consumes fd; high watch connection count | EKS: AWS manages API server; open support ticket; self-managed: restart API server; increase `LimitNOFILE` in API server systemd unit | For self-managed EKS: set `LimitNOFILE=1048576` in kube-apiserver systemd unit; limit watch connections per client via API server rate limiting |
| Pod IP exhaustion in VPC subnet | New pods fail with `Failed to allocate IP`; existing pods unaffected | `aws ec2 describe-subnets --subnet-ids $SUBNET_ID \| jq '.Subnets[].AvailableIpAddressCount'`; `kubectl get nodes -o json \| jq '.items[].status.capacity."vpc.amazonaws.com/pod-eni"'` | Subnet too small; VPC CNI pre-warming IPs; cluster scaled beyond subnet capacity | Add additional subnet CIDR: `aws ec2 associate-vpc-cidr-block --vpc-id $VPC_ID --cidr-block 100.64.0.0/16`; enable prefix delegation: `kubectl set env daemonset aws-node -n kube-system ENABLE_PREFIX_DELEGATION=true` |
| Karpenter NodeClaim provisioning CPU quota exhaustion | New NodeClaims stuck in `Pending`; Karpenter logs `InsufficientCapacity` or `VCpuLimitExceeded` | `kubectl get nodeclaims`; `kubectl describe nodeclaim <name> \| grep -A5 "Status"`; `aws service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A` | EC2 account vCPU quota reached; Spot capacity unavailable in AZ; instance type not available | Request vCPU quota increase: `aws service-quotas request-service-quota-increase --service-code ec2 --quota-code L-1216C47A --desired-value 2000`; diversify instance types in NodePool spec |
| Prometheus TSDB disk exhaustion | Prometheus pod crashes with `disk full`; dashboards show `no data`; alerting stops | `kubectl exec -n monitoring <prometheus-pod> -- df -h /prometheus`; `kubectl get pvc -n monitoring`; Prometheus metric: `prometheus_tsdb_storage_blocks_bytes` | TSDB retention period too long; high cardinality metrics growing TSDB; PVC undersized | Increase PVC: `kubectl patch pvc prometheus-data -n monitoring -p '{"spec":{"resources":{"requests":{"storage":"200Gi"}}}}'`; reduce retention: `--storage.tsdb.retention.time=7d`; drop high-cardinality metrics via `metric_relabel_configs` |
| VPC CNI IPAMD socket exhaustion under high pod churn | New pods fail to get IPs; IPAMD logs `failed to get available IP address`; existing pods unaffected | `kubectl logs -n kube-system -l k8s-app=aws-node \| grep -i "error\|exhausted"`; `kubectl get pods -n kube-system -l k8s-app=aws-node -o wide` | High pod creation/deletion rate exhausting IPAMD socket connections; IPAMD cache stale | Restart aws-node DaemonSet on affected node: `kubectl delete pod -n kube-system <aws-node-pod-on-affected-node>`; reduce pod churn by increasing minReadySeconds on deployments | Set `MINIMUM_IP_TARGET` and `WARM_IP_TARGET` appropriately; upgrade VPC CNI to latest version; avoid rapid pod cycling in batch jobs |
| Kernel PID exhaustion from rapid pod spawning | Node cannot fork new processes; pod creation fails; existing pods unaffected | `kubectl debug node/<node> -it --image=ubuntu -- cat /proc/sys/kernel/pid_max`; `kubectl debug node/<node> -it --image=ubuntu -- ps aux \| wc -l` | High pod density with many processes per pod; or PID-heavy workloads (Java, Node) consuming per-pod PID budget | Cordon node: `kubectl cordon <node>`; drain: `kubectl drain <node> --ignore-daemonsets`; terminate and replace | Set kubelet `--pod-max-pids=32768`; audit per-pod process count; avoid running multiple heavy processes per container |
| Ephemeral port exhaustion on high-connection-rate service | Pods fail outbound connections; `cannot assign requested address`; happens on pods making many short-lived connections | `kubectl exec <pod> -- ss -tan state time-wait \| wc -l`; `kubectl exec <pod> -- sysctl net.ipv4.ip_local_port_range` | Service making many short-lived TCP connections (e.g., per-request HTTP without keep-alive); TIME_WAIT accumulation | Enable keep-alive on HTTP clients; tune: `kubectl exec <pod> -- sysctl -w net.ipv4.tcp_tw_reuse=1`; or patch node sysctl via DaemonSet; add connection pooling | Configure pod sysctl in pod spec: `spec.securityContext.sysctls: [{name: net.ipv4.tcp_tw_reuse, value: "1"}]`; use HTTP/2 connection multiplexing to reduce connection count |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation from Kubernetes controller reconcile loop re-applying | Controller reconciles object that was already acted upon; duplicate AWS resources created (ALBs, security groups, EBS volumes) | `kubectl get events -A \| grep -i "reconcil\|duplicate"`; `aws elbv2 describe-load-balancers \| jq '.LoadBalancers \| length'` vs expected; CloudTrail: duplicate `CreateLoadBalancer` calls within minutes | Duplicate AWS resources; doubled cost; split traffic; security group conflicts | Delete duplicate AWS resources; add idempotency check to controller using Kubernetes finalizers and status conditions; scale down controller, cleanup, scale up |
| Helm rollback saga failure leaving cluster in mixed revision state | `helm rollback` partially succeeds; some Kubernetes resources at old version, others at new; Helm status shows `failed` | `helm status <release> -n <ns>`; `helm history <release> -n <ns>`; `kubectl get all -n <ns> -o yaml \| grep "app.kubernetes.io/version"` — compare versions across resources | Application in inconsistent state; some pods serving old code, others new code | Full re-deploy: `helm upgrade <release> <chart> --version <target-version> --force -n <ns>`; use `--atomic` flag in future deployments to auto-rollback on failure |
| Kubernetes Operator reconcile loop processing stale CRD revision | CRD object updated twice rapidly; Operator processes version N+1 then replays version N from cache; applies old configuration | `kubectl get <crd-object> -n <ns> -o yaml \| grep resourceVersion`; `kubectl events --for <crd-resource>/<name> -n <ns>`; Operator logs: `grep "processing older version"` | CRD object stuck in outdated state; Operator continuously reconciling but applying wrong config | Force re-sync: `kubectl annotate <crd-resource> <name> -n <ns> reconcile/force=$(date +%s)`; restart Operator pod; check Operator informer cache resync interval |
| Cross-namespace Kubernetes resource dependency deadlock | Namespace A Service waits for Namespace B Secret; Namespace B waits for Namespace A ConfigMap; both in `Init:0/1` | `kubectl get pods -A \| grep Init`; `kubectl describe pod <blocked-pod> -n <ns> \| grep -A10 "Init Containers"`; check for circular ExternalSecret or Vault agent dependencies | Both namespaces fail to start; service completely unavailable | Manually create one of the bootstrapping resources to break the cycle: `kubectl create secret generic <name> -n <ns> --from-literal=key=value`; then both can start |
| Out-of-order Kubernetes rolling deployment causing mixed API version | Deployment rolling update started; new pods with API v2 start serving before all old pods (v1) are drained; v1 and v2 incompatible | `kubectl rollout status deployment/<name> -n <ns>`; `kubectl get pods -n <ns> -o jsonpath='{.items[*].spec.containers[*].image}'`; compare running image versions | API incompatibility between client expecting v1 and pods serving v2; or vice versa | Pause rollout: `kubectl rollout pause deployment/<name> -n <ns>`; evaluate: either complete rollout or rollback: `kubectl rollout undo deployment/<name> -n <ns>` |
| At-least-once Kubernetes Event delivery causing duplicate operator actions | Kubernetes controller re-processes event after API server restart; action already taken (e.g., PVC already created) | `kubectl get events -n <ns> --sort-by=.lastTimestamp`; look for duplicate event counts on same object; controller logs: `grep "already exists"` | Duplicate resources created or duplicate notifications sent; operator error count spikes | Add resource existence check before creation: `kubectl get <resource> <name> -n <ns>` before `kubectl apply`; implement controller with `CreateOrUpdate` pattern |
| Argo Workflow or Tekton Pipeline partial failure leaving workspace PVC orphaned | Pipeline step fails mid-execution; workspace PVC created but pipeline cleanup step never runs; PVC and EBS volume remain | `kubectl get pvc -A \| grep -v Bound`; `aws ec2 describe-volumes --filters Name=status,Values=available \| jq '.Volumes[] \| select(.Tags[]?.Value \| test("pipeline\|workflow"))'` | EBS volume cost accrues; PVC quota consumed; future pipeline runs may fail to create new workspace | `kubectl delete pvc <orphaned-pvc> -n <ns>`; `aws ec2 delete-volume --volume-id <vol-id>`; add pipeline teardown step with `when: always` condition |
| Distributed leader election failure causing dual-active controllers | Two controller replicas both believe they are leader; both reconciling same objects simultaneously | `kubectl get lease -n kube-system`; `kubectl describe lease <controller-name> -n kube-system \| grep -E "holder\|time"`; check two controller pods both logging "became leader" | Objects reconciled twice; conflicting updates; potential data corruption in managed resources | Scale controller to 0: `kubectl scale deployment <controller> -n <ns> --replicas=0`; delete lease object: `kubectl delete lease <name> -n kube-system`; scale back to 1; then to desired with leader election enabled |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: one namespace's pods monopolizing node CPU | `kubectl top nodes` shows nodes at 100%; `kubectl top pods -A --sort-by=cpu \| head -20` shows one namespace dominant; no CPU limits set | Other namespaces' pods throttled; latency spikes for all tenants sharing nodes | Immediately add CPU limit to noisy pods: `kubectl set resources deployment <name> -n <noisy-ns> --limits=cpu=500m`; cordon busy nodes: `kubectl cordon <node>` | Enforce CPU limits via LimitRange: `kubectl apply -n <ns> -f - <<EOF\napiVersion: v1\nkind: LimitRange\nmetadata:\n  name: cpu-limits\nspec:\n  limits:\n  - max:\n      cpu: "2"\n    type: Container\nEOF`; use namespace ResourceQuota |
| Memory pressure from adjacent namespace triggering node-level OOM killer | Node OOM killer terminates pods from other namespaces; `kubectl describe node <node> \| grep -A5 "OOMKilled"`; `dmesg \| grep "Out of memory"` via node debug | Innocent tenant's pods killed by OS OOM killer; unexpected restarts; data loss in stateful pods | Drain affected node: `kubectl drain <node> --ignore-daemonsets`; identify the OOM source: check `container_memory_working_set_bytes` in Prometheus per namespace | Set namespace-level ResourceQuota with memory limits: `kubectl apply -f - -n <ns>` with `spec.hard.limits.memory: 4Gi`; enable Kubernetes OOM score adjustment to prioritize critical pods |
| Disk I/O saturation: namespace running ETL jobs starving other workloads | Node disk I/O at 100%; `kubectl debug node/<node> -it --image=ubuntu -- iostat -x 1 5 \| awk '/sda/{print $NF}'`; ETL pods writing large files to node-local storage | Database-backed workloads on same node experience slow I/O; query latency spikes | Evict ETL pods: `kubectl delete pod -n <etl-ns> -l app=etl`; cordon ETL node group: `kubectl cordon <node>` | Add node selectors/taints to isolate ETL workloads: `kubectl taint node <etl-nodes> workload=etl:NoSchedule`; ETL jobs add `tolerations` and `nodeSelector` for dedicated nodes |
| Network bandwidth monopoly: streaming service exhausting node network | `kubectl debug node/<node> -it --image=ubuntu -- iftop -n -t -s 10 2>/dev/null \| tail -20`; one pod consuming 10 Gbps; other pods' connections timeout | All pods on same node experience network congestion; external API calls fail; image pulls timeout | Limit bandwidth via traffic shaping (requires Kubernetes CNI plugin support): add pod annotation `kubernetes.io/egress-bandwidth: "100M"`; or move streaming pod to dedicated node | Add node network bandwidth class to heavy-bandwidth workloads; separate high-bandwidth workloads to dedicated node groups with enhanced networking; use AWS placement groups for same-node network isolation |
| Connection pool starvation: one namespace depleting shared RDS Proxy connections | `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name DatabaseConnections --dimensions Name=DBProxyName,Value=$PROXY --period 60 --statistics Maximum` at max; other namespaces' DB calls fail | All tenants sharing RDS Proxy get connection refused errors; application-level DB failures | Reduce connection count for offending namespace: scale down deployment: `kubectl scale deployment <db-heavy-app> -n <ns> --replicas=5`; set `--max-connections-percent` per IAM group in RDS Proxy | Configure RDS Proxy `--target-group-name` with separate `MaxConnectionsPercent` per tenant namespace role; deploy separate RDS Proxy per tenant tier |
| Quota enforcement gap: namespace without ResourceQuota can consume unlimited cluster resources | `kubectl get resourcequota -A` shows some namespaces with no quota; `kubectl get pods -n <unlimited-ns> \| wc -l` shows hundreds of pods | Other tenants' pods cannot be scheduled; cluster autoscaler not scaling fast enough; scheduler overwhelmed | Apply emergency ResourceQuota: `kubectl apply -n <unlimited-ns> -f - <<EOF\napiVersion: v1\nkind: ResourceQuota\nmetadata:\n  name: emergency-quota\nspec:\n  hard:\n    pods: "50"\n    requests.cpu: "20"\n    requests.memory: 40Gi\nEOF` | Enforce ResourceQuota for all namespaces via admission controller; use Hierarchical Namespace Controller (HNC) to inherit quotas |
| Cross-tenant data leak risk: missing NetworkPolicy allows pod-to-pod communication across namespaces | `kubectl exec <pod-a> -n tenant-a -- curl http://<pod-b-ip>:8080` succeeds despite being in different namespace; no NetworkPolicy in place | All pods can reach all other pods; tenant A can directly access tenant B's databases and services | Apply default-deny NetworkPolicy to all namespaces: `kubectl apply -f default-deny.yaml -n tenant-a`; allow only legitimate ingress/egress | Implement zero-trust namespace isolation: default-deny NetworkPolicy on all namespaces at cluster creation; whitelist only required communication paths |
| Rate limit bypass: one tenant's CronJob firing every second due to misconfigured schedule | `kubectl get cronjobs -A \| grep "* * * * *"`; `kubectl get pods -A \| grep <cj-prefix> \| wc -l` shows hundreds of running instances; API server flooded with pod create requests | API server overwhelmed; etcd write rate high; cluster-wide control plane degradation | Delete the misconfigured CronJob: `kubectl delete cronjob <name> -n <ns>`; delete orphaned pods: `kubectl delete pods -n <ns> -l job-name --force` | Validate CronJob schedules in admission webhook; enforce minimum schedule interval of 1 minute via OPA Gatekeeper; set `concurrencyPolicy: Forbid` on all CronJobs |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Prometheus scrape failure for kube-state-metrics | Kubernetes deployment/pod-level alerts never fire; `kubectl get --raw /metrics` works but Prometheus has no `kube_deployment_status_replicas` data | kube-state-metrics pod OOMKilled; or NetworkPolicy blocking Prometheus scrape port 8080 from monitoring namespace | Check kube-state-metrics pod: `kubectl get pods -n monitoring -l app=kube-state-metrics`; test scrape: `kubectl exec -n monitoring <prometheus-pod> -- curl -s http://kube-state-metrics.monitoring.svc.cluster.local:8080/metrics \| head -20` | Increase kube-state-metrics memory limit; check NetworkPolicy: `kubectl get networkpolicies -n monitoring`; ensure monitoring namespace can scrape all namespaces |
| Trace sampling gap: Kubernetes control plane operations missing from X-Ray traces | API server calls from application not traced; K8s controller actions invisible; only application-level spans present | Kubernetes API server is not instrumented with X-Ray; EKS control plane is AWS-managed and cannot be instrumented directly | Use EKS audit logs as trace substitute: `aws logs filter-log-events --log-group-name /aws/eks/$CLUSTER/cluster --filter-pattern '"requestURI":"/api/v1/namespaces/production/pods"'`; correlate with application X-Ray traces by timestamp | Enable EKS control plane logging: `aws eks update-cluster-config --name $CLUSTER --logging '{"clusterLogging":[{"types":["audit","api"],"enabled":true}]}'`; ship to CloudWatch; use Prometheus kube-apiserver metrics for latency tracking |
| Log pipeline silent drop: Fluent Bit overwhelmed by log cardinality explosion | Pod logs not appearing in CloudWatch Logs or Elasticsearch; Fluent Bit pods at 100% CPU; other pods' logs also silently dropped | One namespace generating millions of log lines/second (e.g., verbose debug logging in production); Fluent Bit buffer overflow causes silent drop | Check Fluent Bit: `kubectl logs -n logging -l app=fluent-bit \| grep -i "drop\|overflow\|buffer"`; `kubectl top pods -n logging -l app=fluent-bit` | Add Fluent Bit throttling filter: `[FILTER] Name throttle Rate 1000 Window 5 Interval 1s`; set per-pod log rate limits; fix verbose logging in offending namespace: `kubectl set env deployment/<noisy-app> LOG_LEVEL=WARN` |
| Alert rule misconfiguration: `PodCrashLooping` alert missing pods in `CrashLoopBackOff` | Pods crash-looping for hours without alert; `kubectl get pods -A \| grep CrashLoopBackOff` shows many; no PagerDuty notification | Alert expression using `kube_pod_container_status_restarts_total` with wrong `rate()` window; or `job_name` label mismatch; or alert firing → resolved loop masking issue | Manually check: `kubectl get pods -A --field-selector=status.phase=Running \| grep -v "1/1\|2/2\|3/3"`; `kubectl get pods -A \| awk '$4 ~ /^[0-9]+$/ && $4 > 3'` for high restart counts | Test alert expression: `curl "http://prometheus:9090/api/v1/query?query=rate(kube_pod_container_status_restarts_total[5m])>0"` should return pods in CrashLoopBackOff; adjust window and threshold |
| Cardinality explosion from pod label proliferation in Prometheus | Prometheus TSDB > 50 GB; query timeouts; dashboards OOM; `kubectl get pods -A -o json \| jq '[.items[].metadata.labels \| keys[]] \| unique \| length'` shows hundreds of unique label keys | Helm charts adding unique labels per release (`helm.sh/chart: app-1.2.3`); team-added per-commit `git-sha` labels propagate to Prometheus via kube-state-metrics | `curl http://prometheus:9090/api/v1/status/tsdb \| jq '.data.seriesCountByMetricName \| to_entries \| sort_by(-.value) \| .[0:10]'`; identify top cardinality metrics | Add `metric_relabel_configs` to Prometheus scrape config dropping high-cardinality labels: drop `helm.sh/chart`, `git-sha`, `pod-template-hash` from all kube-state-metrics |
| Missing Kubernetes node condition monitoring | Node with `MemoryPressure=True` not alerting; pods being evicted but no notification; `kubectl describe node <node> \| grep MemoryPressure:.*True` | Prometheus `kube_node_status_condition` metric present but alert rule missing for all condition types except `Ready=False` | `kubectl get nodes -o json \| jq '.items[] \| {name:.metadata.name, conditions:.status.conditions[] \| select(.type != "Ready") \| select(.status == "True")}'` | Add alert rules for all node conditions: `MemoryPressure`, `DiskPressure`, `PIDPressure`, `NetworkUnavailable`; use `kube_node_status_condition{status="true",condition!="Ready"} == 1` |
| Instrumentation gap in cluster autoscaler scale-up critical path | Autoscaler decides to scale up but new nodes take 8 minutes; no metric tracks scale-up duration; SLA breach invisible | Cluster Autoscaler emits some metrics but not end-to-end node-ready latency; EC2 instance launch time not in Prometheus | Check autoscaler: `kubectl logs -n kube-system -l app=cluster-autoscaler --since=30m \| grep -E "Scale up\|node group\|registered"`; CloudWatch EC2 `ebs-initialize` and `instance-initialization` events | Add CloudWatch EventBridge rule on EC2 instance state changes from `pending` to `running` to `ready`; calculate delta as custom metric; alert if node bootstrap > 10 minutes |
| Alertmanager/PagerDuty outage during cluster node failure | EKS worker nodes go `NotReady`; pods evicted cluster-wide; Alertmanager running on failed nodes also evicted; on-call not paged | Alertmanager pods were scheduled on nodes that failed; monitoring infrastructure co-failed with production | Check externally: `curl https://app.example.com/health`; check EKS: `aws eks describe-cluster --name $CLUSTER --query 'cluster.status'`; verify nodes: `kubectl get nodes` (if API server reachable) | Run Alertmanager on dedicated monitoring node group with node taints `monitoring=true:NoSchedule`; configure external uptime monitors (PagerDuty Uptime or Datadog Synthetics) independent of EKS; set PagerDuty dead man's switch |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| EKS Kubernetes minor version upgrade (1.28 → 1.29) breaking admission webhooks | After control plane upgrade, pod creation fails with `no kind is registered for the version` or `webhook denied`; admission webhook using deprecated API version | `kubectl get validatingwebhookconfigurations -o json \| jq '.items[] \| {name:.metadata.name, rules:[.webhooks[].rules[].apiVersions[]]}' \| grep -B2 "v1beta1"`; `kubectl get mutatingwebhookconfigurations` | Temporarily bypass the failing webhook: `kubectl patch validatingwebhookconfiguration <name> -p '{"webhooks":[{"name":"<webhook>","failurePolicy":"Ignore"}]}'`; update webhook to use stable API version | Check API deprecations before upgrade: `kubectl deprecations --k8s-version 1.29` (pluto tool); test admission webhooks in staging on new K8s version before production |
| EKS node group AMI upgrade breaking custom user data scripts | After node group AMI upgrade from AL2 to AL2023, nodes fail to bootstrap; `kubectl get nodes` shows `NotReady`; user data scripts using AL2-specific paths fail | `aws eks describe-nodegroup --cluster-name $CLUSTER --nodegroup-name $NG --query 'nodegroup.amiType'`; check node bootstrap: `aws ssm start-session --target <instance-id>` then `journalctl -u kubelet --since "1h ago" \| grep -i error` | Roll back AMI type: `aws eks update-nodegroup-config --cluster-name $CLUSTER --nodegroup-name $NG`; update launch template to specify previous AMI ID: `aws ec2 describe-images --owners amazon --filters "Name=name,Values=amazon-eks-node-1.28*"` | Test AMI upgrade on single test node group before production; validate user data scripts against both AL2 and AL2023; use managed addons to reduce user data complexity |
| EKS add-on version upgrade breaking compatibility | After upgrading `vpc-cni` add-on to newer version, pod IP allocation fails; `kubectl get pods -n kube-system -l k8s-app=aws-node` shows crashes | `aws eks describe-addon --cluster-name $CLUSTER --addon-name vpc-cni --query 'addon.addonVersion'`; `kubectl logs -n kube-system -l k8s-app=aws-node \| grep -i error`; `aws eks describe-addon-versions --addon-name vpc-cni --kubernetes-version 1.28` | Downgrade add-on: `aws eks update-addon --cluster-name $CLUSTER --addon-name vpc-cni --addon-version <prev-version>` | Check add-on version compatibility matrix before upgrading: `aws eks describe-addon-versions --addon-name vpc-cni --kubernetes-version <k8s-version>`; test in staging; upgrade one add-on at a time |
| Zero-downtime cluster migration (old cluster → new cluster) leaving split traffic | DNS cutover to new cluster before all services are healthy; 50% traffic to new cluster returning 503 | `kubectl get pods -A --field-selector=status.phase!=Running -n <ns>`; ALB: `aws elbv2 describe-target-health --target-group-arn $NEW_TG_ARN`; check weighted Route53: `aws route53 list-resource-record-sets --hosted-zone-id $ZONE_ID \| jq '.ResourceRecordSets[] \| select(.Name=="app.example.com.")'` | Roll back Route53 weights: `aws route53 change-resource-record-sets` to set `Weight: 0` on new cluster; all traffic returns to old cluster | Health-check new cluster end-to-end before DNS cutover; use weighted Route53 records starting at 5% traffic; automate rollback trigger on error rate threshold |
| Helm chart major version upgrade (v2 → v3 schema) leaving orphaned resources | After Helm v3 upgrade, old Helm v2 ConfigMaps not cleaned up; new Helm release creates duplicate resources; services run in degraded state | `kubectl get configmaps -A \| grep "OWNER=TILLER"`; `helm list -A`; compare resource counts: `kubectl get deployment -n <ns> \| wc -l` vs `helm get manifest <release> -n <ns> \| grep "kind: Deployment" \| wc -l` | Remove Helm v2 secrets/configmaps: `kubectl delete configmap -n kube-system -l OWNER=TILLER`; re-import resources into Helm v3: `helm3 install <release> <chart> --namespace <ns> --set replicaCount=1` then delete and re-create properly | Run `helm2to3 migrate` before removing Helm v2; audit all Helm v2 releases; test migration on staging before production |
| Kubernetes API version migration (from beta to stable) breaking existing YAML | After K8s 1.25 upgrade removing `PodSecurityPolicy`, all pods fail to schedule; `kubectl get psp` returns `No resources found` | `kubectl api-resources \| grep policy`; check for PSP admission: `kubectl get pods -A --field-selector=status.phase=Pending -o json \| jq '.items[].status.conditions[] \| select(.reason=="Forbidden")'` | Re-enable PSP temporarily using admission controller flags (only possible on self-managed); for EKS: migrate to Pod Security Standards: `kubectl label namespace <ns> pod-security.kubernetes.io/enforce=baseline` | Use `pluto detect-files -d manifests/` and `kubectl deprecations` to identify removed APIs before upgrade; migrate from PSP to Pod Security Standards or OPA Gatekeeper before K8s 1.25 |
| Karpenter NodePool / NodeClass migration causing scheduling failure | After migrating from Cluster Autoscaler to Karpenter, pods remain `Pending`; Karpenter not provisioning nodes; old CA annotations conflict | `kubectl get nodepools`; `kubectl get nodeclaims`; `kubectl logs -n kube-system -l app=karpenter \| grep -i "error\|failed\|deny"`; check pod events: `kubectl describe pod <pending-pod> \| grep -A5 Events` | Temporarily re-enable Cluster Autoscaler: `kubectl scale deployment cluster-autoscaler -n kube-system --replicas=1`; scale down Karpenter: `kubectl scale deployment karpenter -n kube-system --replicas=0` | Test Karpenter provisioning of at least one node before disabling CA; run both CA and Karpenter in parallel during migration; ensure NodePool `spec.limits` and `spec.requirements` cover all workload requirements |
| etcd encryption config change causing read failures for existing secrets | After enabling etcd encryption for `secrets`, existing secrets encrypted with old key become unreadable; applications fail to load secrets | `kubectl get secrets -A 2>&1 \| grep -i "error\|decrypt"`; for self-managed: `journalctl -u kube-apiserver \| grep -i "decrypt\|encryption"` | For EKS: AWS manages etcd; open support case; for self-managed: restore previous `EncryptionConfiguration`: `kubectl apply -f - < previous-encryption-config.yaml`; restart api-server | Test encryption config change on staging cluster first; always provide both old and new key in `EncryptionConfiguration` during rotation; rotate key using `kubectl get secrets -A -o json \| kubectl replace -f -` to re-encrypt all secrets before removing old key |

## Kernel/OS & Host-Level Failure Patterns

| Pattern | Symptom | Detection Command | Mitigation |
|---------|---------|-------------------|------------|
| OOM killer terminates kubelet or container runtime on EKS node | Pods evicted; node transitions to `NotReady`; `kubectl describe node` shows `KubeletNotReady` with memory pressure | `dmesg -T \| grep -i "oom\|kill process" \| grep -E "kubelet\|containerd"` and `kubectl describe node <node> \| grep -A5 "Conditions" \| grep -i memory` | Increase node instance size; set kubelet `--system-reserved` and `--kube-reserved` memory; check with `kubectl top nodes`; cordon affected node: `kubectl cordon <node>` |
| Inode exhaustion on EKS worker node | Pod creation fails with `no space left on device` even with disk available; container images can't extract layers | `df -i /var/lib/containerd` and `find /var/lib/containerd -type f \| wc -l`; check node: `kubectl describe node <node> \| grep -i inode` | Prune images: `ssh <node> 'crictl rmi --prune'`; increase inode count in launch template userdata; use larger root volumes in managed node group: `aws eks update-nodegroup-config --cluster-name <cluster> --nodegroup-name <ng> --scaling-config desiredSize=<n>` |
| CPU steal on EKS EC2 worker nodes | Pod CPU throttling but node `CPUUtilization` appears normal; kube-scheduler sees capacity but workloads are slow | `ssh <node> 'sar -u 1 5'` checking steal column; `kubectl top pods -n <ns> --sort-by=cpu` | Migrate to dedicated instances or Fargate profiles; update node group: `aws eks update-nodegroup-config --cluster-name <cluster> --nodegroup-name <ng> --update-config maxUnavailable=1`; use `c5.xlarge` or later non-burstable types |
| NTP drift causes IRSA token validation failure | Pods using IRSA get `ExpiredTokenException` from AWS APIs; `sts:AssumeRoleWithWebIdentity` fails due to clock skew | `kubectl exec <pod> -n <ns> -- date -u` compared to `date -u`; `ssh <node> 'chronyc tracking \| grep "System time"'` | Verify Amazon Time Sync on nodes: `ssh <node> 'chronyc sources \| grep 169.254.169.123'`; restart chrony: `ssh <node> 'systemctl restart chronyd'`; for Fargate pods, recreate the pod |
| File descriptor exhaustion on EKS node | kubelet fails to create new pods; `too many open files` in kubelet logs; existing pods unaffected but no new scheduling | `ssh <node> 'cat /proc/$(pgrep kubelet)/limits \| grep "open files"'` and `ssh <node> 'ls /proc/$(pgrep kubelet)/fd \| wc -l'` | Increase in node AMI userdata: `echo 'fs.file-max = 1048576' >> /etc/sysctl.d/99-eks.conf && sysctl -p`; update kubelet config via `--max-open-files=1048576` |
| Conntrack table full on EKS nodes with many services | Intermittent connectivity between pods; `kube-proxy` iptables rules can't track new connections; DNS failures | `ssh <node> 'sysctl net.netfilter.nf_conntrack_count && sysctl net.netfilter.nf_conntrack_max'` and `ssh <node> 'dmesg \| grep conntrack'` | `ssh <node> 'sysctl -w net.netfilter.nf_conntrack_max=524288'`; persist in node AMI; consider switching from iptables to eBPF kube-proxy (Cilium): `aws eks create-addon --cluster-name <cluster> --addon-name aws-network-policy-agent` |
| Kernel panic on EKS worker node | Node disappears from cluster; pods rescheduled; ASG launches replacement but join takes minutes | `kubectl get nodes -o wide \| grep NotReady` and `aws ec2 describe-instance-status --instance-ids <id> --query "InstanceStatuses[].SystemStatus"` | Enable EC2 auto-recovery; ensure ASG health check: `aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names <asg> --query "AutoScalingGroups[].HealthCheckType"`; set `--node-status-update-frequency=10s` in kubelet |
| NUMA imbalance on large EKS instances | Pods on large instances (e.g., `m5.16xlarge`) show inconsistent latency; some CPUs saturated while others idle | `ssh <node> 'numactl --hardware && numastat -p $(pgrep kubelet)'` and `kubectl top pods -n <ns> --sort-by=cpu` | Enable kubelet topology manager: `--topology-manager-policy=best-effort`; use CPU manager: `--cpu-manager-policy=static`; set pod resource requests to align with NUMA boundaries |

## Deployment Pipeline & GitOps Failure Patterns

| Pattern | Symptom | Detection Command | Mitigation |
|---------|---------|-------------------|------------|
| Image pull failure in EKS pods | Pods stuck in `ImagePullBackOff`; events show `Failed to pull image` with auth or not-found errors | `kubectl describe pod <pod> -n <ns> \| grep -A10 Events \| grep -i "pull\|image"` and `aws ecr describe-images --repository-name <repo> --image-ids imageTag=<tag> 2>&1` | Check ECR auth: `kubectl get secret -n <ns> -o jsonpath='{.items[*].metadata.name}' \| tr ' ' '\n' \| grep -i ecr`; verify node can pull: `ssh <node> 'crictl pull <image> 2>&1'`; create/update pull secret |
| IRSA auth misconfigured for EKS workload | Pods get `AccessDenied` or `WebIdentityErr` when calling AWS APIs; OIDC trust policy mismatch | `kubectl describe sa <sa> -n <ns> \| grep eks.amazonaws.com/role-arn` and `aws iam get-role --role-name <role> --query "Role.AssumeRolePolicyDocument"` | Fix OIDC trust: `aws iam update-assume-role-policy --role-name <role> --policy-document file://trust.json` with correct OIDC issuer from `aws eks describe-cluster --name <cluster> --query "cluster.identity.oidc.issuer"` |
| Helm release drift from EKS cluster state | `helm list` shows deployed but actual resources differ; someone applied manual `kubectl` changes | `helm diff upgrade <release> <chart> -n <ns>` and `helm get manifest <release> -n <ns> \| kubectl diff -f - 2>&1` | Reconcile: `helm upgrade <release> <chart> -n <ns> --reset-values`; enable Helm drift detection if using ArgoCD: `argocd app set <app> --helm-set enableDriftDetection=true` |
| ArgoCD sync stuck on EKS due to webhook validation | ArgoCD sync fails with admission webhook rejection; custom ValidatingWebhookConfiguration blocks changes | `argocd app get <app> \| grep -i "sync\|error"` and `kubectl get validatingwebhookconfigurations -o name` | Temporarily disable blocking webhook: `kubectl delete validatingwebhookconfiguration <webhook> --dry-run=server`; if safe, apply fix and re-sync: `argocd app sync <app> --force` |
| PDB blocks node drain during EKS node group update | Managed node group update stuck; nodes can't drain because PDB prevents pod eviction | `kubectl get pdb -A -o wide` and `aws eks describe-update --name <cluster> --update-id <update-id> --query "update.status"` | Identify blocking PDB: `kubectl get pdb -A \| grep "0 allowed"'`; temporarily relax: `kubectl patch pdb <pdb> -n <ns> --type merge -p '{"spec":{"maxUnavailable":1}}'`; or force node group update: `aws eks update-nodegroup-version --cluster-name <cluster> --nodegroup-name <ng> --force` |
| Blue-green cluster upgrade fails on EKS | New EKS cluster version ready but DNS/ALB not switching; old cluster still receiving traffic | `aws eks describe-cluster --name <cluster-blue> --query "cluster.{version:version,status:status}"` and `aws elbv2 describe-target-groups --query "TargetGroups[?contains(TargetGroupName,'<cluster>')]"` | Update ALB target group: `aws elbv2 modify-listener --listener-arn <arn> --default-actions Type=forward,TargetGroupArn=<green-tg>`; verify DNS: `aws route53 change-resource-record-sets --hosted-zone-id <zone> --change-batch file://dns-switch.json` |
| ConfigMap/Secret drift in EKS after GitOps merge | GitOps applied new ConfigMap but pods use cached mounted version; behavior mismatch | `kubectl get configmap <cm> -n <ns> -o jsonpath='{.metadata.resourceVersion}'` and `kubectl get pods -n <ns> -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.annotations.configmap-hash}{"\n"}{end}'` | Restart pods to pick up changes: `kubectl rollout restart deployment <deploy> -n <ns>`; use configmap hash annotation in pod template to auto-restart on changes |
| Feature flag change breaks EKS pod startup | Feature flag enables init container or sidecar that fails; pods crash-loop; deployment stalled | `kubectl get pods -n <ns> \| grep CrashLoopBackOff` and `kubectl logs <pod> -n <ns> -c <init-container> --previous` | Roll back feature flag; force rollout: `kubectl rollout undo deployment <deploy> -n <ns>`; if stuck, scale down then up: `kubectl scale deployment <deploy> -n <ns> --replicas=0 && kubectl scale deployment <deploy> -n <ns> --replicas=<n>` |

## Service Mesh & API Gateway Edge Cases

| Pattern | Symptom | Detection Command | Mitigation |
|---------|---------|-------------------|------------|
| Istio circuit breaker trips on EKS service | Envoy returns `503 UO` (upstream overflow); service-to-service calls fail cluster-wide | `kubectl exec <pod> -n <ns> -c istio-proxy -- curl -s localhost:15000/clusters \| grep <svc> \| grep "circuit_breaking"` and `istioctl proxy-config cluster <pod> -n <ns> \| grep <svc>` | Tune DestinationRule: `kubectl edit dr <svc>-dr -n <ns>` — increase `connectionPool.tcp.maxConnections` and `outlierDetection.consecutive5xxErrors`; verify with `istioctl analyze -n <ns>` |
| AWS API Gateway rate limit drops EKS ingress traffic | API Gateway throttles requests before they reach EKS pods; 429s returned to clients | `aws cloudwatch get-metric-statistics --namespace AWS/ApiGateway --metric-name 4XXError --dimensions Name=ApiName,Value=<api> --period 300 --statistics Sum` and `kubectl logs -n <ingress-ns> <ingress-pod> \| grep 429 \| wc -l` | Increase API Gateway limits: `aws apigateway update-stage --rest-api-id <id> --stage-name <stage> --patch-operations op=replace,path=/throttling/rateLimit,value=<new>`; or bypass API GW for internal traffic using NLB |
| Stale CoreDNS cache causes service discovery failures | Pods resolve wrong IP for services; recently-deployed services unreachable; DNS TTL not expired | `kubectl exec <pod> -n <ns> -- nslookup <svc>.<ns>.svc.cluster.local` and `kubectl logs -n kube-system -l k8s-app=kube-dns \| grep -i "NXDOMAIN\|SERVFAIL" \| tail -10` | Restart CoreDNS: `kubectl rollout restart deployment coredns -n kube-system`; reduce cache TTL in CoreDNS ConfigMap; verify with `kubectl get configmap coredns -n kube-system -o yaml \| grep cache` |
| mTLS rotation failure between EKS services in Istio | Certificate renewal fails; services get `TLS handshake error`; mutual auth rejected | `istioctl proxy-config secret <pod> -n <ns>` and `kubectl logs -n istio-system -l app=istiod \| grep -i "cert\|error\|expire" \| tail -20` | Force cert rotation: `kubectl rollout restart deployment istiod -n istio-system`; verify CA cert: `kubectl get secret istio-ca-secret -n istio-system -o jsonpath='{.data.ca-cert\.pem}' \| base64 -d \| openssl x509 -noout -enddate`; restart affected pods |
| Retry storm amplification in EKS service mesh | Istio retries + app retries + ALB retries compound; downstream service overwhelmed with 3x-9x traffic | `istioctl proxy-config route <pod> -n <ns> -o json \| jq '.[].virtualHosts[].routes[].route.retryPolicy'` and `kubectl top pods -n <downstream-ns>` | Disable mesh retries: `kubectl annotate svc <svc> -n <ns> sidecar.istio.io/statsInclusionPrefixes="-"`; set VirtualService retry to `attempts: 0`; configure app-level retry with jitter |
| gRPC keepalive mismatch in EKS service mesh | gRPC connections drop after idle period; Envoy enforces `max_connection_age` shorter than client keepalive | `istioctl proxy-config listener <pod> -n <ns> -o json \| jq '.[].filterChains[].filters[] \| select(.name=="envoy.filters.network.http_connection_manager") \| .typedConfig.commonHttpProtocolOptions'` | Configure EnvoyFilter for gRPC keepalive: `kubectl apply -f` EnvoyFilter with `max_connection_age: 0s` and `keepalive_time: 30s`; ensure client gRPC keepalive < Envoy `max_connection_age` |
| Trace context propagation lost at EKS ingress | X-Ray/Jaeger traces show gap between ALB and first service; ingress controller strips trace headers | `kubectl logs -n <ingress-ns> <ingress-pod> \| grep -i "x-request-id\|traceparent\|x-amzn-trace" \| head -5` and `kubectl get ingress -n <ns> -o yaml \| grep -i annotation` | Configure ingress to forward trace headers: for nginx-ingress add `nginx.ingress.kubernetes.io/configuration-snippet: "proxy_set_header traceparent $http_traceparent;"`; for ALB ingress controller, enable X-Ray: `--enable-xray` |
| NLB health check fails for EKS pods during mesh injection | NLB marks targets unhealthy because health probe goes through Envoy sidecar which isn't ready during startup | `aws elbv2 describe-target-health --target-group-arn <arn>` and `kubectl get pods -n <ns> -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[*].ready}{"\n"}{end}'` | Add `holdApplicationUntilProxyStarts: true` to Istio config; configure NLB health check to bypass Envoy: use `service.beta.kubernetes.io/aws-load-balancer-healthcheck-path: /healthz` and target container port directly |
