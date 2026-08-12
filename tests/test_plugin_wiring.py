"""Plugin-native install/uninstall of the Claude Code integration.

`cc-wire` runs as a SessionStart hook and does the one thing a plugin manifest
cannot: point $EDITOR at the wrapper, via the `env` block of settings.json.
`cc-edit` undoes that itself once the plugin is gone, because Claude Code has no
plugin-removal hook.

Every test drives the scripts as subprocesses with HOME redirected at tmp_path,
so the real ~/.claude is never touched.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "setup" / "scripts"


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude").mkdir()
    return tmp_path


@pytest.fixture
def plugin_root(tmp_path):
    """A stand-in for the plugin cache directory the hook is launched from."""
    root = tmp_path / "plugin"
    dest = root / "skills" / "setup" / "scripts"
    dest.mkdir(parents=True)
    for name in ("cc-edit", "cc-session-map", "cc-wire"):
        shutil.copy2(SCRIPTS / name, dest / name)
        (dest / name).chmod(0o755)
    return root


def run_wire(home, plugin_root, **env):
    return subprocess.run(
        ["python3", str(plugin_root / "skills/setup/scripts/cc-wire")],
        env={**os.environ, "HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(plugin_root), **env},
        capture_output=True,
        text=True,
        timeout=30,
    )


def settings_of(home):
    p = home / ".claude" / "settings.json"
    return json.loads(p.read_text()) if p.exists() else {}


def test_wire_points_editor_at_the_shim(home, plugin_root):
    result = run_wire(home, plugin_root)
    assert result.returncode == 0
    # A SessionStart hook's output lands in the transcript, so it must be silent.
    assert result.stdout == ""

    shim = home / ".claude" / "dunders-cc" / "cc-edit"
    assert shim.is_file() and os.access(shim, os.X_OK)
    assert settings_of(home)["env"]["EDITOR"] == str(shim)

    state = json.loads((home / ".claude/dunders-cc/installed.json").read_text())
    assert state["plugin_root"] == str(plugin_root)
    assert state["managed_by"] == "plugin"


def test_wire_preserves_unrelated_settings(home, plugin_root):
    (home / ".claude/settings.json").write_text(
        json.dumps({"model": "opus", "env": {"FOO": "bar"}})
    )
    run_wire(home, plugin_root)
    settings = settings_of(home)
    assert settings["model"] == "opus"
    assert settings["env"]["FOO"] == "bar"
    assert settings["env"]["EDITOR"].endswith("dunders-cc/cc-edit")


def test_wire_is_idempotent(home, plugin_root):
    run_wire(home, plugin_root)
    first = (home / ".claude/settings.json").read_text()
    run_wire(home, plugin_root)
    assert (home / ".claude/settings.json").read_text() == first


def test_wire_does_not_clobber_a_foreign_editor(home, plugin_root):
    (home / ".claude/settings.json").write_text(json.dumps({"env": {"EDITOR": "nvim"}}))
    run_wire(home, plugin_root)
    # Someone else's editor stays; the integration stays inert rather than
    # silently taking over a setting the user chose.
    assert settings_of(home)["env"]["EDITOR"] == "nvim"


def test_wire_leaves_malformed_settings_untouched(home, plugin_root):
    broken = "{ this is not json"
    (home / ".claude/settings.json").write_text(broken)
    run_wire(home, plugin_root)
    assert (home / ".claude/settings.json").read_text() == broken


def test_wire_repoints_the_shim_after_a_version_bump(home, plugin_root, tmp_path):
    run_wire(home, plugin_root)
    newer = tmp_path / "plugin-0.2.0"
    shutil.copytree(plugin_root, newer)
    run_wire(home, newer)
    state = json.loads((home / ".claude/dunders-cc/installed.json").read_text())
    assert state["plugin_root"] == str(newer)
    # EDITOR keeps pointing at the stable shim, not into the versioned cache.
    assert settings_of(home)["env"]["EDITOR"] == str(home / ".claude/dunders-cc/cc-edit")


def test_wire_without_plugin_root_does_nothing(home, plugin_root):
    """install.sh owns the wiring when the hook is not launched by the plugin."""
    result = subprocess.run(
        ["python3", str(plugin_root / "skills/setup/scripts/cc-wire")],
        env={k: v for k, v in {**os.environ, "HOME": str(home)}.items()
             if k != "CLAUDE_PLUGIN_ROOT"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert not (home / ".claude/dunders-cc").exists()
    assert "env" not in settings_of(home)


def _run_edit(home, buf, real_editor="/usr/bin/true"):
    return subprocess.run(
        ["bash", str(home / ".claude/dunders-cc/cc-edit"), str(buf)],
        env={**os.environ, "HOME": str(home), "CC_REAL_EDITOR": real_editor},
        capture_output=True, text=True, timeout=30,
    )


def test_edit_self_removes_once_the_plugin_is_gone(home, plugin_root, tmp_path):
    run_wire(home, plugin_root)
    shutil.rmtree(plugin_root)  # as `/plugin uninstall` would

    buf = tmp_path / "claude-prompt.md"
    buf.write_text("my prompt\n")
    result = _run_edit(home, buf)

    assert result.returncode == 0
    assert "EDITOR" not in settings_of(home).get("env", {})
    assert not (home / ".claude/dunders-cc").exists()
    assert not (home / ".claude/session-map").exists()
    # The keystroke still had to open something: the buffer survives untouched.
    assert buf.read_text() == "my prompt\n"


def test_edit_self_removal_keeps_a_user_supplied_editor(home, plugin_root, tmp_path):
    run_wire(home, plugin_root)
    settings = settings_of(home)
    settings["env"]["EDITOR"] = "nvim"  # user re-pointed it after we wired ours
    (home / ".claude/settings.json").write_text(json.dumps(settings))
    shutil.rmtree(plugin_root)

    buf = tmp_path / "claude-prompt.md"
    buf.write_text("x\n")
    _run_edit(home, buf)
    assert settings_of(home)["env"]["EDITOR"] == "nvim"


def test_edit_still_injects_history_while_the_plugin_lives(home, plugin_root, tmp_path):
    run_wire(home, plugin_root)
    transcript = tmp_path / "sess.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"content":"earlier question"}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"earlier answer"}]}}\n'
    )
    map_dir = home / ".claude/session-map"
    map_dir.mkdir(parents=True, exist_ok=True)
    pid = str(os.getpid())
    (map_dir / f"{pid}.json").write_text(json.dumps({"transcript_path": str(transcript)}))

    buf = tmp_path / "claude-prompt.md"
    buf.write_text("typed text\n")
    seen = tmp_path / "seen.txt"
    fake = tmp_path / "fake-editor"
    fake.write_text(f'#!/usr/bin/env bash\ncp "$1" "{seen}"\n')
    fake.chmod(0o755)

    subprocess.run(
        ["bash", str(home / ".claude/dunders-cc/cc-edit"), str(buf)],
        env={**os.environ, "HOME": str(home), "CC_REAL_EDITOR": str(fake),
             "CLAUDE_CODE_MESSAGING_SOCKET": f"/tmp/cc-socks/{pid}.sock"},
        capture_output=True, text=True, timeout=30,
    )

    assert "earlier answer" in seen.read_text()   # history reached the editor
    # Stripped again. The injection opens with a blank line before the marker,
    # and the cut keeps it, so the text comes back with trailing whitespace.
    assert buf.read_text().rstrip("\n") == "typed text"
    assert "earlier answer" not in buf.read_text()
    assert (home / ".claude/dunders-cc").exists()  # no self-removal while alive
