"""File-type classification and per-type colour rendering in file panels."""

from __future__ import annotations

from pathlib import Path

from dunders.fm.file_colors import classify, glyph_role_color, role_for
from dunders.fm.file_entry import FileEntry
from dunders.fm.file_panel import FilePanel
from dunders.windowing.palette import Palette, Style, Theme
from dunders.windowing.themes.modern_dark import modern_dark


def _entry(name: str, *, is_dir=False, is_symlink=False, is_executable=False) -> FileEntry:
    return FileEntry(
        path=Path("/tmp") / name,
        name=name,
        size=0,
        mtime=0.0,
        is_dir=is_dir,
        is_symlink=is_symlink,
        is_executable=is_executable,
    )


# --- classify ---------------------------------------------------------------

def test_classify_structural_traits_win_over_extension():
    # A symlink wins even if it has an "archive" extension.
    assert classify(_entry("link.zip", is_symlink=True)) == "symlink"
    # A directory named like a source file is still a dir.
    assert classify(_entry("pkg.py", is_dir=True)) == "dir"
    # Executable bit beats extension grouping.
    assert classify(_entry("run.txt", is_executable=True)) == "executable"


def test_classify_parent_is_dir():
    assert classify(_entry("..")) == "dir"


def test_classify_extension_groups():
    cases = {
        "photo.PNG": "image",       # case-insensitive
        "song.flac": "media",
        "clip.mkv": "media",
        "report.pdf": "document",
        "readme.md": "document",
        "main.py": "source",
        "build.sh": "source",
        "config.toml": "config",
        "data.json": "config",
        "backup.tar.gz": "archive",
        "bundle.zip": "archive",
    }
    for name, expected in cases.items():
        assert classify(_entry(name)) == expected, name


def test_classify_dotfile_without_known_ext_is_hidden():
    assert classify(_entry(".bashrc")) == "hidden"
    assert classify(_entry(".gitignore")) == "hidden"


def test_classify_dotfile_with_known_ext_uses_ext():
    # ".config.toml" → config wins over hidden (extension matched first).
    assert classify(_entry(".config.toml")) == "config"


def test_classify_plain_file_is_none():
    assert classify(_entry("notes")) is None
    assert classify(_entry("file.unknownext")) is None


def test_role_for():
    assert role_for("image") == "panel.file.image"


# --- per-type colour in row styling ----------------------------------------

def _panel_with_palette(theme: Theme) -> FilePanel:
    p = FilePanel(cwd=Path("/tmp"))
    pal = Palette(theme)
    p._get_palette = lambda: pal  # type: ignore[method-assign]
    return p


def test_entry_base_style_applies_type_fg():
    p = _panel_with_palette(modern_dark)
    style = p._entry_base_style(_entry("main.py"))
    expected = modern_dark.styles["panel.file.source"].fg
    assert style.color is not None
    assert expected.lstrip("#") in str(style.color).lower()


def test_entry_base_style_plain_file_has_no_type_fg():
    p = _panel_with_palette(modern_dark)
    # A plain file (no category) keeps the window.content base fg.
    plain = p._entry_base_style(_entry("notes"))
    base = p._base_style()
    assert str(plain.color) == str(base.color)


def test_entry_base_style_falls_back_when_theme_lacks_roles():
    # Theme with only a content role and no panel.file.* — graceful fallback.
    bare = Theme(name="bare", styles={"window.content": Style(fg="#d0d0d0", bg="#262626")})
    p = _panel_with_palette(bare)
    style = p._entry_base_style(_entry("photo.png"))
    base = p._base_style()
    assert str(style.color) == str(base.color)


def test_row_style_selected_yellow_overrides_type_fg():
    p = _panel_with_palette(modern_dark)
    style = p._row_style(
        is_cursor=False, is_selected=True, focused=True, entry=_entry("photo.png")
    )
    assert style.color is not None
    assert "yellow" in str(style.color)


def test_row_style_cursor_reverses_type_row():
    p = _panel_with_palette(modern_dark)
    style = p._row_style(
        is_cursor=True, is_selected=False, focused=True, entry=_entry("photo.png")
    )
    assert getattr(style, "reverse", False)


def test_row_style_focused_cursor_is_uniform_across_types():
    # The focused cursor highlight must look the same regardless of file type
    # (no type colour bleeding into the reverse-video bar).
    p = _panel_with_palette(modern_dark)
    img = p._row_style(is_cursor=True, is_selected=False, focused=True, entry=_entry("photo.png"))
    src = p._row_style(is_cursor=True, is_selected=False, focused=True, entry=_entry("main.py"))
    d = p._row_style(is_cursor=True, is_selected=False, focused=True, entry=_entry("sub", is_dir=True))
    assert str(img.color) == str(src.color) == str(d.color)
    assert str(img.bgcolor) == str(src.bgcolor) == str(d.bgcolor)
    # And it matches the plain themed base reversed (no type fg).
    plain = p._base_style()
    assert str(img.color) == str(plain.color)


# --- glyph_role_color ---------------------------------------------------------


def test_glyph_role_color_known():
    assert glyph_role_color("success") == "green"
    assert glyph_role_color("warning") == "yellow"
    assert glyph_role_color("muted") == "bright_black"


def test_glyph_role_color_unknown_is_none():
    assert glyph_role_color("nope") is None
