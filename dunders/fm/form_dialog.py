"""FormDialog: a scrollable, schema-driven form modal.

Each field renders the widget its type maps to (see dunders.forms.types). On
GO the values are validated; invalid fields highlight and the form stays open.
When clean, a typed result dict is posted via :class:`Submitted`.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Container, Horizontal, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.widgets import Checkbox, Input, Select, Static, TextArea

from dunders.fm.dialogs import ShadowButton
from dunders.forms import (
    FieldSpec,
    FormSpec,
    build_result,
    get_type,
    read_clipboard,
    validate_all,
)
from dunders.windowing import WindowContent
from dunders.windowing.palette import Palette

# Sentinel Select value for the "✎ Custom…" entry of an editable combo.
_CUSTOM = "\x00__dunders_custom__"


class _BracketCheckbox(Checkbox):
    """Checkbox rendered as a classic ``[ ]`` / ``[X]`` toggle.

    Textual's default ToggleButton paints a filled block whose state reads only
    by colour; here the state is shown by the mark itself (an ``X`` between
    brackets when on, blank when off), which is unmistakable. The label is drawn
    to the right by the base ``render()``.
    """

    @property
    def _button(self) -> Content:
        style = self.get_visual_style("toggle--button")
        mark = self.BUTTON_INNER if self.value else " "
        return Content.assemble(("[", style), (mark, style), ("]", style))


class FormDialog(Container, WindowContent):
    can_focus = False

    BINDINGS = [Binding("escape", "cancel", show=False)]

    DEFAULT_CSS = """
    FormDialog { layout: vertical; background: $surface; }
    FormDialog #form-fields { height: 1fr; padding: 0 1; }
    FormDialog .form-label { margin-top: 1; color: $text; }
    /* Hidden until a validation error is shown (action_go flips display on), so
       it reserves no row and fields sit one blank line apart, not two. */
    FormDialog .form-error { display: none; height: auto; }
    FormDialog Input { width: 1fr; margin: 0; height: 1; border: none; padding: 0 1; }
    FormDialog Input:focus { border: none; padding: 0 1; }
    FormDialog Select { width: 1fr; margin: 0; height: 1; border: none; }
    FormDialog SelectCurrent { height: 1; border: none; padding: 0 1; }
    FormDialog Select:focus, FormDialog SelectCurrent:focus { border: none; }
    FormDialog Checkbox { height: 1; border: none; padding: 0; margin-top: 1; }
    FormDialog Checkbox:focus { border: none; }
    /* Rendered as "[ ]" / "[X]" by _BracketCheckbox; the mark itself shows the
       state, so the box uses one flat themed colour in both states. */
    FormDialog Checkbox > .toggle--button { background: $surface; color: $text; }
    FormDialog Checkbox.-on > .toggle--button { background: $surface; color: $text; }
    FormDialog TextArea { height: 4; margin: 0; border: none; padding: 0 1; }
    FormDialog TextArea:focus { border: none; }
    /* The combo drop-down: lift it onto the desktop's top `overlay` layer so it
       paints ABOVE the modal window (otherwise it appears behind on first open),
       and drop Textual's border for a flat themed list. */
    FormDialog SelectOverlay, FormDialog SelectOverlay:focus { layer: overlay; border: none; }
    FormDialog #form-buttons { height: 1; align: center middle; margin: 1 0; }
    """

    class Submitted(Message):
        def __init__(self, dialog: "FormDialog", result: dict) -> None:
            self.dialog = dialog
            self.result = result
            super().__init__()

    class Cancelled(Message):
        def __init__(self, dialog: "FormDialog") -> None:
            self.dialog = dialog
            super().__init__()

    def __init__(
        self,
        spec: FormSpec,
        *,
        selected_text: str = "",
        context: object | None = None,
    ) -> None:
        super().__init__()
        self.spec = spec
        self.context = context
        self.window_title = spec.title or "Form"
        self._selected_text = selected_text
        # key -> {"kind": str, "primary": Widget, "alt": Input | None}
        self._rows: dict[str, dict] = {}

    # --- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form-fields"):
            for f in self.spec.fields:
                yield from self._compose_field(f)
        with Horizontal(id="form-buttons"):
            yield ShadowButton("GO", id="form-go", face_bg="rgb(40,150,60)")
            yield ShadowButton("Cancel", id="form-cancel", face_bg="rgb(80,80,90)")

    def _compose_field(self, f: FieldSpec) -> ComposeResult:
        kind = get_type(f.type).widget
        default = f.default or ""
        if kind == "checkbox":
            # The checkbox carries its own label (drawn to the RIGHT of the box),
            # so it gets no separate label line above it.
            checked = default.strip().lower() in ("1", "true", "yes", "on")
            w = _BracketCheckbox(f.label, value=checked, id=f"fw-{f.key}")
            self._rows[f.key] = {"kind": kind, "primary": w, "alt": None}
            yield w
            yield Static("", classes="form-error", id=f"fe-{f.key}")
            return
        label = f.label + (" *" if f.required else "")
        yield Static(label + ":", classes="form-label")
        if kind == "textarea":
            text = self._selected_text if f.type == "selected_text" else default
            w = TextArea(text, id=f"fw-{f.key}")
            w.highlight_cursor_line = False
            self._rows[f.key] = {"kind": kind, "primary": w, "alt": None}
            yield w
        elif kind == "combo":
            opts = [(o, o) for o in f.options]
            # Use Select.NULL (the actual no-selection sentinel) for blank
            value = default if default in f.options else Select.NULL
            w = Select(opts, id=f"fw-{f.key}", allow_blank=True, value=value)
            self._rows[f.key] = {"kind": kind, "primary": w, "alt": None}
            yield w
        elif kind == "ecombo":
            opts = [(o, o) for o in f.options] + [("✎ Custom…", _CUSTOM)]
            value = default if default in f.options else Select.NULL
            sel = Select(opts, id=f"fw-{f.key}", allow_blank=True, value=value)
            inp = Input(value=default, id=f"fwx-{f.key}")
            inp.display = bool(default) and default not in f.options
            self._rows[f.key] = {"kind": kind, "primary": sel, "alt": inp}
            yield sel
            yield inp
        else:  # input-like: str / int / real / date / clipboard
            initial = read_clipboard() if f.type == "clipboard" else default
            if not initial and default:
                initial = default
            w = Input(value=initial, id=f"fw-{f.key}")
            self._rows[f.key] = {"kind": kind, "primary": w, "alt": None}
            yield w
        yield Static("", classes="form-error", id=f"fe-{f.key}")

    # --- theming --------------------------------------------------------------

    def _get_palette(self) -> Palette | None:
        try:
            for anc in self.ancestors_with_self:
                pal = getattr(anc, "palette", None)
                if isinstance(pal, Palette):
                    return pal
        except Exception:
            return None
        return None

    def apply_theme(self) -> None:
        """Paint the dialog surface, fields, and error labels from the active
        palette so theme switches apply without restarting the dialog.

        - Surface: ``window.content`` bg/fg.
        - Input / Select / TextArea: sunken ``desktop.background`` bg so
          fields read as distinct from the surface; same fg as content.
          (Inputs already have ``border: none`` via CSS so they stay 1 row.)
        - Checkbox: content fg/bg.
        - Labels: ``window.subtitle`` fg (falls back to content fg).
        - Error labels: ``editor.syntax.error`` fg (semantic red).
        - If no palette is found we simply refresh so the dialog stays usable
          with Textual's default CSS variables.
        """
        palette = self._get_palette()
        if palette is None:
            self.refresh()
            return
        content = palette.get("window.content")
        sunken = palette.get("desktop.background")
        heading = palette.get("window.subtitle")
        error_style = palette.get("editor.syntax.error")

        # Surface
        if content.bg is not None:
            self.styles.background = content.bg
        if content.fg is not None:
            self.styles.color = content.fg

        # Field background: a slightly darker "sunken" shade distinguishes
        # the editable area from the dialog surface.
        field_bg = sunken.bg or content.bg
        field_fg = content.fg

        try:
            for widget in self.query("Input"):
                if field_bg is not None:
                    widget.styles.background = field_bg
                if field_fg is not None:
                    widget.styles.color = field_fg
                widget.styles.background_tint = Color(0, 0, 0, 0)
        except Exception:
            pass

        try:
            for widget in self.query("Select"):
                if field_bg is not None:
                    widget.styles.background = field_bg
                if field_fg is not None:
                    widget.styles.color = field_fg
                widget.styles.background_tint = Color(0, 0, 0, 0)
        except Exception:
            pass

        try:
            for widget in self.query("TextArea"):
                if field_bg is not None:
                    widget.styles.background = field_bg
                if field_fg is not None:
                    widget.styles.color = field_fg
                widget.styles.background_tint = Color(0, 0, 0, 0)
        except Exception:
            pass

        # The checkbox toggle box ("X" inside ▐ ▌) is themed via DEFAULT_CSS
        # (the `.toggle--button` rules below) so it stays consistent across the
        # off/on states — Textual re-cascades component CSS on every `-on`
        # change, which made an inline (palette-exact) approach flip colours on
        # the first toggle. We only theme the surrounding label row here.
        try:
            for widget in self.query(Checkbox):
                if content.bg is not None:
                    widget.styles.background = content.bg
                if content.fg is not None:
                    widget.styles.color = content.fg
        except Exception:
            pass

        # Labels
        label_fg = (heading.fg if heading.fg is not None else content.fg)
        try:
            for widget in self.query(".form-label"):
                if label_fg is not None:
                    widget.styles.color = label_fg
        except Exception:
            pass

        # Error labels: semantic red
        error_color = error_style.fg or "red"
        try:
            for widget in self.query(".form-error"):
                widget.styles.color = error_color
        except Exception:
            pass

        self.refresh()

    def on_mount(self) -> None:
        self.apply_theme()
        self.call_after_refresh(self.focus_first)

    def focus_first(self) -> None:
        for f in self.spec.fields:
            row = self._rows.get(f.key)
            if row is not None:
                row["primary"].focus()
                return

    # --- value extraction -------------------------------------------------

    def _raw_value(self, f: FieldSpec) -> str:
        row = self._rows[f.key]
        kind = row["kind"]
        if kind == "checkbox":
            return "true" if row["primary"].value else "false"
        if kind == "textarea":
            return row["primary"].text
        if kind == "combo":
            sel: Select = row["primary"]
            return "" if sel.is_blank() else str(sel.value)
        if kind == "ecombo":
            inp = row["alt"]
            if inp is not None and inp.display:
                return inp.value
            sel = row["primary"]
            return "" if sel.is_blank() else str(sel.value)
        return row["primary"].value

    def _raw_values(self) -> dict[str, str]:
        return {f.key: self._raw_value(f) for f in self.spec.fields}

    # --- editable-combo swap ----------------------------------------------

    def _activate_custom(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None or row["kind"] != "ecombo":
            return
        row["primary"].display = False
        if row["alt"] is not None:
            row["alt"].display = True
            self.call_after_refresh(row["alt"].focus)

    def on_select_changed(self, event: Select.Changed) -> None:
        for f in self.spec.fields:
            row = self._rows.get(f.key)
            if row and row["kind"] == "ecombo" and row["primary"] is event.select:
                if event.value == _CUSTOM:
                    self._activate_custom(f.key)
                break

    # --- actions ----------------------------------------------------------

    def on_shadow_button_pressed(self, event: ShadowButton.Pressed) -> None:
        if event.button.id == "form-go":
            self.action_go()
        elif event.button.id == "form-cancel":
            self.action_cancel()

    def action_go(self) -> None:
        raw = self._raw_values()
        errors = validate_all(self.spec, raw)
        for f in self.spec.fields:
            msg = errors.get(f.key, "")
            err = self.query_one(f"#fe-{f.key}", Static)
            err.update(msg)
            err.display = bool(msg)  # only reserve a row when there's an error
        if errors:
            first = next(f for f in self.spec.fields if f.key in errors)
            row = self._rows[first.key]
            (row["alt"] if row["alt"] and row["alt"].display else row["primary"]).focus()
            return
        self.post_message(self.Submitted(self, build_result(self.spec, raw)))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled(self))

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.action_cancel()
