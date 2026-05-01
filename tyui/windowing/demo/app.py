"""Demo app: python -m tyui.windowing.demo

Showcases: borders, titles, decorations, focus cycling, tile/cascade/maximize,
hide/minimize/restore, theme switching, modal dialogs, icon-tray.
"""

from __future__ import annotations

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding

from tyui.windowing import (
    BorderSides,
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
    TitleSpec,
    WindowCommand,
    WindowFocusChanged,
    WindowManager,
    default_bindings,
    list_themes,
    make_window,
    show_command_palette,
    show_modal,
)
from tyui.windowing.demo.contents import (
    FormContent,
    LabelContent,
    ListContent,
    LogContent,
    TextAreaContent,
)
from tyui.windowing.editor import EditorContent
from tyui.windowing.helpers import ModalWindow


HELP_TEXT = """\
tyui.windowing — demo

Menu bar:
  F9 / Ctrl+Q      - open menu bar
  Left/Right       - cycle menus
  Enter / Down     - open dropdown
  Up/Down          - navigate items
  Esc              - close

Navigation:
  Tab / Shift+Tab  - cycle focus
  Click            - focus window + raise

Window:
  Ctrl+W           - close focused
  Ctrl+H           - hide focused
  _                - minimize to icon-tray
  F5               - maximize toggle
  Shift+F5 arrows  - move mode (Enter/Esc to exit)
  Shift+F7 arrows  - resize mode

Layouts:
  F7               - tile horizontal
  F8               - tile vertical
  Ctrl+Alt+G       - tile grid
  Ctrl+Alt+C       - cascade

Exit:
  F10              - quit application

Demo hotkeys:
  F1               - this help
  F2               - cycle theme
  F3               - new window
  F4               - close focused
  Esc              - dismiss modal / exit move-resize mode

Press Esc to close this dialog.
"""


