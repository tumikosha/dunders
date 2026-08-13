"""The $EDITOR wrapper: what reaches the editor, and what reaches Claude back.

cc-edit appends the session transcript below a sentinel line and cuts it away on
exit. The failure modes are deliberately silent — a wrapper that printed errors
would corrupt the prompt buffer — so these tests read the log instead.

HOME points at tmp_path throughout; the real ~/.claude is never touched.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

CC_EDIT = Path(__file__).resolve().parent.parent / "skills" / "setup" / "scripts" / "cc-edit"
MARKER = "HISTORY BELOW"

# Every record shape the renderer has to filter out, plus the two it must keep.
TRANSCRIPT = """\
{"type":"user","message":{"content":"first question"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"first answer"}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls"}}]}}
{"type":"user","message":{"content":[{"type":"tool_result","content":"secret-file.txt"}]}}
{"type":"user","isMeta":true,"message":{"content":"meta noise"}}
{"type":"user","isSidechain":true,"message":{"content":"subagent noise"}}
{"type":"user","message":{"content":"<system-reminder>hidden</system-reminder>"}}
{"type":"summary","summary":"not a message"}
not valid json at all
{"type":"user","message":{"content":"second question"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"second answer"}]}}
"""


@pytest.fixture
def env(tmp_path):
    """A HOME with a session-map entry pointing at a synthetic transcript."""
    (tmp_path / ".claude" / "session-map").mkdir(parents=True)
    transcript = tmp_path / "sess.jsonl"
    transcript.write_text(TRANSCRIPT)
    pid = str(os.getpid())
    (tmp_path / ".claude/session-map" / f"{pid}.json").write_text(
        json.dumps({"transcript_path": str(transcript), "session_id": "s1"})
    )
    return {
        "home": tmp_path,
        "pid": pid,
        "log": tmp_path / ".claude" / "cc-edit.log",
    }


def run(env, buf, real_editor="/usr/bin/true", **extra):
    return subprocess.run(
        ["bash", str(CC_EDIT), str(buf)],
        env={
            **os.environ,
            "HOME": str(env["home"]),
            "CC_REAL_EDITOR": real_editor,
            "CLAUDE_CODE_MESSAGING_SOCKET": f"/tmp/cc-socks/{env['pid']}.sock",
            **extra,
        },
        capture_output=True, text=True, timeout=30,
    )


def capturing_editor(tmp_path):
    """An editor stand-in that saves the buffer exactly as it was handed over."""
    seen = tmp_path / "seen.txt"
    script = tmp_path / "fake-editor"
    script.write_text(f'#!/usr/bin/env bash\ncp "$1" "{seen}"\n')
    script.chmod(0o755)
    return script, seen


def test_transcript_is_resolved_through_the_messaging_socket(env, tmp_path):
    buf = tmp_path / "claude-prompt.md"
    buf.write_text("my typed prompt\n")
    run(env, buf)
    log = env["log"].read_text()
    assert f"resolved by : session-map/{env['pid']}.json" in log
    assert "buf after injection" in log
    assert "stripped ->" in log


def test_only_the_typed_text_goes_back_to_claude(env, tmp_path):
    buf = tmp_path / "claude-prompt.md"
    buf.write_text("my typed prompt\n")
    run(env, buf)
    out = buf.read_text()
    assert out.rstrip("\n") == "my typed prompt"
    assert MARKER not in out
    assert "first answer" not in out


def test_renderer_keeps_conversation_and_drops_the_rest(env, tmp_path):
    editor, seen = capturing_editor(tmp_path)
    buf = tmp_path / "claude-prompt.md"
    buf.write_text("prompt two\n")
    run(env, buf, real_editor=str(editor))

    shown = seen.read_text()
    assert "prompt two" in shown              # typed text stays above the marker
    assert "### user" in shown
    assert "first question" in shown          # plain-string content
    assert "second answer" in shown           # text-block content
    for noise in ("tool_use", "tool_result", "secret-file.txt", "meta noise",
                  "subagent noise", "hidden", "not a message"):
        assert noise not in shown, f"{noise!r} should have been filtered out"


def test_secret_shaped_env_vars_are_redacted_in_the_log(env, tmp_path):
    # The log is exactly the kind of file people paste into a chat, and
    # CLAUDE_CODE_OAUTH_TOKEN lives in this environment.
    buf = tmp_path / "claude-prompt.md"
    buf.write_text("x\n")
    run(env, buf, CLAUDE_CODE_OAUTH_TOKEN="sk-secret-value-1234")
    log = env["log"].read_text()
    assert "sk-secret-value-1234" not in log
    assert "CLAUDE_CODE_OAUTH_TOKEN=<redacted>" in log


@pytest.mark.parametrize("name", ["COMMIT_EDITMSG", "MERGE_MSG", "TAG_EDITMSG",
                                  "git-rebase-todo", "change.patch"])
def test_git_buffers_pass_straight_through(env, tmp_path, name):
    # $EDITOR is inherited by `git commit` too; a transcript in a commit message
    # would be a disaster.
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)
    buf = git_dir / name
    buf.write_text("fix: something\n")
    run(env, buf)
    assert buf.read_text() == "fix: something\n"
    assert MARKER not in buf.read_text()


def test_unresolvable_session_does_not_break_the_buffer(env, tmp_path):
    # Empty the map so neither the socket pid nor PPID resolves — PPID is the
    # test runner's, which the fixture does file an entry under.
    for entry in (env["home"] / ".claude/session-map").glob("*.json"):
        entry.unlink()
    buf = tmp_path / "claude-prompt.md"
    buf.write_text("prompt three\n")
    result = run(env, buf, CLAUDE_CODE_MESSAGING_SOCKET="/tmp/cc-socks/999999.sock")
    assert result.returncode == 0
    assert buf.read_text() == "prompt three\n"
    assert "transcript  : <NOT FOUND>" in env["log"].read_text()


def test_history_limit_keeps_the_most_recent_turns(env, tmp_path):
    editor, seen = capturing_editor(tmp_path)
    buf = tmp_path / "claude-prompt.md"
    buf.write_text("p\n")
    run(env, buf, real_editor=str(editor), CC_HISTORY_LINES="2")
    shown = seen.read_text()
    assert "second answer" in shown       # newest kept
    assert "first question" not in shown  # oldest trimmed


# ---------------------------------------------------------------------------
# A missing real editor — the plugin wires the wrapper but never installs `__`
# ---------------------------------------------------------------------------

def test_a_missing_real_editor_falls_back_instead_of_doing_nothing(env, tmp_path):
    """`ctrl+x ctrl+e` did nothing at all on a machine without dunders.

    The wrapper ran, injected the transcript, then died at the exec with
    `set -e` armed — no editor, and a buffer still carrying the whole
    transcript for Claude to swallow as the prompt.
    """
    buf = tmp_path / "claude-prompt.md"
    buf.write_text("typed text\n")

    result = run(env, buf, real_editor="definitely-not-installed",
                 VISUAL="/usr/bin/true")

    assert result.returncode == 0
    log = env["log"].read_text()
    assert "real editor 'definitely-not-installed' not found" in log
    assert "uv tool install dunders" in log        # says how to fix it
    assert "falling back to /usr/bin/true" in log
    # The transcript is still cut away, so Claude gets the prompt and nothing else.
    assert buf.read_text().rstrip("\n") == "typed text"
    assert "second answer" not in buf.read_text()


def test_the_fallback_never_picks_the_wrapper_itself(env, tmp_path):
    """$VISUAL can point back at cc-edit; choosing it would recurse."""
    decoy = tmp_path / "cc-edit"
    decoy.write_text("#!/usr/bin/env bash\nexit 0\n")
    decoy.chmod(0o755)
    # A PATH without the real `__` and with a harmless `nano`, so the chain has
    # somewhere to land that neither recurses nor blocks on a terminal.
    fake_nano = tmp_path / "nano"
    fake_nano.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_nano.chmod(0o755)

    buf = tmp_path / "claude-prompt.md"
    buf.write_text("typed text\n")
    result = run(env, buf, real_editor="definitely-not-installed",
                 VISUAL=str(decoy), PATH=f"{tmp_path}:/usr/bin:/bin")

    assert result.returncode == 0
    log = env["log"].read_text()
    assert f"falling back to {decoy}" not in log
    assert f"falling back to {fake_nano}" in log or "falling back to nano" in log


def test_an_editor_that_exits_nonzero_still_gets_the_buffer_stripped(env, tmp_path):
    """Otherwise `set -e` skips the strip and the transcript is sent."""
    failing = tmp_path / "failing-editor"
    failing.write_text("#!/usr/bin/env bash\nexit 3\n")
    failing.chmod(0o755)

    buf = tmp_path / "claude-prompt.md"
    buf.write_text("typed text\n")
    result = run(env, buf, real_editor=str(failing))

    assert result.returncode == 0
    assert "editor exited 3" in env["log"].read_text()
    assert buf.read_text().rstrip("\n") == "typed text"


# ---------------------------------------------------------------------------
# The install offer — the only moment with a terminal to ask on
# ---------------------------------------------------------------------------

def run_pty(env, buf, answer, tmp_path, real_editor="__", **extra):
    """Drive the wrapper on a real pty, typing `answer` at the prompt."""
    import pty as pty_mod
    import select

    master, slave = pty_mod.openpty()
    proc = subprocess.Popen(
        ["bash", str(CC_EDIT), str(buf)],
        stdin=slave, stdout=slave, stderr=slave,
        env={
            **os.environ,
            "HOME": str(env["home"]),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
            "CC_REAL_EDITOR": real_editor,
            "VISUAL": "",
            "CLAUDE_CODE_MESSAGING_SOCKET": f"/tmp/cc-socks/{env['pid']}.sock",
            **extra,
        },
    )
    os.close(slave)
    seen = b""
    os.write(master, answer.encode())
    while True:
        ready, _, _ = select.select([master], [], [], 20)
        if not ready:
            break
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        seen += chunk
    proc.wait(timeout=20)
    os.close(master)
    return seen.decode(errors="replace")


@pytest.fixture
def fake_uv(tmp_path):
    """A uv stand-in that "installs" a runnable `__` into ~/.local/bin."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    uv = bindir / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        'echo "uv called: $*"\n'
        'mkdir -p "$HOME/.local/bin"\n'
        'printf \'#!/usr/bin/env bash\\necho opened >> "$HOME/opened.txt"\\n\' '
        '> "$HOME/.local/bin/__"\n'
        'chmod +x "$HOME/.local/bin/__"\n'
    )
    uv.chmod(0o755)
    return uv


