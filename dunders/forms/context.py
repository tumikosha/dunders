"""Autofill context for the form editor. ``read_clipboard`` shells out to the
platform clipboard tool (no pip dependency); any failure degrades to ``""``.

``selected_text`` is NOT read here — it is supplied by the caller (the app
knows the active editor), keeping this core free of fm/windowing imports.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

_POSIX_READERS = [
    ["wl-paste", "--no-newline"],
    ["xclip", "-selection", "clipboard", "-o"],
    ["xsel", "-b", "-o"],
]


def _candidate_commands() -> list[list[str]]:
    if sys.platform == "darwin":
        return [["pbpaste"]]
    if sys.platform.startswith("win"):
        return [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]
    return list(_POSIX_READERS)


def read_clipboard(timeout: float = 1.0) -> str:
    """Return the system clipboard text, or ``""`` if it can't be read."""
    for cmd in _candidate_commands():
        if shutil.which(cmd[0]) is None:
            continue
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            return (out.stdout or "").rstrip("\r\n")
    return ""
