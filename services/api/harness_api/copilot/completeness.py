"""Whether a plain explanation gives the remedy its task asks for (C35).

The explain task (``context.ACTION_INSTRUCTIONS["explain"]``) names two risks whose
remedy the answer must give: a handler that swallows an error ("the remedy is to log
it and re-raise it") and an ordering that decides which rows win ("say how ties are
broken"). Trial 20's EXPLAIN-06 answer described the swallowing handler, said it "does
not log the error, re-raise it", and recommended nothing: the description of what the
code lacks is not the remedy.

``obligations`` finds, in the supplied source, the structures those clauses are about:

- ``swallowed_error``: an exception handler none of whose statements raises (no
  ``RAISE`` or ``RAISE_APPLICATION_ERROR`` before the next handler or the block's end).
  A handler that re-raises, on any path, owes nothing.
- ``row_ties``: a ranking (``ROW_NUMBER``, ``RANK``, ``DENSE_RANK``) or a row limit
  (``FETCH FIRST``, ``ROWNUM``) decided by an ``ORDER BY``.
- ``transaction_state`` (Sync Point 2J, trial 24 EXPLAIN-06): two or more DML
  statements in a block whose handler swallows the error and neither raises nor rolls
  back, with no COMMIT or ROLLBACK in the block. When the first statement succeeds and a
  later one fails, Oracle rolls back only the failing statement, so the earlier rows stay
  in the caller's transaction and the caller can commit them.

``missing_remedies`` then asks of the answer only what the task asks: a sentence that
recommends re-raising (a directive, or replacement code whose every handler raises on
every path), a sentence that says how ties are broken, or one that says the earlier
statement's rows remain in the transaction (or can be committed with the later
statement's work missing). Remarks about other sessions changing rows between the
statements are about concurrency, not this state, and do not count. A sentence that
says the code does not re-raise describes the defect; it is not a recommendation. An
answer that says no change is recommended, and why, has answered a remedy (not the
transaction state). The finding names the obligation and quotes the task; it does not
write the remedy, so the harness recommends nothing of its own.

The source is scanned with comments and literals blanked (``support.code_lines``), so a
handler in a comment owes nothing. No case IDs, rubric text or fixture values are used.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from harness_api.copilot.claim_checks import ClaimFinding
from harness_api.copilot.fences import fenced_blocks
from harness_api.copilot.support import clauses, code_lines, prose_lines

# Bounded work for any input (C27).
_MAX_SOURCE_LINES = 4000
_MAX_CLAUSES = 400

# The task clauses each obligation comes from, quoted in the finding.
TASK_CLAUSES = {
    "swallowed_error": "a handler that swallows an error (the remedy is to log it and re-raise it)",
    "row_ties": "an ordering that decides which rows win (say how ties are broken)",
    "transaction_state": (
        "a handler that swallows an error after more than one change (say what the "
        "transaction holds when a later statement fails)"
    ),
}
# The actions whose instructions carry those clauses.
ACTIONS = frozenset({"explain"})


@dataclass(frozen=True)
class Obligation:
    kind: str
    line: int  # 1-based, in the supplied source as joined
    text: str  # the source line


_RANKING = re.compile(
    r"\b(?:ROW_NUMBER|RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\([^)]*\bORDER\s+BY\b"
    r"|\bFETCH\s+(?:FIRST|NEXT)\b|\bROWNUM\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Handler:
    line: int  # 1-based
    raises: bool  # some statement of the handler raises, on some path
    propagates: bool  # a raise at the handler's own level, reached on every path


# The words that open or close a nesting level, and those a handler's path depends on.
_TOKEN = re.compile(
    r"\bEND\s+(?:IF|LOOP|CASE)\b|\bRAISE_APPLICATION_ERROR\b|\bEXCEPTION\s*;"
    r"|\b(?:BEGIN|IF|CASE|LOOP|END|EXCEPTION|WHEN|THEN|RAISE|RETURN|GOTO|EXIT|CONTINUE)\b",
    re.IGNORECASE,
)


# The labels written just before a loop, with its FOR or WHILE header between:
# "<<outer>> FOR i IN 1 .. 3 LOOP". Matched against the text before the LOOP keyword.
_LOOP_LABELS = re.compile(
    r"((?:<<\s*[A-Za-z][\w$#]*\s*>>\s*)+)(?:(?:FOR|WHILE)\b(?:(?!<<|>>)[^;])*)?$", re.IGNORECASE
)
_LABEL = re.compile(r"<<\s*([A-Za-z][\w$#]*)\s*>>")
# The loop an EXIT or CONTINUE names, if it names one: "EXIT outer WHEN done;".
_TARGET = re.compile(r"\s+(?!WHEN\b)([A-Za-z][\w$#]*)", re.IGNORECASE)
_LABEL_WINDOW = 1000


@dataclass
class _Frame:
    kind: str  # block, if, case or loop
    in_handlers: bool = False
    labels: tuple[str, ...] = ()  # a loop's labels, upper case


@dataclass
class _Open:
    line: int
    depth: int  # the stack depth of the handler's own statements
    started: bool = False  # past its THEN
    raises: bool = False
    propagates: bool = False
    leaves: bool = False  # a RETURN, GOTO, EXIT or CONTINUE that can skip a raise came first


def _stays_inside(inner: Sequence[_Frame], joined: str, after: int) -> bool:
    """Whether the EXIT or CONTINUE ending at ``after`` transfers to a loop among the
    ``inner`` frames, those opened inside the handler. Unlabelled, it names the
    innermost loop; labelled, the loop with that label, which can be one around the
    handler (Sync Point 1C), and an unknown label is taken to leave."""

    loops = [f for f in inner if f.kind == "loop"]
    target = _TARGET.match(joined, after)
    if target is None:
        return bool(loops)
    return any(target[1].upper() in f.labels for f in loops)


def handlers(lines: Sequence[str]) -> list[Handler]:
    """Each exception handler in ``lines`` (comments and literals blanked), whether any
    of its statements raises, and whether it propagates on every path.

    A RAISE before the EXCEPTION keyword, or in another handler, is not this handler's
    (C35-A). A RAISE inside an IF, CASE, loop or nested block of the handler is
    conditional: ``WHEN OTHERS THEN IF 1=0 THEN RAISE; ELSE NULL; END IF;`` raises on no
    path that is known, so it does not propagate (C35-A, Sync Point 1 finding 3). Nor
    does a RAISE after a RETURN or GOTO at any depth of the handler, since a branch that
    returns or jumps can skip it (Sync Point 1B), nor one after an EXIT or CONTINUE that
    leaves a loop around the handler, whether it is unlabelled outside any inner loop
    or names a label that no inner loop carries (Sync Point 1C)."""

    code, _ = code_lines(list(lines[:_MAX_SOURCE_LINES]))
    joined = "\n".join(code)
    starts = [0]
    for text in code:
        starts.append(starts[-1] + len(text) + 1)

    def line_of(offset: int) -> int:
        return next(i for i in range(len(code)) if starts[i + 1] > offset) + 1

    stack: list[_Frame] = [_Frame("block")]
    found: list[Handler] = []
    # The handlers being read, outermost first: a handler can hold a block with its own.
    open_: list[_Open] = []
    prior = ""

    def close(depth: int) -> None:
        """Close every open handler at ``depth`` or deeper."""
        while open_ and open_[-1].depth >= depth:
            done = open_.pop()
            found.append(Handler(done.line, done.raises, done.propagates))

    for token in _TOKEN.finditer(joined):
        word = " ".join(token[0].upper().split())
        if word.startswith("EXCEPTION") and word != "EXCEPTION":
            continue  # a declaration: "e_failed EXCEPTION;"
        close(len(stack) + 1)
        if word.startswith("END "):
            if len(stack) > 1:
                stack.pop()
        elif word == "END":
            # A block's END, or a CASE expression's: either closes the innermost level.
            close(len(stack))
            if len(stack) > 1:
                stack.pop()
            else:
                stack[0] = _Frame("block")
        elif word == "LOOP":
            before = _LOOP_LABELS.search(
                joined[max(0, token.start() - _LABEL_WINDOW) : token.start()]
            )
            labels = tuple(m.upper() for m in _LABEL.findall(before[1])) if before else ()
            stack.append(_Frame("loop", labels=labels))
        elif word in ("BEGIN", "IF", "CASE"):
            stack.append(_Frame("block" if word == "BEGIN" else word.lower()))
        elif word == "EXCEPTION":
            if stack[-1].kind == "block":
                stack[-1].in_handlers = True
        elif word == "WHEN":
            if prior in ("EXIT", "CONTINUE"):
                pass  # "EXIT WHEN done;" is a statement, not a handler
            elif stack[-1].kind == "block" and stack[-1].in_handlers:
                close(len(stack))
                open_.append(_Open(line_of(token.start()), len(stack)))
        elif word == "THEN":
            if open_ and open_[-1].depth == len(stack):
                open_[-1].started = True
        else:
            for handler in open_:
                if not handler.started:
                    continue
                own_level = len(stack) == handler.depth
                if word in ("RAISE", "RAISE_APPLICATION_ERROR"):
                    handler.raises = True
                    if own_level and not handler.leaves:
                        handler.propagates = True
                elif handler.propagates:
                    pass  # after the raise: never reached
                elif word in ("RETURN", "GOTO"):
                    # At any depth: a RETURN or GOTO in a branch can skip the later raise
                    # (Sync Point 1B).
                    handler.leaves = True
                elif not _stays_inside(stack[handler.depth :], joined, token.end()):
                    handler.leaves = True  # EXIT or CONTINUE out of a loop around it
        prior = word
    close(0)
    return sorted(found, key=lambda h: h.line)


def obligations(source: Sequence[str]) -> list[Obligation]:
    """The remedies the explain task asks for, from the structure of ``source``."""

    lines = list(source[:_MAX_SOURCE_LINES])
    code, _ = code_lines(lines)
    joined = "\n".join(code)
    found = [
        Obligation("swallowed_error", h.line, lines[h.line - 1].strip())
        for h in handlers(lines)
        if not h.raises
    ]
    starts = [0]
    for text in code:
        starts.append(starts[-1] + len(text) + 1)

    def line_of(offset: int) -> int:
        return next(i for i in range(len(code)) if starts[i + 1] > offset) + 1

    ranking = _RANKING.search(joined)
    if ranking and re.search(r"\bORDER\s+BY\b", joined, re.IGNORECASE):
        at = line_of(ranking.start())
        found.append(Obligation("row_ties", at, lines[at - 1].strip()))
    swallowing = {h.line for h in handlers(lines) if not h.raises}
    for body, handler_lines in _blocks(joined):
        dml = list(_DML.finditer(body))
        if len(dml) < 2 or _TRANSACTION_CONTROL.search(body):
            continue
        owned = [(line_of(at), text) for at, text in handler_lines if line_of(at) in swallowing]
        owners = [line for line, _ in owned]
        bodies = [text for _, text in owned]
        if owners and not any(_TRANSACTION_CONTROL.search(b) for b in bodies):
            found.append(Obligation("transaction_state", owners[0], lines[owners[0] - 1].strip()))
    return found


# A data change: INSERT INTO / ALL / FIRST, UPDATE t [alias] SET, DELETE [FROM] t,
# MERGE INTO. "SELECT ... FOR UPDATE" has no SET and is not one.
_DML = re.compile(
    r"\bINSERT\s+(?:INTO|ALL|FIRST)\b|\bUPDATE\s+[\w$#.\"]+(?:\s+[\w$#]+)?\s+SET\b"
    r"|\bDELETE\s+(?:FROM\s+)?[A-Za-z\"][\w$#.\"]*|\bMERGE\s+INTO\b",
    re.IGNORECASE,
)
_TRANSACTION_CONTROL = re.compile(r"\b(?:COMMIT|ROLLBACK)\b", re.IGNORECASE)
_BLOCK_TOKEN = re.compile(
    r"\bEND\s+(?:IF|LOOP|CASE)\b|\bEXCEPTION\s*;"
    r"|\b(?:BEGIN|CASE|END|EXCEPTION|WHEN|EXIT|CONTINUE)\b",
    re.IGNORECASE,
)


def _blocks(joined: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Each block with an EXCEPTION section: its statements (BEGIN to EXCEPTION, nested
    blocks included) and each handler's offset and text (WHEN to the next handler or
    the block's END)."""

    found: list[tuple[str, list[tuple[int, str]]]] = []
    # Open levels: [kind, BEGIN end offset, EXCEPTION offset or -1, handler offsets].
    stack: list[list] = []
    prior = ""
    for token in _BLOCK_TOKEN.finditer(joined):
        word = " ".join(token[0].upper().split())
        if word.startswith("END ") or word.startswith("EXCEPTION") and word != "EXCEPTION":
            if word == "END CASE" and stack and stack[-1][0] == "case":
                stack.pop()
            prior = word
            continue
        if word == "BEGIN":
            stack.append(["block", token.end(), -1, []])
        elif word == "CASE":
            stack.append(["case", token.end(), -1, []])
        elif word == "EXCEPTION":
            if stack and stack[-1][0] == "block":
                stack[-1][2] = token.start()
        elif word == "WHEN":
            in_handlers = stack and stack[-1][0] == "block" and stack[-1][2] >= 0
            if in_handlers and prior not in ("EXIT", "CONTINUE"):
                stack[-1][3].append(token.start())
        elif word == "END" and stack:
            kind, begin, exception, whens = stack.pop()
            if kind == "block" and exception >= 0 and whens:
                ends = [*whens[1:], token.start()]
                bodies = [(w, joined[w:e]) for w, e in zip(whens, ends, strict=True)]
                found.append((joined[begin:exception], bodies))
        prior = word
    return found


