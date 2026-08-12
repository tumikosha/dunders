"""Interactive one-time Google Drive sign-in:  ``python -m dunders.fm.providers.gdrive.setup``

Prompts for the OAuth *Desktop app* client_id/secret you created in Google
Cloud, opens the browser for consent on a throwaway 127.0.0.1 server, then
stores the account (0600 ``gdrive.json``) so the ``gdrive`` dunder can use it.
"""

from __future__ import annotations

import sys

from dunders.fm.providers.gdrive.api import DriveError, UrllibTransport
from dunders.fm.providers.gdrive.auth import run_loopback_consent
from dunders.fm.providers.gdrive.config import (
    default_client,
    gdrive_config_path,
    save_account,
)


def main() -> int:  # pragma: no cover - interactive
    print("Google Drive sign-in\n" + "-" * 20)
    label = input("Account label [default]: ").strip() or "default"

    bundled = default_client()
    if bundled is not None:
        # An app-wide client is configured -> pure browser sign-in.
        client_id, client_secret = bundled
        print("Using the app's Google client - opening the browser to sign in...")
    else:
        print(
            "No app-wide Google client is set, so this app needs one OAuth\n"
            "'Desktop app' client (a one-time setup). Create it in Google Cloud:\n"
            "  1. https://console.cloud.google.com/ - new/select a project\n"
            "  2. APIs & Services > Library > enable 'Google Drive API'\n"
            "  3. OAuth consent screen > External; add your account as a Test user\n"
            "  4. Credentials > Create credentials > OAuth client ID >\n"
            "     Application type: Desktop app\n"
            "Tip: set DUNDERS_GDRIVE_CLIENT_ID / _SECRET (or bundle them in\n"
            "config.py) once, and future sign-ins skip these prompts.\n"
        )
        client_id = input("Client ID: ").strip()
        client_secret = input("Client secret: ").strip()
        if not client_id or not client_secret:
            print("client id/secret are required.", file=sys.stderr)
            return 2

    print("\nOpening your browser to authorise... (grant access, then return)")

    def _show_url(url: str) -> None:
        print(f"\nIf that browser isn't signed into your Google account, open "
              f"this URL in the one that is:\n{url}\n")

    try:
        tokens = run_loopback_consent(UrllibTransport(), client_id, client_secret,
                                      on_url=_show_url)
    except DriveError as exc:
        print(f"sign-in failed: {exc}", file=sys.stderr)
        return 1
    if not tokens.refresh_token:
        print(
            "no refresh token returned. Remove the app under "
            "https://myaccount.google.com/permissions and retry, or ensure the "
            "consent screen requests offline access.",
            file=sys.stderr,
        )
        return 1

    save_account(label, client_id=client_id, client_secret=client_secret,
                 refresh_token=tokens.refresh_token)
    print(f"\nSaved account {label!r} to {gdrive_config_path()}")
    print(f"In dunders: '_' menu > Google Drive > type '{label}'.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
