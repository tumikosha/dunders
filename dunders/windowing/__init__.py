"""dunders.windowing — Turbo Vision-inspired windowing framework for Textual.

Public API:

    from dunders.windowing import (
        Window, Desktop, WindowManager,
        BorderStyle, BorderSides, TitleSpec, Decorations,
        WindowContent, Theme, Palette, Style,
        make_window, show_modal,
    )
"""

from .commands import (
    CommandDispatcher,
    CommandRegistry,
    CommandRouter,
    CommandSource,
    ResolvedCommand,
)
from .command_palette import CommandPaletteContent, show_command_palette
from .content import CommandsChanged, WindowCommand, WindowContent
from .desktop import Desktop, IconTray, WindowFocusChanged
from .frame import BorderSides, BorderStyle, Decorations, TitleSpec
from .helpers import make_window, show_modal
from .manager import WindowManager, default_bindings
from .menu_bar import Dropdown, Menu, MenuBar, MenuItem, MenuSeparator
from .status_bar import StatusBar, StatusItem
from .palette import (
    BackgroundPattern,
    DotBackground,
    GridBackground,
    Palette,
    SolidBackground,
    Style,
    Theme,
)
from .themes import list_themes, load_theme, modern_dark, resolve_theme_path, theme_registry
from .window import Window
from .editor import EditorWidget, EditorContent, MacroAssignDialog
from .tree import TreeContent, TreeViewWidget, JsonYamlTreeContent
from .core.tree_model import TreeNode
from .core import (
    TextBuffer,
    FoldEngine,
    FoldRegistry,
    FoldRule,
    FoldRegion,
    IndentFoldRule,
    MacroRecorder,
    MacroStorage,
    MacroAction,
)

__all__ = [
    # core
    "Window",
    "Desktop",
    "IconTray",
    "WindowManager",
    "default_bindings",
    # frame / content
    "BorderStyle",
    "BorderSides",
    "TitleSpec",
    "Decorations",
    "WindowContent",
    "WindowCommand",
    "WindowFocusChanged",
    "CommandsChanged",
    # commands
    "CommandRegistry",
    "CommandDispatcher",
    "CommandRouter",
    "CommandSource",
    "ResolvedCommand",
    "CommandPaletteContent",
    "show_command_palette",
    # palette / themes
    "Theme",
    "Palette",
    "Style",
    "BackgroundPattern",
    "SolidBackground",
    "DotBackground",
    "GridBackground",
    "modern_dark",
    "load_theme",
    "list_themes",
    "resolve_theme_path",
    "theme_registry",
    # helpers
    "make_window",
    "show_modal",
    # menu
    "MenuBar",
    "Menu",
    "MenuItem",
    "MenuSeparator",
    "Dropdown",
    # status bar
    "StatusBar",
    "StatusItem",
    # editor widgets
    "EditorWidget",
    "EditorContent",
    "TreeContent",
    "TreeViewWidget",
    "JsonYamlTreeContent",
    "TreeNode",
    "MacroAssignDialog",
    # core
    "TextBuffer",
    "FoldEngine",
    "FoldRegistry",
    "FoldRule",
    "FoldRegion",
    "IndentFoldRule",
    "MacroRecorder",
    "MacroStorage",
    "MacroAction",
]
