"""Skipping a check whose environment is missing, unless the run said it needs it."""

from __future__ import annotations

import pytest

from tests import oracle_config as qual_config


def skip_or_fail(config: qual_config.OracleTestConfig, area: str, reason: str) -> None:
    """Skip - or fail, when ``HARNESS_QUAL_REQUIRE`` names ``area``.

    A skip is the right answer for a partial run. For the run that is meant to close a
    gate it is the wrong one, because a skipped check and a passing one look the same in a
    summary line.
    """

    if config.requires(area):
        pytest.fail(f"{qual_config.ENV_REQUIRE} names {area!r}: {reason}")
    pytest.skip(reason)
