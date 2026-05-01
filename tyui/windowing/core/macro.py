from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class MacroAction:
    kind: str  # "keypress", "command", "click", "fold"
    data: str


class MacroRecorder:
    def __init__(self) -> None:
        self._recording = False
        self._actions: list[MacroAction] = []

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self) -> None:
        self._recording = True
        self._actions = []

    def stop_recording(self) -> list[MacroAction]:
        self._recording = False
        actions = self._actions
        self._actions = []
        return actions

    def toggle_recording(self) -> list[MacroAction] | None:
        if self._recording:
            return self.stop_recording()
        else:
            self.start_recording()
            return None

    def record_action(self, action: MacroAction) -> None:
        if self._recording:
            self._actions.append(action)


class MacroStorage:
    def __init__(self, config_dir: str) -> None:
        self._config_dir = Path(config_dir)
        self._macros_dir = self._config_dir / "macros"
        self._macros_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self) -> Path:
        return self._macros_dir / "session.json"

    def _saved_path(self) -> Path:
        return self._macros_dir / "saved.json"

    def _load_file(self, path: Path) -> dict:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def _save_file(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def save_macro(self, name: str, key: str, actions: list[MacroAction], permanent: bool) -> None:
        path = self._saved_path() if permanent else self._session_path()
        data = self._load_file(path)
        data[name] = {"key": key, "actions": [asdict(a) for a in actions]}
        self._save_file(path, data)

    def load_macros(self, permanent: bool) -> dict:
        path = self._saved_path() if permanent else self._session_path()
        return self._load_file(path)

    def delete_macro(self, name: str, permanent: bool) -> None:
        path = self._saved_path() if permanent else self._session_path()
        data = self._load_file(path)
        data.pop(name, None)
        self._save_file(path, data)

    def list_all(self) -> list[dict]:
        result = []
        for permanent in (False, True):
            macros = self.load_macros(permanent=permanent)
            for name, info in macros.items():
                result.append({
                    "name": name,
                    "key": info["key"],
                    "permanent": permanent,
                    "action_count": len(info["actions"]),
                })
        return result
