# Server Overview

Run all queries below in a **single** `mariadb --batch --skip-column-names --force` heredoc invocation. The `--force` flag ensures that if any individual query fails, the remaining queries still execute.

Use `CONCAT()` to produce labeled `key: value` lines that are easy to parse from the output.

**Important:** Status variables (Uptime, Threads_connected, Questions, Slow_queries, etc.) are NOT system variables — they cannot be accessed via `@@global.*`. Always read them from `information_schema.GLOBAL_STATUS`.

```bash
mariadb --batch --skip-column-names --force <<'SQL'

-- Server Identity
SELECT CONCAT('version: ', VERSION());
SELECT CONCAT('hostname: ', @@hostname);
SELECT CONCAT('port: ', @@port);
SELECT CONCAT('datadir: ', @@datadir);
SELECT CONCAT('socket: ', @@socket);

-- Uptime (status variable — must use GLOBAL_STATUS, not @@global)
SELECT CONCAT('uptime_seconds: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'UPTIME';

-- Databases and table counts
SELECT CONCAT('db: ', s.SCHEMA_NAME, ': ', COUNT(t.TABLE_NAME), ' tables')
FROM information_schema.SCHEMATA s
LEFT JOIN information_schema.TABLES t ON t.TABLE_SCHEMA = s.SCHEMA_NAME
GROUP BY s.SCHEMA_NAME
ORDER BY COUNT(t.TABLE_NAME) DESC;

-- Storage engines in use (excluding system schemas)
SELECT CONCAT('engine: ', ENGINE, ': ', COUNT(*), ' tables, ',
    COALESCE(ROUND(SUM(DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 1), 0), ' MB')
FROM information_schema.TABLES
WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys')
  AND TABLE_TYPE = 'BASE TABLE'
GROUP BY ENGINE
ORDER BY COUNT(*) DESC;

-- Key configuration (system variables — @@global is correct here)
SELECT CONCAT('innodb_buffer_pool_size: ',
    ROUND(@@global.innodb_buffer_pool_size / 1024 / 1024), ' MB');
SELECT CONCAT('max_connections: ', @@global.max_connections);
SELECT CONCAT('tmp_table_size: ',
    ROUND(@@global.tmp_table_size / 1024 / 1024, 1), ' MB');
SELECT CONCAT('max_heap_table_size: ',
    ROUND(@@global.max_heap_table_size / 1024 / 1024, 1), ' MB');
SELECT CONCAT('table_open_cache: ', @@global.table_open_cache);
SELECT CONCAT('thread_cache_size: ', @@global.thread_cache_size);

-- Connection metrics (status variables)
SELECT CONCAT('threads_connected: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'THREADS_CONNECTED';
SELECT CONCAT('max_used_connections: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'MAX_USED_CONNECTIONS';

-- Key performance metrics (status variables)
SELECT CONCAT('questions: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'QUESTIONS';
SELECT CONCAT('slow_queries: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SLOW_QUERIES';
SELECT CONCAT('aborted_connects: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'ABORTED_CONNECTS';
SELECT CONCAT('aborted_clients: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'ABORTED_CLIENTS';

-- InnoDB buffer pool (status variables)
SELECT CONCAT('innodb_bp_read_requests: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'INNODB_BUFFER_POOL_READ_REQUESTS';
SELECT CONCAT('innodb_bp_reads: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'INNODB_BUFFER_POOL_READS';

-- Replication status
SHOW ALL SLAVES STATUS\G

SQL
```

## Interpreting the results

- **InnoDB buffer pool hit ratio:** `(read_requests - reads) / read_requests * 100`. Healthy is above 99%.
- **Connection utilization:** `threads_connected / max_connections * 100`. Above 80% is a warning.
- **Slow query ratio:** `slow_queries / questions * 100`. Any non-trivial ratio deserves mention.
- **Aborted connects:** A high number relative to uptime suggests authentication failures or network issues.
- **Replication:** If `SHOW ALL SLAVES STATUS` returns no rows, the server is not configured as a replica.
