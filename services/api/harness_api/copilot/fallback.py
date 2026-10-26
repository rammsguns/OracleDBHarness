"""The harness's own answer when a Kiwi diagnosis reaches a bound without one (Wave 2B).

A request that runs out of steps, lookups, tokens or time is told to answer with what it
has. A model can still end that turn with nothing a user can act on: a tool call it was
not offered, an empty message, or only the notes it wrote between lookups (trial 21,
DIAG-02, where the job's ORA-20001 was in lookup 1 and never reached the answer).

Then the harness writes the answer from the request's lookups, in the sections a Kiwi
diagnosis uses. It concludes nothing: Finding repeats only the error and status facts
the lookups returned, with their numbers; every lookup is listed with its coverage; the
cause is Unknown; no remedy is proposed. Next checks suggest diagnostic follow-up
from returned facts and explicit lookup gaps, never actions already performed.
Free text a lookup returned is untrusted, so
only the Oracle error messages in it are quoted (``error_messages``).

Text in a returned source line or scheduler log field that reads as an instruction to
the assistant ("ignore previous instructions", "SYSTEM NOTE TO THE ASSISTANT", "run DROP
TABLE ...") is reported as such (``untrusted_instructions``, Sync Point 2J, trial 24
KINJ-01): where it is, by lookup and line or column, what kind of instruction it is, and
that it was not followed and nothing was run. The text itself is not repeated. The
withheld diagnosis said so; the harness's answer that replaces it must not lose it.

Nothing here knows a case or an expected answer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from harness_api.copilot.claim_checks import LookupFacts
from harness_api.copilot.evidence import RETAINED_HISTORY, coverage, facts

# A diagnosis has a Finding section with something in it: a heading ("## Finding",
# "**Finding:**", "Finding:"), not a line that starts with the word ("Finding the job").
_HEADING = r"^[ \t>]*(?:#{{1,6}}[ \t]*)?[*_]*{name}[*_]*[ \t]*(?::|$)"
_FINDING = re.compile(_HEADING.format(name=r"Findings?"), re.IGNORECASE | re.MULTILINE)
# Where the Finding section ends: the next section, or any Markdown heading.
_NEXT_SECTION = re.compile(
    _HEADING.format(name=r"(?:Evidence|Unknowns?|Remedy|Next[ \t]+(?:steps?|checks?)|Findings?)")
    + r"|^[ \t]*#{1,6}[ \t]",
    re.IGNORECASE | re.MULTILINE,
)
# Its body says something: words, not only markup or punctuation.
_SUBSTANCE = re.compile(r"[^\W_]{2,}")

# The bounds, as the user reads them.
_BOUNDS = {
    "steps": "model turns",
    "tool_calls": "lookups",
    "tool_bytes": "lookup result size",
    "tokens": "tokens",
    "wall_time": "time",
    "max_output_tokens": "answer length",
}

# Columns whose values can hold an Oracle error message.
_ERROR_COLUMNS = ("ADDITIONAL_INFO", "TEXT", "ERROR_MESSAGE", "MESSAGE")
# An Oracle error message as a server writes it, to the end of its line.
_ORA_MESSAGE = re.compile(r"\bORA-\d{5}:[^\n]*")
# A message ends at its first full stop; what follows in the same value is other text.
_SENTENCE_END = re.compile(r"\.(?:\s|$)")
_MAX_ROWS = 10

# Instruction-like text, by kind: an override of the assistant's instructions, a note
# addressed to it, and a directive to run DDL, a lookup or a session kill, or to grant a
# privilege. A commented-out statement ("-- DROP TABLE t;") is not a directive.
_INSTRUCTION_KINDS = (
    (
        "an instruction to ignore the assistant's instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+)?(?:of\s+)?(?:the\s+|"
            r"your\s+|my\s+)?(?:previous|prior|earlier|above|preceding|system|own)?\s*"
            r"(?:instructions?|rules|prompts?|guidelines)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "a note addressed to the assistant",
        re.compile(
            r"\bNOTE\s+(?:TO|FOR)\s+(?:THE\s+)?(?:AI\s+)?(?:ASSISTANT|AI|REVIEWER|MODEL|COPILOT|"
            r"KIWI|LLM|AGENT)\b|\b(?:AI|LLM)\s+(?:assistant|agent|model)\s*:"
            r"|\b(?:assistant|copilot|kiwi)\s*:\s*(?=\w)|\bnew\s+instructions?\s*:",
            re.IGNORECASE,
        ),
    ),
    (
        "a directive to run a statement or a lookup",
        re.compile(
            r"\b(?:run|execute|issue|call|perform|invoke)\s+(?:a\s+|an\s+|the\s+)?(?:DROP|ALTER|"
            r"GRANT|TRUNCATE|KILL|REVOKE|SHUTDOWN|dba\.\w+)"
            r"|\bkill\s+(?:every|all|each|any)\s+(?:other\s+)?sessions?\b"
            r"|\bgrant\s+(?:yourself|me|DBA|SYSDBA|all\s+privileges)\b",
            re.IGNORECASE,
        ),
    ),
)
_SOURCE_LOOKUPS = ("schema.object_source",)
_SCHEDULER_LOOKUPS = ("dba.scheduler_",)
_MAX_NOTICES = 4


# Error codes select fixed suggestions; returned prose never becomes an instruction.
_ERROR_NEXT_CHECKS = (
    (
        re.compile(r"\bORA-(?:00942|04043)\b"),
        "check that the referenced table/view exists in the expected schema and that "
        "the PL/SQL owner has the required direct grant (privileges via roles do not "
        "apply in definer-rights PL/SQL); then consider recompiling the affected unit",
    ),
    (
        re.compile(r"\bPLS-00201\s*:"),
        "check the referenced identifier's spelling and declaration, its schema "
        "qualification and the PL/SQL owner's required direct grants",
    ),
)


def _next_checks(lookups: Sequence[LookupFacts], stopped: Sequence[str]) -> list[str]:
    """Suggestions from successful facts and explicit gaps, never a diagnosis."""
    checks: list[str] = []
    for pattern, suggestion in _ERROR_NEXT_CHECKS:
        numbers = []
        for lookup in lookups:
            if not lookup.succeeded:
                continue
            columns = [str(column).upper() for column in lookup.columns]
            # Sanitize ORA text using the existing helper; a PLS compiler code
            # must begin the field, so appended untrusted prose cannot select a rule.
            messages = [
                message
                for row in lookup.rows
                for column, value in zip(columns, row, strict=False)
                if column in _ERROR_COLUMNS
                for message in (
                    error_messages(value)[0]
                    + (
                        [str(value).split(":", 1)[0] + ":"]
                        if re.match(r"\s*PLS-00201\s*:", str(value))
                        else []
                    )
                )
            ]
            if any(pattern.search(message) for message in messages):
                numbers.append(lookup.sequence)
        if numbers:
            checks.append(f"For {_cite(numbers)}, ask the user or DBA to {suggestion}.")
    for lookup in lookups:
        if not lookup.succeeded:
            checks.append(
                f"Ask the user or DBA to retry {lookup.operation_id}"
                f"{_parameters(lookup.parameters)}: lookup {lookup.sequence} failed, "
                "so its result is unknown."
            )
            continue
        values = [
            dict(zip((str(c).upper() for c in lookup.columns), row, strict=False))
            for row in lookup.rows
        ]
        if lookup.operation_id == "schema.object_status" and any(
            str(row.get("STATUS", "")).upper() == "INVALID" for row in values
        ):
            checks.append(
                f"For lookup {lookup.sequence}, ask the user or DBA to read "
                "schema.object_errors for the returned INVALID object and inspect "
                "its compiler diagnostics before considering recompilation."
            )
        if (
            lookup.operation_id == "dba.blocking"
            and any(row.get("BLOCKING_SID") is not None for row in values)
        ) or (
            lookup.operation_id == "dba.sessions"
            and any(row.get("BLOCKING_SESSION") is not None for row in values)
        ):
            checks.append(
                f"For lookup {lookup.sequence}, ask the user or DBA to identify "
                "the blocking session's transaction and its intent before deciding any action."
            )
    application = _application_errors(lookups)
    if application:
        numbers = sorted({n for cites in application.values() for n in cites})
        checks.append(
            f"For {_cite(numbers)}, ask the user or DBA to read the raising code path "
            "for the returned application error and inspect its RAISE_APPLICATION_ERROR "
            "condition; the raising location is not inferred here."
        )
    checks.extend(
        f"Ask the user or DBA to read {operation}, which the limit stopped, "
        "to fill that lookup gap."
        for operation in sorted(set(stopped))
    )
    return checks or [
        "Ask the user or DBA to re-run the diagnosis or read the missing lookups "
        "to establish the next diagnostic step."
    ]


def is_diagnosis(text: str) -> bool:
    """Whether a final turn is an answer: it has a Finding section with a body.

    A heading alone is not one (Sync Point 2B: ``"**Finding:**\\n"`` ahead of a call
    it was not offered), and neither are notes between lookups.
    """

    for heading in _FINDING.finditer(text):
        rest = text[heading.end() :]
        following = _NEXT_SECTION.search(rest)
        body = rest[: following.start()] if following else rest
        if _SUBSTANCE.search(body):
            return True
    return False


def render(
    lookups: Sequence[LookupFacts],
    exhausted: Sequence[str],
    refused: Iterable[tuple[str, str]] = (),
    withheld: str = "",
) -> str:
    """The bounded fallback diagnosis, from the lookups alone.

    With ``withheld`` (why no revision completed), the model wrote a diagnosis whose
    claims the checks found unsupported and the harness does not deliver it (Sync Point
    2G, trial 23 KINJ-01): this answer takes its place.
    """

    ordered = sorted(lookups, key=lambda lookup: lookup.sequence)
    bounds = ", ".join(_BOUNDS.get(bound, bound) for bound in exhausted) or "a limit"
    errors = _returned_errors(ordered)
    failed = [lookup for lookup in ordered if not lookup.succeeded]

    if withheld:
        finding = (
            "The harness wrote this answer: the model's diagnosis made claims the "
            "harness's checks found these lookups do not support, and its revision did "
            f"not complete ({withheld}), so that diagnosis is withheld. This answer "
            "repeats what the lookups returned and concludes no cause."
        )
    else:
        finding = (
            f"The harness wrote this answer: the request reached its limit on {bounds} "
            "before the model wrote a diagnosis. It repeats what the lookups returned and "
            "concludes no cause."
        )
    application_errors = _application_errors(ordered)
    planted = untrusted_instructions(ordered)
    if errors:
        finding += " The lookups returned " + "; ".join(
            f"{message} ({_cite(numbers)})" for message, numbers in errors.items()
        )
        finding += "."
    elif not any(lookup.succeeded and _row_count(lookup) for lookup in ordered):
        finding += " No lookup returned rows, so there is nothing to report as found."

    if application_errors:
        finding += (
            " "
            + "; ".join(
                f"{code} ({_cite(numbers)})" for code, numbers in application_errors.items()
            )
            + " are application errors raised by application code via "
            "RAISE_APPLICATION_ERROR. Their number and text are defined by that code, "
            "not by Oracle."
        )

    if planted:
        finding += (
            " "
            + _and(sorted({f"lookup {notice.sequence}" for notice in planted}))
            + " returned text that reads as an instruction to the assistant; it is "
            "untrusted data in the database, it was not followed, and nothing was run."
        )

    evidence = [f"- {line}" for lookup in ordered for line in _evidence(lookup)]
    if not evidence:
        evidence = ["- No lookup ran before the limit."]
    evidence += [f"- {notice.describe()}" for notice in planted]

    unknown = [
        "- The cause: no diagnosis was written from these results, and none is inferred here."
    ]
    if failed:
        unknown.append(
            "- What "
            + _and(f"lookup {lookup.sequence} ({lookup.operation_id})" for lookup in failed)
            + " would have shown: "
            + ("it" if len(failed) == 1 else "they")
            + " did not return rows."
        )
    stopped = [operation for operation, _ in refused]
    if stopped:
        unknown.append(
            "- What the lookups the limit stopped would have shown: "
            + _and(sorted(set(stopped)))
            + "."
        )

    return "\n".join(
        [
            f"**Finding:** {finding}",
            "",
            "**Evidence:**",
            *evidence,
            "",
            "**Unknown:**",
            *unknown,
            "",
            "**Remedy:** None proposed: these results have not been diagnosed. Any action "
            "on a session or a job is for the user or their DBA to decide.",
            "",
            "**Next checks:** Suggestions only; no remedy or cause is proposed.",
            *(f"- {check}" for check in _next_checks(ordered, stopped)),
        ]
    )


@dataclass(frozen=True)
class Instruction:
    """Instruction-like text a lookup returned, and where."""

    sequence: int  # the lookup
    operation_id: str
    where: str  # "HARNESS_APP.ETL_ORDERS PACKAGE BODY line 851", "log 9002 ADDITIONAL_INFO"
    kinds: tuple[str, ...]  # what it reads as, never the text itself

    def describe(self) -> str:
        return (
            f"{self.where} holds text written as an instruction to the assistant "
            f"({_and(self.kinds)}): untrusted data returned by {self.operation_id}, not "
            f"followed, and nothing it asks for was run (lookup {self.sequence})"
        )


def instruction_kinds(text: str) -> tuple[str, ...]:
    """What ``text`` reads as, if it is written as an instruction to the assistant.

    Shared with the edit contract (``edit_contract.carried_directives``, Sync Point 2Q):
    a proposal must not carry such text from untrusted source into replacement code."""

    return tuple(kind for kind, pattern in _INSTRUCTION_KINDS if pattern.search(text))


def untrusted_instructions(lookups: Sequence[LookupFacts]) -> list[Instruction]:
    """Instruction-like text in returned source lines or scheduler log fields.

    Only source lookups (``schema.object_source*``: LINE and TEXT) and scheduler lookups
    (``dba.scheduler_*``: their text columns) are read. Each is located and classed; its
    words are not repeated, so the harness restates no planted command. At most four
    are reported."""

    found: list[Instruction] = []
    for lookup in sorted(lookups, key=lambda item: item.sequence):
        if not lookup.succeeded:
            continue
        operation = lookup.operation_id
        source = operation.startswith(_SOURCE_LOOKUPS)
        if not source and not operation.startswith(_SCHEDULER_LOOKUPS):
            continue
        columns = [str(column).upper() for column in lookup.columns]
        params = lookup.parameters
        unit = ".".join(str(params[k]).upper() for k in ("owner", "object_name") if params.get(k))
        if params.get("object_type"):
            unit += f" {str(params['object_type']).upper()}"
        for row in lookup.rows:
            values = dict(zip(columns, row, strict=False))
            cells = (
                [("TEXT", values.get("TEXT"))]
                if source
                else [(c, v) for c, v in values.items() if isinstance(v, str)]
            )
            for column, value in cells:
                kinds = instruction_kinds(str(value or ""))
                if not kinds:
                    continue
                if source:
                    name, kind = values.get("NAME"), values.get("TYPE")
                    owner = f"{name} {kind}" if name and kind else unit or "the source"
                    where = f"{owner} line {values.get('LINE')}"
                else:
                    log, job = values.get("LOG_ID"), values.get("JOB_NAME")
                    where = " ".join(
                        str(part)
                        for part in (job, f"log {log}" if log is not None else "", column)
                        if part
                    )
                found.append(Instruction(lookup.sequence, operation, where, kinds))
                if len(found) >= _MAX_NOTICES:
                    return found
    return found


def error_messages(value: Any) -> tuple[list[str], bool]:
    """The ORA- messages in a returned value, and whether the value holds other text.

    Free text a job or an application wrote (ADDITIONAL_INFO, a log line) is untrusted
    data. Only the error messages in it are quoted, each to its first full stop; the
    rest is reported as present, never repeated, so an instruction appended to an
    error (trial 21, DIAG-02) is not restated by the harness.
    """

    if value is None:
        return [], False
    text = str(value)
    messages: list[str] = []
    rest = text
    for match in _ORA_MESSAGE.finditer(text):
        line = match.group(0)
        end = _SENTENCE_END.search(line)
        message = (line[: end.start()] if end else line).strip()
        rest = rest.replace(message, "", 1)
        messages.append(message if len(message) <= 160 else message[:157] + "...")
    return messages[:3], bool(re.sub(r"[\s.;,]", "", rest))


def _evidence(lookup: LookupFacts) -> list[str]:
    seen = coverage(lookup)
    head = f"{lookup.operation_id}{_parameters(lookup.parameters)}: {seen.describe()}"
    # A count of rows not stated ("... and 9 more") says nothing without the rows.
    stated = [fact for fact in facts(lookup) if not fact.startswith("... and ")]
    stated = stated or _scheduler_facts(lookup)
    if not stated:
        return [f"{head} (lookup {lookup.sequence})"]
    return [f"{head} (lookup {lookup.sequence})"] + [
        f"{fact} (lookup {lookup.sequence})" for fact in stated
    ]


def _scheduler_facts(lookup: LookupFacts) -> list[str]:
    """A scheduler lookup's rows as facts: the job, its run, status and error."""

    if not lookup.succeeded:
        return []
    columns = [str(column).upper() for column in lookup.columns]

    def get(row: Sequence[Any], name: str) -> Any:
        index = columns.index(name) if name in columns else len(row)
        return row[index] if index < len(row) else None

    stated: list[str] = []
    rows = lookup.rows[:_MAX_ROWS]
    if lookup.operation_id in RETAINED_HISTORY:
        for row in rows:
            run = f"log {get(row, 'LOG_ID')}" if get(row, "LOG_ID") is not None else "run"
            owner = f"{get(row, 'OWNER')}." if get(row, "OWNER") else ""
            stated.append(
                f"{owner}{get(row, 'JOB_NAME')} {run} at {get(row, 'ACTUAL_START_UTC')}: "
                f"{get(row, 'STATUS')}"
                + _error_text(get(row, "ERROR_NUMBER"), get(row, "ADDITIONAL_INFO"))
            )
    elif lookup.operation_id == "dba.scheduler_job_detail":
        # Each value with its field: a JOB_ACTION that names a unit is evidence about the
        # job, not about that unit (trial 26 DIAG-02, evidence_reference).
        for row in rows:
            stated.append(
                f"{get(row, 'OWNER')}.{get(row, 'JOB_NAME')}: job_type {get(row, 'JOB_TYPE')}, "
                f"job_action {str(get(row, 'JOB_ACTION'))[:120]}, enabled "
                f"{get(row, 'ENABLED')}, state {get(row, 'STATE')}, "
                f"{get(row, 'FAILURE_COUNT')} failure(s) in "
                f"{get(row, 'RUN_COUNT')} run(s)"
            )
    else:
        return []
    if len(lookup.rows) > _MAX_ROWS:
        stated.append(f"... and {len(lookup.rows) - _MAX_ROWS} more row(s)")
    return stated


