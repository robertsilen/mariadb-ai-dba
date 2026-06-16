# Query Optimization

This path has three phases. Run Phase A and Phase B as separate heredocs, then proceed to Phase C interactively.

## Phase A: Global query metrics

Run in a **single** `mariadb --batch --skip-column-names --force` heredoc:

```bash
mariadb --batch --skip-column-names --force <<'SQL'

-- Slow query configuration
SELECT CONCAT('slow_query_log: ', @@global.slow_query_log);
SELECT CONCAT('long_query_time: ', @@global.long_query_time);
SELECT CONCAT('log_queries_not_using_indexes: ', @@global.log_queries_not_using_indexes);
SELECT CONCAT('log_slow_admin_statements: ', @@global.log_slow_admin_statements);

-- Uptime for rate calculations
SELECT CONCAT('uptime: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'UPTIME';

-- Total queries baseline
SELECT CONCAT('questions: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'QUESTIONS';

-- Join statistics
SELECT CONCAT('select_scan: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SELECT_SCAN';
SELECT CONCAT('select_full_join: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SELECT_FULL_JOIN';
SELECT CONCAT('select_range: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SELECT_RANGE';
SELECT CONCAT('select_full_range_join: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SELECT_FULL_RANGE_JOIN';

-- Sort statistics
SELECT CONCAT('sort_merge_passes: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SORT_MERGE_PASSES';
SELECT CONCAT('sort_scan: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SORT_SCAN';
SELECT CONCAT('sort_range: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SORT_RANGE';
SELECT CONCAT('sort_rows: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SORT_ROWS';

-- Temporary table statistics
SELECT CONCAT('created_tmp_tables: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'CREATED_TMP_TABLES';
SELECT CONCAT('created_tmp_disk_tables: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'CREATED_TMP_DISK_TABLES';
SELECT CONCAT('tmp_table_size: ', ROUND(@@global.tmp_table_size / 1024 / 1024, 1), ' MB');
SELECT CONCAT('max_heap_table_size: ', ROUND(@@global.max_heap_table_size / 1024 / 1024, 1), ' MB');

-- Handler statistics (row access patterns)
SELECT CONCAT('handler_read_first: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'HANDLER_READ_FIRST';
SELECT CONCAT('handler_read_key: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'HANDLER_READ_KEY';
SELECT CONCAT('handler_read_next: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'HANDLER_READ_NEXT';
SELECT CONCAT('handler_read_rnd_next: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'HANDLER_READ_RND_NEXT';

-- Slow queries count
SELECT CONCAT('slow_queries: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SLOW_QUERIES';

-- Sort buffer and join buffer sizes
SELECT CONCAT('sort_buffer_size: ', ROUND(@@global.sort_buffer_size / 1024), ' KB');
SELECT CONCAT('join_buffer_size: ', ROUND(@@global.join_buffer_size / 1024), ' KB');
SELECT CONCAT('read_buffer_size: ', ROUND(@@global.read_buffer_size / 1024), ' KB');
SELECT CONCAT('read_rnd_buffer_size: ', ROUND(@@global.read_rnd_buffer_size / 1024), ' KB');

-- Performance schema availability
SELECT CONCAT('performance_schema: ', @@global.performance_schema);

SQL
```

### Interpreting Phase A

- **Select_full_join > 0:** Joins are happening without indexes on the joined columns. High priority to investigate.
- **Sort_merge_passes > 0:** Sorts are exceeding sort_buffer_size and spilling to disk. May need larger sort_buffer_size or query rewriting.
- **Temp disk ratio:** `created_tmp_disk_tables / created_tmp_tables * 100`. Above 25% means many temp tables go to disk — increase tmp_table_size/max_heap_table_size, or check for TEXT/BLOB columns in GROUP BY (these always force disk temp tables).
- **Handler_read_rnd_next >> Handler_read_key:** The server is doing far more full scans than index lookups. Indicates missing indexes.
- **Slow query log OFF:** Recommend enabling it. Without it, there is no visibility into slow queries.
- **long_query_time = 10 (default):** Lower to 1-2 seconds to catch more queries.

Calculate rates by dividing counters by uptime_seconds. Present both absolute values and per-second rates.

---

