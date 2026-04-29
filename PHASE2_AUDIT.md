# Phase 2 Audit Summary

In-place factual-accuracy review of 30 highest-risk manuals (out of 250),
performed by parallel LLM agents that cross-checked each file against the
respective tech's authoritative docs (GitHub source, official docs).

Each agent applied only 95%+-confidence fixes — no new content added,
no formatting changes.

## Results

| Batch | Manuals | Edits |
|-------|---------|-------|
| 1: Niche/distributed DBs | arangodb / dgraph / scylladb / foundationdb / druid / neo4j | ~113 |
| 2: SQL distributed + cloud DBs | vitess / tidb / cockroachdb / planetscale / pgvector / pinecone | ~56 |
| 3: Vector DBs + niche | milvus / weaviate / qdrant / chromadb / couchdb / supabase | ~45 |
| 4: SaaS observability + CDN | snowflake / sentry / datadog / newrelic / splunk / cloudflare | ~47 |
| 5: Identity + legacy ops | auth0 / okta / zabbix / nagios / spinnaker / flink | ~46 |
| **Total** | **30 manuals** | **~307 fixes** |

## Categories of fixes

- **Hallucinated metric/config names** — most common; e.g.
  `qdrant_requests_total` (not real, replaced with `qdrant_rest_responses_total`),
  `chroma_server_query_count_total` (ChromaDB has no `/metrics`),
  `arangodb_rocksdb_block_cache_hit_rate` (replaced with derived metric),
  `dgraph_alpha_query_latency_bucket` (replaced with `dgraph_latency_bucket`).
- **Wrong port numbers** — e.g. ScyllaDB Prometheus port (10000 → 9180),
  Datadog Agent IPC port (5002 → 5001, ~12 occurrences).
- **Removed/renamed APIs** — e.g. Neo4j `db.indexes()` → `SHOW INDEXES` (5.0+),
  CouchDB `_ensure_full_commit` (removed in 3.x), `bolt+routing://` (removed
  in 4.x), Vitess `vtctldclient` subcommand renames, Flink `flink modify`
  (removed in 1.7).
- **Wrong CLI flags** — e.g. Dgraph `--lru_mb` (removed v21.03+ → `--cache`),
  CockroachDB `cockroach cert check` (doesn't exist → `cockroach cert list`),
  Nagios `nagios --stats` (doesn't exist → separate `nagiostats` binary).
- **Cross-tech contamination** — TiDB metric leaked into CRDB doc,
  Postgres syntax (`ILIKE`, `CREATE INDEX CONCURRENTLY`) in MySQL/CRDB
  examples, Delta Lake setting referenced as CouchDB config.
- **Architecture/concept errors** — ScyllaDB labelled with JVM/GC issues
  (it's C++/Seastar — ~10 occurrences), HBase `hbck -repair` (removed in
  2.x → HBCK2), incorrect role assignments.
- **Wrong error codes / HTTP status mappings** — FoundationDB had ~10
  swapped error codes; Okta had wrong HTTP status codes for error codes.
- **Outdated package names / namespaces** — `com.thinkaurelius.titan.*`
  (renamed to `org.janusgraph.*` in 2017), Pinecone SDK rename
  (`pinecone-client` → `pinecone` in v3+).
- **Vague platitudes replaced with specifics** — "monitor closely" →
  metric + threshold; "appropriate value" → actual number.

## Remaining work (Phase 3, not done)

- Per-tech metric-name-vs-exporter cross-check via automated comparison
  with real exporter source (each exporter publishes a known metric set).
- Vague-advice patterns flagged by lint (5 remaining instances).
- The `vtgate_queries_error` Vitess metric appears ~30 times and is likely
  fabricated; flagged for follow-up but not fixed in batch 2 due to
  pervasive change required.

## Caveat

These manuals remain LLM-synthesized content. Phase 1+2 has removed the
worst credibility-killers (boilerplate, dead-package namespaces,
architecture errors, fabricated APIs) and most metric-name hallucinations
in the 30 highest-risk files. The other 220 files have not been re-audited
beyond Phase 1 systemic cleanup; they may still contain version-drift or
hallucinated detail. Community PRs welcome via ISSUES.md.
