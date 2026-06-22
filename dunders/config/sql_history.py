"""SQL console query history, persisted per connection to a 0600 sql_history.json.

Mirrors dunders.config.bookmarks (stdlib json, atomic + fault-tolerant, 0600)
but keyed by connection: ``{"connections": {"<normalized-root>": [entry, ...]}}``
with the newest entry first. A query body can carry sensitive data in a WHERE
clause (not credentials, but still user data), so the file is 0600 like
bookmarks. Reads never raise into the UI; writes are best-effort.

Each entry is ``{"sql": str, "ts": float, "ok": bool, "info": str}`` where
``info`` is the console's status string (e.g. "42 row(s)", "Error: …").
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dunders.config.user_config import config_dir

__all__ = ["sql_history_path", "load_history", "record", "delete", "clear"]

_CAP = 200  # max entries kept per connection (oldest dropped)


def sql_history_path() -> Path:
    return config_dir() / "sql_history.json"


def _load_all() -> dict:
    """The whole file as ``{"connections": {root: [entry, ...]}}``, or an empty
    skeleton if missing/corrupt."""
    try:
        with open(sql_history_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"connections": {}}
    conns = data.get("connections") if isinstance(data, dict) else None
    return {"connections": conns if isinstance(conns, dict) else {}}


def _valid(entry: object) -> bool:
    return isinstance(entry, dict) and isinstance(entry.get("sql"), str)


def load_history(root: str) -> list[dict]:
    """Every stored entry for ``root``, newest first; [] if none/unreadable.
    Malformed entries (non-dict / missing ``sql``) are filtered out."""
    items = _load_all()["connections"].get(root)
    if not isinstance(items, list):
        return []
    return [e for e in items if _valid(e)]


def record(root: str, sql: str, *, ok: bool, info: str) -> bool:
    """Prepend a query to ``root``'s history (newest first). Whitespace-only SQL
    is ignored. If the same trimmed SQL already exists it moves to the front with
    refreshed ts/ok/info (no duplicate). Capped at _CAP entries. Best-effort:
    returns False on a no-op or any I/O error."""
    if not sql.strip():
        return False
    data = _load_all()
    items = [e for e in data["connections"].get(root, []) if _valid(e)]
    items = [e for e in items if e["sql"].strip() != sql.strip()]
    items.insert(0, {"sql": sql, "ts": time.time(), "ok": ok, "info": info})
    data["connections"][root] = items[:_CAP]
    return _save(data)


def delete(root: str, index: int) -> bool:
    """Remove one entry by its list index (0 = newest). False if out of range."""
    data = _load_all()
    items = [e for e in data["connections"].get(root, []) if _valid(e)]
    if not 0 <= index < len(items):
        return False
    del items[index]
    data["connections"][root] = items
    return _save(data)


def clear(root: str) -> bool:
    """Drop all history for ``root``."""
    data = _load_all()
    data["connections"].pop(root, None)
    return _save(data)


def _save(data: dict) -> bool:
    """Atomically write the store. The temp file is 0600 from creation (os.open
    with mode), so query bodies are never world-readable, not even mid-write."""
    path = sql_history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False
