"""Shared test fixtures.

Redirect XDG_CONFIG_HOME to a per-test tmp dir so anything that reads or
writes the user config (e.g. theme persistence) is fully isolated from the
developer's real ``~/.config/dunders`` and starts from a clean slate.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
