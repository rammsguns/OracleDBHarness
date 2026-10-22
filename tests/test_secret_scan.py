"""Exercise the tracked-file scanner without using repository secrets."""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "secret_scan.sh"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    return tmp_path


def scan(repo: Path, *paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *paths], cwd=repo, text=True, capture_output=True)


def test_planted_key_and_address(repo: Path) -> None:
    (repo / "planted.txt").write_text(
        "-----BEGIN " + "PRIVATE KEY-----\nendpoint=10." + "23.45.67\n"
    )
    git(repo, "add", "planted.txt")
    result = scan(repo)
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "planted.txt:1:private-key",
        "planted.txt:2:ipv4",
    ]
    assert "BEGIN" not in result.stdout


def test_placeholders_and_untracked_files(repo: Path) -> None:
    (repo / "clean.txt").write_text(
        "password=<PASSWORD>\nsecret=\nemail=person@example.com\n"
        "127.9.8.7 0.0.0.0 192.0.2.1 198.51.100.2 203.0.113.3\n"
    )
    (repo / "untracked.txt").write_text("-----BEGIN " + "PRIVATE KEY-----\n")
    git(repo, "add", "clean.txt")
    assert scan(repo).returncode == 0
    assert scan(repo, "clean.txt").returncode == 0
    assert scan(repo, "untracked.txt").returncode == 2


def test_commented_allowlist_and_explicit_selection(repo: Path) -> None:
    (repo / "scripts").mkdir()
    (repo / "scripts/secret_scan.allow").write_text(
        "# Reviewed synthetic test endpoint.\nfixture.txt:^endpoint=\n"
    )
    (repo / "fixture.txt").write_text("endpoint=10." + "23.45.67\n")
    (repo / "other.txt").write_text("-----BEGIN " + "PRIVATE KEY-----\n")
    git(repo, "add", ".")
    assert scan(repo, "fixture.txt").returncode == 0
    assert scan(repo).returncode == 1
    (repo / "scripts/secret_scan.allow").write_text("fixture.txt:.*\n")
    assert scan(repo).returncode == 2


def test_excluded_directories_and_symlinks(repo: Path) -> None:
    for name in ("databases", "OrcaServer"):
        (repo / name).mkdir()
        (repo / name / "fixture").write_text("-----BEGIN " + "PRIVATE KEY-----\n")
    (repo / "link").symlink_to("databases/fixture")
    git(repo, "add", ".")
    assert scan(repo).returncode == 0
