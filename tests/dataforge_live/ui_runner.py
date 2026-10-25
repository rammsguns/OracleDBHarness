"""B2 browser qualification against disposable Oracle/model stand-ins only.

Run with ``uv run --group browser --extra oracle --extra postgres python -m
tests.dataforge_live.ui_runner --targets databases/capacity/a2-run.json``.
Oracle and Playwright imports stay inside the live runner so offline CI can import helpers.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx

from harness_api.db import build_engine, build_session_factory
from harness_api.models import CopilotRequest
from tests.capacity.driver import split_dsn
from tests.dataforge_live.oracle_stack import MODEL, OracleHarness
from tests.dataforge_live.real_runner import (
    ADAPTER_VERSION,
    PROTOCOL_VERSION,
    CheckResult,
    DataForgeProcess,
    RealRunReport,
    _get_git_commit,
)
from tests.dataforge_live.stack import REPOSITORY, _free_port

LABEL = "agent-qualified on test stand-ins (AI agent, not a human)"
SELECT = "SELECT 1 FROM dual"


def screenshot_name(number: int, name: str) -> str:
    """Stable names, rejecting traversal and unsafe report paths."""
    if number < 1 or not name or any(c not in "abcdefghijklmnopqrstuvwxyz_" for c in name):
        raise ValueError("Invalid screenshot name")
    return f"{number:02d}-{name}.png"


def copy_frontend(source: Path, destination: Path) -> None:
    destination.mkdir()
    for folder in ("server", "src"):
        shutil.copytree(source / folder, destination / folder)
    for name in ("node_modules", "package.json", "dist"):
        (destination / name).symlink_to(source / name)


def run_ui(dataforge_dir: Path, targets: Path, workdir: Path, screenshots: Path) -> RealRunReport:
    from playwright.sync_api import sync_playwright

    stack = OracleHarness(workdir / "stack", targets)
    checks: list[CheckResult] = []
    metadata: dict[str, Any] = {
        "screenshots": [],
        "model": MODEL,
        "driverMode": "thin",
        "python": sys.version.split()[0],
        "playwright": version("playwright"),
        "oracledb": version("oracledb"),
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "dataforgePackage": json.loads((dataforge_dir / "package.json").read_text())["version"],
        "harnessProtocol": "1.5",
        "adapterProtocol": PROTOCOL_VERSION,
        "proxy": "nginx:1.27-alpine (shipped config)",
        "notExercised": [
            "Production",
            "Human pilot",
            "Compile and confirmation UI",
        ],
    }
    df: DataForgeProcess | None = None
    engine = None
    screenshots.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        stack.start()
        metadata["oracleVersions"] = stack.versions
        token = stack.issue_dataforge_credential("b2-dataforge-ui")
        clone = workdir / "dataforge"
        copy_frontend(dataforge_dir, clone)
        password = secrets.token_urlsafe(24)
        admin_email = "b2-admin@example.invalid"
        analyst_email = "b2-analyst@example.invalid"
        bootstrap = secrets.token_urlsafe(32)
        df = DataForgeProcess(
            clone,
            _free_port(),
            stack.proxy_url,
            token,
            extra_env={
                "HOST": "0.0.0.0",
                "DATAFORGE_AUTH_TOKEN": bootstrap,
                "DATAFORGE_ENCRYPTION_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
            },
        )
        df.start()
        with httpx.Client(
            base_url=df.base_url, auth=("dataforge", bootstrap), timeout=30
        ) as client:
            for role, email in (("Administrator", admin_email), ("Analyst", analyst_email)):
                client.post(
                    "/api/users",
                    json={"name": f"B2 {role}", "email": email, "role": role, "password": password},
                ).raise_for_status()
            target = stack.config.targets[0]
            host, port, service = split_dsn(target.dsn, target.name)
            result = client.post(
                "/api/connections",
                json={
                    "name": "B2 D1 stand-in",
                    "engine": "oracle",
                    "host": host,
                    "port": port,
                    "database": service,
                    "user": target.user,
                    "password": Path(target.password_file).read_text().rstrip("\r\n"),
                },
            )
            result.raise_for_status()
            connection_id = result.json()["id"]
        assert stack._settings is not None
        engine = build_engine(stack._settings)
        sessions = build_session_factory(engine)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            metadata["chromium"] = browser.version
            context = browser.new_context(
                http_credentials={"username": admin_email, "password": password},
                viewport={"width": 1440, "height": 1100},
            )
            page = context.new_page()
            page.set_default_timeout(30000)
            page.goto(df.base_url)
            panel = page.get_by_role("region", name="Assist panel")
            editor = page.get_by_role("textbox", name="SQL editor", exact=True)

            def check(number: int, name: str, action: Any) -> None:
                start = time.monotonic()
                try:
                    detail = action()
                    checks.append(
                        CheckResult(name, True, (time.monotonic() - start) * 1000, detail)
                    )
                except Exception as exc:  # noqa: BLE001
                    # Exceptions may contain request bodies. Keep only safe exception types.
                    checks.append(
                        CheckResult(
                            name,
                            False,
                            (time.monotonic() - start) * 1000,
                            type(exc).__name__ + ": " + str(exc)[:200]
                            if isinstance(exc, AssertionError)
                            else type(exc).__name__,
                        )
                    )
                path = screenshots / screenshot_name(number, name)
                page.screenshot(path=str(path), full_page=True)
                metadata["screenshots"].append(str(path.relative_to(REPOSITORY)))
                print(f"B2 {name}: {'PASS' if checks[-1].ok else 'FAIL'}", flush=True)

            def review(question: str, action: str = "explain") -> None:
                panel.get_by_label("Assist action").select_option(action)
                panel.get_by_label("Assist question").fill(question)
                panel.get_by_role("button", name="Review outgoing context", exact=True).click()
                panel.get_by_label("Outgoing context").wait_for()

            def send(action: str = "explain") -> None:
                panel.get_by_role("button", name=f"Send {action}", exact=True).click()

            def completed() -> None:
                page.wait_for_function(
                    "document.querySelector('[data-testid=assist-status]')?.textContent === 'succeeded'",
                    timeout=240000,
                )

            def sign_in_review() -> str:
                editor.wait_for()
                page.get_by_label("Worksheet connection").select_option(connection_id)
                editor.fill(SELECT)
                editor.select_text()
                page.get_by_role("button", name="Assist", exact=True).click()
                review("Explain this Oracle SQL in five short paragraphs.")
                preview = panel.get_by_label("Outgoing context").inner_text()
                assert SELECT in preview and "HARNESS_APP" in preview and connection_id in preview
                metadata["reviewScreenshot"] = "01-sign_in_context.png"
                return "Named Administrator authenticated by DataForge Basic sign-in; D1 worksheet selection and server-resolved target/schema reviewed before send"

            check(1, "sign_in_context", sign_in_review)

            def streaming() -> str:
                page.evaluate(
                    """() => { window.b2Renders = []; const el = document.querySelector('[data-testid=assist-answer]'); window.b2Observer = new MutationObserver(() => { if (el.textContent) window.b2Renders.push(el.textContent.length); }); window.b2Observer.observe(el, {childList:true, characterData:true, subtree:true}); }"""
                )
                send()
                completed()
                renders = page.evaluate("window.b2Observer.disconnect(); window.b2Renders")
                chunks = int(
                    panel.get_by_test_id("assist-answer").get_attribute("data-updates") or "0"
                )
                assert len(set(renders)) > 1 and chunks > 1
                assert "local" in panel.inner_text()
                metadata["streaming"] = {
                    "visibleRenderUpdates": len(set(renders)),
                    "deltaEvents": chunks,
                }
                return f"{len(set(renders))} distinct DOM answer renders, {chunks} delta events; local provider/model shown; terminal succeeded"

            check(2, "streamed_answer", streaming)

            def proposals() -> str:
                editor.fill("SELECT broken_column FROM dual")
                editor.select_text()
                review(
                    "Repair the unknown column: return only one fenced sql block containing the complete replacement SELECT 1 FROM dual",
                    "propose",
                )
                send("propose")
                completed()
                panel.get_by_label("Proposal diff").wait_for()
                path = screenshots / "03-proposal-review.png"
                page.screenshot(path=str(path), full_page=True)
                metadata["screenshots"].append(str(path.relative_to(REPOSITORY)))
                panel.get_by_role("button", name="Apply proposal", exact=True).click()
                page.wait_for_function(
                    "document.querySelector('[aria-label=\"Assist panel\"]')?.textContent.includes('Applied to editor')"
                )
                assert "broken_column" not in editor.input_value()
                applied = editor.input_value()
                editor.fill(applied + " -- changed after review")
                panel.get_by_role("button", name="Apply proposal", exact=True).click()
                page.wait_for_function(
                    "document.querySelector('[aria-label=\"Assist panel\"]')?.textContent.includes('Apply refused')"
                )
                assert editor.input_value() == applied + " -- changed after review"
                metadata["staleReason"] = panel.get_by_role("alert").inner_text()
                return "Real proposal diff reviewed; apply replaced buffer, then edited buffer refused with server reasons; apply did not execute SQL"

            check(3, "proposal_stale_refusal", proposals)

            def cancellation() -> str:
                editor.fill(SELECT)
                editor.select_text()
                review("Explain this SQL at length in at least thirty detailed paragraphs.")
                send()
                page.wait_for_function(
                    "Number(document.querySelector('[data-testid=assist-answer]')?.dataset.updates) > 0",
                    timeout=180000,
                )
                request_id = panel.get_by_test_id("assist-answer").get_attribute("data-request-id")
                panel.get_by_role("button", name="Cancel", exact=True).click()
                assert panel.get_by_test_id("assist-status").inner_text() == "cancelled"
                for _ in range(100):
                    with sessions() as db:
                        record = db.get(CopilotRequest, request_id)
                        if record is not None and record.outcome == "cancelled":
                            metadata["cancelledRequestId"] = request_id
                            return "Browser AbortController cancelled mid-stream; UI cancelled and harness record independently verified cancelled"
                    time.sleep(0.2)
                raise AssertionError("Cancellation not recorded")

            check(4, "cancelled", cancellation)

            def outage() -> str:
                stack.stop_api()
                panel.get_by_role("button", name="Refresh integration status").click()
                page.wait_for_function(
                    "document.querySelector('[aria-label=\"Assist panel\"]')?.textContent.includes('unavailable') || document.querySelector('[aria-label=\"Assist panel\"]')?.textContent.includes('could not be reached')"
                )
                editor.fill(SELECT)
                page.get_by_role("button", name="Run", exact=True).first.click()
                page.wait_for_function("document.body.textContent.includes('1 row')", timeout=60000)
                metadata["outageMessage"] = panel.get_by_role("status").inner_text()
                stack.start_api()
                return "Actual harness API stopped; ordinary integration message visible; SELECT 1 FROM dual ran through DataForge's own D1 connection"

            check(5, "outage_worksheet", outage)

            def analyst() -> str:
                nonlocal page, panel
                analyst_context = browser.new_context(
                    http_credentials={"username": analyst_email, "password": password},
                    viewport={"width": 1440, "height": 1100},
                )
                page = analyst_context.new_page()
                page.goto(df.base_url)
                panel = page.get_by_role("region", name="Assist panel")
                panel.get_by_label("Assist action").locator("option").wait_for(state="attached")
                assert panel.get_by_label("Assist action").locator(
                    "option"
                ).all_text_contents() == ["explain"]
                return "Named Analyst documentation panel offers exactly explain; no worksheet privileges added"

            check(6, "analyst_explain_only", analyst)

            def disabled() -> str:
                nonlocal page
                assert df is not None
                df.stop()
                df.harness_enabled = False
                df.start()
                page = context.new_page()
                page.goto(df.base_url)
                page.get_by_role("textbox", name="SQL editor", exact=True).wait_for()
                page.wait_for_function(
                    "async () => (await (await fetch('/api/ai/capabilities')).json()).enabled === false"
                )
                assert page.get_by_role("button", name="Assist", exact=True).count() == 0
                assert page.get_by_role("region", name="Assist panel").count() == 0
                disabled_analyst = browser.new_context(
                    http_credentials={"username": analyst_email, "password": password}
                )
                analyst_page = disabled_analyst.new_page()
                analyst_page.goto(df.base_url)
                analyst_page.get_by_title(
                    "Signed in as B2 Analyst — open account settings", exact=True
                ).wait_for()
                analyst_page.wait_for_function(
                    "async () => (await (await fetch('/api/ai/capabilities')).json()).enabled === false"
                )
                assert analyst_page.get_by_role("region", name="Assist panel").count() == 0
                assert analyst_page.get_by_role("button", name="Assist", exact=True).count() == 0
                disabled_analyst.close()
                return "Disabled integration hides worksheet Assist control and Analyst documentation Assist panel"

            check(7, "disabled_assist_hidden", disabled)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        checks.append(CheckResult("stack_or_run_failure", False, 0, type(exc).__name__))
    finally:
        if engine is not None:
            engine.dispose()
        if df is not None:
            df.stop()
        stack.stop()
    metadata["durationSeconds"] = round(time.monotonic() - started, 3)
    return RealRunReport(
        LABEL,
        _get_git_commit(REPOSITORY),
        _get_git_commit(dataforge_dir),
        ADAPTER_VERSION,
        PROTOCOL_VERSION,
        checks,
        bool(checks) and all(c.ok for c in checks),
        f"{sum(c.ok for c in checks)}/{len(checks)} browser scenarios passed",
        metadata,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataforge-dir", type=Path, default=Path("/home/kiwi/harness-drills/dataforge")
    )
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY / "databases/reports/2026-10-24-b2-dataforge-ui.md",
    )
    parser.add_argument(
        "--screenshots", type=Path, default=REPOSITORY / "databases/reports/2026-10-24-b2-ui"
    )
    args = parser.parse_args()
    # Under the repository: Docker cannot mount secrets from its private /tmp.
    staging = REPOSITORY / "databases/reports"
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="b2-work-", dir=staging) as tmp:
        report = run_ui(args.dataforge_dir, args.targets, Path(tmp), args.screenshots.absolute())
    args.report.write_text(report.to_markdown() + "\n")
    args.report.with_suffix(".json").write_text(report.to_json() + "\n")
    print(report.summary)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
