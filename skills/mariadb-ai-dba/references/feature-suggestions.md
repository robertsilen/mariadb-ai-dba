# MariaDB Feature Suggestions

This path collects schema metadata, then uses AI pattern matching to identify where MariaDB-specific features could improve the database.

## Data collection

Run in a **single** `mariadb --batch --skip-column-names --force` heredoc:

```bash
mariadb --batch --skip-column-names --force <<'SQL'

-- Server version and active plugins (needed to know which features are available)
SELECT CONCAT('version: ', VERSION());
SELECT CONCAT('plugin: ', PLUGIN_NAME, ' status=', PLUGIN_STATUS, ' type=', PLUGIN_TYPE)
FROM information_schema.PLUGINS
WHERE PLUGIN_STATUS = 'ACTIVE'
ORDER BY PLUGIN_TYPE, PLUGIN_NAME;

-- All user tables with metadata
SELECT CONCAT('table_meta: ', TABLE_SCHEMA, '.', TABLE_NAME,
    ' engine=', IFNULL(ENGINE, 'none'),
    ' row_format=', IFNULL(ROW_FORMAT, 'none'),
    ' rows=', IFNULL(TABLE_ROWS, 0),
    ' data_mb=', ROUND(IFNULL(DATA_LENGTH, 0) / 1024 / 1024, 1),
    ' create_options=', IFNULL(CREATE_OPTIONS, 'none'))
FROM information_schema.TABLES
WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
  AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_SCHEMA, TABLE_NAME;

-- All columns with types for user tables
SELECT CONCAT('column: ', TABLE_SCHEMA, '.', TABLE_NAME, '.', COLUMN_NAME,
    ' type=', COLUMN_TYPE,
    ' nullable=', IS_NULLABLE,
    ' default=', IFNULL(COLUMN_DEFAULT, 'NULL'),
    ' extra=', IFNULL(EXTRA, 'none'))
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION;

-- Existing indexes (to know what's already indexed)
SELECT CONCAT('index: ', TABLE_SCHEMA, '.', TABLE_NAME,
    ' name=', INDEX_NAME,
    ' columns=', GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX),
    ' type=', INDEX_TYPE,
    ' unique=', CASE NON_UNIQUE WHEN 0 THEN 'YES' ELSE 'NO' END)
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
GROUP BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, INDEX_TYPE, NON_UNIQUE
ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME;

-- CHECK constraints
SELECT CONCAT('check_constraint: ', CONSTRAINT_SCHEMA, '.', TABLE_NAME,
    ' name=', CONSTRAINT_NAME,
    ' clause=', CHECK_CLAUSE)
FROM information_schema.CHECK_CONSTRAINTS
WHERE CONSTRAINT_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY CONSTRAINT_SCHEMA, TABLE_NAME;

-- Triggers
SELECT CONCAT('trigger: ', TRIGGER_SCHEMA, '.', EVENT_OBJECT_TABLE,
    ' name=', TRIGGER_NAME,
    ' event=', EVENT_MANIPULATION,
    ' timing=', ACTION_TIMING)
FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY TRIGGER_SCHEMA, EVENT_OBJECT_TABLE;

-- Views
SELECT CONCAT('view: ', TABLE_SCHEMA, '.', TABLE_NAME)
FROM information_schema.VIEWS
WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY TABLE_SCHEMA, TABLE_NAME;

-- Stored routines
SELECT CONCAT('routine: ', ROUTINE_SCHEMA, '.', ROUTINE_NAME,
    ' type=', ROUTINE_TYPE)
FROM information_schema.ROUTINES
WHERE ROUTINE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME;

-- System-versioned tables (MariaDB-specific — check create_options)
SELECT CONCAT('system_versioned: ', TABLE_SCHEMA, '.', TABLE_NAME)
FROM information_schema.TABLES
WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys', 'mysql')
  AND CREATE_OPTIONS LIKE '%system_versioning%'
ORDER BY TABLE_SCHEMA, TABLE_NAME;

SQL
```

## AI analysis: feature pattern matching

After collecting the metadata above, analyze it for opportunities to use MariaDB-specific features. This is where the AI reasons over the schema — no additional queries needed.

For each pattern found, present the MariaDB feature, which table/column it applies to, why it's a good fit, and provide ready-to-run SQL.

### 1. System-versioned tables

**Look for:** Tables with audit patterns — triggers that copy rows to history tables, soft-delete columns (is_deleted, deleted_at, active), or created_at/updated_at timestamp pairs that suggest the application wants to track changes over time.

