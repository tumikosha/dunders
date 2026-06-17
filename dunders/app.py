"""dunders — Norton Commander/mc-style terminal shell on top of dunders.windowing.

Phase 1: skeleton only. Composes MenuBar + Desktop + CommandLine + StatusBar
and mounts an initial set of windows depending on the launch mode:

    fm     -> two FilePanel windows tiled in the upper area (default)
    editor -> a placeholder editor window maximized; panels mounted hidden
    cli    -> a placeholder agent window maximized; panels mounted hidden

Later phases will: wire commands, file ops, real editor/agent content, etc.
"""

from __future__ import annotations

import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from rich.segment import Segment
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.geometry import Offset, Size
from textual.strip import Strip

from dunders.core.plugins import EventBus, PluginApi, load_plugins
from dunders.core.vfs import VfsPath
from dunders.fm.actions import (
    OpResult,
    chmod_paths,
    mkdir_at,
    pack_paths,
)
from dunders.fm.vfs_engine import transfer
from dunders.fm.vfs_local import default_registry
from dunders.fm.commandline import CommandLine
from dunders.fm.console.backends import make_backend
import dunders.fm.console.backends.subprocess_be as _subprocess_be  # noqa: F401 — registers "subprocess"
import dunders.fm.console.backends.pty_be  # noqa: F401 — registers "pty" on POSIX
from dunders.fm.console.history import History
from dunders.fm.console.registry import ConsoleRegistry
from dunders.fm.console.handover import TerminalHandover, make_handover
from dunders.fm.console.runner import CommandRunner
from dunders.fm.console.window import ConsoleContent
from dunders.config.bookmarks import add_bookmark, list_bookmarks, remove_bookmark
from dunders.fm.dialogs import (
    AddBookmarkDialog,
    BookmarksDialog,
    ChangeAttributesDialog,
    ConfirmDialog,
    CopyMoveDialog,
    FindFileDialog,
    InputDialog,
    NewFileDialog,
    ProgressDialog,
)
from dunders.fm.file_panel import FilePanel
from dunders.fm.find_file import FindOptions, walk as find_walk
from dunders.fm.find_results import SearchResultsContent
from dunders.fm.keymap import DEFAULT_FKEY_LABELS, EDITOR_FKEY_LABELS
from dunders.fm.user_menu import MacroContext, collect_prompts, expand_macros
from dunders.fm.user_menu_loader import load_menu, seed_global_menu
from dunders.fm.user_menu_dialog import UserMenuDialog
from dunders.config import user_config
from dunders.fm.panel_view import PanelViewMode
from dunders.fm.sort import SortOrder
from dunders.windowing import (
    BorderStyle,
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
    list_themes,
    resolve_theme_path,
    theme_registry,
    make_window,
    show_command_palette,
    show_modal,
)
from dunders.windowing.tree import JsonYamlTreeContent
from dunders.windowing.content import WindowContent
from dunders.windowing.core import clipboard
from dunders.windowing.editor.language_picker import show_language_picker
from dunders.fm.csv_viewer import CsvViewerContent
from dunders.fm.hex_viewer import HexViewerContent, HexViewerWidget
from dunders.fm.key_probe import KeyProbeContent
from dunders.fm.viewer import ViewerContent
from dunders.fm.image_viewer import ImageViewerContent, PILLOW_AVAILABLE, sniff_image
from dunders.windowing.editor import EditorContent


def _theme_label(name: str) -> str:
    """Turn a theme id like ``midnight_commander`` into ``Midnight Commander``."""
    return name.replace("_", " ").replace("-", " ").title()


class _FocusableEditorContent(EditorContent):
    """EditorContent variant that focuses the inner editor on mount.

    The base EditorContent is a non-focusable wrapper; calling .focus()
    on it lands on the wrapper which doesn't accept keys. The actual
    focusable widget is `_editor`. Auto-focusing here removes the
    "click in the window before keys work" gotcha after F4.
    """

    def on_mount(self) -> None:
        self._editor.focus()

    def focus(self, scroll_visible: bool = True):
        # Forward focus to the inner editor widget so click/tab focus
        # via Desktop.focus_window lands on the actual key target.
        try:
            self._editor.focus()
        except Exception:
            return super().focus(scroll_visible)
        return self

    def get_commands(self):
        commands = super().get_commands()
        app = getattr(self, "app", None)
        handler = getattr(app, "action_project_view", None)
        if callable(handler):
            commands.append(
                WindowCommand(
                    id="project_view", label="Project View",
                    handler=handler, hotkey="f1",
                )
            )
        um = getattr(app, "action_user_menu", None)
        if callable(um):
            commands.append(
                WindowCommand(
                    id="user_menu", label="User menu",
                    handler=um, hotkey="f2",
                )
            )
        return commands


from dunders.windowing.helpers import ModalWindow  # noqa: E402


# --- Dialog payload types --------------------------------------------------
# Each F-key flow attaches a typed request to its dialog so
# on_confirm_dialog_result / on_input_dialog_submitted can dispatch via
# isinstance instead of stringly-typed `_op` attributes.


@dataclass(frozen=True)
class CopyMoveRequest:
    op: Literal["copy", "move"]
    targets: list[VfsPath]  # scheme-agnostic so archive members can be extracted
    dest: Path
    base: Path  # source panel cwd — arcname root when the dest is a "zip:" target
    dest_loc: VfsPath  # opposite panel location; non-file -> write INTO an archive


@dataclass(frozen=True)
class DeleteRequest:
    targets: list[VfsPath]  # scheme-agnostic: local files or archive members


@dataclass(frozen=True)
class PackRequest:
    """Create a new .zip from a local selection (F-menu "Create archive…")."""

    targets: list[Path]
    base: Path  # arcnames are relative to this (the source panel cwd)


@dataclass(frozen=True)
class OpenDunderRequest:
    """Open a dunder (provider) in the active panel from the "_" menu: prompt
    for a spec, then resolve_target it and navigate the panel into the result."""

    scheme: str


@dataclass(frozen=True)
class DunderPasswordRequest:
    """A masked password prompt chained after OpenDunderRequest when the
    provider needs credentials (e.g. FTP user without an inline password)."""

    scheme: str
    spec: str


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
class ChangeAttributesRequest:
    targets: list[Path]


@dataclass(frozen=True)
class HexSearchRequest:
    """Routes the InputDialog used by F3 hex viewer back to its widget."""

    widget: HexViewerWidget


@dataclass(frozen=True)
class FindFileRequest:
    """Settings for one Find-file run, attached to the dialog context."""

    options: FindOptions
    start_dir: Path


@dataclass(frozen=True)
class OpenFileRequest:
    """Marker context: the InputDialog value is a path to open in the editor."""


@dataclass(frozen=True)
class CancelSearchRequest:
    """Routes the Yes/No interrupt confirmation back to the SearchResultsContent."""

    content: SearchResultsContent


@dataclass(frozen=True)
class QuitRequest:
    """Marker context: the Yes/No confirmation guarding F10 quit."""


@dataclass(frozen=True)
class UserMenuPromptRequest:
    label: str


@dataclass(frozen=True)
class AddBookmarkRequest:
    loc: VfsPath  # the cwd_loc being bookmarked


LaunchMode = Literal["fm", "editor", "cli", "we", "we-mc"]
TerminalMode = Literal["relay", "suspend"]

# Files with one of these extensions are treated as runnable scripts even when
# they lack the executable bit and a shebang; the value is the interpreter we
# prefix the path with. Mirrors the mc "extension file" convention loosely.
_SCRIPT_INTERPRETERS: dict[str, str] = {
    ".sh": "sh",
    ".bash": "bash",
    ".zsh": "zsh",
    ".py": "python3",
    ".pl": "perl",
    ".rb": "ruby",
    ".js": "node",
}

# `we`-mode cascade geometry: each successive editor window is shifted by
# (_WE_CASCADE_DX, _WE_CASCADE_DY); all windows share one shrunk size so the
# last file's bottom-right corner pins to the desktop corner.
_WE_CASCADE_DX = 2
_WE_CASCADE_DY = 1
_WE_MIN_W = 20
_WE_MIN_H = 6


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


