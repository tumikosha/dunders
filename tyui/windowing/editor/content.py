"""EditorContent: WindowContent wrapper for EditorWidget."""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container

from tyui.windowing.content import WindowContent, WindowCommand
from tyui.windowing.core.buffer import TextBuffer
from tyui.windowing.core.fold_engine import FoldEngine, FoldRegistry
from tyui.windowing.core.indent_fold import IndentFoldRule
from tyui.windowing.core.macro import MacroRecorder, MacroStorage
from tyui.windowing.core.search import SearchOptions

from .search_panel import SearchPanel
from .splitter import Splitter
from .widget import EditorWidget

log = logging.getLogger(__name__)


class EditorContent(WindowContent):
    """WindowContent that wraps an EditorWidget with macro support."""

    BINDINGS = [
        Binding("ctrl+f", "open_find", "Find", show=False),
        Binding("f4", "open_replace", "Replace", show=False),
        Binding("f3", "find_next", "Find Next", show=False),
        Binding("shift+f3", "find_prev", "Find Prev", show=False),
        Binding("f6", "replace_all", "Replace All", show=False),
        Binding("f7", "fold_toggle", "Fold Toggle", show=False),
        Binding("f8", "macro_toggle", "Macro", show=False),
        Binding("ctrl+r", "macro_toggle", "Macro", show=False),
        Binding("escape", "close_search", "Close", show=False),
    ]

    DEFAULT_CSS = """
    EditorContent { background: transparent; }
    EditorContent > Container { height: 1fr; width: 1fr; }
    EditorContent EditorWidget { background: transparent; width: 1fr; height: 1fr; }
    """

    def __init__(
        self,
        initial_text: str = "",
        file_path: str | None = None,
        macro_storage_path: str | None = None,
        title: str | None = None,
        enable_folding: bool = True,
        enable_macros: bool = True,
    ) -> None:
        super().__init__()
        self._initial_text = initial_text
        self._file_path = file_path
        self._macro_storage_path = macro_storage_path
        self._enable_folding = enable_folding
        self._enable_macros = enable_macros

        if title is not None:
            self.window_title = title
        elif file_path is not None:
            self.window_title = Path(file_path).name

        buffer = TextBuffer.from_string(initial_text)
        if file_path:
            buffer.file_path = file_path

        fold_engine = None
        self._fold_registry = None
        if enable_folding:
            registry = FoldRegistry()
            registry.add_rule(IndentFoldRule())
            self._fold_registry = registry
            fold_engine = FoldEngine(registry)

        self._buffer = buffer
        self._editor = EditorWidget(buffer=buffer, fold_engine=fold_engine)
        self._editor.macro_skip_keys.update(self._SEARCH_SKIP_KEYS)
        self._editor2: EditorWidget | None = None
        self._splitter: Splitter | None = None
        self._split_container: Container | None = None

        self._search_panel: SearchPanel | None = None

        self._macro_recorder: MacroRecorder | None = None
        self._macro_storage: MacroStorage | None = None
        self._macro_search_recorded = False
        if enable_macros:
            # Recorder lives independently of disk storage so Ctrl+. works
            # even when no save-to-disk path was provided. Storage is purely
            # for persistence across sessions.
            self._macro_recorder = MacroRecorder()
            self._editor.macro_recorder = self._macro_recorder
            if macro_storage_path:
                self._macro_storage = MacroStorage(macro_storage_path)

    def compose(self) -> ComposeResult:
        self._split_container = Container()
        # Start with horizontal layout (so a future "Split Vertical" shows
        # side-by-side without re-laying out the sole editor on toggle).
        self._split_container.styles.layout = "horizontal"
        with self._split_container:
            yield self._editor
        self._search_panel = SearchPanel()
        yield self._search_panel

    # ------------------------------------------------------------------
    # Macro recording helpers
    # ------------------------------------------------------------------

    def _record(self, kind: str, data: str = "") -> None:
        rec = self._macro_recorder
        if rec is None or not rec.is_recording:
            return
        from tyui.windowing.core.macro import MacroAction
        rec.record_action(MacroAction(kind=kind, data=data))

    def _record_search_once(self) -> None:
        """Record one `search` action per logical session."""
        if self._macro_search_recorded:
            return
        if self._search_panel is None or self._search_panel.find_input is None:
            return
        pattern = self._search_panel.find_input.value
        if not pattern:
            return
        payload = {
            "pattern": pattern,
            "options": dataclasses.asdict(self._search_panel.options),
        }
        self._record("search", json.dumps(payload))
        self._macro_search_recorded = True

    @property
    def is_split(self) -> bool:
        return self._editor2 is not None

    @staticmethod
    def _layout_for(orientation: str) -> str:
        # "horizontal" = horizontal divider → panes stacked top/bottom (Textual: vertical layout)
        # "vertical"   = vertical divider   → panes side-by-side         (Textual: horizontal layout)
        return "vertical" if orientation == "horizontal" else "horizontal"

    def _new_fold_engine(self) -> FoldEngine | None:
        if self._fold_registry is None:
            return None
        return FoldEngine(self._fold_registry)

    def toggle_split(self, orientation: str = "vertical") -> None:
        """Toggle a second editor view on the same buffer.

        orientation: "horizontal" => split by a horizontal line (top/bottom),
                     "vertical"   => split by a vertical line (side-by-side).
        Re-applying the same orientation closes the split; a different
        orientation switches the layout without closing it.
        """
        target_layout = self._layout_for(orientation)
        if self.is_split:
            if self._current_layout_name() == target_layout:
                self._unsplit()
                return
            # Switch layout direction — reset editor sizes and splitter orientation.
            if self._split_container is not None:
                self._split_container.styles.layout = target_layout
            self._reset_editor_sizes()
            if self._splitter is not None:
                self._splitter.set_direction(self._splitter_direction_for(target_layout))
            return
        # Not split yet — set layout and mount second editor + splitter.
        if self._split_container is not None:
            self._split_container.styles.layout = target_layout
        self._mount_second_editor(target_layout)

    def _current_layout_name(self) -> str:
        if self._split_container is None:
            return ""
        layout = self._split_container.styles.layout
        return getattr(layout, "name", "") or ""

    @staticmethod
    def _splitter_direction_for(layout_name: str) -> str:
        # horizontal layout (side-by-side editors) → vertical divider bar
        # vertical   layout (stacked editors)      → horizontal divider bar
        return "v-divider" if layout_name == "horizontal" else "h-divider"

    def _mount_second_editor(self, layout_name: str) -> None:
        if self._editor2 is not None or self._split_container is None:
            return
        ed2 = EditorWidget(buffer=self._buffer, fold_engine=self._new_fold_engine())
        ed2.macro_skip_keys.update(self._SEARCH_SKIP_KEYS)
        if self._enable_macros and self._macro_recorder is not None:
            ed2.macro_recorder = self._macro_recorder
        splitter = Splitter(self._splitter_direction_for(layout_name))
        self._splitter = splitter
        self._editor2 = ed2
        self._split_container.mount(splitter)
        self._split_container.mount(ed2)

    def _unsplit(self) -> None:
        if self._editor2 is None:
            return
        ed2 = self._editor2
        splitter = self._splitter
        self._editor2 = None
        self._splitter = None
        ed2.remove()
        if splitter is not None:
            splitter.remove()
        self._reset_editor_sizes()

    def _reset_editor_sizes(self) -> None:
        """Restore both panes to equal 1fr sizing after drag-resize or layout swap."""
        for ed in (self._editor, self._editor2):
            if ed is None:
                continue
            ed.styles.width = "1fr"
            ed.styles.height = "1fr"

    def on_splitter_dragged(self, event: Splitter.Dragged) -> None:
        """Resize the first editor pane based on splitter drag delta."""
        if self._editor2 is None:
            return
        layout = self._current_layout_name()
        if layout == "horizontal":
            current = self._editor.outer_size.width or self._editor.size.width
            self._editor.styles.width = max(5, current + event.dx)
        else:
            current = self._editor.outer_size.height or self._editor.size.height
            self._editor.styles.height = max(3, current + event.dy)
        event.stop()

    def _sync_other(self, sender: EditorWidget, rescan: bool) -> None:
        for ed in (self._editor, self._editor2):
            if ed is None or ed is sender or not ed.is_mounted:
                continue
            if rescan:
                ed._rescan_folds()
                ed._refresh_render()
            else:
                ed.refresh()

    def on_editor_widget_buffer_modified(self, event: EditorWidget.BufferModified) -> None:
        self.is_dirty = event.modified
        self._sync_other(event.editor, rescan=True)

    def on_editor_widget_cursor_moved(self, event: EditorWidget.CursorMoved) -> None:
        self._sync_other(event.editor, rescan=False)

    def get_commands(self) -> list[WindowCommand]:
        commands = [
            WindowCommand(id="save", label="Save", handler=self._save, hotkey="ctrl+s"),
            WindowCommand(id="save_as", label="Save As...", handler=self._save_as),
            WindowCommand(id="find", label="Find", handler=self._find, hotkey="ctrl+f"),
            WindowCommand(id="replace", label="Replace", handler=self._replace, hotkey="f4"),
            # Editor copy/paste commands. Hotkeys live on EditorWidget's
            # BINDINGS — duplicating them here would dispatch the action twice
            # (binding fires action_copy/paste, then App.on_key → router fires
            # it again), which clears the selection between calls and pastes
            # twice.
            WindowCommand(id="copy", label="Copy", handler=self._editor.action_copy),
            WindowCommand(id="paste", label="Paste", handler=self._editor.action_paste),
            WindowCommand(
                id="split_h", label="Split Horizontal",
                handler=lambda: self.toggle_split("horizontal"),
                hotkey="ctrl+backslash",
            ),
            WindowCommand(
                id="split_v", label="Split Vertical",
                handler=lambda: self.toggle_split("vertical"),
            ),
        ]
        if self._enable_folding:
            commands.extend([
                WindowCommand(id="fold_toggle", label="Fold Toggle", handler=self._fold_toggle, hotkey="f7"),
                WindowCommand(id="fold_all", label="Fold All", handler=self._fold_all),
                WindowCommand(id="unfold_all", label="Unfold All", handler=self._unfold_all),
            ])
        if self._enable_macros and self._macro_recorder:
            # Ctrl+R: reliably delivered by every terminal we care about
            # (macOS Terminal/iTerm/Alacritty), free in EditorWidget's
            # BINDINGS, and "R" matches the universal "record" mnemonic.
            # Function keys (F11/F12) are unreliable on macOS where they
            # are claimed by Show-Desktop / Dashboard.
            commands.append(
                WindowCommand(id="record_macro", label="Record Macro", handler=self._toggle_macro, hotkey="f8"),
            )
        return commands

    def _save(self) -> None:
        if self._editor.buffer.file_path:
            self._editor.buffer.save()
            self.is_dirty = False

    def _save_as(self) -> None:
        # Delegate to the host app — modal dialogs need Desktop access.
        app = getattr(self, "app", None)
        action = getattr(app, "action_save_as", None)
        if callable(action):
            action(self)

    def save_to(self, path: str) -> None:
        """Persist current buffer text to ``path`` and re-bind the buffer.

        Used by the host app's Save-As flow. Updates window title and
        clears the dirty flag on success.
        """
        self._editor.buffer.file_path = path
        self._editor.buffer.save()
        self.window_title = Path(path).name
        self.is_dirty = False

    def _active_editor(self) -> EditorWidget:
        if self._editor2 is not None and getattr(self._editor2, "has_focus", False):
            return self._editor2
        return self._editor

    def _find(self) -> None:
        self.action_open_find()

    def _replace(self) -> None:
        self.action_open_replace()

    _SEARCH_SKIP_KEYS = {"f4", "f3", "shift+f3", "f6", "escape", "ctrl+f"}

    def action_open_find(self) -> None:
        self._macro_search_recorded = False
        if self._search_panel is not None:
            self._search_panel.show_find()

    def action_open_replace(self) -> None:
        self._macro_search_recorded = False
        if self._search_panel is not None:
            self._search_panel.show_replace()

    def action_find_next(self) -> None:
        self._record_search_once()
        self._active_editor().find_next()
        self._record("find_next")
        self._update_panel_status()

    def action_find_prev(self) -> None:
        self._record_search_once()
        self._active_editor().find_prev()
        self._record("find_prev")
        self._update_panel_status()

    def action_replace_all(self) -> None:
        if self._search_panel is None or self._search_panel.replace_input is None:
            return
        self._handle_replace_all(self._search_panel.replace_input.value)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "close_search":
            # Only activate (and consume) Escape when the panel is visible.
            # Returning False means disabled+hidden → key bubbles to parent.
            if self._search_panel is None or not self._search_panel.display:
                return False
        return True

    def action_close_search(self) -> None:
        if self._search_panel is not None and self._search_panel.display:
            ed = self._active_editor()
            ed.clear_search()
            self._search_panel.close()
            ed.focus()
            self._macro_search_recorded = False

    def _update_panel_status(self) -> None:
        if self._search_panel is None:
            return
        ed = self._active_editor()
        self._search_panel.set_status(ed._current_match_idx, len(ed._search_matches))

    def on_search_panel_replace_one(self, event: SearchPanel.ReplaceOne) -> None:
        self._record_search_once()
        if self._active_editor().replace_current(event.replacement):
            self._record("replace_one", json.dumps({"replacement": event.replacement}))
            self._update_panel_status()

    def on_search_panel_replace_all(self, event: SearchPanel.ReplaceAll) -> None:
        self._handle_replace_all(event.replacement)

    def on_search_panel_closed(self, _event: SearchPanel.Closed) -> None:
        ed = self._active_editor()
        ed.clear_search()
        ed.focus()
        self._macro_search_recorded = False

    def _handle_replace_all(self, replacement: str) -> None:
        ed = self._active_editor()
        count = len(ed._search_matches)
        if count == 0:
            if self._search_panel is not None:
                self._search_panel.set_status(-1, 0)
            return

        def _do(confirmed: bool) -> None:
            if not confirmed:
                return
            self._record_search_once()
            n = ed.replace_all(replacement)
            self._record("replace_all", json.dumps({"replacement": replacement}))
            self._notify(f"{n} replacements made")
            self._update_panel_status()

        self._confirm_replace_all(count, _do)

    def _confirm_replace_all(self, count: int, callback) -> None:
        """Show modal yes/no dialog. Tests monkeypatch this method."""
        from tyui.windowing.desktop import Desktop
        from tyui.windowing.editor.replace_dialog import ReplaceAllDialog
        from tyui.windowing.helpers import show_modal

        desktop: Desktop | None = None
        for node in self.ancestors_with_self:
            if isinstance(node, Desktop):
                desktop = node
                break
        if desktop is None:
            callback(False)
            return
        try:
            dialog = ReplaceAllDialog(count, callback)
            show_modal(desktop, dialog, title="Replace All", size=(38, 7))
        except Exception:
            callback(True)

    def on_search_panel_pattern_changed(self, event: SearchPanel.PatternChanged) -> None:
        n = self._active_editor().search(event.pattern, event.options)
        if self._search_panel is None:
            return
        if n == -1:
            self._search_panel.set_status(-1, 0, error="bad regex")
        elif not event.pattern:
            self._search_panel.set_status(-1, 0, error="—")
        else:
            ed = self._active_editor()
            self._search_panel.set_status(ed._current_match_idx, len(ed._search_matches))

    def on_search_panel_find_next(self, _e: SearchPanel.FindNext) -> None:
        self._record_search_once()
        self._active_editor().find_next()
        self._record("find_next")
        self._update_panel_status()

    def on_search_panel_find_prev(self, _e: SearchPanel.FindPrev) -> None:
        self._record_search_once()
        self._active_editor().find_prev()
        self._record("find_prev")
        self._update_panel_status()

    def _fold_all(self) -> None:
        self._editor.fold_all()

    def _unfold_all(self) -> None:
        self._editor.unfold_all()

    def _fold_toggle(self) -> None:
        ed = self._active_editor()
        regions = getattr(ed, "_fold_regions", [])
        any_collapsed = any(getattr(r, "collapsed", False) for r in regions)
        if any_collapsed:
            ed.unfold_all()
        else:
            ed.fold_all()

    def action_fold_toggle(self) -> None:
        self._fold_toggle()

    def action_macro_toggle(self) -> None:
        self._toggle_macro()

    def _toggle_macro(self) -> None:
        rec = self._macro_recorder
        if rec is None:
            return
        # The toggle key itself must not become part of the recorded
        # action stream — otherwise replay would re-trigger recording.
        self._editor.macro_skip_keys.update({"ctrl+r", "f8"})
        if rec.is_recording:
            actions = rec.stop_recording()
            if not actions:
                self._notify("Macro discarded — no actions recorded")
                return
            self._prompt_assign_macro(actions)
        else:
            rec.start_recording()
            self._notify("Recording macro — Ctrl+R to stop")

    def _prompt_assign_macro(self, actions: list) -> None:
        """Show MacroAssignDialog and bind the captured key to a replay
        action on the host App. Persists to ``_macro_storage`` when present
        and the user ticked "Save permanently"."""
        from .macro_dialog import MacroAssignDialog

        app = getattr(self, "app", None)
        if app is None:
            self._notify(f"Macro recorded: {len(actions)} actions (no host)")
            return

        def _on_result(result: object) -> None:
            if result is None:
                self._notify("Macro discarded")
                return
            key, permanent = result  # type: ignore[misc]
            if self._macro_storage is not None and permanent:
                try:
                    self._macro_storage.save_macro(
                        f"macro_{key}", key, actions, permanent
                    )
                except Exception:
                    pass
            self._register_macro_replay(app, key, actions)
            self._notify(f"Macro assigned to {key}")

        try:
            app.push_screen(
                MacroAssignDialog(action_count=len(actions)), _on_result
            )
        except Exception:
            self._notify(f"Macro recorded: {len(actions)} actions")

    def _register_macro_replay(self, app, key: str, actions: list) -> None:
        """Stash macro actions on the host App and bind ``key`` to replay.

        Uses dynamic attribute attachment so the host App needs no built-in
        macro plumbing — the demo's existing ``action_replay_macro`` is
        not available in tyui.app.TyuiApp, but Textual will resolve the
        action via :func:`getattr` if we attach it here.
        """
        macros: dict = getattr(app, "_macros", None) or {}
        macros[key] = actions
        try:
            setattr(app, "_macros", macros)
        except Exception:
            return
        editor = self._editor
        if not hasattr(app, "action_replay_macro"):
            def _replay(k: str) -> None:
                acts = getattr(app, "_macros", {}).get(k)
                if not acts or editor is None:
                    return
                editor.focus()
                for a in acts:
                    if a.kind == "keypress":
                        pressed, _, char = a.data.partition("|")
                        editor.simulate_keypress(pressed, char or None)
                    elif a.kind == "search":
                        try:
                            payload = json.loads(a.data)
                            editor.search(payload["pattern"], SearchOptions(**payload["options"]))
                        except Exception:
                            log.exception("macro replay: search failed")
                    elif a.kind == "find_next":
                        editor.find_next()
                    elif a.kind == "find_prev":
                        editor.find_prev()
                    elif a.kind == "replace_one":
                        try:
                            editor.replace_current(json.loads(a.data)["replacement"])
                        except Exception:
                            log.exception("macro replay: replace_one failed")
                    elif a.kind == "replace_all":
                        try:
                            editor.replace_all(json.loads(a.data)["replacement"])
                        except Exception:
                            log.exception("macro replay: replace_all failed")
            try:
                setattr(app, "action_replay_macro", _replay)
            except Exception:
                pass
        try:
            app.bind(key, f"replay_macro('{key}')", description=f"Macro {key}")
        except Exception:
            pass

    def _notify(self, message: str) -> None:
        """Surface a short status message to the user. Falls back silently
        if the content is not yet mounted on an App."""
        app = getattr(self, "app", None)
        if app is None:
            return
        try:
            app.notify(message)
        except Exception:
            pass
