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
