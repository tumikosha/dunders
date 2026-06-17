import pytest

from dunders.fm.image_viewer import sniff_image, _fit, image_to_ascii


class TestSniffImage:
    def test_png(self):
        assert sniff_image(b"\x89PNG\r\n\x1a\n....") is True

    def test_jpeg(self):
        assert sniff_image(b"\xff\xd8\xff\xe0\x00\x10JFIF") is True

    def test_gif87(self):
        assert sniff_image(b"GIF87a\x01\x00") is True

    def test_gif89(self):
        assert sniff_image(b"GIF89a\x01\x00") is True

    def test_bmp(self):
        assert sniff_image(b"BM\x36\x00\x00\x00") is True

    def test_webp(self):
        assert sniff_image(b"RIFF\x24\x00\x00\x00WEBPVP8 ") is True

    def test_plain_text_is_not_image(self):
        assert sniff_image(b"hello world\n") is False

    def test_nul_binary_is_not_image(self):
        assert sniff_image(b"\x00\x01\x02\x03") is False

    def test_empty(self):
        assert sniff_image(b"") is False


class TestFit:
    def test_wide_image_limited_by_cols(self):
        assert _fit(100, 50, 80, 100) == (80, 20)

    def test_tall_image_limited_by_rows(self):
        out_w, out_h = _fit(50, 100, 80, 24)
        assert out_h == 24
        assert out_w == 24

    def test_minimum_one(self):
        assert _fit(1, 1, 1, 1) == (1, 1)


class TestImageToAscii:
    def test_grid_dimensions(self):
        pixels = [(0, 0, 0)] * (3 * 2)  # 3 wide, 2 tall
        grid = image_to_ascii(pixels, 3, 2, color=False)
        assert len(grid) == 2
        assert all(len(row) == 3 for row in grid)

    def test_black_is_space_mono(self):
        grid = image_to_ascii([(0, 0, 0)], 1, 1, color=False)
        char, style_rgb = grid[0][0]
        assert char == " "
        assert style_rgb is None

    def test_white_is_last_ramp_char_mono(self):
        grid = image_to_ascii([(255, 255, 255)], 1, 1, color=False)
        char, style_rgb = grid[0][0]
        assert char == "@"
        assert style_rgb is None

    def test_color_preserves_rgb(self):
        grid = image_to_ascii([(255, 255, 255)], 1, 1, color=True)
        char, style_rgb = grid[0][0]
        assert char == "@"
        assert style_rgb == (255, 255, 255)

    def test_luminance_uses_green_weight(self):
        # green weight 0.587 -> lum ~149.6 -> round(149.6/255*9)=5 -> _RAMP[5]='+'
        grid = image_to_ascii([(0, 255, 0)], 1, 1, color=False)
        char, _ = grid[0][0]
        assert char == "+"


