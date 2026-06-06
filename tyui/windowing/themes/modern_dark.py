"""Single built-in theme: neutral dark. Example user themes live as TOML files."""

from __future__ import annotations

from ..frame import BorderStyle
from ..palette import SolidBackground, Style, Theme

modern_dark = Theme(
    name="modern_dark",
    styles={
        # Desktop
        "desktop.background":         Style(bg="#1c1c1c"),
        "desktop.pattern":             Style(fg="#2a2a2a", bg="#1c1c1c"),
        # Window borders
        "window.border.focused":       Style(fg="#ffffff", bg="#1c1c1c", bold=True),
        "window.border.unfocused":     Style(fg="#5f5f5f", bg="#1c1c1c"),
        # Titles
        "window.title.focused":        Style(fg="#ffffff", bg="#1c1c1c", bold=True),
        "window.title.unfocused":      Style(fg="#8a8a8a", bg="#1c1c1c"),
        "window.subtitle":             Style(fg="#a0a0a0", bg="#1c1c1c"),
        # Inner content default (only used when content sets no colour itself)
        "window.content":              Style(fg="#d0d0d0", bg="#262626"),
        # File-panel per-type colours (foreground layered on the row base)
        "panel.file.dir":              Style(fg="#5fafff", bold=True),
        "panel.file.symlink":          Style(fg="#00d7d7"),
        "panel.file.executable":       Style(fg="#5fd75f", bold=True),
        "panel.file.archive":          Style(fg="#ff5f5f"),
        "panel.file.image":            Style(fg="#d787ff"),
        "panel.file.media":            Style(fg="#d75fd7"),
        "panel.file.document":         Style(fg="#ffd787"),
        "panel.file.source":           Style(fg="#dcdcaa"),
        "panel.file.config":           Style(fg="#87afaf"),
        "panel.file.hidden":           Style(fg="#8a8a8a"),
        # Decorations
        "decoration.close_box":        Style(fg="#ff5555", bg="#1c1c1c", bold=True),
        "decoration.zoom_box":         Style(fg="#50fa7b", bg="#1c1c1c", bold=True),
        "decoration.resize_grip":      Style(fg="#a0a0a0", bg="#1c1c1c"),
        "decoration.number":           Style(fg="#a0a0a0", bg="#1c1c1c"),
        # Icon tray
        "icon_tray.background":        Style(bg="#0f0f0f"),
        "icon.normal":                 Style(fg="#d0d0d0", bg="#0f0f0f"),
        "icon.hover":                  Style(fg="#1c1c1c", bg="#d0d0d0", bold=True),
        # Modal dim overlay
        "modal.overlay":               Style(bg="#000000", dim=True),
        # Menu bar
        "menu.bar":                    Style(fg="#d0d0d0", bg="#0f0f0f"),
        "menu.item":                   Style(fg="#d0d0d0", bg="#0f0f0f"),
        "menu.item.active":            Style(fg="#0f0f0f", bg="#d0d0d0", bold=True),
        "menu.hotkey":                 Style(fg="#ff8c8c", bg="#0f0f0f"),
        "menu.dropdown.border":        Style(fg="#a0a0a0", bg="#262626"),
        "menu.separator":              Style(fg="#5f5f5f", bg="#262626"),
        # Status bar
        "statusbar_bg":                Style(fg="#d0d0d0", bg="#0f0f0f"),
        "statusbar_key":               Style(fg="#0f0f0f", bg="#00afaf", bold=True),
        "statusbar_label":             Style(fg="#d0d0d0", bg="#0f0f0f"),
        # Editor
        "editor.cursor":               Style(reverse=True),
        "editor.selection":            Style(bg="#264f78"),
        "editor.selection_cursor":     Style(bg="#264f78", reverse=True),
        "editor.search_match":         Style(bg="#806000"),
        "editor.search_current":       Style(bg="#ff8c00"),
        "editor.fold_marker":          Style(fg="#d18616", bold=True),
        "editor.line_numbers":         Style(fg="#5f5f5f"),
        # Editor — syntax highlighting (base roles)
        "editor.syntax.keyword":       Style(fg="#c586c0"),
        "editor.syntax.name":          Style(fg="#9cdcfe"),
        "editor.syntax.function":      Style(fg="#dcdcaa"),
        "editor.syntax.class":         Style(fg="#4ec9b0"),
        "editor.syntax.string":        Style(fg="#ce9178"),
        "editor.syntax.number":        Style(fg="#b5cea8"),
        "editor.syntax.comment":       Style(fg="#6a9955", italic=True),
        "editor.syntax.operator":      Style(fg="#d4d4d4"),
        "editor.syntax.builtin":       Style(fg="#4ec9b0"),
        "editor.syntax.error":         Style(fg="#f44747"),
    },
    border_focused=BorderStyle.DOUBLE,
    border_unfocused=BorderStyle.SINGLE,
    background_pattern=SolidBackground(),
)
