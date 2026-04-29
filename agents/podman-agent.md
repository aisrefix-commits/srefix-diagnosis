---
name: podman-agent
description: >
  Podman specialist agent. Handles rootless container runtime issues
  including networking problems, storage exhaustion, Quadlet/systemd
  integration, pod management, and Buildah/Skopeo operations.
model: haiku
color: "#892CA0"
skills:
  - podman/podman
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-podman-agent
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

You are the Podman Agent — the rootless container runtime expert. When any
alert involves Podman containers, pods, storage, networking, or systemd
integration, you are dispatched to diagnose and remediate.

# Activation Triggers

- Alert tags contain `podman`, `container`, `quadlet`, `buildah`, `skopeo`
- Metrics from Podman stats or systemd journal
- Error messages contain Podman terms (rootless, slirp4netns, conmon, overlay)

### Cluster / Service Visibility

Quick health overview:

```bash
# All container states (rootless — run as the service user)
podman ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'
podman ps -a --filter "status=exited" --format '{{.Names}}: {{.Status}} (exit {{.ExitCode}})'

# Pod status
podman pod ps

# Resource utilization
podman stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.BlockIO}}'

# Storage disk usage
podman system df
df -h ~/.local/share/containers   # rootless storage
df -h /var/lib/containers         # rootful storage

# Network status
podman network ls
podman network inspect podman | jq '.[0] | {driver, subnets: .subnets[].subnet}'

# Systemd/Quadlet service health
systemctl --user status 'podman-*.service'   # rootless
systemctl status 'podman-*.service'          # rootful

# Admin endpoints (Podman API socket)
# Rootless: unix://$XDG_RUNTIME_DIR/podman/podman.sock
# Rootful: unix:///run/podman/podman.sock
curl --unix-socket /run/podman/podman.sock http://localhost/v4.0.0/libpod/info | jq .version
```

### Global Diagnosis Protocol

**Step 1 — Container / pod state sweep**
```bash
podman ps -a --format '{{.Names}} {{.Status}}' | grep -v " Up "
podman pod ps | grep -v Running
# For systemd-managed containers:
systemctl --user status 'container-*.service' 2>/dev/null | grep -E "Active|failed"
```

**Step 2 — Storage backend health**
```bash
podman info | jq '{graphDriver: .store.graphDriverName, graphRoot: .store.graphRoot, volumePath: .store.volumePath}'
df -h $(podman info --format '{{.Store.GraphRoot}}')
podman system df
```

**Step 3 — Data consistency (rootless UID mapping, overlay driver)**
```bash
# Check subuid/subgid allocation for rootless user
grep $USER /etc/subuid /etc/subgid
podman unshare cat /proc/self/uid_map
# Overlay support check
podman info | jq .host.overlayFsFuse
```

**Step 4 — Resource pressure (disk, memory, network)**
```bash
podman stats --no-stream
df -h ~/.local/share/containers/storage  # rootless
# Check for OOMKilled containers
podman inspect $(podman ps -aq) --format '{{.Name}}: OOMKilled={{.State.OOMKilled}}' 2>/dev/null | grep true
```

**Output severity:**
- CRITICAL: all containers exited/failed, storage full preventing starts, subuid/subgid mapping broken, conmon not found
- WARNING: one or more containers OOMKilled, storage > 85%, rootless networking degraded, Quadlet service failing
- OK: all expected containers Up, storage < 70%, healthy network, systemd services active

---

## Prometheus / podman-exporter Metrics and Alert Thresholds

Podman exposes container metrics via `podman-exporter` (a separate community exporter,
available at `ghcr.io/containers/prometheus-podman-exporter`) or via systemd
`podman generate systemd`. For rootless containers, per-user cgroup metrics are
visible under `/sys/fs/cgroup/user.slice/user-<uid>.slice/`.

| Metric | Source | Description | WARNING | CRITICAL |
|--------|--------|-------------|---------|----------|
| `podman_container_state` == 4 (running) | podman-exporter | Container running state (4=running) | — | state != 4 for expected container |
| `podman_container_cpu_seconds_total` rate(5m) | podman-exporter | CPU seconds consumed per container | > 80% of limit | > 95% of limit |
| `podman_container_mem_usage_bytes` / `podman_container_mem_limit_bytes` | podman-exporter | Memory utilization ratio | > 0.85 | > 0.95 |
| `podman_container_oom_events_total` rate(5m) | podman-exporter | OOM kill events | > 0 | > 0 |
| `podman_container_block_output_bytes_total` rate(5m) | podman-exporter | Container write throughput | > 100 MB/s | > 500 MB/s |
| `podman_container_net_output_bytes_total` rate(5m) | podman-exporter | Container network transmit throughput | > 500 MB/s | > 1 GB/s |
| `podman_container_net_input_errors_total` rate(5m) | podman-exporter | Network receive errors | > 0.1/s | > 1/s |
| Storage root free bytes | node-exporter | Free space on `graphRoot` volume | < 20% free | < 5% free |
| `systemd_unit_active_state` for container services | systemd-exporter | Systemd service active state | failed | failed for > 1 min |
| `podman_container_exit_code` != 0 | podman-exporter | Container last exit code | != 0 | != 0 for critical services |

### PromQL Alert Expressions

```yaml
# Container OOM event (any occurrence is critical for production workloads)
- alert: PodmanContainerOOMKilled
  expr: rate(podman_container_oom_events_total[5m]) > 0
  for: 0m
  labels:
    severity: critical
  annotations:
    summary: "OOM kill in Podman container {{ $labels.name }}"

# Memory utilization > 85% of configured limit
- alert: PodmanMemoryPressure
  expr: |
    (
      podman_container_mem_usage_bytes
      / podman_container_mem_limit_bytes > 0
    ) > 0.85
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Container {{ $labels.name }} memory at {{ $value | humanizePercentage }}"

# Container not in running state (state != 4)
- alert: PodmanContainerNotRunning
  expr: podman_container_state{name=~".*critical.*"} != 4
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "Critical Podman container {{ $labels.name }} not running"

# Quadlet/systemd service failed
- alert: PodmanSystemdServiceFailed
  expr: |
    systemd_unit_active_state{name=~"container-.+\\.service"} == 5
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: "Podman systemd service {{ $labels.name }} failed"

# Storage root approaching full (< 15% free)
- alert: PodmanStorageLow
  expr: |
    (
      node_filesystem_avail_bytes{mountpoint="/var/lib/containers"}
      / node_filesystem_size_bytes{mountpoint="/var/lib/containers"}
    ) < 0.15
  for: 5m
  labels:
    severity: warning
```

---

### Focused Diagnostics

#### Scenario 1: Container OOM Kill Loop

- **Symptoms:** Container repeatedly restarting; `OOMKilled: true` in inspect; application truncated logs; exit code 137
- **Metrics to check:** `podman_container_oom_events_total` rate > 0; `podman_container_mem_usage_bytes / podman_container_mem_limit_bytes > 0.95`
- **Diagnosis:**
  ```bash
  podman inspect <container> | jq '.[0].State | {OOMKilled, ExitCode, RestartCount}'
  podman inspect <container> | jq '.[0].HostConfig.Memory'
  podman stats <container> --no-stream
  # Host-level OOM evidence
  dmesg | grep -i "oom\|killed process" | tail -20
  journalctl -k | grep -i oom | tail -10
  # Rootless cgroup memory events
  cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/memory.events
  ```
- **Indicators:** `OOMKilled: true`; exit code 137; kernel OOM log entry; `memory.events` shows `oom_kill` count increasing
- **Quick fix:** Increase limit: `podman update --memory 2g <container>`; for Quadlet, set `Memory=2g` in `.container` file; investigate memory leak; add JVM `-Xmx` for Java workloads

#### Scenario 2: Image Pull Failure Causing Deployment Stall

- **Symptoms:** `podman pull` fails; Quadlet service fails on first start; `ImagePullBackOff`-equivalent from journal
- **Metrics to check:** `podman_container_state` stays in created state; systemd service failed events
- **Diagnosis:**
  ```bash
  podman pull <image>                            # Manual pull; shows exact error
  # Check registry configuration
  cat /etc/containers/registries.conf           # System-wide registry config
  cat ~/.config/containers/registries.conf      # User-level (rootless)
  # Check credentials
  cat ~/.config/containers/auth.json | jq 'keys'
  skopeo inspect docker://<image>               # Inspect without pulling
  # Network connectivity
  curl -v https://<registry>/v2/                # Registry API reachable?
  # DNS from user namespace (rootless)
  podman run --rm busybox nslookup <registry>
  ```
- **Indicators:** `unauthorized: authentication required`; `dial tcp: i/o timeout`; `x509: certificate signed by unknown authority`; missing `[registries.insecure]` entry
- **Quick fix:** Re-login: `podman login <registry>`; add insecure registry to `registries.conf`; copy CA cert to `/etc/pki/ca-trust/source/anchors/` and run `update-ca-trust`; use `skopeo copy` for cross-registry transfers

#### Scenario 3: Rootless Networking Failure (slirp4netns / pasta)

- **Symptoms:** Containers cannot reach external networks; port forwarding not working; `slirp4netns` or `pasta` crashes
- **Metrics to check:** `podman_container_net_input_errors_total` and `net_output_errors_total` rate; container state transitions (running -> exited)
- **Diagnosis:**
  ```bash
  podman info | jq .host.networkBackend
  podman network inspect podman | jq '.[0].network_interface'
  # Test DNS and connectivity inside container
  podman exec <container> curl -s https://8.8.8.8 --connect-timeout 3
  podman exec <container> cat /etc/resolv.conf
  # Check slirp4netns process
  ps aux | grep slirp4netns
  journalctl --user -u container-<name>.service | grep -i "network\|slirp\|pasta\|port" | tail -20
  # User namespace support
  cat /proc/sys/user/max_user_namespaces
  ```
- **Indicators:** `slirp4netns` not in `$PATH`; `pasta` binary missing; kernel user namespaces disabled (`max_user_namespaces` = 0)
- **Quick fix:** Install `slirp4netns` or `passt` package; enable user namespaces: `sysctl user.max_user_namespaces=28633`; switch backend: `podman network create --driver=bridge mynet`; for pasta backend: `podman system reset --network`

#### Scenario 4: Rootless UID Mapping / Permission Failure

- **Symptoms:** Containers fail to start with `newuidmap` error; files inside container have wrong ownership; volumes mounted with permission denied
- **Diagnosis:**
  ```bash
  grep $USER /etc/subuid /etc/subgid
  # Must have at least 65536 UIDs allocated
  podman unshare id
  ls -la ~/.local/share/containers/storage/overlay/
  # Check newuidmap binary
  which newuidmap && ls -la $(which newuidmap)   # must be setuid
  ```
- **Indicators:** `newuidmap: write to uid_map failed: Operation not permitted`; `/etc/subuid` missing entry for user; `newuidmap` not setuid root
- **Quick fix:**
  ```bash
  usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $USER
  # Fix newuidmap permissions (run as root):
  chmod u+s $(which newuidmap) $(which newgidmap)
  podman system migrate   # after subuid changes
  ```

#### Scenario 5: Storage Full / Image Accumulation

- **Symptoms:** Container starts fail with "no space left on device"; image pulls fail; `podman build` fails
- **Metrics to check:** Storage root free space < 5%; `podman_container_block_output_bytes_total` rate spike from a single container (log storm)
- **Diagnosis:**
  ```bash
  podman system df
  df -h ~/.local/share/containers
  podman images --filter "dangling=true"
  podman container ls -a --format '{{.Names}} {{.Status}}' | grep Exited | wc -l
  # Find large images
  podman images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | sort -k2 -rh | head -10
  ```
- **Indicators:** Available space < 1 GB; many dangling/untagged images; many exited containers not cleaned up
- **Quick fix:**
  ```bash
  podman system prune -f --volumes   # remove unused containers, images, volumes, networks
  podman image prune -a --filter "until=168h"
  # Move storage root to larger disk: edit ~/.config/containers/storage.conf
  # graphRoot = "/mnt/bigdisk/containers"
  ```

#### Scenario 6: Quadlet / Systemd Service Restart Loop

- **Symptoms:** Container service fails repeatedly; `systemctl status container-<name>.service` shows `failed`; `podman auto-update` not working
- **Metrics to check:** `systemd_unit_active_state{name="container-<name>.service"}` == 5 (failed)
- **Diagnosis:**
  ```bash
  systemctl --user status container-<name>.service
  journalctl --user -u container-<name>.service -n 50
  cat ~/.config/containers/systemd/<name>.container   # Quadlet unit
  podman container inspect <name> --format '{{.State.Status}} ExitCode={{.State.ExitCode}}'
  ```
- **Indicators:** `ExecStartPre` failing; image not found locally; volume mount path doesn't exist; health check failing before service considered active
- **Quick fix:** Pre-pull the image: `podman pull <image>`; ensure volume directories exist: `mkdir -p <volume-path>`; check Quadlet file syntax with `podman generate systemd --name <container>`; reload: `systemctl --user daemon-reload && systemctl --user restart container-<name>.service`

---

#### Scenario 7: Pod Infra Container Failure Causing All Pod Containers to Stop

**Symptoms:** All containers in a Podman pod stop simultaneously; `podman pod ps` shows pod in `Exited` or `Degraded` state; containers were healthy before; log shows `infra container <id> has exited`

**Root Cause Decision Tree:**
- Infra container OOMKilled → pod namespace controller killed by kernel; all containers in pod lose shared namespaces
- Infra container stopped manually → `podman stop <infra-container-id>` cascades to all
- Pause image pull failure after system restart → infra container cannot start, pod never initializes
- crun/runc crash during infra container lifecycle → container runtime error cascades
- Infra container port conflict → another process bound the port before infra container started

