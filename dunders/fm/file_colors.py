"""File-type classification for per-type colouring in file panels.

Pure logic: :func:`classify` maps a :class:`~dunders.fm.file_entry.FileEntry` to
one of the NC/MC-style categories below, or ``None`` for a plain file with no
special colour. Colours themselves live in the theme as ``panel.file.<category>``
roles (see :func:`role_for`); this module only decides *which* role a row gets.

Priority matters because a file can match several rules — structural traits
(parent / symlink / directory / executable bit) win over extension groups, and
the dotfile ``hidden`` bucket is the last resort before "no category".
"""

from __future__ import annotations

from pathlib import PurePath

from dunders.fm.file_entry import FileEntry

__all__ = ["classify", "role_for", "CATEGORIES", "ROLE_PREFIX"]

ROLE_PREFIX = "panel.file."

# Structural categories (no extension involved) plus the extension groups.
CATEGORIES = (
    "dir",
    "symlink",
    "executable",
    "archive",
    "image",
    "media",
    "document",
    "source",
    "config",
    "hidden",
)

# Extension (with leading dot, lowercase) -> category. Order within a group is
# irrelevant; a dict keeps lookup O(1). Keep groups in sync with CATEGORIES.
_EXT_CATEGORY: dict[str, str] = {}


def _register(category: str, *exts: str) -> None:
    for ext in exts:
        _EXT_CATEGORY["." + ext] = category


_register("archive", "zip", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz",
          "7z", "rar", "zst", "lz", "lzma", "z", "cab", "ar", "iso")
_register("image", "png", "jpg", "jpeg", "gif", "bmp", "svg", "webp", "ico",
          "tif", "tiff", "ppm", "pgm", "xpm", "heic", "avif")
_register("media", "mp3", "flac", "wav", "ogg", "oga", "opus", "m4a", "aac",
          "wma", "mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "mpg",
          "mpeg", "m4v")
_register("document", "pdf", "doc", "docx", "odt", "rtf", "xls", "xlsx", "ods",
          "ppt", "pptx", "odp", "epub", "djvu", "md", "rst", "txt", "tex")
_register("source", "py", "pyi", "js", "jsx", "ts", "tsx", "c", "h", "cc",
          "cpp", "cxx", "hpp", "rs", "go", "java", "kt", "rb", "sh", "bash",
          "zsh", "fish", "lua", "pl", "pm", "php", "swift", "scala", "clj",
          "hs", "ml", "ex", "exs", "vim", "sql", "r")
_register("config", "json", "toml", "yaml", "yml", "ini", "cfg", "conf",
          "config", "env", "xml", "properties", "lock", "editorconfig")


def classify(entry: FileEntry) -> str | None:
    """Return the colour category for ``entry``, or ``None`` for a plain file.

    Structural traits are checked before extension groups so a symlinked
    directory reads as a symlink, an executable script reads as executable,
    etc. Dotfiles that match no extension group fall back to ``hidden``.
    """
    if entry.is_parent:
        return "dir"
    if entry.is_symlink:
        return "symlink"
    if entry.is_dir:
        return "dir"
    if entry.is_executable:
        return "executable"
    suffix = PurePath(entry.name).suffix.lower()
    cat = _EXT_CATEGORY.get(suffix)
    if cat is not None:
        return cat
    if entry.name.startswith("."):
        return "hidden"
    return None


def role_for(category: str) -> str:
    """Theme role name for a category, e.g. ``"image"`` -> ``"panel.file.image"``."""
    return ROLE_PREFIX + category
