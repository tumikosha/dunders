# Relay Kitty Transparent Handover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make tyui's `relay` terminal handover host full-screen / kitty-keyboard-protocol TUIs (notably `claude`, `Shift+Enter`) while keeping its persistent `$SHELL -i` session — full Midnight Commander parity.

**Architecture:** Move command-completion detection off the program's stdout stream onto a dedicated FIFO (mc's `subshell_pipe` approach). The terminal byte bridge then forwards verbatim — no scanning, no 64-byte holdback — so escape-sequence handshakes (kitty, DA1) round-trip unmodified. Per-command `cd` to the active panel dir keeps the persistent shell's cwd in lockstep.

**Tech Stack:** Python ≥3.12, `ptyprocess`, POSIX `os.mkfifo`/`select`/`termios`/`tty`, pytest.

---

## Background for the implementer (read once)

The file you are changing is `tyui/fm/console/handover.py`. Two handover strategies live there:

- `RelayHandover` — a long-lived `$SHELL -i` in a PTY (the one we are fixing).
- `SubprocessHandover` — `subprocess.run` on the inherited tty (the working fallback; **do not touch its behaviour**).

Today `RelayHandover` detects when a command finishes by scanning the program's
stdout for a sentinel `TYUI_END_<tok>_<rc>` via `scan_sentinel`, which **holds
back the last 64 bytes** of every read until more output arrives. That holdback
traps claude's small kitty/DA1 query burst, claude times out, falls back to
legacy keys, and `Shift+Enter` renders as garbage.

The fix routes the completion marker to a FIFO and forwards the PTY stream
byte-for-byte.

### Conventions
- POSIX-only path; non-POSIX / no-tty already degrade to `SubprocessHandover` in `make_handover` — leave that alone.
- Tests substitute a plain `os.pipe()` read-end for the FIFO fd where a real FIFO is unnecessary; production uses a real FIFO opened `O_RDWR | O_NONBLOCK`.
- Run the full handover suite with: `pytest tests/fm/console/test_handover.py -v`
- Lint with: `ruff check tyui/fm/console/handover.py`

### Final state of `RelayHandover` (reference — built up across tasks)
New/changed instance attributes (set in `__init__`): `self._fifo_path: Path | None = None`, `self._fifo_fd: int = -1`, `self._fifo_buf: bytes = b""`. The `self._token` attribute is removed. New methods: `_open_fifo`, `_read_rc_from_fifo`, `_drain_master`, `_drain_startup` (replaces `_drain_to_marker`). Changed signatures: `_send_command(cmd, cwd)`, `_pump`/`_interactive_relay` (FIFO-aware). Module-level `scan_sentinel` and `_END_RE` are deleted in the final task.

---

## File Structure

- Modify: `tyui/fm/console/handover.py` — all production changes live here.
- Modify: `tests/fm/console/test_handover.py` — update obsolete tests, add new ones.
- Unchanged: `tyui/app.py` — `_run_handover_command` already passes `cwd` to `run_foreground`; cwd-sync is internal to the handover. No app changes.

---

## Task 1: Spike — confirm the holdback hypothesis (throwaway, manual)

**Purpose:** Cheaply confirm that the stdout holdback (not the PTY layer) is what breaks kitty, before doing the real rework. This task writes NO permanent code and is NOT committed.

**Files:** none committed (temporary local edit, reverted at the end).

- [ ] **Step 1: Temporarily disable holdback in `_pump`**

In `tyui/fm/console/handover.py`, in `RelayHandover._pump`, replace the master-read block so output is forwarded verbatim while the sentinel is still detected on a copy (temporary hack):

```python
            if master_fd in readable:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    return 0
                if not chunk:
                    return 0
                out.write(chunk)          # SPIKE: forward verbatim, no holdback
                out.flush()
                buf.extend(chunk)
                _, rc, buf = scan_sentinel(buf)   # detect completion only
                if rc is not None:
                    return rc
```

