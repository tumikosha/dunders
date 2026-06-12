"""User Menu (F2): pure Markdown parser + macro engine. No I/O, no Textual."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

_SECTION_RE = re.compile(r"^##\s+(.*\S)\s*$")
_ENTRY_RE = re.compile(r"^###\s+(.*\S)\s*$")
_HOTKEY_RE = re.compile(r"^\(([^)])\)\s*(.*)$")
_FENCE_RE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class MenuEntry:
    hotkey: str | None      # single lowercased char, or None
    title: str              # display title with the (x) stripped
    body: str               # code-block body (may be multi-line), stripped of blank edges
    section: str | None     # enclosing ## section title, or None


def parse_menu(text: str) -> list[MenuEntry]:
    """Parse a User Menu Markdown document into ordered entries.

    `##` = section header, `###` = entry (optional `(x)` hotkey prefix),
    body = the first fenced code block under the entry. Entries without a
    code block are skipped. Never raises.
    """
    lines = text.splitlines()
    entries: list[MenuEntry] = []
    section: str | None = None
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        sec_m = _SECTION_RE.match(line)
        if sec_m:
            section = sec_m.group(1).strip()
            i += 1
            continue
        ent_m = _ENTRY_RE.match(line)
        if not ent_m:
            i += 1
            continue
        raw_title = ent_m.group(1).strip()
        hk_m = _HOTKEY_RE.match(raw_title)
        if hk_m:
            hotkey: str | None = hk_m.group(1).lower()
            title = hk_m.group(2).strip()
        else:
            hotkey, title = None, raw_title
        # Scan forward for the entry's first code block, stopping at the
        # next heading so a bodyless entry doesn't swallow the next one's.
        j = i + 1
        body_lines: list[str] | None = None
        while j < n:
            nxt = lines[j]
            if _SECTION_RE.match(nxt) or _ENTRY_RE.match(nxt) or nxt.startswith("# "):
                break
            if _FENCE_RE.match(nxt):
                body_lines = []
                j += 1
                while j < n and not _FENCE_RE.match(lines[j]):
                    body_lines.append(lines[j])
                    j += 1
                if j < n:  # skip the closing fence
                    j += 1
                break
            j += 1
        if body_lines is not None:
            body = "\n".join(body_lines).strip("\n")
            if body.strip():
                entries.append(MenuEntry(hotkey=hotkey, title=title, body=body, section=section))
        i = j
    return entries


@dataclass(frozen=True)
class MacroContext:
    current_file: str | None        # name of the file under the cursor
    tagged: tuple[str, ...]         # tagged/selected file names
    panel_dir: str                  # active panel cwd (absolute)
    other_file: str | None          # other panel's current file name
    other_dir: str | None           # other panel's cwd


_PROMPT_RE = re.compile(r"%\{([^}]*)\}")
_MACRO_RE = re.compile(r"%%|%\{[^}]*\}|%[fdtsFDxb]")


def collect_prompts(body: str) -> list[str]:
    """Distinct `%{label}` prompts in first-seen order."""
    out: list[str] = []
    for m in _PROMPT_RE.finditer(body):
        label = m.group(1)
        if label not in out:
            out.append(label)
    return out


def expand_macros(body: str, ctx: MacroContext, prompts: dict[str, str]) -> str:
    """Substitute macros. All path/name values are single-quoted (shlex-style);
    absent context expands to empty; `%%` -> literal `%`."""
    def q(s: str) -> str:
        # Always wrap in single quotes, escaping embedded single quotes via
        # the POSIX trick: end quote, escaped quote, reopen quote.
        return "'" + s.replace("'", "'\\''") + "'"

    def join(names: tuple[str, ...]) -> str:
        return " ".join(q(n) for n in names)

    def repl(m: re.Match) -> str:
        tok = m.group(0)
        if tok == "%%":
            return "%"
        if tok.startswith("%{"):
            return q(prompts.get(tok[2:-1], ""))
        code = tok[1]
        if code == "f":
            return q(ctx.current_file) if ctx.current_file else ""
        if code == "d":
            return q(ctx.panel_dir)
        if code == "t":
            return join(ctx.tagged)
        if code == "s":
            return join(ctx.tagged) if ctx.tagged else (q(ctx.current_file) if ctx.current_file else "")
        if code == "F":
            return q(ctx.other_file) if ctx.other_file else ""
        if code == "D":
            return q(ctx.other_dir) if ctx.other_dir else ""
        if code == "x":
            if ctx.current_file:
                ext = PurePath(ctx.current_file).suffix
                return q(ext[1:]) if ext else ""
            return ""
        if code == "b":
            return q(PurePath(ctx.current_file).stem) if ctx.current_file else ""
        return tok

    return _MACRO_RE.sub(repl, body)
