"""User Menu file resolution, merge and first-run seeding (I/O layer)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dunders.config import user_config
from dunders.fm.user_menu import MenuEntry, parse_menu

LOCAL_MENU_NAME = ".dunders.menu.md"

SEED_MENU = """\
# User Menu

## Python

### (v) Activate venv (.venv)
```bash
source %d/.venv/bin/activate
```

### (t) Run tests
```bash
pytest -q
```

## Build

### (b) Build project
```bash
make build
```

## Git

### (s) Status
```bash
git -C %d status
```
"""


def global_menu_path() -> Path:
    return user_config.config_dir() / "menu.md"


def local_menu_path(panel_dir: Path) -> Path:
    return Path(panel_dir) / LOCAL_MENU_NAME


@dataclass(frozen=True)
class Row:
    kind: str                       # "header" | "separator" | "entry"
    text: str = ""                  # header text
    entry: MenuEntry | None = None
    source: Path | None = None      # menu file this entry came from


@dataclass(frozen=True)
class LoadedMenu:
    rows: list[Row] = field(default_factory=list)
    local_path: Path | None = None
    global_path: Path | None = None
    has_any: bool = False
    any_file_exists: bool = False


def _read_entries(path: Path) -> list[MenuEntry]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    return parse_menu(text)


def _block_rows(entries: list[MenuEntry], source: Path) -> list[Row]:
    rows: list[Row] = []
    sentinel: object = object()
    section: object = sentinel
    for e in entries:
        if e.section != section:
            section = e.section
            if e.section:
                rows.append(Row(kind="header", text=e.section))
        rows.append(Row(kind="entry", entry=e, source=source))
    return rows


def build_rows(
    local_entries: list[MenuEntry],
    local_path: Path,
    global_entries: list[MenuEntry],
    global_path: Path,
) -> list[Row]:
    rows: list[Row] = []
    rows.extend(_block_rows(local_entries, local_path))
    if local_entries and global_entries:
        rows.append(Row(kind="separator"))
    rows.extend(_block_rows(global_entries, global_path))
    return rows


def load_menu(panel_dir: Path) -> LoadedMenu:
    gpath = global_menu_path()
    lpath = local_menu_path(panel_dir)
    local_exists = lpath.is_file()
    global_exists = gpath.is_file()
    local_entries = _read_entries(lpath) if local_exists else []
    global_entries = _read_entries(gpath) if global_exists else []
    rows = build_rows(local_entries, lpath, global_entries, gpath)
    return LoadedMenu(
        rows=rows,
        local_path=lpath if local_exists else None,
        global_path=gpath,
        has_any=bool(local_entries or global_entries),
        any_file_exists=local_exists or global_exists,
    )


def seed_global_menu() -> Path:
    """Write the starter menu to the global path if it does not exist."""
    path = global_menu_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(SEED_MENU, encoding="utf-8")
    except OSError:
        pass
    return path
