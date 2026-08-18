import pytest
from textual import events
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
        bar = "".join(seg.text for seg in dlg.render_line(dlg._BAR_Y))
        assert "0/10" in bar


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
        bar = "".join(seg.text for seg in dlg.render_line(dlg._BAR_Y))
        assert "3/10" in bar


@pytest.mark.asyncio
async def test_progress_dialog_copy_status_shows_filename_and_bytes():
    from dunders.fm.actions import CopyStatus
    dlg = ProgressDialog(title="Copying", total=1)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        dlg.set_copy_status(
            CopyStatus(done=512 * 1024, total=1024 * 1024,
                       label="/some/long/path/movie.mkv", is_bytes=True)
        )
        await pilot.pause()
        label = "".join(seg.text for seg in dlg.render_line(dlg._LABEL_Y))
        assert "movie.mkv" in label
        bar = "".join(seg.text for seg in dlg.render_line(dlg._BAR_Y))
        # Human-readable byte counts + a percentage, not raw byte integers.
        assert "512" in bar and "%" in bar
        assert "524288" not in bar


@pytest.mark.asyncio
async def test_progress_dialog_two_bars_for_copy():
    """A copy status renders BOTH an overall bar and a current-file bar, plus a
    'file X/Y' counter on the label line."""
    from dunders.fm.actions import CopyStatus
    dlg = ProgressDialog(title="Copying", total=10)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        dlg.set_copy_status(CopyStatus(
            done=5 * 1024 * 1024, total=20 * 1024 * 1024,
            label="big/current.bin", is_bytes=True,
            file_done=256 * 1024, file_total=1024 * 1024,
            files_done=3, files_total=10,
        ))
        await pilot.pause()
        assert dlg.two_bars is True
        label = "".join(seg.text for seg in dlg.render_line(dlg._LABEL_Y))
        assert "current.bin" in label
        assert "3/10" in label                      # file counter
        overall = "".join(seg.text for seg in dlg.render_line(dlg._BAR_Y))
        assert "5.0M/20.0M" in overall and "25%" in overall
        file_bar = "".join(seg.text for seg in dlg.render_line(dlg._FILE_BAR_Y))
        assert "256.0K/1.0M" in file_bar and "25%" in file_bar


@pytest.mark.asyncio
async def test_progress_dialog_indeterminate_bar_sweeps_smoothly():
    """The unknown-total (indeterminate) bar must sweep one cell per update, not
    teleport — seeding it with the raw byte count made it 'jump like crazy'."""
    from dunders.fm.actions import CopyStatus
    dlg = ProgressDialog(title="Copying", total=0)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        positions = []
        for k in range(15):
            # done jumps by a big, irregular amount each update (like real bytes)
            dlg.set_copy_status(CopyStatus(done=k * 1_000_003, total=0,
                                           label="Measuring…", is_bytes=False))
            await pilot.pause()
            bar = "".join(seg.text for seg in dlg.render_line(dlg._BAR_Y))
            positions.append(bar.index("█") if "█" in bar else -1)
        steps = [abs(positions[i] - positions[i - 1]) for i in range(1, len(positions))]
        assert max(steps) <= 1  # smooth: never teleports despite huge done jumps


@pytest.mark.asyncio
async def test_progress_dialog_space_cancels():
    dlg = ProgressDialog(title="Copying", total=5)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        dlg.focus()
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert dlg.cancel_event.is_set()