# -- the answer ------------------------------------------------------------------------

_RERAISE = re.compile(
    r"\bre-?rais\w*|\bRAISE(?:_APPLICATION_ERROR)?\b|\bpropagat\w*|\blet\s+(?:the|it|errors?)\b"
    r"[^.]{0,30}\b(?:reach|surface|escape|bubble)\b",
    re.IGNORECASE,
)
# A sentence that tells the reader what to do: a modal, a recommendation word, or an
# imperative at its start.
_DIRECTIVE = re.compile(
    r"\b(?:should|must|recommend\w*|suggest\w*"
    r"|(?:fix|remedy|repair|solution)\s+(?:is|would\s+be|here\s+is)\b"
    # "The remedy for the swallowed-error problem is ..." (trial 29 EXPLAIN-06).
    r"|(?:fix|remedy|repair|solution)\s+(?:for|to)\s+[^.]{0,60}?\b(?:is|would\s+be)\b"
    r"|(?:better|right|correct|recommended|proper)\s+(?:change|fix|approach|pattern)\s+"
    r"(?:is|would\s+be)\b"
    r"|instead\b|replace\b|need(?:s)?\s+to|better\s+to|at\s+minimum|at\s+least)\b",
    re.IGNORECASE,
)
_IMPERATIVE = re.compile(
    r"^[\s>*_\-\d.)]*(?:\*\*[^*]{0,40}\*\*\s*:?\s*)?(?:Re-?raise|Raise|Log|Add|Replace|Change"
    r"|Use|Remove|Propagate|Let|Catch|Handle|Record)\b",
    re.IGNORECASE,
)
_TIES = re.compile(r"\btie[ds]?\b|\btie-?break\w*|\bdeterministic\b|\bnon-?deterministic\b", re.I)
_TIE_REMEDY = re.compile(
    r"\badd\b[^.]{0,80}\b(?:ORDER\s+BY|column|key|tie)|\bRANK\b|\bDENSE_RANK\b"
    r"|\btie-?break\w*|\bunique\b[^.]{0,40}\b(?:column|key)\b",
    re.IGNORECASE,
)
_NO_CHANGE = re.compile(
    r"\bno\s+(?:code\s+)?change\s+(?:is\s+)?(?:recommended|needed|required)\b"
    r"|\b(?:nothing|no\s+remedy)\s+(?:needs|is\s+needed)\b",
    re.IGNORECASE,
)
# A decision not to change the code is an answer only with its reason (C35-A): in the
# same sentence, after a colon or dash or a reason word, or in a next sentence that
# opens with one. The reason is the words that follow: a connective alone ("because.",
# "because: ") gives none (Sync Point 1 finding 2).
_REASON_AFTER = re.compile(r"^\s*(?:[:—–-]|,?\s*(?:because|since|given)\b)", re.IGNORECASE)
_REASON_CONNECTIVE = re.compile(r"\b(?:because|since|given\s+that)\b", re.IGNORECASE)
_REASON_STATE = re.compile(
    r"\b(?:by\s+design|intend\w*|deliberate\w*|on\s+purpose)\b", re.IGNORECASE
)
_REASON_NEXT = re.compile(
    r"^\s*(?:because|since|the\s+reason(?:\s+is)?|this\s+is\s+(?:because|by\s+design|"
    r"intended|deliberate)|the\s+(?:handler|procedure|code)\s+is\s+(?:intended|deliberate"
    r"|by\s+design))\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-z][A-Za-z'_-]+")
