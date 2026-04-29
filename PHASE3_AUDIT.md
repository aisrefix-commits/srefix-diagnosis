# Phase 3 Audit Summary

In-place factual-accuracy review of 30 medium-risk manuals (separate from
the 30 high-risk reviewed in Phase 2 — total 60 of 250 audited).
Performed by parallel LLM agents that cross-checked each file against the
respective tech's authoritative docs (GitHub source, official docs).

Each agent applied only 95%+-confidence fixes — no new content added,
no formatting changes.

## Results

| Batch | Manuals | Edits |
|-------|---------|-------|
| 6: Stream/messaging | pulsar / redpanda / nats / rabbitmq / rocketmq / activemq | ~124 |
| 7: Cloud-native networking | istio / envoy / linkerd / kong / traefik / ingress-nginx | ~79 |
| 8: Observability stack | grafana / loki / jaeger / thanos / mimir / otel-collector | ~117 |
| 9: HashiCorp + security/identity | vault / consul / cert-manager / keycloak / external-secrets / nomad | ~91 |
| 10: Big data / ML | spark / hadoop / hive / trino / airflow / databricks | ~101 |
| **Total** | **30 manuals** | **~512 fixes** |

## Categories of fixes

- **Hallucinated metric names** — most common; e.g.
  `pulsar` had `pulsar-admin topics unfence` (replaced with `unload`),
  `loki` had `loki_compactor_runs_total` (replaced with
  `loki_boltdb_shipper_compact_tables_operation_total`),
  `mimir` had `cortex_distributor_dropped_samples_total` (replaced with
  `cortex_discarded_samples_total` — note `cortex_*` prefix is intentionally
  retained in Mimir, not a hallucination), `nomad` had
  `nomad.client.allocs.blocked` (replaced with `nomad.client.allocations.blocked`),
  `external-secrets` had `externalsecrets_*` (replaced with `externalsecret_*`,
  singular).
- **Wrong CLI subcommands** — e.g. `consul query list` (no such CLI; HTTP-only),
  `airflow tasks state --set` (replaced with REST API `PATCH .../taskInstances`),
  `databricks runs list` (replaced with `databricks jobs list-runs`),
  `kong check` ambiguous → `kong check /etc/kong/kong.conf`,
  `hive --service llap --start` flagged as deprecated.
- **Removed/renamed APIs** — e.g. Vault `sys/internal/counters/requests`
  (replaced with `/activity`), Trino PrestoDB class names (`com.facebook.*`
  → `io.trino.*`), Hadoop legacy ports (`50010`/`50075` → `9866`/`9864` for
  3.x), keycloak Wildfly `standalone.sh` and `9990` mgmt port (replaced with
  Quarkus `kc.sh start` and `9000/metrics`), Spark `spark.executor.memoryFraction`
  (removed in 1.6 unified memory manager).
- **Wrong default ports** — Hive HMS web UI `9083` (Thrift) → `9084` (HTTP),
  Jaeger collector admin `14268` → `14269` (14268 is span-ingest), agent admin
  `5778` → `14271` (5778 is sampling).
- **Cross-product contamination** — Trino doc had Impala `INVALIDATE METADATA`
  and Databricks `REORG TABLE`; ActiveMQ doc had a RocketMQ `rocketmq_*` metric
  leaked in; airflow `tasks list-dag-runs` invented from spark/k8s patterns;
  RabbitMQ `rabbitmq_node_partitions` (does not exist; uses `cluster_status`).
- **Architecture/concept errors** — Linkerd 2.x's Rust-based proxy was
  treated as JVM in places; Keycloak Wildfly mgmt API treated as still
  current (it's been Quarkus since 17); Jaeger Agent treated as still
  required (deprecated, OTLP-direct preferred); Mesos referenced as live
  Spark deployment option (removed in 3.4); Cassandra storage claimed for
  Kong (removed in 4.0).
- **Wrong CRD versions / namespace renames** — cert-manager `cert_manager_*`
  metric prefix (real form: `certmanager_*`, no underscore between cert and
  manager); ESO ValidatingWebhookConfiguration name (`external-secrets-webhook`
  → real names `secretstore-validate` and `externalsecret-validate`);
  external-secrets condition type `Valid=*` → real form `Ready=*`.
- **Wrong CLI flags** — e.g. ESO `--max-concurrent-reconciles` →
  `--concurrent` (real flag, default 1); ESO `--qps`/`--burst` →
  `--client-qps`/`--client-burst`; airflow `tasks states-for-dag-run
  --dag-id <id> --execution-date <d>` → positional `<dag-id> <execution-date>`;
  schematool missing required `-dbType` flag; `traefik --check`/`--dry-run`
  (don't exist).
- **License drift** — Vault/Consul/Nomad relicensed to BUSL 1.1 in Aug 2023;
  doc references to MPL 2.0 fixed where present.
- **Hallucinated config keys** — e.g. Spark `spark.sql.adaptive.execution.enabled`
  → real `spark.sql.adaptive.enabled`; Trino `query.history.max-count`
  → `query.max-history`; Trino `hive.metastore.cache-ttl`
  → `hive.metastore-cache-ttl` (dot vs hyphen); Keycloak `hive.heapsize`
  → `HIVE_SERVER2_HEAPSIZE`.

## Combined Phase 2+3 totals

- 60 of 250 manuals audited (24%)
- ~819 specific factual fixes applied (~307 in Phase 2, ~512 in Phase 3)
- All edits are conservative (95%+ confidence, no restructuring, no new
  content, no formatting changes)

## Remaining work (Phase 4, not done)

- 190 manuals not audited beyond Phase 1 systemic cleanup
- Per-tech metric-name-vs-exporter cross-check via automated comparison
  with real exporter source (each exporter publishes a known metric set)
- Vague-advice patterns flagged by lint (5 remaining)
- The `vtgate_queries_error` Vitess metric appears ~30 times and is likely
  fabricated; flagged for follow-up but not fixed in batch 2 due to
  pervasive change required
- Several flagged-but-not-fixed items in Phase 3 reports (low-confidence
  metric names, version-dependent config keys, etc.)

## Caveat

These manuals remain LLM-synthesized content. Phases 1+2+3 have removed
the worst credibility-killers (boilerplate, dead-package namespaces,
architecture errors, fabricated APIs) and most metric-name hallucinations
in the 60 audited files. The other 190 files have not been re-audited
beyond Phase 1 systemic cleanup; they may still contain version-drift or
hallucinated detail. Community PRs welcome via ISSUES.md.
