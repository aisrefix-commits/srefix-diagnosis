---
name: hadoop-agent
description: >
  Apache Hadoop specialist agent. Handles YARN ResourceManager/NodeManager
  issues, HDFS NameNode failures, block replication problems, MapReduce
  job failures, and big data cluster operations.
model: sonnet
color: "#66CCFF"
skills:
  - hadoop/hadoop
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-hadoop-agent
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

You are the Hadoop Agent — the big data platform expert. When any alert involves
YARN scheduling, HDFS availability, NameNode failures, block replication, or
MapReduce job issues, you are dispatched to diagnose and remediate.

# Key Metrics and Alert Thresholds

Hadoop metrics are available via JMX, exposed over HTTP at the `/jmx` endpoint of each daemon's web UI (port 9870 for NameNode, 8088 for ResourceManager, 8042 for NodeManager) and via CloudWatch `AWS/ElasticMapReduce` on EMR.

| Metric | Source | WARNING | CRITICAL | Notes |
|--------|--------|---------|----------|-------|
| Missing blocks | `hdfs dfsadmin -report` / CloudWatch `MissingBlocks` | > 0 | > 0 | Any missing block = potential data loss; page immediately |
| Under-replicated blocks | `hdfs dfsadmin -report` / CloudWatch `UnderReplicatedBlocks` | > 100 | > 1 000 | Degraded durability; check for dead DataNodes |
| HDFS capacity used % | `hdfs dfs -df` / CloudWatch `HDFSUtilization` | > 80% | > 95% | Full HDFS stops all writes; NameNode blocks new files |
| Live DataNodes | `hdfs dfsadmin -report` / CloudWatch `LiveDataNodes` | < expected | < quorum | Each dead DataNode reduces replication and capacity |
| NameNode heap used % | JMX `java.lang:type=Memory HeapMemoryUsage.used / max` | > 70% | > 85% | NameNode heap scales with inode + block count; OOM = cluster down |
| YARN memory utilization % | `yarn top` / CloudWatch `YARNMemoryAvailablePercentage` | < 20% free | < 5% free | Near 0% = no new containers can be allocated |
| YARN pending applications | `yarn application -list -appStates ACCEPTED` | > 20 | > 50 | Sustained queue = resource starvation |
| Dead NodeManagers | `yarn node -list -all` filter UNHEALTHY/LOST | > 1 | > 20% of fleet | Lost NodeManagers reduce available YARN capacity |
| ResourceManager failover | `yarn rmadmin -getServiceState` | RM in STANDBY too long | Active RM unreachable | HA failover taking > 30s = cluster paused |
| HDFS safe mode | `hdfs dfsadmin -safemode get` | — | `Safe mode is ON` unexpectedly | Writes blocked in safe mode; auto-exits after startup normally |
| MapReduce GC time % | `mapred job -counters` GC_TIME_MILLIS / SLOTS_MILLIS | > 10% | > 25% | High GC → slow tasks → job timeout |
| Corrupt blocks | `hdfs fsck /` | > 0 | > 0 | Corrupt blocks = unrecoverable data; escalate immediately |

# Activation Triggers

- Alert tags contain `hadoop`, `yarn`, `hdfs`, `namenode`, `mapreduce`
- NameNode or ResourceManager down alerts
- Under-replicated or missing block alerts
- YARN unhealthy node or resource exhaustion
- MapReduce job failure notifications

### Cluster Visibility

```bash
# NameNode HA status
hdfs haadmin -getServiceState nn1
hdfs haadmin -getServiceState nn2

# HDFS overall health
hdfs dfsadmin -report
hdfs dfsadmin -safemode get

# YARN cluster node list and capacity
yarn node -list -all
yarn cluster -list-node-labels

# ResourceManager HA state
yarn rmadmin -getServiceState rm1

# YARN application queue status
yarn queue -status default
yarn application -list -appStates RUNNING,ACCEPTED

# HDFS storage utilization
hdfs dfs -df -h /
hdfs dfsadmin -report | grep -E "(Configured|DFS Used|DFS Remaining)"

# MapReduce job history
mapred job -list
mapred job -list -jobid job_*

# Web UI key pages
# ResourceManager: http://<rm-host>:8088/cluster
# NameNode:        http://<nn-host>:9870/dfshealth.html
# Job History:     http://<jhs-host>:19888/jobhistory
# NodeManager:     http://<nm-host>:8042/node
```

### Global Diagnosis Protocol

**Step 1: Infrastructure health**
```bash
# Check all HDFS/YARN services
hdfs haadmin -getAllServiceState
yarn rmadmin -getAllServiceState
# Confirm DataNodes are live
hdfs dfsadmin -report | grep "Live datanodes"
# Confirm NodeManagers are healthy
yarn node -list -all | grep -c RUNNING
```

**Step 2: Job/workload health**
```bash
yarn application -list -appStates RUNNING,ACCEPTED,FAILED
mapred job -list
# Check failed jobs in past hour
yarn application -list -appStates FAILED -startedTimeBegin $(date -d '1 hour ago' +%s)000
```

**Step 3: Resource utilization**
```bash
yarn top                          # live resource view
yarn node -list -all              # per-node memory/vcores
hdfs dfsadmin -report | grep -E "(Used|Remaining|Available)"
```

**Step 4: Data pipeline health**
```bash
hdfs dfsadmin -report | grep -E "(Under replicated|Missing blocks|Corrupt blocks)"
hdfs fsck / -files -blocks -locations 2>&1 | tail -20
```

**Severity:**
- CRITICAL: NameNode down, missing blocks > 0, YARN RM down, disk > 95%, corrupt blocks > 0
- WARNING: under-replicated blocks > 100, queue backlog > 50 apps, disk > 80%, NameNode heap > 85%
- OK: all services active, 0 missing blocks, queue latency < 5 s, HDFS capacity < 80%

### Focused Diagnostics

**Job/Task Failure**
```bash
# Get application ID from YARN
yarn application -list -appStates FAILED
# Fetch driver + container logs
yarn logs -applicationId application_<id>
yarn logs -applicationId application_<id> -containerId container_<id>
# MapReduce task attempt details
mapred job -history <job-id>
mapred job -status <job-id>
```

**Resource Exhaustion**
```bash
# Queue capacities and pending apps
yarn queue -status root.default
curl -s "http://<rm-host>:8088/ws/v1/cluster/scheduler" | python3 -m json.tool
# NodeManager memory per host
yarn node -status <node-id>
# Kill low-priority blocked apps
yarn application -kill application_<id>
```

**Data Skew / HDFS Imbalance**
```bash
# DataNode disk imbalance
hdfs dfsadmin -report | grep -E "(Name:|DFS Used%)"
# Run balancer to rebalance
hdfs balancer -threshold 10
# Check block distribution
hdfs fsck /user/<path> -blocks -locations | grep "^/user" | awk '{print $3}' | sort | uniq -c | sort -rn | head -20
```

**HDFS NameNode OOM / Heap Pressure**
```bash
# Check NameNode heap
curl -s http://<nn-host>:9870/jmx?qry=java.lang:type=Memory | python3 -m json.tool
# Count inode/block usage (NameNode heap scales with both)
hdfs dfsadmin -report | grep "Blocks:"
hdfs dfs -count -q /
# Force a checkpoint to reduce edit log size
hdfs dfsadmin -saveNamespace
```

**MapReduce Slow Job / GC Pressure**
```bash
# Check task counters for GC time (WARNING > 10%, CRITICAL > 25% of slot time)
mapred job -counters <job-id> | grep GC
# Review task attempt logs for GC logs
yarn logs -applicationId application_<id> | grep -E "(GC overhead|OutOfMemory|Full GC)"
# Increase child heap
# mapreduce.map.java.opts=-Xmx3g  mapreduce.reduce.java.opts=-Xmx6g
```

**NameNode JMX Health Check**
```bash
# NameNode heap memory usage (WARNING > 70%, CRITICAL > 85%)
curl -s "http://<nn-host>:9870/jmx?qry=java.lang:type=Memory" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)['beans'][0]
used = d['HeapMemoryUsage']['used']
max_ = d['HeapMemoryUsage']['max']
print(f'Heap used: {used/1e9:.1f} GB / {max_/1e9:.1f} GB ({100*used/max_:.1f}%)')
"

# NameNode block/inode stats (high counts increase heap pressure)
curl -s "http://<nn-host>:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystem" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)['beans'][0]
for k in ['FilesTotal','BlocksTotal','UnderReplicatedBlocks','CorruptBlocks','MissingBlocks']:
    print(f'{k}: {d.get(k,\"N/A\")}')
"

# YARN ResourceManager metrics (pending apps, available memory)
curl -s "http://<rm-host>:8088/ws/v1/cluster/metrics" | python3 -m json.tool | grep -E \
  "(appsPending|appsRunning|availableMB|totalMB|unhealthyNodes|lostNodes)"
```

**YARN ResourceManager Failover Causing Running Jobs to Fail**
```bash
# Check RM HA state on both nodes
yarn rmadmin -getServiceState rm1
yarn rmadmin -getServiceState rm2
# Identify current active RM
yarn rmadmin -getAllServiceState
# Check recent RM failover events in logs
grep -iE "failover|became active|became standby|ZooKeeper" /var/log/hadoop-yarn/yarn-yarn-resourcemanager-*.log | tail -20
# Running jobs status after failover (jobs may need resubmit if AM died)
yarn application -list -appStates RUNNING,FAILED | head -20
# Check ZooKeeper quorum (RM HA depends on ZK)
echo ruok | nc <zk-host> 2181
# Verify ZooKeeper znode for RM leadership
zkCli.sh -server <zk-host>:2181 get /yarn-leader-election/<cluster>/ActiveBreadCrumb
```

Root causes: ZooKeeper quorum loss preventing RM leadership election, network partition between RM nodes, RM process OOM causing failover, fencing mechanism failure causing split-brain.
Quick mitigation: If new active RM stabilized, resubmit failed applications; verify ZK quorum health; check RM heap size (YARN_RESOURCEMANAGER_HEAPSIZE); increase ZK session timeout in `yarn-site.xml` (`yarn.resourcemanager.zk-timeout-ms`).

---

**NodeManager Lost Contact with ResourceManager**
```bash
# List all nodes and their states
yarn node -list -all | grep -E "LOST|UNHEALTHY|DECOMMISSIONED"
# NodeManager health on specific host
yarn node -status <node-id>
# NM logs for connection errors
ssh <nm-host> "grep -iE 'lost connection|not registered|heartbeat' /var/log/hadoop-yarn/yarn-yarn-nodemanager-*.log | tail -20"
# NM heartbeat timeout setting
grep "yarn.resourcemanager.nm.liveness-monitor.expiry-interval-ms" /etc/hadoop/conf/yarn-site.xml
# Network connectivity NM -> RM
nc -zv <rm-host> 8032
# JVM health on NM
ssh <nm-host> "curl -s http://localhost:8042/ws/v1/node/info | python3 -m json.tool | grep -E 'healthStatus|totalVmemAllocatedContainersMB'"
```

Root causes: NM heartbeat timeout too short for loaded node, network congestion/packet loss between NM and RM, NM JVM GC pause exceeding heartbeat interval, disk health check failing (causing NM to mark itself unhealthy).
Quick mitigation: Increase `yarn.resourcemanager.nm.liveness-monitor.expiry-interval-ms` (default 600000ms); check disk health scripts in `yarn.nodemanager.health-checker.script.path`; restart NM: `sudo systemctl restart hadoop-yarn-nodemanager`.

---

**MapReduce Job Stuck in ACCEPTED State**
```bash
# Jobs stuck in ACCEPTED (not transitioning to RUNNING)
yarn application -list -appStates ACCEPTED
# Queue capacity check
yarn queue -status root.default
curl -s "http://<rm-host>:8088/ws/v1/cluster/scheduler" | python3 -m json.tool | grep -E "capacity|usedCapacity|pendingContainers" | head -20
# ResourceManager metrics — available memory
curl -s "http://<rm-host>:8088/ws/v1/cluster/metrics" | python3 -m json.tool | grep -E "availableMB|totalMB|unhealthyNodes|lostNodes"
# Check if all NMs are healthy
yarn node -list -all | grep -v RUNNING | head -20
# Container allocation check — AM container required to start job
curl -s "http://<rm-host>:8088/ws/v1/cluster/scheduler" | python3 -m json.tool | grep -E "pendingContainers|allocatedContainers|reservedContainers" | head -10
```

Root causes: All YARN memory/vcores consumed by running jobs, queue capacity limit hit, all NodeManagers unhealthy/lost, AM container memory request exceeding available slot size, queue maximum capacity set too low.
Quick mitigation: Kill low-priority stuck jobs: `yarn application -kill application_<id>`; scale cluster (add NodeManagers); reduce AM container memory request in job config (`yarn.app.mapreduce.am.resource.mb`); increase queue capacity temporarily.

---

**HDFS Safe Mode Blocking Writes After Restart**
```bash
# Check safe mode status
hdfs dfsadmin -safemode get
# Safe mode threshold configuration
hdfs getconf -confKey dfs.namenode.safemode.threshold-pct
hdfs getconf -confKey dfs.namenode.safemode.min.datanodes
# Current block report status
hdfs dfsadmin -report | grep -E "Under replicated|Missing|Live datanodes"
# Force exit safe mode (use only if DataNodes are healthy and reporting)
hdfs dfsadmin -safemode leave
# Watch safe mode exit progress
watch -n5 'hdfs dfsadmin -report | grep -E "Under replicated|Live" && hdfs dfsadmin -safemode get'
# Check edit log / checkpoint status (safe mode may be extended pending checkpoint)
curl -s "http://<nn-host>:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystem" | \
  python3 -c "import sys,json; d=json.load(sys.stdin)['beans'][0]; print('SafeMode:', d.get('SafeModeMessage','not in safemode'), 'UnderReplicated:', d.get('UnderReplicatedBlocks'))"
```

Root causes: Not enough DataNodes checked in (below `safemode.threshold-pct` × expected blocks), DataNodes slow to start and report blocks, manual safe mode entry not exited, checkpoint not completing within timeout.
Quick mitigation: Wait for DataNodes to report (normal startup); if DataNodes all healthy but safe mode persists, `hdfs dfsadmin -safemode leave`; check DataNode logs for block report errors; verify NameNode can reach all DataNodes on the DataNode IPC port (default 9867 in Hadoop 3.x, 50020 in 2.x).

---

**Container Memory Exceeded Causing YARN Container Kill**
```bash
# Find killed containers in YARN logs
yarn logs -applicationId application_<id> | grep -iE "killed|exceeded physical|exceeded virtual|Container killed" | tail -20
# Task-level memory usage
yarn logs -applicationId application_<id> -containerId <container-id> | grep -E "GC|heap|memory|WARN" | tail -30
# YARN memory settings
grep -E "mapreduce.map.memory.mb|mapreduce.reduce.memory.mb|yarn.nodemanager.vmem-pmem-ratio|yarn.nodemanager.pmem-check" /etc/hadoop/conf/mapred-site.xml /etc/hadoop/conf/yarn-site.xml 2>/dev/null
# Current container memory limits per node
curl -s "http://<nm-host>:8042/ws/v1/node/info" | python3 -m json.tool | grep -E "totalMemoryNeededMB|usedMemoryMB"
```

Root causes: Task JVM heap set larger than container memory allocation, `vmem-pmem-ratio` too small (virtual memory check), large sort buffers or data skew causing memory spike, `yarn.nodemanager.pmem-check-enabled=true` with tight limits.
Quick mitigation: Increase container memory allocation in job: `-Dmapreduce.map.memory.mb=4096 -Dmapreduce.map.java.opts=-Xmx3584m`; disable virtual memory check if not needed: `yarn.nodemanager.vmem-check-enabled=false`; profile job with smaller sample to right-size.

