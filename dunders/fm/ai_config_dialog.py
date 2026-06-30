"""AiConfigDialog — the LLM settings wizard (opened from the ``_`` menu).

Styled to the project's Turbo-Vision idiom, not stock Textual: roles are a row
of ``ShadowButton`` tabs, the provider is a ``◀ name ▶`` cycler (like the SQL
console pager), and the surface paints itself from the active **palette** via
``apply_theme`` so theme switches apply (no Textual ``$accent``/``$boost`` vars
— the CSS here is layout-only).

One modal configures the role→{provider, model} map. The provider drives a form
built dynamically from its ``config_schema()`` (so Azure's different fields — and
any plugin provider — need no special-casing). ``Test`` does a live one-shot
call; ``Save`` writes non-secret fields to ``config.json`` and secrets to the
0600 ``secrets.json``, then reloads the service. Dismisses by posting
``Window.Closed`` (mirrors ``SqlHistoryDialog``).
"""

from __future__ import annotations

from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, Static

from dunders.ai.config import ROLES, RoleBinding, save_ai_config
from dunders.ai.presets import PRESETS
from dunders.ai.provider import AiError
from dunders.ai.providers import provider_class
from dunders.ai.service import LlmService
from dunders.ai.types import ChatRequest, FieldSpec, user
from dunders.fm.dialogs import ShadowButton
from dunders.windowing.content import WindowContent
from dunders.windowing.helpers import ModalWindow, show_modal
from dunders.windowing.palette import Palette
from dunders.windowing.window import Window


__all__ = ["AiConfigDialog", "ModelPickerDialog", "provider_choices", "schema_for"]

# Face colours for the role tabs — active vs idle. Kept as literals (like every
# ShadowButton in the app); the surface around them is palette-driven.
_TAB_ON = "rgb(0,160,176)"
_TAB_OFF = "rgb(70,80,96)"


def provider_choices() -> list[str]:
    """Provider names the wizard offers (built-ins + presets)."""
    builtins = ["anthropic", "openai", "azure", "ollama"]
    return [*builtins, *PRESETS.keys(), "fake"]


def schema_for(name: str) -> list[FieldSpec]:
    """The field schema for ``name`` — from the provider class, or synthesized
    for an OpenAI-compatible preset (api_key + model)."""
    if name in PRESETS:
        base_url, env_key, default_model = PRESETS[name]
        return [
            FieldSpec("api_key", "API key", kind="secret", default=env_key,
                      help="Env var name or the key itself", required=False),
            FieldSpec("model", "Model", default=default_model),
        ]
    cls = provider_class(name)
    return list(cls.config_schema()) if cls is not None else []


