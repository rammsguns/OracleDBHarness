"""Shared exclusive live-database locks for qualification and canary runners."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def lock_names(dsns: list[str]) -> list[str]:
    """Normalize Easy Connect aliases and sort unique database identities."""
    identities = set()
    for dsn in dsns:
        identity = dsn.strip().removeprefix("//").lower()
        if not identity:
            continue
        host, separator, service = identity.partition("/")
        host = host.replace("127.0.0.1:", "localhost:").replace("[::1]:", "localhost:")
        if ":" not in host:
            host += ":1521"
        identity = host + separator + service
        identities.add(hashlib.sha256(identity.encode()).hexdigest())
    return sorted(identities)


@contextmanager
def database_locks(
    dsns: list[str], description: str, *, repository: Path = REPO, timeout: float | None = None
) -> Iterator[None]:
    """Hold all database locks until the runner and its child command finish."""
    if timeout is None:
        timeout = float(os.environ.get("HARNESS_DB_LOCK_TIMEOUT_SECONDS", "7200"))
    if timeout < 0:
        raise ValueError("Database lock timeout must be nonnegative")
    directory = repository / "databases/locks"
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        for name in lock_names(dsns):
            path = directory / f"{name}.lock"
            lock = stack.enter_context(path.open("a+"))
            announced = False
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not announced:
                        lock.seek(0)
                        holder = lock.read().strip() or "holder metadata pending"
                        print(
                            f"waiting for lock held by {holder} ({path})",
                            file=sys.stderr,
                            flush=True,
                        )
                        announced = True
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out waiting for database lock {name}") from None
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            lock.seek(0)
            lock.truncate()
            lock.write(f"{description}; PID={os.getpid()}\n")
            lock.flush()
        yield


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--description", required=True)
    parser.add_argument("--dsn", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required")
    with database_locks(args.dsn, args.description):
        return subprocess.run(command, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