---

**Kerberos Ticket Renewal Failure Causing Job Authentication Error**
```bash
# Check Kerberos ticket status
klist -e
# Verify Hadoop principal
klist | grep -E "Default principal|Expires"
# Check if keytab is valid
kinit -kt /etc/security/keytabs/hdfs.headless.keytab hdfs-<cluster>@<REALM>
# HDFS token expiry for long-running jobs
hdfs fetchdt --webservice https://<nn-host>:9871 /tmp/hdfs.token
hdfs debug recoverLease -path /path/to/file
# MapReduce job token renewal settings
grep -E "mapreduce.job.token.renewal|dfs.namenode.delegation.token.renew" /etc/hadoop/conf/*.xml
# Job history for auth errors
yarn logs -applicationId application_<id> | grep -iE "kerberos|token|expired|kinit|auth" | tail -20
```

Root causes: Kerberos ticket expired during multi-hour job (renewal not configured), keytab file missing/wrong permissions on compute nodes, clock skew between nodes exceeding 5 minutes (Kerberos max skew), delegation token expiry for jobs exceeding `dfs.namenode.delegation.token.max-lifetime` (default 7 days).
Quick mitigation: `kinit -R` to renew (if renewable); `kinit -kt <keytab> <principal>` for fresh ticket; enable automatic renewal: set `mapreduce.job.credentials.binary` and configure `mapreduce.job.token.renewal.period.ms`; synchronize clocks with NTP.

---

**Log Aggregation Not Working After Job Completion**
```bash
# Check log aggregation status for application
yarn application -status application_<id> | grep -E "Log Aggregation Status|Final Status"
# Log aggregation directory in HDFS
hdfs dfs -ls /app-logs/<user>/logs/application_<id>/ 2>/dev/null || echo "No aggregated logs found"
# NM log aggregation settings
grep -E "yarn.log-aggregation-enable|yarn.nodemanager.remote-app-log-dir|yarn.log.server.url" /etc/hadoop/conf/yarn-site.xml
# NM logs for aggregation errors on compute node
ssh <nm-host> "grep -iE 'log.*aggr|upload.*log|AggregatedLogDeletionService' /var/log/hadoop-yarn/yarn-yarn-nodemanager-*.log | tail -20"
# Check HDFS write permission for log dir
hdfs dfs -ls /app-logs/
hdfs dfs -stat '%u %g %a' /app-logs/<user>/
# Fetch logs via log server URL if aggregated
curl -s "http://<log-server-host>:19888/jobhistory/logs/application_<id>/<container-id>/<node-host>/<user>"
```

Root causes: HDFS `/app-logs` directory permissions wrong for user, log aggregation disabled in yarn-site.xml, NM cannot write to HDFS (Kerberos issue), local disk on NM full causing log files to be dropped, log retention period too short.
Quick mitigation: Enable log aggregation: `yarn.log-aggregation-enable=true`; fix HDFS permissions: `hdfs dfs -chmod 1777 /app-logs`; check disk space on NM nodes; verify HDFS write access for yarn user.

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `WARN: ResourceManager service failed: xxx` | ResourceManager crash | `yarn rmadmin -getServiceState rm1` |
| `org.apache.hadoop.ipc.RemoteException: xxx: Name node is in safe mode` | NameNode just started, replicating blocks | `hdfs dfsadmin -safemode leave` |
| `ERROR: Application application_xxx failed 2 times due to AM Container for xxx exited with exitCode: -104` | ApplicationMaster killed for exceeding physical memory | increase AM memory: `yarn.app.mapreduce.am.resource.mb` (mapred-site.xml) or framework equivalent |
| `java.io.IOException: Too many fetch-failures` | Shuffle failures during MapReduce | check NodeManager network and disk |
| `Could not find local data directory` | YARN NodeManager data dir missing | check `yarn.nodemanager.local-dirs` |
| `container_xxx is running beyond virtual memory limits` | Virtual memory ratio exceeded | set `yarn.nodemanager.vmem-check-enabled=false` |
| `java.io.FileNotFoundException: Path does not exist: hdfs://xxx` | HDFS file missing | `hdfs dfs -ls <path>` |
| `Block does not satisfy minimum replication` | HDFS block under-replicated | `hdfs dfsadmin -report` |

# Capabilities

1. **HDFS operations** — NameNode HA failover, block replication, fsck
2. **YARN management** — ResourceManager HA, scheduler tuning, node health
3. **MapReduce** — Job failure diagnosis, task debugging, performance
4. **Capacity planning** — NameNode memory, HDFS storage, YARN resources
5. **Rack awareness** — Data placement, cross-rack replication
6. **Federation** — Multi-NameNode namespace management

# Critical Metrics to Check First

1. **Missing blocks** (`hdfs dfsadmin -report` or CloudWatch `MissingBlocks`) — any > 0 = data loss risk
2. **NameNode availability** — if down, HDFS is inaccessible; check HA failover state
3. **HDFS capacity used %** — > 95% stops all writes; > 80% = WARNING
4. **YARN pending applications** — > 50 = resource starvation; check dead NodeManagers
5. **Dead DataNode count** — each dead DN reduces replication factor and available capacity
6. **Corrupt blocks** (`hdfs fsck /` summary) — any > 0 = escalate immediately

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| Running YARN jobs fail mid-execution with connection errors | ZooKeeper session expiry caused ResourceManager HA failover; ApplicationMasters lost their RM connection | `echo ruok | nc <zk-host> 2181` then `grep -iE "failover|ZooKeeper|became active" /var/log/hadoop-yarn/yarn-yarn-resourcemanager-*.log | tail -20` |
| HDFS writes blocked, NameNode in safe mode (unexpected) | DataNode disk monitoring detected failing disk and NM marked itself unhealthy, reducing live replicas below safe mode threshold | `hdfs dfsadmin -report | grep -E "Live datanodes|Under replicated"` then `yarn node -list -all | grep UNHEALTHY` |
| MapReduce jobs stuck in ACCEPTED — no containers allocated | All NodeManager local disks full, causing NMs to mark themselves unhealthy and leave YARN capacity | `yarn node -list -all | grep UNHEALTHY` then `ssh <nm-host> "df -h /data*"` |
| Kerberos-authenticated jobs failing with `GSS initiate failed` | NTP daemon stopped on worker nodes; clock skew > 5 minutes triggers Kerberos hard rejection | `for h in <hosts>; do echo "$h: $(ssh $h date)"; done` |
| NameNode JVM heap growing — OOM risk | Excessive small file creation by upstream ETL pipeline; inode count exceeds design capacity | `hdfs dfs -count -q /` then `curl -s "http://<nn-host>:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystem" | python3 -m json.tool | grep FilesTotal` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N DataNodes offline (others healthy) | `hdfs dfsadmin -report` shows `Live datanodes` count below expected fleet size | Under-replication on blocks that had copies only on dead DN; durability reduced | `hdfs dfsadmin -report | grep "Name:"` to list all DN hostnames and spot the missing one |
| 1 of N NodeManagers unhealthy (disk check failing) | `yarn node -list -all` shows one node in `UNHEALTHY` state | Containers no longer scheduled on that NM; effective YARN capacity reduced by 1 node share | `yarn node -status <node-id>` then `ssh <nm-host> "cat /var/log/hadoop-yarn/yarn-yarn-nodemanager-*.log | grep -iE 'health|disk'"` |
| 1 YARN queue starved while others run normally | `yarn queue -status <queue>` shows high `pendingContainers` while sibling queues have free capacity | Jobs submitted to starved queue wait indefinitely; other queues unaffected | `curl -s "http://<rm-host>:8088/ws/v1/cluster/scheduler" | python3 -m json.tool | grep -A5 "<queue-name>"` |
| 1 DataNode with high disk utilization causing write skew | `hdfs dfsadmin -report | grep -E "Name:|DFS Used%"` shows one DN >> others | New block replicas avoid the full DN; other DNs fill faster; eventual write failures if not rebalanced | `hdfs balancer -threshold 10` (dry run first with `-Ddfs.balancer.simulate=true` if available) |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| HDFS capacity used % | > 80% | > 95% | `hdfs dfs -df -h / \| awk 'NR==2 {gsub(/%/,""); print $5}'` |
| Under-replicated blocks | > 100 | > 1,000 | `hdfs dfsadmin -report \| grep "Under replicated blocks"` |
| Missing blocks | > 0 | > 0 (any = data loss risk) | `hdfs dfsadmin -report \| grep "Missing blocks"` |
| YARN cluster memory available % | < 20% free | < 5% free | `curl -s "http://<rm-host>:8088/ws/v1/cluster/metrics" \| python3 -m json.tool \| grep -E "availableMB\|totalMB"` (compute pct) |
| YARN pending applications | > 20 | > 50 | `yarn application -list -appStates ACCEPTED \| grep -c "application_"` |
| NameNode JVM heap used % | > 70% | > 85% | `curl -s "http://<nn-host>:9870/jmx?qry=java.lang:type=Memory" \| python3 -c "import sys,json; d=json.load(sys.stdin)['beans'][0]; print(round(d['HeapMemoryUsage']['used']/d['HeapMemoryUsage']['max']*100,1))"` |
| MapReduce task GC time % of slot time | > 10% | > 25% | `mapred job -counters <job-id> \| grep -E "GC_TIME_MILLIS\|SLOTS_MILLIS"` (compute ratio) |
| Dead NodeManagers | > 1 | > 20% of fleet | `yarn node -list -all \| grep -cE "LOST\|UNHEALTHY"` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| HDFS capacity used % | > 70% and growing > 5% per week | Add DataNodes or expand DataNode disk capacity; run `hdfs balancer -threshold 10`; archive cold data to object storage | 2 weeks |
| NameNode heap used % | > 60% and trending upward (inode/block count growing) | Increase `HADOOP_NAMENODE_OPTS -Xmx`; audit and delete small files (merge with SequenceFile or ORC); plan NameNode memory upgrade | 1 week |
| HDFS inode count (`FilesTotal` JMX) | > 200 million inodes (NameNode heap scales linearly) | Enforce small-file compaction in ETL pipelines; archive unused namespaces; federate NameNode | 1 month |
| Under-replicated blocks count | > 0 for > 10 minutes (not during DataNode restart) | Investigate dead DataNodes; add capacity if cluster-wide disk usage is high; reduce replication factor only for cold data | 30 min |
| YARN memory available % | < 30% free during off-peak hours | Add NodeManagers; increase NodeManager memory (`yarn.nodemanager.resource.memory-mb`); review and kill idle/stuck applications | 1 week |
| YARN pending applications | > 10 applications in ACCEPTED state for > 5 minutes during off-peak | Scale NodeManager fleet; review queue capacities; increase `yarn.scheduler.capacity.maximum-applications` | 1 hour |
| DataNode disk utilization variance | Std deviation of `DFS Used%` across DataNodes > 15% | Run `hdfs balancer -threshold 5` on schedule; investigate hotspot DataNodes for single-partition skew | 1 day |
| MapReduce GC time % | > 8% of slot time trending upward across multiple jobs | Increase container heap (`mapreduce.map.java.opts=-Xmx`); review job data skew; tune `mapreduce.task.io.sort.mb` | 1 day |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Check HDFS overall health: capacity, under-replicated blocks, missing blocks
hdfs dfsadmin -report | grep -E "^(Configured|DFS Used|Live|Dead|Decommissioning|Under replicated|Blocks with|Missing)"

# Show NameNode heap usage and GC stats via JMX
curl -sf "http://namenode-host:9870/jmx?qry=java.lang:type=Memory" | jq '.beans[0] | {HeapMemoryUsage}'

# List DataNodes with highest disk utilization (identify hot spots)
hdfs dfsadmin -report | grep -A 8 "^Name:" | grep -E "Name:|DFS Used%"

# Count total under-replicated and missing blocks in real time
hdfs fsck / -list-corruptfileblocks 2>/dev/null | tail -10

# Check YARN cluster resource availability (memory and vcores free)
yarn node -list -all 2>/dev/null | awk 'NR>2 {split($5,m,"/"); split($6,c,"/"); print $1, "Mem:", m[1]"/"m[2], "VCores:", c[1]"/"c[2]}'

# Show YARN applications currently running, pending, and recently failed
yarn application -list -appStates RUNNING,ACCEPTED,FAILED 2>/dev/null | head -30

# Check HDFS safe mode status (blocks writes during rolling restart)
hdfs dfsadmin -safemode get

# Inspect NameNode audit log for bulk deletes or suspicious access patterns in last 5 min
tail -n 500 /var/log/hadoop-hdfs/hdfs-audit.log | grep -E "delete|rename" | tail -20

# Show ResourceManager queue capacities and current utilization
yarn queue -status root 2>/dev/null | grep -E "Queue|Capacity|Used|Pending"

