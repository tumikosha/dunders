from pathlib import Path
import stat
import unicodedata

from rich.cells import cell_len

from dunders.core.vfs import VfsPath
from dunders.fm.file_entry import FileEntry
from dunders.fm import panel_view as pv
from dunders.fm.panel_view import PanelViewMode, _name_prefix


def _entry(name, *, is_dir=False, is_symlink=False, is_executable=False, mode=0):
    return FileEntry(
        path=Path("/x") / name, name=name, size=0, mtime=0.0,
        is_dir=is_dir, is_symlink=is_symlink, is_executable=is_executable, mode=mode,
    )


def test_column_count():
    assert pv.column_count(PanelViewMode.BRIEF) == 2
    assert pv.column_count(PanelViewMode.MEDIUM) == 3
    assert pv.column_count(PanelViewMode.FULL) == 1
    assert pv.column_count(PanelViewMode.DETAILED) == 1
    assert pv.column_count(PanelViewMode.DESCRIPTION) == 1


def test_is_multicolumn():
    assert pv.is_multicolumn(PanelViewMode.BRIEF) is True
    assert pv.is_multicolumn(PanelViewMode.MEDIUM) is True
    assert pv.is_multicolumn(PanelViewMode.FULL) is False


def test_format_attrs_is_10_chars_and_typed():
    d = _entry("dir", is_dir=True, mode=stat.S_IFDIR | 0o755)
    f = _entry("f", mode=stat.S_IFREG | 0o644)
    lnk = _entry("ln", is_symlink=True, mode=stat.S_IFLNK | 0o777)
    assert pv.format_attrs(d) == "drwxr-xr-x"
    assert pv.format_attrs(f) == "-rw-r--r--"
    assert pv.format_attrs(lnk) == "lrwxrwxrwx"
    assert len(pv.format_attrs(_entry("z"))) == 10


def test_describe_entry():
    assert pv.describe_entry(_entry("..")) == "Parent directory"
    assert pv.describe_entry(_entry("d", is_dir=True)) == "Directory"
    assert pv.describe_entry(_entry("ln", is_symlink=True)) == "Symlink"
    assert pv.describe_entry(_entry("a.py")) == "Python source"
    assert pv.describe_entry(_entry("a.PNG")) == "PNG image"        # case-insensitive
    assert pv.describe_entry(_entry("run", is_executable=True)) == "Executable"
    assert pv.describe_entry(_entry("weirdfile")) == "File"


def test_name_col_width_full_matches_historical():
    # width 40: 40 - 7(size) - 16(date) - 2(gutters) = 15
    assert pv.name_col_width(PanelViewMode.FULL, 40) == 15


def test_name_col_width_detailed():
    # 40 - 7(size) - 11(date) - 10(attr) - 3(gutters) = 9
    assert pv.name_col_width(PanelViewMode.DETAILED, 40) == 9


def test_column_width_splits_evenly():
    # width 40, k=2, gutter 1 -> (40 - 1) // 2 = 19
    assert pv.column_width(40, 2) == 19
    # k=3 -> (40 - 2) // 3 = 12
    assert pv.column_width(40, 3) == 12


def test_row_text_single_full_layout():
    e = _entry("file.txt", mode=stat.S_IFREG | 0o644)
    text = pv.row_text_single(PanelViewMode.FULL, e, 40)
    assert len(text) == 40
    assert text.startswith(" file.txt")        # leading ls -F space prefix


def test_row_text_single_detailed_has_attrs():
    e = _entry("file.txt", mode=stat.S_IFREG | 0o644)
    text = pv.row_text_single(PanelViewMode.DETAILED, e, 40)
    assert len(text) == 40
    assert text.rstrip().endswith("-rw-r--r--")


def test_row_text_single_description():
    e = _entry("a.py", mode=stat.S_IFREG | 0o644)
    text = pv.row_text_single(PanelViewMode.DESCRIPTION, e, 40)
    assert len(text) == 40
    assert "Python source" in text


def test_format_cell_truncates_with_ellipsis():
    e = _entry("a_very_long_name.txt")
    cell = pv.format_cell(e, 10)
    assert len(cell) == 10
    assert cell.endswith("…")


def test_format_cell_dir_prefix():
    e = _entry("src", is_dir=True)
    assert pv.format_cell(e, 10).startswith("/src")
    assert len(pv.format_cell(e, 10)) == 10


def test_format_cell_minimum_width():
    e = _entry("longname")
    assert len(pv.format_cell(e, 1)) == 1
    assert len(pv.format_cell(e, 2)) == 2