# The fewest words that can state why: a subject, a verb and what it is about.
_MIN_REASON_WORDS = 3


def _substantive(text: str) -> bool:
    return len(_WORD.findall(text)) >= _MIN_REASON_WORDS


def missing_remedies(action: str, source: Sequence[str], answer: str) -> list[ClaimFinding]:
    """The task's remedies the answer does not give, for what the source holds."""

    if action not in ACTIONS:
        return []
    sentences = [c for line in prose_lines(answer) for c in clauses(line)][:_MAX_CLAUSES]
    # What a re-raise does to the earlier work is checked whatever the source owes, and
    # so is the order in which the statements can fail (Wave 2X).
    findings = _reraise_rollback_claims(sentences)
    findings += _control_flow_claims(source, sentences)
    owed = obligations(source)
    if not owed:
        return findings
    state = next((o for o in owed if o.kind == "transaction_state"), None)
    # What the transaction holds is owed even when no change is recommended.
    if state is not None and not any(_states_transaction(s) for s in sentences):
        findings.append(
            ClaimFinding(
                "transaction_state",
                f"source line {state.line}: {state.text[:200]}",
                "the supplied source makes more than one change before a handler that "
                "swallows the error without RAISE or ROLLBACK: when an earlier statement "
                "succeeds and a later one fails, Oracle rolls back only the failing "
                "statement, so the earlier statement's rows stay in the caller's "
                "transaction and the caller can commit them. Say what the transaction "
                "then holds; what other sessions do between the statements is a "
                "different risk.",
            )
        )
    if _no_change_with_reason(sentences):
        return findings
    for kind in dict.fromkeys(o.kind for o in owed if o.kind != "transaction_state"):
        first = next(o for o in owed if o.kind == kind)
        if kind == "swallowed_error" and (
            any(_recommends_reraise(s) for s in sentences) or _code_raises(answer)
        ):
            continue
        if kind == "row_ties" and any(
            _TIES.search(s) and (_TIE_REMEDY.search(s) or _DIRECTIVE.search(s)) for s in sentences
        ):
            continue
        findings.append(
            ClaimFinding(
                "missing_remedy",
                f"source line {first.line}: {first.text[:200]}",
                f"the explain task asks for the remedy for {TASK_CLAUSES[kind]}, and the "
                "supplied source has one here; the answer describes it but does not say "
                "what to change. Say what to change, or say why no change is recommended.",
            )
        )
    return findings