- [ ] **Step 2: Run claude through relay and test Shift+Enter (manual)**

Run: `tyui` in a real terminal that uses the kitty protocol (e.g. PyCharm terminal). Ensure the run-mode chip is `relay`. Type `claude` on the command line, press Enter. In claude, type some text and press `Shift+Enter`.

Expected (hypothesis confirmed): the newline is inserted cleanly, NO garbage characters appear in claude's input area.

- [ ] **Step 3: Decision gate**

- **Garbage gone** → hypothesis CONFIRMED. Proceed to Task 2.
- **Still garbled** → STOP. Do not build the FIFO rework. Pivot: capture observations (does `infocmp`/`$TERM` differ inside the relay? does copying real-terminal termios onto the PTY help?) and revise the plan with the brainstorming/spec author before continuing.

- [ ] **Step 4: Revert the spike edit**

Run: `git checkout -- tyui/fm/console/handover.py`
Expected: working tree clean (`git status` shows no changes). The real implementation starts from the unmodified file.

---

## Task 2: FIFO lifecycle + rc reader

**Files:**
- Modify: `tyui/fm/console/handover.py` (`RelayHandover.__init__`, new `_open_fifo`, `_read_rc_from_fifo`, `shutdown`)
- Test: `tests/fm/console/test_handover.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/fm/console/test_handover.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/fm/console/test_handover.py -k "fifo" -v`
Expected: FAIL (`AttributeError` / methods not defined).

- [ ] **Step 3: Implement FIFO lifecycle + rc reader**

In `handover.py`, add imports near the top (after the existing imports):

```python
import shlex
import tempfile
```

Change `RelayHandover.__init__` to:

```python
    def __init__(self, app) -> None:
        self._app = app
        self._proc = None  # ptyprocess.PtyProcess | None
        self._fifo_path: Path | None = None
        self._fifo_fd: int = -1
        self._fifo_buf: bytes = b""
```

Add these methods to `RelayHandover`:

```python
    def _open_fifo(self) -> None:
        """Create the completion FIFO and open it O_RDWR|O_NONBLOCK.

        O_RDWR keeps a writer (us) permanently attached, so the read end never
        sees a spurious EOF — and ``select`` never spins — in the gaps between
        the shell's per-command marker writes.
        """
        self._fifo_path = (
            Path(tempfile.gettempdir()) / f"tyui-{uuid.uuid4().hex}.fifo"
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
```

Replace the existing `shutdown` with one that also tears down the FIFO:

```python
    def shutdown(self) -> None:
        import signal

        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.kill(signal.SIGTERM)
            except Exception:
                pass
        self._proc = None
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/fm/console/test_handover.py -k "fifo" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tyui/fm/console/handover.py tests/fm/console/test_handover.py
git commit -m "feat(handover): add completion FIFO lifecycle and rc reader"
```

---

## Task 3: Prompt hook writes the marker to the FIFO

**Files:**
- Modify: `tyui/fm/console/handover.py` (`_prompt_hook_setup`)
- Test: `tests/fm/console/test_handover.py`

- [ ] **Step 1: Replace the obsolete hook test with a FIFO-based one**

In `tests/fm/console/test_handover.py`, DELETE `test_prompt_hook_setup_emits_matchable_marker` and add:

```python
def test_prompt_hook_setup_writes_rc_to_fifo():
    from tyui.fm.console.handover import _prompt_hook_setup

    fifo = "/tmp/tyui-test.fifo"
    for shell in ("zsh", "bash", "sh", "fish-unknown"):
        setup = _prompt_hook_setup(shell, fifo)
        assert fifo in setup          # the marker is routed to the FIFO path
        assert "printf" in setup
        assert "TYUI_END" not in setup  # no in-band stdout sentinel anymore
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/fm/console/test_handover.py -k "prompt_hook" -v`
Expected: FAIL (current hook emits `TYUI_END...` to stdout, not the FIFO path).

