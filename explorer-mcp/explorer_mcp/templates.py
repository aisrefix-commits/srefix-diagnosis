"""Pre-canned exploration templates per symptom category and tech.

Each template is a list of steps, each step is a dict telling the LLM:
  - which downstream MCP to call (mcp)
  - which tool on that MCP (tool)
  - pre-filled args (args) — may contain {placeholders} substituted at runtime
  - rationale — what this step is checking and why

The planner combines:
  1. tech-specific overrides (TECH_TEMPLATES[tech][category])
  2. then generic fallbacks (GENERIC_TEMPLATES[category])
deduping by (mcp, tool, args).
"""
from __future__ import annotations


# Generic templates — work for any tech via {tech} / {cluster_id} / {host} substitution.
# Most rely on Prometheus naming conventions: {tech}_<metric> or job="{tech}".
GENERIC_TEMPLATES: dict[str, list[dict]] = {
    "latency": [
        {"rationale": "Look at currently-firing alerts for this tech",
         "mcp": "srefix-prom", "tool": "alerts", "args": {}},
        {"rationale": "Service-level p99 latency over the last 30m (if exposes histogram)",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "histogram_quantile(0.99, sum by (le) (rate({tech}_request_duration_seconds_bucket{{job=~'{tech}.*'}}[5m])))",
                  "start": "-30m", "end": "now", "step": "30s"}},
        {"rationale": "Saturated CPU on hosts running this tech",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "100 - rate(node_cpu_seconds_total{{mode='idle',instance=~'{host_pattern}'}}[5m]) * 100",
                  "start": "-30m", "end": "now", "step": "1m"}},
        {"rationale": "Slow / high-latency log lines",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)slow|timeout|deadline"',
                  "start": "-30m", "end": "now", "limit": 200}},
    ],
    "errors": [
        {"rationale": "Currently firing alerts",
         "mcp": "srefix-prom", "tool": "alerts", "args": {}},
        {"rationale": "Error-rate change over 30m vs 1h baseline",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "rate({tech}_errors_total{{job=~'{tech}.*'}}[5m])",
                  "start": "-30m", "end": "now", "step": "30s"}},
        {"rationale": "Recent ERROR/FATAL log lines",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)error|fatal|exception|panic"',
                  "start": "-30m", "end": "now", "limit": 300}},
        {"rationale": "Container restart counts (if running on K8s)",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "rate(kube_pod_container_status_restarts_total{{namespace=~'.*{tech}.*'}}[15m])",
                  "start": "-1h", "end": "now"}},
    ],
    "down": [
        {"rationale": "Up status of all targets for this tech",
         "mcp": "srefix-prom", "tool": "instant",
         "args": {"query": "up{{job=~'{tech}.*'}}"}},
        {"rationale": "Scrape failure last 1h",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "1 - up{{job=~'{tech}.*'}}",
                  "start": "-1h", "end": "now", "step": "30s"}},
        {"rationale": "List unhealthy scrape targets",
         "mcp": "srefix-prom", "tool": "targets", "args": {"state": "active"}},
        {"rationale": "Recent connection refused / timeout in logs",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)connection refused|connection reset|no route|unreachable"',
                  "start": "-30m", "end": "now", "limit": 200}},
    ],
    "memory": [
        {"rationale": "Memory usage trend",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "process_resident_memory_bytes{{job=~'{tech}.*'}}",
                  "start": "-1h", "end": "now"}},
        {"rationale": "JVM heap (for JVM-based techs like HBase / Kafka / ES)",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "jvm_memory_used_bytes{{job=~'{tech}.*',area='heap'}}",
                  "start": "-1h", "end": "now"}},
        {"rationale": "OOM kills on the hosts",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "increase(node_vmstat_oom_kill{{instance=~'{host_pattern}'}}[1h])",
                  "start": "-1h", "end": "now"}},
        {"rationale": "Recent OOM-related log lines",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)oom|out of memory|allocation|gc.+pause"',
                  "start": "-1h", "end": "now"}},
    ],
    "cpu": [
        {"rationale": "Per-host CPU utilization",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "100 - rate(node_cpu_seconds_total{{mode='idle',instance=~'{host_pattern}'}}[5m]) * 100",
                  "start": "-1h", "end": "now"}},
        {"rationale": "Process CPU usage",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "rate(process_cpu_seconds_total{{job=~'{tech}.*'}}[5m])",
                  "start": "-1h", "end": "now"}},
        {"rationale": "Load average",
         "mcp": "srefix-prom", "tool": "instant",
         "args": {"query": "node_load5{{instance=~'{host_pattern}'}}"}},
    ],
    "disk": [
        {"rationale": "Disk space remaining",
         "mcp": "srefix-prom", "tool": "instant",
         "args": {"query": "100 - node_filesystem_avail_bytes{{instance=~'{host_pattern}'}} / node_filesystem_size_bytes * 100"}},
        {"rationale": "Disk IO util",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "rate(node_disk_io_time_seconds_total{{instance=~'{host_pattern}'}}[5m]) * 100",
                  "start": "-1h", "end": "now"}},
        {"rationale": "Inode usage",
         "mcp": "srefix-prom", "tool": "instant",
         "args": {"query": "100 - node_filesystem_files_free{{instance=~'{host_pattern}'}} / node_filesystem_files * 100"}},
    ],
    "replication": [
        {"rationale": "Generic replication-lag-style metric (works for many techs)",
         "mcp": "srefix-prom", "tool": "instant",
         "args": {"query": "{{__name__=~'.*replication_lag.*|.*replica_lag.*',job=~'{tech}.*'}}"}},
        {"rationale": "Recent replication-related log entries",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)replic|sync|standby|wal|binlog"',
                  "start": "-30m", "end": "now"}},
    ],
    "hot_query": [
        {"rationale": "Generic top-N slow query heuristic",
         "mcp": "srefix-prom", "tool": "instant",
         "args": {"query": "topk(10, {{__name__=~'.*slow.*queries.*|.*query_duration.*',job=~'{tech}.*'}})"}},
        {"rationale": "Slow-query log entries",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)slow query|long.running|duration"',
                  "start": "-30m", "end": "now"}},
    ],
    "config_change": [
        {"rationale": "Recently rediscovered cluster topology (compare to memory of last run)",
         "mcp": "srefix-discovery", "tool": "discover_now", "args": {"tech": "{tech}"}},
        {"rationale": "Container start times (recent rollouts)",
         "mcp": "srefix-prom", "tool": "instant",
         "args": {"query": "time() - process_start_time_seconds{{job=~'{tech}.*'}} < 1800"}},
        {"rationale": "K8s recent deployment status",
         "mcp": "srefix-prom", "tool": "instant",
         "args": {"query": "kube_deployment_status_observed_generation{{namespace=~'.*{tech}.*'}}"}},
    ],
    "network": [
        {"rationale": "TCP retransmissions",
         "mcp": "srefix-prom", "tool": "range_query",
         "args": {"query": "rate(node_netstat_Tcp_RetransSegs{{instance=~'{host_pattern}'}}[5m])",
                  "start": "-30m", "end": "now"}},
        {"rationale": "DNS / cert / connection log patterns",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)dns|certificate|tls|x509|connection refused|reset by peer"',
                  "start": "-30m", "end": "now"}},
    ],
    "saturation": [
        {"rationale": "Connection pool / queue depth",
         "mcp": "srefix-prom", "tool": "instant",
         "args": {"query": "{{__name__=~'.*pool.*|.*queue.*|.*backlog.*',job=~'{tech}.*'}}"}},
        {"rationale": "Rate-limit / throttle errors in logs",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)rate.?limit|throttl|429|backpress|too many"',
                  "start": "-30m", "end": "now"}},
    ],
    "data_consistency": [
        {"rationale": "Checksum / corruption keywords in logs",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)corrupt|checksum|inconsist|diverg|mismatch"',
                  "start": "-1h", "end": "now"}},
    ],
    "security": [
        {"rationale": "Auth-related logs",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)unauthor|forbidden|denied|invalid token|401|403"',
                  "start": "-30m", "end": "now"}},
    ],
    "unknown": [
        {"rationale": "Currently firing alerts (broad triage)",
         "mcp": "srefix-prom", "tool": "alerts", "args": {}},
        {"rationale": "Are scrape targets healthy?",
         "mcp": "srefix-prom", "tool": "targets", "args": {"state": "active"}},
        {"rationale": "Any tech-related ERROR logs",
         "mcp": "srefix-loki", "tool": "query_range",
         "args": {"query": '{{app="{tech}"}} |~ "(?i)error|fail"',
                  "start": "-30m", "end": "now", "limit": 200}},
        {"rationale": "Cluster topology — recent membership changes",
         "mcp": "srefix-discovery", "tool": "discover_now", "args": {"tech": "{tech}"}},
    ],
}


