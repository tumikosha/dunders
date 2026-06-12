"""Terminal handover: give the real terminal to a foreground program
(mc-style) and come back. Two strategies behind one protocol.

- RelayHandover (POSIX): one long-lived $SHELL in a PTY; during a command we
  byte-bridge the real terminal to that PTY raw (no emulation). Command end is
  signalled out of band on a dedicated FIFO (mc-style), so the visible stream
  is forwarded verbatim.
- SubprocessHandover (cross-platform): subprocess.run on the inherited tty.

Both run inside ``with app.suspend(): ...`` so Textual leaves its alt-screen,
restores the terminal, then redraws the whole UI on exit.
"""

from __future__ import annotations

import os
import select
import shlex
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

def _term_size() -> tuple[int, int]:
    """(cols, rows) of the real terminal, with a sane fallback."""
    try:
        sz = os.get_terminal_size()
        return sz.columns, sz.lines
    except OSError:
        return 80, 24


def _prompt_hook_setup(shell_name: str, fifo_path: str) -> str:
    """A shell command that makes the shell write ``<rc>\\n`` to ``fifo_path``
    right before each prompt — on a side channel, never on stdout.

    Routing the marker off stdout is the whole point: the visible byte stream
    is then forwarded verbatim, so a full-screen child's escape-sequence
    handshakes (kitty keyboard protocol, DA1) round-trip unmodified.
    """
    q = shlex.quote(fifo_path)
    if shell_name == "zsh":
        # Additive via precmd_functions so the user's own precmd hooks survive.
        return (
            f"__dunders_precmd() {{ printf '%d\\n' \"$?\" >> {q} }}; "
            f"precmd_functions+=(__dunders_precmd)\n"
        )
    if shell_name == "bash":
        # Prepend so we read $? before any pre-existing PROMPT_COMMAND mutates
        # it; restore $? afterwards for chained commands.
        return (
            f"__dunders_mark() {{ local s=$?; printf '%d\\n' \"$s\" >> {q}; "
            f"return $s; }}; "
            f'PROMPT_COMMAND="__dunders_mark${{PROMPT_COMMAND:+;$PROMPT_COMMAND}}"\n'
        )
    # Unknown / POSIX sh: best-effort via a PS1 command substitution. The
    # subshell inherits $? at entry, so the rc written is the last command's.
    return f"PS1='$(printf \"%d\\n\" \"$?\" >> {q})'\n"


def _cwd_capture_wrap(cmd: str, pwd_file: str) -> str:
    """Wrap a one-shot shell command so the shell's final ``$PWD`` is written to
    ``pwd_file`` after it runs, letting the caller learn where a ``cd`` inside
    the command left the (ephemeral) shell. The command's own exit status is
    preserved as the wrapper's status."""
    q = shlex.quote(pwd_file)
    return f"{cmd}\n__dunders_rc=$?\npwd > {q} 2>/dev/null\nexit $__dunders_rc"