**Diagnosis:**
```bash
podman pod ps -a                                   # Pod state (Exited, Degraded)
podman pod inspect <pod-name> | jq '.[0] | {State: .State, InfraContainerID: .InfraContainerID}'
# Infra container state
INFRA=$(podman pod inspect <pod-name> | jq -r '.[0].InfraContainerID')
podman inspect $INFRA | jq '.[0].State | {Status, OOMKilled, ExitCode, Error}'
# Infra container logs
podman logs $INFRA 2>&1 | tail -20
# OOM evidence
dmesg | grep -i "oom\|killed" | tail -10
# Port conflict check
podman pod inspect <pod-name> | jq '.[0].InfraConfig.PortBindings'
ss -tlnp | grep <port>
# Pause image availability
podman images | grep pause
podman info | jq '.host.ociRuntime'
```

**Thresholds:**
- WARNING: Infra container restart count > 0; pod in Degraded state with some containers running
- CRITICAL: Pod in Exited state; all containers stopped; infra container OOMKilled; port binding failure

#### Scenario 8: Systemd Unit File Generated by podman generate systemd Failing After Update

**Symptoms:** Container service that worked previously fails after Podman upgrade; `systemctl status container-<name>.service` shows `failed`; journal shows `podman: unknown flag` or `deprecated command`; service was generated from old Podman version

**Root Cause Decision Tree:**
- `podman generate systemd` deprecated in Podman >= 4.4 → generated unit uses old `podman run` flags removed in newer version
- Container flags changed between Podman versions → `--cgroupns` or `--ipc` syntax changed
- Unit file uses `--replace` flag not available in older Podman → upgrade path broken
- Quadlet now preferred but old `generate systemd` unit not compatible with new runtime behavior
- `ExecStartPre` pulling image no longer works due to registry credential format change

**Diagnosis:**
```bash
systemctl --user status container-<name>.service   # or system-level without --user
journalctl --user -u container-<name>.service -n 50
# Check Podman version vs when unit was generated
podman --version
grep "# generated by podman" ~/.config/systemd/user/container-<name>.service
# Test the ExecStart command directly
grep ExecStart ~/.config/systemd/user/container-<name>.service | \
  sed 's/^ExecStart=//' | bash -x 2>&1 | head -30
# Check for deprecated flags
podman run --help 2>&1 | grep -i deprecated
# Validate Quadlet alternative
ls ~/.config/containers/systemd/*.container 2>/dev/null
```

**Thresholds:**
- WARNING: Service fails on first attempt but succeeds on retry; deprecated flag warnings in logs
- CRITICAL: Service permanently failed; container not starting; no rollback path to old unit file

#### Scenario 9: Volume Permission Denied in Rootless Mode (User Namespace Mapping)

**Symptoms:** Container fails to write to mounted volume; logs show `permission denied` on `/mnt/<volume>` inside container; `ls -la` inside container shows volume owned by `nfsnobody` or `nobody`; works in rootful mode

**Root Cause Decision Tree:**
- Host directory owned by UID 1000, container runs as UID 0 → mapped to host UID 100000 in subuid range; host dir not writable by that UID
- Volume mount path exists but owned by wrong user → container UID 1000 maps to host UID 101000, not the dir owner
- Named volume permissions set at creation time as root → after switching to rootless, wrong ownership
- Container image runs as non-root UID (e.g., 999) → that UID maps to a host UID that has no permission on the bind mount
- SELinux labels on bind mount path → `:Z` or `:z` relabeling not applied

**Diagnosis:**
```bash
# Check container UID mapping
podman unshare cat /proc/self/uid_map
# UID inside container vs host mapping
podman exec <container> id
podman inspect <container> | jq '.[0] | {User: .Config.User, IDMappings: .HostConfig.IDMappings}'
# Host-side ownership of the bind mount
podman inspect <container> | jq -r '.[0].Mounts[] | select(.Type=="bind") | .Source' | xargs ls -la
# What UID owns the directory from the host's perspective
stat -c "%u %g" <host-dir>
# Subuid mapping for rootless user
grep $USER /etc/subuid
# SELinux context
ls -Z <host-dir> 2>/dev/null
```

**Thresholds:**
- WARNING: Volume writes failing; container starts but cannot write logs or data
- CRITICAL: Application crashes due to inability to write config or data directory; data loss risk

#### Scenario 10: Quadlet Container Not Starting (Systemd .container File Syntax)

**Symptoms:** `systemctl --user start container-<name>.service` fails with `Unit not found` or `Failed to start`; Quadlet unit file created but service not generated; journal shows `podman-systemd.unit` parse errors

**Root Cause Decision Tree:**
- `.container` file in wrong directory → Quadlet looks in `~/.config/containers/systemd/` (rootless) or `/etc/containers/systemd/` (rootful)
- Missing required `Image=` field → Quadlet unit generation fails silently
- Syntax error in Quadlet file (e.g., wrong section name `[container]` vs `[Container]`) → unit file not generated
- `systemctl --user daemon-reload` not run after creating `.container` file → service unit not visible to systemd
- `podman-auto-update.timer` dependency error → service requires Podman timer but not installed

**Diagnosis:**
```bash
# Check Quadlet file location and content
ls -la ~/.config/containers/systemd/              # rootless
ls -la /etc/containers/systemd/                   # rootful
cat ~/.config/containers/systemd/<name>.container
# Run Quadlet generator manually to check for parse errors
/usr/lib/systemd/system-generators/podman-system-generator \
  --user ~/.config/containers/systemd/ /tmp/quadlet-test/ 2>&1
# Check if unit was generated
ls /run/user/$(id -u)/systemd/generator/          # rootless generated units
ls /run/systemd/generator/                        # rootful generated units
# Service status after daemon-reload
systemctl --user daemon-reload
systemctl --user status container-<name>.service
journalctl --user -u container-<name>.service -n 30
```

**Thresholds:**
- WARNING: Unit file missing from generated directory; service in failed state on first start
- CRITICAL: Quadlet generator failing for all `.container` files; no services starting; `podman --version` incompatible

#### Scenario 11: Image Manifest List Resolution Failure in Air-Gapped Environment

**Symptoms:** `podman pull <image>` fails with `manifest unknown` or `unsupported manifest type`; image is available in local registry mirror but pull still fails; images work in connected environment but not air-gapped

**Root Cause Decision Tree:**
- Multi-arch manifest list not mirrored completely → only amd64 layer mirrored, arm64 attempted
- Mirror registry does not support OCI manifest list → returns single-arch manifest unexpectedly
- `registries.conf` mirror entry exists but image not present in mirror → falls back to Docker Hub (blocked)
- Image tagged as `:latest` but mirror only has digest-pinned copy → tag not found
- TLS certificate mismatch on internal registry → pull fails at HTTPS handshake before manifest fetch

**Diagnosis:**
```bash
# Test pull with verbose output
podman pull --log-level=debug <image> 2>&1 | grep -E "manifest|digest|trying|error" | tail -30
# Check registry mirror config
cat /etc/containers/registries.conf
cat ~/.config/containers/registries.conf
# Inspect image in mirror using skopeo (no pull)
skopeo inspect --tls-verify=false docker://<mirror-registry>/<image> 2>&1
# Check available architectures in mirror
skopeo inspect --raw docker://<mirror-registry>/<image> | jq '.manifests[] | {platform, digest}'
# TLS certificate for mirror
openssl s_client -connect <mirror-host>:443 -servername <mirror-host> 2>/dev/null | openssl x509 -noout -subject -dates
# Can reach mirror at all?
curl -v https://<mirror-host>/v2/ 2>&1 | grep -E "HTTP|error|200|401"
```

**Thresholds:**
- WARNING: Pull failing with fallback to Docker Hub (which is blocked); mirror partially populated
- CRITICAL: All image pulls failing; air-gapped cluster cannot start new workloads; mirror registry unreachable

#### Scenario 12: Healthcheck Reporting Unhealthy Causing Auto-Restart Loop

**Symptoms:** Container starts then enters restart loop; `podman inspect <container>` shows `Health.Status = "unhealthy"`; `podman inspect` shows `RestartCount` incrementing; application actually works but restart disrupts service

**Root Cause Decision Tree:**
- Health check command exits with non-zero even when service is functioning → overly strict check or wrong exit code
- `StartPeriod` too short → health check runs before application fully initialized
- Health check interval too frequent with slow health endpoint → check timeout causes unhealthy before response
- Health check uses internal DNS that fails on first check (network not fully up) → unnecessary restart
- `Retries` set to 1 → single failure immediately marks container unhealthy and triggers restart

**Diagnosis:**
```bash
podman inspect <container> | jq '.[0].State.Health | {Status, FailingStreak, Log: [.Log[-5:][].Output]}'
# Run health check manually inside container
HC_CMD=$(podman inspect <container> | jq -r '.[0].Config.Healthcheck.Test[]' | tail -n +2 | tr '\n' ' ')
podman exec <container> bash -c "$HC_CMD"; echo "Exit: $?"
# Health check configuration
podman inspect <container> | jq '.[0].Config.Healthcheck'
# Restart policy
podman inspect <container> | jq '.[0].HostConfig.RestartPolicy'
# Container logs around restart events
podman logs --since 10m <container> | tail -30
journalctl --user -u container-<name>.service | grep -i "unhealthy\|health\|restart" | tail -20
```

**Thresholds:**
- WARNING: `FailingStreak >= 2`; health check timeout approaching; restarts > 2 in 10 min
- CRITICAL: Container in constant restart loop; `RestartCount > 10`; service unavailable due to restarts

#### Scenario 13: Podman Containers Rejected by Kubernetes Admission Webhook After Migration from Docker (Resource Limit Enforcement)

**Symptoms:** Containers that ran fine under Docker Compose or standalone Podman in staging are rejected in production Kubernetes with `admission webhook denied the request: container must have resource limits set`; `kubectl apply` returns `Forbidden: containers must not run as root` even though the same Quadlet/Compose spec worked in staging; images built with Buildah pass local scan but fail OPA/Gatekeeper policy in production.

**Root Cause:** The production Kubernetes cluster enforces OPA Gatekeeper or Kyverno policies requiring all containers to declare CPU/memory `limits`, run as a non-root UID, and have a read-only root filesystem. Docker Compose and standalone Podman do not enforce these constraints at admission time — the workload runs without limits until it reaches Kubernetes. Additionally, images built with `buildah` using `FROM scratch` or multi-stage builds may set `USER root` implicitly at the final stage, causing the `MustRunAsNonRoot` constraint to fail.

**Diagnosis:**
```bash
# Check which admission policies are active in the target namespace
kubectl get constrainttemplate -o name
kubectl get constraint -A -o json | jq '.items[] | {kind: .kind, name: .metadata.name, violations: .status.totalViolations}'

# Describe the specific violation for a rejected pod
kubectl describe pod <rejected-pod-name> -n production 2>/dev/null || \
  kubectl get events -n production --field-selector reason=FailedCreate | tail -20

# Check the image for USER directive
podman inspect <image> --format '{{.Config.User}}'
# If empty or "root" or "0", it will fail MustRunAsNonRoot

# Inspect current resource limits in the container/pod spec
podman inspect <container> | jq '.[0].HostConfig | {Memory, CpuShares, NanoCpus}'

# Check if image was built without explicit USER
podman history <image> --format '{{.CreatedBy}}' | grep -i user

# Dry-run apply to surface admission errors before deployment
kubectl apply --dry-run=server -f pod.yaml 2>&1 | grep -E "admission|denied|constraint|policy"

# List all Gatekeeper/Kyverno violations in namespace
kubectl get constraint -A -o jsonpath='{range .items[*]}{.kind}/{.metadata.name}: violations={.status.totalViolations}{"\n"}{end}'
```

**Thresholds:**
- WARNING: Pod spec missing resource requests; admission webhook warnings logged but not blocking
- CRITICAL: Pod rejected at admission; service cannot start; Gatekeeper enforcement mode active

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `ERRO[0000] unable to write pod event: "write unixgram /run/systemd/journal/socket: sendmsg: no buffer space available"` | journald buffer full | `journalctl --vacuum-size=1G` |
| `Error: failed to mount overlay for xxx: using mount program fuse-overlayfs` | FUSE mount failure in rootless mode | `podman info \| grep graphDriver` |
| `WARN[0000] Failed to detect if xxx is running in a user namespace` | rootless mode detection issue | `podman info \| grep rootless` |
| `Error: OCI runtime error: xxx permission denied` | SELinux or seccomp blocking syscall | `ausearch -m avc -ts recent` |
| `Error: cannot connect to Podman socket: stat /run/xxx: no such file or directory` | Podman socket not active | `systemctl --user start podman.socket` |
| `Error: creating exec session: xxx: OCI runtime exec failed` | target container is not running | `podman ps -a \| grep <container>` |
| `Error: short-name "xxx" did not resolve to an alias` | image registry prefix not specified | `cat /etc/containers/registries.conf` |
| `WARN[0000] Container xxx is already stopped` | redundant stop on already-exited container | `podman ps -a` |
| `Error: error creating container storage: the container name "xxx" is already in use` | stale container with same name | `podman rm <container>` |
| `Error: failed to create shim: OCI runtime create failed: rootfs_linux.go: xxx: no such file or directory` | image layer missing or corrupt | `podman image inspect <image>` |

# Capabilities

1. **Container management** — Lifecycle, resource limits, healthchecks, restarts
2. **Rootless operations** — UID mapping, port binding, cgroup delegation
3. **Networking** — slirp4netns/pasta, custom networks, pod networking
4. **Storage** — Image management, volume lifecycle, cleanup and pruning
5. **Systemd/Quadlet** — Service files, auto-update, linger configuration
6. **Image operations** — Buildah builds, Skopeo transfers, registry auth

# Critical Metrics to Check First

1. `podman_container_oom_events_total` rate — any OOM kill is immediately critical
2. `podman_container_mem_usage_bytes / podman_container_mem_limit_bytes` — memory pressure ratio (> 0.85 = warning)
3. Container running state — exited or OOMKilled containers need attention
4. Storage root free space — full disk blocks all container operations
5. Systemd service state — failed Quadlet services mean workloads are down
6. Network connectivity — rootless networking issues affect external access

# Output

