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
