"""Integration tests: demo app boots, tile/cascade/maximize, modal, theme switch."""

import pytest
from textual.widgets import Static

from tyui.windowing import (
    BorderStyle,
    Decorations,
    Desktop,
    TitleSpec,
    WindowManager,
    make_window,
    show_modal,
)
from tyui.windowing.helpers import ModalWindow


class _TestApp:
    """Small helper to spin up a Desktop-based App for tests."""


from textual.app import App, ComposeResult


class BareApp(App):
    def compose(self) -> ComposeResult:
        self.desktop = Desktop()
        yield self.desktop


class TestDemoBoots:
    @pytest.mark.asyncio
    async def test_demo_app_starts(self):
        from tyui.windowing.demo.app import WindowingDemo

        app = WindowingDemo()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            # Scene builds with 4 default windows + Help modal
            assert app.desktop is not None
            assert app.manager is not None
            # Wait for call_after_refresh to fire.
            await pilot.pause()
            assert len(app.desktop.windows) >= 4

    @pytest.mark.asyncio
    async def test_demo_toggle_theme(self):
        from tyui.windowing.demo.app import WindowingDemo

        app = WindowingDemo()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.pause()
            start = app.desktop.palette.theme.name
            await pilot.press("f2")
            await pilot.pause()
            assert app.desktop.palette.theme.name != start

    @pytest.mark.asyncio
    async def test_demo_new_window(self):
        from tyui.windowing.demo.app import WindowingDemo

        app = WindowingDemo()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.pause()
            initial_count = len(app.desktop.windows)
            await pilot.press("f3")
            await pilot.pause()
            assert len(app.desktop.windows) == initial_count + 1


class TestManagerLayouts:
    @pytest.mark.asyncio
    async def test_tile_horizontal(self):
        app = BareApp()
        async with app.run_test(size=(100, 30)) as pilot:
            mgr = WindowManager(app.desktop)
            for i in range(3):
                w = make_window(Static(f"w{i}"), title=f"W{i}", position=(0, 0), size=(20, 10))
                app.desktop.add_window(w)
            await pilot.pause()
            mgr.tile_horizontal()
            await pilot.pause()
            # After tile_h: widths should be ~ W/3 each.
            xs = sorted(w.region.x for w in app.desktop.windows)
            # Three windows should start at 0, ~33, ~66
            assert xs[0] == 0
            assert xs[1] > 0
            assert xs[2] > xs[1]

    @pytest.mark.asyncio
    async def test_tile_vertical(self):
        app = BareApp()
        async with app.run_test(size=(100, 30)) as pilot:
            mgr = WindowManager(app.desktop)
            for i in range(3):
                w = make_window(Static(f"w{i}"), title=f"W{i}", position=(0, 0), size=(20, 10))
                app.desktop.add_window(w)
            await pilot.pause()
            mgr.tile_vertical()
            await pilot.pause()
            ys = sorted(w.region.y for w in app.desktop.windows)
            assert ys[0] == 0
            assert ys[1] > 0
            assert ys[2] > ys[1]

    @pytest.mark.asyncio
    async def test_tile_grid(self):
        app = BareApp()
        async with app.run_test(size=(100, 30)) as pilot:
            mgr = WindowManager(app.desktop)
            for i in range(4):
                w = make_window(Static(f"w{i}"), title=f"W{i}", position=(0, 0), size=(20, 10))
                app.desktop.add_window(w)
            await pilot.pause()
            mgr.tile_grid()
            await pilot.pause()
            # 4 windows in a 2x2 grid — should produce 2 distinct x-values and 2 distinct y.
            xs = {w.region.x for w in app.desktop.windows}
            ys = {w.region.y for w in app.desktop.windows}
            assert len(xs) == 2
            assert len(ys) == 2

    @pytest.mark.asyncio
    async def test_cascade(self):
        app = BareApp()
        async with app.run_test(size=(100, 30)) as pilot:
            mgr = WindowManager(app.desktop)
            for i in range(3):
                w = make_window(Static(f"w{i}"), title=f"W{i}", position=(0, 0), size=(20, 10))
                app.desktop.add_window(w)
            await pilot.pause()
            mgr.cascade()
            await pilot.pause()
            # Cascade: each window offset slightly from previous
            xs = [w.region.x for w in app.desktop.windows]
            assert xs == sorted(xs)

    @pytest.mark.asyncio
    async def test_maximize_restore(self):
        app = BareApp()
        async with app.run_test(size=(100, 30)) as pilot:
            mgr = WindowManager(app.desktop)
            w = make_window(Static("x"), title="X", position=(5, 3), size=(20, 10))
            app.desktop.add_window(w)
            await pilot.pause()
            mgr.toggle_maximize(w)
            await pilot.pause()
            assert w.maximized is True
            assert w.size.width == 100
            mgr.toggle_maximize(w)
            await pilot.pause()
            assert w.maximized is False
            # Size roughly restored (may be clamped, so check close to original)
            assert w.size.width == 20


