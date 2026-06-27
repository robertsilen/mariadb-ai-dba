#!/usr/bin/env python3
"""MariaDB data collector for the AI DBA skill.

Two modes:
  --snapshot   One-shot full collection (server, schema, security, features, perf, status)
  --daemon     Continuous GLOBAL_STATUS + OS metrics sampling for time-series trending

Output: JSON to stdout (snapshot) or gzipped JSONL files in snapshots/samples/ (daemon).
"""

import argparse
import gzip
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

mariadb = None  # lazy import in main()

SYSTEM_SCHEMAS = ("information_schema", "performance_schema", "sys", "mysql")
SYSTEM_SCHEMAS_SQL = "('information_schema', 'performance_schema', 'sys', 'mysql')"


def connect(args):
    params = {"autocommit": True}
    if args.socket:
        params["unix_socket"] = args.socket
        if args.user:
            params["user"] = args.user
    elif args.defaults_file:
        params["default_file"] = args.defaults_file
    else:
        if args.host:
            params["host"] = args.host
        if args.port:
            params["port"] = args.port
        if args.user:
            params["user"] = args.user
        if args.password:
            params["passwd"] = args.password
        if getattr(args, "ssl", False):
            params["ssl"] = True
        if getattr(args, "ssl_verify_cert", False):
            params["ssl_verify_cert"] = True
    return mariadb.connect(**params)


def query(cur, sql, *, single_row=False, single_value=False, as_dict=True):
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        if single_value and rows:
            return rows[0][0]
        if single_row and rows:
            return dict(zip(cols, rows[0])) if as_dict else rows[0]
        if as_dict and cols:
            return [dict(zip(cols, r)) for r in rows]
        return rows
    except mariadb.Error as e:
        return {"error": str(e), "query": sql[:200]}


def query_value(cur, sql):
    return query(cur, sql, single_value=True)


def query_var(cur, name):
    return query_value(cur, f"SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = '{name}'")


def query_global(cur, name):
    return query_value(cur, f"SELECT @@global.{name}")


def safe_int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Collection functions
# ---------------------------------------------------------------------------

def collect_server(cur):
    data = {}
    data["version"] = query_value(cur, "SELECT VERSION()")
    data["hostname"] = query_global(cur, "hostname")
    data["port"] = safe_int(query_global(cur, "port"))
    data["datadir"] = query_global(cur, "datadir")
    data["socket"] = query_global(cur, "socket")
    data["uptime_seconds"] = safe_int(query_var(cur, "UPTIME"))

    data["databases"] = query(cur, """
        SELECT s.SCHEMA_NAME as name, COUNT(t.TABLE_NAME) as table_count
        FROM information_schema.SCHEMATA s
        LEFT JOIN information_schema.TABLES t ON t.TABLE_SCHEMA = s.SCHEMA_NAME
        GROUP BY s.SCHEMA_NAME
        ORDER BY COUNT(t.TABLE_NAME) DESC
    """)

    data["engines"] = query(cur, f"""
        SELECT ENGINE as engine, COUNT(*) as table_count,
            COALESCE(ROUND(SUM(DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 1), 0) as size_mb
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
          AND TABLE_TYPE = 'BASE TABLE'
        GROUP BY ENGINE
        ORDER BY COUNT(*) DESC
    """)

    return data


def collect_innodb(cur):
    data = {}

    variables = [
        "innodb_buffer_pool_size", "innodb_buffer_pool_instances",
        "innodb_log_file_size", "innodb_log_buffer_size",
        "innodb_flush_log_at_trx_commit", "innodb_flush_method",
        "innodb_doublewrite", "sync_binlog",
        "innodb_buffer_pool_dump_at_shutdown", "innodb_buffer_pool_load_at_startup",
        "innodb_io_capacity", "innodb_io_capacity_max",
        "innodb_adaptive_hash_index",
        "innodb_flush_neighbors", "innodb_autoinc_lock_mode",
        "innodb_stats_on_metadata", "innodb_lru_scan_depth",
        "innodb_file_per_table",
    ]

    for var in variables:
        val = query_global(cur, var)
        if isinstance(val, dict) and "error" in val:
            continue
        data[var] = val

    for var in ["innodb_adaptive_hash_index_parts", "innodb_adaptive_hash_index_partitions",
                "innodb_numa_interleave", "innodb_sync_array_size"]:
        val = query_global(cur, var)
        if isinstance(val, dict) and "error" in val:
            continue
        data[var] = val

    status_vars = [
        "INNODB_BUFFER_POOL_READ_REQUESTS", "INNODB_BUFFER_POOL_READS",
        "INNODB_BUFFER_POOL_PAGES_TOTAL", "INNODB_BUFFER_POOL_PAGES_DATA",
        "INNODB_BUFFER_POOL_PAGES_DIRTY", "INNODB_BUFFER_POOL_PAGES_FREE",
        "INNODB_BUFFER_POOL_PAGES_MISC", "INNODB_BUFFER_POOL_PAGES_OLD",
        "INNODB_CHECKPOINT_AGE", "INNODB_CHECKPOINT_MAX_AGE",
        "INNODB_HISTORY_LIST_LENGTH",
        "INNODB_BUFFER_POOL_PAGES_FLUSHED", "INNODB_BUFFER_POOL_PAGES_LRU_FLUSHED",
        "INNODB_ROWS_INSERTED", "INNODB_ROWS_UPDATED", "INNODB_ROWS_DELETED", "INNODB_ROWS_READ",
        "INNODB_DATA_READS", "INNODB_DATA_WRITES",
        "INNODB_LOG_WRITES", "INNODB_LOG_WAITS",
        "INNODB_ROW_LOCK_TIME", "INNODB_ROW_LOCK_WAITS",
        "INNODB_DEADLOCKS",
        "INNODB_DATA_PENDING_READS", "INNODB_DATA_PENDING_WRITES", "INNODB_DATA_PENDING_FSYNCS",
        "INNODB_MUTEX_SPIN_WAITS", "INNODB_MUTEX_OS_WAITS",
    ]

    for var in status_vars:
        val = query_var(cur, var)
        if val is not None and not (isinstance(val, dict) and "error" in val):
            data[var.lower()] = val

    return data


def collect_innodb_status(cur):
    try:
        cur.execute("SHOW ENGINE INNODB STATUS")
        row = cur.fetchone()
        if not row:
            return {"error": "No output from SHOW ENGINE INNODB STATUS"}
        raw = row[2] if len(row) > 2 else row[0]
    except mariadb.Error as e:
        return {"error": str(e)}

    data = {"raw_length": len(raw)}

    # Extract mutex/lock contention ("has waited" lines)
    mutex_waits = []
    for line in raw.splitlines():
        if "has waited" in line.lower() or "waited at" in line.lower():
            mutex_waits.append(line.strip())
    if mutex_waits:
        data["mutex_waits"] = mutex_waits[:20]

    # Extract history list length from TRANSACTIONS section
    for line in raw.splitlines():
        if "History list length" in line:
            parts = line.strip().split()
            for p in parts:
                if p.isdigit():
                    data["history_list_length"] = int(p)
                    break
            break

    # Extract buffer pool hit rate from BUFFER POOL AND MEMORY section
    for line in raw.splitlines():
        if "Buffer pool hit rate" in line:
            data["buffer_pool_hit_rate_line"] = line.strip()
            break

    return data