@pytest.mark.asyncio
async def test_progress_dialog_indeterminate_counts_up_without_percent():
    """A total==0 copy status (lazily-measured slow-dir transfer) renders a
    count-up + sliding bar, not a misleading 0/0 100% bar."""
    from dunders.fm.actions import CopyStatus
    dlg = ProgressDialog(title="Copying", total=1)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        dlg.set_copy_status(
            CopyStatus(done=3 * 1024 * 1024, total=0,
                       label="node_modules/x.js", is_bytes=True)
        )
        await pilot.pause()
        assert dlg.indeterminate is True
        bar = "".join(seg.text for seg in dlg.render_line(dlg._BAR_Y))
        assert "%" not in bar          # no percentage for an unknown total
        assert "/ 0" not in bar        # never "3.0M / 0"
        assert "3.0M" in bar           # streamed-bytes count-up is shown
        assert dlg._BAR_FILLED in bar  # the sliding block is drawn


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
    """Click on the centred [ Cancel ] button triggers cancel."""
    from types import SimpleNamespace
    dlg = ProgressDialog(title="Working", total=5)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        stops: list[bool] = []
        dlg.on_click(SimpleNamespace(
            x=dlg._cancel_x(dlg.size.width) + 2,
            y=dlg._cancel_y,
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
            x=dlg._cancel_x(dlg.size.width) + len(dlg._CANCEL_LABEL) + 5,
            y=dlg._cancel_y,
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
        # input -> skip-checkbox -> ok -> cancel -> input
        await pilot.press("tab")
        await pilot.pause()
        assert harness.focused.id == "cm-skip"
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
async def test_copymove_dialog_skip_existing_checkbox():
    """The 'Skip existing' checkbox is off by default and toggles via space."""
    harness = _CMHarness(initial="/tmp/x")
    async with harness.run_test() as pilot:
        await pilot.pause()
        assert harness.dialog.skip_existing is False
        harness.dialog.focus_input()
        await pilot.pause()
        await pilot.press("tab")            # -> the skip checkbox
        await pilot.pause()
        assert harness.focused.id == "cm-skip"
        await pilot.press("space")          # toggle on
        await pilot.pause()
        assert harness.dialog.skip_existing is True


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


# --- FolderStatsDialog ------------------------------------------------------

from dunders.fm.dialogs import FolderStatsDialog
from dunders.fm.folder_stats import FolderStats


def _folder_stats_line(dlg, y):
    return "".join(seg.text for seg in dlg.render_line(y))


@pytest.mark.asyncio
async def test_folder_stats_scanning_then_done():
    dlg = FolderStatsDialog(name="myfolder")

    class _H(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _H().run_test() as pilot:
        await pilot.pause()
        # Scanning state: placeholder values + Cancel button + "Scanning…".
        assert not dlg._done
        btn = _folder_stats_line(dlg, dlg._BTN_Y)
        assert "[ Cancel ]" in btn
        assert "Scanning" in _folder_stats_line(dlg, dlg._STATUS_Y)

        dlg.set_stats(
            FolderStats(files=12, dirs=3, total_bytes=2048,
                        largest_name="big.bin", largest_size=2000, max_depth=4),
            done=True,
        )
        await pilot.pause()
        assert dlg._done
        assert "12" in _folder_stats_line(dlg, dlg._FILES_Y)
        assert "3" in _folder_stats_line(dlg, dlg._FOLDERS_Y)
        assert "2048 bytes" in _folder_stats_line(dlg, dlg._SIZE_Y)
        assert "big.bin" in _folder_stats_line(dlg, dlg._LARGEST_Y)
        assert "4" in _folder_stats_line(dlg, dlg._DEPTH_Y)
        assert "Done" in _folder_stats_line(dlg, dlg._STATUS_Y)
        # Button flips to Close once done.
        assert "[ Close ]" in _folder_stats_line(dlg, dlg._BTN_Y)


@pytest.mark.asyncio
async def test_folder_stats_cancel_sets_event_and_marks_partial():
    dlg = FolderStatsDialog(name="f")

    class _H(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _H().run_test() as pilot:
        dlg.focus()
        await pilot.pause()
        await pilot.press("c")           # Cancel while scanning
        await pilot.pause()
        assert dlg.cancel_event.is_set()
        assert not dlg._done             # not dismissed yet — worker will finish
        # Worker returns a partial result.
        dlg.set_stats(FolderStats(files=5, partial=True), done=True)
        await pilot.pause()
        assert "Cancelled" in _folder_stats_line(dlg, dlg._STATUS_Y)
        assert "[ Close ]" in _folder_stats_line(dlg, dlg._BTN_Y)


@pytest.mark.asyncio
async def test_folder_stats_space_activates_button():
    """Space is bound to the button (its focus target): while scanning it
    cancels; the dialog is focused so the keypress lands."""
    dlg = FolderStatsDialog(name="f")

    class _H(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _H().run_test() as pilot:
        dlg.focus()
        await pilot.pause()
        assert pilot.app.focused is dlg
        await pilot.press("space")
        await pilot.pause()
        assert dlg.cancel_event.is_set()


@pytest.mark.asyncio
async def test_folder_stats_values_highlighted():
    """Result values render in a distinct highlight style; the key labels stay
    dim — so the numbers stand out."""
    dlg = FolderStatsDialog(name="proj")

    class _H(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _H().run_test() as pilot:
        await pilot.pause()
        dlg.set_stats(FolderStats(files=42, dirs=7, total_bytes=123456,
                                  largest_name="big.bin", largest_size=99999,
                                  max_depth=3), done=True)
        await pilot.pause()
        segs = [s for s in dlg.render_line(dlg._FILES_Y)._segments if s.text.strip()]
        key_seg, val_seg = segs[0], segs[-1]
        assert "Files" in key_seg.text
        assert "42" in val_seg.text
        # Value style differs from the dim key style (bold and/or coloured).
        assert val_seg.style != key_seg.style
        assert val_seg.style.bold


# --- GDriveConsentDialog ----------------------------------------------------

import threading as _threading

from dunders.fm.dialogs import GDriveConsentDialog


@pytest.mark.asyncio
async def test_gdrive_consent_dialog_copy_and_cancel(monkeypatch):
    ev = _threading.Event()
    url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=X"
    dlg = GDriveConsentDialog(url, cancel_event=ev)

    copied = []
    from dunders.windowing.core import clipboard
    monkeypatch.setattr(clipboard, "copy", lambda text, app=None: copied.append(text))

    cancelled = []

    class _H(App):
        def compose(self) -> ComposeResult:
            yield dlg

        def on_gdrive_consent_dialog_cancelled(self, event) -> None:
            cancelled.append(event)

    async with _H().run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Static
        assert dlg.query_one("#gd-url", Static) is not None and dlg._url == url
        dlg.action_copy_url()                        # the Copy URL button
        assert copied == [url]                       # full URL, no truncation
        dlg.action_cancel()
        await pilot.pause()
        assert ev.is_set()               # aborts the loopback wait
        assert len(cancelled) == 1       # app is told to close the modal


@pytest.mark.asyncio
async def test_progress_dialog_cancel_button_highlights_on_hover():
    """The Cancel pill must visibly change under the mouse — with no hover
    state it rendered one fixed style and read as a dead control."""
    dlg = ProgressDialog(title="Converting big.pdf", total=0)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        y = dlg._cancel_y
        x = dlg._cancel_x(dlg.size.width)
        resting = dlg._cancel_style()

        # Real pointer motion, not a synthetic post_message: this also proves
        # Textual actually routes MouseMove to the dialog.
        await pilot.hover(ProgressDialog, offset=(x + 1, y))
        assert dlg._hover_cancel is True
        assert dlg._cancel_style() != resting

        # Off the button (same row, far left) → back to the resting style.
        await pilot.hover(ProgressDialog, offset=(0, y))
        assert dlg._hover_cancel is False
        assert dlg._cancel_style() == resting


@pytest.mark.asyncio
async def test_progress_dialog_leave_clears_the_hover():
    dlg = ProgressDialog(title="Converting big.pdf", total=0)

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        dlg._hover_cancel = True
        dlg.post_message(events.Leave(dlg))
        await pilot.pause()
        assert dlg._hover_cancel is False


@pytest.mark.asyncio
async def test_folder_stats_button_highlights_on_hover():
    """Same button, same contract as ProgressDialog's Cancel."""
    dlg = FolderStatsDialog("some-dir")

    class _PHarness(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _PHarness().run_test() as pilot:
        await pilot.pause()
        resting = dlg._button_style()
        await pilot.hover(
            FolderStatsDialog, offset=(dlg._btn_x(dlg.size.width) + 1, dlg._BTN_Y)
        )
        assert dlg._hover_btn is True
        assert dlg._button_style() != resting
        dlg.post_message(events.Leave(dlg))
        await pilot.pause()
        assert dlg._hover_btn is False