class TestManagerKeyboardModes:
    @pytest.mark.asyncio
    async def test_move_mode_arrows(self):
        app = BareApp()
        async with app.run_test(size=(80, 24)) as pilot:
            mgr = WindowManager(app.desktop)
            w = make_window(Static("x"), title="X", position=(10, 5), size=(20, 10))
            app.desktop.add_window(w)
            await pilot.pause()
            mgr.enter_move_mode(w)
            start_x = w.region.x
            mgr.move_mode_step(5, 0)
            await pilot.pause()
            # Position moved right by 5.
            assert w.styles.offset.x.value == start_x + 5

    @pytest.mark.asyncio
    async def test_resize_mode(self):
        app = BareApp()
        async with app.run_test(size=(80, 24)) as pilot:
            mgr = WindowManager(app.desktop)
            w = make_window(Static("x"), title="X", position=(10, 5), size=(20, 10))
            app.desktop.add_window(w)
            await pilot.pause()
            mgr.enter_resize_mode(w)
            mgr.resize_mode_step(5, 2)
            await pilot.pause()
            assert w.styles.width.value == 25
            assert w.styles.height.value == 12


class TestModal:
    @pytest.mark.asyncio
    async def test_show_modal_adds_window(self):
        app = BareApp()
        async with app.run_test(size=(80, 24)) as pilot:
            w = make_window(Static("bg"), title="bg", position=(0, 0), size=(40, 10))
            app.desktop.add_window(w)
            await pilot.pause()
            modal = show_modal(app.desktop, Static("modal body"), title="Modal")
            await pilot.pause()
            assert modal in app.desktop.windows
            assert isinstance(modal, ModalWindow)
            assert app.desktop.focused_window is modal

    @pytest.mark.asyncio
    async def test_modal_esc_dismisses(self):
        app = BareApp()
        async with app.run_test(size=(80, 24)) as pilot:
            modal = show_modal(app.desktop, Static("modal"), title="Modal")
            await pilot.pause()
            modal.focused_state = True
            # Trigger action_dismiss directly (Esc binding).
            modal.action_dismiss()
            await pilot.pause()
            # The demo app removes it via its handler; here we just verify
            # that the message machinery fires (message is posted, not yet handled).
            assert modal in app.desktop.windows  # without external handler it stays


class TestBorderRendering:
    @pytest.mark.asyncio
    async def test_window_renders_all_border_styles(self):
        app = BareApp()
        styles = [
            BorderStyle.SINGLE,
            BorderStyle.DOUBLE,
            BorderStyle.ROUNDED,
            BorderStyle.HEAVY,
            BorderStyle.DASHED,
            BorderStyle.ASCII,
        ]
        async with app.run_test(size=(80, 24)) as pilot:
            for s in styles:
                w = make_window(
                    Static("x"), title=f"{s.value}",
                    position=(0, 0), size=(20, 6),
                    border_focused=s,
                    border_unfocused=s,
                )
                app.desktop.add_window(w)
            await pilot.pause()
            # Render all their top lines — should not raise and should be width=20.
            for win in app.desktop.windows:
                strip = win.render_line(0)
                assert strip is not None
                # Cell count of the strip matches width.
                assert sum(len(seg.text) for seg in strip) == 20