def collect_connections(cur):
    data = {}

    config_vars = [
        "max_connections", "thread_cache_size", "table_open_cache",
        "table_definition_cache", "skip_name_resolve",
        "tmp_table_size", "max_heap_table_size",
    ]
    for var in config_vars:
        val = query_global(cur, var)
        if not (isinstance(val, dict) and "error" in val):
            data[var] = val

    for var in ["table_open_cache_instances"]:
        val = query_global(cur, var)
        if not (isinstance(val, dict) and "error" in val):
            data[var] = val

    status_vars = [
        "THREADS_CONNECTED", "THREADS_RUNNING", "THREADS_CACHED", "THREADS_CREATED",
        "MAX_USED_CONNECTIONS", "CONNECTIONS",
        "ABORTED_CONNECTS", "ABORTED_CLIENTS",
    ]
    for var in status_vars:
        val = query_var(cur, var)
        if val is not None:
            data[var.lower()] = val

    return data


def collect_performance(cur):
    data = {}

    # Slow query config
    for var in ["slow_query_log", "long_query_time",
                "log_queries_not_using_indexes", "log_slow_admin_statements",
                "performance_schema",
                "sort_buffer_size", "join_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size"]:
        val = query_global(cur, var)
        if not (isinstance(val, dict) and "error" in val):
            data[var] = val

    # Global counters
    counter_vars = [
        "QUESTIONS", "QUERIES", "SLOW_QUERIES",
        "COM_SELECT", "COM_INSERT", "COM_UPDATE", "COM_DELETE", "COM_REPLACE",
        "COM_COMMIT", "COM_ROLLBACK", "COM_BEGIN",
        "SELECT_SCAN", "SELECT_FULL_JOIN", "SELECT_RANGE", "SELECT_FULL_RANGE_JOIN",
        "SORT_MERGE_PASSES", "SORT_SCAN", "SORT_RANGE", "SORT_ROWS",
        "CREATED_TMP_TABLES", "CREATED_TMP_DISK_TABLES",
        "HANDLER_READ_FIRST", "HANDLER_READ_KEY", "HANDLER_READ_NEXT",
        "HANDLER_READ_PREV", "HANDLER_READ_RND", "HANDLER_READ_RND_NEXT",
        "HANDLER_UPDATE", "HANDLER_WRITE", "HANDLER_DELETE",
        "TABLE_LOCKS_IMMEDIATE", "TABLE_LOCKS_WAITED",
        "OPEN_TABLES", "OPENED_TABLES",
        "TABLE_OPEN_CACHE_HITS", "TABLE_OPEN_CACHE_MISSES", "TABLE_OPEN_CACHE_OVERFLOWS",
    ]
    counters = {}
    for var in counter_vars:
        val = query_var(cur, var)
        if val is not None:
            counters[var.lower()] = val
    data["counters"] = counters

    # Performance Schema digests (if enabled)
    ps_enabled = data.get("performance_schema", "OFF")
    if str(ps_enabled) in ("ON", "1"):
        data["digest_by_time"] = query(cur, f"""
            SELECT LEFT(DIGEST_TEXT, 200) as digest_text,
                COUNT_STAR as executions,
                ROUND(SUM_TIMER_WAIT / 1000000000000, 3) as total_sec,
                ROUND(AVG_TIMER_WAIT / 1000000000, 2) as avg_ms,
                SUM_ROWS_EXAMINED as rows_examined,
                SUM_ROWS_SENT as rows_sent,
                SUM_CREATED_TMP_DISK_TABLES as tmp_disk_tables,
                SUM_NO_INDEX_USED as no_index_used,
                FIRST_SEEN as first_seen,
                LAST_SEEN as last_seen
            FROM performance_schema.events_statements_summary_by_digest
            WHERE DIGEST_TEXT IS NOT NULL
              AND SCHEMA_NAME NOT IN {SYSTEM_SCHEMAS_SQL}
            ORDER BY SUM_TIMER_WAIT DESC
            LIMIT 20
        """)

        data["digest_inefficient"] = query(cur, f"""
            SELECT LEFT(DIGEST_TEXT, 200) as digest_text,
                COUNT_STAR as executions,
                SUM_ROWS_EXAMINED as rows_examined,
                SUM_ROWS_SENT as rows_sent,
                ROUND(SUM_ROWS_EXAMINED / SUM_ROWS_SENT, 1) as ratio,
                ROUND(SUM_TIMER_WAIT / 1000000000000, 3) as total_sec
            FROM performance_schema.events_statements_summary_by_digest
            WHERE DIGEST_TEXT IS NOT NULL
              AND SUM_ROWS_SENT > 0
              AND SUM_ROWS_EXAMINED / SUM_ROWS_SENT > 10
              AND SCHEMA_NAME NOT IN {SYSTEM_SCHEMAS_SQL}
            ORDER BY SUM_ROWS_EXAMINED / SUM_ROWS_SENT DESC
            LIMIT 20
        """)

        data["table_io"] = query(cur, f"""
            SELECT OBJECT_SCHEMA as schema_name, OBJECT_NAME as table_name,
                COUNT_READ as reads, COUNT_WRITE as writes,
                ROUND(SUM_TIMER_WAIT / 1000000000000, 3) as total_sec
            FROM performance_schema.table_io_waits_summary_by_table
            WHERE OBJECT_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
            ORDER BY SUM_TIMER_WAIT DESC
            LIMIT 10
        """)

        data["index_hot"] = query(cur, f"""
            SELECT OBJECT_SCHEMA as schema_name, OBJECT_NAME as table_name,
                INDEX_NAME as index_name,
                COUNT_READ as reads, COUNT_WRITE as writes,
                ROUND(SUM_TIMER_WAIT / 1000000000000, 3) as total_sec
            FROM performance_schema.table_io_waits_summary_by_index_usage
            WHERE INDEX_NAME IS NOT NULL
              AND OBJECT_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
            ORDER BY COUNT_READ + COUNT_WRITE DESC
            LIMIT 10
        """)

        data["index_unused"] = query(cur, f"""
            SELECT object_schema as schema_name, object_name as table_name,
                index_name
            FROM performance_schema.table_io_waits_summary_by_index_usage
            WHERE index_name IS NOT NULL
              AND index_name != 'PRIMARY'
              AND count_star = 0
              AND object_schema NOT IN {SYSTEM_SCHEMAS_SQL}
            ORDER BY object_schema, object_name, index_name
        """)

    return data