# Check HDFS balancer status and cross-rack data distribution
hdfs balancer -query 2>/dev/null || echo "Balancer not running"
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| HDFS data availability (no missing blocks) | 99.9% | `hadoop_namenode_missing_blocks == 0` and `hadoop_namenode_under_replicated_blocks / hadoop_namenode_blocks_total < 0.001`; evaluated every 2 min | 43.8 min | Missing blocks > 0 for > 5 min OR under-replicated > 0.1% of total blocks |
| YARN job submission success rate | 99% | `(apps_completed_successfully / apps_submitted_total)` over 1h rolling window; `yarn_resourcemanager_applications_failed` / total | 7.3 hr | Failure rate > 5% over 15-min window (burn rate > 14.4×) |
| NameNode RPC processing latency p99 | 99.5% of NameNode RPC calls complete within 500 ms | `hadoop_namenode_rpc_processing_time_avg_time` p99 ≤ 500 ms; `hadoop_namenode_rpc_queue_time_avg_time` ≤ 200 ms | 3.6 hr | p99 > 2 000 ms sustained for 10 min |
| HDFS write throughput availability | 99% | `hadoop_datanode_bytes_written_total` rate > 0 across ≥ 80% of DataNodes; measured per 5-min window during business hours | 7.3 hr | Fewer than 80% of DataNodes reporting writes during peak window for > 15 min |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Authentication — Kerberos enforcement | `grep -E "hadoop.security.authentication\|hadoop.security.authorization" /etc/hadoop/conf/core-site.xml` | `kerberos` and `true` respectively; never `simple` in production |
| TLS — wire encryption for RPC and data transfer | `grep -E "dfs.encrypt.data.transfer\|hadoop.rpc.protection" /etc/hadoop/conf/hdfs-site.xml /etc/hadoop/conf/core-site.xml` | `dfs.encrypt.data.transfer = true`; `hadoop.rpc.protection = privacy` |
| Resource limits — YARN memory and CPU caps | `grep -E "yarn.nodemanager.resource.memory-mb\|yarn.scheduler.maximum-allocation-mb" /etc/hadoop/conf/yarn-site.xml` | Allocation ≤ 90% of host physical memory; maximum container ≤ node capacity |
| Retention — HDFS trash interval | `grep "fs.trash.interval" /etc/hadoop/conf/core-site.xml` | Non-zero value (e.g., `1440` minutes = 24 h); `0` disables trash (data loss risk) |
| Replication — default replication factor | `grep "dfs.replication" /etc/hadoop/conf/hdfs-site.xml` | `3` for production; `1` only acceptable for scratch/temp directories |
| Backup — NameNode edit log and fsimage backup | `ls -lht /mnt/namenode-backup/ \| head -5` or check NFS/remote secondary NameNode checkpoint age | Latest checkpoint less than 1 hour old; secondary NameNode or JournalNode quorum confirmed |
| Access controls — HDFS permissions and ACLs | `hdfs dfs -ls / \| awk '{print $1, $3, $4, $NF}'` and `hdfs dfs -getfacl /` | Root HDFS directories not world-writable; superuser group limited to hadoop service accounts |
| Network exposure — NameNode UI bound correctly | `ss -tlnp \| grep -E ':9870\|:8088'` (add `:50070` if running Hadoop 2.x) | Web UIs not exposed on `0.0.0.0` without reverse-proxy authentication; firewall restricts to trusted subnets |
| High availability — JournalNode quorum health | `for jn in JN1 JN2 JN3; do echo "==$jn=="; ssh $jn "systemctl is-active hadoop-journalnode"; done` | All JournalNodes active; odd count (3 or 5) for quorum |
| Audit logging — HDFS audit log enabled | `grep "hdfs.audit.logger" /etc/hadoop/conf/log4j.properties` | `INFO,RFAAUDIT` present; audit log shipping to central log store confirmed |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `FATAL namenode.NameNode: Failed to start namenode: java.io.IOException: There appears to be a gap in the edit log` | Critical | Edit log corruption or missing segment; NameNode cannot start | Run `hdfs namenode -recover` in interactive mode; restore from last good fsimage checkpoint |
| `WARN namenode.FSNamesystem: Low disk space detected: volume /dfs/nn with only X available` | High | NameNode metadata disk nearly full | Remove obsolete fsimage/edits files under `dfs.namenode.name.dir/current/` (keep the latest `fsimage_*` and the edits since it); expand disk or clean `/dfs/nn` |
| `ERROR datanode.DataNode: IOException: Premature EOF from inputStream` | Medium | Network interruption during block transfer to/from DataNode | Usually self-healing; monitor block replication; check network between nodes |
| `WARN namenode.NameNode: DataNode <hostname> is dead and removed from live nodes list` | High | DataNode missed heartbeat; declared dead | Check DataNode service: `systemctl status hadoop-datanode`; inspect DataNode logs for OOM or disk error |
| `ERROR mapreduce.Job: Job job_XXX failed with state FAILED due to: Task failed task_XXX` | High | YARN task/job failure | Run `yarn logs -applicationId <appId>` to retrieve task stderr; fix application logic or data issue |
| `FATAL namenode.NameNode: Exiting due to loss of block replicas for <N> blocks` | Critical | Under-replicated blocks below minimum replication threshold | Re-enable DataNodes; check `dfs.replication.min`; run `hdfs fsck / -blocks -locations` |
| `WARN hdfs.DFSClient: No live nodes contain block BP-XXX` | Critical | All DataNodes holding a block's replicas are unavailable | Bring DataNodes back online; if permanent: `hdfs fsck / -delete` after data recovery attempts |
| `ERROR nodemanager.NodeManager: Resource localization failed for container` | High | YARN container cannot localize (download) application resources from HDFS | Check HDFS connectivity from NodeManager; verify HDFS health; check container tmp space |
| `WARN namenode.Standby: FATAL: Log roll for required HDFS files has failed` | Critical | JournalNode quorum lost; Standby NameNode cannot sync | Check JournalNode status on all 3 JournalNode hosts; restore quorum before next failover |
| `ERROR client.RetryInvocationHandler: Exception while invoking getBlockLocations: StandbyException` | High | Client routing to Standby NameNode; Active NameNode not accessible | Check HA status: `hdfs haadmin -getServiceState nn1`; check Zookeeper for fencing issue |
| `WARN namenode.NameNode: Replication Delay: Block <blkid> exceeds replication target: 0 replicas` | High | Block with zero live replicas (all DataNodes down or disk failures) | Investigate DataNode failures; if data is irretrievably lost, run `hdfs fsck -delete` |
| `ERROR security.UserGroupInformation: Login failed for user: <principal>` | High | Kerberos ticket expired or principal not found in KDC | Run `kinit <principal>` to renew; check KDC connectivity; verify keytab: `klist -kt <keytab>` |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| `SAFE_MODE_EXCEPTION` | NameNode in safemode; writes refused | No HDFS writes; YARN jobs fail to write output | Wait for automatic exit or force: `hdfs dfsadmin -safemode leave` (only after replication recovers) |
| `UnderReplicatedBlocks > 0` | One or more blocks below `dfs.replication` factor | Degraded data durability; no immediate data loss | Check DataNode count; wait for automatic re-replication; fix failing DataNodes |
| `CorruptBlocks > 0` | Checksum verification failed for at least one block | Affected files may be unreadable | `hdfs fsck / -list-corruptfileblocks`; restore from backup or delete corrupt files |
| `MissingBlocks > 0` | Block with zero replicas and no DataNode holding it | Permanent data loss for affected files | Restore from backup; `hdfs fsck / -delete` to remove unreachable files and unblock NameNode |
| `StandbyException` | Operation sent to Standby NameNode | Client operation fails; YARN jobs may fail | Verify Active NameNode via `hdfs haadmin -getServiceState`; fix ZKFC if automatic failover stuck |
| `ResourceManager: FAILED` (YARN app state) | YARN application exhausted retries | Job output lost; client gets failure exit code | `yarn logs -applicationId <id>` for root cause; retry after fixing data or code issue |
| `NodeManager: UNHEALTHY` | NodeManager health check script returned non-zero | Container scheduling avoided on this node | Check `yarn.nodemanager.health-checker.script.path` output; fix disk or resource issue |
| `DiskErrorException` | DataNode disk failed I/O checks; volume marked bad | Reduced replication on blocks stored on that volume | Replace failed disk; restart DataNode; re-commission with `hdfs dfsadmin -refreshNodes` |
| `QuotaExceededException` | HDFS space or name quota exceeded for directory | Writes to that directory tree fail | `hadoop fs -count -q <path>`; increase quota or remove data |
| `LeaseExpiredException` | HDFS client lease on a file expired before close | File may be corrupted or left open | Run `hdfs debug recoverLease -path <file>`; check writing client for failure |
| `GSSException: No valid credentials` | Kerberos credentials missing or expired for HDFS/YARN client | All authenticated HDFS/YARN operations fail | `kinit`; check clock skew (must be <5 min); verify `/etc/krb5.conf` realm settings |
| `EXCEEDED_MEMORY_LIMIT` | YARN container exceeded physical memory limit | Container killed mid-task | Increase `mapreduce.map.memory.mb` / `mapreduce.reduce.memory.mb`; optimize application memory use |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| NameNode Safemode Stuck | HDFS write throughput = 0; UnderReplicatedBlocks > 0 | `NameNode is in safe mode` for all write operations | HDFS writes failing alert | Insufficient DataNodes rejoined after restart; replication below threshold | Wait for DataNodes; force leave only when replication confirmed healthy |
| DataNode Mass Die-Off | Live DataNode count drops suddenly; under-replication spike | `DataNode <X> is dead` repeated for multiple nodes | Live DataNode count alert | Network switch failure or rack power outage removing multiple nodes | Restore network/power; DataNodes auto-reconnect; monitor block re-replication |
| Edit Log Gap / Corruption | NameNode fails to start; edit log gap exception | `gap in the edit log` FATAL on NameNode startup | NameNode down alert | JournalNode quorum failure during write causing missing edit log segment | Run `hdfs namenode -recover`; restore from secondary NN checkpoint |
| YARN Container OOM Cascade | Multiple jobs failing with `EXCEEDED_MEMORY_LIMIT`; NodeManagers restarting | Container killed events in NodeManager logs | Job failure rate spike alert | Physical memory misconfigured; containers fighting for memory on node | Increase container memory limits; reduce NodeManager concurrent containers; add nodes |
| Kerberos Ticket Expiry | All Hadoop service operations failing after 10-24 hours | `GSSException: No valid credentials` across services | Service authentication failure alert | Kerberos ticket renewal misconfigured or KDC unreachable at renewal time | Renew tickets; check `hadoop.kerberos.min.seconds.before.relogin`; fix KDC connectivity |
| HDFS Block Corruption | Corrupt block count increasing; specific files returning CRC errors | `Checksum mismatch` in DataNode logs; `CorruptBlocks > 0` in NN metrics | Corrupt block count alert | Disk hardware error on DataNode; bit rot; failed disk sector | Replace failing disk; `hdfs fsck -delete` for unrecoverable files; restore from backup |
| JournalNode Quorum Loss | Standby NameNode sync lag increasing; HA failover risk | `Log roll for required HDFS files has failed` WARN | JournalNode quorum alert | Majority of JournalNodes unreachable (network/crash) | Restore JournalNode services; verify quorum (need ≥ 2 of 3); investigate network |
| YARN Scheduler Deadlock | All YARN queues at capacity; no containers releasing; cluster idle | `SchedulerUtils: application stuck in ACCEPTED` in RM logs | YARN queue utilization alert | Long-running jobs holding all resources; scheduler fairness misconfigured | Kill stuck applications; review queue capacity and preemption config |
| NameNode Heap Exhaustion | NameNode GC pause time increasing; heap >90% | `GC overhead limit exceeded` or `OutOfMemoryError` in NN logs | NameNode JVM heap alert | Namespace too large for configured heap; too many small files | Increase `HADOOP_NAMENODE_OPTS` `-Xmx`; run `hdfs dfs -count -q /` (or read `FilesTotal` JMX bean) to assess namespace size |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `org.apache.hadoop.ipc.RemoteException: org.apache.hadoop.hdfs.server.namenode.SafeModeException` | Hadoop Java client, HDFS API | NameNode in safe mode after restart; replication below threshold | `hdfs dfsadmin -safemode get` | Wait for replication to recover; `hdfs dfsadmin -safemode leave` only when replication is confirmed healthy |
| `java.io.IOException: File could not be opened, file is not in the HDFS` | Hadoop Java client, Spark, Hive | File deleted between listing and read; or NameNode undergoing namespace compaction | Check HDFS path: `hdfs dfs -ls <path>`; review job timeline | Implement application-level retry with backoff; use speculative execution in Spark |
| `GSSException: No valid credentials provided` | Kerberos-enabled Hadoop client | Kerberos ticket expired or KDC unreachable | `klist` on client; `kinit -v`; test KDC connectivity | Renew ticket: `kinit -kt <keytab> <principal>`; ensure KDC is reachable from application host |
| `Connection refused` to NameNode RPC (port 8020/9000) | hdfs CLI, Hadoop Java client | NameNode service down; port blocked by firewall | `telnet <namenode> 8020`; `systemctl status hadoop-hdfs-namenode` | Restart NameNode; check firewall; verify NN not in standby without active NN |
| `YARN application stuck in ACCEPTED state` | YARN client (Spark, MR, Flink) | YARN resource queue at capacity; no free containers; scheduler deadlock | `yarn application -status <appId>`; `yarn queue -status <queueName>` | Kill lower-priority jobs; increase queue capacity; add NodeManager capacity |
| `org.apache.hadoop.yarn.exceptions.YarnException: Application killed by ResourceManager` | YARN client | Application exceeded memory or vcores limit; AM OOM; node health check failure | `yarn logs -applicationId <id>`; check NodeManager logs for OOM | Increase application memory settings; fix application memory leak; repair unhealthy node |
| `java.io.FileNotFoundException: File does not exist: /tmp/hadoop-yarn/staging` | Spark, MapReduce job submission | HDFS staging directory missing; permissions incorrect | `hdfs dfs -ls /tmp/hadoop-yarn/staging` | Re-create staging directory; fix permissions: `hdfs dfs -chmod -R 1777 /tmp` |
| `HDFS: DFSClient: No live nodes contain block` | Hadoop HDFS client | DataNodes hosting the block are down; under-replication not yet healed | `hdfs fsck <path> -blocks` | Wait for DataNodes to recover and re-replicate; restore from backup if blocks lost |
| `org.apache.hive.service.cli.HiveSQLException: Error while processing statement: FAILED: SemanticException` | Hive JDBC / Beeline | Hive metastore out of sync with HDFS; table partition metadata stale | `MSCK REPAIR TABLE <table>` in Hive; check Metastore logs | Run `MSCK REPAIR TABLE`; check Metastore DB connectivity; verify HDFS paths |
| `Task attempt failed: MapAttempt timed out after 600000ms` | MapReduce client | Slow mapper due to data skew or slow DataNode; GC pause on TaskTracker | Check task log for GC activity; `yarn logs -applicationId <id>` | Increase mapreduce task timeout; add combiner to reduce data skew; fix slow DataNode |
| `java.sql.SQLException: Timeout getting connection from pool` | Hive Metastore JDBC | Metastore DB connection pool exhausted; MySQL/PostgreSQL overloaded | Check Metastore DB connections: `SHOW PROCESSLIST` (MySQL) | Increase Metastore connection pool size; add DB read replica; reduce concurrent Hive sessions |
| `org.apache.hadoop.security.AccessControlException: Permission denied` | HDFS client | File permissions or POSIX ACL mismatch; Ranger/Sentry policy blocking | `hdfs dfs -ls -la <path>`; check Ranger audit logs | Fix HDFS permissions: `hdfs dfs -chmod`; update Ranger policy; check user group membership |

---

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| NameNode heap growth | NameNode JVM heap used % trending up; GC frequency increasing | JMX: `jstat -gcutil <namenode_pid> 5000`; `hdfs dfsadmin -report | grep Heap` | Weeks to months | Increase NameNode heap: `HADOOP_NAMENODE_OPTS=-Xmx<size>`; enable NameNode Federation; clean up old snapshots |
| HDFS small file accumulation | NameNode namespace object count growing; NN response latency increasing | `hdfs dfsadmin -report | grep "Files And Directories"`; target: keep below 100M objects | Months | Compact small files with HAR archives or SequenceFiles; enforce data lifecycle policies |
| DataNode disk fill rate | Disk usage per DataNode growing steadily; approaching configured DFS disk threshold | `hdfs dfsadmin -report` — check "DFS Remaining" per node | Days to weeks | Add DataNode capacity; enforce HDFS quotas per directory; run data retention cleanup jobs |
| YARN queue utilization creep | Queue capacity consistently above 80%; job wait times increasing | `yarn queue -status <queueName>` polled over time | Days | Add NodeManagers; rebalance queue capacity; enforce job resource request limits |
| Hive Metastore table partition explosion | Hive queries taking longer; Metastore DB size growing | `SELECT count(*) FROM PARTITIONS;` in Metastore DB | Months | Enable partition aging/cleanup; convert partition columns to fewer values; use partition pruning in queries |
| HDFS replication factor drift | Average block replication falling below target; `UnderReplicatedBlocks` increasing slowly | `hdfs dfsadmin -report | grep "Under Replicated"`; JMX `UnderReplicatedBlocks` metric | Weeks | Investigate DataNodes with low available space or connectivity issues; force replication check |
| MapReduce / Spark job shuffle disk pressure | Job duration increasing for shuffle-heavy jobs; spill ratio growing | Spark UI > Stages > Shuffle Read/Write; `iostat` on NodeManager hosts | Months as data grows | Add shuffle disk capacity; tune `spark.shuffle.spill.numElementsForceSpillThreshold`; use broadcast joins |
| Kerberos ticket renewal gap | Intermittent auth failures every 10/24 hours across long-running jobs | Correlate failures with ticket renewal intervals; `klist -v` for ticket lifetime | Ongoing | Configure `hadoop.kerberos.min.seconds.before.relogin`; use keytab-based renewal for long jobs |
| Zookeeper session timeout accumulation | HBase and HDFS HA failover becoming slower; Zookeeper watches growing | `echo mntr | nc localhost 2181 | grep zk_watch_count` | Weeks | Increase Zookeeper heap; reduce watch registrations; tune `tickTime` and `sessionTimeout` |
| Edit log segment growth | NameNode checkpoint interval too long; edit log consuming excess disk | `hdfs namenode -metadataVersion`; `ls -lh <dfs.namenode.edits.dir>` | Weeks | Reduce checkpoint interval: `dfs.namenode.checkpoint.period`; ensure Secondary/Standby NN checkpointing |

