"""Tests for FindFileDialog (UI behaviour: submit, cancel, options build)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from dunders.fm.dialogs import FindFileDialog
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
        # mask, text, 5 checkboxes, find, cancel = 9
        assert len(chain) == 9
        # First focusable is the mask input.
        assert chain[0] is dlg._mask_input
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