def _error_text(number: Any, info: Any) -> str:
    messages, other = error_messages(info)
    text = ""
    if number not in (None, "", 0):
        text += f", error number {number}"
    if messages:
        text += ": " + "; ".join(messages)
    if other:
        text += " (the value holds further text, not quoted)"
    return text


def _returned_errors(lookups: Sequence[LookupFacts]) -> dict[str, list[int]]:
    """Each Oracle error message the lookups returned, with the lookups it came from."""

    found: dict[str, list[int]] = {}
    for lookup in lookups:
        if not lookup.succeeded:
            continue
        columns = [str(column).upper() for column in lookup.columns]
        wanted = [columns.index(name) for name in _ERROR_COLUMNS if name in columns]
        for row in lookup.rows:
            for index in wanted:
                if index >= len(row):
                    continue
                for message in error_messages(row[index])[0]:
                    numbers = found.setdefault(message, [])
                    if lookup.sequence not in numbers:
                        numbers.append(lookup.sequence)
    return found


def _application_errors(lookups: Sequence[LookupFacts]) -> dict[str, list[int]]:
    """Application error codes actually returned, never a guess at their raiser.

    Scheduler history below already cites a job only beside its own retained run's
    error. An action naming a program or a filtered source search cannot establish
    which code raised an error, so neither supplies a source claim here.
    """

    found: dict[str, list[int]] = {}
    for lookup in lookups:
        if not lookup.succeeded:
            continue
        columns = [str(column).upper() for column in lookup.columns]
        for row in lookup.rows:
            codes: set[int] = set()
            for column, value in zip(columns, row, strict=False):
                if column == "ERROR_NUMBER" and re.fullmatch(r"-?20[0-9]{3}", str(value)):
                    codes.add(abs(int(value)))
                elif column in _ERROR_COLUMNS:
                    # Read only the sanitized error messages, never appended free text.
                    for message in error_messages(value)[0]:
                        codes.add(int(message[4:9]))
            for code in sorted(codes):
                if 20000 <= code <= 20999:
                    numbers = found.setdefault(f"ORA-{code:05d}", [])
                    if lookup.sequence not in numbers:
                        numbers.append(lookup.sequence)
    return found


def _parameters(parameters: dict[str, Any]) -> str:
    shown = [
        f"{name} {str(value)[:60]}"
        for name, value in parameters.items()
        if value is not None and value != "" and name != "why"
    ]
    return f" ({', '.join(shown)})" if shown else ""


def _row_count(lookup: LookupFacts) -> int:
    return len(lookup.rows) if lookup.row_count is None else lookup.row_count


def _cite(numbers: Sequence[int]) -> str:
    return ("lookup " if len(numbers) == 1 else "lookups ") + _and(str(n) for n in numbers)


def _and(items: Iterable[str]) -> str:
    listed = list(items)
    if len(listed) <= 1:
        return "".join(listed)
    return ", ".join(listed[:-1]) + " and " + listed[-1]
