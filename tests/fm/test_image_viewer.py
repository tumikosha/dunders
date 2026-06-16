from dunders.fm.image_viewer import sniff_image, _fit


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