def collect_schema(cur):
    data = {}

    # Tables without a primary key
    data["no_pk"] = query(cur, f"""
        SELECT t.TABLE_SCHEMA as schema_name, t.TABLE_NAME as table_name,
            t.ENGINE as engine, t.TABLE_ROWS as row_count
        FROM information_schema.TABLES t
        LEFT JOIN information_schema.TABLE_CONSTRAINTS c
            ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
            AND c.TABLE_NAME = t.TABLE_NAME
            AND c.CONSTRAINT_TYPE = 'PRIMARY KEY'
        WHERE t.TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
          AND t.TABLE_TYPE = 'BASE TABLE'
          AND c.CONSTRAINT_NAME IS NULL
        ORDER BY t.TABLE_ROWS DESC
    """)

    # Tables with non-optimal primary keys
    data["nonoptimal_pk"] = query(cur, f"""
        SELECT c.TABLE_SCHEMA as schema_name, c.TABLE_NAME as table_name,
            c.COLUMN_NAME as column_name, c.COLUMN_TYPE as column_type,
            t.TABLE_ROWS as row_count
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
        WHERE c.TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
          AND (c.DATA_TYPE IN ('text', 'blob', 'mediumtext', 'mediumblob', 'longtext', 'longblob')
               OR (c.DATA_TYPE = 'char' AND c.CHARACTER_MAXIMUM_LENGTH > 36)
               OR (c.DATA_TYPE = 'varchar' AND c.CHARACTER_MAXIMUM_LENGTH > 255))
          AND t.TABLE_ROWS > 1000
        ORDER BY t.TABLE_ROWS DESC
    """)

    # Auto-increment fill ratio
    data["autoinc_fill"] = query(cur, f"""
        SELECT t.TABLE_SCHEMA as schema_name, t.TABLE_NAME as table_name,
            c.COLUMN_NAME as column_name, c.COLUMN_TYPE as column_type,
            t.AUTO_INCREMENT as current_value,
            CASE c.DATA_TYPE
                WHEN 'tinyint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 255, 127)
                WHEN 'smallint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 65535, 32767)
                WHEN 'mediumint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 16777215, 8388607)
                WHEN 'int' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 4294967295, 2147483647)
                WHEN 'bigint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 18446744073709551615, 9223372036854775807)
                ELSE 0 END as type_max
        FROM information_schema.TABLES t
        JOIN information_schema.COLUMNS c
            ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
            AND c.TABLE_NAME = t.TABLE_NAME
            AND c.EXTRA LIKE '%auto_increment%'
        WHERE t.TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
          AND t.AUTO_INCREMENT IS NOT NULL
          AND t.AUTO_INCREMENT > 0
        ORDER BY t.AUTO_INCREMENT / CASE c.DATA_TYPE
            WHEN 'tinyint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 255, 127)
            WHEN 'smallint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 65535, 32767)
            WHEN 'mediumint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 16777215, 8388607)
            WHEN 'int' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 4294967295, 2147483647)
            WHEN 'bigint' THEN IF(c.COLUMN_TYPE LIKE '%unsigned%', 18446744073709551615, 9223372036854775807)
            ELSE 1 END DESC
        LIMIT 20
    """)

    # Top 10 largest tables
    data["largest_tables"] = query(cur, f"""
        SELECT TABLE_SCHEMA as schema_name, TABLE_NAME as table_name,
            ENGINE as engine, TABLE_ROWS as row_count,
            ROUND(DATA_LENGTH / 1024 / 1024, 1) as data_mb,
            ROUND(INDEX_LENGTH / 1024 / 1024, 1) as index_mb,
            ROUND(DATA_FREE / 1024 / 1024, 1) as free_mb
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
          AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY DATA_LENGTH + INDEX_LENGTH DESC
        LIMIT 10
    """)

    # Duplicate/redundant indexes
    data["redundant_indexes"] = query(cur, f"""
        SELECT s1.TABLE_SCHEMA as schema_name, s1.TABLE_NAME as table_name,
            s1.INDEX_NAME as index_name,
            GROUP_CONCAT(DISTINCT s1.COLUMN_NAME ORDER BY s1.SEQ_IN_INDEX) as index_cols,
            s2.INDEX_NAME as redundant_with,
            s2.cols as redundant_cols
        FROM information_schema.STATISTICS s1
        JOIN (
            SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME,
                GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) as cols
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
            GROUP BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME
        ) s2
            ON s2.TABLE_SCHEMA = s1.TABLE_SCHEMA
            AND s2.TABLE_NAME = s1.TABLE_NAME
            AND s2.INDEX_NAME != s1.INDEX_NAME
        WHERE s1.TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
        GROUP BY s1.TABLE_SCHEMA, s1.TABLE_NAME, s1.INDEX_NAME, s2.INDEX_NAME, s2.cols
        HAVING GROUP_CONCAT(DISTINCT s1.COLUMN_NAME ORDER BY s1.SEQ_IN_INDEX)
            = SUBSTRING(s2.cols, 1, LENGTH(GROUP_CONCAT(DISTINCT s1.COLUMN_NAME ORDER BY s1.SEQ_IN_INDEX)))
    """)

    # Fragmented tables (DATA_FREE > 100 MB)
    data["fragmented"] = query(cur, f"""
        SELECT TABLE_SCHEMA as schema_name, TABLE_NAME as table_name,
            ROUND(DATA_LENGTH / 1024 / 1024, 1) as data_mb,
            ROUND(DATA_FREE / 1024 / 1024, 1) as free_mb,
            ROUND(DATA_FREE / (DATA_LENGTH + 1) * 100, 1) as frag_pct
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
          AND TABLE_TYPE = 'BASE TABLE'
          AND DATA_FREE > 104857600
        ORDER BY DATA_FREE DESC
    """)

    # Tables >10k rows with no secondary indexes
    data["no_secondary_idx"] = query(cur, f"""
        SELECT t.TABLE_SCHEMA as schema_name, t.TABLE_NAME as table_name,
            t.TABLE_ROWS as row_count,
            ROUND(t.DATA_LENGTH / 1024 / 1024, 1) as data_mb
        FROM information_schema.TABLES t
        WHERE t.TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
          AND t.TABLE_TYPE = 'BASE TABLE'
          AND t.TABLE_ROWS > 10000
          AND (SELECT COUNT(DISTINCT INDEX_NAME) FROM information_schema.STATISTICS s
               WHERE s.TABLE_SCHEMA = t.TABLE_SCHEMA AND s.TABLE_NAME = t.TABLE_NAME
               AND s.INDEX_NAME != 'PRIMARY') = 0
        ORDER BY t.TABLE_ROWS DESC
    """)

    return data


