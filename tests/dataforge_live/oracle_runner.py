"""B1 real DataForge / local provider / Oracle regression evidence.

Failures are evidence, not permission to silently replace a real dependency with a fixture.
"""

from __future__ import annotations

import http.server
import json
import secrets
import shlex
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import oracledb
from sqlalchemy import select

from harness_api.db import build_engine, build_session_factory
from harness_api.models import ConnectionProfile, CopilotRequest
from tests.dataforge_live.oracle_stack import DEVELOPER, MODEL, VIEWER, OracleHarness
from tests.dataforge_live.real_runner import (
    ADAPTER_VERSION,
    PROTOCOL_VERSION,
    CheckResult,
    DataForgeProcess,
    RealRunReport,
    _get_git_commit,
    check_auth_and_protocol,
    check_capabilities,
    check_outage_and_disabled,
    check_proposal_apply,
    parse_sse_events,
)
from tests.dataforge_live.stack import ADMIN_SUBJECT, REPOSITORY, _free_port

LABEL = "agent-qualified on test stand-ins (AI agent, not a human)"
SELECTION = "SELECT 1 AS B1_MARKER FROM dual"


def assist_body(**changes: Any) -> dict[str, Any]:
    body = {
        "action": "explain",
        "connectionId": "load-db-1",
        "schema": "HARNESS_APP",
        "selection": SELECTION,
        "editorId": "b1-editor",
        "editorRevision": "rev-1",
        "question": "Explain this selected Oracle statement briefly. Include one fenced sql block containing the unchanged selected SQL.",
    }
    body.update(changes)
    return body


def stream_assist(
    client: httpx.Client, url: str, body: dict[str, Any]
) -> tuple[list[tuple[str, dict]], int]:
    chunks = []
    with client.stream("POST", f"{url}/api/ai/chat", json=body, timeout=240) as response:
        response.raise_for_status()
        assert "text/event-stream" in response.headers.get("content-type", "")
        for chunk in response.iter_bytes():
            if chunk:
                chunks.append(chunk)
    return parse_sse_events(b"".join(chunks).decode()), len(chunks)


class _FailingProvider(http.server.BaseHTTPRequestHandler):
    """Forward one real local-model request, then corrupt its stream after real text."""

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length", "0")))
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.headers.get("Authorization", ""),
        }
        with httpx.stream(
            "POST",
            "http://127.0.0.1:8080/v1/chat/completions",
            content=body,
            headers=headers,
            timeout=180,
        ) as upstream:
            upstream.raise_for_status()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for line in upstream.iter_lines():
                self.wfile.write((line + "\n").encode())
                self.wfile.flush()
                if line.startswith("data: {"):
                    data = json.loads(line[6:])
                    if any(c.get("delta", {}).get("content") for c in data.get("choices", [])):
                        self.wfile.write(b"\ndata: {invalid-json\n\n")
                        self.wfile.flush()
                        break

    def log_message(self, format: str, *args: Any) -> None:
        pass


def dataforge_session_count(target_dsn: str) -> int:
    """Observe D1 as SYSTEM; read credentials without ever echoing them."""
    values = {}
    for line in (REPOSITORY / "databases/qual/r1.env").read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if parts and "=" in parts[-1]:
            key, value = parts[-1].split("=", 1)
            values[key] = value.replace("${REPO}", str(REPOSITORY)).replace(
                "$REPO", str(REPOSITORY)
            )
    assert values["HARNESS_QUAL_ADMIN_DSN"] == target_dsn, "Admin DSN must be D1"
    assert values["HARNESS_QUAL_ADMIN_USER"].lower() == "system", "D1 SYSTEM required"
    with (
        oracledb.connect(
            user=values["HARNESS_QUAL_ADMIN_USER"],
            password=Path(values["HARNESS_QUAL_ADMIN_PASSWORD_FILE"]).read_text().rstrip("\r\n"),
            dsn=target_dsn,
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*) FROM v$session WHERE "
            "LOWER(program) LIKE '%dataforge%' OR LOWER(program) LIKE '%node%' "
            "OR LOWER(module) LIKE '%dataforge%' OR LOWER(module) LIKE '%node%'"
        )
        return int(cursor.fetchone()[0])