---

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Collects: HDFS status, DataNode health, YARN cluster info, NameNode JVM, replication stats
set -euo pipefail
NAMENODE_HTTP="${NAMENODE_HTTP:-http://localhost:9870}"
RM_HTTP="${RM_HTTP:-http://localhost:8088}"

echo "=== HDFS Service Status ==="
systemctl status hadoop-hdfs-namenode hadoop-hdfs-datanode --no-pager 2>/dev/null || \
  hdfs dfsadmin -report | head -30

echo "=== NameNode Safe Mode ==="
hdfs dfsadmin -safemode get

echo "=== HDFS Summary ==="
hdfs dfsadmin -report | grep -E "Live datanodes|Dead datanodes|Under Replicated|Corrupt|DFS Used|DFS Remaining"

echo "=== Under-Replicated and Corrupt Blocks ==="
hdfs dfsadmin -report | grep -E "Under Replicated|Corrupt Blocks|Missing Blocks"

echo "=== YARN Cluster Status ==="
yarn node -list -all 2>/dev/null | head -30

echo "=== YARN Queue Status ==="
yarn queue -status default 2>/dev/null

echo "=== Running YARN Applications ==="
yarn application -list 2>/dev/null | head -20

echo "=== NameNode JVM Heap (JMX) ==="
curl -sf "$NAMENODE_HTTP/jmx?qry=java.lang:type=Memory" 2>/dev/null | \
  jq '.beans[0] | {HeapMemoryUsage}' || echo "NameNode HTTP not accessible"

echo "=== DataNode Disk Usage ==="
curl -sf "$NAMENODE_HTTP/jmx?qry=Hadoop:service=NameNode,name=NameNodeInfo" 2>/dev/null | \
  jq '.beans[0].LiveNodes' | python3 -c "import sys,json; nodes=json.load(sys.stdin); [print(f'{k}: used={v[\"usedSpace\"]//1073741824}GB remaining={v[\"remaining\"]//1073741824}GB') for k,v in json.loads(nodes).items()]" 2>/dev/null || echo "Requires NameNode HTTP access"
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Diagnoses: slow jobs, data skew, DataNode latency, YARN queue wait times
set -euo pipefail
NAMENODE_HTTP="${NAMENODE_HTTP:-http://localhost:9870}"
RM_HTTP="${RM_HTTP:-http://localhost:8088}"

echo "=== HDFS Fsck Summary ==="
hdfs fsck / -files -blocks -locations 2>/dev/null | tail -20 || echo "Fsck requires HDFS access"

echo "=== Slow/Stuck YARN Applications ==="
yarn application -list -appStates RUNNING 2>/dev/null | awk 'NR>1 {print $0}'

echo "=== YARN Application Attempt Failures (recent) ==="
yarn application -list -appStates FAILED 2>/dev/null | head -20

echo "=== NameNode RPC Queue Depth (JMX) ==="
curl -sf "$NAMENODE_HTTP/jmx?qry=Hadoop:service=NameNode,name=RpcActivityForPort*" 2>/dev/null | \
  jq '.beans[] | {port: .tag.port, RpcProcessingTimeAvgTime, CallQueueLength}' || echo "JMX endpoint not accessible"

echo "=== DataNode Block Scanner Stats ==="
curl -sf "http://localhost:9864/jmx?qry=Hadoop:service=DataNode,name=DataNodeInfo" 2>/dev/null | \
  jq '.beans[0] | {VolumeInfo}' | head -20 || echo "DataNode JMX not accessible locally"

echo "=== HDFS Block Distribution (hot DataNodes) ==="
hdfs dfsadmin -report | grep -A3 "Name:" | grep -E "Name:|Decommission Status|Configured Capacity" | head -40

echo "=== MapReduce Job History (last 10 failed jobs) ==="
mapred job -list failed 2>/dev/null | head -15 || \
  curl -sf "$RM_HTTP/ws/v1/cluster/apps?state=FAILED&limit=10" 2>/dev/null | jq '[.apps.app[] | {id, name, finalStatus, diagnostics}]'
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Audits: Kerberos tickets, ZooKeeper quorum, port connectivity, Hive Metastore, HDFS quota
set -euo pipefail
NAMENODE="${NAMENODE_HOST:-namenode01}"
ZOOKEEPER="${ZK_HOSTS:-localhost:2181}"

echo "=== Kerberos Ticket Status ==="
klist 2>/dev/null || echo "No Kerberos tickets found — kinit required"

echo "=== NameNode Port Connectivity ==="
# 8020 = NN RPC, 9870 = NN HTTP UI, 8485 = JournalNode RPC
for port in 8020 9870 8485; do
  timeout 3 bash -c "echo >/dev/tcp/$NAMENODE/$port" 2>/dev/null && echo "Port $port: OPEN" || echo "Port $port: CLOSED/BLOCKED"
done

echo "=== ResourceManager Port Connectivity ==="
RM_HOST="${RM_HOST:-resourcemanager01}"
for port in 8032 8088 8030; do
  timeout 3 bash -c "echo >/dev/tcp/$RM_HOST/$port" 2>/dev/null && echo "RM Port $port: OPEN" || echo "RM Port $port: CLOSED/BLOCKED"
done

echo "=== ZooKeeper Quorum Health ==="
echo "ruok" | nc "$(echo $ZOOKEEPER | cut -d: -f1)" "$(echo $ZOOKEEPER | cut -d: -f2)" 2>/dev/null && echo "(ruok -> imok)" || echo "ZooKeeper not responding"
echo "mntr" | nc "$(echo $ZOOKEEPER | cut -d: -f1)" "$(echo $ZOOKEEPER | cut -d: -f2)" 2>/dev/null | grep -E "zk_server_state|zk_outstanding_requests|zk_pending_syncs"

echo "=== HDFS Directory Quotas ==="
hdfs dfs -count -q /user 2>/dev/null | sort -k2 -rn | head -20 || echo "HDFS access required"

echo "=== Hive Metastore Connectivity ==="
HIVE_METASTORE_PORT="${HIVE_METASTORE_PORT:-9083}"
HIVE_HOST="${HIVE_HOST:-localhost}"
timeout 5 bash -c "echo >/dev/tcp/$HIVE_HOST/$HIVE_METASTORE_PORT" 2>/dev/null && \
  echo "Hive Metastore port $HIVE_METASTORE_PORT: OPEN" || echo "Hive Metastore port $HIVE_METASTORE_PORT: CLOSED"