# -- control flow (Wave 2X, trial 29 EXPLAIN-06) -----------------------------------------


def sequences(source: Sequence[str]) -> list[list[str]]:
    """The data changes, in order (INSERT, UPDATE, DELETE, MERGE), of each block with an
    exception section whose statements have no handler of their own: no block nested
    among them. Any exception one of them raises transfers control to the block's
    handler, so the statements after it do not run in that call."""

    lines = list(source[:_MAX_SOURCE_LINES])
    code, _ = code_lines(lines)
    found = []
    for body, _handlers in _blocks("\n".join(code)):
        if re.search(r"\bBEGIN\b", body, re.IGNORECASE):
            continue
        kinds = [m[0].split()[0].upper() for m in _DML.finditer(body)]
        if len(dict.fromkeys(kinds)) >= 2:
            found.append(kinds)
    return found


# Two statements said to fail in one call: "the INSERT, the DELETE, or both failed",
# "both statements can fail", "it raises both".
_JOINT_FAILURE = re.compile(
    r"\bor\s+both\b[^.;]{0,20}?\b(?:fail|rais|error|throw)\w*"
    r"|\bboth\s+(?:of\s+them\s+|statements\s+|(?:DML\s+)?statements\s+|changes\s+)?(?:(?:can|could|may|might|will|would)\s+)?"
    r"(?:fail|rais|error|throw)\w*"
    r"|\b(?:fail|rais|throw)\w*\s+(?:in\s+)?both\b",
    re.IGNORECASE,
)
# A later statement said to run after an earlier one fails: "if the INSERT fails, the
# DELETE still runs".
_RUNS_ON = re.compile(
    r"\b(?:still|also|then)\s+(?:runs?|executes?|proceeds?|happens?|goes\s+ahead|is\s+attempted|"
    r"deletes?|removes?|inserts?|updates?|merges?)\b|\b(?:is|are|gets?)\s+still\s+(?:run|executed|"
    r"attempted|reached)\b",
    re.IGNORECASE,
)
_FAILS = re.compile(r"\b(?:fail|rais|error|throw)\w*", re.IGNORECASE)
_ONE_AT_A_TIME = re.compile(
    r"\b(?:cannot|can['’]t|can\s+not|never|not|neither|nor)\s+(?:\w+\s+){0,2}both\b|\bonly\s+one\b"
    r"|\bat\s+most\s+one\b|\b(?:separate|different|other|later|earlier)\s+(?:calls?|runs?|"
    r"invocations?|executions?)\b|\bnot\s+(?:still|also|then)\b|\b(?:is|are)\s+(?:not|never)\s+"
    r"(?:run|attempted|reached|executed)\b|\bskip\w*",
    re.IGNORECASE,
)