def test_a_missing_editor_offers_to_install_and_then_opens(env, tmp_path, fake_uv):
    buf = tmp_path / "claude-prompt.md"
    buf.write_text("typed text\n")

    out = run_pty(env, buf, "\n", tmp_path)          # Enter = the default yes

    assert "is not installed" in out
    assert "uv called: tool install --force" in out  # it really ran the installer
    assert (env["home"] / "opened.txt").exists()     # …and then opened the editor
    assert buf.read_text().rstrip("\n") == "typed text"


def test_declining_is_remembered(env, tmp_path, fake_uv):
    marker = env["home"] / ".claude/dunders-cc/autoinstall-declined"
    buf = tmp_path / "claude-prompt.md"

    buf.write_text("typed text\n")
    # A fallback that exits on its own — nano would block this pty forever.
    out = run_pty(env, buf, "n\n", tmp_path, VISUAL="/usr/bin/true")
    assert "not asking again" in out
    assert marker.exists()
    assert not (env["home"] / "opened.txt").exists()   # nothing was installed

    buf.write_text("typed again\n")
    out2 = run_pty(env, buf, "\n", tmp_path, VISUAL="/usr/bin/true")
    assert "is not installed" not in out2              # and it stays quiet after
    assert "declined earlier" in env["log"].read_text()
