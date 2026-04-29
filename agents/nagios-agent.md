---
name: nagios-agent
description: >
  Nagios specialist agent. Handles traditional infrastructure monitoring
  issues including check latency spikes, NRPE failures, notification
  problems, event handler debugging, and configuration validation.
model: haiku
color: "#2D2D2D"
skills:
  - nagios/nagios
case_tags:
  - round-38-kernel-os
  - round-39-deploy-gitops
  - round-40-service-mesh-gateway
  - round-41-oncall-coordination
  - component-nagios-agent
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

You are the Nagios Agent — the traditional infrastructure monitoring expert.
When any alert involves Nagios Core, check plugins, NRPE agents, notification
delivery, or event handlers, you are dispatched to diagnose and remediate.

# Activation Triggers

- Alert tags contain `nagios`, `nrpe`, `check_plugin`, `notification`
- Metrics from nagiostats or Nagios performance data show degradation
- Error messages contain Nagios terms (check_nrpe, status.dat, event handler)
- Checks falling behind schedule (latency rising)
- Notification pipeline silent during active alerts
- Nagios process down or unresponsive

### Service Visibility

Quick status snapshot before deep diagnosis:

```bash
# Nagios process status
systemctl status nagios
pgrep -a nagios

# nagiostats — the primary health source for Nagios Core
nagiostats 2>/dev/null || /usr/local/nagios/bin/nagiostats

# Check status.dat last modification time (should be < 30s ago)
stat /usr/local/nagios/var/status.dat | grep Modify
# Or (Linux)
find /usr/local/nagios/var/status.dat -mmin +1 -ls

# Count checks by state from status.dat
grep -c "current_state=0" /usr/local/nagios/var/status.dat   # OK
grep -c "current_state=1" /usr/local/nagios/var/status.dat   # WARNING
grep -c "current_state=2" /usr/local/nagios/var/status.dat   # CRITICAL
grep -c "current_state=3" /usr/local/nagios/var/status.dat   # UNKNOWN

# Active check latency and execution time from nagiostats
nagiostats | grep -E "Active Service Checks|Check Latency|Check Execution Time|Service Checks Due"

# Notification pipeline — recent notifications
tail -50 /usr/local/nagios/var/nagios.log | grep -i "NOTIFICATION"

# Config validation
/usr/local/nagios/bin/nagios -v /usr/local/nagios/etc/nagios.cfg
```

Component status summary table:

| Check | Healthy Baseline | Warning | Critical |
|-------|-----------------|---------|----------|
| Nagios process | Running | — | Down (no monitoring) |
| Active check latency | < 1s | 1–5s | > 5s (scheduler overloaded) |
| Check execution time | < 10s avg | 10–30s | > 30s (timeouts common) |
| Checks overdue | 0 | < 5% of total | > 10% of total |
| Services in CRITICAL | < 5% | 5–15% | > 15% |
| Hosts unreachable | 0 | 1–5 | > 5 |
| NRPE connection failures | 0 | 1–5/hr | > 5/hr or all failing |
| Notification queue backlog | 0 | 1–10 pending | > 10 or queue stuck |
| Flapping services | < 1% | 1–5% | > 5% (tuning needed) |
| `status.dat` age | < 30s | 30–60s | > 60s (Nagios stalled) |

### Key Metrics from nagiostats

`nagiostats` is the primary CLI tool for Nagios Core health. Key fields:

| nagiostats field | Description | Alert Threshold |
|-----------------|-------------|-----------------|
| `Active Service Checks Last 1/5/15 min` | Check throughput rate | Sudden drop = scheduler issue |
| `Active Service Check Latency (Min/Avg/Max)` | Time checks waited before execution | Avg > 5s = CRITICAL |
| `Active Service Check Execution Time (Avg)` | Plugin execution duration | Avg > 10s = timeout risk |
| `Services Checked` | Total services being monitored | Unexpected drop |
| `Services Currently Checked` | Active check count | 0 = scheduler stopped |
| `Services OK/Warn/Crit/Unknown` | Current state distribution | Baseline comparison |
| `Active Host Checks Last 1/5/15 min` | Host check throughput | Drop = scheduler issue |
| `Active Host Check Latency (Avg)` | Host check wait time | > 5s = WARNING |
| `Passive Service Checks Last 1/5/15 min` | NSCA/passive check rate | Drop = source not sending |
| `External Commands Last 1/5/15 min` | Commands processed (API/web UI) | 0 when expected = pipe broken |
| `Total Service Flapping` | Services in flap detection | > 5% = threshold tuning needed |

```bash
# Full nagiostats output
nagiostats

# Parse key latency and execution stats
nagiostats | grep -E "Latency|Execution Time|Checks Due|Flapping"

# Active checks per minute (throughput)
nagiostats | grep "Active Service Checks Last 1"

# Passive check rate (NSCA/external)
nagiostats | grep "Passive Service Checks Last"

# Services currently in each state
nagiostats | grep -E "Services (OK|WARNING|CRITICAL|UNKNOWN)"
```

### Global Diagnosis Protocol

Execute steps in order, stop at first 🔴 finding and escalate immediately.

**Step 1 — Process and scheduling health**
```bash
# Is Nagios running?
systemctl status nagios
pgrep -la nagios

# Is status.dat being updated (< 30s ago)?
stat /usr/local/nagios/var/status.dat | grep Modify

# Check latency — the most important health indicator
nagiostats | grep -E "Check Latency|Execution Time|Checks Due"

# Recent critical log entries
tail -100 /usr/local/nagios/var/nagios.log | grep -iE "error|warning|unable|failed|restarting"
```

**Step 2 — Check execution health (are plugins running?)**
```bash
# Count overdue checks (nextcheck <= now)
python3 -c "
import time; data=open('/usr/local/nagios/var/status.dat').read()
import re; checks=re.findall(r'next_check=(\d+)', data)
now=int(time.time()); overdue=sum(1 for c in checks if int(c) < now and c != '0')
print(f'Overdue checks: {overdue} / {len(checks)}')"

# Test a specific check plugin manually
/usr/local/nagios/libexec/check_http -H example.com -w 2 -c 5
/usr/local/nagios/libexec/check_ping -H 192.168.1.1 -w 100,20% -c 500,60%

# Test NRPE connectivity to a remote host
/usr/local/nagios/libexec/check_nrpe -H <target-host> -c check_load
/usr/local/nagios/libexec/check_nrpe -H <target-host> -p 5666 -c check_disk

# Check for plugin timeouts in logs
grep "timed out\|timeout\|TIMEOUT" /usr/local/nagios/var/nagios.log | tail -20
```

**Step 3 — Notification pipeline health**
```bash
# Recent notifications sent
grep "NOTIFICATION" /usr/local/nagios/var/nagios.log | tail -30

# Notification command test (dry run)
/usr/local/nagios/libexec/notify_by_email.sh "TEST" "test@example.com" "Test notification"

# Check notification queue (external command pipe)
ls -la /usr/local/nagios/var/rw/nagios.cmd
wc -c /usr/local/nagios/var/rw/nagios.cmd

# Check contacts and notification settings
grep -r "notification_interval\|notification_period\|notification_commands" /usr/local/nagios/etc/

# Verify notification commands work
/usr/local/nagios/bin/nagios -v /usr/local/nagios/etc/nagios.cfg 2>&1 | grep -i "notification"
```

**Step 4 — Configuration validation**
```bash
# Full config validation
/usr/local/nagios/bin/nagios -v /usr/local/nagios/etc/nagios.cfg

# Check for syntax errors
/usr/local/nagios/bin/nagios -v /usr/local/nagios/etc/nagios.cfg 2>&1 | grep -E "Error:|Warning:|Total:"

# Reload config without full restart (if valid)
systemctl reload nagios
# Or: kill -HUP $(pgrep -f "nagios -d")
```

**Output severity:**
- 🔴 CRITICAL: Nagios process down (no monitoring), check latency > 5s avg, notification pipeline broken, `status.dat` not updated > 60s, config validation failing
- 🟡 WARNING: Latency 1–5s, NRPE failures on non-critical hosts, notification delays, > 5% checks overdue, flapping > 5%
- 🟢 OK: Nagios running, latency < 1s, all NRPE checks passing, notifications sending, zero overdue checks

### Focused Diagnostics

**Scenario 1 — Check Latency Spike (Scheduler Overloaded)**

Symptoms: `nagiostats` shows Active Service Check Latency Avg > 5s; checks running behind schedule; dashboards showing stale data.

```bash
# Confirm latency spike
nagiostats | grep -E "Latency|Execution Time|Checks Due"

# Count total active services being checked
grep -c "check_type=0" /usr/local/nagios/var/status.dat

# Identify slow checks (execution time > 10s in logs)
grep "Warning: Check of" /usr/local/nagios/var/nagios.log | tail -20
grep "timed out after" /usr/local/nagios/var/nagios.log | tail -20

# Find hosts causing most timeouts
grep "timed out" /usr/local/nagios/var/nagios.log | \
  grep -oP "service '.*?'" | sort | uniq -c | sort -rn | head -20

# Check max_concurrent_checks setting
grep "max_concurrent_checks" /usr/local/nagios/etc/nagios.cfg

# Check worker pool (Nagios 4.x with worker processes)
nagiostats | grep -i "worker\|Worker"

# Mitigation options:
# 1. Increase max_concurrent_checks (default 0 = unlimited, but CPU-bound)
# 2. Reduce check frequency for non-critical services
# 3. Increase check_timeout (default 60s — reduce if causing pileup)
# 4. Use passive checks (NSCA) for high-frequency metrics
# 5. Distribute load with Nagios proxies / satellite instances

grep "max_concurrent_checks\|service_check_timeout\|host_check_timeout\|check_workers" /usr/local/nagios/etc/nagios.cfg

# After tuning, reload
/usr/local/nagios/bin/nagios -v /usr/local/nagios/etc/nagios.cfg && systemctl reload nagios
```

Root causes: Too many services scheduled, check timeout too high causing worker saturation, network timeouts on NRPE checks causing worker threads to block, underpowered server.

---

**Scenario 2 — NRPE Agent Connectivity Failure**

Symptoms: Multiple hosts showing CRITICAL for NRPE-based checks; `check_nrpe: Error — Could not complete SSL handshake` or `Connection refused` in service output.

```bash
# Test NRPE connectivity directly
/usr/local/nagios/libexec/check_nrpe -H <target-host> -p 5666 -c check_load
/usr/local/nagios/libexec/check_nrpe -H <target-host> -p 5666 -t 10 -c check_disk -a /

# Common NRPE errors:
# "Connection refused" — NRPE not running or firewall blocking port 5666
# "CHECK_NRPE: Received 0 bytes" — NRPE running but command not found
# "SSL handshake failed" — SSL version mismatch or cert issue
# "Access denied" — Nagios server IP not in allowed_hosts

# Check if NRPE is running on target
ssh <target-host> "systemctl status nrpe || systemctl status nagios-nrpe-server"
ssh <target-host> "ss -tlnp | grep 5666"

# Check allowed_hosts on target
ssh <target-host> "grep allowed_hosts /etc/nagios/nrpe.cfg"

# Check SSL settings match on both sides
grep "ssl_version\|include_dirs\|dont_blame_nrpe" /etc/nagios/nrpe.cfg

# Test with no SSL (debug only)
/usr/local/nagios/libexec/check_nrpe -H <target-host> -n -c check_load

# Verify firewall on target
ssh <target-host> "iptables -L INPUT -n | grep 5666"

# Restart NRPE on target
ssh <target-host> "sudo systemctl restart nrpe"

# Check NRPE logs on target
ssh <target-host> "journalctl -u nrpe -n 50 --no-pager | grep -iE 'error|denied|refused'"
```

Root causes: Nagios server IP missing from NRPE `allowed_hosts`, NRPE not running after host reboot, SSL version mismatch (OpenSSL upgrade), firewall rule added blocking port 5666, NRPE command not defined on target.

---

**Scenario 3 — Notification Pipeline Broken (Alerts Not Firing)**

Symptoms: Services in CRITICAL state for extended time but no emails/pages sent; `NOTIFICATION` entries absent from nagios.log; on-call not paged.

```bash
# Check last notification in logs
grep "NOTIFICATION" /usr/local/nagios/var/nagios.log | tail -20

# Check notification settings for a specific service
grep -A 30 "define service" /usr/local/nagios/etc/services.cfg | grep -A 15 "SERVICE_NAME"

# Key settings that suppress notifications:
# notifications_enabled 0 (globally disabled)
# notification_interval 0 (only notify once)
# notification_period — check if current time is outside notification window
# contacts/contact_groups — may be empty or misconfigured
# first_notification_delay — may be too long

# Check if notifications are globally disabled
grep "enable_notifications" /usr/local/nagios/etc/nagios.cfg

# Check notification commands exist and are executable
ls -la /usr/local/nagios/libexec/notify* 2>/dev/null
grep "notify-by-email\|notify_host\|notify_service" /usr/local/nagios/etc/commands.cfg | head -5

# Test notification command manually
/usr/local/nagios/libexec/notify-service-by-email.sh "PROBLEM" "myservice" "CRITICAL" \
  "admin@example.com" "Test notification from agent"

# Check external command pipe is functional
echo "[$(date +%s)] DISABLE_NOTIFICATIONS" > /usr/local/nagios/var/rw/nagios.cmd 2>&1 || \
  echo "PIPE ERROR: cannot write to nagios.cmd"

# Verify contact email addresses
grep "email\|pager" /usr/local/nagios/etc/contacts.cfg | grep -v "^#"

# Check if downtime is suppressing notifications
grep "DOWNTIME" /usr/local/nagios/var/nagios.log | grep -i "started\|scheduled" | tail -10

# Re-enable notifications if disabled
echo "[$(date +%s)] ENABLE_NOTIFICATIONS" > /usr/local/nagios/var/rw/nagios.cmd
echo "[$(date +%s)] ENABLE_SVC_NOTIFICATIONS;hostname;servicename" > /usr/local/nagios/var/rw/nagios.cmd
```

Root causes: `enable_notifications=0` in nagios.cfg (set during maintenance and not reverted), notification period excluding current time (e.g., business-hours-only window), contact group empty, mail relay not reachable, Nagios downtime covering all affected services.

---

**Scenario 4 — Service Flap Detection Causing Alert Storm**

Symptoms: Services rapidly cycling between OK and CRITICAL; excessive notifications; alerts auto-resolving before investigation.

```bash
# Count currently flapping services
grep -c "is_flapping=1" /usr/local/nagios/var/status.dat

# List flapping services
python3 -c "
import re
data = open('/usr/local/nagios/var/status.dat').read()
services = re.findall(r'servicestatus \{(.*?)\}', data, re.DOTALL)
for s in services:
    if 'is_flapping=1' in s:
        host = re.search(r'host_name=(.+)', s)
        svc = re.search(r'service_description=(.+)', s)
        if host and svc:
            print(f'{host.group(1).strip()} / {svc.group(1).strip()}')
"

# Check flap detection thresholds in nagios.cfg
grep "flap_detection\|low_service_flap\|high_service_flap\|low_host_flap\|high_host_flap" /usr/local/nagios/etc/nagios.cfg

# Current defaults: low=20%, high=30% — tune per service
# Per-service override in service definition:
# flap_detection_enabled 1
# low_flap_threshold 10
# high_flap_threshold 25

# Temporarily disable flap detection for a noisy service
echo "[$(date +%s)] DISABLE_SVC_FLAP_DETECTION;hostname;servicename" > /usr/local/nagios/var/rw/nagios.cmd

# Identify root cause of flapping (intermittent dependency)
grep "hostname\|servicename" /usr/local/nagios/var/nagios.log | grep -E "OK|CRITICAL|WARNING" | tail -30

# Check for check_interval too short (causing false flaps)
grep -r "check_interval\|retry_interval" /usr/local/nagios/etc/services.cfg | sort -t= -k2 -n | head -10
```

