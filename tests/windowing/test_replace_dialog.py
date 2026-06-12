"""Keyboard navigation regression tests for ReplaceAllDialog."""

import pytest
from textual.app import App, ComposeResult

from dunders.fm.dialogs import DialogButton
from dunders.windowing.editor.replace_dialog import ReplaceAllDialog


class _RHarness(App):
    def __init__(self, count: int) -> None:
        super().__init__()
        self._count = count
        self.results: list[bool] = []
        self.dialog: ReplaceAllDialog | None = None

    def compose(self) -> ComposeResult:
        self.dialog = ReplaceAllDialog(
            count=self._count,
            callback=lambda confirmed: self.results.append(confirmed),
        )
        yield self.dialog


@pytest.mark.asyncio
async def test_replace_all_dialog_initial_focus_on_yes():
    harness = _RHarness(count=5)
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        focused = harness.focused
        assert isinstance(focused, DialogButton) and focused.id == "ra-yes"


@pytest.mark.asyncio
async def test_replace_all_dialog_tab_cycles_buttons():
    harness = _RHarness(count=5)
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "ra-no"
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "ra-yes"


@pytest.mark.asyncio
async def test_replace_all_dialog_y_confirms():
    harness = _RHarness(count=3)
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert harness.results == [True]


@pytest.mark.asyncio
async def test_replace_all_dialog_enter_on_cancel_dismisses():
    harness = _RHarness(count=3)
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Tab to cancel button, then Enter.
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "ra-no"
        await pilot.press("enter")
        await pilot.pause()
        assert harness.results == [False]
