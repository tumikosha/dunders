import pytest
from textual.app import App, ComposeResult
from tyui.windowing.editor.search_panel import SearchPanel
from tyui.windowing.editor.content import EditorContent


class _Host(App):
    def compose(self) -> ComposeResult:
        yield SearchPanel(id="sp")


@pytest.mark.asyncio
async def test_panel_starts_hidden():
    async with _Host().run_test() as pilot:
        sp = pilot.app.query_one(SearchPanel)
        assert sp.display is False


@pytest.mark.asyncio
async def test_panel_show_find_mode_focuses_input():
    async with _Host().run_test() as pilot:
        sp = pilot.app.query_one(SearchPanel)
        sp.show_find()
        await pilot.pause()
        assert sp.display is True
        assert sp.mode == "find"
        assert pilot.app.focused is sp.find_input


@pytest.mark.asyncio
async def test_panel_close_hides_and_emits():
    received: list[str] = []

    class App2(_Host):
        def on_search_panel_closed(self, _msg):
            received.append("closed")

    async with App2().run_test() as pilot:
        sp = pilot.app.query_one(SearchPanel)
        sp.show_find()
        await pilot.pause()
        sp.close()
        await pilot.pause()
        assert sp.display is False
        assert received == ["closed"]


class _EditorHost(App):
    def __init__(self, text: str = "foo bar foo") -> None:
        super().__init__()
        self._content = EditorContent(initial_text=text, enable_macros=False)

    def compose(self) -> ComposeResult:
        yield self._content


@pytest.mark.asyncio
async def test_ctrl_f_opens_find_panel():
    app = _EditorHost()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+f")
        sp = app.query_one(SearchPanel)
        assert sp.display is True
        assert sp.mode == "find"


@pytest.mark.asyncio
async def test_pattern_change_highlights_in_widget():
    app = _EditorHost("foo bar foo")
    async with app.run_test() as pilot:
        await pilot.press("ctrl+f")
        sp = app.query_one(SearchPanel)
        sp.find_input.value = "foo"
        await pilot.pause()
        editor = app._content._editor
        assert len(editor._search_matches) == 2


@pytest.mark.asyncio
async def test_escape_closes_panel_and_clears():
    app = _EditorHost("foo")
    async with app.run_test() as pilot:
        await pilot.press("ctrl+f")
        sp = app.query_one(SearchPanel)
        sp.find_input.value = "foo"
        await pilot.pause()
        await pilot.press("escape")
        assert sp.display is False
        editor = app._content._editor
        assert editor._search_matches == []


@pytest.mark.asyncio
async def test_replace_one_via_panel():
    app = _EditorHost("foo bar foo")
    async with app.run_test() as pilot:
        await pilot.press("f4")
        sp = app.query_one(SearchPanel)
        sp.find_input.value = "foo"
        await pilot.pause()
        sp.replace_input.value = "X"
        sp.post_message(SearchPanel.ReplaceOne("X"))
        await pilot.pause()
        assert app._content._editor.buffer.lines == ["X bar foo"]


@pytest.mark.asyncio
async def test_replace_all_with_yes_confirms():
    app = _EditorHost("foo foo foo")
    async with app.run_test() as pilot:
        await pilot.press("f4")
        sp = app.query_one(SearchPanel)
        sp.find_input.value = "foo"
        await pilot.pause()
        sp.replace_input.value = "X"
        app._content._confirm_replace_all = lambda count, callback: callback(True)
        await pilot.press("f6")
        await pilot.pause()
        assert app._content._editor.buffer.lines == ["X X X"]


@pytest.mark.asyncio
async def test_replace_all_with_no_does_nothing():
    app = _EditorHost("foo foo")
    async with app.run_test() as pilot:
        await pilot.press("f4")
        sp = app.query_one(SearchPanel)
        sp.find_input.value = "foo"
        await pilot.pause()
        sp.replace_input.value = "X"
        app._content._confirm_replace_all = lambda count, callback: callback(False)
        await pilot.press("f6")
        await pilot.pause()
        assert app._content._editor.buffer.lines == ["foo foo"]


@pytest.mark.asyncio
async def test_flag_toggle_updates_search_results():
    app = _EditorHost("Foo foo")
    async with app.run_test() as pilot:
        await pilot.press("ctrl+f")
        sp = app.query_one(SearchPanel)
        sp.find_input.value = "foo"
        await pilot.pause()
        editor = app._content._editor
        assert len(editor._search_matches) == 2
        sp._toggle_flag("case_sensitive")
        await pilot.pause()
        assert sp.options.case_sensitive is True
        assert len(editor._search_matches) == 1