- [ ] **Step 3: Rewrite `_prompt_hook_setup`**

Replace the whole `_prompt_hook_setup` function body with:

```python
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
            f"__tyui_precmd() {{ printf '%d\\n' \"$?\" >> {q} }}; "
            f"precmd_functions+=(__tyui_precmd)\n"
        )
    if shell_name == "bash":
        # Prepend so we read $? before any pre-existing PROMPT_COMMAND mutates
        # it; restore $? afterwards for chained commands.
        return (
            f"__tyui_mark() {{ local s=$?; printf '%d\\n' \"$s\" >> {q}; "
            f"return $s; }}; "
            f'PROMPT_COMMAND="__tyui_mark${{PROMPT_COMMAND:+;$PROMPT_COMMAND}}"\n'
        )
    # Unknown / POSIX sh: best-effort via a PS1 command substitution. The
    # subshell inherits $? at entry, so the rc written is the last command's.
    return f"PS1='$(printf \"%d\\n\" \"$?\" >> {q})'\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/fm/console/test_handover.py -k "prompt_hook" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tyui/fm/console/handover.py tests/fm/console/test_handover.py
git commit -m "feat(handover): route completion marker to FIFO in prompt hook"
```

---

## Task 4: Transparent `_pump` (verbatim forward, FIFO completion)

**Files:**
- Modify: `tyui/fm/console/handover.py` (`_pump`, new `_drain_master`)
- Test: `tests/fm/console/test_handover.py`

- [ ] **Step 1: Replace the obsolete pump tests with FIFO-driven ones**

In `tests/fm/console/test_handover.py`, DELETE `test_relay_pump_emits_output_and_stops_at_sentinel` and `test_relay_pump_handles_input_fd_eof`, and add:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/fm/console/test_handover.py -k "pump" -v`
Expected: FAIL (current `_pump` scans the stream and ignores `_fifo_fd`).

- [ ] **Step 3: Rewrite `_pump` and add `_drain_master`**

Replace the whole `_pump` method with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/fm/console/test_handover.py -k "pump" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tyui/fm/console/handover.py tests/fm/console/test_handover.py
git commit -m "feat(handover): transparent verbatim pump with FIFO completion"
```

---

## Task 5: Per-command cwd sync in `_send_command`

**Files:**
- Modify: `tyui/fm/console/handover.py` (`_send_command`, `run_foreground` call site)
- Test: `tests/fm/console/test_handover.py`

- [ ] **Step 1: Replace the obsolete send-command test**

In `tests/fm/console/test_handover.py`, REPLACE `test_relay_sends_only_command_no_sentinel` with:

```python
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
    assert "TYUI_END" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/fm/console/test_handover.py -k "send_command" -v`
Expected: FAIL (current `_send_command` takes only `cmd` and does not cd).

- [ ] **Step 3: Rewrite `_send_command` and its caller**

Replace `_send_command` with:

```python
    def _send_command(self, cmd: str, cwd: Path) -> None:
        """Write the command to the shell, prefixed with a silent ``cd`` to the
        active panel dir so the persistent subshell tracks the panel (mc
        parity). NEVER writes a sentinel — completion comes via the FIFO."""
        assert self._proc is not None
        line = f"cd {shlex.quote(str(cwd))} 2>/dev/null; {cmd}\n"
        self._proc.write(line.encode("utf-8", errors="replace"))
```

In `run_foreground`, update the call site from `self._send_command(cmd)` to:

```python
                self._send_command(cmd, cwd)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/fm/console/test_handover.py -k "send_command" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tyui/fm/console/handover.py tests/fm/console/test_handover.py
git commit -m "feat(handover): cd subshell to active panel dir per command"
```

---

## Task 6: Wire `_ensure_shell` to the FIFO + replace `_drain_to_marker`