def run_oracle_integration(dataforge_dir: Path, targets: Path, workdir: Path) -> RealRunReport:
    stack = OracleHarness(workdir, targets)
    checks: list[CheckResult] = []
    metadata: dict[str, Any] = {
        "setupSeconds": None,
        "oracleVersions": {},
        "model": MODEL,
        "backend": "oracledb",
        "driverMode": "thin",
        "adapterProtocol": "1.1",
        "harnessProtocol": "1.5",
        "proxy": "nginx:1.27-alpine",
        "notExercised": [
            "Editor/UI and confirmation dialogs",
            "Upstream integration",
            "Production and human pilot qualification",
        ],
    }
    df: DataForgeProcess | None = None

    def check(name: str, fn: Callable[[], str]) -> None:
        start = time.monotonic()
        try:
            detail = fn()
            checks.append(CheckResult(name, True, (time.monotonic() - start) * 1000, detail))
        except Exception as exc:  # noqa: BLE001
            # HTTP body/driver exceptions are deliberately not echoed; statuses and assertions suffice.
            detail = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
            checks.append(
                CheckResult(
                    name, False, (time.monotonic() - start) * 1000, detail or "assertion failed"
                )
            )

        print(f"B1 {name}: {'PASS' if checks[-1].ok else 'FAIL'}", flush=True)

    try:
        stack.start()
        metadata["oracleVersions"] = stack.versions
        token = stack.issue_dataforge_credential("b1-dataforge")
        df = DataForgeProcess(dataforge_dir, _free_port(), stack.proxy_url, token)
        df.start()
        setup_start = time.monotonic()
        metadata["setupIncluded"] = (
            "From DataForge configured, Express healthy and disposable harness plus nginx running: minting development/viewer/administrator principal tokens, admin-authenticated target/profile count and D1 SYSTEM session baseline, GET connection list, capability negotiation, scripted selected-statement assist request, and consumption through its successful done event. Excludes provisioning PostgreSQL, target bootstrap/probes, provider health check, credential issuance and process startup. No human interaction."
        )
        assert stack._settings is not None
        engine = build_engine(stack._settings)
        sessions = build_session_factory(engine)
        dev = stack._dev_token(DEVELOPER, ["developer", "dba"])
        viewer = stack._dev_token(VIEWER, ["viewer"])
        with httpx.Client(timeout=240) as client:
            admin = stack._dev_token(ADMIN_SUBJECT, ["administrator"])
            admin_headers = {"Authorization": f"Bearer {admin}"}

            def registered_targets() -> list[dict[str, Any]]:
                # Targets are the public API representation of connection profiles.
                response = client.get(f"{stack.proxy_url}/api/v1/targets", headers=admin_headers)
                response.raise_for_status()
                return response.json()

            targets_before = registered_targets()
            assert len(targets_before) == 2, "Admin must see exactly D1/D2"
            sessions_before = dataforge_session_count(stack.config.targets[0].dsn)
            before = client.get(f"{df.base_url}/api/connections").json()
            checks.append(check_capabilities(client, df.base_url))
            captured: list[tuple[str, dict]] = []

            def streaming() -> str:
                events, chunks = stream_assist(client, df.base_url, assist_body())
                captured.extend(events)
                names = [name for name, _ in events]
                assert chunks > 1, f"Only {chunks} network chunks"
                assert (
                    names[0] == "start"
                    and names[-1] == "done"
                    and "delta" in names
                    and "error" not in names
                ), f"Event sequence: {names}"
                assert dict(events[-1][1]).get("outcome") == "succeeded", (
                    "Non-success terminal event"
                )
                metadata["setupSeconds"] = round(time.monotonic() - setup_start, 3)
                return f"{chunks} chunks and {len(events)} events through shipped nginx; local model returned a successful answer"

            check("streaming_chat", streaming)

            def context() -> str:
                start = next(data for name, data in captured if name == "start")
                preview = start["contextPreview"]
                categories = [a["category"] for a in preview["attachments"]]
                assert categories == ["selected_source"], (
                    f"Unexpected context categories: {categories}"
                )
                assert preview["targetReference"].endswith(":load-db-1:HARNESS_APP")
                assert start["provider"] == "local" and start["isFixtureProvider"] is False
                assert client.get(f"{df.base_url}/api/connections").json() == before
                return "Real start event confirms selected_source only, D1 opaque target reference, local provider and unchanged DataForge connection list"

            check("context_and_connection_registry", context)

            def oracle_explain() -> str:
                assert captured and captured[-1][0] == "done"
                start = next(data for name, data in captured if name == "start")
                assert start["contextPreview"]["targetReference"].endswith(":load-db-1:HARNESS_APP")
                assert captured[-1][1].get("outcome") == "succeeded"
                assert client.get(f"{df.base_url}/api/connections").json() == before
                assert registered_targets() == targets_before, (
                    "Harness target/profile registry changed"
                )
                with sessions() as db:
                    assert len(db.scalars(select(ConnectionProfile)).all()) == 2
                sessions_after = dataforge_session_count(stack.config.targets[0].dsn)
                assert sessions_after == sessions_before, "D1 DataForge/node session count changed"
                metadata["selectedStatementExplain"] = {
                    "harnessTargetsBefore": len(targets_before),
                    "harnessTargetsAfter": len(registered_targets()),
                    "d1DataForgeSessionsBefore": sessions_before,
                    "d1DataForgeSessionsAfter": sessions_after,
                    "liveExplainPlan": "Not a feature: no integration bridge exists, by design",
                }
                return f"Selected D1 statement explained successfully with D1 target reference; DataForge connections unchanged, admin API targets/profiles 2 -> 2, D1 DataForge/node sessions {sessions_before} -> {sessions_after}; live EXPLAIN PLAN has no bridge, by design"

            check("selected_statement_oracle_explain", oracle_explain)

            def proposals() -> str:
                # Keep A6's helper, with its fixed editor context, using a separately scripted request.
                events, _ = stream_assist(
                    client,
                    df.base_url,
                    assist_body(
                        action="propose",
                        connectionId="conn-real-1",
                        selection="SELECT 1 FROM dual",
                        editorId="editor-live-1",
                        question="Change the literal 1 to 2. Return only one fenced sql block containing the complete replacement SELECT 2 FROM dual",
                    ),
                )
                proposal = next(
                    (data.get("proposalId") for name, data in events if name == "proposal"), None
                )
                result = check_proposal_apply(client, df.base_url, proposal)
                assert result.ok, result.detail
                return result.detail

            check("proposal_apply_check", proposals)
            headers = {"Authorization": f"Bearer {dev}"}
            d2 = stack.profiles["load-db-2"]
            suffix = secrets.token_hex(4).upper()
            table = f"B1_MARKERS_{suffix}"
            procedure = f"B1_PROC_{suffix}"
            target = stack.config.targets[1]
            connection = oracledb.connect(
                user=target.user,
                password=Path(target.password_file).read_text().rstrip("\r\n"),
                dsn=target.dsn,
            )
            created: list[tuple[str, str]] = []
            worksheet_ids: list[str] = []
            try:

                def worksheet() -> str:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            f"CREATE TABLE {table} (run_id VARCHAR2(40) NOT NULL, target_name VARCHAR2(100) NOT NULL, user_name VARCHAR2(255) NOT NULL, seq NUMBER(12) NOT NULL, created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL, PRIMARY KEY (run_id, user_name, seq))"
                        )
                    created.append(("TABLE", table))
                    opened = client.post(
                        f"{stack.proxy_url}/api/v1/worksheets",
                        headers=headers,
                        json={"profileId": d2},
                    )
                    opened.raise_for_status()
                    sid = opened.json()["session"]["sessionId"]
                    worksheet_ids.append(sid)
                    for seq, action, expected in ((1, "commit", 1), (2, "rollback", 0)):
                        result = client.post(
                            f"{stack.proxy_url}/api/v1/worksheets/{sid}/execute",
                            headers=headers,
                            json={
                                "statement": f"INSERT INTO {table} (run_id, target_name, user_name, seq) VALUES ('b1', 'D2', 'harness_app', {seq})"
                            },
                        )
                        result.raise_for_status()
                        assert result.json()["outcome"]["state"] == "succeeded", (
                            "Worksheet insert failed"
                        )
                        done = client.post(
                            f"{stack.proxy_url}/api/v1/worksheets/{sid}/{action}", headers=headers
                        )
                        done.raise_for_status()
                        with connection.cursor() as cursor:
                            cursor.execute(
                                f"SELECT COUNT(*) FROM {table} WHERE seq = :seq", seq=seq
                            )
                            assert cursor.fetchone()[0] == expected, (
                                f"{action} not visible from independent Oracle connection"
                            )
                    return "D2 worksheet commit persisted seq=1; rollback discarded seq=2, verified independently in Oracle"

                check("worksheet_commit_rollback", worksheet)

                def compile_procedure() -> str:
                    # Record ownership before dispatch: teardown also covers a response lost after DDL.
                    created.append(("PROCEDURE", procedure))
                    source = f"CREATE OR REPLACE PROCEDURE {procedure} AS BEGIN NULL; END;"
                    response = client.post(
                        f"{stack.proxy_url}/api/v1/plsql/compile",
                        headers=headers,
                        json={"profileId": d2, "source": source},
                    )
                    response.raise_for_status()
                    assert response.json()["compiled"] is True, (
                        "Compile route did not report compiled"
                    )
                    with connection.cursor() as cursor:
                        cursor.execute(f"ALTER PROCEDURE {procedure} COMPILE")
                        cursor.execute(
                            "SELECT status FROM user_objects WHERE object_name = :name AND object_type = 'PROCEDURE'",
                            name=procedure,
                        )
                        assert cursor.fetchone() == ("VALID",)
                    return "Throwaway D2 procedure created through harness compile route; recompiled and verified VALID in Oracle"

                check("plsql_compile", compile_procedure)

                def confirmation() -> str:
                    response = client.post(
                        f"{stack.proxy_url}/api/v1/runbooks/runbook.recompile_object/run",
                        headers=headers,
                        json={
                            "profileId": d2,
                            "parameters": {
                                "owner": "HARNESS_APP",
                                "object_name": procedure,
                                "object_kind": "PROCEDURE",
                            },
                            "confirm": False,
                        },
                    )
                    assert response.status_code in (400, 403, 409), (
                        f"Unconfirmed operation HTTP {response.status_code}"
                    )
                    assert "confirmation" in response.text.lower(), (
                        "Refused for a reason other than explicit confirmation"
                    )
                    return "Administrative recompile of owned throwaway procedure refused without explicit confirmation"

                check("confirmation_required", confirmation)
            finally:
                for sid in worksheet_ids:
                    client.delete(f"{stack.proxy_url}/api/v1/worksheets/{sid}", headers=headers)
                errors = []
                for kind, name in reversed(created):
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute(
                                f"DROP {kind} {name}" + (" PURGE" if kind == "TABLE" else "")
                            )
                    except oracledb.DatabaseError as exc:
                        if exc.args[0].code not in (942, 4043):
                            errors.append(f"{kind} drop failed")
                connection.close()
                checks.append(
                    CheckResult(
                        "oracle_object_teardown",
                        not errors,
                        0,
                        "; ".join(errors)
                        or "All owned throwaway objects dropped; worksheets closed",
                    )
                )

            def role_restrictions() -> str:
                vh = {"Authorization": f"Bearer {viewer}"}
                worksheet_response = client.post(
                    f"{stack.proxy_url}/api/v1/worksheets", headers=vh, json={"profileId": d2}
                )
                assert worksheet_response.status_code == 403, (
                    f"Viewer worksheet HTTP {worksheet_response.status_code}"
                )
                compiled = client.post(
                    f"{stack.proxy_url}/api/v1/plsql/compile",
                    headers=vh,
                    json={
                        "profileId": d2,
                        "source": "CREATE OR REPLACE PROCEDURE B1_VIEWER_DENIED AS BEGIN NULL; END;",
                    },
                )
                assert compiled.status_code == 403, f"Viewer compile HTTP {compiled.status_code}"
                response = client.post(
                    f"{stack.proxy_url}/api/v1/copilot/requests",
                    headers=vh,
                    json={
                        "action": "explain",
                        "targetReference": "b1-viewer",
                        "userMessage": "Explain SELECT 1 FROM dual in one sentence",
                        "attachments": [
                            {
                                "category": "selected_source",
                                "name": "selection",
                                "content": SELECTION,
                            }
                        ],
                    },
                )
                events = parse_sse_events(response.text)
                assert response.status_code == 200, f"Viewer assist HTTP {response.status_code}"
                assert any(name == "delta" for name, _ in events)
                assert events[-1][0] == "done" and events[-1][1].get("outcome") == "succeeded"
                metadata["viewerPolicy"] = (
                    "Worksheet and compile 403; supplied-text assist allowed, intended PERMISSION_READ behavior"
                )

                # Copy source so temporary named users cannot alter the real checkout's store.
                analyst_dir = workdir / "analyst-dataforge"
                analyst_dir.mkdir()
                for folder in ("server", "src"):
                    shutil.copytree(dataforge_dir / folder, analyst_dir / folder)
                for name in ("node_modules", "package.json", "dist"):
                    (analyst_dir / name).symlink_to(dataforge_dir / name)
                analyst_df = DataForgeProcess(analyst_dir, _free_port(), stack.proxy_url, token)
                try:
                    analyst_df.start()
                    password = secrets.token_urlsafe(24)
                    admin_auth = httpx.BasicAuth("b1-admin@example.invalid", password)
                    analyst_auth = httpx.BasicAuth("b1-analyst@example.invalid", password)
                    for role, email, auth in (
                        ("Administrator", "b1-admin@example.invalid", None),
                        ("Analyst", "b1-analyst@example.invalid", admin_auth),
                    ):
                        created_user = client.post(
                            f"{analyst_df.base_url}/api/users",
                            auth=auth,
                            json={
                                "name": f"B1 {role}",
                                "email": email,
                                "role": role,
                                "password": password,
                            },
                        )
                        created_user.raise_for_status()
                    with httpx.Client(auth=analyst_auth, timeout=240) as analyst_client:
                        caps = analyst_client.get(f"{analyst_df.base_url}/api/ai/capabilities")
                        caps.raise_for_status()
                        assert caps.json()["actions"] == ["explain"], (
                            "Analyst actions not restricted"
                        )
                        allowed, _ = stream_assist(
                            analyst_client, analyst_df.base_url, assist_body()
                        )
                        preview = next(
                            data["contextPreview"] for name, data in allowed if name == "start"
                        )
                        categories = [a["category"] for a in preview["attachments"]]
                        assert categories == ["selected_source"], "Analyst received schema context"
                        assert (
                            allowed[-1][0] == "done"
                            and allowed[-1][1].get("outcome") == "succeeded"
                        )
                        denied, _ = stream_assist(
                            analyst_client,
                            analyst_df.base_url,
                            assist_body(action="diagnose", question="Explain SELECT 1 briefly"),
                        )
                        assert any(name == "error" for name, _ in denied) and not any(
                            name in ("start", "delta") for name, _ in denied
                        ), (
                            "Viewer worksheet/compile 403 and assist allowed; Analyst capabilities explain-only and no schema context, but non-allowed diagnose was accepted"
                        )
                    return "Viewer worksheet and compile refused (403), supplied-text assist allowed as intended; authenticated DataForge Analyst offered explain only, no schema context, non-allowed action refused"
                finally:
                    analyst_df.stop()
                    shutil.rmtree(analyst_dir)

            check("viewer_role_restrictions", role_restrictions)

            def cancellation() -> str:
                request_id = None
                with client.stream(
                    "POST",
                    f"{df.base_url}/api/ai/chat",
                    json=assist_body(
                        question="Explain this SQL at length, giving at least 30 detailed paragraphs"
                    ),
                    timeout=180,
                ) as response:
                    pending = ""
                    for text in response.iter_text():
                        pending += text
                        events = parse_sse_events(pending)
                        request_id = next(
                            (data["requestId"] for name, data in events if name == "start"),
                            request_id,
                        )
                        if any(name == "delta" for name, _ in events):
                            break
                assert request_id, "No request id before abort"
                for _ in range(100):
                    with sessions() as db:
                        record = db.get(CopilotRequest, request_id)
                        if record is not None and record.outcome != "running":
                            assert record.outcome == "cancelled", (
                                f"Abort terminal outcome={record.outcome}"
                            )
                            return f"Client aborted after first real delta; harness record {request_id} is cancelled"
                    time.sleep(0.2)
                raise AssertionError("Aborted assist still running after 20s")

            check("stream_cancellation", cancellation)

            def partial_failure() -> str:
                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FailingProvider)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                stack.stop_api()
                original = stack.env["HARNESS_COPILOT_BASE_URL"]
                try:
                    stack.env["HARNESS_COPILOT_BASE_URL"] = (
                        f"http://127.0.0.1:{server.server_port}/v1"
                    )
                    stack.start_api()
                    events, _ = stream_assist(
                        client,
                        df.base_url,
                        assist_body(question="Explain SELECT 1 FROM dual briefly"),
                    )
                    names = [name for name, _ in events]
                    assert "delta" in names and "error" in names, f"Partial event sequence {names}"
                    error = next(data for name, data in events if name == "error")
                    assert error.get("code") == "provider_failure", (
                        f"Wrong typed error {error.get('code')}"
                    )
                    return "Real local model delta followed by injected malformed provider SSE; DataForge returned typed provider_failure"
                finally:
                    stack.stop_api()
                    stack.env["HARNESS_COPILOT_BASE_URL"] = original
                    stack.start_api()
                    server.shutdown()
                    server.server_close()

            check("partial_provider_failure", partial_failure)

            def outage() -> str:
                stack.stop_api()
                response = client.post(f"{df.base_url}/api/ai/chat", json=assist_body())
                events = parse_sse_events(response.text)
                assert any(
                    name == "error" and data.get("code") in ("harness_unreachable", "harness_error")
                    for name, data in events
                ), f"Missing typed outage error on stopped harness: {[name for name, _ in events]}"
                assert client.get(f"{df.base_url}/api/health").status_code == 200
                code = next(data["code"] for name, data in events if name == "error")
                return f"Stopped actual harness API; DataForge returned typed {code} (nginx upstream 502) while non-AI health remained available"

            check("stopped_harness_outage", outage)
            stack.start_api()
        engine.dispose()
        checks.extend(check_outage_and_disabled(dataforge_dir, token)[1:])
        checks.extend(check_auth_and_protocol(dataforge_dir, stack.proxy_url))
    except Exception as exc:  # noqa: BLE001
        checks.append(CheckResult("stack_or_run_failure", False, 0, type(exc).__name__))
    finally:
        if df is not None:
            df.stop()
        stack.stop()
    passed = sum(c.ok for c in checks)
    return RealRunReport(
        label=LABEL,
        harness_build=_get_git_commit(REPOSITORY),
        dataforge_commit=_get_git_commit(dataforge_dir),
        adapter_version=ADAPTER_VERSION,
        protocol_version=PROTOCOL_VERSION,
        checks=checks,
        ok=all(c.ok for c in checks),
        summary=f"{passed}/{len(checks)} checks passed; real Oracle / local model / DataForge; no human qualification",
        environment=metadata,
    )