Standard diagnosis/mitigation format. Always include: container name/ID,
state, resource usage, rootless/rootful mode, metric values, and recommended remediation steps.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| Rootless container network failing; containers cannot reach external services or bind ports < 1024 | Kernel `net.ipv4.ip_unprivileged_port_start` not configured; system default (1024) blocking rootless bind to standard ports | `sysctl net.ipv4.ip_unprivileged_port_start` |
| Container OOMKilled despite application memory usage appearing normal | cgroup v2 memory accounting includes page cache; systemd slice `MemoryMax` set too low, not the container limit itself | `systemctl show user-$(id -u).slice --property MemoryMax` and `cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/memory.max` |
| `podman pull` failing for all images with timeout; registries.conf mirrors configured | Internal registry mirror is down or unreachable; Podman silently exhausted all mirrors and has no internet fallback in air-gapped environment | `curl -sv https://<mirror-host>/v2/ 2>&1 \| grep -E "HTTP\|connect\|timeout"` |
| Quadlet systemd service fails to start after OS update | crun or runc runtime binary upgraded incompatibly; existing containers reference old OCI spec version unsupported by new runtime | `crun --version && podman info --format '{{.Host.OCIRuntime.Name}} {{.Host.OCIRuntime.Version}}'` |
| All rootless containers lose network after host reboot | `slirp4netns` or `pasta` binary missing or removed by package manager upgrade; rootless networking backend not available | `which slirp4netns pasta 2>/dev/null \| xargs -I{} {} --version 2>&1` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N containers in a Podman pod exited (non-infra container); other containers running; pod in `Degraded` state | `podman pod ps` shows `Degraded`; `podman ps -a` shows one container `Exited`; pod-level health check passes because infra container is up | Service partially functioning; one microservice in the pod unavailable; pod not restarted because infra container alive | `podman pod inspect <pod> \| jq '.[0].Containers[] \| {Name:.Name, State:.State}'` |
| 1 of N Quadlet systemd services in a failed state; others active; failure is silent because no monitoring on individual unit state | `systemctl --user list-units 'container-*.service' --state=failed` shows one unit; overall workload appears partially operational | One container workload down; auto-restart exhausted (RestartCount hit limit); no alerting if monitoring only watches the pod not each unit | `systemctl --user list-units 'container-*.service' --state=failed --no-legend` |
| 1 of N storage layers in overlay filesystem corrupted after unclean shutdown; other containers start fine | Only one specific container fails with `failed to mount overlay`; others on same host start normally | Single container cannot start; manual `podman system reset` would fix but destroys all local images | `podman start <container> 2>&1 \| grep -i overlay && podman container inspect <container> \| jq '.[0].GraphDriver'` |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Container memory usage % of limit | > 85% | > 95% | `podman stats --no-stream --format '{{.Name}} {{.MemPerc}}'` |
| OOM kill event count | > 0 | > 0 (any OOM kill) | `podman inspect $(podman ps -aq) --format '{{.Name}}: OOMKilled={{.State.OOMKilled}}' 2>/dev/null \| grep true` |
| Storage root filesystem usage % | > 80% | > 90% | `df -h $(podman info --format '{{.Store.GraphRoot}}') \| awk 'NR==2{print $5}'` |
| Container restart count (last 1h) | > 2 restarts | > 5 restarts | `podman inspect $(podman ps -aq) --format '{{.Name}}: restarts={{.RestartCount}}' \| awk -F= '$2>2{print}'` |
| Container CPU usage % | > 80% of allocated | > 95% of allocated | `podman stats --no-stream --format '{{.Name}} {{.CPUPerc}}'` |
| Failed Quadlet systemd services | > 0 | > 2 | `systemctl --user list-units 'container-*.service' --state=failed --no-legend \| wc -l` |
| Health check failing streak | >= 2 consecutive failures | >= 5 consecutive failures (restart loop) | `podman inspect $(podman ps -q) --format '{{.Name}}: streak={{.State.Health.FailingStreak}}' 2>/dev/null \| awk -F= '$2>1{print}'` |
| Reclaimable storage (dangling images + stopped containers) | > 5 GB | > 20 GB | `podman system df --format '{{.Reclaimable}}'` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Container storage root disk usage | `podman system df` shows disk usage > 60% of volume (`df -h $(podman info --format '{{.Store.GraphRoot}}')`) | Schedule `podman image prune -a --filter "until=168h"` as a cron job; set image retention policy; move graph root to larger volume | 1–2 weeks |
| Image layer count per host | `podman images \| wc -l` > 50 images accumulated | Enable automated image pruning via systemd timer; enforce image tag cleanup in CI/CD pipeline | Days before disk saturation |
| Container memory usage trend | Container RSS growing > 5% per day; `podman stats --no-stream --format "{{.MemUsage}}"` approaching cgroup memory limit | Raise cgroup memory limit in Quadlet unit (`Memory=`); profile application for leaks; enable `--memory-swap` limit | Days before OOM kill |
| CNI/Pasta network namespace count | `ip netns list \| wc -l` growing without container growth (leaked namespaces) | `podman network prune` to clean unused networks; check for orphaned network namespaces from crashed containers | Hours before network resource exhaustion |
| Overlay inode usage | `df -i $(podman info --format '{{.Store.GraphRoot}}')` shows inode usage > 70% | Prune images aggressively (many small layers consume inodes disproportionately); consider switching storage driver from overlay to zfs/btrfs | Weeks |
| Quadlet/systemd unit restart rate | `systemctl show container-<name> \| grep NRestarts` increasing; service entering `failed` state loop | Investigate root cause via `journalctl -u container-<name> --since "1 hour ago"`; add `RestartSec` backoff in unit file; check for config or dependency issues | Hours |
| Rootless user namespace limit | `cat /proc/sys/user/max_user_namespaces` approached by `cat /proc/sys/user/max_user_namespaces` vs running container count | Increase kernel limit: `sysctl -w user.max_user_namespaces=28633`; add to `/etc/sysctl.d/99-podman.conf` for persistence | Days before rootless container start failures |
| Pod log volume (journald pressure) | `journalctl --disk-usage` growing > 1 GB/day from container units | Configure log size limits in Quadlet units (`StandardOutput=journal`); tune journald `SystemMaxUse` in `/etc/systemd/journald.conf` | Days |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# List all containers with status, health, and restart count
podman ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Health}}\t{{.RestartCount}}"

# Live CPU and memory usage for all running containers
podman stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}"

# Disk usage breakdown: images, containers, volumes, build cache
podman system df

# Inspect health check status and last 5 check outputs for a container
podman inspect <container> --format '{{json .State.Health}}' | jq '{status: .Status, failingStreak: .FailingStreak, log: [.Log[-5:][].Output]}'

# Tail last 50 lines of container application logs with timestamps
podman logs --tail 50 --timestamps <container>

# Show recent Podman events (container start/stop/die/oom) in the last 30 minutes
podman events --since "30m" --filter type=container --format '{{.Time}} {{.Actor.Attributes.name}} {{.Status}}'

# Check free disk space on the Podman graph root filesystem
df -h $(podman info --format '{{.Store.GraphRoot}}')

# List all images with size, sorted largest first
podman images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}" | sort -k3 -hr | head -20

# Show Quadlet/systemd service status and recent journal entries for a container unit
systemctl status container-<name>.service && journalctl -u container-<name>.service --since "15 minutes ago" --no-pager | tail -30

