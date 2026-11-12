"""Every copy to the clipboard is confirmed by a toast (DundersApp.copy_to_clipboard)."""

from dunders.app import DundersApp
from dunders.windowing.core import clipboard


def test_copy_notice_formats():
    assert clipboard.copy_notice("abc") == "Copied to clipboard: abc"
    assert clipboard.copy_notice("x" * 61) == "Copied 61 chars to clipboard"
    assert clipboard.copy_notice("a\nb\nc") == "Copied 3 lines (5 chars) to clipboard"


async def test_every_app_copy_writes_system_clipboard_and_notifies(monkeypatch, tmp_path):
    sys_copies: list[str] = []
    monkeypatch.setattr(clipboard, "system_copy", lambda t: sys_copies.append(t) or True)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._notifications.clear()
        # Our helper: one system copy, one toast (not two).
        clipboard.copy("/some/path", app=app)
        # Textual built-ins (Input Ctrl+C, screen selection) call this directly.
        app.copy_to_clipboard("from textual")
        await pilot.pause()
        assert sys_copies == ["/some/path", "from textual"]
        assert [n.message for n in app._notifications] == [
            "Copied to clipboard: /some/path",
            "Copied to clipboard: from textual",
        ]


async def test_failed_system_copy_warns(monkeypatch, tmp_path):
    monkeypatch.setattr(clipboard, "system_copy", lambda _t: False)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._notifications.clear()
        app.copy_to_clipboard("x")
        await pilot.pause()
        (note,) = list(app._notifications)
        assert note.severity == "warning"
        assert "OSC 52 only" in note.message


async def test_empty_copy_is_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(clipboard, "system_copy", lambda _t: True)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._notifications.clear()
        app.copy_to_clipboard("")
        await pilot.pause()
        assert not list(app._notifications)
