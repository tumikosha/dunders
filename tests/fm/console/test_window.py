from __future__ import annotations

from dunders.fm.console.window import ConsoleContent


async def test_append_increments_line_count():
    c = ConsoleContent(window_id="console-default")
    c.append(b"hello\n")
    assert c.buffer.line_count() == 1


async def test_busy_flag_cycle():
    c = ConsoleContent(window_id="console-default")
    assert c.busy is False
    c.busy = True
    c.mark_done(0)
    assert c.busy is False


async def test_get_commands_lists_scroll_and_clear():
    c = ConsoleContent(window_id="console-default")
    cmd_ids = {cmd.id for cmd in c.get_commands()}
    assert "console.scroll_up" in cmd_ids
    assert "console.scroll_bottom" in cmd_ids
    assert "console.clear" in cmd_ids


async def test_clear_command():
    c = ConsoleContent(window_id="console-default")
    c.append(b"hi\n")
    c._action_clear()
    assert c.buffer.line_count() == 0


async def test_id_attribute():
    c = ConsoleContent(window_id="console-default")
    assert c.id == "console-default"


async def test_mouse_wheel_scrolls_buffer(tmp_path):
    """Mouse wheel events on the buffer view must move ConsoleBuffer.view_offset.

    Regression: Textual's Widget._on_mouse_scroll_up no-ops on non-scrollable
    widgets and lets the event bubble up; if our handler isn't on the system
    dispatch path, the wheel does nothing. _BufferView overrides
    _on_mouse_scroll_{up,down} to scroll the buffer directly.
    """
    from textual import events
    from dunders.app import DundersApp

    # Use a non-panel launch mode (cli): in fm/we-mc Ctrl+O now drops into the
    # mc-style command screen, while editor/cli still toggle the embedded
    # console window this test exercises.
    app = DundersApp(launch_mode="cli", initial_path=tmp_path)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        # The console is no longer mounted at startup; reveal it first.
        app.action_toggle_console()
        await pilot.pause()
        cc = app.query_one(ConsoleContent)
        for i in range(50):
            cc.append(f"line {i:02d}\n".encode())
        await pilot.pause()
        view = cc._view
        assert view is not None
        assert cc.buffer.view_offset == 0
        view.post_message(
            events.MouseScrollUp(view, 0, 0, 0, 1, 0, False, False, False)
        )
        await pilot.pause()
        await pilot.pause()
        assert cc.buffer.view_offset > 0
        view.post_message(
            events.MouseScrollDown(view, 0, 0, 0, -1, 0, False, False, False)
        )
        await pilot.pause()
        await pilot.pause()
        assert cc.buffer.view_offset == 0
