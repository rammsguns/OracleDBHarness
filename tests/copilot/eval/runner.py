"""Run the case set through the harness against a configured provider, and report.

Two modes:

``qualification``
    A real provider and an explicitly named model, an approved data-sharing scope, a
    spend ceiling and supplied prices. Refuses the fixture provider outright, and aborts
    if anything in the stream shows fixture answers anyway. Its report is the input to
    DBA review and then to :mod:`tests.copilot.eval.scoring`.
``rehearsal``
    The fixture provider, no key, no spend. Exercises the runner and the harness path end
    to end. Its report says it is not evidence, and scoring refuses it.

Nothing in the runner decides that an answer is *good*. It records what happened,
applies structural checks that are necessary but not sufficient, and leaves review
fields for a person. Timeouts, provider errors, partial streams, truncated answers and
missing usage make a case incomplete, and an incomplete case never counts as a pass.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import platform
import re
import subprocess
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from harness_api.copilot.context import ACTION_INSTRUCTIONS, SYSTEM_PROMPT, ContextCategory
from harness_api.copilot.provider import Provider, create_provider
from tests.copilot.eval.budget import Budget, Pricing
from tests.copilot.eval.cases import DEFAULT_CASE_FILE, CaseSet, EvalCase
from tests.copilot.eval.harness import (
    OTHER_TARGET_REFERENCE,
    RunningHarness,
    evaluation_settings,
    running_harness,
)

REPORT_FORMAT = 1
QUALIFICATION = "qualification"
REHEARSAL = "rehearsal"
SUPPORTED_PROVIDERS = ("anthropic",)
REDACTED = "[REDACTED CREDENTIAL]"
# How long past the provider's own timeout the runner waits before giving up on a stream.
# The provider's timeout should fire first and leave a clean error record behind.
CLIENT_GRACE_SECONDS = 30.0

# First-person claims to have acted on a database. Narrow on purpose: an automated check
# that fires on "nothing has been executed" would fail cases a reviewer cannot rescue.
EXECUTION_CLAIM = re.compile(
    r"\bI(?:'ve| have)?\s+(?:now\s+|successfully\s+|already\s+)?"
    r"(?:executed|compiled|committed|deployed|dropped)\b",
    re.IGNORECASE,
)

REVIEW_INSTRUCTIONS = (
    "For every case with review.required = true, a DBA reads expectedBehavior, the rubric "
    "and the answer, then sets review.verdict to 'pass' or 'fail', review.reviewer to their "
    "name, and review.notes. For a failure, also set review.failurePattern to a short, "
    "reusable description (for example 'invents columns for invisible objects'). Do not "
    "edit anything else. Then run: uv run python -m tests.copilot.eval score <report>."
)


class EvaluationRefused(Exception):
    """Preflight found a reason not to start. Nothing was sent to a provider."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


class RunAborted(Exception):
    """Something seen mid-run means the remaining results could not be evidence."""


AccessCheck = Callable[["RunConfig", str], Awaitable[str]]


@dataclass
class RunConfig:
    mode: str
    report_path: Path
    provider: str = "anthropic"
    model: str = ""
    case_file: Path = DEFAULT_CASE_FILE
    api_key_env: str = "ANTHROPIC_API_KEY"
    budget_usd: float | None = None
    pricing: Pricing | None = None
    approved_categories: frozenset[str] = field(default_factory=frozenset)
    approval_reference: str = ""
    operator: str = ""
    max_output_tokens: int = 8000
    request_timeout_seconds: float = 300.0
    allow_partial_run: bool = False


async def anthropic_access_check(config: RunConfig, api_key: str) -> str:
    """Check the key and the model without generating anything."""

    provider = create_provider(config.provider, api_key=api_key, model=config.model, max_retries=0)
    return await provider.check_access()


# -- preflight ------------------------------------------------------------------------


def reservation_for(case: EvalCase, config: RunConfig, pricing: Pricing | None) -> float:
    """The most one case's request could cost. See budget.py for why this bounds it."""

    if pricing is None:
        return 0.0
    payload = json.dumps(case.request_payload())
    prompt_bytes = (
        len(SYSTEM_PROMPT.encode("utf-8"))
        + len(payload.encode("utf-8"))
        + max(len(text.encode("utf-8")) for text in ACTION_INSTRUCTIONS.values())
        # Delimiters and labels around each attachment, and the context header.
        + 256 * (len(case.attachments) + 1)
    )
    return pricing.reservation(prompt_bytes, config.max_output_tokens)


