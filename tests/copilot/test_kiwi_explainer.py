"""Kiwi's K-6 package and process explainer against the stand-in (KIWI_PLAN.md, K-6).

The stand-in serves a 3k-line ETL_ORDERS package, the chain and job that run it, and a
trigger on the fact table. Two strings in it are written to look like instructions
(a run comment in the K-4 fixtures, and rule 042 of the package); both are test data.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from harness_api.config import Settings
from harness_api.copilot.explainer import Lineage, scrub, split_units, table_access
from harness_worker.backend import etl_fixture as fx
from tests.copilot.test_copilot import integration_headers, sse
from tests.copilot.test_kiwi import RecordingProvider, kiwi_payload, last


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"kiwi_enabled": True})


def use_fixture_answers(client: TestClient) -> RecordingProvider:
    """The fixture provider with no script: it answers each summarise and combine call."""

    provider = RecordingProvider(None)  # type: ignore[arg-type]
    client.app.state.harness.copilot._provider = provider  # type: ignore[attr-defined]
    return provider


def explain(client, headers, profile_id, action, subject, **overrides):
    payload = kiwi_payload(
        profile_id,
        action=action,
        userMessage="",
        subject=subject,
        attachments=[],
        **overrides,
    )
    return sse(client, headers, payload)


def package_events(client, developer, targets):
    use_fixture_answers(client)
    return explain(
        client,
        developer,
        targets["development"]["id"],
        "kiwi.explain_package",
        f"{fx.OWNER}.{fx.PACKAGE}",
    )


def process_events(client, developer, targets):
    use_fixture_answers(client)
    return explain(
        client,
        developer,
        targets["development"]["id"],
        "kiwi.explain_process",
        f"{fx.OWNER}.{fx.JOB}",
    )


def edge_set(lineage: dict) -> set[tuple[str, str, str, str]]:
    labels = {n["id"]: n["label"] for n in lineage["nodes"]}
    return {
        (labels[e["source"]], e["relation"], labels[e["target"]], e["evidence"])
        for e in lineage["edges"]
    }


# -- reading source -------------------------------------------------------------------


def test_comments_and_strings_are_scrubbed_before_anything_is_read() -> None:
    code, strings = scrub("x := 1; /* DROP TABLE a */ -- INSERT INTO b\nEXECUTE IMMEDIATE 'it''s';")
    assert "DROP" not in code and "INSERT" not in code
    assert strings == ["it''s"]
    assert "'#0'" in code


def test_a_comment_that_names_a_table_is_not_an_edge() -> None:
    code, strings = scrub("BEGIN /* DELETE FROM fact */ NULL; END;")
    assert table_access(code, strings, {"FACT"}) == {"reads": {}, "writes": {}}


def test_dynamic_sql_is_inferred_and_plain_sql_is_source() -> None:
    code, strings = scrub(
        "BEGIN EXECUTE IMMEDIATE 'TRUNCATE TABLE stage'; INSERT INTO stage SELECT * FROM src; END;"
    )
    found = table_access(code, strings, {"STAGE", "SRC"})
    assert found["writes"] == {"STAGE": "source"}
    assert found["reads"] == {"SRC": "source"}
    code, strings = scrub("BEGIN EXECUTE IMMEDIATE 'TRUNCATE TABLE stage'; END;")
    assert table_access(code, strings, {"STAGE"})["writes"] == {"STAGE": "inferred"}


def test_a_fixture_body_splits_into_every_subprogram() -> None:
    lines = dict(enumerate(fx.body_lines(), start=1))
    names = [u.name for u in split_units(lines)]
    assert len(lines) >= fx.MIN_BODY_LINES
    assert {"LOAD_STAGE", "TRANSFORM", "PUBLISH", "RUN_ALL", "BATCH_STATUS"} <= set(names)
    assert sum(n.startswith("CHECK_RULE_") for n in names) == fx.RULE_COUNT


def test_an_edge_needs_a_known_evidence_label() -> None:
    lineage = Lineage()
    with pytest.raises(ValueError):
        lineage.edge("a", "b", "calls", "guess")


# -- the package ----------------------------------------------------------------------


def test_the_package_is_explained_within_budget(client: TestClient, developer, targets) -> None:
    events = package_events(client, developer, targets)
    assert "error" not in [name for name, _ in events]
    done = last(events, "done")
    assert done["outcome"] == "succeeded", done
    budget = last(events, "budget")
    assert budget["exhausted"] == []
    assert budget["toolCalls"] <= budget["maxToolCalls"]
    assert budget["steps"] <= budget["maxSteps"]
    text = "".join(d["text"] for n, d in events if n == "delta")
    assert fx.PACKAGE in text


def test_the_source_is_read_a_page_at_a_time(client: TestClient, developer, targets) -> None:
    events = package_events(client, developer, targets)
    pages = [
        d
        for n, d in events
        if n == "tool_call" and d["operationId"] == "schema.object_source_range"
    ]
    assert len(pages) >= 3011 // 200
    assert all(p["callId"].startswith("exp-") and p["why"] for p in pages)


def test_every_lineage_edge_is_labelled_and_the_tables_are_found(
    client: TestClient, developer, targets
) -> None:
    lineage = last(package_events(client, developer, targets), "lineage")
    assert lineage["edges"]
    assert {e["evidence"] for e in lineage["edges"]} <= {
        "source",
        "inferred",
        "catalog",
        "scheduler",
    }
    edges = edge_set(lineage)
    assert (f"{fx.PACKAGE}.LOAD_STAGE", "reads", "ETL_ORDERS_SRC", "source") in edges
    assert (f"{fx.PACKAGE}.LOAD_STAGE", "writes", "ETL_ORDERS_STAGE", "source") in edges
    assert (f"{fx.PACKAGE}.PUBLISH", "writes", "ETL_ORDERS_FACT", "source") in edges
    assert (f"{fx.PACKAGE}.TRANSFORM", "writes", "ETL_ORDERS_REJECTS", "source") in edges
    # Reached only through helpers, and said to be.
    via = [
        e
        for e in lineage["edges"]
        if e["relation"] == "writes" and e.get("detail", "").startswith("via")
    ]
    assert via, "a helper's table access rolls up to its public caller with a note"


def test_a_truncate_in_dynamic_sql_is_only_inferred(client: TestClient, developer, targets) -> None:
    lineage = last(package_events(client, developer, targets), "lineage")
    labels = {n["id"]: n["label"] for n in lineage["nodes"]}
    truncated = [
        e
        for e in lineage["edges"]
        if labels[e["source"]] == f"{fx.PACKAGE}.LOAD_STAGE"
        and labels[e["target"]] == "ETL_ORDERS_STAGE"
        and e["relation"] == "writes"
    ]
    # The plain INSERT is source; the TRUNCATE alone never upgrades to it.
    assert [e["evidence"] for e in truncated] == ["source"]


def test_every_lineage_edge_carries_a_known_evidence_label(
    client: TestClient, developer, targets
) -> None:
    lineage = last(package_events(client, developer, targets), "lineage")
    assert lineage["edges"]
    for edge in lineage["edges"]:
        assert edge["evidence"] in {"source", "inferred", "catalog", "scheduler"}


def test_the_mermaid_export_is_deterministic(client: TestClient, developer, targets) -> None:
    first = last(package_events(client, developer, targets), "lineage")["mermaid"]
    second = last(package_events(client, developer, targets), "lineage")["mermaid"]
    assert first == second
    assert first.startswith("flowchart LR")
    assert "· source" in first
    assert '"' not in first.replace('["', "").replace('"]', "").replace('|"', "").replace('"|', "")


def test_the_injected_comment_causes_no_lookup_and_no_edge(
    client: TestClient, developer, targets
) -> None:
    events = package_events(client, developer, targets)
    called = [d["operationId"] for n, d in events if n == "tool_call"]
    assert "dba.kill_session" not in called
    assert set(called) <= {
        "schema.package_subprograms",
        "schema.object_source_range",
        "schema.object_dependencies",
    }
    # The rule-042 comment names etl_orders_fact for a DROP; it is not a lineage edge.
    lineage = last(events, "lineage")
    labels = {n["id"]: n["label"] for n in lineage["nodes"]}
    assert not any(labels[e["source"]].endswith("CHECK_RULE_042") for e in lineage["edges"])


def test_a_small_budget_gives_a_partial_result_that_says_what_is_missing(
    client: TestClient, developer, targets, settings: Settings
) -> None:
    tight = settings.model_copy(update={"kiwi_explain_max_model_calls": 2})
    client.app.state.harness.copilot._settings = tight  # type: ignore[attr-defined]
    events = package_events(client, developer, targets)
    done = last(events, "done")
    assert done["outcome"] == "partial"
    assert "steps" in done["stopReason"]
    text = "".join(d["text"] for n, d in events if n == "delta")
    assert "not summarised" in text


def test_a_lookup_budget_stops_reading_and_says_so(
    client: TestClient, developer, targets, settings: Settings
) -> None:
    tight = settings.model_copy(update={"kiwi_explain_max_tool_calls": 3})
    client.app.state.harness.copilot._settings = tight  # type: ignore[attr-defined]
    events = package_events(client, developer, targets)
    assert last(events, "done")["outcome"] == "partial"
    assert last(events, "budget")["toolCalls"] <= 3


def test_an_unknown_package_is_an_error_not_an_invention(
    client: TestClient, developer, targets
) -> None:
    use_fixture_answers(client)
    events = explain(
        client, developer, targets["development"]["id"], "kiwi.explain_package", "HARNESS_APP.NOPE"
    )
    assert last(events, "done")["outcome"] == "failed"
    assert "lineage" not in [n for n, _ in events]


# -- the process ----------------------------------------------------------------------


def test_the_etl_chain_is_explained_and_every_edge_is_labelled(
    client: TestClient, developer, targets
) -> None:
    events = process_events(client, developer, targets)
    done = last(events, "done")
    assert done["outcome"] == "succeeded", (done, [d for n, d in events if n == "error"])
    lineage = last(events, "lineage")
    assert all(
        e["evidence"] in {"source", "inferred", "catalog", "scheduler"} for e in lineage["edges"]
    )
    edges = edge_set(lineage)
    chain = f"{fx.OWNER}.{fx.CHAIN}"
    assert (f"{fx.OWNER}.{fx.JOB}", "runs", chain, "scheduler") in edges
    assert (chain, "starts", "LOAD", "scheduler") in edges
    assert ("LOAD", "then", "TRANSFORM", "scheduler") in edges
    assert ("TRANSFORM", "then", "PUBLISH", "scheduler") in edges
    assert ("LOAD", "runs", "ETL_LOAD_STAGE_PROG", "scheduler") in edges
    assert ("ETL_LOAD_STAGE_PROG", "calls", f"{fx.PACKAGE}.LOAD_STAGE", "scheduler") in edges


def test_the_process_follows_the_fact_table_to_its_trigger(
    client: TestClient, developer, targets
) -> None:
    lineage = last(process_events(client, developer, targets), "lineage")
    edges = edge_set(lineage)
    assert ("ETL_ORDERS_FACT", "fires", fx.TRIGGER, "catalog") in edges
    assert (fx.TRIGGER, "writes", "ETL_ORDERS_AUDIT", "source") in edges


def test_the_process_stays_within_its_lookup_budget(client: TestClient, developer, targets) -> None:
    events = process_events(client, developer, targets)
    budget = last(events, "budget")
    assert budget["exhausted"] == []
    assert budget["toolCalls"] <= budget["maxToolCalls"]
    called = {d["operationId"] for n, d in events if n == "tool_call"}
    assert "dba.kill_session" not in called


def test_the_explain_actions_are_advertised(client: TestClient, administrator) -> None:
    headers = integration_headers(client, administrator)
    kiwi = client.get("/api/v1/integrations/capabilities", headers=headers).json()["kiwi"]
    assert "kiwi.explain_package" in kiwi["explainers"]
    assert kiwi["explainLimits"]["maxSourceLines"] >= 3000


def test_a_chain_job_that_names_its_chain_in_program_name_is_still_read(
    client: TestClient, developer, targets, workspace
) -> None:
    process_events(client, developer, targets)  # seeds the stand-in database

    for database in (workspace / "fake").glob("*.sqlite3"):
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE all_scheduler_jobs SET job_action = NULL, program_name = ?"
                " WHERE job_name = ?",
                (f"{fx.OWNER}.{fx.CHAIN}", fx.JOB),
            )
    events = process_events(client, developer, targets)
    edges = edge_set(last(events, "lineage"))
    chain = f"{fx.OWNER}.{fx.CHAIN}"
    assert (f"{fx.OWNER}.{fx.JOB}", "runs", chain, "scheduler") in edges
    assert (chain, "starts", "LOAD", "scheduler") in edges


def test_an_explain_action_is_refused_without_kiwi_or_a_target(
    client: TestClient, developer, targets
) -> None:
    use_fixture_answers(client)
    events = explain(client, developer, "", "kiwi.explain_package", f"{fx.OWNER}.{fx.PACKAGE}")
    assert [n for n, _ in events] == ["error"]

    copilot = client.app.state.harness.copilot  # type: ignore[attr-defined]
    copilot._settings = copilot._settings.model_copy(update={"kiwi_enabled": False})
    events = explain(
        client,
        developer,
        targets["development"]["id"],
        "kiwi.explain_package",
        f"{fx.OWNER}.{fx.PACKAGE}",
    )
    assert [n for n, _ in events] == ["error"]
