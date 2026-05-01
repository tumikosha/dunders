from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SearchOptions:
    regex: bool = False
    case_sensitive: bool = False
    whole_word: bool = False
    wrap_around: bool = True
    in_selection: bool = False


@dataclass(frozen=True)
class Match:
    row: int
    col: int
    length: int


def _build_regex(pattern: str, options: SearchOptions) -> re.Pattern:
    body = pattern if options.regex else re.escape(pattern)
    if options.whole_word:
        body = rf"\b(?:{body})\b"
    flags = re.MULTILINE
    if not options.case_sensitive:
        flags |= re.IGNORECASE
    return re.compile(body, flags)


def _in_range(m: Match, sel: tuple[int, int, int, int]) -> bool:
    sr, sc, er, ec = sel
    if (sr, sc) > (er, ec):
        sr, sc, er, ec = er, ec, sr, sc
    start = (m.row, m.col)
    end = (m.row, m.col + m.length)
    return start >= (sr, sc) and end <= (er, ec)


def find_matches(
    buffer,
    pattern: str,
    options: SearchOptions,
    selection: tuple[int, int, int, int] | None = None,
) -> list[Match]:
    if not pattern:
        return []
    regex = _build_regex(pattern, options)
    apply_filter = options.in_selection and selection is not None
    out: list[Match] = []
    for row, line in enumerate(buffer.lines):
        for m in regex.finditer(line):
            start, end = m.start(), m.end()
            if start == end:
                continue
            match = Match(row=row, col=start, length=end - start)
            if apply_filter and not _in_range(match, selection):
                continue
            out.append(match)
    return out


def options_to_json(opts: SearchOptions) -> str:
    return json.dumps(asdict(opts))


def options_from_json(s: str) -> SearchOptions:
    return SearchOptions(**json.loads(s))