# Tech-specific overrides — checked BEFORE generic for the matched category.
# Keys: tech name → category → list of steps.
TECH_TEMPLATES: dict[str, dict[str, list[dict]]] = {
    "postgres": {
        "latency": [
            {"rationale": "Top slow queries from pg_stat_statements",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "topk(10, pg_stat_statements_mean_time_seconds)"}},
            {"rationale": "Lock waits",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "pg_locks_count{{mode!~'.*Share.*'}}",
                      "start": "-30m", "end": "now"}},
            {"rationale": "Connection pool saturation",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "pg_stat_database_numbackends / pg_settings_max_connections"}},
        ],
        "replication": [
            {"rationale": "PostgreSQL replication lag in seconds",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "pg_replication_lag_seconds{{cluster=~'{cluster_id}.*'}}",
                      "start": "-30m", "end": "now"}},
            {"rationale": "Replication slot WAL retained bytes",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "pg_replication_slot_wal_keep_bytes"}},
            {"rationale": "WAL archive failures",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "rate(pg_stat_archiver_failed_count[5m])",
                      "start": "-1h", "end": "now"}},
        ],
        "disk": [
            {"rationale": "WAL directory size",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "pg_wal_segments_total * pg_wal_segment_size_bytes"}},
            {"rationale": "Database size",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "pg_database_size_bytes"}},
        ],
    },
    "redis": {
        "memory": [
            {"rationale": "Redis memory usage vs maxmemory",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "redis_memory_used_bytes / redis_memory_max_bytes * 100"}},
            {"rationale": "Eviction rate",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "rate(redis_evicted_keys_total[5m])",
                      "start": "-30m", "end": "now"}},
        ],
        "latency": [
            {"rationale": "Redis slowlog count",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "redis_slowlog_length",
                      "start": "-30m", "end": "now"}},
            {"rationale": "Command rate (look for sudden drops)",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "rate(redis_commands_processed_total[5m])",
                      "start": "-30m", "end": "now"}},
        ],
        "replication": [
            {"rationale": "Redis replication offset lag (master vs replica)",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "redis_master_repl_offset - on(instance) redis_slave_repl_offset"}},
        ],
    },
    "kafka": {
        "saturation": [
            {"rationale": "Under-replicated partitions",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "kafka_topic_partition_under_replicated_partitions"}},
            {"rationale": "Consumer group lag (top 10)",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "topk(10, sum by (consumergroup, topic) (kafka_consumergroup_lag))"}},
        ],
        "latency": [
            {"rationale": "Producer / consumer request latency",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "kafka_network_request_total_time_ms{{quantile='0.99'}}",
                      "start": "-30m", "end": "now"}},
        ],
        "disk": [
            {"rationale": "Log size per broker",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "kafka_log_size{{cluster=~'{cluster_id}.*'}}"}},
        ],
    },
    "hbase": {
        "memory": [
            {"rationale": "RegionServer JVM heap",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "jvm_memory_used_bytes{{job='hbase-rs',area='heap'}}",
                      "start": "-1h", "end": "now"}},
            {"rationale": "GC pause times",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "rate(jvm_gc_collection_seconds_sum{{job='hbase-rs'}}[5m])",
                      "start": "-1h", "end": "now"}},
        ],
        "latency": [
            {"rationale": "Region read/write latency",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "hbase_regionserver_read_request_latency_99",
                      "start": "-30m", "end": "now"}},
            {"rationale": "Compaction queue depth",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "hbase_regionserver_compaction_queue_size"}},
        ],
        "down": [
            {"rationale": "HBase live regionservers (from ZK ground truth)",
             "mcp": "srefix-discovery", "tool": "discover_now", "args": {"tech": "hbase"}},
            {"rationale": "Master / RS up status",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "up{{job=~'hbase.*'}}"}},
        ],
    },
    "elasticsearch": {
        "down": [
            {"rationale": "Cluster health (green/yellow/red)",
             "mcp": "srefix-es", "tool": "cluster_health", "args": {}},
            {"rationale": "Node count",
             "mcp": "srefix-es", "tool": "nodes_info", "args": {}},
        ],
        "latency": [
            {"rationale": "Search / index latency",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "elasticsearch_indices_search_query_time_seconds / elasticsearch_indices_search_query_total",
                      "start": "-30m", "end": "now"}},
        ],
        "disk": [
            {"rationale": "ES filesystem free %",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "elasticsearch_filesystem_data_free_bytes / elasticsearch_filesystem_data_size_bytes * 100"}},
        ],
    },
    "kubernetes": {
        "errors": [
            {"rationale": "Pod restart rate",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "rate(kube_pod_container_status_restarts_total[15m])",
                      "start": "-1h", "end": "now"}},
            {"rationale": "Failed pods",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "kube_pod_status_phase{{phase='Failed'}} > 0"}},
        ],
        "saturation": [
            {"rationale": "Node memory pressure",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "kube_node_status_condition{{condition='MemoryPressure',status='true'}}"}},
            {"rationale": "Node disk pressure",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "kube_node_status_condition{{condition='DiskPressure',status='true'}}"}},
        ],
    },
    # Alias for k8s
    "k8s": None,  # filled below
    "mysql": {
        "replication": [
            {"rationale": "MySQL replication lag (Seconds_Behind_Master)",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "mysql_slave_status_seconds_behind_master",
                      "start": "-30m", "end": "now"}},
        ],
        "latency": [
            {"rationale": "Slow query rate",
             "mcp": "srefix-prom", "tool": "range_query",
             "args": {"query": "rate(mysql_global_status_slow_queries[5m])",
                      "start": "-30m", "end": "now"}},
        ],
    },
    "mongo": {
        "replication": [
            {"rationale": "Mongo replica set member states + lag",
             "mcp": "srefix-prom", "tool": "instant",
             "args": {"query": "mongodb_rs_members_optimeDate{{state='SECONDARY'}} - on(set) mongodb_rs_members_optimeDate{{state='PRIMARY'}}"}},
        ],
    },
}

# Aliases
TECH_TEMPLATES["k8s"] = TECH_TEMPLATES["kubernetes"]
TECH_TEMPLATES["postgresql"] = TECH_TEMPLATES["postgres"]
TECH_TEMPLATES["pg"] = TECH_TEMPLATES["postgres"]
TECH_TEMPLATES["es"] = TECH_TEMPLATES["elasticsearch"]
TECH_TEMPLATES["mongodb"] = TECH_TEMPLATES["mongo"]
