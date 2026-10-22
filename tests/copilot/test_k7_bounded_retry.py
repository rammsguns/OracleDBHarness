"""Opt-in answer retry: one existing-budget turn, no tools or provider/database calls."""

from __future__ import annotations

import asyncio

import pytest

from harness_api.config import Settings
from harness_api.copilot.provider import FakeConversation, ScriptedTurn
from harness_api.copilot.service import CopilotService
from tests.copilot.test_claim_checks import WRONG, answer_of
from tests.copilot.test_k7_bounded_answer import (
    DIAGNOSIS,
    NO_ANSWER,
    assert_harness_answer,
    failures,
    investigating,
)
from tests.copilot.test_k7_runtime_bounds import blocking_turn, checks, configure, run_state, timed
from tests.copilot.test_kiwi import last, use_script


@pytest.mark.parametrize("bound", ["steps", "tool_calls"])
@pytest.mark.parametrize(
    "empty", [NO_ANSWER, ScriptedTurn(text=""), ScriptedTurn(text="Notes only.")]
)
def test_successful_retry(client, dba, targets, settings, bound, empty) -> None:
    configure(
        client,
        settings,
        kiwi_answer_retry=True,
        **({"kiwi_max_steps": 3} if bound == "steps" else {"kiwi_max_tool_calls": 1}),
    )
    provider = use_script(
        client, investigating("f1", failures()), empty, ScriptedTurn(text=DIAGNOSIS)
    )
    events, _ = timed(client, dba, targets)
    assert provider.conversations[0].answer_only_turns == [False, True, True]
    assert "Bounded answer retry" in provider.conversations[0].notes[-1]
    assert answer_of(events) == DIAGNOSIS
    retry = next(data for name, data in events if name == "revision")
    assert retry == {"draft": f"Next I will look at f1. {empty.text}", "findings": []}
    assert [data["step"] for name, data in events if name == "plan_step"] == [1, 2, 3]
    assert checks(events)[-1]["action"] == "none"
    assert last(events, "budget")["steps"] == 3
    assert last(events, "budget")["revisions"] == 0
    assert last(events, "done")["outcome"] == "partial"


def test_retry_with_no_diagnosis_falls_back_once(client, dba, targets, settings) -> None:
    configure(client, settings, kiwi_answer_retry=True, kiwi_max_tool_calls=1)
    provider = use_script(
        client, investigating("f1", failures()), NO_ANSWER, NO_ANSWER, ScriptedTurn(text="unused")
    )
    events, _ = timed(client, dba, targets)
    assert_harness_answer(events)
    assert provider.conversations[0].answer_only_turns == [False, True, True]
    assert last(events, "budget")["steps"] == 3


def test_retry_unsupported_claim_is_settled_without_another_turn(
    client, dba, targets, settings
) -> None:
    configure(client, settings, kiwi_answer_retry=True, kiwi_max_tool_calls=1)
    provider = use_script(
        client, blocking_turn(), NO_ANSWER, ScriptedTurn(text=f"**Finding:** {WRONG}")
    )
    events, _ = timed(client, dba, targets)
    assert checks(events)[-1]["action"] == "settled:corrected"
    assert "Session 77 waits on session 42." in answer_of(events)
    assert "ROW_WAIT_OBJ#" in answer_of(events)
    assert WRONG not in answer_of(events)
    assert provider.conversations[0].answer_only_turns == [False, True, True]


def service(enabled=True):
    value = object.__new__(CopilotService)
    value._settings = Settings(kiwi_answer_retry=enabled)
    return value


async def prepared(retry=None):
    if retry is None:
        retry = ScriptedTurn(text="**Finding:** No cause determined.")
    conversation = FakeConversation("fixture", "", "", [], [ScriptedTurn(text=""), retry])
    conversation.require_answer()
    async for _ in conversation.turn():
        pass
    return conversation


def test_default_is_off(monkeypatch) -> None:
    monkeypatch.delenv("HARNESS_KIWI_ANSWER_RETRY", raising=False)
    assert Settings(_env_file=None).kiwi_answer_retry is False


def test_disabled_retry_is_byte_identical_to_existing_fallback() -> None:
    async def play():
        copilot = service(False)
        state = run_state(steps=2, exhausted=["tool_calls"], final_text="notes")
        expected = copilot._bounded_fallback(state, [], [], "notes", True, answered=False)
        state.final_text = "notes"
        actual = [
            event
            async for event in copilot._retry_bounded_answer(
                await prepared(), state, [], [], "notes", True
            )
        ]
        assert actual == expected
        assert state.steps == 2

    asyncio.run(play())