class WindowingDemo(App):
    TITLE = "tyui.windowing demo"

    CSS = """
    Screen { background: $panel; }
    Desktop { margin-top: 1; margin-bottom: 1; }
    """

    BINDINGS = [
        # Static bindings only for things that are NOT regular commands —
        # they need framework-level hooks (focus chain, modal chain) that
        # the dispatcher cannot easily express. Everything else (F1-F8,
        # tile/cascade, hide/min/max, editor hotkeys, ...) is now routed
        # dynamically via CommandRouter against the focused window.
        Binding("f9,ctrl+q", "menu", "Menu", show=True),
        Binding("tab", "focus_next", "Next", show=False),
        Binding("shift+tab", "focus_prev", "Prev", show=False),
        Binding("ctrl+w", "close_focused", "Close", show=False),
        Binding("escape", "escape", "Esc", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.desktop: Desktop | None = None
        self.manager: WindowManager | None = None
        self.menu_bar: MenuBar | None = None
        self.status_bar: StatusBar | None = None
        self._active_dropdown: Dropdown | None = None
        self._theme_names: list[str] = []
        self._theme_index = 0
        self._new_counter = 0
        self._editor_content: EditorContent | None = None
        self._macros: dict[str, list] = {}
        self.command_registry: CommandRegistry = CommandRegistry()
        self.dispatcher: CommandDispatcher | None = None
        self.router: CommandRouter | None = None

    def compose(self) -> ComposeResult:
        self.menu_bar = MenuBar()
        self.desktop = Desktop(theme_name="modern_dark")
        self.status_bar = StatusBar(items=[
            StatusItem("F1", "Help", self.action_help),
            StatusItem("F2", "Theme", self.action_cycle_theme),
            StatusItem("F3", "New", self.action_new_window),
            StatusItem("F4", "Close", self.action_close_focused),
            StatusItem("F9", "Menu", self.action_menu),
            StatusItem("F10", "Quit", self.exit),
        ])
        yield self.menu_bar
        yield self.desktop
        yield self.status_bar

    def on_mount(self) -> None:
        self.manager = WindowManager(self.desktop)
        self._theme_names = list_themes()
        # Sync index with the currently active theme so F2 actually cycles forward.
        current = self.desktop.palette.theme.name
        if current in self._theme_names:
            self._theme_index = self._theme_names.index(current)
        self.dispatcher = CommandDispatcher(self.desktop, self.command_registry)
        self.router = CommandRouter(self.dispatcher)
        self._register_app_commands()
        self.menu_bar.bind_dispatcher(self.dispatcher)
        self._build_menus()
        self._build_scene()
        self.set_focus(None)

    # --- command registry -------------------------------------------------

    def _register_app_commands(self) -> None:
        """Register app-level (focus-independent) commands.

        Editor-scoped commands (save/find/split/fold/macro) come from
        ``EditorContent.get_commands()`` and are picked up automatically when
        the editor window has focus.
        """
        m = self.manager
        cmds = [
            WindowCommand(id="app.help", label="Shortcuts", handler=self.action_help, hotkey="f1"),
            WindowCommand(id="app.cycle_theme", label="Cycle theme", handler=self.action_cycle_theme, hotkey="f2"),
            WindowCommand(id="app.new_window", label="New window", handler=self.action_new_window, hotkey="f3"),
            WindowCommand(id="app.close_focused", label="Close window", handler=self.action_close_focused, hotkey="f4"),
            WindowCommand(id="app.quit", label="Exit", handler=self.exit, hotkey="f10"),
            WindowCommand(id="window.hide", label="Hide", handler=lambda: m.hide_focused(), hotkey="ctrl+h"),
            WindowCommand(id="window.minimize", label="Minimize", handler=lambda: m.minimize_focused(), hotkey="underscore", hotkey_label="_"),
            WindowCommand(id="window.maximize", label="Maximize", handler=lambda: m.maximize_focused(), hotkey="f5"),
            WindowCommand(id="window.move_mode", label="Move mode", handler=lambda: m.enter_move_mode(), hotkey="shift+f5"),
            WindowCommand(id="window.resize_mode", label="Resize mode", handler=lambda: m.enter_resize_mode(), hotkey="shift+f7"),
            WindowCommand(id="view.tile_h", label="Tile horizontal", handler=lambda: m.tile_horizontal(), hotkey="f7"),
            WindowCommand(id="view.tile_v", label="Tile vertical", handler=lambda: m.tile_vertical(), hotkey="f8"),
            WindowCommand(id="view.tile_grid", label="Tile grid", handler=lambda: m.tile_grid(), hotkey="ctrl+alt+g"),
            WindowCommand(id="view.cascade", label="Cascade", handler=lambda: m.cascade(), hotkey="ctrl+alt+c"),
            # demo-specific composite commands that operate on the demo's
            # singleton _editor_content (legacy behaviour kept for parity).
            # Only commands without a focus-scope counterpart need a hotkey
            # here — split_h is already declared by EditorContent and wins
            # via focus when the editor is focused.
            WindowCommand(id="demo.smart_fold", label="Fold All", handler=self.action_smart_fold, hotkey="ctrl+right_square_bracket"),
            WindowCommand(id="demo.toggle_fold", label="Toggle Fold", handler=self.action_toggle_fold_local),
            WindowCommand(id="demo.split_h", label="Split Horizontal", handler=self.action_split_h),
            WindowCommand(id="demo.split_v", label="Split Vertical", handler=self.action_split_v, hotkey="ctrl+alt+backslash"),
            WindowCommand(id="demo.toggle_macro", label="Record Macro", handler=self.action_toggle_macro, hotkey="alt+m"),
            WindowCommand(id="palette.open", label="Command Palette", handler=self.action_open_palette, hotkey="ctrl+p"),
        ]
        self.command_registry.register_many(cmds)

    def action_open_palette(self) -> None:
        if self.dispatcher is None or self.desktop is None:
            return
        show_command_palette(self.desktop, self.dispatcher)

    def on_command_palette_content_picked(self, message) -> None:
        # Close the modal first so the dispatched command runs against the
        # restored focused window, not the palette modal.
        win = self._modal_for(message.palette)
        if win is not None and self.desktop is not None:
            self.desktop.remove_window(win)
        if self.dispatcher is not None:
            self.dispatcher.dispatch(message.command.id)
        message.stop()

    def on_command_palette_content_dismissed(self, message) -> None:
        win = self._modal_for(message.palette)
        if win is not None and self.desktop is not None:
            self.desktop.remove_window(win)
        message.stop()

    def _modal_for(self, content):
        node = getattr(content, "parent", None)
        while node is not None:
            if isinstance(node, ModalWindow):
                return node
            node = getattr(node, "parent", None)
        return None

    # --- menus ------------------------------------------------------------

    def _build_menus(self) -> None:
        # All entries route through the dispatcher; labels/hotkeys/enabled
        # state come from the registered WindowCommand. Editor-scoped pieces
        # (Save/Find/Split…) are picked up automatically when the editor
        # window is focused — they live on EditorContent.get_commands().
        self.menu_bar.menus = [
            Menu("File", [
                MenuItem(command_id="app.new_window"),
                MenuItem(command_id="app.close_focused"),
                MenuSeparator(),
                MenuItem(command_id="app.quit"),
            ]),
            Menu("Edit", [
                # Focus-scope (lit only when editor has focus):
                MenuItem(command_id="save"),
                MenuItem(command_id="find"),
                MenuSeparator(),
                # demo composite (smart fold / toggle):
                MenuItem(command_id="demo.smart_fold", hotkey="Ctrl+] (empty line)"),
                MenuItem(command_id="demo.toggle_fold", hotkey="Ctrl+]"),
                MenuSeparator(),
                MenuItem(command_id="demo.split_h", hotkey="Ctrl+\\"),
                MenuItem(command_id="demo.split_v", hotkey="Ctrl+Alt+\\"),
                MenuSeparator(),
                MenuItem(command_id="demo.toggle_macro", hotkey="Alt+M"),
            ]),
            Menu("Window", [
                MenuItem(command_id="window.hide"),
                MenuItem(command_id="window.minimize"),
                MenuItem(command_id="window.maximize"),
                MenuSeparator(),
                MenuItem(command_id="window.move_mode"),
                MenuItem(command_id="window.resize_mode"),
            ]),
            Menu("View", [
                MenuItem(command_id="view.tile_h"),
                MenuItem(command_id="view.tile_v"),
                MenuItem(command_id="view.tile_grid"),
                MenuItem(command_id="view.cascade"),
                MenuSeparator(),
                MenuItem(command_id="app.cycle_theme"),
            ]),
            Menu("Help", [
                MenuItem(command_id="app.help"),
            ]),
        ]
        self.menu_bar.refresh()

    # --- scene ------------------------------------------------------------

    def _build_scene(self) -> None:
        d = self.desktop
        # Welcome: left-aligned title, double/single.
        w1 = make_window(
            LabelContent(
                "Welcome to tyui.windowing!\n\n"
                "This is a Turbo Vision-inspired window\n"
                "framework for Textual.\n\n"
                "Press F1 to see shortcuts.",
                title="Welcome",
            ),
            title=TitleSpec(text="Welcome", align="left"),
            position=(2, 1),
            size=(38, 12),
            border_focused=BorderStyle.DOUBLE,
            border_unfocused=BorderStyle.SINGLE,
            decorations=Decorations(close_box=True, zoom_box=True),
        )
        d.add_window(w1)

        # Edit me: heavy/single, centered title.
        w2 = make_window(
            TextAreaContent(
                initial="# try editing this\nprint('hello')\n",
                title="Edit me",
            ),
            title=TitleSpec(text="Edit me", align="center"),
            position=(42, 1),
            size=(34, 10),
            border_focused=BorderStyle.HEAVY,
            border_unfocused=BorderStyle.SINGLE,
            decorations=Decorations(close_box=True, zoom_box=True),
        )
        d.add_window(w2)

        # Files: single/none, right-aligned.
        w3 = make_window(
            ListContent(
                ["main.py", "editor.py", "buffer.py", "frame.py", "window.py"],
                title="files",
            ),
            title=TitleSpec(text="files", align="right"),
            position=(2, 14),
            size=(30, 8),
            border_focused=BorderStyle.SINGLE,
            border_unfocused=BorderStyle.NONE,
            decorations=Decorations(),
        )
        d.add_window(w3)

        # Log: rounded, resize-grip.
        log = LogContent(title="Events")
        w4 = make_window(
            log,
            title=TitleSpec(text="Events", align="left"),
            position=(34, 14),
            size=(42, 8),
            border_focused=BorderStyle.ROUNDED,
            border_unfocused=BorderStyle.SINGLE,
            decorations=Decorations(resize_grip=True),
        )
        d.add_window(w4)

        # Editor with folding: showcases EditorContent
        editor_text = '''def greet(name):
    """Say hello."""
    if name:
        print(f"Hello, {name}!")
    else:
        print("Hello, world!")

class Calculator:
    def add(self, a, b):
        return a + b

    def multiply(self, a, b):
        return a * b
'''
        self._editor_content = EditorContent(
            initial_text=editor_text,
            title="Editor",
            enable_folding=True,
            macro_storage_path=".omc/editor-macros",
        )
        w5 = make_window(
            self._editor_content,
            title=TitleSpec(text="Editor (with folding)", align="left"),
            position=(2, 23),
            size=(50, 12),
            border_focused=BorderStyle.DOUBLE,
            border_unfocused=BorderStyle.SINGLE,
            decorations=Decorations(close_box=True, zoom_box=True, resize_grip=True),
        )
        d.add_window(w5)

        # Help modal shown at start.
        self.call_after_refresh(self._show_help)

    # --- actions ----------------------------------------------------------

    def _show_help(self) -> None:
        modal = show_modal(self.desktop, LabelContent(HELP_TEXT), title="Help", size=(52, 18))

    def action_help(self) -> None:
        self._show_help()

    def action_cycle_theme(self) -> None:
        if not self._theme_names:
            return
        self._theme_index = (self._theme_index + 1) % len(self._theme_names)
        name = self._theme_names[self._theme_index]
        self.desktop.set_theme(name)

    def action_fold_all(self) -> None:
        if not self._editor_content:
            return
        editor = self._editor_content._editor
        any_collapsed = any(r.collapsed for r in editor._fold_regions)
        if any_collapsed:
            editor.unfold_all()
        else:
            editor.fold_all()

    def action_toggle_fold_local(self) -> None:
        if not self._editor_content:
            return
        self._editor_content._editor.toggle_fold_at_cursor()

    def action_split_h(self) -> None:
        if not self._editor_content:
            return
        self._editor_content.toggle_split("horizontal")

    def action_split_v(self) -> None:
        if not self._editor_content:
            return
        self._editor_content.toggle_split("vertical")

    def action_toggle_macro(self) -> None:
        """Alt+M: start/stop macro recording on the editor."""
        if not self._editor_content:
            return
        rec = self._editor_content._macro_recorder
        if rec is None:
            self.notify("Macros are not enabled", severity="warning")
            return
        editor = self._editor_content._editor
        if rec.is_recording:
            actions = rec.stop_recording()
            editor.macro_skip_keys.discard("alt+m")
            if not actions:
                self.notify("No actions recorded")
                return
            self._prompt_assign_macro(actions)
        else:
            editor.macro_skip_keys.add("alt+m")
            rec.start_recording()
            self.notify("Recording macro — press Alt+M to stop")

    def _prompt_assign_macro(self, actions: list) -> None:
        from tyui.windowing.editor.macro_dialog import MacroAssignDialog

        def _on_result(result) -> None:
            if result is None:
                self.notify("Macro discarded")
                return
            key, permanent = result
            name = f"macro_{key}"
            storage = self._editor_content._macro_storage
            if storage is not None:
                storage.save_macro(name, key, actions, permanent)
            self._macros[key] = actions
            try:
                self.bind(key, f"replay_macro('{key}')", description=f"Macro {key}")
            except Exception:
                pass
            self.notify(f"Macro assigned to {key}")

        self.push_screen(MacroAssignDialog(action_count=len(actions)), _on_result)

    def action_replay_macro(self, key: str) -> None:
        actions = self._macros.get(key)
        if not actions or not self._editor_content:
            return
        editor = self._editor_content._editor
        editor.focus()
        for a in actions:
            if a.kind != "keypress":
                continue
            k, _, char = a.data.partition("|")
            editor.simulate_keypress(k, char or None)

    def action_smart_fold(self) -> None:
        """Ctrl+]: toggle all folds on empty line, else toggle fold at cursor."""
        if not self._editor_content:
            return
        editor = self._editor_content._editor
        buf = editor.buffer
        row = buf.cursor_row
        line = buf.lines[row] if 0 <= row < len(buf.lines) else ""
        if line.strip() == "":
            self.action_fold_all()
        else:
            editor.toggle_fold_at_cursor()

    def action_new_window(self) -> None:
        self._new_counter += 1
        w = make_window(
            LabelContent(f"I'm window #{self._new_counter}", title=f"win {self._new_counter}"),
            title=f"win {self._new_counter}",
            position=(10 + self._new_counter * 2, 2 + self._new_counter),
            size=(30, 8),
            decorations=Decorations(close_box=True, zoom_box=True),
        )
        self.desktop.add_window(w)

    def action_close_focused(self) -> None:
        self.manager.close_focused()

    def action_hide_focused(self) -> None:
        self.manager.hide_focused()

    def action_minimize_focused(self) -> None:
        self.manager.minimize_focused()

    def action_maximize_focused(self) -> None:
        self.manager.maximize_focused()

    def action_focus_next(self) -> None:
        self.manager.focus_next()

    def action_focus_prev(self) -> None:
        self.manager.focus_prev()

    def action_tile_h(self) -> None:
        self.manager.tile_horizontal()

    def action_tile_v(self) -> None:
        self.manager.tile_vertical()

    def action_tile_grid(self) -> None:
        self.manager.tile_grid()

    def action_cascade(self) -> None:
        self.manager.cascade()

    def action_enter_move(self) -> None:
        self.manager.enter_move_mode()

    def action_enter_resize(self) -> None:
        self.manager.enter_resize_mode()

    def action_escape(self) -> None:
        if self._active_dropdown is not None:
            self._close_dropdown()
            return
        if self.menu_bar is not None and self.menu_bar.active_index is not None:
            self.menu_bar.deactivate()
            return
        if self.manager.mode != "normal":
            self.manager.exit_mode()
            return
        # Dismiss the top-most modal, if any.
        stack = getattr(self.desktop, "_modal_stack", [])
        if stack:
            modal = stack[-1]
            modal.action_dismiss()

    def action_menu(self) -> None:
        """Open the menu bar and highlight the first entry."""
        if self.menu_bar is None:
            return
        if self.menu_bar.active_index is None:
            # Remember where focus was so we can return there on close.
            self._pre_menu_focus = self.focused
            self.menu_bar.activate(0)
            self.menu_bar.focus()
        else:
            self.menu_bar.deactivate()
            self._restore_focus_after_menu()

    def _restore_focus_after_menu(self) -> None:
        prev = getattr(self, "_pre_menu_focus", None)
        self._pre_menu_focus = None
        if prev is not None and prev.is_mounted:
            prev.focus()
            return
        if self.desktop.focused_window is not None:
            try:
                self.desktop.focused_window.content.focus()
            except Exception:
                pass

    # --- focus events -----------------------------------------------------

    def on_window_focus_changed(self, message: WindowFocusChanged) -> None:
        if self.menu_bar is not None:
            self.menu_bar.refresh_for_focus()
        message.stop()

    def on_commands_changed(self, message) -> None:
        if self.menu_bar is not None:
            self.menu_bar.refresh_for_focus()
        try:
            message.stop()
        except Exception:
            pass

    # --- menu-bar event routing -------------------------------------------

    def on_menu_bar_open_requested(self, message: MenuBar.OpenRequested) -> None:
        self._open_dropdown(message.index)
        message.stop()

    def _open_dropdown(self, index: int) -> None:
        if self._active_dropdown is not None:
            # Close current before opening another (user cycled via bar).
            self._active_dropdown.remove()
            self._active_dropdown = None
        menu = self.menu_bar.menus[index]
        # Work out x position of this menu label on the bar.
        spans = self.menu_bar._menu_spans()
        start_x = spans[index][1] if index < len(spans) else 0
        # Dropdown lives on Desktop (so offset is relative to Desktop's top-left,
        # which sits at y=1 below the menu-bar).
        dd = Dropdown(
            menu.items,
            position=(start_x, 0),
            palette=self.desktop.palette,
            dispatcher=self.dispatcher,
        )
        self.desktop.mount(dd)
        self._active_dropdown = dd
        # Ensure dropdown has focus (redundant with on_mount, but bulletproof)
        def force_focus():
            if dd.is_mounted:
                self.set_focus(dd)
        self.call_later(force_focus)

    def _close_dropdown(self) -> None:
        dd = self._active_dropdown
        if dd is None:
            return
        self._active_dropdown = None
        if dd.is_mounted:
            dd.remove()
        if self.menu_bar is not None:
            self.menu_bar.deactivate()
        self._restore_focus_after_menu()

    def on_dropdown_item_chosen(self, message: Dropdown.ItemChosen) -> None:
        self._close_dropdown()
        message.stop()

    def on_dropdown_dismissed(self, message: Dropdown.Dismissed) -> None:
        self._close_dropdown()
        message.stop()

    def on_dropdown_cycle_requested(self, message: Dropdown.CycleRequested) -> None:
        # Close current dropdown and open adjacent menu
        if self._active_dropdown is not None:
            self._active_dropdown.remove()
            self._active_dropdown = None
        if self.menu_bar is not None and self.menu_bar.menus:
            current = self.menu_bar.active_index or 0
            new_index = (current + message.direction) % len(self.menu_bar.menus)
            self.menu_bar.active_index = new_index
            self._open_dropdown(new_index)
        message.stop()

    # --- modal routing ----------------------------------------------------

    def on_modal_window_dismissed(self, message: ModalWindow.Dismissed) -> None:
        # Remove the dismissed modal and un-dim siblings.
        modal = message.window
        if hasattr(self.desktop, "_modal_stack"):
            try:
                self.desktop._modal_stack.remove(modal)
            except ValueError:
                pass
        self.desktop.remove_window(modal)
        for w in self.desktop.windows:
            w.palette_override.pop("window.border.unfocused", None)
            w.refresh()
        message.stop()

    # --- keyboard: move/resize modes --------------------------------------

    def on_key(self, event: events.Key) -> None:
        # Route menu navigation keys to active dropdown only when menu context is active
        dd = self._active_dropdown
        menu_active = (
            dd is not None
            and dd.is_mounted
            and (dd.has_focus or (self.menu_bar and self.menu_bar.has_focus))
        )
        if menu_active:
            if event.key == "up":
                dd.move_highlight(-1)
                event.stop()
                return
            elif event.key == "down":
                dd.move_highlight(1)
                event.stop()
                return
            elif event.key == "left":
                dd.post_message(Dropdown.CycleRequested(dd, -1))
                event.stop()
                return
            elif event.key == "right":
                dd.post_message(Dropdown.CycleRequested(dd, 1))
                event.stop()
                return
            elif event.key == "enter":
                dd.choose_current()
                event.stop()
                return
            elif event.key == "escape":
                dd.dismiss()
                event.stop()
                return

        if self.manager.mode == "move":
            deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
            if event.key in deltas:
                dx, dy = deltas[event.key]
                self.manager.move_mode_step(dx, dy)
                event.stop()
            elif event.key in ("enter", "escape"):
                self.manager.exit_mode()
                event.stop()
        elif self.manager.mode == "resize":
            deltas = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
            if event.key in deltas:
                dw, dh = deltas[event.key]
                self.manager.resize_mode_step(dw, dh)
                event.stop()
            elif event.key in ("enter", "escape"):
                self.manager.exit_mode()
                event.stop()
        elif self.router is not None:
            # Last-resort dynamic hotkey routing: try the focused window's
            # commands, then the app-level registry.
            if self.router.handle_key(event.key):
                event.stop()


if __name__ == "__main__":
    WindowingDemo().run()