## Phase B: Schema analysis

Run in a **single** `mariadb --batch --skip-column-names --force` heredoc:

```bash
mariadb --batch --skip-column-names --force <<'SQL'

-- Tables without a primary key (excluding system schemas)
SELECT CONCAT('no_pk: ', t.TABLE_SCHEMA, '.', t.TABLE_NAME,
    ' engine=', t.ENGINE, ' rows=', t.TABLE_ROWS)
FROM information_schema.TABLES t
LEFT JOIN information_schema.TABLE_CONSTRAINTS c
    ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
    AND c.TABLE_NAME = t.TABLE_NAME
    AND c.CONSTRAINT_TYPE = 'PRIMARY KEY'
WHERE t.TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
  AND t.TABLE_TYPE = 'BASE TABLE'
  AND c.CONSTRAINT_NAME IS NULL
ORDER BY t.TABLE_ROWS DESC;

-- Tables with non-optimal primary keys (TEXT/BLOB/large CHAR PKs)
SELECT CONCAT('nonoptimal_pk: ', c.TABLE_SCHEMA, '.', c.TABLE_NAME,
    ' column=', c.COLUMN_NAME, ' type=', c.COLUMN_TYPE,
    ' rows=', t.TABLE_ROWS)
FROM information_schema.COLUMNS c
JOIN information_schema.TABLE_CONSTRAINTS tc
    ON tc.TABLE_SCHEMA = c.TABLE_SCHEMA
    AND tc.TABLE_NAME = c.TABLE_NAME
    AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
JOIN information_schema.KEY_COLUMN_USAGE kcu
    ON kcu.TABLE_SCHEMA = c.TABLE_SCHEMA
    AND kcu.TABLE_NAME = c.TABLE_NAME
    AND kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
    AND kcu.COLUMN_NAME = c.COLUMN_NAME
JOIN information_schema.TABLES t
    ON t.TABLE_SCHEMA = c.TABLE_SCHEMA
    AND t.TABLE_NAME = c.TABLE_NAME
WHERE c.TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
  AND (c.DATA_TYPE IN ('text', 'blob', 'mediumtext', 'mediumblob', 'longtext', 'longblob')
       OR (c.DATA_TYPE = 'char' AND c.CHARACTER_MAXIMUM_LENGTH > 36)
       OR (c.DATA_TYPE = 'varchar' AND c.CHARACTER_MAXIMUM_LENGTH > 255))
  AND t.TABLE_ROWS > 1000
ORDER BY t.TABLE_ROWS DESC;

-- Auto-increment fill ratio
SELECT CONCAT('autoinc_fill: ', TABLE_SCHEMA, '.', TABLE_NAME,
    ' column=', COLUMN_NAME, ' type=', COLUMN_TYPE,
    ' max_value=', AUTO_INCREMENT,
    ' type_max=', CASE DATA_TYPE
        WHEN 'tinyint' THEN IF(COLUMN_TYPE LIKE '%unsigned%', 255, 127)
        WHEN 'smallint' THEN IF(COLUMN_TYPE LIKE '%unsigned%', 65535, 32767)
        WHEN 'mediumint' THEN IF(COLUMN_TYPE LIKE '%unsigned%', 16777215, 8388607)
        WHEN 'int' THEN IF(COLUMN_TYPE LIKE '%unsigned%', 4294967295, 2147483647)
        WHEN 'bigint' THEN IF(COLUMN_TYPE LIKE '%unsigned%', 18446744073709551615, 9223372036854775807)
        ELSE 0 END)
FROM information_schema.TABLES t
JOIN information_schema.COLUMNS c
    ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
    AND c.TABLE_NAME = t.TABLE_NAME
    AND c.EXTRA LIKE '%auto_increment%'
WHERE t.TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
  AND t.AUTO_INCREMENT IS NOT NULL
  AND t.AUTO_INCREMENT > 0
ORDER BY t.AUTO_INCREMENT / CASE c.DATA_TYPE
    WHEN 'tinyint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 255, 127)
    WHEN 'smallint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 65535, 32767)
    WHEN 'mediumint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 16777215, 8388607)
    WHEN 'int' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 4294967295, 2147483647)
    WHEN 'bigint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 18446744073709551615, 9223372036854775807)
    ELSE 1 END DESC
LIMIT 20;

-- Top 10 largest tables
SELECT CONCAT('large_table: ', TABLE_SCHEMA, '.', TABLE_NAME,
    ' engine=', ENGINE,
    ' rows=', TABLE_ROWS,
    ' data_mb=', ROUND(DATA_LENGTH / 1024 / 1024, 1),
    ' index_mb=', ROUND(INDEX_LENGTH / 1024 / 1024, 1),
    ' free_mb=', ROUND(DATA_FREE / 1024 / 1024, 1))
FROM information_schema.TABLES
WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
  AND TABLE_TYPE = 'BASE TABLE'
ORDER BY DATA_LENGTH + INDEX_LENGTH DESC
LIMIT 10;

-- Duplicate/redundant indexes (shorter index is a left-prefix of longer one)
SELECT CONCAT('redundant_index: ', s1.TABLE_SCHEMA, '.', s1.TABLE_NAME,
    ' index=', s1.INDEX_NAME, ' (', GROUP_CONCAT(DISTINCT s1.COLUMN_NAME ORDER BY s1.SEQ_IN_INDEX), ')',
    ' redundant_with=', s2.INDEX_NAME, ' (', s2.cols, ')')
FROM information_schema.STATISTICS s1
JOIN (
    SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME,
        GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) as cols
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
    GROUP BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME
) s2
    ON s2.TABLE_SCHEMA = s1.TABLE_SCHEMA
    AND s2.TABLE_NAME = s1.TABLE_NAME
    AND s2.INDEX_NAME != s1.INDEX_NAME
WHERE s1.TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
GROUP BY s1.TABLE_SCHEMA, s1.TABLE_NAME, s1.INDEX_NAME, s2.INDEX_NAME, s2.cols
HAVING GROUP_CONCAT(DISTINCT s1.COLUMN_NAME ORDER BY s1.SEQ_IN_INDEX)
    = SUBSTRING(s2.cols, 1, LENGTH(GROUP_CONCAT(DISTINCT s1.COLUMN_NAME ORDER BY s1.SEQ_IN_INDEX)));

-- Tables with high fragmentation (DATA_FREE > 100 MB)
SELECT CONCAT('fragmented: ', TABLE_SCHEMA, '.', TABLE_NAME,
    ' data_mb=', ROUND(DATA_LENGTH / 1024 / 1024, 1),
    ' free_mb=', ROUND(DATA_FREE / 1024 / 1024, 1),
    ' frag_pct=', ROUND(DATA_FREE / (DATA_LENGTH + 1) * 100, 1))
FROM information_schema.TABLES
WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
  AND TABLE_TYPE = 'BASE TABLE'
  AND DATA_FREE > 104857600
ORDER BY DATA_FREE DESC;

-- Tables >10k rows with no secondary indexes (only PK or no indexes at all)
SELECT CONCAT('no_secondary_idx: ', t.TABLE_SCHEMA, '.', t.TABLE_NAME,
    ' rows=', t.TABLE_ROWS,
    ' data_mb=', ROUND(t.DATA_LENGTH / 1024 / 1024, 1))
FROM information_schema.TABLES t
WHERE t.TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
  AND t.TABLE_TYPE = 'BASE TABLE'
  AND t.TABLE_ROWS > 10000
  AND (SELECT COUNT(DISTINCT INDEX_NAME) FROM information_schema.STATISTICS s
       WHERE s.TABLE_SCHEMA = t.TABLE_SCHEMA AND s.TABLE_NAME = t.TABLE_NAME
       AND s.INDEX_NAME != 'PRIMARY') = 0
ORDER BY t.TABLE_ROWS DESC;

-- Unused indexes (only if performance_schema is enabled)
SELECT CONCAT('unused_index: ', object_schema, '.', object_name,
    ' index=', index_name)
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE index_name IS NOT NULL
  AND index_name != 'PRIMARY'
  AND count_star = 0
  AND object_schema NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY object_schema, object_name, index_name;

SQL
```

