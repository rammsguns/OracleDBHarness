"""Offline checks: importing the browser runner requires no optional drivers."""

import pytest

from tests.dataforge_live.ui_runner import screenshot_name


def test_screenshot_names_are_stable() -> None:
    assert screenshot_name(2, "streamed_answer") == "02-streamed_answer.png"


@pytest.mark.parametrize("name", ["../token", "", "secret.png", "a/b"])
def test_screenshot_names_reject_paths(name: str) -> None:
    with pytest.raises(ValueError):
        screenshot_name(1, name)