# Identify orphaned volumes consuming disk (not attached to any container)
podman volume ls -q --filter "dangling=true" | xargs -r -I{} sh -c 'podman volume inspect {} --format "{{.Name}} {{.Mountpoint}}" | xargs -I@ sh -c "du -sh @"'
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Container Availability (health check passing) | 99.9% | Fraction of time each critical container reports `healthy` status (via `podman inspect`); breach = `Status != healthy` or container in `exited`/`dead` state | 43.8 min/month | Container unhealthy or not running for > 2 min → page; check `podman logs` and systemd journal for crash reason |
| Container Restart Rate (no crash loops) | 99.5% | `rate(podman_container_restarts_total[5m]) == 0` for production containers; SLO breach = any container restarts more than 3 times per hour | 3.6 hr/month | More than 3 restarts in any 60-min window → page; indicates OOM kill, config error, or dependency failure |
| Graph Root Disk Headroom (< 80% full) | 99% | `1 - (node_filesystem_avail_bytes{mountpoint="<graph-root-mount>"} / node_filesystem_size_bytes{mountpoint="<graph-root-mount>"}) < 0.80`; sampled every 5 min | 7.3 hr/month | Disk usage > 80% for > 15 min → page; trigger `podman image prune` and alert capacity planning |
| Image Pull Success Rate | 99.5% | `rate(podman_image_pull_errors_total[5m]) / rate(podman_image_pull_total[5m]) < 0.005`; breach = pull failure rate > 0.5%; measured in CI/CD and runtime pull contexts | 3.6 hr/month | Pull failure rate > 1% for > 5 min → page; check registry availability and image signing verification |
5. **Verify:** `podman inspect <container> --format '{{.State.Health.Status}}'` → expected: `healthy`; `podman inspect <container> --format '{{.State.Health.FailingStreak}}'` → expected: `0`; confirm traffic resumes: `curl -sf http://localhost:<port>/health` → expected: HTTP 200; `podman ps --format '{{.Names}} {{.Status}}'` → expected: `Up X minutes (healthy)`

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Container is running as non-root (rootless) | `podman inspect <container> --format '{{.Config.User}}'` | Non-empty, non-root user (not `0` or `root`); rootless containers reduce blast radius of escapes |
| Memory limit is set on production containers | `podman inspect <container> --format '{{.HostConfig.Memory}}'` | Non-zero value (e.g., `536870912` for 512 MiB); `0` means no limit and the container can OOM the host |
| Health check is defined and not defaulting to `none` | `podman inspect <container> --format '{{.Config.Healthcheck.Test}}'` | Non-empty and not `[NONE]`; at minimum `CMD-SHELL curl -sf http://localhost:<port>/health` |
| Image is using a pinned digest or immutable tag | `podman inspect <container> --format '{{.Image}}'` | Image reference includes a sha256 digest or a version tag (not `latest`) to ensure reproducible deploys |
| Secrets are passed via environment or secret mounts, not image layers | `podman history <image> --no-trunc \| grep -iE 'password\|secret\|token\|api_key'` | No matches; credentials must not be baked into image layers |
| Container network is scoped to required networks only | `podman inspect <container> --format '{{json .NetworkSettings.Networks}}' \| jq 'keys'` | Only expected networks listed; avoid attaching to host network mode unless explicitly required |
| Quadlet unit file has `Restart=on-failure` or `Restart=always` | `grep 'Restart=' /etc/containers/systemd/<name>.container` | `Restart=on-failure` (preferred) or `Restart=always`; prevents permanent downtime on transient errors |
| Volume mounts use named volumes or explicit host paths (no anonymous volumes) | `podman inspect <container> --format '{{json .Mounts}}' \| jq '[.[] \| select(.Type=="volume") \| {name: .Name, source: .Source}]'` | All volumes have meaningful names; anonymous volumes (random hash names) are not used for persistent data |
| Image was pulled from a trusted registry with signature verification | `podman image inspect <image> --format '{{.Labels}}'` | `org.opencontainers.image.source` or `io.buildah.version` labels present; pull policy enforced via `registries.conf` `sigstore` or `lookaside` configuration |
| No dangling images or volumes consuming excessive disk | `podman system df` | `Images (unused)` and `Volumes (reclaimable)` < 5 GB; run `podman system prune --volumes` if exceeded |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `container died with exit code 137` | Critical | Container OOM-killed by the kernel (SIGKILL, exit 137) | Check `podman inspect --format '{{.State.OOMKilled}}'`; increase memory limit or reduce app memory usage |
| `Error: container state improper` | High | Container is in a terminal state (stopped/exited) and an incompatible operation was attempted | `podman rm <container>` and `podman run` to recreate; check logs before removing for root cause |
| `WARN: Failed to mount overlay filesystem, trying vfs` | Medium | Kernel lacks overlayfs support or user is not in `subuid`/`subgid` map for rootless | Verify `/etc/subuid` and `/etc/subgid` entries for the user; confirm kernel supports overlay in user namespaces |
| `Error: slirp4netns failed` | High | Rootless networking helper crashed; network unavailable inside container | Verify `slirp4netns` is installed: `which slirp4netns`; install via package manager; check kernel user namespace support |
| `permission denied: OCI runtime error` | High | SELinux or seccomp policy blocking container syscall | `ausearch -m avc -ts recent | audit2allow` to identify the denial; add `:z` or `:Z` to volume mounts for SELinux relabeling |
| `Error: systemd cgroup v2 not available` | High | System running cgroup v1; Podman configured for cgroup v2 | Set `cgroup_manager = "cgroupfs"` in `/etc/containers/containers.conf` or migrate host to cgroup v2 |
| `container health status: unhealthy` | High | Container health check command failing; application not ready | `podman healthcheck run <container>`; check app logs; verify health check endpoint is correct |
| `level=error msg="failed to pull image" error="unexpected status code 401"` | High | Registry authentication failure; credentials expired or missing | `podman login <registry>`; update `$XDG_RUNTIME_DIR/containers/auth.json` or `/run/containers/0/auth.json` |
| `Error: write /var/lib/containers/storage/overlay: no space left on device` | Critical | Container graph root disk full; no space for new layers or writes | `podman system prune -af` to remove unused images/containers; extend or clean the filesystem |
| `WARN podman[X]: Container <name> is not responding to ping, consider a restart` | High | Container process hung; not responding to health checks | `podman restart <container>`; capture logs before restart with `podman logs <container> > /tmp/container.log` |
| `Error: port binding failed: listen tcp :8080 bind: address already in use` | High | Host port already occupied by another container or process | `ss -tlnp | grep :8080`; stop conflicting process; change port mapping |
| `WARNING: image platform (linux/amd64) does not match host platform (linux/arm64)` | Medium | Wrong architecture image pulled for the host | Pull the correct architecture image or use `--platform linux/arm64` explicitly |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| Exit code 137 | Container killed by SIGKILL; typically OOM | Service unavailable; unexpected restart | Increase memory limit; profile memory usage; add `--memory-swap` if swap is intentional |
| Exit code 1 | Process exited with generic error | Service stopped; depends on restart policy | Check `podman logs <container>` for application error message |
| Exit code 125 | Podman itself encountered an error creating the container | Container never started | Check `podman` error output; verify image name, flags, and volume paths are correct |
| Exit code 126 | Container entrypoint is not executable | Container fails to start | Check `chmod +x` on entrypoint script; verify image build |
| Exit code 127 | Entrypoint or CMD command not found in container | Container fails to start immediately | Verify binary exists in image with `podman run --entrypoint /bin/sh <image> -c 'which <cmd>'` |
| `container state: paused` | Container execution paused via `podman pause` | Service suspended; requests hang | `podman unpause <container>` to resume; investigate why it was paused |
| `container state: stopping` (stuck) | Container ignoring SIGTERM; stuck in stop transition | Container blocks pod/service shutdown | `podman kill --signal KILL <container>` to force kill; fix graceful shutdown in application |
| HTTP 401 from registry during pull | Registry credentials not present or expired | Image pull fails; container cannot start with updated image | `podman login <registry>`; check auth.json validity |
| `Error: crun: mount ... operation not permitted` | Rootless container lacking privilege for the mount type | Container fails to start; volume mount denied | Use named volumes instead of host bind mounts to privileged paths; run as root if unavoidable |
| Quadlet unit `failed` state | Systemd container unit exceeded restart limit | Service permanently down until manual intervention | `systemctl reset-failed container-<name>.service && systemctl start container-<name>.service`; investigate crash cause |
| `network not found` error | Container references a Podman network that no longer exists | Container fails to start | `podman network create <name>`; or update container to use an existing network |
| Image `layer already exists` warning during build | Layer caching conflict; non-fatal | Image build may be slower; final image is correct | `podman build --no-cache` to rebuild from scratch; safe to ignore if build succeeds |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| OOM Kill Crash Loop | Container restart count rising; exit code always 137 | `container died with exit code 137`; `OOMKilled: true` in inspect | Container restart rate > 3/hour alert | Application memory leak or memory limit set too low | Increase `--memory` limit; profile app memory; add memory alerting |
| Graph Root Disk Saturation | Disk usage on container storage partition > 90% | `write ... no space left on device` on pull or layer write | Disk > 85% alert | Stale images, stopped containers, or log files accumulating | `podman system prune`; clean old images; consider log rotation |
| Quadlet Unit Start Limit Hit | Systemd unit restart count at `StartLimitBurst` | `StartLimitHitInterval` reached in `journalctl` | Service `failed` state alert | Container crash-looping; systemd stops retrying | `systemctl reset-failed`; investigate crash cause; fix before restarting |
| Registry Auth Failure | Image pull error rate 100% during deployments | HTTP 401 from registry; `auth.json` missing or expired | Deployment failure alert | Registry credentials expired or auth.json not present for rootless user | `podman login <registry>` as the service user; verify auth.json path |
| Health Check Consistently Failing | Container running but health status `unhealthy` for > 3 checks | Application error in health check endpoint logs | Container unhealthy alert | Application not ready; health check URL or port wrong | Fix health check command; verify app listens on health check port |
| SELinux Mount Denial | Container exits with permission error on volume mount | `permission denied: OCI runtime error` + AVC denial in audit log | SELinux denial alert | Volume path missing SELinux label for container access | Add `:z` or `:Z` to volume mount; run `restorecon -Rv <path>` |
| Port Conflict on Restart | Container fails to start after host reboot | `listen tcp :<port> bind: address already in use` | Service start failure alert | Another service or container claims the same host port post-boot | Change host port mapping; disable conflicting service; set `RestartSec` to delay startup |
| Image Platform Mismatch | Container starts but crashes immediately with `exec format error` | `WARNING: image platform does not match host platform` | Container exit code 1 immediately alert | Wrong architecture image pulled (e.g., amd64 image on arm64 host) | Pull correct architecture image; use `--platform` flag explicitly |
| Cgroup v2 Incompatibility | Container resource limits not enforced; cgroup errors in logs | `Error: systemd cgroup v2 not available` | Resource limit enforcement failure | Host running cgroup v1; Podman defaults to cgroup v2 | Set `cgroup_manager = "cgroupfs"` in containers.conf; migrate to cgroup v2 |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| `Error: short-name resolution enforced` | `podman pull` / Dockerfile FROM | Unqualified image name without registry prefix in rootless mode | `podman pull <image>` — shows short-name prompt | Fully qualify: `docker.io/library/<image>` or configure `registries.conf` |
| `Error: crun: ... permission denied: OCI runtime error` | `podman run` / Quadlet | SELinux label missing on mounted volume path | `ausearch -m avc -ts recent` | Add `:z` or `:Z` to volume mount; `chcon -Rt container_file_t <path>` |
| `dial tcp: connection refused` on mapped port | Application HTTP client | Host port not mapped or container network not started | `podman port <container>` | Add `-p <host>:<container>` flag; verify container is running |
| `Error: image not known` | `podman run` | Image not pulled or wrong tag; local cache empty | `podman images | grep <name>` | `podman pull <full-image-ref>`; verify tag exists in registry |
| `exec format error` | Application startup | Wrong architecture image (e.g., amd64 on arm64 host) | `podman inspect <image> | grep Architecture` | Pull correct architecture: `podman pull --platform linux/arm64 <image>` |
| `Error: creating container storage: the container name is already in use` | `podman run` | Container with same name already exists (stopped) | `podman ps -a | grep <name>` | `podman rm <name>` before re-running; or use `--replace` flag |
| `OOMKilled: exit code 137` | Application crash observer | Container exceeded memory limit | `podman inspect <container> | grep -i oom` | Increase `--memory` limit; profile application heap; add memory alerting |
| `Error: no such container` on exec/logs | Application deployment tooling | Container stopped or removed unexpectedly | `podman ps -a --format "{{.Names}} {{.Status}}"` | Check exit reason: `podman inspect <container> | grep ExitCode`; review logs before removal |
| `Error: rootlessport cannot expose privileged port 80` | `podman run -p 80:8080` | Rootless Podman cannot bind port < 1024 by default | `sysctl net.ipv4.ip_unprivileged_port_start` | Set `sysctl net.ipv4.ip_unprivileged_port_start=80` or map to port > 1024 + use reverse proxy |
| `Error: slirp4netns failed` | `podman run` networking | slirp4netns binary missing or network namespace creation failed | `which slirp4netns; slirp4netns --version` | Install `slirp4netns`; or switch to `pasta` network backend in `containers.conf` |
| `write /proc/self/uid_map: operation not permitted` | `podman unshare` / build | Subuid/subgid not configured for the user | `cat /etc/subuid | grep $(whoami)` | Add entries: `echo "$(whoami):100000:65536" >> /etc/subuid /etc/subgid`; run `podman system migrate` |
| `HTTP 500 from Podman REST API` | `podman-remote` / Docker SDK compat | Podman socket service crashed or not started | `systemctl --user status podman.socket` | `systemctl --user start podman.socket`; check `journalctl --user -u podman.socket` |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Container storage disk saturation | Disk usage on overlay partition trending from 60% to 80% | `df -h $(podman info --format '{{.Store.GraphRoot}}')` | 1–2 weeks | `podman system prune --volumes`; remove dangling images; rotate old logs |
| Image layer cache bloat | `podman images` total size growing weekly from CI/CD builds | `podman images --format "{{.Size}}" | sort -h | tail -20` | 1–3 weeks | `podman image prune`; set image retention policy in CI/CD |
| Quadlet/systemd restart rate increasing | `systemctl show <service> --property=NRestarts` value climbing | `journalctl --user -u <service>.service -n 50` | Days | Identify crash root cause; fix application; add liveness healthcheck |
| Rootless user subuid/gid range near exhaustion | New container starts failing with namespace errors | `cat /proc/$(pgrep -u $(whoami) podman | head -1)/status | grep NsId` | Days to weeks | Expand subuid/subgid range in `/etc/subuid`; run `podman system migrate` |
| Volume mount inode exhaustion | Container log writes failing; `no space left` despite free block space | `df -i $(podman volume inspect <vol> --format '{{.Mountpoint}}')` | Days | Delete stale files; increase inode limit on filesystem; use separate volume |
| Overlay filesystem upper layer fragmentation | Container write I/O slowing down over weeks | `podman system df --verbose` — check individual container sizes | Weeks | Stop and remove old containers; `podman system prune`; consider named volumes |
| Cgroup memory pressure from many containers | Host OOM events increasing at night; some containers killed | `cat /sys/fs/cgroup/memory.stat` or `systemd-cgtop` | Days | Set per-container memory limits; consolidate or terminate unused containers |
| Registry pull rate limit approaching | Occasional `429 Too Many Requests` from docker.io during CI | `podman pull docker.io/library/alpine` — observe rate limit headers | Hours to days of CI churn | Authenticate to registry; use a pull-through cache or mirror; cache base images |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Podman Full Health Snapshot
echo "=== Podman Health Snapshot: $(date) ==="

echo "-- Podman Version & Runtime --"
podman version --format "Version: {{.Client.Version}} | OS: {{.Client.OsArch}}"

echo "-- System Info Summary --"
podman info --format "
Store Root: {{.Store.GraphRoot}}
Run Root:   {{.Store.RunRoot}}
Driver:     {{.Store.GraphDriverName}}
" 2>/dev/null

echo "-- Running Containers --"
podman ps --format "{{.Names}}\t{{.Status}}\t{{.Image}}"

echo "-- Unhealthy / Exited Containers --"
podman ps -a --filter "status=exited" --filter "status=paused" \
  --format "{{.Names}}\t{{.Status}}\t{{.ExitCode}}"

echo "-- Disk Usage --"
podman system df

echo "-- Storage Partition Disk Free --"
df -h "$(podman info --format '{{.Store.GraphRoot}}' 2>/dev/null || echo /var/lib/containers)"

echo "-- Quadlet/Systemd Service Health --"
systemctl --user list-units "*.service" --state=failed --no-legend 2>/dev/null || \
  systemctl list-units "*.service" --state=failed --no-legend 2>/dev/null
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Podman Performance Triage — resource usage, restart counts, healthchecks
echo "=== Podman Performance Triage: $(date) ==="

echo "-- Container Resource Stats (5s sample) --"
podman stats --no-stream --format \
  "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"

echo "-- Containers with High Restart Count --"
podman ps -a --format "{{.Names}}\t{{.RestartCount}}\t{{.Status}}" \
  | awk -F'\t' '$2+0 > 2 {print}' | sort -t$'\t' -k2 -rn

echo "-- Unhealthy Healthchecks --"
for cid in $(podman ps -q); do
  STATUS=$(podman inspect "$cid" --format "{{.State.Health.Status}}" 2>/dev/null)
  NAME=$(podman inspect "$cid" --format "{{.Name}}" 2>/dev/null)
  [ "$STATUS" = "unhealthy" ] && echo "UNHEALTHY: $NAME"
done

echo "-- Recent OOM Events --"
journalctl -k --since "1 hour ago" | grep -i "oom\|killed process" | tail -10

echo "-- Large Images (top 10) --"
podman images --sort size --format "{{.Repository}}:{{.Tag}}\t{{.Size}}" | head -10
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Podman Connection and Resource Audit
echo "=== Podman Connection & Resource Audit: $(date) ==="

echo "-- Podman Socket Status --"
systemctl --user status podman.socket 2>/dev/null || \
  systemctl status podman.socket 2>/dev/null || echo "Socket service not found"

echo "-- Open Network Ports (container-mapped) --"
podman ps --format "{{.Names}}: {{.Ports}}" | grep -v "^.*: $"

echo "-- Volume Usage --"
podman volume ls --format "{{.Name}}\t{{.Driver}}\t{{.Mountpoint}}"
podman system df --verbose 2>/dev/null | grep -A5 "Volumes"

echo "-- Subuid / Subgid Configuration --"
echo "subuid entries for $USER:"
grep "^${USER}:" /etc/subuid 2>/dev/null || echo "Not found — rootless containers will fail"
echo "subgid entries for $USER:"
grep "^${USER}:" /etc/subgid 2>/dev/null || echo "Not found — rootless containers will fail"

echo "-- Registry Configuration --"
podman info --format "Registries: {{.Registries}}" 2>/dev/null

echo "-- SELinux Status --"
getenforce 2>/dev/null || echo "SELinux not available"

