"""Checking a few kinds of claim in a Kiwi answer against what the request looked up.

Prompt rules alone did not stop three kinds of wrong statement in the K-7 trials, though
each rule was already in the prompt (round-17 review, 2026-10-18):

- **Session role.** ``V$SESSION.ROW_WAIT_OBJ#`` and its siblings describe the row a
  session is *waiting* for. An answer told the user to read them for the blocking
  session, which waits for nothing.
- **Oracle ``MOD``.** ``MOD(m, n)`` takes the sign of ``m``, so ``MOD(1041, 1042)`` is
  1041, not -1. An answer gave 1041 as an example of ``MOD(x, 1042) = -1``.
- **Where a change belongs.** An answer said the fix belongs in a unit whose source no
  lookup returned.

Trial 18 (2026-10-18) added two about the evidence itself (Q3, ``evidence.py``):

- **A larger cap for a complete result.** A next step asked for a larger ``row_limit``
  on a run-history lookup that had returned 1 row of the 20 asked for, untruncated: every
  retained row was already there.
- **History from current state.** A next step offered a catalog listing to show whether
  a synonym "was dropped or never created". The catalog shows what exists now.

Trial 19 (2026-10-18) added one more about next checks (C29):

- **A check the account cannot make.** A next step offered ALL_OBJECTS/ALL_SOURCE
  lookups of an owner that the request's complete ``schema.list_schemas`` did not list,
  or a source lookup of a VIEW, as settling a question. As this account, neither can
  return anything.

Each kind can be checked mechanically against general Oracle semantics and the lookups
this request made, so the harness checks them rather than trusting the prompt. Nothing
here knows a case, a fixture value or an expected answer: the session roles come from
the columns the blocking and session lookups return, the arithmetic from Oracle's
definition of ``MOD``, and the read units from the source lookups that returned rows.

A finding is not a verdict. The checks match wording, so they can miss a wrong claim
phrased another way, and can flag a right one; the service gives the model one bounded
chance to revise (see ``CopilotService._kiwi_loop``), and a flagged claim the model
keeps is delivered as written, with the finding recorded. Argument flow (which value
reaches a call) is not checked here: tracing it needs a parse of the PL/SQL the model
read, which this module does not attempt.

A claim a check recognises but cannot compute, such as ``MOD`` of ``Infinity`` or of an
operand too long to work with exactly, is returned as *unchecked*: never as a finding,
which would put a correction the harness could not make in front of the model, and never
silently, which would read as a claim that was checked and held (round 18, C22).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, Inexact, InvalidOperation, Rounded, localcontext
from typing import Any

from harness_api.copilot.evidence import RETAINED_HISTORY, coverage
from harness_api.copilot.fences import fenced_blocks
from harness_api.copilot.support import (
    IDENTIFIER,
    QUALIFIED,
    Evidence,
    Identity,
    Item,
    SourceRead,
    Subject,
    answer_items,
    clauses,
    covers,
    evidence_map,
    line_ranges,
    line_references,
    line_units,
    number,
    prose_lines,
    qualified_names,
    subclauses,
)
from harness_worker.catalog import cached_catalog
from harness_worker.errors import HarnessError


@dataclass(frozen=True)
class LookupFacts:
    """What one lookup returned, as the checks need it."""

    operation_id: str
    succeeded: bool
    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]
    parameters: dict[str, Any]
    sequence: int = 0
    # The rest of the request-local record (Q3): which call it was, how many rows reached
    # the model and whether the harness cut the result. ``None`` rows: count ``rows``.
    call_id: str = ""
    row_count: int | None = None
    truncated: bool = False


# Every finding kind the harness reports, in the order the checks run; the published
# protocol and the TypeScript client list the same (C26). read_coverage, read_scope and
# band_reachability come from read_coverage.py, next_checks_block from a Kiwi diagnosis's
# unreadable typed steps (service.py), missing_remedy, transaction_state and
# control_flow from completeness.py (C34, C35, Sync Point 2J, Wave 2X), plan_expression
# from a plain plan explanation, edit_description from an accepted edit's prose and
# proposal_directive from its code (service.py).
FINDING_KINDS = (
    "session_role",
    "lock_origin",
    "session_cursor",
    "mod_arithmetic",
    "change_location",
    "next_check_cap",
    "next_check_access",
    "remedy_alternative",
    "rights_model",
    "elapsed_bound",
    "row_grouping",
    "history_claim",
    "evidence_reference",
    "next_check_unknown",
    "schema_scope",
    "required_change",
    "job_intervention",
    "future_outcome",
    "absence_inference",
    "lock_wait_exception",
    "application_error_number",
    "compiler_error_position",
    "read_coverage",
    "read_scope",
    "band_reachability",
    "next_checks_block",
    "missing_remedy",
    "transaction_state",
    "control_flow",
    "plan_expression",
    "edit_description",
    "proposal_directive",
)


@dataclass(frozen=True)
class ClaimFinding:
    kind: str  # one of FINDING_KINDS
    claim: str  # the answer's words, cut to a line
    problem: str  # what the evidence or Oracle's semantics say instead

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "claim": self.claim, "problem": self.problem}


@dataclass(frozen=True)
class ClaimReview:
    """What the checks found, and the claims they recognised but could not check."""

    findings: list[ClaimFinding]
    unchecked: list[ClaimFinding]


def review_claims(answer: str, lookups: Iterable[LookupFacts]) -> ClaimReview:
    """Kiwi diagnosis findings; lock-wait semantics are confined to plain answers."""

    facts = list(lookups)
    unchecked: list[ClaimFinding] = []
    findings = _session_roles(answer, facts)
    findings += _lock_origins(answer)
    findings += _session_cursors(answer, facts)
    findings += _mod_arithmetic(answer, unchecked)
    findings += _change_locations(answer, facts)
    findings += _next_check_caps(answer, facts)
    findings += _next_check_access(answer, facts)
    findings += _remedy_alternatives(answer, unchecked)
    findings += _rights_models(answer)
    findings += _elapsed_bounds(answer, facts, unchecked)
    findings += _row_groupings(answer, facts)
    findings += _history_claims(answer)
    findings += _evidence_references(answer, facts)
    findings += _next_check_unknowns(answer)
    findings += _schema_scope(answer, facts)
    findings += _required_changes(answer, facts)
    findings += _job_interventions(answer, facts)
    findings += _future_outcomes(answer, facts)
    findings += _absence_inferences(answer, facts)
    findings += application_error_claims(answer)
    findings += compiler_error_position_claims(answer, facts)
    return ClaimReview(findings, unchecked)


def review_plain_claims(answer: str) -> ClaimReview:
    """The checks that need no lookup, for a plain diagnosis (C32).

    A plain answer has no lookups, so only checks resting on Oracle's documented
    behaviour alone apply: Oracle ``MOD``, a synonym offered in place of a privilege, and
    the rights model a privilege remedy describes (Sync Point 2N), and ordinary lock waits
    presented as raised or handled exceptions (K12). The checks that compare
    an answer with returned rows have nothing to compare.
    """

    unchecked: list[ClaimFinding] = []
    findings = _mod_arithmetic(answer, unchecked)
    findings += _remedy_alternatives(answer, unchecked)
    findings += _rights_models(answer)
    findings += lock_wait_exception_claims(answer)
    findings += application_error_claims(answer)
    return ClaimReview(findings, unchecked)


def check_claims(answer: str, lookups: Iterable[LookupFacts]) -> list[ClaimFinding]:
    """The findings of ``review_claims``."""

    return review_claims(answer, lookups).findings


def revision_note(findings: Sequence[ClaimFinding], *, lookups: bool = True) -> str:
    """The harness's message asking for one revised answer.

    ``lookups=False`` is the note for a plain diagnosis, which made none (C32).
    """

    listed = "\n".join(
        f'{index}. You wrote: "{finding.claim}" — {finding.problem}'
        for index, finding in enumerate(findings, 1)
    )
    if not lookups:
        return (
            "[harness] Before your answer is delivered, the harness checked some of its "
            "claims against Oracle's documented behaviour, and these do not hold as "
            f"written:\n{listed}\n"
            "Write your final answer again, complete, in place of the one you just gave. "
            "Correct or remove each listed claim; where the context does not settle the "
            "point, say it is unknown rather than guess. Keep everything else. If a listed "
            "claim is right after all, keep it and say why."
        )
    return (
        "[harness] Before your answer is delivered, the harness checked some of its "
        "claims against the lookups you made and Oracle's documented behaviour, and "
        "these do not hold as written:\n"
        f"{listed}\n"
        "Write your final answer again, complete, in place of the one you just gave. "
        "Correct or remove each listed claim; where the evidence does not settle the "
        "point, say it is unknown or leave the analysis out rather than guess. Keep "
        "everything else your lookups support. If a listed claim is right after all, keep "
        "it and name the lookup that supports it. Your new answer replaces everything "
        "you wrote in this request, the notes between lookups included, so it must "
        "stand alone: carry over only what your lookups support. You cannot make "
        "lookups now."
    )


# -- session role ----------------------------------------------------------------------

_ROW_WAIT = r"ROW_WAIT_(?:OBJ|FILE|BLOCK|ROW)#"
# "SID 42's ROW_WAIT_OBJ#", "session 42's `ROW_WAIT_ROW#`".
_POSSESSIVE = re.compile(
    rf"\b(?:SID|session)\s+(\d+)(?:'s|’s)[^.;:—\n]{{0,40}}?{_ROW_WAIT}", re.IGNORECASE
)
# "ROW_WAIT_OBJ# and ROW_WAIT_ROW# for SID 42", "... of session 42".
_FOR_SESSION = re.compile(
    rf"{_ROW_WAIT}[^.;:—\n]{{0,80}}?\b(?:for|of|in)\s+(?:the\s+)?(?:SID|session)\s+(\d+)",
    re.IGNORECASE,
)
_SID_EQUALS = re.compile(r"\bsid\s*=\s*(\d+)", re.IGNORECASE)


def _session_roles(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    waiting, not_waiting = _session_waits(facts)
    if not not_waiting:
        return []
    findings: list[ClaimFinding] = []
    seen: set[str] = set()

    def flag(sid: str, claim: str) -> None:
        if sid in not_waiting and sid not in waiting and sid not in seen:
            seen.add(sid)
            waiters = ", ".join(sorted(waiting, key=int)) or "none returned"
            findings.append(
                ClaimFinding(
                    "session_role",
                    claim,
                    f"the lookups show session {sid} is not waiting ({not_waiting[sid]}); "
                    f"waiting sessions: {waiters}. Oracle sets ROW_WAIT_OBJ#, "
                    "ROW_WAIT_FILE#, ROW_WAIT_BLOCK# and ROW_WAIT_ROW# for a session "
                    "waiting on a row lock (ROW_WAIT_OBJ# not -1), so they name the row "
                    "the waiter wants and say nothing about a session that is not waiting.",
                )
            )

    for line in _prose_lines(answer):
        for pattern in (_POSSESSIVE, _FOR_SESSION):
            for match in pattern.finditer(line):
                flag(match.group(1), _excerpt(line, match.start(), match.end()))
    for block in fenced_blocks(answer):
        if re.search(_ROW_WAIT, block.text, re.IGNORECASE):
            sids = set(_SID_EQUALS.findall(block.text))
            if not sids & waiting:
                for sid in sorted(sids):
                    flag(sid, _excerpt(block.text))
    return findings


def _session_waits(facts: Sequence[LookupFacts]) -> tuple[set[str], dict[str, str]]:
    """SIDs the lookups show waiting, and those they show not waiting (with why).

    A blocking lookup names both sides of each wait. A session lookup shows a session
    waiting when its ``BLOCKING_SESSION`` is set, and not blocked when it is null.
    """

    waiting: set[str] = set()
    not_waiting: dict[str, str] = {}
    for fact in facts:
        if not fact.succeeded:
            continue
        upper = [str(column).upper() for column in fact.columns]
        place = f"lookup {fact.sequence}" if fact.sequence else fact.operation_id
        if "WAITING_SID" in upper and "BLOCKING_SID" in upper:
            w, b = upper.index("WAITING_SID"), upper.index("BLOCKING_SID")
            for row in fact.rows:
                if row[w] is not None:
                    waiting.add(str(row[w]))
                if row[b] is not None:
                    not_waiting.setdefault(str(row[b]), f"it is the blocking session, {place}")
        elif "SID" in upper and "BLOCKING_SESSION" in upper:
            s, b = upper.index("SID"), upper.index("BLOCKING_SESSION")
            for row in fact.rows:
                if row[s] is None:
                    continue
                if row[b] is not None:
                    waiting.add(str(row[s]))
                else:
                    not_waiting.setdefault(str(row[s]), f"BLOCKING_SESSION is null, {place}")
    for sid in waiting:
        not_waiting.pop(sid, None)
    return waiting, not_waiting


# -- lock origin (Wave 2B, trial 21 DIAG-03) ---------------------------------------------

# "the statement that took the lock", "the lock-holding DML", "the cursor holding the lock".
_LOCK_ORIGIN = re.compile(
    r"\b(?:statement|sql|dml|operation|update|delete|insert|merge|cursor|query)\s+"
    # "the statement in its transaction that took the lock" (Sync Point 2Q, trial 26).
    r"(?:(?:in|of|from)\s+(?:[\w'’]+\s+){1,3}?)?"
    r"(?:that|which)\s+(?:first\s+)?(?:took|takes|acquired|acquires|obtained|placed|holds|"
    r"held|is\s+holding|took\s+out|caused|created|locked)\b"
    r"|\block[- ](?:taking|holding|acquiring)\s+(?:statement|sql|dml|operation|cursor|update)"
    r"|\b(?:statement|sql|dml|cursor)\s+(?:holding|taking)\s+the\s+(?:row\s+)?lock\b"
    r"|\b(?:took|acquired|obtained|holds|placed)\s+the\s+(?:row\s+)?lock\s+(?:with|using|via|"
    r"through|by\s+running)\b"
    # "the statement that put the lock on the row" (Sync Point 2D, trial 22 DIAG-03).
    r"|\b(?:statement|sql|dml|operation|update|delete|insert|merge|cursor|query)\s+"
    r"(?:that|which)\s+(?:first\s+)?(?:put|puts|set|sets)\s+(?:the|a|its)\s+(?:row\s+)?lock\b"
    r"|\b(?:put|puts|placed|places)\s+(?:the|a|its)\s+(?:row\s+)?lock\s+on\b",
    re.IGNORECASE,
)
# A session's own cursor: what V$SESSION's SQL_ID or a cursor-cache search shows.
_SESSION_CURSOR = re.compile(
    r"\b(?:PREV_)?SQL_ID\b|\bcursor\s+cache\b|\bcursor_search\b|\bV\$SQL(?:AREA)?\b"
    r"|\b(?:current|last|latest|previous)\s+(?:SQL|statement|cursor)\b"
    r"|\b(?:last|most\s+recently)[- ]used\s+(?:SQL|statement|cursor)\b",
    re.IGNORECASE,
)
# The session's cursor said to be missing from the cache: "is not in the cache", "was
# not found", "aged out", "no row".
_CURSOR_ABSENT = re.compile(
    r"\bnot\s+(?:\w+\s+)?(?:in|found|present|cached|there)\b|\babsent\b|\bmissing\b"
    r"|\baged\s+out\b|\bno\s+(?:rows?|match|entry)\b|\bempty\b|\bnot\s+cached\b",
    re.IGNORECASE,
)
# One fact given as the reason for, or the consequence of, the other.
_CAUSAL = re.compile(
    r"\b(?:because|since|so|therefore|thus|hence|due\s+to|given\s+that|as\s+a\s+result|"
    r"which\s+is\s+why|that\s+is\s+why|consequently)\b|,\s*as\s+(?=\S)",
    re.IGNORECASE,
)
# A sentence that gives the previous one's reason, or draws its consequence.
_REASON_NEXT = re.compile(
    r"^\W*(?:(?:this|that|it)\s+is\s+because|because|the\s+reason\s+is)\b", re.IGNORECASE
)
_CONSEQUENCE_NEXT = re.compile(
    r"^\W*(?:so|therefore|thus|hence|consequently|as\s+a\s+result|that\s+is\s+why|"
    r"this\s+is\s+why|for\s+that\s+reason)\b",
    re.IGNORECASE,
)
# Wording that keeps the two apart, or asks.
_LOCK_ORIGIN_KEPT_APART = re.compile(
    r"\bnot\s+necessarily\b|\bneed\s+not\b|\bmay\s+(?:not|differ)\b|\bmight\s+not\b"
    r"|\bcan(?:not|'t)\s+(?:show|tell|establish|identify|say)\b"
    r"|\b(?:does|do)\s+not\s+(?:show|tell|establish|identify)\b"
    # A question about the lock's statement, not a decision ("whether to end it").
    r"|\bwhether\b(?!\s+(?:or\s+not\s+)?to\b)"
    # "the lock-taking statement may have run earlier": a different, earlier statement.
    r"|\b(?:may|might|could)\s+have\s+(?:run|been\s+(?:run|issued|executed))\s+(?:earlier|before)\b"
    r"|\bdiffer"
    # The lock's statement itself called unknown ("... that took the lock is unknown"),
    # not something else in the sentence ("so what it did to the row is unknown").
    r"|\block\s+(?:is|remains|stays|was)\s+(?:still\s+)?(?:unknown|not\s+known)\b"
    # An explicit denial of the link: "that says only it is not cached, nothing about the
    # statement that took the lock" (Sync Point 2G, trial 23 DIAG-03).
    r"|\b(?:says?|said|shows?|tells?|reveals?|establish(?:es)?|impl(?:y|ies))\b[^.;]{0,60}?"
    r"\bnothing\s+about\b"
    r"|\bnothing\s+about\s+(?:the|which|what)\s+(?:\w+\s+){0,2}?(?:statement|sql|dml|operation)\b",
    re.IGNORECASE,
)


# The session's cursor said not to be the lock's statement: "not the statement in its
# transaction that took the lock", "is not the lock-taking DML". No lookup shows which
# statement took a row lock, so its identity with the cursor is neither established nor
# ruled out (Sync Point 2Q, trial 26 DIAG-03). "not necessarily" leaves it open.
_EXCLUDED = re.compile(
    r"\b(?:not|isn['’]t|wasn['’]t)\s+(?:the\s+|a\s+)?(?:same\s+(?:\w+\s+)?as\s+(?:the\s+)?)?"
    r"(?:lock[- ](?:taking|holding|acquiring)\s+)?(?:statement|sql|dml|cursor|query|operation|"
    r"one)\b",
    re.IGNORECASE,
)


def _lock_origins(answer: str) -> list[ClaimFinding]:
    """A blocker's own cursor equated with the statement that took its lock.

    ``V$SESSION.SQL_ID`` (and ``PREV_SQL_ID``) is the session's current (or previous)
    cursor field. A row lock is held by the transaction, and the DML that took it may have run
    several statements earlier; an INACTIVE blocker idle at its client usually ran it
    before its latest call. So neither column, nor an empty cursor-cache search for it,
    says anything about the statement that acquired the lock (trial 21, DIAG-03: "The
    statement that took the lock is not in the cursor cache under SID 42's SQL_ID").
    Flagged: a sentence naming the lock-taking statement together with the session's
    cursor evidence, unless it keeps the two apart (not necessarily, may differ, cannot
    show, whether). General Oracle semantics; no case or lookup is consulted. Saying the
    cursor is *not* the lock's statement ("not the statement in its transaction that took
    the lock") is flagged too, whatever else the sentence says: no lookup shows which
    statement took the lock, so it is neither identified nor ruled out (Sync Point 2Q).

    The cursor's absence from the cache given as the reason the lock's statement is
    unknown or not visible ("..., because its last-used cursor is not in the cache"),
    or the lock's statement as its consequence, is flagged however the sentence hedges:
    the absence explains nothing about that statement (Sync Point 2D, trial 22 DIAG-03).
    So is the same link across two sentences, the second opening "This is because ..."
    or "So ...".
    """

    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        parts = _clauses(line)
        for index, clause in enumerate(parts):
            origin = _LOCK_ORIGIN.search(clause)
            following = parts[index + 1] if index + 1 < len(parts) else ""
            excluded = False
            if origin and _SESSION_CURSOR.search(clause):
                if not _excludes(clause) and re.search(
                    r"does\s+not\s+show\s+which\s+statement[^.;]{0,80}took\s+the\s+lock",
                    clause,
                    re.I,
                ):
                    continue
                excluded = _excludes(clause)
                if not excluded and _kept_apart(clause) and not _absence_as_cause(clause):
                    continue
            elif origin and _REASON_NEXT.match(following) and _cursor_absent(following):
                clause = f"{clause} {following}"
            elif (
                _cursor_absent(clause)
                and _CONSEQUENCE_NEXT.match(following)
                and (later := _LOCK_ORIGIN.search(following))
            ):
                clause = f"{clause} {following}"
                origin = re.search(re.escape(later[0]), clause)
            else:
                continue
            assert origin is not None
            if excluded:
                problem = (
                    "no lookup shows which statement in the session's transaction took the "
                    "lock, so its reported SQL_ID can be neither identified with that "
                    "statement nor ruled out as it. Say that the SQL_ID does not show which "
                    "statement took the lock, and leave that statement unknown."
                )
            else:
                problem = (
                    "a session's SQL_ID is its current cursor field (PREV_SQL_ID the one "
                    "before), not necessarily the statement in its transaction that took "
                    "the lock, which may have run earlier; an empty cursor-cache search for "
                    "that SQL_ID shows only that this cursor is not cached. Say that the "
                    "session's reported SQL_ID is not in the cursor cache, and leave the "
                    "statement that acquired the lock unknown."
                )
            findings.append(
                ClaimFinding("lock_origin", _excerpt(clause, origin.start(), origin.end()), problem)
            )
    return findings


def _excludes(clause: str) -> bool:
    """Whether the part of ``clause`` that names the lock's statement says the session's
    cursor is not it."""

    return any(_LOCK_ORIGIN.search(part) and _EXCLUDED.search(part) for part in subclauses(clause))


def _cursor_absent(text: str) -> bool:
    """Whether ``text`` says the session's cursor is missing from the cache."""

    return any(
        _CURSOR_ABSENT.search(text, cursor.start(), cursor.end() + 60)
        or _CURSOR_ABSENT.search(text, max(0, cursor.start() - 40), cursor.start())
        for cursor in _SESSION_CURSOR.finditer(text)
    )


def _absence_as_cause(clause: str) -> bool:
    """Whether ``clause`` links the lock's statement and the cursor's absence by a
    reason or a consequence: a causal word between the two."""

    origin = _LOCK_ORIGIN.search(clause)
    if origin is None or not _cursor_absent(clause):
        return False
    for cursor in _SESSION_CURSOR.finditer(clause):
        if cursor.start() >= origin.end():
            between = clause[origin.end() : cursor.start()]
        else:
            between = clause[cursor.end() : origin.start()]
        if _CAUSAL.search(between):
            return True
    return False


# A later part that leaves the claim before it open: "..., but that is not established".
_LEFT_OPEN = re.compile(
    r"^(?:but|although|though|yet|however)?,?\s*(?:that|this|which|it)\s+"
    r"(?:is|was|remains?|stays?)\s+(?:still\s+)?(?:unknown|unproven|unverified|"
    r"not\s+(?:known|established|shown|proven|verified))\b",
    re.IGNORECASE,
)


def _kept_apart(clause: str) -> bool:
    """Whether the part of ``clause`` that names the lock's statement keeps it apart
    from the session's cursor, or leaves it open.

    Only that part counts, or the part after it when it refers back ("but that is not
    established"). A qualification elsewhere in the sentence, such as "but whether to
    end the session is the DBA's decision", does not retract the claim (Sync Point 2B).
    """

    parts = subclauses(clause)
    for index, part in enumerate(parts):
        if not _LOCK_ORIGIN.search(part):
            continue
        following = parts[index + 1] if index + 1 < len(parts) else ""
        if _LOCK_ORIGIN_KEPT_APART.search(part) or _LEFT_OPEN.search(following):
            return True
    return False


# -- a session's cursor: current, not last run (Sync Point 2N) -------------------------

# "whose last-run cursor", "what the holder last ran", "the last executed SQL", "the
# statement it ran last".
_LAST_RAN = re.compile(
    r"\blast[- ](?:run|ran|executed|issued)\b|\b(?:ran|executed|issued)\s+last\b"
    r"|\blast\s+(?:SQL|statement|cursor|query)\s+(?:(?:it|that|the\s+\w+|SID\s+\d+)\s+){0,2}"
    r"(?:ran|executed|issued|run)\b",
    re.IGNORECASE,
)
# Said of the cursor evidence: the cursor, its SQL, its SQL_ID, the cursor cache.
_CURSOR_EVIDENCE = re.compile(
    r"\b(?:cursors?|SQL|SQL_IDs?|SQL\s+IDs?|cache[ds]?|V\$SQL\w*)\b", re.IGNORECASE
)
_SID_COLUMN = re.compile(r"^(?:\w+_)?SID$", re.IGNORECASE)
# What the session last ran said to be unknown, or the question asked.
_LAST_RAN_OPEN = re.compile(
    r"\b(?:unknown|unclear|not\s+(?:known|shown|returned|established|necessarily)|no\s+lookup"
    r"|(?:does|do|did)\s*(?:not|n't)\s+(?:show|tell|return|establish)"
    r"|(?:cannot|can't)\s+(?:tell|say|show|read|see|know)"
    r"|PREV_SQL_ID)\b",
    re.IGNORECASE,
)
# A cursor's counters (V$SQL.EXECUTIONS, ROWS_PROCESSED) said of one execution: "24 rows
# processed so far", "has processed 24 rows", "in this execution".
_COUNTER = re.compile(
    r"\b\d[\d,]*\s+(?:rows?\s+(?:processed|updated|changed|modified)|executions?)\b"
    r"|\b(?:processed|updated|changed|modified)\s+\d[\d,]*\s+rows?\b|\bROWS_PROCESSED\b",
    re.IGNORECASE,
)
_ONE_EXECUTION = re.compile(
    r"\bso\s+far\b|\balready\b|\bby\s+now\b|\buntil\s+now\b|\bthis\s+execution\b"
    r"|\bin\s+(?:this|the\s+(?:current|blocked))\s+(?:execution|run|call)\b"
    r"|\b(?:has|have)\s+(?:now\s+)?(?:processed|updated|changed|modified|touched)\s+\d",
    re.IGNORECASE,
)
_CUMULATIVE = re.compile(
    r"\bcumulative\w*|\bin\s+total\b|\btotals?\b|\bacross\b|\bover\s+(?:all|its|those|the|\d)"
    r"|\bsummed\b|\bsince\s+(?:it|the\s+cursor)\s+was\s+(?:loaded|parsed|cached)\b"
    r"|\ball\s+(?:its\s+)?executions\b",
    re.IGNORECASE,
)


# A session, or its cursor, said to be executing now: "the cursor it is running now",
# "is currently executing", "SID 42 is running the UPDATE".
_RUNNING_NOW = re.compile(
    r"\b(?:currently|now|still|actively)\s+(?:running|executing)\b"
    r"|\b(?:running|executing|runs|executes)\s+(?:right\s+)?(?:now|currently|at\s+(?:the\s+"
    r"moment|present))\b"
    r"|(?:\b(?:is|are)|['’]s)\s+(?:running|executing)\b[^.;,]{0,40}?\b(?:now|currently)\b"
    r"|(?:\b(?:is|are)|['’]s)\s+(?:running|executing)\s+(?:the\s+|its\s+|this\s+|that\s+)?"
    r"(?:cursor|statement|SQL\b|query|UPDATE|DELETE|INSERT|MERGE|SELECT)"
    r"|\b(?:cursor|statement|SQL|query)\s+(?:that\s+|which\s+)?(?:it|SID\s+\d+|session\s+\d+|"
    r"the\s+(?:session|blocker|holder))\s+is\s+(?:running|executing)\b",
    re.IGNORECASE,
)
# A denial, a condition or a question before it: "is not running", "if it were".
_RUNNING_DENIED = re.compile(
    r"\b(?:not|never|no\s+longer|nothing|neither|whether|if|would|unless|cannot)\b|n['’]t\b",
    re.IGNORECASE,
)
_SID_MENTION = re.compile(r"\b(?:SID|session)\s*#?\s*(\d+)\b", re.IGNORECASE)


def _session_states(facts: Sequence[LookupFacts]) -> tuple[dict[str, str], dict[str, str]]:
    """Each session's STATUS (ACTIVE, INACTIVE) and the SID each reported SQL_ID is
    reported for, from the session and blocking lookups: SID with STATUS and SQL_ID, and
    the blocking lookup's BLOCKING_SID with BLOCKING_STATUS."""

    status: dict[str, str] = {}
    cursor: dict[str, str] = {}
    for fact in facts:
        if not fact.succeeded:
            continue
        columns = [str(c).upper() for c in fact.columns]
        for row in fact.rows:
            values = dict(zip(columns, row, strict=False))
            for prefix in ("", "BLOCKING_", "WAITING_", "HOLDING_"):
                sid = values.get(f"{prefix}SID")
                if sid is None:
                    continue
                state = values.get(f"{prefix}STATUS")
                if isinstance(state, str) and state.upper() in ("ACTIVE", "INACTIVE"):
                    status[str(sid)] = state.upper()
                if values.get(f"{prefix}SQL_ID"):
                    cursor[str(values[f"{prefix}SQL_ID"])] = str(sid)
    return status, cursor


def _running_subject(line: str, at: int, cursor: dict[str, str]) -> str | None:
    """The session a "running now" claim is about: the SID or reported SQL_ID named last
    before it on the line ("SID 77 waits on SID 42, whose reported SQL_ID ... is the
    cursor it is running now"; "SID 42 ...: Its reported SQL_ID is the cursor it is
    running now"), else the first named after it in the rest of the line."""

    def named(text: str) -> list[tuple[int, str]]:
        found = [(m.start(), m[1]) for m in _SID_MENTION.finditer(text)]
        found += [
            (m.start(), sid)
            for sql_id, sid in cursor.items()
            for m in re.finditer(rf"(?<![\w$#]){re.escape(sql_id)}(?![\w$#])", text)
        ]
        return sorted(found)

    before = named(line[:at])
    if before:
        return before[-1][1]
    after = named(line[at:])
    return after[0][1] if after else None


def _left_open(part: str, last: re.Match[str]) -> bool:
    """Whether the stretch of ``part`` holding ``last``, up to a reason or consequence
    word, leaves what the session last ran unknown. "whose last-run cursor is not in the
    cache, so the statement that took the lock is unknown" leaves the lock open, not
    what the session ran."""

    cuts = [0, *(m.start() for m in _CAUSAL.finditer(part)), len(part)]
    for start, end in zip(cuts, cuts[1:], strict=False):
        if start <= last.start() < end:
            return bool(_LAST_RAN_OPEN.search(part, start, end))
    return False


def _session_cursors(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """A session's reported cursor called what it last ran, or a cursor's counters said
    of one execution.

    ``V$SESSION.SQL_ID`` is the session's current cursor field; what it ran before is
    ``PREV_SQL_ID``. Only an ACTIVE session is executing a call: one the session or
    blocking lookups show INACTIVE, said to be running or executing a statement now
    ("its reported SQL_ID is the cursor it is running now", trial 26 DIAG-03), is
    flagged; the session is the SID or reported SQL_ID named last before the claim on
    its line, else the first after it. When the session lookups (those with a SID
    column) returned a ``SQL_ID`` column and no ``PREV_SQL_ID``, nothing the request
    read shows what a session last ran: "whose last-run cursor is not in the cache"
    and "what the holder last ran" (trial 25, DIAG-03, in the Finding
    and in a note between lookups) promote the reported ID to history. ``V$SQL``'s
    ``EXECUTIONS`` and ``ROWS_PROCESSED`` count every execution of the cursor since it
    was loaded, not the one a blocked session is in: "24 rows processed so far" says the
    blocked execution processed them. A sentence that leaves what the session last ran
    unknown is not flagged, nor a count called cumulative in the part of the sentence
    that gives it.
    """

    findings: list[ClaimFinding] = []
    columns = {str(c).upper() for f in facts if f.succeeded for c in f.columns}
    # A session lookup's cursor columns (SQL_ID, WAITING_SQL_ID): one with a SID column.
    session = {
        c
        for f in facts
        if f.succeeded and any(_SID_COLUMN.search(str(col)) for col in f.columns)
        for c in (str(col).upper() for col in f.columns)
        if c.endswith("SQL_ID")
    }
    reported = bool(session) and not any(c.endswith("PREV_SQL_ID") for c in session)
    counted = bool(columns & {"ROWS_PROCESSED", "EXECUTIONS"})
    status, cursor = _session_states(facts)
    inactive = "INACTIVE" in status.values()
    if not (reported or counted or inactive):
        return findings
    for line in _prose_lines(answer):
        searched = 0
        for clause in _clauses(line):
            for part in subclauses(clause):
                at = line.find(part, searched)
                at = searched if at < 0 else at
                searched = at
                running = _RUNNING_NOW.search(part) if inactive else None
                if (
                    running
                    and not _RUNNING_DENIED.search(part, 0, running.start())
                    and not re.search(
                        r"\b(?:running|executing|runs|executes)\s+(?:nothing|no\s+(?:SQL|statement|query))\b",
                        part[running.start() : running.end() + 30],
                        re.I,
                    )
                ):
                    sid = _running_subject(line, at + running.start(), cursor)
                    if sid is not None and status.get(sid) == "INACTIVE":
                        findings.append(
                            ClaimFinding(
                                "session_cursor",
                                _excerpt(part, running.start(), running.end()),
                                f"the session lookups show SID {sid} INACTIVE: it is not "
                                "running a statement now, and its reported SQL_ID is a "
                                "field the session reports, not one it is executing. Say "
                                f"SID {sid} is INACTIVE and give its reported SQL_ID; which "
                                "statement took its lock stays unknown.",
                            )
                        )
                        continue
                last = _LAST_RAN.search(part) if reported else None
                if last and _CURSOR_EVIDENCE.search(part) and not _left_open(part, last):
                    findings.append(
                        ClaimFinding(
                            "session_cursor",
                            _excerpt(part, last.start(), last.end()),
                            "the session lookups returned SQL_ID, the session's current "
                            "cursor field, and not PREV_SQL_ID: nothing read shows what "
                            "the session last ran. Call it the session's reported SQL_ID "
                            "(and, where a cursor search found no row, say that SQL_ID is "
                            "not in the cursor cache); what the session last ran and the "
                            "statement that took its lock stay unknown.",
                        )
                    )
                    continue
                counter = _COUNTER.search(part) if counted else None
                if (
                    counter
                    and _ONE_EXECUTION.search(part)
                    # Cumulative only in the part that holds the count (Sync Point 2O):
                    # "...; the cumulative number of executions is 12" qualifies 12.
                    and not _CUMULATIVE.search(part)
                ):
                    findings.append(
                        ClaimFinding(
                            "session_cursor",
                            _excerpt(part, counter.start(), counter.end()),
                            "V$SQL's EXECUTIONS and ROWS_PROCESSED are cumulative: they "
                            "count every execution of the cursor since it was loaded, not "
                            "the one a session is in now. Say the figures are the cursor's "
                            "totals; how many rows the current execution has processed is "
                            "not shown.",
                        )
                    )
    return findings


# -- Oracle MOD --------------------------------------------------------------------------

_NUMBER = r"[−-]?(?:\d{1,3}(?:,\d{3})+(?!\d)|\d+(?:\.\d+)?)"
_MOD = re.compile(
    rf"MOD\(\s*(?P<m>[^,()]+?)\s*,\s*(?P<n>{_NUMBER})\s*\)`?\s*"
    rf"(?:=|==|returns|is|equals|gives|yields)\s*`?(?P<r>{_NUMBER})(?!\w)",
    re.IGNORECASE,
)
_EXAMPLES = re.compile(
    r"\b(?:for example|for instance|e\.g\.|such as)\s*,?\s*"
    rf"(?P<list>{_NUMBER}(?:\s*(?:,|\bor\b|\band\b)?\s*(?:…|\.\.\.|{_NUMBER}))*)",
    re.IGNORECASE,
)
# A value given to the dividend's own name after the claim: "fires for p_qty = 359".
_ASSIGNED = r"`?\s*(?:=|:=)\s*`?(?P<x>{number})(?!\w)"


def _examples(line: str, claims: list[re.Match[str]]) -> list[tuple[int, int, list[str]]]:
    """Example values in ``line``: span and numbers, listed or assigned to the dividend."""

    found = [
        (example.start(), example.end(), re.findall(_NUMBER, example["list"]))
        for example in _EXAMPLES.finditer(line)
    ]
    for name in {claim["m"].strip("` ") for claim in claims}:
        if not re.fullmatch(r"[A-Za-z_][\w$#.]*", name):
            continue
        pattern = rf"(?<![\w$#.]){re.escape(name)}" + _ASSIGNED.format(number=_NUMBER)
        for match in re.finditer(pattern, line, re.IGNORECASE):
            found.append((match.start(), match.end(), [match["x"]]))
    return sorted(found)


# The digits ``MOD`` may need to be exact: from the larger operand's leading digit to the
# smaller one's last. Oracle's NUMBER holds 38 significant digits with exponents to about
# 125 and -130; a claim past this is reported unchecked rather than computed loosely.
_MAX_MOD_DIGITS = 400
_UNCHECKED_MOD = (
    "not checked: an operand is not a finite number the harness can compute MOD with "
    "exactly, so this claim was neither confirmed nor corrected."
)


class UncheckedOperand(ValueError):
    """An operand ``oracle_mod`` cannot compute with exactly."""


def _mod_arithmetic(answer: str, unchecked: list[ClaimFinding]) -> list[ClaimFinding]:
    findings: list[ClaimFinding] = []

    def mod(m: Decimal, n: Decimal, claim: str) -> Decimal | None:
        try:
            return oracle_mod(m, n)
        except UncheckedOperand:
            unchecked.append(ClaimFinding("mod_arithmetic", claim, _UNCHECKED_MOD))
            return None

    for line in _prose_lines(answer):
        claims = list(_MOD.finditer(line))
        for claim in claims:
            m, n, r = _decimal(claim["m"]), _decimal(claim["n"]), _decimal(claim["r"])
            if n is None or r is None or m is None or n == 0:
                continue
            excerpt = _excerpt(line, claim.start(), claim.end())
            actual = mod(m, n, excerpt)
            if actual is not None and actual != r:
                findings.append(
                    ClaimFinding(
                        "mod_arithmetic",
                        excerpt,
                        f"Oracle's MOD({_written(claim['m'])}, {_written(claim['n'])}) is "
                        f"{_plain(actual)}, not {_written(claim['r'])}: MOD takes the sign "
                        "of its first argument.",
                    )
                )
        for start, end, values in _examples(line, claims):
            before = [c for c in claims if c.end() <= start]
            if not before:
                continue
            claim = before[-1]
            n, r = _decimal(claim["n"]), _decimal(claim["r"])
            if n is None or r is None or n == 0 or _decimal(claim["m"]) is not None:
                continue
            excerpt = _excerpt(line, claim.start(), end)
            wrong: list[tuple[str, Decimal]] = []
            for text in values:
                x = _decimal(text)
                value = None if x is None else mod(x, n, excerpt)
                if value is not None and value != r:
                    wrong.append((text, value))
            if wrong:
                shown = "; ".join(
                    f"MOD({_written(x)}, {_written(claim['n'])}) is {_plain(value)}"
                    for x, value in wrong
                )
                findings.append(
                    ClaimFinding(
                        "mod_arithmetic",
                        excerpt,
                        f"an example given for MOD(..., {_written(claim['n'])}) = "
                        f"{_written(claim['r'])} does not satisfy it in Oracle: {shown}. MOD "
                        "takes the sign of its first argument, so a positive value never has "
                        "a negative remainder.",
                    )
                )
    return findings


def oracle_mod(m: Decimal, n: Decimal) -> Decimal:
    """Oracle's ``MOD``: ``m - n * TRUNC(m / n)``, so the result has the sign of ``m``.

    Exact, or ``UncheckedOperand``: Decimal's ``%`` truncates its quotient the same way,
    and runs here with enough precision for every digit both operands hold and with
    rounding trapped, so no digit is lost on the way.
    """

    if not (m.is_finite() and n.is_finite()):
        raise UncheckedOperand("an operand is not finite")
    if n == 0:
        return m
    if m == 0:
        return Decimal(0)
    digits = max(m.adjusted(), n.adjusted()) - min(_exponent(m), _exponent(n)) + 2
    if digits > _MAX_MOD_DIGITS:
        raise UncheckedOperand("the operands need more digits than the check computes with")
    with localcontext() as context:
        context.prec = digits
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        try:
            return m % n
        except (ArithmeticError, InvalidOperation) as exc:
            raise UncheckedOperand(str(exc)) from None


def _exponent(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)  # finite, checked by the caller
    return exponent


def _decimal(text: str) -> Decimal | None:
    """The number ``text`` writes, or None. Infinity and NaN parse: the check refuses them."""

    cleaned = text.strip().strip("`").replace("−", "-").replace(",", "")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _written(text: str) -> str:
    """An operand as the answer wrote it, digit for digit, so a finding never misquotes
    one. Only thousands separators go: inside ``MOD(...)`` they would read as arguments."""

    return text.strip().strip("`").replace("−", "-").replace(",", "")


def _plain(value: Decimal) -> str:
    """A computed value in plain digits, exactly: no exponent, no trailing zeros."""

    shown = format(value, "f")
    if "." in shown:
        shown = shown.rstrip("0").rstrip(".")
    return "0" if shown in ("-0", "") else shown


# -- where a change belongs ------------------------------------------------------------

_IDENTIFIER = IDENTIFIER
_LOCATION = re.compile(
    r"\b(?:fix|change|repair|correction|remedy)\s+"
    r"(?:belongs|goes|lies|must\s+be\s+made|should\s+be\s+made|is\s+needed|is\s+required)"
    r"\s+in(?:side)?\s+(?:the\s+)?"
    rf"(?P<target>`[^`\n]+`|{_IDENTIFIER}(?:\.{_IDENTIFIER})+)",
    re.IGNORECASE,
)
# "`PKG`'s post_entry": a member named after the unit, if it reads as one.
_POSSESSIVE_MEMBER = re.compile(rf"^'s\s+(?:`({_IDENTIFIER})`|({_IDENTIFIER}))")
_AT_LINE = re.compile(r"^[^.;]*?\blines?\s+(\d+)", re.IGNORECASE)
# Words a backticked target can carry that are not part of a unit's name.
_NOISE = frozenset({"S", "THE", "ERROR", "PATH", "BODY", "SPEC", "PACKAGE", "PROCEDURE"})


@dataclass(frozen=True)
class _Read:
    lookup: int
    read: SourceRead


def _change_locations(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """A change placed in a unit, or a member, is supported only by that code, read.

    Identity, not a matching word (C24): a unit of the same name in another schema is
    not the one read, a name that only appears in a call is a dependency, and a package
    read does not mean each member was. A member needs its implementation read whole,
    in a body or a standalone unit: a declaration in a specification, a header line or
    part of the member is not enough. A line given must be among the lines read.
    """

    reads = [
        _Read(entry.lookup, read)
        for entry in evidence_map(facts).values()
        if entry.succeeded
        for read in entry.source
    ]
    findings: list[ClaimFinding] = []
    for line in prose_lines(answer):
        for match in _LOCATION.finditer(line):
            target = match["target"].strip("`").upper()
            parts = [p for p in re.findall(_IDENTIFIER, target) if p not in _NOISE]
            if not parts:
                continue
            rest = line[match.end() :]
            member = _possessive(rest, reads)
            at = _AT_LINE.match(rest)
            problem = _unsupported_change(parts, member, number(at[1]) if at else None, reads)
            if problem:
                findings.append(
                    ClaimFinding(
                        "change_location",
                        _excerpt(line, match.start(), match.end()),
                        f"{problem} Reading that code is a next step that could locate the "
                        "cause; the remedy may also lie outside code.",
                    )
                )
    return findings


def _possessive(rest: str, reads: Sequence[_Read]) -> str:
    found = _POSSESSIVE_MEMBER.match(rest)
    if found is None:
        return ""
    name = (found[1] or found[2]).upper()
    known = {m.name for r in reads for m in r.read.members}
    # A plain word ("error path") is not a member; a backticked, snake_case or known
    # member name is.
    return name if found[1] or "_" in name or name in known else ""


def _unsupported_change(
    parts: list[str], member: str, line: int | None, reads: Sequence[_Read]
) -> str:
    """Why the reads do not support a change in the named place, or "" when they do."""

    units = {r.read.unit.identity.name for r in reads}
    owner = unit = ""
    if len(parts) >= 3:
        owner, unit, member = parts[-3], parts[-2], parts[-1]
    elif len(parts) == 2:
        first, second = parts
        if first in units and not any(
            r.read.unit.identity.matches(Identity(first, second)) for r in reads
        ):
            unit, member = first, second
        else:
            owner, unit = first, second
    elif parts[0] in units or member:
        unit = parts[0]
    elif any(m.name == parts[0] for r in reads for m in r.read.members):
        member = parts[0]
    else:
        unit = parts[0]
    named = Identity(owner, unit) if unit else None
    if named is None:
        candidates = [r for r in reads if any(m.name == member for m in r.read.members)]
    else:
        candidates = [r for r in reads if r.read.unit.identity.matches(named)]
    if not candidates:
        return _unread(named or Identity("", member), reads)
    if member:
        problem = _member_unread(member, candidates)
        if problem:
            return problem
    if line is not None and all(r.read.lines for r in candidates):
        if not any(line in r.read.lines for r in candidates):
            read = "; ".join(f"lookup {r.lookup}: {r.read.describe_lines()}" for r in candidates)
            return (
                f"line {line} of {candidates[0].read.unit.identity} is not among the lines "
                f"the lookups read ({read}), so they do not show what is there."
            )
    return ""


def _unread(target: Identity, reads: Sequence[_Read]) -> str:
    same_name = sorted(
        {
            f"{r.read.unit.identity} (lookup {r.lookup})"
            for r in reads
            if r.read.unit.identity.name == target.name
        }
    )
    if same_name:
        return (
            f"the source lookups returned {', '.join(same_name)}, which is not {target}: "
            "the same name in another schema is another unit, so the lookups do not show "
            "that a change belongs there."
        )
    return (
        f"no source lookup in this request returned the code of {target}, so the lookups "
        "do not show that a change belongs there; a name seen in a call or a dependency "
        "is not its code."
    )


def _member_unread(member: str, candidates: Sequence[_Read]) -> str:
    shown = [(r, m) for r in candidates for m in r.read.members if m.name == member]
    if any(m.implementation and m.whole for _, m in shown):
        return ""
    unit = candidates[0].read.unit
    partial = [(r, m) for r, m in shown if m.implementation]
    unknown = [(r, m) for r, m in partial if m.unknown]
    if unknown and len(unknown) == len(partial):
        r, m = unknown[0]
        return (
            f"lookup {r.lookup} read {r.read.describe_lines()} of {r.read.unit}: "
            f"{member}'s implementation starts at line {m.first_line}, but {m.unknown}, "
            "so the lines read do not establish where it ends or that all of its code "
            "was read."
        )
    if partial:
        r, m = next((r, m) for r, m in partial if not m.unknown)
        return (
            f"lookup {r.lookup} read {r.read.describe_lines()} of {r.read.unit}: "
            f"{member}'s implementation starts at line {m.first_line}, but no "
            f"END {member}; was found in code among the lines returned (an unnamed END "
            "cannot be matched to it), so the lookups do not show all of the code a "
            "change there would replace."
        )
    if shown:
        r, m = shown[0]
        return (
            f"lookup {r.lookup} read {r.read.unit}, which declares {member} at line "
            f"{m.first_line}; a declaration is not the implementation, which was not read, "
            "so the lookups do not show that it needs a change."
        )
    read = "; ".join(
        f"lookup {r.lookup}: {r.read.describe_lines()} of {r.read.unit}" for r in candidates
    )
    return (
        f"the lookups read {read}, and {member} is not defined in those lines, so they do "
        f"not show its code, nor that a change in {unit.identity}.{member} is needed."
    )


# -- next checks and the evidence they would add ------------------------------------------

_LIMIT = r"`?row[_\s]?limit`?|limit|cap"
_MORE_ROWS = re.compile(
    rf"\b(?:larger|bigger|higher|increased?|raised?|wider|greater|more)\s+(?:the\s+)?(?:row\s+)?"
    rf"(?:{_LIMIT})\b"
    r"|\brow[_\s]?limit`?\s+(?:of\s+)?(?:larger|higher|above|over)\b"
    rf"|\b(?:increase|raise|widen|lift)\s+(?:the\s+)?(?:{_LIMIT})\b"
    r"|\b(?:pull|fetch|read|get|retrieve|return)\s+more\b"
    r"|\bmore\s+(?:rows|of\s+the|history|runs|entries)\b"
    r"|\bnext\s+page\b|\brow_offset\b",
    re.IGNORECASE,
)
# Said before the request, these make it the opposite: "do not increase row_limit".
_NEGATED_BEFORE = re.compile(
    r"\b(?:not|no|never|don't|do\s+not|doesn't|cannot|can't|won't|without|nothing|"
    r"no\s+need\s+to|need\s+not|instead\s+of|rather\s+than)\b",
    re.IGNORECASE,
)
# Said after it: "a larger row_limit would not return more".
_NEGATED_AFTER = re.compile(
    r"^[^.;]*?\b(?:would|will|could|can)\s*(?:not|n't)\b|\breturns?\s+(?:no|nothing)\b",
    re.IGNORECASE,
)
# Words that name a lookup without its id.
_OPERATION_PHRASES = {
    "dba.scheduler_run_history": re.compile(
        r"\b(?:run|job)\s+(?:history|log)\b|\bjob_run_details\b", re.IGNORECASE
    ),
}
# Parameters that page or bound a lookup rather than say what it is about.
_PAGING = frozenset({"row_limit", "row_offset", "start_line", "end_line", "why"})
# A parameter given in the step: "job_name=WEEKLY", "owner => 'APP'".
_ASSIGNED_PARAMETER = re.compile(
    r"\b([a-z_]+)\s*(?:=>|=|:)\s*['\"`]?([A-Za-z0-9_$#%.]+)['\"`]?", re.IGNORECASE
)
# Upper-case names in prose that are not a target: SQL words, statuses, object types.
_NOT_A_TARGET = frozenset(
    {
        "SQL", "PL", "DBA", "ORA", "SYS", "UTC", "DDL", "DML", "NULL", "TRUE", "FALSE",
        "AND", "NOT", "SELECT", "FROM", "WHERE", "SYNONYM", "TABLE", "VIEW", "PACKAGE",
        "BODY", "PROCEDURE", "FUNCTION", "TRIGGER", "TYPE", "INDEX", "SEQUENCE", "JOB",
        "FAILED", "SUCCEEDED", "INVALID", "VALID", "ERROR", "NOTE", "MOD", "NVL",
        "PUBLIC", "ROW_LIMIT", "ROW_OFFSET",
    }
)  # fmt: skip


def _next_check_caps(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """Next steps asking a complete lookup for more of the same rows.

    Judged only against the lookups the step is about: the same operation, no parameter
    the step gives that differs, and no other target named (C24). A step about another
    job, or one saying not to ask for more, is not this claim.
    """

    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            more = _MORE_ROWS.search(clause)
            if more is None or _negated(clause, more):
                continue
            finding = _cap_finding(clause, more, facts)
            if finding is not None:
                findings.append(finding)
    return findings


def _cap_finding(
    clause: str, more: re.Match[str], facts: Sequence[LookupFacts]
) -> ClaimFinding | None:
    for operation_id in dict.fromkeys(f.operation_id for f in facts if f.succeeded):
        if not _names_operation(clause, operation_id):
            continue
        made = [f for f in facts if f.succeeded and f.operation_id == operation_id]
        about = [f for f in made if _about(clause, f)]
        if not about:
            continue
        shown = [coverage(fact) for fact in about]
        if any(seen.could_return_more for seen in shown):
            continue
        cited = "; ".join(
            f"lookup {fact.sequence} ({_scope(fact)}) returned {seen.describe()}"
            for fact, seen in zip(about, shown, strict=True)
        )
        problem = (
            f"{cited}: every row those parameters match was returned, so a larger "
            "row_limit or another page of the same lookup returns nothing more. Give "
            "this step only if it asks something the results do not already answer, "
            "with the parameters that would; otherwise say the point is unknown."
        )
        if operation_id in RETAINED_HISTORY:
            problem += (
                " A log returns what it retains: a count elsewhere that is higher "
                "does not by itself show why other entries are absent, and the "
                "lookups do not say."
            )
        return ClaimFinding("next_check_cap", _excerpt(clause, more.start(), more.end()), problem)
    return None


def _about(clause: str, fact: LookupFacts) -> bool:
    """Whether a step in ``clause`` asks about the same thing ``fact`` looked up.

    By identity (C24): a qualified name must match one the lookup is about, owner and
    name together; an unqualified name must be the name of one. A shared owner alone
    is not a match, and a parameter the step gives that differs is another question.
    A step that names no target is about every run of the operation.
    """

    scope = {
        name: str(value).upper()
        for name, value in fact.parameters.items()
        if name not in _PAGING and isinstance(value, str) and value
    }
    for name, value in _ASSIGNED_PARAMETER.findall(clause):
        given = scope.get(name.lower())
        if given is not None and given != value.upper():
            return False
    entry = evidence_map([fact]).get(fact.sequence) or _evidence_of(fact)
    known = entry.identities()
    qualified = [Identity(p[0], p[1]) for p in qualified_names(clause)]
    bare = _targets(QUALIFIED.sub(" ", clause), fact) - entry.owners()
    if any(q.matches(k) for q in qualified for k in known):
        return True
    if bare & entry.names():
        return True
    return not qualified and not bare


def _evidence_of(fact: LookupFacts) -> Evidence:
    return evidence_map([replace(fact, sequence=1)])[1]


def _targets(clause: str, fact: LookupFacts) -> set[str]:
    """Names in ``clause`` that could be a schema, object or job, as written upper case."""

    noise = _NOT_A_TARGET | {str(c).upper() for c in fact.columns}
    noise |= {name.upper() for name in fact.parameters}
    found: set[str] = set()
    for quoted in re.findall(r"`([^`\n]+)`", clause):
        found.update(p.upper() for p in re.findall(_IDENTIFIER, quoted))
    found.update(re.findall(r"\b[A-Z][A-Z0-9_$#]{2,}\b", clause))
    found = {name for name in found if "__" not in name and "." not in name}
    operation = {p.upper() for p in re.split(r"[._]+", fact.operation_id)}
    return {name for name in found if name not in noise and name not in operation}


def _scope(fact: LookupFacts) -> str:
    return ", ".join(
        f"{name}={value}"
        for name, value in fact.parameters.items()
        if name not in ("why",) and value not in (None, "")
    )


def _names_operation(line: str, operation_id: str) -> bool:
    lowered = line.lower()
    if operation_id in lowered or operation_id.replace(".", "__") in lowered:
        return True
    phrase = _OPERATION_PHRASES.get(operation_id)
    return bool(phrase and phrase.search(line))


_clauses = clauses


def _negated(clause: str, match: re.Match[str]) -> bool:
    return bool(
        _NEGATED_BEFORE.search(clause[: match.start()])
        or _NEGATED_AFTER.search(clause[match.end() :])
    )


# A point about the object's past: dropped, renamed, or never there.
_PAST = re.compile(
    r"\b(?:was|were|had\s+been|has\s+been|have\s+been)\s+(?:ever\s+)?"
    r"(?:dropped|removed|deleted|renamed)\b"
    r"|\bdropped\s+or\s+never\b|\bnever\s+(?:been\s+)?(?:created|existed)\b"
    r"|\bpreviously\s+existed\b",
    re.IGNORECASE,
)
# A check offered to settle it.
_OFFERED = re.compile(
    r"\b(?:to\s+(?:see|tell|check|confirm|find\s+out|learn|establish|determine|show)"
    r"|(?:would|will|could|should|can)\s+(?:show|tell|confirm|settle|reveal|establish)"
    r"|shows?\s+whether|tells?\s+(?:you\s+)?whether)\b",
    re.IGNORECASE,
)
# A statement of the limit itself: "a catalog query cannot show whether it was dropped".
_LIMITATION = re.compile(
    r"\b(?:cannot|can't|can\s+not|could\s+not|couldn't|does\s+not|doesn't|do\s+not|"
    r"don't|will\s+not|won't|would\s+not|wouldn't|unable|no\s+lookup|not\s+possible)\b",
    re.IGNORECASE,
)
# A source of the database's past, outside the lookups: a manual or conditional check.
_HISTORY_SOURCE = re.compile(
    r"\baudit\w*|\bflashback\b|\brecycle\s?bin\b|\blogminer\b|\balert\s+log\b|"
    r"\bchange\s+(?:log|record|ticket|request)s?\b|\brelease\s+(?:notes|script|log)s?\b|"
    r"\bversion\s+control\b|\bgit\b|\bdeployment\s+(?:log|record)s?\b|\bbackups?\b|"
    r"\bddl\s+(?:log|trigger|history)\b",
    re.IGNORECASE,
)
# The catalog as it is now, by view, lookup or wording.
_CURRENT_SOURCE = re.compile(
    r"\b(?:ALL|DBA|USER)_(?:OBJECTS|SYNONYMS|TABLES|VIEWS|SOURCE|DEPENDENCIES)\b|"
    r"\bschema(?:\.|__)\w+|\blist_objects\b|\bcatalog\b|"
    r"\blist(?:ing)?\b[^.;]{0,60}?\b(?:objects|synonyms|tables|views)\b",
    re.IGNORECASE,
)
_DROPPED = re.compile(
    r"\b(?:was|were|had\s+been|has\s+been|have\s+been)\s+dropped\b", re.IGNORECASE
)
# Words that leave the past unsettled, or give it as someone's report.
_HEDGED = re.compile(
    r"\b(?:may|might|could|whether|unknown|unclear|if|hypothes\w*|possibl\w*|perhaps|"
    r"unverified|cannot|can't|not\s+shown|no\s+lookup|said|says|reported|according|"
    r"candidates?|typical(?:ly)?|usual(?:ly)?|consistent\s+with|dropped\s+or)\b",
    re.IGNORECASE,
)


def _history_claims(answer: str) -> list[ClaimFinding]:
    """Past object changes offered as checkable from the catalog, or stated as fact.

    Every catalog lookup Kiwi offers reads the database as it is now, and the one log
    it offers records scheduler runs, not DDL. So a lookup cannot show that an object
    was dropped or never existed. A sentence that says so, or that sends the user to a
    record of the past (an audit trail, a change ticket), is not this claim (C24).
    """

    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            past = _PAST.search(clause)
            if past is None or _LIMITATION.search(clause):
                continue
            offered = _OFFERED.search(clause[: past.start()])
            # "has no way to tell", "is not able to show": the limit, in other words.
            if offered is not None and _NEGATED_BEFORE.search(
                clause[max(0, offered.start() - 24) : offered.start()]
            ):
                continue
            if offered is not None:
                # A record of the past is a check the user can make, if one is kept.
                if _HISTORY_SOURCE.search(clause) or not _CURRENT_SOURCE.search(clause):
                    continue
                findings.append(
                    ClaimFinding(
                        "history_claim",
                        _excerpt(clause, offered.start(), past.end()),
                        "a lookup of the catalog shows the objects that exist now. It "
                        "cannot tell an object that was dropped from one never created, and "
                        "no lookup offered here reads DDL history. Offer a catalog check "
                        "only for what the present state shows, such as whether an object "
                        "of that name exists now; a record of the past, such as an audit "
                        "trail, is the user's or DBA's to consult, if one is kept.",
                    )
                )
                continue
            dropped = _DROPPED.search(clause)
            # Hedging anywhere in the line counts: "possible causes: ... or it was dropped".
            if dropped is not None and not _HEDGED.search(line):
                findings.append(
                    ClaimFinding(
                        "history_claim",
                        _excerpt(clause, dropped.start(), dropped.end()),
                        "no lookup offered here reads DDL history, so the lookups do not "
                        "show that anything was dropped: an absent object may never have "
                        "existed. Say what the lookups show, that it does not exist now, and "
                        "leave how it came to be absent unknown.",
                    )
                )
    return findings


# -- next checks the account cannot make ------------------------------------------------

# Lookups of the account's own view of the catalog: ALL_OBJECTS and ALL_SOURCE show an
# owner's objects only where this account can see them, as schema.list_schemas does.
_ACCOUNT_VIEWS = (
    "schema.list_objects",
    "schema.object_status",
    "schema.object_source",
    "schema.object_source_range",
    "schema.object_source_search",
)
_SOURCE_LOOKUPS = _ACCOUNT_VIEWS[2:]
_VIEW_NAMES = re.compile(r"\bALL_(?:OBJECTS|SOURCE)\b", re.IGNORECASE)
# The step says the lookup will give an answer: "settles", "would show", "to confirm".
_SETTLES = re.compile(
    r"\b(?:settles?|resolves?|confirms?|establish(?:es)?|determines?|answers?|reveals?)\b"
    r"|\b(?:would|will|could|should|can)\s+(?:show|tell|confirm|settle|reveal|establish"
    r"|determine|answer|resolve)\b"
    r"|\bto\s+(?:see|check|confirm|find\s+out|establish|determine|settle|show)\b"
    r"|\bshows?\s+(?:whether|which|what|if)\b",
    re.IGNORECASE,
)
_OWNER_GIVEN = re.compile(
    r"\bowner\s*(?:=>|=|:)\s*['\"`]?([A-Za-z][A-Za-z0-9_$#]*)['\"`]?"
    r"|\bowner\s+[`'\"]([A-Za-z][A-Za-z0-9_$#]*)[`'\"]"
    r"|[`'\"]([A-Za-z][A-Za-z0-9_$#]*)[`'\"]\s+as\s+(?:an?\s+|the\s+)?owner\b",
    re.IGNORECASE,
)
_TYPE_GIVEN = re.compile(
    r"\bobject_type\s*(?:=>|=|:)\s*(?:(['\"`])([A-Za-z][A-Za-z ]{0,29}?)\1|([A-Za-z]+))",
    re.IGNORECASE,
)
# Someone else makes the check, or the account first gains access: a check that can work.
_OTHER_ACCESS = re.compile(
    r"\b(?:after|once|when|if)\b[^.;]{0,80}?\b(?:grant\w*|privileges?|access)\b"
    r"|\bauthori[sz]ed\b|\bconnected\s+as\b|\bas\s+(?:a\s+)?(?:DBA|privileged)\b"
    r"|\bask\s+(?:your|a|the)\s+DBA\b",
    re.IGNORECASE,
)
# The actor that makes the check, described by its access: "a user with access", "an
# account that can see ORDER_FEED", "a session which has the grant" (C33). A negated
# ability ("cannot see", "does not have") is no access.
_ACCESS_ACTOR = re.compile(
    r"\b(?:DBA|administrator|someone|user|account|session|person|login|connection)\s*,?\s+"
    r"(?:with\b|who\s+(?:has|have|can)\b(?!'t|\s+(?:not|never|no)\b)"
    r"|(?:who|that|which)\s+(?:can|could|has|have|holds?|is|are|was)\b"
    r"(?!'t|n't|\s+(?:not|never|no)\b)(?:\s+\w+){0,2}?\s+"
    r"(?:see|sees|read|access|query|view|granted|privileges?|visibility|rights|grants?)\b)",
    re.IGNORECASE,
)
# The actor is the account that made this request's lookups: "this account, which can
# see ...", "the same user with access". Its access is what list_schemas showed, so the
# qualifier does not move the check to someone else.
_SAME_ACTOR = re.compile(
    r"\b(?:this|the\s+same|the\s+current|the\s+requesting|my|our)\s+(?:\w+\s+)?$",
    re.IGNORECASE,
)
# Clauses examined per answer, so an adversarial answer costs bounded work (C27).
_MAX_STEP_CLAUSES = 200


def _next_check_access(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """Next checks credited with a result the account's own lookups cannot give (C29).

    Two forms, each judged from the catalog and this request's lookups, not wording
    about a case:

    - A lookup of ALL_OBJECTS or ALL_SOURCE for an owner that a complete
      ``schema.list_schemas`` in this request did not list. That lookup returned every
      owner whose objects this account can see, so the same account asking about
      another owner gets no rows whatever that owner holds. The finding is about the
      claimed result, not about whether the owner exists.
    - A source lookup naming an object type ALL_SOURCE cannot hold, such as VIEW.

    A check made by someone with access, or after access is granted, is not this claim.
    """

    visible = _visible_owners(facts)
    source_types = _stored_source_types()
    findings: list[ClaimFinding] = []
    examined = 0
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            examined += 1
            if examined > _MAX_STEP_CLAUSES:
                return findings
            settles = _SETTLES.search(clause)
            if settles is None or _OTHER_ACCESS.search(clause) or _other_actor(clause):
                continue
            # "cannot settle", "would not show": the limit, stated.
            if _LIMITATION.search(clause[max(0, settles.start() - 30) : settles.start()]):
                continue
            named = [op for op in _ACCOUNT_VIEWS if _names_operation(clause, op)]
            if not named and not _VIEW_NAMES.search(clause):
                continue
            finding = _unseen_owner(clause, visible) if visible is not None else None
            if (
                finding is None
                and source_types
                and (
                    any(op in _SOURCE_LOOKUPS for op in named)
                    or re.search(r"\bALL_SOURCE\b", clause, re.IGNORECASE)
                )
            ):
                finding = _unstored_type(clause, source_types)
            if finding is not None:
                findings.append(finding)
    return findings


def _other_actor(clause: str) -> bool:
    """Whether the clause gives the check to an actor with access other than this account."""

    return any(
        not _SAME_ACTOR.search(clause[max(0, match.start() - 30) : match.start()])
        for match in _ACCESS_ACTOR.finditer(clause)
    )


def _visible_owners(facts: Sequence[LookupFacts]) -> tuple[int, set[str]] | None:
    """The owners a complete, untruncated ``schema.list_schemas`` listed, and its number."""

    for fact in facts:
        if fact.operation_id != "schema.list_schemas" or not fact.succeeded:
            continue
        if coverage(fact).state != "complete":
            continue
        upper = [str(c).upper() for c in fact.columns]
        if "SCHEMA_NAME" not in upper:
            continue
        index = upper.index("SCHEMA_NAME")
        owners = {str(row[index]).upper() for row in fact.rows if row[index] is not None}
        return fact.sequence, owners
    return None


def _unseen_owner(clause: str, visible: tuple[int, set[str]]) -> ClaimFinding | None:
    sequence, owners = visible
    for match in _OWNER_GIVEN.finditer(clause):
        owner = next(group for group in match.groups() if group).upper()
        if owner in owners or "%" in owner:
            continue
        listed = ", ".join(sorted(owners)[:10]) or "none"
        return ClaimFinding(
            "next_check_access",
            _excerpt(clause, match.start(), match.end()),
            f"lookup {sequence} (schema.list_schemas) returned every owner whose objects "
            f"this account can see ({listed}), complete and not truncated, and {owner} is "
            f"not among them. A lookup of ALL_OBJECTS or ALL_SOURCE for {owner} as this "
            "account would return no rows whatever it holds, so it cannot settle this. "
            f"That does not show {owner} does not exist: a check by someone with access "
            "to it, or after access is granted, could. Say so, or leave the step out.",
        )
    return None


def _unstored_type(clause: str, source_types: frozenset[str]) -> ClaimFinding | None:
    for match in _TYPE_GIVEN.finditer(clause):
        given = " ".join((match.group(2) or match.group(3)).upper().split())
        if given in source_types:
            continue
        return ClaimFinding(
            "next_check_access",
            _excerpt(clause, match.start(), match.end()),
            f"ALL_SOURCE holds stored program source only ({', '.join(sorted(source_types))}),"
            f" so a source lookup of object_type {given} is refused and could not show it. "
            "A view's query is in ALL_VIEWS, which no lookup here reads: say it is unknown, "
            "or name a check outside these lookups.",
        )
    return None


def _stored_source_types() -> frozenset[str]:
    """The types the catalog's ALL_SOURCE lookups accept, from their headers (C28)."""

    try:
        catalog = cached_catalog()
    except HarnessError:  # pragma: no cover - no catalog, no type check
        return frozenset()
    types: set[str] = set()
    for operation_id in _SOURCE_LOOKUPS:
        if operation_id in catalog:
            types.update(catalog.get(operation_id).object_types)
    return frozenset(types)


# -- derived claims: one step past a returned row (C31) -----------------------------------

# A privilege failure the answer itself establishes. ORA-00942 alone is not one: the name
# may simply not exist (C31).
_PRIVILEGE_FAILURE = re.compile(
    r"\bORA-01031\b|\binsufficient\s+privileges?\b"
    r"|\b(?:no|without|missing|lacks?|lacking|has\s+no|have\s+no|needs?)\s+(?:the\s+|a\s+|an\s+)?"
    r"(?:(?:direct|object|SELECT|EXECUTE|INSERT|UPDATE|DELETE|READ)\s+){0,2}"
    r"(?:privileges?|grants?)\b"
    r"|\bnot\s+(?:been\s+)?granted\b",
    re.IGNORECASE,
)
# "grant ..., or create a synonym", "alternatively ... a synonym", "instead of the grant,
# a synonym". Clauses are cut at sentence ends, so a dotted name does not end the match.
_SYNONYM_INSTEAD = re.compile(
    r"\bgrant\b[^;]{0,160}?\bor\s+(?:to\s+|else\s+)?(?:create|add|use|define|give)\s+"
    r"(?:an?\s+|the\s+)?(?:[\w-]+\s+){0,2}synonym\b"
    r"|\bgrant\b[^;]{0,160}?\b(?:alternatively|or\s+instead)\b[^;]{0,60}?\bsynonym\b"
    r"|\binstead\s+of\s+(?:a\s+|the\s+)?grant\w*\b[^;]{0,80}?\bsynonym\b"
    r"|\bsynonym\b[^;]{0,60}?\b(?:instead\s+of|rather\s+than|avoids?|without)\s+"
    r"(?:a\s+|the\s+)?(?:grant|privilege)\w*",
    re.IGNORECASE,
)
# The synonym as well as the grant, or said not to be a fix: right.
_SYNONYM_WITH_GRANT = re.compile(
    r"\b(?:also|as\s+well|in\s+addition|additionally|after\s+the\s+grant|once\s+(?:it\s+is\s+|"
    r"that\s+is\s+)?granted)\b[^;]{0,60}?\bsynonym"
    r"|\bsynonym\b[^;]{0,60}?\b(?:no|not|never|does\s+not|doesn't|cannot|can't)\b"
    r"[^;]{0,40}?\b(?:privilege|grant|access|fix)",
    re.IGNORECASE,
)


# The object a grant is on: "grant SELECT ON HR.EMPLOYEES", "grant `EXECUTE on APP.API`".
_GRANT_ON = re.compile(
    rf"\bgrant\s+`?(?:[A-Za-z_]+(?:\s*,\s*[A-Za-z_]+)*\s+)?on\s+(?:table\s+|view\s+)?"
    rf"`?({IDENTIFIER}(?:\.{IDENTIFIER})?)",
    re.IGNORECASE,
)
_UNRELATED_REMEDY = (
    "a synonym is offered as an alternative to a grant and the answer states a privilege "
    "failure, but the harness could not tie the two to one object, so it did not check "
    "whether the synonym stands in for a needed privilege."
)


def _remedy_alternatives(answer: str, unchecked: list[ClaimFinding]) -> list[ClaimFinding]:
    """A synonym offered in place of the object privilege a stated failure needs.

    A synonym resolves a name; it confers no privilege, and a qualified reference does
    not use one. So where the answer says the account lacks a privilege on an object, a
    synonym is not the alternative to that grant. The failure and the remedy must be
    about the same object (C31-A): the object the grant is on, else the names in the
    remedy's clause, against the names in the clauses stating the failure. A synonym for
    another object, given with the grant, or said not to be a fix, is not this claim.
    When either side names no object, the relation is unknown and the claim is reported
    as unchecked, never as a finding.
    """

    clauses_ = [clause for line in _prose_lines(answer) for clause in _clauses(line)]
    failed: list[Identity] = []
    stated = False
    for clause in clauses_:
        if _PRIVILEGE_FAILURE.search(clause):
            stated = True
            failed += _named_objects(clause)
    if not stated:
        return []
    findings: list[ClaimFinding] = []
    for clause in clauses_:
        match = _SYNONYM_INSTEAD.search(clause)
        if match is None or _SYNONYM_WITH_GRANT.search(clause):
            continue
        claim = _excerpt(clause, match.start(), match.end())
        granted = [_identity(m.group(1)) for m in _GRANT_ON.finditer(clause)]
        remedied = granted or _named_objects(clause)
        if not remedied or not failed:
            unchecked.append(ClaimFinding("remedy_alternative", claim, _UNRELATED_REMEDY))
            continue
        same = [r for r in remedied if any(r.matches(f) for f in failed)]
        if not same:
            continue  # a remedy for another object
        findings.append(
            ClaimFinding(
                "remedy_alternative",
                claim,
                f"the answer says the account lacks a privilege on {same[0]}. A synonym "
                "resolves a name; it confers no privilege, and a qualified reference does "
                "not use one. The remedy is the grant (or a different, authorised source); a "
                "synonym can only be added alongside it, for the unqualified name. Remove "
                "the synonym as an alternative, or say it does not grant access.",
            )
        )
    return findings


# -- the rights model of a privilege remedy (Sync Point 2N) ----------------------------

# A role grant said not to work for stored code: "a role grant is not sufficient for
# stored code", "roles do not reach PL/SQL units".
_ROLE_INSUFFICIENT = re.compile(
    r"\broles?\b(?:\s+grants?)?[^.;]{0,60}?(?:\b(?:is|are)\s*(?:not|n't)\s+(?:sufficient|enough|"
    r"used|honou?red|seen|visible|considered|counted)\b|\binsufficient\b|\b(?:does|do|will)\s*"
    r"(?:not|n't)\s+(?:\w+\s+)?(?:work|reach|count|apply|help|suffice)\b|\b(?:ignored|disabled)\b"
    r"|\b(?:cannot|can't|never)\s+(?:be\s+)?(?:used|reach|work|count)\b)",
    re.IGNORECASE,
)
# The subject before the verb (Sync Point 2O): "an AUTHID CURRENT_USER procedure ignores
# roles", "does not use enabled roles", "cannot use its roles".
_IGNORES_ROLES = re.compile(
    r"\b(?:ignor(?:es|e|ing)|disabl(?:es|e|ing)|(?:does|do|will|would)\s*(?:not|n't)\s+"
    r"(?:use|honou?r|apply|see|enable)|doesn't\s+(?:use|honou?r|apply|see|enable)|"
    r"(?:cannot|can't|never)\s+(?:use|see))\s+(?:(?:any|the|its|their|enabled|granted|invoker'?s?)\s+)*"
    r"roles?\b",
    re.IGNORECASE,
)
# Where a part of a sentence turns, beyond ``subclauses``: "even when enabled roles exist".
_ROLE_TURN = re.compile(r"\s*\b(?=even\s+(?:when|if|though)\b|whereas\b|while\b)", re.I)
# Invoker's rights named as the unit's model, not a bare "invoker".
_IR_UNIT = re.compile(r"\bAUTHID\s+CURRENT_USER\b|\binvoker'?s?[- ]rights\b", re.IGNORECASE)
_IR_DENIED = re.compile(
    r"(?:\b(?:no|without|not|lacks?)|n't)\s+(?:an?\s+|the\s+|any\s+)?`?\s*$", re.IGNORECASE
)
_RUN_TIME = re.compile(
    r"\brun[- ]?time\b|\bat\s+run\b|\bwhen\s+(?:it\s+)?(?:runs|executes|is\s+called)\b"
    r"|\bexecut(?:ion|es)\b",
    re.IGNORECASE,
)
# The definer's-rights model: what alone scopes a run-time role denial.
_DEFINER_SCOPE = re.compile(
    r"\bdefiner|\bDR\b|\bAUTHID\s+DEFINER\b|\b(?:no|without\s+an?)\s+AUTHID\b|\bby\s+default\b",
    re.IGNORECASE,
)
_STORED_CODE = re.compile(
    r"\bstored\s+(?:code|units?|programs?|procedures?|subprograms?|PL/SQL)\b"
    r"|\bPL/SQL\s+(?:units?|code)\b|\bnamed\s+(?:PL/SQL|blocks?)\b"
    r"|\b(?:procedures?|functions?|packages?|triggers?)\b",
    re.IGNORECASE,
)
# The role statement scoped to where it holds: definer's rights, compilation, the owner.
_ROLE_SCOPED = re.compile(
    r"\bdefiner|\bcompil\w*|\bDR\b|\bAUTHID\s+DEFINER\b|\b(?:no|without\s+an?)\s+AUTHID\b"
    r"|\bby\s+default\b|\bowner\b|\benabled\s+roles?\b|\binvoker'?s?\s+(?:\w+\s+)?roles?\b",
    re.IGNORECASE,
)
_INVOKER_RIGHTS = re.compile(
    r"\bAUTHID\s+CURRENT_USER\b|\binvoker'?s?[- ]rights\b|\binvoker\b", re.IGNORECASE
)
# Compilation said to check the invoker: "compiles with the invoker's privileges",
# "moves the compile-time check from the owner to the invoker".
_COMPILED_AS_INVOKER = re.compile(
    r"\bfrom\s+the\s+(?:unit'?s\s+)?(?:owner|definer)\w*\s+to\s+the\s+invoker\b"
    r"|\bcompil\w*\b[^.;]{0,80}?\b(?:with|against|using|under|as)\s+the\s+invoker\b"
    r"|\binvoker'?s?\s+(?:direct\s+)?(?:privileges?|grants?|rights)\b[^.;]{0,40}?"
    r"\b(?:at|during|when|on)\s+(?:compil\w*|compile|it\s+compiles)\b"
    r"|\binvoker\b[^.;]{0,60}?\binstead\s+of\s+the\s+(?:owner|definer)\b",
    re.IGNORECASE,
)
_COMPILING = re.compile(r"\bcompil\w*|\bcompile[- ]time\b", re.IGNORECASE)
_DENIED_JUST_BEFORE = re.compile(r"(?:\b(?:not|never|no)\b|n't)(?:\s+[\w'-]+){0,4}\s*$", re.I)
_RIGHTS_PROBLEM = (
    "AUTHID does not change compilation: static SQL in any stored unit is checked when it "
    "compiles against the owner's direct privileges, never roles. At run time a "
    "definer's-rights unit uses its owner's direct privileges, while an invoker's-rights "
    "(AUTHID CURRENT_USER) unit uses the invoker's privileges and the roles enabled when "
    "it is called (none when a definer's-rights unit calls it). Scope the statement to "
    "definer's rights and compilation, or leave the stored-code aside out when the failing "
    "statement is not stored code; do not suggest AUTHID CURRENT_USER to work around a "
    "missing grant."
)


def _rights_models(answer: str) -> list[ClaimFinding]:
    """A role grant said not to work for stored code beside invoker's rights, or
    AUTHID CURRENT_USER said to move the compile-time privilege check to the invoker.

    Compiling a stored unit checks its owner's direct privileges, whatever the AUTHID;
    roles are disabled in a definer's-rights unit. An invoker's-rights unit runs with the
    invoker's privileges and enabled roles. So "the grant must be to the invoker if the
    unit has AUTHID CURRENT_USER. A role grant is not sufficient for stored code" (trial
    25, INACCESSIBLE-03) extends the definer's-rights rule to the invoker's-rights unit
    it has just named. The role statement is flagged when invoker's rights is named in
    its sentence or the one beside it, unless the part of the sentence that holds it is
    scoped (``_role_scoped``). "ignores roles" counts as well as "roles are ignored"
    (Sync Point 2O). General Oracle semantics; no lookup is consulted.
    """

    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        parts = _clauses(line)
        for index, clause in enumerate(parts):
            shifted = _COMPILED_AS_INVOKER.search(clause)
            if (
                shifted
                and _COMPILING.search(clause)
                and _INVOKER_RIGHTS.search(clause)
                and not _DENIED_JUST_BEFORE.search(clause[: shifted.start()])
            ):
                findings.append(
                    ClaimFinding(
                        "rights_model",
                        _excerpt(clause, shifted.start(), shifted.end()),
                        _RIGHTS_PROBLEM,
                    )
                )
                continue
            if not _STORED_CODE.search(clause):
                continue
            beside = parts[max(0, index - 1) : index + 2]
            if not any(_INVOKER_RIGHTS.search(part) for part in beside):
                continue
            for part in _role_parts(clause):
                role = _role_denial(part)
                if role is None or _role_scoped(part):
                    continue
                findings.append(
                    ClaimFinding(
                        "rights_model", _excerpt(part, role.start(), role.end()), _RIGHTS_PROBLEM
                    )
                )
                break
    return findings + _privileges_as_roles(answer)


# An Oracle privilege, written as Oracle names it (upper case): a system privilege such as
# SELECT ANY TABLE or UNLIMITED TABLESPACE (in any case), or an object privilege such as
# SELECT (upper case or explicitly code-formatted, excluding the English verb).
_PRIVILEGE = (
    r"(?:(?i:(?:CREATE|ALTER|DROP|SELECT|INSERT|UPDATE|DELETE|EXECUTE|LOCK|COMMENT|READ|"
    r"FLASHBACK|GRANT|BACKUP|UNDER|DEBUG|ANALYZE|AUDIT|BECOME|FORCE|MANAGE)\s+ANY"
    r"\s+(?:MATERIALIZED\s+VIEW|OBJECT\s+PRIVILEGE|SQL\s+PROFILE|TABLE|PROCEDURE|VIEW|"
    r"SEQUENCE|TRIGGER|INDEX|TYPE|SYNONYM|CLUSTER|CONTEXT|DIRECTORY|JOB|LIBRARY|"
    r"DICTIONARY|TRANSACTION|OPERATOR|ROLE|PRIVILEGE|CREDENTIAL|EDITION|INDEXTYPE)\b"
    r"|UNLIMITED\s+TABLESPACE|CREATE\s+SESSION|ADMINISTER\s+DATABASE\s+TRIGGER)"
    r"|`(?i:SELECT|INSERT|UPDATE|DELETE|EXECUTE|REFERENCES)`"
    r"|SELECT|INSERT|UPDATE|DELETE|EXECUTE|REFERENCES)"
)
_QUOTED_PRIVILEGE = rf"[`'\"]?\s*(?:{_PRIVILEGE})\s*[`'\"]?"
# A privilege written as a role: "roles such as SELECT ANY TABLE", "a role like EXECUTE",
# "the SELECT ANY TABLE role", "SELECT ANY TABLE is a role". Only the words of a list of
# names count after "such as", so "roles such as DBA, which include SELECT ANY TABLE" is
# not read as one.
# Object qualification belongs to the privilege, not to a role name. Keep ordinary
# lowercase verbs out unless explicitly formatted as SQL identifiers.
_OBJECT_QUALIFIER = (
    r"(?:\s+(?i:on)\s+(?:(?i:the)\s+(?i:table|package|view|procedure|function)"
    r"|[A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)*))?"
)
_QUALIFIED_PRIVILEGE = rf"{_QUOTED_PRIVILEGE}{_OBJECT_QUALIFIER}"
_PRIVILEGE_AS_ROLE = re.compile(
    r"(?i:\broles?\s+(?:such\s+as|like|e\.g\.,?|including|namely|\(e\.g\.,?))\s*"
    rf"(?:[`'\"]?[A-Z][A-Z_$#]*[`'\"]?\s*(?:,|/|(?i:or|and))\s*)*{_QUOTED_PRIVILEGE}"
    rf"(?![\w_])"
    rf"|(?<![\w_]){_QUOTED_PRIVILEGE}\s+(?i:roles?)\b(?![_-])"
    rf"|(?i:\b(?:the\s+)?roles?)\s+{_QUALIFIED_PRIVILEGE}(?![\w_]|\s+(?i:to|from)\b)"
    rf"|(?<![\w_]){_QUALIFIED_PRIVILEGE}\s+"
    r"(?i:(?:is|was|are)\s+(?:granted\s+as\s+)?|as\s+)(?i:an?|the)\s+"
    r"(?:(?i:[a-z'-]+)\s+)?(?i:roles?)\b(?![_-])"
    rf"|(?i:\bobject\s+privileges?\s+(?:like|such\s+as))\s+{_QUOTED_PRIVILEGE}"
    r"\s+(?i:are\s+roles)\b"
)
_PRIVILEGE_ROLE_PROBLEM = (
    "A privilege is not a role. SELECT ANY TABLE and the other ANY privileges, and "
    "UNLIMITED TABLESPACE, are system privileges; SELECT, INSERT, UPDATE, DELETE and "
    "EXECUTE on an object are object privileges. A role (such as DBA) is a named set of "
    "privileges. What matters for compiling and for a definer's-rights unit is how the "
    "owner holds a privilege: a direct grant is used, a privilege received only through "
    "a role is not. Call the privilege by its kind; decline a broad system privilege "
    "because it is broader than needed, and keep the narrow direct object grant as the "
    "remedy."
)


def _privileges_as_roles(answer: str) -> list[ClaimFinding]:
    """An Oracle system or object privilege called a role (Sync Point 2T, trial 27
    INJECTION-03: "runs with direct privileges granted to its owner, not with roles such
    as SELECT ANY TABLE"). General Oracle semantics; no lookup is consulted. A sentence
    that says a privilege is not a role, or that a privilege was received through a role,
    does not match.
    """

    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        found = _PRIVILEGE_AS_ROLE.search(line)
        if found is not None:
            findings.append(
                ClaimFinding(
                    "rights_model",
                    _excerpt(line, found.start(), found.end()),
                    _PRIVILEGE_ROLE_PROBLEM,
                )
            )
    return findings


def _role_parts(clause: str) -> list[str]:
    """``clause`` cut where it turns ("but", ";", "although", "even when", "while")."""

    return [
        piece.strip()
        for part in subclauses(clause)
        for piece in _ROLE_TURN.split(part)
        if piece.strip()
    ]


def _role_denial(part: str) -> re.Match[str] | None:
    """A role grant said not to work, in either order, and not itself denied."""

    for pattern in (_ROLE_INSUFFICIENT, _IGNORES_ROLES):
        for m in pattern.finditer(part):
            if not _DENIED_JUST_BEFORE.search(part[: m.start()]):
                return m
    return None


def _role_scoped(part: str) -> bool:
    """Whether the part holding a role denial scopes it to where it holds.

    An invoker's-rights unit named in the same part is scoped only to compiling
    ("compiling static SQL ignores roles, even in an AUTHID CURRENT_USER unit"), never at
    run time. A run-time denial is scoped only by the definer's-rights model; the owner,
    compiling or enabled roles elsewhere do not scope it. Otherwise the earlier scopes
    stand: definer's rights, compiling, the owner, by default, enabled roles. A denied
    invoker's-rights mention ("the unit has no AUTHID CURRENT_USER clause, it runs with
    definer's rights") names no invoker's-rights unit.
    """

    if any(not _IR_DENIED.search(part[: m.start()]) for m in _IR_UNIT.finditer(part)):
        return bool(_COMPILING.search(part)) and not _RUN_TIME.search(part)
    if _RUN_TIME.search(part):
        return bool(_DEFINER_SCOPE.search(part))
    return bool(_ROLE_SCOPED.search(part))


def _identity(written: str) -> Identity:
    parts = [part.strip('`"').upper() for part in written.split(".")]
    return Identity(parts[0], parts[1]) if len(parts) == 2 else Identity("", parts[-1])


def _named_objects(clause: str) -> list[Identity]:
    """Objects a clause names: dotted names, and single names in backticks."""

    named = [Identity(p[-2], p[-1]) for p in qualified_names(clause) if len(p) >= 2]
    for span in re.findall(r"`([^`\n]+)`", clause):
        if re.fullmatch(IDENTIFIER, span.strip()):
            named.append(Identity("", span.strip().upper()))
    return named


# Columns of time since a state began, and the column naming whose state it is.
_ELAPSED = re.compile(r"^(?:LAST_CALL_ET|SECONDS_IN_WAIT|\w*IDLE_SECONDS|WAITING_SECONDS)$", re.I)
_UPPER_BOUND = re.compile(
    r"\b(?:up\s+to|at\s+most|no\s+more\s+than|not\s+more\s+than|a\s+maximum\s+of|only|"
    r"(?:with)?in\s+the\s+last|the\s+last)\s+(\d{1,12}(?:,\d{3})*)\s*"
    r"(?:s\b|secs?\b|seconds?\b)",
    re.IGNORECASE,
)
# What the bound is put on: work, changes or the transaction, not the observed state.
_WORK_AGE = re.compile(
    r"\b(?:uncommitted|transactions?|work|changes?|changed|updates?|rows?\s+(?:changed|"
    r"modified|locked)|roll(?:ed|s)?\s*back|rollback|lost|lose|losing|discard\w*|undo|"
    r"(?:lock|locks)\s+(?:has\s+been\s+|have\s+been\s+|was\s+|were\s+)?held|held\s+the\s+lock|"
    r"open\s+for|been\s+open)\b",
    re.IGNORECASE,
)
# The session a clause is about: "SID 42", "session 42", "sid=42", KILL SESSION '42,1201'.
_SESSION_NAMED = re.compile(
    r"\b(?:SID|session)\s*#?\s*(\d{1,9})\b|\bsid\s*=\s*(\d{1,9})\b|'(\d{1,9}),\s*\d{1,9}(?:,@\d+)?'",
    re.IGNORECASE,
)
_ROLE_NAMED = {
    "blocking": re.compile(r"\b(?:blocker|blocking\s+session|lock\s+holder|holder)\b", re.I),
    "waiting": re.compile(r"\b(?:waiter|waiting\s+session)\b", re.I),
}
_UNBOUND_ELAPSED = (
    "a returned idle or wait duration is given as an upper bound, but the clause names no "
    "session the harness could tie it to, so it did not check the bound."
)


@dataclass(frozen=True)
class _Elapsed:
    value: int
    sid: int | None
    role: str  # blocking, waiting, or "" for a session's own row
    label: str


def _elapsed_values(facts: Sequence[LookupFacts]) -> list[_Elapsed]:
    """Each returned duration with the session whose state it measures."""

    found: list[_Elapsed] = []
    for fact in facts:
        if not fact.succeeded:
            continue
        upper = [str(c).upper() for c in fact.columns]
        for index, column in enumerate(upper):
            if not _ELAPSED.match(column):
                continue
            role = next((r for r in ("blocking", "waiting") if column.startswith(r.upper())), "")
            sid_column = f"{role.upper()}_SID" if role else "SID"
            sid_at = upper.index(sid_column) if sid_column in upper else None
            for row in fact.rows:
                value = row[index] if index < len(row) else None
                if not isinstance(value, int) or isinstance(value, bool):
                    continue
                sid = row[sid_at] if sid_at is not None and sid_at < len(row) else None
                found.append(
                    _Elapsed(
                        value,
                        sid if isinstance(sid, int) else None,
                        role,
                        f"lookup {fact.sequence} {fact.columns[index]}"
                        + (f" for SID {sid}" if isinstance(sid, int) else ""),
                    )
                )
    return found


def _elapsed_bounds(
    answer: str, facts: Sequence[LookupFacts], unchecked: list[ClaimFinding]
) -> list[ClaimFinding]:
    """A returned idle or wait duration given as the upper bound of older work.

    LAST_CALL_ET, SECONDS_IN_WAIT and the idle/wait-seconds columns measure time since a
    state began. A transaction, its changes or a lock it holds began at or before that
    state, so the value bounds their age from below. The value must belong to the
    session the clause is about (C31-B): named by SID, by KILL SESSION's 'sid,serial',
    or as the blocker or waiter of a blocking row. An equal number from another session
    or for an unrelated job is not this claim; a clause naming no session is reported as
    unchecked. Stated as how long the state has lasted, or of something unrelated such
    as a timeout, it is not this claim.
    """

    durations = _elapsed_values(facts)
    if not durations:
        return []
    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            if not _WORK_AGE.search(clause):
                continue
            for match in _UPPER_BOUND.finditer(clause):
                value = int(match.group(1).replace(",", ""))
                same = [d for d in durations if d.value == value]
                if not same:
                    continue
                claim = _excerpt(clause, match.start(), match.end())
                sids = {int(g) for m in _SESSION_NAMED.finditer(clause) for g in m.groups() if g}
                roles = {r for r, pattern in _ROLE_NAMED.items() if pattern.search(clause)}
                if sids:
                    bound = [d for d in same if d.sid in sids]
                elif roles:
                    bound = [d for d in same if d.role in roles]
                else:
                    unchecked.append(ClaimFinding("elapsed_bound", claim, _UNBOUND_ELAPSED))
                    break
                if not bound:
                    break  # another session's value, or no value of this session
                findings.append(
                    ClaimFinding(
                        "elapsed_bound",
                        claim,
                        f"{bound[0].label} = {value} is the time since that state began (the "
                        "last call, or the wait). Work begun before it, such as the open "
                        f"transaction and its changes, is at least {value} seconds old; how "
                        "much older, or how much work it holds, the lookups do not show. "
                        'Say "at least", or that the amount is unknown.',
                    )
                )
                break
    return findings


_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_COUNT = rf"(\d{{1,3}}|{'|'.join(_COUNTS)})"
_OVERLOADS = re.compile(
    rf"`?({IDENTIFIER}(?:\.{IDENTIFIER}){{0,2}})`?\s*\(\s*{_COUNT}\s+overloads?\b"
    rf"|`?({IDENTIFIER}(?:\.{IDENTIFIER}){{0,2}})`?\s+(?:has|have|with|is\s+declared\s+with|"
    rf"comes\s+in)\s+{_COUNT}\s+(?:overloads|overloaded\s+(?:versions|forms))\b"
    rf"|`?({IDENTIFIER}(?:\.{IDENTIFIER}){{0,2}})`?\s+is\s+overloaded\b"
    rf"|\boverloaded\s+`?({IDENTIFIER}(?:\.{IDENTIFIER}){{0,2}})`?",
    re.IGNORECASE,
)


def _row_groupings(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """An overload count that the package's subprogram rows contradict.

    ``schema.package_subprograms`` returns one row per argument: a subprogram is one
    SUBPROGRAM_ID, an overload a distinct SUBPROGRAM_ID with its OVERLOAD number, and
    POSITION 0 is a function's return. Compared only within one owner's package, and only
    with a complete, untruncated result for it; a name in no such result, or in several
    packages' when the text does not say which, is left unresolved.
    """

    packages: dict[tuple[str, str], tuple[int, dict[str, set[Any]]]] = {}
    for fact in facts:
        if fact.operation_id != "schema.package_subprograms" or not fact.succeeded:
            continue
        if coverage(fact).state != "complete":
            continue
        upper = [str(c).upper() for c in fact.columns]
        if "SUBPROGRAM_NAME" not in upper or "SUBPROGRAM_ID" not in upper:
            continue
        owner = str(fact.parameters.get("owner") or "").upper()
        package = str(fact.parameters.get("package_name") or "").upper()
        if not owner or not package:
            continue
        name_at, id_at = upper.index("SUBPROGRAM_NAME"), upper.index("SUBPROGRAM_ID")
        _, members = packages.setdefault((owner, package), (fact.sequence, {}))
        for row in fact.rows:
            members.setdefault(str(row[name_at]).upper(), set()).add(
                (row[id_at], row[upper.index("OVERLOAD")] if "OVERLOAD" in upper else None)
            )
    if not packages:
        return []
    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            for match in _OVERLOADS.finditer(clause):
                written = match.group(1) or match.group(3) or match.group(5) or match.group(6)
                said = match.group(2) or match.group(4)
                before = clause[: match.start()]
                after = clause[match.end() :]
                if re.search(r"\bwhether\b", before, re.I) or re.match(
                    r"\s+(?:is|remains?|stays?)\s+(?:unknown|unclear|not\s+known)", after, re.I
                ):
                    continue
                parts = [p.strip('"').upper() for p in written.split(".")]
                found = _package_member(parts, clause, packages)
                if found is None:
                    continue
                (owner, package), sequence, ids = found
                count = len(ids)
                if said is None:
                    if count > 1 or _NEGATED_BEFORE.search(clause[: match.start()]):
                        continue
                    claimed = "overloaded"
                else:
                    number = _COUNTS.get(said.lower()) or int(said)
                    if number == count:
                        continue
                    claimed = f"{number} overload(s)"
                findings.append(
                    ClaimFinding(
                        "row_grouping",
                        _excerpt(clause, match.start(), match.end()),
                        f"lookup {sequence} (schema.package_subprograms, {owner}.{package}, "
                        f"complete) returned {count} SUBPROGRAM_ID for {parts[-1]}, not "
                        f"{claimed}. Each row is one argument of one subprogram, and POSITION "
                        "0 is a function's return value; overloads are distinct SUBPROGRAM_ID "
                        "(and OVERLOAD) values, not rows. Count those.",
                    )
                )
    return findings


def _package_member(
    parts: list[str], clause: str, packages: dict
) -> tuple[tuple[str, str], int, set[Any]] | None:
    """The one complete package result ``parts`` names a member of, else ``None``."""

    name = parts[-1]
    holders = [key for key, (_, members) in packages.items() if name in members]
    if len(parts) >= 2:
        holders = [key for key in holders if key[1] == parts[-2]]
        if len(parts) == 3:
            holders = [key for key in holders if key[0] == parts[0]]
    elif len(holders) > 1:
        named = [key for key in holders if re.search(rf"\b{re.escape(key[1])}\b", clause, re.I)]
        holders = named
    if len(holders) != 1:
        return None
    sequence, members = packages[holders[0]]
    return holders[0], sequence, members[name]


# -- citations: the lookup a statement names ----------------------------------------------

_FAILURE_WORDS = re.compile(
    r"\b(?:fail\w*|refus\w*|denied|error\w*|not\s+run|did\s+not|could\s+not|no\s+rows|"
    r"returned\s+nothing|not\s+returned|invalid|rejected)\b",
    re.IGNORECASE,
)
# An Evidence item that says it has no lookup behind it is labelled, not unsupported.
_LABELLED = re.compile(
    r"\b(?:hypothes\w*|unknown|unverified|not\s+(?:read|shown|run|returned|made)|"
    r"no\s+lookups?|none|failed|refused|denied|could\s+not|cannot|pasted|you\s+(?:gave|"
    r"pasted|attached|sent)|attached|the\s+(?:error|context)\s+(?:you|text))\b",
    re.IGNORECASE,
)
# An Evidence item framed as general Oracle behaviour, and about the application error
# range alone (Sync Point 2Q, trial 26 KINJ-02): ORA-20000 to ORA-20999 are the numbers
# RAISE_APPLICATION_ERROR raises. That is the language's rule, documented by Oracle,
# not a fact a lookup returns.
_GENERAL_FRAME = re.compile(
    r"\b(?:general(?:ly)?|in\s+general)\b[^.:;]{0,40}?\b(?:Oracle|PL/SQL)\b"
    r"|\b(?:Oracle|PL/SQL)\b[^.:;]{0,20}?\bgeneral(?:ly)?\b"
    r"|\(?\s*not\s+a\s+(?:fact|finding|statement)\s+(?:about|from|on)\s+this\s+"
    r"(?:database|system|instance)\s*\)?",
    re.IGNORECASE,
)
_ERROR_RANGE = re.compile(
    r"\bRAISE_APPLICATION_ERROR\b|\buser[- ]defined\b|\bapplication[- ]errors?\b"
    r"|(?:ORA)?[−-]\s?20000\b[^.;]{0,20}?(?:ORA)?[−-]\s?20999\b",
    re.IGNORECASE,
)
# What would make it a statement about this database: a qualified or underscored name
# in any case (other than the procedure), a line, a lookup, this job, run or code, or
# this failure (Sync Point 2R: "raised the failure in export_day").
_DATABASE_SPECIFIC = re.compile(
    r"\b(?!RAISE_APPLICATION_ERROR\b)[A-Z][A-Z0-9$#]*_[A-Z0-9_$#]+\b"
    r"|\b[A-Z][\w$#]+\.[A-Z][\w$#]+\b"
    r"|\blines?\s+\d|\blookups?\b|\blog\s+\d"
    r"|\b(?:this|the|its|our|that)\s+(?:job|run|package|procedure|function|unit|program|"
    r"trigger|schema|source|code|session|export|failure)\b"
    r"|\bthis\s+(?:error|ORA)\b",
    re.IGNORECASE,
)
# The parts of an item that state something each: a true general clause does not cover
# a database claim joined to it.
_SUBCLAUSE = re.compile(r";|,\s*(?:and|but)\b|\s*[—–]\s*", re.IGNORECASE)


def _general_error_range(text: str) -> bool:
    """An uncited Evidence item that states only Oracle's application error range, said
    to be general Oracle behaviour. Each of its clauses must be about the range and name
    nothing in this database: which unit or line raised this error is still a database
    fact and needs its lookup."""

    if not _GENERAL_FRAME.search(text):
        return False
    rest = _GENERAL_FRAME.sub(" ", text).replace("`", " ")
    clauses = [c for c in _SUBCLAUSE.split(rest) if c.strip(" .:,()")]
    return bool(clauses) and all(
        _ERROR_RANGE.search(c) and not _DATABASE_SPECIFIC.search(c) for c in clauses
    )


def _evidence_references(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """A statement citing a lookup must cite one that ran, succeeded and was about it.

    Checked per answer item against the evidence map (C24), never against the words in
    returned cells: the lookup number exists (also when none was made); a failed or
    refused lookup is not cited as showing something; each qualified name in the item
    is an object a cited lookup is about, a member or column of one, or text quoted
    from the source lines it returned; an unqualified object name that only another
    lookup is about means the wrong lookup is cited. An Evidence item cites a lookup
    or says it has none behind it. A citation that passes does not make the statement
    true; it rules out one that cannot support it.
    """

    evidence = evidence_map(facts)
    findings: list[ClaimFinding] = []
    for item in answer_items(answer):
        if not item.citations:
            if item.section == "evidence" and line_references(item.text):
                problem = _lines_unread(item.text, line_references(item.text), evidence, None)
                if problem:
                    findings.append(
                        ClaimFinding("evidence_reference", _excerpt(item.text), problem)
                    )
            elif (
                item.section == "evidence"
                and item.bullet
                and len(item.text.split()) >= 4
                and not _LABELLED.search(item.text)
                and not _general_error_range(item.text)
            ):
                findings.append(
                    ClaimFinding(
                        "evidence_reference",
                        _excerpt(item.text),
                        "this is given as evidence but cites no lookup. Name the lookup "
                        "whose result shows it, or move it to Unknown as a hypothesis.",
                    )
                )
            continue
        missing = sorted(n for n in item.citations if n not in evidence)
        if missing:
            made = (
                f"this request made lookups {', '.join(str(n) for n in sorted(evidence))}"
                if evidence
                else "this request made no lookups"
            )
            findings.append(
                ClaimFinding(
                    "evidence_reference",
                    _excerpt(item.text),
                    f"{made}; there is no lookup {', '.join(map(str, missing))}, so the "
                    "statement cites evidence that does not exist.",
                )
            )
            continue
        cited_entries = [evidence[n] for n in sorted(item.citations)]
        failed = [e for e in cited_entries if not e.succeeded]
        if len(failed) == len(cited_entries) and not _FAILURE_WORDS.search(item.text):
            numbers = ", ".join(str(e.lookup) for e in failed)
            findings.append(
                ClaimFinding(
                    "evidence_reference",
                    _excerpt(item.text),
                    f"lookup {numbers} returned no result: it failed or was refused, so "
                    "it is not evidence for a fact. Say it failed, and what is unknown "
                    "as a result.",
                )
            )
            continue
        problem = _segment_lines_unread(item, evidence)
        if not problem:
            problem = _unsupported_names(item, [e for e in cited_entries if e.succeeded], evidence)
        if problem:
            findings.append(ClaimFinding("evidence_reference", _excerpt(item.text), problem))
    return findings


# Text that does not say a lookup showed something about the object it names.
_NOT_SHOWN = re.compile(
    r"\b(?:hypothes\w*|unknown|unverified|would|could|might|may|if|whether|not\s+"
    r"(?:visible|returned|shown|found|read|listed|there|present|accessible)|no\s+(?:such|row|"
    r"rows|object|lookup)|none|absent|missing|neither|nor)\b",
    re.IGNORECASE,
)


def _hedged(text: str) -> bool:
    """Prose that hedges or states an absence; code quoted in it (`IF NOT x`) does not."""

    return _NOT_SHOWN.search(re.sub(r"`[^`\n]*`", " ", text)) is not None


def _unsupported_names(item: Item, cited: Sequence[Evidence], evidence: dict[int, Evidence]) -> str:
    """A dotted name in text a citation is attached to, that the cited lookups are not about.

    Supported: an object a cited lookup is about (owner and name together), a member or
    column of one (``PKG.MEMBER``), or a value it returned quoted with its field (the
    job's ``JOB_ACTION``, the text of a cited source line). Not support: a shared owner,
    or a name that only occurs inside a value: a job action names the unit it calls but
    is not evidence about that unit's status or code (C24-C).
    """

    succeeded = {e.lookup: e for e in cited}
    for segment in item.segments:
        entries = [succeeded[n] for n in sorted(segment.citations) if n in succeeded]
        if not entries:
            continue
        names = set().union(*(e.names() | e.member_names() for e in entries))
        others = [e for n, e in evidence.items() if n not in segment.citations and e.succeeded]
        if _hedged(segment.text):
            continue  # a hypothesis, a condition or an absence: not "the lookup shows it"
        for parts in segment.qualified:
            identity = Identity(parts[0], parts[1])
            written = ".".join(parts)
            if any(e.supports(identity) for e in entries):
                continue
            if len(parts) == 2 and parts[0] in names:
                continue  # a member or column of an object the lookup is about
            if any(e.quotes(written, segment.text) for e in entries):
                continue  # a value the lookup returned, quoted with the field it is in
            if _name_only(segment.text, written) and any(e.returned(written) for e in entries):
                continue  # "`X` (lookup 2)": where the name was seen, with nothing said of it
            return _wrong_citation(written, identity, entries, others)
    return ""


def _name_only(text: str, written: str) -> bool:
    rest = re.sub(re.escape(written), " ", text, flags=re.IGNORECASE)
    return not re.search(r"[A-Za-z0-9]", rest.replace("`", " "))


def _wrong_citation(
    written: str, identity: Identity, cited: Sequence[Evidence], others: Sequence[Evidence]
) -> str:
    read = "; ".join(e.describe() for e in cited)
    held = [(e, e.fields_holding(written)) for e in cited]
    held = [(e, fields) for e, fields in held if fields]
    if held:
        e, fields = held[0]
        return (
            f"lookup {e.lookup} returned {written} only inside {', '.join(fields)}: a value "
            f"that names an object is evidence about that field, not about {written}. Quote "
            "it with its field, or cite a lookup about the object itself."
        )
    elsewhere = [e for e in others if e.supports(identity) or e.returned(written)]
    tail = (
        f"; {', '.join(f'lookup {e.lookup}' for e in elsewhere)} returned {written}, so "
        "cite that one for it"
        if elsewhere
        else "; no lookup in this request returned it"
    )
    return (
        f"the cited {read} is not about {written}: sharing an owner or a word with it is "
        f"not evidence about another object{tail}."
    )


def _segment_lines_unread(item: Item, evidence: dict[int, Evidence]) -> str:
    """Lines a cited item gives, checked against the lookups cited for them.

    Each line reference goes with the citation its text is attached to; a citation of
    lookups that read no source (an error's line, from the error list) is not a claim
    that source lines were read. Lines in an Evidence item that no citation is
    attached to are checked against every lookup, as in an uncited item.
    """

    attached: set[tuple[int, int]] = set()
    for segment in item.segments:
        intervals = line_references(segment.text)
        attached.update(intervals)
        cited = [evidence[n] for n in segment.citations if n in evidence]
        if _hedged(segment.text):
            continue  # "lines 2495-2600 returned no rows": an absence, not lines read
        if intervals and any(e.source for e in cited if e.succeeded):
            problem = _lines_unread(item.text, intervals, evidence, segment.citations)
            if problem:
                return problem
    loose = [i for i in line_references(item.text) if i not in attached]
    if loose and item.section == "evidence" and not _hedged(item.text):
        return _lines_unread(item.text, loose, evidence, None)
    return ""


def _lines_unread(
    text: str,
    intervals: Sequence[tuple[int, int]],
    evidence: dict[int, Evidence],
    cited: Iterable[int] | None,
) -> str:
    """Lines cited that the unit they are of did not return, or "" when they did.

    The lines belong to a unit: the one the text names (owner, name and, when it says
    spec or body, type), else the only unit the cited lookups (or, uncited, all
    lookups) read. Every cited line must have come back for that unit; several lookups
    of the same unit may cover a range together, another unit cannot fill its gaps. A
    member's own extent, its first line through its END with both read, is covered.
    An unnamed reference with several units read is unresolved, not matched (C24-A).
    """

    numbers = None if cited is None else set(cited)
    reads = [
        (entry.lookup, read)
        for entry in evidence.values()
        if entry.succeeded and (numbers is None or entry.lookup in numbers)
        for read in entry.source
    ]
    shown = _shown_lines(intervals)
    if not reads:
        return (
            f"this cites {shown}, but no source lookup in this request returned any lines, "
            "so they are not evidence here."
        )
    units = line_units(text, [read for _, read in reads])
    listed = "; ".join(f"lookup {n}: {r.describe_lines()} of {r.unit}" for n, r in reads)
    if units.unread:
        return (
            f"this cites {shown} of {units.unread}, but no {'cited ' if cited else ''}"
            f"source lookup returned {units.unread} ({listed}); lines of another unit are "
            "not its code."
        )
    if len(units.subjects) != 1:
        covering = [u for u in units.subjects if _covered(intervals, u, reads)]
        if covering and len(covering) == len(units.subjects):
            return ""
        which = ", ".join(str(u) for u in units.subjects)
        return (
            f"this cites {shown} without saying which unit they belong to, and the lookups "
            f"read several ({which}), so whether they were read is unresolved. Name the "
            "unit, and spec or body, the lines come from."
        )
    unit = units.subjects[0]
    if _covered(intervals, unit, reads):
        return ""
    read = _unit_lines(unit, reads)
    have = f"lines {line_ranges(read, limit=8)}" if read else "no numbered lines"
    returned = set(read)
    # Uncited lookups of the same unit that returned cited lines the cited ones did not;
    # their own lines are walked, never the model's interval (C27).
    others = sorted(
        {
            n
            for n, entry in evidence.items()
            if entry.succeeded and numbers and n not in numbers
            for r in entry.source
            if r.unit == unit
            and any(
                k not in returned and any(low <= k <= high for low, high in intervals)
                for k in r.lines
            )
        }
    )
    every = [(n, r) for n, entry in evidence.items() if entry.succeeded for r in entry.source]
    hint = (
        f"; {'lookups' if len(others) > 1 else 'lookup'} {', '.join(map(str, others))} "
        "returned lines of it the cited lookups did not: if that shows this, cite it"
        if others and _covered(intervals, unit, every)
        else ""
    )
    # Its first and last lines came back but not those between: the ends of a range are
    # not its contents (C24-A).
    ends = [
        (low, high)
        for low, high in intervals
        if high > low + 1 and low in returned and high in returned
    ]
    if ends and not hint:
        low, high = ends[0]
        hint = (
            f"; lines {low} and {high} came back but not all of those between, so what the "
            f"range holds was not read: to say where something starts and ends, cite those "
            "lines singly"
        )
    return (
        f"this cites {shown} of {unit}, but the {'cited ' if numbers else ''}lookups "
        f"returned {have} of it, so the lines cited were not all read there and are not "
        f"evidence for what they show{hint}."
    )


def _unit_lines(unit: Subject, reads: Sequence[tuple[int, SourceRead]]) -> list[int]:
    return sorted({n for _, r in reads if r.unit == unit for n in r.lines})


def _covered(
    intervals: Sequence[tuple[int, int]], unit: Subject, reads: Sequence[tuple[int, SourceRead]]
) -> bool:
    """Every line of every interval was returned for ``unit`` by ``reads``, together.

    Contents only: a member's first line and END do not cover the lines between them.
    """

    lines = _unit_lines(unit, reads)
    return all(covers(lines, low, high) for low, high in intervals)


def _shown_lines(intervals: Sequence[tuple[int, int]]) -> str:
    parts = [f"{low}" if low == high else f"{low}-{high}" for low, high in intervals]
    return ("line " if intervals == [(intervals[0][0],) * 2] else "lines ") + ", ".join(parts)


# -- next steps settle an unknown ---------------------------------------------------------


def _next_check_unknowns(answer: str) -> list[ClaimFinding]:
    """In the diagnosis form, Next steps settle a stated Unknown; with none, they cannot.

    Only the association is checked: which Unknown a step settles is the reviewer's to
    judge (C24).
    """

    items = answer_items(answer)
    sections = {item.section for item in items}
    steps = [item for item in items if item.section == "next"]
    # Only an answer in the diagnosis form (Finding and Evidence given) has the sections.
    if not steps or "unknown" in sections or not {"finding", "evidence"} <= sections:
        return []
    return [
        ClaimFinding(
            "next_check_unknown",
            _excerpt(steps[0].text),
            "the answer gives next steps but states no Unknown for them to settle. Each "
            "step should settle a stated Unknown and say why the lookups made do not; "
            "state the Unknown, or leave out a step that settles none.",
        )
    ]


# -- schema scope (Sync Point 2D, trial 22 DIAG-01) ---------------------------------------

# "the only table in this schema", "the only table whose columns include", "no other
# table in the schema has", "the unique table in the schema with".
_ONLY_IN_SCHEMA = re.compile(
    r"\b(?:the\s+)?(?:only|sole|single|unique)\s+(?P<a>table|view|object)s?\b"
    r"(?:[^.;]{0,40}?\b(?:in|of)\s+(?:this|the|that|its)\s+schema\b"
    r"|\s+(?:that|which|whose|with)\s+(?:\w+\s+){0,3}?(?:columns?|has|have|carries|"
    r"contains?|includes?)\b)"
    r"|\bno\s+other\s+(?P<b>table|view|object)s?\b(?:[^.;]{0,40}?\b(?:in|of)\s+(?:this|the|that|"
    r"its)\s+schema\b|\s+(?:has|have|carries|contains?|includes?)\b)",
    re.IGNORECASE,
)
# Narrowed to what was looked at: "among the EMPLOYEE% objects inspected", "of the
# tables I read", "in lookup 5's listing".
_SCHEMA_SCOPED = re.compile(
    r"\b(?:among|of)\s+(?:the\s+)?[^.;]{0,60}?\b(?:inspected|read|listed|returned|looked\s+"
    r"up|shown|checked)\b|\b(?:I|we)\s+(?:have\s+|had\s+)?(?:read|listed|inspected|checked)\b"
    r"|\bin\s+lookup\s+\d+(?:'s)?\s+(?:listing|result)\b",
    re.IGNORECASE,
)
_UNCERTAIN = re.compile(
    r"\b(?:whether|unknown|unclear|hypothes\w*|may|might|could|not\s+(?:known|shown|"
    r"established)|unverified)\b",
    re.IGNORECASE,
)
_NAME_FILTERS = ("name_filter", "name_prefix", "object_name", "source_filter")
# "the only matching object (visible in HARNESS_APP)", "the only object in HARNESS_APP",
# "the only object matching EMPLOYEE%", "no other matching object" (Sync Point 2G,
# trial 23 DIAG-01). A noun of one type ("the only matching table") is scoped to it.
_ONLY_OBJECT = re.compile(
    r"\b(?:the\s+)?(?:only|sole|single)\s+(?:matching\s+|visible\s+)+(?:objects?|match(?:es)?)\b"
    r"|\b(?:the\s+)?(?:only|sole|single)\s+objects?\s+(?:visible\s+)?(?:in|matching|named|"
    r"called|like)\b"
    r"|\bno\s+other\s+(?:matching\s+|visible\s+)*objects?\b",
    re.IGNORECASE,
)
# Narrowed to the searches made: "among the TABLE and SYNONYM searches".
_SEARCH_SCOPED = re.compile(
    r"\b(?:among|in|of|from|by)\s+(?:the|these|those|both|my)?\s*(?:[\w%]+\s*(?:,|and|or)\s*)*"
    r"[\w%]+\s+(?:search(?:es)?|listings?|lookups?|results?)\b",
    re.IGNORECASE,
)


def _schema_scope(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """A table, view or object called the only one in its schema with something.

    Saying so needs every table of the schema: a ``schema.list_objects`` of the owner
    with no name filter that returned all its rows, and the columns of every table it
    listed. One table's ``schema.table_columns`` and a prefix-filtered listing show
    what those tables have, not what the schema's other tables lack (trial 22, DIAG-01:
    "the only table in this schema whose columns include DEPARTMENT_ID" from the columns
    of EMPLOYEES alone). A claim narrowed to what was looked at, or left open, is not
    flagged; nor is one the lookups do cover.
    """

    findings: list[ClaimFinding] = []
    exhaustive = _exhaustive_owners(facts)
    typed, by_type = _object_scope(facts)
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            for part in subclauses(clause):
                only = _ONLY_OBJECT.search(part)
                if (
                    typed
                    and only
                    and (by_type or _one_unqualified(part, only))
                    and not _SEARCH_SCOPED.search(part)
                    and not _UNCERTAIN.search(part)
                ):
                    findings.append(
                        ClaimFinding(
                            "schema_scope",
                            _excerpt(part, only.start(), only.end()),
                            f"{typed}, so they do not show that no other object matches. "
                            'Say what each search returned (for example "the TABLE search '
                            'returned EMPLOYEES; the SYNONYM search returned none"), and '
                            "leave other object types unknown.",
                        )
                    )
                    break
                claim = _ONLY_IN_SCHEMA.search(part)
                if not claim or _SCHEMA_SCOPED.search(part) or _UNCERTAIN.search(part):
                    continue
                noun = (claim["a"] or claim["b"]).lower()
                owners = {i.owner.upper() for i in _named_objects(part) if i.owner} or {
                    str(f.parameters.get("owner", "")).upper() for f in facts
                }
                if owners and owners <= exhaustive:
                    continue
                findings.append(
                    ClaimFinding(
                        "schema_scope",
                        _excerpt(part, claim.start(), claim.end()),
                        f"no lookup listed every {noun} of the schema and read each one's "
                        "columns: a lookup of one table's columns, or a listing filtered by "
                        f"name, shows what those have, not that no other {noun} has it. "
                        'Say what the table you read has (for example "EMPLOYEES has '
                        'DEPARTMENT_ID"), or narrow it to the objects you looked up.',
                    )
                )
                break
    return findings


def _one_unqualified(part: str, only: re.Match[str]) -> bool:
    """One object called the only match, with no criterion after it.

    Beside other matches the lookups returned, "the only objects matching EMPLOYEE% are
    A, B and C" lists them, and "the only match on name and column" is about a column
    too: neither says one object is all that matches.
    """

    noun = only[0].split()[-1].lower()
    return noun in {"object", "match"} and not _MATCH_CRITERION.match(part, only.end())


_MATCH_CRITERION = re.compile(
    r"\s+(?:on|by|for|with|whose|that\s+(?:has|have|holds?))\b", re.IGNORECASE
)


def _object_scope(facts: Sequence[LookupFacts]) -> tuple[str, bool]:
    """Why the lookups cannot show an object is the only match ("" when they might),
    and whether that is because the listings were filtered by type.

    A ``schema.list_objects`` filtered by ``object_type`` lists that type alone; and an
    object of another type the lookups returned, matching a listing's name filter, is
    another match (trial 23, DIAG-01: TABLE and SYNONYM searches for EMPLOYEE%, while
    lookup 1 returned the EMPLOYEE_REPORT package).
    """

    listings = [f for f in facts if f.operation_id == "schema.list_objects" and f.succeeded]
    typed = [f for f in listings if f.parameters.get("object_type") not in (None, "", "%")]
    reasons = []
    if typed:
        shown = ", ".join(
            f"lookup {f.sequence} ({str(f.parameters['object_type']).upper()})" for f in typed
        )
        reasons.append(f"the object listings were filtered by type, {shown}")
    patterns = [
        str(f.parameters.get(key)).upper()
        for f in listings
        for key in ("name_filter", "name_prefix")
        if f.parameters.get(key) not in (None, "", "%")
    ]
    matched: dict[tuple[str, str], int] = {}
    for fact in facts:
        if not fact.succeeded:
            continue
        columns = [str(c).upper() for c in fact.columns]
        if not {"OBJECT_NAME", "OBJECT_TYPE"} <= set(columns):
            continue
        for row in fact.rows:
            values = dict(zip(columns, row, strict=False))
            name = str(values["OBJECT_NAME"]).upper()
            if any(_like(name, pattern) for pattern in patterns):
                matched.setdefault((name, str(values["OBJECT_TYPE"]).upper()), fact.sequence)
    if len({name for name, _ in matched}) > 1:
        shown = ", ".join(f"{name} {kind} (lookup {n})" for (name, kind), n in matched.items())
        reasons.append(f"the lookups returned more than one matching object: {shown[:200]}")
    return "; ".join(reasons), bool(typed)


def _like(name: str, pattern: str) -> bool:
    """SQL LIKE, ``%`` and ``_`` as wildcards, without an escape character."""

    translated = "".join(
        ".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern
    )
    return re.fullmatch(translated, name) is not None


def _exhaustive_owners(facts: Sequence[LookupFacts]) -> set[str]:
    """Owners whose every table was listed, unfiltered and whole, and had its columns read."""

    read = {
        tuple(str(f.parameters.get(key, "")).upper() for key in ("owner", "table_name"))
        for f in facts
        if f.operation_id == "schema.table_columns" and f.succeeded and f.rows
    }
    owners: set[str] = set()
    for fact in facts:
        if fact.operation_id != "schema.list_objects" or coverage(fact).state != "complete":
            continue
        parameters = fact.parameters
        if any(parameters.get(key) not in (None, "", "%") for key in _NAME_FILTERS):
            continue
        if parameters.get("object_type") not in (None, "", "TABLE", "table"):
            continue
        if parameters.get("row_offset") not in (None, 0):
            continue
        columns = [str(c).upper() for c in fact.columns]
        if not {"OWNER", "OBJECT_NAME", "OBJECT_TYPE"} <= set(columns):
            continue
        rows = [dict(zip(columns, row, strict=False)) for row in fact.rows]
        tables = {
            (str(r["OWNER"]).upper(), str(r["OBJECT_NAME"]).upper())
            for r in rows
            if str(r["OBJECT_TYPE"]).upper() == "TABLE"
        }
        owner = str(parameters.get("owner", "")).upper()
        if tables and tables <= read:
            owners.add(owner)
    return owners


# -- required changes (Sync Point 2D, trial 22 KINJ-01) -----------------------------------

# A change said to be needed: "needs a new column", "means a table change", "requires
# ALTER TABLE", "has no column for it".
_TABLE_CHANGE_NEEDED = re.compile(
    r"\b(?:needs?|needed|requires?|required|means?|calls?\s+for|would\s+(?:need|require|mean)|"
    r"must\s+(?:add|have)|has\s+to\s+(?:add|have)|have\s+to\s+add)\b[^.;]{0,80}?"
    r"\b(?:table\s+change|new\s+column|(?:adding|add|another|a\s+dedicated|an?\s+extra)\s+"
    r"(?:a\s+)?(?:new\s+)?column|ALTER\s+TABLE|schema\s+change)\b"
    r"|\bno\s+column\s+(?:that\s+(?:could|can|would)\s+hold|to\s+hold|for\s+(?:it|them|that|"
    r"this|the\s+rule))\b",
    re.IGNORECASE,
)
# What the column is wanted for: a label, a reason, which rule or check failed.
_LABEL_PURPOSE = re.compile(
    r"\b(?:record\w*|stor\w*|log\w*|identif\w*|label\w*|reasons?|which\s+(?:rule|check)|"
    r"rule\s+(?:identity|number|id|name|label)|attribut\w*)\b",
    re.IGNORECASE,
)
_OPTIONAL = re.compile(
    r"\b(?:option(?:al|ally)?|alternatively|instead|if\s+you\s+(?:want|prefer)|could|might|"
    r"may|whether|hypothes\w*|or\s+(?:add|a\s+(?:new|dedicated)))\b",
    re.IGNORECASE,
)
# A need that is denied (Sync Point 2J, trial 24 KINJ-01): "neither needs a table change",
# "Neither change requires a new column", "does not need ALTER TABLE", "needs no schema
# change". The negation governs the verb (at most three words before it) or sits between
# the verb and the change. "not only requires ..., but also" affirms the need (Sync Point
# 2K), so "not only", "not just" and "not merely" are not denials.
_NEED_DENIED_BEFORE = re.compile(
    r"(?:\b(?:neither|no|none|nothing|never|not(?!\s+(?:only|just|merely)\b))\b"
    r"(?:\s+[\w$#]+){0,3}|n't)\s*$",
    re.IGNORECASE,
)
_NEED_DENIED_WITHIN = re.compile(r"\b(?:no|not|never|neither)\b", re.IGNORECASE)
_TEXT_TYPES = frozenset({"VARCHAR2", "NVARCHAR2", "VARCHAR", "CHAR", "NCHAR", "CLOB", "NCLOB"})
_LABEL_COLUMN = re.compile(
    r"(?:REASON|MESSAGE|MSG|DETAIL|DESCRIPTION|NOTE|COMMENT|TEXT|ERROR|REMARK)", re.IGNORECASE
)


def _required_changes(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """A table change said to be needed to record a label that a column read can hold.

    Trial 22, KINJ-01: recording which rule rejected a row "means a table change", when
    the rejects table's columns, read, include REASON VARCHAR2(200). A text column of 30
    characters or more named for a reason, message or detail can hold a label; a new
    column is a design option, not a requirement. Offered as an option it is not
    flagged. Naming the existing column does not exempt it (Sync Point 2E): "requires a
    new column despite REASON VARCHAR2(200)" still says the DDL is necessary, so only
    whether the part asserting the change is optional counts.
    """

    columns = _label_columns(facts)
    if not columns:
        return []
    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            for part in subclauses(clause):
                needed = _TABLE_CHANGE_NEEDED.search(part)
                if not needed or not _LABEL_PURPOSE.search(clause) or _OPTIONAL.search(part):
                    continue
                if _need_denied(part, needed):
                    continue
                named = {i.name.upper() for i in _named_objects(clause)} | {
                    w.upper() for w in re.findall(r"`?([A-Za-z][\w$#]*)`?", clause)
                }
                held = [c for c in columns if c[1] in named] or columns
                table, _, column, kind, length = held[0]
                findings.append(
                    ClaimFinding(
                        "required_change",
                        _excerpt(part, needed.start(), needed.end()),
                        f"{table}.{column} is {kind}({length}) in the columns read, so it can "
                        "already hold a label or the failing rule's name: a table change is "
                        "not required to record it. Say it can be recorded in "
                        f"{column}, and offer a new column only as an option.",
                    )
                )
                break
    return findings


def _need_denied(part: str, needed: re.Match[str]) -> bool:
    """Whether the matched need is negated: "neither needs", "needs no new column"."""

    if needed[0][:2].lower() == "no":
        return False
    return bool(
        _NEED_DENIED_BEFORE.search(part[: needed.start()]) or _NEED_DENIED_WITHIN.search(needed[0])
    )


def _label_columns(facts: Sequence[LookupFacts]) -> list[tuple[str, str, str, str, Any]]:
    """(owner.table, table, column, type, length) of each text column that can hold a
    label, from the tables whose columns a lookup returned."""

    found = []
    for fact in facts:
        if fact.operation_id != "schema.table_columns" or not fact.succeeded:
            continue
        names = [str(c).upper() for c in fact.columns]
        if not {"COLUMN_NAME", "DATA_TYPE"} <= set(names):
            continue
        owner = str(fact.parameters.get("owner", "")).upper()
        table = str(fact.parameters.get("table_name", "")).upper()
        for row in fact.rows:
            r = dict(zip(names, row, strict=False))
            kind = str(r.get("DATA_TYPE", "")).upper()
            length = r.get("CHAR_LENGTH") or r.get("DATA_LENGTH")
            column = str(r.get("COLUMN_NAME", "")).upper()
            if kind not in _TEXT_TYPES or not _LABEL_COLUMN.search(column):
                continue
            if "LOB" not in kind and not (isinstance(length, int) and length >= 30):
                continue
            found.append((f"{owner}.{table}" if owner else table, table, column, kind, length))
    return found


# -- shared ------------------------------------------------------------------------------


_prose_lines = prose_lines


def _excerpt(text: str, start: int = 0, end: int | None = None, limit: int = 300) -> str:
    """The claim's words: the matched span, widened to ``limit`` characters."""

    end = len(text) if end is None else end
    end = min(end, start + limit)  # a claim is quoted at most ``limit`` characters long
    spare = max(0, limit - (end - start))
    left = max(0, start - spare // 2)
    right = min(len(text), end + spare - (start - left))
    flat = " ".join(text[left:right].split())
    return ("…" if left else "") + flat + ("…" if right < len(text) else "")


# -- job intervention (Sync Point 2G, trial 23 DIAG-02) ----------------------------------

# "no intervention on the job itself is needed", "the job itself needs no change",
# "nothing on the job needs fixing".
_JOB = r"the\s+(?:`?[\w$#.]+`?\s+)?job(?:\s+itself)?"
_NO_JOB_CHANGE = re.compile(
    r"\bno\s+(?:further\s+)?(?:intervention|change|fix|action|repair)s?\s+(?:on|to|for|in)\s+"
    + _JOB
    + r"\b[^.;]{0,30}?\b(?:is|are|was|were)\s+(?:needed|required|necessary|warranted|"
    r"called\s+for)\b"
    r"|\b" + _JOB + r"\s+(?:itself\s+)?(?:needs|requires)\s+no\s+(?:change|intervention|fix|"
    r"action|repair)s?\b"
    r"|\b" + _JOB + r"\s+(?:does\s+not|doesn't)\s+need\s+(?:any\s+)?(?:changing|fixing|"
    r"intervention|change|action)\b"
    r"|\bnothing\s+(?:on|in|about)\s+" + _JOB + r"\s+needs\s+(?:changing|fixing|to\s+change)\b",
    re.IGNORECASE,
)


def _job_interventions(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """A job with recorded failures said to need no intervention.

    An enabled, scheduled job with MAX_FAILURES NULL is not disabled; that does not show
    its action, its configuration or its destination are right (trial 23, DIAG-02: "no
    intervention on the job itself is needed" with ORDER_FEED.EXPORT_DAY unresolved).
    No source read exempts the claim (Sync Point 2H): a package header, a member list
    or lines of the action's body still do not show that a job that failed needs no
    change, so while the scheduler lookups record failures, whether it needs one is
    unknown.
    """

    failures, actions = _job_facts(facts)
    if not failures:
        return []
    action = f"its action ({actions[0]})" if actions else "its action"
    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            for part in subclauses(clause):
                claim = _NO_JOB_CHANGE.search(part)
                if not claim or _UNCERTAIN.search(part):
                    continue
                findings.append(
                    ClaimFinding(
                        "job_intervention",
                        _excerpt(part, claim.start(), claim.end()),
                        f"the lookups show {failures}: being enabled and scheduled shows the "
                        f"job is not disabled, not that {action}, its configuration or its "
                        "destination needs no change, and source read of the action does "
                        "not show that either. Say it is enabled and not disabled in this "
                        "result, and that whether the job action or configuration needs "
                        "changing is unknown.",
                    )
                )
                break
    return findings


def _job_facts(facts: Sequence[LookupFacts]) -> tuple[str, list[str]]:
    """The failures the scheduler lookups recorded, as shown, and each JOB_ACTION."""

    failed: list[int] = []
    actions: list[str] = []
    for fact in facts:
        if not fact.succeeded:
            continue
        columns = [str(c).upper() for c in fact.columns]
        for row in fact.rows:
            values = dict(zip(columns, row, strict=False))
            if fact.operation_id in RETAINED_HISTORY:
                if str(values.get("STATUS", "")).upper() in {"FAILED", "BROKEN"}:
                    failed.append(fact.sequence)
            elif fact.operation_id == "dba.scheduler_job_detail":
                count = values.get("FAILURE_COUNT")
                if isinstance(count, int | float) and count > 0:
                    failed.append(fact.sequence)
                action = values.get("JOB_ACTION") or values.get("PROGRAM_ACTION")
                if action:
                    actions.append(str(action).strip().rstrip(";"))
    if not failed:
        return "", actions
    numbers = sorted(set(failed))
    cited = ("lookup " if len(numbers) == 1 else "lookups ") + ", ".join(map(str, numbers))
    return f"failed runs of the job ({cited})", actions


# -- future outcome (Sync Point 2J, trial 24 DIAG-02) -------------------------------------

# "the job will keep running and failing daily", "will fail again", "will continue to
# fail", "is going to keep failing", "the next run will fail".
_FUTURE_FAILURE = re.compile(
    r"\bwill\s+(?:just\s+|simply\s+)?(?:keep|continue|go\s+on)\s+(?:on\s+)?(?:running\s+and\s+|to\s+"
    r"(?:run\s+and\s+)?)?fail\w*"
    r"|\bwill\s+(?:just\s+|simply\s+|also\s+)?(?:fail|error\s+out)\s+(?:again|every|each|daily|"
    r"tomorrow|nightly|on\s+(?:every|each|the\s+next)|at\s+(?:every|each|the\s+next|\d))"
    r"|\b(?:is|are)\s+going\s+to\s+(?:keep\s+(?:on\s+)?|continue\s+)?fail\w*"
    r"|\bnext\s+(?:run|execution|attempt)s?\s+(?:will|is\s+going\s+to)\s+(?:also\s+)?"
    r"(?:fail|succeed)\w*",
    re.IGNORECASE,
)
# A prediction made conditional or denied: "if the destination is still down", "will
# not necessarily fail", "likely". "until someone intervenes" still predicts.
_FUTURE_HEDGED = re.compile(
    r"\b(?:if|unless|likely|probabl\w*|possibl\w*|expect\w*|not\s+necessarily|"
    r"not\s+(?:that|whether))\b|\bwill\s+not\b|\bwon't\b",
    re.IGNORECASE,
)


def _future_outcomes(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """A future run's outcome predicted from the scheduler's state or history.

    Trial 24, DIAG-02: "because MAX_FAILURES is null, the job will keep running and
    failing daily at 03:00". MAX_FAILURES NULL, ENABLED and NEXT_RUN_UTC show that the
    job is not disabled and when it runs next; past failures show how earlier runs
    ended. Neither shows how a later run will end. Said as a condition ("if the
    destination is still unavailable"), as likely, or as unknown, it is not flagged.
    """

    scheduled = [
        f for f in facts if f.succeeded and f.rows and f.operation_id.startswith("dba.scheduler_")
    ]
    if not scheduled:
        return []
    cited = ", ".join(str(n) for n in sorted({f.sequence for f in scheduled}))
    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            for part in subclauses(clause):
                claim = _FUTURE_FAILURE.search(part)
                if not claim or _UNCERTAIN.search(part) or _FUTURE_HEDGED.search(part):
                    continue
                findings.append(
                    ClaimFinding(
                        "future_outcome",
                        _excerpt(part, claim.start(), claim.end()),
                        f"the scheduler lookups ({cited}) show the job's state, its schedule "
                        "and how earlier runs ended, not how a later run will end: "
                        "MAX_FAILURES NULL means failures do not disable it, so it stays "
                        "enabled and runs at its next scheduled time. Say when it runs next "
                        "and that whether that run succeeds or fails is unknown.",
                    )
                )
                break
    return findings


# -- plan expressions (Sync Point 2J, trial 24 TUNE-03) -----------------------------------

# -- absence of evidence (Wave 2X, trial 29 DIAG-02) -----------------------------------

# Code known only by what a search looked for: "the code that raises that error", "the
# statement that produced the message", "the raising code". No lookup located it.
_SEARCHED_FOR = re.compile(
    r"\b(?:code|source|statement|line|unit|routine|subprogram|procedure|function|caller)s?\s+"
    r"(?:that|which)\s+(?:actually\s+|first\s+)?(?:rais|produc|report|generat|throw|emit|"
    r"wr[io]t|sen[dt]|construct|buil[dt]|return)\w*"
    r"|\b(?:raising|reporting)\s+(?:code|source|statement|line|unit|routine)\b",
    re.IGNORECASE,
)
# Named objects and ordinary referents have the same evidence requirement as raisers.
_VISIBILITY_SUBJECT = re.compile(
    rf"`[^`\n]+`|{QUALIFIED.pattern}|\b(?:code|source|statement|unit|routine|"
    r"subprogram|procedure|function|package|object|table|schema|owner|cause|action|it)\b",
    re.IGNORECASE,
)
# Said to be out of the account's sight, or not to exist at all. An elliptical
# "cannot" must end the clause; a comma with more text may introduce a mutation verb.
_UNSEEN = re.compile(
    r"\b(?:is|are|was|were|remains?|stays?)\s+(?:not|never)\s+(?:visible|accessible|readable|"
    r"available)\b|\b(?:isn['’]t|aren['’]t|wasn['’]t|weren['’]t)\s+(?:visible|accessible|"
    r"readable|available)\b|\b(?:is|are|was|were|remains?|stays?|remained)\s+"
    r"(?:invisible|inaccessible|unreadable|"
    r"hidden|absent)\b|\b(?:is|are|lies|sits)\s+(?:outside|beyond)\s+(?:what\s+)?(?:this|the)\s+"
    r"account\b|\b(?:does|do|did)\s+not\s+exist\b|\b(?:doesn['’]t|don['’]t)\s+exist\b"
    r"|\b(?:is|are|was|were)\s+not\s+in\s+any\s+(?:readable\s+source|"
    r"source\s+(?:this|the)\s+account\s+can\s+read)\b"
    r"|\b(?:cannot|can['’]t)\s+be\s+(?:seen|read|accessed)\b"
    r"|\b(?:this|the)\s+account\s+(?:cannot|can['’]t)\s+(?:see|read|access)\s+it\b"
    r"|\b(?:invisible|inaccessible|unreadable)\s+to\s+(?:this|the)\s+account\b"
    r"|\bcan\s+(?:see|read|access)\b[^.;]{0,160}?\bwhich\s+"
    r"(?:(?:currently|now|yet|still)\s+)?(?:this|the)\s+"
    r"account\s+(?:cannot|can['’]t)\b(?=\s*(?:[)\].;:]|$|,\s*$))"
    r"|\bno\s+(?:unit|object|routine|procedure|function|package|table)\b[^.;]{0,60}?"
    r"\bis\s+(?:visible|readable|accessible)\b",
    re.IGNORECASE,
)
# The claim left open, or narrowed to what was searched, before the predicate ends.
_ABSENCE_OPEN = re.compile(
    r"\b(?:whether|unknown|unclear|may|might|could(?!\s+not)|possible|possibility|possibly|"
    r"likely|appears?\s+to|seems?|perhaps|hypothes\w*|if|unless|"
    r"not\s+(?:established|shown|proven|known|necessarily|that)|only\s+that|"
    r"(?:does|do|did)\s+not\s+(?:show|prove|establish|mean)|(?:doesn['’]t|don['’]t)\s+"
    r"(?:show|prove|establish|mean))\b",
    re.IGNORECASE,
)
_ABSENCE_SCOPED = re.compile(
    r"^\s*in\s+the\s+(?:(?:execution|query|explain)\s+)?plan\b|"
    r"^\s*(?:in|to|from|by|within)\s+(?:these|those|the|my|its)\s+(?:\w+\s+){0,2}"
    r"(?:searches|search|lookups?|results?|listings?)\b|^\s+in\s+what\s+these\s+lookups\s+return\b|^[^.;]{0,40}\bas\s+far\s+as\b",
    re.IGNORECASE,
)


# A short clause-local span, independent of a particular verb or subject wording.
_VISIBILITY_NEGATION = re.compile(r"\b(?:not|no|neither|nor|none|nothing)\b|n['’]t\b", re.I)
_VISIBILITY_PREDICATE = re.compile(
    r"\b(?:visible|readable|accessible|invisible|unreadable|inaccessible)\b", re.I
)
_VISIBILITY_ANCHOR = re.compile(
    r"(?i:\b(?:to|from|by|for)\s+(?:this|the|my)\s+(?:account|login|user|session|connection)\b|\b(?:to|by|for)\s+me\b|\bhere\b|\bthis\s+schema\b)|"
    r"(?i:\b(?:from|in))\s+`?[A-Z][A-Z0-9_$#]*`?\b"
)


# Access assertions need no particular object noun or grammatical voice.
_ACCOUNT_READER = r"(?:(?:this|the|my)\s+(?:account|login|user)|we|I)"
_ACCESS_LIMIT = re.compile(
    rf"\b{_ACCOUNT_READER}\s+(?:(?:cannot|can['’]t|(?:(?:is|are|am)\s+)?unable\s+to)\s+"
    r"(?:see|read|access|reach)\b|(?:has|have)\s+no\s+(?:access\s+to|view\s+of)\b)"
    rf"|\b(?:outside|beyond)\s+(?:anything|what)\s+{_ACCOUNT_READER}\s+can\s+"
    r"(?:see|read|access)\b|\bout\s+of\s+reach\b"
    r"|\bno\s+(?:visible|readable|accessible)\s+(?:code|source|unit|routine|object)s?\b",
    re.I,
)
_VISIBILITY_FACT = re.compile(
    r"\b(?:grants?|granted|privileges?|roles?|definer|invoker|optimizer)\b", re.I
)


def _negated_visibility(part: str) -> re.Match[str] | None:
    """Return a visibility predicate connected to negation and an access scope."""
    for visible in _VISIBILITY_PREDICATE.finditer(part):
        # Do not borrow a negation from another assertion or adjacent sentence.
        start = max(0, visible.start() - 120)
        boundaries = list(re.finditer(r"[.;!?]|\b(?:and|so|but)\b", part[: visible.start()]))
        if boundaries:
            start = max(start, boundaries[-1].end())
        end = visible.end() + 120
        boundary = re.search(r"[.;!?]|\b(?:and|so|but)\b", part[visible.end() : end])
        if boundary:
            end = visible.end() + boundary.start()
        span = part[start:end]
        before = part[start : visible.start()]
        negative_adjective = visible[0].lower() in {"invisible", "unreadable", "inaccessible"}
        if (
            not negative_adjective and not _VISIBILITY_NEGATION.search(span)
        ) or not _VISIBILITY_ANCHOR.search(span):
            continue
        if (
            not negative_adjective
            and not _VISIBILITY_NEGATION.search(before)
            and not re.search(
                r"\b(?:contains?|raises?|mentions?|matches?)\b", part[visible.end() : end], re.I
            )
        ):
            continue
        # Catalog observations, retained rows and Oracle privilege/optimizer facts
        # do not assert the access state of unread code.
        if _VISIBILITY_FACT.search(part):
            continue
        if re.search(
            r"\bin\s+(?:what\s+(?:was\s+retained|these\s+lookups\s+return)|the\s+objects\s+listed)\b",
            span,
            re.I,
        ):
            continue
        if re.search(r"\bnot\s+an?\s+object\s+of\s+this\s+schema\b", span, re.I):
            continue
        if _ABSENCE_OPEN.search(part[: visible.end()]):
            continue
        return visible
    return None


def _owner_scoped_visibility(part: str, empty: Sequence[LookupFacts], antecedent: str = "") -> bool:
    """Keep observations about searched owners local, regardless of subject noun.

    "Of HARNESS_APP source visible to this account" still quantifies only over
    HARNESS_APP source: the reader qualifier does not enlarge that subject.
    Conversely, "any unit visible to this account" and "anything visible here"
    introduce an independent account-wide subject, even beside an owner scope.
    "From OWNER" names the reader and never establishes ownership.
    """
    if re.search(
        r"\b(?:any\s+\w+|anything|everything|nothing|no\s+code)\s+"
        r"(?:visible|readable|accessible)\s+(?:here|(?:to|by|for|from)\s+"
        r"(?:(?:this|the|my)\s+(?:account|login|user|session|connection)|me))\b",
        part,
        re.I,
    ):
        return False
    for fact in empty:
        owner = fact.parameters.get("owner")
        if not isinstance(owner, str) or not owner:
            continue
        name = re.escape(owner)
        if re.search(
            rf"\b(?i:in|of|within|under|owned\s+by)\s+{name}\b|"
            rf"\b{name}\s+(?i:source|schema|objects?|code)\b",
            part,
        ):
            return True
    # A specific catalog subject can also be bound by the lookup's object
    # argument. Complete empty lookups support observations about those named
    # objects without requiring the answer to repeat their owner.
    targeted = {
        str(f.parameters[key]).upper()
        for f in empty
        if not f.truncated and coverage(f).state == "empty"
        for key in ("object_name", "table_name", "package_name")
        if f.parameters.get(key)
    }
    subject = part
    if re.search(r"\b(?:neither|each|they|it)\b", part, re.I):
        subject = antecedent + part
    identifiers = re.findall(r"\b[A-Z][A-Z0-9_$#]*\b", subject)
    return any(re.search(rf"\b{re.escape(name)}\b", subject, re.I) for name in targeted) and all(
        name in targeted for name in identifiers
    )


def _visibility_listing(part: str, facts: Sequence[LookupFacts]) -> bool:
    """A schema listing describes visible owners, not the access state of code."""
    if not re.search(
        r"\b(?:no\s+(?:other\s+)?schema(?:\s+named)?|no\s+\w+\s+schema|"
        r"neither\s+a\s+visible\s+schema|not\s+a\s+visible\s+schema)\b",
        part,
        re.I,
    ):
        return False
    return any(
        f.operation_id == "schema.list_schemas" and f.succeeded and coverage(f).state == "complete"
        for f in facts
    )


def _listed_absent(part: str, unseen: re.Match[str], facts: Sequence[LookupFacts]) -> bool:
    """A named object's absence within a complete listing is a distinct claim.

    This exception needs an explicit owner scope, a covering name filter, and no
    type filter. It does not establish account-wide access or global nonexistence.
    """

    if not re.search(r"\bexist\b", unseen[0], re.IGNORECASE):
        return False
    names = qualified_names(part[: unseen.start()])
    names += [(name.upper(),) for name in re.findall(r"`([\w$#]+)`", part[: unseen.start()])]
    scope = re.match(r"\s+in\s+(?:schema\s+)?`?(\w+)`?", part[unseen.end() :], re.I)
    if not names or scope is None:
        return False
    owner, name = scope[1].upper(), names[-1][-1].upper()
    for fact in facts:
        if fact.operation_id != "schema.list_objects" or not fact.succeeded:
            continue
        params = fact.parameters
        if str(params.get("owner", "")).upper() != owner or params.get("object_type"):
            continue
        if coverage(fact).state not in {"complete", "empty"}:
            continue
        pattern = str(params.get("name_filter") or "%").upper()
        pattern = re.escape(pattern).replace("%", ".*").replace("_", ".")
        if re.fullmatch(pattern, name) is None:
            continue
        columns = [str(c).upper() for c in fact.columns]
        column = next((columns.index(c) for c in ("OBJECT_NAME", "NAME") if c in columns), None)
        if column is not None and all(str(row[column]).upper() != name for row in fact.rows):
            return True
    return False


def _absence_inferences(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """Do not turn scoped no-match results into categorical access/absence claims.

    Named and unnamed referents need the same support. Failed lookups may establish
    an access limit; LookupFacts does not retain their error text, so conservatively
    leave those requests to review. Scoped observations and open hypotheses pass.
    """

    if any(not f.succeeded for f in facts):
        return []
    empty = [
        f
        for f in facts
        if f.operation_id.startswith("schema.")
        and (f.row_count if f.row_count is not None else len(f.rows)) == 0
    ]
    if not empty:
        return []
    shown = ", ".join(f"lookup {f.sequence} ({f.operation_id})" for f in empty[:6])
    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            for part in subclauses(clause):
                subject = _SEARCHED_FOR.search(part) or _VISIBILITY_SUBJECT.search(part)
                # Inline errors and quoted statements are evidence, not absence claims.
                # Word-internal apostrophes belong to the single-quoted span; they
                # cannot open one in ordinary prose such as "the table isn't visible".
                predicates = re.sub(
                    r"`[^`\n]+`|\"[^\"\n]+\"|“[^”\n]+”|"
                    r"(?<!\w)'(?:[^'\n]|(?<=\w)'(?=\w))+'(?!\w)|‘[^’\n]+’",
                    lambda m: (
                        " " + m[0][1:-1] + " "
                        if re.fullmatch(r"`[\w$#]+`", m[0])
                        else " " * len(m[0])
                    ),
                    part,
                )
                structural = _negated_visibility(predicates)
                access = _ACCESS_LIMIT.search(predicates)
                # Missing information and conditional alternatives do not assert
                # the account-wide access state of a database object.
                if access and (
                    re.search(
                        r"\b(?:see|read)\s+(?:who|why|when|how|(?:the\s+)?intent)\b",
                        access[0] + predicates[access.end() :],
                        re.I,
                    )
                    or re.search(
                        r"\b(?:intent|provenance|history)\s*$",
                        predicates[: access.start()],
                        re.I,
                    )
                    or re.match(r"\s*(?:[-*]\s*)?if\b", line, re.I)
                    or re.search(r"\bor\s+(?:something|someone)\s+else\b", predicates, re.I)
                ):
                    access = None
                unseen = _UNSEEN.search(predicates) or structural or access
                if unseen is not None:
                    # An owner in an earlier observation cannot scope a later
                    # account-wide conclusion introduced by "so" or "and".
                    bounds = list(re.finditer(r"\b(?:and|so|but)\b", predicates[: unseen.start()]))
                    start = bounds[-1].end() if bounds else 0
                    end = re.search(r"\b(?:and|so|but)\b", predicates[unseen.end() :])
                    stop = unseen.end() + end.start() if end else len(predicates)
                    local = predicates[start:stop]
                    if _VISIBILITY_FACT.search(predicates):
                        continue
                    if (
                        (_VISIBILITY_PREDICATE.search(local) or access)
                        and _owner_scoped_visibility(local, empty, predicates[:start])
                    ) or _visibility_listing(local, facts):
                        continue
                subject = subject or structural or access
                if unseen is None or subject is None or unseen.start() - subject.end() > 180:
                    continue
                if re.match(r"\s+to\s+(?:the\s+)?optimizer\b", predicates[unseen.end() :], re.I):
                    continue
                if _ABSENCE_OPEN.search(
                    part[: unseen.end()]
                    + re.split(r"\b(?:and|so|but)\b", part[unseen.end() :], maxsplit=1)[0]
                ) or _ABSENCE_SCOPED.search(part[unseen.end() :]):
                    continue
                if _listed_absent(part, unseen, facts):
                    continue
                findings.append(
                    ClaimFinding(
                        "absence_inference",
                        _excerpt(part, subject.start(), unseen.end()),
                        f"{shown} returned no rows: a search that matches nothing shows only "
                        "that nothing matched its parameters in the scope searched. No lookup "
                        "established that the referent is invisible, "
                        "inaccessible or absent. Say it was not found in those searches, "
                        "naming their scope, and leave whether this account can see it "
                        "unknown.",
                    )
                )
                break
    return findings


_WRAPPED_COLUMN = re.compile(r'\bINTERNAL_FUNCTION\s*\(\s*"([^"]{1,128})"\s*\)', re.IGNORECASE)
_WRAPPED_BIND = re.compile(r"\bINTERNAL_FUNCTION\s*\(\s*:[\w$#]+", re.IGNORECASE)
_INTERNAL_FUNCTION = re.compile(r"\bINTERNAL[_ ]FUNCTION\b", re.IGNORECASE)
# What a bind is said to undergo: "the bind conversion", "converting the bind",
# "the bind's datatype", "a bind of type TIMESTAMP".
_BIND_CAUSE = re.compile(
    r"\bbind(?:\s+variable)?(?:'s)?\s+(?:value\s+)?(?:conver\w*|(?:data\s*)?types?|datatypes?|cast\w*)"
    r"|\b(?:conver\w*|cast\w*|coerc\w*)\s+(?:of\s+|on\s+|applied\s+to\s+)?(?:the\s+)?"
    r"(?::[\w$#]+|bind\w*)"
    r"|\bbind\w*\b[^.;]{0,40}\b(?:is|was|being)\s+(?:a\s+)?(?:TIMESTAMP|VARCHAR2|NUMBER|"
    r"converted|cast)\b",
    re.IGNORECASE,
)
# Said as a possibility, a condition or a check to make, the cause is not asserted:
# "may indicate", "possibly related to", "not proven", "check the bind's type if".
_CAUSE_HEDGED = re.compile(
    r"\b(?:may|might|could|can\s+appear|possibl\w*|perhaps|hypothes\w*|not\s+(?:proven|shown|"
    r"known|established)|unknown|if|whether|check|verify|confirm|compare)\b",
    re.IGNORECASE,
)
_CAUSE_DENIED = re.compile(
    r"\b(?:not|no|never|neither|nor|rather\s+than|instead\s+of|without|unknown|unshown|"
    r"unclear)\b[^.;]{0,50}$",
    re.IGNORECASE,
)


def plan_expression_claims(plan: Sequence[str], answer: str) -> list[ClaimFinding]:
    """INTERNAL_FUNCTION round a column, attributed to a bind conversion.

    Trial 24, TUNE-03: the plan's predicate is TRUNC(INTERNAL_FUNCTION("ORDER_DATE"))=:D,
    and the answer called INTERNAL_FUNCTION "the optimizer's internal representation of
    the bind conversion". The expression encloses the column, not :D, and no bind
    datatype was supplied. A plan whose INTERNAL_FUNCTION encloses a bind is not checked;
    saying what the function encloses, that its conversion is unknown, or that a bind
    conversion is a possibility to check, is not flagged.
    """

    text = "\n".join(plan)
    columns = list(dict.fromkeys(m[1] for m in _WRAPPED_COLUMN.finditer(text)))
    if not columns or _WRAPPED_BIND.search(text):
        return []
    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for clause in _clauses(line):
            if not _INTERNAL_FUNCTION.search(clause):
                continue
            # A hedge qualifies the part it is in (Sync Point 2K): in "INTERNAL_FUNCTION is
            # the bind conversion, although whether it helps performance is unknown" the
            # unknown is the performance, not the conversion.
            cause = next(
                (
                    (part, m)
                    for part in subclauses(clause)
                    if not _CAUSE_HEDGED.search(part)
                    for m in _BIND_CAUSE.finditer(part)
                    if not _CAUSE_DENIED.search(part[: m.start()])
                ),
                None,
            )
            if cause is None:
                continue
            part, m = cause
            findings.append(
                ClaimFinding(
                    "plan_expression",
                    _excerpt(part, m.start(), m.end()),
                    f'the supplied plan shows INTERNAL_FUNCTION("{columns[0]}"): it encloses '
                    "the column, not the bind, and the plan shows no bind datatype or "
                    "conversion. Say it wraps the column, and that which internal "
                    "conversion it stands for is not shown.",
                )
            )
    return findings


# Ordinary waiting is not an exception. Match concepts and their grammatical
# relationship, rather than particular case wording or a particular handler.
# Bare blocking/row locks remain eligible in exception claims, but references
# to blocking sessions or locks held/acquired by sessions are diagnostic prose.
_LOCK_WAIT = re.compile(
    r"\b(?:(?:row[- ]?)?lock[- ](?:waits?|waiting|conflicts?|contention)|"
    r"wait(?:s|ing)?\s+(?:on|for)\s+(?:a\s+|the\s+)?"
    r"(?:(?:TX\s+)?row[- ]?|TX\s+|table\s+)?locks?|"
    r"blocked\s+(?:by|on)\s+(?:a\s+|the\s+)?(?:row[- ]?)?lock|"
    r"blocked\s+(?:row[- ]?)?lock|row[- ]locks?|"
    r"blocking(?!\s+(?:sessions?|locks?|chains?|transactions?|SIDs?|holders?|"
    r"delet\w*|insert\w*|updat\w*)\b))\b",
    re.I,
)
_HANDLER_EFFECT = (
    r"absorb\w*|ignor\w*|eat(?:s|en|ing)?|"
    r"turn(?:s|ed)?\s+into\s+(?:a\s+)?(?:silent\s+)?success"
)
_EXCEPTION_CONTEXT = re.compile(
    rf"\b(?:{_HANDLER_EFFECT}|exceptions?|handlers?|errors?|problems?|failures?|"
    r"WHEN\s+OTHERS|swallow\w*|catch\w*|caught|"
    r"hid(?:e[sn]?|den|ing)|suppress\w*|mask\w*)\b",
    re.I,
)
_EXCEPTION_ACTION = re.compile(
    rf"\b(?:{_HANDLER_EFFECT}|rais\w*|throw\w*|catch\w*|caught|swallow\w*|hid(?:e[sn]?|den|ing)|"
    r"mask\w*|suppress\w*|discard\w*|includ\w*|examples?|like|e\.g(?=\.)|"
    r"for\s+(?:example|instance)|such\s+as|among\s+them|"
    r"(?:runtime|run-time|DML)\s+(?:errors?|failures?)|"
    r"(?:is|are)\s+(?:an?\s+)?(?:error|exception|failure)s?)\b",
    re.I,
)
_WAIT_DENIAL = re.compile(
    r"\b(?:(?P<local>without\s+(?:raising|throwing|causing|signalling|signaling|generating)|"
    r"free\s+of\s+errors?)|no\s+(?:exception|error)|(?:does|do|would|will|can)\s+not\s+"
    r"(?:rais\w*|throw\w*|catch\w*|hide|swallow\w*)|"
    r"(?:is|are)\s+not\s+(?:an?\s+)?(?:errors?|exceptions?|failures?)|"
    rf"(?:never|not)\s+(?:{_HANDLER_EFFECT}|raised|caught|hidden|swallowed)|"
    r"not\s+an?\s+(?:error|exception))\b",
    re.I,
)


def lock_wait_exception_claims(answer: str) -> list[ClaimFinding]:
    """Flag waiting presented as an exception, including an introduced bullet list.

    A list inherits its introduction until the next non-list paragraph or heading.
    Qualified lock timeouts and explicit denials remain ordinary Oracle prose.
    Questions do not assert an exception. Fenced source is never checked.
    """
    findings: list[ClaimFinding] = []
    introduction = ""
    previous = ""
    for line in _prose_lines(answer):
        bullet = bool(re.match(r"^(?:[-*+] |\d+[.)] )", line))
        if line.startswith("#"):
            introduction = previous = ""
            continue
        if not bullet:
            introduction = ""
        elif not introduction:
            introduction = previous if previous.endswith(":") else ""
        # Keep the dotted enumerator with its examples in this local check.
        sentences = re.split(r"(?<!e\.g\.)(?<=[.!?])\s+", line, flags=re.I)
        for clause in (part for sentence in sentences for part in subclauses(sentence)):
            context = introduction + " " + clause
            waits = list(_LOCK_WAIT.finditer(clause))
            waits = [
                m
                for m in waits
                if not re.search(r"\bno\s+$", clause[: m.start()], re.I)
                and not (
                    re.fullmatch(r"row[- ]locks?", m.group(), re.I)
                    and (
                        re.search(
                            r"\b(?:holds?|held|holding|acquir\w*|tak\w*|taken|uses?|using)"
                            r"(?:\s+\w+){0,2}\s+$",
                            clause[: m.start()],
                            re.I,
                        )
                        or re.match(
                            r"\s+(?:(?:are|were|being)\s+)?(?:held|acquired|taken)\b",
                            clause[m.end() :],
                            re.I,
                        )
                    )
                )
                and not re.match(
                    r"\s+(?:ending\s+in\s+errors|that\s+(?:exceed|reach)\s+(?:a\s+)?timeout)",
                    clause[m.end() :],
                    re.I,
                )
            ]
            if not waits:
                continue
            if (
                "?" in context
                # An introduction's "completes without raising" describes the
                # handler's result, not whether each listed condition raises.
                or any(
                    m.lastgroup != "local" or m.start() >= len(introduction)
                    for m in _WAIT_DENIAL.finditer(context)
                )
                or re.search(r"\b(?:whether|unknown|unclear)\b", context, re.I)
                or not _EXCEPTION_CONTEXT.search(context)
                or not _EXCEPTION_ACTION.search(context)
            ):
                continue
            # An explicit timeout/error qualification distinguishes an exception
            # from bare waiting. Do not excuse a list's separate ordinary wait.
            if re.search(r"ORA-(?:00054|30006|00060)|DDL_LOCK_TIMEOUT", clause, re.I):
                if re.search(
                    r"\b(?:timeout|times?\s+out|NOWAIT|WAIT\s+(?:n|\d+)|deadlock)\b", clause, re.I
                ):
                    continue
            findings.append(
                ClaimFinding(
                    "lock_wait_exception",
                    context.strip(),
                    "An ordinary enqueue or row-lock wait blocks the session and raises no "
                    "exception for a handler to catch or hide. List only raised conditions: "
                    "ORA-00054 for NOWAIT or busy DDL (including DDL_LOCK_TIMEOUT expiry), "
                    "ORA-30006 for SELECT FOR UPDATE WAIT n timeout, and ORA-00060 for a "
                    "deadlock in one involved session, rolling back only that statement.",
                )
            )
        previous = line
    return findings


# -- application-error numbers and compiler-error positions (K13) ----------------------


def application_error_claims(answer: str, *, code: bool = False) -> list[ClaimFinding]:
    """Check literal arguments, leaving constants, binds and expressions unchecked.

    Executable source is lexed before inspection; prose includes inline code, while
    explicit warnings about invalid calls are not recommendations to use those calls.
    """
    from harness_worker.statement import strip_literals_and_comments

    segments = (
        [(answer, True)]
        if code
        else [
            (sentence, False)
            for line in _prose_lines(answer)
            for sentence in re.split(r"(?<=[.!?])\s+", line)
        ]
    )
    if not code:
        segments += [(block.text, True) for block in fenced_blocks(answer)]
    findings: list[ClaimFinding] = []
    for text, source in segments:
        inspected = strip_literals_and_comments(text) if source else text
        warning_text = strip_literals_and_comments(re.sub(r"(?<=\w)'(?=\w)", "’", text))
        if source:
            inspected = re.sub(r'"(?:[^"\n]|"")*"', lambda m: " " * len(m[0]), inspected)
        for match in re.finditer(
            r"\bRAISE_APPLICATION_ERROR\s*\(\s*([+-]?\s*\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(?=,|\))",
            inspected,
            re.I,
        ):
            value = Decimal(re.sub(r"\s", "", match[1]))
            if -20999 <= value <= -20000:
                continue
            if not source and re.search(
                r"\b(?:invalid|illegal|outside|out.of.range|incorrect|wrong|"
                r"do\s+not|don['’]t|never|(?:should|must|may|can)\s+not|cannot|can['’]t|"
                r"not\s+(?:valid|allowed|permitted|supported|legal|acceptable)|"
                r"(?:must|should|need(?:s)?\s+to)\s+be\s+negative|ORA-21000)\b",
                warning_text,
                re.I,
            ):
                continue
            findings.append(
                ClaimFinding(
                    "application_error_number",
                    text.strip(),
                    f"RAISE_APPLICATION_ERROR's literal {match[1].strip()} is outside "
                    "-20999..-20000; Oracle raises ORA-21000. Use a number in that range.",
                )
            )
    return findings


def compiler_error_position_claims(answer: str, facts: Sequence[LookupFacts]) -> list[ClaimFinding]:
    """Compare a collective position claim with the returned ALL_ERRORS positions.

    A shared line alone is not a shared position. Rows from separate objects or types
    are never combined to manufacture a contradiction.
    """
    groups: dict[tuple[str, ...], set[tuple[str, str]]] = {}
    for fact in facts:
        if not fact.succeeded or fact.operation_id != "schema.object_errors":
            continue
        columns = [c.upper() for c in fact.columns]
        if "LINE" not in columns or "POSITION" not in columns:
            continue
        for row in fact.rows:
            if len(row) < len(columns):
                continue
            values = dict(zip(columns, row, strict=True))
            key = tuple(
                str(values.get(c, fact.parameters.get(p, ""))).upper()
                for c, p in (("OWNER", "owner"), ("NAME", "object_name"), ("TYPE", "object_type"))
            )
            if values["LINE"] is not None and values["POSITION"] is not None:
                groups.setdefault(key, set()).add((str(values["LINE"]), str(values["POSITION"])))
    findings: list[ClaimFinding] = []
    for line in _prose_lines(answer):
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if not re.search(
                r"\b(?:(?:both|all|each|every)\s+(?:of\s+)?(?:the\s+)?"
                r"(?:(?:reported|returned|recorded|listed|retained|two|\d+)\s+)?"
                r"(?:compiler\s+|compilation\s+)?errors?|errors?\s+(?:both|all|each))\b",
                sentence,
                re.I,
            ):
                continue
            if re.search(
                r"\b(?:never|different|distinct|whether|unknown)\b|\?|\bnot\s+(?:all|both|each|every|at|on)\b",
                sentence,
                re.I,
            ):
                continue
            positions = re.findall(
                r"\bline\s+(\d+)\s*[,;:]?\s*(?:position|column|col\.?)\s+(\d+)"
                r"|\b(\d+)\s*:\s*(\d+)\b",
                sentence,
                re.I,
            )
            claimed = {(a or c, b or d) for a, b, c, d in positions}
            if len(claimed) != 1:
                continue
            location = re.search(r"\bline\s+\d+|\b\d+\s*:\s*\d+", sentence, re.I)
            if location and re.search(r"\bnot\b", sentence[: location.start()], re.I):
                continue
            candidates = (
                groups
                if len(groups) == 1
                else {k: v for k, v in groups.items() if k[1] and k[1] in sentence.upper()}
            )
            if len(candidates) != 1:
                continue
            key, retained = next(iter(candidates.items()))
            if len(retained) < 2:
                continue
            positions_shown = ", ".join(f"{a}:{b}" for a, b in sorted(retained))
            findings.append(
                ClaimFinding(
                    "compiler_error_position",
                    sentence.strip(),
                    f"The retained compiler errors for {'.'.join(k for k in key if k)} "
                    f"have distinct line:position values: {positions_shown}. "
                    "Do not place all errors at one position.",
                )
            )
    return findings
