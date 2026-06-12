"""Unit tests for the JSON user-config store (theme persistence).

The autouse fixture in tests/conftest.py points XDG_CONFIG_HOME at a tmp dir,
so these never touch the developer's real ~/.config/dunders.
"""

from dunders.config import user_config


def test_missing_config_returns_empty():
    assert user_config.load_config() == {}
    assert user_config.get_theme() is None


def test_set_and_get_theme_roundtrip():
    assert user_config.set_theme("dracula") is True
    assert user_config.get_theme() == "dracula"
    # Written under the redirected XDG dir, named config.json.
    assert user_config.config_path().name == "config.json"
    assert user_config.config_path().exists()


def test_set_theme_preserves_other_keys():
    user_config.save_config({"theme": "nord", "other": 42})
    user_config.set_theme("monokai")
    data = user_config.load_config()
    assert data["theme"] == "monokai"
    assert data["other"] == 42


def test_corrupt_file_is_tolerated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = user_config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert user_config.load_config() == {}
    assert user_config.get_theme() is None
