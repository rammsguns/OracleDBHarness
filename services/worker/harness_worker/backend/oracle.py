"""python-oracledb backend.

Driver mode is process wide, so a deployment that needs Thick mode (Native Network
Encryption, some wallet configurations) must run a separately configured worker
rather than switching at runtime. See the driver initialization documentation:
https://python-oracledb.readthedocs.io/en/stable/user_guide/initialization.html
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from harness_worker.backend.base import (
    ConnectionSpec,
    OracleBackend,
    OracleConnection,
    StatementResult,
)
from harness_worker.errors import (
    CancelledError_,
    CapabilityError,
    ConfigurationError,
    ConnectionFailedError,
    OracleError,
    OutcomeUnknownError,
    ValidationError,
)
from harness_worker.statement import (
    is_simple_identifier,
    quote_identifier,
    strip_literals_and_comments,
)
from harness_worker.types import (
    Capability,
    CapabilityReport,
    ColumnMetadata,
    CompilerError,
    ExecutionLimits,
    ResultSet,
    StatementKind,
    TargetIdentity,
)

try:  # pragma: no cover - exercised only where the driver is installed
    import oracledb
except ImportError:  # pragma: no cover
    oracledb = None  # type: ignore[assignment]


log = logging.getLogger("harness.backend.oracle")

_init_lock = threading.Lock()
_initialized_mode: str | None = None

# How many DBMS_OUTPUT lines are asked for per GET_LINES round trip.
_DBMS_OUTPUT_CHUNK_LINES = 100

IDENTITY_SQL = """
SELECT SYS_CONTEXT('USERENV', 'DB_NAME')            AS database_name,
       SYS_CONTEXT('USERENV', 'INSTANCE_NAME')      AS instance_name,
       SYS_CONTEXT('USERENV', 'SERVER_HOST')        AS host_name,
       SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')     AS current_schema,
       SYS_CONTEXT('USERENV', 'SESSION_USER')       AS session_user,
       SYS_CONTEXT('USERENV', 'CON_NAME')           AS container_name,
       SYS_CONTEXT('USERENV', 'SID')                AS session_id
  FROM dual
"""

VERSION_SQL = """
SELECT version, banner_full
  FROM product_component_version
 WHERE product LIKE 'Oracle Database%'
   AND ROWNUM <= 1
