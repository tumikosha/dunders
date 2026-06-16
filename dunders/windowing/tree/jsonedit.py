"""Minimal runner: edit a JSON/YAML file as a tree.

    python -m dunders.windowing.tree.jsonedit path/to/file.json

Ctrl+S saves, F10 / Ctrl+Q quits. See JsonYamlTreeContent for the key map.
"""

from __future__ import annotations

import sys

from textual.app import App, ComposeResult
from textual.binding import Binding

from dunders.windowing.desktop import Desktop
from dunders.windowing.frame import Decorations, TitleSpec
from dunders.windowing.helpers import make_window
from dunders.windowing.manager import WindowManager
from dunders.windowing.tree.data_content import JsonYamlTreeContent


class JsonEditApp(App):
    CSS = """
    Screen { background: $panel; }
    Desktop { margin-bottom: 1; }
    """
    BINDINGS = [Binding("f10,ctrl+q", "quit", "Quit", show=True)]

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        self.desktop: Desktop | None = None
        self.manager: WindowManager | None = None

    def compose(self) -> ComposeResult:
        self.desktop = Desktop(theme_name="modern_dark")
        yield self.desktop

    def on_mount(self) -> None:
        self.manager = WindowManager(self.desktop)
        content = JsonYamlTreeContent(self._path)
        window = make_window(
            content,
            title=TitleSpec(text=content.window_title),
            position=(1, 1),
            size=(64, 22),
            decorations=Decorations(close_box=True, zoom_box=True),
        )
        self.desktop.add_window(window)
        self.desktop.focus_window(window)
        self.call_after_refresh(lambda: self.manager.maximize_focused())


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m dunders.windowing.tree.jsonedit <file.json|.yaml>")
        return 2
    path = argv[0]
    # Pre-flight: fail cleanly on a malformed file instead of crashing the UI.
    # A missing file is fine — it opens empty and is created on save (Ctrl+S).
    from pathlib import Path
    p = Path(path)
    if p.exists() and p.read_text(encoding="utf-8").strip():
        try:
            text = p.read_text(encoding="utf-8")
            if path.lower().endswith((".yaml", ".yml")):
                import yaml
                yaml.safe_load(text)
            else:
                import json
                json.loads(text)
        except Exception as exc:  # noqa: BLE001 — surface any parse error plainly
            print(f"jsonedit: cannot parse {path}: {exc}")
            return 1
    JsonEditApp(path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
