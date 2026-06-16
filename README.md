# MariaDB AI DBA

An AI-powered database administrator skill for Claude Code. Connects to a MariaDB server and provides analysis across four areas: server overview, query optimization, MariaDB feature suggestions, and security audit.

## What It Does

When activated, the AI DBA will:

1. Load MariaDB-specific knowledge from companion skills (`mariadb-features`, `mariadb-query-optimization`, `mysql-to-mariadb`)
2. Ask for your connection details
3. Present four paths to choose from:
   - **Server overview** — server state, storage engines, connections, buffer pool, replication, and health flags; option to save a report file
   - **Query optimization** — slow queries, missing indexes, execution plan analysis
   - **MariaDB feature suggestions** — inspects your schemas and identifies concrete improvements using MariaDB-native capabilities
   - **Security audit** — users, grants, privileges, SSL status, and authentication configuration

All queries are read-only. The skill never modifies data, schema, or configuration.

## How to Use

### Quick start (run from the repo)

Clone the repo and run directly — no installation needed:

```bash
git clone https://github.com/mariadb/mariadb-ai-dba.git
cd mariadb-ai-dba
claude "dba"
```

That's it. Claude reads the skill from the repo and walks you through connecting.

### Install as a skill (optional)

To make the skill available from any directory, symlink it into your skills directory:

```bash
ln -s /path/to/mariadb-ai-dba/skills/mariadb-ai-dba ~/.claude/skills/mariadb-ai-dba
```

Also install the companion skills from [github.com/mariadb/skills](https://github.com/mariadb/skills) for deeper MariaDB-specific analysis.

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
- Companion skills installed (see [How to Use](#how-to-use) above); the skill works without them but analysis will be less MariaDB-specific
