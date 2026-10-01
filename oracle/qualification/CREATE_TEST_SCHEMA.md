# Create the HARNESS_APP test schema

Step-by-step setup of the schema the qualification suite (`tests/qualification`,
`tests/integration`) runs against. Use a **non-production** Oracle 19c database.
For the full suite reference see `README.md` in this folder.

Never put a database password in the repo, a commit, a PR or chat. Keep passwords
in files outside the repo (below).

## 1. Create the schema

Connect with your Oracle client as `SYSTEM` (or any DBA account) and run:

```sql
CREATE USER harness_app IDENTIFIED BY "<choose a password>"
  DEFAULT TABLESPACE users
  QUOTA UNLIMITED ON users;

GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE PROCEDURE,
      CREATE SEQUENCE, CREATE JOB TO harness_app;

-- Lets the Kiwi catalog lookups read the data dictionary.
GRANT SELECT_CATALOG_ROLE TO harness_app;
```

The schema must be named `HARNESS_APP`; the integration and e2e suites expect it.

### Preferred grants (when you can connect as SYS)

`SELECT_CATALOG_ROLE` is the fallback. If you can connect as `SYS`, run
`oracle/grants/harness_roles.sql` instead of the last grant. It grants only the
dictionary views the harness needs. `SYSTEM` gets `ORA-01031` on grants of SYS
views, so that script must run as `SYS`.

## 2. Check the connection

```sql
CONNECT harness_app@//<host>:<port>/<service>
SELECT user FROM dual;
```

The DSN format the suite accepts is `host:port/service`.

## 3. Store the passwords outside the repo

```bash
mkdir -p ~/.harness && umask 077
read -rs -p "harness_app password: " P; echo; printf '%s' "$P" > ~/.harness/app.pw
read -rs -p "SYSTEM password: " P; echo; printf '%s' "$P" > ~/.harness/system.pw; unset P
```

## 4. Configure the run

```bash
export HARNESS_QUAL_ORACLE_DSN=<host>:<port>/<service>
export HARNESS_QUAL_ORACLE_USER=harness_app
export HARNESS_QUAL_ORACLE_PASSWORD_FILE=~/.harness/app.pw

# Optional admin account: connection-loss and KILL SESSION tests
export HARNESS_QUAL_ADMIN_DSN=<host>:<port>/<service>
export HARNESS_QUAL_ADMIN_USER=system
export HARNESS_QUAL_ADMIN_PASSWORD_FILE=~/.harness/system.pw

export HARNESS_QUAL_REPORT=./qualification-report.md
```

With no DSN set the whole suite skips. A half-configured run fails instead of
skipping, which is intended.

## 5. Run

```bash
uv sync --extra oracle
uv run pytest tests/qualification -v
uv run pytest tests/integration/test_process_death.py -v
```

The process-death test needs `SELECT` on `V$SESSION`, can start child Python
processes and opens an ephemeral `127.0.0.1` port.

The fixtures (EMPLOYEES, DEPARTMENTS, ORDER_LINES, scheduler objects and scratch
objects) are created and dropped by the tests themselves; you do not create them.

## Expected skips

- PDB checks: a non-CDB database is recorded as a coverage gap, not a pass.
- Second target and restricted account: skipped unless you configure
  `HARNESS_QUAL_SECOND_*` and `HARNESS_QUAL_RESTRICTED_*`.

## 6. Clean up

```sql
DROP USER harness_app CASCADE;
```

When testing is done, rotate the `SYSTEM` password and close any port forward that
exposed the database.