class AiConfigDialog(VerticalScroll, WindowContent):
    can_focus = False

    # Layout only — every colour comes from the palette via apply_theme().
    DEFAULT_CSS = """
    AiConfigDialog { layout: vertical; width: 74; height: auto; max-height: 26;
                     padding: 1 2; }
    AiConfigDialog .ai-heading { margin-top: 1; text-style: bold; }
    AiConfigDialog #ai-roles { height: 1; }
    AiConfigDialog #ai-provider-row { height: 1; }
    AiConfigDialog #ai-provider-name { width: 1fr; content-align: center middle;
                                       text-style: bold; }
    AiConfigDialog .ai-field-label { margin-top: 1; }
    /* Field height/border come inline from the palette (apply_theme); here we
       only fix the width and remove the default top margin. */
    AiConfigDialog Input { width: 1fr; margin: 0; }
    AiConfigDialog #ai-status { margin-top: 1; }
    AiConfigDialog #ai-buttons { height: 1; align: center middle; margin-top: 1; }
    """

    def __init__(self, service: LlmService) -> None:
        super().__init__()
        self.window_title = "AI / LLM settings"
        self._svc = service
        self._role = "default"
        self._inputs: dict[str, Input] = {}
        self._providers = provider_choices()
        self._prov_index = 0
        self.last_status = ""
        # Palette-derived field colours, filled by apply_theme: a sunken field
        # whose border (not its fill) brightens on focus, so it reads as a real
        # text field in any theme — and inline styles override Textual's stock
        # Input:focus accent border (the "Textual sample" look).
        self._field_bg: str | None = None
        self._field_fg: str | None = None
        self._focus_bg: str | None = None
        self._border_idle: str | None = None
        self._border_focus: str | None = None
        self._role_buttons: dict[str, ShadowButton] = {
            r: ShadowButton(r, id=f"ai-role-{r}", face_bg=_TAB_OFF) for r in ROLES
        }
        self._prov_name = Static("—", id="ai-provider-name")
        self._fields = Vertical(id="ai-fields")
        self._status = Static("", id="ai-status")

    # --- layout ------------------------------------------------------------

    def compose(self):
        yield Static("Role", classes="ai-heading")
        with Horizontal(id="ai-roles"):
            for r in ROLES:
                yield self._role_buttons[r]
        yield Static("Provider", classes="ai-heading")
        with Horizontal(id="ai-provider-row"):
            yield ShadowButton("◀", id="ai-prov-prev", face_bg=_TAB_OFF)
            yield self._prov_name
            yield ShadowButton("▶", id="ai-prov-next", face_bg=_TAB_OFF)
        yield self._fields
        yield self._status
        with Horizontal(id="ai-buttons"):
            yield ShadowButton("Models", id="ai-models", face_bg=_TAB_OFF)
            yield ShadowButton("Test", id="ai-test", face_bg=_TAB_OFF)
            yield ShadowButton("Save", id="ai-save", face_bg="rgb(40,150,60)")
            yield ShadowButton("Close", id="ai-close", face_bg="rgb(80,80,90)")

    def on_mount(self) -> None:
        self._load_role("default")
        self.apply_theme()
        self.call_after_refresh(self.focus_first_input)

    # --- theming -----------------------------------------------------------

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
        """Paint the surface, headings, inputs and status from the palette so a
        theme switch (Options menu / Ctrl+T) restyles the whole dialog.

        Text fields use a sunken background (``desktop.background`` against the
        ``window.content`` surface) so they read as fields, and a menu-selection
        highlight (``menu.item.active``) on focus."""
        palette = self._get_palette()
        if palette is None:
            self.refresh()
            return
        content = palette.get("window.content")
        heading = palette.get("window.subtitle")
        sunken = palette.get("desktop.background")
        border_idle = palette.get("menu.dropdown.border")
        border_focus = palette.get("window.border.focused")
        self._field_bg = sunken.bg or content.bg
        self._field_fg = content.fg
        self._focus_bg = content.bg or sunken.bg  # a touch lighter than idle
        self._border_idle = border_idle.fg or content.fg
        self._border_focus = border_focus.fg or content.fg
        for node in (self, self._fields):
            if content.bg is not None:
                node.styles.background = content.bg
            if content.fg is not None:
                node.styles.color = content.fg
        for inp in self._inputs.values():
            self._style_input(inp, focused=inp.has_focus)
        try:
            for label in self.query(".ai-heading, .ai-field-label"):
                if heading.fg is not None:
                    label.styles.color = heading.fg
            if content.fg is not None:
                self._prov_name.styles.color = content.fg
                self._status.styles.color = content.fg
        except Exception:
            pass
        self.refresh()

    def _style_input(self, inp: Input, *, focused: bool) -> None:
        bg = self._focus_bg if focused else self._field_bg
        border = self._border_focus if focused else self._border_idle
        if bg is not None:
            inp.styles.background = bg
        if self._field_fg is not None:
            inp.styles.color = self._field_fg
        # Inline border overrides Textual's stock Input / Input:focus rules so
        # the field never flips to the default accent box.
        if border is not None:
            inp.styles.border = ("round", border)

    def focus_first_input(self) -> None:
        """Focus the first field so the user can type immediately on open."""
        for inp in self._inputs.values():
            inp.focus()
            return

    def on_descendant_focus(self, event) -> None:
        w = getattr(event, "widget", None)
        if isinstance(w, Input):
            self._style_input(w, focused=True)

    def on_descendant_blur(self, event) -> None:
        w = getattr(event, "widget", None)
        if isinstance(w, Input):
            self._style_input(w, focused=False)

    # --- role / provider wiring -------------------------------------------

    def _load_role(self, role: str) -> None:
        self._role = role
        for r, btn in self._role_buttons.items():
            btn._face_bg = _TAB_ON if r == role else _TAB_OFF
            btn.refresh()
        binding = self._svc.config.resolve_role(role)
        provider = binding.provider or self._providers[0]
        if provider not in self._providers:
            self._providers.append(provider)
        self._prov_index = self._providers.index(provider)
        self._rebuild_fields(provider, binding.model)

    def _current_provider(self) -> str:
        return self._providers[self._prov_index]

    def _cycle_provider(self, delta: int) -> None:
        self._prov_index = (self._prov_index + delta) % len(self._providers)
        provider = self._current_provider()
        # Keep the saved model only when the role still points at this provider.
        binding = self._svc.config.resolve_role(self._role)
        model = binding.model if binding.provider == provider else None
        self._rebuild_fields(provider, model)

    def _rebuild_fields(self, provider: str, model: str | None) -> None:
        self._prov_name.update(provider)
        self._fields.remove_children()
        self._inputs.clear()
        widgets: list = []
        for spec in schema_for(provider):
            value = ""
            if spec.name == "model" and model:
                value = model
            elif spec.kind != "secret":
                saved = self._svc.config.providers.get(provider, {})
                value = str(saved.get(spec.name, spec.default or ""))
            placeholder = "stored — leave blank to keep" if spec.kind == "secret" else ""
            inp = Input(value=value, password=spec.kind == "secret",
                        placeholder=placeholder)
            # Solid (non-blinking) cursor: a URL value gets underlined by many
            # terminals, and a blinking block is then hard to spot inside it —
            # keep the cursor always visible so the field stays editable.
            inp.cursor_blink = False
            self._inputs[spec.name] = inp
            label = spec.label + ("" if spec.required else " (optional)")
            widgets.append(Static(label, classes="ai-field-label"))
            widgets.append(inp)
        if widgets:
            self._fields.mount(*widgets)
        if self.is_mounted:
            self.call_after_refresh(self.apply_theme)

    # --- build / save ------------------------------------------------------

    def _form_values(self) -> dict[str, str]:
        return {name: inp.value for name, inp in self._inputs.items()}

    def _build_from_form(self):
        """Construct a provider from the *unsaved* form (for Test)."""
        provider = self._current_provider()
        vals = self._form_values()
        cfg: dict = {}
        for spec in schema_for(provider):
            v = vals.get(spec.name, "")
            if spec.kind == "secret":
                cfg[spec.name] = v or (spec.default or "")
            elif v:
                cfg[spec.name] = v
        if provider in PRESETS:
            from dunders.ai.presets import preset_provider

            return preset_provider(provider, cfg, self._svc.secrets)
        cls = provider_class(provider)
        if cls is None:
            raise AiError(f"Unknown provider {provider!r}")
        return cls.from_config(cfg, self._svc.secrets)

    def _save(self) -> None:
        provider = self._current_provider()
        vals = self._form_values()
        cfg: dict = {}
        model = ""
        for spec in schema_for(provider):
            v = vals.get(spec.name, "")
            if spec.kind == "secret":
                ref = spec.default or f"{provider.upper()}_API_KEY"
                cfg[spec.name] = ref
                if v:
                    self._svc.secrets.set(ref, v)
            else:
                if spec.name == "model":
                    model = v or str(spec.default or "")
                if v or spec.default is not None:
                    cfg[spec.name] = v or str(spec.default or "")
        self._svc.config.providers[provider] = cfg
        self._svc.config.roles[self._role] = RoleBinding(provider=provider, model=model)
        if save_ai_config(self._svc.config):
            self._svc.reload()
            self._set_status(f"Saved role '{self._role}' → {provider}/{model}.")
        else:
            self._set_status("Could not write config (read-only home?).")

    def _set_status(self, text: str) -> None:
        self.last_status = text
        try:
            self._status.update(text)
        except Exception:
            pass

    # --- buttons / keys ----------------------------------------------------

    def on_shadow_button_pressed(self, event: ShadowButton.Pressed) -> None:
        event.stop()
        bid = event.button.id or ""
        if bid == "ai-close":
            self._dismiss()
        elif bid == "ai-save":
            self._save()
        elif bid == "ai-test":
            self._set_status("Testing…")
            self.app.run_worker(self._run_test(), exclusive=True)
        elif bid == "ai-models":
            self._set_status("Fetching models…")
            self.app.run_worker(self._run_fetch_models(), exclusive=True)
        elif bid == "ai-prov-prev":
            self._cycle_provider(-1)
        elif bid == "ai-prov-next":
            self._cycle_provider(+1)
        elif bid.startswith("ai-role-"):
            self._load_role(bid[len("ai-role-"):])

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self._dismiss()

    async def _run_fetch_models(self) -> None:
        """Fetch the provider's live model list and open a picker to choose one.
        Manual entry in the model field still works for ids not in the list."""
        provider = self._current_provider()
        try:
            built = self._build_from_form()
            lister = getattr(built, "list_models", None)
            if lister is None:
                self._set_status(f"{provider} can't list models.")
                return
            models = await lister()
            await built.aclose()
        except AiError as exc:
            self._set_status(f"Failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Failed: {exc}")
            return
        if not models:
            self._set_status("No models returned.")
            return
        self._set_status(f"{len(models)} models — pick one.")
        self._open_model_picker(models)

    def _open_model_picker(self, models: list[str]) -> None:
        desktop = getattr(self.app, "desktop", None)
        if desktop is None:
            return
        picker = ModelPickerDialog(models, on_pick=self._set_model)
        w = min(60, max(24, max((len(m) for m in models), default=10) + 8))
        h = min(len(models) + 4, 22)
        show_modal(desktop, picker, title="Select model", size=(w, h))
        self.call_after_refresh(picker.focus_list)

    def _set_model(self, model: str) -> None:
        inp = self._inputs.get("model")
        if inp is not None:
            inp.value = model
            self._set_status(f"Model set to {model}.")

    async def _run_test(self) -> None:
        try:
            provider = self._build_from_form()
            resp = await provider.chat(
                ChatRequest(
                    messages=[user("Reply with a short greeting.")],
                    max_tokens=256,
                )
            )
            await provider.aclose()
        except AiError as exc:
            self._set_status(f"Failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - surface any backend error
            self._set_status(f"Failed: {exc}")
            return
        text = (resp.text or "").strip()
        if text:
            self._set_status("OK — " + text.splitlines()[0])
        else:
            # Connected, but no visible text — common for reasoning models that
            # spend the budget on hidden reasoning. Report it as a success and
            # show enough to prove the round-trip worked.
            stop = resp.stop_reason or "?"
            hint = " (raise max_tokens)" if stop in ("length", "max_tokens") else ""
            self._set_status(
                f"OK — connected to {resp.model or 'model'} "
                f"({resp.usage.output_tokens} out tok, stop={stop}){hint}"
            )

    def _dismiss(self) -> None:
        node = self
        while node is not None:
            if isinstance(node, ModalWindow):
                node.post_message(Window.Closed(node))
                return
            node = getattr(node, "parent", None)


class ModelPickerDialog(Container, WindowContent):
    """A small modal list of model ids fetched from the provider's endpoint.

    Callback-driven (``on_pick`` gets the chosen id), themed from the palette,
    dismisses itself via ``Window.Closed`` — same shape as ``SqlHistoryDialog``.
    """

    can_focus = False

    DEFAULT_CSS = """
    ModelPickerDialog { layout: vertical; width: 100%; height: 100%; padding: 1 1; }
    /* 1fr (not a fixed max-height) so the list fills the modal and scrolls
       inside it — otherwise a tall list overflows the window on a short
       terminal and the row cursor runs off-screen with no scrollback. */
    ModelPickerDialog DataTable { height: 1fr; }
    ModelPickerDialog #mp-buttons { height: 1; align: center middle; margin-top: 1; }
    """

    def __init__(self, models: list[str], *, on_pick) -> None:
        super().__init__()
        self.window_title = "Select model"
        self._models = list(models)
        self._on_pick = on_pick
        self._table = DataTable(id="mp-table", cursor_type="row", zebra_stripes=False)

    def compose(self):
        yield self._table
        with Horizontal(id="mp-buttons"):
            yield ShadowButton("Close", id="mp-close", face_bg="rgb(80,80,90)")

    def on_mount(self) -> None:
        self._table.add_column("Model")
        for m in self._models:
            self._table.add_row(m)
        self.apply_theme()
        self.call_after_refresh(self.focus_list)

    def focus_list(self) -> None:
        try:
            self._table.focus()
        except Exception:
            pass

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
        palette = self._get_palette()
        if palette is not None:
            content = palette.get("window.content")
            for node in (self, self._table):
                if content.bg is not None:
                    node.styles.background = content.bg
                if content.fg is not None:
                    node.styles.color = content.fg
        self.refresh()

    def on_data_table_row_selected(self, event) -> None:
        row = getattr(event, "cursor_row", None)
        if row is None or not (0 <= row < len(self._models)):
            return
        self._on_pick(self._models[row])
        self._dismiss()

    def on_shadow_button_pressed(self, event: ShadowButton.Pressed) -> None:
        event.stop()
        if (event.button.id or "") == "mp-close":
            self._dismiss()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self._dismiss()

    def _dismiss(self) -> None:
        node = self
        while node is not None:
            if isinstance(node, ModalWindow):
                node.post_message(Window.Closed(node))
                return
            node = getattr(node, "parent", None)
