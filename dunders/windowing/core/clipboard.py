"""Single source of truth for clipboard access.

Everything (editor, command line, panel header copy button) routes through
here so they all share ONE system clipboard:

* ``system_copy`` / ``system_paste`` talk to the real OS clipboard via
  ``pbcopy``/``pbpaste`` on macOS and ``xclip`` on Linux.
* ``copy`` / ``paste`` add an OSC 52 fallback (Textual's ``app.copy_to_clipboard``
  / ``app.clipboard``) so copy/paste keep working over SSH where the local
  ``pbcopy``/``xclip`` binaries aren't reachable.
"""

from __future__ import annotations

import platform
import subprocess


def system_copy(text: str) -> bool:
    """Write ``text`` to the OS clipboard. Returns True on success."""
    try:
        if platform.system() == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        else:
            subprocess.run(
                ["xclip", "-selection", "clipboard"], input=text.encode(), check=True
            )
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def system_paste() -> str:
    """Read the OS clipboard. Returns "" if it's unavailable/empty."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(["pbpaste"], capture_output=True, check=True)
        else:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                check=True,
            )
        return result.stdout.decode()
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""


def copy(text: str, app=None) -> None:
    """Copy ``text`` to the system clipboard, with an OSC 52 fallback.

    The OSC 52 path (``app.copy_to_clipboard``) is what makes copy work over
    SSH/remote sessions where ``pbcopy``/``xclip`` aren't reachable.
    """
    system_copy(text)
    if app is not None:
        try:
            app.copy_to_clipboard(text)
        except Exception:
            pass


def paste(app=None) -> str:
    """Read from the system clipboard, falling back to Textual's clipboard.

    The fallback covers SSH/remote sessions: a prior in-app copy lands in
    ``app.clipboard`` (and may have been delivered via OSC 52) even when the
    local ``pbpaste``/``xclip`` returns nothing.
    """
    text = system_paste()
    if not text and app is not None:
        try:
            text = app.clipboard or ""
        except Exception:
            text = ""
    return text
