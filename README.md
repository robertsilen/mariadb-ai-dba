# MariaDB AI DBA

An AI-powered database administrator skill for Claude Code. Connects to a MariaDB server and produces a comprehensive audit report covering server health, query optimization, security, and MariaDB-specific feature opportunities.

## What It Does

When activated, the AI DBA will:

1. Ask which analysis paths to include (multi-select, all selected by default):
   - **Server overview** — server state, InnoDB health, connections, buffer pool, replication, and performance counters
   - **Query optimization** — slow query config, missing indexes, schema analysis, duplicate indexes, and Performance Schema statement digests
   - **MariaDB feature suggestions** — inspects schemas and identifies improvements using MariaDB-native capabilities
   - **Security audit** — users, grants, privileges, SSL status, and authentication configuration
2. Ask for connection details
3. Run all selected paths and collect diagnostic data
4. Generate a timestamped audit report (`mariadb-audit_{timestamp}.md`) with findings, severity ratings, and actionable recommendations

All queries are read-only. The skill never modifies data, schema, or configuration.

Companion skills from [github.com/MariaDB/skills](https://github.com/MariaDB/skills) are loaded automatically for deeper MariaDB-specific analysis. If not installed, the skill offers to fetch them from GitHub.

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

- `mariadb` command-line client installed and on your PATH
- Network access to the target MariaDB server
- A database user with at least read privileges (see [Security](#security) above for the recommended setup)