Root causes: Check interval too short for service with inherent variability, network hiccup causing intermittent NRPE timeouts, flap thresholds set too low (20/30% defaults), dependency service unstable.

---

**Scenario 5 — Plugin Returning Non-Standard Exit Code (UNKNOWN Flood)**

Symptoms: Many services showing UNKNOWN state; `grep -c "current_state=3" status.dat` elevated; `nagiostats` shows high UNKNOWN count; no actual outages — plugins misbehaving.

```bash
# Count UNKNOWN state services
grep -c "current_state=3" /usr/local/nagios/var/status.dat

# Identify which plugins are returning UNKNOWN
python3 -c "
import re
data = open('/usr/local/nagios/var/status.dat').read()
services = re.findall(r'servicestatus \{(.*?)\}', data, re.DOTALL)
from collections import Counter
cmd_counts = Counter()
for s in services:
    if 'current_state=3' in s:
        check = re.search(r'check_command=(.+)', s)
        if check: cmd_counts[check.group(1).strip()] += 1
for cmd, count in cmd_counts.most_common(10):
    print(f'{count:5d} {cmd}')
"

# Test the misbehaving plugin manually
/usr/local/nagios/libexec/<failing_check> <args>
echo "Exit code: $?"
# Valid: 0=OK, 1=WARNING, 2=CRITICAL, 3=UNKNOWN, anything else = invalid

# Plugins returning >3 or non-numeric (e.g., signal 11 = segfault)
grep "plugin returned exit" /usr/local/nagios/var/nagios.log | tail -20
grep "check resulted in a return code of" /usr/local/nagios/var/nagios.log | \
  grep -v "return code of [0123];" | tail -20

# Check plugin version and dependencies
file /usr/local/nagios/libexec/<plugin>
ldd /usr/local/nagios/libexec/<plugin> 2>/dev/null | grep "not found"
/usr/local/nagios/libexec/<plugin> --help 2>&1 | head -3

# Perl plugin missing modules
perl -c /usr/local/nagios/libexec/<plugin>.pl 2>&1 | head -10
```

Root causes: Plugin segfault due to library mismatch after OS upgrade, missing Perl/Python module after system update, plugin script syntax error after manual edit, check command arguments malformed (wrong escaping), plugin returning exit code > 3 instead of 3.
Quick fix: Replace plugin with updated version from `nagios-plugins` package; fix missing dependency (`cpan install` or `pip install`); update plugin command definition with correct argument syntax; set explicit `check_command` timeout so timed-out checks return UNKNOWN(3) not signal.

---

**Scenario 6 — Passive Check Not Received Within Freshness Threshold**

Symptoms: Passive check services showing stale state; `is_freshness_check=1` entries in logs; services going UNKNOWN after freshness timeout; NSCA feed silent.

```bash
# Find services with freshness checking enabled
python3 -c "
import re
data = open('/usr/local/nagios/var/status.dat').read()
services = re.findall(r'servicestatus \{(.*?)\}', data, re.DOTALL)
for s in services:
    if 'check_type=1' in s:  # passive check
        host = re.search(r'host_name=(.+)', s)
        svc = re.search(r'service_description=(.+)', s)
        state = re.search(r'current_state=(\d)', s)
        last = re.search(r'last_check=(\d+)', s)
        import time
        if host and svc and last:
            age = int(time.time()) - int(last.group(1))
            print(f'{host.group(1).strip()} / {svc.group(1).strip()} last_check_age={age}s state={state.group(1) if state else \"?\"}')"

# Freshness threshold settings
grep -r "freshness_threshold\|check_freshness\|passive_checks_enabled" /usr/local/nagios/etc/ | grep -v "^#" | head -20

# Check NSCA (passive check receiver) is running
systemctl status nsca 2>/dev/null || pgrep -a nsca
# NSCA receiving log
tail -50 /usr/local/nagios/var/nsca.log 2>/dev/null || journalctl -u nsca -n 50 --no-pager

# Test sending a passive check manually
echo "hostname;service_description;0;OK - manual test" | \
  /usr/local/nagios/bin/send_nsca -H localhost -c /etc/nagios/send_nsca.cfg

# Check external command pipe
ls -la /usr/local/nagios/var/rw/nagios.cmd
# Manually submit passive check via command pipe
printf "[%lu] PROCESS_SERVICE_CHECK_RESULT;%s;%s;0;OK manual test\n" \
  $(date +%s) "hostname" "service_description" > /usr/local/nagios/var/rw/nagios.cmd
```

Root causes: Source system stopped sending passive checks (cron disabled, monitoring agent crashed), NSCA daemon not running or not accepting connections, NSCA encryption password mismatch between sender and receiver, external command pipe full or not writable, freshness_threshold too short for check interval.
Quick fix: Verify source system cron/agent is sending; restart NSCA: `systemctl restart nsca`; verify NSCA encryption keys match in both `/etc/nagios/nsca.cfg` and `send_nsca.cfg`; increase `freshness_threshold` to 2× expected check interval.

---

**Scenario 7 — Nagios Process OOM from Large Config with Many Checks**

Symptoms: Nagios process killed by OOM killer; monitoring gap of several minutes; `systemctl status nagios` shows `ExecMainStatus=137` (SIGKILL); large config with thousands of hosts/services.

```bash
# Confirm OOM kill
dmesg | grep -iE "oom.*nagios|nagios.*killed" | tail -5
journalctl -k | grep -iE "oom.*nagios|nagios.*killed" | tail -5
grep -i "oom\|killed" /var/log/kern.log 2>/dev/null | grep -i nagios | tail -5

# Count config objects (scale indicator)
/usr/local/nagios/bin/nagios -v /usr/local/nagios/etc/nagios.cfg 2>&1 | grep -E "Total|hosts:|services:|contacts:"

# Check current nagios memory usage
ps aux | grep nagios | grep -v grep | awk '{print "RSS:", $6/1024, "MB"}'
cat /proc/$(pgrep -f "nagios -d")/status | grep -E "VmRSS|VmPeak|VmSize" 2>/dev/null

# Nagios memory optimization settings
grep -E "use_large_installation_tweaks|free_child_process_memory|child_processes_fork_twice|max_debug_file_size" /usr/local/nagios/etc/nagios.cfg

# Object count per host (high object count = more RAM)
wc -l /usr/local/nagios/etc/objects/*.cfg 2>/dev/null | sort -rn | head -10

# Configure systemd memory limit (current)
systemctl show nagios | grep MemoryMax
```

Root causes: `use_large_installation_tweaks=0` (default) causing full config reloading into child processes, excessive debug logging (`debug_level > 0`), very large number of services with complex dependency chains, `nagios.log` not rotating causing large file mapped into process space.
Quick fix: Enable large installation tweaks: `echo "use_large_installation_tweaks=1" >> /usr/local/nagios/etc/nagios.cfg`; enable memory optimization: `free_child_process_memory=1`; add systemd memory limit: `systemctl edit nagios` with `[Service]\nMemoryMax=4G`; rotate large log file; split config into smaller files with fewer services per host.

---

**Scenario 8 — Check Command Timeout Causing Host/Service Marked Down**

Symptoms: Hosts/services sporadically flipping to CRITICAL then recovering; `nagiostats` shows high execution time; `grep "timed out" nagios.log` shows many entries; false alarms waking on-call.

```bash
# Count timeout entries in log
grep "timed out after" /usr/local/nagios/var/nagios.log | wc -l
# Recent timeouts with host/service context
grep "timed out after" /usr/local/nagios/var/nagios.log | tail -20

# Check global timeout settings
grep -E "service_check_timeout|host_check_timeout|event_handler_timeout|notification_timeout|ochp_timeout" /usr/local/nagios/etc/nagios.cfg

# Find checks with highest average execution time
nagiostats | grep -E "Execution Time"

# Test specific slow check manually with timing
time /usr/local/nagios/libexec/check_http -H <slow-host> -w 5 -c 10 -t 30

# Network latency to problematic hosts
ping -c5 -W2 <host-ip> | tail -3
traceroute -n <host-ip> 2>/dev/null | tail -5

# NRPE timeout specifically
grep "command_timeout" /etc/nagios/nrpe.cfg 2>/dev/null
# Test with explicit timeout
/usr/local/nagios/libexec/check_nrpe -H <host> -t 20 -c check_load
```

Root causes: Network latency spike from degraded switch/link, target host under load causing slow plugin response, check plugin making DNS lookups causing delay, `service_check_timeout` (default 60s) too short for slow checks, single-threaded check for many SNMP OIDs causing pileup.
Quick fix: Increase global timeout for specific checks: `service_check_timeout=120`; add `-t <timeout>` argument to individual check commands; identify and fix slow hosts (network path, load); use passive checks or shorter check intervals with recheck logic; enable check reaping: reduce `check_timeout` for fast checks, set longer timeout only for known-slow checks.

**Scenario 9 — Production NRPE Mutual TLS Enforcement Blocking Checks**

Symptoms: All NRPE-based checks returning `SSL handshake failed` after a security hardening change in production; staging NRPE checks pass because staging uses `ssl=false`; production enforces TLS 1.2+ with client certificate authentication and production NRPE is configured with `require_client_certs=1` and a trusted CA.

Root causes: Production NRPE upgraded to require mutual TLS (`ssl_client_certs=1`, `ssl_cacert_file` set) but the Nagios server-side `check_nrpe` invocation lacks the `-C <cert>` and `-K <key>` arguments; certificate expired; CA cert not trusted on either side; cipher suite mismatch between OpenSSL versions in staging vs prod.

```bash
# Reproduce the failure
/usr/local/nagios/libexec/check_nrpe -H <prod-target> -p 5666 -c check_load
# Expected: "CHECK_NRPE: Error - Could not complete SSL handshake"

# Inspect the TLS handshake in detail
openssl s_client -connect <prod-target>:5666 -tls1_2 \
  -cert /etc/nagios/ssl/nagios-client.crt \
  -key  /etc/nagios/ssl/nagios-client.key \
  -CAfile /etc/nagios/ssl/prod-ca.crt 2>&1 | grep -E "Verify|subject|issuer|Cipher|SSL"

# Check certificate expiry on both sides
openssl x509 -in /etc/nagios/ssl/nagios-client.crt -noout -dates
ssh <prod-target> "openssl x509 -in /etc/nrpe/ssl/nrpe-server.crt -noout -dates"

# Confirm NRPE config on target requires client certs
ssh <prod-target> "grep -E 'ssl|cert|ca|cipher' /etc/nagios/nrpe.cfg"

# Check what TLS versions and ciphers the prod NRPE expects
ssh <prod-target> "grep -E 'tls_min_version|cipher_list|ssl_client_certs' /etc/nagios/nrpe.cfg"

# Test with explicit cert and CA
/usr/local/nagios/libexec/check_nrpe -H <prod-target> -p 5666 \
  -C /etc/nagios/ssl/nagios-client.crt \
  -K /etc/nagios/ssl/nagios-client.key \
  -A /etc/nagios/ssl/prod-ca.crt \
  -c check_load

# If check now passes, update the command definition in Nagios
grep "check_nrpe" /usr/local/nagios/etc/commands.cfg | head -5
```

Fix: Update the `check_nrpe` command definition to include the client cert, key, and CA arguments:
```
define command {
    command_name  check_nrpe_ssl
    command_line  $USER1$/check_nrpe -H $HOSTADDRESS$ -p 5666 \
                    -C /etc/nagios/ssl/nagios-client.crt \
                    -K /etc/nagios/ssl/nagios-client.key \
                    -A /etc/nagios/ssl/prod-ca.crt \
                    -c $ARG1$
}
```
Renew expired certificates with your PKI tool; ensure prod CA cert is deployed to `/etc/nagios/ssl/` on the Nagios server. Reload Nagios after updating commands: `systemctl reload nagios`.

---

## Common Error Messages & Root Causes

| Error / Log Pattern | Root Cause | First Command |
|---------------------|-----------|---------------|
| `CHECK_NRPE: Error - Could not complete SSL handshake` | NRPE SSL version mismatch or cert error | Check `ssl_version` in `/etc/nrpe/nrpe.cfg` |
| `CHECK_NRPE: Received 0 bytes from daemon` | NRPE daemon not running on target host | `systemctl status nrpe` |
| `CRITICAL: xxx - Connection refused` | Monitored service not listening on port | Check service status on target host |
| `Error: Return code of 127 is out of bounds` | Check script not found on remote host | Verify script path in NRPE config |
| `Warning: Nagios process is not running` | Nagios core crashed or stopped | `systemctl start nagios` |
| `Error: Could not read object configuration file` | nagios.cfg syntax error | `nagios -v /etc/nagios/nagios.cfg` |
| `UNKNOWN: NRPE: Command 'xxx' not defined` | Check command missing from nrpe.cfg | Add command definition to `/etc/nrpe/nrpe.cfg` |
| `Cannot write to log file '/var/log/nagios/nagios.log'` | File permission issue | `chown nagios:nagios /var/log/nagios/` |

# Capabilities

1. **Core health** — Process status, check scheduling, latency management
2. **Plugin management** — Check plugin debugging, timeout tuning, custom plugins
3. **NRPE** — Remote agent connectivity, `allowed_hosts`, SSL, command configuration
4. **Notifications** — Escalation chains, contact groups, notification filtering, periods
5. **Dependencies** — Host/service dependency trees, flap detection tuning
6. **Configuration** — Config validation, object definitions, template inheritance

# Critical Metrics to Check First

1. Nagios process running (`systemctl status nagios`) — down means zero monitoring
2. Active check latency avg (`nagiostats`) — > 5s = scheduler overloaded
3. Check execution time avg — > 10s = timeout risk, worker exhaustion
4. Services in CRITICAL state count — baseline comparison
5. NRPE connection failures — remote monitoring blind spots
6. `status.dat` age — > 60s means Nagios stalled
7. Last `NOTIFICATION` log entry — absent means alerting pipeline broken
8. Flapping service count — noise suppressing real alerts

# Output

Standard diagnosis/mitigation format. Always include: `nagiostats` output,
check latency stats, NRPE connectivity results, notification pipeline status,
and recommended remediation steps.

## Cross-Service Failure Chains

When this service shows symptoms, the real root cause is often elsewhere:

| This Service Symptom | Actual Root Cause | First Check |
|----------------------|------------------|-------------|
| Check latency spike across all active checks simultaneously | DNS resolver slowdown — `check_http`, `check_ping`, and NRPE checks all resolve hostnames; a degraded resolver adds 2–5s to every check | `time nslookup <monitored-host>` from the Nagios server; check `/etc/resolv.conf` and `systemctl status systemd-resolved` |
| Notification emails not delivered despite pipeline showing healthy | Mail relay (Postfix/SMTP gateway) silently deferring — notification command exits 0 but `notify-by-email` script uses `sendmail` which queues rather than rejects | `mailq \| head -20` to see deferred queue; `postqueue -p` and check `/var/log/mail.log \| grep "deferred"` |
| NRPE checks returning UNKNOWN on one subnet of hosts after network change | New firewall rule blocking port 5666 added to a network segment — Nagios server cannot reach NRPE agents on that subnet | `traceroute -n <affected-host>` and `nmap -p 5666 <affected-subnet>/24 --open` from the Nagios server |
| `status.dat` not updating (> 60s stale) but Nagios process is running | Filesystem where `/usr/local/nagios/var/` resides is full or mounted read-only after an I/O error | `df -h /usr/local/nagios/var/` and `dmesg \| grep -iE "ext4\|xfs\|remount.*read-only"` |
| All passive checks going UNKNOWN (freshness timeout) simultaneously | NSCA daemon stopped receiving from sources because the monitoring agent (e.g. Prometheus Alertmanager → NSCA bridge) pod was evicted | `systemctl status nsca` and check the agent sending passive checks: `kubectl get pods -n monitoring \| grep nsca-bridge` |
| Nagios OOM-killed every few hours on large installation | Nagios forking many child processes for concurrent checks and inheriting full config memory footprint — `use_large_installation_tweaks=0` default causes each child to map the full config | `cat /proc/sys/vm/overcommit_memory` and `grep use_large_installation_tweaks /usr/local/nagios/etc/nagios.cfg` |

