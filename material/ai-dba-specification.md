# AI DBA Specification Document

**Date:** 2026-06-23 (updated)
**Sources:** "MySQL Performance Tuning v19" by Frédéric Descamps (@lefred), Lefred Consulting Scripts, lefred's "a graph a day" blog series (https://lefred.be/?s=a+graph+a+day)
**Purpose:** Define what an AI DBA skill should analyze, and how the user experience should work

---

## STEP 1: Complete Catalog of DBA Analysis Paths

Every tuning/analysis area found in the source material is listed below. Each is marked as either **SELECTED** (top 10 for v1 implementation) or **DEFERRED** (valid but lower priority for a first release).

The **Collection Type** column classifies what kind of data each analysis needs:

- **Config** — Server configuration (GLOBAL VARIABLES). Static — only changes when someone changes it. A single read is definitive.
- **Snapshot** — Point-in-time state (schema structure, user grants, InnoDB status). Factual at the moment of capture. Safe to act on alone.
- **Cumulative** — STATUS counters accumulated since server start. A single read gives a lifetime average, which can mask recent problems. **Two reads separated by time (delta) are far more meaningful** — they show what happened during that window.
- **Trending** — Metrics that must be sampled repeatedly over time (minutes/hours) to reveal patterns, spikes, or periodic issues. Single or even dual snapshots are insufficient.

### Category A: InnoDB Storage Engine

| # | Analysis Path | Collection Type | Source | Decision | Rationale |
|---|---|---|---|---|---|
| A1 | **InnoDB buffer pool sizing** — check if innodb_buffer_pool_size is 70-80% of RAM (dedicated server), verify disk read ratio via `SHOW ENGINE INNODB STATUS` (reads from disk should be < 1-2% of total reads) | **Config** (size setting) + **Cumulative** (hit ratio) | PDF p.25-29 | **SELECTED** | Single highest-impact tuning variable. Config check is safe from one read; hit ratio needs delta to be trustworthy. |
| A2 | **InnoDB redo log sizing / checkpoint age** — compare checkpoint age to max checkpoint age from `SHOW ENGINE INNODB STATUS`, verify innodb_log_file_size is large enough that checkpoint age never exceeds 80% of max | **Snapshot** (current checkpoint age) + **Trending** (checkpoint age over time reveals spikes during peak writes) | PDF p.10-16 | **SELECTED** | Critical for write-heavy workloads. A single snapshot shows current pressure, but trending reveals whether checkpoint age spikes dangerously during peak load and then recovers. |
| A3 | **InnoDB flush method** — verify innodb_flush_method = O_DIRECT (avoids double-buffering with filesystem cache) | **Config** | PDF p.8-9, 49 | **IMPLEMENTED** | Collected and shown in the Durability & Logging table. |
| A4 | **InnoDB durability settings** — check innodb_flush_log_at_trx_commit (1=safe, 2=fast), innodb_doublewrite, sync_binlog | **Config** | PDF p.7-9 | **SELECTED** | Directly affects data safety vs. performance tradeoff. Pure config check — safe from one read. |
| A5 | **InnoDB file-per-table / tablespace layout** — check innodb_file_per_table, detect tables still in shared ibdata1 tablespace | **Config** + **Snapshot** | PDF p.17-20, Lefred env script | **SELECTED** | Tables in ibdata1 are a ticking time bomb (space never reclaimed). Easy to detect and actionable. |
| A6 | **InnoDB general tablespaces** — check for use of CREATE TABLESPACE for separating hot/cold data on different disks | **Config** + **Snapshot** | PDF p.19-20 | DEFERRED | Advanced optimization. Not relevant for most users. |
| A7 | **InnoDB buffer pool dump/load** — check innodb_buffer_pool_dump_at_shutdown and innodb_buffer_pool_load_at_startup | **Config** | PDF p.30-31 | **IMPLEMENTED** | Collected and shown in Buffer Pool table. |
| A8 | **InnoDB compression / row format analysis** — check ROW_FORMAT, detect tables using COMPRESSED or REDUNDANT formats | **Snapshot** | Lefred env script | DEFERRED | Niche optimization. |
| A9 | **InnoDB fragmentation** — detect fragmented InnoDB tables (data_free significantly high vs data_length) | **Snapshot** | Lefred env script | **IMPLEMENTED** | Collector detects fragmented tables (>100MB free). Shown in schema section. |
| A10 | **InnoDB History List Length** — track `Innodb_history_list_length` to detect long-running transactions holding back purge. A growing HLL means undo logs are accumulating because old read views prevent cleanup, distinct from checkpoint age which measures write volume pressure | **Trending** (HLL fluctuates with transaction lifetime; a single snapshot may catch it at zero or at a spike) | Lefred comment, lefred "a graph a day" blog series | **IMPLEMENTED** | Graph in graph.py (`history_list`). Different signal than checkpoint age — reveals long-running transactions and purge lag rather than write throughput pressure. |

