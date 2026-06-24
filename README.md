<img src="MariaDB_Foundation_logo.png" alt="MariaDB Foundation" width="300">

# MariaDB AI DBA

An AI-powered database administrator skill for Claude Code. Connects to a MariaDB server and produces a factual server inventory covering configuration, schema, performance counters, security, and MariaDB-specific features.

## What You Get

A timestamped HTML report covering:

- **Executive Summary** with AI Suggestions tailored to your purpose and Next Steps you can continue exploring in Claude Code
- **Server overview** — version, uptime, databases, storage engines, memory
- **InnoDB health** — buffer pool, checkpoint age, history list, I/O
- **Connections** — current vs peak, thread cache, aborted
- **Query performance** — throughput, slow queries, temp tables, handler stats
- **Schema analysis** — table sizes, missing indexes, auto-increment fill, fragmentation
- **Security audit** — users, grants, SSL, auth plugins (with severity ratings)
- **MariaDB features** — what's available vs what's in use
- **Inline graphs** — 15 time-series charts + workload donut
- **Δ columns** — what changed since the previous snapshot

## Quick Start

```bash
git clone https://github.com/mariadb/mariadb-ai-dba.git
cd mariadb-ai-dba
claude "dba"
```

That's it. Claude reads the skill from the repo and walks you through the audit.

### Recommended: install companion skills

For deeper MariaDB-specific analysis, install the companion skills from [github.com/MariaDB/skills](https://github.com/MariaDB/skills):

```bash
git clone https://github.com/MariaDB/skills.git ~/.claude/skills/mariadb-skills
```

These are loaded silently during the audit for optimal results.

### Optional: install as a global skill

To make the skill available from any directory, symlink it into your skills directory:

```bash
ln -s /path/to/mariadb-ai-dba/skills/mariadb-ai-dba ~/.claude/skills/mariadb-ai-dba
```

Then from any directory: "Analyze my MariaDB server", "Run a database health check", "Audit my MariaDB security".

## Continuous Monitoring

For higher-resolution graphs, run the daemon in a background terminal before your next audit:

```bash
python3 skills/mariadb-ai-dba/collect.py --daemon --interval 1 --socket /tmp/mysql.sock --snapshots-dir ./snapshots
```

This samples MariaDB status counters and OS metrics (CPU, memory, swap, disk I/O) every second. The next audit will include time-series graphs showing MariaDB and OS metrics side by side — slow queries coinciding with swap activity or disk I/O spikes become visible at a glance.

Even without the daemon, graphs are generated from snapshot history if multiple snapshots exist.

## How It Works

1. Ask what brings you here — general overview or investigating a specific problem
2. Ask how to connect (local socket, defaults file, or remote)
3. Collect all diagnostic data in one pass. AI DBA will ask what previous snapshot to compare to, and a Δ column in the report will show what changed. For higher-resolution graphs, run the companion daemon beforehand — instructions above.
4. Generate a timestamped HTML report (`mariadb-audit_{timestamp}.html`) that opens in the browser.
5. The Executive Summary includes AI-generated "Next Steps" suggestions that you can continue exploring in Claude Code.

All queries are read-only. The skill never modifies data, schema, or configuration. Use a [read-only database user](#security) to be sure.

## Requirements

- Python 3 with the `mariadb` module (`pip install mariadb`) — requires [MariaDB Connector/C](https://mariadb.com/docs/server/connect/programming-languages/python/install/) at OS level
- Network access to the target MariaDB server
- A database user with at least read privileges (see [Security](#security) below for the recommended setup)
- Optional: `seaborn` (`pip install seaborn`) for time-series graphs in the HTML report

Falls back to the `mariadb` CLI client if Python or the module is unavailable. Graphs are omitted if seaborn is not installed.

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

## Contributing

Suggestions and pull requests are welcome. Open an issue or PR at [github.com/mariadb/mariadb-ai-dba](https://github.com/mariadb/mariadb-ai-dba).

## Acknowledgments

Developed by [@robertsilen](https://github.com/robertsilen) based on DBA skills by [@lefred](https://github.com/lefred) and an idea by [@kajarnocom](https://github.com/kajarnocom).
