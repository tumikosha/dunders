"""`dunders --setup-claude` — Claude Code integration without the plugin.

The uv one-liner already puts `__` on PATH; this is what turns that into a
working ctrl+x ctrl+e. Every test points HOME at tmp_path, so the real
~/.claude is never touched.
"""

import json

import pytest

from dunders.claude_setup import remove_claude, setup_claude


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude").mkdir()
    return tmp_path


def settings_of(home):
    p = home / ".claude" / "settings.json"
    return json.loads(p.read_text()) if p.exists() else {}


def session_hooks(home):
    return [
        hook["command"]
        for entry in settings_of(home).get("hooks", {}).get("SessionStart", [])
        for hook in entry["hooks"]
    ]


def test_setup_places_the_wrappers_and_points_editor_at_them(home):
    code, _lines = setup_claude(home)
    assert code == 0

    shim = home / ".claude/dunders-cc/cc-edit"
    assert shim.is_file() and shim.stat().st_mode & 0o111
    assert (home / ".claude/dunders-cc/cc-session-map").is_file()
    assert settings_of(home)["env"]["EDITOR"] == str(shim)
    assert any("cc-session-map" in c for c in session_hooks(home))


def test_the_shim_lives_outside_the_versioned_tool_directory(home):
    """An upgrade deletes the tool dir; EDITOR must not point into it."""
    setup_claude(home)
    editor = settings_of(home)["env"]["EDITOR"]
    assert editor.startswith(str(home / ".claude" / "dunders-cc"))


def test_setup_is_idempotent(home):
    setup_claude(home)
    first = (home / ".claude/settings.json").read_text()
    setup_claude(home)
    assert (home / ".claude/settings.json").read_text() == first
    assert len(session_hooks(home)) == 1        # not registered twice


def test_setup_keeps_unrelated_settings_and_a_foreign_editor(home):
    (home / ".claude/settings.json").write_text(json.dumps({
        "env": {"EDITOR": "nvim", "FOO": "1"},
        "theme": "dark",
    }))
    _code, lines = setup_claude(home)

    settings = settings_of(home)
    assert settings["env"]["EDITOR"] == "nvim"     # someone else's choice stands
    assert settings["env"]["FOO"] == "1"
    assert settings["theme"] == "dark"
    assert any("left alone" in ln for ln in lines)
    # The hook is still worth having: it is what maps PID -> transcript.
    assert any("cc-session-map" in c for c in session_hooks(home))


def test_setup_backs_up_the_settings_it_edits(home):
    original = json.dumps({"theme": "dark"})
    (home / ".claude/settings.json").write_text(original)
    setup_claude(home)
    assert (home / ".claude/settings.json.bak-dunders-cc").read_text() == original


def test_setup_refuses_to_touch_malformed_settings(home):
    broken = "{ not json"
    (home / ".claude/settings.json").write_text(broken)
    code, lines = setup_claude(home)

    assert code == 1
    assert (home / ".claude/settings.json").read_text() == broken
    assert any("not readable JSON" in ln for ln in lines)


def test_remove_undoes_everything_it_wrote(home):
    setup_claude(home)
    code, _lines = remove_claude(home)

    assert code == 0
    assert not (home / ".claude/dunders-cc").exists()
    assert "EDITOR" not in settings_of(home).get("env", {})
    assert session_hooks(home) == []


def test_remove_leaves_a_user_supplied_editor_and_other_hooks(home):
    setup_claude(home)
    settings = settings_of(home)
    settings["env"]["EDITOR"] = "nvim"            # re-pointed after setup
    settings["hooks"]["SessionStart"].append({
        "hooks": [{"type": "command", "command": "/opt/other-hook"}]
    })
    (home / ".claude/settings.json").write_text(json.dumps(settings))

    remove_claude(home)

    assert settings_of(home)["env"]["EDITOR"] == "nvim"
    assert session_hooks(home) == ["/opt/other-hook"]


def test_remove_is_safe_on_a_machine_that_never_ran_setup(home):
    code, _lines = remove_claude(home)
    assert code == 0
    assert not (home / ".claude/dunders-cc").exists()


def test_windows_says_so_instead_of_half_installing(home, monkeypatch):
    """The wrappers are bash; pretending otherwise would leave a dead EDITOR."""
    monkeypatch.setattr("dunders.claude_setup.os.name", "nt")
    code, lines = setup_claude(home)

    assert code == 1
    assert not (home / ".claude/dunders-cc").exists()
    assert not (home / ".claude/settings.json").exists()
    assert any("Windows" in ln for ln in lines)


def test_the_cli_flags_reach_the_functions(monkeypatch, home):
    """`dunders --setup-claude` and `--remove-claude` are the public surface."""
    import dunders.main as main_mod

    calls = []
    monkeypatch.setattr("dunders.claude_setup.setup_claude",
                        lambda: calls.append("setup") or (0, []))
    monkeypatch.setattr("dunders.claude_setup.remove_claude",
                        lambda: calls.append("remove") or (0, []))

    monkeypatch.setattr("sys.argv", ["dunders", "--setup-claude"])
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 0

    monkeypatch.setattr("sys.argv", ["dunders", "--remove-claude"])
    with pytest.raises(SystemExit):
        main_mod.main()

    assert calls == ["setup", "remove"]