### Interpreting Phase B

- **No primary key:** InnoDB uses a hidden 6-byte row ID when no PK exists. This hurts range scans, causes fragmentation, and can break row-based replication. Always recommend adding a PK.
- **Non-optimal PK:** TEXT/BLOB PKs are stored off-page, making every secondary index lookup expensive. Large VARCHAR PKs bloat every secondary index. Recommend switching to INT/BIGINT AUTO_INCREMENT.
- **Auto-increment fill > 75%:** The table will fail to insert once the maximum is reached. For INT columns near the limit, ALTER to BIGINT before overflow.
- **Duplicate indexes:** The shorter index is fully covered by the longer one. The shorter index wastes disk, memory in the buffer pool, and slows writes (two indexes to maintain instead of one). Safe to DROP the shorter one.
- **Fragmentation:** DATA_FREE > 20% of DATA_LENGTH suggests the table would benefit from `OPTIMIZE TABLE` (online for InnoDB, blocks writes for MyISAM/Aria).
- **No secondary indexes on large tables:** Tables with >10k rows and only a PK almost certainly need secondary indexes for query patterns.
- **Unused indexes (performance_schema):** Indexes with zero reads since the last server restart. Confirm the server has been running long enough to represent the full workload before dropping.

---

## Phase D: Performance Schema deep profiling (conditional)