## Partial Failure Patterns

One-of-N degraded — harder to detect than full outage:

| Pattern | Detection | Impact | Isolation Command |
|---------|-----------|--------|------------------|
| 1 of N Nagios satellite/proxy instances (in distributed setup) has stopped receiving passive checks from its segment | Central Nagios shows increasing UNKNOWN count for services in one network segment; other segments normal; `nagiostats` on satellite shows `Passive Service Checks Last 1 min = 0` | Blind spot in one network zone; no alerts for that segment's services | `nagiostats` on each satellite: `ssh <satellite> "nagiostats \| grep 'Passive Service'"` and compare counts |
| 1 contact in a contact group has an invalid email address causing notification failures for all contacts in that group | Notification log shows `NOTIFICATION: admin;...;FAILED;notify-by-email;...` only for services mapped to that contact group; other contact groups notify fine | On-call for affected services not paged; silently broken for potentially weeks | `grep "FAILED" /usr/local/nagios/var/nagios.log \| grep NOTIFICATION \| awk -F';' '{print $2}' \| sort \| uniq -c \| sort -rn \| head -10` |
| 1 NRPE plugin segfaulting on a subset of target hosts (after library upgrade) — returns exit code 139 instead of 0–3 | `grep "return code of 139" /usr/local/nagios/var/nagios.log` shows only certain hosts; other hosts' NRPE checks pass; affected hosts show UNKNOWN state | Services on affected hosts show UNKNOWN instead of real state; SLA reporting inaccurate | `grep "return code of 139" /usr/local/nagios/var/nagios.log \| grep -oP "service '.*?'" \| sort \| uniq -c \| sort -rn \| head -10` then test plugin: `ssh <host> "/usr/local/nagios/libexec/<plugin>"; echo $?` |

## Performance Thresholds

Key metrics with production warning and critical thresholds:

| Metric | Warning | Critical | Check Command |
|--------|---------|----------|---------------|
| Active service checks execution latency (avg, s) | > 5 s | > 30 s | `nagiostats \| grep "Active Service Check Latency"` |
| Check execution time (avg, s) | > 10 s | > 60 s (approaching `service_check_timeout`) | `nagiostats \| grep "Active Service Check Execution Time"` |
| Services in CRITICAL state (count) | > 10 | > 50 | `nagiostats \| grep "Services Currently Critical"` |
| Passive check freshness failures (stale count) | > 5 | > 20 | `grep "CHECK RESULT FRESHNESS" /usr/local/nagios/var/nagios.log \| grep "$(date +%Y-%m-%d)" \| wc -l` |
| Notification failures per hour | > 5/hr | > 20/hr | `grep "NOTIFICATION.*FAILED" /usr/local/nagios/var/nagios.log \| grep "$(date +%H)" \| wc -l` |
| Nagios process CPU usage % | > 70% | > 90% | `ps -p $(cat /usr/local/nagios/var/nagios.lock) -o %cpu --no-headers` |
| External command queue depth (unprocessed) | > 100 | > 1,000 | `nagiostats \| grep "External Commands"` |
| Hosts in DOWN state (count) | > 5 | > 25 | `nagiostats \| grep "Hosts Currently Down"` |

## Capacity Planning Indicators

Leading indicators to act on *before* limits are breached:

| Metric | Trend to Watch | Action | Lead Time |
|--------|---------------|--------|-----------|
| Check execution latency (`nagiostats -m -d AVGACTSVCLAT`) | Average active check latency > 10 s and rising | Increase `max_concurrent_checks` in `nagios.cfg`; reduce check interval for low-priority services; scale out to a distributed Nagios setup | 1–2 weeks |
| Check result reaper queue depth (`nagios.log` `REAPER` lag) | Reaper taking > 5 s per cycle | Reduce `check_result_reap_time`; increase `max_reaper_time` in `nagios.cfg`; move check results to a RAM disk | Days |
| `status.dat` file size | File > 50 MB | Audit number of monitored hosts and services; archive old/decommissioned hosts; consider migrating to Nagios XI or Icinga2 for scalability | 2–4 weeks |
| Nagios process CPU utilization | `top -p $(pgrep nagios) -bn1 \| awk '/nagios/{print $9}'` > 80 % sustained | Reduce check frequency for non-critical services; offload passive checks via NSCA; upgrade hardware | 1–2 weeks |
| Event handler execution queue | `grep "EVENT HANDLER" /usr/local/nagios/var/nagios.log \| wc -l` growing faster than it clears | Audit event handler scripts for slow execution (add timeouts); increase `event_handler_timeout` cautiously | Days |
| Log file disk usage (`du -sh /usr/local/nagios/var/nagios.log`) | Log file > 1 GB | Configure log rotation in `nagios.cfg` (`log_rotation_method=d`); archive rotated logs to object storage | 1 week |
| Number of monitored hosts/services | Total services in `status.dat` approaching 5 000 on a single Nagios instance | Plan distributed deployment with Nagios Distributed Monitoring or migrate to Icinga2 with a proper DB backend | 1–3 months |
| Notification command failure rate | `grep "FAILED.*notify" /usr/local/nagios/var/nagios.log \| wc -l` > 5 in a day | Audit and test all notification commands; add fallback notification channels; check MTA health | 1 day |

## Diagnostic Cheatsheet

Copy-paste one-liners for rapid incident triage:

```bash
# Show overall Nagios scheduling performance (check latency and backlog)
/usr/local/nagios/bin/nagiostats | grep -E "Checks Scheduled|Average Latency|Max Latency|Active Checks"

# List all services currently in CRITICAL or UNKNOWN state
grep -E "current_state=[23]" /usr/local/nagios/var/status.dat | grep "service_description" | awk -F= '{print $2}'

# Count checks falling behind schedule (latency > 10s)
grep "check_execution_time" /usr/local/nagios/var/status.dat | awk -F= '{if ($2+0 > 10) count++} END {print count " checks over 10s"}'

# Tail live Nagios event log for state changes and notifications
tail -f /usr/local/nagios/var/nagios.log | grep -E "SERVICE ALERT|HOST ALERT|NOTIFICATION|EXTERNAL COMMAND"

# Verify Nagios process is healthy and count active check workers
ps aux | grep -E "nagios|nrpe" | grep -v grep && cat /usr/local/nagios/var/nagios.lock

# Check NRPE connectivity to a specific host
/usr/local/nagios/libexec/check_nrpe -H <target-host> -c check_load

# List hosts with flapping status enabled
grep -B5 "is_flapping=1" /usr/local/nagios/var/status.dat | grep "host_name"

# Count pending notifications in the notification queue
grep "notification_type" /usr/local/nagios/var/status.dat | grep -c "PROBLEM"

# Validate Nagios configuration before reload
/usr/local/nagios/bin/nagios -v /usr/local/nagios/etc/nagios.cfg 2>&1 | tail -10

# Show most recently checked services and their result codes
grep "last_check" /usr/local/nagios/var/status.dat | sort -t= -k2 -rn | head -20
```

## SLO Definitions

| SLO | Target | Measurement | 30-day Error Budget | Burn Rate Alert (1h window) |
|-----|--------|-------------|--------------------|-----------------------------|
| Nagios Process Availability | 99.9% | `nagios_up == 1` (nagiostats exporter) | 43.8 min | > 14.4x burn rate |
| Check Execution Latency P95 ≤ 5s | 99.5% | `nagios_active_service_latency_seconds{quantile="0.95"} < 5` | 3.6 hr | > 6x burn rate |
| Notification Delivery Success Rate | 99% | `1 - (rate(nagios_notifications_failed_total[5m]) / rate(nagios_notifications_total[5m]))` | 7.3 hr | > 3x burn rate |
| Active Check Completion Rate | 99.5% | `rate(nagios_active_service_checks_total[5m]) / nagios_active_service_checks_scheduled > 0.95` | 3.6 hr | > 6x burn rate |

## Configuration Audit Checklist

Verify before production deployment or after configuration changes:

| Check | Command | Pass Criteria |
|-------|---------|---------------|
| Config validation passes | `/usr/local/nagios/bin/nagios -v /usr/local/nagios/etc/nagios.cfg 2>&1 \| tail -5` | `Total Warnings: 0` and `Total Errors: 0` |
| Max concurrent checks set | `grep "max_concurrent_checks" /usr/local/nagios/etc/nagios.cfg` | Value matches expected worker capacity; not set to `0` (unlimited) in resource-constrained environments |
| Notification commands defined | `grep -r "define command" /usr/local/nagios/etc/objects/ \| grep notify` | At least one email or PagerDuty notification command defined and referenced by a contact |
| Passive check freshness enabled | `grep "check_result_reaper_frequency\|freshness_check_interval" /usr/local/nagios/etc/nagios.cfg` | Freshness checking enabled (`check_service_freshness=1`) |
| NSCA or NRPE configured for remote hosts | `grep -r "check_nrpe\|passive\|check_nsca" /usr/local/nagios/etc/objects/` | Remote hosts using NRPE or passive checks have valid command definitions |
| Log rotation configured | `ls -lh /etc/logrotate.d/nagios` | Log rotate config exists; logs not growing unbounded |
| External commands enabled | `grep "check_external_commands" /usr/local/nagios/etc/nagios.cfg` | `check_external_commands=1`; command file pipe exists |
| Contacts have valid email | `grep -r "email" /usr/local/nagios/etc/objects/contacts.cfg` | All active contacts have a non-placeholder email address |
| Performance data enabled | `grep "process_performance_data" /usr/local/nagios/etc/nagios.cfg` | `process_performance_data=1` if feeding Graphite/InfluxDB |
| CGI authentication enforced | `grep "use_authentication" /usr/local/nagios/etc/cgi.cfg` | `use_authentication=1`; default admin password changed |

## Log Pattern Library

Common log signatures and their meaning:

| Log Pattern | Severity | Root Cause | Immediate Action |
|-------------|----------|-----------|-----------------|
| `Error: Could not stat() command file '/usr/local/nagios/var/rw/nagios.cmd'` | Critical | External command pipe missing; `check_external_commands=1` but pipe not created | Restart Nagios; verify `command_file` path and permissions |
| `Warning: Return code of 255 for check of service` | Warning | NRPE plugin or check script exited abnormally; possible timeout or missing plugin | SSH to host; test NRPE manually: `/usr/local/nagios/libexec/check_nrpe -H <host> -c <cmd>` |
| `Error: Nagios is not running` (from external watchdog) | Critical | Nagios process died; no checks executing | `systemctl start nagios`; check `nagios.log` for crash reason |
| `Warning: Host check timed out after N seconds` | Warning | Host unreachable or ICMP blocked; check command taking too long | Verify ICMP reachability; increase `host_check_timeout` if justified |
| `Error: File '/var/spool/nagios/checkresults/...' not found` | Error | Check result spool directory missing or wrong permissions | Recreate spool dir; fix permissions to `nagios:nagios` |
| `Warning: Passive check result was received for service that has passive checks disabled` | Warning | Passive result received for a service configured as active-only | Update service definition to `passive_checks_enabled 1` or fix sending system |
| `Error: There was a problem processing the config file` | Critical | Config validation failed; Nagios did not start or reload | Run `/usr/local/nagios/bin/nagios -v /usr/local/nagios/etc/nagios.cfg` and fix errors |
| `Warning: Service check for '<host>/<svc>' took N seconds but max timeout is M` | Warning | Check plugin running too slow; timeout will fire next time | Profile plugin; increase `service_check_timeout` or optimize plugin |
| `Error: Could not open host performance data file` | Error | Performance data file path doesn't exist or wrong permissions | Create directory; fix permissions; restart Nagios |
| `Warning: Freshness threshold exceeded for service` | Warning | Passive check not received within freshness interval; service may be stale | Verify sending agent is running; check NSCA or event handler |
| `Error: NRPE: Unable to read output` | Error | NRPE daemon not running on remote host or connection refused | Start NRPE on remote host; check firewall on port 5666 |
| `Warning: Maximum concurrent service checks (N) has been reached` | Warning | Check queue full; checks being deferred | Increase `max_concurrent_checks`; add more workers or reduce check frequency |

## Error Code Quick Reference

| Error Code / State | Meaning | Service Impact | Remediation |
|-------------------|---------|----------------|-------------|
| Plugin exit code `1` (WARNING) | Check returned WARNING threshold exceeded | Service state set to WARNING; notification may fire | Investigate the reported metric; adjust thresholds if false positive |
| Plugin exit code `2` (CRITICAL) | Check returned CRITICAL threshold exceeded | Service state CRITICAL; alert sent to contacts | Investigate root cause immediately; acknowledge in UI |
| Plugin exit code `3` (UNKNOWN) | Check could not determine status (error in plugin) | Service state UNKNOWN; no reliable monitoring | Debug plugin execution manually; check plugin dependencies |
| Plugin exit code `255` / `-1` | Plugin crashed or timed out; Nagios treats as CRITICAL | Service flagged CRITICAL spuriously | Test plugin manually; check for missing libraries or timeout |
| `HARD` state after N checks | Service has failed `max_check_attempts` times in a row | Notifications sent; escalation may trigger | Confirm issue is real; fix underlying problem or adjust `max_check_attempts` |
| `SOFT` state | Service failing but not yet at `max_check_attempts` | No notification yet; re-checks in progress | Monitor; intervene if failure trend continues |
| `UNREACHABLE` host state | Host's parent is DOWN; host not directly checked | All services on this host suppress notifications | Restore parent host/network path; re-check parent |
| `FLAPPING` state | Service/host changing state too rapidly | Notifications suppressed to reduce noise | Investigate instability; adjust `high_flap_threshold` / `low_flap_threshold` |
| `DOWNTIME` state | Host or service in scheduled maintenance window | Notifications suppressed | No action needed; verify downtime window is correctly set |
| `PENDING` state | Service has not been checked yet since startup | No status data available | Wait for first check interval; verify check command is valid |
| NRPE `CHECK_NRPE: Error - Could not complete SSL handshake` | TLS mismatch between Nagios and NRPE versions | Remote check returns UNKNOWN | Align OpenSSL versions; check `ssl_version` in `nrpe.cfg` |
| `Config error: Circular object dependency detected` | Host or service dependency forms a circular chain | Nagios fails to start after config reload | Map dependencies; break circular chain in object definitions |

## Known Failure Signatures

Multi-signal patterns for precise root cause identification:

