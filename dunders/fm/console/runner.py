"""Parse the input line, route to a Console target, run via the backend."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol

from .backends.base import Backend, Handle
from .registry import ConsoleRegistry


CommandKind = Literal["run", "cd", "backend", "to", "noop"]


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    kind: CommandKind
    target: str | None
    anonymous: bool
    body: str


class _TargetLike(Protocol):
    id: str
    busy: bool
    def append(self, b: bytes) -> None: ...
    def mark_done(self, rc: int) -> None: ...


def _resolve_target_id(name: str | None) -> str:
    """Mirror ConsoleRegistry.get_or_create's id formula."""
    if name in (None, "", "default"):
        return "console-default"
    return f"console-{name}"


_AT_RE = re.compile(r"^@(\S+)\s+(.*)$")


class CommandRunner:
    def __init__(
        self,
        *,
        registry: ConsoleRegistry,
        backend: Backend,
        panel_cwd_getter: Callable[[], Path],
        panel_cd: Callable[[Path], str | None],
        on_busy_changed: Callable[[str, bool], None] | None = None,
    ) -> None:
        self._registry = registry
        self._backend = backend
        self._panel_cwd = panel_cwd_getter
        self._panel_cd = panel_cd
        self._current_target: str | None = None
        self._handles: dict[str, Handle] = {}
        self._on_busy_changed = on_busy_changed

    @staticmethod
    def parse(text: str, *, anonymous: bool) -> ParsedCommand:
        s = text.strip()
        if not s:
            return ParsedCommand("noop", None, anonymous, "")
        if s == "cd" or s.startswith("cd "):
            arg = s[2:].strip()
            return ParsedCommand("cd", None, anonymous, arg)
        if s.startswith(":backend "):
            return ParsedCommand("backend", None, anonymous, s[len(":backend "):].strip())
        if s.startswith(":to "):
            return ParsedCommand("to", s[len(":to "):].strip(), anonymous, "")
        m = _AT_RE.match(s)
        if m:
            return ParsedCommand("run", m.group(1), anonymous, m.group(2))
        return ParsedCommand("run", None, anonymous, s)

    def execute(self, text: str, *, anonymous: bool = False) -> None:
        cmd = self.parse(text, anonymous=anonymous)
        if cmd.kind == "noop":
            return
        if cmd.kind == "cd":
            target = self._registry.get_or_create(self._current_target)
            self._echo_prompt(target, text)
            self._handle_cd(cmd.body)
            return
        if cmd.kind == "backend":
            self._handle_backend_switch(cmd.body)
            return
        if cmd.kind == "to":
            self._current_target = cmd.target if cmd.target else None
            return
        # run
        target_name = cmd.target if cmd.target is not None else self._current_target
        target = self._registry.get_or_create(target_name, anonymous=cmd.anonymous)
        if target.busy:
            # The target is running an interactive child. Pipe the typed
            # text into its stdin instead of rejecting the line — that's
            # what lets the user drive a python REPL, sudo prompt, etc.
            self._send_to_handle(target, cmd.body)
            return
        target.busy = True
        self._notify_busy(target.id, True)
        cwd = self._panel_cwd()
        self._echo_prompt(target, cmd.body, cwd=cwd)
        handle = self._backend.spawn(
            cmd.body,
            cwd,
            on_chunk=target.append,
            on_exit=lambda rc, _t=target: self._on_handle_exit(_t, rc),
        )
        self._handles[target.id] = handle

    def _on_handle_exit(self, target: _TargetLike, rc: int) -> None:
        self._handles.pop(target.id, None)
        target.mark_done(rc)
        self._notify_busy(target.id, False)

    def _notify_busy(self, target_id: str, busy: bool) -> None:
        if self._on_busy_changed is None:
            return
        try:
            self._on_busy_changed(target_id, busy)
        except Exception:
            pass

    def _send_to_handle(self, target: _TargetLike, body: str) -> None:
        """Forward `body` + newline to the target's running child as stdin.
        Echoes the line into the console so the user sees what they typed —
        pipes don't auto-echo like a real tty."""
        handle = self._handles.get(target.id)
        if handle is None or not handle.running:
            target.append(f"[{target.id}: not running, input dropped]\n".encode())
            return
        target.append((body + "\n").encode())
        try:
            handle.write_stdin((body + "\n").encode())
        except Exception as e:
            target.append(f"[stdin write failed: {e}]\n".encode())

    def _echo_prompt(self, target: _TargetLike, body: str, *, cwd: Path | None = None) -> None:
        """Print `cwd $ command` prefix before running so transcripts read
        like a real shell session."""
        if cwd is None:
            cwd = self._panel_cwd()
        cwd_str = str(cwd)
        home = str(Path.home())
        if cwd_str == home:
            cwd_str = "~"
        elif cwd_str.startswith(home + "/"):
            cwd_str = "~" + cwd_str[len(home):]
        # Cyan path, bold $, then the command on the same line.
        line = f"\x1b[36m{cwd_str}\x1b[0m \x1b[1m$\x1b[0m {body}\n"
        target.append(line.encode())

    def _handle_cd(self, arg: str) -> None:
        if not arg:
            path = Path.home()
        else:
            path = Path(arg).expanduser()
            if not path.is_absolute():
                path = (self._panel_cwd() / path).resolve()
        if not path.exists() or not path.is_dir():
            self._registry.get_or_create(None).append(
                f"cd: {path}: No such directory\n".encode()
            )
            return
        err = self._panel_cd(path)
        if err is not None:
            self._registry.get_or_create(None).append(f"cd: {err}\n".encode())

    def _handle_backend_switch(self, name: str) -> None:
        target = self._registry.get_or_create(None)
        target.append(f"[backend switch requested: {name}]\n".encode())
        self._on_backend_request(name)

    def set_backend(self, backend: Backend) -> None:
        self._backend = backend

    def _on_backend_request(self, name: str) -> None:
        pass

    def cancel_current(self) -> None:
        h = self._handles.get(_resolve_target_id(self._current_target))
        if h is not None and h.running:
            h.cancel()

    def send_eof(self) -> bool:
        """Close the running child's stdin (Ctrl+D semantics). Returns True
        if a live handle existed."""
        h = self._handles.get(_resolve_target_id(self._current_target))
        if h is None or not h.running:
            return False
        try:
            h.close_stdin()
        except Exception:
            pass
        return True

    def kill_current(self) -> bool:
        """Send SIGKILL to the running child. Returns True if it was alive."""
        h = self._handles.get(_resolve_target_id(self._current_target))
        if h is None or not h.running:
            return False
        try:
            h.kill()
        except Exception:
            pass
        return True

    def is_busy(self) -> bool:
        """True if the current target has a live handle."""
        h = self._handles.get(_resolve_target_id(self._current_target))
        return h is not None and h.running
