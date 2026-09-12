"""The evaluation case format, and the rules a case set has to satisfy before a run.

A case set is one JSON file. Everything that decides whether a run qualified is fixed
in it before any provider is called: which cases count towards the correctness bar,
the size of that denominator, which cases are authorization or no-automatic-execution
gates, and which cases need a DBA's judgment rather than a structural check.

Format 1
--------

Top level: ``format`` (1), ``caseSetVersion``, ``correctnessDenominator``, ``defaults``
(target reference, database version and schema applied to every request) and
``cases``. Each case has:

``id``
    Stable identifier. Reports and reviews refer to it.
``category``
    One of :data:`CATEGORIES`.
``gates``
    ``"correctness"`` (explain and fix cases, scored against the 90% bar) and/or
    ``"safety"`` (authorization and no-automatic-execution cases, which must all pass).
``review``
    ``"dba"`` when the answer needs a person's judgment, ``"automated"`` when the whole
    pass condition is structural -- a refusal, a 404, a refused stale diff. Every
    correctness case and every case whose outcome is a model answer judged on its
    content is ``"dba"``.
``request``
    ``action``, ``userMessage``, ``attachments`` (category, name, content -- a string or
    a list of lines -- and provenance),
    an optional ``editor`` (``editorId``, ``revision`` and ``textFrom``, the name of the
    attachment whose content is the buffer) and ``credential``: ``developer``,
    ``integration`` or ``integration_without_scope``.
``permittedContext``
    The context categories this case may send. The runner checks what the harness
    reports it sent against it.
``expect``
    ``outcome`` (``answer`` or ``refused``), ``refusalCode``, ``proposal``
    (``required``, ``forbidden`` or ``optional``), ``applyCheck`` and regular expressions
    the answer or proposal must not match. These are necessary conditions only: a case
    that satisfies them still needs its review.
``expectedBehavior`` and ``rubric``
    What the reviewer judges the answer against.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness_api.copilot import ACTIONS, ContextCategory
from harness_api.copilot.context import FORBIDDEN_CATEGORIES

FORMAT_VERSION = 1

DEFAULT_CASE_FILE = Path(__file__).with_name("cases.json")

CATEGORIES = (
    "explain",
    "fix",
    "draft",
    "test_block",
    "tuning",
    "inaccessible",
    "stale_source",
    "embedded_instructions",
    "authorization",
)

# Categories whose answers are judged on content. They can never be marked automated.
REVIEWED_CATEGORIES = frozenset(
    {"explain", "fix", "draft", "test_block", "tuning", "inaccessible", "embedded_instructions"}
)
CORRECTNESS_CATEGORIES = frozenset({"explain", "fix"})
SAFETY_CATEGORIES = frozenset({"authorization", "stale_source", "embedded_instructions"})
CREDENTIALS = ("developer", "integration", "integration_without_scope")
PROPOSAL_EXPECTATIONS = ("required", "forbidden", "optional")
APPLY_CHANGES = ("none", "text", "revision", "target")
APPLY_ACTORS = ("developer", "dba")


class CaseSetError(ValueError):
    """The case set is malformed. Nothing is run against a case set that fails this."""


@dataclass(frozen=True)
class ApplyCheck:
    actor: str = "developer"
    change: str = "none"
    can_apply: bool | None = None
    status: int = 200


@dataclass(frozen=True)
class Expectation:
    outcome: str
    refusal_code: str = ""
    proposal: str = "optional"
    apply_check: ApplyCheck | None = None
    answer_must_not_match: tuple[str, ...] = ()
    proposal_must_not_match: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    gates: frozenset[str]
    review: str
    action: str
    user_message: str
    attachments: tuple[dict[str, str], ...]
    editor: dict[str, str] | None
    credential: str
    permitted_context: frozenset[str]
    expect: Expectation
    expected_behavior: str
    rubric: tuple[str, ...]
    target_reference: str
    database_version: str
    schema: str

    @property
    def dispatches(self) -> bool:
        """Whether a correct harness sends this case to the provider at all."""

        return self.expect.outcome == "answer"

    @property
    def sent_categories(self) -> frozenset[str]:
        """What leaves the harness if the case is handled correctly."""

        if not self.dispatches:
            return frozenset()
        sent = {a["category"] for a in self.attachments}
        if self.user_message.strip():
            sent.add(ContextCategory.USER_MESSAGE.value)
        return frozenset(sent)

    def request_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "targetReference": self.target_reference,
            "userMessage": self.user_message,
            "databaseVersion": self.database_version,
            "schema": self.schema,
            "attachments": [dict(a) for a in self.attachments],
        }
        if self.editor is not None:
            payload["editor"] = dict(self.editor)
        if self.credential.startswith("integration"):
            payload["actorReference"] = "evaluation-user"
        return payload


@dataclass(frozen=True)
class CaseSet:
    path: Path
    version: str
    sha256: str
    correctness_denominator: int
    cases: tuple[EvalCase, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, Any]:
        by_category: dict[str, int] = {}
        for case in self.cases:
            by_category[case.category] = by_category.get(case.category, 0) + 1
        return {
            "path": self.path.as_posix(),
            "version": self.version,
            "sha256": self.sha256,
            "caseCount": len(self.cases),
            "dispatchedCaseCount": sum(1 for c in self.cases if c.dispatches),
            "correctnessDenominator": self.correctness_denominator,
            "safetyCaseCount": sum(1 for c in self.cases if "safety" in c.gates),
            "byCategory": dict(sorted(by_category.items())),
        }


def load_case_set(path: Path = DEFAULT_CASE_FILE) -> CaseSet:
    raw_bytes = path.read_bytes()
    try:
        document = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise CaseSetError(f"{path} is not valid JSON: {exc}") from exc
    # Hashed with LF line endings, so a checkout on Windows names the same case set.
    digest = hashlib.sha256(raw_bytes.replace(b"\r\n", b"\n")).hexdigest()
    return parse_case_set(document, path=path, sha256=digest)


def parse_case_set(document: Any, *, path: Path, sha256: str) -> CaseSet:
    if not isinstance(document, dict):
        raise CaseSetError("A case set is a JSON object.")
    if document.get("format") != FORMAT_VERSION:
        raise CaseSetError(
            f"Unsupported case format {document.get('format')!r}; this runner reads "
            f"format {FORMAT_VERSION}."
        )
    version = _text(document, "caseSetVersion", where="case set")
    defaults = document.get("defaults") or {}
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CaseSetError("The case set has no cases.")

    cases = tuple(_parse_case(raw, defaults) for raw in raw_cases)

    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise CaseSetError(f"Case id {case.id!r} appears more than once.")
        seen.add(case.id)

    declared = document.get("correctnessDenominator")
    counted = sum(1 for case in cases if "correctness" in case.gates)
    if declared != counted:
        raise CaseSetError(
            f"correctnessDenominator is {declared!r} but {counted} cases carry the "
            "correctness gate. The denominator is fixed before a run; change both "
            "together, deliberately."
        )
    return CaseSet(
        path=path,
        version=version,
        sha256=sha256,
        correctness_denominator=counted,
        cases=cases,
    )


def _parse_case(raw: Any, defaults: dict[str, Any]) -> EvalCase:
    if not isinstance(raw, dict):
        raise CaseSetError("Every case is a JSON object.")
    case_id = _text(raw, "id", where="case")
    where = f"case {case_id}"

    category = _text(raw, "category", where=where)
    if category not in CATEGORIES:
        raise CaseSetError(f"{where}: unknown category {category!r}.")

    gates = frozenset(raw.get("gates") or ())
    if not gates <= {"correctness", "safety"}:
        raise CaseSetError(f"{where}: unknown gate in {sorted(gates)}.")
    if ("correctness" in gates) != (category in CORRECTNESS_CATEGORIES):
        raise CaseSetError(f"{where}: the correctness gate is exactly the explain and fix cases.")
    if ("safety" in gates) != (category in SAFETY_CATEGORIES):
        raise CaseSetError(
            f"{where}: the safety gate is exactly the authorization, stale-source and "
            "embedded-instruction cases."
        )

    review = _text(raw, "review", where=where)
    if review not in ("dba", "automated"):
        raise CaseSetError(f"{where}: review must be 'dba' or 'automated'.")
    if review == "automated" and category in REVIEWED_CATEGORIES:
        raise CaseSetError(
            f"{where}: {category} answers are judged on content and cannot be "
            "marked for automated review."
        )

    request = raw.get("request")
    if not isinstance(request, dict):
        raise CaseSetError(f"{where}: request is required.")
    action = _text(request, "action", where=where)
    if action not in ACTIONS:
        raise CaseSetError(f"{where}: unknown action {action!r}.")
    credential = request.get("credential", "developer")
    if credential not in CREDENTIALS:
        raise CaseSetError(f"{where}: unknown credential {credential!r}.")

    attachments = []
    for attachment in request.get("attachments") or ():
        if not isinstance(attachment, dict):
            raise CaseSetError(f"{where}: every attachment is an object.")
        attachments.append(
            {
                "category": _text(attachment, "category", where=where),
                "name": _text(attachment, "name", where=where),
                "content": _content(attachment, where=where),
                "provenance": str(attachment.get("provenance", "evaluation case")),
            }
        )

    editor = None
    if request.get("editor") is not None:
        spec = request["editor"]
        source = _text(spec, "textFrom", where=where)
        match = [a for a in attachments if a["name"] == source]
        if len(match) != 1:
            raise CaseSetError(f"{where}: editor.textFrom names no single attachment.")
        editor = {
            "editorId": _text(spec, "editorId", where=where),
            "revision": _text(spec, "revision", where=where),
            "text": match[0]["content"],
        }

    expect = _parse_expectation(raw.get("expect"), where=where, has_editor=editor is not None)

    permitted = frozenset(raw.get("permittedContext") or ())
    known = {c.value for c in ContextCategory}
    if not permitted <= known:
        raise CaseSetError(f"{where}: permittedContext names unknown categories.")

    case = EvalCase(
        id=case_id,
        category=category,
        gates=gates,
        review=review,
        action=action,
        user_message=str(request.get("userMessage", "")),
        attachments=tuple(attachments),
        editor=editor,
        credential=credential,
        permitted_context=permitted,
        expect=expect,
        expected_behavior=_text(raw, "expectedBehavior", where=where),
        rubric=tuple(str(item) for item in raw.get("rubric") or ()),
        target_reference=str(request.get("targetReference", defaults.get("targetReference", ""))),
        database_version=str(request.get("databaseVersion", defaults.get("databaseVersion", ""))),
        schema=str(request.get("schema", defaults.get("schema", ""))),
    )

    if case.dispatches:
        if not case.sent_categories <= permitted:
            raise CaseSetError(
                f"{where}: the request sends {sorted(case.sent_categories - permitted)}, "
                "which permittedContext does not allow."
            )
        forbidden = {a["category"] for a in attachments} & set(FORBIDDEN_CATEGORIES)
        if forbidden:
            raise CaseSetError(
                f"{where}: a case expecting an answer cannot carry {sorted(forbidden)}."
            )
    if review == "dba" and not case.rubric:
        raise CaseSetError(f"{where}: a reviewed case needs a rubric.")
    if not case.target_reference:
        raise CaseSetError(f"{where}: no target reference, and no default.")
    return case


def _parse_expectation(raw: Any, *, where: str, has_editor: bool) -> Expectation:
    if not isinstance(raw, dict):
        raise CaseSetError(f"{where}: expect is required.")
    outcome = _text(raw, "outcome", where=where)
    if outcome not in ("answer", "refused"):
        raise CaseSetError(f"{where}: expect.outcome must be 'answer' or 'refused'.")
    refusal_code = str(raw.get("refusalCode", ""))
    if outcome == "refused" and not refusal_code:
        raise CaseSetError(f"{where}: a refusal names the error code it expects.")

    proposal = raw.get("proposal", "optional")
    if proposal not in PROPOSAL_EXPECTATIONS:
        raise CaseSetError(f"{where}: unknown proposal expectation {proposal!r}.")
    if proposal == "required" and not has_editor:
        raise CaseSetError(f"{where}: a proposal needs an editor buffer to be pinned to.")

    apply_check = None
    if raw.get("applyCheck") is not None:
        spec = raw["applyCheck"]
        if proposal != "required":
            raise CaseSetError(f"{where}: an apply check needs a required proposal.")
        apply_check = ApplyCheck(
            actor=spec.get("actor", "developer"),
            change=spec.get("change", "none"),
            can_apply=spec.get("canApply"),
            status=int(spec.get("status", 200)),
        )
        if apply_check.actor not in APPLY_ACTORS or apply_check.change not in APPLY_CHANGES:
            raise CaseSetError(f"{where}: unknown apply check actor or change.")
        if (apply_check.status == 200) == (apply_check.can_apply is None):
            raise CaseSetError(
                f"{where}: an apply check expects either canApply (status 200) or a "
                "non-200 status, not both."
            )

    patterns: dict[str, tuple[str, ...]] = {}
    for key in ("answerMustNotMatch", "proposalMustNotMatch"):
        values = tuple(str(p) for p in raw.get(key) or ())
        for pattern in values:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise CaseSetError(f"{where}: {key} pattern {pattern!r}: {exc}") from exc
        patterns[key] = values

    return Expectation(
        outcome=outcome,
        refusal_code=refusal_code,
        proposal=proposal,
        apply_check=apply_check,
        answer_must_not_match=patterns["answerMustNotMatch"],
        proposal_must_not_match=patterns["proposalMustNotMatch"],
    )


def _content(raw: dict[str, Any], *, where: str) -> str:
    """Attachment content, as a string or, for a reviewer's sake, a list of lines."""

    value = raw.get("content")
    if isinstance(value, list) and value and all(isinstance(line, str) for line in value):
        return "\n".join(value) + "\n"
    return _text(raw, "content", where=where)


def _text(raw: dict[str, Any], key: str, *, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CaseSetError(f"{where}: {key} is required.")
    return value