def collect_security(cur):
    data = {}

    data["user_accounts"] = query(cur, """
        SELECT user, host,
            IFNULL(plugin, 'none') as plugin,
            IFNULL(ssl_type, 'none') as ssl_type,
            IFNULL(is_role, 'N') as is_role,
            CASE WHEN password = '' AND authentication_string = ''
                 THEN 'YES' ELSE 'NO' END as empty_password
        FROM mysql.user
        WHERE is_role = 'N' OR is_role = '' OR is_role IS NULL
        ORDER BY user, host
    """)

    data["anonymous_accounts"] = query(cur, """
        SELECT user, host FROM mysql.user WHERE user = ''
    """)

    data["nonlocal_root"] = query(cur, """
        SELECT host FROM mysql.user
        WHERE user = 'root'
          AND host NOT IN ('localhost', '127.0.0.1', '::1')
    """)

    data["wildcard_host"] = query(cur, """
        SELECT user, host FROM mysql.user WHERE host = '%'
    """)

    data["empty_password"] = query(cur, """
        SELECT user, host, IFNULL(plugin, 'none') as plugin
        FROM mysql.user
        WHERE (password = '' AND authentication_string = '')
          AND plugin NOT IN ('unix_socket', 'auth_socket', 'auth_pam')
          AND user NOT IN ('', 'mariadb.sys', 'PUBLIC')
          AND (is_role = 'N' OR is_role = '' OR is_role IS NULL)
    """)

    data["shared_passwords"] = query(cur, """
        SELECT GROUP_CONCAT(CONCAT(user, '@', host) SEPARATOR ', ') as accounts,
            COUNT(*) as count
        FROM mysql.user
        WHERE authentication_string != ''
          AND authentication_string IS NOT NULL
          AND plugin NOT IN ('unix_socket', 'auth_socket', 'auth_pam')
        GROUP BY authentication_string
        HAVING COUNT(*) > 1
    """)

    data["all_privs"] = query(cur, """
        SELECT grantee FROM information_schema.USER_PRIVILEGES
        WHERE PRIVILEGE_TYPE = 'ALL PRIVILEGES'
          AND grantee NOT LIKE '''root''%'
          AND grantee NOT LIKE '''mariadb.sys''%'
    """)

    data["admin_privs"] = query(cur, """
        SELECT grantee, PRIVILEGE_TYPE as privilege
        FROM information_schema.USER_PRIVILEGES
        WHERE PRIVILEGE_TYPE IN ('SUPER', 'SHUTDOWN', 'RELOAD', 'PROCESS', 'FILE', 'CREATE USER')
          AND grantee NOT LIKE '''root''%'
          AND grantee NOT LIKE '''mariadb.sys''%'
        ORDER BY grantee, PRIVILEGE_TYPE
    """)

    # SSL/TLS config
    data["have_ssl"] = query_global(cur, "have_ssl")
    data["have_openssl"] = query_global(cur, "have_openssl")
    val = query_global(cur, "require_secure_transport")
    if not (isinstance(val, dict) and "error" in val):
        data["require_secure_transport"] = val

    data["auth_plugin_dist"] = query(cur, """
        SELECT IFNULL(plugin, 'none') as plugin, COUNT(*) as count
        FROM mysql.user
        WHERE is_role = 'N' OR is_role = '' OR is_role IS NULL
        GROUP BY plugin
        ORDER BY COUNT(*) DESC
    """)

    data["test_db_exists"] = query(cur, """
        SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = 'test'
    """)

    data["local_infile"] = query_global(cur, "local_infile")
    data["skip_name_resolve"] = query_global(cur, "skip_name_resolve")

    data["password_plugins"] = query(cur, """
        SELECT PLUGIN_NAME as name, PLUGIN_STATUS as status
        FROM information_schema.PLUGINS
        WHERE PLUGIN_NAME LIKE '%password%' OR PLUGIN_NAME LIKE '%validate%'
    """)

    data["public_table_grants"] = query(cur, """
        SELECT TABLE_SCHEMA as schema_name, TABLE_NAME as table_name,
            PRIVILEGE_TYPE as privilege
        FROM information_schema.TABLE_PRIVILEGES
        WHERE GRANTEE LIKE '''PUBLIC''%'
    """)

    data["public_schema_grants"] = query(cur, """
        SELECT TABLE_SCHEMA as schema_name, PRIVILEGE_TYPE as privilege
        FROM information_schema.SCHEMA_PRIVILEGES
        WHERE GRANTEE LIKE '''PUBLIC''%'
    """)

    ps_enabled = query_global(cur, "performance_schema")
    if str(ps_enabled) in ("ON", "1"):
        data["unused_accounts"] = query(cur, """
            SELECT u.user, u.host
            FROM mysql.user u
            LEFT JOIN performance_schema.accounts a
                ON u.user = a.user AND u.host = a.host
            WHERE (u.is_role = 'N' OR u.is_role = '' OR u.is_role IS NULL)
              AND u.user != ''
              AND u.user NOT IN ('mariadb.sys', 'PUBLIC')
              AND (a.user IS NULL OR a.total_connections = 0)
            ORDER BY u.user, u.host
        """)
    else:
        data["unused_accounts"] = None

    return data