**Files:**
- Modify: `tyui/fm/console/handover.py` (`_ensure_shell`, replace `_drain_to_marker` with `_drain_startup`)
- Test: `tests/fm/console/test_handover.py`

- [ ] **Step 1: Update the real-command integration test**

In `tests/fm/console/test_handover.py`, REPLACE `test_relay_runs_real_command` with:

```python
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
        assert b"TYUI_END" not in out.getvalue()  # no marker leaks to stdout
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
        h.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/fm/console/test_handover.py::test_relay_runs_real_command -v`
Expected: FAIL (`_ensure_shell` still calls `_drain_to_marker`, no FIFO created → `_fifo_fd` is -1).

- [ ] **Step 3: Rewrite `_ensure_shell` and replace `_drain_to_marker`**

Replace `_ensure_shell` with:

```python
    def _ensure_shell(self, cwd: Path) -> None:
        if self._proc is not None and self._proc.isalive():
            return
        from ptyprocess import PtyProcess

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
```

Replace the whole `_drain_to_marker` method with `_drain_startup`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/fm/console/test_handover.py::test_relay_runs_real_command -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tyui/fm/console/handover.py tests/fm/console/test_handover.py
git commit -m "feat(handover): spawn shell with FIFO marker and drain startup via FIFO"
```

---

## Task 7: FIFO-aware `_interactive_relay` (Ctrl+O screen)

**Files:**
- Modify: `tyui/fm/console/handover.py` (`_interactive_relay`)
- Test: `tests/fm/console/test_handover.py`

- [ ] **Step 1: Replace the marker-stripping interactive test**

In `tests/fm/console/test_handover.py`, REPLACE `test_interactive_relay_strips_markers_does_not_exit_on_them` with:

```python
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
        assert b"TYUI_END" not in out.getvalue()
    finally:
        os.close(fr)
        os.close(fw)
        os.close(master)
        os.close(slave)
        os.close(stdin_r)
```

(Leave `test_interactive_relay_exits_on_toggle_and_forwards_prefix` as-is; it sets no FIFO, so `_fifo_fd` stays -1 and is skipped by `_watch_fifo`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/fm/console/test_handover.py -k "interactive_relay" -v`
Expected: FAIL (current `_interactive_relay` scans the stream for sentinels and does not watch the FIFO).

- [ ] **Step 3: Rewrite `_interactive_relay`**

