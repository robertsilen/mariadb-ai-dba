# Report Template

This file defines the structure and quality bar for the `mariadb-audit.md` output file. Use it as a skeleton when presenting findings on screen and when saving the report.

**Rules:**
- This template is a floor, not a ceiling — add findings beyond it when the data warrants it.
- Keep all section headers even for paths that were not run — write "Analysis for this section was not collected." under those headers.
- Replace `{placeholders}` with observed values. Remove placeholder text that does not apply.
- Every abbreviation or technical term must be expanded on first use (per the Plain Language principle).
- Findings must include severity, what, why it matters, and a concrete fix (SQL or config).

---

# MariaDB Audit Report — {hostname}

**Server:** {version} on {os} ({cpu_model}, {ram_total})
**Generated:** {YYMMDD-HHMMSS timestamp from `date +%y%m%d-%H%M%S`}
**Auditor:** Claude Code with [MariaDB AI DBA](https://github.com/robertsilen/mariadb-ai-dba) skill

---

## Severity Levels

All findings in this report are classified using the following severity levels:

| Severity | Meaning |
|----------|---------|
| **CRITICAL** | Immediate risk to data integrity, security, or availability. Fix now — the server is actively vulnerable or degraded. Examples: anonymous database accounts, data loss risk from unsafe durability settings. |
| **HIGH** | Significant issue that will cause problems under load or exposes a serious security gap. Should be addressed soon. Examples: non-local root accounts, missing primary keys on large tables, buffer pool severely undersized. |
| **MEDIUM** | Suboptimal configuration or practice that impacts performance or security posture. Plan to address. Examples: SSL disabled, duplicate indexes wasting resources, admin privileges granted too broadly. |
| **LOW** | Minor improvement opportunity or best-practice recommendation. Address when convenient. Examples: test database present, slow query log disabled, password validation plugin not installed. |

---

## 1. Executive Summary

One or two paragraphs in plain language:
- Overall health assessment (healthy / needs attention / critical issues found)
- Number of findings by severity (CRITICAL / HIGH / MEDIUM / LOW)
- Whether the server appears to be development, staging, or production — based on signals like connection limits, buffer pool size relative to RAM, SSL configuration, replication setup, and uptime
- The single most important recommendation, if any

### Findings at a Glance

| Severity | Count | Top Issue |
|----------|-------|-----------|
| CRITICAL | {n}   | {one-liner or "None"} |
| HIGH     | {n}   | {one-liner or "None"} |
| MEDIUM   | {n}   | {one-liner or "None"} |
| LOW      | {n}   | {one-liner or "None"} |

---

## 2. Server Identity & Environment

The foundation for every recommendation in this report. Version, hardware, and uptime determine what configuration values are appropriate and whether the server is end-of-life or due for an upgrade.

| Item | Value |
|------|-------|
| Version | {version} — note if rolling release (12.x) vs LTS (11.4, 11.8) |
| Hostname | {hostname} |
| Port / Socket | {port} / {socket} |
| Data directory | {datadir} |
| Uptime | {days/hours human-readable} |
| OS | {os version, kernel, architecture} |
| CPU | {model, core count} |
| RAM | {total} |
| Disk (datadir) | {used} / {total} ({percent}% used, {free} free) |

**Flags to check:**
- Version end-of-life or rolling release in production
- Uptime < 1 day (recent restart — why?)
- Disk usage > 80%

---

## 3. InnoDB (MariaDB's default storage engine) Configuration & Health

InnoDB manages how data is stored, cached, and written to disk. Misconfiguration here is the most common cause of poor performance and the most common source of data loss risk after a crash.

### Buffer Pool (BP)

| Metric | Value | Assessment |
|--------|-------|------------|
| Size | {size} | Target 60-80% of RAM on a dedicated database server |
| Hit ratio | {ratio}% | (read_requests - reads) / read_requests x 100; healthy > 99% |
| Pages: total / data / dirty / free | | Free < 5% of total = undersized; free > 50% = oversized |
| Instances | {n} | 1 per GB recommended for pools > 1 GB |
| Dump at shutdown | {ON/OFF} | Should be ON — preserves warm cache across restarts |
| Load at startup | {ON/OFF} | Should be ON |

### Durability & Logging

| Setting | Value | Assessment |
|---------|-------|------------|
| innodb_flush_log_at_trx_commit | {0/1/2} | 1 = full ACID (safe), 2 = flush once/sec (fast), 0 = risky |
| sync_binlog | {0/1/N} | 1 = safest, 0 = fast but data-loss risk on crash |
| innodb_flush_method | {value} | O_DIRECT recommended on Linux to avoid double-buffering |
| innodb_doublewrite | {ON/OFF} | Should be ON unless hardware has battery-backed write cache |
| innodb_log_file_size | {size} | Should hold approximately 1 hour of peak write traffic |
| innodb_log_buffer_size | {size} | 16-64 MB is typical |

### Checkpoint Health

| Metric | Value | Assessment |
|--------|-------|------------|
| Checkpoint age vs max age | | Age > 75% of max = redo log files too small, increase innodb_log_file_size |

---

## 4. Connections & Threading

Each client connection consumes memory and a thread. Running out of connections causes application errors; misconfigured threading wastes resources or adds latency.

| Metric | Value | Assessment |
|--------|-------|------------|
| Current connections | {n} | |
| Peak connections (Max_used_connections) | {n} | |
| max_connections | {n} | Utilization = peak / max x 100; > 80% = warning |
| Aborted connects | {n} | High count relative to uptime = authentication failures or network issues |
| Aborted clients | {n} | High count = clients not closing connections properly |
| thread_cache_size | {n} | Should cover typical connection churn |
| skip_name_resolve | {ON/OFF} | Should be ON for performance (avoids DNS lookups on connect) |

---

## 5. Query Performance Indicators

Server-wide counters that reveal whether queries are running efficiently. High scan rates, excessive temp tables on disk, or joins without indexes point to specific optimization opportunities.

### Global Counters

| Metric | Value | Rate (/sec) | Assessment |
|--------|-------|-------------|------------|
| Questions (total queries) | {n} | {rate} | Baseline for other ratios |
| Slow queries | {n} | {rate} | Any non-trivial ratio to Questions = investigate |
| Select_scan (full table scans) | {n} | {rate} | High rate = missing indexes |
| Select_full_join (joins without indexes) | {n} | {rate} | Should be 0 |
| Sort_merge_passes | {n} | {rate} | > 0 suggests sort_buffer_size may be too small |
| Created_tmp_tables | {n} | {rate} | |
| Created_tmp_disk_tables | {n} | {rate} | Disk / total > 25% = raise tmp_table_size or max_heap_table_size |
| Handler_read_rnd_next (full-scan row reads) | {n} | {rate} | High value confirms full table scans |

### Slow Query Log Configuration

| Setting | Value | Recommendation |
|---------|-------|----------------|
| slow_query_log | {ON/OFF} | Should be ON |
| long_query_time | {seconds} | 1-2 seconds for most workloads |
| log_queries_not_using_indexes | {ON/OFF} | ON for development and staging |

### Statement Digest Analysis (requires Performance Schema)

If Performance Schema is enabled, this section shows the actual queries consuming the most server resources. Data is cumulative since last server restart. If uptime is less than 24 hours, note that results may not represent the full workload cycle.

If Performance Schema is not enabled: "Performance Schema is not enabled. Enable it by adding `performance_schema=ON` to `my.cnf` and restarting the server. This unlocks statement-level profiling — the most powerful tool for identifying optimization targets. Let data accumulate for at least 24 hours before re-running the audit. Overhead is modest (~5% CPU, additional memory for instrumentation)."

#### Top Queries by Total Execution Time

| # | Query Pattern | Executions | Total Time (s) | Avg Time (ms) | Rows Examined | Rows Sent | Tmp Disk Tables |
|---|---|---|---|---|---|---|---|

Query patterns are normalized (literal values replaced with `?`). Focus on the top entries — these are where optimization effort has the highest return.

#### Most Inefficient Queries (rows examined vs rows sent)

| # | Query Pattern | Executions | Rows Examined | Rows Sent | Ratio |
|---|---|---|---|---|---|

A ratio above 10 means the query reads far more rows than it returns — typically a sign of missing or suboptimal indexes. Cross-reference with Schema Analysis (section 6) for index recommendations.

#### Table I/O Hotspots

| Schema.Table | Reads | Writes | Total Wait (s) |
|---|---|---|---|

Shows which tables generate the most I/O wait time. High-wait tables are prime candidates for index optimization, buffer pool sizing, or schema redesign.

---

## 6. Schema Analysis

The physical structure of tables and indexes has a direct impact on query speed, storage efficiency, and replication reliability. Schema problems tend to get worse over time as data grows.

### Databases

| Database | Tables | Size (MB) | Notes |
|----------|--------|-----------|-------|

Include storage engines in use across all user databases (excluding system schemas):

| Engine | Tables | Size (MB) |
|--------|--------|-----------|

### Tables Without Primary Key

List any tables lacking a primary key. Explain: InnoDB clusters data on the primary key. Without one, InnoDB uses an invisible 6-byte row ID, which hurts performance and can break row-based replication.

| Schema | Table | Engine | Rows |
|--------|-------|--------|------|

### Auto-Increment Fill

Tables where the auto-increment column value is approaching the maximum for its data type (>75% full). Reaching the max causes insert failures.

| Schema | Table | Column | Type | Current Max | Type Max | Fill % |
|--------|-------|--------|------|-------------|----------|--------|

### Top 10 Largest Tables

| Schema.Table | Engine | Rows | Data (MB) | Index (MB) | Fragmentation (MB) |
|---|---|---|---|---|---|

Tables with significant DATA_FREE relative to DATA_LENGTH may benefit from `OPTIMIZE TABLE`.

### Duplicate / Redundant Indexes

Indexes where one is a left-prefix of another. The longer index can serve both patterns — the shorter one wastes disk and slows writes.

| Schema.Table | Redundant Index | Columns | Superseded By | Columns |
|---|---|---|---|---|

### Tables Without Secondary Indexes

Tables with >10,000 rows that have only a primary key. These likely need secondary indexes for common query patterns.

| Schema | Table | Rows | Size (MB) |
|--------|-------|------|-----------|

---

## 7. Security

Who can connect, what they can do, and whether the connection is encrypted. A single misconfigured account can expose the entire database — security issues are often invisible until exploited.

### User Accounts

| Account | Auth Plugin | Host | SSL Required | Flags |
|---------|-------------|------|-------------|-------|

Flag column marks: anonymous, wildcard host, empty password, excessive privileges, etc.

### Findings

For each finding, include:

**{SEVERITY}: {title}**
- **What:** {description}
- **Why it matters:** {risk explanation}
- **Fix:**
```sql
{exact SQL or config change}
```

#### Standard checks (include all that apply):
- Anonymous accounts (user = '') — CRITICAL
- Non-local root accounts (root accessible from non-localhost) — HIGH
- Wildcard host accounts (host = '%') — HIGH
- Empty passwords (excluding unix_socket/auth_socket which are passwordless by design) — HIGH
- Accounts sharing identical password hashes — MEDIUM
- Non-root users with ALL PRIVILEGES — MEDIUM
- Non-root users with admin privileges (SUPER, SHUTDOWN, RELOAD, PROCESS, FILE) — MEDIUM
- SSL/TLS (Secure Sockets Layer / Transport Layer Security) disabled — MEDIUM
- require_secure_transport OFF — MEDIUM
- local_infile ON — LOW
- Test database present — LOW
- Password validation plugin not installed — LOW

---

## 8. MariaDB Feature Opportunities

MariaDB includes capabilities that many applications implement in application code or miss entirely. Using built-in features reduces complexity, improves performance, and strengthens data integrity at the database level.

For each suggestion:

### {N}. {Feature Name} for `{schema.table}` ({SEVERITY})

{One-line description of the MariaDB feature.}

**Where it applies:** `{schema.table.column}` — {why this table/column is a candidate}

**Benefit:** {what improves — query speed, data integrity, auditability, storage}

```sql
{Ready-to-run ALTER TABLE or CREATE statement}
```

#### Features to scan for:
- **System-versioned tables** — tables with audit triggers, soft-delete patterns (is_deleted, deleted_at), or created_at/updated_at columns that would benefit from built-in temporal history
- **VECTOR columns and indexes** — BLOB or VARBINARY columns named embedding, vector, feature, or similar that should use native VECTOR type with appropriate DISTANCE metric
- **Generated (virtual/stored) columns** — derived data patterns like full_name from first_name + last_name, or JSON key extraction that could be computed columns
- **CHECK constraints** — ENUM columns, numeric domain columns (age, percentage, rating, status codes) that would benefit from domain validation
- **Spatial indexes** — latitude/longitude or x/y coordinate column pairs that could use GEOMETRY + SPATIAL INDEX
- **Sequences** — single-column tables or patterns used purely for ID generation that could use CREATE SEQUENCE
- **Application-time periods** — valid_from/valid_to or start_date/end_date column pairs that could use WITHOUT OVERLAPS constraints
- **Row compression** — large tables with TEXT/BLOB columns using uncompressed row format that could benefit from PAGE compression
- **JSON functions** — VARCHAR or TEXT columns storing JSON strings that could use native JSON column type with JSON_TABLE, JSON_VALUE
- **Invisible columns** — cases where adding a column for new functionality should be invisible to preserve backward compatibility with existing SELECT * queries

---

## 9. Replication

Replication keeps copies of the database in sync for high availability, read scaling, or disaster recovery. Lag, errors, or misconfiguration here can cause stale reads or data loss during failover.

**If the server is configured as a replica:**

| Metric | Value |
|--------|-------|
| Replication status | Running / Stopped / Errored |
| Seconds behind master | {lag} |
| GTID (Global Transaction ID) position | {gtid} |
| Parallel replication | {type and workers} |
| Semi-sync replication | {enabled/disabled} |

Include any replication errors or warnings.

**If the server is NOT a replica:** State "Not configured as a replica" and skip this section.

---

## 10. Recommendations Summary

Every finding from the report in one place, sorted by severity. Use this as a prioritized action list — start from the top.

All findings from all sections, sorted by severity:

| # | Severity | Section | Finding | Fix |
|---|----------|---------|---------|-----|
| 1 | CRITICAL | | | |
| 2 | HIGH | | | |
| ... | | | | |

---

## Appendix: Raw Configuration

Reference values for DBAs who want to verify findings or spot issues not covered by the automated checks.

Selected `@@global.*` values for expert review. Include at minimum:

```
innodb_buffer_pool_size, innodb_buffer_pool_instances,
innodb_log_file_size, innodb_log_buffer_size,
innodb_flush_log_at_trx_commit, innodb_flush_method,
innodb_doublewrite, sync_binlog,
max_connections, thread_cache_size, table_open_cache,
tmp_table_size, max_heap_table_size,
sort_buffer_size, join_buffer_size,
read_buffer_size, read_rnd_buffer_size,
skip_name_resolve, local_infile,
have_ssl, require_secure_transport,
performance_schema,
slow_query_log, long_query_time, log_queries_not_using_indexes
```
