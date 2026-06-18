# Report Template

This file defines the structure and quality bar for the `mariadb-audit.md` output file. Use it as a skeleton when presenting findings on screen and when saving the report.

**Rules:**
- This template is a floor, not a ceiling — add observations beyond it when the data warrants it.
- Keep all section headers even for paths that were not run — write "Data for this section was not collected." under those headers.
- Replace `{placeholders}` with observed values. Remove placeholder text that does not apply.
- Every abbreviation or technical term must be expanded on first use (per the Plain Language principle).
- Present facts and measurements. Do not assign severity levels, suggest fixes, or make recommendations — except in the Security section, where severity and fix recommendations are always included.

---

<img src="https://raw.githubusercontent.com/robertsilen/mariadb-ai-dba/main/MariaDB_Foundation_logo.png" alt="MariaDB Foundation">

# MariaDB Server Inventory — {hostname}

**Server:** {version} on {os} ({cpu_model}, {ram_total})<br/>
**Generated:** {YYYY-MM-DD} at {HH:MM} {timezone, e.g. CET}<br/>
**Auditor:** Claude Code with [MariaDB AI DBA](https://github.com/robertsilen/mariadb-ai-dba) skill

---

## 1. Executive Summary

One or two paragraphs in plain language:
- What data was collected (which paths were run)
- Server version, uptime, and environment type (development, staging, or production — based on signals like connection limits, buffer pool size relative to RAM, SSL configuration, replication setup, and uptime)
- Key observations — notable facts about the server's current state, without value judgments
- If the security path was run, note the number of security findings by severity

### Overview

| Area | Observation |
|------|-------------|
| Version | {version} — rolling release (12.x) or LTS (11.4, 11.8) |
| Uptime | {human-readable} |
| Databases | {count} user databases, {total_size} |
| Connections | Peak {peak} of {max} configured |
| InnoDB Buffer Pool | {size} ({percent_of_ram}% of RAM) |
| Security findings | {count by severity, or "Not audited"} |

---

## 2. Server Identity & Environment

The foundation of this inventory. Version, hardware, and uptime provide context for interpreting all other values in this report.

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

---

## 3. InnoDB (MariaDB's default storage engine) Configuration & Health

InnoDB manages how data is stored, cached, and written to disk. This section documents the current InnoDB configuration and buffer pool state.

### Buffer Pool (BP)

| Setting / Metric | Value | Description |
|------------------|-------|-------------|
| Size | {size} | Allocated buffer pool memory ({percent}% of total RAM) |
| Hit ratio | {ratio}% | Calculated as (`innodb_buffer_pool_read_requests` - `innodb_buffer_pool_reads`) / `innodb_buffer_pool_read_requests` × 100 |
| Pages: total / data / dirty / free | | Current page allocation within the buffer pool |
| Instances | {n} | Number of buffer pool instances (removed in MariaDB 11.x+) |
| Dump at shutdown | {ON/OFF} | Whether buffer pool contents are saved on shutdown |
| Load at startup | {ON/OFF} | Whether saved buffer pool contents are loaded on startup |

### Durability & Logging

| Setting | Value | Description |
|---------|-------|-------------|
| `innodb_flush_log_at_trx_commit` | {0/1/2} | 0 = flush approximately once per second, 1 = flush on every commit (full ACID), 2 = write to OS buffer on commit, flush once per second |
| `sync_binlog` | {0/1/N} | 0 = OS controls flushing, 1 = sync on every commit, N = sync every N commits |
| `innodb_flush_method` | {value} | How InnoDB flushes data and log files to disk |
| `innodb_doublewrite` | {ON/OFF} | Doublewrite buffer for crash recovery protection against torn pages |
| `innodb_log_file_size` | {size} | Size of each redo log file |
| `innodb_log_buffer_size` | {size} | Memory buffer for redo log writes |

### Checkpoint Health

| Metric | Value |
|--------|-------|
| Checkpoint age | {value} |
| Max checkpoint age | {value} |
| Checkpoint age as % of max | {percent}% |

---

## 4. Connections & Threading

Each client connection consumes memory and a thread. This section documents the current connection configuration and usage.

| Setting / Metric | Value | Description |
|------------------|-------|-------------|
| Current connections | {n} | Active connections at time of snapshot |
| Peak connections (`Max_used_connections`) | {n} | Highest simultaneous connections since last restart |
| `max_connections` | {n} | Configured connection limit (utilization: peak / max = {percent}%) |
| Aborted connects | {n} | Failed connection attempts ({rate}/day over {uptime}) |
| Aborted clients | {n} | Connections closed improperly ({rate}/day over {uptime}) |
| `thread_cache_size` | {n} | Threads kept cached for reuse |
| `skip_name_resolve` | {ON/OFF} | Whether DNS reverse lookups are skipped on connect |

---

## 5. Query Performance Indicators

Server-wide counters that characterize the query workload. All values are cumulative since the last server restart ({uptime}).

### Global Counters

| Metric | Value | Rate (/sec) |
|--------|-------|-------------|
| Questions (total queries) | {n} | {rate} |
| Slow queries | {n} | {rate} |
| `Select_scan` (full table scans) | {n} | {rate} |
| `Select_full_join` (joins without indexes) | {n} | {rate} |
| `Sort_merge_passes` | {n} | {rate} |
| `Created_tmp_tables` | {n} | {rate} |
| `Created_tmp_disk_tables` | {n} | {rate} |
| `Handler_read_rnd_next` (full-scan row reads) | {n} | {rate} |

### Slow Query Log Configuration

| Setting | Value |
|---------|-------|
| `slow_query_log` | {ON/OFF} |
| `long_query_time` | {seconds} |
| `log_queries_not_using_indexes` | {ON/OFF} |

### Statement Digest Analysis (requires Performance Schema)

If Performance Schema is enabled, this section shows the actual queries consuming the most server resources. Data is cumulative since last server restart. If uptime is less than 24 hours, note that results may not represent the full workload cycle.

If Performance Schema is not enabled: "Performance Schema is not enabled. Enabling it (by adding `performance_schema = ON` to `my.cnf` and restarting) unlocks statement-level profiling — including which query patterns consume the most time, which indexes are actually used, and which tables generate the most I/O. Overhead is modest (~5% CPU, additional memory for instrumentation)."

#### Top Queries by Total Execution Time

| # | Query Pattern | Executions | Total Time (s) | Avg Time (ms) | Rows Examined | Rows Sent | Tmp Disk Tables |
|---|---|---|---|---|---|---|---|

Query patterns are normalized (literal values replaced with `?`). These are the query patterns that have consumed the most cumulative server time.

#### Queries with Highest Examination-to-Return Ratio

| # | Query Pattern | Executions | Rows Examined | Rows Sent | Ratio |
|---|---|---|---|---|---|

A high ratio means the query reads many more rows than it returns — the server does work that doesn't reach the client.

#### Table I/O Hotspots

| Schema.Table | Reads | Writes | Total Wait (s) |
|---|---|---|---|

Tables ranked by total I/O wait time.

---

## 6. Schema Analysis

The physical structure of tables and indexes. This section documents what exists in each database.

### Databases

| Database | Tables | Size (MB) | Notes |
|----------|--------|-----------|-------|

Storage engines in use across all user databases (excluding system schemas):

| Engine | Tables | Size (MB) |
|--------|--------|-----------|

### Tables Without Primary Key

InnoDB clusters data on the primary key. Without one, InnoDB uses an invisible 6-byte row ID. Tables listed here have no primary key defined.

| Schema | Table | Engine | Rows |
|--------|-------|--------|------|

### Auto-Increment Fill

Tables where the auto-increment column value has reached a significant portion of its data type's maximum.

| Schema | Table | Column | Type | Current Max | Type Max | Fill % |
|--------|-------|--------|------|-------------|----------|--------|

### Top 10 Largest Tables

| Schema.Table | Engine | Rows | Data (MB) | Index (MB) | Fragmentation (MB) |
|---|---|---|---|---|---|

### Duplicate / Redundant Indexes

Indexes where one is a left-prefix of another — the longer index can serve both query patterns.

| Schema.Table | Redundant Index | Columns | Superseded By | Columns |
|---|---|---|---|---|

### Tables Without Secondary Indexes

Tables with more than 10,000 rows that have only a primary key and no secondary indexes.

| Schema | Table | Rows | Size (MB) |
|--------|-------|------|-----------|

---

## 7. Security

Who can connect, what they can do, and whether the connection is encrypted. This section inventories all user accounts and flags security concerns.

### User Accounts

| Account | Auth Plugin | Host | SSL Required | Flags |
|---------|-------------|------|-------------|-------|

Flag column marks: anonymous, wildcard host, empty password, excessive privileges, etc.

### Security Findings

| # | Severity | Finding |
|---|----------|---------|

#### Standard checks (include all that apply):
- Anonymous accounts (user = '') — CRITICAL
- Non-local root accounts (root accessible from non-localhost) — HIGH
- Wildcard host accounts (host = '%') — HIGH
- Empty passwords (excluding unix_socket/auth_socket which are passwordless by design) — HIGH
- Accounts sharing identical password hashes — MEDIUM
- Non-root users with ALL PRIVILEGES — MEDIUM
- Non-root users with admin privileges (SUPER, SHUTDOWN, RELOAD, PROCESS, FILE) — MEDIUM
- SSL/TLS (Secure Sockets Layer / Transport Layer Security) disabled — MEDIUM
- `require_secure_transport` OFF — MEDIUM
- `local_infile` ON — LOW
- Test database present — LOW
- Password validation plugin not installed — LOW

If any CRITICAL or HIGH findings exist, add one short paragraph after the table explaining what the most urgent items are and how to fix them. Keep it concise — a few sentences, not a block per finding.

---

## 8. MariaDB Features

MariaDB includes capabilities that many applications implement in application code or miss entirely. This section inventories which MariaDB-specific features are currently in use and which are available but not yet used.

### Features in Use

| Feature | Where | Details |
|---------|-------|---------|

List MariaDB-specific features detected in the schema: system versioning, VECTOR columns, generated columns, CHECK constraints, spatial indexes, sequences, application-time periods, page compression, JSON columns, invisible columns.

### Features Available but Not in Use

For each feature available in this MariaDB version that the schema could potentially use:

| Feature | Available Since | Potential Applicability |
|---------|----------------|----------------------|

Note where schema patterns suggest a feature could apply (e.g., "timestamp pairs in `orders.valid_from`/`orders.valid_to` — application-time periods are available") without recommending specific changes.

#### Features to scan for:
- **System-versioned tables** — tables with audit triggers, soft-delete patterns (is_deleted, deleted_at), or created_at/updated_at columns
- **VECTOR columns and indexes** — BLOB or VARBINARY columns named embedding, vector, feature, or similar
- **Generated (virtual/stored) columns** — derived data patterns like full_name from first_name + last_name, or JSON key extraction
- **CHECK constraints** — ENUM columns, numeric domain columns (age, percentage, rating, status codes)
- **Spatial indexes** — latitude/longitude or x/y coordinate column pairs
- **Sequences** — single-column tables or patterns used purely for ID generation
- **Application-time periods** — valid_from/valid_to or start_date/end_date column pairs
- **Row compression** — large tables with TEXT/BLOB columns using uncompressed row format
- **JSON functions** — VARCHAR or TEXT columns storing JSON strings
- **Invisible columns** — note as available for future schema additions

---

## 9. Replication

Replication keeps copies of the database in sync for high availability, read scaling, or disaster recovery. This section documents the current replication configuration.

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

## Appendix A: Raw Configuration

Reference values for DBAs who want to verify observations or spot issues not covered by the automated checks.

Selected `@@global.*` values for expert review. Include at minimum:

`innodb_buffer_pool_size`, `innodb_buffer_pool_instances`,
`innodb_log_file_size`, `innodb_log_buffer_size`,
`innodb_flush_log_at_trx_commit`, `innodb_flush_method`,
`innodb_doublewrite`, `sync_binlog`,
`max_connections`, `thread_cache_size`, `table_open_cache`,
`tmp_table_size`, `max_heap_table_size`,
`sort_buffer_size`, `join_buffer_size`,
`read_buffer_size`, `read_rnd_buffer_size`,
`skip_name_resolve`, `local_infile`,
`have_ssl`, `require_secure_transport`,
`performance_schema`,
`slow_query_log`, `long_query_time`, `log_queries_not_using_indexes`

---

## Appendix B: Credits

MariaDB Foundation AI DBA Server Inventory created {YYYY-MM-DD} {HH:MM} {timezone}<br/>
Auditor: Claude Code with [MariaDB AI DBA](https://github.com/mariadb/skills/tree/main/skills/mariadb-ai-dba) skill<br/>
Developed by [@robertsilen](https://github.com/robertsilen) based on DBA skills by [@lefred](https://github.com/lefred) and an idea by [@kajarnocom](https://github.com/kajarnocom)

Install:
```bash
git clone https://github.com/mariadb/skills.git /tmp/mariadb-skills && ln -s /tmp/mariadb-skills/skills/mariadb-ai-dba ~/.claude/skills/mariadb-ai-dba
```