echo "-- Cgroup Version --"
podman info --format "CgroupVersion: {{.Host.CgroupVersion}}" 2>/dev/null
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Memory-hungry container triggering host OOM | Other containers or host processes killed unexpectedly; exit code 137 | `journalctl -k | grep "oom_kill_process"`; `podman inspect <killed> | grep OOMKilled` | Set `--memory` hard limit on offending container; lower limit or fix leak | Always set `--memory` limits; configure cgroup memory notifications |
| Overlay filesystem layer cache filling host disk | New container start or image pull fails with `no space left on device` | `podman system df`; `df -h $(podman info --format '{{.Store.GraphRoot}}')` | `podman system prune -a --volumes` (remove unused); move graph root to larger volume | Set up periodic `podman system prune` cron; mount container storage on dedicated partition |
| CPU-bound container starving other workloads | Host load average rising; other containers or system services slow | `podman stats --no-stream | sort -k3 -rn` — find high CPU containers | `podman update --cpus 0.5 <container>` to throttle; or `podman stop` | Set `--cpus` limit at container start; monitor with `systemd-cgtop` |
| Port binding conflict between multiple containers | Second container fails to start after host reboot; `address already in use` | `ss -tlnp | grep <port>`; `podman ps --format "{{.Ports}}"` | Stop conflicting container; reassign port mapping | Maintain port allocation registry; use Quadlet labels to document port ownership |
| Shared volume write contention | Data corruption or lock errors in application; intermittent write failures | Check container logs for file lock errors; `fuser -v <mountpoint>` | Add file locking in application; split volume per container | Design volumes as single-writer; use named pipes or message queues for multi-writer patterns |
| Registry rate-limit exhaustion from CI runners | Pulls failing with `429 Too Many Requests`; CI pipeline stalling | `podman pull docker.io/library/alpine` — observe rate limit in response headers | Authenticate all runners: `podman login docker.io`; use pull-through mirror | Host a local registry mirror (e.g., `registry:2`); cache base images in internal registry |
| Quadlet service restart storm filling journal | journald disk usage rising; `journalctl` slow; log storage filling | `journalctl --disk-usage`; `systemctl --user list-units --state=failed` | `systemctl --user reset-failed`; fix crashing service; set `SystemMaxUse` in journald.conf | Set `StartLimitBurst` and `StartLimitIntervalSec` in Quadlet unit to cap restart rate |
| Slirp4netns / pasta spawning per-container processes | High process count on host; user process limit (`ulimit -u`) approached | `ps aux | grep -c slirp4netns`; `cat /proc/sys/kernel/pid_max` | Switch multiple containers to a shared Pod network instead of per-container networking | Use Podman pods to share single network namespace; reduces network process count linearly |
| Buildah build cache thrashing storage | `podman build` consuming all free disk; other containers unable to write layers | `podman system df --verbose`; check build cache size | `podman builder prune`; set `--layers=false` for throwaway builds | Set `podman build --squash-all` for final images; run builder prune after each CI job |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Podman socket (`podman.socket`) dies | systemd-activated socket no longer accepts connections → all container management commands fail → Quadlet services cannot restart crashed containers → cascading container failures | All containers managed via systemd Quadlet; CI/CD pipelines; health checks | `systemctl --user status podman.socket` shows `failed`; `podman ps` returns `Error: unable to connect to Podman socket` | `systemctl --user restart podman.socket`; `systemctl --user restart podman.service` |
| Overlay filesystem storage exhausted | New container start fails with `Error: creating container storage: no space left on device` → existing containers cannot write → application writes fail → data corruption risk | All new container operations; running containers that write to overlay layers | `df -h $(podman info --format '{{.Store.GraphRoot}}')` at 100%; `podman system df` shows large build cache | `podman system prune -a --volumes --force`; extend volume or move graph root |
| `subuid`/`subgid` mapping broken after OS user change | Rootless containers fail to start with `Error: cannot set up namespace using newuidmap: exit status 1` → all user containers fail → services managed by affected user down | All rootless containers for that user account | `podman info` shows `Error: newuidmap` in stderr; `cat /etc/subuid | grep <user>` returns empty | Re-add subuid mapping: `usermod --add-subuids 100000-165535 <user>`; `podman system migrate` |
| Conmon (container monitor) process killed | Container loses monitor → container appears running but is unmanaged → signals cannot be sent → graceful shutdown impossible | All containers whose conmon process was killed | `podman ps` shows container running but `podman stop <name>` hangs; `ps aux | grep conmon` missing entries | `podman kill --signal KILL <container>`; then `podman rm -f <container>` and restart |
| Host kernel OOM kills container init process (PID 1) | Container stops unexpectedly (exit code 137) → systemd Quadlet restarts container → restart loop if OOM persists → host memory further depleted | The OOM-killed container and its dependent services; potential host instability | `journalctl -k | grep oom_kill_process`; `podman inspect <container> --format '{{.State.OOMKilled}}'` returns `true` | `podman update --memory 512m <container>` to cap memory; investigate memory leak in application |
| CNI/netavark network plugin failure | Container networking initialization fails → `Error: failed to create network <name>: ...` → dependent containers cannot start | All containers using the affected network | `podman network inspect <name>` fails; new container start logs `Error: setup rootless network` | `podman network rm <name> && podman network create <name>`; restart containers |
| Host NTP clock jump during container runtime | Container `time.Now()` diverges from host → JWT token validation failures → application authentication errors → cascade of 401s | All containers using time-sensitive operations (JWT, TLS cert validation, rate limiting) | `chronyc tracking` shows large offset; application logs: `JWT is expired` for valid tokens | `chronyc makestep` to force immediate sync; restart affected containers after clock stabilized |
| Volume mount path deleted on host | Container starts but application fails to find expected data → `No such file or directory` errors → application crash loop | All containers mounting the deleted path | `podman inspect <container> --format '{{.Mounts}}'` shows path that no longer exists on host | `mkdir -p <host_path>`; `podman restart <container>`; restore data from backup |
| Slirp4netns / pasta process crash for rootless container | Container loses network connectivity mid-run → TCP connections time out → application cannot reach database or external APIs | The affected rootless container's network connectivity only | Container logs show TCP timeout errors; `podman exec <container> ping 8.8.8.8` fails; `ps aux | grep slirp4netns` shows missing process | `podman restart <container>` to respawn networking subprocess |
| Quadlet-managed service restart loop hitting `StartLimitBurst` | systemd stops restarting after `StartLimitBurst` exceeded → container remains down → dependent services fail | The specific Quadlet service and its downstream dependencies | `systemctl --user status <service>` shows `start request repeated too quickly`; container stays stopped | `systemctl --user reset-failed <service>.service && systemctl --user start <service>.service`; fix underlying crash first |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Podman version upgrade (e.g., 4.x → 5.x) | `Error: unknown flag: --format` or changed default network mode breaks existing scripts; `crun` ABI changes break container start | Immediately after upgrade | `podman --version` before/after; check Podman 5.0 migration guide for breaking changes | `dnf downgrade podman` or `apt install podman=<old_version>`; re-run `podman system migrate` |
| Switching from CNI to Netavark network backend | Existing CNI networks not visible to Netavark → `Error: network not found` for all containers using old networks | After `podman system reset` or fresh install with Netavark default | `podman info --format '{{.Host.NetworkBackend}}'` shows `netavark`; old CNI config files in `/etc/cni/net.d/` | Recreate networks using `podman network create`; update Quadlet files with new network name |
| Base image update with changed entrypoint | Container starts with wrong command → application not launched → container exits immediately with code 0 or 1 | On next `podman pull` + `podman restart` | `podman history <image>:<new_tag>` vs `<old_tag>` — compare `ENTRYPOINT`/`CMD` layers | Pin image to previous digest: `FROM image@sha256:<digest>` in Containerfile; roll back registry tag |
| SELinux policy update blocking container volume access | Application gets `Permission denied` on volume-mounted files despite correct Unix permissions | After OS/SELinux policy package update | `ausearch -m avc -ts recent | grep podman`; `getenforce` returns `Enforcing` | `podman run --security-opt label=disable ...` as temporary fix; add correct SELinux context: `chcon -Rt container_file_t <host_path>` |
| `ulimit` change on host (open files) | Container processes hit file descriptor limit → `too many open files` errors in application | Under load, after ulimit reduction | `podman exec <container> sh -c 'ulimit -n'`; compare with `cat /proc/sys/fs/file-max` on host; correlate with ulimit change timestamp | `podman run --ulimit nofile=65536:65536 ...`; update systemd `DefaultLimitNOFILE` |
| Quadlet `.container` file update without systemd daemon-reload | Old service definition still running; new Quadlet file has no effect | Immediately after file edit | `systemctl --user cat <service>.service` shows stale `ExecStart`; `systemctl --user show <service> -p FragmentPath` | `systemctl --user daemon-reload && systemctl --user restart <service>.service` |
| Rootless user session `loginctl` sessions changed | `podman` commands work interactively but Quadlet services fail after reboot with `Error: cannot get user info` | After user session or PAM configuration change | `loginctl show-user <user> --property=Linger`; if `Linger=no`, services die on logout | `loginctl enable-linger <user>`; `systemctl --user start <service>` |
| cgroup v1 → v2 migration on host | `podman stats` fails; resource limits not applied; `--memory` flag has no effect | After kernel upgrade or OS migration to cgroup v2 | `podman info --format '{{.Host.CgroupVersion}}'` shows `v2`; `podman stats` errors | Rebuild container with cgroup v2-compatible runtime; verify `crun --version` supports cgroupv2 |
| Image registry authentication expired | `podman pull` fails with `Error: initializing source: pinging container registry: unauthorized` → automated updates fail → pods run stale images | When pull credentials expire (Docker Hub token TTL) | `podman login --get-login docker.io` returns error; pull test fails | `podman login docker.io -u <user> -p <token>`; update Quadlet `ContainerCredential` or k8s `imagePullSecret` |
| `/etc/containers/registries.conf` change blocking pulls | `Error: error pinging docker.io: ...` or pulls redirected to wrong registry | Immediately after config change | `podman info | grep -A5 registries`; compare with previous `registries.conf` | Revert `registries.conf` from backup: `cp /etc/containers/registries.conf.bak /etc/containers/registries.conf`; `podman system reset` if needed |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| Named volume data drift between container restarts | `podman diff <container>` shows unexpected file changes; `podman volume inspect <vol> --format '{{.Mountpoint}}'` then `ls -la <mountpoint>` | Container writes unexpected files or overwrites correct data on volume | Application state inconsistent between restarts; corrupted config files | `podman volume export <vol> > backup.tar`; inspect and repair data manually; reimport |
| Two container instances mounting same volume (write-write conflict) | Application-level corruption or `flock` errors; `podman ps` shows two containers with same volume in `Mounts` column | Both containers write to shared volume without coordination | Data corruption; file truncation; split writes | Stop one container immediately; repair corrupted files from backup; redesign for single-writer or use lock files |
| Container config drift from Quadlet file (manual `podman run` created extra container) | `podman ps` shows duplicate container names or unexpected containers; `systemctl --user status <service>` shows correct service but extra container running | Two versions of service competing; network port conflict; split write traffic | Unpredictable behavior; both containers receive some traffic | `podman stop <manually_created_container> && podman rm <id>`; use only Quadlet for container lifecycle management |
| Secret file updated on host but not propagated to running container | `podman exec <container> cat /run/secrets/<secret>` returns old value | Container uses stale credentials; authentication failures to external services | Application fails to authenticate; silent failures if old credential not yet expired | `podman restart <container>` to mount updated secret; verify with `podman exec <container> cat /run/secrets/<secret>` |
| Image tag mismatch between dev and prod (same tag, different digest) | `podman inspect <image>:<tag> --format '{{.Digest}}'` differs between environments | Code that works in dev fails in prod silently or produces different behavior | Untestable production bugs; environment-specific failures | Pin images by digest: `image@sha256:<digest>` in Containerfile and Quadlet; enforce in CI |
| Overlay layer corruption after unclean host shutdown | `podman start <container>` fails with `Error: layers from lost+found present in storage, to fix, run 'podman system renumber'` | Container cannot start; layers inconsistent | Service outage until repaired | `podman system renumber` to fix layer numbering; if still broken: `podman system reset` (destroys all containers/images) |
| Stale lock file in container storage preventing operations | `podman ps` hangs; `podman info` takes > 30s | All Podman commands block indefinitely | Complete management outage | `rm -f /run/user/<uid>/libpod/tmp/podman.pid`; `rm -f /tmp/podman-run-<uid>/podman/podman.pid`; restart podman socket |
| `COPY` in Containerfile cached from old context despite content change | `podman build` uses cached layer; new file not included in image | Image built successfully but missing updated config or binary | Running container uses old version of file | `podman build --no-cache` to force fresh build; or invalidate cache by changing a preceding layer |
| Container environment variable drift (`.env` file changed, container not restarted) | `podman exec <container> env | grep <VAR>` shows old value | Container uses stale configuration; new feature flags or credentials not active | Incorrect application behavior; security risk if credential rotated | `podman restart <container>`; for Quadlet: `systemctl --user restart <service>.service` |
| Hostname resolution inconsistency between containers in same pod | `podman exec <container_a> getent hosts <container_b>` returns wrong IP after container_b restart | Service discovery within pod fails; inter-container calls fail with `connection refused` | Application microservice calls fail; health checks break | `podman pod restart <pod_name>` to reset all containers and DNS within the pod |

## Runbook Decision Trees

### Decision Tree 1: Container fails to start

```
Is `podman start <container>` returning an error?
├── YES → What does `podman logs <container>` show?
│   ├── "no space left on device" → Is disk usage > 95%?
│   │   ├── YES → Run `podman system prune -f` → retry start
│   │   └── NO  → Volume mount issue → check `podman inspect <container> --format '{{.Mounts}}'`
│   │             ├── Host path missing → `mkdir -p <path>`; restore from backup; retry start
│   │             └── Permission denied → `chcon -Rt container_file_t <path>`; retry start
│   ├── "permission denied" or SELinux AVC → Check `ausearch -m avc -ts recent | grep podman`
│   │   ├── AVC found → `chcon -Rt container_file_t <host_path>`; or `--security-opt label=disable`
│   │   └── No AVC → Check Unix permissions: `ls -la <host_path>`; fix with `chmod`/`chown`
│   ├── "image not found" → Run `podman pull <image>:<tag>`
│   │   ├── Pull fails with 401 → `podman login <registry>` → retry pull → retry start
│   │   └── Pull succeeds → retry `podman start <container>`
│   └── "Error: unable to connect to Podman socket" → `systemctl --user restart podman.socket` → retry
└── NO  → Container starts but exits immediately → check `podman inspect <container> --format '{{.State.ExitCode}}'`
          ├── Exit code 1 → Application crash → `podman logs <container> --tail 100` for app error
          ├── Exit code 137 → OOM killed → `podman update --memory 512m <container>`; restart
          └── Exit code 0 → Entrypoint runs and exits → check `CMD`/`ENTRYPOINT` in Containerfile
```

### Decision Tree 2: Container networking is broken

