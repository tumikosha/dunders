"""NL → command: turn a natural-language intent into a shell command.

Pure helpers (``build_prompt`` / ``parse_suggestion``) keep the prompt shaping
and response parsing provider-agnostic and unit-testable; ``NlCommandDialog`` is
the project-styled confirm modal (Run / Edit / Cancel). The app wires these to
the command line and ``app.ai`` — see ``DundersApp`` NL-command handlers.
"""

from __future__ import annotations

from collections.abc import Callable

from textual.containers import Container, Horizontal
from textual.widgets import Static

from dunders.fm.dialogs import ShadowButton
from dunders.windowing.content import WindowContent
from dunders.windowing.helpers import ModalWindow
from dunders.windowing.palette import Palette
from dunders.windowing.window import Window


__all__ = ["SYSTEM_PROMPT", "build_prompt", "parse_suggestion", "NlCommandDialog"]


SYSTEM_PROMPT = (
    "You are a shell command generator for a terminal file manager. "
    "Given the user's intent, reply with EXACTLY two lines and nothing else "
    "(no markdown, no code fences, no commentary):\n"
    "CMD: <a single shell command that accomplishes the intent>\n"
    "WHY: <one short sentence explaining what it does>\n"
    "Prefer a single line. Use flags appropriate to the stated OS."
)


def build_prompt(intent: str, cwd: str, platform: str) -> str:
    """The user-turn content: the intent plus the context the model needs."""
    return (
        f"Intent: {intent.strip()}\n"
        f"Working directory: {cwd}\n"
        f"OS/platform: {platform}"
    )


def _clean_cmd(cmd: str) -> str:
    cmd = cmd.strip().strip("`").strip()
    if cmd.startswith("$ "):
        cmd = cmd[2:]
    return cmd.strip()


def parse_suggestion(text: str) -> tuple[str, str]:
    """Return ``(command, why)`` parsed from the model output.

    Reads the ``CMD:``/``WHY:`` markers; if no ``CMD:`` line is present, falls
    back to the first non-empty (de-fenced) line as the command.
    """
    cmd = ""
    why = ""
    for line in text.splitlines():
        s = line.strip()
        if not cmd and s[:4].upper() == "CMD:":
            cmd = s[4:].strip()
        elif not why and s[:4].upper() == "WHY:":
            why = s[4:].strip()
    if not cmd:
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("```"):
                cmd = s
                break
        if not cmd:
            cmd = text.strip()
    return _clean_cmd(cmd), why.strip()


class NlCommandDialog(Container, WindowContent):
    """Confirm modal for an AI-suggested command. Run / Edit / Cancel.

    Callback-driven: ``on_run(cmd)`` runs it, ``on_edit(cmd)`` drops it into the
    command line for editing. Themed from the palette; dismisses via
    ``Window.Closed`` (like the other dunder dialogs).
    """

    can_focus = False

    DEFAULT_CSS = """
    NlCommandDialog { layout: vertical; width: 78; height: auto; max-height: 20;
                      padding: 1 2; }
    NlCommandDialog .nl-label { color: $text-muted; margin-top: 1; }
    NlCommandDialog #nl-cmd { padding: 0 1; margin-top: 0; text-style: bold; }
    NlCommandDialog #nl-why { margin-top: 1; }
    NlCommandDialog #nl-buttons { height: 1; align: center middle; margin-top: 1; }
    """

    def __init__(
        self,
        *,
        intent: str,
        command: str,
        why: str,
        on_run: Callable[[str], None],
        on_edit: Callable[[str], None],
    ) -> None:
        super().__init__()
        self.window_title = "AI command"
        self._intent = intent
        self._command = command
        self._why = why
        self._on_run = on_run
        self._on_edit = on_edit
        self._cmd_view = Static(command, id="nl-cmd")
        self._run_btn = ShadowButton("Run", id="nl-run", face_bg="rgb(40,150,60)")

    def compose(self):
        yield Static(f"Intent: {self._intent}", classes="nl-label")
        yield Static("Suggested command:", classes="nl-label")
        yield self._cmd_view
        if self._why:
            yield Static(self._why, id="nl-why", classes="nl-label")
        with Horizontal(id="nl-buttons"):
            yield self._run_btn
            yield ShadowButton("Edit", id="nl-edit", face_bg="rgb(0,160,176)")
            yield ShadowButton("Cancel", id="nl-cancel", face_bg="rgb(80,80,90)")

    def on_mount(self) -> None:
        self.apply_theme()
        self.call_after_refresh(self.focus_run)

    def focus_run(self) -> None:
        """Focus the Run button so Enter/Space runs the suggested command."""
        try:
            self._run_btn.focus()
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
            sunken = palette.get("desktop.background")
            if content.bg is not None:
                self.styles.background = content.bg
            if content.fg is not None:
                self.styles.color = content.fg
            # The command reads as a field: sunken bg, normal fg.
            if sunken.bg is not None:
                self._cmd_view.styles.background = sunken.bg
            if content.fg is not None:
                self._cmd_view.styles.color = content.fg
        self.refresh()

    def on_shadow_button_pressed(self, event: ShadowButton.Pressed) -> None:
        event.stop()
        bid = event.button.id or ""
        if bid == "nl-run":
            self._dismiss()
            self._on_run(self._command)
        elif bid == "nl-edit":
            self._dismiss()
            self._on_edit(self._command)
        else:
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
