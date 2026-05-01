"""tyui — Norton Commander/mc-style terminal shell on top of tyui.windowing.

Phase 1: skeleton only. Composes MenuBar + Desktop + CommandLine + StatusBar
and mounts an initial set of windows depending on the launch mode:

    fm     -> two FilePanel windows tiled in the upper area (default)
    editor -> a placeholder editor window maximized; panels mounted hidden
    cli    -> a placeholder agent window maximized; panels mounted hidden

Later phases will: wire commands, file ops, real editor/agent content, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from rich.segment import Segment
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.geometry import Offset, Size
from textual.strip import Strip

from tyui.fm.actions import (
    OpResult,
    copy_paths,
    delete_paths,
    mkdir_at,
    move_paths,
)
from tyui.fm.commandline import CommandLine
from tyui.fm.dialogs import (
    ConfirmDialog,
    CopyMoveDialog,
    InputDialog,
    NewFileDialog,
    ProgressDialog,
)
from tyui.fm.file_panel import FilePanel
from tyui.fm.keymap import DEFAULT_FKEY_LABELS, EDITOR_FKEY_LABELS
from tyui.fm.sort import SortOrder
from tyui.windowing import (
    CommandDispatcher,
    CommandPaletteContent,
    CommandRegistry,
    CommandRouter,
    Decorations,
    Desktop,
    Dropdown,
    Menu,
    MenuBar,
    MenuItem,
    MenuSeparator,
    StatusBar,
    StatusItem,
    Window,
    WindowCommand,
    WindowFocusChanged,
    WindowManager,
    make_window,
    show_command_palette,
    show_modal,
)
from tyui.windowing.content import WindowContent
from tyui.fm.hex_viewer import HexViewerContent, HexViewerWidget
from tyui.fm.viewer import ViewerContent
from tyui.windowing.editor import EditorContent


class _FocusableEditorContent(EditorContent):
    """EditorContent variant that focuses the inner editor on mount.

    The base EditorContent is a non-focusable wrapper; calling .focus()
    on it lands on the wrapper which doesn't accept keys. The actual
    focusable widget is `_editor`. Auto-focusing here removes the
    "click in the window before keys work" gotcha after F4.
    """

    def on_mount(self) -> None:
        self._editor.focus()
from tyui.windowing.helpers import ModalWindow


# --- Dialog payload types --------------------------------------------------
# Each F-key flow attaches a typed request to its dialog so
# on_confirm_dialog_result / on_input_dialog_submitted can dispatch via
# isinstance instead of stringly-typed `_op` attributes.


@dataclass(frozen=True)
class CopyMoveRequest:
    op: Literal["copy", "move"]
    targets: list[Path]
    dest: Path


@dataclass(frozen=True)
class DeleteRequest:
    targets: list[Path]


@dataclass(frozen=True)
class MkdirRequest:
    parent: Path


@dataclass(frozen=True)
class NewFileRequest:
    parent: Path


@dataclass(frozen=True)
class SaveAsRequest:
    """Routes the prompt dialog used by Editor → Save As back to the editor."""

    editor: "EditorContent"


@dataclass(frozen=True)
class HexSearchRequest:
    """Routes the InputDialog used by F3 hex viewer back to its widget."""

    widget: HexViewerWidget


LaunchMode = Literal["fm", "editor", "cli"]


class _StubContent(WindowContent):
    """One-line placeholder used by editor/agent windows in Phase 1."""

    def __init__(self, message: str, title: str) -> None:
        super().__init__()
        self._message = message
        self.window_title = title

    def render_line(self, y: int) -> Strip:
        if y != 0 or self.size.width <= 0:
            return Strip.blank(max(0, self.size.width))
        text = f" {self._message} ".ljust(self.size.width)[: self.size.width]
        return Strip([Segment(text)])


class TyuiApp(App):
    """Top-level app shell for the NC-style tyui."""

    TITLE = "tyui"

    CSS = """
    Screen { background: $panel; }
    Desktop { margin-top: 1; margin-bottom: 2; }
    """

    BINDINGS = [
        # Static bindings only for app-level mechanics (menu activation,
        # focus chain, app quit, modal escape). The Norton-Commander F-keys
        # (F3 View, F4 Edit, F5 Copy, F6 Move, F7 Mkdir, F8 Delete) come
        # from FilePanel.get_commands() and are routed dynamically via
        # CommandRouter when a panel has focus. Editor-scoped hotkeys
        # (Save/Split/Fold) come from EditorContent.get_commands().
        Binding("f9", "menu", "Menu", show=False),
        Binding("f10", "quit", "Quit", show=False),
        Binding("escape", "close_editor", "Close editor", show=False),
        # priority=True so Tab routes to panel-switch instead of Textual's
        # default focus cycler. Future text-input children (CommandLine
        # input, dialogs) will need a guard inside action_focus_other_panel
        # that no-ops when self.focused is an Input-derived widget.
        Binding("tab", "focus_other_panel", "Other panel", show=False, priority=True),
        # Shift+Tab cycles forward through every visible desktop window
        # (panels + open editor/viewer). priority=True so Textual's default
        # shift+tab focus_previous doesn't pre-empt it.
        Binding("shift+tab", "cycle_window", "Other window", show=False, priority=True),
        Binding("alt+l", "focus_left_panel", "Left panel", show=False),
        Binding("alt+r", "focus_right_panel", "Right panel", show=False),
    ]

    def __init__(
        self,
        *,
        launch_mode: LaunchMode = "fm",
        initial_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        # Drop Textual's built-in priority ctrl+q→quit binding so the key is
        # free for user-bindable actions (e.g. recorded macros). The app
        # exits via F10 instead.
        self._bindings.key_to_bindings.pop("ctrl+q", None)
        self.launch_mode: LaunchMode = launch_mode
        self.initial_path: Path | None = (
            Path(initial_path).expanduser() if initial_path else None
        )
        self.desktop: Desktop | None = None
        self.menu_bar: MenuBar | None = None
        self.status_bar: StatusBar | None = None
        self.command_line: CommandLine | None = None
        self.manager: WindowManager | None = None
        # Saved before activating the menu bar so Esc/dismiss can return
        # focus to whatever the user was on (typically a panel).
        self._pre_menu_focus = None
        # Captured separately from widget focus: when the menu is opened via
        # mouse click, MenuBar steals widget focus before our handler runs,
        # but Desktop.focused_window is unaffected (Dropdown is not a Window).
        # Preserving it lets us restore z-order on dismiss.
        self._pre_menu_window: Window | None = None
        # Saved before opening a modal dialog so _close_modal returns focus
        # to the panel the user was on, not always to panel-left.
        self._pre_modal_panel_id: str | None = None
        # TV-style command dispatcher: focused window's content publishes
        # commands; this app routes hotkeys + menu items through them.
        self.command_registry: CommandRegistry = CommandRegistry()
        self.dispatcher: CommandDispatcher | None = None
        self.router: CommandRouter | None = None
        self._active_dropdown: Dropdown | None = None
        # Full menu list, including focus-scoped menus like "Editor" that
        # are only mounted on `menu_bar.menus` while a relevant window is
        # focused.
        self._all_menus: list[Menu] = []

    def compose(self) -> ComposeResult:
        self.menu_bar = MenuBar()
        self.desktop = Desktop(theme_name="modern_dark")
        self.command_line = CommandLine(id="cmdline")
        self.status_bar = StatusBar(items=self._panel_status_items())
        yield self.menu_bar
        yield self.desktop
        yield self.command_line
        yield self.status_bar

    def on_mount(self) -> None:
        assert self.desktop is not None and self.menu_bar is not None
        self.manager = WindowManager(self.desktop)
        self.dispatcher = CommandDispatcher(self.desktop, self.command_registry)
        self.router = CommandRouter(self.dispatcher)
        self._register_app_commands()
        self.menu_bar.bind_dispatcher(self.dispatcher)
        self._build_menus()
        self._mount_initial_windows()
        self._refresh_panels()
        if self.launch_mode == "fm":
            self._focus_panel("panel-left")
        # Watch the menu bar's active index: when it transitions to None
        # the menu was dismissed (Esc / item chosen), so restore focus to
        # whatever was active before F9.
        self.watch(
            self.menu_bar,
            "active_index",
            self._on_menu_active_index_changed,
            init=False,
        )
        # Defer layout: at on_mount Desktop.size is still 0×0, so tile
        # math early-returns. call_after_refresh fires once Textual has
        # propagated the real terminal size to children.
        self.call_after_refresh(self._apply_default_layout)

    def on_confirm_dialog_result(self, event: ConfirmDialog.Result) -> None:
        ctx = event.dialog.context
        self._close_modal(event.dialog)
        if not event.confirmed:
            return
        if isinstance(ctx, DeleteRequest):
            self._run_delete(ctx)

    def on_copy_move_dialog_submitted(
        self, event: CopyMoveDialog.Submitted
    ) -> None:
        ctx = event.dialog.context
        self._close_modal(event.dialog)
        if not isinstance(ctx, CopyMoveRequest):
            return
        raw = (event.value or "").strip()
        user_dest = Path(raw).expanduser() if raw else ctx.dest
        self._run_copy_move(ctx, user_dest)

    def on_copy_move_dialog_cancelled(
        self, event: CopyMoveDialog.Cancelled
    ) -> None:
        self._close_modal(event.dialog)

    def _run_copy_move(self, req: CopyMoveRequest, user_dest: Path) -> None:
        if self.desktop is None:
            return
        if user_dest.is_dir():
            dest_dir = user_dest
            rename_to: str | None = None
        else:
            dest_dir = user_dest.parent
            rename_to = user_dest.name if len(req.targets) == 1 else None
        op_label = "Copying" if req.op == "copy" else "Moving"
        progress = ProgressDialog(title=op_label, total=len(req.targets))
        show_modal(self.desktop, progress, title=op_label, size=(60, 7))
        self.call_after_refresh(progress.focus)

        def _worker() -> None:
            def _on_progress(i: int, n: int) -> None:
                self.call_from_thread(progress.set_progress, i, n)

            if req.op == "copy":
                result = copy_paths(
                    req.targets,
                    dest_dir,
                    rename_to=rename_to,
                    on_progress=_on_progress,
                    cancel_event=progress.cancel_event,
                )
            else:
                result = move_paths(
                    req.targets,
                    dest_dir,
                    rename_to=rename_to,
                    on_progress=_on_progress,
                    cancel_event=progress.cancel_event,
                )
            self.call_from_thread(self._finish_op, req.op, progress, result)

        self.run_worker(_worker, thread=True, exclusive=False, group="fileop")

    def _run_delete(self, req: DeleteRequest) -> None:
        if self.desktop is None:
            return
        progress = ProgressDialog(title="Deleting", total=len(req.targets))
        show_modal(self.desktop, progress, title="Delete", size=(60, 7))
        self.call_after_refresh(progress.focus)

        def _worker() -> None:
            def _on_progress(i: int, n: int) -> None:
                self.call_from_thread(progress.set_progress, i, n)

            result = delete_paths(
                req.targets,
                on_progress=_on_progress,
                cancel_event=progress.cancel_event,
            )
            self.call_from_thread(self._finish_op, "delete", progress, result)

        self.run_worker(_worker, thread=True, exclusive=False, group="fileop")

    def _finish_op(
        self,
        op: str,
        progress: ProgressDialog,
        result: OpResult,
    ) -> None:
        """Called on the main thread after a worker copy/move/delete finishes."""
        self._close_modal(progress)
        self._report_op_result(op, result)
        self._refresh_panels()

    def on_input_dialog_submitted(self, event: InputDialog.Submitted) -> None:
        ctx = event.dialog.context
        if isinstance(ctx, MkdirRequest) and event.value:
            result = mkdir_at(ctx.parent, event.value)
            self._report_op_result("mkdir", result)
            self._refresh_panels()
        elif isinstance(ctx, HexSearchRequest) and event.value:
            # Close modal first so the viewer is back on top before we scroll
            # — otherwise the post-search refresh paints behind the dialog.
            self._close_modal(event.dialog)
            ctx.widget.search(event.value)
            ctx.widget.focus()
            return
        self._close_modal(event.dialog)

    def on_hex_viewer_widget_find_requested(
        self, event: HexViewerWidget.FindRequested
    ) -> None:
        if self.desktop is None:
            return
        self._remember_active_panel_id()
        dialog = InputDialog(
            "Find string:",
            initial="",
            context=HexSearchRequest(widget=event.widget),
        )
        show_modal(self.desktop, dialog, title="Find", size=(50, 5))
        # InputDialog defers focus until on_mount fires; calling focus_input
        # immediately after show_modal is the established pattern in mkdir.
        dialog.focus_input()

    def on_input_dialog_cancelled(self, event: InputDialog.Cancelled) -> None:
        self._close_modal(event.dialog)

    def _report_op_result(self, op_name: str, result: OpResult) -> None:
        """Hook for surfacing OpResult.errors to the user.

        Phase 5 (CommandRunner / ConsoleOutputWindow) wires this to the
        console output window. Until then the errors are silently dropped
        — but every action call site already passes through this seam so
        Phase 5 is a one-place change.
        """
        # Intentionally a no-op for Phase 3.
        return

    def on_resize(self, event) -> None:
        # Re-tile when the terminal resizes so panels keep filling the
        # full width. Guard on `self.manager` because Textual fires the
        # first Resize event before `on_mount` has finished — at that
        # point WindowManager isn't constructed yet and the windows
        # haven't been mounted onto the Desktop.
        if (
            self.launch_mode == "fm"
            and self.desktop is not None
            and self.manager is not None
        ):
            self._apply_default_layout()

    # --- private helpers --------------------------------------------------

    def _register_app_commands(self) -> None:
        """Register focus-independent commands.

        Panel F-keys (View/Edit/Copy/Move/Mkdir/Delete) are NOT registered
        here — they live on FilePanel.get_commands() and route via focus.
        Editor commands (Save/Find/Split/Fold) live on EditorContent.
        """
        m = self.manager
        # Hotkeys ALREADY declared in BINDINGS (f9, f10, tab, alt+l, alt+r,
        # escape) are intentionally NOT duplicated here — both paths firing
        # would call the action twice (and destroy ``_pre_menu_focus``).
        cmds = [
            WindowCommand(id="app.menu", label="Menu", handler=self.action_menu),
            WindowCommand(id="app.quit", label="Quit", handler=self.exit),
            WindowCommand(id="view.tile_h", label="Tile horizontal", handler=lambda: m.tile_horizontal()),
            WindowCommand(id="view.tile_v", label="Tile vertical", handler=lambda: m.tile_vertical()),
            WindowCommand(id="view.cascade", label="Cascade", handler=lambda: m.cascade()),
            WindowCommand(id="window.hide", label="Hide", handler=lambda: m.hide_focused()),
            WindowCommand(id="window.maximize", label="Maximize", handler=lambda: m.maximize_focused(), hotkey="f5"),
            WindowCommand(id="panel.focus_left", label="Focus left panel", handler=lambda: self._focus_panel("panel-left")),
            WindowCommand(id="panel.focus_right", label="Focus right panel", handler=lambda: self._focus_panel("panel-right")),
            WindowCommand(id="palette.open", label="Command Palette", handler=self.action_open_palette, hotkey="ctrl+p"),
        ]
        # Per-panel sort commands. Side-suffixed labels are what the command
        # palette shows; menu items override the label so the dropdown reads
        # "Name / Extension / Size / Date" without redundant " (left)" tags.
        for side, panel_id in (("left", "panel-left"), ("right", "panel-right")):
            for order, label in (
                (SortOrder.NAME, "name"),
                (SortOrder.EXT, "extension"),
                (SortOrder.SIZE, "size"),
                (SortOrder.MTIME, "date"),
            ):
                cmds.append(WindowCommand(
                    id=f"panel.{side}.sort_{order.value}",
                    label=f"Sort by {label} ({side})",
                    handler=(lambda pid=panel_id, o=order: self._set_panel_sort(pid, o)),
                ))
        self.command_registry.register_many(cmds)

    def action_open_palette(self) -> None:
        if self.dispatcher is None or self.desktop is None:
            return
        show_command_palette(self.desktop, self.dispatcher)

    def on_command_palette_content_picked(self, message) -> None:
        win = self._modal_window_for(message.palette)
        if win is not None and self.desktop is not None:
            self.desktop.remove_window(win)
        if self.dispatcher is not None:
            self.dispatcher.dispatch(message.command.id)
        message.stop()

    def on_command_palette_content_dismissed(self, message) -> None:
        win = self._modal_window_for(message.palette)
        if win is not None and self.desktop is not None:
            self.desktop.remove_window(win)
        message.stop()

    def _modal_window_for(self, content):
        node = getattr(content, "parent", None)
        while node is not None:
            if isinstance(node, ModalWindow):
                return node
            node = getattr(node, "parent", None)
        return None

    def _build_menus(self) -> None:
        """Populate the menu bar.

        ``MenuItem(command_id=...)`` resolves through the dispatcher: lazy
        labels, hotkey labels and enabled-state come from the matching
        :class:`WindowCommand`. Focus-scoped commands (panel.* / save / find
        / split_*) auto-light when the relevant window is focused.
        """
        assert self.menu_bar is not None
        self._all_menus = [
            Menu("Left", [
                MenuItem(command_id="panel.focus_left"),
                MenuSeparator(),
                MenuItem(label="Sort by name",      command_id="panel.left.sort_name"),
                MenuItem(label="Sort by extension", command_id="panel.left.sort_ext"),
                MenuItem(label="Sort by size",      command_id="panel.left.sort_size"),
                MenuItem(label="Sort by date",      command_id="panel.left.sort_mtime"),
            ]),
            Menu("File", [
                MenuItem(command_id="panel.new"),
                MenuItem(command_id="panel.view"),
                MenuItem(command_id="panel.edit"),
                MenuSeparator(),
                MenuItem(command_id="save"),
                MenuItem(command_id="save_as"),
            ]),
            Menu("Command", [
                MenuItem(command_id="panel.copy"),
                MenuItem(command_id="panel.move"),
                MenuItem(command_id="panel.mkdir"),
                MenuItem(command_id="panel.delete"),
            ]),
            Menu("Right", [
                MenuItem(command_id="panel.focus_right"),
                MenuSeparator(),
                MenuItem(label="Sort by name",      command_id="panel.right.sort_name"),
                MenuItem(label="Sort by extension", command_id="panel.right.sort_ext"),
                MenuItem(label="Sort by size",      command_id="panel.right.sort_size"),
                MenuItem(label="Sort by date",      command_id="panel.right.sort_mtime"),
            ]),
            Menu("Editor", [
                MenuItem("Agent", hotkey="F12"),
                MenuSeparator(),
                # New sub‑section for editor‑level actions
                MenuSeparator(),
                MenuItem(command_id="find"),
                MenuItem(command_id="copy"),
                MenuItem(command_id="paste"),
                # Separator after Paste
                MenuSeparator(),
                # Existing editor commands
                MenuItem(command_id="split_h"),
                MenuItem(command_id="split_v"),
                MenuItem(command_id="fold_all"),
                MenuItem(command_id="unfold_all"),
                MenuItem(command_id="record_macro"),
            ]),
            # Items are rebuilt on every menu activation by
            # ``_refresh_windows_menu``; the empty list here is a placeholder.
            Menu("Windows", []),
            Menu("Help", [
                MenuItem(command_id="app.quit"),
            ]),
        ]
        self._recompute_menu_bar()

    def _recompute_menu_bar(self) -> None:
        """Show focus-scoped menus only when relevant.

        ``Editor`` hosts editor-only commands (split / fold / record_macro);
        showing it with a FilePanel focused leaves dead, disabled items
        in the dropdown. Filter the menu list to match the focused window's
        content type.
        """
        if self.menu_bar is None or not self._all_menus:
            return
        show_editor = self._is_editor_focused()
        self._refresh_windows_menu()
        self.menu_bar.menus = [
            m for m in self._all_menus
            if m.label != "Editor" or show_editor
        ]
        # Reset highlight if the active menu got filtered out.
        if (
            self.menu_bar.active_index is not None
            and self.menu_bar.active_index >= len(self.menu_bar.menus)
        ):
            self.menu_bar.active_index = None
        self.menu_bar.refresh()
        self._refresh_status_bar()

    def _panel_status_items(self) -> list[StatusItem]:
        # F-keys that drive file-panel actions. F1 (Help) and F2 (UsrMnu) are
        # not implemented yet — leaving their handler at None makes the
        # status bar ignore clicks on those cells.
        handlers: dict[str, Callable[[], None]] = {
            "3":  self.action_view,
            "4":  self.action_edit,
            "5":  self.action_copy,
            "6":  self.action_move,
            "7":  self.action_mkdir,
            "8":  self.action_delete,
            "9":  self.action_menu,
            "10": self.exit,
        }
        return [
            StatusItem(key=label.key, label=label.label, handler=handlers.get(label.key))
            for label in DEFAULT_FKEY_LABELS
        ]

    def _editor_status_items(self) -> list[StatusItem]:
        # F-keys for an editor window. Routes through the dispatcher so the
        # focused editor's own commands fire (no panel-actions reachable —
        # those would otherwise crash because there is no active FilePanel).
        def _dispatch(cmd_id: str) -> Callable[[], None]:
            def _run() -> None:
                if self.dispatcher is not None:
                    self.dispatcher.dispatch(cmd_id)
            return _run

        handlers: dict[str, Callable[[], None]] = {
            "2":  _dispatch("save"),
            "3":  _dispatch("save_as"),
            "4":  _dispatch("replace"),
            "5":  _dispatch("split_h"),
            "6":  _dispatch("split_v"),
            "7":  _dispatch("fold_toggle"),
            "8":  _dispatch("record_macro"),
            "9":  self.action_menu,
            "10": self.exit,
        }
        return [
            StatusItem(key=label.key, label=label.label, handler=handlers.get(label.key))
            for label in EDITOR_FKEY_LABELS
        ]

    def _refresh_status_bar(self) -> None:
        if self.status_bar is None:
            return
        if self._is_editor_focused():
            self.status_bar.items = self._editor_status_items()
        else:
            self.status_bar.items = self._panel_status_items()

    def _is_editor_focused(self) -> bool:
        if self.desktop is None:
            return False
        win = self.desktop.focused_window
        if win is None:
            return False
        return isinstance(getattr(win, "content", None), EditorContent)

    def _refresh_windows_menu(self) -> None:
        """Rebuild the ``Windows`` menu's items from ``desktop.windows``.

        Each visible desktop window gets a row whose handler raises and
        focuses that window through ``Desktop.focus_window`` (which keeps
        z-order in sync). Cycling shortcut Shift+Tab is shown next to the
        first entry as a hint, since per-row hotkeys would conflict with
        editor input.
        """
        if self.desktop is None:
            return
        win_menu = next(
            (m for m in self._all_menus if m.label == "Windows"), None
        )
        if win_menu is None:
            return

        def _title(w) -> str:
            spec = getattr(w, "title", None)
            text = getattr(spec, "text", None) if spec is not None else None
            if text:
                return text
            wid = getattr(w, "id", None)
            return wid or "<window>"

        items: list[MenuItem | MenuSeparator] = []
        for w in list(self.desktop.windows):
            if not getattr(w, "display", True):
                continue
            label = _title(w)
            if w is self.desktop.focused_window:
                label = f"• {label}"
            items.append(
                MenuItem(
                    label=label,
                    handler=(lambda win=w: self._select_window(win)),
                )
            )
        if not items:
            items = [MenuItem(label="(no windows)", enabled=False)]
        items.append(MenuSeparator())
        items.append(MenuItem(command_id="view.tile_h"))
        items.append(MenuItem(command_id="view.tile_v"))
        items.append(MenuItem(command_id="view.cascade"))
        win_menu.items = items

    def _select_window(self, win: Window) -> None:
        """Focus ``win`` from a Windows-menu pick.

        Updates the post-menu restore target so ``_on_menu_active_index_changed``
        keeps focus on the chosen window instead of bouncing back to the
        window that was active when the menu opened.
        """
        if self.desktop is None:
            return
        try:
            self.desktop.focus_window(win)
        except Exception:
            return
        self._pre_menu_window = win
        self._pre_menu_focus = None

    def _panel_cwd(self) -> Path:
        if self.initial_path is not None:
            return self.initial_path if self.initial_path.is_dir() else self.initial_path.parent
        return Path.cwd()

    def _mount_initial_windows(self) -> None:
        assert self.desktop is not None
        cwd = self._panel_cwd()

        if self.launch_mode == "fm":
            self._add_panel_windows(cwd, visible=True)
            return

        if self.launch_mode == "editor":
            file_label = (
                str(self.initial_path) if self.initial_path else "<no file>"
            )
            editor = make_window(
                _StubContent(
                    f"Editor placeholder — {file_label}",
                    title=file_label,
                ),
                title=file_label,
                position=(2, 2),
                size=(60, 18),
                id="editor",
            )
            self.desktop.add_window(editor)
            # Panels mounted but hidden — provide hotkey reveal in later phases.
            self._add_panel_windows(cwd, visible=False)
            return

        if self.launch_mode == "cli":
            agent = make_window(
                _StubContent(
                    "Agent mode — coming soon", title="Agent"
                ),
                title="Agent",
                position=(2, 2),
                size=(60, 18),
                id="agent",
            )
            self.desktop.add_window(agent)
            self._add_panel_windows(cwd, visible=False)
            return

        raise ValueError(f"unknown launch_mode: {self.launch_mode!r}")

    def _add_panel_windows(self, cwd: Path, *, visible: bool) -> None:
        assert self.desktop is not None
        left = make_window(
            FilePanel(cwd=cwd), title=str(cwd), position=(0, 0), size=(40, 12),
            id="panel-left",
        )
        right = make_window(
            FilePanel(cwd=cwd), title=str(cwd), position=(40, 0), size=(40, 12),
            id="panel-right",
        )
        self.desktop.add_window(left)
        self.desktop.add_window(right)
        if not visible:
            self.desktop.hide_window(left)
            self.desktop.hide_window(right)

    def _apply_default_layout(self) -> None:
        assert self.desktop is not None and self.manager is not None
        if self.launch_mode != "fm":
            return
        # Tile the two PanelWindows side by side, filling the full
        # Desktop area. The Desktop already accounts for MenuBar +
        # CommandLine + StatusBar via its margin CSS, so 100% of its
        # height/width is what the panels should occupy.
        W, H = self.desktop.size
        if W <= 0 or H <= 0:
            return
        half = max(3, W // 2)
        for i, win_id in enumerate(("panel-left", "panel-right")):
            try:
                w = self.desktop.query_one(f"#{win_id}", Window)
            except Exception:
                continue
            x = 0 if i == 0 else half
            width = half if i == 0 else (W - half)
            w.styles.offset = Offset(x, 0)
            w.styles.width = max(3, width)
            w.styles.height = max(3, H)

    def _refresh_panels(self) -> None:
        """Load directory contents into both panels (left and right)."""
        from tyui.fm.file_panel import FilePanel  # local: avoid circular at import-time
        for panel_id in ("panel-left", "panel-right"):
            try:
                win = self.desktop.query_one(f"#{panel_id}", Window)
            except Exception:
                continue
            content = win.content
            if isinstance(content, FilePanel):
                content.refresh_listing()
                content.refresh()

    def _set_panel_sort(self, panel_id: str, order: SortOrder) -> None:
        if self.desktop is None:
            return
        try:
            win = self.desktop.query_one(f"#{panel_id}", Window)
        except Exception:
            return
        panel = win.content
        if not isinstance(panel, FilePanel):
            return
        # Re-invoking the same sort from the menu flips direction; selecting a
        # different sort jumps to that order's natural direction. Mirrors the
        # double-click-on-header gesture so both UI paths feel symmetrical.
        if panel.sort_order == order:
            panel.set_sort_order(order, descending=not panel.sort_descending)
        else:
            panel.set_sort_order(order)
        panel.refresh()

    def _focus_panel(self, panel_id: str) -> None:
        from tyui.fm.file_panel import FilePanel
        try:
            win = self.desktop.query_one(f"#{panel_id}", Window)
        except Exception:
            return
        if isinstance(win.content, FilePanel):
            self.desktop.focus_window(win)
            self.set_focus(win.content)

    def _active_panel(self):
        """Return the currently focused FilePanel, or None."""
        node = self.focused
        while node is not None:
            if isinstance(node, FilePanel):
                return node
            node = getattr(node, "parent", None)
        # Fallback: the left panel when nothing else is focused.
        try:
            win = self.desktop.query_one("#panel-left", Window)
            if isinstance(win.content, FilePanel):
                return win.content
        except Exception:
            pass
        return None

    def _opposite_panel(self, active):
        """Given an active FilePanel, return the other panel (if any)."""
        for panel_id in ("panel-left", "panel-right"):
            try:
                win = self.desktop.query_one(f"#{panel_id}", Window)
            except Exception:
                continue
            if isinstance(win.content, FilePanel) and win.content is not active:
                return win.content
        return None

    def _close_modal(self, dialog) -> None:
        """Close the ModalWindow enclosing `dialog`. Restores panel focus.

        Walks specifically up to a ModalWindow (not just any Window) so a
        bubble-up from an inner Input or any DOM weirdness can never end
        up calling remove_window() on a panel.
        """
        win_node = dialog.parent
        while win_node is not None and not isinstance(win_node, ModalWindow):
            win_node = getattr(win_node, "parent", None)
        if win_node is not None and self.desktop is not None:
            self.desktop.remove_window(win_node)
        if self.launch_mode == "fm":
            target = self._pre_modal_panel_id or "panel-left"
            # Don't clear _pre_modal_panel_id here: a single action may chain
            # modals (Confirm -> Progress) and the chained close still needs
            # to know where to send focus. Each action_* re-snaps the id at
            # the start of its run, so stale values are always overwritten.
            self._focus_panel(target)

    def _has_active_modal(self) -> bool:
        """True if any ModalWindow is currently mounted on the Desktop.

        Used to gate panel-switching and F-key actions: while a modal is
        up (Confirm / Input / Progress), Tab / Alt+L / Alt+R and the
        F-key bindings must NOT trigger so focus stays on the dialog and
        Esc / clicks reliably reach it.
        """
        if self.desktop is None:
            return False
        return any(
            isinstance(w, ModalWindow)
            for w in (self.desktop.windows + self.desktop.hidden_windows)
        )

    def _remember_active_panel_id(self) -> None:
        """Snap the active panel's id so _close_modal can route focus back."""
        panel = self._active_panel()
        if panel is None or not panel.is_mounted:
            return
        win = panel.parent
        while win is not None and not isinstance(win, Window):
            win = getattr(win, "parent", None)
        if win is not None and win.id in ("panel-left", "panel-right"):
            self._pre_modal_panel_id = win.id

    # --- placeholder action handlers --------------------------------------

    def action_mkdir(self) -> None:
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        self._remember_active_panel_id()
        cwd = panel.cwd
        dialog = InputDialog(
            prompt=f"Create directory in {cwd}:",
            context=MkdirRequest(parent=cwd),
        )
        show_modal(self.desktop, dialog, title="Mkdir", size=(50, 5))
        self.call_after_refresh(dialog.focus_input)

    def action_new(self) -> None:
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        self._remember_active_panel_id()
        cwd = panel.cwd
        dialog = NewFileDialog(
            prompt=f"New file in {cwd}:",
            context=NewFileRequest(parent=cwd),
        )
        show_modal(self.desktop, dialog, title="New", size=(60, 7))
        self.call_after_refresh(dialog.focus_input)

    def action_save_as(self, editor: EditorContent | None = None) -> None:
        if self._has_active_modal() or self.desktop is None:
            return
        if editor is None:
            win = self.desktop.focused_window
            if win is None or not isinstance(win.content, EditorContent):
                return
            editor = win.content
        self._remember_active_panel_id()
        current = editor._editor.buffer.file_path
        if current:
            initial = current
        else:
            panel = self._active_panel()
            base = panel.cwd if panel is not None else Path.cwd()
            initial = str(base) + "/"
        dialog = NewFileDialog(
            prompt="Save as:",
            context=SaveAsRequest(editor=editor),
            submit_label="Save",
            title="Save As",
            initial=initial,
        )
        show_modal(self.desktop, dialog, title="Save As", size=(72, 7))
        self.call_after_refresh(dialog.focus_input)

    def on_new_file_dialog_submitted(
        self, event: NewFileDialog.Submitted
    ) -> None:
        ctx = event.dialog.context
        self._close_modal(event.dialog)
        if isinstance(ctx, NewFileRequest):
            name = event.value.strip()
            if not name:
                return
            target = ctx.parent / name
            try:
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
            except OSError:
                return
            if target.is_dir():
                return
            self._refresh_panels()
            self._open_editor_window(target, read_only=False)
            return
        if isinstance(ctx, SaveAsRequest):
            raw = event.value.strip()
            if raw:
                target = Path(raw).expanduser()
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    ctx.editor.save_to(str(target))
                except OSError:
                    pass
                else:
                    self._refresh_panels()
            # _close_modal raised the file panel; bring the editor back.
            self._focus_editor_content(ctx.editor)
            return

    def on_new_file_dialog_cancelled(
        self, event: NewFileDialog.Cancelled
    ) -> None:
        ctx = event.dialog.context
        self._close_modal(event.dialog)
        if isinstance(ctx, SaveAsRequest):
            self._focus_editor_content(ctx.editor)

    def _focus_editor_content(self, editor: EditorContent) -> None:
        """Raise the window hosting `editor` and focus its inner widget.

        ``_close_modal`` unconditionally returns focus to a file panel —
        for editor-scoped modals (Save As) we need to undo that and put
        the keyboard back on the editor instead.
        """
        if self.desktop is None:
            return
        win = self._enclosing_window(editor)
        if win is None or win not in self.desktop.windows:
            return
        try:
            self.desktop.focus_window(win)
        except Exception:
            pass
        inner = getattr(editor, "_editor", None)
        try:
            self.set_focus(inner if inner is not None else editor)
        except Exception:
            pass

    def action_copy(self) -> None:
        if self._has_active_modal():
            return
        self._open_copy_move_dialog("copy")

    def action_move(self) -> None:
        if self._has_active_modal():
            return
        self._open_copy_move_dialog("move")

    def _open_copy_move_dialog(self, op: Literal["copy", "move"]) -> None:
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        targets = panel.effective_targets()
        if not targets:
            return
        opposite = self._opposite_panel(panel)
        if opposite is None:
            return
        self._remember_active_panel_id()
        dest = opposite.cwd
        verb = "Copy" if op == "copy" else "Move"
        if len(targets) == 1:
            initial = str(dest / targets[0].name)
            prompt = f"{verb} '{targets[0].name}' to:"
        else:
            initial = str(dest) + "/"
            prompt = f"{verb} {len(targets)} item(s) to:"
        dialog = CopyMoveDialog(
            prompt=prompt,
            initial=initial,
            ok_label=verb,
            title=verb,
            context=CopyMoveRequest(op=op, targets=targets, dest=dest),
        )
        show_modal(self.desktop, dialog, title=verb, size=(72, 9))
        self.call_after_refresh(dialog.focus_input)

    def action_edit(self) -> None:
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        if not (0 <= panel.cursor < len(panel.entries)):
            return
        entry = panel.entries[panel.cursor]
        if entry.is_dir:
            return  # F4 on a dir is a no-op
        self._open_editor_window(entry.path, read_only=False)

    def action_view(self) -> None:
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        if not (0 <= panel.cursor < len(panel.entries)):
            return
        entry = panel.entries[panel.cursor]
        if entry.is_dir:
            return  # F3 on a dir is a no-op
        self._open_editor_window(entry.path, read_only=True)

    # Threshold above which F3 switches to the chunked hex viewer instead of
    # slurping the file into a TextBuffer. 4 MiB is a pragmatic cut-off:
    # below it Textual renders text views without noticeable lag; above it
    # both load time and memory pressure get bad fast.
    _HEX_VIEW_SIZE_THRESHOLD = 4 * 1024 * 1024

    @staticmethod
    def _looks_binary(path: Path) -> bool:
        """Sniff the first 8 KiB for NULs as a cheap binary heuristic."""
        try:
            with open(path, "rb") as fh:
                sample = fh.read(8192)
        except OSError:
            return False
        return b"\x00" in sample

    def _should_use_hex_viewer(self, path: Path) -> bool:
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size > self._HEX_VIEW_SIZE_THRESHOLD:
            return True
        return self._looks_binary(path)

    def _open_editor_window(self, path: Path, *, read_only: bool = False) -> None:
        if self.desktop is None:
            return
        self._remember_active_panel_id()
        # F3 on a large or binary file → hex viewer with chunked mmap reads.
        # Skip the read_text() pre-load entirely so multi-GB files don't hang
        # the UI thread.
        if read_only and self._should_use_hex_viewer(path):
            content = HexViewerContent(path)
            title = f"Hex: {path.name}"
            win_id = "hexviewer"
        else:
            # EditorContent.__init__ does NOT read the file — it only stores
            # file_path on the buffer for later save. We have to load the
            # text ourselves and feed it as initial_text.
            try:
                text = path.read_text()
            except OSError:
                text = ""
            if read_only:
                content = ViewerContent(initial_text=text, file_path=str(path))
                title = f"View: {path.name}"
                win_id = "viewer"
            else:
                content = _FocusableEditorContent(initial_text=text, file_path=str(path))
                title = path.name
                win_id = "editor"
        dw, dh = self.desktop.size.width, self.desktop.size.height
        win = make_window(
            content,
            title=title,
            position=(0, 0),
            size=(dw, dh),
            decorations=Decorations(close_box=True, zoom_box=True, resize_grip=True),
            id=win_id,
        )
        # Born maximized: pre-seed the restore rect so F5 / [↕] toggles back
        # to a sensible windowed size instead of being a no-op.
        win._saved_rect = (Offset(2, 1), Size(max(1, dw - 4), max(1, dh - 2)))
        win.maximized = True
        self.desktop.add_window(win)

    def on_file_panel_item_activated(
        self, event: FilePanel.ItemActivated
    ) -> None:
        # Phase 4: Enter on a file opens the editor. Directories are
        # handled inside FilePanel.activate() (cwd change), so we only
        # see ItemActivated for non-dir entries.
        if event.entry.is_dir:
            return
        self._open_editor_window(event.entry.path)

    def action_close_editor(self) -> None:
        """Esc closes the topmost editor window if one is open.

        Gated by `_has_active_modal()` so Esc still routes to dialog
        cancel handlers when a modal is up. If no editor is open this
        is a silent no-op (panels and other widgets handle their own
        Esc bindings before this app-level fallback runs).
        """
        if self.desktop is None or self._has_active_modal():
            return
        for win in reversed(list(self.desktop.windows)):
            if isinstance(win.content, (EditorContent, HexViewerContent)):
                self.desktop.remove_window(win)
                # on_window_closed isn't fired by remove_window; do the
                # post-close housekeeping inline.
                self._refresh_panels()
                if self.launch_mode == "fm":
                    target = self._pre_modal_panel_id or "panel-left"
                    self._focus_panel(target)
                return

    def on_toggle_maximize(self, event) -> None:
        # Posted by Window when the [↕] zoom box is clicked. Route to manager
        # so the click and the F5 hotkey share the same code path.
        if self.manager is None:
            return
        self.manager.toggle_maximize(event.window)
        event.stop()

    def on_window_closed(self, event) -> None:
        """Editor window closed: refresh panels (file may have been saved),
        restore focus to the panel that opened the editor."""
        win = getattr(event, "window", None)
        if win is None or not isinstance(getattr(win, "content", None), EditorContent):
            return
        # Window framework removes the closed window itself; ensure panels
        # see any new mtime/size.
        self._refresh_panels()
        if self.launch_mode == "fm":
            target = self._pre_modal_panel_id or "panel-left"
            self._focus_panel(target)

    def action_delete(self) -> None:
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        targets = panel.effective_targets()
        if not targets:
            return
        self._remember_active_panel_id()
        prompt = (
            f"Delete {len(targets)} item(s)?"
            if len(targets) > 1
            else f"Delete {targets[0].name}?"
        )
        dialog = ConfirmDialog(
            prompt=prompt,
            context=DeleteRequest(targets=targets),
        )
        show_modal(self.desktop, dialog, title="Delete", size=(56, 9))
        self.call_after_refresh(dialog.focus)

    def action_menu(self) -> None:
        if self.menu_bar is not None:
            # Remember focus so dismiss can route back.
            self._pre_menu_focus = self.focused
            if self.desktop is not None:
                self._pre_menu_window = self.desktop.focused_window
            self.menu_bar.activate(0)
            self.set_focus(self.menu_bar)

    # --- focus & menu routing ---------------------------------------------

    def on_window_focus_changed(self, message: WindowFocusChanged) -> None:
        if self.menu_bar is not None:
            self.menu_bar.refresh_for_focus()
            self._recompute_menu_bar()
        message.stop()

    def on_menu_bar_open_requested(self, message: MenuBar.OpenRequested) -> None:
        # Mouse-click path: action_menu() wasn't called, so capture the
        # pre-menu desktop window here. Widget focus has already moved to
        # MenuBar at this point — only the window-level state is reliable.
        if self._pre_menu_window is None and self.desktop is not None:
            self._pre_menu_window = self.desktop.focused_window
        # Re-snapshot dynamic content (Windows list) right before opening
        # the dropdown so it reflects the current desktop state, including
        # the now-saved pre-menu window.
        self._refresh_windows_menu()
        self._open_dropdown(message.index)
        message.stop()

    def _open_dropdown(self, index: int) -> None:
        if self.menu_bar is None or self.desktop is None:
            return
        if self._active_dropdown is not None:
            self._active_dropdown.remove()
            self._active_dropdown = None
        menu = self.menu_bar.menus[index]
        spans = self.menu_bar._menu_spans()
        start_x = spans[index][1] if index < len(spans) else 0
        dd = Dropdown(
            menu.items,
            position=(start_x, 0),
            palette=self.desktop.palette,
            dispatcher=self.dispatcher,
        )
        self.desktop.mount(dd)
        self._active_dropdown = dd

        def _force_focus() -> None:
            if dd.is_mounted:
                self.set_focus(dd)

        self.call_later(_force_focus)

    def _close_dropdown(self) -> None:
        dd = self._active_dropdown
        if dd is None:
            return
        self._active_dropdown = None
        if dd.is_mounted:
            dd.remove()
        if self.menu_bar is not None:
            self.menu_bar.deactivate()

    def on_dropdown_item_chosen(self, message: Dropdown.ItemChosen) -> None:
        self._close_dropdown()
        message.stop()

    def on_dropdown_dismissed(self, message: Dropdown.Dismissed) -> None:
        # Identity check: a Dropdown removed while cycling between menus posts
        # Dismissed (via on_blur) AFTER the new dropdown is already active.
        # Without this guard the late message would tear down the freshly
        # opened sibling dropdown.
        if message.dropdown is self._active_dropdown:
            self._close_dropdown()
        message.stop()

    def on_dropdown_cycle_requested(self, message: Dropdown.CycleRequested) -> None:
        if self._active_dropdown is not None:
            self._active_dropdown.remove()
            self._active_dropdown = None
        if self.menu_bar is not None and self.menu_bar.menus:
            current = self.menu_bar.active_index or 0
            new_index = (current + message.direction) % len(self.menu_bar.menus)
            self.menu_bar.active_index = new_index
            self._open_dropdown(new_index)
        message.stop()

    def on_key(self, event) -> None:
        # Route navigation keys to the open dropdown when one is up.
        dd = self._active_dropdown
        if dd is not None and dd.is_mounted and (
            dd.has_focus or (self.menu_bar and self.menu_bar.has_focus)
        ):
            k = event.key
            if k == "up":
                dd.move_highlight(-1); event.stop(); return
            if k == "down":
                dd.move_highlight(1); event.stop(); return
            if k == "left":
                dd.post_message(Dropdown.CycleRequested(dd, -1)); event.stop(); return
            if k == "right":
                dd.post_message(Dropdown.CycleRequested(dd, 1)); event.stop(); return
            if k == "enter":
                dd.choose_current(); event.stop(); return
            if k == "escape":
                dd.dismiss(); event.stop(); return
        # Fallthrough: dynamic command routing against focused window.
        if self._has_active_modal():
            return
        if self.router is not None and self.router.handle_key(event.key):
            event.stop()

    def _on_menu_active_index_changed(self, new) -> None:
        # Fired on every active_index reactive change. We only care about
        # the closing transition (None means dismissed/no item highlighted).
        if new is not None:
            return
        target = self._pre_menu_focus
        win_target = self._pre_menu_window
        self._pre_menu_focus = None
        self._pre_menu_window = None

        # If the chosen menu item opened a modal (mkdir / new / copy / move
        # / delete confirm / find), the modal is already on top of the stack
        # and its action handler scheduled focus_input(). Restoring the
        # pre-menu window here would raise the panel above the modal and
        # steal keyboard focus from the dialog — bail out instead.
        if self._has_active_modal():
            return

        # Pick the window to raise. Prefer the one enclosing the saved
        # widget focus (F9 path); fall back to the captured pre-menu window
        # (mouse-click path).
        win: Window | None = None
        if target is not None:
            win = self._enclosing_window(target)
        if win is None:
            win = win_target

        if (
            win is not None
            and self.desktop is not None
            and win in self.desktop.windows
        ):
            try:
                self.desktop.focus_window(win)
            except Exception:
                pass

        if target is not None:
            try:
                self.set_focus(target)
                return
            except Exception:
                pass

        # No saved widget focus (mouse-click open). Try to land focus on
        # the window's content/inner editor so it remains keyboard-active.
        if win is not None:
            content = getattr(win, "content", None)
            inner = getattr(content, "_editor", None)
            try:
                self.set_focus(inner if inner is not None else content)
                return
            except Exception:
                pass

        if self.launch_mode == "fm":
            self._focus_panel("panel-left")

    def _enclosing_window(self, widget) -> Window | None:
        node = widget
        while node is not None:
            if isinstance(node, Window):
                return node
            node = getattr(node, "parent", None)
        return None

    def action_focus_other_panel(self) -> None:
        if self._has_active_modal():
            return
        focused = self.focused
        if self._focused_inside_search_panel(focused):
            try:
                self.screen.focus_next()
            except Exception:
                pass
            return
        target: str | None = None
        node = focused
        while node is not None:
            nid = getattr(node, "id", None)
            if nid == "panel-right":
                target = "panel-left"
                break
            if nid == "panel-left":
                target = "panel-right"
                break
            node = getattr(node, "parent", None)
        if target is None:
            # Tab pressed outside a file panel (e.g. inside the editor).
            # The app-level priority binding consumed the key before the
            # focused widget could see it — forward to its own insert_tab
            # action so editors keep their tab behaviour.
            insert = getattr(focused, "action_insert_tab", None) if focused is not None else None
            if callable(insert):
                try:
                    insert()
                except Exception:
                    pass
            return
        self._focus_panel(target)

    def action_cycle_window(self) -> None:
        """Shift+Tab: focus the next visible desktop window in cycle order."""
        if self._has_active_modal() or self.desktop is None:
            return
        if self._focused_inside_search_panel(self.focused):
            try:
                self.screen.focus_previous()
            except Exception:
                pass
            return
        self.desktop.cycle_focus(+1)

    @staticmethod
    def _focused_inside_search_panel(focused) -> bool:
        from tyui.windowing.editor.search_panel import SearchPanel
        node = focused
        while node is not None:
            if isinstance(node, SearchPanel):
                return True
            node = getattr(node, "parent", None)
        return False

    def action_focus_left_panel(self) -> None:
        if self._has_active_modal():
            return
        self._focus_panel("panel-left")

    def action_focus_right_panel(self) -> None:
        if self._has_active_modal():
            return
        self._focus_panel("panel-right")
