"""Terminal-resize relayout regression tests.

Covers two fixes:

1. Maximized windows must keep filling the desktop on resize (the Desktop's
   clamp only shrinks, never grows — so without a re-fill they got stuck at
   the old size when the terminal was enlarged).
2. In non-fm launch modes (``we``/``editor``/``cli``) ``App.on_resize`` now
   re-tiles panels and refills cascade editors. Previously it was gated to
   fm only, so on resize the file panels overlapped on shrink and nothing
   grew on enlarge.
"""

from __future__ import annotations

import pytest

from dunders.app import DundersApp
from dunders.windowing import Window


async def _settle(pilot):
    await pilot.pause()
    await pilot.pause()


@pytest.mark.parametrize("width,height", [(160, 50), (70, 20)])
async def test_fm_panels_stay_tiled_after_resize(width, height):
    app = DundersApp(launch_mode="fm")
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        await pilot.resize_terminal(width, height)
        await _settle(pilot)

        usable_w, _ = app.desktop.usable_size
        left = app.desktop.query_one("#panel-left", Window)
        right = app.desktop.query_one("#panel-right", Window)
        # Two adjacent halves filling the full width — no overlap, no gap.
        assert right.region.x - left.region.x == left.region.width
        assert left.region.width + right.region.width == usable_w


@pytest.mark.parametrize("width,height", [(160, 50), (200, 60), (70, 20)])
async def test_maximized_window_tracks_desktop_on_resize(width, height):
    app = DundersApp(launch_mode="fm")
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        left = app.desktop.query_one("#panel-left", Window)
        app.manager.toggle_maximize(left)
        await _settle(pilot)
        assert left.maximized

        await pilot.resize_terminal(width, height)
        await _settle(pilot)

        usable_w, usable_h = app.desktop.usable_size
        assert left.region.x == 0
        assert left.region.width == usable_w
        assert left.region.height == usable_h


@pytest.mark.parametrize("width,height", [(160, 50), (70, 20)])
async def test_we_mode_relayout_on_resize(tmp_path, width, height):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    app = DundersApp(launch_mode="we", initial_paths=[str(f)])
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        # Reveal both file panels (hidden by default in we mode).
        app._focus_panel("panel-left")
        app._focus_panel("panel-right")
        await _settle(pilot)

        await pilot.resize_terminal(width, height)
        await _settle(pilot)

        usable_w, _ = app.desktop.usable_size
        left = app.desktop.query_one("#panel-left", Window)
        right = app.desktop.query_one("#panel-right", Window)
        # Panels stay tiled as two adjacent halves — no overlap, no gap.
        assert right.region.x - left.region.x == left.region.width
        assert left.region.width + right.region.width == usable_w
        # The cascade editor window keeps filling the full desktop width.
        editor = app.desktop.query_one(f"#{app._cascade_ids[0]}", Window)
        assert editor.region.width == usable_w