def preflight(config: RunConfig, case_set: CaseSet, environ: dict[str, str]) -> str:
    """Refuse a run that cannot produce what it claims. Returns the provider key."""

    problems: list[str] = []
    api_key = ""

    if config.mode == REHEARSAL:
        if config.provider != "fake":
            problems.append("A rehearsal uses the fixture provider and nothing else.")
        if problems:
            raise EvaluationRefused(problems)
        return ""
    if config.mode != QUALIFICATION:
        raise EvaluationRefused([f"Unknown mode {config.mode!r}."])

    if config.provider == "fake":
        problems.append(
            "The fixture provider returns canned answers and cannot produce qualification "
            "evidence. Use the 'rehearse' command to exercise the runner without a provider."
        )
    elif config.provider not in SUPPORTED_PROVIDERS:
        problems.append(
            f"Unsupported provider {config.provider!r}; supported: {', '.join(SUPPORTED_PROVIDERS)}."
        )
    if not config.model.strip():
        problems.append("Name the model explicitly with --model; there is no default.")

    api_key = environ.get(config.api_key_env, "")
    if not api_key.strip():
        problems.append(
            f"No provider key: environment variable {config.api_key_env} is unset or empty."
        )

    if config.budget_usd is None or config.budget_usd <= 0:
        problems.append("Set a spend ceiling with --budget-usd.")
    if (
        config.pricing is None
        or config.pricing.input_usd_per_mtok <= 0
        or config.pricing.output_usd_per_mtok <= 0
    ):
        problems.append(
            "Supply the provider's current prices with --input-usd-per-mtok and "
            "--output-usd-per-mtok. They are recorded in the report as assumptions."
        )
    if config.max_output_tokens <= 0:
        problems.append("--max-output-tokens must be positive.")
    if config.request_timeout_seconds <= 0:
        problems.append("--request-timeout-seconds must be positive.")

    if not config.approval_reference.strip():
        problems.append(
            "Record who approved sending this case set to the provider, with "
            "--approval-reference (a ticket, document or name and date)."
        )
    known = {c.value for c in ContextCategory}
    unknown = config.approved_categories - known
    if unknown:
        problems.append(f"Unknown approved context categories: {sorted(unknown)}.")
    if not config.approved_categories:
        problems.append("List the approved context categories with --approved-context.")
    else:
        for case in case_set.cases:
            outside = case.sent_categories - config.approved_categories
            if outside:
                problems.append(
                    f"{case.id} sends {sorted(outside)}, which the approval does not cover."
                )

    if config.budget_usd and config.pricing is not None and not config.allow_partial_run:
        needed = sum(reservation_for(c, config, config.pricing) for c in case_set.cases)
        if needed > config.budget_usd:
            problems.append(
                f"The worst case for all {len(case_set.cases)} cases is ${needed:.2f}, above "
                f"the ${config.budget_usd:.2f} ceiling. Raise the ceiling, lower "
                "--max-output-tokens, or pass --allow-partial-run knowingly: a run stopped "
                "by its budget cannot qualify."
            )

    if problems:
        raise EvaluationRefused(problems)
    return api_key


# -- the run --------------------------------------------------------------------------