| Signature Name | Metrics | Logs | Alerts | Root Cause | Action |
|---------------|---------|------|--------|-----------|--------|
| Config Reload Failure | Nagios process age not updating after reload attempt; stale config version | `Error: There was a problem processing the config file` | NagiosConfigError alert (if self-monitored) | Syntax or logic error in a config file change | Run `-v` validation; revert bad file; reload |
| Check Queue Saturation | `active_scheduled_service_checks` at `max_concurrent_checks`; checks deferred | `Maximum concurrent service checks has been reached` | CheckLatencyHigh alert | Too many checks for available workers | Reduce check frequency; increase `max_concurrent_checks`; shard hosts |
| NRPE Blanket Failure | All remote service checks UNKNOWN simultaneously | `NRPE: Unable to read output`; `Error - Could not complete SSL handshake` | MultipleUnknown alert | NRPE daemon down on remote hosts or network/firewall change | Restart NRPE on remotes; check firewall; verify SSL version compatibility |
| Notification Blackhole | Known-CRITICAL services not sending alerts; contacts not receiving email | No notification log entries for HARD state services | Detected by absence (SLA breach) | Notification commands broken, SMTP down, or contacts misconfigured | Test notification command manually; verify SMTP relay; check contact definitions |
| Stale Passive Checks | Passive-check services entering UNKNOWN with freshness errors | `Freshness threshold exceeded for service` | FreshnessExpired alert | Sending agent (NSCA client or event handler) stopped | Restart sending agent; verify NSCA daemon; check network to Nagios server |
| Flapping Storm | Many services entering FLAPPING state at once | Repeated state-change log entries; flapping detection log | FlapDetected alerts flooding | Underlying instability (e.g., network jitter) or thresholds too tight | Widen `high_flap_threshold`; investigate underlying instability |
| Nagios OOM Crash | Process exits unexpectedly; memory usage at host limit before crash | `nagios: killed` in dmesg; no graceful shutdown log | NagiosDown alert | Too many concurrent checks or large object count exhausting RAM | Reduce concurrency; add RAM; split into multiple Nagios instances |
| Performance Data Write Failure | No new data points in Graphite/InfluxDB; PNP4Nagios graphs flat | `Could not open host performance data file` | GraphsStale alert (if monitored) | Performance data directory missing or wrong owner | Recreate dir; fix permissions; restart Nagios |
| Circular Dependency Deadlock | Nagios fails to start after adding new host/service dependency | `Circular object dependency detected` in config validation | NagiosStartFailed alert | New dependency definition creates a loop | Map dependency graph; remove the circular link |

## Application-Layer Error Patterns

What your application/client sees when this service fails:

| Client Error | SDK / Library | Root Cause in Service | How to Confirm | Mitigation |
|-------------|--------------|----------------------|---------------|-----------|
| No alert received for known DOWN service | Email, PagerDuty, Slack integration | Notification command broken; SMTP relay down; contact misconfigured | Manually test: `echo "test" \| mail -s test <contact>`; check notification log | Fix notification command; test SMTP relay; verify contact definition |
| All remote checks showing UNKNOWN | NRPE, check_by_ssh | NRPE daemon stopped on remote hosts or firewall change blocking port 5666 | `check_nrpe -H <host> -c check_load` from Nagios server | Restart NRPE on remote hosts; open port 5666; check SSL version compatibility |
| Nagios web UI unreachable (HTTP 503) | Browser / monitoring dashboards | Apache/Nginx serving Nagios CGI crashed; Nagios process down | `systemctl status nagios`; `systemctl status apache2` | Restart web server; restart Nagios; check error log |
| Passive check services stuck in UNKNOWN | NSCA, send_nsca clients | NSCA daemon not running or passive check freshness expired | `systemctl status nsca`; check `freshness_threshold` in service definition | Restart NSCA; verify sending agents are running; adjust `freshness_threshold` |
| `HTTP/1.1 403 Forbidden` on CGI access | Browser | `nagiosadmin` password changed or htpasswd file missing | `cat /etc/nagios/htpasswd.users`; check Apache config | Recreate htpasswd entry: `htpasswd /etc/nagios/htpasswd.users nagiosadmin` |
| Check result not updating (stale status) | Nagios web UI | Nagios process hung or check scheduler not advancing | `stat /var/nagios/nagios.log` — check modification time; `nagiostats` | Restart Nagios; check for zombie worker processes |
| `NRPE: Command '<name>' not defined` | NRPE | Check command not present in `nrpe.cfg` on remote host | `grep "<name>" /etc/nagios/nrpe.cfg` on remote | Add command definition to `nrpe.cfg`; reload NRPE |
| `check_http: Connection refused` | check_http plugin | Target web service down or port changed | `curl -v http://<host>:<port>` from Nagios server | Verify service is running; update port in Nagios service definition |
| Alert storm after maintenance window | Email / PagerDuty | Downtime not scheduled; services returning HARD state simultaneously | Check scheduled downtime in Nagios UI | Schedule downtime before maintenance; use `nagios_downtime` API |
| Notification delay > expected interval | Email recipients | `notification_interval` set to 0 or re-notification disabled | `grep notification_interval /etc/nagios/objects/services.cfg` | Set `notification_interval` to desired re-alert period (e.g., 60) |
| `(No output)` returned by check | Nagios UI | Check plugin produces no stdout; plugin crashes or times out | Run plugin manually on Nagios server: `/usr/lib64/nagios/plugins/check_xyz` | Fix plugin; increase `service_check_timeout`; check plugin dependencies |
| Event handler not executing | Custom scripts | Event handler disabled globally or per-service; path wrong | `grep enable_event_handlers /etc/nagios/nagios.cfg` | Enable event handlers; verify script path and permissions |

## Slow Degradation Patterns

| Pattern | Early Signal | Detection Command | Lead Time Before Incident | Action |
|---------|-------------|------------------|--------------------------|--------|
| Check latency creep | `check_latency` average rising in Nagios performance data; checks consistently behind schedule | `nagiostats 2>/dev/null \| grep -i latency` or Nagios web Tactical Overview | Days to weeks | Reduce check interval; increase `max_concurrent_checks`; shard to distributed Nagios |
| Performance data directory growing unbounded | Disk usage on `/var/nagios/` growing; performance data not being processed by PNP4Nagios/Graphite | `du -sh /var/nagios/spool/perfdata/` | Weeks | Enable performance data processing daemon; check PNP4Nagios health; set retention |
| Object cache file growing | `objects.cache` and `status.dat` parsing time increasing; web UI slow to load | `ls -lh /var/nagios/objects.cache /var/nagios/status.dat` | Months | Prune retired hosts/services from config; archive unused object definitions |
| Notification queue backlog | Email delivery delayed; MTA queue filling | `mailq \| wc -l` | Hours to days | Fix SMTP relay; rotate/purge mail queue; switch to webhook-based alerting |
| Plugin timeout accumulation | `service_check_timed_out` counter rising in Nagios log | `grep "timed out" /var/nagios/nagios.log \| wc -l` | Hours | Increase `service_check_timeout`; investigate slow network paths to monitored hosts |
| Log file growing without rotation | `/var/nagios/nagios.log` exceeding disk quota | `du -sh /var/nagios/nagios.log` | Months | Configure `log_rotation_method=d` in `nagios.cfg`; set up logrotate |
| NRPE SSL certificate nearing expiry | NRPE checks start failing with SSL errors as cert approaches expiry | `echo \| openssl s_client -connect <host>:5666 2>/dev/null \| openssl x509 -noout -dates` | Weeks | Renew NRPE SSL certificates on all monitored hosts | 
| Flap detection threshold drift | Service entering/exiting FLAPPING state; notifications suppressed without operator awareness | `grep FLAPPING /var/nagios/nagios.log \| tail -20` | Days | Investigate underlying instability; widen flap thresholds; add stability checks |

## Diagnostic Automation Scripts

### Script 1: Full Health Snapshot
```bash
#!/bin/bash
# Nagios Full Health Snapshot
NAGIOS_LOG="${NAGIOS_LOG:-/var/nagios/nagios.log}"
NAGIOS_STATUS="${NAGIOS_STATUS:-/var/nagios/status.dat}"
echo "=== Nagios Health Snapshot $(date) ==="
echo "--- Nagios Process Status ---"
systemctl status nagios 2>/dev/null | head -10 || service nagios status 2>/dev/null | head -10
echo ""
echo "--- Nagios Runtime Stats ---"
nagiostats 2>/dev/null || /usr/local/nagios/bin/nagiostats 2>/dev/null || echo "nagiostats not available; check status.dat"
echo ""
echo "--- Service States Summary (from status.dat) ---"
if [ -f "$NAGIOS_STATUS" ]; then
  echo "OK:       $(grep -c 'current_state=0' "$NAGIOS_STATUS")"
  echo "WARNING:  $(grep -c 'current_state=1' "$NAGIOS_STATUS")"
  echo "CRITICAL: $(grep -c 'current_state=2' "$NAGIOS_STATUS")"
  echo "UNKNOWN:  $(grep -c 'current_state=3' "$NAGIOS_STATUS")"
else
  echo "status.dat not found at $NAGIOS_STATUS"
fi
echo ""
echo "--- Recent Critical Events (last 20) ---"
grep "SERVICE ALERT\|HOST ALERT" "$NAGIOS_LOG" 2>/dev/null | grep "CRITICAL\|DOWN" | tail -20
echo ""
echo "--- Web Server Status ---"
systemctl status apache2 2>/dev/null | head -5 || systemctl status httpd 2>/dev/null | head -5
echo "=== Snapshot Complete ==="
```

### Script 2: Performance Triage
```bash
#!/bin/bash
# Nagios Performance Triage
NAGIOS_LOG="${NAGIOS_LOG:-/var/nagios/nagios.log}"
NAGIOS_CFG="${NAGIOS_CFG:-/etc/nagios/nagios.cfg}"
echo "=== Nagios Performance Triage $(date) ==="
echo "--- Check Execution Times (timed out checks in last hour) ---"
grep "timed out" "$NAGIOS_LOG" 2>/dev/null | grep "$(date +%Y-%m-%d)" | wc -l | xargs echo "Timed-out checks today:"
echo ""
echo "--- Concurrent Check Config ---"
grep -E "max_concurrent_checks|check_reaper_interval|service_check_timeout" "$NAGIOS_CFG" 2>/dev/null
echo ""
echo "--- NRPE Responsiveness Test (sample 3 hosts) ---"
SAMPLE_HOSTS=$(grep -h "address=" /etc/nagios/objects/*.cfg 2>/dev/null | awk -F= '{print $2}' | sort -u | head -3)
for host in $SAMPLE_HOSTS; do
  echo -n "  $host: "
  timeout 5 /usr/lib64/nagios/plugins/check_nrpe -H "$host" -c check_load 2>&1 | head -1 || echo "TIMEOUT"
done
echo ""
echo "--- Notification Log (last 10) ---"
grep "SERVICE NOTIFICATION\|HOST NOTIFICATION" "$NAGIOS_LOG" 2>/dev/null | tail -10
echo ""
echo "--- Performance Data Spool Size ---"
PERFDATA_DIR=$(grep "service_perfdata_file" "$NAGIOS_CFG" 2>/dev/null | awk -F= '{print $2}' | xargs dirname 2>/dev/null)
[ -n "$PERFDATA_DIR" ] && du -sh "$PERFDATA_DIR" 2>/dev/null || echo "Performance data dir not configured"
echo "=== Performance Triage Complete ==="
```

### Script 3: Connection / Resource Audit
```bash
#!/bin/bash
# Nagios Connection and Resource Audit
NAGIOS_CFG="${NAGIOS_CFG:-/etc/nagios/nagios.cfg}"
NAGIOS_LOG="${NAGIOS_LOG:-/var/nagios/nagios.log}"
echo "=== Nagios Connection / Resource Audit $(date) ==="
echo "--- Nagios Process Resource Usage ---"
NAGIOS_PID=$(pgrep -x nagios 2>/dev/null || pgrep -x nagios3 2>/dev/null)
if [ -n "$NAGIOS_PID" ]; then
  echo "Nagios PID: $NAGIOS_PID"
  ps -p "$NAGIOS_PID" -o pid,vsz,rss,pcpu,etime 2>/dev/null
  ls /proc/$NAGIOS_PID/fd 2>/dev/null | wc -l | xargs echo "Open FDs:"
fi
echo ""
echo "--- Config Validation ---"
nagios -v "$NAGIOS_CFG" 2>&1 | tail -10
echo ""
echo "--- Log File Sizes ---"
ls -lh /var/nagios/*.log /var/nagios/*.dat 2>/dev/null
echo ""
echo "--- NRPE Port Availability on Monitored Hosts (sample 5) ---"
grep -rh "address=" /etc/nagios/objects/ 2>/dev/null | awk -F= '{print $2}' | sort -u | head -5 | while read host; do
  result=$(nc -z -w2 "$host" 5666 2>&1 && echo "OPEN" || echo "CLOSED/FILTERED")
  echo "  $host:5666 -> $result"
done
echo ""
echo "--- Disk Usage of Nagios Directories ---"
du -sh /var/nagios/ /etc/nagios/ /usr/lib64/nagios/ 2>/dev/null
echo "=== Audit Complete ==="
```

## Noisy Neighbor & Resource Contention Patterns

| Contention Type | Symptoms | Identify the Culprit | Isolate / Mitigate | Prevent |
|----------------|---------|---------------------|-------------------|---------|
| Excessive concurrent checks saturating CPU | Nagios CPU at 100%; check latency rising; results arriving late | `nagiostats` — `Active service checks running` at `max_concurrent_checks` | Reduce `max_concurrent_checks`; spread check intervals | Set realistic `max_concurrent_checks` based on CPU; use check distribution across time |
| Performance data writer blocking check processing | Checks completing but results queued; `service_perfdata_file_processing_interval` too frequent | Check PNP4Nagios or perfdata processor CPU; disk I/O on spool dir | Reduce perfdata write frequency; move spool to fast disk | Set `service_perfdata_file_processing_interval` >= 15 s; use async perfdata processor |
| Log file writes locking scheduler thread | Nagios check scheduler stalling periodically; correlated with high log write rate | `strace -p <nagios_pid> \| grep write` during stall | Enable `log_initial_states=0`; reduce verbose logging | Use `log_rotation_method=d`; forward logs to syslog/Loki instead of local file |
| NRPE check pileup on slow network hosts | Checks timing out for one network segment; check queue depth rising for those hosts | `grep "timed out" /var/nagios/nagios.log \| grep <subnet>` | Increase `service_check_timeout` for affected hosts; use check_by_ssh fallback | Set per-host `check_timeout` overrides; use satellite Nagios for remote networks |
| Event handler script monopolizing CPU | Shell scripts consuming CPU; Nagios worker children not returning | `ps aux \| grep "event_handler\|nagios"` — look for zombie/long-running handlers | Kill long-running handlers; increase event handler timeout | Set `event_handler_timeout`; write event handlers in compiled languages or lightweight scripts |
| Notification flood consuming SMTP bandwidth | Mail server queue growing; other services' email delayed | `mailq \| head -30` — bulk of messages from `nagios@host` | Enable notification throttling; use `notification_interval` > 0; enable `flap_detection` | Use PagerDuty/Slack webhook instead of SMTP; configure `first_notification_delay` |
| Large status.dat causing slow web UI | Nagios CGI pages timing out; status.dat > 50 MB | `ls -lh /var/nagios/status.dat` | Increase CGI timeout in Apache; prune retired services | Archive inactive hosts/services; split large installations across multiple Nagios instances |
| Shared host competing for RAM with other services | Nagios OOM-killed; `dmesg` shows kill; co-located services (MySQL, etc.) also degraded | `dmesg \| grep -i oom`; `free -m` trend | Move Nagios to dedicated VM; reduce `max_concurrent_checks` | Allocate minimum 2 GB RAM for Nagios; use cgroups to isolate memory |

---

