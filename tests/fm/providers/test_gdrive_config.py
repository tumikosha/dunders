"""Google Drive account storage, connector, and registration."""

import json
import os
import stat

import pytest

from dunders.fm.providers.gdrive.api import DriveApi, DriveError
from dunders.fm.providers.gdrive.config import (
    default_client,
    load_accounts,
    make_connector,
    save_account,
)


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "gdrive.json"
    save_account("work", client_id="cid", client_secret="sec",
                 refresh_token="RT", path=p)
    accounts = load_accounts(p)
    assert accounts["work"] == {
        "client_id": "cid", "client_secret": "sec", "refresh_token": "RT",
    }


def test_saved_file_is_0600(tmp_path):
    p = tmp_path / "gdrive.json"
    save_account("a", client_id="c", client_secret="s", refresh_token="r", path=p)
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600


def test_load_missing_or_corrupt_returns_empty(tmp_path):
    assert load_accounts(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_accounts(bad) == {}


def test_connector_unknown_label_raises(tmp_path):
    connect = make_connector(path=tmp_path / "gdrive.json")
    with pytest.raises(DriveError) as ei:
        connect("ghost")
    assert "not configured" in str(ei.value)


def test_connector_builds_api_for_known_label(tmp_path):
    p = tmp_path / "gdrive.json"
    save_account("me", client_id="c", client_secret="s", refresh_token="r", path=p)

    class _FakeTransport:
        def request(self, *a, **k):
            return 200, b"{}"

        def open(self, *a, **k):
            raise AssertionError

    connect = make_connector(path=p, transport_factory=_FakeTransport)
    api = connect("me")
    assert isinstance(api, DriveApi)


def test_refresh_token_persisted_on_refresh(tmp_path):
    # A refresh updates the stored token via the connector's on_refresh hook.
    p = tmp_path / "gdrive.json"
    save_account("me", client_id="c", client_secret="s", refresh_token="OLD",
                 path=p)

    class _RefreshTransport:
        def request(self, method, url, *, headers, body=None):
            return 200, json.dumps(
                {"access_token": "AT", "refresh_token": "NEW", "expires_in": 3600}
            ).encode()

        def open(self, *a, **k):
            raise AssertionError

    connect = make_connector(path=p, transport_factory=_RefreshTransport)
    api = connect("me")
    api._token()  # force a token fetch -> refresh -> persist
    assert load_accounts(p)["me"]["refresh_token"] == "NEW"


def test_gdrive_registered_in_default_registry():
    from dunders.fm.vfs_local import default_registry
    assert "gdrive" in default_registry().schemes()


def test_default_client_from_env(monkeypatch):
    monkeypatch.setenv("DUNDERS_GDRIVE_CLIENT_ID", "cid")
    monkeypatch.setenv("DUNDERS_GDRIVE_CLIENT_SECRET", "sec")
    assert default_client() == ("cid", "sec")


def test_default_client_none_when_unset(monkeypatch):
    monkeypatch.delenv("DUNDERS_GDRIVE_CLIENT_ID", raising=False)
    monkeypatch.delenv("DUNDERS_GDRIVE_CLIENT_SECRET", raising=False)
    # Bundled constants are empty by default.
    assert default_client() is None


def test_default_client_needs_both(monkeypatch):
    monkeypatch.setenv("DUNDERS_GDRIVE_CLIENT_ID", "cid")
    monkeypatch.delenv("DUNDERS_GDRIVE_CLIENT_SECRET", raising=False)
    assert default_client() is None