def _control_flow_claims(source: Sequence[str], sentences: Sequence[str]) -> list[ClaimFinding]:
    """Statements of one block said to fail together, or the later one to run after the
    earlier one fails.

    The supplied source runs its statements in order; an exception transfers control
    to the enclosing handler, and nothing after the raising statement runs in that call
    (trial 29 EXPLAIN-06: "even if the INSERT, the DELETE, or both failed", of an INSERT
    then a DELETE with one WHEN OTHERS handler). A statement inside a nested block with
    its own handler can be followed by more work, so such a block is not checked. Said
    of separate calls, or denied ("cannot both fail"), it is not flagged.
    """

    findings: list[ClaimFinding] = []
    for kinds in sequences(source):
        order = list(dict.fromkeys(kinds))
        for sentence in sentences:
            named = [k for k in order if re.search(rf"\b{k}\b", sentence, re.IGNORECASE)]
            if len(named) < 2 or _ONE_AT_A_TIME.search(sentence):
                continue
            first, later = named[0], named[1]
            joint = _JOINT_FAILURE.search(sentence)
            runs_on = None
            failed = re.search(rf"\b{first}\b[^.;]{{0,60}}?{_FAILS.pattern}", sentence, re.I)
            if failed:
                after = re.search(rf"\b{later}\b", sentence[failed.end() :], re.I)
                if after:
                    runs_on = _RUNS_ON.search(sentence, failed.end() + after.end())
            if not joint and not runs_on:
                continue
            findings.append(
                ClaimFinding(
                    "control_flow",
                    sentence.strip()[:300],
                    f"the supplied source runs the {first} and then the {later} in one block, "
                    "with no handler of their own: when the earlier statement raises, control "
                    "passes to the block's handler and the later one is not attempted in that "
                    "call. So both cannot fail in one call. Say which statement's failure "
                    "leaves what state.",
                )
            )
        if findings:
            break
    return findings


