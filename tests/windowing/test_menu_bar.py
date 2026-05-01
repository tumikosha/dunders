"""Menu bar + Dropdown integration tests."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from tyui.windowing import (
    Desktop,
    Dropdown,
    Menu,
    MenuBar,
    MenuItem,
    MenuSeparator,
)


class MenuApp(App):
    def __init__(self, menus: list[Menu]) -> None:
        super().__init__()
        self._menus = menus

    def compose(self) -> ComposeResult:
        self.menu_bar = MenuBar(self._menus)
        self.desktop = Desktop()
        yield self.menu_bar
        yield self.desktop


class TestMenuBar:
    @pytest.mark.asyncio
    async def test_menu_bar_mounts_with_menus(self):
        app = MenuApp([
            Menu("File", [MenuItem("New", hotkey="F3")]),
            Menu("Edit", [MenuItem("Copy")]),
        ])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.menu_bar.is_mounted
            assert [m.label for m in app.menu_bar.menus] == ["File", "Edit"]
            assert app.menu_bar.active_index is None

    @pytest.mark.asyncio
    async def test_activate_and_cycle(self):
        app = MenuApp([
            Menu("A", [MenuItem("a1")]),
            Menu("B", [MenuItem("b1")]),
            Menu("C", [MenuItem("c1")]),
        ])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.menu_bar.activate(0)
            await pilot.pause()
            assert app.menu_bar.active_index == 0
            app.menu_bar.cycle(1)
            assert app.menu_bar.active_index == 1
            app.menu_bar.cycle(-1)
            assert app.menu_bar.active_index == 0
            app.menu_bar.cycle(-1)
            assert app.menu_bar.active_index == 2  # wraps

    @pytest.mark.asyncio
    async def test_render_shows_menu_labels(self):
        app = MenuApp([
            Menu("File", [MenuItem("x")]),
            Menu("Help", [MenuItem("y")]),
        ])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            strip = app.menu_bar.render_line(0)
            text = "".join(seg.text for seg in strip)
            assert "File" in text
            assert "Help" in text


class TestDropdown:
    @pytest.mark.asyncio
    async def test_dropdown_mounts_and_renders(self):
        app = MenuApp([Menu("X", [])])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            dd = Dropdown(
                [MenuItem("first"), MenuItem("second")],
                position=(0, 1),
                palette=app.desktop.palette,
            )
            app.desktop.mount(dd)
            await pilot.pause()
            assert dd.is_mounted
            # Render all rows, verify item text appears.
            text = ""
            for y in range(dd.size.height):
                strip = dd.render_line(y)
                text += "".join(seg.text for seg in strip)
            assert "first" in text
            assert "second" in text

    @pytest.mark.asyncio
    async def test_dropdown_highlight_moves(self):
        app = MenuApp([Menu("X", [])])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            dd = Dropdown(
                [MenuItem("a"), MenuItem("b"), MenuItem("c")],
                palette=app.desktop.palette,
            )
            app.desktop.mount(dd)
            await pilot.pause()
            assert dd.highlight == 0
            dd.move_highlight(1)
            assert dd.highlight == 1
            dd.move_highlight(1)
            assert dd.highlight == 2
            dd.move_highlight(1)
            assert dd.highlight == 0  # wraps

    @pytest.mark.asyncio
    async def test_dropdown_skips_separator(self):
        app = MenuApp([Menu("X", [])])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            dd = Dropdown(
                [MenuItem("a"), MenuSeparator(), MenuItem("b")],
                palette=app.desktop.palette,
            )
            app.desktop.mount(dd)
            await pilot.pause()
            assert dd.highlight == 0
            dd.move_highlight(1)
            # Separator at index 1 skipped → highlight lands on 2.
            assert dd.highlight == 2

    @pytest.mark.asyncio
    async def test_dropdown_choose_calls_handler(self):
        called = {"n": 0}

        def handler():
            called["n"] += 1

        app = MenuApp([Menu("X", [])])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            dd = Dropdown(
                [MenuItem("do", handler=handler)],
                palette=app.desktop.palette,
            )
            app.desktop.mount(dd)
            await pilot.pause()
            dd.choose_current()
            await pilot.pause()
            assert called["n"] == 1

    @pytest.mark.asyncio
    async def test_demo_menu_bar_integration(self):
        from tyui.windowing.demo.app import WindowingDemo

        app = WindowingDemo()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(); await pilot.pause(); await pilot.pause()
            # Close Help modal first
            await pilot.press("escape")
            await pilot.pause()
            # F9 should activate menu bar (F10 quits the demo)
            await pilot.press("f9")
            await pilot.pause()
            assert app.menu_bar.active_index == 0
            # Right arrow cycles to next menu
            await pilot.press("right")
            await pilot.pause()
            assert app.menu_bar.active_index == 1
            # Enter opens dropdown
            await pilot.press("enter")
            await pilot.pause()
            assert app._active_dropdown is not None
            # Esc closes it
            await pilot.press("escape")
            await pilot.pause()
            assert app._active_dropdown is None
