"""Unit tests for the TV-style command dispatcher (dunders.windowing.commands)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dunders.windowing import (
    CommandDispatcher,
    CommandRegistry,
    CommandRouter,
    WindowCommand,
)


# --- fakes -----------------------------------------------------------------


class _FakeContent:
    def __init__(self, commands: list[WindowCommand]) -> None:
        self._commands = commands

    def get_commands(self) -> list[WindowCommand]:
        return list(self._commands)


class _FakeWindow:
    def __init__(self, content) -> None:
        self.content = content


class _FakeDesktop:
    def __init__(self, focused=None) -> None:
        self.focused_window = focused


def _focused(commands: list[WindowCommand]) -> _FakeDesktop:
    return _FakeDesktop(focused=_FakeWindow(_FakeContent(commands)))


# --- registry --------------------------------------------------------------


def test_registry_register_get_unregister():
    reg = CommandRegistry()
    cmd = WindowCommand(id="x", label="X", handler=lambda: None)
    reg.register(cmd)
    assert reg.get("x") is cmd
    assert reg.all() == [cmd]
    reg.unregister("x")
    assert reg.get("x") is None
    assert reg.all() == []


def test_registry_register_many():
    reg = CommandRegistry()
    cmds = [WindowCommand(id=f"c{i}", label=f"L{i}") for i in range(3)]
    reg.register_many(cmds)
    assert len(reg.all()) == 3
    assert reg.get("c1").label == "L1"


def test_registry_overwrite():
    reg = CommandRegistry()
    reg.register(WindowCommand(id="x", label="A"))
    reg.register(WindowCommand(id="x", label="B"))
    assert reg.get("x").label == "B"


# --- dispatcher resolve ----------------------------------------------------


def test_dispatch_focus_wins_over_app():
    calls: list[str] = []
    focus_cmd = WindowCommand(id="save", label="F-save", handler=lambda: calls.append("focus"))
    app_cmd = WindowCommand(id="save", label="A-save", handler=lambda: calls.append("app"))
    reg = CommandRegistry()
    reg.register(app_cmd)
    desktop = _focused([focus_cmd])
    disp = CommandDispatcher(desktop, reg)

    resolved = disp.resolve("save")
    assert resolved is not None
    assert resolved.scope == "focus"
    assert resolved.command is focus_cmd

    assert disp.dispatch("save") is True
    assert calls == ["focus"]


def test_dispatch_falls_back_to_app_when_no_focus_match():
    calls: list[str] = []
    app_cmd = WindowCommand(id="quit", label="Quit", handler=lambda: calls.append("quit"))
    reg = CommandRegistry()
    reg.register(app_cmd)
    desktop = _focused([WindowCommand(id="other", label="x", handler=lambda: None)])
    disp = CommandDispatcher(desktop, reg)

    assert disp.dispatch("quit") is True
    assert calls == ["quit"]


def test_dispatch_unknown_returns_false():
    desktop = _focused([])
    disp = CommandDispatcher(desktop, CommandRegistry())
    assert disp.dispatch("nope") is False


def test_dispatch_disabled_command_skipped():
    calls = []
    cmd = WindowCommand(id="x", label="x", handler=lambda: calls.append(1), enabled=False)
    desktop = _focused([cmd])
    disp = CommandDispatcher(desktop, CommandRegistry())
    assert disp.dispatch("x") is False
    assert calls == []


def test_dispatch_dynamic_enabled_callable():
    state = {"on": False}
    cmd = WindowCommand(id="x", label="x", handler=lambda: None, enabled=lambda: state["on"])
    desktop = _focused([cmd])
    disp = CommandDispatcher(desktop, CommandRegistry())
    assert disp.dispatch("x") is False
    state["on"] = True
    assert disp.dispatch("x") is True


def test_dispatch_handler_none_returns_false():
    cmd = WindowCommand(id="x", label="x", handler=None)
    desktop = _focused([cmd])
    disp = CommandDispatcher(desktop, CommandRegistry())
    assert disp.dispatch("x") is False


def test_dispatch_swallows_handler_exceptions():
    def boom():
        raise RuntimeError("boom")

    cmd = WindowCommand(id="x", label="x", handler=boom)
    desktop = _focused([cmd])
    disp = CommandDispatcher(desktop, CommandRegistry())
    assert disp.dispatch("x") is False


# --- focus access edge cases -----------------------------------------------


def test_no_focused_window_yields_app_only():
    reg = CommandRegistry()
    reg.register(WindowCommand(id="a", label="A"))
    desktop = _FakeDesktop(focused=None)
    disp = CommandDispatcher(desktop, reg)
    assert [c.id for c in disp.commands_for_focus()] == ["a"]


def test_focus_get_commands_raises_is_swallowed():
    class _Bad:
        def get_commands(self):
            raise RuntimeError("nope")

    desktop = _FakeDesktop(focused=_FakeWindow(_Bad()))
    disp = CommandDispatcher(desktop, CommandRegistry())
    assert disp.commands_for_focus() == []


def test_content_without_get_commands():
    desktop = _FakeDesktop(focused=_FakeWindow(object()))
    disp = CommandDispatcher(desktop, CommandRegistry())
    assert disp.commands_for_focus() == []


# --- commands_for_focus ----------------------------------------------------


def test_commands_for_focus_dedups_by_id_focus_first():
    focus_cmd = WindowCommand(id="x", label="F", handler=lambda: None)
    app_cmd = WindowCommand(id="x", label="A", handler=lambda: None)
    other_app = WindowCommand(id="y", label="Y", handler=lambda: None)
    reg = CommandRegistry()
    reg.register_many([app_cmd, other_app])
    desktop = _focused([focus_cmd])
    disp = CommandDispatcher(desktop, reg)
    out = disp.commands_for_focus()
    assert [c.label for c in out] == ["F", "Y"]


def test_commands_for_focus_skips_invisible():
    focus_cmd = WindowCommand(id="x", label="x", visible=False)
    desktop = _focused([focus_cmd])
    disp = CommandDispatcher(desktop, CommandRegistry())
    assert disp.commands_for_focus() == []


# --- hotkey lookup ---------------------------------------------------------


def test_hotkey_lookup_focus_first_then_app():
    focus = WindowCommand(id="save", label="x", handler=lambda: None, hotkey="ctrl+s")
    app = WindowCommand(id="other", label="x", handler=lambda: None, hotkey="ctrl+o")
    reg = CommandRegistry(); reg.register(app)
    disp = CommandDispatcher(_focused([focus]), reg)
    assert disp.hotkey_lookup("ctrl+s") is focus
    assert disp.hotkey_lookup("ctrl+o") is app
    assert disp.hotkey_lookup("ctrl+x") is None


def test_hotkey_lookup_case_insensitive():
    focus = WindowCommand(id="save", label="x", handler=lambda: None, hotkey="Ctrl+S")
    disp = CommandDispatcher(_focused([focus]), CommandRegistry())
    assert disp.hotkey_lookup("ctrl+s") is focus


def test_hotkey_lookup_skips_disabled():
    focus = WindowCommand(id="save", label="x", handler=lambda: None, hotkey="ctrl+s", enabled=False)
    disp = CommandDispatcher(_focused([focus]), CommandRegistry())
    assert disp.hotkey_lookup("ctrl+s") is None


def test_hotkey_lookup_empty_key_safe():
    disp = CommandDispatcher(_focused([]), CommandRegistry())
    assert disp.hotkey_lookup("") is None


# --- router ----------------------------------------------------------------


def test_router_executes_handler_and_reports_handled():
    calls = []
    focus = WindowCommand(id="x", label="x", handler=lambda: calls.append(1), hotkey="ctrl+s")
    disp = CommandDispatcher(_focused([focus]), CommandRegistry())
    router = CommandRouter(disp)
    assert router.handle_key("ctrl+s") is True
    assert calls == [1]


def test_router_no_match_returns_false():
    disp = CommandDispatcher(_focused([]), CommandRegistry())
    router = CommandRouter(disp)
    assert router.handle_key("ctrl+s") is False


def test_router_handler_exception_is_false():
    def boom():
        raise RuntimeError("x")

    focus = WindowCommand(id="x", label="x", handler=boom, hotkey="ctrl+s")
    disp = CommandDispatcher(_focused([focus]), CommandRegistry())
    router = CommandRouter(disp)
    assert router.handle_key("ctrl+s") is False


# --- WindowCommand display helpers -----------------------------------------


def test_display_hotkey_humanises():
    assert WindowCommand(id="x", label="x", hotkey="ctrl+s").display_hotkey() == "Ctrl+S"
    assert WindowCommand(id="x", label="x", hotkey="ctrl+full_stop").display_hotkey() == "Ctrl+."
    assert WindowCommand(id="x", label="x", hotkey="ctrl+right_square_bracket").display_hotkey() == "Ctrl+]"
    assert WindowCommand(id="x", label="x", hotkey="f5").display_hotkey() == "F5"
    assert WindowCommand(id="x", label="x", hotkey=None).display_hotkey() == ""


def test_display_hotkey_label_overrides():
    cmd = WindowCommand(id="x", label="x", hotkey="ctrl+s", hotkey_label="Save!")
    assert cmd.display_hotkey() == "Save!"


def test_is_enabled_static_and_callable():
    assert WindowCommand(id="x", label="x").is_enabled() is True
    assert WindowCommand(id="x", label="x", enabled=False).is_enabled() is False
    assert WindowCommand(id="x", label="x", enabled=lambda: True).is_enabled() is True
    assert WindowCommand(id="x", label="x", enabled=lambda: 0).is_enabled() is False


def test_is_enabled_callable_exception_treated_as_disabled():
    def boom():
        raise RuntimeError()

    assert WindowCommand(id="x", label="x", enabled=boom).is_enabled() is False
