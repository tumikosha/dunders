import pytest
from textual.app import App, ComposeResult

from tyui.fm.dialogs import ConfirmDialog


class _Harness(App):
    def __init__(self, dialog) -> None:
        super().__init__()
        self.dialog = dialog
        self.results: list[bool] = []

    def compose(self) -> ComposeResult:
        yield self.dialog

    def on_confirm_dialog_result(self, event: ConfirmDialog.Result) -> None:
        self.results.append(event.confirmed)


@pytest.mark.asyncio
async def test_confirm_dialog_y_emits_confirmed_true():
    dlg = ConfirmDialog(prompt="Delete 3 items?")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        dlg.focus()
        await pilot.press("y")
        await pilot.pause()
        assert harness.results == [True]


@pytest.mark.asyncio
async def test_confirm_dialog_n_emits_confirmed_false():
    dlg = ConfirmDialog(prompt="Delete 3 items?")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        dlg.focus()
        await pilot.press("n")
        await pilot.pause()
        assert harness.results == [False]


@pytest.mark.asyncio
async def test_confirm_dialog_enter_confirms_and_escape_cancels():
    dlg = ConfirmDialog(prompt="Delete?")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        dlg.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert harness.results == [True]

    dlg2 = ConfirmDialog(prompt="Delete?")
    harness2 = _Harness(dlg2)
    async with harness2.run_test() as pilot:
        dlg2.focus()
        await pilot.press("escape")
        await pilot.pause()
        assert harness2.results == [False]


@pytest.mark.asyncio
async def test_confirm_dialog_renders_prompt():
    dlg = ConfirmDialog(prompt="Delete 7 items?")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Static
        prompt_widget = dlg.query_one("#cd-prompt", Static)
        assert "Delete 7 items?" in str(prompt_widget.render())


from tyui.fm.dialogs import InputDialog


class _InputHarness(App):
    def __init__(self, dialog) -> None:
        super().__init__()
        self.dialog = dialog
        self.submitted: list[str] = []
        self.cancelled: int = 0

    def compose(self) -> ComposeResult:
        yield self.dialog

    def on_input_dialog_submitted(self, event: InputDialog.Submitted) -> None:
        self.submitted.append(event.value)

    def on_input_dialog_cancelled(self, _event: InputDialog.Cancelled) -> None:
        self.cancelled += 1


@pytest.mark.asyncio
async def test_input_dialog_submit_emits_value():
    dlg = InputDialog(prompt="Create directory:")
    harness = _InputHarness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        dlg.set_value("newdir")
        dlg.action_submit()
        await pilot.pause()
        assert harness.submitted == ["newdir"]


@pytest.mark.asyncio
async def test_input_dialog_escape_cancels():
    dlg = InputDialog(prompt="Create directory:")
    harness = _InputHarness(dlg)
    async with harness.run_test() as pilot:
        dlg.focus_input()
        await pilot.press("escape")
        await pilot.pause()
        assert harness.cancelled == 1


@pytest.mark.asyncio
async def test_input_dialog_initial_value():
    dlg = InputDialog(prompt="Rename:", initial="oldname")
    harness = _InputHarness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        assert dlg.get_value() == "oldname"


import threading

from tyui.fm.dialogs import ProgressDialog


@pytest.mark.asyncio
async def test_progress_dialog_initial_render():
    dlg = ProgressDialog(title="Deleting...", total=10)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        line0 = "".join(seg.text for seg in dlg.render_line(0))
        assert "Deleting..." in line0
        line1 = "".join(seg.text for seg in dlg.render_line(1))
        assert "0 / 10" in line1


@pytest.mark.asyncio
async def test_progress_dialog_set_progress_updates_render():
    dlg = ProgressDialog(title="Deleting...", total=10)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        dlg.set_progress(3, 10)
        await pilot.pause()
        line1 = "".join(seg.text for seg in dlg.render_line(1))
        assert "3 / 10" in line1


@pytest.mark.asyncio
async def test_progress_dialog_cancel_sets_event():
    dlg = ProgressDialog(title="Working", total=5)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        dlg.focus()
        await pilot.press("c")
        await pilot.pause()
        assert dlg.cancel_event.is_set()


@pytest.mark.asyncio
async def test_progress_dialog_escape_also_cancels():
    dlg = ProgressDialog(title="Working", total=5)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        dlg.focus()
        await pilot.press("escape")
        await pilot.pause()
        assert dlg.cancel_event.is_set()


@pytest.mark.asyncio
async def test_progress_dialog_mouse_click_on_cancel_button_cancels():
    """Click anywhere on the [C] Cancel row triggers cancel."""
    from types import SimpleNamespace
    dlg = ProgressDialog(title="Working", total=5)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        stops: list[bool] = []
        dlg.on_click(SimpleNamespace(
            x=dlg._CANCEL_X + 2,
            y=dlg._CANCEL_Y,
            stop=lambda: stops.append(True),
        ))
        assert dlg.cancel_event.is_set()
        assert stops == [True]


@pytest.mark.asyncio
async def test_progress_dialog_mouse_click_outside_button_is_ignored():
    from types import SimpleNamespace
    dlg = ProgressDialog(title="Working", total=5)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        dlg.on_click(SimpleNamespace(x=4, y=0, stop=lambda: None))
        assert not dlg.cancel_event.is_set()
        dlg.on_click(SimpleNamespace(
            x=dlg._CANCEL_X + len(dlg._CANCEL_LABEL) + 5,
            y=dlg._CANCEL_Y,
            stop=lambda: None,
        ))
        assert not dlg.cancel_event.is_set()
