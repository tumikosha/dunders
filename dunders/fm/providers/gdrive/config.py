"""Persisted Google Drive accounts + a connector factory.

Credentials (OAuth client_id/secret and the long-lived refresh token) live in a
0600 ``gdrive.json`` next to the rest of the config, keyed by a short account
*label* which becomes the ``gdrive`` locator's ``root``. :func:`make_connector`
turns a label into a live :class:`DriveApi` (refreshing + persisting tokens),
and is what the provider is wired with.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from dunders.config.user_config import config_dir
from dunders.fm.providers.gdrive.api import DriveApi, DriveError, UrllibTransport
from dunders.fm.providers.gdrive.auth import GDriveAuth


__all__ = [
    "gdrive_config_path", "load_accounts", "save_account", "make_connector",
    "default_client",
]

# A bundled "Desktop app" OAuth client so end users sign in with just a browser
# (no console.google.com, no typing credentials). Register ONE client once and
# put it here or in the env — a desktop client's secret is not confidential
# (Google treats installed-app secrets as non-secret). Leave empty to fall back
# to per-account credentials entered at sign-in time.
_BUNDLED_CLIENT_ID = ""
_BUNDLED_CLIENT_SECRET = ""


def default_client() -> tuple[str, str] | None:
    """The app-wide OAuth client (client_id, client_secret), or None.

    Resolved env-first (``DUNDERS_GDRIVE_CLIENT_ID`` /
    ``DUNDERS_GDRIVE_CLIENT_SECRET``) so the bundled default can be overridden
    without touching the source; then the bundled constants above."""
    cid = os.environ.get("DUNDERS_GDRIVE_CLIENT_ID") or _BUNDLED_CLIENT_ID
    secret = os.environ.get("DUNDERS_GDRIVE_CLIENT_SECRET") or _BUNDLED_CLIENT_SECRET
    if cid and secret:
        return cid, secret
    return None


def gdrive_config_path() -> Path:
    return config_dir() / "gdrive.json"


def load_accounts(path: Path | None = None) -> dict[str, dict]:
    p = path or gdrive_config_path()
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_account(
    label: str, *, client_id: str, client_secret: str, refresh_token: str,
    path: Path | None = None,
) -> None:
    """Write/overwrite one account. Best-effort 0600, never raises into the UI."""
    p = path or gdrive_config_path()
    accounts = load_accounts(p)
    accounts[label] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    _atomic_write_0600(p, json.dumps(accounts, indent=2))


def _set_refresh_token(label: str, refresh_token: str, path: Path | None = None) -> None:
    p = path or gdrive_config_path()
    accounts = load_accounts(p)
    if label in accounts:
        accounts[label]["refresh_token"] = refresh_token
        _atomic_write_0600(p, json.dumps(accounts, indent=2))


def _atomic_write_0600(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError:
        pass  # config is best-effort; never crash the UI over it


def make_connector(
    path: Path | None = None,
    transport_factory: Callable[[], object] = UrllibTransport,
) -> Callable[[str], DriveApi]:
    """A ``label -> DriveApi`` connector reading creds from ``gdrive.json``.

    ``transport_factory`` is injectable so tests can supply a fake HTTP layer.
    Raises :class:`DriveError` with actionable text when the label is unknown.
    """
    def connect(label: str) -> DriveApi:
        acct = load_accounts(path).get(label)
        if not acct:
            raise DriveError(
                f"Google Drive account {label!r} is not configured — run "
                "'python -m dunders.fm.providers.gdrive.setup' to sign in"
            )
        transport = transport_factory()
        auth = GDriveAuth(
            transport,
            client_id=acct["client_id"],
            client_secret=acct["client_secret"],
            refresh_token=acct.get("refresh_token", ""),
            on_refresh=lambda rt: _set_refresh_token(label, rt, path),
        )
        return DriveApi(transport, auth.token)

    return connect
