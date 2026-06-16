# Security Audit

Run all queries below in a **single** `mariadb --batch --skip-column-names --force` heredoc invocation.

Use `CONCAT()` to produce labeled `key: value` lines. For multi-row results, use a prefix like `user_account:` so rows are grouped in the output.

```bash
mariadb --batch --skip-column-names --force <<'SQL'

-- All user accounts with auth details
SELECT CONCAT('user_account: ', user, '@', host,
    ' plugin=', IFNULL(plugin, 'none'),
    ' ssl_type=', IFNULL(ssl_type, 'none'),
    ' is_role=', IFNULL(is_role, 'N'),
    CASE WHEN password = '' AND authentication_string = ''
         THEN ' EMPTY_PASSWORD' ELSE '' END)
FROM mysql.user
WHERE is_role = 'N' OR is_role = '' OR is_role IS NULL
ORDER BY user, host;

-- Anonymous accounts
SELECT CONCAT('anonymous_account: ', user, '@', host)
FROM mysql.user WHERE user = '';

-- Non-local root accounts
SELECT CONCAT('nonlocal_root: root@', host)
FROM mysql.user
WHERE user = 'root'
  AND host NOT IN ('localhost', '127.0.0.1', '::1');

-- Wildcard host accounts
SELECT CONCAT('wildcard_host: ', user, '@', host)
FROM mysql.user WHERE host = '%';

-- Accounts with empty passwords (excluding socket auth and system accounts)
SELECT CONCAT('empty_password: ', user, '@', host, ' plugin=', IFNULL(plugin, 'none'))
FROM mysql.user
WHERE (password = '' AND authentication_string = '')
  AND plugin NOT IN ('unix_socket', 'auth_socket', 'auth_pam')
  AND user NOT IN ('', 'mariadb.sys', 'PUBLIC')
  AND (is_role = 'N' OR is_role = '' OR is_role IS NULL);

-- Accounts sharing identical password hashes
SELECT CONCAT('shared_password: ', GROUP_CONCAT(CONCAT(user, '@', host) SEPARATOR ', '),
    ' (', COUNT(*), ' accounts)')
FROM mysql.user
WHERE authentication_string != ''
  AND authentication_string IS NOT NULL
  AND plugin NOT IN ('unix_socket', 'auth_socket', 'auth_pam')
GROUP BY authentication_string
HAVING COUNT(*) > 1;

-- Non-root accounts with ALL PRIVILEGES (global level)
SELECT CONCAT('all_privs: ', grantee)
FROM information_schema.USER_PRIVILEGES
WHERE PRIVILEGE_TYPE = 'ALL PRIVILEGES'
  AND grantee NOT LIKE '''root''%'
  AND grantee NOT LIKE '''mariadb.sys''%';

-- Non-root accounts with admin privileges
SELECT CONCAT('admin_priv: ', grantee, ' has ', PRIVILEGE_TYPE)
FROM information_schema.USER_PRIVILEGES
WHERE PRIVILEGE_TYPE IN ('SUPER', 'SHUTDOWN', 'RELOAD', 'PROCESS', 'FILE', 'CREATE USER')
  AND grantee NOT LIKE '''root''%'
  AND grantee NOT LIKE '''mariadb.sys''%'
ORDER BY grantee, PRIVILEGE_TYPE;

-- SSL/TLS configuration
SELECT CONCAT('have_ssl: ', @@global.have_ssl);
SELECT CONCAT('have_openssl: ', @@global.have_openssl);

-- require_secure_transport (may not exist in older versions)
SELECT CONCAT('require_secure_transport: ', @@global.require_secure_transport);

-- Authentication plugin distribution
SELECT CONCAT('auth_plugin_dist: ', IFNULL(plugin, 'none'), ': ', COUNT(*), ' accounts')
FROM mysql.user
WHERE is_role = 'N' OR is_role = '' OR is_role IS NULL
GROUP BY plugin
ORDER BY COUNT(*) DESC;

-- Test database
SELECT CONCAT('test_db_exists: ', SCHEMA_NAME)
FROM information_schema.SCHEMATA
WHERE SCHEMA_NAME = 'test';

-- local_infile setting
SELECT CONCAT('local_infile: ', @@global.local_infile);

-- skip_name_resolve
SELECT CONCAT('skip_name_resolve: ', @@global.skip_name_resolve);

-- Password validation plugin
SELECT CONCAT('password_plugin: ', PLUGIN_NAME, ' status=', PLUGIN_STATUS)
FROM information_schema.PLUGINS
WHERE PLUGIN_NAME LIKE '%password%' OR PLUGIN_NAME LIKE '%validate%';

-- PUBLIC role grants (MariaDB-specific)
SELECT CONCAT('public_grant: ', TABLE_SCHEMA, '.', TABLE_NAME, ' priv=', PRIVILEGE_TYPE)
FROM information_schema.TABLE_PRIVILEGES
WHERE GRANTEE LIKE '''PUBLIC''%';

SELECT CONCAT('public_schema_grant: ', TABLE_SCHEMA, ' priv=', PRIVILEGE_TYPE)
FROM information_schema.SCHEMA_PRIVILEGES
WHERE GRANTEE LIKE '''PUBLIC''%';

SQL
```

## Interpreting the results

### Severity mapping

- **CRITICAL:** Anonymous accounts (user = '') — anyone can connect without credentials
- **HIGH:** Non-local root accounts, wildcard host accounts (host = '%'), empty passwords on non-socket-auth accounts
- **HIGH:** Non-root accounts with ALL PRIVILEGES at global level
- **MEDIUM:** Accounts sharing identical passwords, non-root accounts with admin privileges (SUPER, SHUTDOWN, etc.)
- **MEDIUM:** SSL/TLS disabled (have_ssl = DISABLED), require_secure_transport = OFF
- **LOW:** local_infile = ON, test database present, password validation plugin not installed
- **LOW:** PUBLIC role grants on test or user databases, skip_name_resolve = OFF

### Notes

- **Socket auth plugins** (unix_socket, auth_socket, auth_pam): These are passwordless by design — the OS handles authentication. Do not flag empty passwords for these accounts.
- **is_role:** Roles are not user accounts and should not appear in user account listings.
- **mysql.user:** All queries use `mysql.user` which is available across all MariaDB versions and exposes plugin, ssl_type, and authentication_string columns directly.
- **require_secure_transport:** May not exist in MariaDB versions before 10.5. If the query fails, note it as unavailable rather than as a finding.
- **Password validation:** MariaDB has `simple_password_check` and `cracklib_password_check` plugins. Neither is installed by default.
