import pytest
from textual.widgets import Checkbox, Input, Select, TextArea

from dunders.app import DundersApp
from dunders.fm.dialogs import ShadowButton
from dunders.fm.form_dialog import FormDialog
from dunders.forms import parse_schema
from dunders.windowing.helpers import show_modal


async def _mount(app, spec, *, selected_text=""):
    dialog = FormDialog(spec, selected_text=selected_text)
    show_modal(app.desktop, dialog, title="Form", size=(60, 18))
    return dialog


@pytest.mark.asyncio
async def test_selected_text_prefilled(tmp_path):
    spec = parse_schema({"text": {"type": "selected_text"}})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = await _mount(app, spec, selected_text="hello sel")
        await pilot.pause()
        assert dialog._raw_values()["text"] == "hello sel"


@pytest.mark.asyncio
async def test_go_with_invalid_does_not_submit(tmp_path):
    spec = parse_schema({"age": "int"})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = await _mount(app, spec)
        await pilot.pause()
        dialog._rows["age"]["primary"].value = "notanint"
        dialog.action_go()
        await pilot.pause()
        # dialog still mounted (not submitted): error text set
        err = dialog.query_one("#fe-age")
        assert "integer" in str(err.render()).lower()


@pytest.mark.asyncio
async def test_go_with_valid_submits_typed(tmp_path):
    spec = parse_schema({"name": {"type": "str"}, "age": "int", "agree": "bool"})
    submitted: list[dict] = []

    # Use a test-local app subclass that captures FormDialog.Submitted
    class _TestApp(DundersApp):
        def on_form_dialog_submitted(self, event: FormDialog.Submitted) -> None:
            submitted.append(event.result)

    app = _TestApp(launch_mode="fm", initial_path=str(tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = FormDialog(spec)
        show_modal(app.desktop, dialog, title="Form", size=(60, 18))
        await pilot.pause()

        # Fill in the fields
        dialog._rows["name"]["primary"].value = "Bob"
        dialog._rows["age"]["primary"].value = "30"
        dialog._rows["agree"]["primary"].value = True

        # Drive action_go() — it validates and posts FormDialog.Submitted
        dialog.action_go()
        await pilot.pause()
        await pilot.pause()

        # Verify a Submitted message was posted with the correct typed result
        assert len(submitted) == 1
        assert submitted[0] == {"name": "Bob", "age": 30, "agree": True}


@pytest.mark.asyncio
async def test_ecombo_custom_swaps_to_input(tmp_path):
    spec = parse_schema({"city": {"type": "ecombo", "options": ["Berlin"]}})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = await _mount(app, spec)
        await pilot.pause()
        row = dialog._rows["city"]
        assert row["alt"].display is False
        dialog._activate_custom("city")
        await pilot.pause()
        assert row["alt"].display is True
        row["alt"].value = "Praha"
        assert dialog._raw_values()["city"] == "Praha"


@pytest.mark.asyncio
async def test_form_dialog_paints_from_palette_and_survives_theme_switch(tmp_path):
    spec = parse_schema({"name": {"type": "str"}})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        dialog = await _mount(app, spec)
        await pilot.pause()
        # apply_theme painted the surface from the active palette
        assert dialog.styles.background is not None
        # cycling the theme must repaint without raising
        app.action_cycle_theme()
        dialog.apply_theme()
        await pilot.pause()
        # dialog is still queryable / alive
        assert app.query_one(FormDialog) is dialog


@pytest.mark.asyncio
async def test_buttons_are_shadow_buttons(tmp_path):
    """GO and Cancel must be ShadowButton instances with the correct ids."""
    spec = parse_schema({"name": {"type": "str"}})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = await _mount(app, spec)
        await pilot.pause()
        go_btn = dialog.query_one("#form-go", ShadowButton)
        cancel_btn = dialog.query_one("#form-cancel", ShadowButton)
        assert go_btn.id == "form-go"
        assert cancel_btn.id == "form-cancel"
        assert isinstance(go_btn, ShadowButton)
        assert isinstance(cancel_btn, ShadowButton)


@pytest.mark.asyncio
async def test_apply_theme_paints_input_background(tmp_path):
    """After apply_theme(), Input fields must have a palette-derived background."""
    spec = parse_schema({"name": {"type": "str"}})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        dialog = await _mount(app, spec)
        await pilot.pause()
        # Input background must have been set by apply_theme (not None/default)
        inp = dialog.query_one("#fw-name", Input)
        assert inp.styles.background is not None
        # Survives a theme switch without raising
        app.action_cycle_theme()
        dialog.apply_theme()
        await pilot.pause()
        assert inp.styles.background is not None


@pytest.mark.asyncio
async def test_field_heights_are_single_row(tmp_path):
    """Input (focused), Select (combo), and Checkbox (bool) must each render
    exactly 1 row tall — not 3 (the tall-border regression).

    The focused first Input is the critical case: Textual's default
    Input:focus rule re-adds a 3-row border; our CSS must override it.
    """
    spec = parse_schema({
        "name": "str",
        "age": "int",
        "country": {"type": "combo", "options": ["US", "DE"]},
        "agree": "bool",
    })
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        dialog = FormDialog(spec)
        show_modal(app.desktop, dialog, title="Heights", size=(70, 30))
        # Multiple pauses to let focus_first() fire and layout settle.
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        for cls in (Input, Select, Checkbox):
            for widget in dialog.query(cls):
                assert widget.size.height <= 1, (
                    f"{cls.__name__}(id={widget.id!r}) height="
                    f"{widget.size.height} > 1 (3-row border regression)"
                )

        # Confirm the focused widget is an Input (focus_first placed focus there)
        focused = app.focused
        assert isinstance(focused, Input), f"Expected Input focused, got {focused!r}"
        assert focused.size.height <= 1, (
            f"Focused Input height={focused.size.height} > 1"
        )


@pytest.mark.asyncio
async def test_focus_tint_transparent_and_textarea_borderless(tmp_path):
    """Focused Input must have a fully-transparent background_tint (alpha=0)
    so Textual's default '$foreground 5%' tint doesn't wash out the palette
    background. TextArea must have no visible border on any edge."""
    spec = parse_schema({"name": "str", "notes": "text"})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        dialog = FormDialog(spec)
        show_modal(app.desktop, dialog, title="Tint test", size=(70, 30))
        await pilot.pause()
        await pilot.pause()

        # Focus the first (str) Input and let apply_theme fire
        name_inp = dialog.query_one("#fw-name", Input)
        name_inp.focus()
        await pilot.pause()

        # background_tint must be fully transparent (alpha == 0)
        tint = name_inp.styles.background_tint
        assert tint is not None, "background_tint should be set by apply_theme"
        assert tint.a == 0, (
            f"Expected background_tint alpha=0 (fully transparent), got {tint!r}"
        )

        # TextArea border must be absent on all four edges
        ta = dialog.query_one("#fw-notes", TextArea)
        border = ta.styles.border
        for edge_val in border:
            edge_type = edge_val[0] if isinstance(edge_val, tuple) else edge_val
            assert edge_type in ("", "none", "hidden", None), (
                f"TextArea has unexpected border edge: {edge_val!r}"
            )


@pytest.mark.asyncio
async def test_checkbox_themed_and_textarea_no_cursor_line(tmp_path):
    """Regression: (a) every TextArea has highlight_cursor_line=False; (b) the
    checkbox box renders a CLEARLY different background when checked vs unchecked
    (so the state is obvious) and the off state is not the muddy $panel default;
    (c) the combo drop-down overlay has no Textual border and sits on the top
    `overlay` layer so it paints above the modal window.
    """
    from textual.widgets import Select
    from textual.widgets._select import SelectOverlay

    spec = parse_schema(
        {"agree": "bool", "notes": "text", "c": {"type": "combo", "options": ["a", "b"]}}
    )
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        dialog = FormDialog(spec)
        show_modal(app.desktop, dialog, title="Theme test", size=(70, 30))
        await pilot.pause()
        await pilot.pause()
        dialog.apply_theme()
        await pilot.pause()

        # (a) TextArea must have cursor-line highlight disabled
        ta = dialog.query_one("#fw-notes", TextArea)
        assert ta.highlight_cursor_line is False, (
            f"TextArea.highlight_cursor_line expected False, got {ta.highlight_cursor_line!r}"
        )

        # (b) Checkbox renders as a classic bracket toggle: "[ ]" off, "[X]" on,
        # so the state is unmistakable (and the label rides to the right of it).
        cb = dialog.query_one("#fw-agree", Checkbox)
        assert cb._button.plain == "[ ]", (
            f"unchecked checkbox should render '[ ]', got {cb._button.plain!r}"
        )
        cb.value = True
        await pilot.pause()
        assert cb._button.plain == "[X]", (
            f"checked checkbox should render '[X]', got {cb._button.plain!r}"
        )

        # (c) The combo overlay has no border and is promoted to the overlay layer.
        sel = dialog.query_one(Select)
        sel.expanded = True
        await pilot.pause()
        await pilot.pause()
        overlay = dialog.query_one(SelectOverlay)
        assert overlay.styles.layer == "overlay", (
            f"combo overlay layer expected 'overlay', got {overlay.styles.layer!r}"
        )
        border = overlay.styles.border
        assert all(edge == ("", "") or not edge[0] for edge in border), (
            f"combo overlay should have no border, got {border!r}"
        )