class DundersApp(App):
    """Top-level app shell for the NC-style dunders."""

    TITLE = "dunders"

    # Disable Textual's built-in command palette: it binds ctrl+p as a
    # priority binding that pre-empts our CommandRouter, and it only lists
    # generic Textual commands (no app providers registered). The app's own
    # CommandPaletteContent (palette.open, Ctrl+K) is the real palette.
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen { background: $panel; }
    Desktop { margin-top: 1; }
    Vertical#bottom-stack { dock: bottom; height: auto; }
    #bottom-stack > CommandLine { height: auto; }
    #bottom-stack > StatusBar { height: 1; }
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
        Binding("alt+c", "focus_command_line", "Command line", show=False, priority=True),
        Binding("ctrl+n", "insert_current_file", "Insert file name/path", show=False),
        # IconTray restore: Ctrl+W is a chord prefix; the next 1..9 keypress
        # restores the corresponding tray icon. Priority so it fires even
        # when an editor / Input has focus. Ctrl-digit alone doesn't work
        # in macOS Terminal.app / iTerm2 (no escape sequence emitted), and
        # Alt-digit needs "Esc as Meta" to be enabled in the terminal —
        # the chord-style works everywhere, mirrors tmux/screen prefix
        # idiom.
        Binding("ctrl+w", "tray_chord_start", show=False, priority=True),
        # The digit half of the chord. These are priority bindings so they
        # fire BEFORE the focused widget (e.g. an editor in `we`-mode) can
        # swallow the digit. check_action() keeps them disabled until a
        # Ctrl+W set _tray_chord_pending, so normal digit typing passes
        # straight through to the editor.
        *[
            Binding(str(d), f"tray_restore_digit({d})", show=False, priority=True)
            for d in range(1, 10)
        ],
    ]

    def __init__(
        self,
        *,
        launch_mode: LaunchMode = "fm",
        initial_path: str | Path | None = None,
        initial_paths: list[str | Path] | None = None,
        terminal_mode: TerminalMode = "relay",
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
        self.initial_paths: list[Path] = [
            Path(p).expanduser() for p in (initial_paths or [])
        ]
        self.terminal_mode: TerminalMode = terminal_mode
        # VFS provider registry for file operations routed through transfer().
        self._vfs_registry = default_registry()
        # Plugin runtime: an event bus + the narrow PluginApi handed to each
        # discovered dunder. Plugins register before panels are built (below in
        # _add_panel_windows), so a plugin-added provider is live from startup.
        # Set DUNDERS_NO_PLUGINS=1 to skip discovery (CI / bisecting).
        self.events = EventBus()
        self.plugin_api = PluginApi(vfs=self._vfs_registry, events=self.events)
        self.loaded_plugins: list[str] = []
        if not os.environ.get("DUNDERS_NO_PLUGINS"):
            self.loaded_plugins = load_plugins(self.plugin_api)
        # The active terminal-handover strategy in we-mc mode (None otherwise).
        self._handover = None
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
        # Tracks the last panel window that held desktop focus.  Used so
        # F-key commands and _active_panel() still resolve correctly when
        # Textual widget focus is on the CommandLine input rather than a
        # panel content widget.
        self._last_focused_panel_window: Window | None = None
        self._active_dropdown: Dropdown | None = None
        self.console_registry: ConsoleRegistry[ConsoleContent] = ConsoleRegistry(
            factory=self._create_console_content,
        )
        self.command_history: History | None = None
        self.command_runner: CommandRunner | None = None
        self._pre_console_focus = None
        self._console_default_window = None
        self._user_menu_ctx: MacroContext | None = None
        self._user_menu_pending: dict | None = None
        self._editor_seq = 0
        # Project View (F1): id of the panel currently acting as the 1/4 tree,
        # or None when Project View is not active. Drives resize relayout and
        # the editor-side entry point; cleared by _toggle_panel (the exit path).
        self._project_tree_panel_id: str | None = None
        # The tree panel's view mode before Project View narrowed it to SHORT,
        # restored when Project View exits. None while not in Project View.
        self._project_prev_view_mode: PanelViewMode | None = None
        # Ids of the editor windows created by the `we`-mode cascade, so the
        # deferred geometry callback only ever resizes THIS cascade's windows
        # (never a later F4-opened editor, never panels).
        self._cascade_ids: list[str] = []
        # True for one keypress after Ctrl+W: the next digit (1-9) restores
        # that tray icon. Cleared on any keypress (digit or otherwise).
        self._tray_chord_pending: bool = False
        # Full menu list, including focus-scoped menus like "Editor" that
        # are only mounted on `menu_bar.menus` while a relevant window is
        # focused.
        self._all_menus: list[Menu] = []
        # Available theme names (built-in + user + examples) and the index of
        # the currently active one, so "Cycle theme" advances predictably.
        self._theme_names: list[str] = []
        self._theme_index: int = 0
        # Set by _resolve_initial_theme when the persisted theme fails to load
        # and we fall back to modern_dark; surfaced as a notify once mounted so
        # the fallback is never silent.
        self._theme_load_warning: str | None = None

    def compose(self) -> ComposeResult:
        self.menu_bar = MenuBar()
        self.desktop = Desktop(theme_name=self._resolve_initial_theme())
        hist_path = Path.home() / ".config" / "dunders" / "history"
        self.command_history = History(hist_path, cap=1000)
        self.command_line = CommandLine(id="cmdline", history=self.command_history)
        # While file panels are visible, up/down at the cmdline buffer edge
        # drive the active panel instead of command history (NC/Far style).
        self.command_line.set_panel_nav(self._cmdline_panel_nav)
        label, tip = self._run_mode_chip()
        self.command_line.set_run_mode(label, tooltip=tip)
        self.status_bar = StatusBar(items=self._panel_status_items())
        yield self.menu_bar
        yield self.desktop
        # Wrap the bottom strip in a Vertical so cmdline and statusbar
        # stack as proper 2 separate rows (multiple `dock: bottom` widgets
        # at the same edge collapse into one row in Textual).
        with Vertical(id="bottom-stack"):
            yield self.command_line
            yield self.status_bar

    def on_mount(self) -> None:
        assert self.desktop is not None and self.menu_bar is not None
        self.manager = WindowManager(self.desktop)
        # Re-tile panels / refill cascade editors whenever the desktop is
        # resized. Hooked on the Desktop (not App.on_resize) because there
        # Desktop.size already reflects the new terminal size.
        self.desktop.on_resized = self._relayout_after_resize
        self.dispatcher = CommandDispatcher(self.desktop, self.command_registry)
        self.router = CommandRouter(self.dispatcher)
        self.command_runner = CommandRunner(
            registry=self.console_registry,
            backend=make_backend("subprocess"),
            panel_cwd_getter=self._panel_cwd_for_test,
            panel_cd=self._panel_cd,
            on_busy_changed=self._on_console_busy_changed,
        )

        # Replace the runner's default no-op backend-request hook so :backend
        # switches actually swap the runner's backend.
        def _on_backend_request(name: str) -> None:
            if self.command_runner is None:
                return
            target = self.console_registry.get_or_create(None)
            try:
                self.command_runner.set_backend(make_backend(name))
                target.append(f"[backend switched to {name}]\n".encode())
            except Exception as e:
                target.append(f"[backend {name} unavailable: {e}]\n".encode())

        self.command_runner._on_backend_request = _on_backend_request  # type: ignore[assignment]

        self._register_app_commands()
        self.menu_bar.bind_dispatcher(self.dispatcher)
        self._build_menus()
        self._mount_initial_windows()
        self._refresh_panels()
        if self._is_panel_mode():
            # Start with only the panels + command line visible; the console
            # is mounted lazily on the first command (or via Toggle console),
            # so the bottom half stays clear until the user needs it.
            self._focus_panel("panel-left")
            # Default focus rule: with a file argument the user is in
            # "open this thing" mode and the cmdline is more useful as the
            # initial focus; without one, they're browsing — keep keyboard
            # focus on the active panel so arrow keys move the selection.
            if self.initial_path is not None and self.initial_path.is_file():
                self.call_after_refresh(self._focus_command_line)
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
        # A persisted theme that failed to load fell back to modern_dark during
        # compose(); now that the app is mounted, tell the user (notify needs a
        # running app, so it can't fire from _resolve_initial_theme).
        if self._theme_load_warning is not None:
            self.notify(
                self._theme_load_warning,
                title="Ошибка темы",
                severity="warning",
                timeout=8.0,
            )
            self._theme_load_warning = None

    def on_unmount(self) -> None:
        # Tear down the we-mc terminal handover so its persistent PTY shell
        # (relay mode) doesn't leak when the app exits.
        if self._handover is not None:
            try:
                self._handover.shutdown()
            except Exception:
                pass

    def on_confirm_dialog_result(self, event: ConfirmDialog.Result) -> None:
        ctx = event.dialog.context
        self._close_modal(event.dialog)
        if not event.confirmed:
            return
        if isinstance(ctx, DeleteRequest):
            self._run_delete(ctx)
        elif isinstance(ctx, CancelSearchRequest):
            ctx.content.cancel_event.set()
        elif isinstance(ctx, QuitRequest):
            self.exit()

    def on_copy_move_dialog_submitted(
        self, event: CopyMoveDialog.Submitted
    ) -> None:
        ctx = event.dialog.context
        self._close_modal(event.dialog)
        if not isinstance(ctx, CopyMoveRequest):
            return
        raw = (event.value or "").strip()
        if ctx.dest_loc.scheme != "file":
            # Copy/move INTO a browsed archive at its current sub-path; the
            # typed value is ignored (append-only, no path editing). Checked
            # before the provider-prefix path since the prefill is a "<scheme>://"
            # URI which would otherwise match the prefix dispatch.
            self._run_copy_into_archive(ctx)
            return
        # Provider prefix: "<scheme>:<spec>" (e.g. zip:backup.zip, ftp:host/path)
        # hands <spec> to that provider, which creates the target archive/
        # connection; we copy the selection in and open it in the dest panel.
        target = self._resolve_prefixed_target(raw, base=ctx.dest_loc)
        if target is not None:
            dest_panel = self._opposite_panel(self._active_panel())
            self._run_copy_into_target(ctx, target, open_in=dest_panel)
            return
        user_dest = Path(raw).expanduser() if raw else ctx.dest
        self._run_copy_move(ctx, user_dest)

    def _resolve_prefixed_target(self, raw: str, *, base: VfsPath) -> VfsPath | None:
        """Map a typed ``<scheme>:<spec>`` destination to a write-target locator
        via the matching provider's ``resolve_target`` (its declared prefix).

        Returns None when there is no ``<scheme>:`` prefix for a registered
        provider that implements the optional resolver — the caller then treats
        the value as an ordinary local path.
        """
        scheme, sep, spec = raw.partition(":")
        if not sep or scheme not in self._vfs_registry.schemes():
            return None
        provider = self._vfs_registry.for_scheme(scheme)
        resolver = getattr(provider, "resolve_target", None)
        if resolver is None:
            return None
        try:
            return resolver(spec.strip(), base=base)
        except Exception:
            return None

    def _status_marshaller(self, progress: ProgressDialog):
        """Wrap a worker-thread copy callback so it can't starve the UI.

        Byte-granular copy fires a status per 1 MiB chunk — thousands per
        second on a fast disk. Marshalling every one to the UI thread pegs the
        event loop so it never services the Cancel click. We throttle to
        ~12 fps, but always let through the *first sight of a new file* (so the
        name shows before its bytes start flowing) and the final 100 %.
        """
        last_t = [0.0]
        last_label = [None]

        def _on_status(status) -> None:
            now = time.monotonic()
            new_file = status.label != last_label[0]
            done = status.done >= status.total
            if new_file or done or (now - last_t[0]) >= 0.08:
                last_t[0] = now
                last_label[0] = status.label
                self.call_from_thread(progress.set_copy_status, status)

        return _on_status

    def _run_copy_into_archive(self, ctx: CopyMoveRequest) -> None:
        """Copy/move the selection into the browsed archive (ctx.dest_loc)."""
        self._run_copy_into_target(ctx, ctx.dest_loc)

    def _run_copy_into_target(
        self, ctx: CopyMoveRequest, target: VfsPath, *, open_in=None
    ) -> None:
        """Copy/move the selection into ``target`` via the transfer engine.

        ``open_in`` (a FilePanel) is navigated into ``target`` once the op
        succeeds — so creating ``zip:foo`` lands you inside the new archive.
        """
        if self.desktop is None:
            return
        op_label = "Copying" if ctx.op == "copy" else "Moving"
        progress = ProgressDialog(title=op_label, total=len(ctx.targets))
        show_modal(self.desktop, progress, title=op_label, size=(64, 9))
        self.call_after_refresh(progress.focus)

        _on_status = self._status_marshaller(progress)

        def _worker() -> None:
            def _on_progress(i: int, n: int) -> None:
                self.call_from_thread(progress.set_progress, i, n)

            result = transfer(
                self._vfs_registry,
                ctx.targets,
                target,
                mode=ctx.op,
                on_progress=_on_progress,
                on_status=_on_status,
                cancel_event=progress.cancel_event,
            )
            self.call_from_thread(
                self._finish_into_target, ctx.op, progress, result, target, open_in
            )

        self.run_worker(_worker, thread=True, exclusive=False, group="fileop")

    def _finish_into_target(self, op, progress, result, target, open_in) -> None:
        self._finish_op(op, progress, result)
        if open_in is not None and not result.errors and not result.cancelled:
            open_in._change_cwd_loc(target)

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
        show_modal(self.desktop, progress, title=op_label, size=(64, 9))
        self.call_after_refresh(progress.focus)

        _on_status = self._status_marshaller(progress)

        def _worker() -> None:
            def _on_progress(i: int, n: int) -> None:
                self.call_from_thread(progress.set_progress, i, n)

            result = transfer(
                self._vfs_registry,
                req.targets,  # already VfsPath (may be zip-scheme for extraction)
                VfsPath.local(dest_dir),
                mode=req.op,
                rename_to=rename_to,
                on_progress=_on_progress,
                on_status=_on_status,
                cancel_event=progress.cancel_event,
            )
            self.call_from_thread(self._finish_op, req.op, progress, result)

        self.run_worker(_worker, thread=True, exclusive=False, group="fileop")

    def _run_delete(self, req: DeleteRequest) -> None:
        if self.desktop is None:
            return
        progress = ProgressDialog(title="Deleting", total=len(req.targets))
        show_modal(self.desktop, progress, title="Delete", size=(64, 9))
        self.call_after_refresh(progress.focus)

        targets = req.targets
        provider = self._vfs_registry.resolve(targets[0]) if targets else None

        def _worker() -> None:
            def _on_progress(i: int, n: int) -> None:
                self.call_from_thread(progress.set_progress, i, n)

            if provider is None:
                result = OpResult()
            else:
                # LocalProvider.delete wraps delete_paths; archive providers
                # repack / `7z d`. Same contract, so one path serves both.
                result = provider.delete(
                    targets,
                    on_progress=_on_progress,
                    cancel_event=progress.cancel_event,
                )
            self.call_from_thread(self._finish_op, "delete", progress, result)

        self.run_worker(_worker, thread=True, exclusive=False, group="fileop")

    def _run_pack(self, targets: list[Path], dest_zip: Path, base: Path) -> None:
        if self.desktop is None:
            return
        progress = ProgressDialog(title="Packing", total=len(targets))
        show_modal(self.desktop, progress, title="Create archive", size=(64, 9))
        self.call_after_refresh(progress.focus)

        def _worker() -> None:
            def _on_progress(i: int, n: int) -> None:
                self.call_from_thread(progress.set_progress, i, n)

            result = pack_paths(
                targets,
                dest_zip,
                base=base,
                on_progress=_on_progress,
                cancel_event=progress.cancel_event,
            )
            self.call_from_thread(self._finish_op, "pack", progress, result)

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
        # Notify plugins (e.g. __office__ playing a sound on a successful copy).
        self.events.emit(f"op.{op}.done", result)

    def on_input_dialog_submitted(self, event: InputDialog.Submitted) -> None:
        ctx = event.dialog.context
        if isinstance(ctx, UserMenuPromptRequest):
            self._close_modal(event.dialog)
            if self._user_menu_pending is not None:
                self._user_menu_pending["values"][ctx.label] = event.value
                self._advance_user_menu_prompts()
            return
        if isinstance(ctx, HexSearchRequest) and event.value:
            # Close modal first so the viewer is back on top before we scroll
            # — otherwise the post-search refresh paints behind the dialog.
            self._close_modal(event.dialog)
            ctx.widget.search(event.value)
            ctx.widget.focus()
            return
        if isinstance(ctx, OpenFileRequest):
            self._close_modal(event.dialog)
            raw = event.value.strip()
            if raw:
                target = Path(raw).expanduser()
                if not target.is_dir():
                    self._open_editor_window(target, read_only=False)
            return
        if isinstance(ctx, DunderPasswordRequest):
            self._close_modal(event.dialog)
            self._do_open_dunder(ctx.scheme, ctx.spec, password=event.value)
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
        if isinstance(event.dialog.context, UserMenuPromptRequest):
            self._user_menu_pending = None
        self._close_modal(event.dialog)

    def _report_op_result(self, op_name: str, result: OpResult) -> None:
        """Surface OpResult.errors to the default console window."""
        if not result.errors:
            return
        if self.command_runner is None:
            return
        target = self.console_registry.get_or_create(None)
        for err in result.errors:
            target.append(f"{op_name}: {err}\n".encode())

    def _relayout_after_resize(self) -> None:
        """Re-tile panels and refill cascade editors after a terminal resize.

        Driven by ``Desktop.on_resize`` (via ``Desktop.on_resized``) rather
        than the App's own resize event, because at that point ``Desktop.size``
        already reflects the new terminal size. App.on_resize fires too early —
        ``Desktop.size`` still lags — so panels would re-tile to the previous
        half-width (notably in we/editor/cli, where no later layout pass
        corrects it).

        Panels are tiled to halves in every mode. In editor/cli/we they may be
        hidden, in which case this is a harmless no-op until they're revealed.
        Without re-tiling, a resize leaves panels at stale geometry: the
        Desktop's clamp only shrinks (never grows) and, on shrink, slides an
        out-of-bounds panel inward — so the two panels overlap on shrink and
        don't grow on enlarge.
        """
        if self.desktop is None or self.manager is None:
            return
        if self._project_tree_panel_id is not None:
            self._relayout_project_view()
            return
        self._tile_panels()
        # we-mode cascade editor windows must keep filling the desktop too.
        if self._cascade_ids:
            self._apply_cascade_geometry()

    def _relayout_project_view(self) -> None:
        """Re-apply the 1/4 tree + 3/4 editor split after a terminal resize."""
        tree_id = self._project_tree_panel_id
        if tree_id is None:
            return
        try:
            tree_win = self.desktop.query_one(f"#{tree_id}", Window)
        except Exception:
            return
        editors = [
            w for w in self.desktop.windows
            if isinstance(w.content, EditorContent)
        ]
        if tree_win not in self.desktop.windows or not editors:
            return
        # Project View keeps exactly one visible editor; others are minimized.
        self._layout_project_view(tree_win=tree_win, editor_win=editors[-1])

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
            WindowCommand(id="app.quit", label="Quit", handler=self.action_quit),
            WindowCommand(id="app.open_file", label="Open File...", handler=self.action_open_file),
            WindowCommand(id="view.tile_h", label="Tile horizontal", handler=lambda: m.tile_horizontal()),
            WindowCommand(id="view.tile_v", label="Tile vertical", handler=lambda: m.tile_vertical(), hotkey="ctrl+u"),
            WindowCommand(id="view.cascade", label="Cascade", handler=lambda: m.cascade()),
            # Terminal-independent alias for the alt+c command-line focus
            # binding: macOS Terminal/iTerm send Option+C as the literal
            # character "ç" (unless "Use Option as Meta" is on), so alt+c
            # never reaches the app there. Ctrl+E always does. alt+c stays in
            # BINDINGS for terminals that do forward Meta.
            WindowCommand(
                id="app.focus_command_line",
                label="Command line",
                handler=self._focus_command_line,
                hotkey="ctrl+e",
            ),
            WindowCommand(id="window.hide", label="Hide", handler=lambda: m.hide_focused()),
            WindowCommand(id="window.maximize", label="Maximize", handler=lambda: m.maximize_focused(), hotkey="f5"),
            WindowCommand(id="panel.left.toggle", label="Toggle Left Panel", handler=lambda: self._toggle_panel("panel-left"), hotkey="ctrl+1"),
            WindowCommand(id="panel.right.toggle", label="Toggle Right Panel", handler=lambda: self._toggle_panel("panel-right"), hotkey="ctrl+2"),
            WindowCommand(id="palette.open", label="Command Palette", handler=self.action_open_palette, hotkey="ctrl+k"),
            WindowCommand(id="bookmark.add", label="Add bookmark…", handler=self.action_add_bookmark, hotkey="ctrl+d"),
            WindowCommand(id="bookmark.open", label="Bookmarks…", handler=self.action_open_bookmarks, hotkey="ctrl+b"),
            WindowCommand(id="panels.fullscreen", label="Panels Fullscreen", handler=self.action_panels_fullscreen, hotkey="ctrl+p"),
            WindowCommand(
                id="console.toggle",
                label="Toggle console",
                handler=self.action_toggle_console,
                hotkey="ctrl+o",
            ),
            # Diagnostic: open the Key Probe window. No hotkey on purpose — the
            # whole point is to diagnose keys (Ctrl+digit / Alt+letter) that do
            # NOT reach the app, so it's reached via the Help menu / palette.
            WindowCommand(
                id="help.key_probe",
                label="Key Probe",
                handler=self.action_key_probe,
            ),
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
        for side, panel_id in (("left", "panel-left"), ("right", "panel-right")):
            for mode, label in (
                (PanelViewMode.BRIEF, "Brief"),
                (PanelViewMode.MEDIUM, "Medium"),
                (PanelViewMode.SHORT, "Short"),
                (PanelViewMode.FULL, "Full"),
                (PanelViewMode.DETAILED, "Detailed"),
                (PanelViewMode.DESCRIPTION, "Description"),
            ):
                cmds.append(WindowCommand(
                    id=f"panel.{side}.view_{mode.value}",
                    label=f"View: {label} ({side})",
                    handler=(lambda pid=panel_id, m=mode: self._set_panel_view_mode(pid, m)),
                ))
        # Theme commands: one "Cycle theme" plus a direct-select command per
        # available theme. Names come from list_themes() (built-in + user +
        # examples), kept in self._theme_names so Options menu mirrors them.
        self._theme_names = list_themes()
        if self.desktop is not None:
            current = self.desktop.palette.theme.name
            if current in self._theme_names:
                self._theme_index = self._theme_names.index(current)
        cmds.append(WindowCommand(
            id="theme.cycle",
            label="Cycle theme",
            handler=self.action_cycle_theme,
            hotkey="ctrl+t",
        ))
        cmds.append(WindowCommand(
            id="theme.edit",
            label="Edit theme",
            handler=self.action_edit_theme,
        ))
        for name in self._theme_names:
            cmds.append(WindowCommand(
                id=f"theme.set.{name}",
                label=f"Theme: {_theme_label(name)}",
                handler=(lambda n=name: self._apply_theme(n, persist=True)),
            ))
        # Dunders menu ("_"): one open-command per registered provider that
        # declares a display name and a resolve_target (archive/connection it
        # can open or create in the active panel).
        for scheme, label in self._openable_dunders():
            cmds.append(WindowCommand(
                id=f"dunder.open.{scheme}",
                label=label,
                handler=(lambda s=scheme: self._open_dunder(s)),
            ))
        cmds.append(WindowCommand(
            id="dunder.open.tree",
            label="Tree (JSON/YAML)",
            handler=self._open_tree_dunder,
        ))
        cmds.extend(self._bookmark_commands())
        self.command_registry.register_many(cmds)

    def _openable_dunders(self) -> list[tuple[str, str]]:
        """(scheme, display_name) for providers that can be opened from "_",
        i.e. those declaring both a display name and a resolve_target."""
        out: list[tuple[str, str]] = []
        # "file" (Local files) first — the quick way home/back to the local FS —
        # then the rest alphabetically.
        for scheme in sorted(self._vfs_registry.schemes(), key=lambda s: (s != "file", s)):
            provider = self._vfs_registry.for_scheme(scheme)
            label = getattr(provider, "display_name", None)
            if label and hasattr(provider, "resolve_target"):
                out.append((scheme, label))
        return out

    def action_open_palette(self) -> None:
        if self.dispatcher is None or self.desktop is None:
            return
        show_command_palette(self.desktop, self.dispatcher)

    def action_panels_fullscreen(self) -> None:
        """Ctrl+P (global): bring the two file panels back full-screen.

        Minimizes any open editor/viewer/console windows to the IconTray
        (preserving their state, restorable via Ctrl+W; the default console
        also re-surfaces on the next command or Ctrl+O), exits Project View,
        reveals both panels un-maximized, tiles them across the FULL Desktop,
        and focuses the last-active panel. A no-op re-tile when already in the
        panel layout.
        """
        if self._has_active_modal() or self.desktop is None:
            return
        # Exit Project View if active so panels return to a full 1/2 split.
        self._restore_tree_view_mode()
        self._project_tree_panel_id = None
        # Stash editor/viewer/console windows in the tray so the panels can
        # fill the WHOLE screen (otherwise the console reserves the bottom
        # half in _tile_panels and the panels only get the top half).
        for w in list(self.desktop.windows):
            if isinstance(w.content, (EditorContent, ViewerContent, HexViewerContent, ImageViewerContent, CsvViewerContent, ConsoleContent)):
                self.desktop.minimize_window(w)
        # Reveal both panels un-maximized.
        for panel_id in ("panel-left", "panel-right"):
            try:
                win = self.desktop.query_one(f"#{panel_id}", Window)
            except Exception:
                continue
            win.maximized = False
            if win not in self.desktop.windows:
                self.desktop.show_window(win)
        self._tile_panels()
        # Focus the last-active panel, falling back to the left one.
        target = self._last_focused_panel_window
        if target is None or target not in self.desktop.windows:
            target_id = "panel-left"
        else:
            target_id = target.id or "panel-left"
        self._focus_panel(target_id)

    def _resolve_initial_theme(self) -> str:
        """Theme to paint on startup: the persisted one if still valid, else
        the built-in default. Validates against list_themes() and a load probe
        so a stale/renamed/corrupt entry can never crash the initial paint."""
        name = user_config.get_theme()
        if name and name in list_themes():
            try:
                theme_registry.get(name)
            except Exception as exc:
                self._theme_load_warning = (
                    f"Тема «{name}» не загрузилась: {exc}.\n"
                    "Используется modern_dark — выберите другую в меню Options."
                )
                return "modern_dark"
            return name
        return "modern_dark"

    def _apply_theme(self, name: str, *, persist: bool = False) -> None:
        """Switch the active theme and repaint the whole shell.

        Desktop.set_theme repaints the desktop, windows and icon tray; the
        menu bar and status bar resolve the palette lazily from the desktop,
        so they just need a refresh to pick up the new colours. ``persist``
        writes the choice to the user config so it survives a restart — set
        for user-initiated switches, left off for programmatic/test calls.
        """
        if self.desktop is None:
            return
        # Drop any cached parse so re-selecting a theme after editing its file
        # (Options → Edit theme) re-reads it from disk and shows the changes.
        theme_registry.invalidate(name)
        # A malformed theme file still appears in list_themes()/the Options menu,
        # so selecting it lands here. Loading raises ThemeLoadError; if that
        # escaped this handler the menu dropdown would freeze. Catch it, keep the
        # current (working) theme, leave the config untouched, and tell the user
        # so they can pick another from the still-live Options menu.
        try:
            self.desktop.set_theme(name)
        except Exception as exc:
            self.notify(
                f"Тема «{name}» не загрузилась: {exc}.\n"
                "Выберите другую тему в меню Options.",
                title="Ошибка темы",
                severity="error",
                timeout=8.0,
            )
            return
        if name in self._theme_names:
            self._theme_index = self._theme_names.index(name)
        if self.menu_bar is not None:
            self.menu_bar.refresh()
        if self.status_bar is not None:
            self.status_bar.refresh()
        if persist:
            user_config.set_theme(name)

    def action_cycle_theme(self) -> None:
        """Advance to the next available theme (Ctrl+T / Options menu)."""
        if not self._theme_names:
            return
        self._theme_index = (self._theme_index + 1) % len(self._theme_names)
        self._apply_theme(self._theme_names[self._theme_index], persist=True)

    def action_edit_theme(self) -> None:
        """Open the current theme's .toml in the editor (Options → Edit theme)."""
        if self.desktop is None or self._has_active_modal():
            return
        name = self.desktop.palette.theme.name
        path = resolve_theme_path(name)
        if path is None:
            self.notify(
                f"Тема «{name}» встроена и не имеет файла — "
                "переключитесь на другую тему, чтобы редактировать.",
                severity="warning",
            )
            return
        self._open_editor_window(path)

    def action_key_probe(self) -> None:
        """Open the diagnostic Key Probe window.

        Reachable from Help > Key Probe (and the command palette). Mounts a
        focusable KeyProbeContent so every keystroke that arrives is echoed,
        letting users see exactly what Textual decodes on their terminal.
        """
        if self.desktop is None:
            return
        if self._has_active_modal():
            return
        content = KeyProbeContent()
        win = make_window(
            content,
            title="Key Probe",
            position=(4, 2),
            size=(64, 18),
            decorations=Decorations(close_box=True, zoom_box=True, resize_grip=True),
            id="key-probe",
        )
        self.desktop.add_window(win)
        self.call_after_refresh(content.focus)

    def action_open_file(self) -> None:
        if self.desktop is None:
            return
        if self._has_active_modal():
            return
        self._remember_active_panel_id()
        dialog = InputDialog(
            "Open file (path):",
            initial="",
            context=OpenFileRequest(),
        )
        show_modal(self.desktop, dialog, title="Open File", size=(60, 5))
        self.call_after_refresh(dialog.focus_input)

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
            # The brand menu: every openable dunder (provider) — fill in the blank.
            Menu("_", [
                *[
                    MenuItem(label=label, command_id=f"dunder.open.{scheme}")
                    for scheme, label in self._openable_dunders()
                ],
                MenuItem(label="Tree (JSON/YAML)", command_id="dunder.open.tree"),
                MenuSeparator(),
                MenuItem(label="Add current location…", command_id="bookmark.add.menu"),
                *[
                    MenuItem(label=b["label"], command_id=f"bookmark.open.{i}")
                    for i, b in enumerate(list_bookmarks())
                ],
            ]),
            Menu("Left", [
                MenuItem(label="Toggle visibility", command_id="panel.left.toggle"),
                MenuSeparator(),
                MenuItem(label="Sort by name",      command_id="panel.left.sort_name"),
                MenuItem(label="Sort by extension", command_id="panel.left.sort_ext"),
                MenuItem(label="Sort by size",      command_id="panel.left.sort_size"),
                MenuItem(label="Sort by date",      command_id="panel.left.sort_mtime"),
                MenuSeparator(),
                MenuItem(label="View: Brief",       command_id="panel.left.view_brief"),
                MenuItem(label="View: Medium",      command_id="panel.left.view_medium"),
                MenuItem(label="View: Short",       command_id="panel.left.view_short"),
                MenuItem(label="View: Full",        command_id="panel.left.view_full"),
                MenuItem(label="View: Detailed",    command_id="panel.left.view_detailed"),
                MenuItem(label="View: Description", command_id="panel.left.view_description"),
            ]),
            Menu("File", [
                MenuItem(command_id="app.open_file"),
                MenuSeparator(),
                MenuItem(command_id="panel.new"),
                MenuItem(command_id="panel.view"),
                MenuItem(command_id="panel.edit"),
                MenuSeparator(),
                MenuItem(command_id="panel.chmod"),
                MenuSeparator(),
                MenuItem(command_id="save"),
                MenuItem(command_id="save_as"),
                MenuSeparator(),
                MenuItem(label="Quit", hotkey="F10", command_id="app.quit"),
            ]),
            Menu("Command", [
                MenuItem(command_id="panel.copy"),
                MenuItem(command_id="panel.move"),
                MenuItem(command_id="panel.mkdir"),
                MenuItem(command_id="panel.delete"),
                MenuItem(command_id="panel.pack"),
                MenuItem(command_id="panel.find_file"),
            ]),
            Menu("Right", [
                MenuItem(label="Toggle visibility", command_id="panel.right.toggle"),
                MenuSeparator(),
                MenuItem(label="Sort by name",      command_id="panel.right.sort_name"),
                MenuItem(label="Sort by extension", command_id="panel.right.sort_ext"),
                MenuItem(label="Sort by size",      command_id="panel.right.sort_size"),
                MenuItem(label="Sort by date",      command_id="panel.right.sort_mtime"),
                MenuSeparator(),
                MenuItem(label="View: Brief",       command_id="panel.right.view_brief"),
                MenuItem(label="View: Medium",      command_id="panel.right.view_medium"),
                MenuItem(label="View: Short",       command_id="panel.right.view_short"),
                MenuItem(label="View: Full",        command_id="panel.right.view_full"),
                MenuItem(label="View: Detailed",    command_id="panel.right.view_detailed"),
                MenuItem(label="View: Description", command_id="panel.right.view_description"),
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
                MenuSeparator(),
                MenuItem(command_id="toggle_syntax"),
                MenuItem(command_id="set_language"),
            ]),
            Menu("View", [
                MenuItem(command_id="panels.fullscreen"),  # Ctrl+P
                MenuItem(command_id="console.toggle"),       # Ctrl+O
                MenuItem(command_id="panel.toggle_hidden"),  # Alt+H
            ]),
            Menu("Options", [
                MenuItem(label="Cycle theme", command_id="theme.cycle"),
                MenuItem(label="Edit theme", command_id="theme.edit"),
                MenuSeparator(),
                *[
                    MenuItem(label=_theme_label(name), command_id=f"theme.set.{name}")
                    for name in self._theme_names
                ],
            ]),
            Menu("Help", [
                MenuItem(command_id="help.key_probe"),
            ]),
            # Items are rebuilt on every menu activation by
            # ``_refresh_windows_menu``; the empty list here is a placeholder.
            Menu("Windows", []),
        ]
        self._recompute_menu_bar()

    def _provider_menu(self) -> "Menu | None":
        """A contextual menu of the focused panel's provider actions (e.g.
        Docker lifecycle), or None when the focused scheme has no actions."""
        panel = self._active_panel()
        if panel is None:
            return None
        try:
            provider = panel._registry.resolve(panel.cwd_loc)
        except Exception:
            return None
        actions = getattr(provider, "actions", None)
        if not callable(actions):
            return None
        title = getattr(provider, "display_name", provider.scheme)
        items = [
            MenuItem(label=f"{a.icon} {a.label}".strip(),
                     command_id=f"provider.{a.id}")
            for a in actions()
        ]
        return Menu(title, items) if items else None

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
        provider_menu = self._provider_menu()
        if provider_menu is not None:
            self.menu_bar.menus = [*self.menu_bar.menus, provider_menu]
        # Reset highlight if the active menu got filtered out.
        if (
            self.menu_bar.active_index is not None
            and self.menu_bar.active_index >= len(self.menu_bar.menus)
        ):
            self.menu_bar.active_index = None
        self.menu_bar.refresh()
        self._refresh_status_bar()

    def _panel_status_items(self) -> list[StatusItem]:
        # F-keys that drive file-panel actions. F1 ("Prj Edit") opens Project
        # View. F2 is unused — leaving its handler at None makes the status bar
        # ignore clicks on that cell.
        handlers: dict[str, Callable[[], None]] = {
            "1":  self.action_project_view,
            "2":  self.action_user_menu,
            "3":  self.action_view,
            "4":  self.action_edit,
            "5":  self.action_copy,
            "6":  self.action_move,
            "7":  self.action_mkdir,
            "8":  self.action_delete,
            "9":  self.action_menu,
            "10": self.action_quit,
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
            "1":  _dispatch("project_view"),
            "2":  _dispatch("user_menu"),
            "3":  _dispatch("save_as"),
            "4":  _dispatch("replace"),
            "5":  _dispatch("split_h"),
            "6":  _dispatch("split_v"),
            "7":  _dispatch("fold_toggle"),
            "8":  _dispatch("record_macro"),
            "9":  self.action_menu,
            "10": self.action_quit,
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
        # Minimized windows: list them after a separator with a [■] prefix
        # so they can be restored from the menu (in addition to clicking
        # their icon in the IconTray).
        if self.desktop.minimized_windows:
            if items:
                items.append(MenuSeparator())
            for w in list(self.desktop.minimized_windows):
                items.append(
                    MenuItem(
                        label=f"[■] {_title(w)}",
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

        If the picked window is currently minimized, restore it first
        (``Desktop.restore_window`` already focuses it). Otherwise just
        focus directly. Updates the post-menu restore target so
        ``_on_menu_active_index_changed`` keeps focus on the chosen window
        instead of bouncing back to the window that was active when the
        menu opened.
        """
        if self.desktop is None:
            return
        try:
            if win in self.desktop.minimized_windows:
                self.desktop.restore_window(win)
            else:
                self.desktop.focus_window(win)
        except Exception:
            return
        self._pre_menu_window = win
        self._pre_menu_focus = None

    def _is_panel_mode(self) -> bool:
        """True for layouts that show the two FM panels (fm + we-mc)."""
        return self.launch_mode in ("fm", "we-mc")

    def _panel_cwd(self) -> Path:
        if self.initial_path is not None:
            return self.initial_path if self.initial_path.is_dir() else self.initial_path.parent
        return Path.cwd()

    def _mount_initial_windows(self) -> None:
        assert self.desktop is not None
        cwd = self._panel_cwd()

        if self.launch_mode in ("fm", "we-mc"):
            self._add_panel_windows(cwd, visible=True)
            if self.launch_mode == "we-mc":
                self._handover = make_handover(self, self.terminal_mode)
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

        if self.launch_mode == "we":
            self._add_panel_windows(cwd, visible=False)
            self._mount_cascaded_editors()
            return

        raise ValueError(f"unknown launch_mode: {self.launch_mode!r}")

    def _mount_cascaded_editors(self) -> None:
        assert self.desktop is not None
        # Filter: directories are skipped; missing files are kept (they open
        # as an empty buffer that saves to that path on Ctrl+S).
        files: list[Path] = []
        for p in self.initial_paths:
            if p.is_dir():
                self.notify(f"skipped {p}: not a file", severity="warning")
                continue
            files.append(p)

        self._cascade_ids = []

        if not files:
            # No usable paths -> a single untitled editor window.
            self._editor_seq += 1
            win_id = f"editor-{self._editor_seq}"
            win = self._make_editor_window(
                None,
                position=(0, 0),
                size=(_WE_MIN_W, _WE_MIN_H),
                win_id=win_id,
            )
            self.desktop.add_window(win)
            self._cascade_ids.append(win_id)
        else:
            n = len(files)
            # Add in reverse so the first file is added LAST -> ends up on top
            # of the z-order and focused, sitting at offset (0, 0).
            for i in reversed(range(n)):
                self._editor_seq += 1
                win_id = f"editor-{self._editor_seq}"
                win = self._make_editor_window(
                    files[i],
                    position=(i * _WE_CASCADE_DX, i * _WE_CASCADE_DY),
                    size=(_WE_MIN_W, _WE_MIN_H),
                    win_id=win_id,
                )
                self.desktop.add_window(win)
                self._cascade_ids.append(win_id)

        # Defer geometry to after Textual has done its first layout pass so
        # that usable_size is non-zero.
        self.call_after_refresh(self._apply_cascade_geometry)

    def _apply_cascade_geometry(self) -> None:
        """Resize cascade editor windows to fill the desktop.

        Called via call_after_refresh so Desktop.usable_size is non-zero.
        Scoped to the ids captured in `_cascade_ids` so it never touches a
        later F4-opened editor or a panel.
        """
        assert self.desktop is not None
        W = self.desktop.usable_size.width
        H = self.desktop.usable_size.height
        if W <= 0 or H <= 0:
            return
        n = len(self._cascade_ids)
        if n == 0:
            return
        cw = max(_WE_MIN_W, W - (n - 1) * _WE_CASCADE_DX)
        ch = max(_WE_MIN_H, H - (n - 1) * _WE_CASCADE_DY)
        for wid in self._cascade_ids:
            try:
                win = self.desktop.query_one(f"#{wid}", Window)
            except Exception:
                continue
            win.styles.width = cw
            win.styles.height = ch

    def _add_panel_windows(self, cwd: Path, *, visible: bool) -> None:
        assert self.desktop is not None
        left = make_window(
            FilePanel(cwd=cwd, registry=self._vfs_registry),
            title=str(cwd), position=(0, 0), size=(40, 12),
            decorations=Decorations(close_box=True, copy_box=True),
            id="panel-left",
        )
        right = make_window(
            FilePanel(cwd=cwd, registry=self._vfs_registry),
            title=str(cwd), position=(40, 0), size=(40, 12),
            decorations=Decorations(close_box=True, copy_box=True),
            id="panel-right",
        )
        # Closing a panel (close box or Left/Right > Hide) hides it instead of
        # destroying it — panels are looked up by id elsewhere and must persist.
        left.hide_on_close = True
        right.hide_on_close = True
        self.desktop.add_window(left)
        self.desktop.add_window(right)
        if not visible:
            self.desktop.hide_window(left)
            self.desktop.hide_window(right)

    def _apply_default_layout(self) -> None:
        assert self.desktop is not None and self.manager is not None
        if not self._is_panel_mode():
            return
        if self._project_tree_panel_id is not None:
            self._relayout_project_view()
            return
        self._tile_panels()

    def _tile_panels(self) -> None:
        """Tile the two PanelWindows side by side, filling the Desktop area.

        Launch-mode-agnostic (``_apply_default_layout`` gates on fm mode; the
        panel-reveal path in ``_focus_panel`` calls this directly so panels
        come back at half-screen in editor/cli modes too).
        """
        assert self.desktop is not None
        # The Desktop already accounts for MenuBar + CommandLine + StatusBar
        # via its margin CSS, so 100% of its height/width is what the panels
        # should occupy.
        W, H = self.desktop.usable_size
        if W <= 0 or H <= 0:
            return
        half = max(3, W // 2)
        # If the default console window is mounted (and not maximized),
        # panels occupy the TOP portion and the console takes the BOTTOM.
        console_h = 0
        cwin = self._console_default_window
        if cwin is not None and not cwin.maximized and cwin in self.desktop.windows:
            console_h = max(3, H // 2)
        panels_h = max(3, H - console_h)
        for i, win_id in enumerate(("panel-left", "panel-right")):
            try:
                w = self.desktop.query_one(f"#{win_id}", Window)
            except Exception:
                continue
            # A maximized panel fills the whole desktop; the Desktop's own
            # resize handler keeps it filled. Re-tiling it to a half here would
            # fight that (and the winner depends on event ordering), so skip it.
            if w.maximized:
                continue
            x = 0 if i == 0 else half
            width = half if i == 0 else (W - half)
            w.styles.offset = Offset(x, 0)
            w.styles.width = max(3, width)
            w.styles.height = panels_h
        # Refit any console windows so they don't run past the desktop edge.
        for cw in self.desktop.windows:
            if cw.id and cw.id.startswith("win-console-") and not cw.maximized:
                self._fit_console_window(cw)

    def _layout_project_view(self, tree_win: Window, editor_win: Window) -> None:
        """Dock `tree_win` as a 1/4-width tree on its own side, `editor_win` 3/4.

        The tree keeps the side its id implies: ``panel-right`` docks right (with
        the editor on the left), every other id docks left. Both windows fill the
        full usable height.
        """
        assert self.desktop is not None
        W, H = self.desktop.usable_size
        if W <= 0 or H <= 0:
            return
        tree_w = max(8, W // 4)
        editor_w = max(3, W - tree_w)
        if tree_win.id == "panel-right":
            editor_x, tree_x = 0, W - tree_w
        else:
            tree_x, editor_x = 0, tree_w
        for win, x, width in (
            (tree_win, tree_x, tree_w),
            (editor_win, editor_x, editor_w),
        ):
            win.maximized = False
            win.styles.offset = Offset(x, 0)
            win.styles.width = width
            win.styles.height = H

    def _refresh_panels(self) -> None:
        """Load directory contents into both panels (left and right).

        Each panel keeps the cursor on whatever entry it was on: a plain
        re-scan (e.g. after closing a viewer/editor) must not jump the cursor
        to the top. This matters most for slow providers (SFTP) whose async
        re-scan resets the cursor to 0 before the listing returns, so we pass
        the focused locator through as ``focus_loc`` to land back on it.
        """
        from dunders.fm.file_panel import FilePanel  # local: avoid circular at import-time
        for panel_id in ("panel-left", "panel-right"):
            try:
                win = self.desktop.query_one(f"#{panel_id}", Window)
            except Exception:
                continue
            content = win.content
            if isinstance(content, FilePanel):
                focus_loc = (
                    content.entries[content.cursor].loc
                    if 0 <= content.cursor < len(content.entries)
                    else None
                )
                content.refresh_listing(focus_loc=focus_loc)
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

    def _set_panel_view_mode(self, panel_id: str, mode: PanelViewMode) -> None:
        if self.desktop is None:
            return
        try:
            win = self.desktop.query_one(f"#{panel_id}", Window)
        except Exception:
            return
        panel = win.content
        if not isinstance(panel, FilePanel):
            return
        panel.view_mode = mode
        panel._ensure_cursor_visible()
        panel.refresh()

    def _focus_panel(self, panel_id: str) -> None:
        from dunders.fm.file_panel import FilePanel
        try:
            win = self.desktop.query_one(f"#{panel_id}", Window)
        except Exception:
            return
        if not isinstance(win.content, FilePanel):
            return
        # In editor/cli launch modes the panels are mounted hidden (and a panel
        # may also be minimized). Reveal ONLY the requested panel, give it a
        # sane half-screen geometry, and raise+focus it. ``show_window`` removes
        # it from hidden/minimized, re-adds it to the visible stack, mounts it,
        # and raises+focuses it (so it sits on top of the editor window).
        if win not in self.desktop.windows:
            self.desktop.show_window(win)
            self._tile_panels()
        self.desktop.focus_window(win)
        self.set_focus(win.content)
        self._last_focused_panel_window = win
        # If this was invoked from a menu pick, keep the post-menu focus
        # restoration on this panel — otherwise ``_on_menu_active_index_changed``
        # raises the previously-active window (the editor) back on top, hiding
        # the panel. Both menu-open paths recapture these on the next open, so
        # setting them here is safe even outside a menu. Mirrors _select_window.
        self._pre_menu_window = win
        self._pre_menu_focus = None

    def _toggle_panel(self, panel_id: str) -> None:
        """Toggle a file panel's visibility (Alt+F1 left / Alt+F2 right).

        Panels are looked up by id throughout the app, so a hidden panel is
        kept (not destroyed) and simply re-shown. Mirrors the close box,
        which hides the panel too.
        """
        if self.desktop is None:
            return
        # Toggling a panel is the Project View exit path.
        self._restore_tree_view_mode()
        self._project_tree_panel_id = None
        try:
            win = self.desktop.query_one(f"#{panel_id}", Window)
        except Exception:
            return
        if win in self.desktop.windows:
            self.desktop.hide_window(win)
        else:
            self._focus_panel(panel_id)

    def _focus_command_line(self) -> None:
        """Move Textual widget focus to the CommandLine input.

        desktop.focused_window is left unchanged (still pointing at the
        active panel window) so CommandDispatcher hotkey routing keeps
        working when the user types F-keys while the cmdline is focused.

        We use both Widget.focus() and App.set_focus() to cover both the
        Textual-internal focus state and the screen-level focus chain.
        The input is accessed via the stored ``_input`` attribute rather than
        query_one so focus lands correctly even before the widget is fully
        included in the query index.
        """
        if self.command_line is None:
            return
        inp = self.command_line._input
        if inp is None or not inp.is_mounted:
            return
        self.set_focus(inp)
        inp.focus()
        # Place the cursor at the end of the current value (no selection)
        # and force a cursor-blink "on" tick so it's visible immediately
        # rather than waiting for the first blink cycle.
        try:
            inp.cursor_position = len(inp.value)
        except Exception:
            pass
        try:
            inp._cursor_visible = True  # private reactive in Textual.Input
        except Exception:
            pass

    def _active_panel(self):
        """Return the currently focused FilePanel, or None.

        Resolution order:
          1. Walk up from ``self.focused`` — works for direct keypresses.
          2. ``desktop.focused_window.content`` — survives menu activation
             (the menu bar steals widget focus, but the desktop's tracked
             window is unaffected since dropdowns aren't windows).
          3. ``_pre_menu_window.content`` — set when the menu was opened,
             so commands invoked via menu items still target the panel
             the user was on before pressing F9.
          4. ``panel-left`` as a last-resort fallback.
        """
        node = self.focused
        while node is not None:
            if isinstance(node, FilePanel):
                return node
            node = getattr(node, "parent", None)
        if self.desktop is not None:
            win = self.desktop.focused_window
            if win is not None and isinstance(win.content, FilePanel):
                return win.content
        if self._pre_menu_window is not None and isinstance(
            self._pre_menu_window.content, FilePanel
        ):
            return self._pre_menu_window.content
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

    def _panels_visible(self) -> bool:
        """True if either file panel is currently on the visible window stack.

        Hidden panels (editor/cli launch modes, close-box, Alt+F1/F2 toggle)
        live in ``desktop.hidden_windows``; a visible panel is in
        ``desktop.windows``. Drives whether cmdline up/down navigates the
        panel (panels visible) or command history (console-only).
        """
        if self.desktop is None:
            return False
        visible_ids = {w.id for w in self.desktop.windows}
        return "panel-left" in visible_ids or "panel-right" in visible_ids

    def _cmdline_panel_nav(self, direction: int) -> bool:
        """Divert a cmdline boundary up/down to the active file panel.

        Returns True when the key was consumed (panel cursor moved); False
        lets the CommandLine fall back to command-history navigation. Moves
        focus onto the panel so Enter/F-keys act on it natively — the
        existing Far-style letter routing bounces focus back to the cmdline
        on the next typed character.
        """
        if not self._panels_visible():
            return False
        panel = self._active_panel()
        if panel is None:
            return False
        win = panel.parent
        while win is not None and not isinstance(win, Window):
            win = getattr(win, "parent", None)
        if win is None or win.id not in ("panel-left", "panel-right"):
            return False
        self._focus_panel(win.id)
        panel.move_cursor(direction)
        return True

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
        if self._is_panel_mode():
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
        if panel.cwd_loc.scheme != "file":
            self._warn_archive_unsupported()
            return
        self._remember_active_panel_id()
        cwd = panel.cwd
        dialog = NewFileDialog(
            prompt=f"Create directory in {cwd}:",
            context=MkdirRequest(parent=cwd),
            submit_label="Make",
            title="Mkdir",
        )
        show_modal(self.desktop, dialog, title="Mkdir", size=(60, 7))
        self.call_after_refresh(dialog.focus_input)

    def action_pack(self) -> None:
        """Create a new .zip from the selection (File → Create archive…)."""
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        if panel.cwd_loc.scheme != "file":
            self._warn_archive_unsupported()  # packing from inside an archive: later
            return
        targets = panel.effective_targets()
        if not targets:
            return
        self._remember_active_panel_id()
        default = f"{targets[0].name}.zip" if len(targets) == 1 else "archive.zip"
        dialog = NewFileDialog(
            prompt=f"Create archive in {panel.cwd}:",
            initial=default,
            context=PackRequest(targets=targets, base=panel.cwd),
            submit_label="Pack",
            title="Create archive",
        )
        show_modal(self.desktop, dialog, title="Create archive", size=(60, 7))
        self.call_after_refresh(dialog.focus_input)

    def _open_dunder(self, scheme: str) -> None:
        """"_" menu: prompt for a spec, then open that provider in the active
        panel (create/open an archive, initialise a connection, …)."""
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        provider = self._vfs_registry.for_scheme(scheme)
        label = getattr(provider, "display_name", scheme)
        # A provider may guide the prompt: `open_placeholder` is a greyed hint
        # shown in the empty field; `default_open_spec` pre-fills a value the
        # user can accept or edit. Both may be a plain str or a callable.
        placeholder = self._provider_hint(provider, "open_placeholder")
        initial = self._provider_hint(provider, "default_open_spec")
        self._remember_active_panel_id()
        dialog = NewFileDialog(
            prompt=f"Open {label} — name or address:",
            context=OpenDunderRequest(scheme=scheme),
            submit_label="Open",
            title=label,
            initial=initial,
            placeholder=placeholder,
        )
        # Wider than the default modal so a provider's hint (e.g. Docker's
        # ssh:// URL form) fits in the input placeholder without truncation.
        width = max(60, len(placeholder) + 8) if placeholder else 60
        show_modal(self.desktop, dialog, title=label, size=(width, 7))
        self.call_after_refresh(dialog.focus_input)

    def _open_tree_dunder(self) -> None:
        """"_"/palette: open the .json/.yaml file under the cursor as an
        editable tree (the JSON/YAML tree dunder)."""
        if self._has_active_modal() or self.desktop is None:
            return
        panel = self._active_panel()
        if panel is None or not (0 <= panel.cursor < len(panel.entries)):
            return
        entry = panel.entries[panel.cursor]
        if entry.is_dir or not self._is_local_entry(entry):
            self.notify("Tree: select a local .json/.yaml/.yml file", severity="warning")
            return
        path = entry.path
        if path.suffix.lower() not in (".json", ".yaml", ".yml"):
            self.notify("Tree opens .json/.yaml/.yml files", severity="warning")
            return
        self._remember_active_panel_id()
        self._editor_seq += 1
        try:
            content = JsonYamlTreeContent(str(path))
        except Exception as exc:  # noqa: BLE001 — surface parse/IO errors as a toast
            self.notify(f"Tree: cannot open {path.name}: {exc}", severity="error")
            return
        self._mount_maximized_content(
            content, title=f"Tree: {path.name}", win_id=f"tree-{self._editor_seq}"
        )

    @staticmethod
    def _provider_hint(provider: object, attr: str) -> str:
        """Read an optional provider hint (e.g. ``open_placeholder``); accept a
        plain str or a callable, and tolerate its absence / failure."""
        hint = getattr(provider, attr, "")
        try:
            hint = hint() if callable(hint) else hint
        except Exception:
            return ""
        return hint if isinstance(hint, str) else ""

    def _do_open_dunder(self, scheme: str, spec: str, *, password: str | None = None) -> None:
        panel = self._active_panel()
        if panel is None:
            return
        provider = self._vfs_registry.for_scheme(scheme)
        resolver = getattr(provider, "resolve_target", None)
        if resolver is None:
            return
        # Provider needs a secret (e.g. FTP user without an inline password) and
        # none supplied yet → prompt for it, then re-enter with the password.
        needs = getattr(provider, "needs_password", None)
        if password is None and callable(needs) and needs(spec):
            self._prompt_dunder_password(scheme, spec)
            return
        base = panel.cwd_loc
        # Slow (network) providers connect inside resolve_target — run it on a
        # worker so opening never freezes the UI. Fast providers (zip/7z create)
        # resolve locally and instantly, so stay synchronous.
        if "slow" not in getattr(provider, "capabilities", frozenset()):
            self._apply_open_result(panel, spec, self._resolve_safe(resolver, spec, base, password))
            return
        self.notify(f"Connecting to {scheme}…", severity="information", timeout=3)

        def _work() -> None:
            result = self._resolve_safe(resolver, spec, base, password)
            try:
                self.call_from_thread(self._apply_open_result, panel, spec, result)
            except Exception:
                pass  # app went away

        self.run_worker(_work, thread=True, exclusive=False, group="dunder-connect")

    def _run_provider_action(self, action, targets=None) -> None:
        """Run a ProviderAction on the active panel's selection, off the UI thread."""
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None:
            return
        if targets is None:
            locs = set(panel.effective_target_locs())
            targets = [e.loc for e in panel.entries
                       if e.loc in locs and action.applies_to(e)]
        if not targets:
            return
        self.notify(f"{action.label}…", severity="information", timeout=2)

        focus = targets[0]  # keep the cursor on the acted container after re-scan

        def _work() -> None:
            result = action.run(targets)
            try:
                self.call_from_thread(self._after_provider_action, panel, result, focus)
            except Exception:
                pass  # app went away

        self.run_worker(_work, thread=True, exclusive=False, group="provider-action")

    def _after_provider_action(self, panel, result, focus_loc=None) -> None:
        errors = getattr(result, "errors", None)
        if errors:
            self.notify(str(errors[0]), severity="error")
        # Restore the cursor onto the acted container (the slow/async re-scan
        # otherwise resets it to the top). A removed container won't match and
        # the cursor simply clamps.
        panel.refresh_listing(focus_loc=focus_loc)

    @staticmethod
    def _resolve_safe(resolver, spec, base, password):
        """Run resolve_target, capturing (target, error) so the result can cross
        a worker-thread boundary."""
        try:
            return (resolver(spec.strip(), base=base, password=password), None)
        except Exception as exc:
            return (None, exc)

    def _apply_open_result(self, panel, spec, result) -> None:
        target, error = result
        if error is not None:
            # Operational failure (bad host, refused, auth, timeout): show the
            # provider's specific reason.
            self.notify(str(error) or f"Cannot open {spec!r}", severity="error")
            return
        if target is None:
            self.notify(f"Cannot open {spec!r}", severity="warning")
            return
        panel._change_cwd_loc(target)

    def _prompt_dunder_password(self, scheme: str, spec: str) -> None:
        if self.desktop is None:
            return
        self._remember_active_panel_id()
        dialog = InputDialog(
            "Password:",
            context=DunderPasswordRequest(scheme=scheme, spec=spec),
            password=True,
        )
        show_modal(self.desktop, dialog, title="Password", size=(50, 5))
        self.call_after_refresh(dialog.focus_input)

    def _scheme_is_slow(self, scheme: str) -> bool:
        try:
            provider = self._vfs_registry.for_scheme(scheme)
        except KeyError:
            return False
        return "slow" in getattr(provider, "capabilities", frozenset())

    def _scheme_accepts_empty_open(self, scheme: str) -> bool:
        """Whether opening this provider with an empty spec is meaningful
        (e.g. Docker → the container index). Defaults to False."""
        try:
            provider = self._vfs_registry.for_scheme(scheme)
        except KeyError:
            return False
        return bool(getattr(provider, "accepts_empty_open", False))

    def action_add_bookmark(self) -> None:
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        loc = panel.cwd_loc
        self._remember_active_panel_id()
        dialog = AddBookmarkDialog(
            default_label=loc.name,
            ask_password=self._scheme_is_slow(loc.scheme),
            context=AddBookmarkRequest(loc=loc),
        )
        show_modal(self.desktop, dialog, title="Add bookmark", size=(62, 9))
        self.call_after_refresh(dialog._label_input.focus)

    def _refresh_bookmarks_menu(self) -> None:
        # Re-register bookmark commands (labels/ids change as bookmarks change),
        # then rebuild the menu so the "_" section reflects the new list.
        self.command_registry.register_many(self._bookmark_commands())
        if self._all_menus:
            self._build_menus()

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

    def on_add_bookmark_dialog_submitted(self, event: AddBookmarkDialog.Submitted) -> None:
        ctx = event.dialog.context
        self._close_modal(event.dialog)
        if not isinstance(ctx, AddBookmarkRequest):
            return
        label = (event.label or "").strip()
        if not label:
            return
        password = None
        if event.remember:
            provider = self._vfs_registry.for_scheme(ctx.loc.scheme)
            getpw = getattr(provider, "connection_password", None)
            if callable(getpw):
                password = getpw(ctx.loc.root)
        add_bookmark(label, ctx.loc.as_uri(), password)
        self._refresh_bookmarks_menu()

    def on_add_bookmark_dialog_cancelled(self, event: AddBookmarkDialog.Cancelled) -> None:
        self._close_modal(event.dialog)

    def action_open_bookmarks(self) -> None:
        if self._has_active_modal() or self.desktop is None:
            return
        self._remember_active_panel_id()
        dialog = BookmarksDialog(list_bookmarks())
        show_modal(self.desktop, dialog, title="Bookmarks", size=(86, 24))
        self.call_after_refresh(dialog._table.focus)

    def on_bookmarks_dialog_open(self, event: BookmarksDialog.Open) -> None:
        self._close_modal(event.dialog)
        items = list_bookmarks()
        if 0 <= event.index < len(items):
            self._open_bookmark(items[event.index])

    def on_bookmarks_dialog_remove(self, event: BookmarksDialog.Remove) -> None:
        remove_bookmark(event.index)
        self._refresh_bookmarks_menu()
        # Keep the picker open and refresh its table in place so the user can
        # delete several bookmarks in a row.
        event.dialog.refresh_rows(list_bookmarks())

    def on_bookmarks_dialog_add_current(self, event: BookmarksDialog.AddCurrent) -> None:
        self._close_modal(event.dialog)
        self.action_add_bookmark()

    def on_bookmarks_dialog_cancelled(self, event: BookmarksDialog.Cancelled) -> None:
        self._close_modal(event.dialog)

    def _open_bookmark(self, bm: dict) -> None:
        panel = self._active_panel()
        if panel is None:
            return
        try:
            loc = VfsPath.parse(bm["uri"])
        except Exception:
            self.notify(f"Bad bookmark: {bm.get('label')!r}", severity="warning")
            return
        if self._scheme_is_slow(loc.scheme):
            spec = loc.root + ("/" + "/".join(loc.parts) if loc.parts else "/")
            self._do_open_dunder(loc.scheme, spec, password=bm.get("password"))
        else:
            panel._change_cwd_loc(loc)

    def _bookmark_commands(self) -> "list[WindowCommand]":
        out = [WindowCommand(id="bookmark.add.menu", label="Add current location…",
                             handler=self.action_add_bookmark)]
        for i, b in enumerate(list_bookmarks()):
            out.append(WindowCommand(
                id=f"bookmark.open.{i}",
                label=b["label"],
                handler=(lambda idx=i: self._open_bookmark_by_index(idx)),
            ))
        return out

    def _open_bookmark_by_index(self, index: int) -> None:
        items = list_bookmarks()
        if 0 <= index < len(items):
            self._open_bookmark(items[index])

    # --- Find file ---------------------------------------------------------

    def action_find_file(self) -> None:
        """Open the Far-style Find file dialog. Search starts when user
        submits; results land in a non-modal SearchResultsContent window
        spawned by ``_start_search``.
        """
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        self._remember_active_panel_id()
        dialog = FindFileDialog(start_dir=panel.cwd)
        show_modal(self.desktop, dialog, title="Find file", size=(72, 16))
        self.call_after_refresh(dialog.focus_input)

    def on_find_file_dialog_submitted(
        self, event: FindFileDialog.Submitted
    ) -> None:
        dialog = event.dialog
        start_dir = dialog.start_dir
        self._close_modal(dialog)
        self._start_search(event.options, start_dir)

    def on_find_file_dialog_cancelled(
        self, event: FindFileDialog.Cancelled
    ) -> None:
        self._close_modal(event.dialog)

    def _start_search(self, options: FindOptions, start_dir: Path) -> None:
        if self.desktop is None:
            return
        content = SearchResultsContent(options=options, start_dir=start_dir)
        dw, dh = self.desktop.usable_size.width, self.desktop.usable_size.height
        win = make_window(
            content,
            title=f"Find file: {' '.join(options.masks)}",
            position=(0, 0),
            size=(max(40, dw), max(10, dh)),
            decorations=Decorations(close_box=True, zoom_box=True, minimize_box=True, resize_grip=True),
            id="find_results",
        )
        win._saved_rect = (Offset(2, 1), Size(max(1, dw - 4), max(1, dh - 2)))
        win.maximized = True
        self.desktop.add_window(win)
        self.call_after_refresh(content.focus)

        def _worker() -> None:
            def _on_progress(cur_dir: Path, files: int, folders: int) -> None:
                self.call_from_thread(content.update_status, cur_dir, files, folders)

            def _on_match(path: Path) -> None:
                self.call_from_thread(content.add_match, path)

            result = find_walk(
                start_dir,
                options,
                on_progress=_on_progress,
                on_match=_on_match,
                cancel_event=content.cancel_event,
            )
            self.call_from_thread(content.finish, result)

        self.run_worker(_worker, thread=True, exclusive=False, group="findfile")

    # --- Search-results window plumbing ------------------------------------

    def on_search_results_content_go_to_requested(
        self, event: SearchResultsContent.GoToRequested
    ) -> None:
        target = event.path
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        parent = target.parent if target.is_file() else target
        try:
            panel._change_cwd(parent)
        except OSError:
            return
        # _change_cwd left cursor at the parent row; move it onto the file.
        for i, entry in enumerate(panel.entries):
            if entry.path == target:
                panel.cursor = i
                break
        panel.refresh()
        self._close_results_window(event.content)

    def on_search_results_content_view_requested(
        self, event: SearchResultsContent.ViewRequested
    ) -> None:
        if event.path.is_file():
            self._open_editor_window(event.path, read_only=True)

    def on_search_results_content_edit_requested(
        self, event: SearchResultsContent.EditRequested
    ) -> None:
        if event.path.is_file():
            self._open_editor_window(event.path, read_only=False)

    def on_search_results_content_stop_requested(
        self, event: SearchResultsContent.StopRequested
    ) -> None:
        if self.desktop is None:
            return
        confirm = ConfirmDialog(
            prompt="Operation has been interrupted.\nDo you really want to cancel it?",
            context=CancelSearchRequest(content=event.content),
        )
        show_modal(self.desktop, confirm, title="Interrupt", size=(50, 6))

    def on_search_results_content_close_requested(
        self, event: SearchResultsContent.CloseRequested
    ) -> None:
        # If the search is still running, signal cancellation so the worker
        # exits promptly instead of holding the thread until natural EOF.
        if event.content.search_running:
            event.content.cancel_event.set()
        self._close_results_window(event.content)

    def on_search_results_content_new_search_requested(
        self, event: SearchResultsContent.NewSearchRequested
    ) -> None:
        # Cancel any in-flight worker, close the window, re-open the dialog.
        if event.content.search_running:
            event.content.cancel_event.set()
        self._close_results_window(event.content)
        self.action_find_file()

    def _close_results_window(self, content: SearchResultsContent) -> None:
        if self.desktop is None:
            return
        # Walk up to the enclosing Window — content is mounted inside one.
        node = content
        while node is not None:
            parent = getattr(node, "parent", None)
            if isinstance(parent, Window):
                try:
                    self.desktop.remove_window(parent)
                except Exception:
                    pass
                return
            node = parent

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

    def action_set_language(self, editor: "EditorContent") -> None:
        if self.desktop is None:
            return
        if self._has_active_modal():
            return
        self._remember_active_panel_id()
        show_language_picker(self.desktop, editor)

    def on_language_picker_content_picked(self, message) -> None:
        win = self._modal_window_for(message.picker)
        if win is not None and self.desktop is not None:
            self.desktop.remove_window(win)
        if message.picker.editor is not None:
            message.picker.editor._editor.set_language(message.language)
            message.picker.editor._editor.focus()
        message.stop()

    def on_language_picker_content_dismissed(self, message) -> None:
        win = self._modal_window_for(message.picker)
        if win is not None and self.desktop is not None:
            self.desktop.remove_window(win)
        message.stop()

    def on_new_file_dialog_submitted(
        self, event: NewFileDialog.Submitted
    ) -> None:
        ctx = event.dialog.context
        self._close_modal(event.dialog)
        if isinstance(ctx, MkdirRequest):
            name = event.value.strip()
            if not name:
                return
            result = mkdir_at(ctx.parent, name)
            self._report_op_result("mkdir", result)
            self._refresh_panels()
            return
        if isinstance(ctx, PackRequest):
            name = event.value.strip()
            if not name:
                return
            if not name.lower().endswith(".zip"):
                name += ".zip"
            self._run_pack(ctx.targets, ctx.base / name, ctx.base)
            return
        if isinstance(ctx, OpenDunderRequest):
            spec = event.value.strip()
            # Empty spec is meaningful for providers that expose a "default
            # landing" (Docker → the container index); for host-based ones
            # (FTP/SFTP) it stays a no-op. Providers opt in via
            # ``accepts_empty_open``.
            if spec or self._scheme_accepts_empty_open(ctx.scheme):
                self._do_open_dunder(ctx.scheme, spec)
            return
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
        # Source may be a non-local panel (e.g. inside a zip) — copy then
        # extracts via the cross-provider transfer engine. VfsPath targets.
        targets = panel.effective_target_locs()
        if not targets:
            return
        opposite = self._opposite_panel(panel)
        if opposite is None:
            return
        dest_loc = opposite.cwd_loc
        into_archive = dest_loc.scheme != "file"
        if into_archive and "write" not in self._vfs_registry.resolve(dest_loc).capabilities:
            self._warn_archive_unsupported()  # destination provider is read-only
            return
        self._remember_active_panel_id()
        dest = opposite.cwd
        verb = "Copy" if op == "copy" else "Move"
        if into_archive:
            # Destination is a browsed archive dir; the path isn't user-editable
            # (append at the current sub-path). Show the locator for clarity.
            initial = dest_loc.as_uri()
            n = len(targets)
            prompt = f"{verb} {n} item(s) into archive:"
        elif len(targets) == 1:
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
            context=CopyMoveRequest(
                op=op, targets=targets, dest=dest, base=panel.cwd, dest_loc=dest_loc
            ),
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
        if not self._is_local_entry(entry):
            self._open_member_edit(entry)  # edit in place via the provider
            return
        self._open_editor_window(entry.path, read_only=False)

    @staticmethod
    def _is_local_entry(entry) -> bool:
        """True if `entry` is a real local-filesystem file/dir.

        Inside a non-local source (e.g. a browsed zip) entry.path would raise,
        and view/edit/mkdir there are not wired yet — read-only browse only.
        """
        return entry.loc.scheme == "file"

    def _warn_archive_unsupported(self) -> None:
        self.notify(
            "Not available inside archives yet (read-only browse)",
            severity="warning",
        )

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
        if not self._is_local_entry(entry):
            self._open_member_view(entry)  # read through the VFS provider
            return
        self._open_editor_window(entry.path, read_only=True)

    def action_toggle_hidden(self) -> None:
        """Alt+H / View menu: show or hide dot-files in the active panel."""
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None:
            return
        panel.toggle_show_hidden()
        panel.refresh()

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

    @staticmethod
    def _looks_csv(name: object) -> bool:
        """True for delimited-text extensions (.csv/.tsv). Cheap name-only check;
        the size/binary guards still decide whether it's small enough to parse."""
        return str(name).lower().endswith((".csv", ".tsv"))

    @staticmethod
    def _looks_image(path: Path) -> bool:
        """Sniff the first 16 bytes for a known image magic signature."""
        try:
            with open(path, "rb") as fh:
                head = fh.read(16)
        except OSError:
            return False
        return sniff_image(head)

    def _should_use_hex_viewer(self, path: Path) -> bool:
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size > self._HEX_VIEW_SIZE_THRESHOLD:
            return True
        return self._looks_binary(path)

    def _make_editor_window(
        self,
        path: Path | None,
        *,
        position: tuple[int, int],
        size: tuple[int, int],
        win_id: str,
        text: str | None = None,
    ) -> Window:
        """Build a focusable editor Window for `path` (None -> untitled).

        Single source of truth for editor-window construction, shared by
        `_open_editor_window` and the `we`-mode cascade. Does NOT add the
        window to the desktop. If `text` is given, it is used as-is (the
        caller already read the file); otherwise the file is read here.
        """
        if path is None:
            text = "" if text is None else text
            title = "untitled"
            file_path = None
        else:
            if text is None:
                try:
                    text = path.read_text()
                except OSError:
                    text = ""
            title = path.name
            file_path = str(path)
        content = _FocusableEditorContent(initial_text=text, file_path=file_path)
        return make_window(
            content,
            title=title,
            position=position,
            size=size,
            decorations=Decorations(
                close_box=True, zoom_box=True, minimize_box=True, resize_grip=True
            ),
            id=win_id,
        )

    def _window_of(self, content) -> Window | None:
        """Walk up from a WindowContent to its enclosing Window (or None)."""
        node = getattr(content, "parent", None)
        while node is not None and not isinstance(node, Window):
            node = getattr(node, "parent", None)
        return node

    def action_project_view(self) -> None:
        """F1: enter Project View. Branches on whether an editor or panel is focused."""
        if self._has_active_modal():
            return
        if self.desktop is None:
            return
        if self._is_editor_focused():
            self._project_view_from_editor()
            return
        panel = self._active_panel()
        if panel is not None:
            # Only enter Project View when the resolved panel's window is
            # actually visible — _active_panel() has a panel-left last-resort
            # fallback that fires even when focus is on the command line or
            # another widget, which would silently enter Project View against a
            # panel the user isn't looking at.
            panel_win = self._window_of(panel)
            if panel_win is None or panel_win not in self.desktop.windows:
                return
            self._project_view_from_panel(panel)

    def _project_view_from_panel(self, panel) -> None:
        from dunders.fm.file_panel import FilePanel
        if not isinstance(panel, FilePanel):
            return
        tree_win = self._window_of(panel)
        if tree_win is None or tree_win.id not in ("panel-left", "panel-right"):
            return
        if not (0 <= panel.cursor < len(panel.entries)):
            return
        entry = panel.entries[panel.cursor]
        if entry.is_dir:
            return  # F1 on a directory is a no-op, mirroring F4.
        tree_id = tree_win.id
        other_id = "panel-right" if tree_id == "panel-left" else "panel-left"
        # Hide the opposite panel.
        try:
            other = self.desktop.query_one(f"#{other_id}", Window)
            if other in self.desktop.windows:
                self.desktop.hide_window(other)
        except Exception:
            pass
        # Minimize any currently-open editor windows to the IconTray.
        for w in list(self.desktop.windows):
            if isinstance(w.content, EditorContent):
                self.desktop.minimize_window(w)
        # Build a fresh, NON-maximized editor for the selected file.
        self._editor_seq += 1
        try:
            text = entry.path.read_text()
        except OSError:
            text = ""
        W, H = self.desktop.usable_size
        editor_win = self._make_editor_window(
            entry.path,
            position=(0, 0),
            size=(max(3, W), max(3, H)),
            win_id=f"editor-{self._editor_seq}",
            text=text,
        )
        self.desktop.add_window(editor_win)
        self._project_tree_panel_id = tree_id
        self._enter_tree_short_mode(panel)
        self._layout_project_view(tree_win=tree_win, editor_win=editor_win)
        self.desktop.focus_window(editor_win)

    def _enter_tree_short_mode(self, panel) -> None:
        """Narrow the project tree to Short view, remembering the old mode.

        The tree only gets 1/4 of the width, so a multi-column or wide listing
        is unusable there; Short (Name + Size, single column) fits. The prior
        mode is restored by _restore_tree_view_mode when Project View exits.
        """
        from dunders.fm.file_panel import FilePanel
        if not isinstance(panel, FilePanel):
            return
        if panel.view_mode is not PanelViewMode.SHORT:
            self._project_prev_view_mode = panel.view_mode
        panel.view_mode = PanelViewMode.SHORT
        panel._ensure_cursor_visible()
        panel.refresh()

    def _restore_tree_view_mode(self) -> None:
        """Restore the tree panel's pre-Project-View mode, if one was saved."""
        if self.desktop is None or self._project_prev_view_mode is None:
            return
        prev = self._project_prev_view_mode
        self._project_prev_view_mode = None
        tree_id = self._project_tree_panel_id or "panel-left"
        try:
            win = self.desktop.query_one(f"#{tree_id}", Window)
        except Exception:
            return
        panel = win.content
        from dunders.fm.file_panel import FilePanel
        if not isinstance(panel, FilePanel):
            return
        panel.view_mode = prev
        panel._ensure_cursor_visible()
        panel.refresh()

    def _project_view_from_editor(self) -> None:
        from dunders.fm.file_panel import FilePanel
        editor_win = self.desktop.focused_window
        if editor_win is None or not isinstance(editor_win.content, EditorContent):
            return
        tree_id = self._project_tree_panel_id or "panel-left"
        other_id = "panel-right" if tree_id == "panel-left" else "panel-left"
        try:
            tree_win = self.desktop.query_one(f"#{tree_id}", Window)
        except Exception:
            return
        if not isinstance(tree_win.content, FilePanel):
            return
        if tree_win not in self.desktop.windows:
            self.desktop.show_window(tree_win)
        try:
            other = self.desktop.query_one(f"#{other_id}", Window)
            if other in self.desktop.windows:
                self.desktop.hide_window(other)
        except Exception:
            pass
        self._project_tree_panel_id = tree_id
        self._enter_tree_short_mode(tree_win.content)
        self._layout_project_view(tree_win=tree_win, editor_win=editor_win)
        # F1 from the editor jumps focus INTO the tree so the user can navigate
        # the file listing immediately (the editor stays open on the right).
        self.desktop.focus_window(tree_win)

    def _mount_maximized_content(self, content, *, title: str, win_id: str) -> None:
        """Wrap a WindowContent in a born-maximized Window and add it.

        Shared by the path-based editor/viewer and the in-archive member
        viewer so window plumbing lives in one place.
        """
        dw, dh = self.desktop.usable_size.width, self.desktop.usable_size.height
        win = make_window(
            content,
            title=title,
            position=(0, 0),
            size=(dw, dh),
            decorations=Decorations(close_box=True, zoom_box=True, minimize_box=True, resize_grip=True),
            id=win_id,
        )
        # Born maximized: pre-seed the restore rect so F5 / [↕] toggles back
        # to a sensible windowed size instead of being a no-op.
        win._saved_rect = (Offset(2, 1), Size(max(1, dw - 4), max(1, dh - 2)))
        win.maximized = True
        self.desktop.add_window(win)
        # If the action was kicked off from the menu (File → View / Edit),
        # the post-menu restore in `_on_menu_active_index_changed` would
        # otherwise raise the original FilePanel back on top of the new
        # editor. Redirect the restore target to the window we just made.
        if self._pre_menu_focus is not None or self._pre_menu_window is not None:
            self._pre_menu_window = win
            self._pre_menu_focus = None

    def _read_member_bytes(self, entry) -> bytes | None:
        """Read a VFS member (archive/SFTP/FTP/…) through its provider, returning
        the raw bytes — or None (with a notification) if too large or unreadable.

        Caps reads at the hex-viewer threshold so a huge remote file can't be
        slurped into memory; a local file would lazily mmap, but a VFS member has
        no local path to map, so the whole buffer is read at once."""
        if entry.size > self._HEX_VIEW_SIZE_THRESHOLD:
            self.notify(
                f"{entry.name}: too large to open remotely yet",
                severity="warning",
            )
            return None
        provider = self._vfs_registry.resolve(entry.loc)
        try:
            with provider.open_read(entry.loc) as fh:
                return fh.read()
        except OSError as exc:
            self.notify(f"Cannot read {entry.name}: {exc}", severity="warning")
            return None

    def _read_member_text(self, entry) -> str | None:
        """Read a VFS member as decoded text — or None (with a notification) if
        too large, unreadable, or binary. Used by the in-place editor (F4),
        which can't meaningfully edit binary content."""
        data = self._read_member_bytes(entry)
        if data is None:
            return None
        if b"\x00" in data[:8192]:
            self.notify(
                f"{entry.name}: binary file (not editable yet)",
                severity="warning",
            )
            return None
        return data.decode("utf-8", errors="replace")

    def _open_member_view(self, entry) -> None:
        """F3/Enter on a VFS file (archive/SFTP/…): read-only viewer. Text opens
        in the plain viewer; binary content falls back to the hex viewer fed from
        the bytes we just read (no local path to mmap)."""
        if self.desktop is None:
            return
        data = self._read_member_bytes(entry)
        if data is None:
            return
        self._remember_active_panel_id()
        self._editor_seq += 1
        # Image bytes → ASCII-art viewer (Pillow opt-in). Without the extra,
        # fall through to the hex view below like any other binary.
        if PILLOW_AVAILABLE and sniff_image(data[:16]):
            content = ImageViewerContent.from_bytes(entry.name, data)
            self._mount_maximized_content(
                content,
                title=f"Image: {entry.name}",
                win_id=f"imgviewer-{self._editor_seq}",
            )
            return
        if b"\x00" in data[:8192]:
            content = HexViewerContent.from_bytes(entry.name, data)
            title = f"Hex: {entry.name}"
            win_id = f"hexviewer-{self._editor_seq}"
        elif self._looks_csv(entry.name):
            content = CsvViewerContent.from_bytes(entry.name, data)
            title = f"CSV: {entry.name}"
            win_id = f"csvviewer-{self._editor_seq}"
        else:
            content = ViewerContent(
                initial_text=data.decode("utf-8", errors="replace"),
                file_path=entry.name,
            )
            title = f"View: {entry.name}"
            win_id = f"viewer-{self._editor_seq}"
        self._mount_maximized_content(content, title=title, win_id=win_id)

    def _open_member_edit(self, entry) -> None:
        """F4 on a file inside a writable archive: editable editor whose save
        writes the member back through the provider (overwrite in place)."""
        if self.desktop is None:
            return
        provider = self._vfs_registry.resolve(entry.loc)
        if "write" not in getattr(provider, "capabilities", frozenset()):
            self._warn_archive_unsupported()
            return
        text = self._read_member_text(entry)
        if text is None:
            return
        loc = entry.loc

        def _save(buf_text: str) -> bool:
            p = self._vfs_registry.resolve(loc)
            try:
                with p.open_write(loc, overwrite=True) as w:
                    w.write(buf_text.encode("utf-8"))
            except OSError as exc:
                self.notify(f"Cannot save {loc.name}: {exc}", severity="warning")
                return False
            return True

        self._remember_active_panel_id()
        self._editor_seq += 1
        content = _FocusableEditorContent(
            initial_text=text, file_path=entry.name, save_handler=_save
        )
        self._mount_maximized_content(
            content, title=f"Edit: {entry.name}", win_id=f"editor-{self._editor_seq}"
        )

    def _open_editor_window(self, path: Path, *, read_only: bool = False) -> None:
        if self.desktop is None:
            return
        self._remember_active_panel_id()
        # Each open assigns a unique id so multiple editor / viewer windows
        # can coexist (including ones currently minimized in the IconTray).
        self._editor_seq += 1
        seq = self._editor_seq
        # F3 on an image → ASCII-art viewer (Pillow opt-in extra). When the
        # extra isn't installed, notify and fall through to the hex/text
        # branch below instead of opening the viewer.
        if read_only and self._looks_image(path):
            if PILLOW_AVAILABLE:
                content = ImageViewerContent(path)
                self._mount_maximized_content(
                    content,
                    title=f"Image: {path.name}",
                    win_id=f"imgviewer-{seq}",
                )
                return
            self.notify(
                "Install dunders[image] to view images as ASCII",
                severity="warning",
            )
        # F3 on a large or binary file → hex viewer with chunked mmap reads.
        # Skip the read_text() pre-load entirely so multi-GB files don't hang
        # the UI thread.
        if read_only and self._should_use_hex_viewer(path):
            content = HexViewerContent(path)
            title = f"Hex: {path.name}"
            win_id = f"hexviewer-{seq}"
        else:
            # EditorContent.__init__ does NOT read the file — it only stores
            # file_path on the buffer for later save. We have to load the
            # text ourselves and feed it as initial_text.
            try:
                text = path.read_text()
            except OSError:
                text = ""
            if read_only and self._looks_csv(path):
                content = CsvViewerContent(initial_text=text, display_name=path.name)
                title = f"CSV: {path.name}"
                win_id = f"csvviewer-{seq}"
            elif read_only:
                content = ViewerContent(initial_text=text, file_path=str(path))
                title = f"View: {path.name}"
                win_id = f"viewer-{seq}"
            else:
                # Editable editor: delegate entirely to the shared helper so
                # _make_editor_window is the single source of truth.
                dw, dh = self.desktop.usable_size.width, self.desktop.usable_size.height
                win = self._make_editor_window(
                    path,
                    position=(0, 0),
                    size=(dw, dh),
                    win_id=f"editor-{seq}",
                    text=text,
                )
                win._saved_rect = (
                    Offset(2, 1), Size(max(1, dw - 4), max(1, dh - 2))
                )
                win.maximized = True
                self.desktop.add_window(win)
                if self._pre_menu_focus is not None or self._pre_menu_window is not None:
                    self._pre_menu_window = win
                    self._pre_menu_focus = None
                return
        self._mount_maximized_content(content, title=title, win_id=win_id)

    def on_file_panel_item_activated(
        self, event: FilePanel.ItemActivated
    ) -> None:
        # Enter / double-click on a file. Directories are handled inside
        # FilePanel.activate() (cwd change), so we only see ItemActivated for
        # non-dir entries. An executable / runnable script is launched in the
        # console; everything else opens in the editor.
        if event.entry.is_dir:
            return
        if not self._is_local_entry(event.entry):
            self._open_member_view(event.entry)  # read-only inside archives
            return
        cmd = self._executable_command(event.entry.path)
        if cmd is not None and self._run_in_console(cmd):
            return
        self._open_editor_window(event.entry.path)

    @staticmethod
    def _read_shebang(path: Path) -> str | None:
        """Return the interpreter line of a `#!`-prefixed file, or None.

        e.g. `#!/usr/bin/env python3\\n` → `/usr/bin/env python3`.
        """
        try:
            with path.open("rb") as f:
                first = f.readline(256)
        except OSError:
            return None
        if not first.startswith(b"#!"):
            return None
        try:
            line = first[2:].decode().strip()
        except UnicodeDecodeError:
            return None
        return line or None

    @classmethod
    def _executable_command(cls, path: Path) -> str | None:
        """Build the shell command that runs `path`, or None if it isn't a
        runnable file.

        Detection (mc-style), in order:
        1. The executable bit is set → run the path directly (the kernel
           honours the shebang or runs the binary).
        2. A `#!` shebang is present → invoke the declared interpreter.
        3. A known script extension → invoke the mapped interpreter.
        """
        try:
            if not path.is_file():
                return None
        except OSError:
            return None
        quoted = shlex.quote(str(path))
        try:
            if os.access(path, os.X_OK):
                return quoted
        except OSError:
            pass
        shebang = cls._read_shebang(path)
        if shebang:
            return f"{shebang} {quoted}"
        interp = _SCRIPT_INTERPRETERS.get(path.suffix.lower())
        if interp:
            return f"{interp} {quoted}"
        return None

    def _run_in_console(self, cmd: str) -> bool:
        """Run `cmd` by handing the real terminal to it (mc/NC-style).

        Full-screen programs (claude, vim, htop, …) need a real terminal:
        cursor addressing, scroll regions, the kitty keyboard protocol, etc.
        dunders's embedded relay console only emulates a thin slice of ANSI, so a
        TUI launched there renders garbage (e.g. Shift+Enter in claude). We
        instead suspend the UI and give the program the real tty via the
        handover layer — exactly what mc does when you press Enter on an
        executable.

        Returns True if dispatched, False if no handover is available (so the
        caller can fall back to opening the editor).
        """
        if self._has_active_modal():
            return False
        if self._ensure_handover() is None:
            return False
        self._run_handover_command(cmd)
        return True

    def _ensure_handover(self) -> TerminalHandover | None:
        """Lazily build the terminal-handover strategy.

        we-mc constructs it at mount; the other modes (fm/we/editor/cli) build
        it on first use so running a program from the panel hands over the real
        terminal everywhere, not just in we-mc.
        """
        if self._handover is None:
            try:
                self._handover = make_handover(self, self.terminal_mode)
            except Exception:
                self._handover = None
        return self._handover

    def _run_mode_chip(self) -> tuple[str, str]:
        """(label, tooltip) for the command-line run-mode chip, reflecting the
        current terminal handover mode."""
        if self.terminal_mode == "suspend":
            return (
                "run: tty",
                "Run mode: suspend — programs get the real terminal directly "
                "(full-screen TUIs like claude work). Click to switch.",
            )
        return (
            "run: relay",
            "Run mode: relay — persistent subshell via a nested PTY (may garble "
            "full-screen TUIs like claude). Click to switch to tty.",
        )

    def on_command_line_run_mode_toggle_requested(
        self, event: CommandLine.RunModeToggleRequested
    ) -> None:
        event.stop()
        self._set_terminal_mode(
            "suspend" if self.terminal_mode == "relay" else "relay"
        )

    def _set_terminal_mode(self, mode: TerminalMode) -> None:
        if mode == self.terminal_mode:
            return
        self.terminal_mode = mode
        # Drop the live handover so the next command rebuilds it in the new
        # mode (relay ↔ suspend pick different strategies in make_handover).
        if self._handover is not None:
            try:
                self._handover.shutdown()
            except Exception:
                pass
            self._handover = None
        if self.command_line is not None:
            label, tip = self._run_mode_chip()
            self.command_line.set_run_mode(label, tooltip=tip)
        self.notify(f"Run mode: {mode}", severity="information")

    def action_close_editor(self) -> None:
        """Esc handler — multiple roles in order of precedence:

        1. No-op while a modal is up (dialog owns Esc).
        2. When Textual focus is on the CommandLine input, move focus to
           the active panel so the user can immediately use F-keys /
           cursor without clicking.
        3. Close the topmost editor/viewer window (text, hex, or image) if
           one is open.
        4. Silent no-op otherwise.
        """
        if self.desktop is None or self._has_active_modal():
            return
        # Esc from CommandLine → return to active panel.
        if self._focused_on_command_line(self.focused):
            win = self._last_focused_panel_window
            if win is not None and win.id in ("panel-left", "panel-right"):
                self._focus_panel(win.id)
            else:
                self._focus_panel("panel-left")
            return
        for win in reversed(list(self.desktop.windows)):
            if isinstance(
                win.content,
                (EditorContent, ViewerContent, HexViewerContent, ImageViewerContent, CsvViewerContent),
            ):
                self.desktop.remove_window(win)
                # on_window_closed isn't fired by remove_window; do the
                # post-close housekeeping inline.
                self._refresh_panels()
                if self._is_panel_mode():
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

    def on_window_minimized(self, event) -> None:
        # Posted by Window when the [_] minimize box is clicked.
        if self.desktop is None:
            return
        self.desktop.minimize_window(event.window)
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
        if self._is_panel_mode():
            target = self._pre_modal_panel_id or "panel-left"
            self._focus_panel(target)
        # Project View exit: if the last visible editor just closed, clear state
        # and restore the normal two-panel split.  The closed window has already
        # been removed from desktop.windows by the framework at this point.
        if self._project_tree_panel_id is not None and not [
            w for w in self.desktop.windows if isinstance(w.content, EditorContent)
        ]:
            self._restore_tree_view_mode()
            self._project_tree_panel_id = None
            for pid in ("panel-left", "panel-right"):
                try:
                    pw = self.desktop.query_one(f"#{pid}", Window)
                except Exception:
                    continue
                if pw not in self.desktop.windows:
                    self.desktop.show_window(pw)
            self._tile_panels()

    def action_chmod(self) -> None:
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        targets = panel.effective_targets()
        if not targets:
            return
        self._remember_active_panel_id()
        if len(targets) == 1:
            label = targets[0].name
        else:
            label = f"<{len(targets)} items>"
        try:
            st = targets[0].stat()
            current_mode = st.st_mode & 0o7777
            full_mode = st.st_mode
        except OSError:
            current_mode = 0o644
            full_mode = 0o100644
        owner_name = ""
        group_name = ""
        try:
            import pwd  # local import: stdlib, only POSIX
            owner_name = pwd.getpwuid(st.st_uid).pw_name
        except (KeyError, OSError, ImportError, NameError):
            pass
        try:
            import grp  # local import: stdlib, only POSIX
            group_name = grp.getgrgid(st.st_gid).gr_name
        except (KeyError, OSError, ImportError, NameError):
            pass
        dialog = ChangeAttributesDialog(
            target_label=label,
            current_mode=current_mode,
            full_st_mode=full_mode,
            owner_name=owner_name,
            group_name=group_name,
            context=ChangeAttributesRequest(targets=targets),
        )
        show_modal(self.desktop, dialog, title="Chmod command", size=(74, 18))
        self.call_after_refresh(dialog.focus_input)

    def on_change_attributes_dialog_submitted(
        self, event: ChangeAttributesDialog.Submitted
    ) -> None:
        ctx = event.dialog.context
        self._close_modal(event.dialog)
        if not isinstance(ctx, ChangeAttributesRequest):
            return
        self._run_chmod(ctx, event.mode)

    def on_change_attributes_dialog_cancelled(
        self, event: ChangeAttributesDialog.Cancelled
    ) -> None:
        self._close_modal(event.dialog)

    def _run_chmod(self, req: ChangeAttributesRequest, mode: int) -> None:
        if self.desktop is None:
            return
        # chmod is essentially instantaneous per file; keep it on the UI
        # thread so the call site stays simple. If a future caller passes
        # thousands of targets we can switch to the run_worker pattern.
        result = chmod_paths(req.targets, mode)
        self._report_op_result("chmod", result)
        self._refresh_panels()

    def action_delete(self) -> None:
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        # Delete works on local files and on members inside a writable archive.
        if panel.cwd_loc.scheme != "file":
            provider = self._vfs_registry.resolve(panel.cwd_loc)
            if "write" not in getattr(provider, "capabilities", frozenset()):
                self._warn_archive_unsupported()
                return
        targets = panel.effective_target_locs()
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
            # Drop the first menu open straight away, as if the user pressed
            # Enter on it — F9 should reveal the menu, not just highlight it.
            self.menu_bar.open_active()

    def action_quit(self) -> None:  # noqa: D401 - overrides Textual's built-in
        # F10 / menu Quit / command-line "10" all land here: confirm first so
        # a stray keypress can't drop the user out of the app.
        if self.desktop is None:
            self.exit()
            return
        if self._has_active_modal():
            return
        confirm = ConfirmDialog(
            prompt="Quit dunders?",
            context=QuitRequest(),
        )
        show_modal(self.desktop, confirm, title="Exit", size=(40, 6))

    # --- focus & menu routing ---------------------------------------------

    def on_window_focus_changed(self, message: WindowFocusChanged) -> None:
        if self.menu_bar is not None:
            self.menu_bar.refresh_for_focus()
            self._recompute_menu_bar()
        # Keep _last_focused_panel_window in sync so F-key routing works
        # when Textual widget focus is on the CommandLine input.
        if message.current is not None and isinstance(
            getattr(message.current, "content", None), FilePanel
        ):
            self._last_focused_panel_window = message.current
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
        # Tray-restore chord: Ctrl+W set _tray_chord_pending. Digits 1..9 are
        # consumed earlier by the priority-bound action_tray_restore_digit
        # (so a focused editor can't swallow them), which clears the flag.
        # If a NON-digit key reaches us while pending, the chord wasn't
        # completed -> cancel it and let the key be handled normally.
        if self._tray_chord_pending:
            self._tray_chord_pending = False
            # Fall through: cancel and let the key be handled normally.

        # Far-style letter routing: typing a printable character while a file
        # panel has focus moves focus to the command line and inserts the
        # character there. Active quick-search (Ctrl+S) on the panel keeps
        # its own keystrokes and pre-empts this path.
        from dunders.fm.file_panel import FilePanel
        focused = self.focused
        if (
            isinstance(focused, FilePanel)
            and not getattr(focused, "_qs_active", False)
            and self.command_line is not None
        ):
            ch = getattr(event, "character", None)
            if ch is not None and len(ch) == 1 and ch.isprintable():
                inp = self.command_line._input
                inp.value = inp.value + ch
                try:
                    inp.cursor_position = len(inp.value)
                except Exception:
                    pass
                self.set_focus(inp)
                event.stop()
                return

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

        if self._is_panel_mode():
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
            # Modal is up — Tab must cycle inside the dialog instead of
            # switching panels. The app-level priority binding ate the
            # key before the dialog could see it; forward to Textual's
            # focus_next which walks the DOM-order chain. ModalWindow's
            # _freeze_siblings stripped can_focus from sibling-window
            # widgets, so focus stays inside the modal.
            try:
                self.screen.focus_next()
            except Exception:
                pass
            return
        focused = self.focused
        if self._focused_inside_search_panel(focused):
            try:
                self.screen.focus_next()
            except Exception:
                pass
            return
        # If focus is on the CommandLine input, Tab moves to the active
        # panel window rather than swapping panels.  This mirrors far's
        # behaviour: Esc returns to cmdline, Tab goes to/from panels.
        if self._focused_on_command_line(focused):
            win = self._last_focused_panel_window
            if win is not None and win.id in ("panel-left", "panel-right"):
                self._focus_panel(win.id)
            else:
                self._focus_panel("panel-left")
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
        if self._has_active_modal():
            # Modal is up — Shift+Tab cycles backwards inside the dialog.
            # See action_focus_other_panel for the rationale.
            try:
                self.screen.focus_previous()
            except Exception:
                pass
            return
        if self.desktop is None:
            return
        if self._focused_inside_search_panel(self.focused):
            try:
                self.screen.focus_previous()
            except Exception:
                pass
            return
        # If focus is on the CommandLine input, Shift+Tab moves to the
        # active panel (mirrors Tab — both keys yield to the panel from
        # the command line, far-style).
        if self._focused_on_command_line(self.focused):
            win = self._last_focused_panel_window
            if win is not None and win.id in ("panel-left", "panel-right"):
                self._focus_panel(win.id)
            else:
                self._focus_panel("panel-left")
            return
        self.desktop.cycle_focus(+1)

    @staticmethod
    def _focused_inside_search_panel(focused) -> bool:
        from dunders.windowing.editor.search_panel import SearchPanel
        node = focused
        while node is not None:
            if isinstance(node, SearchPanel):
                return True
            node = getattr(node, "parent", None)
        return False

    def _focused_on_command_line(self, focused) -> bool:
        """Return True when the focused widget is inside the CommandLine."""
        if self.command_line is None:
            return False
        node = focused
        while node is not None:
            if node is self.command_line:
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

    def action_focus_command_line(self) -> None:
        if self._has_active_modal():
            return
        self._focus_command_line()

    def on_window_copy_box_clicked(self, event) -> None:
        """⧉ title-bar button: copy the panel's directory path to the clipboard."""
        from dunders.fm.file_panel import FilePanel

        win = event.window
        content = getattr(win, "content", None)
        if not isinstance(content, FilePanel):
            return
        path = content.cwd_loc.display()  # full clean URL (not the truncated root)
        # System clipboard (pbcopy/xclip) + OSC 52 fallback for SSH.
        clipboard.copy(path, app=self)
        self.notify(f"copied {path}")

    def action_insert_current_file(self) -> None:
        """Ctrl+N: insert the active panel's current entry into the command
        line — its name for a file/folder, or the current directory path when
        the cursor is on the synthetic ".." parent row.

        Not a priority binding: while quick-search is active the panel handles
        Ctrl+N itself (next match) and stops the event; otherwise it bubbles
        up here.
        """
        if self._has_active_modal() or self.command_line is None:
            return
        panel = self._active_panel()
        if panel is None:
            return
        if not (0 <= panel.cursor < len(panel.entries)):
            return
        entry = panel.entries[panel.cursor]
        raw = str(panel.cwd) if entry.is_parent else entry.path.name
        self.command_line.insert_at_cursor(shlex.quote(raw) + " ")
        self._focus_command_line()

    def _focused_editor_content(self):
        """Return the focused EditorContent, or None."""
        if self.desktop is None:
            return None
        win = self.desktop.focused_window
        content = getattr(win, "content", None) if win is not None else None
        return content if isinstance(content, EditorContent) else None

    def _build_macro_context(self) -> tuple[MacroContext, Path]:
        """Build the macro context + run cwd from the focused editor or panel.

        A focused editor wins: ``_active_panel()`` falls back to ``panel-left``
        whenever it exists (even when the editor has focus), so the editor
        branch must be checked first or its file would never reach the macros.
        """
        content = self._focused_editor_content()
        if content is not None:
            fp = getattr(content, "_file_path", None)
            if fp:
                p = Path(fp)
                ctx = MacroContext(
                    current_file=p.name, tagged=(), panel_dir=str(p.parent),
                    other_file=None, other_dir=None,
                )
                return ctx, p.parent
        panel = self._active_panel()
        if panel is not None:
            cur = None
            if 0 <= panel.cursor < len(panel.entries):
                e = panel.entries[panel.cursor]
                if not e.is_parent:
                    cur = e.path.name
            tagged = tuple(p.name for p in panel.selected_paths())
            other = self._opposite_panel(panel)
            other_file = other_dir = None
            if other is not None:
                other_dir = str(other.cwd)
                if 0 <= other.cursor < len(other.entries):
                    oe = other.entries[other.cursor]
                    if not oe.is_parent:
                        other_file = oe.path.name
            ctx = MacroContext(
                current_file=cur, tagged=tagged, panel_dir=str(panel.cwd),
                other_file=other_file, other_dir=other_dir,
            )
            return ctx, panel.cwd
        cwd = self._panel_cwd_for_test()
        ctx = MacroContext(
            current_file=None, tagged=(), panel_dir=str(cwd),
            other_file=None, other_dir=None,
        )
        return ctx, cwd

    def action_user_menu(self) -> None:
        """F2: open the mc/far-style User Menu (panel or editor)."""
        if self._has_active_modal() or self.desktop is None:
            return
        ctx, _cwd = self._build_macro_context()
        self._user_menu_ctx = ctx
        loaded = load_menu(Path(ctx.panel_dir))
        if not loaded.has_any and not loaded.any_file_exists:
            path = seed_global_menu()
            self._open_editor_window(path, read_only=False)
            return
        self._remember_active_panel_id()
        dialog = UserMenuDialog(
            loaded.rows, default_source=loaded.local_path or loaded.global_path
        )
        show_modal(self.desktop, dialog, title="User menu", size=(60, 18))

    def on_user_menu_dialog_selected(self, event: UserMenuDialog.Selected) -> None:
        self._close_modal(event.dialog)
        ctx = self._user_menu_ctx
        cwd = Path(ctx.panel_dir) if ctx is not None else self._panel_cwd_for_test()
        self._user_menu_pending = {
            "body": event.entry.body,
            "prompts": collect_prompts(event.entry.body),
            "values": {},
            "cwd": cwd,
        }
        self._advance_user_menu_prompts()

    def on_user_menu_dialog_cancelled(self, event: UserMenuDialog.Cancelled) -> None:
        self._close_modal(event.dialog)

    def on_user_menu_dialog_edit_requested(
        self, event: UserMenuDialog.EditRequested
    ) -> None:
        self._close_modal(event.dialog)
        self._open_editor_window(event.source, read_only=False)

    def _advance_user_menu_prompts(self) -> None:
        pend = self._user_menu_pending
        if pend is None or self.desktop is None:
            return
        remaining = [p for p in pend["prompts"] if p not in pend["values"]]
        if not remaining:
            ctx = self._user_menu_ctx or MacroContext(None, (), str(pend["cwd"]), None, None)
            body = expand_macros(pend["body"], ctx, pend["values"])
            self._user_menu_pending = None
            self._run_user_menu_body(body, pend["cwd"])
            return
        label = remaining[0]
        dialog = InputDialog(label, context=UserMenuPromptRequest(label=label))
        show_modal(self.desktop, dialog, title="User menu", size=(50, 5))
        dialog.focus_input()

    def _run_user_menu_body(self, body: str, cwd: Path) -> None:
        body = body.strip()
        if not body or self._ensure_handover() is None:
            return
        self._handover.run_foreground(body, cwd)
        if self.command_history is not None:
            self.command_history.append(body)
        # A `cd` inside the menu entry runs in the foreground shell; follow it
        # with the active panel (mc User-Menu parity), then refresh listings.
        self._follow_handover_cwd(cwd)
        self._refresh_panels()

    def _follow_handover_cwd(self, prev: Path) -> None:
        """If the last foreground command left its shell in a different
        directory than ``prev``, move the active panel there."""
        new_cwd = getattr(self._handover, "last_cwd", None)
        if new_cwd is None:
            return
        try:
            unchanged = new_cwd.resolve() == prev.resolve()
        except OSError:
            unchanged = str(new_cwd) == str(prev)
        if unchanged or not new_cwd.is_dir():
            return
        self._panel_cd(new_cwd)

    def _restore_tray_at(self, index: int) -> None:
        if self._has_active_modal() or self.desktop is None:
            return
        items = self.desktop.minimized_windows
        if 0 <= index < len(items):
            self.desktop.restore_window(items[index])

    def action_tray_chord_start(self) -> None:
        """Begin the Ctrl+W tray-restore chord; next 1..9 restores Nth icon."""
        if self._has_active_modal():
            return
        self._tray_chord_pending = True

    def action_tray_restore_digit(self, n: int) -> None:
        """Second half of the Ctrl+W chord: restore the Nth tray icon."""
        self._tray_chord_pending = False
        self._restore_tray_at(n - 1)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # The 1..9 digit bindings only exist to complete a Ctrl+W chord.
        # Keep them disabled (None -> binding skipped, key falls through to
        # the focused widget) unless a chord is actually pending, so typing
        # digits into an editor / command line works normally.
        if action == "tray_restore_digit":
            return True if self._tray_chord_pending else None
        return True

    # --- CommandLine message handlers -------------------------------------

    def on_command_line_submitted(self, event: CommandLine.Submitted) -> None:
        event.stop()
        # Every typed command runs by handing over the real terminal (mc-style)
        # so full-screen TUIs (claude, vim, htop, …) get a proper terminal
        # instead of the thin relay-console ANSI emulator, which renders them as
        # garbage. we-mc always did this; we/fm/editor/cli now do too.
        self._run_handover_command(event.text)

    def _run_handover_command(self, text: str) -> None:
        """Run a typed command by handing over the real terminal (mc-style)."""
        text = text.strip()
        if not text or self._has_active_modal():
            return
        if text == "cd" or text.startswith("cd "):
            self._handover_cd(text[2:].strip())
            return
        if self._ensure_handover() is None:
            return
        cwd = self._panel_cwd_for_test()
        self._handover.run_foreground(text, cwd)
        if self.command_history is not None:
            self.command_history.append(text)
        # A command that changes directory (e.g. `pushd`, or a script ending in
        # a `cd`) moves the active panel too — same as the literal `cd` above,
        # and as User Menu entries.
        self._follow_handover_cwd(cwd)
        self._refresh_panels()

    def _handover_cd(self, arg: str) -> None:
        """`cd` in we-mc: move the active panel to the target directory."""
        if not arg:
            target = Path.home()
        else:
            target = Path(arg).expanduser()
            if not target.is_absolute():
                target = (self._panel_cwd_for_test() / target).resolve()
        if not target.is_dir():
            self.notify(f"cd: {target}: not a directory", severity="warning")
            return
        err = self._panel_cd(target)
        if err is not None:
            self.notify(f"cd: {err}", severity="warning")

    def on_command_line_cancel_requested(self, event: CommandLine.CancelRequested) -> None:
        event.stop()
        if self.command_runner is not None:
            self.command_runner.cancel_current()

    def on_command_line_eof_requested(self, event: CommandLine.EofRequested) -> None:
        event.stop()
        if self.command_runner is not None:
            self.command_runner.send_eof()

    def on_command_line_kill_requested(self, event: CommandLine.KillRequested) -> None:
        event.stop()
        if self.command_runner is not None:
            self.command_runner.kill_current()

    def _on_console_busy_changed(self, target_id: str, busy: bool) -> None:
        """Hook from CommandRunner; update the cmdline hint to reflect that
        the active console target is running an interactive child."""
        if self.command_line is None:
            return
        # Only react to the target the cmdline is currently routing to.
        active_id = (
            "console-default"
            if self.command_runner is None or self.command_runner._current_target is None
            else f"console-{self.command_runner._current_target}"
        )
        if target_id != active_id:
            return
        self.command_line.set_busy(busy)

    # --- Console helpers --------------------------------------------------

    def _panel_cwd_for_test(self) -> Path:
        """Return the active panel's CWD (live, not just the initial path)."""
        panel = self._active_panel()
        if panel is not None:
            return panel.cwd
        # Fall back to initial_path logic from _panel_cwd
        if self.initial_path is not None:
            return self.initial_path if self.initial_path.is_dir() else self.initial_path.parent
        return Path.cwd()

    def _active_panel_side(self) -> str:
        """Return 'left' or 'right' based on the focused panel's window id."""
        panel = self._active_panel()
        if panel is None:
            return "left"
        node = panel.parent
        while node is not None:
            nid = getattr(node, "id", None)
            if nid == "panel-right":
                return "right"
            if nid == "panel-left":
                return "left"
            node = getattr(node, "parent", None)
        return "left"

    def _panel_cd(self, path: Path) -> str | None:
        """Change the active panel's CWD. Returns error string or None."""
        if not path.exists():
            return f"{path}: No such file or directory"
        if not path.is_dir():
            return f"{path}: Not a directory"
        side = self._active_panel_side()
        self.set_panel_cwd(side, path)
        return None

    def set_panel_cwd(self, side: str, path: Path) -> None:
        """Change the CWD of the named panel ('left' or 'right')."""
        panel_id = "panel-left" if side == "left" else "panel-right"
        if self.desktop is None:
            return
        try:
            win = self.desktop.query_one(f"#{panel_id}", Window)
        except Exception:
            return
        panel = win.content
        if isinstance(panel, FilePanel):
            panel._change_cwd(path)

    def _create_console_content(self, target_id: str) -> ConsoleContent:
        content = ConsoleContent(window_id=target_id)
        self._mount_console_window(content)
        return content

    def _mount_console_window(self, content: ConsoleContent) -> None:
        if self.desktop is None:
            return
        w = make_window(
            content,
            title="",
            position=(0, 0),
            size=(40, 8),
            border_focused=BorderStyle.NONE,
            border_unfocused=BorderStyle.NONE,
            id=f"win-{content.id}",
        )
        self.desktop.add_window(w)
        if content.id == "console-default":
            self._console_default_window = w
        self._fit_console_window(w)

    def _fit_console_window(self, w: Window) -> None:
        """Place a console window to occupy the bottom half of the desktop."""
        if self.desktop is None:
            return
        W, H = self.desktop.usable_size
        if W <= 0 or H <= 0:
            return
        h = max(3, H // 2)
        w.styles.offset = Offset(0, max(0, H - h))
        w.styles.width = W
        w.styles.height = h

    def _ensure_console_visible(self) -> bool:
        """Re-surface the default console window if it was stashed away.

        After ``action_panels_fullscreen`` (Ctrl+P) the console is minimized to
        the tray. Running a command or toggling the console must bring it back
        into the visible split. No-op when the console is absent or already
        visible. Restores the bottom-half split layout. Returns True when it
        actually restored the console.
        """
        if self.desktop is None:
            return False
        win = self._console_default_window
        if win is None or win in self.desktop.windows:
            return False
        self.desktop.show_window(win)
        self._fit_console_window(win)
        self._tile_panels()
        return True

    def action_toggle_console(self) -> None:
        """Ctrl+O: panel layouts drop into the mc-style command screen; other
        modes toggle the embedded console window.

        Every typed command already runs through the handover (full-screen,
        real tty), so in fm/we-mc the embedded console window would only ever be
        empty — Ctrl+O there hands the real terminal to the live subshell
        instead, matching Midnight Commander.
        """
        if self._is_panel_mode():
            handover = self._ensure_handover()
            if handover is not None:
                cwd = self._panel_cwd_for_test()
                handover.command_screen(cwd)
                # A reattached command that finished (or a subshell `cd`) may have
                # moved the shell; follow it and refresh listings (mc parity).
                self._follow_handover_cwd(cwd)
                self._refresh_panels()
            return
        # Lazily create the console-default window if it doesn't exist yet.
        self.console_registry.get_or_create(None)
        win = self._console_default_window
        if win is None or self.manager is None or self.desktop is None:
            return
        # If the console was stashed (e.g. by Ctrl+P panels-fullscreen), bring
        # it back into the visible stack before toggling its maximize state.
        if win not in self.desktop.windows:
            self._ensure_console_visible()
        if win.maximized:
            self.manager.toggle_maximize(win)
            if self._pre_console_focus is not None:
                try:
                    self.desktop.focus_window(self._pre_console_focus)
                except Exception:
                    pass
                self._pre_console_focus = None
        else:
            self._pre_console_focus = self.desktop.focused_window
            self.manager.toggle_maximize(win)
            try:
                self.desktop.focus_window(win)
            except Exception:
                pass
            self._focus_command_line()

    def _on_command_submitted_for_test(self, text: str, *, anonymous: bool) -> None:
        """Test entry point: execute a command as if typed into CommandLine."""
        if self.command_runner is None:
            return
        self.command_runner.execute(text, anonymous=anonymous)