def _read_pwd_file(path: Path) -> Path | None:
    """Read a captured ``$PWD`` back into a Path, or None if absent/empty."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return Path(text) if text else None


@runtime_checkable
class TerminalHandover(Protocol):
    # Set after run_foreground to the shell's resulting cwd (so a `cd` inside a
    # foreground command can move the active panel), or None if unknown.
    last_cwd: Path | None

    def run_foreground(self, cmd: str, cwd: Path) -> int: ...
    def command_screen(self, cwd: Path) -> None: ...
    def shutdown(self) -> None: ...


# Ctrl+O byte: toggles in/out of the interactive command screen.
_TOGGLE = 0x0F


class SubprocessHandover:
    """Cross-platform handover: a fresh subprocess on the inherited real tty."""

    name = "subprocess"

    def __init__(self, app, *, runner: Callable = subprocess.run) -> None:
        self._app = app
        self._runner = runner
        self.last_cwd: Path | None = None
        # Lazily-built relay used ONLY for the command screen, so Ctrl+O toggles
        # in/out exactly like in `we` (relay) mode. Commands still run via
        # subprocess; this relay's shell only backs the interactive screen.
        self._relay: "RelayHandover | None" = None

    def run_foreground(self, cmd: str, cwd: Path) -> int:
        self.last_cwd = None
        # The shell is ephemeral, so a `cd` inside it can't propagate back on its
        # own — capture the resulting $PWD inline before the shell exits.
        pwd_file = Path(tempfile.gettempdir()) / f"dunders-pwd-{uuid.uuid4().hex}"
        with self._app.suspend():
            result = self._runner(
                _cwd_capture_wrap(cmd, str(pwd_file)), shell=True, cwd=str(cwd)
            )
        self.last_cwd = _read_pwd_file(pwd_file)
        return getattr(result, "returncode", 0) or 0

    def command_screen(self, cwd: Path) -> None:
        # On POSIX with a real tty, reuse the relay's interactive screen so the
        # user toggles back with Ctrl+O (consistent with `we`).
        if sys.platform != "win32" and sys.stdin.isatty():
            if self._relay is None:
                self._relay = RelayHandover(self._app)
            self._relay.command_screen(cwd)
            return
        # Windows / no tty: drop into a plain interactive shell; the user
        # returns with `exit` / Ctrl+D (no way to intercept a toggle key).
        shell = os.environ.get("SHELL") or os.environ.get("COMSPEC", "/bin/sh")
        with self._app.suspend():
            subprocess.run([shell], cwd=str(cwd))

    def shutdown(self) -> None:
        if self._relay is not None:
            self._relay.shutdown()
            self._relay = None


class RelayHandover:
    """POSIX persistent-subshell relay (Midnight-Commander style).

    One ``$SHELL -i`` lives in a PTY. A prompt hook installed once at startup
    (see :func:`_prompt_hook_setup`) writes the exit code to a dedicated FIFO
    when the shell returns to its prompt. During the command the real terminal
    is bridged raw to the PTY (no emulation); completion is detected on the
    FIFO, so the visible byte stream is forwarded verbatim.
    """

    name = "relay"

    def __init__(self, app) -> None:
        self._app = app
        self._proc = None  # ptyprocess.PtyProcess | None
        self._fifo_path: Path | None = None
        self._fifo_fd: int = -1
        self._fifo_buf: bytes = b""
        self.last_cwd: Path | None = None

    def _open_fifo(self) -> None:
        """Create the completion FIFO and open it O_RDWR|O_NONBLOCK.

        O_RDWR keeps a writer (us) permanently attached, so the read end never
        sees a spurious EOF — and ``select`` never spins — in the gaps between
        the shell's per-command marker writes.
        """
        self._fifo_path = (
            Path(tempfile.gettempdir()) / f"dunders-{uuid.uuid4().hex}.fifo"
        )
        os.mkfifo(self._fifo_path, 0o600)
        self._fifo_fd = os.open(self._fifo_path, os.O_RDWR | os.O_NONBLOCK)
        self._fifo_buf = b""

    def _read_rc_from_fifo(self) -> int | None:
        """Drain pending FIFO bytes; return the most recent parsed exit code,
        or None if no complete ``<rc>\\n`` line has arrived yet."""
        if self._fifo_fd < 0:
            return None
        try:
            data = os.read(self._fifo_fd, 4096)
        except (BlockingIOError, OSError):
            return None
        if not data:
            return None
        self._fifo_buf += data
        rc: int | None = None
        while b"\n" in self._fifo_buf:
            line, _, self._fifo_buf = self._fifo_buf.partition(b"\n")
            s = line.strip()
            if not s:
                continue
            try:
                rc = int(s)
            except ValueError:
                continue
        return rc

    def _close_fifo(self) -> None:
        if self._fifo_fd >= 0:
            try:
                os.close(self._fifo_fd)
            except OSError:
                pass
            self._fifo_fd = -1
        if self._fifo_path is not None:
            try:
                os.unlink(self._fifo_path)
            except OSError:
                pass
            self._fifo_path = None

    def _ensure_shell(self, cwd: Path) -> None:
        if self._proc is not None and self._proc.isalive():
            return
        from ptyprocess import PtyProcess

        self._close_fifo()
        self._open_fifo()
        shell = os.environ.get("SHELL", "/bin/sh")
        env = dict(os.environ)
        cols, rows = _term_size()
        self._proc = PtyProcess.spawn(
            [shell, "-i"], cwd=str(cwd), env=env, dimensions=(rows, cols)
        )
        setup = _prompt_hook_setup(
            os.path.basename(shell), str(self._fifo_path)
        )
        self._proc.write(setup.encode("utf-8"))
        # Swallow shell startup + the hook-install echo, up to the first FIFO
        # marker, so the next command starts at a clean point.
        self._drain_startup()

    def _drain_startup(self, timeout: float = 5.0) -> None:
        """Discard shell-startup output from master up to the first FIFO marker
        (the hook firing at the first prompt)."""
        if self._proc is None:
            return
        while True:
            try:
                r, _, _ = select.select(
                    [self._proc.fd, *self._watch_fifo()], [], [], timeout
                )
            except InterruptedError:
                continue
            if not r:
                return  # timed out — proceed best-effort
            if self._proc.fd in r:
                try:
                    os.read(self._proc.fd, 65536)  # discard banner/echo
                except OSError:
                    return
            if self._fifo_fd >= 0 and self._fifo_fd in r:
                if self._read_rc_from_fifo() is not None:
                    return

    def _send_command(self, cmd: str, cwd: Path) -> None:
        """Write the command to the shell, prefixed with a silent ``cd`` to the
        active panel dir so the persistent subshell tracks the panel (mc
        parity). NEVER writes a sentinel — completion comes via the FIFO."""
        assert self._proc is not None
        line = f"cd {shlex.quote(str(cwd))} 2>/dev/null; {cmd}\n"
        self._proc.write(line.encode("utf-8", errors="replace"))

    def _sync_cwd(self, cwd: Path) -> None:
        """``cd`` the persistent subshell to the active panel dir before the
        interactive command screen appears. The shell is long-lived and may
        have been left in a different directory by an earlier command, so
        without this Ctrl+O would land in a stale cwd instead of the directory
        shown in the panel (mc parity)."""
        assert self._proc is not None
        line = f"cd {shlex.quote(str(cwd))} 2>/dev/null\n"
        self._proc.write(line.encode("utf-8", errors="replace"))

    def _pump(self, in_fds: list[int], master_fd: int, out) -> int:
        """Bridge bytes verbatim until the completion marker arrives on the
        FIFO. ``in_fds`` -> master (raw keys), master -> ``out`` (program
        output, forwarded byte-for-byte: no scanning, no holdback). Returns rc.
        """
        in_fds = list(in_fds)
        while True:
            watch = [master_fd, *self._watch_fifo(), *in_fds]
            try:
                readable, _, _ = select.select(watch, [], [])
            except InterruptedError:
                continue
            for fd in list(in_fds):
                if fd in readable:
                    data = os.read(fd, 65536)
                    if data:
                        os.write(master_fd, data)
                    else:
                        # EOF on this input fd: stop watching it so select does
                        # not spin reporting it readable forever.
                        in_fds = [f for f in in_fds if f != fd]
            if master_fd in readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError:
                    return 0
                if not chunk:
                    return 0  # master EOF: the shell died
                out.write(chunk)
                out.flush()
            if self._fifo_fd >= 0 and self._fifo_fd in readable:
                rc = self._read_rc_from_fifo()
                if rc is not None:
                    # Grab any final program output already queued on master
                    # (the marker is written by precmd, after the child's last
                    # write) before returning.
                    self._drain_master(master_fd, out)
                    return rc

    def _watch_fifo(self) -> list[int]:
        return [self._fifo_fd] if self._fifo_fd >= 0 else []

    def _drain_master(self, master_fd: int, out) -> None:
        """Non-blocking flush of whatever is currently queued on master."""
        while True:
            try:
                r, _, _ = select.select([master_fd], [], [], 0)
            except InterruptedError:
                continue
            if master_fd not in r:
                return
            try:
                chunk = os.read(master_fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            out.write(chunk)
            out.flush()

    def run_foreground(self, cmd: str, cwd: Path) -> int:
        self._ensure_shell(cwd)
        self.last_cwd = None
        import termios
        import tty

        stdin_fd = sys.stdin.fileno()
        old = termios.tcgetattr(stdin_fd)
        with self._app.suspend():
            tty.setraw(stdin_fd)
            try:
                assert self._proc is not None
                self._propagate_winsize()
                self._send_command(cmd, cwd)
                rc = self._pump([stdin_fd], self._proc.fd, sys.stdout.buffer)
            finally:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)
        # The persistent subshell now sits at whatever directory the command
        # left it in (a `cd` inside cmd persists). Read it back so the caller
        # can follow it with the panel (mc parity).
        self.last_cwd = self._capture_cwd()
        return rc

    def _capture_cwd(self, timeout: float = 2.0) -> Path | None:
        """Ask the live subshell for its current directory by writing ``pwd`` to
        a temp file and waiting for the next prompt marker on the FIFO. Returns
        the parsed directory, or None if the shell is gone / it times out.

        Runs after the terminal is restored: it only touches our private PTY and
        FIFO fds (never the real stdin/stdout), so nothing leaks to the screen.
        """
        if self._proc is None or not self._proc.isalive():
            return None
        pwd_file = Path(tempfile.gettempdir()) / f"dunders-pwd-{uuid.uuid4().hex}"
        try:
            self._proc.write(
                f"pwd > {shlex.quote(str(pwd_file))} 2>/dev/null\n".encode()
            )
        except OSError:
            return None
        # Drain the command echo on master and wait for the prompt marker that
        # fires once `pwd` has finished writing the file.
        while True:
            try:
                r, _, _ = select.select(
                    [self._proc.fd, *self._watch_fifo()], [], [], timeout
                )
            except InterruptedError:
                continue
            if not r:
                break  # timed out — give up best-effort
            if self._proc.fd in r:
                try:
                    os.read(self._proc.fd, 65536)  # discard echo
                except OSError:
                    break
            if self._fifo_fd >= 0 and self._fifo_fd in r:
                if self._read_rc_from_fifo() is not None:
                    break
        return _read_pwd_file(pwd_file)

    def _propagate_winsize(self) -> None:
        if self._proc is None:
            return
        cols, rows = _term_size()
        try:
            self._proc.setwinsize(rows, cols)
        except Exception:
            pass

    def command_screen(self, cwd: Path) -> None:
        """Ctrl+O: drop into the live subshell interactively until the user
        presses Ctrl+O again (mc-style toggle). Completion markers are stripped
        from the view; the user can type commands and watch output."""
        self._ensure_shell(cwd)
        import termios
        import tty

        stdin_fd = sys.stdin.fileno()
        old = termios.tcgetattr(stdin_fd)
        with self._app.suspend():
            tty.setraw(stdin_fd)
            try:
                assert self._proc is not None
                self._propagate_winsize()
                # Sync to the panel dir, which also nudges a fresh prompt.
                self._sync_cwd(cwd)
                self._interactive_relay(
                    stdin_fd, self._proc.fd, sys.stdout.buffer
                )
            finally:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)

    def _interactive_relay(self, stdin_fd: int, master_fd: int, out) -> None:
        """Bridge the real terminal to the subshell until Ctrl+O. Output is
        forwarded verbatim; completion markers arrive on the FIFO and are
        consumed (not shown, not treated as exit — we stay until the user
        toggles out or stdin hits EOF)."""
        while True:
            try:
                readable, _, _ = select.select(
                    [master_fd, *self._watch_fifo(), stdin_fd], [], [], 0.05
                )
            except InterruptedError:
                continue
            if master_fd in readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                out.write(chunk)
                out.flush()
            if self._fifo_fd >= 0 and self._fifo_fd in readable:
                self._read_rc_from_fifo()  # consume + discard completion markers
            if stdin_fd in readable:
                data = os.read(stdin_fd, 65536)
                if not data:
                    return
                i = data.find(_TOGGLE)
                if i != -1:
                    if i:
                        os.write(master_fd, data[:i])
                    return
                os.write(master_fd, data)

    def shutdown(self) -> None:
        import signal

        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.kill(signal.SIGTERM)
            except Exception:
                pass
        self._proc = None
        self._close_fifo()


def make_handover(app, mode: str) -> TerminalHandover:
    """Pick a handover strategy. ``mode`` is "relay" or "suspend"."""
    if mode == "suspend":
        return SubprocessHandover(app)
    if sys.platform == "win32":
        app.notify(
            "relay terminal mode is POSIX-only; using suspend mode",
            severity="warning",
        )
        return SubprocessHandover(app)
    if not sys.stdin.isatty():
        return SubprocessHandover(app)
    return RelayHandover(app)
