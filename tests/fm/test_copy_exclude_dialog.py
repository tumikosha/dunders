"""The copy/move dialog's exclusion row: checkbox gates the mask field."""

from dunders.app import DundersApp
from dunders.fm.dialogs import CopyMoveDialog, _PermCheckbox
from dunders.fm.exclude import DEFAULT_EXCLUDE
from dunders.windowing.helpers import show_modal


async def _open(app, pilot) -> CopyMoveDialog:
    dialog = CopyMoveDialog(prompt="Copy to:", initial="/tmp", ok_label="Copy")
    show_modal(app.desktop, dialog, title="Copy", size=(72, 14))
    await pilot.pause()
    await pilot.pause()
    return dialog


async def test_field_is_prefilled_with_the_default_mask(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        dialog = await _open(app, pilot)

        assert dialog._exclude_input.value == DEFAULT_EXCLUDE == ".*;-.gitignore"
        # Prefilled but inert: the checkbox is still the switch.
        assert dialog.exclude_masks == ()

        dialog.query_one("#cm-exclude-on", _PermCheckbox).checked = True
        assert dialog.exclude_masks == (".*", "-.gitignore")


async def test_masks_are_empty_while_the_checkbox_is_off(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        dialog = await _open(app, pilot)

        # Typed but not enabled -> nothing is excluded.
        dialog._exclude_input.value = "*.pyc node_modules"
        assert dialog.exclude_masks == ()


async def test_checkbox_on_parses_the_mask_field(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        dialog = await _open(app, pilot)

        dialog.query_one("#cm-exclude-on", _PermCheckbox).checked = True
        dialog._exclude_input.value = "*.pyc, node_modules"
        assert dialog.exclude_masks == ("*.pyc", "node_modules")

        # Enabled but blank is still "exclude nothing".
        dialog._exclude_input.value = ""
        assert dialog.exclude_masks == ()


async def test_checkbox_precedes_the_field_in_the_focus_chain(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        dialog = await _open(app, pilot)

        chain = dialog._focusables()
        box = dialog.query_one("#cm-exclude-on", _PermCheckbox)
        assert chain.index(box) < chain.index(dialog._exclude_input)
        # …and both sit between "Skip existing" and the buttons.
        skip = dialog.query_one("#cm-skip", _PermCheckbox)
        ok = dialog.query_one("#cm-ok")
        assert chain.index(skip) < chain.index(box)
        assert chain.index(dialog._exclude_input) < chain.index(ok)