"""

# One probe per capability. Each is the cheapest statement that proves the privilege
# actually exists, because a role granted on paper may still be revoked or unusable.
CAPABILITY_PROBES: dict[Capability, str] = {
    Capability.CONNECT: "SELECT 1 FROM dual",
    Capability.SESSION_SCHEMA: "SELECT COUNT(*) FROM user_objects WHERE ROWNUM <= 1",
    Capability.ALL_OBJECTS: "SELECT COUNT(*) FROM all_objects WHERE ROWNUM <= 1",
    Capability.EXPLAIN_PLAN: "SELECT COUNT(*) FROM plan_table WHERE ROWNUM <= 1",
    Capability.DISPLAY_CURSOR: "SELECT COUNT(*) FROM v$sql_plan WHERE ROWNUM <= 1",
    Capability.V_SESSION: "SELECT COUNT(*) FROM v$session WHERE ROWNUM <= 1",
    Capability.V_SQL: "SELECT COUNT(*) FROM v$sql WHERE ROWNUM <= 1",
    Capability.DBA_TABLESPACES: "SELECT COUNT(*) FROM dba_tablespace_usage_metrics"
    " WHERE ROWNUM <= 1",
    Capability.DBA_SCHEDULER_JOBS: "SELECT COUNT(*) FROM dba_scheduler_jobs WHERE ROWNUM <= 1",
    Capability.DBMS_STATS: "SELECT COUNT(*) FROM all_objects"
    " WHERE owner = 'SYS' AND object_name = 'DBMS_STATS'"
    "   AND object_type = 'PACKAGE'",
    Capability.COMPILE_OBJECTS: "SELECT COUNT(*) FROM all_errors WHERE ROWNUM <= 1",
}

# A COMMIT or ROLLBACK typed into the worksheet resolves the transaction exactly as the
# toolbar buttons do. ROLLBACK TO SAVEPOINT does not -- it rewinds inside a transaction
# that stays open -- and FORCE resolves an in-doubt distributed transaction, not this one.
_ENDS_TRANSACTION = re.compile(
    r"^(?:COMMIT|ROLLBACK)\b(?!\s+(?:WORK\s+)?(?:TO|FORCE)\b)", re.IGNORECASE
)
_IS_COMMIT = re.compile(r"^COMMIT\b", re.IGNORECASE)

# The object a CREATE ... statement compiles, so its errors can be read back out of
# ALL_ERRORS. The keyword list is the one the classifier uses to call a statement
# PL/SQL source; the two have to agree, or a unit is compiled and never checked.
#
# A name is either quoted and stored literally, or unquoted and folded to upper case.
# The lookahead is what stops an unquoted match from ending inside a name that
# continues with a character the class does not cover -- an accented letter, say.
# ALL_ERRORS read for a prefix of the real name returns no rows, and no rows is how a
# unit that compiled cleanly looks.
_OBJECT_NAME = r'"[^"\n]+"|[A-Za-z0-9_$#]+(?![\w$#])'
_CREATED_UNIT = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?"
    r"(PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE\s+BODY|TYPE|LIBRARY)\s+"
    rf"(?:({_OBJECT_NAME})\s*\.\s*)?({_OBJECT_NAME})",
    re.IGNORECASE,
)

# ORA codes that mean the session is no longer usable and must be discarded rather
# than returned to a lease.
_FATAL_CODES = {
    "ORA-00028",  # session killed
    "ORA-01012",  # not logged on
    "ORA-01041",  # internal error, hostdef extension does not exist
    "ORA-03113",  # end-of-file on communication channel
    "ORA-03114",  # not connected to Oracle
    "ORA-03135",  # connection lost contact
    "ORA-12571",  # packet writer failure
    "DPY-4011",  # the database or network closed the connection
    "DPY-1001",  # not connected
}

# Oracle acknowledging a break request. Not a failure: the call was stopped on our own
# instruction, and the statement it was running was rolled back to where it started.
_CANCELLED_CODE = "ORA-01013"


def initialize(mode: str, lib_dir: str | None = None) -> None:
    """Initialise the driver once per process and refuse a later mode change."""

    global _initialized_mode
    if oracledb is None:
        raise ConfigurationError(
            "python-oracledb is not installed. Install the 'oracle' extra of "
            "harness-worker, or set HARNESS_ORACLE_BACKEND=fake for local development."
        )
    with _init_lock:
        if _initialized_mode is not None:
            if _initialized_mode != mode:
                raise ConfigurationError(
                    "python-oracledb driver mode is process wide and is already "
                    f"initialised as {_initialized_mode!r}. Deploy a separate worker "
                    f"for {mode!r} instead of switching at runtime."
                )
            return
        if mode == "thick":
            oracledb.init_oracle_client(lib_dir=lib_dir or None)
        _initialized_mode = mode


class OracleDbConnection(OracleConnection):
    def __init__(self, spec: ConnectionSpec, connection: Any) -> None:
        self._spec = spec
        self._conn = connection
        self._transaction_open = False
        self._broken = False
        self._closed = False
        self._lock = threading.RLock()
        self._dbms_output_enabled = False

    # -- identity and capabilities -------------------------------------------------

    def identity(self) -> TargetIdentity:
        with self._conn.cursor() as cur:
            cur.execute(IDENTITY_SQL)
            row: tuple[Any, ...] = cur.fetchone() or ()
            version, banner = "", ""
            try:
                cur.execute(VERSION_SQL)
                version_row = cur.fetchone()
                if version_row:
                    version, banner = version_row[0] or "", version_row[1] or ""
            except Exception:  # noqa: BLE001 - version view is not always readable
                version = getattr(self._conn, "version", "") or ""
        con_name = row[5] if len(row) > 5 else None
        return TargetIdentity(
            databaseName=row[0] or "",
            instanceName=row[1],
            hostName=row[2],
            version=version,
            versionFull=banner or version,
            isCdb=bool(con_name) and con_name != "CDB$ROOT" or con_name == "CDB$ROOT",
            containerName=con_name,
            currentSchema=row[3] or "",
            currentUser=row[4] or "",
            sessionId=int(row[6]) if len(row) > 6 and row[6] else None,
            serialNumber=None,
        )

    def probe_capability(self, capability: Capability) -> CapabilityReport:
        sql = CAPABILITY_PROBES.get(capability)
        if sql is None:
            return CapabilityReport(
                capability=capability, available=False, detail="No probe is defined."
            )
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql)
                cur.fetchone()
        except Exception as exc:  # noqa: BLE001 - a failed probe is a normal answer
            return CapabilityReport(
                capability=capability,
                available=False,
                detail=_message(exc),
            )
        return CapabilityReport(capability=capability, available=True, detail="Probe succeeded.")

    # -- execution -----------------------------------------------------------------

    def enable_dbms_output(self, size_bytes: int) -> None:
        with self._conn.cursor() as cur:
            cur.callproc("dbms_output.enable", [max(size_bytes, 20000)])
        self._dbms_output_enabled = True

    def execute(
        self,
        statement: str,
        binds: dict[str, Any],
        kind: StatementKind,
        limits: ExecutionLimits,
        *,
        collect_dbms_output: bool = False,
    ) -> StatementResult:
        with self._lock:
            if collect_dbms_output and not self._dbms_output_enabled:
                self.enable_dbms_output(limits.max_dbms_output_bytes)
            # The driver round-trip limit is a second line of defence; the engine also
            # enforces a total operation deadline above this call.
            self._conn.call_timeout = int(limits.deadline_seconds * 1000)
            cur = None
            # Only set once the statement has run and its result is complete, so the
            # cleanup below can tell a failure that belongs to the statement from one
            # that happened after it was already applied.
            completed: StatementResult | None = None
            try:
                cur = self._conn.cursor()
                cur.arraysize = min(limits.max_rows, 500)
                cur.prefetchrows = cur.arraysize + 1
                cur.execute(statement, binds or {})
                result = StatementResult(statement_kind=kind)
                if cur.description:
                    result.result_set = self._fetch_bounded(cur, limits)
                else:
                    result.rows_affected = cur.rowcount
                    if kind in (StatementKind.DML, StatementKind.PLSQL_BLOCK):
                        self._transaction_open = True
                if kind == StatementKind.PLSQL_SOURCE:
                    errors = self._compiler_errors(statement)
                    if errors is None:
                        # ALL_ERRORS was never queried, so "no errors" would be a
                        # guess, and a guess of "valid" is the one that costs a user
                        # something. Say what was and was not checked instead.
                        log.warning("Could not identify the object the statement compiled")
                        result.warnings.append(
                            "The unit was submitted, but the harness could not work out "
                            "which object it created, so its compilation status was not "
                            "checked. Query ALL_ERRORS to confirm it is valid."
                        )
                    else:
                        result.compiler_errors = errors
                        if errors:
                            result.warnings.append(f"Compiled with {len(errors)} error(s).")
                    # DDL implicitly commits, so nothing is left pending afterwards.
                    self._transaction_open = False
                if kind == StatementKind.DDL:
                    self._transaction_open = False
                if kind == StatementKind.TRANSACTION_CONTROL and _ends_transaction(statement):
                    # The same resolution the toolbar buttons perform. Leaving the flag
                    # set would report pending work that no longer exists and would make
                    # the engine refuse the next DDL as implicitly committing.
                    self._transaction_open = False
                if collect_dbms_output:
                    # The statement has already run. Failing to read its output back
                    # is a gap in what we can report, not a failed execution, so it
                    # never turns an applied block into an error.
                    try:
                        lines, truncated = self._drain_dbms_output(limits.max_dbms_output_bytes)
                    except Exception as exc:  # noqa: BLE001 - the statement itself succeeded
                        log.warning("Could not read DBMS_OUTPUT back", exc_info=True)
                        result.warnings.append(
                            "The statement ran, but its DBMS_OUTPUT could not be read "
                            f"back: {_message(exc)}"
                        )
                    else:
                        result.dbms_output = lines
                        result.dbms_output_truncated = truncated
                result.bind_outputs = _read_out_binds(cur)
                completed = result
            except Exception as exc:  # noqa: BLE001 - normalise every driver round trip
                self._note_failure(exc)
                if kind == StatementKind.TRANSACTION_CONTROL and _is_commit(statement):
                    # A COMMIT is a COMMIT however it was entered. Routing it through the
                    # same translation as the toolbar keeps a connection lost in flight
                    # an unknown outcome rather than a clean failure inviting a retry.
                    raise self._translate_commit(exc) from exc
                raise self._translate(exc, kind) from exc
            finally:
                # Cleanup runs after the statement has already been sent. A failure
                # here is a fact about the connection, not about the statement, so it
                # never replaces a completed execution with an error: a write reported
                # as failed invites a retry that applies it twice. Both steps are
                # attempted whatever the first one does, and a driver error that means
                # the session is gone still marks the connection broken, so the session
                # is retired rather than leased out again.
                if cur is not None:
                    try:
                        cur.close()
                    except Exception as exc:  # noqa: BLE001 - the statement already ran
                        self._note_cleanup_failure(exc, completed, "close the cursor")
                try:
                    self._conn.call_timeout = 0
                except Exception as exc:  # noqa: BLE001 - a disconnected driver rejects this
                    self._note_cleanup_failure(exc, completed, "reset the driver round-trip limit")
            return result

    def _note_cleanup_failure(
        self, exc: Exception, completed: StatementResult | None, what: str
    ) -> None:
        """Record a cleanup step that failed after its statement had already run.

        ``completed`` is the result being returned, or ``None`` when the statement
        itself failed -- in which case its own error is the one worth reporting and
        this is only noted in the log.
        """

        log.warning("Could not %s after a statement", what, exc_info=True)
        self._note_failure(exc)
        if completed is not None:
            completed.warnings.append(
                f"The statement ran, but the harness could not {what} afterwards: {_message(exc)}"
            )

    def _fetch_bounded(self, cur: Any, limits: ExecutionLimits) -> ResultSet:
        columns = [
            ColumnMetadata(
                name=d[0],
                typeName=getattr(d[1], "name", str(d[1])),
                nullable=bool(d[6]) if len(d) > 6 else True,
                precision=d[4] if len(d) > 4 else None,
                scale=d[5] if len(d) > 5 else None,
                displaySize=d[2] if len(d) > 2 else None,
            )
            for d in cur.description
        ]
        rows: list[list[Any]] = []
        truncated = False
        reason: str | None = None
        used_bytes = 0
        while len(rows) < limits.max_rows:
            batch = cur.fetchmany(min(cur.arraysize, limits.max_rows - len(rows)))
            if not batch:
                break
            for raw in batch:
                shaped = [_shape(value, limits.lob_preview_bytes) for value in raw]
                used_bytes += sum(len(repr(v)) for v in shaped)
                if used_bytes > limits.max_response_bytes:
                    truncated = True
                    reason = "Response size limit reached."
                    break
                rows.append(shaped)
            if truncated:
                break
        if not truncated and len(rows) >= limits.max_rows and cur.fetchone() is not None:
            truncated = True
            reason = f"Row limit of {limits.max_rows} reached."
        return ResultSet(
            columns=columns,
            rows=rows,
            rowCount=len(rows),
            truncated=truncated,
            truncationReason=reason,
        )

    def _drain_dbms_output(self, budget: int) -> tuple[list[str], bool]:
        lines: list[str] = []
        used = 0
        truncated = False
        with self._conn.cursor() as cur:
            # GET_LINES takes DBMSOUTPUT_LINESARRAY, a PL/SQL index-by table, so the
            # bind has to be a collection: ``arrayvar`` makes one, ``var(...,
            # arraysize=n)`` makes a scalar the call rejects. See
            # https://python-oracledb.readthedocs.io/en/stable/user_guide/plsql_execution.html
            chunk = cur.arrayvar(str, _DBMS_OUTPUT_CHUNK_LINES)
            count = cur.var(int)
            # NUMLINES is IN OUT: on the way in it is how many lines we will take, on
            # the way out how many we actually got. The loop only continues on a full
            # chunk, so it stays at the requested size without being reset.
            count.setvalue(0, _DBMS_OUTPUT_CHUNK_LINES)
            while True:
                cur.callproc("dbms_output.get_lines", [chunk, count])
                fetched = chunk.getvalue()[: count.getvalue()]
                for line in fetched:
                    text = line or ""
                    size = len(text.encode("utf-8")) + 1
                    if used + size > budget:
                        truncated = True
                        break
                    lines.append(text)
                    used += size
                if truncated or count.getvalue() < _DBMS_OUTPUT_CHUNK_LINES:
                    break
        return lines, truncated

    def _compiler_errors(self, statement: str) -> list[CompilerError] | None:
        """Read ALL_ERRORS for the object the statement just compiled.

        None means the object could not be identified from the statement. That is not
        the same answer as an empty list: nothing was read, so nothing can be said
        about whether the unit is valid.
        """

        # Comments are masked first. The classifier that routed this statement here
        # ignores a leading comment; matching the raw text would not, and the unit
        # would then compile with errors and be reported as clean.
        match = _CREATED_UNIT.match(strip_literals_and_comments(statement).strip())
        if not match:
            return None
        obj_type = re.sub(r"\s+", " ", match.group(1)).upper()
        owner = _dictionary_name(match.group(2)) if match.group(2) else None
        name = _dictionary_name(match.group(3))
        sql = (
            "SELECT line, position, text, attribute, message_number FROM all_errors"
            " WHERE name = :name AND type = :type"
            " AND owner = NVL(:owner, SYS_CONTEXT('USERENV','CURRENT_SCHEMA'))"
            " ORDER BY sequence"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql, {"name": name, "type": obj_type, "owner": owner})
            return [
                CompilerError(
                    line=row[0],
                    position=row[1],
                    text=row[2],
                    attribute=row[3] or "ERROR",
                    messageNumber=row[4],
                )
                for row in cur.fetchall()
            ]

    # -- transaction and lifecycle -------------------------------------------------

    def commit(self) -> None:
        try:
            self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            raise self._translate_commit(exc) from exc
        self._transaction_open = False

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            raise self._translate(exc, StatementKind.TRANSACTION_CONTROL) from exc
        self._transaction_open = False

    def cancel(self) -> bool:
        try:
            self._conn.cancel()
        except Exception:  # noqa: BLE001 - the break itself may fail
            return False
        return True

    def close(self) -> None:
        if self._closed:
            return
        # Closing is best effort by design: a session that is already gone cannot be
        # rolled back, and raising here would mask the reason it is being closed.
        try:
            if self._transaction_open and not self._broken:
                self._conn.rollback()
        except Exception:  # noqa: BLE001,S110 - best effort on the way out
            pass
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001,S110 - the session is being discarded anyway
            pass
        self._closed = True

    def is_healthy(self) -> bool:
        if self._closed or self._broken:
            return False
        try:
            self._conn.ping()
        except Exception:  # noqa: BLE001
            self._broken = True
            return False
        return True

    @property
    def transaction_open(self) -> bool:
        return self._transaction_open

    @property
    def is_broken(self) -> bool:
        return self._broken

    # -- error mapping -------------------------------------------------------------

    def _note_failure(self, exc: Exception) -> None:
        if _error_code(exc) in _FATAL_CODES:
            self._broken = True

    def _translate_commit(self, exc: Exception) -> Exception:
        """A commit that lost its connection has an unknown outcome, not a failure.

        Oracle may have made the transaction durable and lost only the acknowledgement
        on the way back. Reporting that as a clean failure invites a retry that applies
        the work twice, so it is reported as ``outcome_unknown`` instead, and the caller
        retires the session rather than reusing it.
        """

        code = _error_code(exc)
        if code in _FATAL_CODES:
            return OutcomeUnknownError(
                "The connection was lost while the commit was in flight. Whether Oracle "
                "committed the transaction is unknown; verify in the database before "
                "retrying.",
                detail={"oracleCode": code, "message": _message(exc)},
            )
        return self._translate(exc, StatementKind.TRANSACTION_CONTROL)

    def _translate(self, exc: Exception, kind: StatementKind) -> Exception:
        code = _error_code(exc)
        message = _message(exc)
        if code == _CANCELLED_CODE:
            # The only way to stop a statement is to break the call, and Oracle reports
            # that break back as an error. Left as a generic ``OracleError`` the engine
            # has nothing to tell it apart from a statement Oracle refused, so work the
            # user deliberately cancelled is reported as ``failed`` -- sending them to
            # look for a bug in SQL that was simply stopped. The statement did reach the
            # database, so it started; ORA-01013 rolls back that statement alone, and
            # anything already done in the transaction is still pending.
            return CancelledError_(
                "The statement was cancelled before it finished. Oracle rolled it back; "
                "any earlier work in this transaction is still pending.",
                detail={"oracleCode": code, "message": message, "statementStarted": True},
            )
        if code in _FATAL_CODES:
            if kind in (
                StatementKind.DML,
                StatementKind.PLSQL_BLOCK,
                StatementKind.PLSQL_SOURCE,
                StatementKind.DDL,
            ):
                return OutcomeUnknownError(
                    "The connection was lost while the statement was in flight. Whether "
                    "Oracle applied it is unknown; verify before retrying.",
                    detail={"oracleCode": code, "message": message},
                )
            return ConnectionFailedError(message, detail={"oracleCode": code})
        if code in {"ORA-00942", "ORA-01031"}:
            return CapabilityError(message, detail={"oracleCode": code})
        return OracleError(message, oracle_code=code)


class OracleDbBackend(OracleBackend):
    name = "oracledb"

    def __init__(self, driver_mode: str = "thin", lib_dir: str | None = None) -> None:
        self._mode = driver_mode
        self._lib_dir = lib_dir

    def connect(self, spec: ConnectionSpec) -> OracleDbConnection:
        initialize(self._mode, self._lib_dir)
        assert oracledb is not None
        # Resolved before the session is opened so an unusable name fails without
        # spending a connection on it.
        schema_sql = _current_schema_sql(spec)
        params: dict[str, Any] = {
            "user": spec.username,
            "password": spec.password,
            "dsn": _dsn(spec),
            "tcp_connect_timeout": spec.connect_timeout_seconds,
        }
        if spec.wallet_dir:
            params["config_dir"] = spec.wallet_dir
            params["wallet_location"] = spec.wallet_dir
            if spec.wallet_password:
                params["wallet_password"] = spec.wallet_password
        try:
            connection = oracledb.connect(**params)
        except Exception as exc:  # noqa: BLE001
            raise ConnectionFailedError(
                f"Could not connect to {spec.dsn()}: {_message(exc)}",
                detail={"oracleCode": _error_code(exc), **spec.redacted()},
            ) from exc
        connection.autocommit = False
        connection.module = "OracleDBHarness"
        if schema_sql is not None:
            _apply_default_schema(connection, spec, schema_sql)
        return OracleDbConnection(spec, connection)


def _ends_transaction(statement: str) -> bool:
    return bool(_ENDS_TRANSACTION.match(strip_literals_and_comments(statement).strip()))


def _is_commit(statement: str) -> bool:
    return bool(_IS_COMMIT.match(strip_literals_and_comments(statement).strip()))


def _dictionary_name(token: str) -> str:
    """A parsed identifier as the data dictionary stores it."""

    return token[1:-1] if token.startswith('"') else token.upper()


def _current_schema_sql(spec: ConnectionSpec) -> str | None:
    """The ALTER SESSION that points unqualified names at the profile's schema.

    Identifiers cannot be bound, so the name is quoted here. Oracle folds an unquoted
    name to upper case, which is what a profile configured as ``hr`` means; a name that
    needs quoting to be legal is used exactly as it was configured.
    """

    schema = (spec.default_schema or "").strip()
    if not schema:
        return None
    try:
        identifier = quote_identifier(schema.upper() if is_simple_identifier(schema) else schema)
    except ValidationError as exc:
        raise ConfigurationError(
            f"Profile {spec.profile_id} has an unusable default schema: {exc.message}",
            detail={"defaultSchema": schema[:64]},
        ) from exc
    return f"ALTER SESSION SET CURRENT_SCHEMA = {identifier}"


def _apply_default_schema(connection: Any, spec: ConnectionSpec, sql: str) -> None:
    """Set the session's current schema, or fail the connection.

    Without this the session resolves unqualified names against the login user, so a
    profile that logs in as one account but works in an application schema would read
    and write the wrong objects. A schema that cannot be set is therefore a failed
    connection and not a warning: handing back the session anyway would run the user's
    statements somewhere other than where the profile says they run.
    """

    try:
        with connection.cursor() as cur:
            cur.execute(sql)
    except Exception as exc:  # noqa: BLE001 - reported as a connection failure
        try:
            connection.close()
        except Exception:  # noqa: BLE001,S110 - the session is being discarded anyway
            pass
        raise ConnectionFailedError(
            f"Connected to {spec.dsn()} but could not set the default schema to "
            f"{spec.default_schema}: {_message(exc)}",
            detail={
                "oracleCode": _error_code(exc),
                "defaultSchema": spec.default_schema,
                **spec.redacted(),
            },
        ) from exc


def _dsn(spec: ConnectionSpec) -> str:
    protocol = "tcps" if spec.protocol.lower() == "tcps" else "tcp"
    return (
        f"(DESCRIPTION=(ADDRESS=(PROTOCOL={protocol})(HOST={spec.host})(PORT={spec.port}))"
        f"(CONNECT_DATA=(SERVICE_NAME={spec.service_name})))"
    )


def _error_code(exc: Exception) -> str:
    error = getattr(exc, "args", [None])[0]
    code = getattr(error, "full_code", None)
    if code:
        return str(code)
    message = str(exc)
    for prefix in ("ORA-", "DPY-", "DPI-", "PLS-"):
        index = message.find(prefix)
        if index != -1:
            return message[index : index + len(prefix) + 5]
    return ""


def _message(exc: Exception) -> str:
    error = getattr(exc, "args", [None])[0]
    text = getattr(error, "message", None)
    return str(text or exc).strip()


def _shape(value: Any, lob_preview_bytes: int) -> Any:
    """Bound LOBs and make binary values JSON-safe."""

    read = getattr(value, "read", None)
    if callable(read) and hasattr(value, "size"):
        size = value.size()
        preview = read(1, min(lob_preview_bytes, size)) if size else ""
        if isinstance(preview, bytes):
            preview = preview.hex()
        return {
            "kind": "lob",
            "preview": preview,
            "byteLength": size,
            "truncated": size > lob_preview_bytes,
        }
    if isinstance(value, bytes):
        return {
            "kind": "raw",
            "preview": value[:lob_preview_bytes].hex(),
            "byteLength": len(value),
            "truncated": len(value) > lob_preview_bytes,
        }
    return value


def _read_out_binds(cur: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    bindvars = getattr(cur, "bindvars", None)
    if isinstance(bindvars, dict):
        for name, var in bindvars.items():
            try:
                values[name] = var.getvalue()
            except Exception:  # noqa: BLE001,S112 - an unreadable out bind is not fatal
                continue
    return values
