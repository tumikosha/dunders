"""Tests for FindFileDialog (UI behaviour: submit, cancel, options build)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from dunders.fm.dialogs import DEFAULT_EXCLUDE_MASKS, FindFileDialog
from dunders.fm.find_file import FindOptions


class _Harness(App):
    def __init__(self, dialog: FindFileDialog) -> None:
        super().__init__()
        self.dialog = dialog
        self.submitted: list[FindOptions] = []
        self.cancelled: int = 0

    def compose(self) -> ComposeResult:
        yield self.dialog

    def on_find_file_dialog_submitted(self, event: FindFileDialog.Submitted) -> None:
        self.submitted.append(event.options)

    def on_find_file_dialog_cancelled(self, _event: FindFileDialog.Cancelled) -> None:
        self.cancelled += 1


@pytest.mark.asyncio
async def test_dialog_submit_with_default_mask_emits_options(tmp_path: Path):
    dlg = FindFileDialog(start_dir=tmp_path)
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        dlg.action_submit()
        await pilot.pause()
        assert len(harness.submitted) == 1
        opts = harness.submitted[0]
        assert opts.masks == ("*",)
        assert opts.contains == ""
        assert opts.case_sensitive_mask is False
        # The exclude field is prefilled, so the default search already
        # skips the virtualenv.
        assert opts.exclude_masks == (".venv", ".omc")
        assert opts.search_for_folders is True  # default in dialog
        assert opts.follow_symlinks is True


@pytest.mark.asyncio
async def test_dialog_empty_mask_does_not_submit(tmp_path: Path):
    """Submitting with no mask is a no-op — user must type at least '*'."""
    dlg = FindFileDialog(start_dir=tmp_path, initial_mask="")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        dlg.action_submit()
        await pilot.pause()
        assert harness.submitted == []
        assert harness.cancelled == 0


@pytest.mark.asyncio
async def test_dialog_cancel_emits_cancelled(tmp_path: Path):
    dlg = FindFileDialog(start_dir=tmp_path)
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        dlg.action_cancel()
        await pilot.pause()
        assert harness.cancelled == 1


@pytest.mark.asyncio
async def test_dialog_checkbox_toggle_reflected_in_options(tmp_path: Path):
    dlg = FindFileDialog(start_dir=tmp_path, initial_mask="*.py")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        # Toggle whole_words on
        dlg._checkboxes["ff-whole"].action_toggle()
        # Toggle search_for_folders off (was True by default)
        dlg._checkboxes["ff-folders"].action_toggle()
        dlg.action_submit()
        await pilot.pause()
        assert len(harness.submitted) == 1
        opts = harness.submitted[0]
        assert opts.masks == ("*.py",)
        assert opts.whole_words is True
        assert opts.search_for_folders is False


@pytest.mark.asyncio
async def test_dialog_contains_text_propagates(tmp_path: Path):
    dlg = FindFileDialog(start_dir=tmp_path, initial_contains="hello")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        dlg.action_submit()
        await pilot.pause()
        assert harness.submitted[0].contains == "hello"


@pytest.mark.asyncio
async def test_dialog_focus_chain_starts_on_mask_input(tmp_path: Path):
    dlg = FindFileDialog(start_dir=tmp_path)
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        chain = dlg._focusables()
        # mask, exclude, text, 5 checkboxes, find, cancel = 10
        assert len(chain) == 10
        # First focusable is the mask input, then the exclude mask.
        assert chain[0] is dlg._mask_input
        assert chain[1] is dlg._exclude_input
        # Last two are the buttons.
        assert chain[-2].id == "ff-find"
        assert chain[-1].id == "ff-cancel"


@pytest.mark.asyncio
async def test_dialog_escape_cancels(tmp_path: Path):
    dlg = FindFileDialog(start_dir=tmp_path)
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        # Park focus on a button so Esc bubbles to the dialog binding.
        dlg.query_one("#ff-find").focus()
        await pilot.press("escape")
        await pilot.pause()
        assert harness.cancelled == 1


@pytest.mark.asyncio
async def test_dialog_exclude_masks_propagate(tmp_path: Path):
    dlg = FindFileDialog(
        start_dir=tmp_path,
        initial_mask="*.py",
        initial_exclude=".venv, node_modules",
    )
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        dlg.action_submit()
        await pilot.pause()
        opts = harness.submitted[0]
        assert opts.exclude_masks == (".venv", "node_modules")


@pytest.mark.asyncio
async def test_dialog_exclude_masks_typed_at_runtime(tmp_path: Path):
    dlg = FindFileDialog(start_dir=tmp_path, initial_mask="*.py")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        dlg._exclude_input.value = "*.pyc .git"
        assert dlg.get_exclude_text() == "*.pyc .git"
        dlg.action_submit()
        await pilot.pause()
        assert harness.submitted[0].exclude_masks == ("*.pyc", ".git")


# --- session memory of the last search (app level) -------------------------

async def _open_find_dialog(app, pilot) -> FindFileDialog:
    app.action_find_file()
    await pilot.pause()
    await pilot.pause()
    return app.query_one(FindFileDialog)


@pytest.mark.asyncio
async def test_app_reopens_find_dialog_with_previous_values(tmp_path: Path):
    """Second Alt+F7 starts from the last submitted search, not the defaults."""
    from dunders.app import DundersApp

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        dlg = await _open_find_dialog(app, pilot)
        dlg._mask_input.value = "*.py"
        dlg._exclude_input.value = ".venv;node_modules"
        dlg._text_input.value = "pipe"
        dlg._checkboxes["ff-whole"].action_toggle()      # off -> on
        dlg._checkboxes["ff-folders"].action_toggle()    # on  -> off
        dlg.action_submit()
        await pilot.pause()

        assert app._last_find_options is not None
        assert app._last_find_options.exclude_masks == (".venv", "node_modules")

        again = await _open_find_dialog(app, pilot)
        assert again.get_mask_text() == "*.py"
        assert again.get_exclude_text() == ".venv;node_modules"
        assert again.get_contains_text() == "pipe"
        assert again.is_checked("ff-whole") is True
        assert again.is_checked("ff-folders") is False
        again.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_app_find_dialog_defaults_on_first_open(tmp_path: Path):
    from dunders.app import DundersApp

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._find_dialog_prefs() == {}
        dlg = await _open_find_dialog(app, pilot)
        assert dlg.get_mask_text() == "*"
        assert dlg.get_exclude_text() == DEFAULT_EXCLUDE_MASKS
        assert dlg.get_contains_text() == ""
        dlg.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_dialog_default_exclude_is_prefilled_and_clearable(tmp_path: Path):
    dlg = FindFileDialog(start_dir=tmp_path, initial_mask="*.py")
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        assert dlg.get_exclude_text() == DEFAULT_EXCLUDE_MASKS
        dlg._exclude_input.value = ""
        dlg.action_submit()
        await pilot.pause()
        assert harness.submitted[0].exclude_masks == ()


@pytest.mark.asyncio
async def test_dialog_semicolon_separated_exclude_masks(tmp_path: Path):
    dlg = FindFileDialog(
        start_dir=tmp_path,
        initial_mask="*.py",
        initial_exclude=".venv;.omc;node_modules;*.pyc",
    )
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        dlg.action_submit()
        await pilot.pause()
        assert harness.submitted[0].exclude_masks == (
            ".venv",
            ".omc",
            "node_modules",
            "*.pyc",
        )


@pytest.mark.asyncio
async def test_dialog_exclude_keeps_dash_exceptions(tmp_path: Path):
    """The `-` prefix reaches FindOptions intact (exclude.split_masks reads it)."""
    dlg = FindFileDialog(
        start_dir=tmp_path,
        initial_mask="*.py",
        initial_exclude=".*;-.gitignore",
    )
    harness = _Harness(dlg)
    async with harness.run_test() as pilot:
        await pilot.pause()
        dlg.action_submit()
        await pilot.pause()
        assert harness.submitted[0].exclude_masks == (".*", "-.gitignore")
