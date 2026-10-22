#!/usr/bin/env bash
# Scan tracked working-tree files; Python 3 supplies regex and IP parsing.
set -euo pipefail
exec python3 - "$@" <<'PY'
import ipaddress
import pathlib
import re
import subprocess
import sys


def fail(message):
    print("secret_scan: " + message, file=sys.stderr)
    sys.exit(2)


try:
    root = pathlib.Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL
    ).decode().strip())
    tracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
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

allow = {}
allow_path = root / "scripts/secret_scan.allow"
if allow_path.is_file() and "scripts/secret_scan.allow" in names:
    comment = False
    for entry in allow_path.read_text().splitlines():
        if entry.startswith("#") and entry[1:].strip():
            comment = True
        elif entry.strip():
            if not comment or ":" not in entry:
                fail("allowlist entries need a preceding comment and path:regex")
            path, pattern = entry.split(":", 1)
            try:
                allow.setdefault(path, []).append(re.compile(pattern))
            except re.error:
                fail("invalid allowlist regex")
            comment = False
        else:
            comment = False

placeholder = re.compile(
    r"^(?:<[^>]+>|\$.*|\{.*|.*\bexample\.(?:com|org)\b.*|"
    r"(?i:placeholder|change[-_]?me|replace[-_]?.*|your[-_].*|dummy|fake|test|"
    r"password|secret|token|redacted|none|null|true|false|localhost))$"
)
assignment = re.compile(
    r"""(?ix)\b[\w-]*(?:password|passwd|secret|token)[\w-]*["']?\s*[:=]\s*
    (?:"([^"\n]*)"|'([^'\n]*)'|([^\s,;\#\)\}]+))"""
)
rules = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "cloud-host": re.compile(r"\b[\w.-]+\.(?:oraclecloud|amazonaws)\.com\b", re.I),
    "home-path": re.compile(r"/home/(?!<)[\w.-]+/"),
    "email": re.compile(r"\b[\w.+-]+@([\w.-]+\.[A-Za-z]{2,})\b"),
    "oracle-connect": re.compile(r"(?i)(?:\b[\w]+/[^\s/@]+@[^\s]+|\b(?:jdbc:oracle:|tcps?://)[^\s]+)"),
    "wallet-path": re.compile(r"(?i)(?:wallet_location|wallet_root|tns_admin)\s*[:=]\s*[\"']?/(?![<$])[^\s\"']+"),
}
ip_pattern = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
documentation = [ipaddress.ip_network(n) for n in
                 ("127.0.0.0/8", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
hits = 0
for name in sorted(selected):
    # Never follow symlinks or inspect excluded local stores, even if tracked.
    if name.split("/")[0] in {"OrcaServer", "databases"}:
        continue
    path = root / name
    if path.is_symlink() or not path.is_file():
        continue
    try:
        lines = path.read_bytes().decode("utf-8").splitlines()
    except UnicodeDecodeError:
        continue
    for number, line in enumerate(lines, 1):
        if any(pattern.search(line) for pattern in allow.get(name, [])):
            continue
        found = set()
        for rule, pattern in rules.items():
            for match in pattern.finditer(line):
                value = match.group()
                if rule == "email" and (
                    match.group(1).lower() in {"example.com", "example.org"}
                    or match.group(1).lower().endswith((".example.com", ".example.org"))
                ):
                    continue
                if not placeholder.fullmatch(value):
                    found.add(rule)
        for match in assignment.finditer(line):
            value = next(v for v in match.groups() if v is not None)
            if value and not placeholder.fullmatch(value):
                found.add("credential-assignment")
        for match in ip_pattern.finditer(line):
            try:
                address = ipaddress.ip_address(match.group())
            except ValueError:
                continue
            if address != ipaddress.ip_address("0.0.0.0") and not any(
                address in network for network in documentation
            ):
                found.add("ipv4")
        for rule in sorted(found):
            print(f"{name}:{number}:{rule}")
            hits += 1
sys.exit(1 if hits else 0)
PY
