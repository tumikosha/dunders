import pytest
from textual.app import App, ComposeResult
from tyui.windowing.editor.search_panel import SearchPanel

class TestApp(App):
    def compose(self) -> ComposeResult:
        yield SearchPanel(id="sp")

@pytest.mark.asyncio
async def test_tab_navigation():
    async with TestApp().run_test() as pilot:
        sp: SearchPanel = pilot.app.query_one(SearchPanel)
        sp.show_find()
        await pilot.pause()
        
        assert pilot.app.focused is sp.find_input
        await pilot.press("tab")
        await pilot.pause()
        assert getattr(pilot.app.focused, "id", "").startswith("flag-")

        expected_ids = [
            "flag-whole_word",
            "flag-regex",
            "flag-wrap_around",
            "flag-in_selection",
            "status",
            "btn-search",
            "btn-close",
            "replace-input",
            "btn-replace",
            "btn-replace-all",
        ]
        for exp_id in expected_ids:
            await pilot.press("tab")
            await pilot.pause()
            focused = pilot.app.focused
            assert getattr(focused, "id", None) == exp_id, f"Expected {exp_id}, got {getattr(focused, 'id', None)}"

        await pilot.press("tab")
        await pilot.pause()
        assert pilot.app.focused is sp.find_input

@pytest.mark.asyncio
async def test_enter_activation():
    async with TestApp().run_test() as pilot:
        sp: SearchPanel = pilot.app.query_one(SearchPanel)
        sp.show_find()
        await pilot.pause()
        assert pilot.app.focused is sp.find_input

        messages = []
        def handler(msg):
            messages.append(msg)
        sp.post_message_handler = handler
        await pilot.press("enter")
        await pilot.pause()
        assert any(isinstance(m, sp.FindNext) for m in messages), "FindNext not emitted"
