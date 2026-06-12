"""User-preference persistence: ``$XDG_CONFIG_HOME/dunders/config.json``.

A tiny, dependency-free (stdlib ``json``) key/value store for preferences that
must survive restarts — currently just the selected theme. Reads are
fault-tolerant (missing or corrupt file → ``{}``); writes are best-effort and
atomic, and never raise into the UI so a read-only home directory can't crash
the app. Honours ``XDG_CONFIG_HOME`` (falling back to ``~/.config``), so the
test suite can redirect it to a tmp dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "dunders"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict:
    """Return the parsed config, or ``{}`` if missing/unreadable/malformed."""
    try:
        with open(config_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict) -> bool:
    """Atomically write ``data`` as JSON. Returns False on any I/O error."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def get_theme() -> str | None:
    """Return the persisted theme name, or None if unset."""
    value = load_config().get("theme")
    return value if isinstance(value, str) else None


def set_theme(name: str) -> bool:
    """Persist ``name`` as the active theme, preserving other keys."""
    data = load_config()
    data["theme"] = name
    return save_config(data)
