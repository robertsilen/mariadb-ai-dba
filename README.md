<img src="MariaDB_Foundation_logo.png" alt="MariaDB Foundation" width="300">

# MariaDB AI DBA

An AI-powered database administrator skill for Claude Code. Connects to a MariaDB server and produces a factual server inventory covering configuration, schema, performance counters, security, and MariaDB-specific features.

## What It Does

When activated, the AI DBA will:

1. Ask which analysis paths to include (multi-select, all selected by default):
   - **Server overview** — server state, InnoDB configuration, connections, buffer pool, replication, and performance counters
   - **Query optimization** — slow query config, schema structure, indexes, and Performance Schema statement digests
   - **MariaDB features** — which MariaDB-specific features are in use and which are available
   - **Security audit** — users, grants, privileges, SSL status, and authentication configuration
2. Ask for connection details
3. Run all selected paths and collect diagnostic data
4. Generate a timestamped server inventory (`mariadb-audit_{timestamp}.md` + `.html`) with a factual snapshot of the server's configuration, schema, accounts, and performance counters — the HTML version opens in a browser for easy copy-paste into email or Google Docs. Security findings include severity ratings and fix recommendations.

All queries are read-only. The skill never modifies data, schema, or configuration. Use a [read-only database user](#security) to be sure.

Companion skills from [github.com/MariaDB/skills](https://github.com/MariaDB/skills) are loaded automatically for deeper MariaDB-specific context. If not installed, the skill offers to fetch them from GitHub.

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

### Optional: continuous monitoring

For time-series trending with graphs, run the collector as a daemon before your next audit:

```bash
python3 skills/mariadb-ai-dba/collect.py --daemon --interval 1 --socket /tmp/mysql.sock --snapshots-dir ./snapshots
```

This samples GLOBAL_STATUS every second. The next audit run will include time-series data and trends.