### Category B: Memory & Sessions

| # | Analysis Path | Collection Type | Source | Decision | Rationale |
|---|---|---|---|---|---|
| B1 | **Total memory consumption estimation** — calculate theoretical max memory: buffer pool + (per-session buffers x max_connections) + other global buffers. Check if it exceeds available RAM (OOM risk) | **Config** + **Snapshot** (current RAM) | PDF p.32-36 | **SELECTED** | Prevents the #1 catastrophic failure: OOM killer taking down the database. Pure arithmetic on config values — safe from one read. |
| B2 | **max_connections tuning** — compare max_used_connections to max_connections. Check if max_connections is excessively high (wastes memory reservation) or too low (connection refused errors) | **Config** + **Cumulative** (Max_used_connections is a high-water mark since startup) | PDF p.34-35, Lefred env script | **SELECTED** | Over-provisioned max_connections is the most common rookie mistake. The high-water mark is cumulative but useful — it tells you the peak ever reached. Delta adds value: seeing current Threads_connected over time shows patterns. |
| B3 | **Temporary tables to disk** — check ratio of Created_tmp_disk_tables to Created_tmp_tables, investigate if tmp_table_size / max_heap_table_size are too small or if queries use BLOB/TEXT columns (which force disk temp tables regardless) | **Cumulative** (ratio is only meaningful as delta between two points) | PDF p.35-36 | **SELECTED** | High disk temp table ratio = slow queries. **Lefred's concern applies here strongly** — a lifetime ratio of 5% could hide the fact that the last hour was 80%. Delta between two snapshots is essential. |
| B4 | **Performance Schema memory tracking** — use performance_schema to identify memory consumers | **Snapshot** | PDF p.32 | DEFERRED | Requires performance_schema enabled, which has overhead. Good for deep dives but not first-pass audit. |
| B5 | **Session buffer sizing** — check sort_buffer_size, join_buffer_size, read_buffer_size, read_rnd_buffer_size individually | **Config** | PDF p.34 | DEFERRED | Fine-grained session tuning is rarely needed outside extreme workloads. Covered indirectly by B1. |

### Category C: OS & Hardware

