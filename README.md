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

### Claude Code (CLI or Desktop App)

Claude Code supports skills natively. Symlink the skill into your skills directory:

```bash
ln -s /path/to/mariadb-ai-dba/skills/mariadb-ai-dba ~/.claude/skills/mariadb-ai-dba
```

Also install the companion skills from [github.com/mariadb/skills](https://github.com/mariadb/skills):

```bash
ln -s /path/to/mariadb-skills/mariadb-features ~/.claude/skills/mariadb-features
ln -s /path/to/mariadb-skills/mariadb-query-optimization ~/.claude/skills/mariadb-query-optimization
ln -s /path/to/mariadb-skills/mysql-to-mariadb ~/.claude/skills/mysql-to-mariadb
```

Then start a conversation and ask something like:
- "Analyze my MariaDB server"
- "Run a database health check"
- "Audit my MariaDB security"

Claude will automatically activate the skill and walk you through connecting.

### Claude Desktop (claude.ai app)

Claude Desktop does not support skills or the Bash tool, so this skill cannot run there directly. Two alternatives:

1. **Use Claude Code instead** — it has shell access and skill support, which is what this tool needs.

2. **Use the [MariaDB MCP Server](https://github.com/MariaDB/mcp)** — this gives Claude Desktop a database connection via MCP. You can paste the diagnostic queries from `skills/mariadb-ai-dba/references/` into the conversation manually, or add them as Project instructions. Setup:
   - Install the MCP server following its README
   - Add it to Claude Desktop's MCP config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS)
   - Start a conversation and ask Claude to analyze your database

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
