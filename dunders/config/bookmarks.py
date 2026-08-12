"""Bookmarks: named saved locations, persisted to a 0600 bookmarks.json.

Mirrors dunders.config.user_config (stdlib json, atomic + fault-tolerant) but in
its own file because a bookmark may hold a plaintext password — the 0600
permission is the only thing guarding it. Reads never raise into the UI; writes
are best-effort.

Each bookmark is a dict: {"label": str, "uri": str, "password": str | None}.
``uri`` is a VfsPath.as_uri() so one field captures local/archive/network
locations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dunders.config.user_config import config_dir


__all__ = ["bookmarks_path", "list_bookmarks", "add_bookmark", "remove_bookmark", "bookmarks_mtime"]


def bookmarks_path() -> Path:
    return config_dir() / "bookmarks.json"


def list_bookmarks(path: Path | None = None) -> list[dict]:
    """Every stored bookmark, or [] if the file is missing/corrupt."""
    target = path if path is not None else bookmarks_path()
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    items = data.get("bookmarks") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [b for b in items if isinstance(b, dict) and "uri" in b and "label" in b]


def bookmarks_mtime(path: Path | None = None) -> float:
    """The bookmarks file mtime, or 0.0 if missing/unreadable."""
    target = path if path is not None else bookmarks_path()
    try:
        return target.stat().st_mtime
    except OSError:
        return 0.0


def add_bookmark(label: str, uri: str, password: str | None = None) -> bool:
    items = list_bookmarks()
    items.append({"label": label, "uri": uri, "password": password})
    return _save(items)


def remove_bookmark(index: int) -> bool:
    items = list_bookmarks()
    if not 0 <= index < len(items):
        return False
    del items[index]
    return _save(items)


def _save(items: list[dict]) -> bool:
    """Atomically write the list. The temp file is created 0600 from the start
    (os.open with mode), so the plaintext password is never world-readable, not
    even during the write."""
    path = bookmarks_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"bookmarks": items}, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False