class TestImageViewerContent:
    def _make_png(self, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        p = tmp_path / "tiny.png"
        img = Image.new("RGB", (4, 4), (255, 0, 0))
        img.save(p)
        return p

    async def test_content_opens_and_toggles(self, tmp_path):
        pytest.importorskip("PIL")
        from textual.app import App

        from dunders.fm.image_viewer import ImageViewerContent

        png = self._make_png(tmp_path)

        class _Host(App):
            def compose(self):
                yield ImageViewerContent(png)

        app = _Host()
        async with app.run_test() as pilot:
            content = app.query_one(ImageViewerContent)
            assert content.widget.color is True
            assert content.widget.img_size == (4, 4)
            assert content.widget._grid  # grid populated => image decoded
            assert content._button.label.plain == "[ Color ]"
            content._toggle_color()
            await pilot.pause()
            assert content.widget.color is False
            assert content._button.label.plain == "[ Mono ]"
            assert content.widget._grid[0][0][1] is None  # mono => rgb is None


    async def test_toolbar_button_toggles_and_stays_visible_on_hover(self, tmp_path):
        """The palette-driven toolbar button toggles color on press and keeps
        a readable label when hovered/focused (regression: stock Button hid
        its text on hover)."""
        pytest.importorskip("PIL")
        from textual.app import App

        from dunders.fm.image_viewer import ImageViewerContent
        from dunders.windowing.palette import Palette
        from dunders.windowing.themes.modern_dark import modern_dark

        png = self._make_png(tmp_path)

        class _Host(App):
            def compose(self):
                yield ImageViewerContent(png)

        app = _Host()
        async with app.run_test() as pilot:
            content = app.query_one(ImageViewerContent)
            btn = content._button

            # Press (keyboard/programmatic) toggles color + relabels.
            btn.action_press()
            await pilot.pause()
            assert content.widget.color is False
            assert btn.label.plain == "[ Mono ]"

            # With a real skin, hover uses the active role and stays visible:
            # foreground and background differ (text never collapses into bg).
            palette = Palette(modern_dark)
            btn._get_palette = lambda: palette
            btn._hover = False
            normal = btn.render()
            btn._hover = True
            hover = btn.render()
            assert hover.plain.strip() == "[ Mono ]"
            assert normal.style != hover.style
            hover_style = hover.style
            assert hover_style.color is not None
            assert hover_style.bgcolor is not None
            assert hover_style.color != hover_style.bgcolor

    async def test_content_opens_from_bytes(self, tmp_path):
        """ASCII viewer decodes from an in-memory buffer (no local path), as
        used for images pulled over a VFS provider like SFTP."""
        pytest.importorskip("PIL")
        from textual.app import App
        from PIL import Image
        import io

        from dunders.fm.image_viewer import ImageViewerContent

        buf = io.BytesIO()
        Image.new("RGB", (4, 4), (0, 128, 255)).save(buf, format="PNG")
        data = buf.getvalue()

        class _Host(App):
            def compose(self):
                yield ImageViewerContent.from_bytes("remote.png", data)

        app = _Host()
        async with app.run_test():
            content = app.query_one(ImageViewerContent)
            assert content.window_title == "Image: remote.png"
            assert content.widget.img_size == (4, 4)
            assert content.widget._grid  # decoded from bytes

    async def test_member_view_routes_image_to_ascii(self, tmp_path, monkeypatch):
        """F3 on an image VFS member (e.g. over SFTP) opens the ASCII image
        viewer fed from the read bytes, not the hex viewer."""
        pytest.importorskip("PIL")
        from contextlib import contextmanager
        from types import SimpleNamespace
        import io
        from PIL import Image

        from dunders.app import DundersApp
        from dunders.fm.image_viewer import ImageViewerContent

        buf = io.BytesIO()
        Image.new("RGB", (4, 4), (200, 100, 50)).save(buf, format="PNG")
        data = buf.getvalue()

        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()

            @contextmanager
            def _open_read(_loc):
                yield SimpleNamespace(read=lambda: data)

            fake_provider = SimpleNamespace(open_read=_open_read)
            monkeypatch.setattr(
                app._vfs_registry, "resolve", lambda _loc: fake_provider
            )
            loc = SimpleNamespace(scheme="sftp", name="pic.png")
            entry = SimpleNamespace(name="pic.png", size=len(data), loc=loc)

            app._open_member_view(entry)
            await pilot.pause()

            img_windows = [
                w for w in app.desktop.windows
                if isinstance(w.content, ImageViewerContent)
            ]
            assert len(img_windows) == 1
            assert img_windows[0].content.widget.img_size == (4, 4)

    async def test_esc_closes_image_viewer_window(self, tmp_path):
        """Esc on a focused ASCII image viewer closes the window and returns
        focus to the panel (regression: Esc was a no-op on image viewers)."""
        pytest.importorskip("PIL")
        from PIL import Image

        from dunders.app import DundersApp
        from dunders.fm.image_viewer import ImageViewerContent

        png = tmp_path / "pic.png"
        Image.new("RGB", (4, 4), (200, 100, 50)).save(png)

        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_editor_window(png, read_only=True)
            await pilot.pause()
            assert any(
                isinstance(w.content, ImageViewerContent)
                for w in app.desktop.windows
            )
            await pilot.press("escape")
            await pilot.pause()
            assert not any(
                isinstance(w.content, ImageViewerContent)
                for w in app.desktop.windows
            )


class TestDegradation:
    async def test_image_falls_back_to_hex_without_pillow(self, tmp_path, monkeypatch):
        """With Pillow unavailable, F3 on an image must NOT open the ASCII
        viewer — it falls through to the hex viewer."""
        pytest.importorskip("PIL")
        from PIL import Image

        import dunders.app as appmod
        from dunders.app import DundersApp
        from dunders.fm.hex_viewer import HexViewerContent
        from dunders.fm.image_viewer import ImageViewerContent

        png = tmp_path / "x.png"
        Image.new("RGB", (4, 4), (10, 20, 30)).save(png)

        # _looks_image still detects it by magic bytes; the routing guard is
        # PILLOW_AVAILABLE, which the app module reads from its own namespace.
        monkeypatch.setattr(appmod, "PILLOW_AVAILABLE", False)
        assert DundersApp._looks_image(png) is True

        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_editor_window(png, read_only=True)
            await pilot.pause()
            windows = list(app.desktop.windows)
            assert not any(isinstance(w.content, ImageViewerContent) for w in windows)
            assert any(isinstance(w.content, HexViewerContent) for w in windows)