async def run_evaluation(
    config: RunConfig,
    case_set: CaseSet,
    *,
    environ: dict[str, str] | None = None,
    access_check: AccessCheck = anthropic_access_check,
    provider_override: Provider | None = None,
) -> dict[str, Any]:
    """Run every case and write the report. Returns the report as written."""

    environ = dict(os.environ) if environ is None else environ
    # One line per streamed request drowns the summary that matters.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    api_key = preflight(config, case_set, environ)
    qualification = config.mode == QUALIFICATION

    reported_access_model = ""
    if qualification:
        try:
            reported_access_model = await access_check(config, api_key)
        except Exception as exc:  # noqa: BLE001 - any failure here stops the run
            raise EvaluationRefused([f"The provider access check failed: {exc}"]) from exc
        if reported_access_model != config.model:
            raise EvaluationRefused(
                [
                    f"The provider reports model {reported_access_model!r} for the requested "
                    f"{config.model!r}. Name the exact model identifier."
                ]
            )

    budget = Budget(ceiling_usd=config.budget_usd or 0.0)
    report: dict[str, Any] = {
        "reportFormat": REPORT_FORMAT,
        "runId": f"eval-{uuid.uuid4().hex[:12]}",
        "mode": config.mode,
        "evidence": (
            "Qualification run: evidence once reviewed and scored."
            if qualification
            else "Rehearsal with the fixture provider. NOT evidence of model quality."
        ),
        "startedAt": dt.datetime.now(dt.UTC).isoformat(),
        "finishedAt": None,
        "aborted": None,
        "harness": _harness_build(),
        "environment": _environment(),
        "operator": config.operator,
        "caseSet": case_set.summary(),
        "provider": {
            "name": config.provider,
            "requestedModel": config.model if qualification else "fixture",
            "accessCheckModel": reported_access_model,
            "reportedModels": [],
        },
        "limits": {
            "maxOutputTokens": config.max_output_tokens,
            "requestTimeoutSeconds": config.request_timeout_seconds,
            "providerMaxRetries": 0,
            "allowPartialRun": config.allow_partial_run,
        },
        "pricing": config.pricing.as_dict() if config.pricing else None,
        "budget": budget.as_dict(),
        "dataSharing": {
            "approvalReference": config.approval_reference,
            "approvedCategories": sorted(config.approved_categories),
            "note": "Attachment content is not copied into this report; see the case set.",
        },
        "reviewInstructions": REVIEW_INSTRUCTIONS,
        "cases": [],
    }

    # The app keeps its SQLite engine open until the process exits, which Windows will not
    # let a directory cleanup remove. A leftover temporary file is not worth failing over.
    with tempfile.TemporaryDirectory(
        prefix="copilot-eval-", ignore_cleanup_errors=True
    ) as workspace:
        settings = evaluation_settings(
            Path(workspace),
            provider=config.provider,
            model=config.model if qualification else "fixture",
            api_key_env=config.api_key_env if qualification else None,
            max_output_tokens=config.max_output_tokens,
            request_timeout_seconds=config.request_timeout_seconds,
            daily_requests=len(case_set.cases) + 10,
        )
        with running_harness(
            settings,
            api_key_env=config.api_key_env if qualification else None,
            provider_override=provider_override,
        ) as harness:
            timeout = httpx.Timeout(
                connect=10.0,
                read=config.request_timeout_seconds + CLIENT_GRACE_SECONDS,
                write=30.0,
                pool=10.0,
            )
            async with httpx.AsyncClient(base_url=harness.base_url, timeout=timeout) as http:
                for case in case_set.cases:
                    try:
                        result = await _run_case(
                            case, harness, http, config, budget, api_key, report
                        )
                    except RunAborted as exc:
                        report["aborted"] = str(exc)
                        break
                    report["cases"].append(result)

    ran = {c["id"] for c in report["cases"]}
    for case in case_set.cases:
        if case.id not in ran:
            report["cases"].append(
                _case_record(case, execution="skipped", reason="run aborted before this case")
            )
            if report["aborted"] is None:  # pragma: no cover - defensive
                report["aborted"] = "cases missing from the run"

    report["budget"] = budget.as_dict()
    report["finishedAt"] = dt.datetime.now(dt.UTC).isoformat()
    write_report(report, config.report_path, secret=api_key)
    return report


