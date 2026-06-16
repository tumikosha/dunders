"""JsonYamlTreeContent — edit a JSON/YAML file as an editable snippet tree.

Keys become labels, scalar values become editable bodies; ``Ctrl+S`` serialises
the tree back to the file (format chosen by extension, types inferred).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from textual import events

from dunders.windowing.content import WindowCommand
from dunders.windowing.tree.content import TreeContent
from dunders.windowing.tree.data_adapter import data_from_tree, tree_from_data


class JsonYamlTreeContent(TreeContent):
    """A tree dunder backed by a JSON or YAML file."""

    ACTION_BUTTONS = TreeContent.ACTION_BUTTONS + [("⇩", "save", "Save (Ctrl+S)")]

    def _run_action(self, action: str) -> None:
        if action == "save":
            self.save()
            return
        super()._run_action(action)

    def __init__(self, path: str | os.PathLike, **kwargs) -> None:
        self.path = os.fspath(path)
        self._is_yaml = self.path.lower().endswith((".yaml", ".yml"))
        root = tree_from_data(self._load())
        super().__init__(root, title=os.path.basename(self.path), **kwargs)

    # --- file I/O ----------------------------------------------------------

    def _load(self) -> Any:
        p = Path(self.path)
        if not p.exists():
            return {}                       # open-or-create: new file on save
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        if self._is_yaml:
            return yaml.safe_load(text) or {}
        return json.loads(text)

    def _serialize(self, data: Any) -> str:
        if self._is_yaml:
            return yaml.safe_dump(
                data, sort_keys=False, allow_unicode=True, default_flow_style=False
            )
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    def save(self) -> None:
        """Commit any in-progress edit and write the tree back to the file."""
        if self.is_editing:
            self._commit_edit()
        data = data_from_tree(self.root)
        try:
            Path(self.path).write_text(self._serialize(data), encoding="utf-8")
        except OSError as exc:
            self._toast(f"Save failed: {exc}", severity="error", timeout=5)
            return
        self.is_dirty = False
        self._toast(f"Saved {os.path.basename(self.path)}", timeout=2)

    def _toast(self, message: str, *, severity: str = "information",
               timeout: float = 3) -> None:
        try:
            self.app.notify(message, severity=severity, timeout=timeout)
        except Exception:  # noqa: BLE001 — never let a toast break saving
            pass

    # --- commands ----------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+s":
            event.stop()
            event.prevent_default()
            self.save()
            return
        super().on_key(event)

    def get_commands(self) -> list[WindowCommand]:
        # id "save" matches the app's File → Save menu item, so it routes here
        # (via the focused-window command dispatcher) while this tree is focused.
        return super().get_commands() + [
            WindowCommand(id="save", label="Save", hotkey="ctrl+s",
                          handler=self.save),
        ]
