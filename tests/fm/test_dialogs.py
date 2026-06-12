import pytest
from textual.app import App, ComposeResult

from dunders.fm.dialogs import ConfirmDialog


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


from dunders.fm.dialogs import InputDialog


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



from dunders.fm.dialogs import ProgressDialog


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


# --- Keyboard navigation across dialog buttons --------------------------
#
# These regression tests guard the FocusChainMixin behaviour: every modal
# dialog with multiple buttons must let the user reach each button via
# Tab / Shift+Tab / Left / Right and activate it via Enter — without
# touching the mouse.

from dunders.fm.dialogs import (
    CopyMoveDialog,
    NewFileDialog,
    ShadowButton,
    ChangeAttributesDialog,
    DialogButton,
)


@pytest.mark.asyncio
async def test_confirm_dialog_initial_focus_on_yes():
    dlg = ConfirmDialog(prompt="Delete?")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        focused = harness.focused
        assert isinstance(focused, ShadowButton)
        assert focused.id == "cd-yes"


@pytest.mark.asyncio
async def test_confirm_dialog_tab_cycles_yes_no():
    dlg = ConfirmDialog(prompt="Delete?")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "cd-no"
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "cd-yes"


@pytest.mark.asyncio
async def test_confirm_dialog_right_left_swap_buttons():
    dlg = ConfirmDialog(prompt="Delete?")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert harness.focused.id == "cd-no"
        await pilot.press("left")
        await pilot.pause()
        assert harness.focused.id == "cd-yes"


@pytest.mark.asyncio
async def test_confirm_dialog_enter_on_no_cancels():
    dlg = ConfirmDialog(prompt="Delete?")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("tab")  # focus -> cd-no
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert harness.results == [False]


class _CMHarness(App):
    def __init__(self, *, initial: str) -> None:
        super().__init__()
        self._initial = initial
        self.dialog: CopyMoveDialog | None = None

    def compose(self) -> ComposeResult:
        # Construct INSIDE compose: Input(value=...) hits a reactive
        # watcher that touches self.app, which fails outside an active
        # app context.
        self.dialog = CopyMoveDialog(
            prompt="Copy x to:", initial=self._initial, title="Copy"
        )
        yield self.dialog


@pytest.mark.asyncio
async def test_copymove_dialog_tab_chain():
    harness = _CMHarness(initial="/tmp/x")
    async with harness.run_test() as pilot:
        await pilot.pause()
        harness.dialog.focus_input()
        await pilot.pause()
        # input -> ok -> cancel -> input
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "cm-ok"
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "cm-cancel"
        await pilot.press("tab")
        await pilot.pause()
        from textual.widgets import Input
        assert isinstance(harness.focused, Input)


@pytest.mark.asyncio
async def test_copymove_dialog_left_in_input_keeps_focus():
    harness = _CMHarness(initial="abc")
    async with harness.run_test() as pilot:
        await pilot.pause()
        harness.dialog.focus_input()
        await pilot.pause()
        from textual.widgets import Input
        assert isinstance(harness.focused, Input)
        await pilot.press("left")
        await pilot.pause()
        # Focus must still be on the Input — Left moves the cursor inside it.
        assert isinstance(harness.focused, Input)


class _NFHarness(App):
    def __init__(self, *, initial: str) -> None:
        super().__init__()
        self._initial = initial
        self.dialog: NewFileDialog | None = None

    def compose(self) -> ComposeResult:
        self.dialog = NewFileDialog(
            prompt="New file name:", initial=self._initial
        )
        yield self.dialog


@pytest.mark.asyncio
async def test_newfile_dialog_tab_chain():
    harness = _NFHarness(initial="x.txt")
    async with harness.run_test() as pilot:
        await pilot.pause()
        harness.dialog.focus_input()
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "nf-create"
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "nf-cancel"


@pytest.mark.asyncio
async def test_change_attributes_tab_chain_includes_buttons():
    """Regression: Tab from the last perm checkbox lands on Set, then
    Cancel, then wraps back to the first checkbox."""
    dlg = ChangeAttributesDialog(target_label="x", current_mode=0o644)

    class _CAHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _CAHarness().run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # 12 tabs after the initial first-checkbox focus → land on ca-set.
        for _ in range(12):
            await pilot.press("tab")
            await pilot.pause()
        focused = pilot.app.focused
        assert isinstance(focused, DialogButton) and focused.id == "ca-set"
        await pilot.press("tab")
        await pilot.pause()
        focused = pilot.app.focused
        assert isinstance(focused, DialogButton) and focused.id == "ca-cancel"