# The transaction a later failure leaves: the earlier statement's rows still there
# ("the inserted rows remain in the transaction", "are not rolled back"), what a commit
# then makes permanent ("the caller commits the archive copy with the orders still in
# ORDERS", "duplicated in both tables", "may commit partial work"), or the statement-
# level rollback itself.
_EARLIER_WORK = re.compile(
    r"\b(?:rows?|insert\w*|first\s+(?:statement|DML|change)|earlier\s+(?:statement|DML|change|"
    r"work)|changes?|work|cop(?:y|ied|ies)|archived|archive\s+(?:rows?|cop(?:y|ies)))\b",
    re.IGNORECASE,
)
_RETAINED = re.compile(
    r"\b(?:remain\w*|stay\w*|kept|keeps?|persist\w*|left\s+(?:in|pending|uncommitted)|"
    r"still\s+(?:be\s+|being\s+)?(?:there|in|pending|present|part)|survive\w*|(?:is|are|were|was|be)\s+not\s+"
    r"(?:\w+\s+)?rolled\s+back|(?:isn't|aren't|weren't|wasn't)\s+rolled\s+back|undone\s+only|"
    r"not\s+undone)\b",
    re.IGNORECASE,
)
_TRANSACTION_WORD = re.compile(
    r"\b(?:transactions?|commit\w*|caller|pending|uncommitted|roll(?:ed|s)?\s*back|rollback)\b",
    re.IGNORECASE,
)
_PARTIAL_STATE = (
    r"duplicat\w*|both\s+tables|in\s+both|twice|not\s+(?:been\s+)?(?:deleted|removed)|still\s+in|"
    r"without\s+(?:the\s+(?:DELETE|delete|second|later)|being\s+(?:removed|deleted))|half\w*|"
    r"partial\w*|inconsistent|incomplete"
)
# The groups mark the commit and the partial state, each of which a denial can govern.
_COMMITTED_STATE = re.compile(
    rf"\b(?P<c1>commit\w*)\b[^.;]{{0,160}}\b(?P<p1>{_PARTIAL_STATE})\b"
    rf"|\b(?P<p2>{_PARTIAL_STATE})\b[^.;]{{0,160}}\b(?P<c2>commit\w*)\b",
    re.IGNORECASE,
)
_STATEMENT_ROLLBACK = re.compile(
    r"\bstatement[- ]level\s+(?:rollback|atomicity)\b|\brolls?\s+back\s+only\s+(?:the|that)\s+"
    r"(?:failing|failed|second|later|DELETE|UPDATE|INSERT|MERGE)\b|\bonly\s+the\s+(?:failing|failed)"
    r"\s+statement\b[^.;]{0,40}\broll",
    re.IGNORECASE,
)
_CONCURRENCY = re.compile(
    r"\b(?:another|other|concurrent|second|separate)\s+sessions?\b|\bconcurren\w*", re.IGNORECASE
)


# The state named only to be dismissed or denied (Sync Point 2K): "statement-level
# rollback is irrelevant here", "the inserted rows do not remain in the transaction",
# "whether the rows remain is unclear", "the caller will never commit duplicates", "cannot
# commit partial work" (Sync Point 2L). A negation within three words before the verb, the
# commit or the partial state denies it; "not only" does not, and "are not rolled back"
# is itself the retained state.
_STATE_DISMISSED = re.compile(
    r"\b(?:irrelevant|not\s+relevant|(?:does|do|did)\s+not\s+(?:apply|matter)|"
    r"(?:doesn't|don't|didn't)\s+(?:apply|matter))\b",
    re.IGNORECASE,
)
_STATE_DENIED_BEFORE = re.compile(
    r"(?:\b(?:neither|no|none|nothing|never|whether|without|cannot|"
    r"not(?!\s+(?:only|just|merely)\b))\b"
    r"(?:\s+[\w$#']+){0,3}|n't)\s*$",
    re.IGNORECASE,
)


def _affirmed(pattern: re.Pattern[str], sentence: str) -> bool:
    """Whether ``pattern`` matches somewhere it is not denied just before."""

    return any(
        not _STATE_DENIED_BEFORE.search(sentence[: m.start()]) for m in pattern.finditer(sentence)
    )


