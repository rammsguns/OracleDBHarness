# B4: python-oracledb 26.0.1 offline rehearsal

Date: 2026-10-24. Branch: `plan/B4-oracledb26`. This is G5 rehearsal evidence,
not Oracle qualification. No Oracle connection, Docker run, or access to
`databases/` was performed. Live qualification remains with the coordinator.

## Release-note review

No `.venv` or installed release notes existed before synchronization, so the
review used the official [26.0.0 notes](https://python-oracledb.readthedocs.io/en/latest/release_notes.html#oracledb-26-0-0)
and [26.0.1 notes](https://python-oracledb.readthedocs.io/en/latest/release_notes.html#oracledb-26-0-1).

Relevant changes in 26.0.0:

- Year-based version numbering; Python 3.9 removed, initial 3.15 support added.
- Thick initialization requires `libclntsh.so` / `libclntsh.dylib`, rather than
  version-specific filenames; Linux `lib_dir` supports RPATH `$ORIGIN`.
- LOB `Var.setvalue()` no longer accepts strings/bytes; use `Connection.createlob()`.
- Failed connection closure marks the connection unusable.
- Nonpositive sparse-vector dimensions raise `ValueError`.
- In/out `executemany()` and OSON encoding fixes; internal operation generators
  and modernized typing.
- New connection host/port/protocol/database-name properties, transaction priority,
  application context, operation/round-trip callbacks, templates, and HA readiness.

26.0.1 fixes empty transaction priority, NUMBER/SYS_REFCURSOR bind hangs, duplicate
Arrow fetch data, and async TLS renegotiation. The other 26.0.0 additions concern
async security/termination, authentication/configuration plugins, AQ validation,
Arrow conversion/types, secret caching, security typing, tracing, and upstream tests.

Neither release announces renamed APIs, changed timeout/fetch defaults, changed
DPY-/ORA- formats, or changed cancellation/break semantics.

## Repository impact

Searched `services/`, `scripts/`, `tests/capacity/`, and `tests/qualification/`
with `rg` for every API/category above, plus `call_timeout`, `cancel`, `_error_code`,
`_message`, `expire_time`, `tcp_connect_timeout`, `thick_mode_dsn_passthrough`,
`fetch_lobs`, `fetch_decimals`, `arraysize`, and `prefetchrows`.
The table records repository findings; unchanged behavior is an offline assessment
that still needs live qualification.

| Surface | Repository finding and action |
| --- | --- |
| Python | Root and worker require Python >=3.11. Installed driver metadata requires >=3.10; this run uses CPython 3.12.15. No project minimum change needed. |
| `init_oracle_client` | `backend/oracle.py:initialize` passes a directory, and `scripts/fleet_discovery.py` uses default discovery. Neither hardcodes a versioned library filename. Documented the deployment filename requirement in `compatibility.md`; actual Thick loading belongs to live qualification. |
| LOB `Var.setvalue`, `createlob` | The adapter's sole `setvalue()` sets the numeric DBMS_OUTPUT line count. No LOB variable setter or `createlob()` call exists in the searched paths. LOB fetches use locator `size()`/`read()` and bounded previews; no migration needed. |
| Connection close | Adapter closure is best effort and marks the wrapper closed regardless; schema-setup cleanup also discards the connection. Discovery and qualification close rather than reuse it. No caller relies on reusing a connection after failed close. |
| Sparse vectors | Adapter reads returned sparse-vector attributes, but constructs only dense `array.array` binds. Bind validation already rejects empty vectors. No `SparseVector` constructor exists in scope. |
| `executemany`, OSON | `executemany()` hits are SQLite stand-in calls. No Oracle in/out batch bind or OSON encoder call exists. |
| New connection properties/context/callbacks/priority/templates | No matching driver calls in scope. Identity remains SQL based. Default transaction priority is not set by callers. |
| Async TLS / NUMBER + SYS_REFCURSOR / Arrow | Oracle connections are synchronous. No SYS_REFCURSOR bind, `fetch_df_all`, `fetch_df_batches`, or Arrow ingestion call exists in scope. |
| Remaining additions | No driver AQ, security-context, external/token authentication, centralized configuration, secret-cache, or operation-tracing API calls exist in scope. Wallet-based TCPS remains unchanged. |
| `thick_mode_dsn_passthrough` | Installed `ConnectParams()` reports `True`. Backend constructs a full descriptor with Thick-mode EXPIRE_TIME and wallet clauses. Discovery explicitly passes `False`. Preserve both choices. |
| `expire_time`, `tcp_connect_timeout` | Installed defaults are 0 minutes and 20 seconds. Backend explicitly passes profile timeout and configured keepalive (default 1 minute); capacity helpers and discovery pass configured keepalive. Preserve existing parameters and descriptors. |
| `call_timeout`, cancel/break | Installed connection source still documents milliseconds, zero for disabled, and `cancel()` as breaking a long-running statement. Adapter sets deadline milliseconds, resets to zero only on healthy connections, and calls cancel from the cancellation path. Preserve fatal-session guards, ORA-01013 cancellation, and DPY-4024 timeout translation. The previously recorded setter crash is not proven fixed by this rehearsal. |
| Error parsing | Installed `oracledb/errors.py` retains `_Error.full_code` and `.message`, including additional help text and mapped driver errors. Adapter `_error_code` prefers `full_code`; `_message` preserves the message. Existing fallback parsing and fatal/cancellation classifications are unchanged. Discovery similarly extracts a full code. |
| Fetch defaults | Installed defaults: `fetch_lobs=True`, `fetch_decimals=False`, `arraysize=100`, `prefetchrows=2`. Adapter explicitly sets bounded arraysize/prefetchrows and a NUMBER-to-Decimal output handler; LOB locators remain required for previews. Preserve these settings. |

No application code adaptation was necessary, so no 26.x-specific code comments
were added. Changes are the worker pin, generated lock, qualification-status note,
and this audit. Historic 4.0.2 qualification records remain historic evidence.

## Offline verification

`uv lock` initially failed because the default `/home/kiwi/.cache/uv` was not
writable. Retrying with `UV_CACHE_DIR=/home/kiwi/.cache/harness-uv` succeeded:
`Resolved 63 packages`, `Updated oracledb v4.0.2 -> v26.0.1`.
`UV_CACHE_DIR=/home/kiwi/.cache/harness-uv uv sync --all-extras` installed 26.0.1.
Only the Oracle driver dependency changed in the lock resolution.

For pytest, inherited `HARNESS_QUAL_*` variables were removed in the subprocess
environment and `HARNESS_ORACLE_BACKEND=fake` was set to ensure offline execution.
All commands used the writable UV cache; no secret values were printed.

- `uv run pytest -q tests/unit tests/integration`: `900 passed, 14 skipped in 207.47s (0:03:27)`.
- `uv run ruff check .`: `All checks passed!`
- `uv run ruff format --check .`: `389 files already formatted`

An offline smoke check using installed 26.0.1 `_Error`/`DatabaseError` objects
also passed adapter `full_code` and message extraction for DPY-4011, DPY-4024,
and ORA-01013, without opening a connection.

## Concurrent thin-connection robustness regression (B4b)

Claude's live r2 (D2 23ai, thin) runs found that python-oracledb 26.0.1
segfaults, without a Python exception, when two threads concurrently use one
thin connection. This reproduced on Python 3.11 and 3.12; 4.0.2 did not crash
in the same qualification scenario. The test's daemon kill thread was still
executing `ALTER SYSTEM KILL SESSION` on the admin connection when fixture
teardown called `close()` on it, particularly when COMMIT won the race.
This is a test lifecycle bug and a driver robustness regression recorded for
the ADR, not a blocker.

The harness product does not share a connection for concurrent ordinary driver
operations this way. Code inspection found that `SessionManager.acquire()` in
`services/worker/harness_worker/sessions.py` takes the session lock for exclusive
execution, and session disposal respects that lock. The intentional concurrent
path is cancellation: `OracleConnection.cancel()` in
`services/worker/harness_worker/backend/oracle.py:649` calls
`self._conn.cancel()` (line 651), catches a failed break, and returns a boolean.
It does not execute SQL or close the connection; there is no separate `break()`
call. `SessionManager.request_cancel()` delivers this cancel/break outside the
execution lock while tracking cancellation delivery under the state lock.

All three `_kill_after` callers now join in `finally` with a 30-second bound,
including the inconclusive COMMIT skip path. Cancellation and blocking helpers
have the same bounded cleanup; fixture teardown refuses rollback/close while a
registered connection thread remains alive, reporting a clear failure instead.
The stand-in restart test also joins its writer in `finally` on assertion failures.
No live database checks were run for this fix; Claude owns live qualification.

B4b offline verification used the existing Python 3.11.16 venv and
`UV_CACHE_DIR=/home/kiwi/.cache/harness-uv`:

- `uv run pytest -q tests/unit`: `777 passed, 7 skipped in 106.89s`.
- `uv run pytest -q tests/integration/test_restart.py` with `HARNESS_QUAL_*`
  removed and `HARNESS_ORACLE_BACKEND=fake`: `14 passed in 10.02s`.
- `uv run ruff check .`: `All checks passed!`.
- `uv run ruff format --check .`: `392 files already formatted`.