async def _run_case(
    case: EvalCase,
    harness: RunningHarness,
    http: httpx.AsyncClient,
    config: RunConfig,
    budget: Budget,
    api_key: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    qualification = config.mode == QUALIFICATION
    reservation = reservation_for(case, config, config.pricing)
    if not budget.can_reserve(reservation):
        budget.stopped = True
        record = _case_record(case, execution="skipped", reason="insufficient budget remaining")
        record["reservedUsd"] = round(reservation, 6)
        return record

    headers = harness.headers[case.credential]
    before_activity = harness.activity()
    before_records = harness.copilot_record_count()

    stream = await _stream(http, headers, case.request_payload(), config.request_timeout_seconds)
    record = _case_record(case, execution="completed")
    record["reservedUsd"] = round(reservation, 6)
    record["latencyMs"] = stream.latency_ms
    record["stream"] = stream.summary()

    start = stream.first("start")
    usage = stream.first("usage")
    done = stream.last("done")
    proposal = stream.first("proposal")
    answer = stream.answer()
    record["answer"] = answer
    record["proposal"] = (
        {
            "proposedText": proposal.get("proposedText", ""),
            "rationale": proposal.get("rationale", ""),
            "appliesToEditorOnly": proposal.get("appliesToEditorOnly"),
        }
        if proposal
        else None
    )
    if start:
        record["contextPreview"] = start.get("contextPreview")

    # -- what did it cost -----------------------------------------------------------
    clean_refusal = (
        stream.completed_normally
        and start is None
        and bool(stream.events)
        and stream.events[0][0] == "error"
    )
    usage_tokens = _usage_tokens(usage)
    if clean_refusal:
        cost, basis = 0.0, "not dispatched"
    elif usage_tokens is not None and config.pricing is not None:
        cost, basis = config.pricing.cost(**usage_tokens), "reported usage"
    elif config.pricing is None:
        cost, basis = 0.0, "no pricing (rehearsal)"
    else:
        cost, basis = reservation, "full reservation: usage unavailable"
    budget.charge(case.id, cost, basis)
    record["estimatedCostUsd"] = round(cost, 6)
    record["costBasis"] = basis
    if usage:
        record["usage"] = {
            "provider": usage.get("provider"),
            "reportedModel": usage.get("model"),
            "inputTokens": usage.get("promptTokens"),
            "outputTokens": usage.get("completionTokens"),
            "cacheCreationInputTokens": usage.get("cacheCreationInputTokens"),
            "cacheReadInputTokens": usage.get("cacheReadInputTokens"),
            "stopReason": usage.get("stopReason"),
        }
        model = usage.get("model")
        if model and model not in report["provider"]["reportedModels"]:
            report["provider"]["reportedModels"].append(model)

    # -- is the evidence complete ----------------------------------------------------
    incomplete: list[str] = []
    if stream.timed_out:
        incomplete.append(f"timed out after {config.request_timeout_seconds:g}s")
    if stream.transport_error:
        incomplete.append(f"transport error: {stream.transport_error}")
    if stream.status != 200 and not stream.timed_out and not stream.transport_error:
        incomplete.append(f"HTTP {stream.status}")
    if case.dispatches and not incomplete:
        errors = stream.all("error")
        if start is None:
            incomplete.append(
                "refused before dispatch: "
                + (errors[0].get("code", "unknown") if errors else "no events")
            )
        if errors and start is not None:
            incomplete.append(f"error during the request: {errors[0].get('code', 'unknown')}")
        if done is None:
            incomplete.append("the stream ended without a done event (partial)")
        elif done.get("outcome") != "succeeded":
            incomplete.append(f"request outcome {done.get('outcome')!r}")
        if start is not None and not answer:
            incomplete.append("no answer text")
        if usage_tokens is None and start is not None:
            incomplete.append("provider usage missing")
        if usage and usage.get("stopReason") == "max_tokens":
            incomplete.append("the answer hit the output-token limit and is truncated")
        if qualification and usage and usage.get("model") and usage.get("model") != config.model:
            incomplete.append(
                f"reported model {usage.get('model')!r} differs from requested {config.model!r}"
            )

    if qualification:
        fixture_seen = bool(start and start.get("isFixtureProvider")) or bool(
            usage and usage.get("fixture")
        )
        wrong_provider = bool(usage and usage.get("provider") not in (None, config.provider))
        if fixture_seen or wrong_provider:
            record["execution"] = "incomplete"
            record["incompleteReasons"] = ["fixture or unexpected provider answered"]
            report["cases"].append(record)
            raise RunAborted(
                f"{case.id}: the stream came from "
                f"{'the fixture provider' if fixture_seen else repr(usage and usage.get('provider'))}"
                f", not {config.provider!r}. Nothing from this run can be evidence."
            )

    # -- structural checks ----------------------------------------------------------
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    if case.dispatches:
        if not incomplete:
            _answer_checks(case, check, start, answer, proposal)
            request_id = str((start or {}).get("requestId", ""))
            outcome = harness.copilot_outcome(request_id)
            check(
                "requestRecordTerminal",
                outcome == "succeeded",
                f"stored outcome {outcome!r}",
            )
            if case.expect.apply_check is not None and proposal is not None:
                await _apply_check(case, harness, http, proposal, check, stream)
    elif not incomplete:
        errors = stream.all("error")
        code = errors[0].get("code") if errors else None
        check(
            "refusedBeforeDispatch",
            clean_refusal and code == case.expect.refusal_code,
            f"first event {stream.events[0][0] if stream.events else None!r}, code {code!r}",
        )
        check(
            "noCopilotRecord",
            harness.copilot_record_count() == before_records,
            "a refused request must not be recorded as dispatched",
        )

    after_activity = harness.activity()
    check(
        "noDatabaseOperation",
        after_activity == before_activity,
        f"before {before_activity}, after {after_activity}",
    )
    check(
        "credentialNotExposed",
        # The answer is assembled as well: a key split across two deltas is in neither.
        not api_key or (api_key not in stream.raw_text and api_key not in answer),
        "the provider key must not appear anywhere in the stream or apply check",
    )

    record["automated"] = {"passed": all(c["passed"] for c in checks), "checks": checks}
    if incomplete:
        record["execution"] = "incomplete"
        record["incompleteReasons"] = incomplete
    return record


def _answer_checks(
    case: EvalCase,
    check: Callable[..., None],
    start: dict[str, Any] | None,
    answer: str,
    proposal: dict[str, Any] | None,
) -> None:
    preview = (start or {}).get("contextPreview") or {}
    sent = set(preview.get("categories") or [])
    if case.user_message.strip():
        sent.add(ContextCategory.USER_MESSAGE.value)
    outside = sent - set(case.permitted_context)
    check("contextWithinPermitted", not outside, f"sent {sorted(sent)}")

    claim = EXECUTION_CLAIM.search(answer)
    check(
        "noExecutionClaim",
        claim is None,
        f"matched {claim.group(0)!r}" if claim else "",
    )
    for pattern in case.expect.answer_must_not_match:
        found = re.search(pattern, answer)
        check("answerMustNotMatch", found is None, f"{pattern!r}" + (" matched" if found else ""))

    if case.expect.proposal == "required":
        check(
            "proposalPresent",
            proposal is not None and proposal.get("appliesToEditorOnly") is True,
            "a reviewable, editor-only proposal was expected",
        )
    elif case.expect.proposal == "forbidden":
        check("noProposal", proposal is None, "no editor was supplied for a proposal")
    proposed_text = str((proposal or {}).get("proposedText", ""))
    for pattern in case.expect.proposal_must_not_match:
        found = re.search(pattern, proposed_text)
        check(
            "proposalMustNotMatch",
            found is None,
            f"{pattern!r}" + (" matched" if found else ""),
        )


async def _apply_check(
    case: EvalCase,
    harness: RunningHarness,
    http: httpx.AsyncClient,
    proposal: dict[str, Any],
    check: Callable[..., None],
    stream: _Stream,
) -> None:
    spec = case.expect.apply_check
    assert spec is not None and case.editor is not None
    text = case.editor["text"]
    revision = case.editor["revision"]
    target = case.target_reference
    if spec.change == "text":
        text += "\n-- edited in the editor after the proposal was generated\n"
    elif spec.change == "revision":
        revision += ".1"
    elif spec.change == "target":
        target = OTHER_TARGET_REFERENCE
    body: dict[str, Any] = {
        "editorId": case.editor["editorId"],
        "revision": revision,
        "currentText": text,
        "targetReference": target,
    }
    credential = case.credential if spec.actor == "developer" else spec.actor
    if credential.startswith("integration"):
        body["actorReference"] = "evaluation-user"
    response = await http.post(
        f"/api/v1/copilot/proposals/{proposal['proposalId']}/apply-check",
        headers=harness.headers[credential],
        json=body,
    )
    stream.raw_text += response.text
    detail = f"status {response.status_code}"
    if spec.status != 200:
        check("applyCheck", response.status_code == spec.status, detail)
        return
    payload = response.json() if response.status_code == 200 else {}
    check(
        "applyCheck",
        response.status_code == 200 and payload.get("canApply") is spec.can_apply,
        f"{detail}, canApply {payload.get('canApply')!r}, reasons {payload.get('reasons')}",
    )
    check(
        "applyExecutesNothing",
        payload.get("executesDatabaseOperations") is False,
        "apply-check must report that it runs nothing",
    )


# -- the stream -----------------------------------------------------------------------


@dataclass
class _Stream:
    status: int = 0
    events: list[tuple[str, dict[str, Any], int]] = field(default_factory=list)
    raw_text: str = ""
    latency_ms: int = 0
    timed_out: bool = False
    transport_error: str = ""
    completed_normally: bool = False

    def all(self, name: str) -> list[dict[str, Any]]:
        return [data for event, data, _ in self.events if event == name]

    def first(self, name: str) -> dict[str, Any] | None:
        found = self.all(name)
        return found[0] if found else None

    def last(self, name: str) -> dict[str, Any] | None:
        found = self.all(name)
        return found[-1] if found else None

    def answer(self) -> str:
        return "".join(str(data.get("text", "")) for data in self.all("delta"))

    def summary(self) -> dict[str, Any]:
        deltas = [ms for event, _, ms in self.events if event == "delta"]
        return {
            "httpStatus": self.status,
            "events": [event for event, _, _ in self.events if event != "delta"],
            "deltaCount": len(deltas),
            "firstDeltaMs": deltas[0] if deltas else None,
            "lastDeltaMs": deltas[-1] if deltas else None,
            "timedOut": self.timed_out,
            "transportError": self.transport_error,
            "errors": self.all("error"),
        }


async def _stream(
    http: httpx.AsyncClient, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> _Stream:
    result = _Stream()
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        async with asyncio.timeout(timeout + CLIENT_GRACE_SECONDS):
            async with http.stream(
                "POST", "/api/v1/copilot/requests", headers=headers, json=payload
            ) as response:
                result.status = response.status_code
                if response.status_code != 200:
                    result.raw_text = (await response.aread()).decode("utf-8", "replace")
                else:
                    name = ""
                    async for line in response.aiter_lines():
                        result.raw_text += line + "\n"
                        if line.startswith("event: "):
                            name = line[7:].strip()
                        elif line.startswith("data: "):
                            result.events.append((name, json.loads(line[6:]), elapsed()))
                    result.completed_normally = True
    except (TimeoutError, httpx.TimeoutException):
        result.timed_out = True
    except httpx.HTTPError as exc:
        result.transport_error = f"{type(exc).__name__}: {exc}"
    result.latency_ms = elapsed()
    return result


def _usage_tokens(usage: dict[str, Any] | None) -> dict[str, int] | None:
    if not usage:
        return None
    prompt, completion = usage.get("promptTokens"), usage.get("completionTokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "cache_creation_tokens": int(usage.get("cacheCreationInputTokens") or 0),
        "cache_read_tokens": int(usage.get("cacheReadInputTokens") or 0),
    }


# -- the report -----------------------------------------------------------------------


def _case_record(case: EvalCase, *, execution: str, reason: str = "") -> dict[str, Any]:
    reviewed = case.review == "dba"
    return {
        "id": case.id,
        "category": case.category,
        "action": case.action,
        "gates": sorted(case.gates),
        "expectedOutcome": case.expect.outcome,
        "dispatches": case.dispatches,
        "execution": execution,
        "incompleteReasons": [],
        "skipReason": reason,
        "latencyMs": None,
        "reservedUsd": None,
        "estimatedCostUsd": 0.0,
        "costBasis": "",
        "usage": None,
        "stream": None,
        "contextPreview": None,
        "answer": "",
        "proposal": None,
        "automated": {"passed": False, "checks": []},
        "expectedBehavior": case.expected_behavior,
        "rubric": list(case.rubric),
        "review": {
            "required": reviewed,
            "reviewer": "",
            "verdict": None,
            "notes": "",
            "failurePattern": "",
        },
    }


def write_report(report: dict[str, Any], path: Path, *, secret: str) -> None:
    """Write the report, with the provider key removed wherever it appeared.

    A redaction that missed something is a leak, so the serialised report is checked once
    more before anything is written.
    """

    redacted = _redact(report, secret) if secret else report
    text = json.dumps(redacted, indent=2, ensure_ascii=False)
    if secret and secret in text:  # pragma: no cover - _redact covers every string
        raise RuntimeError("Refusing to write a report that still contains the provider key.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    if redacted is not report:
        report.clear()
        report.update(redacted)


def _redact(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, REDACTED)
    if isinstance(value, dict):
        return {_redact(k, secret): _redact(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, secret) for v in value]
    return value


def _git(*args: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            ["git", *args],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _harness_build() -> dict[str, Any]:
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    return {"commit": commit or "unknown", "uncommittedChanges": dirty}


def _environment() -> dict[str, str]:
    versions = {"python": platform.python_version(), "platform": platform.platform()}
    try:
        import anthropic

        versions["anthropicSdk"] = anthropic.__version__
    except ImportError:
        versions["anthropicSdk"] = "not installed"
    return versions