@pytest.mark.parametrize(
    "overrides",
    [
        {"steps": 8},
        {"tokens": 1000},
        {"tokens": 489},  # 511 remaining: below the named minimal-answer allowance
        {"max_wall_seconds": 24},
        {"max_wall_seconds": 0},
    ],
)
def test_budget_exhausted_before_retry_skips_it(overrides) -> None:
    async def play():
        conversation = await prepared()
        state = (
            run_state(steps=2, exhausted=["tool_calls"], **overrides)
            if "steps" not in overrides
            else run_state(exhausted=["steps"], **overrides)
        )
        events = [
            event
            async for event in service()._retry_bounded_answer(
                conversation, state, [], [], "notes", True
            )
        ]
        assert_harness_answer(events, "notes")
        assert conversation.answer_only_turns == [True]

    asyncio.run(play())


def test_retry_missing_prompt_usage_skips_it(monkeypatch) -> None:
    async def play():
        conversation = await prepared()
        monkeypatch.setattr(conversation, "turn_usage", lambda: [])
        events = [
            event
            async for event in service()._retry_bounded_answer(
                conversation, run_state(exhausted=["steps"]), [], [], "notes", True
            )
        ]
        assert_harness_answer(events, "notes")
        assert conversation.answer_only_turns == [True]

    asyncio.run(play())


def test_retry_actual_token_overrun_is_recorded_and_answer_checked() -> None:
    async def play():
        text = "**Finding:** " + "No cause determined. " * 300
        conversation = await prepared(ScriptedTurn(text=text))
        usage = conversation.usage()
        spent = usage.prompt_tokens + usage.completion_tokens
        state = run_state(
            max_tokens=spent + usage.prompt_tokens + 512,
            tokens=spent,
            exhausted=["tool_calls"],
            steps=2,
        )
        events = [
            event
            async for event in service()._retry_bounded_answer(
                conversation, state, [], [], "notes", True
            )
        ]
        assert state.tokens > state.max_tokens
        assert "tokens" in state.exhausted
        assert state.steps == 3
        assert state.final_text == text
        assert checks(events)[-1]["action"] == "none"
        assert conversation.answer_only_turns == [True, True]

    asyncio.run(play())


def test_cancellation_during_retry_propagates_and_closes_turn() -> None:
    async def play():
        conversation = await prepared(ScriptedTurn(text="Starting the answer.", delay=30))
        entered = asyncio.Event()
        events = []
        state = run_state(exhausted=["tool_calls"], steps=2)

        async def consume():
            async for event in service()._retry_bounded_answer(
                conversation, state, [], [], "notes", True
            ):
                events.append(event)
                if event[0] == "delta":
                    entered.set()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert conversation.finished
        assert conversation.usage().extra["complete"] is False
        assert not [data for name, data in events if name == "revision" and data.get("rendered")]
        assert state.steps == 3

    asyncio.run(play())


@pytest.mark.parametrize("failure", ["provider", "timeout"])
def test_failed_retry_falls_back(client, dba, targets, settings, monkeypatch, failure) -> None:
    from harness_api.copilot import service as module

    configure(client, settings, kiwi_answer_retry=True, kiwi_max_tool_calls=1)
    if failure == "timeout":
        monkeypatch.setattr(module, "_MIN_REVISION_SECONDS", 0)
        monkeypatch.setattr(module, "_REVISION_SETTLE_SECONDS", 0)
        configure(
            client,
            settings,
            kiwi_answer_retry=True,
            kiwi_max_tool_calls=1,
            kiwi_max_wall_seconds=0.5,
        )
        retry = ScriptedTurn(text="", delay=5)
    else:
        retry = ScriptedTurn(text="", fail="fixture failure")
    provider = use_script(client, investigating("f1", failures()), NO_ANSWER, retry)
    events, elapsed = timed(client, dba, targets)
    assert_harness_answer(events)
    assert elapsed < 3
    assert provider.conversations[0].answer_only_turns == [False, True, True]
    if failure == "timeout":
        assert "wall_time" in last(events, "budget")["exhausted"]


def test_unforced_truncated_turn_does_not_get_answer_retry(client, dba, targets, settings) -> None:
    configure(client, settings, kiwi_answer_retry=True)
    provider = use_script(
        client,
        ScriptedTurn(text="Notes only.", stop_reason="max_tokens"),
        ScriptedTurn(text=DIAGNOSIS),
    )
    events, _ = timed(client, dba, targets)
    assert_harness_answer(events)
    assert provider.conversations[0].answer_only_turns == [False]


def test_disabled_retry_never_enters_async_retry_path(
    client, dba, targets, settings, monkeypatch
) -> None:
    configure(client, settings, kiwi_answer_retry=False, kiwi_max_tool_calls=1)
    copilot = client.app.state.harness.copilot

    def unexpected(*args, **kwargs):
        raise AssertionError("Disabled retry must use the original synchronous fallback")

    monkeypatch.setattr(copilot, "_retry_bounded_answer", unexpected)
    provider = use_script(client, investigating("f1", failures()), NO_ANSWER)
    events, _ = timed(client, dba, targets)
    assert_harness_answer(events)
    assert provider.conversations[0].answer_only_turns == [False, True]
