"""A file secret reference can only read inside the configured secret directory.

The locator is stored in the metadata store and editable by an administrator, so it
is input. The containment check has to be a path relationship: comparing strings
lets ``../secrets-other/key`` through, because ``/run/secrets`` is a string prefix
of ``/run/secrets-other``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_api.models import SecretReference
from harness_api.secrets import SecretResolver
from harness_worker.errors import ConfigurationError, NotFoundError


def _reference(locator: str) -> SecretReference:
    return SecretReference(id="sec_test", name="test", provider="file", locator=locator)


@pytest.fixture
def secret_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "app.password").write_text("inside\n", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "key").write_text("nested value", encoding="utf-8")
    return root


def test_a_file_inside_the_directory_is_read(secret_root: Path) -> None:
    resolver = SecretResolver(str(secret_root))
    assert resolver.resolve(_reference("app.password")) == "inside"
    assert resolver.resolve(_reference("nested/key")) == "nested value"
    # Going up and back down again still lands inside, so it is allowed.
    assert resolver.resolve(_reference("nested/../app.password")) == "inside"


def test_a_sibling_directory_sharing_the_name_prefix_is_refused(secret_root: Path) -> None:
    sibling = secret_root.parent / "vault-other"
    sibling.mkdir()
    (sibling / "file").write_text("outside", encoding="utf-8")

    resolver = SecretResolver(str(secret_root))
    with pytest.raises(ConfigurationError, match="outside the configured secret directory"):
        resolver.resolve(_reference("../vault-other/file"))


@pytest.mark.parametrize(
    "locator",
    ["../outside.txt", "../../outside.txt", "nested/../../outside.txt"],
)
def test_parent_traversal_is_refused(secret_root: Path, locator: str) -> None:
    (secret_root.parent / "outside.txt").write_text("outside", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        SecretResolver(str(secret_root)).resolve(_reference(locator))


def test_an_absolute_locator_outside_the_directory_is_refused(secret_root: Path) -> None:
    outside = secret_root.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        SecretResolver(str(secret_root)).resolve(_reference(str(outside)))


def test_the_directory_itself_is_not_a_secret(secret_root: Path) -> None:
    with pytest.raises(ConfigurationError):
        SecretResolver(str(secret_root)).resolve(_reference("."))


def test_a_symlink_leading_out_of_the_directory_is_refused(secret_root: Path) -> None:
    outside = secret_root.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = secret_root / "link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("This platform does not let the test user create symlinks.")
    with pytest.raises(ConfigurationError):
        SecretResolver(str(secret_root)).resolve(_reference("link"))


def test_a_missing_file_inside_the_directory_is_not_found(secret_root: Path) -> None:
    with pytest.raises(NotFoundError):
        SecretResolver(str(secret_root)).resolve(_reference("absent.password"))
