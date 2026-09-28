"""Terminal cell-width calibration (dunders.windowing.cell_widths)."""

import pytest
import rich.cells as cells
from rich._unicode_data import load

from dunders.windowing import cell_widths
from dunders.windowing.cell_widths import VS16, Overrides, patched_table, plan_overrides

# What macOS Terminal.app reported via temp/probe_width.py.
TERMINAL_APP = {
    "⭐": 1, "⚡": 1, "⌚": 1, "⚡" + VS16: 2,
    "🗣": 2, "🌡": 2, "🖥": 2, "🗣" + VS16: 3,
}


@pytest.fixture
def restore_rich():
    original = cells.load_cell_table
    yield
    cells.load_cell_table = original
    cells.get_character_cell_size.cache_clear()
    cells.cached_cell_len.cache_clear()


def test_groups_contain_the_known_offenders():
    bmp, smp = cell_widths.groups()
    assert {"⭐", "⚡", "⌚", "✅"} <= bmp.members
    assert "🔌" not in bmp.members  # wide everywhere, never touched
    assert {"🗣", "🌡", "🖥"} <= smp.members
    assert "🧠" not in smp.members
    for group in (bmp, smp):
        assert set(group.probes) <= group.members


def test_terminal_app_measurements_patch_rich_widths(restore_rich):
    overrides = plan_overrides(TERMINAL_APP, cells.cell_len)
    assert overrides.widths[ord("⭐")] == 1
    assert overrides.widths[ord("🗣")] == 2
    cell_widths.apply(overrides)
    assert cells.cell_len("⭐") == 1
    assert cells.cell_len("⚡") == 1
    assert cells.cell_len("⚡" + VS16) == 2
    assert cells.cell_len("🗣") == 2
    assert cells.cell_len("🗣" + VS16) == 3
    assert cells.cell_len("🔌") == 2
    assert cells.cell_len("abc→—") == 5
    # The Markdown row that pushed the window border left.
    row = "| ⭐1 | **NL → команда** (🔌) | S |"
    assert cells.cell_len(row) == len(row) + 1  # only 🔌 is wide now
    # Other Unicode versions are left alone.
    assert cells.get_character_cell_size("⭐", "latest") == 2


def test_matching_terminal_changes_nothing():
    modern = {"⭐": 2, "⚡": 2, "⌚": 2, "⚡" + VS16: 2,
              "🗣": 1, "🌡": 1, "🖥": 1, "🗣" + VS16: 2}
    overrides = plan_overrides(modern, cells.cell_len)
    assert overrides.widths == {}


def test_disagreeing_or_missing_probes_change_nothing():
    mixed = dict(TERMINAL_APP, **{"⌚": 2})
    assert not plan_overrides(mixed, cells.cell_len).widths.get(ord("⭐"))
    missing = {k: v for k, v in TERMINAL_APP.items() if k != "🌡"}
    assert ord("🗣") not in plan_overrides(missing, cells.cell_len).widths


def test_patched_table_splits_ranges_and_keeps_order():
    table = load("latest")
    star = ord("⭐")
    patched = patched_table(table, Overrides(widths={star: 1, 0x1F5E3: 2}))
    assert patched.widths == sorted(patched.widths)
    starts = [s for s, _e, _w in patched.widths]
    assert len(starts) == len(set(starts))
    assert cell_widths._width_in(patched, star) == 1
    assert cell_widths._width_in(patched, 0x1F5E3) == 2
    assert cell_widths._width_in(patched, star - 1) == cell_widths._width_in(table, star - 1)
    assert cell_widths._width_in(patched, star + 1) == cell_widths._width_in(table, star + 1)


def test_calibrate_is_noop_without_a_tty(restore_rich):
    assert not cell_widths.calibrate()  # pytest captures stdin: not a tty
    assert cells.cell_len("⭐") == 2


def test_calibrate_can_be_disabled(monkeypatch, restore_rich):
    monkeypatch.setenv("DUNDERS_CELL_PROBE", "0")
    monkeypatch.setattr(cell_widths, "measure", lambda probes: pytest.fail("probed"))
    assert not cell_widths.calibrate()
