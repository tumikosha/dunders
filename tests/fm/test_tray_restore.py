"""Hotkey restore (Ctrl+W chord then 1..9) for minimized windows in the IconTray."""

from __future__ import annotations

from pathlib import Path

import pytest

from dunders.app import DundersApp
from dunders.windowing import Window


def _editor_window(app: DundersApp) -> Window | None:
    if app.desktop is None:
        return None
    for w in app.desktop.windows + app.desktop.minimized_windows:
        if (w.id or "").startswith("editor-"):
            return w
    return None


@pytest.mark.asyncio
async def test_chord_ctrl_w_then_1_restores_first_tray_icon(tmp_path: Path):
    f = tmp_path / "hello.txt"
    f.write_text("hi")
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app._open_editor_window(f, read_only=False)
        for _ in range(10):
            await pilot.pause()
            if _editor_window(app) is not None:
                break
        win = _editor_window(app)
        assert win is not None, "editor window did not mount"
        app.desktop.minimize_window(win)
        await pilot.pause()
        assert win in app.desktop.minimized_windows
        # Chord: Ctrl+W then 1.
        await pilot.press("ctrl+w")
        await pilot.pause()
        assert app._tray_chord_pending is True
        await pilot.press("1")
        await pilot.pause()
        await pilot.pause()
        assert app._tray_chord_pending is False
        assert win not in app.desktop.minimized_windows
        assert win in app.desktop.windows


@pytest.mark.asyncio
async def test_chord_with_invalid_digit_is_cancelled(tmp_path: Path):
    f = tmp_path / "hello.txt"
    f.write_text("hi")
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app._open_editor_window(f, read_only=False)
        for _ in range(10):
            await pilot.pause()
            if _editor_window(app) is not None:
                break
        win = _editor_window(app)
        assert win is not None
        app.desktop.minimize_window(win)
        await pilot.pause()
        # Only one icon present; Ctrl+W then 2 must not restore and must
        # clear the pending flag.
        await pilot.press("ctrl+w")
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        assert app._tray_chord_pending is False
        assert win in app.desktop.minimized_windows


@pytest.mark.asyncio
async def test_chord_cancelled_by_non_digit(tmp_path: Path):
    """Pressing Ctrl+W then a non-digit cancels the chord without acting."""
    f = tmp_path / "hello.txt"
    f.write_text("hi")
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app._open_editor_window(f, read_only=False)
        for _ in range(10):
            await pilot.pause()
            if _editor_window(app) is not None:
                break
        win = _editor_window(app)
        assert win is not None
        app.desktop.minimize_window(win)
        await pilot.pause()
        await pilot.press("ctrl+w")
        await pilot.pause()
        assert app._tray_chord_pending is True
        await pilot.press("escape")
        await pilot.pause()
        assert app._tray_chord_pending is False
        assert win in app.desktop.minimized_windows


@pytest.mark.asyncio
async def test_chord_restores_with_editor_focused_and_no_digit_leak(tmp_path: Path):
    """In `we`-mode a sibling editor still holds focus when the chord fires.

    Regression: the digit half of the chord must be intercepted at the app
    level (priority binding) so the focused editor neither swallows it nor
    has it typed into its buffer; the tray icon must still restore.
    """
    f1 = tmp_path / "a.txt"
    f1.write_text("aaa")
    f2 = tmp_path / "b.txt"
    f2.write_text("bbb")
    app = DundersApp(launch_mode="we", initial_paths=[f1, f2])
    async with app.run_test(size=(120, 30)) as pilot:
        for _ in range(10):
            await pilot.pause()
        # Minimize the focused (top) editor; a sibling editor keeps focus.
        top = app.desktop.focused_window
        assert top is not None
        app.desktop.minimize_window(top)
        await pilot.pause()
        focused = app.focused
        assert hasattr(focused, "buffer"), "expected a focused editor widget"
        before = list(focused.buffer.lines)
        assert top in app.desktop.minimized_windows

        await pilot.press("ctrl+w")
        await pilot.pause()
        assert app._tray_chord_pending is True
        await pilot.press("1")
        await pilot.pause()
        await pilot.pause()

        assert app._tray_chord_pending is False
        assert top in app.desktop.windows, "tray icon was not restored"
        assert list(focused.buffer.lines) == before, "the digit leaked into the editor"


@pytest.mark.asyncio
async def test_normal_digit_typing_unaffected_when_not_in_chord(tmp_path: Path):
    """Digit bindings stay disabled unless a Ctrl+W chord is pending, so
    typing a digit into an editor works as usual."""
    f1 = tmp_path / "a.txt"
    f1.write_text("x")
    app = DundersApp(launch_mode="we", initial_paths=[f1])
    async with app.run_test(size=(120, 30)) as pilot:
        for _ in range(10):
            await pilot.pause()
        focused = app.focused
        assert hasattr(focused, "buffer")
        assert app._tray_chord_pending is False
        await pilot.press("7")
        await pilot.pause()
        assert any("7" in line for line in focused.buffer.lines)


@pytest.mark.asyncio
async def test_icon_tray_shows_hint_and_position_number(tmp_path: Path):
    f = tmp_path / "hello.txt"
    f.write_text("hi")
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app._open_editor_window(f, read_only=False)
        for _ in range(10):
            await pilot.pause()
            if _editor_window(app) is not None:
                break
        win = _editor_window(app)
        assert win is not None
        app.desktop.minimize_window(win)
        await pilot.pause()
        from dunders.windowing.desktop import IconTray
        tray = app.query_one(IconTray)
        strip = tray.render_line(0)
        text = "".join(seg.text for seg in strip)
        assert text.startswith("Ctrl+W + "), (
            f"expected hint prefix 'Ctrl+W + ' in tray, got {text!r}"
        )
        assert "[1 " in text, f"expected '[1 ' position number, got {text!r}"
