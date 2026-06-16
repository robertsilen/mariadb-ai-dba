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
SELECT CONCAT('innodb_buffer_pool_instances: ', @@global.innodb_buffer_pool_instances);
SELECT CONCAT('innodb_log_file_size: ',
    ROUND(@@global.innodb_log_file_size / 1024 / 1024), ' MB');
SELECT CONCAT('innodb_log_buffer_size: ',
    ROUND(@@global.innodb_log_buffer_size / 1024 / 1024), ' MB');
SELECT CONCAT('innodb_flush_log_at_trx_commit: ', @@global.innodb_flush_log_at_trx_commit);
SELECT CONCAT('innodb_flush_method: ', @@global.innodb_flush_method);
SELECT CONCAT('innodb_doublewrite: ', @@global.innodb_doublewrite);
SELECT CONCAT('sync_binlog: ', @@global.sync_binlog);
SELECT CONCAT('innodb_buffer_pool_dump_at_shutdown: ', @@global.innodb_buffer_pool_dump_at_shutdown);
SELECT CONCAT('innodb_buffer_pool_load_at_startup: ', @@global.innodb_buffer_pool_load_at_startup);
SELECT CONCAT('max_connections: ', @@global.max_connections);
SELECT CONCAT('tmp_table_size: ',
    ROUND(@@global.tmp_table_size / 1024 / 1024, 1), ' MB');
SELECT CONCAT('max_heap_table_size: ',
    ROUND(@@global.max_heap_table_size / 1024 / 1024, 1), ' MB');
SELECT CONCAT('table_open_cache: ', @@global.table_open_cache);
SELECT CONCAT('thread_cache_size: ', @@global.thread_cache_size);
SELECT CONCAT('skip_name_resolve: ', @@global.skip_name_resolve);

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
SELECT CONCAT('innodb_bp_pages_total: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'INNODB_BUFFER_POOL_PAGES_TOTAL';
SELECT CONCAT('innodb_bp_pages_data: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'INNODB_BUFFER_POOL_PAGES_DATA';
SELECT CONCAT('innodb_bp_pages_dirty: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'INNODB_BUFFER_POOL_PAGES_DIRTY';
SELECT CONCAT('innodb_bp_pages_free: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'INNODB_BUFFER_POOL_PAGES_FREE';

-- Query performance counters (status variables)
SELECT CONCAT('created_tmp_tables: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'CREATED_TMP_TABLES';
SELECT CONCAT('created_tmp_disk_tables: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'CREATED_TMP_DISK_TABLES';
SELECT CONCAT('select_scan: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SELECT_SCAN';
SELECT CONCAT('select_full_join: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SELECT_FULL_JOIN';
SELECT CONCAT('sort_merge_passes: ', VARIABLE_VALUE)
FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'SORT_MERGE_PASSES';

-- Replication status
SHOW ALL SLAVES STATUS\G

SQL
```

## OS-level context

Run these **before** the SQL heredoc in a separate Bash call. They provide system context needed to evaluate whether configuration values (e.g. buffer pool size) are appropriate for the hardware.

The commands differ by platform. Detect the platform and run the appropriate set.

### macOS and Linux

```bash
OS=$(uname -s)
echo "os: $OS $(uname -r) $(uname -m)"

if [ "$OS" = "Darwin" ]; then
    echo "ram_bytes: $(sysctl -n hw.memsize)"
    echo "cpu_cores: $(sysctl -n hw.ncpu)"
    echo "cpu_model: $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
elif [ "$OS" = "Linux" ]; then
    echo "ram_bytes: $(awk '/MemTotal/ {print $2 * 1024}' /proc/meminfo)"
    echo "cpu_cores: $(nproc)"
    echo "cpu_model: $(awk -F': ' '/model name/ {print $2; exit}' /proc/cpuinfo)"
    echo "swappiness: $(cat /proc/sys/vm/swappiness 2>/dev/null || echo unknown)"
fi
```

### Windows

If `uname` is not available or returns `MINGW`/`MSYS`/`CYGWIN`, use PowerShell instead:

```powershell
Write-Output "os: Windows $([System.Environment]::OSVersion.Version)"
Write-Output "ram_bytes: $((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory)"
Write-Output "cpu_cores: $((Get-CimInstance Win32_Processor).NumberOfLogicalProcessors)"
Write-Output "cpu_model: $((Get-CimInstance Win32_Processor).Name)"
```

Run this via: `powershell -NoProfile -Command "..."` if the Bash tool is available, or directly if running in a PowerShell environment.

### Disk space

After the SQL queries have run and you know `@@datadir`, check disk space for the datadir:

- **macOS / Linux:** `df -h /path/to/datadir`
- **Windows:** `powershell -NoProfile -Command "Get-PSDrive -Name (Split-Path -Qualifier 'C:\path\to\datadir').TrimEnd(':')"`

Substitute the actual `@@datadir` value returned by the SQL queries.

Use these values in the summary to contextualize recommendations — e.g. "buffer pool is 128 MB on a machine with 8 GB RAM; could safely increase to 2-4 GB" or "datadir disk is 87% full."

## Interpreting the results

- **InnoDB buffer pool hit ratio:** `(read_requests - reads) / read_requests * 100`. Healthy is above 99%.
- **Buffer pool pages:** Free < 5% of total = pool is undersized. Free > 50% = pool is oversized (wasting RAM).
- **Buffer pool instances:** Recommend 1 instance per GB for pools larger than 1 GB (reduces contention). Note: this variable was removed in MariaDB 11.x+ (single instance is used internally). If the query fails, skip this metric.
- **Buffer pool dump/load:** Both `innodb_buffer_pool_dump_at_shutdown` and `innodb_buffer_pool_load_at_startup` should be ON to preserve a warm cache across restarts.
- **innodb_flush_log_at_trx_commit:** 1 = full ACID compliance (safest), 2 = flush to OS once per second (faster, small data-loss window), 0 = risky (up to 1 second of lost transactions on crash).
- **sync_binlog:** 1 = safest (every transaction synced), 0 = fast but risk of binlog/data divergence on crash.
- **innodb_flush_method:** O_DIRECT is recommended on Linux to avoid double-buffering between InnoDB and the OS page cache.
- **innodb_doublewrite:** Should be ON unless the storage has battery-backed write cache. Protects against torn pages.
- **innodb_log_file_size:** Should be large enough to hold approximately 1 hour of peak write traffic. Too small causes excessive checkpointing.
- **Connection utilization:** `max_used_connections / max_connections * 100`. Above 80% is a warning.
- **Slow query ratio:** `slow_queries / questions * 100`. Any non-trivial ratio deserves mention.
- **Aborted connects:** A high number relative to uptime suggests authentication failures or network issues.
- **Temp disk ratio:** `created_tmp_disk_tables / created_tmp_tables * 100`. Above 25% suggests tmp_table_size or max_heap_table_size should be increased, or queries are creating large temp tables with TEXT/BLOB columns (which always go to disk).
- **Select_full_join:** Should be 0. Any value > 0 means joins are happening without indexes on the joined columns.
- **Sort_merge_passes:** Values > 0 suggest sort_buffer_size may be too small for the workload.
- **skip_name_resolve:** Should be ON for performance. When OFF, MariaDB does a DNS reverse lookup for every connection.
- **Swappiness (Linux):** Values above 10 are problematic for database servers. Recommend 1-5 to keep database pages in RAM.
- **Replication:** If `SHOW ALL SLAVES STATUS` returns no rows, the server is not configured as a replica.
