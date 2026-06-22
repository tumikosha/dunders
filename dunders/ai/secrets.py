"""Secret storage: ``secrets.json`` (0600) + environment-variable fallback.

Keys (e.g. ``ANTHROPIC_API_KEY``, ``GROQ_API_KEY``) resolve env-first: an
environment variable always overrides the on-disk file, so CI / shell exports
win without touching the wizard-written file. The file lives next to
``config.json`` and is written 0600 (best-effort, never raises into the UI).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dunders.config.user_config import config_dir


__all__ = ["SecretResolver", "secrets_path"]


def secrets_path() -> Path:
    return config_dir() / "secrets.json"


class SecretResolver:
    """Resolve secrets env-first, then from ``secrets.json``.

    The file is read fresh on every ``resolve`` (it is tiny and rarely hit on a
    hot path) so an externally-edited file is picked up without a restart.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or secrets_path()

    def _load(self) -> dict[str, str]:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(
            data, dict
        ) else {}

    def resolve(self, name: str) -> str | None:
        """Return the secret value: env var ``name`` wins, else the file."""
        env = os.environ.get(name)
        if env:
            return env
        return self._load().get(name)

    def set(self, name: str, value: str) -> bool:
        """Persist ``value`` under ``name`` in the 0600 secrets file."""
        data = self._load()
        data[name] = value
        path = self._path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return True
        except OSError:
            return False

    def delete(self, name: str) -> bool:
        """Remove ``name`` from the file (no-op if absent)."""
        data = self._load()
        if name not in data:
            return True
        del data[name]
        path = self._path
        try:
            tmp = path.with_name(path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            return True
        except OSError:
            return False
