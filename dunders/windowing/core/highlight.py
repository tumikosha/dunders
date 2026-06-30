"""Pygments-backed syntax tokenizer producing per-line style spans.

Editor-agnostic: no Textual/rich imports. Maps the Pygments token hierarchy
down to a small set of base roles consumed by the palette as
``editor.syntax.<role>``.
"""

from __future__ import annotations

from dataclasses import dataclass

from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename, guess_lexer
from pygments.token import (
    Comment,
    Error,
    Keyword,
    Name,
    Number,
    Operator,
    String,
    _TokenType,
)
from pygments.util import ClassNotFound

# guess_lexer always returns *some* lexer, ranking by Lexer.analyse_text. On
# prose / URL-heavy text the winner is noise (e.g. "Tera Term macro", ~0.01,
# tie-breaking over the plain-text lexer) that fragments every word and URL
# component into its own token — dozens of syntax spans per line, one Rich
# segment each on every scroll. Real code clears this floor by a wide margin
# (Python/Bash score ~1.0), so reject any content guess at or below it.
_MIN_GUESS_CONFIDENCE = 0.1


@dataclass(frozen=True)
class Span:
    """A styled run on a single buffer line, in buffer-column coordinates."""

    start: int
    end: int
    role: str


def token_to_role(tok: _TokenType) -> str | None:
    """Collapse a Pygments token type to a base role, or None for default text.

    Order matters: Name subtypes are checked before generic Name.
    """
    if tok in Comment:
        return "comment"
    if tok in String:
        return "string"
    if tok in Number:
        return "number"
    if tok in Keyword:
        return "keyword"
    if tok in Name.Function:
        return "function"
    if tok in Name.Class:
        return "class"
    if tok in Name.Builtin:
        return "builtin"
    if tok in Name:
        return "name"
    if tok in Operator:
        return "operator"
    if tok in Error:
        return "error"
    return None


class SyntaxHighlighter:
    """Resolves a lexer (auto or manual) and tokenizes buffer lines to spans."""

    def __init__(self) -> None:
        self._auto_lexer: Lexer | None = None
        self._user_lexer: Lexer | None = None

    @property
    def _active_lexer(self) -> Lexer | None:
        return self._user_lexer or self._auto_lexer

    @property
    def enabled(self) -> bool:
        return self._active_lexer is not None

    @property
    def language_name(self) -> str:
        lex = self._active_lexer
        return lex.name if lex is not None else ""

    def detect(self, file_path: str | None, sample_text: str) -> None:
        """Pick a lexer by filename, falling back to content guessing."""
        self._user_lexer = None
        lexer: Lexer | None = None
        if file_path:
            try:
                lexer = get_lexer_for_filename(file_path)
            except ClassNotFound:
                lexer = None
        if lexer is None and sample_text.strip():
            try:
                guessed = guess_lexer(sample_text)
            except ClassNotFound:
                guessed = None
            # Only trust a content guess that scores above the noise floor;
            # otherwise prose gets a bogus lexer that fragments every line.
            if guessed is not None:
                try:
                    confidence = guessed.analyse_text(sample_text) or 0.0
                except Exception:
                    confidence = 0.0
                if confidence >= _MIN_GUESS_CONFIDENCE:
                    lexer = guessed
        self._auto_lexer = self._configure(lexer)

    def set_language(self, name: str | None) -> None:
        """Set a manual lexer override by Pygments name/alias, or clear it."""
        if not name:
            self._user_lexer = None
            return
        try:
            self._user_lexer = self._configure(get_lexer_by_name(name))
        except ClassNotFound:
            pass  # keep the current override / auto lexer

    @staticmethod
    def _configure(lexer: Lexer | None) -> Lexer | None:
        if lexer is not None:
            # Keep blank lines so token rows line up 1:1 with buffer rows.
            lexer.stripnl = False
            lexer.stripall = False
        return lexer

    def tokenize(self, lines: list[str]) -> list[list[Span]]:
        """Return one list of Spans per input line (empty list = no highlight)."""
        result: list[list[Span]] = [[] for _ in lines]
        lexer = self._active_lexer
        if lexer is None:
            return result
        text = "\n".join(lines)
        row = 0
        col = 0
        for tok, value in lexer.get_tokens(text):
            role = token_to_role(tok)
            parts = value.split("\n")
            for k, part in enumerate(parts):
                if k > 0:
                    row += 1
                    col = 0
                if part and role and row < len(result):
                    result[row].append(Span(col, col + len(part), role))
                col += len(part)
        return result
