import json

import pytest

from dunders.app import DundersApp
from dunders.fm.form_dialog import FormDialog
from dunders.forms import parse_schema


@pytest.mark.asyncio
async def test_ask_returns_dict_on_go(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        spec = parse_schema({"name": {"type": "str"}, "age": "int"})
        fut = app.forms.ask(spec)
        # drive the dialog
        await pilot.pause()
        dialog = app.query_one(FormDialog)
        dialog._rows["name"]["primary"].value = "Bob"
        dialog._rows["age"]["primary"].value = "7"
        dialog.action_go()
        await pilot.pause()
        result = await fut
        assert result == {"name": "Bob", "age": 7}


@pytest.mark.asyncio
async def test_ask_returns_none_on_cancel(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        spec = parse_schema({"name": "str"})
        fut = app.forms.ask(spec)
        await pilot.pause()
        app.query_one(FormDialog).action_cancel()
        await pilot.pause()
        assert await fut is None


@pytest.mark.asyncio
async def test_file_form_writes_result(tmp_path):
    schema = tmp_path / "demo.form.json"
    schema.write_text(json.dumps({"name": "str", "age": "int"}), encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_form_from_file(schema)
        await pilot.pause()
        dialog = app.query_one(FormDialog)
        dialog._rows["name"]["primary"].value = "Ann"
        dialog._rows["age"]["primary"].value = "5"
        dialog.action_go()
        await pilot.pause()
        out = tmp_path / "demo.result.json"
        assert out.exists()
        assert json.loads(out.read_text()) == {"name": "Ann", "age": 5}


@pytest.mark.asyncio
async def test_bad_schema_file_notifies_no_dialog(tmp_path):
    schema = tmp_path / "bad.form.json"
    schema.write_text("{ not json", encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_form_from_file(schema)
        await pilot.pause()
        assert not app.query(FormDialog)