**Only run this phase if `performance_schema` was reported as ON in Phase A.** If it is OFF, skip this phase entirely — the queries will fail.

If OFF, include a recommendation in the findings explaining what the user gains by enabling it (see interpretation section below).

Run in a **single** `mariadb --batch --skip-column-names --force` heredoc:

```bash
mariadb --batch --skip-column-names --force <<'SQL'

-- Top 20 queries by total execution time
SELECT CONCAT('digest_by_time: ',
    LEFT(DIGEST_TEXT, 200),
    ' ||executions=', COUNT_STAR,
    ' ||total_sec=', ROUND(SUM_TIMER_WAIT / 1000000000000, 3),
    ' ||avg_ms=', ROUND(AVG_TIMER_WAIT / 1000000000, 2),
    ' ||rows_examined=', SUM_ROWS_EXAMINED,
    ' ||rows_sent=', SUM_ROWS_SENT,
    ' ||tmp_disk_tables=', SUM_CREATED_TMP_DISK_TABLES,
    ' ||no_index_used=', SUM_NO_INDEX_USED,
    ' ||first_seen=', FIRST_SEEN,
    ' ||last_seen=', LAST_SEEN)
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST_TEXT IS NOT NULL
  AND SCHEMA_NAME NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY SUM_TIMER_WAIT DESC
LIMIT 20;

-- Top 20 most inefficient queries (rows examined >> rows sent)
SELECT CONCAT('digest_inefficient: ',
    LEFT(DIGEST_TEXT, 200),
    ' ||executions=', COUNT_STAR,
    ' ||rows_examined=', SUM_ROWS_EXAMINED,
    ' ||rows_sent=', SUM_ROWS_SENT,
    ' ||ratio=', ROUND(SUM_ROWS_EXAMINED / SUM_ROWS_SENT, 1),
    ' ||total_sec=', ROUND(SUM_TIMER_WAIT / 1000000000000, 3))
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST_TEXT IS NOT NULL
  AND SUM_ROWS_SENT > 0
  AND SUM_ROWS_EXAMINED / SUM_ROWS_SENT > 10
  AND SCHEMA_NAME NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY SUM_ROWS_EXAMINED / SUM_ROWS_SENT DESC
LIMIT 20;

-- Table I/O hotspots — top 10 by total wait time
SELECT CONCAT('table_io: ', OBJECT_SCHEMA, '.', OBJECT_NAME,
    ' ||reads=', COUNT_READ,
    ' ||writes=', COUNT_WRITE,
    ' ||total_sec=', ROUND(SUM_TIMER_WAIT / 1000000000000, 3))
FROM performance_schema.table_io_waits_summary_by_table
WHERE OBJECT_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY SUM_TIMER_WAIT DESC
LIMIT 10;

-- Top 10 most-used indexes (validates which indexes are worth keeping)
SELECT CONCAT('index_hot: ', OBJECT_SCHEMA, '.', OBJECT_NAME,
    ' index=', INDEX_NAME,
    ' ||reads=', COUNT_READ,
    ' ||writes=', COUNT_WRITE,
    ' ||total_sec=', ROUND(SUM_TIMER_WAIT / 1000000000000, 3))
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE INDEX_NAME IS NOT NULL
  AND OBJECT_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY COUNT_READ + COUNT_WRITE DESC
LIMIT 10;

-- Unused indexes (already in Phase B, but repeated here for completeness if Phase B skipped it)
SELECT CONCAT('index_unused: ', object_schema, '.', object_name,
    ' index=', index_name)
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE index_name IS NOT NULL
  AND index_name != 'PRIMARY'
  AND count_star = 0
  AND object_schema NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY object_schema, object_name, index_name;

SQL
```

