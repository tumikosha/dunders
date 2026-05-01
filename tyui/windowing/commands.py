"""Turbo-Vision-style command dispatcher.

A ``WindowContent`` declares its commands via :meth:`get_commands`. Hosts
(applications) build a :class:`CommandRegistry` for app-level commands and a
:class:`CommandDispatcher` that routes ``dispatch(id)`` requests:

    1. focused window's content commands  (scope="focus")
    2. app-level registry                  (scope="app")

Menus, hotkeys (via :class:`CommandRouter`) and command palette all funnel
through the dispatcher — the same model as Turbo Vision's ``cmXxx`` events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Protocol

from .content import WindowCommand

if TYPE_CHECKING:
    from .desktop import Desktop


__all__ = [
    "CommandSource",
    "ResolvedCommand",
    "CommandRegistry",
    "CommandDispatcher",
    "CommandRouter",
]


class CommandSource(Protocol):
    """Anything that exposes a list of commands (WindowContent, app-level
    registries, plug-ins).
    """

    def get_commands(self) -> list[WindowCommand]: ...


@dataclass
class ResolvedCommand:
    command: WindowCommand
    source: object
    scope: str  # "focus" | "app"


class CommandRegistry:
    """App-level command registry (layout, theme, palette, quit, ...).

    Focus-scoped commands are NOT stored here — they live on the content
    objects and are queried via ``content.get_commands()``.
    """

    def __init__(self) -> None:
        self._commands: dict[str, WindowCommand] = {}

    def register(self, cmd: WindowCommand) -> None:
        self._commands[cmd.id] = cmd

    def register_many(self, cmds: Iterable[WindowCommand]) -> None:
        for cmd in cmds:
            self.register(cmd)

    def unregister(self, cmd_id: str) -> None:
        self._commands.pop(cmd_id, None)

    def get(self, cmd_id: str) -> WindowCommand | None:
        return self._commands.get(cmd_id)

    def all(self) -> list[WindowCommand]:
        return list(self._commands.values())

    def get_commands(self) -> list[WindowCommand]:
        return self.all()


class CommandDispatcher:
    """Resolves command IDs and hotkeys against focus + app scopes.

    The dispatcher is intentionally stateless beyond its dependencies — every
    lookup re-reads the focused window so menus/palette never go stale.
    """

    def __init__(self, desktop: "Desktop", app_registry: CommandRegistry) -> None:
        self.desktop = desktop
        self.app_registry = app_registry

    # --- focus access ------------------------------------------------------

    def _focus_commands(self) -> list[WindowCommand]:
        win = self.desktop.focused_window
        if win is None:
            return []
        content = getattr(win, "content", None)
        if content is None:
            return []
        getter = getattr(content, "get_commands", None)
        if not callable(getter):
            return []
        try:
            return list(getter())
        except Exception:
            return []

    # --- lookup ------------------------------------------------------------

    def resolve(self, cmd_id: str) -> ResolvedCommand | None:
        for cmd in self._focus_commands():
            if cmd.id == cmd_id:
                return ResolvedCommand(cmd, self.desktop.focused_window, "focus")
        cmd = self.app_registry.get(cmd_id)
        if cmd is not None:
            return ResolvedCommand(cmd, self.app_registry, "app")
        return None

    def commands_for_focus(self) -> list[WindowCommand]:
        """All commands available in the current context: focus first, then
        app-level. Focus-scope wins on ID conflicts.
        """
        seen: set[str] = set()
        out: list[WindowCommand] = []
        for cmd in self._focus_commands():
            if cmd.visible and cmd.id not in seen:
                out.append(cmd)
                seen.add(cmd.id)
        for cmd in self.app_registry.all():
            if cmd.visible and cmd.id not in seen:
                out.append(cmd)
                seen.add(cmd.id)
        return out

    def hotkey_lookup(self, key: str) -> WindowCommand | None:
        if not key:
            return None
        norm = key.lower()
        for cmd in self._focus_commands():
            if cmd.hotkey and cmd.hotkey.lower() == norm and cmd.is_enabled():
                return cmd
        for cmd in self.app_registry.all():
            if cmd.hotkey and cmd.hotkey.lower() == norm and cmd.is_enabled():
                return cmd
        return None

    # --- execute -----------------------------------------------------------

    def dispatch(self, cmd_id: str) -> bool:
        """Run the command. Returns True iff a command was found, enabled and
        had a handler that ran without raising.
        """
        resolved = self.resolve(cmd_id)
        if resolved is None:
            return False
        cmd = resolved.command
        if not cmd.is_enabled():
            return False
        if cmd.handler is None:
            return False
        try:
            cmd.handler()
        except Exception:
            return False
        return True


class CommandRouter:
    """Glue between Textual's ``on_key`` and the dispatcher.

    The host App calls :meth:`handle_key` after its own static ``BINDINGS``
    have had a chance to fire. If the router returns True, the event has been
    handled and should be stopped.
    """

    def __init__(self, dispatcher: CommandDispatcher) -> None:
        self.dispatcher = dispatcher

    def handle_key(self, key: str) -> bool:
        cmd = self.dispatcher.hotkey_lookup(key)
        if cmd is None or cmd.handler is None:
            return False
        try:
            cmd.handler()
        except Exception:
            return False
        return True
