"""App-level wizard flow: open the AI settings dialog, save a role, test a call."""

import pytest

from dunders.ai.config import load_ai_config
from dunders.app import DundersApp
from dunders.fm.ai_config_dialog import AiConfigDialog, ModelPickerDialog


@pytest.mark.asyncio
async def test_wizard_opens_and_builds_fields(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_ai_settings()
        await pilot.pause()
        dialog = app.query_one(AiConfigDialog)
        # default role seeds ollama → its fields are built
        assert "model" in dialog._inputs


@pytest.mark.asyncio
async def test_wizard_saves_role(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_ai_settings()
        await pilot.pause()
        dialog = app.query_one(AiConfigDialog)
        dialog._prov_index = dialog._providers.index("fake")
        dialog._rebuild_fields("fake", None)
        await pilot.pause()
        dialog._inputs["model"].value = "fake-9"
        dialog._save()
        await pilot.pause()
        binding = load_ai_config().resolve_role("default")
        assert binding.provider == "fake"
        assert binding.model == "fake-9"
        # the running service picked up the reload
        assert app.ai.config.resolve_role("default").model == "fake-9"


@pytest.mark.asyncio
async def test_wizard_paints_from_palette_and_survives_theme_switch(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_ai_settings()
        await pilot.pause()
        dialog = app.query_one(AiConfigDialog)
        # apply_theme painted the surface from the active palette
        assert dialog.styles.background is not None
        # cycling the theme must repaint without raising
        app.action_cycle_theme()
        dialog.apply_theme()
        await pilot.pause()
        assert app.query_one(AiConfigDialog) is dialog


@pytest.mark.asyncio
async def test_wizard_fetch_models_and_pick(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_ai_settings()
        await pilot.pause()
        dialog = app.query_one(AiConfigDialog)
        dialog._prov_index = dialog._providers.index("fake")
        dialog._rebuild_fields("fake", None)
        await pilot.pause()
        await dialog._run_fetch_models()
        await pilot.pause()
        picker = app.query_one(ModelPickerDialog)
        assert picker._table.row_count == 3
        picker._on_pick(picker._models[1])
        picker._dismiss()
        await pilot.pause()
        assert dialog._inputs["model"].value == "fake-mini"


@pytest.mark.asyncio
async def test_wizard_test_button_with_fake(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_ai_settings()
        await pilot.pause()
        dialog = app.query_one(AiConfigDialog)
        dialog._prov_index = dialog._providers.index("fake")
        dialog._rebuild_fields("fake", None)
        await pilot.pause()
        await dialog._run_test()
        assert "OK" in dialog.last_status  # echoed ping