def collect_features(cur):
    data = {}

    data["plugins"] = query(cur, """
        SELECT PLUGIN_NAME as name, PLUGIN_STATUS as status, PLUGIN_TYPE as type
        FROM information_schema.PLUGINS
        WHERE PLUGIN_STATUS = 'ACTIVE'
        ORDER BY PLUGIN_TYPE, PLUGIN_NAME
    """)

    data["table_meta"] = query(cur, f"""
        SELECT TABLE_SCHEMA as schema_name, TABLE_NAME as table_name,
            IFNULL(ENGINE, 'none') as engine,
            IFNULL(ROW_FORMAT, 'none') as row_format,
            IFNULL(TABLE_ROWS, 0) as row_count,
            ROUND(IFNULL(DATA_LENGTH, 0) / 1024 / 1024, 1) as data_mb,
            IFNULL(CREATE_OPTIONS, 'none') as create_options
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
          AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)

    data["columns"] = query(cur, f"""
        SELECT TABLE_SCHEMA as schema_name, TABLE_NAME as table_name,
            COLUMN_NAME as column_name, COLUMN_TYPE as column_type,
            IS_NULLABLE as nullable,
            IFNULL(COLUMN_DEFAULT, 'NULL') as col_default,
            IFNULL(EXTRA, 'none') as extra
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
    """)

    data["indexes"] = query(cur, f"""
        SELECT TABLE_SCHEMA as schema_name, TABLE_NAME as table_name,
            INDEX_NAME as index_name,
            GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) as columns,
            INDEX_TYPE as index_type,
            CASE NON_UNIQUE WHEN 0 THEN 'YES' ELSE 'NO' END as is_unique
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
        GROUP BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, INDEX_TYPE, NON_UNIQUE
        ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME
    """)

    data["check_constraints"] = query(cur, f"""
        SELECT CONSTRAINT_SCHEMA as schema_name, TABLE_NAME as table_name,
            CONSTRAINT_NAME as name, CHECK_CLAUSE as clause
        FROM information_schema.CHECK_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
        ORDER BY CONSTRAINT_SCHEMA, TABLE_NAME
    """)

    data["triggers"] = query(cur, f"""
        SELECT TRIGGER_SCHEMA as schema_name, EVENT_OBJECT_TABLE as table_name,
            TRIGGER_NAME as name, EVENT_MANIPULATION as event,
            ACTION_TIMING as timing
        FROM information_schema.TRIGGERS
        WHERE TRIGGER_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
        ORDER BY TRIGGER_SCHEMA, EVENT_OBJECT_TABLE
    """)

    data["views"] = query(cur, f"""
        SELECT TABLE_SCHEMA as schema_name, TABLE_NAME as name
        FROM information_schema.VIEWS
        WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)

    data["routines"] = query(cur, f"""
        SELECT ROUTINE_SCHEMA as schema_name, ROUTINE_NAME as name,
            ROUTINE_TYPE as type
        FROM information_schema.ROUTINES
        WHERE ROUTINE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
        ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
    """)

    data["system_versioned"] = query(cur, f"""
        SELECT TABLE_SCHEMA as schema_name, TABLE_NAME as table_name
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS_SQL}
          AND CREATE_OPTIONS LIKE '%system_versioning%'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)

    return data


def collect_replication(cur):
    data = {}

    for var in ["binlog_format", "binlog_row_image",
                "binlog_cache_size", "binlog_stmt_cache_size",
                "log_bin"]:
        val = query_global(cur, var)
        if not (isinstance(val, dict) and "error" in val):
            data[var] = val

    for var in ["BINLOG_COMMITS", "BINLOG_GROUP_COMMITS",
                "BINLOG_CACHE_DISK_USE", "BINLOG_CACHE_USE"]:
        val = query_var(cur, var)
        if val is not None:
            data[var.lower()] = val

    # SHOW ALL SLAVES STATUS
    try:
        cur.execute("SHOW ALL SLAVES STATUS")
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        if rows:
            data["slaves"] = [dict(zip(cols, r)) for r in rows]
        else:
            data["slaves"] = []
    except mariadb.Error as e:
        data["slaves_error"] = str(e)

    return data


def collect_status_snapshot(cur):
    result = {}
    try:
        cur.execute("SELECT VARIABLE_NAME, VARIABLE_VALUE FROM information_schema.GLOBAL_STATUS")
        for row in cur.fetchall():
            result[row[0]] = row[1]
    except mariadb.Error as e:
        result["_error"] = str(e)
    return result


def collect_os():
    data = {"platform": platform.system(), "machine": platform.machine()}

    if platform.system() == "Darwin":
        try:
            data["ram_bytes"] = int(subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True).strip())
        except Exception:
            pass
        try:
            data["cpu_cores"] = int(subprocess.check_output(
                ["sysctl", "-n", "hw.ncpu"], text=True).strip())
        except Exception:
            pass
        try:
            data["cpu_model"] = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            pass

    elif platform.system() == "Linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        data["ram_bytes"] = kb * 1024
                        break
        except Exception:
            pass
        try:
            data["cpu_cores"] = os.cpu_count()
        except Exception:
            pass
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        data["cpu_model"] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
        try:
            with open("/proc/sys/vm/swappiness") as f:
                data["swappiness"] = int(f.read().strip())
        except Exception:
            pass

        # Disk scheduler
        try:
            schedulers = {}
            for dev in Path("/sys/block").iterdir():
                sched_path = dev / "queue" / "scheduler"
                if sched_path.exists():
                    content = sched_path.read_text().strip()
                    schedulers[dev.name] = content
            if schedulers:
                data["disk_schedulers"] = schedulers
        except Exception:
            pass

        # NUMA nodes
        try:
            numa_dir = Path("/sys/devices/system/node")
            if numa_dir.exists():
                nodes = [d.name for d in numa_dir.iterdir() if d.name.startswith("node")]
                data["numa_nodes"] = len(nodes)
        except Exception:
            pass

        # MariaDB process swap usage
        try:
            pid = subprocess.check_output(
                ["bash", "-c", "pidof mariadbd || pidof mysqld"],
                text=True, stderr=subprocess.DEVNULL).strip().split()[0]
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmSwap"):
                        kb = int(line.split()[1])
                        data["mariadbd_vmswap_kb"] = kb
                        break
        except Exception:
            pass

        # Memory allocator detection
        try:
            maps_output = subprocess.check_output(
                ["bash", "-c", "cat /proc/$(pidof mariadbd || pidof mysqld)/maps 2>/dev/null | grep -oE '(jemalloc|tcmalloc|libtcmalloc)' | head -1"],
                text=True, stderr=subprocess.DEVNULL).strip()
            if maps_output:
                data["memory_allocator"] = maps_output
        except Exception:
            pass

    return data


def get_disk_free(datadir):
    try:
        st = os.statvfs(datadir)
        return {
            "total_bytes": st.f_frsize * st.f_blocks,
            "free_bytes": st.f_frsize * st.f_bavail,
            "used_pct": round((1 - st.f_bavail / st.f_blocks) * 100, 1) if st.f_blocks else 0,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Snapshot mode
# ---------------------------------------------------------------------------

def run_snapshot(conn, args):
    cur = conn.cursor()

    result = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "collector_version": "1.0",
            "mode": "snapshot",
        },
        "errors": [],
    }

    sections = [
        ("os", lambda: collect_os()),
        ("server", lambda: collect_server(cur)),
        ("innodb", lambda: collect_innodb(cur)),
        ("innodb_status", lambda: collect_innodb_status(cur)),
        ("connections", lambda: collect_connections(cur)),
        ("performance", lambda: collect_performance(cur)),
        ("schema", lambda: collect_schema(cur)),
        ("security", lambda: collect_security(cur)),
        ("features", lambda: collect_features(cur)),
        ("replication", lambda: collect_replication(cur)),
        ("status_snapshot", lambda: collect_status_snapshot(cur)),
    ]

    for name, fn in sections:
        try:
            result[name] = fn()
        except Exception as e:
            result[name] = {}
            result["errors"].append({"section": name, "error": str(e)})

    # Add disk info if we got datadir
    if "server" in result and isinstance(result["server"], dict):
        datadir = result["server"].get("datadir")
        if datadir:
            disk = get_disk_free(datadir)
            if disk:
                result["os"]["disk"] = disk
                result["os"]["disk"]["path"] = datadir

        result["meta"]["hostname"] = result["server"].get("hostname", "unknown")

    # Save snapshot file if --snapshots-dir specified
    if args.snapshots_dir:
        snap_dir = Path(args.snapshots_dir)
        snap_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        snap_path = snap_dir / f"snapshot_{ts}.json"
        with open(snap_path, "w") as f:
            json.dump(result, f, default=str)
        result["meta"]["snapshot_file"] = str(snap_path)

        # Load previous snapshot for delta computation
        if args.compare_to:
            prev = load_snapshot_file(Path(args.compare_to))
        else:
            hostname = result.get("meta", {}).get("hostname", "unknown")
            port = result.get("server", {}).get("port", 3306)
            prev = load_previous_snapshot(snap_dir, snap_path, hostname, port)
        if prev:
            result["deltas"] = compute_deltas(prev, result)
            result["deltas"]["compare_file"] = prev.get("_source_file", "unknown")

    json.dump(result, sys.stdout, indent=2, default=str)
    print()


def load_snapshot_file(path):
    try:
        with open(path) as f:
            data = json.load(f)
        data["_source_file"] = str(path)
        return data
    except Exception:
        return None


def load_previous_snapshot(snap_dir, current_path, hostname, port):
    snapshots = sorted(snap_dir.glob("snapshot_*.json"))
    snapshots = [s for s in snapshots if s != current_path]
    if not snapshots:
        return None
    # Try to find most recent snapshot from the same server
    for s in reversed(snapshots):
        try:
            with open(s) as f:
                data = json.load(f)
            snap_host = data.get("meta", {}).get("hostname", "")
            snap_port = data.get("server", {}).get("port", 3306)
            if snap_host == hostname and snap_port == port:
                data["_source_file"] = str(s)
                return data
        except Exception:
            continue
    return None


def list_snapshots(snap_dir):
    snapshots = sorted(snap_dir.glob("snapshot_*.json"))
    if not snapshots:
        print("No snapshots found.")
        return
    result = []
    for s in snapshots:
        try:
            with open(s) as f:
                data = json.load(f)
            result.append({
                "file": s.name,
                "path": str(s),
                "timestamp": data.get("meta", {}).get("timestamp", "unknown"),
                "hostname": data.get("meta", {}).get("hostname", "unknown"),
                "port": data.get("server", {}).get("port", "unknown"),
                "version": data.get("server", {}).get("version", "unknown"),
            })
        except Exception:
            result.append({"file": s.name, "path": str(s), "error": "unreadable"})
    json.dump(result, sys.stdout, indent=2, default=str)
    print()


def compute_deltas(prev, current):
    deltas = {}
    prev_status = prev.get("status_snapshot", {})
    cur_status = current.get("status_snapshot", {})
    prev_uptime = safe_int(prev_status.get("UPTIME", 0))
    cur_uptime = safe_int(cur_status.get("UPTIME", 0))

    if cur_uptime < prev_uptime:
        deltas["_note"] = "Server restarted between snapshots"
        return deltas

    elapsed = cur_uptime - prev_uptime
    if elapsed <= 0:
        return deltas

    deltas["elapsed_seconds"] = elapsed
    deltas["previous_timestamp"] = prev.get("meta", {}).get("timestamp")

    # --- Rate-based comparisons (cumulative counters) ---
    rate_vars = [
        "QUESTIONS", "QUERIES", "COM_SELECT", "COM_INSERT", "COM_UPDATE", "COM_DELETE",
        "COM_COMMIT", "COM_ROLLBACK",
        "SLOW_QUERIES", "CREATED_TMP_TABLES", "CREATED_TMP_DISK_TABLES",
        "SELECT_SCAN", "SELECT_FULL_JOIN", "SELECT_RANGE", "SORT_MERGE_PASSES",
        "HANDLER_READ_RND_NEXT",
        "INNODB_BUFFER_POOL_READ_REQUESTS", "INNODB_BUFFER_POOL_READS",
        "INNODB_BUFFER_POOL_PAGES_FLUSHED", "INNODB_BUFFER_POOL_PAGES_LRU_FLUSHED",
        "INNODB_ROWS_INSERTED", "INNODB_ROWS_UPDATED", "INNODB_ROWS_DELETED", "INNODB_ROWS_READ",
        "INNODB_DATA_READS", "INNODB_DATA_WRITES",
        "INNODB_LOG_WRITES", "INNODB_LOG_WAITS",
        "INNODB_ROW_LOCK_WAITS", "INNODB_DEADLOCKS",
        "TABLE_LOCKS_WAITED",
        "ABORTED_CONNECTS", "ABORTED_CLIENTS",
        "CONNECTIONS", "THREADS_CREATED",
    ]

    rates = {}
    for var in rate_vars:
        prev_val = safe_int(prev_status.get(var, 0))
        cur_val = safe_int(cur_status.get(var, 0))
        delta = cur_val - prev_val
        rates[var.lower()] = {
            "previous": prev_val,
            "current": cur_val,
            "delta": delta,
            "rate_per_sec": round(delta / elapsed, 6),
        }
    deltas["rates"] = rates

    # --- Gauge comparisons (point-in-time values from status) ---
    gauge_vars = [
        "INNODB_BUFFER_POOL_PAGES_DATA", "INNODB_BUFFER_POOL_PAGES_DIRTY",
        "INNODB_BUFFER_POOL_PAGES_FREE", "INNODB_BUFFER_POOL_PAGES_TOTAL",
        "INNODB_CHECKPOINT_AGE", "INNODB_CHECKPOINT_MAX_AGE",
        "INNODB_HISTORY_LIST_LENGTH",
        "INNODB_DATA_PENDING_READS", "INNODB_DATA_PENDING_WRITES",
        "THREADS_CONNECTED", "THREADS_RUNNING", "THREADS_CACHED",
        "MAX_USED_CONNECTIONS",
        "OPEN_TABLES", "OPENED_TABLES",
    ]
    gauges = {}
    for var in gauge_vars:
        prev_val = prev_status.get(var)
        cur_val = cur_status.get(var)
        if prev_val is not None and cur_val is not None:
            gauges[var.lower()] = {
                "previous": prev_val,
                "current": cur_val,
                "changed": str(prev_val) != str(cur_val),
            }
    deltas["gauges"] = gauges

    # --- Config comparisons (settings that may have been changed) ---
    config_sections = {
        "innodb": [
            "innodb_buffer_pool_size", "innodb_log_file_size", "innodb_log_buffer_size",
            "innodb_flush_log_at_trx_commit", "innodb_flush_method", "innodb_doublewrite",
            "sync_binlog", "innodb_io_capacity", "innodb_io_capacity_max",
            "innodb_adaptive_hash_index", "innodb_flush_neighbors",
            "innodb_autoinc_lock_mode", "innodb_stats_on_metadata",
            "innodb_lru_scan_depth", "innodb_file_per_table",
            "innodb_buffer_pool_dump_at_shutdown", "innodb_buffer_pool_load_at_startup",
        ],
        "connections": [
            "max_connections", "thread_cache_size", "table_open_cache",
            "table_definition_cache", "skip_name_resolve",
            "tmp_table_size", "max_heap_table_size", "table_open_cache_instances",
        ],
        "performance": [
            "slow_query_log", "long_query_time", "log_queries_not_using_indexes",
            "performance_schema", "sort_buffer_size", "join_buffer_size",
            "read_buffer_size", "read_rnd_buffer_size",
        ],
        "security": [
            "require_secure_transport", "local_infile", "have_ssl",
        ],
    }
    config_changes = {}
    for section, keys in config_sections.items():
        prev_section = prev.get(section, {})
        cur_section = current.get(section, {})
        for key in keys:
            prev_val = prev_section.get(key)
            cur_val = cur_section.get(key)
            if prev_val is not None and cur_val is not None:
                config_changes[key] = {
                    "previous": prev_val,
                    "current": cur_val,
                    "changed": str(prev_val) != str(cur_val),
                }
    deltas["config"] = config_changes

    return deltas


# ---------------------------------------------------------------------------
# Daemon mode
# ---------------------------------------------------------------------------

DAEMON_VARS = [
    "COM_SELECT", "COM_INSERT", "COM_UPDATE", "COM_DELETE", "COM_REPLACE",
    "QUESTIONS", "QUERIES",
    "COM_COMMIT", "COM_ROLLBACK", "COM_BEGIN",
    "INNODB_BUFFER_POOL_PAGES_DATA", "INNODB_BUFFER_POOL_PAGES_DIRTY",
    "INNODB_BUFFER_POOL_PAGES_FREE", "INNODB_BUFFER_POOL_PAGES_MISC",
    "INNODB_BUFFER_POOL_PAGES_OLD",
    "INNODB_BUFFER_POOL_READ_REQUESTS", "INNODB_BUFFER_POOL_READS",
    "INNODB_CHECKPOINT_AGE", "INNODB_CHECKPOINT_MAX_AGE",
    "INNODB_HISTORY_LIST_LENGTH",
    "INNODB_BUFFER_POOL_PAGES_FLUSHED", "INNODB_BUFFER_POOL_PAGES_LRU_FLUSHED",
    "INNODB_ROWS_INSERTED", "INNODB_ROWS_UPDATED", "INNODB_ROWS_DELETED", "INNODB_ROWS_READ",
    "INNODB_DATA_READS", "INNODB_DATA_WRITES",
    "INNODB_LOG_WRITES", "INNODB_LOG_WAITS",
    "INNODB_ROW_LOCK_TIME", "INNODB_ROW_LOCK_WAITS",
    "INNODB_DEADLOCKS",
    "INNODB_DATA_PENDING_READS", "INNODB_DATA_PENDING_WRITES", "INNODB_DATA_PENDING_FSYNCS",
    "INNODB_MUTEX_SPIN_WAITS", "INNODB_MUTEX_OS_WAITS",
    "THREADS_CONNECTED", "THREADS_RUNNING", "THREADS_CACHED", "THREADS_CREATED",
    "ABORTED_CONNECTS", "ABORTED_CLIENTS", "CONNECTIONS",
    "MAX_USED_CONNECTIONS",
    "CREATED_TMP_DISK_TABLES", "CREATED_TMP_TABLES",
    "TABLE_LOCKS_IMMEDIATE", "TABLE_LOCKS_WAITED",
    "OPEN_TABLES", "OPENED_TABLES",
    "TABLE_OPEN_CACHE_HITS", "TABLE_OPEN_CACHE_MISSES", "TABLE_OPEN_CACHE_OVERFLOWS",
    "SELECT_SCAN", "SELECT_FULL_JOIN", "SELECT_RANGE", "SELECT_FULL_RANGE_JOIN",
    "SORT_MERGE_PASSES", "SORT_RANGE", "SORT_SCAN", "SORT_ROWS",
    "HANDLER_READ_FIRST", "HANDLER_READ_KEY", "HANDLER_READ_NEXT",
    "HANDLER_READ_PREV", "HANDLER_READ_RND", "HANDLER_READ_RND_NEXT",
    "BINLOG_COMMITS", "BINLOG_GROUP_COMMITS",
    "BINLOG_CACHE_DISK_USE", "BINLOG_CACHE_USE",
    "UPTIME",
]

# Build a WHERE IN clause for efficiency
DAEMON_VARS_SQL = "(" + ",".join(f"'{v}'" for v in DAEMON_VARS) + ")"


def sample_os_metrics():
    """Collect lightweight OS metrics for daemon samples. Fast — no subprocesses."""
    metrics = {}
    system = platform.system()

    if system == "Linux":
        # CPU usage from /proc/stat (raw jiffies — compute rate in graph.py)
        try:
            with open("/proc/stat") as f:
                line = f.readline()  # "cpu  user nice system idle iowait irq softirq ..."
                parts = line.split()
                if parts[0] == "cpu":
                    vals = [int(x) for x in parts[1:]]
                    metrics["os_cpu_user"] = vals[0]
                    metrics["os_cpu_nice"] = vals[1]
                    metrics["os_cpu_system"] = vals[2]
                    metrics["os_cpu_idle"] = vals[3]
                    metrics["os_cpu_iowait"] = vals[4] if len(vals) > 4 else 0
        except Exception:
            pass

        # Memory from /proc/meminfo
        try:
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if parts[0].rstrip(":") in ("MemTotal", "MemAvailable", "MemFree",
                                                  "Buffers", "Cached", "SwapTotal", "SwapFree"):
                        mem[parts[0].rstrip(":")] = int(parts[1]) * 1024  # kB to bytes
            metrics["os_mem_total"] = mem.get("MemTotal", 0)
            metrics["os_mem_available"] = mem.get("MemAvailable", mem.get("MemFree", 0))
            metrics["os_swap_total"] = mem.get("SwapTotal", 0)
            metrics["os_swap_used"] = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
        except Exception:
            pass

        # Load average
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
                metrics["os_load_1m"] = float(parts[0])
                metrics["os_load_5m"] = float(parts[1])
                metrics["os_load_15m"] = float(parts[2])
        except Exception:
            pass

        # Disk I/O from /proc/diskstats (cumulative — compute rates in graph.py)
        # Fields: reads_completed, read_ms, writes_completed, write_ms
        # We pick the device with most I/O (typically sda, nvme0n1, vda)
        try:
            best = None
            with open("/proc/diskstats") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 14:
                        continue
                    dev = parts[2]
                    # Skip partitions (sda1, nvme0n1p1) and loop/ram devices
                    if any(c.isdigit() for c in dev.lstrip("nvme").split("n")[-1].lstrip("0123456789")):
                        if not dev.startswith("nvme") or "p" in dev:
                            continue
                    if dev.startswith(("loop", "ram", "dm-")):
                        continue
                    reads = int(parts[3])
                    writes = int(parts[7])
                    total = reads + writes
                    if best is None or total > best[1]:
                        best = (dev, total, parts)
            if best:
                dev, _, parts = best
                metrics["os_disk_dev"] = dev
                metrics["os_disk_reads"] = int(parts[3])
                metrics["os_disk_read_ms"] = int(parts[6])
                metrics["os_disk_writes"] = int(parts[7])
                metrics["os_disk_write_ms"] = int(parts[10])
                metrics["os_disk_io_ms"] = int(parts[12])
        except Exception:
            pass

    elif system == "Darwin":
        # macOS: use host_statistics via sysctl for memory
        try:
            import resource
            page_size = resource.getpagesize()
            vm = subprocess.check_output(["vm_stat"], text=True, timeout=2)
            vm_data = {}
            for line in vm.strip().split("\n")[1:]:
                if ":" in line:
                    key, val = line.split(":", 1)
                    val = val.strip().rstrip(".")
                    try:
                        vm_data[key.strip()] = int(val)
                    except ValueError:
                        pass
            free = vm_data.get("Pages free", 0)
            active = vm_data.get("Pages active", 0)
            inactive = vm_data.get("Pages inactive", 0)
            speculative = vm_data.get("Pages speculative", 0)
            wired = vm_data.get("Pages wired down", 0)
            compressed = vm_data.get("Pages occupied by compressor", 0)
            total_pages = free + active + inactive + speculative + wired + compressed
            metrics["os_mem_total"] = total_pages * page_size
            metrics["os_mem_available"] = (free + inactive) * page_size
            metrics["os_swap_total"] = 0
            metrics["os_swap_used"] = 0
            # macOS dynamic swap — check sysctl
            try:
                swap_info = subprocess.check_output(
                    ["sysctl", "-n", "vm.swapusage"], text=True, timeout=2).strip()
                # "total = 0.00M  used = 0.00M  free = 0.00M"
                for part in swap_info.split("  "):
                    k, v = part.strip().split(" = ")
                    val_mb = float(v.rstrip("M"))
                    if k == "total":
                        metrics["os_swap_total"] = int(val_mb * 1024 * 1024)
                    elif k == "used":
                        metrics["os_swap_used"] = int(val_mb * 1024 * 1024)
            except Exception:
                pass
        except Exception:
            pass

        # Load average
        try:
            load = os.getloadavg()
            metrics["os_load_1m"] = round(load[0], 2)
            metrics["os_load_5m"] = round(load[1], 2)
            metrics["os_load_15m"] = round(load[2], 2)
        except Exception:
            pass

    return metrics


def check_disk_space(path, min_free_mb=500, min_free_pct=5):
    """Return True if disk has enough free space, False otherwise."""
    try:
        usage = shutil.disk_usage(path)
        free_mb = usage.free / (1024 * 1024)
        free_pct = (usage.free / usage.total) * 100
        if free_mb < min_free_mb or free_pct < min_free_pct:
            print(f"WARNING: Low disk space — {free_mb:.0f} MB free ({free_pct:.1f}%). "
                  f"Minimum: {min_free_mb} MB or {min_free_pct}%.", file=sys.stderr)
            return False
        return True
    except Exception:
        return True


def run_daemon(conn, args):
    cur = conn.cursor()
    interval = args.interval
    snap_dir = Path(args.snapshots_dir or "snapshots")
    samples_dir = snap_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    retention_days = args.retention_days

    if not check_disk_space(samples_dir):
        print("ERROR: Not enough disk space to start daemon. Exiting.", file=sys.stderr)
        return

    running = True

    def handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"MariaDB AI DBA daemon started. Sampling every {interval}s. Writing to {samples_dir}/", file=sys.stderr)
    print(f"Press Ctrl+C to stop.", file=sys.stderr)

    current_hour = None
    current_file = None
    sample_count = 0

    while running:
        ts = time.time()
        hour_key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d_%H")

        # Rotate file on hour change
        if hour_key != current_hour:
            if current_file:
                current_file.close()
            current_hour = hour_key
            filepath = samples_dir / f"samples_{hour_key}.jsonl.gz"
            current_file = gzip.open(filepath, "at", compresslevel=6)

            # Prune old files
            if retention_days > 0:
                prune_old_samples(samples_dir, retention_days)

        # Check disk space every 60 samples
        sample_count += 1
        if sample_count % 60 == 0 and not check_disk_space(samples_dir):
            print("Stopping daemon due to low disk space.", file=sys.stderr)
            break

        # Collect status
        sample = {"ts": int(ts)}
        try:
            cur.execute(f"""
                SELECT VARIABLE_NAME, VARIABLE_VALUE
                FROM information_schema.GLOBAL_STATUS
                WHERE VARIABLE_NAME IN {DAEMON_VARS_SQL}
            """)
            for row in cur.fetchall():
                sample[row[0]] = row[1]
        except mariadb.Error as e:
            sample["_error"] = str(e)
            # Try to reconnect
            try:
                conn.reconnect()
                cur = conn.cursor()
            except Exception:
                pass

        # Collect OS metrics
        sample.update(sample_os_metrics())

        current_file.write(json.dumps(sample, separators=(",", ":")))
        current_file.write("\n")
        current_file.flush()

        # Sleep for the remaining interval
        elapsed = time.time() - ts
        sleep_time = max(0, interval - elapsed)
        if sleep_time > 0 and running:
            time.sleep(sleep_time)

    if current_file:
        current_file.close()

    print("\nDaemon stopped.", file=sys.stderr)


def prune_old_samples(samples_dir, retention_days):
    cutoff = time.time() - retention_days * 86400
    for pattern in ("samples_*.jsonl", "samples_*.jsonl.gz"):
        for f in samples_dir.glob(pattern):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="MariaDB data collector for the AI DBA skill.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Snapshot via local socket
  python3 collect.py --snapshot --socket /tmp/mysql.sock

  # Snapshot via TCP
  python3 collect.py --snapshot --host 127.0.0.1 --port 3306 --user root --password secret

  # Snapshot using defaults file
  python3 collect.py --snapshot --defaults-file ~/.my.cnf

  # Daemon mode (1-second sampling)
  python3 collect.py --daemon --interval 1 --socket /tmp/mysql.sock

  # Save snapshots for trending
  python3 collect.py --snapshot --socket /tmp/mysql.sock --snapshots-dir ./snapshots
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", action="store_true", help="One-shot full data collection")
    mode.add_argument("--daemon", action="store_true", help="Continuous status sampling")
    mode.add_argument("--list-snapshots", action="store_true", help="List all snapshots in --snapshots-dir and exit")

    conn_group = parser.add_argument_group("connection")
    conn_group.add_argument("--socket", help="Unix socket path")
    conn_group.add_argument("--defaults-file", help="Path to .my.cnf defaults file")
    conn_group.add_argument("--host", help="Server hostname")
    conn_group.add_argument("--port", type=int, help="Server port")
    conn_group.add_argument("--user", help="Username")
    conn_group.add_argument("--password", help="Password")
    conn_group.add_argument("--ssl", action="store_true", help="Use SSL/TLS for the connection")
    conn_group.add_argument("--ssl-verify-server-cert", dest="ssl_verify_cert", action="store_true", help="Verify SSL server certificate")

    parser.add_argument("--snapshots-dir", help="Directory to store snapshot/sample files for trending")
    parser.add_argument("--compare-to", help="Path to a specific snapshot file to compute deltas against")
    parser.add_argument("--interval", type=int, default=1, help="Daemon sampling interval in seconds (default: 1)")
    parser.add_argument("--retention-days", type=int, default=7, help="Days to keep daemon sample files (default: 7)")

    args = parser.parse_args()

    if args.list_snapshots:
        if not args.snapshots_dir:
            print("Error: --list-snapshots requires --snapshots-dir", file=sys.stderr)
            sys.exit(1)
        list_snapshots(Path(args.snapshots_dir))
        sys.exit(0)

    global mariadb
    try:
        import mariadb as _mariadb
        mariadb = _mariadb
    except ImportError:
        print(
            "Error: the 'mariadb' Python module is not installed.\n"
            "Install it with: pip install mariadb\n"
            "Note: MariaDB Connector/C must be installed first.\n"
            "See: https://mariadb.com/docs/server/connect/programming-languages/python/install/",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        conn = connect(args)
    except mariadb.Error as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.snapshot:
            run_snapshot(conn, args)
        elif args.daemon:
            run_daemon(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