echo "=== HDFS HA State ==="
hdfs haadmin -getServiceState nn1 2>/dev/null; hdfs haadmin -getServiceState nn2 2>/dev/null || echo "HA not configured or haadmin not available"
```

---

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| YARN queue monopolization by one team | Other teams' jobs stuck in ACCEPTED; resource utilization skewed | `yarn application -list -appStates RUNNING`; check per-user/queue resource usage in RM UI | Kill or preempt lower-priority jobs; enable YARN preemption: `yarn.scheduler.capacity.preemption.enabled=true` | Configure separate YARN queues per team; set per-queue and per-user resource limits |
| NameNode RPC flooding by one job | NameNode CallQueueLength spike; other HDFS operations slow | JMX `CallQueueLength` metric; RPC audit log: `hdfs.audit` for high-frequency callers | Throttle the specific job; kill if necessary; increase NN RPC handler count temporarily | Set `ipc.server.max.connections` per client; use HDFS caching for hot directories |
| DataNode disk I/O saturation during replication | HDFS write throughput drops cluster-wide; replication queue growing | `iostat -xz 1` on DataNode hosts; `hdfs dfsadmin -report | grep "Blocks Scheduled"` | Reduce replication factor temporarily; throttle DataNode bandwidth: `dfs.datanode.balance.bandwidthPerSec` | Enable HDFS balancer throttling; distribute DataNodes across storage tiers |
| Spark shuffle data overwhelming NodeManager disk | Shuffle-heavy jobs consuming all NodeManager local disk; other jobs failing | `df -h` on NodeManager hosts; correlate with Spark UI shuffle write totals | Kill shuffle-heavy jobs; clean shuffle dirs: `rm -rf /var/yarn/nm-local-dir/usercache` | Separate shuffle disk from OS/log disk; set `spark.local.dir` to dedicated disks; use external shuffle service |
| Hive Metastore connection pool exhaustion | Hive queries fail with connection timeout; Beeline sessions hanging | `SHOW PROCESSLIST` in Metastore DB; check `datanucleus.connectionPool.maxPoolSize` | Kill idle Hive sessions; increase pool size in `hive-site.xml` | Set `hive.server2.session.check.interval`; enforce HiveServer2 connection limits per user |
| ZooKeeper watch storm during HDFS HA failover | ZooKeeper client errors across all services during failover; `ZooKeeperConnectionException` | `echo mntr | nc <zk> 2181 | grep zk_watch_count`; spike in watch count during failover | Increase ZooKeeper `maxClientCnxns`; stagger service ZK reconnect | Size ZooKeeper ensemble for total connected clients; use dedicated ZooKeeper for HDFS vs. HBase |
| HDFS balancer monopolizing network bandwidth | Network bandwidth on DataNode hosts saturated; job data transfer slow | `iftop` on DataNode NICs during balancer run; check balancer log | Throttle balancer: `hdfs balancer -threshold 20 -bandwidth 104857600` | Schedule balancer during off-peak; set persistent bandwidth limit in `hdfs-site.xml` |
| MapReduce speculative task doubling resource usage | YARN resource utilization spikes; queues at capacity from speculative tasks | YARN RM UI — tasks with `(speculative)` suffix; `mapreduce.map.speculative` setting | Disable speculation for specific jobs: `-Dmapreduce.map.speculative=false` | Tune speculation parameters; use Spark instead of MR where speculation overhead is costly |
| Large job consuming HDFS block report bandwidth | NameNode slow responding during large job startup; block report queue growing | JMX `BlocksTotal` and `BlockReportAverageTime` metrics on NameNode | Spread job execution start time; reduce block report frequency: `dfs.blockreport.intervalMsec` | Use HDFS Federation to distribute namespace load; configure delayed block reports for non-critical DataNodes |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| NameNode JVM heap OOM | NameNode stops responding to RPC → all HDFS operations hang → YARN jobs fail to launch (can't read job jars) → Hive/Spark/HBase fail → all downstream analytics dead | Entire HDFS cluster and all dependent services (Hive, HBase, Spark, YARN) | JMX `HeapMemoryUsage` at max before crash; `namenode.log`: `java.lang.OutOfMemoryError`; HDFS client: `org.apache.hadoop.ipc.RemoteException: StandbyException` | Fail over to Standby NameNode: `hdfs haadmin -failover nn1 nn2`; restart failed NN with increased heap |
| ZooKeeper quorum loss (>1 node down in 3-node ensemble) | HDFS HA fencing cannot proceed → split-brain risk → YARN ResourceManager cannot elect active → YARN jobs fail to submit | HDFS HA, YARN HA, HBase, any ZooKeeper-dependent service | ZK: `echo ruok | nc zk1 2181` times out; YARN/RM logs: `ZooKeeperConnectionException: HadoopZooKeeper`; HDFS: both NNs remain in standby | Restore ZooKeeper quorum by restarting failed ZK nodes; do NOT delete ZK data until quorum restored |
| YARN ResourceManager failure (non-HA) | No new YARN jobs accepted → running jobs continue until AM failure; killed jobs cannot restart → Spark streaming stops → Hive queries queue indefinitely | All new YARN job submissions; Spark streaming pipelines | YARN UI unreachable; `yarn application -list` returns `Connection refused`; alerts on `yarn_resourcemanager_numactivenms` | Enable YARN HA with ZooKeeper; restart RM; running AMs survive RM restart in Hadoop 2+ |
| DataNode mass failure (>33% cluster) | HDFS falls below replication factor → blocks under-replicated → reads fail for missing blocks → YARN jobs reading HDFS fail | All HDFS reads of under-replicated blocks; jobs requiring high data locality | HDFS `dfsadmin -report`: `Under replicated blocks > 0`; NameNode log: `ReplicationMonitor: blocks waiting to be replicated` | Restore DataNodes; HDFS auto-replicates; `hdfs fsck / -list-corruptfileblocks` to identify corruption |
| YARN NodeManager disk full | NodeManager containers fail to launch → running containers writing to local dir fail with `No space left on device` → job failures cascade | All YARN jobs assigned to that NM | `df -h /var/yarn/nm-local-dir`; YARN NM log: `DiskChecker: Exception in checking`; NM transitions to UNHEALTHY state | Stop NM, clear stale usercache/filecache under `yarn.nodemanager.local-dirs` (e.g., `rm -rf /var/yarn/nm-local-dir/usercache/*`), then restart NM; add disk or mount NFS |
| Hive Metastore DB connection pool exhausted | New Hive queries fail with `Failed to get schema`; existing queries using cached metadata continue | New Hive query submissions; schema discovery | Hive log: `Unable to open a test connection to the given database`; Metastore DB `show processlist` shows max connections | Increase `datanucleus.connectionPool.maxPoolSize`; kill idle Metastore connections; restart HiveServer2 |
| Kerberos KDC unreachable | All authenticated HDFS/YARN operations fail with `GSS initiate failed` → jobs cannot access data → cron-scheduled pipelines fail | All Kerberos-secured cluster services | `kinit` fails: `Cannot contact any KDC`; HDFS client: `GSSException: No valid credentials provided` | Use KDC replica; renew long-lived keytab credentials before KDC comes back; enable KDC HA |
| HDFS safe mode stuck after NN restart | HDFS read-only → no new YARN jobs can write output → incremental ETL pipelines stop | All HDFS writes and job output directories | `hdfs dfsadmin -safemode get` returns `ON`; NN log: `SafeModeMonitor: still waiting for` block reports | Wait for block reports or force leave: `hdfs dfsadmin -safemode forceExit` (only if cluster is healthy) |
| JournalNode quorum failure (HDFS HA) | Active NN cannot commit edits → NameNode enters safe mode → all HDFS writes blocked | HDFS writes; HBase WAL writes; YARN job output | NN log: `Unable to commit edit log segment`; JournalNode: only 1 of 3 responding | Restore at least 2/3 JournalNodes; on a freshly empty JN, copy `dfs.journalnode.edits.dir` from a healthy peer (with lagging JN stopped) so it can rejoin the quorum |
| HDFS Federation namespace saturation | One namespace NN OOM while others healthy → all clients using that namespace fail | HDFS clients mapped to saturated namespace | JMX `FilesTotal` at configured limit; NN log: `FSNamesystem: exceeded max namespace objects` | Add NameNode RAM; archive old files; reduce small-file count using HAR archives or SequenceFiles |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Hadoop version upgrade (e.g., 2.x → 3.x) | YARN classpath incompatibility; MapReduce jobs fail with `ClassNotFoundException`; HDFS block format change causes DataNode rejection | On first job submission or DN start after upgrade | Check `hadoop version` on all nodes; cross-reference job exception class with Hadoop changelog | Downgrade Hadoop RPMs/DEBs; restore `hadoop-env.sh` and `core-site.xml` from backup |
| `hdfs-site.xml` replication factor reduction | New files written with lower replication → data durability risk; re-replication not triggered for existing files | Immediate for new files after config push | `hdfs getconf -confKey dfs.replication`; check block reports for new files; config SCM diff | Restore original replication factor; re-replicate critical files: `hdfs setrep -w 3 /path/to/data` |
| YARN fair scheduler queue config change | Jobs assigned to wrong queue → wrong resource limits → SLA violations | Within 1 scheduler cycle after config reload | YARN RM log: `refreshQueues succeeded`; check queue assignments in RM UI before/after | `yarn rmadmin -refreshQueues`; restore `fair-scheduler.xml` from Git; `yarn rmadmin -refreshQueues` again |
| `core-site.xml` `fs.defaultFS` change | All HDFS clients fail with `No FileSystem for scheme` or connect to wrong NameNode | Immediate after service restart | Compare `fs.defaultFS` in `core-site.xml` across cluster nodes; check client error messages | Restore `core-site.xml` from config management; restart affected services |
| DataNode `dfs.data.dir` path change | DataNode cannot find existing block files → starts with empty storage → NN marks blocks as missing | On DataNode restart after change | DataNode log: `DataStorage.recoverTransitionAdminConfigToBlockPool: Block pool directory missing` | Revert `dfs.data.dir` to original path; restart DataNode; validate block count recovery |
| JVM GC algorithm change on NameNode | Increased GC pause duration → RPC timeouts from HDFS clients → intermittent `StandbyException` | Under load after change | NN GC log: long stop-the-world pauses; correlate with `hadoop.metrics2` NameNode RPC latency spike | Revert GC flags in `hadoop-env.sh`; restart NameNode; monitor with `jstat -gcutil` |
| YARN `yarn.nodemanager.resource.memory-mb` increase | Containers over-allocated RAM → NM host OOM → kernel OOM kills containers → jobs fail mid-run | Under full NM load after change | `dmesg | grep -i oom` on NM host; correlate with NM config change timestamp | Reduce `yarn.nodemanager.resource.memory-mb` back; restart NodeManagers; monitor with `free -h` |
| Kerberos keytab rotation without service restart | Services continue with expired keytab → authentication failures begin at ticket expiry (typically 24 hr) | At Kerberos ticket expiration (check `klist -e`) | `klist -k /etc/hadoop/conf/hdfs.keytab` — compare expiry; `kinit -k -t /etc/hadoop/conf/hdfs.keytab hdfs/<host>` fails | Re-distribute new keytab to all nodes; `kdestroy && kinit -k -t <keytab> <principal>` per service |
| Log aggregation path change in `yarn-site.xml` | Job history UI shows empty container logs; debugging requires SSH to NM | After first job completes after change | Compare `yarn.nodemanager.remote-app-log-dir` before/after; check `hdfs dfs -ls /app-logs` | Restore original HDFS log aggregation path; move existing aggregated logs if needed; restart NM |
| `mapreduce.task.timeout` reduction | Long-running Shuffle/Sort steps killed prematurely → MapReduce jobs fail with `Task timeout` | Within first long MR job after config change | MR job log: `Task timeout`; correlate task duration histogram with config change window | Revert timeout in `mapred-site.xml`; resubmit killed jobs |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| HDFS NameNode split-brain (both NNs active) | `hdfs haadmin -getServiceState nn1` and `hdfs haadmin -getServiceState nn2` — both return `active` | Two NNs accepting writes → block metadata diverges → data corruption risk | Severe: block list inconsistency; potential data loss or corruption on failback | Immediately fence the stale active (STONITH); determine correct primary by edit log recency; run `hdfs namenode -recover` on stale NN |
| HDFS edit log JournalNode lag | `curl -s http://<standby-nn>:9870/jmx?qry=Hadoop:service=NameNode,name=JournalTransactionInfo` for `LastJournalTxId` lag, or check JN logs for `lagging behind by N transactions` | Standby NN cannot catch up to active → failover dangerous → split-brain risk on automatic failover | HA failover quality degraded; failover may cause Standby to miss recent transactions | Investigate disk I/O on lagging JN; if a JN is missing edits, take it offline and bootstrap from a healthy peer (`hdfs namenode -bootstrapStandby` is for NN; for JN sync, copy `dfs.journalnode.edits.dir` from a healthy JN while the lagging one is stopped) |
| Corrupt HDFS blocks (hardware failure) | `hdfs fsck / -list-corruptfileblocks` | Jobs reading corrupt blocks fail with `ChecksumException`; HDFS reports `Corrupt blocks` in health | Data loss for files with no remaining healthy replicas | Restore from backup; delete corrupt blocks: `hdfs fsck / -delete` (only if backed up); trigger re-replication |
| YARN ResourceManager HA — stale ZK state after failover | `yarn rmadmin -getServiceState rm1` after failover — verify single active | Standby RM comes up with old job state; some running jobs may be double-counted | In-flight job status inconsistency; completed jobs marked as running | Let RM re-sync from running NMs (heartbeats); manually kill phantom jobs if needed |
| HDFS quota inconsistency after namespace migration | `hdfs dfs -count -q /user` — compare quota vs actual usage | Quota enforcement incorrect; some directories reject writes due to phantom quota violation | User jobs failing with `DiskQuotaExceededException` despite actual space being available | Run `hdfs dfsadmin -saveNamespace` then `hdfs fsck / -files -blocks -locations > /tmp/fsck.out`; repair quotas: `hdfs dfs -setSpaceQuota -1 /user/<dir>` then reset |
| Hive Metastore stale partition metadata | `hive -e "MSCK REPAIR TABLE <db>.<table>"` — check added/removed partitions | Hive queries return stale or incomplete results; new HDFS partitions not visible to Hive | Incorrect analytics results; ETL jobs producing data not queryable | Run `MSCK REPAIR TABLE` on affected tables; add to scheduled maintenance; use partition discovery |
| ZooKeeper data inconsistency between ensemble members | `echo dump | nc zk1 2181 | diff - <(echo dump | nc zk2 2181)` | Different ZK nodes return different data for HDFS/YARN leader election znodes | Leader election instability; HDFS HA may fence healthy NN | Identify lagging ZK member by `echo mntr | nc zk 2181 | grep zk_pending_syncs`; rolling restart of lagging member |
| HDFS block over-replication after DataNode re-add | `hdfs dfsadmin -report | grep "Over Replicated"` | Blocks have more replicas than `dfs.replication`; wastes storage | Minor: no data loss; excess storage consumed | HDFS auto-removes excess replicas; to accelerate: `hdfs dfsadmin -setBalancerBandwidth <bytes>` and run balancer |
| YARN container log aggregation failure (HDFS unavailable at aggregation time) | `yarn logs -applicationId <id>` returns `Log aggregation has not completed` | Container logs never uploaded to HDFS; lost after NM local dir cleanup | No post-mortem debugging capability for failed jobs | Retrieve from NM local before cleanup: `ssh <nm-host> "ls /var/yarn/nm-local-dir/usercache/<user>/appcache/<app-id>/"` |
| MapReduce speculation causing non-idempotent task re-execution | Duplicate output records in reducer output; job output count > input count | Duplicate rows in output directory; downstream aggregations inflated | Data quality corruption in downstream tables | Disable speculation: `mapreduce.map.speculative=false mapreduce.reduce.speculative=false`; rebuild affected output tables |

## Runbook Decision Trees

### Decision Tree 1: HDFS NameNode Safe Mode / Cluster Not Accessible
```
Is NameNode in Safe Mode?
├── YES → What is the block health status?
│         ├── Under-replicated blocks < threshold → Normal startup safe mode; wait for DNs to report: hdfs dfsadmin -safemode wait
│         └── Many missing/corrupt blocks → DO NOT leave safe mode blindly
│                   ├── Run fsck: hdfs fsck / -list-corruptfileblocks
│                   └── Corrupt files found?
│                       ├── YES → Delete corrupt files or restore from backup; then: hdfs dfsadmin -safemode leave
│                       └── NO  → Safe mode trigger is block count; check: hdfs dfsadmin -report | grep "Blocks:"
│                                  └── If enough DNs not yet checked in: wait or restart unreachable DNs
└── NO  → Is NameNode process running?
          ├── NO  → Check HA state: hdfs haadmin -getServiceState nn1; hdfs haadmin -getServiceState nn2
          │         ├── Both Standby → Force active: hdfs haadmin -transitionToActive nn1 --forcemanual
          │         └── Both dead → Full NN recovery: restore fsimage + edits (see DR Scenario 1)
          └── YES → Is NameNode RPC port responding?
                    ├── NO  → NN overloaded: check heap: jmap -histo $(cat /var/run/hadoop-hdfs/hdfs-namenode.pid) | head -20
                    │         └── OOM or GC pause → Increase NameNode Xmx in hadoop-env.sh; rolling restart
                    └── YES → Check client error: hdfs dfs -ls / 2>&1
                               └── Permission error → Check HDFS superuser; Kerberos ticket expired: kinit -kt /etc/security/hdfs.keytab hdfs
```

### Decision Tree 2: YARN Job Stuck / Not Starting
```
Is YARN ResourceManager accepting job submissions?
├── NO  → Is RM process running? (jps | grep ResourceManager)
│         ├── NO  → Check RM logs: tail -100 /var/log/hadoop-yarn/hadoop-yarn-resourcemanager-*.log | grep -E "ERROR|FATAL"
│         │         └── Restart RM: systemctl start hadoop-yarn-resourcemanager; check HA failover
│         └── YES → RM port blocked: curl -s http://<rm>:8032 || echo blocked
│                   └── Fix firewall or RM bind address in yarn-site.xml
└── YES → Is the job queued but not running?
          ├── YES → Check YARN queue capacity: curl -s http://<rm>:8088/ws/v1/cluster/scheduler | jq '.scheduler.schedulerInfo.queues'
          │         ├── Queue at 100% → Wait for running jobs to finish or preempt: yarn application -kill <app-id>
          │         └── Queue not full → Check NodeManager availability: yarn node -list | grep RUNNING
          │                              └── No NMs RUNNING → Restart NMs: for h in $(cat /etc/hadoop/conf/workers); do ssh $h "systemctl restart hadoop-yarn-nodemanager"; done
          └── NO  → Job running but stuck: yarn logs -applicationId <app-id> 2>&1 | grep -E "ERROR|Exception|killed"
                    ├── Container killed for exceeding memory → Increase mapreduce.map.memory.mb / mapreduce.reduce.memory.mb
                    └── Shuffle failure → Check intermediate data disks on NMs: df -h $(yarn node -status <nm> | grep "Local-dirs" | awk '{print $2}')
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| HDFS disk exhaustion | Uncontrolled data growth, no TTL on output dirs, pipeline reprocessing | `hdfs dfsadmin -report | grep -E "DFS Used|DFS Remaining"`; `hdfs dfs -du -s -h /user/* \| sort -rh \| head -20` | Cluster enters safe mode; all writes fail | `hdfs dfs -rm -r /tmp/*` and stale output dirs; `hdfs dfsadmin -setSpaceQuota 10t /user/<abuser>` | Set per-user space quotas; TTL job to purge `/tmp` and old pipeline outputs |
| NameNode heap explosion | Too many small files; millions of inodes; no HDFS federation | `curl -s http://nn:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystemState | jq .beans[0].FilesTotal` | NameNode GC pauses → slow metadata ops → cluster unusability | Merge small files with `hadoop archive`; delete redundant small file dirs | Enforce small file quotas; use HDFS federation or NameNode heap alerting |
| YARN queue starvation | Single team submitting excessive concurrent jobs | `curl -s http://rm:8088/ws/v1/cluster/apps?state=RUNNING | jq '[.apps.app[] | {user,name}] | group_by(.user) | map({user: .[0].user, count: length}) | sort_by(-.count)'` | Other teams' jobs queue indefinitely | Kill excess jobs: `yarn application -list -appStates RUNNING | grep <user>` then kill oldest; set per-user limits | Configure YARN capacity scheduler user-limit-factor and max-am-resource-percent |
| Speculative execution CPU waste | High speculation rate on slow clusters amplifying resource usage | `curl -s http://rm:8088/ws/v1/cluster/apps | jq '[.apps.app[] | .numNonAMContainerPreempted]'` | 2x compute cost; other jobs starved | Disable speculation for known-slow jobs: `mapreduce.map.speculative=false` in job conf | Tune `mapreduce.job.speculative.slowtaskthreshold`; disable on ETL jobs with skewed reducers |
| DataNode storage imbalance | Rebalancer never run; hot spots on some DNs | `hdfs dfsadmin -report | awk '/^Name:/{dn=$2} /DFS Used%/{print dn, $3}' | sort -t% -k1 -rn | head -10` | Hot DNs hit disk full; triggers block re-replication storm | Run balancer: `hdfs balancer -threshold 10` | Schedule weekly `hdfs balancer` via cron; alert when any DN > 85% used |
| Excessive replication factor | Pipelines writing with dfs.replication=5 on large datasets | `hdfs fsck /pipelines -files -blocks 2>/dev/null | grep -E "Under replicated|replication factor [4-9]"` | 5x storage amplification vs 3x default | Change replication: `hdfs dfs -setrep -R 3 /pipelines/<dir>` | Enforce default replication in client configs; audit per-dir replication in code review |
| MapReduce shuffle disk saturation | Many large reduce jobs writing shuffle data to same DN disks | `df -h $(grep mapreduce.cluster.local.dir /etc/hadoop/conf/mapred-site.xml | grep -oP '(?<=<value>)[^<]+')`; `iostat -x 5 3 | grep -v idle` | Job failures, DN instability | Kill shuffle-heavy jobs: `yarn application -kill <id>`; free space in local dirs | Separate shuffle disks from HDFS DN disks; use SSD for mapreduce.local.dir |
| Audit log volume | HDFS audit logging at INFO level; high throughput clusters generating GBs/hr | `du -sh /var/log/hadoop-hdfs/hdfs-audit.log*`; `wc -l /var/log/hadoop-hdfs/hdfs-audit.log` | Log disk exhaustion on NN host → NN crash | `logrotate -f /etc/logrotate.d/hadoop-hdfs`; set `dfs.namenode.audit.loggers=NullAuditLogger` temporarily | Use async audit logging; route audit logs to Graylog/Kafka; configure log4j rolling |
| Resource overcommit via container sizing | Jobs requesting 8GB containers when 2GB needed | `curl -s http://rm:8088/ws/v1/cluster/apps?state=RUNNING | jq '[.apps.app[] | {id, memorySeconds, vcoreSeconds}] | sort_by(-.memorySeconds)[:5]'` | NM memory pressure; fewer containers can run | Advise users to rightsize; set per-queue container size limits | Enforce `yarn.scheduler.maximum-allocation-mb` per queue; profile job memory usage |

## Latency & Performance Degradation Patterns
| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot DataNode (HDFS hot shard) | One DataNode handles disproportionate read/write load; clients experience latency | `hdfs dfsadmin -report | awk '/^Name:/{dn=$2} /DFS Used%/{print $3, dn}' | sort -rn | head -5`; `iostat -x 5 3 -p sdb` on hot DN | Poor block placement policy; replication factor imbalance after node addition | Run HDFS balancer: `hdfs balancer -threshold 5`; consider custom `BlockPlacementPolicy` |
| YARN connection pool exhaustion (AM → RM) | Jobs fail to start; ApplicationMaster logs `Failed to connect to ResourceManager` | `curl -s http://<rm>:8088/ws/v1/cluster/scheduler | jq '.scheduler.schedulerInfo.capacity'`; `netstat -an | grep 8032 | grep ESTABLISHED | wc -l` | Too many concurrent AMs attempting to register with RM; RM handler thread pool saturated | Set `yarn.resourcemanager.client.thread-count=64` in yarn-site.xml; limit concurrent AMs via queue `maximum-am-resource-percent` |
| NameNode GC / memory pressure | HDFS metadata operations slow; NameNode RPC queue grows | `jstat -gcutil $(cat /var/run/hadoop-hdfs/hdfs-namenode.pid) 2000 5`; `curl -s "http://<nn>:9870/jmx?qry=Hadoop:service=NameNode,name=JvmMetrics" | jq '.beans[0] | {GcTimeMillis, GcCount}'` | Too many small files inflating NameNode heap; inode cache pressure | Merge small files with HAR archives: `hadoop archive -archiveName data.har -p /user/data/*`; increase `-Xmx` in `hadoop-env.sh` |
| YARN NodeManager thread pool saturation | Container launch delays; NM logs `ContainerLaunch: timed out waiting for container` | `curl -s http://<nm>:8042/ws/v1/node/containers | jq '.containers.container | length'`; check `yarn.nodemanager.container-executor.class` config | Too many containers per NM; `yarn.nodemanager.resource.cpu-vcores` misconfigured | Limit containers per NM: `yarn.nodemanager.resource.cpu-vcores` to actual CPU count; lower `mapreduce.job.reduces` |
| MapReduce slow reducer due to data skew | Job completes all map tasks but hangs on few slow reducers | `yarn application -status <app-id>`; `mapred job -history <app-id> | grep "Reduce task" | sort -k5 -rn | head -5` | Skewed key distribution sending all records for one key to one reducer | Add combiner class; use `mapreduce.job.reduce.slowstart.completedmaps=0.95`; partition by salted key |
| CPU steal on HDFS DataNode | DN read throughput drops without increased load | `vmstat 1 10 | awk '{print $16}'`; `top -b -n1 -p $(pgrep -d, java)` on DN host | Co-located VMs competing for hypervisor CPU; DN on oversubscribed host | Migrate DN to bare metal or dedicated VM; pin JVM to NUMA node: `-XX:+UseNUMA` in `hadoop-env.sh` |
| HDFS NameNode RPC lock contention | Multiple threads queue behind `FSNamesystem` write lock; metadata operations serialize | `curl -s "http://<nn>:9870/jmx?qry=Hadoop:service=NameNode,name=NameNodeStatus" | jq '.beans[0].Safemode'`; `jstack $(cat /var/run/hadoop-hdfs/hdfs-namenode.pid) | grep -A5 "FSNamesystem\|writeLock"` | Global write lock on NameNode for all namespace mutations; high rename/delete rate | Batch rename/delete operations; use HDFS federation to split namespace; upgrade to NameNode read-lock patch |
| Java serialization overhead in MapReduce shuffle | Slow shuffle phase; high network I/O but low actual data throughput | `mapred job -history <app-id> | grep "Shuffle Errors"`; `yarn logs -applicationId <app-id> | grep "TIME_SPENT_ON_TASK_SERIALIZATION"` | Default Java serialization (WritableSerialization) slow for complex objects | Switch to SnappyCodec: `mapreduce.map.output.compress=true`; `mapreduce.map.output.compress.codec=org.apache.hadoop.io.compress.SnappyCodec` |
| HDFS block report storm after DataNode restart | All restarted DNs send full block reports simultaneously; NameNode RPC queue floods | `curl -s "http://<nn>:9870/jmx?qry=Hadoop:service=NameNode,name=NameNodeActivity" | jq '.beans[0].BlockReceivedAndDeletedNumOps'` | All DNs restart at same time after maintenance window; each sends full block report | Stagger DN restarts: `for dn in $(cat /etc/hadoop/conf/workers); do ssh $dn "systemctl restart hadoop-hdfs-datanode"; sleep 30; done` |
| Downstream dependency latency (Hive Metastore) | MapReduce or Spark jobs hang at planning phase; `MetaException: Unable to connect to metastore` | `beeline -e "show databases;" 2>&1 | grep -i "error\|timeout"`; `hive --service metatool -listFSRoot` | Hive Metastore DB (MySQL/PostgreSQL) slow or overloaded; connection pool exhausted | Restart HMS: `systemctl restart hive-metastore`; check HMS DB: `mysql -e "SHOW PROCESSLIST" | grep -v Sleep`; increase `datanucleus.connectionPool.maxPoolSize` |

## Network & TLS Failure Patterns
| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS cert expiry on Kerberos KDC | `kinit` fails with `Clock skew too great` or `Certificate expired`; all HDFS operations fail | `openssl x509 -noout -dates -in /etc/security/cacerts`; `klist -e` on Hadoop client | KDC TLS certificate or Hadoop SSL cert expired | Renew SSL cert in `ssl-server.xml` and `ssl-client.xml`; `keytool -importcert`; restart NameNode and DataNodes |
| mTLS (HDFS data transfer) failure after cert rotation | DataNode `DataXceiver` logs `javax.net.ssl.SSLHandshakeException`; block transfers fail | `openssl s_client -connect <dn>:9866 -CAfile /etc/hadoop/conf/hadoop-ca.crt` (port 50010 in Hadoop 2.x) | DataNode SSL keystore not reloaded after cert rotation | Rolling restart DataNodes to reload keystore; set `dfs.encrypt.data.transfer.cipher.suites` consistently on all nodes |
| DNS resolution failure for DataNode registration | DataNode fails to register with NameNode; `java.net.UnknownHostException: <dn-hostname>` in NN log | `dig $(hostname -f)` on DataNode host; check `/etc/hosts` on NameNode | Reverse DNS not configured for new DataNode; `/etc/hosts` inconsistency | Add DN hostname to `/etc/hosts` on NameNode and all clients; configure reverse DNS in BIND/Route53 |
| TCP connection exhaustion between client and NameNode | HDFS clients get `Could not obtain block: RPC queue is full` | `netstat -an | grep 8020 | grep ESTABLISHED | wc -l`; check NN RPC queue: `curl -s http://<nn>:9870/jmx?qry=Hadoop:service=NameNode,name=RpcActivityForPort8020` | Too many concurrent HDFS clients; NameNode RPC handler thread count insufficient | Increase `dfs.namenode.handler.count` (rule: 20 × log(cluster_size)); set `ipc.server.max.response.size` |
| YARN ResourceManager → NodeManager network isolation | NM unreachable from RM; containers allocated but never launched; NM health check fails | `curl -s http://<rm>:8088/ws/v1/cluster/nodes/<node-id> | jq '.node.nodeState'`; `ping <nm-host>` from RM | Network ACL or security group change blocking TCP 8042 (NM web) and 45454 (container manager) | Open TCP 8042 and 45454 in firewall; verify NM binds to correct interface: `yarn.nodemanager.localizer.address` |
| Packet loss during HDFS large block transfer | DataNode replication pipeline fails; block under-replicated; `WriteAbortedException` in client log | `tcpdump -i eth0 -nn 'tcp port 9866' -s 128 -c 100 2>/dev/null | grep RST` (port 50010 on Hadoop 2.x); `ifconfig eth0 | grep -i error` | NIC error or network congestion causing TCP retransmits; pipeline write fails after threshold | Switch to 3-way replication with shorter block size: `dfs.blocksize=67108864`; check NIC driver: `ethtool -S eth0 | grep error` |
| MTU mismatch in VXLAN/overlay network | Block transfers fail with `broken pipe`; small files work but large blocks fail | `ping -M do -s 8972 <dn-host>` (jumbo frame test); `ip link show eth0` | MTU set to 9000 on DN host but switch does not support jumbo frames | `ip link set eth0 mtu 1500`; or configure all switches for jumbo frames consistently |
| Firewall rule change blocking YARN shuffle port range | MapReduce shuffle fails with `java.io.IOException: error=111, Connection refused`; jobs fail at reduce phase | `nc -z <nm-host> 13562`; check `mapreduce.shuffle.port` config; `iptables -L INPUT -n | grep 13562` | Firewall closed shuffle TCP port (default 13562) after security review | Open TCP 13562 on NM hosts: `iptables -A INPUT -p tcp --dport 13562 -j ACCEPT` |
| SSL handshake timeout on NameNode Web UI | Hadoop UI not accessible; `curl -vI https://<nn>:9871` hangs for 30s | `openssl s_client -connect <nn>:9871 -debug 2>&1 | head -60` | TLS version mismatch between browser/client and Hadoop `ssl-server.xml` config | Add TLS 1.2 to `hadoop.ssl.enabled.protocols` in ssl-server.xml; restart NameNode |
| Kerberos connection reset due to clock skew | All Hadoop operations fail: `Failure unspecified at GSS-API level (Mechanism level: Clock skew too great)` | `ntpq -p`; `date` on client vs KDC; `kinit -V -k -t /etc/security/hdfs.keytab hdfs` | NTP drift > 5 minutes between Hadoop node and KDC | `ntpdate -u <ntp-server>`; configure chrony: `chronyd -q 'pool <ntp-server> iburst'`; ensure KDC and all nodes sync to same NTP |

## Resource Exhaustion Patterns
| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of NameNode JVM | NameNode process killed; HDFS enters safe mode; all file operations fail | `journalctl -k | grep -i "oom\|namenode"`; `dmesg | grep -i "oom_kill\|namenode"` | `systemctl start hadoop-hdfs-namenode`; after recovery: increase `-Xmx` in `hadoop-env.sh` NameNode opts | Configure `-XX:+HeapDumpOnOutOfMemoryError`; alert on NameNode heap > 80%: `curl -s http://<nn>:9870/jmx?qry=Hadoop:service=NameNode,name=JvmMetrics` |
| HDFS data partition full (DataNode) | DataNode logs `No space left on device`; DN marked as dead by NN; blocks under-replicated | `hdfs dfsadmin -report | grep -A5 "Name: <dn>"`; `df -h /data/*` on DN hosts | Data directories full; TTL-expired data not cleaned up | Delete old pipeline output dirs: `hdfs dfs -rm -r /user/<user>/output_old`; add new DN; run `hdfs balancer` |
| HDFS NameNode edit log partition full | NameNode shuts down; edit log cannot be written; cluster enters read-only state | `df -h /var/lib/hadoop-hdfs/name`; `du -sh /var/lib/hadoop-hdfs/name/current/edits_*` | Edit log directory on full partition; journal node disk exhausted | Free space by removing old fsimage/edits: keep only latest checkpoint; run `hdfs namenode -checkpoint`; mount on larger volume |
| File descriptor exhaustion on DataNode | DN logs `java.io.IOException: Too many open files`; block transfers fail | `cat /proc/$(pgrep -f DataNode)/limits | grep "open files"`; `ls /proc/$(pgrep -f DataNode)/fd | wc -l` | `HADOOP_DATANODE_OPTS` does not set `ulimit -n`; default system limit too low | `ulimit -n 65536` in DN startup; set `LimitNOFILE=65536` in systemd unit for hadoop-hdfs-datanode |
| Inode exhaustion on MapReduce local tmp directory | Container launches fail: `too many links`; `df -i /tmp/hadoop-yarn` | `df -i /tmp/hadoop-yarn`; `find /tmp/hadoop-yarn -type f | wc -l` | Millions of small mapper output files accumulating in `/tmp/hadoop-yarn`; inode limit hit | Delete stale tmp files: `find /tmp/hadoop-yarn -mtime +1 -delete`; configure NM local dirs on XFS with more inodes |
| YARN NM CPU throttle | Container CPU throttled in cgroup; jobs run slowly despite low system CPU | `cat /sys/fs/cgroup/cpu/yarn/<container-id>/cpu.stat | grep throttled`; `yarn node -status <nm-host>` | `yarn.nodemanager.resource.cpu-vcores` exceeds actual vcores; cgroup CPU quota too strict | Align `yarn.nodemanager.resource.cpu-vcores` with physical vCPU count; adjust cgroup CPU quota: `cgset -r cpu.cfs_quota_us=400000 yarn` |
| Swap exhaustion causing MapReduce shuffle OOM | Reducers killed during shuffle merge; `java.lang.OutOfMemoryError: GC overhead limit exceeded` | `vmstat 1 5 | awk '{print $7,$8}'`; `swapon -s` on NM hosts | Map output too large for in-memory sort buffer; spills to disk; swap consumed by parallel reduces | `swapoff -a && swapon -a`; reduce `mapreduce.reduce.memory.mb`; add RAM to NM hosts; enable `mapreduce.reduce.merge.inmem.threshold` |
| YARN kernel thread limit (container fork exhaustion) | NM cannot fork new containers; `Cannot fork new process` in NM log | `cat /proc/sys/kernel/threads-max`; `ps -eLf | wc -l` | System thread limit reached by many container JVMs forking subprocesses | `sysctl -w kernel.threads-max=1000000`; `sysctl -w kernel.pid_max=4194304`; reduce containers per NM |
| Network socket buffer saturation during HDFS replication | Block replication throughput capped; DataNode logs high write latency | `sysctl net.core.rmem_max net.core.wmem_max`; `netstat -s | grep -i "receive buffer\|overflow"` | Default socket buffers (128KB) insufficient for 10GbE DataNode-to-DataNode replication | `sysctl -w net.core.rmem_max=134217728`; `sysctl -w net.core.wmem_max=134217728`; persist in `/etc/sysctl.d/99-hadoop.conf` |
| Ephemeral port exhaustion on HDFS client host | HDFS client gets `Cannot assign requested address` on heavy parallel read workload | `ss -s | grep TIME-WAIT`; `sysctl net.ipv4.ip_local_port_range`; `netstat -an | grep 8020 | wc -l` | Each HDFS block read opens new TCP connection to NN and DN; TIME_WAIT ports exhausted | `sysctl -w net.ipv4.ip_local_port_range="1024 65535"`; `sysctl -w net.ipv4.tcp_tw_reuse=1`; increase `dfs.client.socketcache.capacity` |

## Distributed Transaction & Event Ordering Failures
| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| HDFS safe mode after partial write (idempotency violation) | Reprocessed pipeline writes duplicate output to HDFS; downstream Hive reads duplicates | `hdfs dfsadmin -safemode get`; check output dir: `hdfs dfs -count /pipeline/output/<date>` — files > expected | Duplicate records in Hive/HBase tables downstream; report inflation | Delete duplicate output: `hdfs dfs -rm -r /pipeline/output/<date>_retry`; use `hdfs dfs -checksum` to verify | Implement output commit protocol: write to `_tmp` dir and atomic rename; check for `_SUCCESS` marker before processing |
| YARN job partial failure leaving corrupt output | MapReduce job fails mid-run; partial output files written to HDFS output directory | `hdfs dfs -ls /output/dir | grep -v _SUCCESS`; `hadoop fs -get /output/dir/part-r-00001 /tmp/ && wc -l /tmp/part-r-00001` | Hive/Spark reading partial output silently returns wrong results | Delete partial output: `hdfs dfs -rm -r /output/dir`; re-run job with `mapreduce.job.abort.on.task.failure=true` | Use `FileOutputCommitter` v2 (`mapreduce.fileoutputcommitter.algorithm.version=2`) for atomic output commits |
| HDFS write pipeline partial failure causing block corruption | Client writes block to 3-DN pipeline; DN2 fails mid-write; block has 2 replicas and is corrupt | `hdfs fsck /path/to/file -files -blocks -locations 2>&1 | grep "CORRUPT"` | Corrupt block causes read failures for downstream consumers | `hdfs dfs -setrep 3 /path/to/file` to trigger re-replication; if still corrupt: delete and re-ingest source data | Monitor corrupt block count: `curl -s http://<nn>:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystemState | jq '.beans[0].CorruptBlocks'`; alert on > 0 |
| YARN ResourceManager failover causing running job state loss | HA RM failover during job execution; standby RM loses in-flight job state | `yarn rmadmin -checkHealth`; `yarn application -status <app-id>`; check RM active: `curl -s http://<rm1>:8088/ws/v1/cluster/info | jq .` | Running jobs in RUNNING state on old RM are abandoned; AMs must re-register | Re-submit failed jobs; enable RM recovery: `yarn.resourcemanager.recovery.enabled=true` with ZK state store | Configure ZK-based RM state store: `yarn.resourcemanager.store.class=org.apache.hadoop.yarn.server.resourcemanager.recovery.ZKRMStateStore` |
| NameNode HA split-brain during ZooKeeper partition | Both NN nodes think they are Active; two NNs accept writes; namespace diverges | `hdfs haadmin -getServiceState nn1`; `hdfs haadmin -getServiceState nn2` — both return Active | Namespace corruption; data loss when fencing restores single active NN | Run fencing: `hdfs haadmin -failover --forcefence nn1 nn2`; validate namespace with `hdfs fsck /` | Configure STONITH-based fencing; ensure `dfs.ha.fencing.methods` includes `sshfence` and a `shell(...)` fallback |
| YARN container launched with stale Kerberos ticket | Long-running container's service ticket expires mid-job; authentication to HDFS fails | `yarn logs -applicationId <app-id> | grep "Token is expired\|KerberosName\|GSSException"` | Container fails HDFS operations; data written so far is partial; job fails | Cancel and resubmit job; add token renewal: `mapreduce.job.hdfs-servers` with token delegation enabled | Set `yarn.resourcemanager.delegation.token.max-lifetime` to cover job duration; enable `mapreduce.job.token.tracking.ids` |
| Distributed lock expiry during Hive ALTER TABLE on HDFS | HCatalog lock expires mid-alter; table in partially renamed state in Hive Metastore | `hive --service metatool -listAllExternalTables`; `beeline -e "SHOW TABLES LIKE '<table>'"` | Downstream queries fail with `Table not found` or read old schema; ETL pipeline broken | Run `beeline -e "MSCK REPAIR TABLE <table>"` to sync HMS with HDFS; verify partition list | Increase `hive.lock.numretries` and `hive.lock.sleep.between.retries`; use Hive ACID tables for atomic schema changes |
| MapReduce output sort ordering violated by speculative execution | Speculative task produces output with different sort order than original; merged output is unsorted | `yarn logs -applicationId <app-id> | grep "speculative"`; validate sort: `hadoop fs -get /output/part-r-00000 /tmp/; sort -c /tmp/part-r-00000` | Downstream pipeline assuming sorted input reads wrong data; aggregation errors | Kill speculative tasks: `mapred job -kill-task <task-id>`; disable speculation: `mapreduce.map.speculative=false` | Disable speculative execution for sort-sensitive pipelines; validate output sort order in CI |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor from runaway MapReduce job | `yarn top` — one application consuming all cluster CPU vcores; `yarn application -list | grep RUNNING` | Other tenants' jobs queued; SLA breached for time-sensitive pipelines | Kill offending job: `yarn application -kill <app-id>` | Set per-queue vcore limits in YARN CapacityScheduler: `yarn.scheduler.capacity.<queue>.maximum-capacity=30`; enforce with admission control |
| Memory pressure from large Spark executor | `yarn application -status <app-id>` shows excessive container memory; `hdfs dfsadmin -report` shows DataNode memory alerts | Other YARN applications OOM'd out of cluster; NameNode GC pressure from memory alerts | Reduce executor memory: `yarn application -kill <app-id>`; resubmit with `--executor-memory 4G` | Set `yarn.scheduler.capacity.<queue>.maximum-am-resource-percent=0.2`; limit executor memory via queue resource limits |
| Disk I/O saturation from Hive bulk load | `iostat -x 2 5 -p sda` on DataNode — one user's distcp or bulk load saturating disk | All HDFS reads (other jobs) slow; DataNode heartbeats delayed; blocks marked stale | Throttle distcp: `hadoop distcp -bandwidth 50 <src> <dst>` — restart with bandwidth cap | Enable HDFS short-circuit read; use separate spindle disks per YARN queue via `yarn.nodemanager.local-dirs` mapping |
| Network bandwidth monopoly from large distcp | `iftop -n -P -i eth0 2>/dev/null` on DN host — one user's distcp consuming 10GbE | HDFS replication and other distcp jobs throttled; replication factor drops temporarily | Kill distcp job: `yarn application -kill <app-id>`; resubmit with `-bandwidth <mbps>` flag | Set default distcp bandwidth limit in cluster config; enforce via YARN queue label assignment |
| YARN connection pool starvation (AM → RM) | `netstat -an | grep 8032 | wc -l`; `curl -s http://<rm>:8088/ws/v1/cluster/apps?state=RUNNING | jq '.apps.app | length'` — too many concurrent AMs | Small jobs cannot acquire AM containers; stuck in `ACCEPTED` state indefinitely | Limit concurrent running apps per queue: `yarn.scheduler.capacity.<queue>.maximum-applications=50` applied via `yarn rmadmin -refreshQueues` | Set per-user AM resource limit: `yarn.scheduler.capacity.<queue>.user-limit-factor=1`; cap AM allocation |
| Quota enforcement gap for HDFS namespace | `hdfs dfsadmin -count -q /user/<tenant>`; `hdfs dfs -count -q /user/<tenant> | awk '{print $1,$2}'` — namespace quota exceeded | NameNode heap pressure from large namespace; other tenants' file creation slows | Set HDFS quota: `hdfs dfsadmin -setQuota 1000000 /user/<noisy-tenant>`; `hdfs dfsadmin -setSpaceQuota 10t /user/<noisy-tenant>` | Pre-provision HDFS quotas for all tenant home directories at cluster onboarding |
| Cross-tenant data leak risk via YARN log aggregation | `hdfs dfs -ls -R /app-logs/ | awk '{print $3,$8}'` — check if other tenants can read log dirs | Tenant A reads application logs for Tenant B; sensitive data in logs exposed | Restrict log directory permissions: `hdfs dfs -chmod 700 /app-logs/<user>/*`; verify with `hdfs dfs -ls /app-logs/<user>/` | Set `yarn.log.aggregation.file-formats` and restrict log aggregation dir permissions: `yarn.nodemanager.remote-app-log-dir-permissions=0700` |
| Rate limit bypass via parallel job submission | `curl -s "http://<rm>:8088/ws/v1/cluster/apps?state=SUBMITTED&user=<user>" | jq '.apps.app | length'` — one user submitting hundreds of jobs | Cluster scheduler overwhelmed; legitimate queue submissions delayed | Throttle user submissions: set `yarn.scheduler.capacity.<queue>.maximum-applications-per-user=100`; apply: `yarn rmadmin -refreshQueues` | Enable YARN queue ACLs: `yarn.scheduler.capacity.<queue>.acl_submit_applications=<allowed-users>`; enforce via LDAP groups |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| NameNode JMX scrape failure | HDFS capacity dashboards show stale data; NameNode heap pressure invisible | Prometheus JMX exporter on NameNode fails (port closed or JVM OOM killed exporter thread) | `curl -s http://<nn>:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystemState | jq '.beans[0].FilesTotal'` — direct JMX check | Restart JMX exporter; add alert: `up{job="hadoop-namenode"} == 0`; configure NameNode JMX port in `hadoop-env.sh` HADOOP_JMX_BASE |
| YARN ResourceManager metric gap during failover | RM metrics disappear during HA failover; queue utilization invisible for 1–5 minutes | Active RM changes during failover; Prometheus scrape target pointed at old active RM | Check RM HA state: `curl -s http://<rm1>:8088/ws/v1/cluster/info | jq .haState`; `curl -s http://<rm2>:8088/ws/v1/cluster/info | jq .haState` | Configure Prometheus with service discovery to scrape both RM nodes; filter by active state label |
| Trace sampling gap misses slow HDFS block placement | Block placement latency spikes during DataNode addition not captured; only 1% of RPC calls sampled | Low trace sampling misses NameNode block placement events; appears as intermittent client slowness | Enable NameNode RPC timing log: `log4j.logger.org.apache.hadoop.ipc.Server=DEBUG` temporarily; collect RPC stats from JMX | Configure NameNode RPC audit with timing: `dfs.namenode.audit.log.token.tracking.ids=true`; send audit log to Graylog |
| HDFS audit log silent drop on NameNode disk full | HDFS audit log stops recording; compliance gap undetected; operators notice only via disk alert | NameNode edit log partition fills; audit log write fails silently; `allowed=true` entries stop | `df -h /var/log/hadoop-hdfs/`; `tail -1 /var/log/hadoop-hdfs/hdfs-audit.log` — check last timestamp | Alert on NameNode log partition disk > 70%; mount audit log on separate volume; configure log rotation for audit log |
| YARN alert misconfiguration missing job failures | Alert fires on cluster CPU > 90% but not on individual job failure; SLA breaches go unnoticed | Job failure metrics not directly exposed; require querying ResourceManager REST API | Poll RM for failed apps: `curl -s "http://<rm>:8088/ws/v1/cluster/apps?states=FAILED&startedTimeBegin=$(date -d '1 hour ago' +%s000)" | jq '.apps.app | length'` | Add Prometheus recording rule counting failed YARN apps per queue per hour; alert threshold > 5 |
| Cardinality explosion from per-attempt YARN metrics | YARN metrics per container attempt create thousands of time series; Prometheus OOM | Each YARN container attempt generates unique label set; scrape interval too short captures every attempt | `curl -s http://<rm>:8088/ws/v1/cluster/metrics | jq .clusterMetrics.appsRunning` — use aggregate metric | Relabel YARN metrics to drop per-attempt labels; use YARN aggregate metrics only in Prometheus scrape config |
| Missing DataNode disk health monitoring | DataNode disk silently fills; DN marked dead; blocks under-replicated; discovered only when replication alert fires | DataNode disk fill rate not monitored; only capacity is alerted; gradual fill sneaks past threshold | `for dn in $(hdfs dfsadmin -report | grep "Name:" | awk '{print $2}'); do ssh ${dn%%:*} "df -h /data/*"; done` | Add per-disk utilization alert; configure DataNode `dfs.datanode.du.reserved` to reserve 10% and alert when remaining < reserved |
| Alertmanager outage during HDFS under-replication storm | 100 blocks go under-replicated; Prometheus fires alerts; Alertmanager is down; no pages sent | Alertmanager HA not configured; single Alertmanager instance; failure is silent | `curl -s http://<alertmanager>:9093/-/healthy`; check Prometheus alerting: `curl -s http://localhost:9090/api/v1/alerts | jq '.data[] | select(.labels.alertname=="HDFSUnderReplicatedBlocks")'` | Deploy Alertmanager in HA mode with 2+ instances; add direct PagerDuty integration as fallback in Prometheus `rule_files` |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor Hadoop version upgrade rollback (e.g., 3.3.4 → 3.3.6) | Post-upgrade DataNodes fail to register with NameNode; `IncorrectVersionException` in NN log | `journalctl -u hadoop-hdfs-namenode | grep -i "version\|incompatible\|IncorrectVersion"` | Downgrade DataNodes first: reinstall old package on each DN; then downgrade NameNode; verify registration | Use the official rolling-upgrade protocol (`hdfs dfsadmin -rollingUpgrade prepare/finalize`) so a downgrade is always possible until finalize is called |
| HDFS major version upgrade (2.x → 3.x) rollback | HDFS erasure coding enabled in v3 but v2 DNs cannot read EC blocks; data unavailable | `hdfs fsck / -blocks -files 2>&1 | grep "ERASURE_CODED"`; `hdfs dfsadmin -report | grep "EC Block Groups"` | Disable EC: `hdfs ec -disablePolicy RS-6-3-1024k`; convert EC files back to replicated: `hdfs dfs -setrep 3 <path>`; downgrade DNs | Convert all EC blocks to replicated before initiating downgrade; document EC block paths |
| HDFS fsimage schema migration partial completion | NameNode refuses to start after upgrade: `FSImage: unrecognized fsimage flag` | `journalctl -u hadoop-hdfs-namenode | grep -i "fsimage\|schema\|flag"`; `hdfs oiv -p XML -i /var/lib/hadoop-hdfs/name/current/fsimage_latest -o /tmp/fsimage.xml 2>&1 | head -20` | Restore from last valid fsimage: `cp /var/lib/hadoop-hdfs/name/current/fsimage.bak /var/lib/hadoop-hdfs/name/current/fsimage`; downgrade NN | Always checkpoint before upgrade: `hdfs dfsadmin -safemode enter; hdfs dfsadmin -saveNamespace; hdfs dfsadmin -safemode leave` |
| Rolling HDFS upgrade version skew causing block report failure | DNs on mixed versions; new-format block report rejected by old NameNode | `hdfs dfsadmin -report | grep -E "Decommission Status|Name:"`; `journalctl -u hadoop-hdfs-namenode | grep "BlockReport"` | Complete NN upgrade before starting DN upgrades; use `hdfs dfsadmin -rollingUpgrade started` protocol | Follow official rolling upgrade procedure: upgrade JournalNodes → StandbyNN → ActiveNN → then DataNodes sequentially |
| YARN ResourceManager ZK state migration gone wrong | After RM state store migration from FileSystemRMStateStore to ZKRMStateStore, running jobs lost | `yarn rmadmin -checkHealth`; `curl -s http://<rm>:8088/ws/v1/cluster/apps?state=RUNNING | jq .` — running apps missing | Restore RM state from filesystem backup; revert `yarn.resourcemanager.store.class` to old state store; restart RM | Export RM state before migration; test ZK state store in staging; drain all jobs before migrating state store |
| Hadoop config format change breaking XML parsing | YARN ResourceManager fails to start: `SAXParseException: invalid content` in yarn-site.xml | `xmllint --noout /etc/hadoop/conf/yarn-site.xml 2>&1` | Restore XML config from backup: `cp /etc/hadoop/conf/yarn-site.xml.bak /etc/hadoop/conf/yarn-site.xml`; restart RM | Always validate XML syntax before applying config changes: `xmllint --noout <config>.xml`; use configuration management system |
| Hive Metastore schema migration regression after Hadoop upgrade | HiveMetaStore schema version mismatch after Hadoop/Hive joint upgrade; HMS fails to start | `journalctl -u hive-metastore | grep -i "schema\|version\|upgrade"`; `mysql -e "SELECT VERSION FROM VERSION" hive` | Restore Hive metastore DB: `mysql hive < hive-metastore-pre-upgrade.sql`; reinstall old Hive version | Backup Hive metastore DB before any Hadoop/Hive upgrade: `mysqldump hive > hive-metastore-backup.sql` |
| YARN dependency conflict with updated Hadoop libraries | Custom YARN application fails with `ClassNotFoundException` after cluster upgrade; old JAR incompatible | `yarn logs -applicationId <app-id> | grep -i "ClassNotFound\|NoClassDefFound"`; `hadoop classpath | tr ':' '\n' | head -20` | Recompile application against new Hadoop version; or use `--libjars` to bundle old compatibility JARs | Always compile and test custom YARN applications against target Hadoop version before cluster upgrade; use Maven Shade plugin to bundle dependencies |

## Kernel/OS & Host-Level Failure Patterns

| Failure Mode | Impact on Hadoop | Detection Command | Remediation |
|-------------|-----------------|-------------------|-------------|
| OOM killer terminates NameNode or DataNode process | NameNode killed: entire HDFS unavailable, all jobs fail; DataNode killed: blocks on that node become under-replicated | `dmesg -T \| grep -i "oom-kill" \| grep -E "NameNode\|DataNode\|java"` on affected host; `hdfs dfsadmin -report \| grep "Dead datanodes"` | Increase Java heap: set `HADOOP_NAMENODE_OPTS=-Xmx16g` in `hadoop-env.sh`; configure `vm.overcommit_memory=2` on NameNode host; set DataNode `HADOOP_DATANODE_OPTS=-Xmx4g`; ensure NameNode host has dedicated RAM (no colocation) |
| Inode exhaustion on DataNode data directory | DataNode cannot create new block files; write operations fail; HDFS reports volume failure on that DataNode | `df -i /data/hdfs/dn` on each DataNode; `hdfs dfsadmin -report \| grep "Failed Volumes"` — non-zero indicates volume failure | Reformat data disk with more inodes: `mkfs.ext4 -i 4096 $DEV` (requires block migration); for immediate relief: clean orphaned block files: `hdfs dfsadmin -deleteBlockPool $DN_ID`; configure `dfs.datanode.data.dir` to use multiple disks |
| CPU steal on virtualized Hadoop nodes | MapReduce/Spark jobs run 3-5x slower; YARN container CPU time exceeds wall clock time; job SLAs missed | `sar -u 1 5 \| grep "steal"` on each node; `yarn application -status $APP_ID \| grep "elapsed"` vs expected runtime | Migrate Hadoop cluster to bare-metal or dedicated VMs; for cloud: use compute-optimized instances; configure YARN `yarn.nodemanager.resource.cpu-vcores` to account for steal overhead; add steal monitoring per node |
| NTP skew causing HDFS lease expiry and YARN token failures | HDFS lease recovery triggered prematurely; clients lose file handles; YARN delegation tokens expire early; jobs fail with `InvalidToken` | `for H in $HOSTS; do ssh $H "chronyc tracking \| grep 'System time'"; done`; `hdfs dfsadmin -metasave /tmp/meta.txt && grep "lease" /tmp/meta.txt \| wc -l` | Sync NTP on all cluster hosts: `ansible all -m command -a "chronyc makestep"`; verify: `for H in $HOSTS; do ssh $H "date -u"; done \| sort`; increase HDFS lease soft limit: `dfs.namenode.lease-recheck-interval-ms=30000` |
| File descriptor exhaustion on NameNode | NameNode cannot accept new RPC connections; DataNode heartbeats fail; HDFS goes into safe mode; all jobs blocked | `cat /proc/$(pgrep -f NameNode)/limits \| grep "open files"`; `ls /proc/$(pgrep -f NameNode)/fd \| wc -l`; `hdfs dfsadmin -safemode get` | Increase fd limits: `ulimit -n 1048576` in `hadoop-env.sh`; add `LimitNOFILE=1048576` to NameNode systemd unit; set `fs.file-max=2097152` in sysctl.conf; reduce number of small files via HAR archive or file compaction |
| Conntrack table saturation on Hadoop hosts | DataNode-to-DataNode replication connections fail; block replication pipeline breaks; under-replicated blocks increase | `sysctl net.netfilter.nf_conntrack_count` vs `net.netfilter.nf_conntrack_max`; `dmesg \| grep "nf_conntrack: table full"` on DataNode hosts | Increase conntrack: `sysctl -w net.netfilter.nf_conntrack_max=1048576`; reduce timeout: `sysctl -w net.netfilter.nf_conntrack_tcp_timeout_established=300`; for dedicated Hadoop hosts: disable conntrack on DN ports (Hadoop 3.x: `--dport 9866:9867`; Hadoop 2.x: `--dport 50010:50020`) with `iptables -t raw -A PREROUTING -p tcp --dport <range> -j NOTRACK` |
| Kernel panic on NameNode host | Entire HDFS cluster unavailable; all MapReduce/Spark/Hive jobs fail; data inaccessible until NameNode restarts | `journalctl -k --since "1 hour ago" \| grep -i "panic\|BUG\|oops"` post-reboot; `hdfs haadmin -getServiceState nn1` — check HA failover occurred | Enable NameNode HA: verify standby took over with `hdfs haadmin -getServiceState nn2`; enable kdump on NameNode host; configure automatic NameNode startup: `systemctl enable hadoop-hdfs-namenode`; monitor with `hdfs dfsadmin -safemode get` after restart |
| NUMA imbalance on NameNode or ResourceManager host | NameNode RPC latency varies significantly; some RPCs take 5ms, others 50ms; ResourceManager scheduling latency inconsistent | `numactl --hardware` on NN/RM host; `numastat -p $(pgrep -f NameNode)` — check for cross-NUMA memory access | Pin JVM to single NUMA node: `numactl --cpunodebind=0 --membind=0 hdfs namenode`; set JVM `-XX:+UseNUMA` flag in `HADOOP_NAMENODE_OPTS`; ensure `shared_buffers` equivalent (JVM heap) fits within single NUMA node |

## Deployment Pipeline & GitOps Failure Patterns

| Failure Mode | Impact on Hadoop | Detection Command | Remediation |
|-------------|-----------------|-------------------|-------------|
| Image pull failure for Hadoop container (Docker/K8s-based deployment) | Hadoop container pod stuck in `ImagePullBackOff`; DataNode or NodeManager cannot start; cluster capacity reduced | `kubectl describe pod $POD -n hadoop \| grep -A3 "ImagePullBackOff"`; `docker pull $HADOOP_IMAGE 2>&1 \| grep -E "toomanyrequests\|unauthorized"` | Use private registry mirror; pre-pull images: `for H in $HOSTS; do ssh $H "docker pull $REGISTRY/hadoop:$VERSION"; done`; for bare-metal: verify RPM/DEB package repository availability: `yum repolist \| grep hadoop` |
| Auth failure during Hadoop package repository access | `yum install hadoop-hdfs` fails with `401 Unauthorized`; cluster upgrade blocked; new nodes cannot join | `yum repolist enabled \| grep hadoop`; `curl -I $HADOOP_REPO_URL 2>&1 \| grep "HTTP/"` — check for 401/403 | Update repo credentials: `yum-config-manager --save --setopt=hadoop-repo.username=$USER --setopt=hadoop-repo.password=$PASS`; for Cloudera/HDP: refresh subscription: `subscription-manager refresh` |
| Helm drift between Git and live Hadoop cluster config | Hadoop config files (`hdfs-site.xml`, `yarn-site.xml`) manually edited on nodes; drift from Git-managed config | `for H in $HOSTS; do ssh $H "md5sum /etc/hadoop/conf/hdfs-site.xml"; done \| sort -k1 \| uniq -c` — check for inconsistent checksums | Use Ansible/Puppet to enforce config from Git: `ansible hadoop-all -m copy -a "src=hdfs-site.xml dest=/etc/hadoop/conf/hdfs-site.xml" --check --diff`; add scheduled drift detection job; version config files in Git |
| Ansible/config sync stuck during rolling Hadoop upgrade | Ansible playbook for rolling upgrade hangs at DataNode decommission step; partially upgraded cluster in mixed state | `ansible-playbook hadoop-upgrade.yml --list-tasks`; `hdfs dfsadmin -report \| grep "Decommission"` — check if DN stuck in `Decommission in Progress` | Cancel decommission: `hdfs dfsadmin -cancelDecommission $DN_HOST`; complete upgrade manually: `for H in $REMAINING; do ssh $H "yum install hadoop-hdfs-datanode-$VERSION && systemctl restart hadoop-hdfs-datanode"; done` |
| PDB/maintenance window blocking Hadoop node drain (K8s-based) | Kubernetes PDB prevents Hadoop pod eviction during node drain; cluster upgrade stalled | `kubectl get pdb -n hadoop -o json \| jq '.items[] \| select(.status.disruptionsAllowed==0)'`; `kubectl get nodes \| grep SchedulingDisabled` | For bare-metal: decommission DataNode before maintenance: `hdfs dfsadmin -decommission $DN_HOST`; wait for blocks to replicate: `hdfs dfsadmin -report \| grep "$DN_HOST"` until `Decommissioned`; then power down host |
| Blue-green cluster cutover failure | Traffic switched to new Hadoop cluster; new NameNode missing blocks; HDFS reports under-replicated blocks; Hive queries fail | `hdfs fsck / -files -blocks \| grep -E "Under-replicated\|Missing"` on new cluster; `hdfs dfsadmin -report \| grep "Under replicated"` | Switch traffic back to old cluster; verify DistCp completed: `hadoop distcp -status $DISTCP_JOB`; re-run missing transfers: `hadoop distcp -update hdfs://$OLD_NN/ hdfs://$NEW_NN/`; verify with `hdfs fsck / -files` before cutover |
| ConfigMap/config drift between Hadoop nodes | `hdfs-site.xml` differs between NameNode and DataNodes; replication factor mismatch; blocks placed incorrectly | `for H in $HOSTS; do ssh $H "xmllint --xpath '//property[name=\"dfs.replication\"]/value/text()' /etc/hadoop/conf/hdfs-site.xml"; done` — check for inconsistent values | Push consistent config from Git: `ansible hadoop-all -m copy -a "src=hdfs-site.xml dest=/etc/hadoop/conf/hdfs-site.xml"`; restart affected daemons: `ansible hadoop-datanodes -m command -a "systemctl restart hadoop-hdfs-datanode"`; validate with `hdfs getconf -confKey dfs.replication` |
| Feature flag misconfiguration enabling HDFS erasure coding on wrong directory | EC policy applied to directory requiring fast random reads; read performance degrades 3x; HBase/Hive queries slow | `hdfs ec -getPolicy -path /user/hive`; `hdfs ec -listPolicies`; `hdfs dfs -stat "%r %o %b" /user/hive/warehouse/table/part_0000` | Revert EC policy: `hdfs ec -unsetPolicy -path /user/hive`; convert existing EC files back to replicated: `hdfs dfs -setrep 3 /user/hive/warehouse/`; add validation: only apply EC to cold storage paths; document EC-eligible directories |

## Service Mesh & API Gateway Edge Cases

| Failure Mode | Impact on Hadoop | Detection Command | Remediation |
|-------------|-----------------|-------------------|-------------|
| Circuit breaker trips on Hadoop REST API during job surge | Knox gateway or reverse proxy circuit breaker trips during job submission surge; WebHDFS and YARN REST API return 503 | `curl -s -o /dev/null -w "%{http_code}" "https://$KNOX_HOST:8443/gateway/default/webhdfs/v1/?op=LISTSTATUS"` — returns 503; check Knox logs: `tail -50 /var/log/knox/gateway.log \| grep "circuit\|breaker"` | Increase Knox thread pool: set `gateway.httpclient.maxConnections=256` in Knox `gateway-site.xml`; increase circuit breaker threshold; add YARN REST API rate limiting at application level instead of proxy level |
| Rate limiting blocks HDFS REST API calls | WebHDFS operations throttled by API gateway; ETL pipelines that use WebHDFS fail intermittently with 429 | `curl -s -o /dev/null -w "%{http_code}" "http://$NN_HOST:9870/webhdfs/v1/?op=LISTSTATUS"` — 429 indicates throttling; check NameNode log: `tail -100 /var/log/hadoop-hdfs/hadoop-hdfs-namenode-*.log \| grep "throttle\|reject"` | Use native HDFS client instead of WebHDFS for bulk operations; increase NameNode handler count: `dfs.namenode.handler.count=100` in `hdfs-site.xml`; add batch retry with backoff in ETL pipeline |
| Stale service discovery for Hadoop HA NameNode | Client connects to standby NameNode after failover; reads succeed but writes fail with `StandbyException`; application retries to wrong node | `hdfs haadmin -getServiceState nn1 && hdfs haadmin -getServiceState nn2` — identify which is active; `hdfs getconf -namenodes` — check client-side config | Update `dfs.namenode.rpc-address.$NSID.nn1` and `nn2` if IPs changed; verify ZooKeeper-based failover: `echo stat \| nc $ZK_HOST 2181`; check ZKFC: `hdfs zkfc -formatZK` if ZK state corrupted; restart HDFS client applications to refresh NameNode cache |
| mTLS/Kerberos rotation breaks Hadoop inter-service communication | Kerberos keytab renewal fails; DataNode cannot authenticate to NameNode; `GSSException: No valid credentials` in DataNode log | `klist -kt /etc/hadoop/conf/hdfs.keytab \| tail -5` — check keytab validity; `kinit -kt /etc/hadoop/conf/hdfs.keytab hdfs/$HOSTNAME` — test authentication; `journalctl -u hadoop-hdfs-datanode \| grep "GSSException"` | Regenerate keytabs: `kadmin.local -q "ktadd -k /etc/hadoop/conf/hdfs.keytab hdfs/$HOSTNAME"`; distribute to all hosts: `ansible hadoop-all -m copy -a "src=hdfs.keytab dest=/etc/hadoop/conf/hdfs.keytab"`; restart HDFS daemons |
| Retry storm from MapReduce speculative execution | Speculative execution spawns duplicate tasks; each speculative task hits same hot HDFS blocks; NameNode RPC queue saturated | `hdfs dfsadmin -metasave /tmp/meta.txt && wc -l /tmp/meta.txt` — high line count indicates RPC pressure; `yarn application -status $APP_ID \| grep "Speculative"` | Disable speculative execution for I/O-heavy jobs: `mapreduce.map.speculative=false` and `mapreduce.reduce.speculative=false` in `mapred-site.xml`; increase NameNode handler count: `dfs.namenode.handler.count=128`; add backpressure in application |
| gRPC/RPC keepalive mismatch between NameNode and DataNode | DataNode connections to NameNode drop silently; heartbeat gaps cause NameNode to declare DataNode dead; blocks under-replicated | `hdfs dfsadmin -report \| grep "Dead datanodes"`; `journalctl -u hadoop-hdfs-datanode \| grep -E "timeout\|heartbeat\|connection reset"` | Increase heartbeat interval: `dfs.heartbeat.interval=3` (default) in `hdfs-site.xml`; increase NameNode staleness: `dfs.namenode.stale.datanode.interval=60000`; check firewall/NAT timeout between NN and DN: ensure idle timeout > heartbeat interval |
| Trace context propagation lost in Hadoop job execution | Distributed traces break at MapReduce/Spark task boundary; cannot correlate HDFS reads with job-level traces | `yarn logs -applicationId $APP_ID \| grep -E "trace.id\|span.id\|x-request-id" \| head -10`; check HTrace config: `grep htrace /etc/hadoop/conf/hdfs-site.xml` | Enable HTrace in Hadoop: add `dfs.htrace.spanreceiver.classes=org.apache.htrace.core.StandardOutSpanReceiver` to `hdfs-site.xml`; for Spark: configure `spark.opentelemetry.enabled=true`; integrate with Jaeger/Zipkin backend |
| Load balancer health check fails for Hadoop Knox gateway | LB health check to Knox `/gateway/default/healthcheck` fails; all WebHDFS/YARN REST traffic dropped | `curl -s -o /dev/null -w "%{http_code}" "https://$KNOX_HOST:8443/gateway/default/healthcheck"`; `tail -20 /var/log/knox/gateway.log \| grep -E "error\|fail\|health"` | Fix Knox health endpoint: verify topology descriptor includes health service; update LB health check path: `https://$KNOX_HOST:8443/gateway/manager/admin/api/v1/version`; increase LB health check timeout to accommodate Knox startup time |