Replace the whole `_interactive_relay` method with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/fm/console/test_handover.py -k "interactive_relay" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add tyui/fm/console/handover.py tests/fm/console/test_handover.py
git commit -m "feat(handover): FIFO-aware interactive command screen"
```

---

## Task 8: Remove dead `scan_sentinel`/`_END_RE` and finalise

**Files:**
- Modify: `tyui/fm/console/handover.py` (delete `scan_sentinel`, `_END_RE`, unused `re` import; update module docstring)
- Test: `tests/fm/console/test_handover.py` (delete `scan_sentinel` tests + its import)

- [ ] **Step 1: Delete the `scan_sentinel` unit tests and import**

In `tests/fm/console/test_handover.py`:
- Remove `scan_sentinel` from the import line `from tyui.fm.console.handover import SubprocessHandover, scan_sentinel` → leave `from tyui.fm.console.handover import SubprocessHandover`.
- DELETE these tests: `test_scan_sentinel_no_marker_holds_back_tail`, `test_scan_sentinel_emits_all_but_tail_when_long`, `test_scan_sentinel_finds_marker_and_extracts_rc`, `test_scan_sentinel_negative_rc`, `test_scan_sentinel_ignores_unexpanded_echo_line`, `test_scan_sentinel_crlf_line_ending`, `test_scan_sentinel_exact_tail_boundary_holds_all`.

- [ ] **Step 2: Delete dead production code**

In `tyui/fm/console/handover.py`:
- DELETE the `_END_RE = re.compile(...)` module constant and its preceding comment.
- DELETE the entire `scan_sentinel` function.
- DELETE the now-unused `import re` line.
- Update the module docstring's RelayHandover bullet to reflect the FIFO design, e.g. replace the sentence about the echoed sentinel with: "one long-lived $SHELL in a PTY; during a command we byte-bridge the real terminal to that PTY raw (no emulation). Command end is signalled out of band on a dedicated FIFO (mc-style), so the visible stream is forwarded verbatim."

- [ ] **Step 3: Run the full handover suite**

Run: `pytest tests/fm/console/test_handover.py -v`
Expected: PASS, no errors, no references to `scan_sentinel`/`_END_RE`.

- [ ] **Step 4: Run the broader console + fm suites and lint**

Run: `pytest tests/fm/console/ tests/fm/test_run_mode_toggle.py tests/fm/test_run_executable.py tests/fm/test_we_mc_mode.py -v`
Expected: PASS.

Run: `ruff check tyui/fm/console/handover.py`
Expected: no warnings (no unused `re`/`scan_sentinel`).

- [ ] **Step 5: Commit**

```bash
git add tyui/fm/console/handover.py tests/fm/console/test_handover.py
git commit -m "refactor(handover): drop stdout sentinel scanning (FIFO replaces it)"
```

---

## Task 9: End-to-end manual verification

**Files:** none (manual acceptance).

- [ ] **Step 1: Verify claude / Shift+Enter in relay mode**

Run `tyui` in a kitty-protocol terminal (PyCharm). Run-mode chip = `relay`. Launch `claude`, type text, press `Shift+Enter`.
Expected: clean newline insertion, no garbage. (This is the original symptom from HANDOVER.md, now fixed in relay.)

- [ ] **Step 2: Verify persistent session + cwd sync**

In relay mode: run `export FOO=bar` then `echo $FOO` → prints `bar` (session persists). Navigate the active panel to another directory, run `pwd` → prints the panel's directory (cwd sync). Activate a venv in one command, run `which python` in the next → reflects the venv.

- [ ] **Step 3: Verify Ctrl+O command screen**

Press Ctrl+O → drops into the live subshell, prompt visible, no stray `TYUI_END`/marker text. Type a command, see output. Press Ctrl+O again → returns to tyui.

- [ ] **Step 4: Verify other TUIs and plain commands**

Run `vim`, `htop`, `less somefile` — all render and accept keys correctly; on exit, control returns to tyui. Run `ls`, `git status` — output appears, exit codes correct (`false` then a command shows non-zero handling is unaffected).

- [ ] **Step 5: Update HANDOVER.md**

Edit `tyui/fm/console/HANDOVER.md`: mark relay as now hosting kitty-protocol TUIs via the side-channel FIFO; move the old "Possible future fixes #1" to "Done". Commit:

```bash
git add tyui/fm/console/HANDOVER.md
git commit -m "docs(handover): relay now hosts kitty TUIs via side-channel FIFO"
```

---

## Self-review notes

- **Spec coverage:** spike (Task 1) ↔ spec Component 1; FIFO side channel (Tasks 2,3,6) ↔ Component 2; transparent bridge (Task 4) ↔ Component 3; cwd sync (Task 5) ↔ Component 4; interactive screen (Task 7) ↔ spec; tests across all tasks ↔ Testing section; graceful degradation unchanged in `make_handover` ↔ Risks/rollback.
- **Type consistency:** `_send_command(cmd, cwd)`, `_read_rc_from_fifo() -> int | None`, `_watch_fifo() -> list[int]`, `_drain_startup`, `_drain_master`, attrs `_fifo_path`/`_fifo_fd`/`_fifo_buf` are used identically across Tasks 2–8.
- **Deviation from spec:** the prompt hook embeds the literal FIFO path (shlex-quoted) directly rather than reading a `TYUI_DONE_FIFO` env var — fewer moving parts, no reliance on env inheritance. Functionally equivalent.
