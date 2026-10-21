"""One request, one budget; arithmetic checks that are exact or say nothing (K-7, Q1).

Round 18 (docs/k7-claude-round18-review-2026-10-18.md) found two defects in the claim
checks and revision turn added in round 17:

- **C21.** A revision ran outside the request's own bounds: it was not a step, it had
  120 seconds of its own past the request's wall time, and its deadline was read only
  when the provider sent something, so a server that sent one late final message was
  waited for and believed. An empty revision replaced the draft with nothing.
- **C22.** ``MOD`` was computed at the default Decimal precision, so a correct claim
  about a 38-digit operand was "corrected" to a wrong value with a rounded operand, and
  ``MOD(Infinity, 2)`` raised out of the checker.

Each test below failed before the fix. The service tests use the fixture provider with
a delay; nothing here calls a real model or a database.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from fractions import Fraction
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from harness_api.config import Settings
from harness_api.copilot import service as service_module
from harness_api.copilot.claim_checks import check_claims, oracle_mod, review_claims
from harness_api.copilot.local import LocalProvider
from harness_api.copilot.provider import ScriptedTurn, TextDelta, ToolCall, TurnEnd
from harness_api.copilot.service import CopilotService, _KiwiRun
from tests.copilot.test_claim_checks import BLOCKING_TOOL, RIGHT, WRONG, answer_of, kiwi_payload
from tests.copilot.test_copilot import sse
from tests.copilot.test_kiwi import last, names, use_script

# -- C22: MOD is exact for what it accepts, and silent on what it does not --------------

BIG = "99999999999999999999999999999999999999"  # 38 nines: odd, so MOD(BIG, 2) is 1


def mods(answer: str) -> list[str]:
    return [f.problem for f in check_claims(answer, []) if f.kind == "mod_arithmetic"]


def test_a_right_claim_about_a_38_digit_operand_is_not_corrected() -> None:
    # Round 18: "finding says MOD(100000000000000000000000000000000000000, 2) is -1".
    assert mods(f"MOD({BIG}, 2) = 1") == []


def test_a_wrong_claim_about_a_38_digit_operand_shows_the_operand_and_value_exactly() -> None:
    [problem] = mods(f"MOD({BIG}, 2) = -1")
    assert f"MOD({BIG}, 2) is 1, not -1" in problem


@pytest.mark.parametrize(
    ("m", "n"),
    [
        (BIG, "2"),
        ("-" + BIG, "7"),
        (BIG, "-" + BIG[:-1]),
        ("12345678901234567890123456789012345678.5", "7"),
        ("10.75", "0.5"),
        ("-0.75", "0.5"),
        ("7", "-4"),
        ("-7", "4"),
        ("0", "5"),
        ("1E+60", "7"),
        ("0.000000000000000000000000000000000000001", "0.0000000000000000000000000000000000000003"),
    ],
)
def test_oracle_mod_is_exact(m: str, n: str) -> None:
    exact_m, exact_n = Fraction(m), Fraction(n)
    quotient = int(exact_m / exact_n)  # int() truncates toward zero, as TRUNC does
    expected = exact_m - exact_n * quotient
    assert Fraction(oracle_mod(Decimal(m), Decimal(n))) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "MOD(-7, 4) = -3",
        "MOD(7, -4) = 3",
        "MOD(0, 5) = 0",
        "MOD(10.75, 0.5) = 0.25",
        "MOD(-0.75, 0.5) = -0.25",
        "MOD(10.50, 4) = 2.5",  # trailing zeros on an operand change nothing
        f"MOD(x, {BIG}) = 1, for example 1{'0' * 38}",  # BIG + 1
    ],
)
def test_right_claims_across_signs_scales_and_zero_pass(answer: str) -> None:
    assert mods(answer) == []


def test_a_scaled_operand_keeps_its_written_form_in_the_finding() -> None:
    [problem] = mods("MOD(10.50, 4) = 3")
    assert "MOD(10.50, 4) is 2.5, not 3" in problem


@pytest.mark.parametrize(
    "operand",
    ["Infinity", "-Infinity", "inf", "NaN", "sNaN", "1E+999999", "1e-999999", "9" * 500],
)
def test_an_operand_the_checker_cannot_compute_is_unchecked_not_corrected(operand: str) -> None:
    # Infinity raised decimal.InvalidOperation out of check_claims in round 18.
    review = review_claims(f"MOD({operand}, 2) = 1", [])
    assert review.findings == []
    assert [u.kind for u in review.unchecked] == ["mod_arithmetic"]
    assert "not checked" in review.unchecked[0].problem


def test_an_example_the_checker_cannot_compute_is_unchecked() -> None:
    review = review_claims("MOD(x, 2) = 1, for example " + "9" * 500, [])
    assert review.findings == []
    assert [u.kind for u in review.unchecked] == ["mod_arithmetic"]


@pytest.mark.parametrize(
    "answer",
    [
        "MOD(,2)=1",
        "MOD(((, 2) = 1",
        "MOD(1, 0) = 1",
        "MOD(1e5, 2) = 1e5",
        "MOD(" + "1" * 5000 + ", 3) = 1",
        "MOD(1, 2) = 1, for example ..., …, or",
        "\x00MOD(−−, 2) = −",
    ],
)
def test_malformed_answers_never_raise(answer: str) -> None:
    review_claims(answer, [])


# -- C21: the revision is a turn of the same request ---------------------------------------


def blocking_turn() -> ScriptedTurn:
    call = ToolCall(id="b1", name=BLOCKING_TOOL, input={"why": "Who blocks whom?"})
    return ScriptedTurn(text="Checking blockers. ", tool_calls=(call,))


def configure(client: TestClient, settings: Settings, **update: Any) -> None:
    client.app.state.harness.copilot._settings = settings.model_copy(  # type: ignore[attr-defined]
        update={"kiwi_enabled": True, **update}
    )


def timed(client: TestClient, dba, targets) -> tuple[list[tuple[str, dict]], float]:
    started = time.perf_counter()
    events = sse(client, dba, kiwi_payload(targets["development"]["id"]))
    return events, time.perf_counter() - started


def checks(events: list[tuple[str, dict]]) -> list[dict]:
    return [data for name, data in events if name == "claim_check"]


def run_state(**overrides: Any) -> _KiwiRun:
    values: dict[str, Any] = {
        "max_steps": 8,
        "max_tool_calls": 12,
        "max_tokens": 1000,
        "max_wall_seconds": 180.0,
        "max_tool_bytes": 1024,
        "started": time.perf_counter(),
    }
    values.update(overrides)
    return _KiwiRun(**values)


ANSWER_END = TurnEnd("end_turn", (), service_module.ProviderUsage())


@pytest.mark.parametrize(
    ("overrides", "refusal"),
    [
        ({"steps": 8}, "skipped:steps"),
        ({"tokens": 1000}, "skipped:tokens"),
        ({"started": time.perf_counter() - 181}, "skipped:wall_time"),
        # Round 18's reproduction: both at once still returned "revise".
        ({"steps": 8, "started": time.perf_counter() - 181}, "skipped:steps"),
    ],
)
def test_a_revision_is_refused_when_the_request_has_no_turn_left(
    settings: Settings, overrides: dict, refusal: str
) -> None:
    service = CopilotService(settings, session_factory=None)  # type: ignore[arg-type]
    assert service._revision_refused(run_state(**overrides), ANSWER_END) == refusal
    assert service._revision_refused(run_state(), ANSWER_END) == "revise"


def test_the_revision_is_counted_as_a_step(client: TestClient, dba, targets, settings) -> None:
    configure(client, settings)
    use_script(client, blocking_turn(), ScriptedTurn(text=WRONG), ScriptedTurn(text=RIGHT))
    events, _ = timed(client, dba, targets)

    assert [c["action"] for c in checks(events)] == ["revise", "none"]
    budget = last(events, "budget")
    assert budget["steps"] == 3
    assert budget["revisions"] == 1
    assert budget["steps"] <= budget["maxSteps"]


def test_no_revision_when_the_answer_used_the_last_step(
    client: TestClient, dba, targets, settings
) -> None:
    configure(client, settings, kiwi_max_steps=2)
    # A diagnosis on the last step: notes alone there would get the harness's answer.
    provider = use_script(client, blocking_turn(), ScriptedTurn(text=f"**Finding:** {WRONG}"))
    events, _ = timed(client, dba, targets)

    [check] = checks(events)
    assert check["action"] == "skipped:steps"
    assert [f["kind"] for f in check["findings"]] == ["session_role"]
    # No revision is asked for; the flagged draft is withheld for the harness's answer.
    [revision] = [data for name, data in events if name == "revision"]
    assert revision["withheld"] is True and revision["rendered"] == "bounded_fallback"
    assert WRONG not in answer_of(events)
    assert "(no model turn was left to revise it)" in answer_of(events)
    # The only harness message is the instruction to answer; no revision was asked for.
    [note] = provider.conversations[0].notes
    assert "last this request allows" in note
    assert last(events, "budget")["steps"] == 2


def test_a_late_final_message_does_not_outlast_the_revision_allowance(
    client: TestClient, dba, targets, settings
) -> None:
    # Round 18's reproduction: a 0.01 s allowance, a provider that answers after 0.06 s
    # with nothing, and the empty "revision" was accepted as the answer.
    configure(client, settings, kiwi_revision_max_wall_seconds=0.05)
    use_script(client, blocking_turn(), ScriptedTurn(text=WRONG), ScriptedTurn(text="", delay=2.0))
    events, elapsed = timed(client, dba, targets)

    assert elapsed < 1.5
    assert answer_of(events).startswith(
        "**Finding:** The harness wrote this answer: the model's diagnosis made claims"
    )
    assert "(the revision ran out of time)" in answer_of(events)
    assert last(events, "revision")["withheld"] is True
    assert last(events, "claim_check")["action"] == "revision_failed:wall_time"
    assert last(events, "claim_check")["findings"]
    # The allowance ran out, not the request: the harness's answer is a whole one.
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_revision_cannot_run_past_the_request_wall_time(
    client: TestClient, dba, targets, settings, monkeypatch
) -> None:
    # No settle margin or minimum window, so the revision starts and waits for what is
    # left of the request. Since Sync Point 2J (trial 24 KINJ-01) its timeout withholds
    # the draft without making the completed diagnosis partial.
    monkeypatch.setattr(service_module, "_REVISION_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(service_module, "_MIN_REVISION_SECONDS", 0.0)
    configure(client, settings, kiwi_max_wall_seconds=1.0, kiwi_revision_max_wall_seconds=120)
    use_script(
        client,
        blocking_turn(),
        ScriptedTurn(text=WRONG),
        ScriptedTurn(text="Revis", delay=5.0),
    )
    events, elapsed = timed(client, dba, targets)

    assert elapsed < 2.5
    assert answer_of(events).startswith(
        "**Finding:** The harness wrote this answer: the model's diagnosis made claims"
    )
    assert WRONG not in answer_of(events)
    assert last(events, "claim_check")["action"] == "revision_failed:wall_time"
    assert "wall_time" not in last(events, "budget")["exhausted"]
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_silent_provider_is_stopped_at_the_request_wall_time(
    client: TestClient, dba, targets, settings
) -> None:
    configure(client, settings, kiwi_max_wall_seconds=0.3)
    provider = use_script(client, ScriptedTurn(text="", delay=5.0))
    events, elapsed = timed(client, dba, targets)

    assert elapsed < 2.0
    done = last(events, "done")
    assert done["outcome"] == "partial" and done["stopReason"] == "wall_time"
    # The abandoned turn is closed and the usage total says a turn did not finish.
    conversation = provider.conversations[0]
    assert conversation.finished
    assert conversation.usage().extra["complete"] is False


def test_a_final_message_after_the_wall_time_is_not_believed(
    client: TestClient, dba, targets, settings
) -> None:
    configure(client, settings, kiwi_max_wall_seconds=0.3)
    use_script(client, ScriptedTurn(text="Half an answer", delay=5.0))
    events, elapsed = timed(client, dba, targets)

    assert elapsed < 2.0
    # The text streamed before the deadline is no diagnosis, so the harness writes its
    # own (Sync Point 2B) and removes that text, kept as the revision's draft (Wave 2X).
    # The final message the server sends late is not read.
    answer = answer_of(events)
    assert answer.startswith("**Finding:** The harness wrote this answer")
    assert "Half an answer" not in answer
    [fallback] = [d for n, d in events if n == "revision"]
    assert fallback["draft"] == "Half an answer"
    assert "limit on time" in answer
    assert last(events, "done")["stopReason"] == "wall_time"
    assert [c["action"] for c in checks(events)] == ["none"]


@pytest.mark.parametrize(
    ("revision", "reason"),
    [
        (ScriptedTurn(text=""), "empty"),
        (ScriptedTurn(text="  \n "), "empty"),
        (
            ScriptedTurn(
                text="Let me check one more thing.",
                tool_calls=(ToolCall(id="r1", name=BLOCKING_TOOL, input={}),),
            ),
            "tool_request",
        ),
        (ScriptedTurn(text="Session 77 waits on", stop_reason="max_tokens"), "max_output_tokens"),
        (ScriptedTurn(text="Half a revis", fail="connection dropped"), "provider_failure"),
    ],
)
def test_a_revision_that_is_not_an_answer_withholds_the_draft(
    client: TestClient, dba, targets, settings, revision: ScriptedTurn, reason: str
) -> None:
    configure(client, settings)
    use_script(client, blocking_turn(), ScriptedTurn(text=WRONG), revision)
    events, _ = timed(client, dba, targets)

    shown = answer_of(events)
    assert shown.startswith(
        "**Finding:** The harness wrote this answer: the model's diagnosis made claims"
    )
    assert WRONG not in shown
    withheld = last(events, "revision")
    assert withheld["withheld"] is True and withheld["draft"] == revision.text
    check = last(events, "claim_check")
    assert check["stage"] == "revised"
    assert check["action"] == f"revision_failed:{reason}"
    assert [f["kind"] for f in check["findings"]] == ["session_role"]
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_checker_failure_is_reported_and_the_answer_stands(
    client: TestClient, dba, targets, settings, monkeypatch
) -> None:
    def broken(answer: str, lookups: Any) -> Any:
        raise ArithmeticError("boom")

    monkeypatch.setattr(service_module, "review_claims", broken)
    configure(client, settings)
    use_script(client, blocking_turn(), ScriptedTurn(text=WRONG))
    events, _ = timed(client, dba, targets)

    [check] = checks(events)
    assert check == {
        "stage": "answer",
        "findings": [],
        "action": "check_failed:ArithmeticError",
        "unchecked": [],
    }
    assert "revision" not in names(events)
    assert answer_of(events) == "Checking blockers. " + WRONG
    assert last(events, "done")["outcome"] == "succeeded"


def test_an_unchecked_claim_is_reported_and_starts_no_revision(
    client: TestClient, dba, targets, settings
) -> None:
    configure(client, settings)
    provider = use_script(client, blocking_turn(), ScriptedTurn(text="MOD(Infinity, 2) = 1."))
    events, _ = timed(client, dba, targets)

    [check] = checks(events)
    assert check["findings"] == [] and check["action"] == "none"
    assert [u["kind"] for u in check["unchecked"]] == ["mod_arithmetic"]
    assert provider.conversations[0].notes == []


# -- the local adapter closes a stream that a timeout abandons ----------------------------


class StallingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        yield b'data: {"model": "m", "choices": [{"index": 0, "delta": {"content": "Hi"}}]}\n\n'
        await asyncio.sleep(30)

    async def aclose(self) -> None:
        self.closed = True


def test_a_timed_out_local_turn_closes_its_response() -> None:
    stream = StallingStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    provider = LocalProvider(
        "http://127.0.0.1:8080/v1", api_key="k", model="m", transport=httpx.MockTransport(handler)
    )
    conversation = provider.start_conversation("system", "user", [])

    async def play() -> list[Any]:
        seen: list[Any] = []
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.2):
                async for event in conversation.turn():
                    seen.append(event)
        return seen

    started = time.perf_counter()
    seen = asyncio.run(play())
    assert time.perf_counter() - started < 2
    assert [type(e) for e in seen] == [TextDelta]
    assert stream.closed
    assert conversation.finished
    assert conversation.usage().extra["complete"] is False