### Interpreting Phase D

- **Timer values** are in picoseconds (10⁻¹² seconds). Divide by 1,000,000,000,000 for seconds, by 1,000,000,000 for milliseconds. The queries above already do this conversion.
- **Statement digests** normalize SQL by replacing literal values with `?` — the text shown is a query pattern, not a specific query instance. Multiple queries with different WHERE values but the same structure share one digest row.
- **`DIGEST_TEXT` truncation:** Queries longer than `performance_schema_max_digest_length` (default 1024 bytes) are truncated. The output further truncates to 200 chars for readability — mention this when presenting.
- **`SUM_NO_INDEX_USED > 0`** = executions where no index was used at all. Cross-reference with Phase B schema analysis to suggest indexes.
- **`SUM_CREATED_TMP_DISK_TABLES > 0`** = query patterns forcing temporary tables to disk. Check for TEXT/BLOB in GROUP BY or large result sets.
- **Rows examined >> rows sent** (ratio > 10) = the query reads far more rows than it returns, indicating missing or suboptimal indexes.
- **Data is cumulative** since the last server restart or since `TRUNCATE` of the `events_statements_summary_by_digest` table.
- **Uptime matters:** If uptime is less than 24 hours, note that digest data may not represent the full workload cycle (e.g. nightly batch jobs, peak business hours). Ideally let data accumulate for a full business cycle before drawing conclusions.
- **Top-used indexes** validate that existing indexes are actually serving queries — useful context when deciding whether to add more.

### If Performance Schema is OFF

Include this recommendation in findings (MEDIUM severity):

> **Performance Schema is not enabled.** Enable it to unlock statement-level profiling — the most powerful tool for identifying slow queries and optimization targets.
>
> **What you gain:**
> - **Statement digest analysis** — see exactly which query patterns consume the most time, examine the most rows, and create temp tables on disk
> - **Index usage tracking** — identify truly unused indexes (safe to drop) vs heavily used ones
> - **Table I/O profiling** — which tables are the I/O hotspots
>
> **Overhead:** Modest memory increase and approximately 5% CPU in typical workloads. The server allocates memory for instrumentation structures at startup.
>
> **How to enable:**
> ```ini
> [mysqld]
> performance_schema = ON
> ```
> Restart the server. Let data accumulate for at least 24 hours (ideally a full business cycle) before running the audit again for meaningful results.

---

## Phase C: Interactive analysis

After presenting Phase A, B, and D (if available) findings, offer the user these options via `AskUserQuestion`:

1. **Analyze a specific query** — ask for the SQL, then run `EXPLAIN FORMAT=JSON` against it and interpret the execution plan
2. **Inspect a specific table** — run `SHOW CREATE TABLE` and `SHOW INDEX FROM` for the table, analyze index coverage
3. **Done with query optimization** — return to the what-next menu

For each query the user provides:
- Run `EXPLAIN FORMAT=JSON {query}` (read-only — EXPLAIN does not execute the query)
- Look for: `type: ALL` (full scan), `possible_keys: null`, `rows` vs actual result set, `Using filesort`, `Using temporary`, `Using where` without an index
- Suggest specific indexes based on the WHERE, JOIN, ORDER BY, and GROUP BY clauses
- If the query touches a table flagged in Phase B (no PK, no secondary indexes), connect the dots

For table inspection:
- Show the CREATE TABLE output and current indexes
- Cross-reference with Phase B findings
- Suggest specific indexes for common query patterns (look at column names for clues: status, created_at, user_id, email, etc.)
