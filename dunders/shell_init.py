"""cd-on-exit shell integration (mc's ``mc-wrapper.sh`` idea).

A program cannot change its parent shell's directory, so "Quit → Yes, current
dir" needs help from the shell: ``eval "$(dunders --shell-init zsh)"`` in
``~/.zshrc`` defines wrapper functions for ``dunders``/``__``/``__w`` that run
the real command with ``DUNDERS_CWD_FILE`` pointing at a temp file, then ``cd``
into whatever directory the app wrote there.

The app always writes the chosen directory on a confirmed quit — the start dir
for "Yes, start dir", so the shell stays put. Without the wrapper the variable
is unset and nothing is written.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_VAR = "DUNDERS_CWD_FILE"
COMMANDS = ("dunders", "__", "__w")
SHELLS = ("zsh", "bash")

_SNIPPET = r'''# dunders: cd into the panel's directory on "Quit → Yes, current dir".
_dunders_cd() {
  local _dunders_f _dunders_d _dunders_rc
  _dunders_f="$(mktemp "${TMPDIR:-/tmp}/dunders-cwd.XXXXXX")" || { command "$@"; return; }
  DUNDERS_CWD_FILE="$_dunders_f" command "$@"
  _dunders_rc=$?
  _dunders_d="$(cat -- "$_dunders_f" 2>/dev/null)"
  rm -f -- "$_dunders_f"
  if [ -n "$_dunders_d" ] && [ -d "$_dunders_d" ] && [ "$_dunders_d" != "$PWD" ]; then
    cd -- "$_dunders_d" || true
  fi
  return $_dunders_rc
}
'''


def snippet(shell: str) -> str:
    """Shell code defining the cd-on-exit wrappers (same for zsh and bash)."""
    if shell not in SHELLS:
        raise ValueError(f"unsupported shell: {shell}")
    funcs = "".join(f'{cmd}() {{ _dunders_cd {cmd} "$@"; }}\n' for cmd in COMMANDS)
    return _SNIPPET + funcs


def take_cwd_file() -> str | None:
    """Read and remove ``$DUNDERS_CWD_FILE`` from this process's environment.

    Removed so shells and tools started from inside dunders don't inherit it —
    a nested ``command dunders`` would otherwise write into our file.
    """
    return os.environ.pop(ENV_VAR, None) or None


def rc_hint() -> str:
    """The ``~/.zshrc``/``~/.bashrc`` line that enables the wrapper."""
    shell = Path(os.environ.get("SHELL", "zsh")).name
    shell = shell if shell in SHELLS else "zsh"
    return f'eval "$(dunders --shell-init {shell})"  # in ~/.{shell}rc'


def report_exit_dir(result: object, cwd_file: str | None, start_dir: Path) -> None:
    """Hand the quit choice to the shell wrapper, or explain how to get one.

    ``result`` is ``App.run()``'s return value: a directory string from the
    Quit dialog, or ``None`` for any other exit (Ctrl+G, errors) — then the
    shell stays where it was and nothing is written.
    """
    if not isinstance(result, str) or not result:
        return
    if cwd_file:
        try:
            Path(cwd_file).write_text(result, encoding="utf-8")
        except OSError as exc:
            print(f"dunders: cannot write {cwd_file}: {exc}", file=sys.stderr)
        return
    if Path(result) != start_dir:
        print(
            f"dunders: the shell cannot follow the panel to {result} without "
            f"the wrapper. Enable it with:\n  {rc_hint()}",
            file=sys.stderr,
        )
