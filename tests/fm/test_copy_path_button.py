import pytest
from textual.geometry import Offset

from dunders.app import DundersApp
from dunders.windowing.core import clipboard


def _panel_window(app, win_id):
    for w in app.desktop.windows:
        if str(w.id) == win_id:
            return w
    return None


@pytest.mark.asyncio
async def test_panels_have_copy_box(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        for wid in ("panel-left", "panel-right"):
            win = _panel_window(app, wid)
            assert win is not None
            assert win.decorations.copy_box is True
            assert win.decorations.close_box is True


@pytest.mark.asyncio
async def test_hit_test_copy_box_after_close(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        win = _panel_window(app, "panel-left")
        # close_box at x 1..3, copy_box at x 4..6 on the top edge.
        assert win.hit_test(Offset(2, 0)) == "close_box"
        assert win.hit_test(Offset(5, 0)) == "copy_box"


@pytest.mark.asyncio
async def test_copy_box_click_copies_cwd_and_notifies(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        copied = []
        notes = []
        monkeypatch.setattr(clipboard, "copy", lambda text, app=None: copied.append(text))
        monkeypatch.setattr(app, "notify", lambda msg, **k: notes.append(msg))
        win = _panel_window(app, "panel-left")
        event = type("E", (), {"window": win})()
        app.on_window_copy_box_clicked(event)
        await pilot.pause()
        assert copied == [str(win.content.cwd)]
        assert any("copied" in n for n in notes)
