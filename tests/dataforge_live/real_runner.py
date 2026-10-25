"""Runs end-to-end integration checks against a real OracleDataForge server."""

from __future__ import annotations

import http.server
import json
import os
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import httpx

from tests.dataforge_live.stack import LiveHarness, _free_port

ADAPTER_VERSION = "0.1.0"
PROTOCOL_VERSION = "1.1"
REPORT_LABEL = "AI agent run on test stand-ins"


@dataclass
class CheckResult:
    name: str
    ok: bool
    duration_ms: float
    detail: str


@dataclass
class RealRunReport:
    label: str
    harness_build: str
    dataforge_commit: str
    adapter_version: str
    protocol_version: str
    checks: list[CheckResult]
    ok: bool
    summary: str
    environment: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {
            "label": self.label,
            "harnessBuild": self.harness_build,
            "dataforgeCommit": self.dataforge_commit,
            "adapterVersion": self.adapter_version,
            "protocolVersion": self.protocol_version,
            "ok": self.ok,
            "summary": self.summary,
            "environment": self.environment,
            "checks": [
                {
                    "name": c.name,
                    "ok": c.ok,
                    "durationMs": c.duration_ms,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }
        return json.dumps(payload, indent=2)

    def to_markdown(self) -> str:
        lines = [
            "# OracleDataForge Integration Report",
            f"**Label:** {self.label}",
            "",
            f"- **Harness build:** `{self.harness_build}`",
            f"- **DataForge commit:** `{self.dataforge_commit}`",
            f"- **Adapter version:** `{self.adapter_version}`",
            f"- **Protocol version:** `{self.protocol_version}`",
            f"- **Overall outcome:** {'PASSED' if self.ok else 'FAILED'}",
            f"- **Summary:** {self.summary}",
            "",
            "## Checks",
            "",
            "| Check | Status | Duration (ms) | Detail |",
            "| --- | --- | --- | --- |",
        ]
        for c in self.checks:
            status = "PASS" if c.ok else "FAIL"
            escaped_detail = c.detail.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {c.name} | {status} | {c.duration_ms:.1f} | {escaped_detail} |")
        lines.extend(
            ["", "## Environment", "", "```json", json.dumps(self.environment, indent=2), "```", ""]
        )
        return "\n".join(lines)


class DataForgeProcess:
    def __init__(
        self,
        dataforge_dir: Path,
        port: int,
        harness_url: str,
        harness_token: str,
        harness_enabled: bool = True,
        extra_env: dict[str, str] | None = None,
    ):
        self.dir = dataforge_dir
        self.port = port
        self.harness_url = harness_url
        self.harness_token = harness_token
        self.harness_enabled = harness_enabled
        self.extra_env = extra_env or {}
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_file: TextIO | None = None
        self._log_path = dataforge_dir / f"server-{port}.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 20.0) -> None:
        env = {key: value for key, value in os.environ.items()}
        env["PORT"] = str(self.port)
        env["HOST"] = "127.0.0.1"
        env["DATAFORGE_HARNESS_ENABLED"] = "true" if self.harness_enabled else "false"
        env["DATAFORGE_HARNESS_URL"] = self.harness_url
        env["DATAFORGE_HARNESS_TOKEN"] = self.harness_token
        for k, v in self.extra_env.items():
            env[k] = v

        self._log_file = self._log_path.open("w", encoding="utf-8")
        command = ["node", "--import", "tsx", "server/index.ts"]
        self._proc = subprocess.Popen(  # noqa: S603
            command,
            cwd=self.dir,
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        self._wait_ready(timeout)

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(
                    f"DataForge process exited early (code {self._proc.returncode}). Log:\n{self.log()}"
                )
            try:
                auth_token = self.extra_env.get("DATAFORGE_AUTH_TOKEN")
                resp = httpx.get(
                    f"{self.base_url}/api/health",
                    timeout=1.0,
                    auth=("dataforge", auth_token) if auth_token else None,
                )
                if resp.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        raise RuntimeError(
            f"DataForge process did not become healthy within {timeout:.0f}s. Log:\n{self.log()}"
        )

    def log(self) -> str:
        if self._log_path.exists():
            return self._log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        return ""

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
        if self._proc is not None:
            self._proc.wait(timeout=10)
        if self._log_file is not None:
            self._log_file.close()
        self._proc = None
        if self._log_path.exists():
            try:
                self._log_path.unlink()
            except OSError:
                pass


def parse_sse_events(raw_text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse SSE blocks into (event_name, data_dict) tuples."""
    events: list[tuple[str, dict[str, Any]]] = []
    blocks = raw_text.strip().split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        event_name = ""
        data_text = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                data_text += line[6:].strip()
        if event_name and data_text:
            try:
                events.append((event_name, json.loads(data_text)))
            except json.JSONDecodeError:
                events.append((event_name, {"raw": data_text}))
    return events


def check_capabilities(client: httpx.Client, df_url: str) -> CheckResult:
    start = time.monotonic()
    try:
        resp = client.get(f"{df_url}/api/ai/capabilities", timeout=10.0)
        dur = (time.monotonic() - start) * 1000
        if resp.status_code != 200:
            return CheckResult(
                "capabilities_negotiation", False, dur, f"HTTP {resp.status_code}: {resp.text}"
            )
        body = resp.json()
        if not body.get("enabled"):
            return CheckResult(
                "capabilities_negotiation", False, dur, "capabilities.enabled is not True"
            )
        if not body.get("reachable"):
            return CheckResult(
                "capabilities_negotiation", False, dur, f"harness unreachable: {body.get('detail')}"
            )
        if not body.get("compatible"):
            return CheckResult(
                "capabilities_negotiation",
                False,
                dur,
                f"protocol incompatible: {body.get('detail')}",
            )
        actions = body.get("actions", [])
        if "explain" not in actions:
            return CheckResult(
                "capabilities_negotiation",
                False,
                dur,
                f"expected 'explain' in actions, got: {actions}",
            )
        return CheckResult(
            "capabilities_negotiation",
            True,
            dur,
            f"Negotiated protocol {body.get('protocolVersion')}, enabled={body.get('enabled')}, actions={actions}",
        )
    except Exception as exc:  # noqa: BLE001
        dur = (time.monotonic() - start) * 1000
        return CheckResult("capabilities_negotiation", False, dur, f"Exception: {exc}")


def check_streaming_chat(
    client: httpx.Client, df_url: str
) -> tuple[CheckResult, str | None, str | None]:
    start = time.monotonic()
    proposal_id: str | None = None
    proposed_text: str | None = None
    try:
        req_body = {
            "action": "explain",
            "connectionId": "conn-real-1",
            "schema": "HARNESS_APP",
            "selection": "SELECT 1 FROM dual",
            "editorId": "editor-live-1",
            "editorRevision": "rev-1",
            "question": "table or view does not exist ORA-00942",
        }
        chunks: list[bytes] = []
        with client.stream("POST", f"{df_url}/api/ai/chat", json=req_body, timeout=30.0) as resp:
            dur = (time.monotonic() - start) * 1000
            if resp.status_code != 200:
                return (
                    CheckResult(
                        "streaming_chat",
                        False,
                        dur,
                        f"HTTP {resp.status_code}: {resp.read().decode()}",
                    ),
                    None,
                    None,
                )
            ct = resp.headers.get("content-type", "")
            if "text/event-stream" not in ct:
                return (
                    CheckResult(
                        "streaming_chat",
                        False,
                        dur,
                        f"Expected text/event-stream content-type, got {ct}",
                    ),
                    None,
                    None,
                )
            for chunk in resp.iter_bytes():
                if chunk:
                    chunks.append(chunk)

        dur = (time.monotonic() - start) * 1000
        raw_output = b"".join(chunks).decode("utf-8", errors="replace")
        events = parse_sse_events(raw_output)
        if len(chunks) <= 1:
            return (
                CheckResult(
                    "streaming_chat",
                    False,
                    dur,
                    f"Expected >1 streamed SSE chunk, got {len(chunks)} chunks",
                ),
                None,
                None,
            )

        if not events:
            return (
                CheckResult("streaming_chat", False, dur, "No valid SSE events parsed from stream"),
                None,
                None,
            )

        event_names = [e[0] for e in events]
        if event_names[0] != "start":
            return (
                CheckResult(
                    "streaming_chat",
                    False,
                    dur,
                    f"First event was {event_names[0]!r}, expected 'start'",
                ),
                None,
                None,
            )
        if "delta" not in event_names:
            return (
                CheckResult("streaming_chat", False, dur, "Stream contained no 'delta' events"),
                None,
                None,
            )
        if event_names[-1] != "done":
            return (
                CheckResult(
                    "streaming_chat",
                    False,
                    dur,
                    f"Last event was {event_names[-1]!r}, expected 'done'",
                ),
                None,
                None,
            )

        for name, data in events:
            if name == "proposal":
                proposal_id = data.get("proposalId")
                proposed_text = data.get("proposedText")

        return (
            CheckResult(
                "streaming_chat",
                True,
                dur,
                f"Received {len(chunks)} SSE chunks across {len(events)} events ({', '.join(set(event_names))})",
            ),
            proposal_id,
            proposed_text,
        )
    except Exception as exc:  # noqa: BLE001
        dur = (time.monotonic() - start) * 1000
        return CheckResult("streaming_chat", False, dur, f"Exception: {exc}"), None, None


def check_proposal_apply(client: httpx.Client, df_url: str, proposal_id: str | None) -> CheckResult:
    start = time.monotonic()
    if not proposal_id:
        return CheckResult(
            "proposal_apply_check",
            False,
            0.0,
            "No proposalId captured from streaming chat step to verify apply check",
        )
    try:
        # 1. Stale revision / mismatched source text must be rejected
        stale_body = {
            "editorId": "editor-live-1",
            "connectionId": "conn-real-1",
            "schema": "HARNESS_APP",
            "revision": "stale-revision-99",
            "currentText": "SELECT 999 FROM modified_table",
        }
        resp = client.post(
            f"{df_url}/api/ai/proposals/{proposal_id}/apply-check",
            json=stale_body,
            timeout=10.0,
        )
        if resp.status_code != 200:
            dur = (time.monotonic() - start) * 1000
            return CheckResult(
                "proposal_apply_check",
                False,
                dur,
                f"Stale check HTTP {resp.status_code}: {resp.text}",
            )
        stale_res = resp.json()
        if stale_res.get("canApply") is not False:
            dur = (time.monotonic() - start) * 1000
            return CheckResult(
                "proposal_apply_check", False, dur, f"Stale revision was not rejected: {stale_res}"
            )
        if not stale_res.get("reasons"):
            dur = (time.monotonic() - start) * 1000
            return CheckResult(
                "proposal_apply_check", False, dur, "Stale rejection missing explanation reasons"
            )

        # 2. Matching revision and source text must be accepted as reviewable
        matching_body = {
            "editorId": "editor-live-1",
            "connectionId": "conn-real-1",
            "schema": "HARNESS_APP",
            "revision": "rev-1",
            "currentText": "SELECT 1 FROM dual",
        }
        resp2 = client.post(
            f"{df_url}/api/ai/proposals/{proposal_id}/apply-check",
            json=matching_body,
            timeout=10.0,
        )
        if resp2.status_code != 200:
            dur = (time.monotonic() - start) * 1000
            return CheckResult(
                "proposal_apply_check",
                False,
                dur,
                f"Matching check HTTP {resp2.status_code}: {resp2.text}",
            )
        matching_res = resp2.json()
        if matching_res.get("canApply") is not True:
            dur = (time.monotonic() - start) * 1000
            return CheckResult(
                "proposal_apply_check",
                False,
                dur,
                f"Matching check was not accepted: {matching_res}",
            )
        if matching_res.get("reasons"):
            dur = (time.monotonic() - start) * 1000
            return CheckResult(
                "proposal_apply_check",
                False,
                dur,
                f"Matching check unexpectedly returned reasons: {matching_res.get('reasons')}",
            )

        dur = (time.monotonic() - start) * 1000
        return CheckResult(
            "proposal_apply_check",
            True,
            dur,
            "Stale revision correctly rejected; matching proposal accepted as reviewable with no DB/git mutation",
        )
    except Exception as exc:  # noqa: BLE001
        dur = (time.monotonic() - start) * 1000
        return CheckResult("proposal_apply_check", False, dur, f"Exception: {exc}")


def check_outage_and_disabled(dataforge_dir: Path, token: str) -> list[CheckResult]:
    results: list[CheckResult] = []

    # Outage test: start DataForge pointing at a closed port
    start = time.monotonic()
    dead_port = _free_port()
    df_port = _free_port()
    df_proc = DataForgeProcess(
        dataforge_dir=dataforge_dir,
        port=df_port,
        harness_url=f"http://127.0.0.1:{dead_port}",
        harness_token=token,
        harness_enabled=True,
    )
    try:
        df_proc.start()
        with httpx.Client() as client:
            # AI capabilities route reports unreachable
            cap_resp = client.get(f"{df_proc.base_url}/api/ai/capabilities", timeout=5.0)
            cap_body = cap_resp.json()
            ok_cap = (
                cap_resp.status_code == 200
                and cap_body.get("enabled") is True
                and cap_body.get("reachable") is False
                and "could not be reached" in cap_body.get("detail", "").lower()
            )

            # AI chat route reports typed error
            chat_resp = client.post(
                f"{df_proc.base_url}/api/ai/chat",
                json={
                    "action": "explain",
                    "connectionId": "conn-1",
                    "selection": "SELECT 1 FROM dual",
                    "editorId": "e1",
                    "editorRevision": "1",
                },
                timeout=5.0,
            )
            chat_events = parse_sse_events(chat_resp.text)
            ok_chat = any(
                e[0] == "error" and e[1].get("code") == "harness_unreachable" for e in chat_events
            )

            # Ordinary non-AI route answers normally
            health_resp = client.get(f"{df_proc.base_url}/api/health", timeout=5.0)
            conn_resp = client.get(f"{df_proc.base_url}/api/connections", timeout=5.0)
            ok_non_ai = (
                health_resp.status_code == 200
                and health_resp.json().get("ok") is True
                and conn_resp.status_code == 200
            )

            dur = (time.monotonic() - start) * 1000
            all_ok = ok_cap and ok_chat and ok_non_ai
            detail = (
                f"Outage handled: capabilities reachable={cap_body.get('reachable')}, "
                f"chat error code={chat_events[0][1].get('code') if chat_events else None}, "
                f"non-AI health ok={ok_non_ai}"
            )
            results.append(CheckResult("harness_outage_handling", all_ok, dur, detail))
    except Exception as exc:  # noqa: BLE001
        dur = (time.monotonic() - start) * 1000
        results.append(CheckResult("harness_outage_handling", False, dur, f"Exception: {exc}"))
    finally:
        df_proc.stop()

    # Disabled mode test: start DataForge with DATAFORGE_HARNESS_ENABLED=false
    start = time.monotonic()
    df_port_disabled = _free_port()
    df_proc_disabled = DataForgeProcess(
        dataforge_dir=dataforge_dir,
        port=df_port_disabled,
        harness_url="http://127.0.0.1:8000",
        harness_token="none",
        harness_enabled=False,
    )
    try:
        df_proc_disabled.start()
        with httpx.Client() as client:
            cap_resp = client.get(f"{df_proc_disabled.base_url}/api/ai/capabilities", timeout=5.0)
            cap_body = cap_resp.json()
            ok_cap = (
                cap_resp.status_code == 200
                and cap_body.get("enabled") is False
                and cap_body.get("reachable") is False
            )

            chat_resp = client.post(
                f"{df_proc_disabled.base_url}/api/ai/chat",
                json={
                    "action": "explain",
                    "connectionId": "conn-1",
                    "selection": "SELECT 1 FROM dual",
                    "editorId": "e1",
                    "editorRevision": "1",
                },
                timeout=5.0,
            )
            chat_events = parse_sse_events(chat_resp.text)
            ok_chat = any(
                e[0] == "error" and e[1].get("code") == "integration_disabled" for e in chat_events
            )

            preview = client.post(
                f"{df_proc_disabled.base_url}/api/ai/context-preview",
                json={
                    "action": "explain",
                    "connectionId": "conn-1",
                    "selection": "SELECT 1 FROM dual",
                    "editorId": "e1",
                    "editorRevision": "1",
                },
            )
            apply = client.post(
                f"{df_proc_disabled.base_url}/api/ai/proposals/missing/apply-check",
                json={
                    "editorId": "e1",
                    "connectionId": "conn-1",
                    "revision": "1",
                    "currentText": "SELECT 1 FROM dual",
                },
            )
            ok_guards = (
                preview.status_code == 403
                and preview.json() == {"error": "integration_disabled"}
                and apply.json() == {"canApply": False, "reasons": ["integration_disabled"]}
            )
            dur = (time.monotonic() - start) * 1000
            all_ok = ok_cap and ok_chat and ok_guards
            detail = (
                f"Disabled mode: capabilities enabled={cap_body.get('enabled')}, "
                f"chat error code={chat_events[0][1].get('code') if chat_events else None}; preview/apply guards={ok_guards}"
            )
            results.append(CheckResult("disabled_mode_handling", all_ok, dur, detail))
    except Exception as exc:  # noqa: BLE001
        dur = (time.monotonic() - start) * 1000
        results.append(CheckResult("disabled_mode_handling", False, dur, f"Exception: {exc}"))
    finally:
        df_proc_disabled.stop()

    return results


class _Protocol2Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/v1/integrations/capabilities"):
            payload = json.dumps(
                {
                    "protocolVersion": "2.0",
                    "actions": ["explain", "optimize"],
                    "enabled": True,
                    "providerReady": True,
                    "isFixtureProvider": True,
                    "limits": {},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass


def check_auth_and_protocol(dataforge_dir: Path, harness_url: str) -> list[CheckResult]:
    results: list[CheckResult] = []

    # 1. Invalid/revoked token check
    start = time.monotonic()
    df_port = _free_port()
    df_proc = DataForgeProcess(
        dataforge_dir=dataforge_dir,
        port=df_port,
        harness_url=harness_url,
        harness_token=secrets.token_urlsafe(24),  # never issued, so the harness refuses it
        harness_enabled=True,
    )
    try:
        df_proc.start()
        with httpx.Client() as client:
            cap_resp = client.get(f"{df_proc.base_url}/api/ai/capabilities", timeout=5.0)
            cap_body = cap_resp.json()
            ok_cap = (
                cap_resp.status_code == 200
                and cap_body.get("compatible") is False
                and "refused" in cap_body.get("detail", "").lower()
            )

            chat_resp = client.post(
                f"{df_proc.base_url}/api/ai/chat",
                json={
                    "action": "explain",
                    "connectionId": "conn-1",
                    "selection": "SELECT 1 FROM dual",
                    "editorId": "e1",
                    "editorRevision": "1",
                },
                timeout=5.0,
            )
            chat_events = parse_sse_events(chat_resp.text)
            ok_chat = any(
                e[0] == "error" and e[1].get("code") == "integration_unauthorized"
                for e in chat_events
            )

            dur = (time.monotonic() - start) * 1000
            all_ok = ok_cap and ok_chat
            detail = (
                f"Unauthorized token: capabilities compatible={cap_body.get('compatible')}, "
                f"chat error code={chat_events[0][1].get('code') if chat_events else None}"
            )
            results.append(CheckResult("invalid_token_unauthorized", all_ok, dur, detail))
    except Exception as exc:  # noqa: BLE001
        dur = (time.monotonic() - start) * 1000
        results.append(CheckResult("invalid_token_unauthorized", False, dur, f"Exception: {exc}"))
    finally:
        df_proc.stop()

    # 2. Incompatible protocol check
    start = time.monotonic()
    mock_port = _free_port()
    mock_server = http.server.HTTPServer(("127.0.0.1", mock_port), _Protocol2Handler)
    server_thread = threading.Thread(target=mock_server.serve_forever, daemon=True)
    server_thread.start()

    df_port_proto = _free_port()
    df_proc_proto = DataForgeProcess(
        dataforge_dir=dataforge_dir,
        port=df_port_proto,
        harness_url=f"http://127.0.0.1:{mock_port}",
        harness_token=secrets.token_urlsafe(24),
        harness_enabled=True,
    )
    try:
        df_proc_proto.start()
        with httpx.Client() as client:
            cap_resp = client.get(f"{df_proc_proto.base_url}/api/ai/capabilities", timeout=5.0)
            cap_body = cap_resp.json()
            ok_proto = (
                cap_resp.status_code == 200
                and cap_body.get("compatible") is False
                and cap_body.get("actions") == []
                and "speaks protocol 2.0" in cap_body.get("detail", "").lower()
            )
            dur = (time.monotonic() - start) * 1000
            detail = (
                f"Incompatible protocol 2.0 refused: compatible={cap_body.get('compatible')}, "
                f"actions={cap_body.get('actions')}, detail={cap_body.get('detail')}"
            )
            results.append(CheckResult("incompatible_protocol_refused", ok_proto, dur, detail))
    except Exception as exc:  # noqa: BLE001
        dur = (time.monotonic() - start) * 1000
        results.append(
            CheckResult("incompatible_protocol_refused", False, dur, f"Exception: {exc}")
        )
    finally:
        df_proc_proto.stop()
        mock_server.shutdown()
        mock_server.server_close()

    return results


def _get_git_commit(path: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
        )
        return res.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def run_dataforge_integration(
    dataforge_dir: Path,
    harness_url: str | None = None,
    token: str | None = None,
    workdir: Path | None = None,
) -> RealRunReport:
    """Run full integration suite against real DataForge and real harness."""
    repo_dir = Path(__file__).resolve().parents[2]
    harness_build = _get_git_commit(repo_dir)
    df_commit = _get_git_commit(dataforge_dir)

    disposable_harness: LiveHarness | None = None
    effective_url = harness_url
    effective_token = token or ""

    if not effective_url or not effective_token:
        assert workdir is not None
        disposable_harness = LiveHarness(workdir=workdir)
        disposable_harness.start()
        effective_url = disposable_harness.base_url
        effective_token = disposable_harness.issue_dataforge_credential()

    checks: list[CheckResult] = []

    # Phase 1: Live server run (capabilities, streaming chat, proposal apply)
    df_port = _free_port()
    df_proc = DataForgeProcess(
        dataforge_dir=dataforge_dir,
        port=df_port,
        harness_url=effective_url,
        harness_token=effective_token,
        harness_enabled=True,
    )

    try:
        df_proc.start()
        with httpx.Client() as client:
            cap_result = check_capabilities(client, df_proc.base_url)
            checks.append(cap_result)

            stream_result, proposal_id, _ = check_streaming_chat(client, df_proc.base_url)
            checks.append(stream_result)

            apply_result = check_proposal_apply(client, df_proc.base_url, proposal_id)
            checks.append(apply_result)
    finally:
        df_proc.stop()

    # Phase 2: Outage & Disabled mode checks
    checks.extend(check_outage_and_disabled(dataforge_dir, effective_token))

    # Phase 3: Auth & Incompatible Protocol checks
    checks.extend(check_auth_and_protocol(dataforge_dir, effective_url))

    if disposable_harness is not None:
        disposable_harness.stop()

    all_passed = all(c.ok for c in checks)
    passed_count = sum(1 for c in checks if c.ok)
    summary = f"{passed_count}/{len(checks)} checks passed against DataForge at {df_commit[:7]}"

    return RealRunReport(
        label=REPORT_LABEL,
        harness_build=harness_build,
        dataforge_commit=df_commit,
        adapter_version=ADAPTER_VERSION,
        protocol_version=PROTOCOL_VERSION,
        checks=checks,
        ok=all_passed,
        summary=summary,
    )