```
Can container reach external hosts? `podman exec <container> curl -sf https://1.1.1.1 -o /dev/null`
├── NO → Is this a rootless container?
│   ├── YES → Is slirp4netns/pasta running? `ps aux | grep "slirp4netns\|pasta" | grep -v grep`
│   │   ├── NOT running → `podman restart <container>` to respawn networking subprocess
│   │   └── Running → Check pasta/slirp config: `podman inspect <container> --format '{{.NetworkSettings}}'`
│   │             → DNS issue: `podman exec <container> cat /etc/resolv.conf` — if empty, add `--dns 8.8.8.8`
│   └── NO (rootful) → Is Netavark/CNI healthy? `podman network inspect <net-name>`
│       ├── Network missing → `podman network create <net-name>`; `podman restart <container>`
│       └── Network exists → Check iptables: `iptables -L FORWARD -n | grep ACCEPT` — if missing rules:
│                           → `podman network reload <container>` to reapply network rules
└── YES → Can container reach other containers? `podman exec <c1> ping <c2-name>`
          ├── NO → Are both on same network? `podman inspect <c1> --format '{{.NetworkSettings.Networks}}'`
          │   ├── Different networks → Connect: `podman network connect <net> <container>`
          │   └── Same network → Restart pod: `podman pod restart <pod>` to reset DNS resolution
          └── YES → Specific endpoint unreachable → Check firewalld/iptables rules on host port
                    → `iptables -L -n | grep <port>`; add rule or check `firewall-cmd --list-all`
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Runaway container logging filling `/var/log` | Container writing > 1 GB/hr of logs; disk fills | `journalctl --disk-usage`; `podman inspect <container> --format '{{.HostConfig.LogConfig}}'` | Disk full → all containers on host fail; OS log loss | `podman update <container> --log-opt max-size=100m --log-opt max-file=3`; `journalctl --vacuum-size=2G` | Always set `--log-opt max-size=50m --log-opt max-file=5` in Quadlet `[Container]` section |
| Unbounded image accumulation from CI/CD builds | `podman system df` shows images consuming > 50 GB; disk usage growing daily | `podman images --format "{{.Size}}\t{{.Repository}}:{{.Tag}}" | sort -rh | head -20` | Disk exhaustion; new builds fail | `podman image prune -a -f` to delete untagged/unused images; `podman rmi <old-image>` for specific ones | Systemd timer for weekly `podman image prune -a`; tag images by git SHA and keep only last 3 |
| Memory leak in long-running container exhausting host RAM | Container RSS growing unbounded; host swap in use; OOM kills | `podman stats --no-stream --format "{{.Name}}: {{.MemUsage}}"` | OOM kill cascade; other containers terminated | `podman update --memory 1g --memory-swap 1g <container>` to apply hard cap; restart container | Set `--memory` limit in every Quadlet `[Container]` section; alert on memory > 80% of limit |
| Runaway `podman build` filling temp/overlay storage | Build using > 20 GB for large multi-stage Containerfile | `df -h /var/tmp`; `podman system df`; `ls -lh /var/tmp/buildah*` | Disk exhaustion; all container operations on host fail | `pkill podman`; `podman system prune -f`; `rm -rf /var/tmp/buildah*` | Use `--squash-all` for final build stage; set `buildUIDMap` limits in `containers.conf` |
| Stale Quadlet containers not garbage-collected after service removal | Stopped containers from old service versions accumulate; storage grows | `podman ps -a --format "{{.Names}}\t{{.Status}}" | grep -v "Up "` — count stopped containers | Storage waste; name conflict if re-deploying | `podman rm $(podman ps -aq --filter status=exited)` | Add `AutoRemove=true` to Quadlet `[Container]` for stateless services |
| Excessive `podman pull` in CI/CD without layer cache | Network bandwidth and registry rate limits consumed; Docker Hub 429 errors | `grep "pull" /var/log/syslog | wc -l`; `podman events --filter event=pull --since 24h | wc -l` | Registry rate limit (Docker Hub: 100/6hr unauthenticated); CI pipeline failures | Authenticate pulls: `podman login docker.io`; implement local mirror with `podman image scp` | Use internal registry mirror (e.g., Harbor); cache images in CI with `--cache-from` |
| `podman volume prune` accidentally removes active data volumes | Application data gone; containers restart with empty volumes | `podman volume ls -q --filter dangling=true` — shows "dangling" volumes that prune would delete | Permanent data loss if no backup | Stop prune immediately if in progress; restore from last volume backup/snapshot | Never run `podman volume prune` without `--filter label=purge=true`; label volumes-to-keep explicitly |
| Container with `--privileged` flag allowing uncapped resource usage | Privileged container bypasses cgroup limits; can exhaust CPU/memory/disk | `podman ps --format "{{.Names}}\t{{.Command}}" | xargs -I{} podman inspect {} --format '{{.HostConfig.Privileged}}'` | Host-wide resource exhaustion | Remove `--privileged`; restart with explicit capability grants: `--cap-add NET_ADMIN` only | Audit all containers for `--privileged`; enforce policy in CI to block privileged containers except explicitly approved |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot container from single-instance bottleneck | One container CPU at 100%; request queue depth growing; other containers idle | `podman stats --no-stream --format "{{.Name}}: {{.CPUPerc}} {{.MemUsage}}"` | No horizontal scaling; single Quadlet service handling all traffic | Add replicas via multiple Quadlet units with load balancer; or use `podman kube play` for scaling |
| Connection pool exhaustion from container not closing sockets | Container's outbound connection count at OS limit; new requests fail | `ss -tp | grep <container-pid> | wc -l` and `cat /proc/$(podman inspect <c> --format '{{.State.Pid}}')/fd | wc -l` | Application in container not closing HTTP/DB connections; no pool limit | Apply `--ulimit nofile=65536:65536` on container; fix connection lifecycle in app; add connection pool |
| Overlay filesystem pressure causing I/O latency spike | Container disk I/O latency high; `podman stats` shows high block I/O; host I/O scheduler saturated | `podman stats --no-stream --format "{{.Name}}: {{.BlockInput}} {{.BlockOutput}}"`; `iostat -xz 1 5` on host | Container writing to overlay layer instead of named volume; overlay CoW overhead | Mount named volume for write-heavy paths: `podman run -v app-data:/data ...`; use `fuse-overlayfs` on rootless |
| Thread pool saturation in container from misconfigured worker count | Container CPU high; request timeouts; many goroutines/threads blocked | `podman exec <container> sh -c "ps -eLf | wc -l"` and `podman exec <container> top -H` | Worker thread count set too high for available CPU; context-switch overhead | Set worker threads = 2 × CPU cores; configure via env var: `podman run -e WORKER_THREADS=4 ...` |
| Slow container startup from large image pull on restart | Restart latency p99 > 60 s; Quadlet restart log shows pull time | `podman events --filter event=pull --since 1h --format "{{.Time}} {{.Actor.Attributes.name}}"` | Image not pre-pulled on host; pulled on every restart | Pre-pull image: `podman pull <image>`; use Quadlet `Pull=never` + image pinned by digest to ensure local availability |
| CPU steal from unpinned container on shared NUMA node | Container CPU work high but throughput low; `numastat` shows remote memory access | `numastat -p $(podman inspect <c> --format '{{.State.Pid}}')` | Container accessing cross-NUMA memory; NUMA-unaware CPU pinning | Pin container to NUMA node: `podman run --cpuset-cpus=0-7 --cpuset-mems=0 ...` |
| Lock contention in container's shared memory (IPC namespace) | Container's internal operations slow; `podman exec <c> ipcs` shows semaphore wait | `podman exec <container> ipcs -s`; `podman exec <container> ipcs -si <semid>` | Shared-memory-based IPC semaphores under contention from multiple goroutines | Set `--ipc=private` for containers that don't need shared IPC; refactor to message-passing |
| Serialization overhead from JSON log driver for high-throughput services | Container log writes adding > 5 ms per log line; application latency rising with log volume | `podman inspect <container> --format '{{.HostConfig.LogConfig.Type}}'`; `time podman logs --tail 1 <container>` | Default JSON-file log driver serializing every log line synchronously | Switch to `--log-driver=passthrough` or `journald`; reduce log verbosity; use structured logging with sampling |
| Batch size misconfiguration: container job processing 1 item per invocation | Job container restarts 10000× instead of 10; overhead dominates runtime | `podman ps -a --filter name=job --format "{{.Names}}\t{{.Status}}" | grep Exited | wc -l` | Batch job processes single item and exits; startup overhead per item > processing time | Use `--env BATCH_SIZE=500`; redesign job to process multiple items per container invocation |
| Downstream service DNS latency inside container | Application in container seeing > 200 ms DNS resolution for every request | `podman exec <container> time nslookup <service-host>`; `podman exec <container> cat /etc/resolv.conf` | Default resolver in container using host's slow upstream DNS; no DNS caching in container | Configure fast resolver: `--dns=169.254.169.254`; or run local dnsmasq; set `--dns-search` for short-name resolution |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| TLS certificate expiry in container | Container application logs `x509: certificate has expired`; healthcheck fails | `podman exec <container> openssl s_client -connect <backend>:443 < /dev/null 2>/dev/null | openssl x509 -noout -dates` | Expired cert mounted into container via volume or baked into image | Remount renewed cert: `podman run -v /etc/ssl/new-cert.pem:/app/cert.pem ...`; use cert-manager or Vault PKI sidecar |
| mTLS rotation failure between containers | Inter-container HTTPS fails after cert rotation; `podman network inspect` shows containers still connected | `podman exec <client-c> curl -v --cacert /app/ca.pem https://<server-c>:8443/ 2>&1 | grep -E "SSL|TLS|error"` | New CA cert not yet propagated to all containers; stale cert in shared volume | Rebuild containers with new CA baked in; or update shared cert volume and `podman restart <container>` |
| DNS resolution failure inside container network | Container cannot reach other containers by name; `nslookup <service>` fails inside container | `podman exec <container> nslookup <other-container>`; `podman network inspect <network-name> | jq .[].dns_enabled` | DNS disabled on Podman network (`dns_enabled: false`); or container not on same network | `podman network create --dns-enable` ; reconnect: `podman network connect <network> <container>` |
| TCP connection exhaustion from port forwarding misconfiguration | Host ports exhausted; `podman run -p` fails with `bind: address already in use` | `ss -tlnp | grep <host-port>`; `podman ps --format "{{.Ports}}"` | Multiple containers mapped to same host port; or container not releasing port on stop | `podman stop <old-container>`; use `podman run --replace` for upgrades to atomically swap containers |
| Load balancer misconfiguration in podman-compose or Quadlet network | Traffic routed to stopped container; healthy container receives no requests | `podman ps --filter network=<net> --format "{{.Names}}\t{{.Status}}"`; check Nginx/HAProxy upstream config | Load balancer not updated after container replacement; stale IP in upstream | Update upstream config to use container names (not IPs) with Podman's DNS: `server <container-name>:8080` |
| Packet loss on Podman CNI/Netavark bridge | Inter-container traffic dropping packets; `ping` between containers shows loss | `podman exec <c1> ping -c 20 <c2-ip>`; `podman exec <c1> mtr --report <c2-ip>` | CNI/Netavark bridge interface overloaded; iptables rules corrupted | `podman network reload`; inspect bridge: `ip link show podman0`; check iptables: `iptables -L -n | grep <container-ip>` |
| MTU mismatch on container network causing fragmentation | Large HTTP responses fail inside container network; small requests work | `podman exec <container> ping -M do -s 1400 <other-container-ip>` — fails if MTU < 1428 | Container network MTU (1450) lower than host MTU (9000); fragmentation | Set network MTU: `podman network create --opt mtu=1450 <network>`; match host network MTU |
| Firewall rule (iptables/nftables) blocking container traffic after host update | Containers suddenly cannot reach external services after OS update | `iptables -L FORWARD -n | grep <container-subnet>`; `podman exec <c> curl -v https://8.8.8.8` | OS update reset iptables; FORWARD chain drops container traffic | `iptables -A FORWARD -i <bridge> -j ACCEPT`; `systemctl restart podman` to reinitialise rules |
| SSL handshake timeout from slow TLS in rootless container | HTTPS connections from rootless container take > 5 s; direct host connections are fast | `podman exec <container> time curl -v https://<host> 2>&1 | grep -E "TLSv|Connected|time"` | Entropy starvation in rootless container's kernel namespace; slow `/dev/random` | Mount `/dev/urandom`: `podman run -v /dev/urandom:/dev/urandom:ro ...`; use `rngd` on host; install `haveged` |
| Connection reset for long-lived container TCP sessions through NAT | Long-running container connections (SSH tunnels, gRPC streams) drop after 15–30 min | `podman exec <container> grep "Connection reset\|Broken pipe" /app/logs/app.log | tail -20` | Conntrack table entry expiry; NAT session timeout shorter than application keepalive interval | Set TCP keepalive in container: `podman run -e GRPC_KEEPALIVE_TIME_MS=30000 ...`; sysctl in container: `net.ipv4.tcp_keepalive_time=60` |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill of container | Container exits with `OOMKilled` status; `dmesg` shows OOM kill | `podman inspect <container> --format '{{.State.OOMKilled}} {{.State.ExitCode}}'`; `journalctl -k --since "1h ago" | grep -i "oom\|killed"` | `podman start <container>`; reduce container memory pressure; increase memory limit | Set `--memory` limit in Quadlet; monitor RSS vs limit with `podman stats`; alert at 80% |
| Disk full on overlay storage partition | New container starts fail; `podman pull` fails; writes inside containers fail | `df -h /var/lib/containers`; `podman system df` | `podman image prune -a -f`; `podman container prune`; `podman volume prune --filter label=safe-to-prune=true` | Systemd timer for weekly `podman system prune -f`; separate partition for `/var/lib/containers` |
| Disk full on container log partition | Container log writes fail; application exits; host syslog fills | `journalctl --disk-usage`; `du -sh /var/log/journal`; `podman inspect <c> --format '{{.HostConfig.LogConfig}}'` | `journalctl --vacuum-size=2G`; rotate logs: `podman update --log-opt max-size=50m <container>` | Set `--log-opt max-size=50m --log-opt max-file=5`; configure `journald.conf` `SystemMaxUse=4G` |
| File descriptor exhaustion in container | Application fails to open files; `EMFILE` errors in logs | `ls -l /proc/$(podman inspect <c> --format '{{.State.Pid}}')/fd | wc -l`; compare to `ulimit -n` | `podman update --ulimit nofile=65536:65536 <container>`; restart container | Set `--ulimit nofile=65536:65536` in every Quadlet `[Container]` section |
| Inode exhaustion from container temp file accumulation | Writes inside container fail; `df -i` on host overlay shows 100% | `df -i /var/lib/containers/storage/overlay`; `find /var/lib/containers/storage/overlay -type f | wc -l` | `podman container prune`; `podman image prune -a` to reclaim inode-heavy layers | Monitor inodes at 80%; use `ext4` with `large_file` for overlay storage partition |
| CPU steal/throttle from cgroup CPU quota | Container CPU-bound work takes 3–5× longer than expected; `%throttled` in cgroup | `cat /sys/fs/cgroup/system.slice/$(systemctl --user show <service> -p Id --value)/cpu.stat | grep throttled` | `podman update --cpus 2.0 <container>` to increase quota; or remove quota temporarily | Set `--cpus` proportional to actual need; benchmark before setting CPU limits |
| Swap exhaustion from container memory pressure | Host swap 100%; containers OOM-killed sequentially; host unresponsive | `free -h`; `podman stats --no-stream --format "{{.Name}}: {{.MemUsage}}"` sorted by usage | `podman stop <heaviest-container>`; `swapoff -a && swapon -a` | Set `--memory-swap` equal to `--memory` to disable swap for containers; `vm.swappiness=10` on host |
| Kernel PID limit from container fork bomb or thread explosion | New processes cannot be spawned on host; `fork: Resource temporarily unavailable` | `cat /proc/sys/kernel/pid_max`; `podman exec <c> ps aux | wc -l` | `podman stop <suspected-container>`; `sysctl -w kernel.pid_max=131072` | Set `--pids-limit 500` in Quadlet `[Container]` to prevent fork bombs; monitor with `podman stats` |
| Network socket buffer exhaustion for high-throughput container service | Container network throughput plateaus; kernel drops packets; socket send buffer full | `ss -m | grep <container-ip>`; `sysctl net.core.rmem_max net.core.wmem_max` | `sysctl -w net.core.rmem_max=134217728 net.core.wmem_max=134217728` | Pre-tune socket buffers for high-throughput containers; pass via `--sysctl net.core.rmem_max=134217728` |
| Ephemeral port exhaustion from container outbound connection storm | Container application: `Cannot assign requested address` for all outbound connections | `podman exec <c> ss -s | grep TIME-WAIT`; `cat /proc/sys/net/ipv4/ip_local_port_range` | `sysctl -w net.ipv4.tcp_fin_timeout=10 net.ipv4.tcp_tw_reuse=1`; pass via `--sysctl` | Set `--sysctl net.ipv4.tcp_tw_reuse=1` on container; implement outbound connection pooling |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation from container restart mid-job | Job container processes same work unit twice after OOM restart; duplicate records in DB | `podman inspect <job-container> --format '{{.State.ExitCode}} {{.State.OOMKilled}}'`; check job DB for duplicates | Duplicate data; potential billing or inventory errors | Add idempotency key to job: write `processed_jobs` record before processing; check before starting |
| Saga partial failure: container A committed, container B crashed | Container A wrote to DB; container B (next saga step) OOM-killed before processing | `podman inspect <container-b> --format '{{.State.ExitCode}} {{.State.OOMKilled}}'`; check application DB for uncommitted saga step | Workflow stuck in partial state; resource locked | Implement saga orchestrator with compensating transactions; use outbox pattern in container A |
| Message replay causing stale container state | Quadlet service restarted and reprocesses old messages from Kafka/Redis queue | `podman logs --since 1h <container> | grep "Processing message\|Duplicate"` | Stale data overwrites current state; data integrity regression | Add message offset tracking in container's state store; use `--consumer-group` with committed offsets |
| Cross-container deadlock via shared named volume | Two containers writing to same file in named volume simultaneously; both block indefinitely | `podman exec <c1> ls -la /data/lockfile`; `podman exec <c2> ls -la /data/lockfile`; `fuser /var/lib/containers/storage/volumes/<vol>/_data/*` | Both containers hung; service outage | `podman restart <c1>`; redesign to use one container as writer, others as readers | Use advisory file locks with timeout; or replace shared-volume IPC with message queue |
| Out-of-order container startup causing dependency race | Service container starts before its database dependency is ready; connects to empty DB | `podman logs <service-container> | grep "connection refused\|no such table"`; `systemctl list-dependencies <service>.service` | Service initialises against empty/partial DB; corrupted application state | Add `After=<db-service>.service` and `ExecStartPre=/bin/sh -c "until podman exec <db> pg_isready; do sleep 1; done"` in Quadlet |
| At-least-once delivery duplicate from Quadlet container restart on failure | Quadlet restarts container on non-zero exit; partial work already committed; restarts re-execute same work | `journalctl --user -u <service>.service | grep "Restart\|MainPID"` restart count | Duplicate processing; idempotency violated | Add `Restart=on-failure` with `StartLimitBurst=3`; wrap work unit in idempotency check before processing |
| Compensating transaction failure after container hard-kill | Container killed mid-transaction via `podman stop --time 0`; DB left in intermediate state | `podman inspect <c> --format '{{.State.FinishedAt}} {{.State.ExitCode}}'`; check DB for open transactions | Orphaned locks or partial writes in DB | Allow graceful shutdown: use `podman stop --time 30` to give SIGTERM time for cleanup; implement SIGTERM handler in app |
| Distributed lock expiry mid-operation: container paused by cgroup CPU throttle | Container holds distributed lock (Redis/DB advisory) but CPU throttled; lock TTL expires; second container acquires lock | `cat /sys/fs/cgroup/system.slice/$(systemctl show <service> -p Id --value)/cpu.stat | grep throttled_time`; check Redis `TTL <lockkey>` | Two containers executing same critical section simultaneously; data corruption | Extend lock TTL beyond worst-case throttle duration; implement lock heartbeat thread; use `--cpus` to prevent throttle |

