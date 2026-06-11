from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from textual.widgets.text_area import Selection

from tyui.fm.commandline import CommandLine
from tyui.fm.console.history import History
from tyui.windowing.core import clipboard


class _Probe(App):
    def __init__(self, history: History) -> None:
        super().__init__()
        self._history = history

    def compose(self) -> ComposeResult:
        self.cl = CommandLine(id="cl", history=self._history)
        yield self.cl


@pytest.mark.asyncio
async def test_submitted_default_not_anonymous(tmp_path):
    h = History(tmp_path / "h", cap=10)
    app = _Probe(h)
    async with app.run_test() as pilot:
        sent: list[CommandLine.Submitted] = []
        app.cl.subscribe(sent.append)
        app.cl.set_text("ls")
        app.cl._submit(anonymous=False)
        await pilot.pause()
        assert sent[0].anonymous is False
        assert sent[0].text == "ls"


@pytest.mark.asyncio
async def test_alt_enter_marks_anonymous(tmp_path):
    h = History(tmp_path / "h", cap=10)
    app = _Probe(h)
    async with app.run_test() as pilot:
        sent: list[CommandLine.Submitted] = []
        app.cl.subscribe(sent.append)
        app.cl.set_text("echo x")
        app.cl._submit(anonymous=True)
        await pilot.pause()
        assert sent[0].anonymous is True


@pytest.mark.asyncio
async def test_ctrl_d_on_empty_posts_eof(tmp_path):
    h = History(tmp_path / "h", cap=10)
    app = _Probe(h)
    async with app.run_test() as pilot:
        # Empty buffer → EofRequested.
        seen: list[CommandLine.EofRequested] = []
        app.cl.action_ctrl_d()  # bypass key dispatch; just exercise the action.

        # Use a small loop to drain the message queue.
        events: list = []
        original = app.cl.post_message
        def capture(msg):
            events.append(msg)
            return original(msg)
        app.cl.post_message = capture  # type: ignore[method-assign]
        app.cl.action_ctrl_d()
        await pilot.pause()
        assert any(isinstance(e, CommandLine.EofRequested) for e in events)


@pytest.mark.asyncio
async def test_ctrl_d_with_text_does_not_post_eof(tmp_path):
    h = History(tmp_path / "h", cap=10)
    app = _Probe(h)
    async with app.run_test() as pilot:
        app.cl.set_text("abc")
        events: list = []
        original = app.cl.post_message
        app.cl.post_message = lambda m: (events.append(m), original(m))[-1]  # type: ignore[method-assign]
        app.cl.action_ctrl_d()
        await pilot.pause()
        assert not any(isinstance(e, CommandLine.EofRequested) for e in events)


@pytest.mark.asyncio
async def test_set_busy_swaps_hint(tmp_path):
    h = History(tmp_path / "h", cap=10)
    app = _Probe(h)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.cl.set_busy(True)
        await pilot.pause()
        assert "stdin" in str(app.cl._hint._Static__content)
        app.cl.set_busy(False)
        await pilot.pause()
        assert "Alt+C" in str(app.cl._hint._Static__content)


@pytest.mark.asyncio
async def test_history_up_down(tmp_path):
    h = History(tmp_path / "h", cap=10)
    h.append("ls")
    h.append("pwd")
    app = _Probe(h)
    async with app.run_test() as _pilot:
        app.cl.history_prev()
        assert app.cl.text == "pwd"
        app.cl.history_prev()
        assert app.cl.text == "ls"
        app.cl.history_next()
        assert app.cl.text == "pwd"


@pytest.mark.asyncio
async def test_ctrl_c_with_selection_copies_and_keeps_text(tmp_path, monkeypatch):
    h = History(tmp_path / "h", cap=10)
    app = _Probe(h)
    async with app.run_test() as pilot:
        copied: list[str] = []
        monkeypatch.setattr(clipboard, "copy", lambda text, app=None: copied.append(text))
        app.cl.set_text("hello world")
        app.cl._input.selection = Selection((0, 0), (0, 5))  # "hello"
        await pilot.pause()
        app.cl.action_ctrl_c()
        await pilot.pause()
        assert copied == ["hello"]
        # Selection-copy must NOT clear the command buffer.
        assert app.cl.text == "hello world"


@pytest.mark.asyncio
async def test_ctrl_c_without_selection_clears_buffer(tmp_path, monkeypatch):
    h = History(tmp_path / "h", cap=10)
    app = _Probe(h)
    async with app.run_test() as pilot:
        copied: list[str] = []
        monkeypatch.setattr(clipboard, "copy", lambda text, app=None: copied.append(text))
        app.cl.set_text("some command")
        app.cl._input.selection = Selection((0, 0), (0, 0))  # no selection
        await pilot.pause()
        app.cl.action_ctrl_c()
        await pilot.pause()
        assert copied == []
        assert app.cl.text == ""


@pytest.mark.asyncio
async def test_insert_at_cursor_into_empty(tmp_path):
    h = History(tmp_path / "h", cap=10)
    app = _Probe(h)
    async with app.run_test() as pilot:
        app.cl.insert_at_cursor("foo ")
        await pilot.pause()
        assert app.cl.text == "foo "


@pytest.mark.asyncio
async def test_insert_at_cursor_mid_text(tmp_path):
    h = History(tmp_path / "h", cap=10)
    app = _Probe(h)
    async with app.run_test() as pilot:
        app.cl.set_text("abcd")
        app.cl._input.move_cursor((0, 2))  # between "b" and "c"
        app.cl.insert_at_cursor("X")
        await pilot.pause()
        assert app.cl.text == "abXcd"
