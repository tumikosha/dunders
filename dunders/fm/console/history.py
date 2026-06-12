"""Persistent command history with Up/Down cursor."""

from __future__ import annotations

from collections import deque
from pathlib import Path


class History:
    def __init__(self, path: Path, cap: int = 1000) -> None:
        self._path = Path(path)
        self._cap = cap
        self._entries: deque[str] = deque(maxlen=cap)
        self._cursor: int | None = None
        self._load()

    def entries(self) -> list[str]:
        return list(self._entries)

    def append(self, cmd: str) -> None:
        cmd = cmd.rstrip("\n")
        if not cmd:
            return
        if self._entries and self._entries[-1] == cmd:
            return
        self._entries.append(cmd)
        self._persist_append(cmd)
        self._cursor = None

    def reset_cursor(self) -> None:
        self._cursor = None

    def previous(self) -> str:
        if not self._entries:
            return ""
        if self._cursor is None:
            self._cursor = len(self._entries) - 1
        else:
            self._cursor = max(0, self._cursor - 1)
        return self._entries[self._cursor]

    def next(self) -> str:
        if self._cursor is None:
            return ""
        self._cursor += 1
        if self._cursor >= len(self._entries):
            self._cursor = None
            return ""
        return self._entries[self._cursor]

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            text = self._path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in text.splitlines():
            if line:
                self._entries.append(line)
        if len(self._entries) == self._cap:
            self._rewrite()

    def _persist_append(self, cmd: str) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(cmd + "\n")
        except OSError:
            return
        if len(self._entries) == self._cap:
            self._rewrite()

    def _rewrite(self) -> None:
        try:
            tmp = Path(str(self._path) + ".tmp")
            tmp.write_text("\n".join(self._entries) + "\n", encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            return
