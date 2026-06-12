from dunders.windowing.core.fold_engine import FoldRule
from dunders.windowing.core.indent_fold import IndentFoldRule

DEFAULT_FOLD_RULES: list[FoldRule] = [
    FoldRule(start_label="{", end_label="}", placeholder="⋯", priority=10),
    FoldRule(start_label="(", end_label=")", placeholder="⦅…⦆", priority=5),
    FoldRule(start_label="[", end_label="]", placeholder="[…]", priority=5),
    FoldRule(start_label="#region", end_label="#endregion", placeholder="▸ region", priority=20),
    FoldRule(start_label='"""', end_label='"""', placeholder="▸ doc…", priority=15),
    IndentFoldRule(placeholder=" ⋯", priority=1, min_lines=2,
                   blank_lines_break=False, tab_size=4),
]

DEFAULT_KEY_BINDINGS: dict[str, str] = {
    "ctrl+period": "macro_toggle",
    "ctrl+o": "fullscreen_cli",
    "ctrl+s": "save",
    "ctrl+f": "find",
    "ctrl+g": "goto",
    "f10": "menu",
}

DEFAULT_SETTINGS: dict[str, object] = {
    "tab_size": 4,
    "insert_spaces": True,
    "encoding": "utf-8",
    "layout": "standard",
    "show_line_numbers": True,
    "word_wrap": False,
    "fold_by_indent": True,
    "show_fold_line_count": False,
}
