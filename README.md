<img src="MariaDB_Foundation_logo.png" alt="MariaDB Foundation" width="300">

# MariaDB AI DBA

An AI-powered database administrator skill for Claude Code. Connects to a MariaDB server and produces a factual server inventory covering configuration, schema, performance counters, security, and MariaDB-specific features.

## What It Does

When activated, the AI DBA will:

1. Ask how to connect (local socket, defaults file, or remote)
2. Collect all diagnostic data in one pass — server overview, InnoDB health, connections, query performance, schema analysis, security audit, and MariaDB feature inventory
3. Generate a timestamped server inventory (`mariadb-audit_{timestamp}.md` + `.html`) — the HTML version opens in a browser for easy sharing. Security findings include severity ratings and fix recommendations.

If previous snapshots exist, the report includes **Δ columns** showing what changed since the last run — config drift, workload shifts, and gauge changes are highlighted inline.

All queries are read-only. The skill never modifies data, schema, or configuration. Use a [read-only database user](#security) to be sure.

Companion skills from [github.com/MariaDB/skills](https://github.com/MariaDB/skills) are loaded silently for deeper MariaDB-specific context.

## How to Use

### Quick start (run from the repo)

```bash
git clone https://github.com/mariadb/mariadb-ai-dba.git
cd mariadb-ai-dba
claude "dba"
```

That's it. Claude reads the skill from the repo and walks you through the audit.

### Install as a skill (optional)

To make the skill available from any directory, symlink it into your skills directory:

```bash
ln -s /path/to/mariadb-ai-dba/skills/mariadb-ai-dba ~/.claude/skills/mariadb-ai-dba
```

Then from any directory: "Analyze my MariaDB server", "Run a database health check", "Audit my MariaDB security".

## Security

The skill instructs the AI to run only read-only queries, but that is a soft guardrail — it relies on the AI following instructions. The right approach is to enforce it at the database level with a dedicated least-privilege user.

### Create a read-only monitor user

```sql
-- Local socket auth (no password, uses OS user identity — recommended)
CREATE USER 'dba_monitor'@'localhost' IDENTIFIED VIA unix_socket;

-- Or with a password for remote access:
-- CREATE USER 'dba_monitor'@'%' IDENTIFIED BY 'a_strong_password';

GRANT SELECT ON *.* TO 'dba_monitor'@'localhost';
GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'dba_monitor'@'localhost';
```

- `SELECT ON *.*` — covers `information_schema`, table sizes, and user schemas
- `PROCESS` — required for `SHOW GLOBAL STATUS` and `SHOW VARIABLES`
- `REPLICATION CLIENT` — required for `SHOW ALL SLAVES STATUS`

This user cannot write data, modify schema, or run admin commands — even if the AI generates a bad query or is prompt-injected via data it reads from the database.

Then connect via a `.my.cnf` defaults file to keep credentials out of the process list:

```ini
[client]
user=dba_monitor
socket=/tmp/mysql.sock
```

Pass it to the skill when prompted: choose **Defaults file** and provide the path.

## Requirements

- Python 3 with the `mariadb` module (`pip install mariadb`) — requires [MariaDB Connector/C](https://mariadb.com/docs/server/connect/programming-languages/python/install/) at OS level
- Network access to the target MariaDB server
- A database user with at least read privileges (see [Security](#security) above for the recommended setup)

Falls back to the `mariadb` CLI client if Python or the module is unavailable.

### Snapshot trending

Each audit run saves a JSON snapshot to `./snapshots/`. On subsequent runs, the collector automatically compares against the most recent previous snapshot from the same server and computes deltas — rates for cumulative counters, gauge changes for point-in-time metrics, and config drift detection. These appear as **Δ columns** in the report tables: `=` means compared and unchanged, previous values shown when something changed.

### Optional: continuous monitoring

For high-resolution time-series trending with graphs, run the collector as a daemon before your next audit:

```bash
python3 skills/mariadb-ai-dba/collect.py --daemon --interval 1 --socket /tmp/mysql.sock --snapshots-dir ./snapshots
```

This samples GLOBAL_STATUS every second. The next audit run will include time-series data and trends.