def test_short_mode_is_name_and_size_single_column():
    e = _entry("file.txt", mode=stat.S_IFREG | 0o644)
    assert pv.column_count(PanelViewMode.SHORT) == 1
    assert pv.is_multicolumn(PanelViewMode.SHORT) is False
    # name col = width - size(7) - gutter(1) = 32
    assert pv.name_col_width(PanelViewMode.SHORT, 40) == 32
    text = pv.row_text_single(PanelViewMode.SHORT, e, 40)
    assert len(text) == 40
    assert text.startswith(" file.txt")
    assert text.rstrip().endswith("0")        # size column (format_size(0))
    # Short omits the Date column that Full carries.
    assert text != pv.row_text_single(PanelViewMode.FULL, e, 40)


def test_row_text_uses_vertical_bar_separator():
    assert pv.COL_SEP == "│"
    e = _entry("file.txt", mode=stat.S_IFREG | 0o644)
    for mode in (PanelViewMode.FULL, PanelViewMode.SHORT,
                 PanelViewMode.DETAILED, PanelViewMode.DESCRIPTION):
        assert pv.COL_SEP in pv.row_text_single(mode, e, 40)


def test_empty_row_text_keeps_separators_at_same_columns():
    e = _entry("file.txt", mode=stat.S_IFREG | 0o644)
    for mode in (PanelViewMode.FULL, PanelViewMode.SHORT,
                 PanelViewMode.DETAILED, PanelViewMode.DESCRIPTION):
        empty = pv.empty_row_text(mode, 40)
        assert len(empty) == 40
        assert empty.strip(" " + pv.COL_SEP) == ""        # spaces + separators only
        # Separator positions match the real row's separators.
        full = pv.row_text_single(mode, e, 40)
        sep_cols_empty = [i for i, c in enumerate(empty) if c == pv.COL_SEP]
        sep_cols_full = [i for i, c in enumerate(full) if c == pv.COL_SEP]
        assert sep_cols_empty == sep_cols_full


def _docker_entry(**kw):
    base = dict(loc=VfsPath(scheme="docker", root="web"), name="web",
                size=0, mtime=0.0, is_dir=True)
    base.update(kw)
    return FileEntry(**base)


def test_name_prefix_ignores_provider_glyph():
    # The state glyph lives in a dedicated provider column (Docker "S"), NOT in
    # the name prefix — so the prefix is the plain ls -F marker.
    assert _name_prefix(_docker_entry(extra={"glyph": "▶"})) == "/"  # is_dir → "/"


def test_default_prefix_when_no_glyph():
    assert _name_prefix(_docker_entry(extra={})) == "/"  # is_dir → "/"


# --- terminal-cell widths (NFD / wide chars) ---------------------------------
#
# macOS stores filenames in NFD, so "й" is "и" + U+0306: two code points, one
# cell. Rows padded with str.ljust came out one cell short per combining mark
# and the column separator drifted left on exactly those rows.


_NFD_NAME = unicodedata.normalize(
    "NFD", "Макс Вебер - Хозяйство и общество.pdf"
)


def test_nfd_name_is_two_code_points_wider_than_it_is_cells():
    """Guards the premise: without it the rest of these tests prove nothing."""
    assert len(_NFD_NAME) == cell_len(_NFD_NAME) + 1


def test_row_text_single_is_exactly_width_cells_for_an_nfd_name():
    for mode in (
        PanelViewMode.FULL,
        PanelViewMode.SHORT,
        PanelViewMode.DETAILED,
        PanelViewMode.DESCRIPTION,
    ):
        text = pv.row_text_single(mode, _entry(_NFD_NAME), 58)
        assert cell_len(text) == 58, mode


def test_separator_lands_in_the_same_cell_for_nfd_and_ascii_names():
    """The reported bug: the Name|Size border drifted left on NFD rows."""
    def sep_cell(text: str) -> int:
        return cell_len(text[: text.index(pv.COL_SEP)])

    ascii_row = pv.row_text_single(PanelViewMode.FULL, _entry("plain.pdf"), 58)
    nfd_row = pv.row_text_single(PanelViewMode.FULL, _entry(_NFD_NAME), 58)
    assert sep_cell(nfd_row) == sep_cell(ascii_row)


def test_wide_chars_do_not_overrun_the_name_column():
    """The mirror-image failure: len() undercounts a 2-cell CJK/emoji char."""
    wide = pv.row_text_single(PanelViewMode.FULL, _entry("日本語" * 20), 58)
    assert cell_len(wide) == 58


def test_format_cell_pads_an_nfd_name_to_exact_cells():
    assert cell_len(pv.format_cell(_entry(_NFD_NAME), 20)) == 20
    assert cell_len(pv.format_cell(_entry("ascii.txt"), 20)) == 20
