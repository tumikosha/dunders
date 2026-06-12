"""Tests for SearchResultsContent (live results window behaviour)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from dunders.fm.find_file import FindOptions, FindResult
from dunders.fm.find_results import SearchResultsContent


def _opts() -> FindOptions:
    return FindOptions(
        masks=("*.py",),
        case_sensitive_mask=False,
        contains="",
        case_sensitive_text=False,
        whole_words=False,
        search_for_folders=False,
        follow_symlinks=False,
    )


class _Harness(App):
    def __init__(self, content: SearchResultsContent) -> None:
        super().__init__()
        self.content = content
        self.go_to: list[Path] = []
        self.view: list[Path] = []
        self.edit: list[Path] = []
        self.stop: int = 0
        self.close: int = 0
        self.new_search: int = 0

    def compose(self) -> ComposeResult:
        yield self.content

    def on_search_results_content_go_to_requested(
        self, event: SearchResultsContent.GoToRequested
    ) -> None:
        self.go_to.append(event.path)

    def on_search_results_content_view_requested(
        self, event: SearchResultsContent.ViewRequested
    ) -> None:
        self.view.append(event.path)

    def on_search_results_content_edit_requested(
        self, event: SearchResultsContent.EditRequested
    ) -> None:
        self.edit.append(event.path)

    def on_search_results_content_stop_requested(
        self, _event: SearchResultsContent.StopRequested
    ) -> None:
        self.stop += 1

    def on_search_results_content_close_requested(
        self, _event: SearchResultsContent.CloseRequested
    ) -> None:
        self.close += 1

    def on_search_results_content_new_search_requested(
        self, _event: SearchResultsContent.NewSearchRequested
    ) -> None:
        self.new_search += 1


@pytest.mark.asyncio
async def test_results_add_match_appends_to_listing(tmp_path: Path):
    content = SearchResultsContent(options=_opts(), start_dir=tmp_path)
    harness = _Harness(content)
    async with harness.run_test() as pilot:
        await pilot.pause()
        content.add_match(tmp_path / "a.py")
        content.add_match(tmp_path / "b.py")
        await pilot.pause()
        assert content.matches == [tmp_path / "a.py", tmp_path / "b.py"]


@pytest.mark.asyncio
async def test_results_go_to_emits_path_for_selected_match(tmp_path: Path):
    content = SearchResultsContent(options=_opts(), start_dir=tmp_path)
    harness = _Harness(content)
    async with harness.run_test() as pilot:
        await pilot.pause()
        content.add_match(tmp_path / "first.py")
        content.add_match(tmp_path / "second.py")
        await pilot.pause()
        # ListView.index should default to 0 once items are mounted.
        content._list.index = 1
        await pilot.pause()
        content.action_go_to()
        await pilot.pause()
        assert harness.go_to == [tmp_path / "second.py"]


@pytest.mark.asyncio
async def test_results_stop_emits_only_while_running(tmp_path: Path):
    content = SearchResultsContent(options=_opts(), start_dir=tmp_path)
    harness = _Harness(content)
    async with harness.run_test() as pilot:
        await pilot.pause()
        # While running -> Stop posts the message.
        content.action_stop()
        await pilot.pause()
        assert harness.stop == 1

        # After finish() -> Stop is a no-op.
        content.finish(FindResult(matches=[], cancelled=False, files_scanned=10, folders_scanned=2))
        await pilot.pause()
        content.action_stop()
        await pilot.pause()
        assert harness.stop == 1


@pytest.mark.asyncio
async def test_results_finish_marks_not_running(tmp_path: Path):
    content = SearchResultsContent(options=_opts(), start_dir=tmp_path)
    harness = _Harness(content)
    async with harness.run_test() as pilot:
        await pilot.pause()
        content.finish(FindResult(matches=[], cancelled=True, files_scanned=5, folders_scanned=1))
        await pilot.pause()
        assert content.search_running is False
        assert content.result is not None
        assert content.result.cancelled is True


@pytest.mark.asyncio
async def test_results_close_via_panel_button(tmp_path: Path):
    content = SearchResultsContent(options=_opts(), start_dir=tmp_path)
    harness = _Harness(content)
    async with harness.run_test() as pilot:
        await pilot.pause()
        content.action_request_close()
        await pilot.pause()
        assert harness.close == 1


@pytest.mark.asyncio
async def test_results_new_search_button(tmp_path: Path):
    content = SearchResultsContent(options=_opts(), start_dir=tmp_path)
    harness = _Harness(content)
    async with harness.run_test() as pilot:
        await pilot.pause()
        content.action_new_search()
        await pilot.pause()
        assert harness.new_search == 1


@pytest.mark.asyncio
async def test_results_status_shows_done_after_finish(tmp_path: Path):
    content = SearchResultsContent(options=_opts(), start_dir=tmp_path)
    harness = _Harness(content)
    async with harness.run_test() as pilot:
        await pilot.pause()
        content.add_match(tmp_path / "x.py")
        content.finish(FindResult(
            matches=[tmp_path / "x.py"], cancelled=False,
            files_scanned=42, folders_scanned=3,
        ))
        await pilot.pause()
        rendered = str(content._status.render())
        assert "Done" in rendered
        assert "42" in rendered
        assert "Found 1" in rendered


@pytest.mark.asyncio
async def test_results_status_shows_cancelled_after_cancel(tmp_path: Path):
    content = SearchResultsContent(options=_opts(), start_dir=tmp_path)
    harness = _Harness(content)
    async with harness.run_test() as pilot:
        await pilot.pause()
        content.finish(FindResult(matches=[], cancelled=True, files_scanned=1, folders_scanned=1))
        await pilot.pause()
        rendered = str(content._status.render())
        assert "Cancelled" in rendered