**MariaDB feature:** `ALTER TABLE ... ADD SYSTEM VERSIONING` provides automatic, transparent row history without application changes. Enables temporal queries like `FOR SYSTEM_TIME AS OF '2025-01-01'` and `FOR SYSTEM_TIME ALL`.

**Skip if:** The table already has system versioning (check `system_versioned:` output).

### 2. VECTOR columns and indexes

**Look for:** BLOB or VARBINARY columns with names like embedding, vector, feature, representation, or encoding. Also check for fixed-size BLOB columns that suggest vector storage.

**MariaDB feature:** Native `VECTOR(dimensions)` column type with `VECTOR INDEX ... DISTANCE=cosine|euclidean`. Provides approximate nearest neighbor search without external vector databases.

**Note:** Check the server version — VECTOR support requires MariaDB 11.6+.

### 3. Generated (virtual/stored) columns

**Look for:** Patterns where data is derived from other columns:
- Full name from first_name + last_name
- JSON key extraction (columns storing JSON with application-side parsing)
- Concatenated codes or identifiers
- Computed status from multiple flags

**MariaDB feature:** `ALTER TABLE ... ADD COLUMN full_name VARCHAR(200) AS (CONCAT(first_name, ' ', last_name)) VIRTUAL` — computed on read, zero storage. Use `STORED` if the column needs to be indexed.

### 4. CHECK constraints

**Look for:** Columns that represent bounded domains:
- ENUM columns (already constrained, but CHECK can add cross-column validation)
- Numeric columns like age, percentage, rating, score, quantity, price
- Status or type codes stored as INT/VARCHAR
- Columns where negative values or values above a threshold make no sense

**MariaDB feature:** `ALTER TABLE ... ADD CONSTRAINT chk_age CHECK (age BETWEEN 0 AND 150)`. Enforced at the database level — application bugs cannot insert invalid data.

### 5. Spatial indexes

**Look for:** Column pairs named latitude/longitude, lat/lng, lat/lon, x/y, or any FLOAT/DOUBLE/DECIMAL pair that represents geographic coordinates.

**MariaDB feature:** Store as `POINT` geometry, add `SPATIAL INDEX`. Enables efficient bounding-box and distance queries using `ST_Distance_Sphere()`, `MBRContains()`, etc.

### 6. Sequences

**Look for:** Tables used solely for ID generation — single-column tables, tables with only an auto-increment column and no other data, or application patterns that `SELECT MAX(id) + 1`.

**MariaDB feature:** `CREATE SEQUENCE` provides atomic, gap-free or gapped sequences without table locking. `NEXT VALUE FOR sequence_name` is faster than INSERT + LAST_INSERT_ID on a helper table.

### 7. Application-time periods

**Look for:** Column pairs like valid_from/valid_to, start_date/end_date, effective_start/effective_end that represent time ranges.

**MariaDB feature:** `ALTER TABLE ... ADD PERIOD FOR valid_period(valid_from, valid_to)` with `WITHOUT OVERLAPS` constraint. Prevents overlapping time ranges at the database level — critical for pricing, scheduling, and contract management.

### 8. Row compression

**Look for:** Large InnoDB tables (>100 MB) using `ROW_FORMAT=Dynamic` or `ROW_FORMAT=Compact` that contain TEXT, BLOB, or many VARCHAR columns.

**MariaDB feature:** `ALTER TABLE ... ROW_FORMAT=COMPRESSED` or `PAGE_COMPRESSED=1` (MariaDB's transparent page compression). Reduces disk I/O and storage. PAGE_COMPRESSED is preferred on modern MariaDB — it's transparent to the buffer pool.

### 9. JSON functions

**Look for:** VARCHAR or TEXT columns that store JSON strings — identifiable by column names like data, metadata, config, settings, properties, payload, or by examining sample values if available.

**MariaDB feature:** Native JSON column type with `JSON_VALUE()`, `JSON_TABLE()`, virtual generated columns for indexing specific JSON keys. Example: `ALTER TABLE ... ADD COLUMN status VARCHAR(50) AS (JSON_VALUE(metadata, '$.status')) VIRTUAL, ADD INDEX idx_status(status)`.

### 10. Invisible columns

**Look for:** This is not a pattern to detect in existing schemas — instead, mention it as a recommendation when suggesting schema additions. If you recommend adding a new column to an existing table, consider whether it should be `INVISIBLE` to avoid breaking existing `SELECT *` queries.

**MariaDB feature:** `ALTER TABLE ... ADD COLUMN audit_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP INVISIBLE`. The column exists and is populated, but does not appear in `SELECT *` — only in explicit `SELECT audit_ts, ...`. Useful for backward-compatible schema evolution.