## Cascading Failure Patterns
| Trigger Event | Propagation Path | Blast Radius | Detection Signals | Stop-gap Action |
|--------------|-----------------|--------------|-------------------|-----------------|
| Nagios process crashes or is killed | All active monitoring stops; no alerts fire for any downstream failures; silent outage period | Entire monitored infrastructure goes blind — disk full, service crashes, link failures all undetected | `systemctl status nagios` shows failed; `nagios_checks_executed_total` metric flatlines; no new alert emails | `systemctl restart nagios`; send manual alert to ops team that monitoring was down; review nagios.log for crash cause |
| NRPE agent crash on monitored host | All checks using `check_nrpe` for that host return `CRITICAL: Connection refused`; host may be healthy | That host's service checks all appear critical; alert flood for one host can mask real incidents | Nagios alert emails: `check_nrpe: Error - Could not connect to <host> (Connection refused)`; `systemctl status nrpe` on remote host | `systemctl restart nrpe` on remote host; verify: `check_nrpe -H host -c check_disk` |
| Network path failure between Nagios and monitored segment | All hosts on that segment transition to UNREACHABLE state; notifications fire for all; Nagios may incorrectly mark hosts DOWN | All services on affected network segment appear critical simultaneously | `check_host_alive` returning 100% packet loss to entire subnet; `ping -c3 gateway-host` fails from Nagios server | Define parent-child relationships in Nagios config; parent going down suppresses child notifications: `define host { parents gateway-host }` |
| Disk full on Nagios server (log/retention) | Nagios cannot write status.dat or log entries; check results discarded; scheduler stalls | All monitoring state is lost; web UI shows stale data | `df -h /var/nagios` shows 100%; Nagios log: `Error writing to file`; `nagios.log` stops growing | Clear old logs: `find /var/nagios -name "*.log" -mtime +30 -delete`; truncate nagios.log; add log rotation |
| SMTP server failure (notification transport) | Alert emails silently dropped; ops team not notified of incidents | All notification-dependent escalations fail; incidents go unnoticed until manual check | Nagios log: `Error: Failed to send email to oncall@company.com`; mail queue: `mailq | wc -l` growing | Switch notification method to Slack/PagerDuty webhook; fix SMTP; verify with: `echo test | mail -s test oncall@company.com` |
| Database backend failure (if using NDOUtils/MySQL for history) | Historical performance data and event log unavailable; web UI still functional using status.dat | Trending/capacity planning tools lose data; SLA reporting broken | MySQL error in `ndo2db.log`; `systemctl status ndo2db` shows failed; `mysql nagios -e "SELECT 1"` fails | Restart ndo2db: `systemctl restart ndo2db`; repair MySQL tables if needed: `mysqlcheck -r nagios` |
| Nagios check fork bomb (misconfigured check spawning infinite children) | Nagios spawns excessive child processes; system load explodes; SSH and other services on Nagios host degraded | Nagios host becomes unresponsive; monitoring ceases; co-located services affected | `ps aux | grep check_ | wc -l` — hundreds of processes; system `uptime` load average > CPU count × 5 | Kill all check processes: `pkill -f check_`; restart Nagios; set `max_concurrent_checks` to reasonable value |
| Stale check results causing false alerting loop | Notifications fire for recovered services; then fire again for recovery; alert fatigue grows | On-call team overwhelmed with flapping alerts; real incidents buried in noise | `grep "FLAPPING" /var/nagios/nagios.log | wc -l` — high count; check execution time > `check_interval` | Enable flap detection: `enable_flap_detection=1`; tune `high_flap_threshold`; fix slow checks |
| External check command (event handler) locks up | Nagios external command pipe fills; new commands not processed; acknowledgments and downtime not applied | Operators cannot silence alerts or acknowledge incidents via UI | `ls -la /var/nagios/rw/nagios.cmd` — pipe exists but commands queue; `wc -c /var/nagios/rw/nagios.cmd` growing | Restart Nagios to flush pipe; check event handler process: `pgrep -a nagios_event_handler` | 
| PNP4Nagios / Graphite perfdata processor crash | Performance data queue fills on disk; no trending graphs available; Nagios check processing unaffected | Operations team loses trend visibility; capacity planning broken | `du -sh /var/spool/nagios/perfdata` growing rapidly; PNP4Nagios web returns 503 | Restart PNP4Nagios: `systemctl restart npcd`; clear spool if too large: `rm /var/spool/nagios/perfdata/*` |

## Change-Induced Failure Patterns
| Change Type | Failure Symptom | Time to Manifest | Correlation Method | Rollback / Fix |
|------------|----------------|-----------------|-------------------|----------------|
| Nagios version upgrade (e.g., 4.4 → 4.5) | Config directive renamed or removed; Nagios fails to start: `Error in configuration file nagios.cfg`; or plugin ABI changes break check output parsing | Immediately on `nagios -v nagios.cfg` and first start | `nagios -v /etc/nagios/nagios.cfg 2>&1 | grep -E 'Error\|Warning'`; correlate with upgrade timestamp | Revert Nagios binary; restore `nagios.cfg` from backup; check upgrade notes for renamed directives |
| Adding new check plugin without testing return codes | Plugin returns unexpected exit codes; Nagios misinterprets 2 as OK or vice versa; wrong alerting state | Immediately on first check execution | `grep "<new_plugin>" /var/nagios/nagios.log | grep -v "OK"` — unexpected states; test: `/usr/lib64/nagios/plugins/check_new 2>&1; echo $?` | Fix plugin to return correct Nagios exit codes (0=OK, 1=WARN, 2=CRIT, 3=UNKNOWN); retest |
| Host or service deletion from config without `nagios -v` pre-check | Nagios fails to reload: `Error: Could not find any host matching 'old-host'` in service definition | Immediately on `systemctl reload nagios` | `nagios -v /etc/nagios/nagios.cfg 2>&1 | grep Error`; correlate with config change commit | Fix dangling references; run `nagios -v` before every reload; use version-controlled config with pre-commit hook |
| Changing `check_interval` from 5 to 1 minute for all services | Check concurrency explodes; CPU high; `max_concurrent_checks` hit immediately; check latency rises | Within the next check cycle (1 minute after config reload) | `grep "Max concurrent service checks" /var/nagios/nagios.log`; `nagiostats` shows check queue depth | Revert check intervals; `systemctl reload nagios`; tune `max_concurrent_checks` proportionally |
| Contact group change removing on-call from notification | Incidents fire but no one receives alerts; on-call blind to outages | Immediately on next triggered alert after config reload | Compare contact list in alert email headers before/after; `grep "Notification" /var/nagios/nagios.log | grep oncall` | Restore contact group: add back on-call contact; `systemctl reload nagios`; test: `send_ack` or force check to trigger |
| NRPE config update on monitored host allowing new commands | If `dont_blame_nrpe=0` unchanged, new commands with arguments rejected: `NRPE: Command ... not defined` | Immediately when new check runs | Nagios alert: `CRITICAL: NRPE: Command 'check_new' not defined`; correlate with nrpe.cfg change | Define command in `/etc/nagios/nrpe.cfg`; `systemctl reload nrpe`; test: `check_nrpe -H host -c check_new` |
| SSL certificate update for NRPE without matching Nagios server cert | NRPE SSL handshake fails: `CHECK_NRPE: Error - Could not complete SSL handshake`; all NRPE checks fail | Immediately after cert deployment on either side | Nagios check output: `SSL handshake failed`; correlate with cert rotation event | Ensure both Nagios server and NRPE use matching CA; redeploy certs consistently; verify: `check_nrpe --ssl -H host -c check_users` |
| Flap detection threshold change to overly sensitive values | Alert storm for normally stable services crossing threshold frequently; notification flood | Within one check cycle after reload | `grep "FLAPPING START" /var/nagios/nagios.log | wc -l` spike; correlate with config reload | Set `high_flap_threshold=50` (50%) and `low_flap_threshold=25` (25%); reload; wait for flap state to clear |
| Adding many new hosts without adjusting `max_concurrent_checks` | Check scheduling backs up; latency climbs; results arriving after `check_interval`; stale states | Within minutes of first full check cycle after host addition | `nagiostats` shows `Active service checks running` at ceiling; check latency in stats | Increase `max_concurrent_checks`; or use distributed Nagios with satellite schedulers |
| Perfdata format change in check plugin output | PNP4Nagios / Graphite stops ingesting new data: `ERROR: Cannot parse perfdata`; graphs flatline | Immediately after plugin update | PNP4Nagios log: `/var/log/pnp4nagios/npcd.log` — parse errors; correlate with plugin update timestamp | Roll back plugin; fix perfdata format to `label=value[UOM];warn;crit;min;max`; validate with `process_perfdata.pl -d -p "perfdata_string"` |

## Data Consistency & Split-Brain Patterns
| Scenario | Detection Command | Symptoms | Impact | Recovery Procedure |
|---------|-----------------|---------|--------|-------------------|
| status.dat out of sync with running state | `nagiostats` shows checks running but `status.dat` mtime not updating | Web UI shows stale host/service states; operators see wrong status | Decisions based on stale UI data; missed escalations | Restart Nagios to regenerate status.dat: `systemctl restart nagios`; verify mtime updates: `watch -n1 "stat /var/nagios/status.dat"` |
| Config drift between Nagios server and object config files (manual edits) | `nagios -v /etc/nagios/nagios.cfg` reports errors or warnings not in version control | Production monitoring differs from documented/code-managed state | Ungoverned monitoring changes; regressions after next deploy | Diff running config vs git: `git diff /etc/nagios/`; reconcile; enforce config management (Puppet/Ansible/Chef) for Nagios |
| Distributed Nagios satellites sending duplicate check results | Duplicate notifications for same event; event log shows same host/service firing twice | Operations team confused by duplicate alerts; alert deduplication broken | Noise in alerting; potential for duplicate incident tickets | Verify satellite `obsessive_over_services=0` on non-central instances; ensure only one satellite checks each host |
| NDOUtils duplicate event entries in MySQL | `SELECT COUNT(*) FROM nagios_servicechecks WHERE service_object_id=X GROUP BY start_time HAVING COUNT(*) > 1` — duplicates present | Historical graphs show double data points; SLA calculation inflated | Inaccurate reporting; capacity planning skewed | Stop ndo2db; `DELETE FROM nagios_servicechecks WHERE id NOT IN (SELECT MIN(id) ...)` deduplicate; restart ndo2db |
| Routing inconsistency — same host checked by two Nagios instances | Both instances alert independently; notifications doubled; acknowledgment on one doesn't suppress other | Duplicate pages/emails for same incident | On-call fatigue; confusion about which ack is canonical | Designate single authoritative Nagios instance per host; use `host.cfg` `active_checks_enabled=0` on secondary instances |
| Time zone mismatch between Nagios server and monitored hosts | Check scheduling off by hours; maintenance windows miss actual maintenance; event correlation broken | Downtime periods don't align with actual maintenance; ops sees alerts during scheduled windows | Maintenance windows ineffective; compliance SLAs miscalculated | Set `use_timezone=UTC` in `nagios.cfg`; ensure all monitored hosts NTP-synced; verify: `date -u` on Nagios server |
| Stale object cache (`objects.cache`) after config delete | Deleted host still appears in UI and fires checks despite removal from config files | Ghost checks for decommissioned hosts; spurious alerts for non-existent services | Noise; potentially masking real issues; operator confusion | `systemctl restart nagios` — rebuilds objects.cache from current config; verify: `grep "old-host" /var/nagios/objects.cache` returns nothing |
| NRPE command output truncation at 1024 bytes causing parse failure | Check returns `NRPE: Unable to read output` or truncated perfdata; Nagios marks service UNKNOWN | Monitoring blind for that check; no alerting for real issues on that service | Silent monitoring gap | Increase NRPE `command_timeout`; use `--no-ssl` if SSL overhead truncating; fix plugin to reduce output size |
| Cert mismatch between Nagios and NSCA passive check clients | Passive check results rejected: `NSCA: error receiving data from client`; passive services stay in stale state | Passive checks never update; services appear UP forever | Remote passive monitors provide no signal; incidents missed | Re-issue NSCA shared key consistently; verify: `send_nsca -H nagios -c /etc/nagios/send_nsca.cfg <<< "host;svc;0;OK"` |
| Config include glob matching too broadly (`cfg_dir=/etc/nagios/conf.d/`) | `nagios -v` fails with `duplicate object definition`; reload rejected | Nagios reload fails silently or crashes; monitoring state frozen | Config changes cannot be applied; monitoring stagnates until fixed | Remove duplicate definitions; use explicit `cfg_file=` directives or rename conflicting files; `nagios -v` before every reload |

## Runbook Decision Trees

### Tree 1: Alert fires but check result is stale / not updating

```
Is Nagios process running?
├── NO  → systemctl start nagios
│         └── Did it start cleanly? → Check nagios.log for errors → Fix config → restart
└── YES → Is check scheduled?
          ├── NO  → grep "hostname;service" /var/nagios/objects.cache
          │         ├── NOT FOUND → service definition missing; add to config; reload
          │         └── FOUND     → force reschedule via external command:
          │                         echo "[$(date +%s)] SCHEDULE_FORCED_SVC_CHECK;host;svc;$(date +%s)" > /var/nagios/rw/nagios.cmd
          └── YES → Is check executing (check last_check in status.dat)?
                    ├── NOT EXECUTING → max_concurrent_checks exhausted?
                    │                   ├── YES → increase max_concurrent_checks; reload
                    │                   └── NO  → check if check command hangs: timeout 10 /path/to/check
                    └── EXECUTING     → Status not changing after execution?
                                        ├── Check worker result pipe: ls -la /var/nagios/rw/
                                        └── Restart Nagios to clear stuck result processor
```

### Tree 2: Notification not received for a CRITICAL service

```
Did the check reach CRITICAL state?
├── NO  → Check current state: grep "SERVICE ALERT.*CRITICAL" /var/nagios/nagios.log
│         └── State is SOFT CRITICAL? → max_check_attempts not yet reached → wait or lower max_check_attempts
└── YES → Is notification enabled for this service?
          ├── grep "notifications_enabled" /etc/nagios/objects/service.cfg
          ├── DISABLED → Enable: echo "[$(date +%s)] ENABLE_SVC_NOTIFICATIONS;host;svc" > /var/nagios/rw/nagios.cmd
          └── ENABLED  → Is contact group correctly assigned?
                          ├── NO  → Fix contact_groups in service definition; nagios -v; reload
                          └── YES → Is notification command working?
                                    ├── Test SMTP: echo "test" | mail -s "nagios-test" oncall@company.com
                                    ├── SMTP failing → check /var/log/mail.log; fix MTA; or switch to webhook
                                    └── SMTP OK → Is service in scheduled downtime?
                                                   ├── YES → Downtime is suppressing; check: grep "DOWNTIME" /var/nagios/nagios.log
                                                   └── NO  → Check notification_interval; first notification may be delayed
```

## Cost & Quota Runaway Patterns

