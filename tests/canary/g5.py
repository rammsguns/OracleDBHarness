"""G5 requalification bound to the P1 non-CDB shape and D1 staging twin."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tests.canary import production
from tests.canary.rehearsal import load_admin_settings

REPO = Path(__file__).resolve().parents[2]
OFFICIAL = REPO / "databases/reports/2026-10-25-g3-canary"
APPROVER = "Claude (AI agent, acting DBA), not a human"
LABEL = production.LABEL
SUITES = ["tests/qualification", "tests/integration", "tests/e2e"]


def validate_directory(directory: Path) -> Path:
    directory = directory.resolve()
    official = OFFICIAL.resolve()
    if directory == official or directory in official.parents or official in directory.parents:
        raise ValueError("G5 report directory must be separate from the official C4 window")
    return directory


def decision(before: dict[str, str], after: dict[str, str], results: dict[str, int]) -> str:
    return (
        "PROMOTE"
        if set(results) == {"r1", "r3", "p1"}
        and all(code == 0 for code in results.values())
        and all(before.get(k) and after.get(k) for k in ("D1", "P1", "oracledb"))
        and before == after
        and after["D1"] == after["P1"]
        else "BLOCK"
    )


def probe() -> dict[str, str]:
    import oracledb

    versions = {"oracledb": oracledb.__version__}
    for target, profile, expected in (
        ("D1", "r1", "localhost:1522/ORCL"),
        ("P1", "p1", "localhost:1523/PRODCDB"),
    ):
        settings = load_admin_settings(REPO / f"databases/qual/{profile}.env", REPO)
        if settings["HARNESS_QUAL_ADMIN_DSN"] != expected:
            raise ValueError("Unexpected staging/production DSN; refusing connection")
        password = settings.get("HARNESS_QUAL_ADMIN_PASSWORD")
        if password is None:
            password = Path(settings["HARNESS_QUAL_ADMIN_PASSWORD_FILE"]).read_text().strip()
        with oracledb.connect(
            user=settings["HARNESS_QUAL_ADMIN_USER"], password=password, dsn=expected
        ) as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT version_full FROM v$instance")
                versions[target] = str(cursor.fetchone()[0])
    return versions


def canary_allowed(now: datetime) -> bool:
    local = now.astimezone(ZoneInfo("America/Mexico_City"))
    return not 8 * 60 + 50 <= local.hour * 60 + local.minute < 9 * 60 + 30


def commands(directory: Path) -> dict[str, list[str]]:
    return {
        **{
            profile: [
                "bash",
                "scripts/qual_run.sh",
                profile,
                "--d1-only",
                "--report-path",
                str(directory / f"{profile}.md"),
                *SUITES,
            ]
            for profile in ("r1", "r3")
        },
        "p1": [
            "uv",
            "run",
            "--extra",
            "oracle",
            "python",
            "-m",
            "tests.canary.production",
            "pass",
            "--report-dir",
            str(directory),
        ],
    }


def version_evidence(
    change: str, before: dict[str, str], after: dict[str, str], previous: Path | None
) -> tuple[dict[str, Any], list[str]]:
    old = before
    if previous is not None:
        prior = json.loads(previous.read_text())
        if prior.get("label") != LABEL or prior.get("decision") != "PROMOTE":
            raise ValueError("Previous record must be a successful labelled G5 record")
        old = prior["after"]
    reasons = []
    if "->" in change:
        source, destination = (part.strip() for part in change.split("->", 1))
        if source.startswith("python-oracledb "):
            key, declared_old = "oracledb", source.removeprefix("python-oracledb ")
            keys = [key]
        elif source.startswith("oracle RU "):
            declared_old = source.removeprefix("oracle RU ")
            keys = ["D1", "P1"]
        else:
            raise ValueError("Unsupported version-change description")
        for key in keys:
            if old.get(key) != declared_old:
                reasons.append(f"{key}: declared old version lacks matching probe evidence")
            if after.get(key) != destination:
                reasons.append(f"{key}: installed version does not match declared new version")
    return {
        "old": old,
        "new": after,
        "previous_record": str(previous) if previous else None,
    }, reasons


def run(change: str, directory: Path, dry_run: bool = False, previous: Path | None = None) -> int:
    directory = validate_directory(directory)
    plan = commands(directory)
    if dry_run:
        print(
            json.dumps(
                {
                    "label": LABEL,
                    "change": change,
                    "commands": plan,
                    "probe": "D1/P1 v$instance.version_full and installed oracledb, before/after",
                    "budget_source": str(OFFICIAL / "budget.json"),
                    "previous_record": str(previous) if previous else None,
                },
                indent=2,
            )
        )
        return 0
    # A fresh directory prevents stale passes from being mistaken for this run's evidence.
    directory.mkdir(parents=True, exist_ok=False)
    record: dict[str, Any] = {
        "label": LABEL,
        "approver": APPROVER,
        "change": change,
        "at": datetime.now(UTC).isoformat(),
        "decision": "BLOCK",
        "commands": plan,
        "baseline": {"RU": "19.9.1.0.210119", "oracledb": "26.0.1"},
        "version_note": "before/after are actual probes of this run, not proof of a historical installation",
        "declared_change": change.split(" -> ", 1),
        "results": {},
    }
    try:
        budget = production.read_budget(OFFICIAL)
        budget_record = json.loads((OFFICIAL / "budget.json").read_text())
        budget_record["ceilings"] = budget
        production.write_json(directory / "budget.json", budget_record)
        record["before"] = probe()
        for name, command in plan.items():
            if name == "p1" and not canary_allowed(datetime.now(UTC)):
                record["results"][name] = 1
                record["failure"] = "P1 pass refused during 08:50–09:30 CST timer exclusion"
                continue
            with (directory / f"{name}.log").open("w") as output:
                result = subprocess.run(
                    command, cwd=REPO, stdout=output, stderr=subprocess.STDOUT, check=False
                )  # noqa: S603 - fixed local commands
            record["results"][name] = result.returncode
        record["after"] = probe()
        record["versions"], record["version_failures"] = version_evidence(
            change, record["before"], record["after"], previous
        )
        record["decision"] = decision(record["before"], record["after"], record["results"])
        if record["version_failures"]:
            record["decision"] = "BLOCK"
    except Exception as exc:
        record["failure"] = f"Requalification failed ({type(exc).__name__}); details withheld"
    production.write_json(directory / "g5.json", record)
    (directory / "g5.md").write_text(
        f"# G5 P1 shape requalification\n\n{LABEL}\n\nApprover: {APPROVER}\n\n"
        f"```json\n{json.dumps(record, indent=2)}\n```\n"
    )
    print(json.dumps(record, indent=2))
    return 0 if record["decision"] == "PROMOTE" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--change", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--previous-record", type=Path)
    args = parser.parse_args()
    try:
        return run(args.change, args.report_dir, args.dry_run, args.previous_record)
    except (ValueError, OSError) as exc:
        print(
            f"G5 refused ({type(exc).__name__}); use a fresh, separate report directory",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
