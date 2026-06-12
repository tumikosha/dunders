import io
import os
import sys
from contextlib import contextmanager

import pytest

from dunders.fm.console.handover import SubprocessHandover


class _FakeApp:
    """Stand-in for DundersApp: suspend() is a no-op context manager."""

    def __init__(self):
        self.notes = []

    @contextmanager
    def suspend(self):
        yield

    def notify(self, msg, severity="information"):
        self.notes.append((severity, msg))


class _FakeCompleted:
    def __init__(self, rc):
        self.returncode = rc


def test_subprocess_handover_runs_in_suspend_and_returns_rc(tmp_path):
    calls = {}

    def fake_runner(cmd, shell, cwd):
        calls["cmd"] = cmd
        calls["shell"] = shell
        calls["cwd"] = cwd
        return _FakeCompleted(7)

    h = SubprocessHandover(_FakeApp(), runner=fake_runner)
    rc = h.run_foreground("htop", tmp_path)

    assert rc == 7
    assert calls["shell"] is True
    assert calls["cwd"] == str(tmp_path)
    # The command is wrapped to capture the shell's final $PWD, but the user's
    # command still leads and its exit status is preserved.
    assert calls["cmd"].startswith("htop\n")
    assert "pwd >" in calls["cmd"]
    assert "exit $__dunders_rc" in calls["cmd"]


def test_subprocess_handover_captures_cwd_after_cd(tmp_path):
    # A real shell: a `cd` inside the foreground command is reported via last_cwd
    # so the caller can follow it with the panel.
    import shlex as _shlex

    sub = tmp_path / "sub"
    sub.mkdir()
    h = SubprocessHandover(_FakeApp())  # real subprocess.run
    h.run_foreground(f"cd {_shlex.quote(str(sub))}", tmp_path)
    assert h.last_cwd is not None
    assert h.last_cwd.resolve() == sub.resolve()


def test_subprocess_handover_cwd_unchanged_without_cd(tmp_path):
    h = SubprocessHandover(_FakeApp())
    h.run_foreground("true", tmp_path)
    assert h.last_cwd is not None
    assert h.last_cwd.resolve() == tmp_path.resolve()


from dunders.fm.console.handover import RelayHandover, make_handover


def test_make_handover_suspend_mode_picks_subprocess():
    h = make_handover(_FakeApp(), "suspend")
    assert isinstance(h, SubprocessHandover)