def _committed(sentence: str) -> bool:
    """Whether a commit of the partial state is said, with neither the commit nor the
    state denied just before it."""

    for m in _COMMITTED_STATE.finditer(sentence):
        commit, state = ("c1", "p1") if m["c1"] else ("c2", "p2")
        if not any(_STATE_DENIED_BEFORE.search(sentence[: m.start(g)]) for g in (commit, state)):
            return True
    return False


def _states_transaction(sentence: str) -> bool:
    """A sentence that says what the transaction holds after a later statement fails."""

    if _CONCURRENCY.search(sentence) or _STATE_DISMISSED.search(sentence):
        return False
    if _affirmed(_STATEMENT_ROLLBACK, sentence) or _committed(sentence):
        return True
    return bool(
        _EARLIER_WORK.search(sentence)
        and _affirmed(_RETAINED, sentence)
        and _TRANSACTION_WORD.search(sentence)
    )


# What re-raising does to the earlier work (Sync Point 2N, trial 25 EXPLAIN-06): "re-raising
# does not roll back the successful first DML statement automatically" is true only when
# an enclosing PL/SQL caller catches the exception. When it leaves the top-level database
# call unhandled, Oracle rolls back that call's work, the earlier INSERT included; work
# the session did before the call stays either way. A sentence that denies the rollback
# beside a re-raise, about the earlier statement's work, owes that distinction: in the
# sentence itself (a catching caller, or work from before the call), or in a sentence of
# the answer that states the unhandled call's rollback.
_NO_ROLLBACK = re.compile(
    r"\b(?:does|do|did|will|would|can|is|are)\s*(?:not|n't)\s+(?:\w+\s+){0,2}"
    r"(?:roll(?:ed)?\s*back|undo(?:ne)?)\b"
    r"|\b(?:doesn't|don't|didn't|won't|wouldn't|isn't|aren't)\s+(?:\w+\s+){0,2}"
    r"(?:roll(?:ed)?\s*back|undo(?:ne)?)\b"
    r"|\bno\s+(?:automatic\s+|implicit\s+)?rollback\b"
    r"|\bnever\s+(?:\w+\s+)?(?:roll(?:s|ed)?\s*back|undo(?:es|ne)?)\b"
    r"|\bnot\s+(?:be\s+)?(?:automatically\s+)?(?:rolled\s+back|undone)\b",
    re.IGNORECASE,
)
_RERAISED = re.compile(r"\bre-?rais\w*|\bRAISE\b|\bpropagat\w*", re.IGNORECASE)
_EARLIER_DML = re.compile(
    r"\b(?:first|earlier|successful|previous|prior)\s+(?:\w+\s+){0,2}(?:DML|statement|"
    r"change|INSERT|work)s?\b|\bINSERT\w*\b|\b(?:inserted|archived)\s+rows?\b",
    re.IGNORECASE,
)
# A catching caller is named by its verb ("an enclosing caller catches it"); the verb
# must be affirmed: "when no enclosing caller catches", "does not catch the error" deny
# it (Sync Point 2O). Work from before the call is the other scope a denial may have.
_CAUGHT_BY_CALLER = re.compile(
    r"\b(?:catch\w*|caught|trap\w*|handl(?:es|ed|ing)\s+(?:it|the\s+(?:exception|error)))\b",
    re.IGNORECASE,
)
_CATCH_DENIED_BEFORE = re.compile(
    r"(?:\b(?:no|not|never|without|neither|nor|unless)\b(?:\s+[\w/'-]+){0,4}|n't(?:\s+[\w/'-]+){0,2})"
    r"\s*$",
    re.IGNORECASE,
)
_PRE_CALL = re.compile(
    r"\bbefore\s+the\s+(?:call|procedure\s+(?:ran|runs|was\s+called))\b|\bpre-?call\b",
    re.IGNORECASE,
)
# The unhandled call's rollback asked or left unknown, not stated.
_OPEN_PROPOSITION = re.compile(
    r"\b(?:whether|unknown|unclear|uncertain|not\s+(?:known|sure|clear)|"
    r"(?:do|does|did)\s*(?:not|n't)\s+know|don't\s+know|(?:cannot|can't)\s+(?:tell|say))\b",
    re.IGNORECASE,
)
_UNHANDLED_CALL = re.compile(
    r"\bunhandled\b|\btop-?level\b|\boutermost\b|\bescap\w*|\b(?:reaches|returns\s+to|"
    r"propagates\s+to)\s+the\s+(?:client|host)\b",
    re.IGNORECASE,
)
_ROLLED_BACK = re.compile(r"\broll(?:s|ed)?\s*back\b|\bundo(?:es|ne)?\b", re.IGNORECASE)


