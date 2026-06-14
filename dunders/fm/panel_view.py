"""Panel view modes: layout + formatting helpers for FilePanel.

Pure module — no Textual imports. FilePanel owns all Strip/segment styling
and calls these helpers for text content, column widths, and descriptions.

Far-Manager-style modes:
  BRIEF       names only, 2 columns
  MEDIUM      names only, 3 columns
  FULL        Name | Size | Date         (the historical default)
  DETAILED    Name | Size | Date | Attr  (ls-style permission string)
  DESCRIPTION Name | <file-type description>
"""

from __future__ import annotations

import stat
from enum import Enum

from dunders.fm.file_entry import (
    FileEntry,
    format_mtime,
    format_mtime_short,
    format_size,
)


__all__ = ["PanelViewMode", "column_count", "is_multicolumn",
           "format_attrs", "describe_entry", "name_col_width",
           "column_width", "row_text_single", "format_cell", "COL_SEP",
           "empty_row_text"]


# Column separator: a vertical bar (one cell wide, same as the old space) drawn
# between every column in every view mode, NC/mc style.
COL_SEP = "│"


class PanelViewMode(Enum):
    BRIEF = "brief"
    MEDIUM = "medium"
    SHORT = "short"
    FULL = "full"
    DETAILED = "detailed"
    DESCRIPTION = "description"


# Column widths shared with the historical FilePanel layout.
_SIZE_COL = 7      # "9999K" / "<DIR>" / "<UP>"
_DATE_COL = 16     # "YYYY-MM-DD HH:MM"
_DATE_SHORT = 11   # "MM-DD HH:MM"  (Detailed)
_ATTR_COL = 10     # "drwxr-xr-x"
_GUTTER = 1


def column_count(mode: PanelViewMode) -> int:
    if mode is PanelViewMode.BRIEF:
        return 2
    if mode is PanelViewMode.MEDIUM:
        return 3
    return 1


def is_multicolumn(mode: PanelViewMode) -> bool:
    return column_count(mode) > 1


def format_attrs(entry: FileEntry) -> str:
    """ls-style 10-char mode string, e.g. 'drwxr-xr-x'. Never raises."""
    if entry.is_symlink:
        type_char = "l"
    elif entry.is_dir:
        type_char = "d"
    else:
        type_char = "-"
    m = entry.mode
    bits = (
        (stat.S_IRUSR, "r"), (stat.S_IWUSR, "w"), (stat.S_IXUSR, "x"),
        (stat.S_IRGRP, "r"), (stat.S_IWGRP, "w"), (stat.S_IXGRP, "x"),
        (stat.S_IROTH, "r"), (stat.S_IWOTH, "w"), (stat.S_IXOTH, "x"),
    )
    return type_char + "".join(ch if m & flag else "-" for flag, ch in bits)


_EXT_DESC = {
    ".py": "Python source", ".pyi": "Python stub",
    ".md": "Markdown document", ".rst": "reStructuredText", ".txt": "Text file",
    ".json": "JSON data", ".yaml": "YAML data", ".yml": "YAML data",
    ".toml": "TOML config", ".ini": "INI config", ".cfg": "Config file",
    ".sh": "Shell script", ".bash": "Shell script", ".zsh": "Shell script",
    ".c": "C source", ".h": "C header", ".cpp": "C++ source", ".hpp": "C++ header",
    ".js": "JavaScript", ".ts": "TypeScript", ".html": "HTML document",
    ".css": "Stylesheet", ".jpg": "JPEG image", ".jpeg": "JPEG image",
    ".png": "PNG image", ".gif": "GIF image", ".svg": "SVG image",
    ".pdf": "PDF document", ".zip": "ZIP archive", ".tar": "TAR archive",
    ".gz": "Gzip archive", ".lock": "Lock file",
}


def describe_entry(entry: FileEntry) -> str:
    """Short, total file-type label. Never raises."""
    if entry.is_parent:
        return "Parent directory"
    if entry.is_symlink:
        return "Symlink"
    if entry.is_dir:
        return "Directory"
    ext = entry.path.suffix.lower()
    if ext in _EXT_DESC:
        return _EXT_DESC[ext]
    if entry.is_executable:
        return "Executable"
    return "File"


