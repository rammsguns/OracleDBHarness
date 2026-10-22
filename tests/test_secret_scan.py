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
        "# Reviewed synthetic test endpoint.\nfixture.txt:ipv4:^endpoint=\n"
    )
    (repo / "fixture.txt").write_text("endpoint=10." + "23.45.67\n")
    (repo / "other.txt").write_text("-----BEGIN " + "PRIVATE KEY-----\n")
    git(repo, "add", ".")
    assert scan(repo, "fixture.txt").returncode == 0
    assert scan(repo).returncode == 1
    (repo / "scripts/secret_scan.allow").write_text("fixture.txt:ipv4:.*\n")
    assert scan(repo).returncode == 2


def test_excluded_directories_and_symlinks(repo: Path) -> None:
    for name in ("databases", "OrcaServer"):
        (repo / name).mkdir()
        (repo / name / "fixture").write_text("-----BEGIN " + "PRIVATE KEY-----\n")
    (repo / "link").symlink_to("databases/fixture")
    git(repo, "add", ".")
    assert scan(repo).returncode == 0


def planted(value: str) -> str:
    return (
        value.replace("<LIVE_SUFFIX>", "${PASSWORD}" + "LiveSuffix_9")
        .replace("<LIVE_WALLET>", "/" + "operator/wallet")
        .replace("<LIVE_EXAMPLE>", "Production_" + "example_9!")
        .replace("<LIVE_VALUE>", "Production_" + "9!")
        .replace("<LIVE_HOST>", "db." + "business.org")
        .replace("<LIVE_EMAIL>", "person@" + "business.org")
        .replace("<LIVE_SERVICE>", "tenant123_sales_medium.adb." + "oraclecloud.com")
    )


