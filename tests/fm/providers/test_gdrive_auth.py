"""OAuth token math for the Google Drive dunder (offline)."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from dunders.fm.providers.gdrive.api import DriveError
from dunders.fm.providers.gdrive.auth import (
    SCOPE,
    GDriveAuth,
    build_consent_url,
    exchange_code,
    refresh_tokens,
)


class FakeTokenTransport:
    """Answers the OAuth token endpoint from a queue of canned responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.forms = []

    def request(self, method, url, *, headers, body=None):
        assert method == "POST" and url.endswith("/token")
        self.forms.append({k: v[0] for k, v in parse_qs(body.decode()).items()})
        status, payload = self._responses.pop(0)
        return status, json.dumps(payload).encode()

    def open(self, *a, **k):  # unused
        raise AssertionError


def test_build_consent_url_has_offline_and_scope():
    url = build_consent_url("cid", "http://127.0.0.1:9/")
    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == ["cid"]
    assert qs["access_type"] == ["offline"]     # required for a refresh token
    assert qs["response_type"] == ["code"]
    assert qs["scope"] == [SCOPE]


def test_exchange_code_returns_tokens():
    t = FakeTokenTransport([(200, {
        "access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
    })])
    tok = exchange_code(t, "cid", "sec", "code", "http://127.0.0.1:9/",
                        now=lambda: 1000.0)
    assert tok.access_token == "AT" and tok.refresh_token == "RT"
    assert tok.expires_at == 1000.0 + 3600
    assert t.forms[0]["grant_type"] == "authorization_code"


def test_refresh_keeps_existing_refresh_token_when_omitted():
    t = FakeTokenTransport([(200, {"access_token": "AT2", "expires_in": 100})])
    tok = refresh_tokens(t, "cid", "sec", "RT", now=lambda: 500.0)
    assert tok.access_token == "AT2"
    assert tok.refresh_token == "RT"            # Google omits it -> keep old
    assert tok.expires_at == 600.0
    assert t.forms[0]["grant_type"] == "refresh_token"


def test_error_response_raises_driveerror():
    t = FakeTokenTransport([(400, {"error": "invalid_grant",
                                   "error_description": "bad"})])
    with pytest.raises(DriveError) as ei:
        refresh_tokens(t, "cid", "sec", "RT")
    assert ei.value.status == 400 and "bad" in str(ei.value)


def test_invalid_grant_explains_expired_sign_in():
    """Google says only 'Bad Request' for a dead refresh token — the message
    must name the cause (expired/revoked) and the cure (re-run setup)."""
    t = FakeTokenTransport([(400, {"error": "invalid_grant",
                                   "error_description": "Bad Request"})])
    with pytest.raises(DriveError) as ei:
        refresh_tokens(t, "cid", "sec", "RT")
    text = str(ei.value)
    assert "expired or was revoked" in text
    assert "gdrive.setup" in text
    assert "7 days" in text


def test_other_oauth_errors_keep_plain_description():
    t = FakeTokenTransport([(401, {"error": "invalid_client",
                                   "error_description": "Unauthorized"})])
    with pytest.raises(DriveError) as ei:
        refresh_tokens(t, "cid", "sec", "RT")
    text = str(ei.value)
    assert "Unauthorized" in text
    assert "expired or was revoked" not in text


def test_auth_refreshes_lazily_and_caches():
    clock = {"t": 0.0}
    t = FakeTokenTransport([
        (200, {"access_token": "AT1", "expires_in": 100}),
        (200, {"access_token": "AT2", "expires_in": 100}),
    ])
    saved = []
    auth = GDriveAuth(t, client_id="cid", client_secret="sec",
                      refresh_token="RT", clock=lambda: clock["t"],
                      on_refresh=saved.append)
    assert auth.token() == "AT1"          # first call refreshes
    assert auth.token() == "AT1"          # cached (not yet near expiry)
    assert len(t.forms) == 1
    clock["t"] = 100.0                     # now past expiry - skew
    assert auth.token() == "AT2"          # refreshes again
    assert len(t.forms) == 2
    assert saved == ["RT", "RT"]          # persisted each refresh


def test_auth_without_refresh_token_raises():
    t = FakeTokenTransport([])
    auth = GDriveAuth(t, client_id="cid", client_secret="sec", refresh_token="")
    with pytest.raises(DriveError):
        auth.token()


def test_loopback_ignores_favicon_and_catches_code():
    """The loopback server must survive a favicon/preflight probe (204) without
    consuming the real redirect, and respond with Content-Length so the browser
    page doesn't hang — the two symptoms the user hit."""
    import json
    import threading
    import time
    import urllib.parse
    import urllib.request

    from dunders.fm.providers.gdrive.auth import run_loopback_consent

    class _Tok:
        def request(self, method, url, *, headers, body=None):
            return 200, json.dumps(
                {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
            ).encode()

        def open(self, *a, **k):
            raise AssertionError

    seen = {}

    def _on_url(url):
        redir = urllib.parse.parse_qs(
            urllib.parse.urlparse(url).query)["redirect_uri"][0]

        def _drive():
            time.sleep(0.15)
            # favicon probe first — must not drop the code
            try:
                urllib.request.urlopen(redir + "favicon.ico", timeout=2).read()
            except Exception:  # noqa: BLE001
                seen["favicon"] = "err"
            r = urllib.request.urlopen(redir + "?code=THECODE", timeout=2)
            seen["status"] = r.status

        threading.Thread(target=_drive, daemon=True).start()

    tokens = run_loopback_consent(_Tok(), "cid", "sec",
                                  open_browser=False, on_url=_on_url)
    assert seen.get("favicon") != "err"     # favicon handled, not a crash
    assert seen.get("status") == 200        # redirect page completed (no hang)
    assert tokens.access_token == "AT" and tokens.refresh_token == "RT"
