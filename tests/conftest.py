"""Shared test fixtures.

Redirect XDG_CONFIG_HOME to a per-test tmp dir so anything that reads or
writes the user config (e.g. theme persistence) is fully isolated from the
developer's real ``~/.config/dunders`` and starts from a clean slate.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


@pytest.fixture(autouse=True)
def _isolated_netrc(tmp_path, monkeypatch):
    """Redirect ~/.netrc to a per-test tmp file so credential persistence in the
    FTP/SFTP providers never reads or writes the developer's real ~/.netrc."""
    from dunders.fm import netrc_store
    monkeypatch.setattr(netrc_store, "netrc_path", lambda: tmp_path / ".netrc")
