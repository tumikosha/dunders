import json
import pytest
from textual.app import App, ComposeResult
from dunders.windowing.editor.content import EditorContent
from dunders.windowing.editor.search_panel import SearchPanel


class _Host(App):
    def __init__(self, text: str = "foo bar foo") -> None:
        super().__init__()
        self.content = EditorContent(initial_text=text, enable_macros=True)

    def compose(self) -> ComposeResult:
        yield self.content


@pytest.mark.asyncio
async def test_records_search_action_on_replace_one():
    app = _Host("foo foo")
    async with app.run_test() as pilot:
        rec = app.content._macro_recorder
        rec.start_recording()
        await pilot.press("f4")
        sp = app.query_one(SearchPanel)
        sp.find_input.value = "foo"
        await pilot.pause()
        sp.replace_input.value = "X"
        sp.post_message(SearchPanel.ReplaceOne("X"))
        await pilot.pause()
        actions = rec.stop_recording()
        kinds = [a.kind for a in actions]
        assert "search" in kinds
        assert "replace_one" in kinds
        s = next(a for a in actions if a.kind == "search")
        payload = json.loads(s.data)
        assert payload["pattern"] == "foo"
        assert payload["options"]["case_sensitive"] is False


@pytest.mark.asyncio
async def test_records_replace_all_action_with_confirmation():
    app = _Host("foo foo")
    async with app.run_test() as pilot:
        rec = app.content._macro_recorder
        rec.start_recording()
        app.content._confirm_replace_all = lambda count, callback: callback(True)
        await pilot.press("f4")
        sp = app.query_one(SearchPanel)
        sp.find_input.value = "foo"
        await pilot.pause()
        sp.replace_input.value = "X"
        await pilot.press("f6")
        await pilot.pause()
        actions = rec.stop_recording()
        kinds = [a.kind for a in actions]
        assert "replace_all" in kinds


@pytest.mark.asyncio
async def test_replay_search_and_replace_all():
    app = _Host("foo foo foo")
    async with app.run_test() as pilot:
        from dunders.windowing.core.macro import MacroAction
        actions = [
            MacroAction(
                kind="search",
                data=json.dumps({
                    "pattern": "foo",
                    "options": {
                        "regex": False, "case_sensitive": True,
                        "whole_word": False, "wrap_around": True,
                        "in_selection": False,
                    },
                }),
            ),
            MacroAction(kind="replace_all", data=json.dumps({"replacement": "Y"})),
        ]
        app.content._register_macro_replay(app, "ctrl+m", actions)
        await pilot.press("ctrl+m")
        await pilot.pause()
        assert app.content._editor.buffer.lines == ["Y Y Y"]


@pytest.mark.asyncio
async def test_replay_old_keypress_macro_still_works():
    app = _Host("")
    async with app.run_test() as pilot:
        from dunders.windowing.core.macro import MacroAction
        actions = [MacroAction(kind="keypress", data="a|a")]
        app.content._register_macro_replay(app, "ctrl+m", actions)
        await pilot.press("ctrl+m")
        await pilot.pause()
        assert app.content._editor.buffer.lines == ["a"]


@pytest.mark.asyncio
async def test_replay_bad_regex_search_does_not_crash():
    app = _Host("foo")
    async with app.run_test() as pilot:
        from dunders.windowing.core.macro import MacroAction
        actions = [
            MacroAction(
                kind="search",
                data=json.dumps({
                    "pattern": "(unclosed",
                    "options": {
                        "regex": True, "case_sensitive": True,
                        "whole_word": False, "wrap_around": True,
                        "in_selection": False,
                    },
                }),
            ),
        ]
        app.content._register_macro_replay(app, "ctrl+m", actions)
        await pilot.press("ctrl+m")
        await pilot.pause()
        assert app.content._editor.buffer.lines == ["foo"]


@pytest.mark.asyncio
async def test_panel_keys_not_recorded_as_keypress():
    app = _Host("foo")
    async with app.run_test() as pilot:
        rec = app.content._macro_recorder
        rec.start_recording()
        await pilot.press("ctrl+f")
        sp = app.query_one(SearchPanel)
        sp.find_input.value = "foo"
        await pilot.pause()
        await pilot.press("escape")
        actions = rec.stop_recording()
        kinds = [a.kind for a in actions]
        assert "keypress" not in kinds