## Multi-tenancy & Noisy Neighbor Patterns
| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor: one tenant's container consuming all available CPU | `podman stats --no-stream --format "{{.Name}}: {{.CPUPerc}}"` — one container at 90%+ | Other tenant containers CPU-starved; increased latency | `podman update --cpus 1.0 <noisy-container>` | Set `--cpus` limit in all Quadlet definitions; use cgroup CPU shares: `--cpu-shares=512` for fair scheduling |
| Memory pressure from adjacent tenant container | `podman stats --no-stream --format "{{.Name}}: {{.MemUsage}}"` — one container near its limit, others being swapped | Host swap pressure; OOM risk for adjacent containers | `podman update --memory 512m --memory-swap 512m <noisy-container>` | Enforce `--memory` and `--memory-swap` limits in all Quadlet files; monitor per-container RSS with `podman stats` |
| Disk I/O saturation from tenant container bulk write | `iostat -xz 1 5` shows host disk `%util` near 100% correlating with one container's I/O | All containers see increased filesystem latency | `podman update --blkio-weight 100 <noisy-container>` to deprioritize its I/O | Use `--device-write-bps` and `--device-read-bps` limits; mount tenant write paths on separate volumes on dedicated disks |
| Network bandwidth monopoly from tenant container | `podman exec <container> cat /proc/net/dev | awk '{print $1, $2, $10}'` — one container's interface at bandwidth cap | Other containers see network latency; inter-container communication degraded | `podman network create --opt com.docker.network.driver.mtu=1500 tenant-limited` | Use `tc qdisc` on container veth interface: `tc qdisc add dev veth<id> root tbf rate 100mbit burst 32kbit latency 400ms` |
| Connection pool starvation: tenant container leaking outbound sockets | `ss -tp | grep <tenant-container-pid> | grep ESTABLISHED | wc -l` — hundreds of connections | Shared backend services (DB, cache) connection limits exhausted | `podman restart <tenant-container>` to close leaked connections | Set `--ulimit nofile=1024:1024` for tenant containers; enforce connection pool in application |
| Quota enforcement gap: container writing to shared named volume without limit | `podman volume inspect <shared-vol> | jq '.[].Mountpoint'`; `du -sh <mountpoint>` shows one tenant's data dominating | Shared volume fills up; other tenants' writes fail | No built-in Podman volume quota; use ext4/XFS project quotas on volume mountpoint | Use separate named volumes per tenant; enforce filesystem-level project quota: `xfs_quota -x -c "project -s -p /mnt/vol <tenant_id>"` |
| Cross-tenant data leak risk via shared named volume | `podman volume inspect <vol>`; `ls -la <mountpoint>` — check if multiple tenant containers mount same volume with `rw` | Tenant A container can read Tenant B's files in shared volume | `podman run --volume <vol>:/data:ro <tenant-b-container>` — remount as read-only | Mount per-tenant volumes: `podman run -v tenant_<id>_data:/data ...`; never share `rw` volumes across tenants |
| Rate limit bypass: tenant container spawning excessive worker processes | `podman exec <tenant-container> ps aux | wc -l` — many worker processes; host PID table filling | Host PID limit approached; other containers cannot spawn new processes | `podman update --pids-limit 100 <tenant-container>` | Set `--pids-limit` in all tenant Quadlet definitions; alert when container PID count > 80% of limit |

## Observability Gap & Monitoring Failure Patterns
| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure for container resource metrics | `container_cpu_usage_seconds_total` absent in Grafana; Prometheus shows scrape error | `podman-exporter` or `cadvisor` sidecar crashed; network partition to metrics port | `podman stats --no-stream` directly on host; `podman inspect <container>` for resource config | Add liveness probe to `podman-exporter`; alert on `up{job="podman_exporter"}==0`; use systemd watchdog for exporter |
| Trace sampling gap: missing container crash restart traces | Container OOM-kill cycle not visible in APM; only healthy-state traces collected | Container restarts happen in < 1 s; traces in-flight discarded on crash | `podman events --filter event=died --since 1h`; `podman inspect <container> --format '{{.State.RestartCount}}'` | Add Prometheus counter `container_restart_count_total`; alert on `delta(container_restart_count_total[5m]) > 2` |
| Log pipeline silent drop for container stdout logs | Application logs missing from Splunk during high-throughput burst | Journald log rate-limit (`RateLimitBurst`) exceeded; logs dropped at journald level | `journalctl --user -u <service> --since "1h ago" | head -100` to check if logs present in journald | Increase `RateLimitBurst=10000` in `/etc/systemd/journald.conf`; or switch container to `--log-driver=json-file` with explicit rotation |
| Alert rule misconfiguration: container OOM alert never fires | Container OOM-killed repeatedly; no PagerDuty page | Alert watching `container_oom_kill_total` metric but Podman exporter uses different metric name | `podman inspect <container> --format '{{.State.OOMKilled}}'`; `journalctl -k | grep -i "oom\|killed"` | Reconcile metric name: check `podman_container_info` labels; update alert to correct metric; test with `podman run --memory 10m stress` |
| Cardinality explosion from per-container-run-id label | Prometheus OOM; every container restart creates new series due to unique run ID label | Short-lived job containers each emit metrics with unique `container_id` label | `curl http://localhost:9090/api/v1/label/__name__/values | jq length` — if exploding, cardinality issue | Remove `container_id` label; aggregate metrics by `container_name` only; drop high-cardinality labels in Prometheus relabeling rules |
| Missing health endpoint for Quadlet-managed service | Quadlet service crashing in restart loop; no alert; load balancer still routing traffic | Quadlet unit has `RestartAlways` but no HTTP health check configured | `systemctl --user status <service>.service`; `podman inspect <container> --format '{{.State.Status}}'` | Add `HEALTHCHECK` in Dockerfile; configure Quadlet `HealthStartPeriod` and `HealthCmd`; alert on `podman healthcheck run <c>` non-zero |
| Instrumentation gap: container image pull latency not tracked | Slow image pull on container restart extends downtime; not visible in APM | Podman pull events not exported to Prometheus; no timing metric | `podman events --filter event=pull --since 1h --format "{{.Time}} {{.Actor.Attributes.name}}"` | Add custom metric: scrape `podman events` stream; publish `podman_image_pull_duration_seconds` gauge per image tag |
| Alertmanager outage silencing Podman host-level alerts | Podman host OOM event undetected; no on-call notification | Alertmanager pod itself running in Podman and OOM-killed during host memory pressure event | `amtool alert query`; `podman ps -a | grep alertmanager` — check if container exited | Run Alertmanager outside Podman on a separate host; or use external alerting (PagerDuty Heartbeat); set `--memory 512m` for alertmanager container |

## Upgrade & Migration Failure Patterns
| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Podman minor version upgrade rollback | Container fails to start after Podman upgrade; `crun` or `runc` version mismatch; `podman run` returns `Error: container_linux.go` errors | `podman --version`; `crun --version`; `podman info | grep -E "version|runtime"` | `dnf downgrade podman`; or `apt-get install podman=<prev-ver>`; `systemctl daemon-reload` | Test Podman upgrade in staging environment first; pin `podman` version in Ansible/Salt/Chef config management |
| Container image upgrade: new image breaks volume mount paths | Container starts but application fails because data path changed in new image | `podman inspect <container> --format '{{.Mounts}}'` — compare source/destination with previous image's VOLUME declarations | `podman stop <container>`; `podman run -v <old-volume>:<new-path> <old-image>` to revert to old image | Always `podman inspect` new image for VOLUME and WORKDIR changes before deploying; pin image by digest |
| Quadlet schema change breaking service unit after systemd upgrade | `systemctl --user start <service>` fails with `Unknown key name`; Quadlet `.container` file not parsed | `systemctl --user status <service>`; `journalctl --user -u <service> | grep "Unknown\|invalid"` | Revert systemd to previous version; or remove unsupported Quadlet key from `.container` file | Check Quadlet changelog for removed/renamed keys before upgrading systemd; use `systemd-analyze verify` on unit files |
| Rolling upgrade version skew: old container using v1 API, new using v2 | Mixed deployment: some containers return 200, others return 404 for new API path | `podman ps --format "{{.Names}}\t{{.Image}}"` — check image tags across all instances; `podman inspect <c> --format '{{.Config.Image}}'` | `podman stop <new-containers>`; `podman run <old-image>` to revert | Use `podman run --replace` for atomic swap; enforce all-or-nothing deployment via Quadlet + systemd |
| Zero-downtime Quadlet migration from old unit file gone wrong | Service unreachable after Quadlet file update and `systemctl --user daemon-reload` | `systemctl --user status <service>.service`; `podman ps -a | grep <service>` — check if new container started correctly | `cp /backup/<service>.container ~/.config/containers/systemd/`; `systemctl --user daemon-reload && systemctl --user restart <service>` | Backup existing Quadlet files before modification; test with `--dry-run` flag; validate with `systemd-analyze verify <file>` |
| Config format change breaking old container: deprecated `--net` flag | `podman run --net=host` fails after upgrade where flag renamed to `--network=host` | `podman run --help | grep network` to verify current flag name | Update Quadlet file: replace `Network=host` key; or use old CLI form if still supported | Use `podman generate systemd` to generate Quadlet from working `podman run` command; avoid deprecated flags |
| Data format incompatibility: overlay storage migrated to new format | `podman start <container>` fails: `Error: layers from different storage drivers cannot be combined` after storage migration | `podman info | grep graphDriver`; `cat /etc/containers/storage.conf | grep driver` | `podman system reset --force` (WARNING: deletes all containers/images); restore from registry and named volume backups | Back up named volumes before storage driver migration; never change storage driver on live system without full backup |
| Dependency version conflict: container base image updated, shared library version changed | Application in container exits with `error while loading shared libraries: libssl.so.3` | `podman exec <container> ldd /app/binary | grep "not found"`; `podman inspect <container> --format '{{.Config.Image}}'` | Roll back to previous image tag: `podman run <image>:<prev-tag>`; update Quadlet `Image=` to pinned digest | Pin base image by digest in Dockerfile `FROM`; use multi-stage build to control library versions explicitly |