def _name_prefix(entry: FileEntry) -> str:
    """ls -F style prefix: '/' dir/parent, '*' executable, ' ' otherwise.

    (Provider state glyphs, e.g. Docker container status, live in a dedicated
    provider column — see FilePanel — not glued to the name.)"""
    if entry.is_dir or entry.is_parent:
        return "/"
    if entry.is_executable:
        return "*"
    return " "


def _size_text(entry: FileEntry) -> str:
    if entry.is_parent:
        return "<UP>"
    if entry.is_dir:
        return "<DIR>"
    return format_size(entry.size)


def name_col_width(mode: PanelViewMode, width: int) -> int:
    """Width of the Name field for single-column modes."""
    if mode is PanelViewMode.DETAILED:
        return max(1, width - _SIZE_COL - _DATE_SHORT - _ATTR_COL - 3 * _GUTTER)
    if mode is PanelViewMode.DESCRIPTION:
        return max(1, width // 2)
    if mode is PanelViewMode.SHORT:
        return max(1, width - _SIZE_COL - _GUTTER)
    # FULL (and any single-column fallback)
    return max(1, width - _SIZE_COL - _DATE_COL - 2 * _GUTTER)


def column_width(width: int, k: int) -> int:
    """Per-column width for a k-column names-only layout."""
    if k <= 1:
        return max(1, width)
    return max(1, (width - (k - 1) * _GUTTER) // k)


def _fit_name(name: str, field: int) -> str:
    """Truncate `name` to fit a `field`-wide cell that also holds a 1-char
    prefix; appends '…' when cut. Mirrors the historical FilePanel logic."""
    if len(name) > field - 1:
        name = name[: max(0, field - 2)] + "…"
    return name


def format_cell(entry: FileEntry, col_w: int) -> str:
    """Names-only cell (prefix + truncated name) padded to `col_w`."""
    name = _fit_name(entry.name, col_w)
    return (_name_prefix(entry) + name).ljust(col_w)[:col_w]


def row_text_single(mode: PanelViewMode, entry: FileEntry, width: int) -> str:
    """Full row text (no styling) for a single-column mode, padded to width.

    FULL output is byte-for-byte identical to the historical FilePanel row so
    existing render tests keep passing.
    """
    ncol = name_col_width(mode, width)
    name = _fit_name(entry.name, ncol)
    name_field = (_name_prefix(entry) + name).ljust(ncol)

    if mode is PanelViewMode.DETAILED:
        size = _size_text(entry).rjust(_SIZE_COL)
        date = format_mtime_short(entry.mtime).ljust(_DATE_SHORT)
        attr = format_attrs(entry)
        text = f"{name_field}{COL_SEP}{size}{COL_SEP}{date}{COL_SEP}{attr}"
    elif mode is PanelViewMode.DESCRIPTION:
        desc_col = max(1, width - ncol - _GUTTER)
        desc = describe_entry(entry)[:desc_col]
        text = f"{name_field}{COL_SEP}{desc.ljust(desc_col)}"
    elif mode is PanelViewMode.SHORT:
        size = _size_text(entry).rjust(_SIZE_COL)
        text = f"{name_field}{COL_SEP}{size}"
    else:  # FULL
        size = _size_text(entry).rjust(_SIZE_COL)
        date = format_mtime(entry.mtime).ljust(_DATE_COL)
        text = f"{name_field}{COL_SEP}{size}{COL_SEP}{date}"

    return text[:width].ljust(width)


def empty_row_text(mode: PanelViewMode, width: int) -> str:
    """A blank single-column row that still carries the column separators, so
    the vertical bars continue to the bottom of the panel below the listing."""
    ncol = name_col_width(mode, width)
    blanks = [" " * ncol]
    if mode is PanelViewMode.DETAILED:
        blanks += [" " * _SIZE_COL, " " * _DATE_SHORT, " " * _ATTR_COL]
    elif mode is PanelViewMode.DESCRIPTION:
        blanks += [" " * max(1, width - ncol - _GUTTER)]
    elif mode is PanelViewMode.SHORT:
        blanks += [" " * _SIZE_COL]
    else:  # FULL
        blanks += [" " * _SIZE_COL, " " * _DATE_COL]
    return COL_SEP.join(blanks)[:width].ljust(width)