| Pattern | Trigger | Detection Command | Blast Radius | Immediate Mitigation | Prevention |
|---------|---------|-------------------|--------------|---------------------|------------|
| Check plugin spawning subprocess for every execution | System process count rising; load average climbing proportionally to check count | `ps aux | grep check_ | wc -l` — compare to expected check count | CPU saturation; Nagios host unresponsive | Kill runaway check processes: `pkill -f check_`; set `max_concurrent_checks=50`; restart Nagios | Use compiled plugins instead of shell scripts; set `check_timeout` in `nagios.cfg` |
| NDOUtils/MySQL growing without historical data pruning | MySQL `nagios` database disk usage climbing daily | `mysql nagios -e "SELECT table_name, ROUND(data_length/1024/1024,1) AS MB FROM information_schema.tables WHERE table_schema='nagios' ORDER BY data_length DESC LIMIT 10"` | MySQL disk exhaustion | Purge old data: `DELETE FROM nagios_servicechecks WHERE start_time < UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 30 DAY)) LIMIT 50000` | Configure NDO data retention (NDO `max_timedevents_age`, `max_logentries_age`); automate monthly cleanup |
| PNP4Nagios RRD files consuming disk for all services × hosts | `/var/lib/pnp4nagios/perfdata/` growing unbounded | `du -sh /var/lib/pnp4nagios/perfdata/` total; `find /var/lib/pnp4nagios -name "*.rrd" | wc -l` file count | Disk exhaustion | Remove RRD files for decommissioned hosts: `find /var/lib/pnp4nagios/perfdata/old-host -delete` | Decommission RRD files when hosts are removed from Nagios; use `pnp4nagios_cleanup.sh` |
| Nagios log file growing without rotation configured | `/var/nagios/nagios.log` size growing continuously; disk alert fires | `ls -lh /var/nagios/nagios.log` | Disk exhaustion on Nagios server | Enable log rotation in `nagios.cfg`: `log_rotation_method=d`; or configure logrotate | Set `log_rotation_method=d` and `log_archive_path=/var/nagios/archives/` in nagios.cfg from day one |
| NRPE check command output piped to disk on each execution | Nagios server /tmp filling with check output files from misconfigured plugin | `find /tmp -name "nagios_*" -mmin -10 | wc -l` | Disk exhaustion → check failures | Remove temp files: `find /tmp -name "nagios_*" -delete`; fix plugin to use stdout only | Code review all custom plugins; never write check output to files |
| Excessive notification emails from flapping services | Email gateway rate-limited; mail queue depth explodes | `mailq | wc -l` — queue growing; `grep "NOTIFICATION" /var/nagios/nagios.log | wc -l` spike | Email gateway blocked; real alerts delayed | Enable flap detection globally: `enable_flap_detection=1`; set `notification_interval=60`; silence noisy hosts | Always enable flap detection in production; set minimum `notification_interval=30` |
| Distributed Nagios with too many satellite pollers sending results to central | Central Nagios result processing queue backing up; check latency rising | `ls /var/nagios/rw/nagios.cmd | wc -c` pipe data accumulating; `nagiostats | grep "Passive"` | Check result processing delays | Reduce passive check submission rate from satellites; batch results | Architect satellites to aggregate results before forwarding; limit to 1 satellite per network segment |
| Config validation running on every commit via CI with large config tree | CI runner CPU/time costs high from frequent full `nagios -v` runs | CI pipeline duration logs — `nagios -v` step > 30 s | CI cost runaway | Validate only changed files using `nagios -v` with targeted cfg_file; parallelize CI | Cache object config files in CI; only re-validate when host/service definitions change |

## Latency & Performance Degradation Patterns

| Pattern | Symptom | Detection Command | Root Cause | Mitigation |
|---------|---------|-------------------|------------|------------|
| Hot host group / check concentration | Nagios check latency high; many checks for same host group piling up; check freshness warnings | `nagiostats -m | grep -E "Active.*Latency"` — look for high average/max check latency | Too many checks scheduled at same interval for same host group; scheduler overloaded | Stagger check intervals with `check_interval` jitter; reduce check frequency for non-critical services |
| External command pipe backlog | Passive checks or acknowledgements delayed; `nagios.cmd` file writes slow | `ls -la /var/nagios/rw/nagios.cmd`; `wc -l /var/nagios/rw/nagios.cmd`; `nagiostats -m | grep "Ext Cmds"` | Command pipe buffer full; Nagios processing loop behind | Restart Nagios: `systemctl restart nagios`; tune `external_command_buffer_slots` in `nagios.cfg` |
| Worker process pool saturation | Check execution delayed by minutes; `check_latency` metric in `nagiostats` climbing | `nagiostats -m | grep "Active Checks"` — compare scheduled vs executed; check worker count | Insufficient Nagios worker processes for check volume; NRPE or NSCA overwhelmed | Tune `max_concurrent_checks` in `nagios.cfg`; deploy distributed monitoring with Nagios Core + Livestatus |
| NRPE connection timeout | Remote checks return `CHECK_NRPE: Error - Could not complete SSL handshake` or timeout | `check_nrpe -H <target> -c check_disk` — measure response time; `/usr/lib/nagios/plugins/check_nrpe -H <host>` | NRPE daemon overloaded; network latency; NRPE `connection_timeout` too short | Increase `connection_timeout` in `/etc/nagios/nrpe.cfg`; tune NRPE `max_connections`; check NRPE logs: `/var/log/nagios/nrpe.log` |
| Slow check plugin execution | Check plugin (e.g., `check_http`, `check_snmp`) taking > 30s; timeout kills cause false alerts | `time /usr/lib/nagios/plugins/check_http -H <host>`; `grep "plugin_output.*TIMEOUT" /var/nagios/nagios.log` | Remote service slow to respond; DNS resolution in plugin adds latency; SNMP community timeout | Set appropriate `check_timeout` in `nagios.cfg`; use `--timeout` flag in plugin command definition; cache DNS |
| CPU steal on Nagios host | Check scheduling loop delayed; `nagiostats` shows high latency even with few checks | `top -b -n1 | grep Cpu` — check `st`; `vmstat 1 10` | Cloud VM hypervisor stealing CPU; Nagios process-intensive check execution competes with other VMs | Move Nagios to dedicated instance; reduce check parallelism; enable Nagios Core caching |
| Database lock contention (NDO) | NDO database inserts slow; Nagios history queries time out; MySQL shows long-running NDO inserts | `SHOW PROCESSLIST` on NDO MySQL — look for slow `ndo*` table inserts; `SHOW ENGINE INNODB STATUS\G` | NDO schema not indexed; MySQL under heavy write load from many Nagios events | Add index on `nagios_statehistory(start_time)`; tune NDO `max_timedevents_age` to reduce write volume |
| Serialization overhead for large notification backlog | Email/notification queue backed up; alert emails arrive minutes after event | `ls -la /var/spool/nagios/`; `postqueue -p | wc -l` | Large notification burst (many hosts down simultaneously); notification command execution slow | Implement notification flap detection; stagger `notification_interval`; use async notification via EventBroker |
| Batch check interval misconfiguration | All checks fire simultaneously causing CPU/network burst every N minutes | `nagiostats -m | grep "Scheduled"` — look for clustering; watch `sar -u 1 10` for periodic spikes | All services set to same `check_interval` (e.g., `check_interval 5`) without spread | Distribute checks with varied intervals; enable `auto_reschedule_checks=1` and `auto_rescheduling_interval` in `nagios.cfg` |
| Downstream NDO MySQL replication lag | Nagios history queries return stale results; NDO writes intermittently fail | `SHOW SLAVE STATUS\G` — `Seconds_Behind_Master`; `grep "NDO.*error" /var/nagios/nagios.log` | NDO MySQL replica lagging behind primary; NDO writes directed to replica | Point NDO `db_host` to MySQL primary; enable `slave_parallel_workers` on replica to reduce lag |

## Network & TLS Failure Patterns

| Failure Type | Detection Signal | Root Cause | Impact | Remediation |
|--------------|-----------------|------------|--------|-------------|
| NRPE SSL cert expiry | Remote checks return `CHECK_NRPE: Error - Could not complete SSL handshake`; all NRPE checks critical | `check_nrpe -H <target> -c check_disk 2>&1`; `echo | openssl s_client -connect <host>:5666 2>/dev/null | openssl x509 -noout -dates` | NRPE SSL cert expired on remote agent host | Regenerate NRPE cert: `openssl req -new -x509 -days 365 -nodes -out /etc/nagios/nrpe.pem -keyout /etc/nagios/nrpe.key`; restart NRPE |
| Nagios Web UI TLS cert expiry | Admin cannot access `/nagios/`; browser shows cert expired; OpsGenie/PagerDuty webhook ingest may fail | `echo | openssl s_client -connect nagios-host:443 2>/dev/null | openssl x509 -noout -dates` | Apache/nginx serving Nagios Web UI with expired TLS cert | Renew Let's Encrypt cert: `certbot renew`; reload Apache: `systemctl reload apache2` |
| DNS resolution failure for monitored hosts | Host checks return `UNKNOWN: Unable to connect`; `check_dns` alerts for monitored DNS entries | `/usr/lib/nagios/plugins/check_dns -H <target-hostname>` — returns CRITICAL; `dig <hostname>` from Nagios host | DNS entry removed or stale after infrastructure change | Update DNS; update Nagios host definition `address` field to IP as fallback; `systemctl reload nagios` after config change |
| TCP connection exhaustion on Nagios host | Nagios check connections refused; NRPE connections fail; `ss -s` shows high TIME_WAIT | `ss -s`; `netstat -an | grep TIME_WAIT | wc -l`; `nagiostats -m | grep "Parallel Checks"` | Nagios spawning too many short-lived check processes; ephemeral ports exhausted | Reduce `max_concurrent_checks`; enable `net.ipv4.tcp_tw_reuse=1`; implement connection keep-alive in NRPE |
| Eventbroker / MK Livestatus socket unavailable | Nagios Thruk/Grafana dashboards show no data; Livestatus queries fail | `ls -la /var/nagios/rw/live`; `echo "GET status\n\n" | nc -U /var/nagios/rw/live` | Eventbroker socket file deleted or permissions changed; Nagios restarted without recreating socket | Restart Nagios to recreate Livestatus socket; check `event_broker_options` in `nagios.cfg`; fix socket permissions |
| Packet loss causing SNMP check failures | SNMP-based checks (check_snmp) intermittently return UNKNOWN; no consistent pattern | `ping -c 100 <snmp-target>` — check packet loss %; `/usr/lib/nagios/plugins/check_snmp -H <host> -o sysUpTime.0 -C public` | Network packet loss between Nagios and SNMP targets; UDP packet drop more likely than TCP | Add `-t 10` timeout flag to check_snmp command; switch to TCP SNMP transport; add retries: `-r 3` |
| MTU mismatch causing large check_http payload truncation | check_http returns partial HTML; content checks fail for large pages | `ping -M do -s 8972 <target>` — if ICMP "frag needed"; `check_http -H <host> -u / -s "expected string" -v` | MTU mismatch causing HTTP response fragmentation; Nagios check plugin receives truncated body | Align MTU on Nagios host NIC; check PMTUD path to target; use `check_http --pagesize` to detect truncation |
| Firewall rule change blocking Nagios passive check port | NSCA passive check submissions rejected; passive service checks go stale | `telnet nagios-host 5667` from NSCA sender; `check_nsca -H nagios-host 2>&1` | Firewall update blocking NSCA port 5667 | Restore firewall: `iptables -I INPUT -p tcp --dport 5667 -s <nsca-sender-subnet> -j ACCEPT` |
| SSL handshake timeout for NSCA encrypted passive checks | Passive check submissions very slow; NSCA client logs show handshake timeout | `time send_nsca -H nagios-host -c /etc/nagios/send_nsca.cfg`; check NSCA encryption setting in `nsca.cfg` | NSCA using DES/3DES encryption with weak entropy; handshake slow under load | Switch NSCA encryption to AES: `decryption_method=8` in `nsca.cfg`; install `haveged` on Nagios host |
| Connection reset during bulk passive check ingest | NSCA bulk submission fails mid-stream; partial check results ingested; some hosts remain stale | `grep "connection reset\|broken pipe" /var/log/nagios/nsca.log` | NSCA connection timeout shorter than bulk submission duration; large batch size | Reduce NSCA batch size to < 100 checks per submission; increase `read_timeout` in `nsca.cfg` |

## Resource Exhaustion Patterns

| Resource | Exhaustion Signal | Detection Command | Recovery Steps | Prevention |
|----------|------------------|-------------------|----------------|------------|
| OOM kill (Nagios process) | Nagios process killed; all monitoring stops; `dmesg` shows OOM; `systemd` shows service failed | `dmesg -T | grep -i "oom\|nagios"`; `journalctl -u nagios --since "1h ago" | grep -i killed` | `systemctl restart nagios`; verify check scheduling resumed: `nagiostats -m | grep "Checks Scheduled"` | Limit `max_concurrent_checks` to reduce per-check process memory; set cgroup memory limit; monitor Nagios RSS |
| Disk full on data partition (status/archive logs) | `nagios.log` rotation fails; `status.dat` write fails; Nagios cannot record check results | `df -h /var/nagios`; `du -sh /var/nagios/archives/` | `find /var/nagios/archives -mtime +30 -delete`; `truncate -s 0 /var/nagios/nagios.log` (with caution) | Configure logrotate for `/var/nagios/nagios.log`; set `log_archive_path` to separate volume; purge archives > 90 days |
| Disk full on log partition | Nagios error log stops; Apache/NRPE logs fill `/var/log`; process may fail to start | `df -h /var/log`; `du -sh /var/log/nagios/` | `logrotate -f /etc/logrotate.d/nagios`; `journalctl --vacuum-size=1G` | Configure logrotate for all Nagios-related logs; forward to remote syslog; alert at 80% disk |
| File descriptor exhaustion | `Nagios: execve of '/usr/lib/nagios/plugins/...' failed`; check spawning fails | `cat /proc/$(pgrep nagios)/limits | grep "open files"`; `lsof -p $(pgrep nagios) | wc -l` | Restart Nagios after setting `LimitNOFILE=65536` in systemd override | Set `LimitNOFILE=65536` in `/etc/systemd/system/nagios.service.d/override.conf`; reload: `systemctl daemon-reload` |
| Inode exhaustion (archive directory) | Nagios cannot create new archive log files; daily log rotation fails silently | `df -i /var/nagios`; `find /var/nagios/archives -type f | wc -l` | Delete old archive files: `find /var/nagios/archives -name "nagios-*.log" -mtime +90 -delete` | Mount `/var/nagios/archives` on XFS volume with large inode count; automate archive purge via cron |
| CPU steal / throttle | Check latency climbs; `nagiostats` shows checks running behind schedule; false positives from timeout | `top -b -n1 | grep Cpu` — check `st`; `vmstat 1 10`; `nagiostats -m | grep "Checks Late"` | Migrate to dedicated instance; reduce `max_concurrent_checks`; stagger check intervals | Use fixed-performance VM; monitor `node_cpu_seconds_total{mode="steal"}`; alert if check latency > 30s |
| Swap exhaustion | Nagios process paging heavily; check execution extremely slow; system unresponsive | `free -h`; `vmstat 1 5 | awk '{print $7,$8}'` — check `si`/`so` | Add swap: `fallocate -l 8G /swapfile && mkswap /swapfile && swapon /swapfile`; restart Nagios | Size RAM for Nagios process + all concurrent check processes; set `vm.swappiness=10`; avoid running NDO MySQL on same host |
| Kernel PID/thread limit | Nagios cannot fork new check worker processes; `Resource temporarily unavailable` in nagios.log | `cat /proc/sys/kernel/threads-max`; `ps -eLf | wc -l` | `sysctl -w kernel.threads-max=128000`; reduce `max_concurrent_checks`; restart Nagios | Set `kernel.pid_max=4194304`; cap `max_concurrent_checks` to stay within process limits; use Nagios XI worker daemons |
| Network socket buffer exhaustion | SNMP checks losing UDP datagrams; NRPE check results incomplete | `ss -m`; `netstat -s | grep -i "receive buffer\|overrun"` | `sysctl -w net.core.rmem_max=134217728`; increase UDP receive buffer | Pre-tune sysctl for monitoring server: `net.core.rmem_default=16777216`; tune SNMP socket buffer |
| Ephemeral port exhaustion | Nagios check plugins cannot open new outbound connections; `cannot assign requested address` | `ss -s | grep TIME-WAIT`; `cat /proc/sys/net/ipv4/ip_local_port_range` | `sysctl -w net.ipv4.ip_local_port_range="1024 65535"`; `sysctl -w net.ipv4.tcp_tw_reuse=1` | Reduce `max_concurrent_checks`; enable connection reuse in NRPE; stagger check intervals to reduce simultaneous connections |

