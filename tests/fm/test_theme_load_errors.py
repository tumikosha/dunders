"""Theme load failures must never crash or freeze the shell.

A malformed theme file still shows up in ``list_themes()`` (the glob finds the
file) and therefore in the Options menu. Selecting it routes through
``_apply_theme`` -> ``Desktop.set_theme`` -> ``theme_registry.get`` which raises
``ThemeLoadError``. If that escapes the menu-click handler the UI freezes, so
``_apply_theme`` must swallow it, keep the current theme, and surface the error.
"""

import pytest

from dunders.app import DundersApp
from dunders.config import user_config
from dunders.windowing.themes import loader


def _seed_theme(tmp_path, monkeypatch, name, body):
    themes_dir = tmp_path / "user-themes"
    themes_dir.mkdir(exist_ok=True)
    (themes_dir / f"{name}.toml").write_text(body)
    monkeypatch.setattr(loader, "USER_THEMES_DIR", themes_dir)
    loader.theme_registry.invalidate(name)
    return themes_dir


def _seed_broken_theme(tmp_path, monkeypatch):
    return _seed_theme(tmp_path, monkeypatch, "broken", "not valid = = toml [[[\n")


# A theme whose TOML parses fine but carries an invalid colour (a 5-digit hex).
# This passed the old load-time checks and only blew up at render time — the
# first time the menu painted ``menu.item`` — so it must now be rejected at load.
_BAD_COLOR_THEME = '[theme]\nname = "badcolor"\n[styles."menu.item"]\nfg = "#11111"\n'


def test_load_theme_rejects_invalid_color(tmp_path, monkeypatch):
    _seed_theme(tmp_path, monkeypatch, "badcolor", _BAD_COLOR_THEME)
    with pytest.raises(loader.ThemeLoadError):
        loader.load_theme("badcolor")


@pytest.mark.asyncio
async def test_apply_broken_theme_does_not_crash(tmp_path, monkeypatch):
    _seed_broken_theme(tmp_path, monkeypatch)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        before = app.desktop.palette.theme.name
        notes: list[tuple] = []
        monkeypatch.setattr(app, "notify", lambda *a, **k: notes.append((a, k)))
        app._apply_theme("broken", persist=True)
        await pilot.pause()
        # Theme is unchanged and the broken name was not persisted.
        assert app.desktop.palette.theme.name == before
        assert user_config.get_theme() != "broken"
        # The user is told the theme failed (so they can pick another).
        assert any("broken" in str(a) for a, _ in notes)


@pytest.mark.asyncio
async def test_startup_falls_back_and_notifies_when_persisted_theme_broken(
    tmp_path, monkeypatch
):
    _seed_broken_theme(tmp_path, monkeypatch)
    # Persist the broken theme as the one to paint on startup.
    user_config.set_theme("broken")

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    notes: list[tuple] = []
    monkeypatch.setattr(app, "notify", lambda *a, **k: notes.append((a, k)))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Startup paints the safe built-in instead of crashing...
        assert app.desktop.palette.theme.name == "modern_dark"
        # ...and the user is told their theme failed (silent fallback is wrong).
        assert any("broken" in str(a) for a, _ in notes)


@pytest.mark.asyncio
async def test_startup_falls_back_when_persisted_theme_has_invalid_color(
    tmp_path, monkeypatch
):
    _seed_theme(tmp_path, monkeypatch, "badcolor", _BAD_COLOR_THEME)
    user_config.set_theme("badcolor")

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    notes: list[tuple] = []
    monkeypatch.setattr(app, "notify", lambda *a, **k: notes.append((a, k)))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Invalid colours used to slip past load and explode when the menu first
        # painted; now the theme is rejected at load and we fall back cleanly.
        assert app.desktop.palette.theme.name == "modern_dark"
        assert any("badcolor" in str(a) for a, _ in notes)
