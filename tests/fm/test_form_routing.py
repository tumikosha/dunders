import json

import pytest

from dunders.app import DundersApp
from dunders.fm.form_dialog import FormDialog
from dunders.forms import EXAMPLE_FORM_JSON, parse_schema
from dunders.windowing.editor import EditorContent


def _editor_windows(app: DundersApp) -> list:
    desktop = app.desktop
    if desktop is None:
        return []
    return [w for w in desktop.windows if isinstance(w.content, EditorContent)]


def test_example_form_json_parses():
    data = json.loads(EXAMPLE_FORM_JSON)
    schema = parse_schema(data)
    field_keys = {f.key for f in schema.fields}
    assert "name" in field_keys
    assert "age" in field_keys
    assert "notes" in field_keys


@pytest.mark.asyncio
async def test_f3_on_form_json_opens_form(tmp_path):
    schema = tmp_path / "x.form.json"
    schema.write_text(json.dumps({"name": "str"}), encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_editor_window(schema, read_only=True)
        await pilot.pause()
        assert app.query(FormDialog)


@pytest.mark.asyncio
async def test_f4_on_form_json_opens_source_in_editor(tmp_path):
    schema = tmp_path / "y.form.json"
    schema.write_text(json.dumps({"age": "int"}), encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_editor_window(schema, read_only=False)
        await pilot.pause()
        assert not app.query(FormDialog)
        assert _editor_windows(app)


@pytest.mark.asyncio
async def test_plain_json_does_not_open_form(tmp_path):
    plain = tmp_path / "data.json"
    plain.write_text(json.dumps({"name": "str"}), encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_editor_window(plain, read_only=True)
        await pilot.pause()
        assert not app.query(FormDialog)


@pytest.mark.asyncio
async def test_form_open_command_registered(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.command_registry.get("form.open") is not None


@pytest.mark.asyncio
async def test_seed_on_missing_creates_file(tmp_path):
    """Non-existent .form.json path: _open_form_source seeds the example and opens the editor."""
    path = tmp_path / "new.form.json"
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_form_source(path)
        await pilot.pause()
        assert path.exists()
        schema = parse_schema(json.loads(path.read_text(encoding="utf-8")))
        assert schema.fields
        # editor opened, not form dialog
        assert not app.query(FormDialog)
        assert _editor_windows(app)


@pytest.mark.asyncio
async def test_seed_normalizes_non_form_extension(tmp_path):
    """A path without .form.json extension seeds <stem>.form.json."""
    path = tmp_path / "myform"
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_form_source(path)
        await pilot.pause()
        seeded = tmp_path / "myform.form.json"
        assert seeded.exists()
        parse_schema(json.loads(seeded.read_text(encoding="utf-8")))


@pytest.mark.asyncio
async def test_seed_normalizes_json_extension(tmp_path):
    """A path ending in .json (not .form.json) seeds <stem>.form.json."""
    path = tmp_path / "myform.json"
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_form_source(path)
        await pilot.pause()
        seeded = tmp_path / "myform.form.json"
        assert seeded.exists()


@pytest.mark.asyncio
async def test_open_form_source_existing_opens_editor(tmp_path):
    """_open_form_source on an existing .form.json opens the text editor (no FormDialog, no re-seed)."""
    schema = tmp_path / "existing.form.json"
    original = json.dumps({"name": "str"})
    schema.write_text(original, encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_form_source(schema)
        await pilot.pause()
        assert not app.query(FormDialog)
        assert _editor_windows(app)
        # content must be unchanged (no re-seed)
        assert schema.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_action_form_open_cursor_opens_editor(tmp_path):
    """action_form_open with a .form.json under cursor opens editor, not FormDialog."""
    schema = tmp_path / "cursor.form.json"
    schema.write_text(json.dumps({"field": "str"}), encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Drive through _open_form_source directly (same path action_form_open takes)
        app._open_form_source(schema)
        await pilot.pause()
        assert not app.query(FormDialog)
        assert _editor_windows(app)
