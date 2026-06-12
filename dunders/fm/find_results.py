"""SearchResultsContent — non-modal window showing live find-file results.

Hosted in a regular Window (not a ModalWindow): the user can browse other
panels while the search runs. Posts request messages back to the app
(GoTo, View, Edit, Stop, NewSearch) — the app handles the side effects
(switching cwd, opening editor, confirm-and-cancel, etc.).

The progress status line is throttled at the content level: very fast
walks were causing 100k+ ``call_from_thread`` deliveries per second; we
only refresh the status text 10x/sec regardless of how often
``update_status`` is called.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import ListItem, ListView, Static

from dunders.fm.dialogs import ShadowButton
from dunders.fm.find_file import FindOptions, FindResult
from dunders.windowing.content import WindowCommand, WindowContent


__all__ = ["SearchResultsContent"]


_STATUS_REFRESH_INTERVAL = 0.1   # seconds — throttle status repaints to 10 Hz


class SearchResultsContent(WindowContent):
    """Live results listing + status line + action buttons.

    The owning app drives the lifecycle:
        1. Construct with the FindOptions and start_dir.
        2. Mount inside a Window (via make_window).
        3. Spawn a worker thread that calls
           :meth:`add_match` / :meth:`update_status` / :meth:`finish`
           via ``call_from_thread``.
    """

    can_focus = True

    BINDINGS = [
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("enter", "go_to", show=False),
        Binding("escape", "request_close", show=False),
    ]

    DEFAULT_CSS = """
    SearchResultsContent {
        layout: vertical;
        background: $surface;
    }
    SearchResultsContent #fr-list {
        height: 1fr;
        background: $surface;
    }
    SearchResultsContent #fr-status {
        height: 2;
        padding: 0 1;
        background: $boost;
        color: $text;
    }
    SearchResultsContent #fr-buttons {
        height: 2;
        align: center middle;
    }
    """

    # ---- messages emitted to the app shell --------------------------------

    class GoToRequested(Message):
        def __init__(self, content: "SearchResultsContent", path: Path) -> None:
            self.content = content
            self.path = path
            super().__init__()

    class ViewRequested(Message):
        def __init__(self, content: "SearchResultsContent", path: Path) -> None:
            self.content = content
            self.path = path
            super().__init__()

    class EditRequested(Message):
        def __init__(self, content: "SearchResultsContent", path: Path) -> None:
            self.content = content
            self.path = path
            super().__init__()

    class StopRequested(Message):
        def __init__(self, content: "SearchResultsContent") -> None:
            self.content = content
            super().__init__()

    class CloseRequested(Message):
        """User asked to close the results window (Panel button or Esc)."""

        def __init__(self, content: "SearchResultsContent") -> None:
            self.content = content
            super().__init__()

    class NewSearchRequested(Message):
        def __init__(self, content: "SearchResultsContent") -> None:
            self.content = content
            super().__init__()

    # ---- lifecycle --------------------------------------------------------

    def __init__(
        self,
        *,
        options: FindOptions,
        start_dir: Path,
    ) -> None:
        super().__init__()
        self.options = options
        self.start_dir = start_dir
        # Worker writes here from another thread; reads always happen on
        # the UI thread inside call_from_thread, so no lock needed.
        self.cancel_event = threading.Event()
        self.matches: list[Path] = []
        self.search_running = True
        self.result: FindResult | None = None
        self.window_title = self._title_for(options)
        self._list = ListView(id="fr-list")
        self._status = Static("", id="fr-status")
        self._last_status_refresh = 0.0
        self._pending_status: tuple[Path | None, int, int] | None = None
        self._buttons: dict[str, ShadowButton] = {}

    @staticmethod
    def _title_for(options: FindOptions) -> str:
        if not options.masks:
            return "Find file: *"
        return f"Find file: {' '.join(options.masks)}"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield self._list
            yield self._status
            with Horizontal(id="fr-buttons"):
                yield self._make_button("New search", "fr-new",   "rgb(0,160,176)", "n")
                yield self._make_button("Go to",      "fr-goto",  "rgb(0,160,90)",  "g")
                yield self._make_button("View",       "fr-view",  "rgb(0,160,176)", "v")
                yield self._make_button("Edit",       "fr-edit",  "rgb(0,160,176)", "e")
                yield self._make_button("Panel",      "fr-panel", "rgb(0,160,176)", "p")
                yield self._make_button("Stop",       "fr-stop",  "rgb(160,40,40)", "s")

    def _make_button(self, label: str, btn_id: str, bg: str, hotkey: str) -> ShadowButton:
        btn = ShadowButton(label, id=btn_id, face_bg=bg, hotkey=hotkey)
        self._buttons[btn_id] = btn
        return btn

    def on_mount(self) -> None:
        self._refresh_status_line(force=True)

    # ---- worker-driven API (called via call_from_thread) ------------------

    def add_match(self, path: Path) -> None:
        self.matches.append(path)
        try:
            self._list.append(ListItem(Static(str(path))))
        except Exception:
            # ListView may not be mounted yet on the very first match; the
            # status_line redraw on next refresh will catch up via matches list.
            pass

    def update_status(self, current_dir: Path | None, files: int, folders: int) -> None:
        self._pending_status = (current_dir, files, folders)
        # Throttle: only flush to the Static widget at most every 100 ms.
        now = time.monotonic()
        if now - self._last_status_refresh < _STATUS_REFRESH_INTERVAL:
            return
        self._refresh_status_line()

    def finish(self, result: FindResult) -> None:
        self.search_running = False
        self.result = result
        # Flush whatever was pending so the user sees the final counters.
        self._refresh_status_line(force=True)
        # Stop button stays visible — it just no-ops once search_running is
        # False (see action_stop). We deliberately don't toggle its
        # ``disabled`` attribute because ShadowButton's render_line does
        # not paint a disabled state and would crash on a None style.

    # ---- status line ------------------------------------------------------

    def _refresh_status_line(self, *, force: bool = False) -> None:
        if self._pending_status is None and not force:
            return
        cur_dir, files, folders = self._pending_status or (None, 0, 0)
        if not self.search_running and self.result is not None:
            tag = "Cancelled" if self.result.cancelled else "Done"
            line1 = f"Files: {self.result.files_scanned}, folders: {self.result.folders_scanned}"
            line2 = f"{tag}. Found {len(self.matches)} item(s)."
        else:
            line1 = f"Files: {files}, folders: {folders}"
            line2 = f"Searching in {cur_dir}" if cur_dir is not None else "Searching…"
        self._status.update(f"{line1}\n{line2}")
        self._last_status_refresh = time.monotonic()

    # ---- selection helpers ------------------------------------------------

    def _selected_path(self) -> Path | None:
        idx = self._list.index
        if idx is None or idx < 0 or idx >= len(self.matches):
            return None
        return self.matches[idx]

    # ---- actions ----------------------------------------------------------

    def action_cursor_up(self) -> None:
        self._list.action_cursor_up()

    def action_cursor_down(self) -> None:
        self._list.action_cursor_down()

    def action_page_up(self) -> None:
        self._list.action_cursor_up()

    def action_page_down(self) -> None:
        self._list.action_cursor_down()

    def action_go_to(self) -> None:
        path = self._selected_path()
        if path is not None:
            self.post_message(SearchResultsContent.GoToRequested(self, path))

    def action_view(self) -> None:
        path = self._selected_path()
        if path is not None:
            self.post_message(SearchResultsContent.ViewRequested(self, path))

    def action_edit(self) -> None:
        path = self._selected_path()
        if path is not None:
            self.post_message(SearchResultsContent.EditRequested(self, path))

    def action_stop(self) -> None:
        if self.search_running:
            self.post_message(SearchResultsContent.StopRequested(self))

    def action_request_close(self) -> None:
        self.post_message(SearchResultsContent.CloseRequested(self))

    def action_new_search(self) -> None:
        self.post_message(SearchResultsContent.NewSearchRequested(self))

    # ---- input plumbing ---------------------------------------------------

    def on_shadow_button_pressed(self, event: "ShadowButton.Pressed") -> None:
        event.stop()
        bid = event.button.id
        if bid == "fr-new":
            self.action_new_search()
        elif bid == "fr-goto":
            self.action_go_to()
        elif bid == "fr-view":
            self.action_view()
        elif bid == "fr-edit":
            self.action_edit()
        elif bid == "fr-panel":
            self.action_request_close()
        elif bid == "fr-stop":
            self.action_stop()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # Default Enter on a ListItem inside ListView posts Selected — treat
        # it as the user picking the current row for "Go to".
        event.stop()
        self.action_go_to()

    # ---- focus-scoped commands for menus / palette ------------------------

    def get_commands(self) -> list[WindowCommand]:
        def _bind(method_name: str):
            def _handler() -> None:
                getattr(self, method_name)()
            return _handler

        return [
            WindowCommand(
                id="find.goto",
                label="Go to",
                handler=_bind("action_go_to"),
                hotkey="enter",
            ),
            WindowCommand(
                id="find.view",
                label="View",
                handler=_bind("action_view"),
                hotkey="f3",
            ),
            WindowCommand(
                id="find.edit",
                label="Edit",
                handler=_bind("action_edit"),
                hotkey="f4",
            ),
            WindowCommand(
                id="find.stop",
                label="Stop",
                handler=_bind("action_stop"),
                enabled=lambda: self.search_running,
            ),
        ]