## Distributed Transaction & Event Ordering Failures

| Failure Pattern | Detection | Service-Specific Commands | Impact | Recovery |
|----------------|-----------|--------------------------|--------|----------|
| Idempotency violation causing duplicate alert notifications | Same host/service alert notification sent multiple times to PagerDuty/email; `notification_count` resets unexpectedly | `grep "HOST NOTIFICATION\|SERVICE NOTIFICATION" /var/nagios/nagios.log | grep "<host>" | tail -20` — look for repeated entries | Oncall engineer receives duplicate pages; alert fatigue | Implement deduplication in notification handler (PagerDuty dedup key = `host+service+state`); set `notification_interval=60` to suppress repeats |
| Passive check staleness after NSCA delivery failure | Passive-only service stays in last-known state; freshness threshold triggers UNKNOWN despite service being healthy | `grep "Freshness threshold.*exceeded" /var/nagios/nagios.log`; `check_nsca -H nagios-host 2>&1` | False UNKNOWN/CRITICAL alerts for passive-checked services; oncall alerted unnecessarily | Investigate NSCA delivery failure; manually submit check result: `echo "<host>;<svc>;0;OK" | send_nsca -H nagios-host`; fix NSCA connectivity |
| Config reload race condition during check execution | Nagios `HUP` reload during active check execution; in-flight check results lost; brief gap in state history | `grep "Caught SIGHUP\|processing results" /var/nagios/nagios.log`; `nagiostats -m | grep "Checks Run"` | Check results from window around reload are discarded; monitoring gap | Schedule reloads during low-activity windows; use `nagios -v /etc/nagios/nagios.cfg` pre-validation; prefer rolling restarts |
| Event handler ordering failure | Event handler (auto-remediation script) fires before acknowledgement is processed; remediation and human intervention conflict | `grep "EVENT HANDLER" /var/nagios/nagios.log` — check timing relative to `ACKNOWLEDGEMENT`; `nagiostats -m | grep "Ext Cmds"` | Both auto-remediation and human action taken simultaneously; duplicate remediation side-effects | Add acknowledgement check in event handler script; use `NAGIOS_SERVICEACKCOMMENT` env var; implement mutex in remediation script |
| Out-of-order passive check processing | NSCA passive checks arrive out of order (network delay); older check result processed after newer; state rolls back | `grep "Passive check.*<service>" /var/nagios/nagios.log | tail -20` — compare timestamps vs check_time in results | Service state temporarily incorrect; false alert or missed recovery notification | Add check_time timestamp validation in NSCA handler; reject passive results older than last known result time |
| At-least-once event broker notification duplicate | MK Livestatus or NDO event broker sends event twice after Nagios restart; downstream CMDB/ticketing gets duplicate updates | `grep "PROCESS_SERVICE_CHECK_RESULT" /var/nagios/nagios.log | grep "<host>" | head -10` — duplicate entries; check ticketing system for duplicate tickets | Duplicate tickets opened in ServiceNow/Jira; engineer works duplicate | Implement dedup in downstream webhook handler; use `last_check` timestamp as idempotency key; NDO: enable `handle_processes_for` dedup |
| Compensating state change failure after flap detection | Host flapping; Nagios suppresses notifications correctly; flapping ends but state change notification never fires because compensating logic missed transition | `grep "FLAPPING" /var/nagios/nagios.log | tail -20`; check `flap_detection_enabled` and `low_flap_threshold` | Host recovered but oncall not notified; recovery invisible; silent failure | Manually verify state: `/usr/lib/nagios/plugins/check_<plugin> -H <host>`; force notification: `echo "[$(date +%s)] SEND_CUSTOM_HOST_NOTIFICATION;<host>;1;nagiosadmin;Manual notification after flap" > /var/nagios/rw/nagios.cmd` |
| Distributed check fan-out ordering (distributed Nagios) | Parent Nagios receiving NSCA results from child nodes processes child results out of order; parent shows stale state | `grep "PASSIVE HOST CHECK\|PASSIVE SERVICE CHECK" /var/nagios/nagios.log | grep "<host>" | tail -30` — verify timestamp ordering | Monitoring state on parent lags behind actual state by minutes | Ensure child Nagios nodes NTP-sync with < 1s offset; `ntpq -p` on all nodes; use `obsess_over_services` with consistent timing |

## Multi-tenancy & Noisy Neighbor Patterns

| Pattern | Detection Signal | Affected Tenant Impact | Isolation Command | Remediation |
|---------|-----------------|----------------------|-------------------|-------------|
| CPU noisy neighbor from expensive check plugin | `nagiostats -m | grep "Active.*Latency"` — high check latency; `top` shows one check plugin (`check_http`, `check_snmp`) consuming CPU | Other host group's checks pile up; check latency for all tenants increases; false `Freshness threshold exceeded` alerts | `kill $(pgrep -f "check_snmp -H <noisy-host>")` — terminate stuck check | Increase `check_timeout` only for noisy plugin; use `nagios_user` resource limits via cgroups; offload SNMP checks to separate Nagios instance |
| Memory pressure from large passive check backlog | `ls -la /var/spool/nagios/`; `nagiostats -m | grep "Ext Cmds"` — command buffer near `external_command_buffer_slots` limit | Passive checks for all host groups delayed; freshness checks trigger false UNKNOWN alerts | `echo "[$(date +%s)] DISABLE_PASSIVE_SVC_CHECKS;<host>;<svc>" > /var/nagios/rw/nagios.cmd` — disable noisy passive check source | Increase `external_command_buffer_slots` in `nagios.cfg`; separate passive check-heavy host groups to dedicated Nagios instance |
| Disk I/O saturation from log archiving | `iostat -x 1 5` — `/var/nagios` partition near 100% ioutil during log rotation; `du -sh /var/nagios/archives/` growing | Active check execution delayed; `status.dat` writes slow; check scheduling falls behind | `ionice -c 3 -p $(pgrep nagios)` — reduce Nagios I/O priority temporarily | Move `/var/nagios/archives/` to separate volume; configure `log_rotation_method=d` with shorter retention; archive to remote NFS/S3 |
| Network bandwidth monopoly from bulk check_http | `iftop` — Nagios generating large HTTP check traffic to one slow host group; saturating uplink | Other host checks delayed; NRPE checks timeout due to network congestion | Reduce check frequency for bandwidth-heavy host group: `check_interval 10` instead of `1` | Stagger check intervals by host group; use `check_http --timeout 3` to fail fast; offload large-payload HTTP checks to separate Nagios satellite |
| Connection pool starvation for NRPE | `nagiostats -m | grep "Parallel Checks"` near `max_concurrent_checks`; NRPE connections piling up for one host group | NRPE checks for other host groups queued; check latency high | Temporarily suspend checks for noisy host group: `echo "[$(date +%s)] DISABLE_HOSTGROUP_SVC_CHECKS;<hostgroup>" > /var/nagios/rw/nagios.cmd` | Increase `max_concurrent_checks` carefully; deploy distributed Nagios with host group affinity; use NRPE connection pool |
| Quota enforcement gap (host count) | `grep -c "^define host" /etc/nagios/conf.d/*.cfg` — host count far above baseline; config reload time > 60s | Nagios reload after config change takes minutes; check scheduling restart delayed for all host groups | Validate config without reloading: `nagios -v /etc/nagios/nagios.cfg` — check object count | Enforce maximum host count per config directory; split large host groups to satellite Nagios instances; monitor config object count in CI |
| Cross-tenant config leak risk | `ls -la /etc/nagios/conf.d/` — config files readable by all Nagios admins; one team's host definitions visible to another | One team can see another team's host IPs, service names, and check command parameters | `chmod 640 /etc/nagios/conf.d/<team-A-hosts>.cfg`; change group ownership to team-specific Linux group | Implement per-team config directories with group-level file permissions; use Nagios XI user/group separation; consider separate Nagios instances per team |
| Rate limit bypass for passive check injection | `wc -l /var/nagios/rw/nagios.cmd` — command pipe growing rapidly; Nagios processing loop falling behind | Nagios external command queue fills; active check scheduling delayed; performance degrades for all host groups | `cat /dev/null > /var/nagios/rw/nagios.cmd` — drain pipe with caution | Add NSCA rate limiting at network level (`iptables -m limit`); set `max_external_command_data_length` in `nagios.cfg` |

## Observability Gap & Monitoring Failure Patterns

| Gap Type | Symptom | Why It's Blind | Detection Workaround | Fix |
|----------|---------|----------------|---------------------|-----|
| Metric scrape failure | Grafana Nagios dashboards show "No data"; `nagios_up` metric absent from Prometheus | `nagios_exporter` or `nagiostats` sidecar crashed; Nagios status socket unavailable | `nagiostats -m | grep "Running"` directly on host; `systemctl status nagios-exporter` | Restart exporter: `systemctl restart nagios-exporter`; verify `status_file=/var/nagios/status.dat` in exporter config; alert on `nagios_up == 0` |
| Trace sampling gap missing brief check failures | Intermittent check failures (1 of 3 consecutive) never trigger alert due to `max_check_attempts=3`; silently retried | By design, Nagios soft states do not alert; brief failures invisible in dashboards | `grep "SOFT.*WARNING\|SOFT.*CRITICAL" /var/nagios/nagios.log | grep "<service>" | tail -20` — look for soft state pattern | Add `check_flapping` on critical services; reduce `max_check_attempts=1` for critical services; alert on soft states in Elasticsearch |
| Log pipeline silent drop | Nagios event history not in Elasticsearch; Kibana timeline gaps | Filebeat configured for `/var/nagios/nagios.log` but not `/var/nagios/archives/nagios-*.log` rotated files | `ls /var/nagios/archives/` — count files; compare to Kibana document count for same date | Configure Filebeat glob: `path: ['/var/nagios/nagios.log', '/var/nagios/archives/nagios-*.log']`; add `scan_frequency: 30s` |
| Alert rule misconfiguration | PagerDuty never receives Nagios host-down alert | Nagios `contacts_nagiosadmin` contact points to `nagiosadmin` but email address is wrong or SMTP relay broken | `grep "email\|smtp" /etc/nagios/objects/contacts.cfg`; `postqueue -p | head -10` — check mail queue | Test notification: `echo "[$(date +%s)] SEND_CUSTOM_HOST_NOTIFICATION;<host>;4;nagiosadmin;Test" > /var/nagios/rw/nagios.cmd`; fix SMTP relay |
| Cardinality explosion blinding dashboards | Nagios Prometheus dashboards slow; Prometheus memory high | Exporter emitting per-service metrics with `hostname` + `service_description` labels across thousands of hosts | `curl http://localhost:9126/metrics | awk '{print $1}' | cut -d'{' -f1 | sort | uniq -c | sort -rn | head` | Limit exporter to host-level metrics; use recording rules for service-level aggregates; filter high-cardinality labels in Prometheus `metric_relabel_configs` |
| Missing health endpoint | Load balancer sending monitoring traffic to Nagios node that is reloading config | LB health check only tests Apache TCP port 80, not Nagios process liveness | `nagios -v /etc/nagios/nagios.cfg`; `systemctl is-active nagios` from LB health check script | Implement LB health check script: `nagiostats -m | grep "Running: 1"` returns OK; configure LB probe interval 10s |
| Instrumentation gap in critical path | Notification delivery failures (SMTP down) not tracked; alert sent but never received | Nagios only logs notification attempt, not delivery success; SMTP errors not surfaced in `nagios.log` | `grep "SERVICE NOTIFICATION\|HOST NOTIFICATION" /var/nagios/nagios.log | tail -20`; check Postfix: `postqueue -p` | Add SMTP delivery monitoring: check_smtp plugin against relay host; alert on mail queue depth > 10 |
| Alertmanager / PagerDuty outage | Nagios detects host down; sends email via SMTP; SMTP relay is also down; no alert received | Single notification channel (email only); no redundant delivery path | `postqueue -p | wc -l` — queue depth; `/usr/lib/nagios/plugins/check_smtp -H <smtp-relay>` | Implement multiple notification methods: email + PagerDuty webhook + Slack; configure PagerDuty as secondary contact; test monthly |

## Upgrade & Migration Failure Patterns

| Change Type | Failure Symptom | Detection Command | Rollback Procedure | Prevention |
|-------------|----------------|-------------------|-------------------|------------|
| Minor version upgrade rollback (e.g., nagios4.4.13 → 4.4.14) | New version introduces CGI behavior change; web UI shows blank pages or broken dashboard | `nagios --version`; `grep "error\|warn" /var/log/apache2/error.log | tail -20`; `systemctl status nagios` | `apt install nagios4=4.4.13`; `systemctl restart nagios apache2` | Test minor upgrades on staging Nagios instance with production config copy; review Nagios changelog; smoke test CGI after upgrade |
| Major version upgrade rollback (e.g., Nagios 3 → 4) | Nagios 4 config syntax differs from 3; existing object configs fail to parse | `nagios -v /etc/nagios/nagios.cfg 2>&1 | grep "Error"` | `apt install nagios3`; restore old config files from backup: `cp -r /backup/etc/nagios/ /etc/nagios/`; restart | Validate config compatibility: `nagios4 -v /etc/nagios/nagios.cfg` before cutover; back up full `/etc/nagios/` and `/var/nagios/` directories |
| Schema migration partial completion (NDO MySQL) | NDO database missing new tables after partial upgrade; Nagios NDO broker module errors | `SHOW TABLES IN nagios LIKE 'nagios_%'` — compare count to expected; `grep "NDO\|database" /var/nagios/nagios.log | tail -20` | Re-run NDO schema SQL: `mysql nagios < /usr/share/ndoutils/db/mysql.sql`; restart Nagios | Backup NDO database before upgrade: `mysqldump nagios > nagios-pre-upgrade.sql`; run schema migration on staging first |
| Rolling upgrade version skew (Nagios + NRPE) | Upgraded NRPE agents on hosts incompatible with older Nagios server's `check_nrpe` plugin | `check_nrpe -H <host> -c check_load 2>&1` — `CHECK_NRPE: Error receiving data`; version mismatch in error | Revert NRPE on affected hosts: `apt install nagios-nrpe-server=<old-version>`; restart: `systemctl restart nagios-nrpe-server` | Upgrade `check_nrpe` plugin on Nagios server before upgrading NRPE agents; test compatibility matrix in staging |
| Zero-downtime migration to new Nagios server | Config synced to new server but `status.dat` not pre-populated; brief period where all hosts show UNKNOWN | `nagios -v /etc/nagios/nagios.cfg` on new server; `curl http://new-nagios/nagios/` — check for UNKNOWN hosts | Fail back to old server: update DNS/LB to point back to original Nagios host | Pre-warm new server by running in passive mode first; sync `status.dat` from old server before cutover; use Nagios distributed architecture |
| Config format change breaking old include | New Nagios version deprecates `cfg_dir` syntax; `nagios.cfg` with old format causes silent config skip | `nagios -v /etc/nagios/nagios.cfg 2>&1 | grep "Warning\|Error"` | Restore old config: `cp /backup/nagios.cfg /etc/nagios/nagios.cfg`; restart Nagios | Validate config after each upgrade: `nagios -v /etc/nagios/nagios.cfg`; maintain config in git; diff output against pre-upgrade version |
| Data format incompatibility (status.dat) | `status.dat` written by new Nagios version unreadable by old version after rollback; Nagios fails to start | `nagios --verify-config 2>&1 | head -20`; `grep "Error" /var/log/syslog | grep nagios | tail -10` | Delete stale `status.dat`: `rm /var/nagios/status.dat`; restart Nagios (will rebuild from scratch) | Backup `status.dat` before upgrade: `cp /var/nagios/status.dat /backup/status-pre-upgrade.dat`; accept brief UNKNOWN state on rollback |
| Feature flag rollout causing regression | Enabling `process_performance_data=1` triggers performance data handler flood; disk fills rapidly | `df -h /var/nagios`; `ls -lh /var/nagios/perfdata/ | wc -l` — large file count | Disable: `process_performance_data=0` in `nagios.cfg`; reload: `systemctl reload nagios`; clear perfdata: `rm -rf /var/nagios/perfdata/*` | Test `process_performance_data` change in staging; monitor disk usage after enabling; configure perfdata handler with throttle and cleanup cron |
| Dependency version conflict | Nagios upgrade pulls new `libpng`/`libgd` version; Nagios status map CGI crashes | `nagios.cgi 2>&1 | grep "error while loading shared libraries"`; `ldd /usr/lib/cgi-bin/nagios4/status.cgi | grep "not found"` | Pin package: `apt-mark hold nagios4`; reinstall compatible library: `apt install libgd3=<version>` | Test full package dependency resolution on staging OS before upgrading production; use Docker image for Nagios to avoid host library conflicts |