def test_make_handover_windows_degrades_to_subprocess(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    app = _FakeApp()
    h = make_handover(app, "relay")
    assert isinstance(h, SubprocessHandover)
    assert app.notes  # a degradation warning was emitted


def test_make_handover_no_tty_degrades_to_subprocess(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    h = make_handover(_FakeApp(), "relay")
    assert isinstance(h, SubprocessHandover)


def test_make_handover_posix_tty_picks_relay(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    h = make_handover(_FakeApp(), "relay")
    assert isinstance(h, RelayHandover)


def test_relay_pump_forwards_output_verbatim_and_stops_on_fifo():
    h = RelayHandover(_FakeApp())
    fr, fw = os.pipe()          # stand-in FIFO
    h._fifo_fd = fr
    h._fifo_buf = b""
    master, slave = os.openpty()
    try:
        os.write(slave, b"hello world output")
        os.write(fw, b"0\n")    # completion marker on the side channel
        out = io.BytesIO()
        rc = h._pump([], master, out)
        assert rc == 0
        assert out.getvalue() == b"hello world output"
    finally:
        os.close(fr)
        os.close(fw)
        os.close(master)
        os.close(slave)


def test_relay_pump_does_not_hold_back_small_bursts():
    # Regression for the kitty bug: a <64-byte burst with no in-band sentinel
    # must reach the terminal, not sit in a holdback buffer.
    h = RelayHandover(_FakeApp())
    fr, fw = os.pipe()
    h._fifo_fd = fr
    h._fifo_buf = b""
    master, slave = os.openpty()
    try:
        os.write(slave, b"\x1b[?u")   # 4-byte kitty query, well under 64
        os.write(fw, b"0\n")
        out = io.BytesIO()
        rc = h._pump([], master, out)
        assert rc == 0
        assert out.getvalue() == b"\x1b[?u"  # forwarded, not held back
    finally:
        os.close(fr)
        os.close(fw)
        os.close(master)
        os.close(slave)


def test_relay_send_command_cds_to_cwd_and_carries_no_sentinel():
    # The per-command PTY write must (1) cd to the active panel dir so the
    # persistent shell tracks it, and (2) carry NO in-band sentinel — an
    # interactive child like htop would otherwise eat queued sentinel bytes.
    from pathlib import Path

    h = RelayHandover(_FakeApp())

    class _CapturingProc:
        def write(self, data):
            self.last = data

    h._proc = _CapturingProc()
    h._send_command("htop", Path("/tmp/some dir"))
    text = h._proc.last.decode()
    assert text.endswith("htop\n")
    assert "cd " in text
    assert "'/tmp/some dir'" in text  # shlex-quoted path
    assert "DUNDERS_END" not in text


def test_relay_sync_cwd_cds_to_panel_dir_with_no_command():
    # Ctrl+O into the live subshell must first cd to the active panel dir so
    # the long-lived shell tracks the panel (mc parity) — and it carries no
    # trailing command, only the cd.
    from pathlib import Path

    h = RelayHandover(_FakeApp())

    class _CapturingProc:
        def write(self, data):
            self.last = data

    h._proc = _CapturingProc()
    h._sync_cwd(Path("/tmp/some dir"))
    text = h._proc.last.decode()
    assert text.startswith("cd ")
    assert "'/tmp/some dir'" in text  # shlex-quoted path
    assert text.endswith("\n")
    assert ";" not in text  # no chained command, unlike _send_command


def test_relay_command_screen_syncs_cwd_to_panel(monkeypatch, tmp_path):
    # Reusing an already-alive subshell: command_screen must still cd to the
    # current panel dir before handing over the screen, otherwise it lands in
    # whatever directory the last command left it in (the reported bug).
    import shlex as _shlex
    import termios
    import tty

    h = RelayHandover(_FakeApp())

    class _CapturingProc:
        fd = 0

        def __init__(self):
            self.writes = []

        def write(self, data):
            self.writes.append(data)

    monkeypatch.setattr(
        h, "_ensure_shell", lambda cwd: setattr(h, "_proc", _CapturingProc())
    )
    monkeypatch.setattr(h, "_propagate_winsize", lambda: None)
    monkeypatch.setattr(h, "_interactive_relay", lambda *a, **k: None)
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: None)
    monkeypatch.setattr(termios, "tcsetattr", lambda fd, when, old: None)
    monkeypatch.setattr(tty, "setraw", lambda fd: None)

    class _FakeStdin:
        def fileno(self):
            return 0

    monkeypatch.setattr(sys, "stdin", _FakeStdin())

    h.command_screen(tmp_path)
    joined = b"".join(h._proc.writes).decode()
    assert "cd " in joined
    assert _shlex.quote(str(tmp_path)) in joined


def test_prompt_hook_setup_writes_rc_to_fifo():
    from dunders.fm.console.handover import _prompt_hook_setup

    fifo = "/tmp/dunders-test.fifo"
    for shell in ("zsh", "bash", "sh", "fish-unknown"):
        setup = _prompt_hook_setup(shell, fifo)
        assert fifo in setup          # the marker is routed to the FIFO path
        assert "printf" in setup
        assert "DUNDERS_END" not in setup  # no in-band stdout sentinel anymore


def test_subprocess_command_screen_delegates_to_relay_on_posix_tty(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    calls = []
    monkeypatch.setattr(
        RelayHandover, "command_screen", lambda self, cwd: calls.append(cwd)
    )
    h = SubprocessHandover(_FakeApp())
    h.command_screen(tmp_path)
    assert calls == [tmp_path]
    assert h._relay is not None


def test_subprocess_command_screen_falls_back_without_tty(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    ran = []
    monkeypatch.setattr(
        "dunders.fm.console.handover.subprocess.run",
        lambda *a, **k: ran.append((a, k)),
    )
    h = SubprocessHandover(_FakeApp())
    h.command_screen(tmp_path)
    assert ran  # fell back to launching a plain interactive shell
    assert h._relay is None


def test_subprocess_shutdown_closes_command_screen_relay(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(RelayHandover, "command_screen", lambda self, cwd: None)
    shut = []
    monkeypatch.setattr(RelayHandover, "shutdown", lambda self: shut.append(True))
    h = SubprocessHandover(_FakeApp())
    h.command_screen(tmp_path)
    h.shutdown()
    assert shut == [True]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty only")
def test_relay_runs_real_command(tmp_path):
    import signal

    def _alarm(_signum, _frame):
        raise TimeoutError("relay pump hung (prompt hook never fired)")

    h = RelayHandover(_FakeApp())
    h._ensure_shell(tmp_path)  # creates FIFO, installs hook, drains startup
    assert h._fifo_fd >= 0
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(10)
    try:
        out = io.BytesIO()
        # Completion is detected via the FIFO marker, not a fed-in sentinel.
        h._send_command("echo marker-hi", tmp_path)
        rc = h._pump([], h._proc.fd, out)
        assert rc == 0
        assert b"marker-hi" in out.getvalue()
        assert b"DUNDERS_END" not in out.getvalue()  # no marker leaks to stdout
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
        h.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty only")
def test_relay_capture_cwd_follows_cd(tmp_path):
    import signal

    def _alarm(_signum, _frame):
        raise TimeoutError("relay capture hung")

    import shlex as _shlex

    sub = tmp_path / "sub"
    sub.mkdir()
    h = RelayHandover(_FakeApp())
    h._ensure_shell(tmp_path)
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(10)
    try:
        # Run a `cd` into the subdir, then read the subshell's resulting cwd.
        h._send_command(f"cd {_shlex.quote(str(sub))}", tmp_path)
        h._pump([], h._proc.fd, io.BytesIO())
        captured = h._capture_cwd()
        assert captured is not None
        assert captured.resolve() == sub.resolve()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
        h.shutdown()


def test_cwd_capture_wrap_preserves_command_and_exit_status():
    from dunders.fm.console.handover import _cwd_capture_wrap

    wrapped = _cwd_capture_wrap("make all", "/tmp/x")
    assert wrapped.startswith("make all\n")
    assert "pwd > /tmp/x" in wrapped
    assert wrapped.strip().endswith("exit $__dunders_rc")


def test_read_pwd_file_reads_and_unlinks(tmp_path):
    from dunders.fm.console.handover import _read_pwd_file

    f = tmp_path / "pwd"
    f.write_text("/some/dir\n", encoding="utf-8")
    got = _read_pwd_file(f)
    assert got == __import__("pathlib").Path("/some/dir")
    assert not f.exists()  # consumed
    # Missing / empty -> None
    assert _read_pwd_file(tmp_path / "nope") is None
    (tmp_path / "empty").write_text("", encoding="utf-8")
    assert _read_pwd_file(tmp_path / "empty") is None


def test_interactive_relay_exits_on_toggle_and_forwards_prefix():
    # Ctrl+O (0x0f) leaves the command screen; bytes before it reach the shell.
    import tty

    h = RelayHandover(_FakeApp())
    master, slave = os.openpty()
    tty.setraw(slave)  # so "ab" (no newline) is readable without canonical wait
    stdin_r, stdin_w = os.pipe()
    try:
        os.write(stdin_w, b"ab\x0fcd")  # "ab" -> shell, then toggle -> exit
        h._interactive_relay(stdin_r, master, io.BytesIO())
        # "ab" was forwarded to the master (readable from the slave side).
        import select as _sel

        r, _, _ = _sel.select([slave], [], [], 1.0)
        assert slave in r
        assert os.read(slave, 64) == b"ab"
    finally:
        os.close(master)
        os.close(slave)
        os.close(stdin_r)
        os.close(stdin_w)


def test_relay_pump_handles_input_fd_eof():
    h = RelayHandover(_FakeApp())
    fr, fw = os.pipe()
    h._fifo_fd = fr
    h._fifo_buf = b""
    r, w = os.pipe()
    os.close(w)  # r is now at EOF (select-readable, read returns b"")
    master, slave = os.openpty()
    try:
        os.write(slave, b"out")
        os.write(fw, b"0\n")
        out = io.BytesIO()
        rc = h._pump([r], master, out)
        assert rc == 0
        assert b"out" in out.getvalue()
    finally:
        os.close(fr)
        os.close(fw)
        os.close(r)
        os.close(master)
        os.close(slave)


def test_interactive_relay_consumes_fifo_markers_without_exiting():
    # Completion markers now arrive on the FIFO; they must be consumed (so the
    # fd stops being readable) and must NOT cause an exit — we leave only on the
    # Ctrl+O toggle or stdin EOF. The visible stream is forwarded verbatim.
    h = RelayHandover(_FakeApp())
    fr, fw = os.pipe()
    h._fifo_fd = fr
    h._fifo_buf = b""
    master, slave = os.openpty()
    stdin_r, stdin_w = os.pipe()
    try:
        os.write(slave, b"out")
        os.write(fw, b"0\n")        # a completion marker; must not exit
        os.close(stdin_w)           # stdin EOF -> relay returns
        out = io.BytesIO()
        h._interactive_relay(stdin_r, master, out)
        assert b"out" in out.getvalue()
        assert b"DUNDERS_END" not in out.getvalue()
    finally:
        os.close(fr)
        os.close(fw)
        os.close(master)
        os.close(slave)
        os.close(stdin_r)


def test_read_rc_from_fifo_parses_latest_complete_line():
    h = RelayHandover(_FakeApp())
    r, w = os.pipe()
    h._fifo_fd = r
    h._fifo_buf = b""
    try:
        os.write(w, b"0\n")
        assert h._read_rc_from_fifo() == 0
        os.write(w, b"7\n42\n")  # two markers in one read -> latest wins
        assert h._read_rc_from_fifo() == 42
    finally:
        os.close(r)
        os.close(w)


def test_read_rc_from_fifo_holds_partial_line():
    h = RelayHandover(_FakeApp())
    r, w = os.pipe()
    h._fifo_fd = r
    h._fifo_buf = b""
    try:
        os.write(w, b"13")          # no newline yet
        assert h._read_rc_from_fifo() is None
        os.write(w, b"\n")          # completes the line
        assert h._read_rc_from_fifo() == 13
    finally:
        os.close(r)
        os.close(w)


def test_open_fifo_creates_readable_fifo_and_shutdown_cleans_up():
    h = RelayHandover(_FakeApp())
    h._open_fifo()
    try:
        assert h._fifo_path is not None
        assert h._fifo_path.exists()
        assert h._fifo_fd >= 0
        # A writer using the path delivers an rc to the reader.
        wfd = os.open(str(h._fifo_path), os.O_WRONLY)
        try:
            os.write(wfd, b"5\n")
        finally:
            os.close(wfd)
        assert h._read_rc_from_fifo() == 5
    finally:
        path = h._fifo_path
        h.shutdown()
        assert h._fifo_fd == -1
        assert h._fifo_path is None
        assert not path.exists()
