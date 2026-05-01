"""Core editor components: buffer, folding, macros."""

from .buffer import TextBuffer
from .fold_engine import FoldEngine, FoldRegistry, FoldRule, FoldRegion, effective_placeholder
from .indent_fold import IndentFoldRule, scan_indent_regions
from .macro import MacroAction, MacroRecorder, MacroStorage

__all__ = [
    "TextBuffer",
    "FoldEngine",
    "FoldRegistry",
    "FoldRule",
    "FoldRegion",
    "effective_placeholder",
    "IndentFoldRule",
    "scan_indent_regions",
    "MacroAction",
    "MacroRecorder",
    "MacroStorage",
]