| # | Analysis Path | Collection Type | Source | Decision | Rationale |
|---|---|---|---|---|---|
| C1 | **Swap usage detection** — check if mysqld process is using swap (`/proc/{pid}/status` VmSwap), check swappiness setting (should be 1-5, default 60 is too high) | **Snapshot** (current swap state) + **Config** (swappiness) | PDF p.39-40 | **SELECTED** | Swap = death for database performance. Easy check, clear fix. |
| C2 | **I/O scheduler** — check disk scheduler (should be noop/none for SSDs, not CFQ) | **Config** | PDF p.47 | DEFERRED | Important but OS-level, usually set once. |
| C3 | **Filesystem type & mount options** — check if XFS (kernel > 4.1) or ext4, verify mount options (noatime, etc.) | **Config** | PDF p.48 | DEFERRED | OS-level, set once at provisioning. |
| C4 | **Filesystem cache tuning** — check dirty_ratio, dirty_background_ratio, vfs_cache_pressure sysctl values | **Config** | PDF p.49-52 | DEFERRED | OS-level tuning, valid but secondary. |
| C5 | **NUMA configuration** — detect multi-NUMA-node systems, check if innodb_numa_interleave is enabled | **Config** | PDF p.45-46 | DEFERRED | Only relevant for multi-socket servers. |
| C6 | **Memory allocator** — check if tcmalloc or jemalloc is loaded instead of glibc-malloc | **Snapshot** | PDF p.41-43 | DEFERRED | Hard to change in practice (requires restart + package install). |
| C7 | **CPU info / core count** — collect CPU architecture for context | **Snapshot** | PDF p.45-46, Lefred env script | DEFERRED | Informational, not directly actionable. |
| C8 | **Disk I/O response time** — collect read and write response times separately (not just throughput). Standard Linux `iostat` doesn't split read/write latency well; `pt-diskstats` has the columns needed. Correlate with MariaDB behavior: high I/O latency often explains slow queries, and processes in `b` (blocked) column of `mpstat` usually indicates storage bottleneck | **Trending** (needs continuous sampling over minutes/hours) | Lefred metrics script, Lefred comment | **IMPLEMENTED** | Daemon collects `/proc/diskstats` read/write latency (Linux). Graph `os_disk_latency` shows separate read/write ms per I/O. |
| C9 | **OS metric trending** — sample CPU usage, memory/swap usage, and load average over time alongside MariaDB daemon samples. Enables matching MariaDB behavior to OS state: e.g. queries getting slow while OS is swapping, or CPU saturation during query spikes | **Trending** (same cadence as MariaDB daemon samples) | Lefred comment | **IMPLEMENTED** | `sample_os_metrics()` in collect.py — CPU jiffies (Linux), memory, swap, load average, disk I/O. Sampled every daemon interval alongside MariaDB status. |
| C10 | **OS/MariaDB correlation graphs** — display OS metric graphs (CPU, memory, swap, I/O) alongside MariaDB graphs in the report so trends can be visually matched. E.g. a spike in query latency lines up with a spike in swap activity or disk I/O wait | **Trending** (requires C9 data) | Lefred comment | **IMPLEMENTED** | 5 OS graphs in graph.py (`os_memory`, `os_swap`, `os_load`, `os_cpu`, `os_disk_latency`), placed after Server Identity via `<!-- GRAPHS:os -->` marker. |

### Category D: Schema & Data Design

| # | Analysis Path | Collection Type | Source | Decision | Rationale |
|---|---|---|---|---|---|
| D1 | **MyISAM detection** — find any tables still using MyISAM engine. Should be converted to InnoDB. | **Snapshot** | PDF p.37-38, Lefred env script | **SELECTED** | MyISAM is always wrong in modern MySQL/MariaDB. Zero nuance, clear recommendation. Easy win. |
| D2 | **Tables missing primary keys** — detect InnoDB tables without an explicit PRIMARY KEY | **Snapshot** | Lefred env script | **IMPLEMENTED** | Collector query `tables_no_pk`. Shown in schema section. |
| D3 | **Non-optimal primary keys** — detect tables using TEXT/BLOB columns as PK, or excessively wide PKs | **Snapshot** | Lefred env script | **IMPLEMENTED** | Collector query `tables_bad_pk`. Shown in schema section. |
| D4 | **Duplicate/redundant indexes** — find indexes that are prefixes of other indexes (wastes space and slows writes) | **Snapshot** | Lefred env script (pt-duplicate-key-checker) | **IMPLEMENTED** | Collector query `index_duplicates`. Shown in schema section. |
| D5 | **Auto-increment fill ratio** — detect tables approaching auto-increment overflow | **Snapshot** | Lefred env script | **IMPLEMENTED** | Collector query `autoinc_fill`. Shown in schema section. |
| D6 | **Dataset size analysis** — total data size by engine, schema, table; identify largest tables | **Snapshot** | Lefred env script | **IMPLEMENTED** | Collector queries `databases`, `engine_summary`, `top_tables`. Shown in schema section. |

### Category E: Security & Access

