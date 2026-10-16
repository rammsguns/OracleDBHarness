"""Reading the fenced code blocks of a copilot answer.

A proposal is taken from the answer's code blocks, so the blocks have to be found the
way Markdown finds them: a line at a time. A block opens on a line that starts with a
fence (three or more backticks or tildes) and any label (```` ```text ````,
```` ```plsql part=body ````), possibly indented inside a list item, and closes on a
line holding only a fence of the same character, at least as long. A fence that is
never closed opens no block.

A regular expression over the whole answer cannot do this: a block labelled with a
language it does not expect is skipped, and its closing fence is then read as the
opening of a block that runs to the next fence, so the prose between two blocks becomes
the "code" (K-7, 2026-10-15).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_OPEN = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*?)\s*$")
# Labels a proposal may carry; any other (``text``, ``bash``) is not code to apply.
CODE_LANGUAGES = frozenset({"", "sql", "plsql"})
_PART = re.compile(r"part=([A-Za-z0-9_-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class Fenced:
    """One block: its label, its text and where it sits in the answer."""

    language: str
    part: str | None
    text: str
    start: int
    end: int


def _label(info: str) -> tuple[str, str | None] | None:
    """``(language, part)`` for a label, or None for one that is neither."""

    words = info.split()
    language = ""
    if words and not words[0].lower().startswith("part="):
        language = words.pop(0).lower()
    if not words:
        return language, None
    if len(words) == 1 and (match := _PART.fullmatch(words[0])):
        return language, match.group(1)
    return None


def fenced_blocks(answer: str) -> list[Fenced]:
    """Every closed fenced block in ``answer``, in order."""

    lines = answer.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    blocks: list[Fenced] = []
    index = 0
    while index < len(lines):
        opening = _OPEN.match(lines[index])
        if opening is None or (opening.group(2)[0] == "`" and "`" in opening.group(3)):
            index += 1
            continue
        indent, fence, info = opening.groups()
        close = re.compile(rf"^[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
        end = next(
            (n for n in range(index + 1, len(lines)) if close.match(lines[n].rstrip("\r\n"))),
            None,
        )
        if end is None:
            break
        label = _label(info)
        if label is not None:
            body = [_dedent(line, len(indent)) for line in lines[index + 1 : end]]
            blocks.append(
                Fenced(
                    language=label[0],
                    part=label[1],
                    text="".join(body).rstrip(),
                    start=offsets[index],
                    end=offsets[end + 1],
                )
            )
        index = end + 1
    return blocks


def _dedent(line: str, width: int) -> str:
    """Remove up to ``width`` leading spaces: the indentation of the opening fence."""

    stripped = 0
    while stripped < width and stripped < len(line) and line[stripped] in " \t":
        stripped += 1
    return line[stripped:]


def without(answer: str, blocks: list[Fenced]) -> str:
    """``answer`` with ``blocks`` cut out, for the rationale shown beside a proposal."""

    kept, position = [], 0
    for block in sorted(blocks, key=lambda b: b.start):
        kept.append(answer[position : block.start])
        position = block.end
    kept.append(answer[position:])
    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()
