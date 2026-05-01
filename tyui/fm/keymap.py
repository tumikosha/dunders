"""Default NC/mc-style F-key labels for the status bar.

Phase 1 only uses `.label` — handlers are wired up in later phases. Storing
the table here keeps app.py free of NC-specific strings and centralises
future customisation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FKeyLabel:
    key: str    # status bar key column, e.g. "1"
    label: str  # description, e.g. "Help"


DEFAULT_FKEY_LABELS: tuple[FKeyLabel, ...] = (
    FKeyLabel("1",  "Help"),
    FKeyLabel("2",  "UsrMnu"),
    FKeyLabel("3",  "View"),
    FKeyLabel("4",  "Edit"),
    FKeyLabel("5",  "Copy"),
    FKeyLabel("6",  "RenMov"),
    FKeyLabel("7",  "Mkdir"),
    FKeyLabel("8",  "Delete"),
    FKeyLabel("9",  "Menu"),
    FKeyLabel("10", "Quit"),
)


EDITOR_FKEY_LABELS: tuple[FKeyLabel, ...] = (
    FKeyLabel("1",  "Help"),
    FKeyLabel("2",  "Save"),
    FKeyLabel("3",  "SvAs"),
    FKeyLabel("4",  "Repl"),
    FKeyLabel("5",  "SplH"),
    FKeyLabel("6",  "SplV"),
    FKeyLabel("7",  "Fold"),
    FKeyLabel("8",  "Macr"),
    FKeyLabel("9",  "Menu"),
    FKeyLabel("10", "Quit"),
)
