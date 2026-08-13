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


# ---------------------------------------------------------------------------
# Uninstall detection: the registry, not the directory
# ---------------------------------------------------------------------------

def _as_cache_plugin(home, plugin_root, marketplace="dunders", name="dunders",
                     version="0.1.0"):
    """Move the fixture plugin to the real cache layout and register it.

    `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>` is what
    CLAUDE_PLUGIN_ROOT actually points at, and the key cc-wire derives from it
    is what the uninstall check looks up.
    """
    cache = home / ".claude/plugins/cache" / marketplace / name / version
    cache.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plugin_root, cache)
    registry = home / ".claude/plugins/installed_plugins.json"
    registry.write_text(json.dumps({
        "version": 2,
        "plugins": {f"{name}@{marketplace}": [{"installPath": str(cache)}]},
    }))
    return cache, registry


def test_wire_records_the_plugin_key(home, plugin_root):
    cache, _registry = _as_cache_plugin(home, plugin_root)
    run_wire(home, cache)
    state = json.loads((home / ".claude/dunders-cc/installed.json").read_text())
    assert state["plugin_key"] == "dunders@dunders"


def test_wire_records_the_install_even_when_it_declines_the_editor(home, plugin_root):
    """The shim is on disk either way, so it must stay removable."""
    (home / ".claude/settings.json").write_text(json.dumps({"env": {"EDITOR": "nvim"}}))
    run_wire(home, plugin_root)

    state = json.loads((home / ".claude/dunders-cc/installed.json").read_text())
    assert state["claimed_editor"] is False
    assert settings_of(home)["env"]["EDITOR"] == "nvim"


def test_edit_self_removes_when_uninstall_leaves_the_cache_behind(
    home, plugin_root, tmp_path
):
    """The bug: `/plugin uninstall` keeps the versioned cache directory.

    Testing the directory reads "still installed" forever, so the integration
    went on answering ctrl+x ctrl+e after being uninstalled.
    """
    cache, registry = _as_cache_plugin(home, plugin_root)
    run_wire(home, cache)

    # Uninstall as Claude Code performs it: the key goes, the directory stays.
    registry.write_text(json.dumps({"version": 2, "plugins": {}}))
    assert cache.is_dir()

    buf = tmp_path / "claude-prompt.md"
    buf.write_text("my prompt\n")
    result = _run_edit(home, buf)

    assert result.returncode == 0
    assert "EDITOR" not in settings_of(home).get("env", {})
    assert not (home / ".claude/dunders-cc").exists()
    assert buf.read_text() == "my prompt\n"


def test_edit_stays_put_while_the_plugin_is_registered(home, plugin_root, tmp_path):
    cache, _registry = _as_cache_plugin(home, plugin_root)
    run_wire(home, cache)

    buf = tmp_path / "claude-prompt.md"
    buf.write_text("typed\n")
    _run_edit(home, buf)

    assert (home / ".claude/dunders-cc/cc-edit").exists()
    assert settings_of(home)["env"]["EDITOR"] == str(home / ".claude/dunders-cc/cc-edit")


def test_edit_stays_put_for_a_merely_disabled_plugin(home, plugin_root, tmp_path):
    """Disabled is not uninstalled — the registry still lists it."""
    cache, _registry = _as_cache_plugin(home, plugin_root)
    run_wire(home, cache)
    settings = settings_of(home)
    settings["enabledPlugins"] = {"dunders@dunders": False}
    (home / ".claude/settings.json").write_text(json.dumps(settings))

    buf = tmp_path / "claude-prompt.md"
    buf.write_text("typed\n")
    _run_edit(home, buf)

    assert (home / ".claude/dunders-cc/cc-edit").exists()


def test_edit_reports_a_shell_profile_that_still_exports_the_editor(
    home, plugin_root, tmp_path
):
    """The other half of "I uninstalled it and ctrl+g still opens it"."""
    cache, registry = _as_cache_plugin(home, plugin_root)
    run_wire(home, cache)
    (home / ".zshrc").write_text(
        "# >>> dunders claude-code editor >>>\n"
        f'export EDITOR="{home}/.claude/dunders-cc/cc-edit"\n'
        "# <<< dunders claude-code editor <<<\n"
    )
    registry.write_text(json.dumps({"version": 2, "plugins": {}}))

    buf = tmp_path / "claude-prompt.md"
    buf.write_text("x\n")
    _run_edit(home, buf)

    log = (home / ".claude/cc-edit.log").read_text()
    assert "still exports EDITOR" in log
    assert ".zshrc" in log


def test_edit_falls_back_to_the_directory_without_a_recorded_key(
    home, plugin_root, tmp_path
):
    """A clone or an older install has no key; the old probe still applies."""
    run_wire(home, plugin_root)           # fixture path, so plugin_key == ""
    state = json.loads((home / ".claude/dunders-cc/installed.json").read_text())
    assert state["plugin_key"] == ""

    shutil.rmtree(plugin_root)
    buf = tmp_path / "claude-prompt.md"
    buf.write_text("x\n")
    _run_edit(home, buf)

    assert not (home / ".claude/dunders-cc").exists()


# ---------------------------------------------------------------------------
# Manifests — what actually reaches an installing user
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent


def _manifests():
    plugin = json.loads((REPO / ".claude-plugin/plugin.json").read_text())
    market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
    return plugin, market


def test_the_two_manifests_agree_on_the_version():
    """Claude Code caches a plugin as `<name>/<version>`.

    Ship changed content under an unchanged version and the cache is reused:
    the installing user gets the previous tree, with no error anywhere. That
    is how a released `cc-wire` failed to reach a machine that reinstalled the
    plugin. Bumping is therefore part of shipping, not bookkeeping.
    """
    plugin, market = _manifests()
    assert plugin["version"] == market["plugins"][0]["version"]


def test_the_version_moves_when_the_shipped_scripts_do():
    """A guard against re-releasing 0.1.0 forever."""
    plugin, _market = _manifests()
    assert plugin["version"] != "0.1.0", (
        "0.1.0 shipped without cc-wire; anything newer needs its own version"
    )


def test_every_hook_command_exists_in_the_repo():
    plugin, _market = _manifests()
    for event, entries in plugin.get("hooks", {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                rel = hook["command"].replace("${CLAUDE_PLUGIN_ROOT}/", "")
                path = REPO / rel
                assert path.is_file(), f"{event}: {rel} is declared but missing"
                assert os.access(path, os.X_OK), f"{event}: {rel} is not executable"


def test_the_wiring_hook_is_declared():
    """cc-wire is the whole plugin-native install; a manifest without it is inert."""
    plugin, _market = _manifests()
    commands = [
        hook["command"]
        for entries in plugin.get("hooks", {}).values()
        for entry in entries
        for hook in entry.get("hooks", [])
    ]
    assert any(c.endswith("/cc-wire") for c in commands)
    assert any(c.endswith("/cc-session-map") for c in commands)
