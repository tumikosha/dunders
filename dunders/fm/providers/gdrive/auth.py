"""OAuth2 for the Google Drive dunder — desktop *loopback* flow + refresh.

The token math (build consent URL, exchange an auth code, refresh an access
token) is pure and unit-tested over the same injectable ``HttpTransport`` the
API uses. Only :func:`run_loopback_consent` touches the browser + a throwaway
local HTTP server, and is kept thin so the rest stays offline-testable.

Scope is full Drive (``.../auth/drive``); the client_id/secret come from a
Google Cloud "Desktop app" OAuth client the user creates, and the long-lived
refresh token is cached (0600) so consent happens only once.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from dunders.fm.providers.gdrive.api import DriveError, HttpTransport


__all__ = [
    "GDriveAuth", "OAuthTokens", "SCOPE",
    "build_consent_url", "exchange_code", "refresh_tokens",
    "run_loopback_consent",
]

SCOPE = "https://www.googleapis.com/auth/drive"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Refresh this many seconds before the token actually expires.
_SKEW = 60.0


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: float  # epoch seconds


def build_consent_url(client_id: str, redirect_uri: str, *, scope: str = SCOPE) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",   # ask for a refresh token
        "prompt": "consent",
    }
    return f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _post_token(transport: HttpTransport, form: dict[str, str]) -> dict:
    body = urllib.parse.urlencode(form).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    status, raw = transport.request("POST", _TOKEN_URL, headers=headers, body=body)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError as exc:
        raise DriveError(f"bad token response: {exc}", status=status) from exc
    if status >= 400:
        msg = data.get("error_description") or data.get("error") or "token error"
        if data.get("error") == "invalid_grant":
            # Google answers a dead refresh token with a useless "Bad Request"
            # description, so spell out the cause and the cure instead.
            msg = (
                f"Google Drive sign-in expired or was revoked ({msg}). "
                "Re-run 'python -m dunders.fm.providers.gdrive.setup'. "
                "If this keeps happening every week, the OAuth consent screen "
                "is still in Testing — refresh tokens then expire after 7 days; "
                "publish the app (Publishing status ▸ In production) to "
                "stop it."
            )
        raise DriveError(f"OAuth {status}: {msg}", status=status)
    return data


def exchange_code(
    transport: HttpTransport, client_id: str, client_secret: str,
    code: str, redirect_uri: str, *, now: Callable[[], float] = time.time,
) -> OAuthTokens:
    data = _post_token(transport, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    return OAuthTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=now() + float(data.get("expires_in", 3600)),
    )


def refresh_tokens(
    transport: HttpTransport, client_id: str, client_secret: str,
    refresh_token: str, *, now: Callable[[], float] = time.time,
) -> OAuthTokens:
    data = _post_token(transport, {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    return OAuthTokens(
        access_token=data["access_token"],
        # Google usually omits refresh_token on refresh — keep the existing one.
        refresh_token=data.get("refresh_token") or refresh_token,
        expires_at=now() + float(data.get("expires_in", 3600)),
    )


class GDriveAuth:
    """Holds credentials and hands out a valid access token, refreshing lazily.

    ``on_refresh(refresh_token)`` is called whenever the refresh token is (re)set
    so the caller can persist it (0600). ``token()`` is what ``DriveApi`` calls.
    """

    def __init__(
        self, transport: HttpTransport, *, client_id: str, client_secret: str,
        refresh_token: str, clock: Callable[[], float] = time.time,
        on_refresh: Callable[[str], None] | None = None,
    ) -> None:
        self._t = transport
        self._cid = client_id
        self._secret = client_secret
        self._clock = clock
        self._on_refresh = on_refresh
        self._tokens = OAuthTokens("", refresh_token, 0.0)

    def token(self) -> str:
        if self._clock() >= self._tokens.expires_at - _SKEW:
            self._refresh()
        return self._tokens.access_token

    def _refresh(self) -> None:
        rt = self._tokens.refresh_token
        if not rt:
            raise DriveError("no refresh token — re-run Google Drive sign-in")
        self._tokens = refresh_tokens(
            self._t, self._cid, self._secret, rt, now=self._clock,
        )
        if self._on_refresh and self._tokens.refresh_token:
            self._on_refresh(self._tokens.refresh_token)


def run_loopback_consent(
    transport: HttpTransport, client_id: str, client_secret: str, *,
    open_browser: bool = True,
    on_url: Callable[[str], None] | None = None,
    cancel_event: "object | None" = None,
    timeout: float = 300.0,
) -> OAuthTokens:  # pragma: no cover - interactive (browser + local server)
    """Run the one-time desktop consent: pop a browser, catch the redirect on a
    throwaway ``127.0.0.1`` server, exchange the code for tokens.

    ``webbrowser.open`` may launch a *fresh* browser profile that isn't signed
    into Google (so you'd see a login screen). ``on_url`` receives the consent
    URL so the caller can also show it — you can open it in your already
    signed-in browser instead; the loopback catches the redirect either way.
    """
    import http.server
    import threading
    import webbrowser

    holder: dict[str, str] = {}
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        # HTTP/1.0 => the connection closes after each response, so the browser
        # never waits on keep-alive (a classic "the page just spins" hang).
        protocol_version = "HTTP/1.0"

        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs or "error" in qs:
                holder.update({k: v[0] for k, v in qs.items()})
                body = (b"<html><body style='font-family:sans-serif'>"
                        b"<h3>Google Drive authorised.</h3>"
                        b"You can close this tab and return to dunders."
                        b"</body></html>")
                self._respond(200, body)
                done.set()
            else:
                # Ignore favicon / preflight probes — DON'T consume the code
                # request. Serving only one request used to drop the redirect.
                self._respond(204, b"")

        def _respond(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *_a):  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    url = build_consent_url(client_id, redirect_uri)
    if on_url is not None:
        on_url(url)
    if open_browser:
        try:
            webbrowser.open(url, new=2, autoraise=True)
        except Exception:
            pass  # no browser / headless — the user opens on_url's link manually
    # serve_forever in the background so favicon + the real redirect are all
    # handled; stop as soon as the code (or an error) lands.
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while not done.wait(0.3):
        if cancel_event is not None and cancel_event.is_set():
            holder["error"] = "cancelled"
            break
        if time.monotonic() >= deadline:
            break
    server.shutdown()
    server.server_close()
    if "code" not in holder:
        raise DriveError(f"sign-in did not complete: {holder.get('error', 'timeout')}")
    return exchange_code(transport, client_id, client_secret,
                         holder["code"], redirect_uri)