| # | Analysis Path | Collection Type | Source | Decision | Rationale |
|---|---|---|---|---|---|
| E1 | **Basic security audit** — check for: root accessible from non-localhost, anonymous accounts, wildcard host grants, users with no password, users with excessive privileges (SUPER, ALL on *.*) | **Snapshot** | Lefred env script | **SELECTED** | Security is non-negotiable. These checks are simple SQL queries with clear pass/fail results. Rookie users especially need this. |
| E2 | **Identical passwords across accounts** — detect users sharing the same password hash | **Snapshot** | Lefred env script | **IMPLEMENTED** | Collector query `shared_passwords`. Shown as MEDIUM finding. |
| E3 | **Unused accounts** — find database accounts that have been granted access but never connected. Stale accounts are an attack surface — they have credentials but no one monitoring their use. When Performance Schema is available, check `performance_schema.accounts` for accounts with zero connections; without PS, flag accounts with no recent activity heuristics | **Cumulative** | Lefred env script, Lefred comment | **IMPLEMENTED** | Collector query `unused_accounts` (PS-gated). Shown as LOW finding. |

### Category F: Query & Performance

| # | Analysis Path | Collection Type | Source | Decision | Rationale |
|---|---|---|---|---|---|
| F1 | **Unused indexes** — from performance_schema, find indexes that have never been used for reads | **Cumulative** (needs representative uptime period) | Lefred env script | **IMPLEMENTED** | Collector query `index_unused` (PS-gated). Shown in schema section. |
| F2 | **Full table scans** — from performance_schema, identify tables with excessive full scans | **Cumulative** | Lefred env script | DEFERRED | Requires performance_schema. |
| F3 | **Slow query analysis** — identify problematic queries by user from performance_schema | **Cumulative** / **Trending** | Lefred env script | DEFERRED | Requires performance_schema and is workload-dependent. |
| F4 | **Processlist analysis** — capture running queries over time to identify long-running or blocking sessions | **Trending** (needs repeated sampling every 1-60s over minutes/hours) | Lefred metrics script | DEFERRED | Requires continuous collection. |
| F5 | **Query response time distribution** — histogram of query response times | **Trending** | Lefred metrics script | DEFERRED | Not available in MariaDB. |
| F6 | **Read/write workload ratio** — show the ratio of reads (`Com_select`) to writes (`Com_insert` + `Com_update` + `Com_delete` + `Com_replace`) as a pie chart at the top of the report. People commonly misjudge their workload — "my system is read-intensive" — but each read also generates redo log writes, and the actual DML mix often tells a different story. Present at the top of the Executive Summary to frame everything that follows | **Cumulative** (lifetime ratio from STATUS counters; delta ratio from comparison window is even more useful) | Lefred comment | **IMPLEMENTED** | Pie chart graph in graph.py (`workload_ratio`), injected via `<!-- GRAPHS:workload -->` marker in Workload Profile subsection. |

### Category G: Replication

| # | Analysis Path | Collection Type | Source | Decision | Rationale |
|---|---|---|---|---|---|
| G1 | **Replication lag monitoring** — check Seconds_Behind_Master / slave status | **Trending** (lag fluctuates; a single snapshot is nearly useless) | Lefred metrics script | DEFERRED | Only relevant if replication is configured. |
| G2 | **Replication configuration audit** — check binlog format, GTID, semi-sync settings | **Config** | Lefred env script | DEFERRED | Only relevant if replication is configured. |

### Category H: Network & Connections

| # | Analysis Path | Collection Type | Source | Decision | Rationale |
|---|---|---|---|---|---|
| H1 | **skip_name_resolve** — check if DNS resolution is adding latency to connections | **Config** | PDF p.44 | **IMPLEMENTED** | Collected in security audit, shown as LOW finding when OFF. |
| H2 | **Connection pooling assessment** — advise on connection pooling based on connection patterns | **Trending** (need to observe connection open/close patterns over time) | PDF p.55-61 | DEFERRED | Application-side, not server-side. Out of scope for a server audit tool. |

### Category I: Server Logs