def _reraise_rollback_claims(sentences: Sequence[str]) -> list[ClaimFinding]:
    """Rollback denied after a re-raise, with no distinction of who receives the error."""

    if any(_states_call_rollback(s) for s in sentences):
        return []
    for sentence in sentences:
        if not (_RERAISED.search(sentence) and _EARLIER_DML.search(sentence)):
            continue
        denied = _NO_ROLLBACK.search(sentence)
        if denied is None or _caught(sentence) or _PRE_CALL.search(sentence):
            continue
        return [
            ClaimFinding(
                "transaction_state",
                sentence[:300],
                "what happens to the earlier statement's work after the error is re-raised "
                "depends on who receives it: when the exception leaves the top-level "
                "database call unhandled (it reaches the client), Oracle rolls back that "
                "call's work, the earlier INSERT included; when an enclosing PL/SQL caller "
                "catches it, the earlier INSERT stays pending in the transaction. Work the "
                "session did before the call is not rolled back either way. Say which case "
                "you mean, or both; do not add a ROLLBACK or COMMIT to the procedure.",
            )
        ]
    return []


def _caught(sentence: str) -> bool:
    """Whether the sentence names a caller that catches the exception, affirmed."""

    return any(
        not _CATCH_DENIED_BEFORE.search(sentence[: m.start()])
        for m in _CAUGHT_BY_CALLER.finditer(sentence)
    )


def _states_call_rollback(sentence: str) -> bool:
    """A sentence saying an unhandled exception rolls back the failed call's work, as a
    statement: "whether an unhandled error rolls back the INSERT is unknown" is not one."""

    return bool(
        not _OPEN_PROPOSITION.search(sentence)
        and _UNHANDLED_CALL.search(sentence)
        and _affirmed(_ROLLED_BACK, sentence)
        and not _NO_ROLLBACK.search(sentence)
    )


# "does not re-raise", "no RAISE", "neither logs nor re-raises": what the code lacks.
_NEGATED = re.compile(r"\b(?:not|no|never|neither|nor|without)\b[^.,;:]{0,25}$|n't\s*$", re.I)


def _recommends_reraise(sentence: str) -> bool:
    """A re-raise the sentence asks for, not one it says the code lacks."""

    asked = [m for m in _RERAISE.finditer(sentence) if not _NEGATED.search(sentence[: m.start()])]
    if not asked:
        return False
    # "the better change is: log the failure first, then re-raise it": the imperative
    # after a colon or semicolon gives the remedy too (Sync Point 2G, trial 23 EXPLAIN-06).
    parts = re.split(r"[:;]\s+", sentence)
    return bool(_DIRECTIVE.search(sentence) or any(_IMPERATIVE.match(p) for p in parts))


def _no_change_with_reason(sentences: Sequence[str]) -> bool:
    for index, sentence in enumerate(sentences):
        found = _NO_CHANGE.search(sentence)
        if found is None:
            continue
        rest = sentence[found.end() :]
        after = _REASON_AFTER.match(rest)
        if after and _substantive(rest[after.end() :]):
            return True
        # "because ..." elsewhere in the sentence, with its reason after it.
        if any(_substantive(sentence[m.end() :]) for m in _REASON_CONNECTIVE.finditer(sentence)):
            return True
        # "the handler is deliberate": the stated intent, with what it is about.
        outside = sentence[: found.start()] + " " + rest
        if _REASON_STATE.search(outside) and _substantive(_REASON_STATE.sub(" ", outside)):
            return True
        following = sentences[index + 1] if index + 1 < len(sentences) else ""
        opener = _REASON_NEXT.match(following)
        if opener and _substantive(following[opener.end() :]):
            return True
    return False


def _code_raises(answer: str) -> bool:
    """Replacement code in which every exception handler itself propagates the error.

    A RAISE elsewhere in the block, before EXCEPTION or in another handler, does not
    count: ``RAISE; ... EXCEPTION WHEN OTHERS THEN NULL;`` still swallows the error. Nor
    does a RAISE the handler reaches only on some path (``IF ... THEN RAISE; ELSE NULL;
    END IF;``): the prose must then give the remedy."""

    for block in fenced_blocks(answer):
        found = handlers(block.text.splitlines())
        if found and all(h.propagates for h in found):
            return True
        # A handler body given on its own, in any fence ("```text" included), that ends
        # by propagating: "RAISE;" as the replacement for "NULL;" (trial 23, EXPLAIN-06).
        if not found and _HANDLER_BODY_RAISES.search(block.text):
            return True
    return False


# A replacement handler body whose last statement propagates the error.
_HANDLER_BODY_RAISES = re.compile(
    r"(?:^|;)\s*(?:RAISE|RAISE_APPLICATION_ERROR\s*\([^;]*\))\s*;\s*\Z", re.IGNORECASE
)
