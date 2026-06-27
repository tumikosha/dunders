"""File-association file resolution, merge, and first-run seeding (I/O layer)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from dunders.config import user_config
from dunders.fm.associations import (
    BUILTIN_DEFAULTS,
    merge_tables,
    parse_associations,
)

SEED_ASSOCIATIONS = """\
# dunders file associations.
#
# Each section is a file extension (no dot). Verbs:
#   open  -> Enter / double-click
#   view  -> F3
#   edit  -> F4
#
# A verb is either a built-in handler name or "!<external command>".
# Built-in handlers: auto editor viewer hex image csv markdown office database
# External commands use the User Menu macros (%f file, %d dir, %s selection).
# A verb may be a string (all OSes) or a table with macos/linux/windows/default.

[jpg]
open = "image"
view = "image"
[jpg.edit]
default = "!xdg-open %f"
macos   = "!open -a Preview %f"
windows = "!start \\"\\" %f"

[png]
open = "image"
view = "image"

[md]
open = "markdown"
view = "markdown"
edit = "editor"
"""


def associations_path() -> Path:
    return user_config.config_dir() / "associations.toml"


def load_table() -> tuple[dict, str | None]:
    """Return ``(merged_table, error_or_None)``. Never raises: a missing file
    yields the built-in defaults; a malformed file yields defaults + a message."""
    path = associations_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return merge_tables(BUILTIN_DEFAULTS, {}), None
    try:
        user = parse_associations(text)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        return merge_tables(BUILTIN_DEFAULTS, {}), str(exc)
    return merge_tables(BUILTIN_DEFAULTS, user), None


def seed_associations() -> Path:
    """Write the starter file if it does not exist; return the path."""
    path = associations_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(SEED_ASSOCIATIONS, encoding="utf-8")
    except OSError:
        pass
    return path
