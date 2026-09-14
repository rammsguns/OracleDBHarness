"""Which database a session is really on, and how two of them relate.

A DBID alone does not decide whether two targets are separate: databases cloned from one
image (common with prebuilt container images) share it while being entirely separate. So
the comparison is on the container *and* the instance serving it.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness_worker.backend import OracleConnection
from harness_worker.types import ExecutionLimits, StatementKind

DATABASE_SQL = """
SELECT SYS_CONTEXT('USERENV', 'DBID'),
       SYS_CONTEXT('USERENV', 'CON_DBID'),
       SYS_CONTEXT('USERENV', 'CON_NAME'),
       SYS_CONTEXT('USERENV', 'DB_UNIQUE_NAME'),
       SYS_CONTEXT('USERENV', 'INSTANCE_NAME'),
       SYS_CONTEXT('USERENV', 'SERVER_HOST'),
       SYS_CONTEXT('USERENV', 'SESSION_USER')
  FROM dual
"""

SAME_CONTAINER = "same container"


@dataclass(frozen=True)
class DatabaseIdentity:
    dbid: str
    con_dbid: str
    container: str
    unique_name: str
    instance: str
    server_host: str
    session_user: str

    @property
    def instance_key(self) -> tuple[str, str]:
        return (self.server_host.lower(), self.instance.lower())

    def describe(self) -> str:
        return (
            f"DBID `{self.dbid}`, CON_DBID `{self.con_dbid}`, CON_NAME `{self.container}`, "
            f"DB_UNIQUE_NAME `{self.unique_name}`, instance `{self.instance}` on "
            f"`{self.server_host}`, user `{self.session_user}`"
        )


def identify(connection: OracleConnection, limits: ExecutionLimits) -> DatabaseIdentity:
    result = connection.execute(DATABASE_SQL, {}, StatementKind.QUERY, limits)
    assert result.result_set is not None and result.result_set.rows
    return DatabaseIdentity(*(str(value or "") for value in result.result_set.rows[0]))


def relation(primary: DatabaseIdentity, second: DatabaseIdentity) -> str:
    """How two targets relate. :data:`SAME_CONTAINER` means they are not two targets."""

    same_instance = primary.instance_key == second.instance_key
    if same_instance and primary.con_dbid == second.con_dbid:
        return SAME_CONTAINER
    if same_instance:
        return "two containers served by one instance (PDBs of one CDB)"
    if primary.dbid == second.dbid:
        return "separate instances of databases sharing a DBID (clones of one database)"
    return "separate databases on separate instances"