| # | Analysis Path | Collection Type | Source | Decision | Rationale |
|---|---|---|---|---|---|
| I1 | **Error log analysis** — read and pattern-match the MariaDB error log for warnings, crashes, stack traces, OOM events, plugin failures, and replication errors. Normalize timestamps and connection IDs, extract recurring patterns, and surface the most recent critical events | **Snapshot** (tail of current error log) | [MariaDB DB Agents](https://github.com/mariadb-JagsR/mariadb-db-agents) | DEFERRED | The error log is the server's own incident record — it captures events that no status variable or Performance Schema query reveals (crashes, startup warnings, deprecated feature notices). Requires file access to datadir or access via a cloud API. |

---

## Implementation Status — Summary

All original top-10 items are **IMPLEMENTED**, plus 15 additional items beyond the original scope. The tool now covers 25 of the 35 catalog items.

| ID | Name | Status | Notes |
|---|---|---|---|
| A1 | InnoDB buffer pool sizing | **IMPLEMENTED** | Config + hit ratio + trending graphs |
| A2 | Redo log / checkpoint age | **IMPLEMENTED** | Checkpoint age graph with trending |
| A3 | InnoDB flush method | **IMPLEMENTED** | Durability & Logging table |
| A4 | Durability settings audit | **IMPLEMENTED** | Durability & Logging table |
| A5 | Tablespace layout / ibdata1 | **IMPLEMENTED** | Schema section |
| A7 | Buffer pool dump/load | **IMPLEMENTED** | Buffer Pool table |
| A9 | InnoDB fragmentation | **IMPLEMENTED** | Schema section (>100MB free) |
| A10 | InnoDB History List Length | **IMPLEMENTED** | Graph (`history_list`) |
| B1 | Memory consumption estimation | **IMPLEMENTED** | Server overview |
| B2 | max_connections analysis | **IMPLEMENTED** | Connections table + graph |
| B3 | Temp tables to disk ratio | **IMPLEMENTED** | Performance counters |
| C1 | Swap usage detection | **IMPLEMENTED** | OS metrics + swap graph |
| C8 | Disk I/O response time | **IMPLEMENTED** | Graph (`os_disk_latency`) |
| C9 | OS metric trending | **IMPLEMENTED** | `sample_os_metrics()` in collector |
| C10 | OS/MariaDB correlation graphs | **IMPLEMENTED** | 5 OS graphs in report |
| D1 | MyISAM detection | **IMPLEMENTED** | Engine summary |
| D2–D6 | Schema analysis (PK, indexes, etc.) | **IMPLEMENTED** | Full schema section |
| E1 | Basic security audit | **IMPLEMENTED** | Security findings table |
| E2 | Identical passwords | **IMPLEMENTED** | MEDIUM finding |
| E3 | Unused accounts | **IMPLEMENTED** | LOW finding (PS-gated) |
| F1 | Unused indexes | **IMPLEMENTED** | Schema section (PS-gated) |
| F6 | Read/write workload ratio | **IMPLEMENTED** | Pie chart in Executive Summary |
| H1 | skip_name_resolve | **IMPLEMENTED** | LOW finding in security |

**Still DEFERRED:** A6 (general tablespaces), A8 (compression), B4–B5 (PS memory, session buffers), C2–C7 (OS-level config), F2–F5 (query analysis, full scans, slow query, response time distribution), G1–G2 (replication monitoring), H2 (connection pooling), I1 (error log)

---

## STEP 2: How the Tool Works

### Architecture

The tool is a **Claude Code skill** — an AI instruction file (`SKILL.md`) backed by a Python data collector (`collect.py`) and a graph generator (`graph.py`). The AI reads the collector's JSON output, interprets it using reference files, and writes a structured HTML+Markdown report.

```
SKILL.md (AI instructions)
    ↓ runs
collect.py --snapshot (one-shot full collection → JSON)
collect.py --daemon   (continuous sampling → JSONL files)
    ↓ data
AI reads JSON, writes HTML report with <!-- GRAPHS:section --> markers
    ↓ runs
graph.py --inject report.html (replaces markers with PNG graphs)
    ↓
Final report opened in browser
```

### Data Collection (`collect.py`, ~1300 lines Python)

A single Python script using the official `mariadb` connector module. Connects once and collects all data in structured JSON sections:

- **`server`** — version, hostname, uptime, OS info (RAM, CPU count, datadir disk)
- **`innodb`** — buffer pool config/stats, durability settings, checkpoint health, log sizing, I/O capacity, adaptive hash index
- **`innodb_status`** — parsed `SHOW ENGINE INNODB STATUS` (mutex contention, history list length, precise buffer pool hit rate)
- **`connections`** — current/peak/max threads, thread cache, aborted connections, table cache config
- **`performance`** — global counters, slow query config, handler stats, joins, sorting, temp tables, Performance Schema digests (top queries by time, inefficient queries, table I/O hotspots, unused indexes)
- **`schema`** — databases, engine summary, top tables, auto-increment fill, duplicate indexes, fragmented tables, tables without PK, tables with non-optimal PK
- **`security`** — user accounts, grants, SSL/TLS, auth plugins, unused accounts (PS-gated), skip_name_resolve
- **`features`** — MariaDB-specific feature detection
- **`replication`** — `SHOW ALL SLAVES STATUS`, binlog format/row image/cache
- **`os`** — OS metrics from `sample_os_metrics()` (Linux: /proc/stat, /proc/meminfo, /proc/diskstats; macOS: vm_stat, sysctl)
- **`status_snapshot`** — full `GLOBAL_STATUS` dump for delta computation

**Two operating modes:**

| Mode | Command | What it does | Output |
|---|---|---|---|
| **Snapshot** | `--snapshot` | Full one-shot collection of all sections | Single JSON file in `snapshots/` |
| **Daemon** | `--daemon --interval N` | Continuous lightweight sampling of STATUS counters + OS metrics every N seconds | JSONL files in `snapshots/samples/` (one file per hour) |

The snapshot mode automatically compares against the most recent previous snapshot from the same server (matched by hostname:port) and produces a `deltas` section with three sub-objects: `rates` (cumulative counter comparisons with `rate_per_sec`), `gauges` (point-in-time metric changes), and `config` (configuration drift).

### Delta System

No within-session wait. The collector takes a single snapshot and computes deltas by comparing to the most recent previous snapshot from the same server. This means:

- **First run:** No deltas available. Report shows current state only.
- **Subsequent runs:** Δ columns appear in all tables showing what changed since the last snapshot.
- **Daemon data:** If the daemon has been running, high-resolution trending data is available for graphs.

The spec originally called for a two-phase collection with a 10-minute in-session wait. This was replaced with between-session deltas because: (1) the user gets results immediately instead of waiting, (2) deltas between runs days/weeks apart reveal meaningful trends that a 10-minute window cannot, (3) daemon mode provides the high-resolution data when needed.

### Graph Generation (`graph.py`, ~570 lines Python)

Generates PNG graphs using seaborn/matplotlib from daemon sample data or snapshot history. Currently defines 16 graphs across 5 sections:

| Section | Graphs | Data source |
|---|---|---|
| **workload** | Read/write ratio pie chart | Com_* counter deltas |
| **performance** | Statement throughput, query latency, slow queries, temp tables | Daemon samples or snapshot history |
| **innodb** | Buffer pool pages, checkpoint age, history list length, I/O throughput, row operations | Daemon samples or snapshot history |
| **connections** | Thread activity | Daemon samples or snapshot history |
| **os** | Memory %, swap, load average, CPU %, disk read/write latency | OS metrics from daemon samples |

Graphs are injected directly into the HTML file via `--inject` mode — the AI never loads base64 data into its context.

### Report Generation

The AI writes both `.md` and `.html` reports using templates in `references/`:

- **`mariadb-audit-template.md`** — Report structure, section order, quality bar, Δ column formatting rules
- **`mariadb-audit-template.html`** — HTML skeleton with all CSS styling
- **Reference files** (`server-overview.md`, `query-optimization.md`, `feature-suggestions.md`, `security-audit.md`) — Internal AI guidance for interpreting data (thresholds and rules used to understand the data, but NOT surfaced as recommendations in the report)

The report is **descriptive, not prescriptive** — it presents what the server IS, not what it should be. The only exception is the Security section, which includes severity ratings and fix suggestions. Every abbreviation is expanded on first use.

### User Experience Flow

```
User: "dba"
  ↓
AI: "How should I connect?" (socket / defaults file / remote)
  ↓
User: picks connection method
  ↓
AI: Connects, checks for previous snapshots
  ↓
If snapshots exist: "Compare to latest from {date}?" (yes / pick different / skip)
  ↓
AI: Runs collector → reads JSON → writes report → injects graphs → opens in browser
  ↓
AI: "Report generated. Dig deeper or done?"
```

Total time: **~2 minutes** from "dba" to report in browser.

### Purpose-Driven Analysis

The tool should ask the user their purpose before collecting data:

**"What brings you here?"**
1. **General overview** (default) — standard comprehensive audit
2. **Investigating a specific problem** — user describes the issue (e.g., "queries are slow since yesterday", "server ran out of connections at 3am")

The report is generated the same way regardless of the answer — all data is collected, all sections are written. But the Executive Summary adapts:

- **General overview:** Standard summary of server state
- **Problem investigation:** The Executive Summary includes an **"AI Suggestions"** subsection that takes the user's problem description into account and highlights which sections of the report are most likely relevant. For example, if the user says "queries are slow since yesterday," the AI Suggestions might point to the buffer pool hit ratio delta, temp tables to disk ratio, and any checkpoint age pressure — connecting the user's symptom to the data.

This framing helps rookies who don't know where to look. The data is identical; only the narrative guidance in the Executive Summary changes.

---

## Implementation Notes

### Technology

- **Language:** Python 3 with the `mariadb` module (requires MariaDB Connector/C at OS level)
- **Graphs:** seaborn + matplotlib (optional — graphs omitted if not installed)
- **Fallback:** If Python or the mariadb module is unavailable, the skill falls back to shell heredoc queries via the `mariadb` CLI client
- **Target:** MariaDB only. The source material is MySQL-focused but all collected data uses MariaDB-compatible queries. MySQL-specific features (e.g., query response time distribution histograms) are marked as "Not available in MariaDB" and deferred.

### OS Metrics

OS metrics are now collected alongside MariaDB status in daemon mode:
- **Linux:** CPU jiffies from `/proc/stat`, memory from `/proc/meminfo`, swap, load from `/proc/loadavg`, disk I/O from `/proc/diskstats`
- **macOS:** memory via `vm_stat`, swap via `sysctl`, load via `os.getloadavg()`

These enable OS/MariaDB correlation graphs — seeing a query latency spike line up with a swap or disk I/O event reveals root causes that MariaDB-only metrics can't explain.

### What's NOT Implemented (and why)

- **Within-session delta wait** — Original spec called for a 10-minute in-session wait with two-phase collection. Replaced with between-session deltas + daemon mode for trending. Simpler, faster, more useful.
- **Query analysis / EXPLAIN plans** (F2–F4) — Requires Performance Schema and workload-specific analysis. Performance Schema digests (top queries by time, unused indexes) are collected; deeper analysis deferred.
- **OS-level config tuning** (C2–C7) — I/O scheduler, filesystem, NUMA, memory allocator. Requires root access; out of scope for a SQL-level tool.
- **Replication monitoring** (G1–G2) — Basic replication status is collected. Continuous lag monitoring deferred.
- **Error log analysis** (I1) — Requires file access to datadir. Deferred.
- **Connection pooling** (H2) — Application-side concern, not server-side.

### Attribution

Primary source material: "MySQL Performance Tuning v19" by Frédéric Descamps (@lefred), March 2021. Supplemented by Lefred Consulting Scripts by Kenny, Fred & Peter Boros. Additional insights from lefred's direct feedback and "a graph a day" blog series (https://lefred.be/?s=a+graph+a+day).