## Kernel/OS & Host-Level Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| OOM killer targets rootless Podman conmon process | Container abruptly stops; `podman ps -a` shows `Exited (137)`; no application-level error | `dmesg -T | grep -i 'oom.*conmon'`; `journalctl -k --since "1h ago" | grep -i killed`; `podman inspect <c> --format '{{.State.OOMKilled}}'` | Container workload terminated mid-operation; data corruption possible if write in progress; restart loop if systemd `Restart=always` | Set `--memory` limit below host cgroup threshold; `loginctl enable-linger <user>` for rootless; add `OOMScoreAdjust=-500` in Quadlet unit |
| Inode exhaustion from overlay storage layers | `podman build` fails with `no space left on device`; `podman pull` fails; running containers unaffected | `df -i /var/lib/containers/storage` (rootful) or `df -i ~/.local/share/containers/storage` (rootless); `podman system df` | Cannot pull new images, build, or create containers; existing running containers continue but cannot checkpoint or export | `podman system prune --all --force`; `podman image prune -a`; mount storage on XFS with higher inode density; set up cron: `podman system prune --filter until=72h` |
| CPU steal delays container health checks causing false restarts | Quadlet-managed container repeatedly restarted by systemd; health check passes when run manually | `sar -u 1 5 | grep steal`; `systemctl --user status <service>.service` shows `start-limit-hit`; `podman healthcheck run <c>` succeeds manually | Service flaps between running and restarting; dependent services see intermittent connectivity; load balancer removes backend | Increase `HealthStartPeriod` and `HealthInterval` in Quadlet; set `StartLimitBurst=10` in systemd unit; migrate to dedicated instance |
| NTP clock skew breaks rootless Podman certificate validation | `podman pull` fails with `x509: certificate has expired or is not yet valid`; system clock off by minutes | `timedatectl status | grep synchronized`; `date -u`; `podman pull --tls-verify=false <image>` succeeds (confirms clock issue) | Cannot pull images; container builds fail; registry auth token validation fails; all image operations blocked | `chronyc makestep`; enable and start `chronyd.service`; alert on `abs(clock_skew_seconds) > 2`; never use `--tls-verify=false` in production |
| File descriptor exhaustion from leaked Podman exec sessions | `podman exec` returns `too many open files`; `podman logs` hangs; new containers cannot start | `ls /proc/$(pgrep conmon)/fd | wc -l`; `podman system info --format '{{.Host.ConmonVersion}}'`; `ulimit -n` | Cannot attach to running containers; log streaming breaks; health checks that use `podman exec` fail causing container restarts | Upgrade conmon to latest version (fixes fd leak); set `LimitNOFILE=65536` in Quadlet unit; restart leaked conmon processes: `podman container cleanup --all` |
| TCP conntrack table full drops container-to-container traffic | Containers in same pod communicate intermittently; `curl` between containers times out sporadically | `dmesg | grep "nf_conntrack: table full"`; `sysctl net.netfilter.nf_conntrack_count`; `podman network inspect <network>` | Inter-container networking in pods breaks; database connections from app container to DB container fail | `sysctl -w net.netfilter.nf_conntrack_max=524288`; for rootless, use `pasta` network mode to bypass conntrack; reduce connection churn with persistent connections |
| cgroup v2 delegation missing for rootless Podman | `podman run --cpus=2` ignored; container uses all host CPUs; `podman stats` shows no CPU limit enforced | `podman info --format '{{.Host.CgroupVersion}}'`; `cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/cgroup.controllers` — missing `cpu memory` | Resource limits not enforced; noisy neighbor containers starve others; runaway container consumes all host CPU | Add `Delegate=cpu memory pids` to `/etc/systemd/system/user@.service.d/delegate.conf`; `systemctl daemon-reload`; verify with `podman run --rm --cpus=1 stress --cpu 4` |
| Kernel user namespace limit reached for rootless containers | `podman run` fails with `cannot set up user namespace`; `podman unshare` fails | `sysctl user.max_user_namespaces`; `ls /proc/*/ns/user | wc -l`; `podman info --format '{{.Host.IDMappings}}'` | No new rootless containers can be created; existing containers unaffected but cannot restart if stopped | `sysctl -w user.max_user_namespaces=65536`; persist in `/etc/sysctl.d/99-podman.conf`; clean up orphaned namespaces: `podman system reset` on unused accounts |

## Deployment Pipeline & GitOps Failure Patterns
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| Image pull rate limit from Docker Hub in CI pipeline | `podman pull docker.io/<image>` fails with `429 Too Many Requests`; CI pipeline stalls | `podman pull <image> 2>&1 | grep -i "429\|rate\|limit"`; check Docker Hub rate limit: `skopeo inspect --raw docker://docker.io/<image> 2>&1` | All container builds and deployments blocked; no new images can be pulled; existing cached images still work | Configure registry mirror: `[[registry.mirror]] location="mirror.example.com"` in `/etc/containers/registries.conf`; authenticate to Docker Hub: `podman login docker.io` |
| Quadlet file drift between Git and running systemd state | Quadlet `.container` file updated in Git but `systemctl --user daemon-reload` not run; old container spec still active | `diff <(cat ~/.config/containers/systemd/<service>.container) <(systemctl --user show <service> -p ExecStart)`; `git diff HEAD -- <quadlet-file>` | Running container has stale config (wrong image tag, missing env vars, old volume mounts); drift grows over time | Add `systemctl --user daemon-reload && systemctl --user restart <service>` to deployment pipeline; use Ansible/Salt to enforce Quadlet state |
| Buildah build fails due to stale layer cache after base image CVE patch | `podman build` uses cached layer with vulnerable base; security scan fails post-build | `podman image inspect <image> --format '{{.Created}}'`; `buildah inspect --format '{{.FromImage}}'`; `skopeo inspect docker://<base-image> | jq '.Created'` | Vulnerable image deployed to production; security scan blocks promotion; manual rebuild required | Add `--no-cache` for security-sensitive builds; `podman build --pull=always` to force base image refresh; add base image age check to CI |
| PDB-equivalent (systemd restart limit) blocks Quadlet service recovery | Quadlet service hit `StartLimitBurst`; systemd refuses to restart; container stays down | `systemctl --user status <service>.service | grep "start-limit-hit"`; `journalctl --user -u <service> | tail -20` | Service permanently down until manual intervention; load balancer health checks fail; dependent services cascade | `systemctl --user reset-failed <service>.service && systemctl --user start <service>.service`; increase `StartLimitBurst=20 StartLimitIntervalSec=600` in Quadlet |
| Blue-green Quadlet cutover fails: port conflict between old and new container | New container cannot bind port; `podman run` returns `address already in use`; old container still running | `podman ps --format '{{.Names}} {{.Ports}}'`; `ss -tlnp | grep <port>`; `systemctl --user status <old-service> <new-service>` | Zero-downtime deployment fails; either old or new service runs but not both; manual cleanup required | Use `podman run --replace` for atomic container swap; or use `podman pod` with shared network namespace; stop old before starting new in Quadlet ordering |
| ConfigMap-equivalent drift: Podman secret outdated after rotation | Application reads stale database password from `podman secret`; authentication fails | `podman secret inspect <secret> --format '{{.CreatedAt}}'`; compare with secret source (Vault, file); `podman logs <c> | grep -i "auth\|denied\|password"` | Application cannot authenticate to database/API; cascading failures; container restarts don't help because secret is stale | `podman secret rm <secret> && podman secret create <secret> <new-file>`; restart container: `podman restart <c>`; automate rotation with Vault agent |
| Skopeo copy fails during cross-registry image promotion | `skopeo copy` from staging to production registry fails with TLS error; image promotion pipeline blocked | `skopeo copy --debug docker://<src> docker://<dst> 2>&1 | grep -i "tls\|cert\|auth"`; `skopeo inspect docker://<dst>/<image>` | New image version not available in production registry; rollout blocked; staging and production image diverge | Verify registry certificates: `openssl s_client -connect <registry>:443`; update `/etc/containers/certs.d/<registry>/` with CA cert; retry with `--src-tls-verify=false` only for debugging |
| Ansible playbook deploys Quadlet file but forgets daemon-reload | New Quadlet `.container` file placed but systemd unaware; service continues running old spec | `systemctl --user show <service> -p FragmentPath`; `diff <(systemctl --user cat <service>) <(cat <quadlet-file>)` | Deployed changes not applied; operator believes deployment succeeded; config drift accumulates | Add `systemctl --user daemon-reload` handler in Ansible; use `systemd` Ansible module with `daemon_reload: yes`; add post-deploy verification step |

## Service Mesh & API Gateway Edge Cases
| Failure | Symptom | Detection | Service-Specific Impact | Remediation |
|---------|---------|-----------|------------------------|-------------|
| Podman container network conflicts with host CNI/mesh sidecar | Container in `podman pod` cannot reach mesh-managed services; DNS resolution works but TCP connections reset | `podman network inspect podman`; `ip route show table all | grep podman`; `podman exec <c> curl -v <mesh-service>` | Containers running outside Kubernetes mesh cannot communicate with mesh-internal services; hybrid deployments broken | Use `--network=host` for mesh-integrated containers; or configure `pasta` network with explicit routes to mesh subnet; add iptables DNAT rules |
| Rate limiting on reverse proxy blocks Podman health check endpoint | Health check returns 429; Quadlet marks container unhealthy; systemd restarts container in loop | `podman healthcheck run <c>`; `podman logs <c> | grep -i "429\|rate"`; check reverse proxy access log for health check path | Legitimate health checks throttled; container restart loop; service unavailable despite healthy application | Exempt health check path from rate limiting in reverse proxy config; use TCP health check instead of HTTP; configure separate health check port |
| Stale DNS in Podman container after host resolver change | Container resolves old IP for service endpoint; new pods/containers at new IP unreachable | `podman exec <c> cat /etc/resolv.conf`; `podman exec <c> nslookup <service>`; compare with host `resolvectl status` | Container talks to wrong backend; stale responses; potential data going to decommissioned host | Restart container to pick up new resolv.conf: `podman restart <c>`; or use `--dns` flag to pin DNS server; mount `/etc/resolv.conf` as volume |
| mTLS sidecar certificate rotation breaks Podman-managed reverse proxy | Podman-managed nginx/envoy container loses TLS connectivity when host-level cert rotated; `502 Bad Gateway` | `podman exec <c> openssl s_client -connect <upstream>:443 2>&1 | grep "verify error"`; `podman exec <c> ls -la /certs/` | Reverse proxy cannot terminate or originate TLS; all HTTPS traffic through this proxy fails | Mount certificate directory as volume with `:z` SELinux label; use `inotifywait` sidecar to reload proxy on cert change; `podman exec <c> nginx -s reload` |
| Retry storm from Podman containers overwhelms backend service | Backend service receives 5x normal traffic; all from Podman-hosted retry-enabled clients | `podman stats --no-stream --format '{{.Name}} {{.NetIO}}'`; `podman exec <c> ss -tn | wc -l`; backend access logs show repeated requests from same source | Backend overloaded; cascading failure; retries make situation worse; circuit breaker on backend trips | Add exponential backoff to application retry config; configure connection limits per container: `podman run --ulimit nofile=1024`; implement client-side circuit breaker |
| gRPC long-lived streams broken by Podman container restart during rolling update | gRPC clients receive `UNAVAILABLE` during container replacement; reconnection takes 30s+ due to DNS caching | `podman logs <c> | grep -i "grpc\|goaway\|unavailable"`; `podman events --filter event=die --since 5m` | gRPC streaming consumers disconnect; message processing paused; backlog accumulates | Implement gRPC graceful shutdown: send GOAWAY before SIGTERM; set `--stop-timeout=30` in Podman; configure client keepalive and retry policy |
| Trace context lost between Podman containers in same pod | Distributed traces show gap between frontend and backend containers; no parent-child span relationship | `podman exec <frontend> env | grep -i trace`; `podman exec <backend> env | grep -i trace`; check Jaeger for orphan spans | Cannot trace requests end-to-end; latency attribution broken; debugging cross-container issues requires log correlation | Configure shared `podman pod` network namespace; propagate trace headers via environment or shared volume; use OpenTelemetry auto-instrumentation |
| API gateway cannot reach Podman container after IP change from restart | Gateway returns 502; backend container restarted and got new IP; gateway health check cached old IP | `podman inspect <c> --format '{{.NetworkSettings.IPAddress}}'`; compare with gateway upstream config; `podman events --filter event=start --since 1h` | Requests to backend fail; gateway marks upstream unhealthy; traffic shifted to remaining backends (if any) | Use Podman DNS name resolution (`podman network create` with DNS plugin); configure gateway upstream by hostname not IP; reduce gateway health check interval |
