"""Scan tracked UTF-8 working-tree files and emit only file:line:rule findings."""

import ipaddress
import pathlib
import re
import shutil
import subprocess
import sys


def fail(message):
    print("secret_scan: " + message, file=sys.stderr)
    sys.exit(2)


git_executable = shutil.which("git")
if git_executable is None:
    fail("Git is required")

try:
    root = pathlib.Path(
        subprocess.check_output(  # noqa: S603 - fixed Git argv, no shell.
            [git_executable, "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL
        )
        .decode()
        .strip()
    )
    tracked = subprocess.check_output(  # noqa: S603 - fixed Git argv, no shell.
        [git_executable, "-C", str(root), "ls-files", "-z"]
    )
except subprocess.CalledProcessError:
    fail("run inside a Git repository")
names = {p.decode() for p in tracked.split(b"\0") if p}
requested = sys.argv[1:]
if requested and requested[0] == "--":
    requested = requested[1:]
selected = set()
for argument in requested:
    path = pathlib.Path(argument)
    try:
        name = (pathlib.Path.cwd() / path).absolute().relative_to(root).as_posix()
    except ValueError:
        fail("paths must be inside the repository")
    if name not in names:
        fail("each path must be a tracked file")
    selected.add(name)
if not requested:
    selected = names

# Exceptions apply to one rule in one exact file.
rule_names = {
    "private-key",
    "credential-assignment",
    "oracle-connect",
    "wallet-path",
    "cloud-host",
    "ipv4",
    "home-path",
    "email",
}
allow = {}
allow_path = root / "scripts/secret_scan.allow"
if allow_path.is_file() and "scripts/secret_scan.allow" in names:
    comment = False
    for entry in allow_path.read_text().splitlines():
        if entry.startswith("#") and entry[1:].strip():
            comment = True
        elif entry.strip():
            parts = entry.split(":", 2)
            if not comment or len(parts) != 3 or parts[1] not in rule_names:
                fail("allowlist entries need a preceding comment and path:rule:regex")
            path, rule, pattern = parts
            if path not in names:
                fail("allowlist paths must be exact tracked files")
            try:
                allow.setdefault((path, rule), []).append(re.compile(pattern))
            except re.error:
                fail("invalid allowlist regex")
            comment = False
        else:
            comment = False


def placeholder_domain(domain):
    domain = domain.lower().rstrip(".")
    return domain in {"localhost", "example.com", "example.org", "example.net"} or domain.endswith(
        (
            ".localhost",
            ".example",
            ".internal",
            ".test",
            ".invalid",
            ".example.com",
            ".example.org",
            ".example.net",
        )
    )


placeholder = re.compile(
    r"(?i)^(?:<[^>]+>|\$\{[A-Z_][A-Z_0-9]*\}|"
    r"change[-_]?me(?:[-_]in[-_]any[-_]shared[-_]environment)?|"
    r"placeholder|example|dummy|fake|test|redacted|none|null|true|false)$"
)
# Quoted literals everywhere; unquoted literals in config/shell files only.
assignment = re.compile(
    r"""(?ix)(?<![\w-])([\w-]+)["']?[ \t]*(?::[ \t]*(?:str|String)(?:[ \t]*\|[ \t]*None)?[ \t]*)?
    (?::|=(?!=))[ \t]*(?:"([^"\n]*)"|'([^'\n]*)'|([^\s,;\#\)\}]+))"""
)
secret_key = re.compile(r"(?i)(?:password|passwd|secret|token|apikey|accesskey|privatekey)")
reference_key = re.compile(
    r"(?i)(?:reference(?:_id)?|passwordfile|_file|_secret_dir|_ref|_dir|_name|_locator|_id)$"
)
rules = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "cloud-host": re.compile(r"\b[\w.-]+\.(?:oraclecloud|amazonaws)\.com\b", re.I),
    "home-path": re.compile(r"/home/(?!<)[\w.-]+/"),
    "email": re.compile(r"\b[\w.+-]+@([\w.-]+\.[A-Za-z]{2,}|localhost)\b"),
    "oracle-connect": re.compile(
        r"(?i)(?:\b[\w-]+" r"/" r"[^\s/@]+@[^\s]+|\b(?:jdbc\x3aoracle\x3a" r"|tcps?://)[^\s]+)"
    ),
    "wallet-path": re.compile(
        r"(?i)(?:wallet_location|wallet_root|walletdir|tns_admin)"
        r"[\"']?[ \t]*[:=][ \t]*[\"']?/(?![<$])[^\s\"']+"
    ),
}
ip_pattern = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
documentation = [
    ipaddress.ip_network(n)
    for n in ("127.0.0.0/8", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
]
# Exact reviewed synthetic literals; plausible new credentials in tests still fail.
fixture_values = {
    "password",
    "secret",
    "token",
    "pw",
    "p",
    "at",
    "at_123",
    "odbh_test",
    "odbh_x",
    "only-an-id-token",
    "qualification-only",
    "not-a-real-password",
    "test-secret",
    "Not-A-Real-Pw_42",
    "Not-A-Real-Pw_77",
    "admin",
    "w",
    "k",
    "test-key",
    "fixture",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJsb2FkIn0.c2lnbmF0dXJlLXNpZ25hdHVyZQ",
}
hits = 0
for name in sorted(selected):
    if name.split("/")[0] in {"OrcaServer", "databases"}:
        continue
    path = root / name
    if path.is_symlink() or not path.is_file():
        continue
    try:
        lines = path.read_bytes().decode("utf-8").splitlines()
    except UnicodeDecodeError:
        continue
    fixture = (
        name.startswith(("tests/", "integrations/dataforge/test/"))
        or ".test." in name
        or "/fixtures/" in name
    )
    config = path.suffix in {
        ".json",
        ".yaml",
        ".yml",
        ".env",
        ".example",
        ".sh",
    } or path.name.startswith(".env")
    for number, line in enumerate(lines, 1):
        found = set()
        for rule, pattern in rules.items():
            for match in pattern.finditer(line):
                value = match.group()
                if rule == "email" and placeholder_domain(match.group(1)):
                    continue
                if rule == "cloud-host" and line[: match.start()].endswith(("<region>.", "<db>")):
                    continue
                if rule == "oracle-connect":
                    if re.search(r"\buses:[ \t]*" + re.escape(value), line):
                        continue
                    if any(
                        marker in value
                        for marker in ("<password>", "{system_password}", "@&", "%s")
                    ):
                        continue
                    domain = re.search(r"@(?://)?([^:/\s]+)", value)
                    if (
                        domain
                        and placeholder_domain(domain[1])
                        and (domain[1] != "localhost" or name.startswith(".github/workflows/"))
                    ):
                        continue
                if not placeholder.fullmatch(value):
                    found.add(rule)
        for match in assignment.finditer(line):
            key, quoted_double, quoted_single, bare = match.groups()
            # Shell read prompts mention the field but contain no assigned credential.
            prompt = re.match(r'read .*?-p "[^"\n]*"', line.lstrip())
            if prompt and match.start() < len(line) - len(line.lstrip()) + prompt.end():
                continue
            words = re.sub(r"([a-z])([A-Z])", r"\1_\2", key).lower().split("_")
            if not any(secret_key.fullmatch(word) for word in words) and "".join(
                words[-2:]
            ) not in {"apikey", "accesskey", "privatekey"}:
                continue
            if reference_key.search(key) or key.lower() in {
                "secretreference",
                "passwordfile",
                "secretdir",
                "tokendir",
            }:
                continue
            if any(
                word
                in {
                    "tokens",
                    "endpoint",
                    "type",
                    "configured",
                    "hash",
                    "prefix",
                    "length",
                    "lifespan",
                }
                for word in words
            ):
                continue
            if bare is not None and (not config or bare.startswith(("$", "{", "<"))):
                continue
            value = (
                quoted_double
                if quoted_double is not None
                else quoted_single
                if quoted_single is not None
                else bare
            )
            if match.group(0).endswith(('"K7p_"', "'K7p_'")) and line[
                match.end() :
            ].lstrip().startswith("+ secrets.token_hex("):
                continue
            if (value.startswith("${") and value.endswith("}")) or value.startswith("$("):
                continue
            if bare is not None and line.lstrip().startswith("#") and ":" in match.group(0):
                continue
            if not value or placeholder.fullmatch(value):
                continue
            if value == "ci-only-not-a-secret" and name.startswith(".github/workflows/"):
                continue
            if fixture and value in fixture_values:
                continue
            found.add("credential-assignment")
        for match in ip_pattern.finditer(line):
            try:
                address = ipaddress.ip_address(match.group())
            except ValueError:
                continue
            if int(address) != 0 and not any(address in network for network in documentation):
                found.add("ipv4")
        for rule in sorted(found):
            if any(pattern.search(line) for pattern in allow.get((name, rule), [])):
                continue
            print(f"{name}:{number}:{rule}")
            hits += 1
sys.exit(1 if hits else 0)