def tracked(repo: Path, name: str, content: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(planted(content))
    git(repo, "add", name)


@pytest.mark.parametrize(
    "domain",
    ["app.example", "app.internal", "app.test", "app.invalid", "localhost", "sub.example.com"],
)
def test_placeholder_domains_do_not_hide_real_hosts(repo: Path, domain: str) -> None:
    tracked(
        repo,
        "fixture.txt",
        f"person@{domain}\n" + ("u/" + f"fake@{domain}\n" if domain != "localhost" else ""),
    )
    assert scan(repo).returncode == 0
    tracked(repo, "fixture.txt", "<LIVE_EMAIL>\nu/" + "<LIVE_VALUE>@<LIVE_HOST>\n")
    assert scan(repo).returncode == 1
    assert "email" in scan(repo).stdout
    assert "oracle-connect" in scan(repo).stdout


@pytest.mark.parametrize(
    "key", ["secretReference", "passwordFile", "PASSWORD_FILE", "APP_SECRET_DIR", "TOKEN_REF"]
)
def test_reference_keys_do_not_hide_literal_credentials(repo: Path, key: str) -> None:
    tracked(repo, "config.json", '{"' + key + '": "credential-location"}\n')
    assert scan(repo).returncode == 0
    tracked(repo, "config.json", '{"password": "<LIVE_VALUE>"}\n')
    assert scan(repo).stdout == "config.json:1:credential-assignment\n"


@pytest.mark.parametrize("value", ["", "${PASSWORD}", "<PASSWORD>", "changeme", "example"])
def test_placeholder_values_are_complete_values(repo: Path, value: str) -> None:
    tracked(repo, "config.env", f"{'password'}={value!r}\n")
    assert scan(repo).returncode == 0
    tracked(repo, "config.env", 'password="<LIVE_EXAMPLE>"\n')
    assert scan(repo).returncode == 1


def test_source_expressions_types_and_generated_passwords(repo: Path) -> None:
    tracked(
        repo,
        "code.py",
        'password: str = resolver.read()\nmax_tokens = 500\naccount_password = "K7p_" + secrets.token_hex(8)\n',
    )
    assert scan(repo).returncode == 0
    tracked(repo, "code.py", 'password: str = "<LIVE_VALUE>"\napi_key = "<LIVE_VALUE>"\n')
    assert scan(repo).stdout.splitlines() == [
        "code.py:1:credential-assignment",
        "code.py:2:credential-assignment",
    ]


def test_synthetic_test_literals_do_not_exempt_test_files(repo: Path) -> None:
    tracked(repo, "tests/fixture.py", 'password = "test-secret"\n')
    assert scan(repo).returncode == 0
    tracked(repo, "tests/fixture.py", 'password = "<LIVE_VALUE>"\n')
    assert scan(repo).returncode == 1
    tracked(repo, "live.py", 'password = "test-secret"\n')
    assert "live.py:1:credential-assignment" in scan(repo).stdout


def test_cloud_placeholders_cannot_hide_tenancy_service(repo: Path) -> None:
    tracked(
        repo,
        "targets.json",
        '{"host":"adb.<region>.oraclecloud.com", "serviceName":"<tenancy>_<db>_medium.adb.oraclecloud.com"}\n',
    )
    assert scan(repo).returncode == 0
    tracked(
        repo,
        "targets.json",
        '{"host":"adb.<region>.oraclecloud.com", "serviceName":"<LIVE_SERVICE>"}\n',
    )
    assert scan(repo).stdout == "targets.json:1:cloud-host\n"


def test_documentation_addresses_do_not_hide_public_addresses(repo: Path) -> None:
    tracked(repo, "fixture.txt", "203.0.113.10\n")
    assert scan(repo).returncode == 0
    tracked(repo, "fixture.txt", "8." + "9.10.11\n")
    assert scan(repo).stdout == "fixture.txt:1:ipv4\n"


def test_rule_scoped_history_exception(repo: Path) -> None:
    tracked(repo, "claude_k7.md", "/home/" + "historic/repo\n")
    tracked(
        repo,
        "scripts/secret_scan.allow",
        "# Dated username path.\nclaude_k7.md:home-path:/home/[\\w.-]+/\n",
    )
    assert scan(repo).returncode == 0
    tracked(repo, "live.md", "/home/" + "operator/repo\n")
    tracked(repo, "claude_k7.md", "/home/" + 'historic/repo password="<LIVE_VALUE>"\n')
    assert scan(repo).stdout.splitlines() == [
        "claude_k7.md:1:credential-assignment",
        "live.md:1:home-path",
    ]


def test_package_lock_metadata_is_not_a_credential_exemption(repo: Path) -> None:
    tracked(
        repo,
        "package-lock.json",
        '{"resolved":"https://registry.npmjs.org/pkg/-/pkg-1.tgz", "integrity":"sha512-fakesample"}\n',
    )
    assert scan(repo).returncode == 0
    tracked(
        repo, "package-lock.json", '{"integrity":"sha512-fakesample", "password":"<LIVE_VALUE>"}\n'
    )
    assert scan(repo).returncode == 1


def test_ci_actions_and_local_service_connections(repo: Path) -> None:
    tracked(
        repo,
        ".github/workflows/ci.yml",
        "uses: astral-sh/setup-uv@v5\nPOSTGRES_PASSWORD: ci-only-not-a-secret\nurl: app/"
        + "ci-only-not-a-secret@localhost:1521/TEST\n",
    )
    assert scan(repo).returncode == 0
    tracked(
        repo,
        ".github/workflows/ci.yml",
        "POSTGRES_PASSWORD: <LIVE_VALUE>\nurl: app/" + "<LIVE_VALUE>@<LIVE_HOST>:1521/PROD\n",
    )
    assert scan(repo).returncode == 1
    tracked(repo, "production.txt", "app/" + "<LIVE_VALUE>@localhost:1521/PROD\n")
    assert "production.txt:1:oracle-connect" in scan(repo).stdout


def test_sql_substitutions_and_shell_prompts(repo: Path) -> None:
    tracked(repo, "script.sql", 'CONNECT owner/"fixture"@&1\n')
    tracked(repo, "script.sh", 'read -rs -p "SYSTEM password' + ': " P\npassword="$(cat file)"\n')
    assert scan(repo).returncode == 0
    tracked(repo, "script.sql", "CONNECT owner/" + "<LIVE_VALUE>@<LIVE_HOST>\n")
    tracked(repo, "script.sh", 'password="<LIVE_VALUE>"\n')
    assert scan(repo).returncode == 1


def test_environment_placeholder_suffix_remains_a_finding(repo: Path) -> None:
    tracked(repo, "config.env", 'password="<LIVE_SUFFIX>"\n')
    assert scan(repo).returncode == 1


def test_fixture_exceptions_do_not_exempt_their_files(repo: Path) -> None:
    entries = (SCRIPT.parent / "secret_scan.allow").read_text().splitlines()
    exceptions = []
    for entry in entries:
        if entry.startswith("tests/"):
            exceptions.extend(["# Reviewed fixture exception.", entry])
    tracked(repo, "tests/copilot/test_local_provider.py", "endpoint=8." + "9.10.11\n")
    tracked(repo, "tests/unit/test_bootstrap_targets.py", 'walletDir="<LIVE_WALLET>"\n')
    tracked(repo, "scripts/secret_scan.allow", "\n".join(exceptions) + "\n")
    assert scan(repo).stdout.splitlines() == [
        "tests/copilot/test_local_provider.py:1:ipv4",
        "tests/unit/test_bootstrap_targets.py:1:wallet-path",
    ]
