"""Cross-platform credential persistence via the standard ``~/.netrc`` file.

``.netrc`` (``_netrc`` on Windows) is the long-established place ftp/ssh clients
read login/password from — plaintext but user-owned and ``chmod 600``. Network
dunders (FTP/SFTP) look it up so a host authenticated once isn't re-prompted
after a restart, and optionally remember a prompted password by writing it back.

We never invent our own secret store: reading uses the stdlib :mod:`netrc`;
writing rewrites the single-line ``machine`` entry for that host, preserving the
rest of the file and forcing ``0600`` perms.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["lookup", "save", "netrc_path"]


def netrc_path() -> Path:
    """``~/.netrc`` (``~/_netrc`` on Windows — the convention the stdlib uses)."""
    return Path.home() / ("_netrc" if os.name == "nt" else ".netrc")


def lookup(host: str) -> tuple[str, str] | None:
    """``(login, password)`` for ``host`` from ``~/.netrc``, or ``None``.

    Never raises: a missing/malformed file or an entry without a password
    simply yields ``None`` (callers fall back to prompting)."""
    p = netrc_path()
    if not p.exists():
        return None
    try:
        import netrc

        auth = netrc.netrc(str(p)).authenticators(host)
    except Exception:
        return None
    if not auth:
        return None
    login, _account, password = auth
    if not login or password is None:
        return None
    return login, password


def _is_machine_line(line: str, host: str) -> bool:
    toks = line.split()
    return len(toks) >= 2 and toks[0] == "machine" and toks[1] == host


def save(host: str, login: str, password: str) -> None:
    """Remember ``login``/``password`` for ``host`` in ``~/.netrc``.

    Replaces any existing single-line ``machine <host>`` entry, keeps everything
    else, and writes atomically with ``0600`` perms. Best-effort — failures are
    swallowed (persistence is a convenience, not a guarantee)."""
    try:
        p = netrc_path()
        lines = p.read_text().splitlines() if p.exists() else []
        kept = [ln for ln in lines if not _is_machine_line(ln, host)]
        kept.append(f"machine {host} login {login} password {password}")
        text = "\n".join(kept) + "\n"
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
        os.chmod(p, 0o600)
    except OSError:
        pass