## Kernel/OS & Host-Level Failure Patterns

| Failure | Symptom | Service-Specific Detection | Root Cause | Remediation |
|---------|---------|---------------------------|------------|-------------|
| OOM killer targets Nagios daemon | Nagios process killed; all host/service checks stop; notification gap until restart | `dmesg -T \| grep -i "oom.*nagios"`; `journalctl -u nagios --since "1 hour ago" \| grep "killed\|signal"` | Large `status.dat` file held in memory + check result processing buffer exceeds cgroup limit during check burst | Reduce `status_update_interval` from 10 to 30; limit concurrent checks: `max_concurrent_checks=200`; increase host memory or cgroup limit |
| Inode exhaustion on Nagios spool directory | Check results not processed; `check_result_path` full; plugins run but results lost | `df -i /var/nagios/spool/checkresults`; `find /var/nagios/spool/checkresults -type f \| wc -l` | Thousands of check result files accumulate when Nagios event broker stalls; old result files not reaped | Clear stale results: `find /var/nagios/spool/checkresults -mmin +30 -delete`; increase inode count on spool partition; reduce `check_result_reaper_frequency` |
| CPU steal degrades check scheduling | Check latency spikes; `nagiostats` shows high service check latency; checks run behind schedule | `mpstat 1 5 \| grep steal`; `nagiostats -m -d AVGACTSVCLAT` — average service check latency | Noisy neighbor on shared VM stealing CPU cycles; Nagios scheduler cannot keep up with check interval demands | Migrate to dedicated instance; reduce check frequency for low-priority services; increase `check_worker_threads` in Nagios 4.x; use mod_gearman for distributed checks |
| NTP skew breaks notification timing | Notifications sent with wrong timestamps; `date` commands in notification scripts return future/past times; downtime windows misfire | `chronyc tracking \| grep "System time"`; `date -d @$(stat -c %Y /var/nagios/nagios.log) "+%Y-%m-%d %H:%M"` — compare file timestamp to wall clock | Clock drift >5s causes scheduled downtime windows to start/end at wrong times; notification timestamps confuse operators | Ensure `chrony` synced: `timedatectl set-ntp true`; add NTP check: `/usr/lib/nagios/plugins/check_ntp_time -H pool.ntp.org -w 0.5 -c 1.0`; restart Nagios after clock fix |
| File descriptor exhaustion | Nagios cannot fork check plugins; `check_nrpe` connections fail; `Resource temporarily unavailable` in log | `cat /proc/$(pidof nagios)/limits \| grep "open files"`; `ls /proc/$(pidof nagios)/fd \| wc -l`; `grep "Resource temporarily unavailable" /var/nagios/nagios.log` | Each active check forks a child process consuming 3+ FDs; thousands of concurrent checks exhaust ulimit | Increase in systemd unit: `LimitNOFILE=65536`; reduce `max_concurrent_checks`; use mod_gearman to distribute checks across workers |
| TCP conntrack saturation on Nagios host | NRPE checks to remote hosts fail with `Connection refused`; `check_nrpe` returns `CRITICAL - Socket timeout` | `cat /proc/sys/net/netfilter/nf_conntrack_count`; `dmesg \| grep conntrack`; `grep "Socket timeout" /var/nagios/nagios.log \| tail -10` | Thousands of check_nrpe connections per minute (one per host per check) exhaust conntrack table | Increase `nf_conntrack_max=262144`; reduce NRPE check frequency; use NSCA passive checks instead of active NRPE for bulk hosts |
| NUMA imbalance on Nagios server | Check scheduling becomes uneven; some checks consistently late; `nagiostats` shows intermittent latency spikes | `numactl --hardware`; `numastat -p $(pidof nagios)` | Nagios process memory allocated on remote NUMA node; check result processing requires cross-node memory access | Start Nagios with `numactl --interleave=all nagios -d /etc/nagios/nagios.cfg`; or pin to single NUMA node; use `taskset` for CPU affinity |
| Kernel semaphore exhaustion blocks check plugins | Check plugins hang; `Cannot create semaphore` errors; NRPE connections queue up | `ipcs -s \| wc -l`; `cat /proc/sys/kernel/sem`; `grep "semaphore\|Cannot create" /var/nagios/nagios.log` | Each check_nrpe call uses SysV semaphore; default kernel limit (128 semaphore sets) exhausted with many concurrent checks | Increase: `sysctl -w kernel.sem="250 32000 100 128"`; persist in `/etc/sysctl.d/99-nagios.conf`; reduce concurrent checks or use mod_gearman |

## Deployment Pipeline & GitOps Failure Patterns

| Failure | Symptom | Service-Specific Detection | Root Cause | Remediation |
|---------|---------|---------------------------|------------|-------------|
| Nagios config image pull fails during container rebuild | Nagios container stuck in `ImagePullBackOff`; monitoring gap during rebuild | `kubectl describe pod nagios-0 \| grep "Failed to pull"`; `docker pull nagios/nagios:latest 2>&1 \| grep "rate limit"` | Docker Hub rate limit hit pulling custom Nagios image with baked-in config and plugins | Use private registry; build and push Nagios image in CI pipeline; pin image by digest; cache base image locally |
| Config drift between Git and live Nagios | Live Nagios has 50 more host definitions than Git repo; `nagios -v` passes on live but fails on Git version | `nagios -v /etc/nagios/nagios.cfg 2>&1 \| tail -5`; `diff <(ls /etc/nagios/objects/hosts/) <(ls git-repo/nagios/objects/hosts/)` | Operators added hosts via web UI (Nagios XI) or direct file edit without committing to Git | Enforce config-as-code: make `/etc/nagios/objects/` read-only; deploy config only via CI/CD pipeline; add pre-deploy `nagios -v` validation |
| ArgoCD sync stuck on Nagios ConfigMap update | ArgoCD shows `OutOfSync`; Nagios running with old check commands; new hosts not monitored | `argocd app get nagios --output json \| jq '.status.sync.status'`; `kubectl get cm nagios-config -n monitoring -o yaml \| md5sum` | ConfigMap updated but Nagios pod not restarted; Nagios reads config at startup only, not dynamically | Add ConfigMap hash annotation to Deployment: `checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . \| sha256sum }}`; trigger pod restart on ConfigMap change |
| PDB blocks Nagios pod restart for config reload | Cannot restart Nagios pod to apply new monitoring config; PDB prevents eviction; stale checks running | `kubectl get pdb -n monitoring`; `kubectl describe pdb nagios-pdb \| grep "Allowed disruptions"` | PDB `minAvailable: 1` with single-replica Nagios; zero disruptions allowed | Remove PDB for singleton Nagios; accept brief monitoring gap during restart; or use `nagios -v && systemctl reload nagios` for config reload without restart |
| Blue-green cutover fails for Nagios server | New Nagios instance starts but all hosts show PENDING; notification gap during initial check cycle | `curl http://new-nagios/nagios/cgi-bin/statusjson.cgi?query=hostlist \| jq '.data.hostlist \| to_entries[] \| select(.value.status=="PENDING")'` | New Nagios has no `status.dat` history; needs full check cycle (5-15 min) before all hosts have state | Pre-warm new Nagios: copy `status.dat` from old instance before cutover; or run new instance in passive mode first; use NSCA to feed initial state |
| ConfigMap drift — notification commands silently broken | Nagios detects DOWN host but notification email not sent; `notification_command` path changed | `grep "notification_command" /etc/nagios/nagios.cfg`; `nagios -v /etc/nagios/nagios.cfg 2>&1 \| grep "Warning.*notification"` | Emergency edit to ConfigMap changed notification script path; change not tested or committed to Git | Add post-deploy smoke test: send test notification via `nagios -v` + `echo "test" \| /usr/bin/printf "$(cat /etc/nagios/objects/commands/notify-host-by-email.cfg)"`; maintain all configs in Git |
| Secret rotation breaks NRPE SSL communication | All NRPE checks return `CHECK_NRPE: Error - Could not complete SSL handshake`; hosts show UNKNOWN | `check_nrpe -H <host> -n 2>&1 \| grep "SSL handshake"`; `openssl s_client -connect <host>:5666 2>&1 \| grep "certificate verify"` | NRPE SSL certificate rotated on agents but Nagios server still has old CA cert; mutual TLS handshake fails | Distribute new CA cert to Nagios server: update `/etc/nagios/ssl/`; restart Nagios; coordinate cert rotation: deploy CA first, then leaf certs |
| Plugin update breaks check compatibility | After `yum update nagios-plugins`, `check_disk` output format changed; Nagios cannot parse performance data | `check_disk -w 20% -c 10% / 2>&1`; `grep "check_disk.*UNKNOWN\|parse" /var/nagios/nagios.log` | Plugin major version changed output format; Nagios `check_command` parsing regex fails on new format | Pin plugin package version: `yum versionlock nagios-plugins-disk-2.3.3`; test plugin updates in staging with production `command_name` definitions |

## Service Mesh & API Gateway Edge Cases

| Failure | Symptom | Service-Specific Detection | Root Cause | Remediation |
|---------|---------|---------------------------|------------|-------------|
| Istio sidecar intercepts NRPE checks | `check_nrpe` connections proxied through Envoy; SSL handshake fails; all NRPE checks return CRITICAL | `istioctl proxy-config listener nagios-0 -n monitoring \| grep 5666`; `check_nrpe -H <host> -c check_load 2>&1 \| grep "SSL\|connect"` | Envoy intercepts outbound port 5666 (NRPE); attempts HTTP/2 upgrade on NRPE binary protocol | Exclude NRPE port from mesh: `traffic.sidecar.istio.io/excludeOutboundPorts: "5666"` annotation on Nagios pod; or use `PeerAuthentication` PERMISSIVE for NRPE |
| Envoy rate limiter blocks Nagios check bursts | Nagios check scheduling runs burst of 500 checks at interval boundary; mesh returns 429 for some hosts | `kubectl logs nagios-0 -c istio-proxy -n monitoring \| grep "429\|rate_limit"`; `nagiostats -m -d NUMHSTACTCHK1M` — active checks in last minute | Global rate limit applies to all Nagios outbound connections; check burst exceeds per-second limit | Exempt Nagios from mesh rate limiting; or stagger checks: set `check_interval_jitter=30` in `nagios.cfg`; reduce burst by using `max_concurrent_checks=100` |
| Stale endpoints after monitored service pod restart | Nagios check still hitting old pod IP; check returns CRITICAL but service is healthy on new IP | `check_nrpe -H <old-ip> 2>&1`; `kubectl get endpoints <service> -n <ns>`; `grep "HOST ALERT.*CRITICAL" /var/nagios/nagios.log \| tail -5` | Nagios host definition uses static IP; Kubernetes pod IP changed after restart; Nagios config not updated | Use DNS names instead of IPs in Nagios host definitions: `address = service.namespace.svc.cluster.local`; or use Nagios auto-discovery integration with Kubernetes API |
| mTLS rotation breaks Nagios HTTPS checks | `check_http` with `--ssl` returns `SSL handshake failed`; HTTPS-monitored services show CRITICAL | `check_http -H <host> -S -p 443 2>&1 \| grep "SSL"`; `openssl s_client -connect <host>:443 2>&1 \| grep "verify return"` | Istio mTLS rotation invalidates CA trusted by Nagios `check_http`; Nagios uses system CA store which lacks mesh-internal CA | Update Nagios CA bundle: copy Istio root CA to `/etc/nagios/ssl/mesh-ca.pem`; use `check_http --ssl -J /etc/nagios/ssl/mesh-ca.pem`; automate CA sync with cert-manager |
| Retry storm from check_nrpe timeout retries | NRPE target host under load; check_nrpe times out; Nagios retry logic + mesh retry amplifies load on target | `nagiostats -m -d NUMSVCRETCHK1M` — service check retries last minute; `kubectl logs nagios-0 -c istio-proxy \| grep "retry"` | Nagios `max_check_attempts=3` combined with Envoy 3x retry = 9 total attempts per check; overwhelms struggling target host | Disable mesh retries for NRPE traffic; or reduce Nagios `max_check_attempts=2`; increase `check_nrpe -t 30` timeout to avoid premature retry |
| gRPC keepalive conflicts with NRPE protocol | Envoy sends HTTP/2 PING frames on NRPE connection; NRPE daemon interprets as malformed data; check crashes | `grep "NRPE.*error\|malformed" /var/nagios/nagios.log`; `kubectl logs nagios-0 -c istio-proxy \| grep "5666.*reset"` | Envoy misidentifies NRPE TCP stream as HTTP/2; sends keepalive probes that corrupt NRPE binary protocol | Explicitly mark NRPE port as TCP: `appProtocol: tcp` in Service definition; or exclude from mesh entirely with `traffic.sidecar.istio.io/excludeOutboundPorts: "5666"` |
| Trace context injection into check plugin HTTP calls | `check_http` includes extra headers from Envoy; some monitored services reject requests with unexpected `traceparent` header | `check_http -H <host> -p 80 -v 2>&1 \| grep "traceparent"`; monitored service logs showing `400 Bad Request: unexpected header` | Envoy injects W3C trace headers into HTTP check requests; strict-parsing monitored services reject the header | Disable tracing for Nagios outbound: `proxy.istio.io/config: '{"tracing":{"sampling":0}}'` annotation; or configure monitored services to ignore unknown headers |
| API gateway blocks Nagios CGI access for operators | Operators cannot access Nagios web UI through gateway; `403 Forbidden` or `401 Unauthorized` | `curl -v http://nagios-gateway/nagios/ 2>&1 \| grep "403\|401"`; `kubectl logs -n gateway -l app=api-gateway \| grep "nagios\|forbidden"` | API gateway auth policy requires JWT token; Nagios CGI uses basic auth; gateway rejects non-JWT requests | Create gateway route for Nagios with basic auth passthrough; or use `kubectl port-forward svc/nagios 8080:80 -n monitoring` for direct access; add Nagios behind OAuth2 proxy |
