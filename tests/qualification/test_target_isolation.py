"""Two configured targets are two databases, according to the databases.

``tests/oracle_config.py`` refuses a second DSN spelled the same as the first, and the
API-level check in ``tests/integration/test_targets_and_access.py`` shows a committed
change on one target is not visible on the other. Neither can tell that two different
spellings - a host alias and an IP, two services on one PDB - lead to the same container,
in which case the isolation run proves nothing. Only the databases can say that.
"""

from __future__ import annotations

from harness_worker.backend import OracleConnection
from harness_worker.types import ExecutionLimits
from tests.oracle_config import OracleTestConfig
from tests.qualification.databases import SAME_CONTAINER, identify, relation
from tests.qualification.evidence import Evidence


def test_the_second_target_is_a_different_database(
    connection: OracleConnection,
    second_target_connection: OracleConnection,
    oracle_config: OracleTestConfig,
    evidence: Evidence,
    limits: ExecutionLimits,
) -> None:
    assert oracle_config.second is not None
    primary = identify(connection, limits)
    second = identify(second_target_connection, limits)
    found = relation(primary, second)

    evidence.note(
        "Second target identity",
        f"{found}. Primary: {primary.describe()}. Second: {second.describe()}.",
    )
    assert found != SAME_CONTAINER, (
        f"{oracle_config.dsn} and {oracle_config.second.dsn} reach the same container "
        f"({primary.describe()}). The two-target isolation checks would pass without "
        "demonstrating anything."
    )
    assert second.session_user.upper() == oracle_config.second.username.upper(), (
        "the second target's session is not authenticated as its own account"
    )
